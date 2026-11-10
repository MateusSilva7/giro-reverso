"""
ERP sintético de uma rede fictícia de autopeças (5 lojas e 1 CD).

Cada leitura devolve um DataFrame no formato que as telas e as regras esperam, gerado aqui por um modelo estatístico simples e
reprodutível (semente fixa):

- catálogo de ~1.200 peças em 14 categorias, com fabricantes, aplicações e custos fictícios;
- cada loja trabalha uma parte do catálogo; cada (loja, item) tem um perfil de demanda: ativo (log-normal com sazonalidade leve),
  em queda, parado há alguns meses ou sem venda no ano; a venda de cada mês é Poisson da demanda daquele mês (12 meses);
- o estoque segue a demanda (alvo de cobertura + ruído), com uma parte em excesso e o estoque residual dos itens parados, que é o que a
  regra existe para achar;
- prazo de reposição por loja, mínimo de exposição, múltiplo de transferência, tempo de loja, sazonalidade e promoção por item;
- os pedidos de devolução que o Compras emite na demonstração ficam em duas tabelas do banco local (`erp_pedido`, `erp_pedido_item`),
  para o vínculo, a separação e o histórico funcionarem de ponta a ponta.

Nenhum dado aqui é real: nomes de loja, fabricantes, códigos, números de pedido e valores são inventados.
"""
from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timedelta
from functools import lru_cache

import numpy as np
import pandas as pd
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    func,
    insert,
    select,
)

import config

# ============================================================
# CONSTANTES
# ============================================================
COLS_VENDA = [f"v{m:02d}" for m in range(1, config.MESES_HISTORICO + 1)]   # v01 = mês mais antigo, v12 = últimos 30 dias
COLS_BASE = ["cd_loja", "sku", "descricao", "categoria", "fabricante", "ref_fab", "aplicacao", "endereco", "origem",
             "estoque", "custo_unit", "multiplo", "exposicao_min", "lead_time_dias", *COLS_VENDA, "dias_na_loja", "sazonal",
             "em_promocao", "cmv_mes"]
COLS_REVALIDAR = ["sku", "estoque", "custo_unit", "multiplo", "exposicao_min", "lead_time_dias", *COLS_VENDA, "dias_na_loja",
                  "sazonal", "em_promocao"]
COLS_STATUS_DEVOLUCAO = ["cd_loja", "num_pedido", "serie", "dt_emissao", "separado", "dt_conferencia", "com_falta", "cancelada"]
COLS_PEDIDO = ["num_pedido", "serie", "valor", "custo", "volumes", "peso", "separado_por", "nf", "cancelada", "observa", "com_falta",
               "emissao", "inicio_separacao", "fim_separacao", "fim_conferencia"]
COLS_ITENS = ["num_pedido", "serie", "nu_item", "sku", "qtde", "preco", "custo", "descricao", "ref_fab", "fabricante"]

OBSERVACAO_GIRO_REVERSO = "%GIRO REVERSO%"
CD = "01"
NOMES_OUTRAS = {CD: "CD", "08": "Regional A", "10": "Regional B"}   # o CD e duas lojas de outra regional (só aparecem na ficha)

SEMENTE = 2026
N_PRODUTOS = 1200
MINUTOS_ATE_SEPARAR = 2   # pedido emitido na demonstração: a loja "separa" depois de 2 minutos, para o quadro andar sozinho

# ============================================================
# CATÁLOGO FICTÍCIO
# ============================================================
# (categoria, peso no catálogo, custo típico R$, múltiplo de transferência, sazonal?, mês de pico, descrições)
CATEGORIAS = [
    ("FREIOS", 14, 95, 1, False, 0, ["PASTILHA FREIO DIANT", "PASTILHA FREIO TRAS", "LONA FREIO TRAS", "CILINDRO RODA",
                                     "FLEXIVEL FREIO", "SAPATA FREIO"]),
    ("FREIOS - DISCOS", 5, 180, 2, False, 0, ["DISCO FREIO DIANT VENT", "DISCO FREIO DIANT SOLIDO", "TAMBOR FREIO TRAS"]),
    ("SUSPENSÃO", 12, 210, 1, False, 0, ["AMORTECEDOR DIANT", "AMORTECEDOR TRAS", "BANDEJA SUSPENSAO", "PIVO SUSPENSAO", "BIELETA"]),
    ("SUSPENSÃO - COXINS", 6, 60, 1, False, 0, ["COXIM AMORTECEDOR", "COXIM MOTOR", "BATENTE AMORTECEDOR", "KIT REPARO SUSP"]),
    ("FILTROS", 14, 32, 1, False, 0, ["FILTRO OLEO", "FILTRO AR MOTOR", "FILTRO COMBUSTIVEL", "FILTRO AR CABINE"]),
    ("IGNIÇÃO", 8, 38, 4, False, 0, ["VELA IGNICAO", "VELA IGNICAO IRIDIUM"]),
    ("IGNIÇÃO - CABOS", 4, 140, 1, False, 0, ["CABO VELA JOGO", "BOBINA IGNICAO"]),
    ("ARREFECIMENTO", 8, 150, 1, True, 1, ["BOMBA DAGUA", "VALVULA TERMOSTATICA", "RESERVATORIO EXPANSAO", "RADIADOR",
                                          "MANGUEIRA RADIADOR"]),
    ("MOTOR", 9, 260, 1, False, 0, ["CORREIA DENTADA", "TENSOR CORREIA", "JUNTA CABECOTE", "KIT EMBREAGEM", "CORREIA POLY-V"]),
    ("ELÉTRICA", 7, 45, 2, False, 0, ["LAMPADA H4", "LAMPADA H7", "RELE AUXILIAR", "SENSOR ROTACAO", "SENSOR TEMPERATURA"]),
    ("ILUMINAÇÃO", 4, 320, 1, False, 0, ["FAROL LE", "FAROL LD", "LANTERNA TRASEIRA LE", "LANTERNA TRASEIRA LD"]),
    ("DIREÇÃO", 6, 120, 1, False, 0, ["TERMINAL DIRECAO", "BARRA AXIAL", "BRACO AXIAL"]),
    ("BATERIAS", 3, 420, 1, True, 7, ["BATERIA 45AH", "BATERIA 60AH", "BATERIA 70AH"]),
    ("ACESSÓRIOS", 3, 25, 1, False, 0, ["TAPETE BORRACHA JOGO", "CAPA VOLANTE", "ADITIVO RADIADOR 1L"]),
]
FABRICANTES = ["ATLAS PECAS", "BOREAL", "COBALTO", "DUNAS AUTO", "FORJA NOVA", "GRANITO", "HELICE", "INDIGO PARTS",
               "JADE MOTORS", "KRONA", "LUMEN AUTO", "MERIDIANO"]
APLICACOES = ["HATCH 1.0 2012-2020", "HATCH 1.6 2015-2022", "SEDAN 1.6 2010-2019", "SEDAN 2.0 2016-2023", "SUV 1.3 TURBO 2019-2024",
              "SUV 2.0 2014-2021", "PICAPE 2.0 DIESEL 2012-2022", "UTILITARIO 1.4 2010-2020", "MULTIMARCAS"]
PORTE_LOJA = {"03": 1.35, "04": 1.15, "05": 1.0, "06": 0.85, "07": 0.7}   # fator de demanda por loja
LEAD_TIME_LOJA = {"03": 3, "04": 4, "05": 5, "06": 6, "07": 7}           # dias para o CD repor a loja


def _hash(*partes) -> int:
    """Inteiro estável a partir de texto (o hash() do Python muda a cada processo)."""
    return int(hashlib.md5("|".join(map(str, partes)).encode()).hexdigest()[:12], 16)


@lru_cache(maxsize=1)
def _catalogo() -> pd.DataFrame:
    rng = np.random.default_rng(SEMENTE)
    pesos = np.array([c[1] for c in CATEGORIAS], dtype=float)
    linhas, cod = [], 10000
    for _ in range(N_PRODUTOS):
        k = int(rng.choice(len(CATEGORIAS), p=pesos / pesos.sum()))
        categoria, _, custo_base, mult, sazonal, pico, nomes = CATEGORIAS[k]
        fab = int(rng.integers(len(FABRICANTES)))
        aplic = APLICACOES[int(rng.integers(len(APLICACOES)))]
        linhas.append(dict(
            sku=f"{cod:06d}", descricao=f"{nomes[int(rng.integers(len(nomes)))]} {aplic.split()[0]}", aplicacao=aplic,
            categoria=categoria, fabricante=FABRICANTES[fab], ref_fab=f"{FABRICANTES[fab][:2]}-{int(rng.integers(1000, 99999))}",
            custo_unit=round(float(custo_base * rng.lognormal(0, 0.35)), 2),
            multiplo=float(mult if rng.random() < 0.9 else 2), sazonal_cat=sazonal, pico=pico))
        cod += int(rng.integers(1, 4))
    return pd.DataFrame(linhas)


def _sazonalidade(meses: np.ndarray, pico: np.ndarray, amplitude: np.ndarray) -> np.ndarray:
    """Fator multiplicativo por mês (1 ± amplitude, com pico no mês `pico`, 1 = jan); pico 0 = sem sazonalidade."""
    fator = 1 + amplitude[:, None] * np.cos((meses[None, :] - pico[:, None]) / 12 * 2 * np.pi)
    return np.where(pico[:, None] > 0, fator, 1.0)


@lru_cache(maxsize=1)
def _base() -> pd.DataFrame:
    """Uma linha por (loja, item) com tudo o que as leituras do ERP precisam. Determinística (semente fixa)."""
    rng = np.random.default_rng(SEMENTE + 1)
    cat = _catalogo()
    hoje = config.agora().date()
    meses = np.array([(hoje.month - config.MESES_HISTORICO + m - 1) % 12 + 1 for m in range(1, config.MESES_HISTORICO + 1)])
    partes = []
    for cd in config.LOJAS_TODAS.values():
        df = cat[rng.random(len(cat)) < 0.55].copy()
        n = len(df)
        df.insert(0, "cd_loja", cd)
        perfil = rng.choice(["ativo", "queda", "parado", "morto"], n, p=[0.65, 0.15, 0.12, 0.08])
        lam = np.clip(rng.lognormal(0.2, 1.0, n), 0.15, 40.0) * PORTE_LOJA[cd]
        t = np.arange(config.MESES_HISTORICO)
        curva = np.ones((n, len(t)))
        curva[perfil == "queda"] = np.linspace(1.0, 0.15, len(t))
        corte = rng.integers(3, 10, n)                                    # parado: vendeu até o mês `corte` e parou
        curva[perfil == "parado"] = (t[None, :] < corte[perfil == "parado", None]).astype(float)
        curva[perfil == "morto"] = 0.0
        sazon = _sazonalidade(meses, df["pico"].to_numpy(), np.full(n, 0.35))
        lam_mes = lam[:, None] * curva * sazon
        vendas = rng.poisson(lam_mes)
        for j, c in enumerate(COLS_VENDA):
            df[c] = vendas[:, j].astype(float)
        lt = LEAD_TIME_LOJA[cd] + rng.integers(0, 3, n)
        expo = np.where(rng.random(n) < 0.5, rng.integers(1, 4, n), 0)
        mult = df["multiplo"].to_numpy()
        lam_atual = lam * curva[:, -1]
        alvo = lam_atual / 30 * (30 + lt) * rng.uniform(0.8, 1.6, n) + expo
        excesso = np.where(rng.random(n) < 0.15, np.ceil(lam * rng.uniform(1.5, 5, n)), 0)
        residual = np.where(perfil == "queda", np.ceil(lam * rng.uniform(0.5, 3, n)), 0)   # comprado para a demanda antiga
        estoque = np.where(np.isin(perfil, ["parado", "morto"]), rng.integers(1, 9, n) + expo, np.ceil(alvo) + excesso + residual)
        estoque = np.where(rng.random(n) < 0.04, 0, np.ceil(estoque / mult) * mult)
        df["estoque"] = estoque.astype(float)
        df["exposicao_min"] = expo.astype(float)
        df["lead_time_dias"] = lt.astype(float)
        dias = np.where(rng.random(n) < 0.10, rng.integers(10, 90, n), rng.integers(120, 1800, n)).astype(float)
        df["dias_na_loja"] = np.where(rng.random(n) < 0.04, np.nan, dias)
        df["sazonal"] = df["sazonal_cat"].to_numpy() & (rng.random(n) < 0.5)
        df["em_promocao"] = rng.random(n) < 0.03
        df["endereco"] = [f"{chr(65 + int(a))}{int(b):02d}-{int(c):02d}" for a, b, c in
                          zip(rng.integers(0, 8, n), rng.integers(1, 30, n), rng.integers(1, 6, n))]
        outras = [nm for c, nm in config.NOME_LOJA.items() if c != cd]
        tipo = rng.choice(["cd", "transf", "direta"], n, p=[0.75, 0.15, 0.10])
        df["origem"] = np.where(tipo == "cd", "CD", np.where(tipo == "direta", "Compra direta",
                                [f"Transferência — {outras[int(i)]}" for i in rng.integers(0, len(outras), n)]))
        df["_demanda"] = lam_atual
        partes.append(df)
    base = pd.concat(partes, ignore_index=True)
    cmv = (base["_demanda"] * base["custo_unit"]).groupby(base["cd_loja"]).sum()
    base["cmv_mes"] = base["cd_loja"].map(cmv).round(2)
    return base


def _da_loja(cd: str) -> pd.DataFrame:
    b = _base()
    return b[b["cd_loja"] == cd]


# ============================================================
# LEITURAS
# ============================================================
def ler_base() -> pd.DataFrame:
    """Estoque, custo, parâmetros de reposição e 12 meses de venda de todas as lojas ativas (sem cálculo). Quem cacheia é ui/cache.py."""
    b = _base()
    return b[b["cd_loja"].isin(config.LOJAS.values())][COLS_BASE].reset_index(drop=True).copy()


def ler_revalidacao(skus: list[str], cd_loja: str) -> pd.DataFrame:
    """Estoque e venda AGORA para os candidatos do dia (as entradas da regra)."""
    if not skus:
        return pd.DataFrame(columns=COLS_REVALIDAR)
    df = _da_loja(cd_loja)
    return df[df["sku"].isin(set(skus))][COLS_REVALIDAR].reset_index(drop=True).copy()


def ler_cadastro_produtos(skus: list[str]) -> pd.DataFrame:
    """Cadastro do item (unidade, código de barras, referência original, medidas, data de cadastro, fora de linha)."""
    cat = _catalogo().set_index("sku")
    linhas = []
    for c in skus:
        if c not in cat.index:
            continue
        r, h = cat.loc[c], _hash("cad", c)
        linhas.append({"sku": c, "unidade": "JG" if "JOGO" in r["descricao"] else "PC", "ref_original": f"OR{h % 900000 + 100000}",
                       "cod_barras": f"789{h % 10**10:010d}", "dt_cadastro": (datetime(2015, 1, 1) + timedelta(days=h % 3500)).date(),
                       "fora_linha": h % 40 == 0, "peso_kg": round(0.2 + (h % 80) / 10, 1),
                       "dimensoes_cm": f"{3 + h % 25} × {5 + h % 40} × {2 + h % 30}", "multiplo": r["multiplo"]})
    return pd.DataFrame(linhas, columns=["sku", "unidade", "ref_original", "cod_barras", "dt_cadastro", "fora_linha", "peso_kg",
                                         "dimensoes_cm", "multiplo"])


def ler_outras_lojas(skus: list[str]) -> pd.DataFrame:
    """Estoque e venda média mensal do item no CD e nas outras lojas."""
    b = _base()
    b = b[b["sku"].isin(set(skus))]
    linhas = b.assign(venda_media=b[COLS_VENDA[-3:]].mean(axis=1).round(1))[["sku", "cd_loja", "estoque", "venda_media"]].to_dict("records")
    for c in skus:
        for cd in NOMES_OUTRAS:
            h = _hash("outra", cd, c)
            if cd != CD and h % 3:
                continue
            linhas.append({"sku": c, "cd_loja": cd, "estoque": float(h % (60 if cd == CD else 8)), "venda_media": round((h % 40) / 4, 1)})
    return pd.DataFrame(linhas, columns=["sku", "cd_loja", "estoque", "venda_media"])


def ler_cmv_mensal(cd_loja: str) -> pd.DataFrame:
    """CMV por mês nos últimos 12 meses, mais o mês corrente (parcial), com sazonalidade leve."""
    base = float(_da_loja(cd_loja)["cmv_mes"].max() or 0)
    hoje = config.agora().date().replace(day=1)
    meses = [(pd.Timestamp(hoje) - pd.DateOffset(months=k)).date() for k in range(12, -1, -1)]
    rng = np.random.default_rng(_hash("cmv", cd_loja) % 2**32)
    fator = [1 + 0.08 * np.sin((m.month - 3) / 12 * 2 * np.pi) + rng.normal(0, 0.04) for m in meses]
    fator[-1] *= min(config.agora().day / 30, 1)
    return pd.DataFrame({"mes": meses, "cmv": [round(base * f, 2) for f in fator]})


# ============================================================
# PEDIDOS DE DEVOLUÇÃO (loja → CD)
# ============================================================
metadata = MetaData()
erp_pedido = Table(
    "erp_pedido", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("cd_loja", String(2), nullable=False),
    Column("num_pedido", String(10), nullable=False),
    Column("serie", String(4), nullable=False),
    Column("emissao", DateTime, nullable=False),
    Column("separado_em", DateTime),
    Column("cancelada", String(1), nullable=False, default="N"),
    Column("observa", String(120)),
    UniqueConstraint("cd_loja", "num_pedido", "serie", name="uq_erp_pedido"),
)
erp_pedido_item = Table(
    "erp_pedido_item", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("pedido_id", Integer, nullable=False),
    Column("nu_item", Integer, nullable=False),
    Column("sku", String(6), nullable=False),
    Column("qtde", Float, nullable=False),
)
_TRAVA = threading.Lock()
PRIMEIRO_NUMERO = 600001


def criar_tabelas(engine) -> None:
    metadata.create_all(engine)


def _engine():
    from data import estado  # import tardio: estado importa este módulo
    return estado.get_engine_estado()


def _itens_do_lote(cd_loja: str, n: int, semente: int) -> list[tuple[str, float]]:
    """n produtos parados da loja, para os pedidos de exemplo e o histórico."""
    df = _da_loja(cd_loja)
    candidatos = df[(df[COLS_VENDA[-3:]].sum(axis=1) == 0) & (df["estoque"] > 0)]
    if candidatos.empty:
        candidatos = df[df["estoque"] > 0]
    escolha = candidatos.sample(n=min(n, len(candidatos)), random_state=semente)
    return [(r.sku, float(max(1, r.estoque - 1))) for r in escolha.itertuples()]


def semear_pedidos(conn, pedidos: list[tuple[str, str, str, float, float | None]]) -> None:
    """Pedidos de exemplo no ERP: (cd_loja, num_pedido, serie, horas desde a emissão, horas desde a separação ou None)."""
    agora = config.agora()
    for k, (cd, nota, serie, h_em, h_sep) in enumerate(pedidos):
        if conn.execute(select(erp_pedido.c.id).where(erp_pedido.c.cd_loja == cd, erp_pedido.c.num_pedido == nota,
                                                      erp_pedido.c.serie == serie)).first():
            continue
        pid = conn.execute(insert(erp_pedido).values(
            cd_loja=cd, num_pedido=nota, serie=serie, emissao=agora - timedelta(hours=h_em),
            separado_em=None if h_sep is None else agora - timedelta(hours=h_sep), cancelada="N",
            observa=config.OBSERVACAO_PADRAO_PEDIDO)).inserted_primary_key[0]
        itens = _itens_do_lote(cd, 3 + k % 4, semente=k)
        conn.execute(insert(erp_pedido_item), [{"pedido_id": pid, "nu_item": i + 1, "sku": c, "qtde": q}
                                               for i, (c, q) in enumerate(itens)])


def emitir_pedido(cd_loja: str, itens: list[tuple[str, float]], observa: str | None = None, serie: str = config.SERIE_PEDIDO) -> tuple[str, str]:
    """Simula o Compras batendo o pedido de devolução no ERP: grava o pedido com os itens e devolve (número, série).
    A loja "separa" sozinha `MINUTOS_ATE_SEPARAR` minutos depois (na demonstração não há ninguém do outro lado)."""
    agora = config.agora()
    with _TRAVA, _engine().begin() as conn:
        maior = conn.execute(select(func.max(erp_pedido.c.num_pedido))).scalar()
        numero = str(max(PRIMEIRO_NUMERO, int(maior) + 1 if maior and str(maior).isdigit() else PRIMEIRO_NUMERO))
        pid = conn.execute(insert(erp_pedido).values(
            cd_loja=cd_loja, num_pedido=numero, serie=serie, emissao=agora,
            separado_em=agora + timedelta(minutes=MINUTOS_ATE_SEPARAR), cancelada="N",
            observa=observa or config.OBSERVACAO_PADRAO_PEDIDO)).inserted_primary_key[0]
        conn.execute(insert(erp_pedido_item), [{"pedido_id": pid, "nu_item": i + 1, "sku": str(c), "qtde": float(q)}
                                               for i, (c, q) in enumerate(itens)])
    return numero, serie


def _pedidos(where=None) -> pd.DataFrame:
    stmt = select(erp_pedido)
    if where is not None:
        stmt = stmt.where(where)
    with _engine().connect() as conn:
        return pd.read_sql(stmt, conn)


def _itens(ids: list[int]) -> pd.DataFrame:
    if not ids:
        return pd.DataFrame(columns=["pedido_id", "nu_item", "sku", "qtde"])
    with _engine().connect() as conn:
        return pd.read_sql(select(erp_pedido_item).where(erp_pedido_item.c.pedido_id.in_(ids)), conn)


def _cabecalho(p: pd.Series, itens: pd.DataFrame, agora: datetime) -> dict:
    sep = p["separado_em"] if pd.notna(p["separado_em"]) and p["separado_em"] <= agora else pd.NaT
    valor = float((itens["qtde"] * itens["preco"]).sum()) if not itens.empty else 0.0
    h = _hash("ped", p["cd_loja"], p["num_pedido"])
    return {"num_pedido": p["num_pedido"], "serie": p["serie"], "valor": round(valor, 2), "custo": round(valor * 0.78, 2),
            "volumes": float(1 + len(itens) // 4), "peso": round(1.5 * max(len(itens), 1), 1), "separado_por": f"OP{h % 90 + 10}",
            "nf": f"{h % 900000 + 100000}" if pd.notna(sep) else None, "cancelada": p["cancelada"], "observa": p["observa"],
            "com_falta": None, "emissao": p["emissao"],
            "inicio_separacao": p["emissao"] + timedelta(minutes=1) if pd.notna(sep) else pd.NaT, "fim_separacao": sep,
            "fim_conferencia": sep}


def _itens_com_produto(itens: pd.DataFrame, cd_loja: str) -> pd.DataFrame:
    cat = _catalogo().set_index("sku")
    itens = itens.copy()
    itens["preco"] = [float(cat.at[c, "custo_unit"]) * 1.25 if c in cat.index else 0.0 for c in itens["sku"]]
    itens["custo"] = [float(cat.at[c, "custo_unit"]) if c in cat.index else 0.0 for c in itens["sku"]]
    for col in ("descricao", "ref_fab", "fabricante"):
        itens[col] = [cat.at[c, col] if c in cat.index else "" for c in itens["sku"]]
    return itens


def ler_pedido_devolucao(cd_loja: str, num_pedido: str, serie: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Cabeçalho e itens de UM pedido de devolução da loja ao CD (vazio se o ERP não o conhece)."""
    ped = _pedidos((erp_pedido.c.cd_loja == cd_loja) & (erp_pedido.c.num_pedido == str(num_pedido)) & (erp_pedido.c.serie == str(serie)))
    if ped.empty:
        return pd.DataFrame(columns=COLS_PEDIDO), pd.DataFrame(columns=COLS_ITENS)
    p = ped.iloc[0]
    itens = _itens_com_produto(_itens([int(p["id"])]), cd_loja)
    itens = itens.assign(num_pedido=p["num_pedido"], serie=p["serie"]).sort_values("nu_item")
    return pd.DataFrame([_cabecalho(p, itens, config.agora())], columns=COLS_PEDIDO), itens[COLS_ITENS].reset_index(drop=True)


def ler_status_devolucoes(chaves: list[tuple[str, str, str]]) -> pd.DataFrame:
    """Status ao vivo (emissão, separação, cancelamento) dos pedidos vinculados."""
    if not chaves:
        return pd.DataFrame(columns=COLS_STATUS_DEVOLUCAO)
    pedidos = _pedidos()
    if pedidos.empty:
        return pd.DataFrame(columns=COLS_STATUS_DEVOLUCAO)
    agora = config.agora()
    quer = pd.DataFrame(chaves, columns=["cd_loja", "num_pedido", "serie"]).astype(str)
    df = pedidos.merge(quer, on=["cd_loja", "num_pedido", "serie"])
    if df.empty:
        return pd.DataFrame(columns=COLS_STATUS_DEVOLUCAO)
    separado = df["separado_em"].notna() & (df["separado_em"] <= agora)
    return pd.DataFrame({"cd_loja": df["cd_loja"], "num_pedido": df["num_pedido"], "serie": df["serie"], "dt_emissao": df["emissao"],
                         "separado": separado, "dt_conferencia": df["separado_em"].where(separado), "com_falta": None,
                         "cancelada": df["cancelada"]})[COLS_STATUS_DEVOLUCAO]


def ler_status_para_vinculos(vinculos: pd.DataFrame) -> pd.DataFrame:
    """Atalho: extrai (cd_loja, num_pedido, serie) de data/estado.py::listar_pedidos_gr() e busca o status de todos de uma vez."""
    if vinculos.empty:
        return pd.DataFrame(columns=COLS_STATUS_DEVOLUCAO)
    return ler_status_devolucoes(list(vinculos[["cd_loja", "num_pedido", "serie"]].itertuples(index=False, name=None)))


def _historico_sintetico(cd_loja: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pedidos Giro Reverso dos últimos 12 meses (antes do app): ~3 por mês, mais lentos que os de hoje."""
    rng = np.random.default_rng(_hash("hist", cd_loja) % 2**32)
    agora = config.agora()
    pedidos, itens = [], []
    n = int(30 * PORTE_LOJA.get(cd_loja, 1))
    for i in range(n):
        em = agora - timedelta(days=int(365 - i * 360 / n), hours=int(rng.integers(8, 18)))
        ok = rng.random() > 0.08
        nota = f"{4 * 10**5 + _hash('h', cd_loja, i) % 10**5}"
        sep = em + timedelta(hours=float(rng.uniform(4, 70))) if ok else pd.NaT
        pedidos.append({"num_pedido": nota, "serie": config.SERIE_PEDIDO, "cancelada": "N" if ok else "S", "observa": config.OBSERVACAO_PADRAO_PEDIDO,
                        "emissao": em, "separado_em": sep, "cd_loja": cd_loja})
        for k, (c, q) in enumerate(_itens_do_lote(cd_loja, int(rng.integers(3, 12)), semente=1000 + i)):
            itens.append({"num_pedido": nota, "serie": config.SERIE_PEDIDO, "nu_item": k + 1, "sku": c, "qtde": q})
    return pd.DataFrame(pedidos), pd.DataFrame(itens)


def ler_devolucoes_giro_reverso(cd_loja: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(pedidos, itens) de todos os pedidos de devolução Giro Reverso da loja: o histórico e os emitidos nesta demonstração."""
    hist_p, hist_i = _historico_sintetico(cd_loja)
    novos = _pedidos(erp_pedido.c.cd_loja == cd_loja)
    novos = novos[novos["observa"].fillna("").str.upper().str.contains(OBSERVACAO_GIRO_REVERSO.strip("%"))]
    itens_novos = _itens(novos["id"].astype(int).tolist()).merge(novos[["id", "num_pedido", "serie"]], left_on="pedido_id", right_on="id")
    todos_p = pd.concat([hist_p, novos.drop(columns=["id"])], ignore_index=True)
    todos_i = pd.concat([hist_i, itens_novos[["num_pedido", "serie", "nu_item", "sku", "qtde"]]], ignore_index=True)
    todos_i = _itens_com_produto(todos_i, cd_loja) if not todos_i.empty else pd.DataFrame(columns=COLS_ITENS)
    agora = config.agora()
    cab = [_cabecalho(p, todos_i[(todos_i["num_pedido"] == p["num_pedido"]) & (todos_i["serie"] == p["serie"])], agora)
           for _, p in todos_p.iterrows()]
    pedidos = pd.DataFrame(cab, columns=COLS_PEDIDO).sort_values("emissao").reset_index(drop=True)
    return pedidos, todos_i[COLS_ITENS].reset_index(drop=True)


def ler_devolucoes_por_tipo(cd_loja: str) -> pd.DataFrame:
    """Todas as devoluções da loja ao CD, agrupadas pela observação do pedido."""
    h = _hash("tipos", cd_loja)
    hoje = config.agora().date()
    porte = PORTE_LOJA.get(cd_loja, 1)
    tipos = [("DEVOLUCAO LOJA > CD - ERRO DE FABRICA", 320), ("DEVOLUCAO LOJA > CD - AVARIA NO TRANSPORTE", 140),
             ("DEVOLUCAO LOJA > CD - GARANTIA", 210), ("DEVOLUCAO LOJA > CD - VENDA LOGISTICA", 90),
             (config.OBSERVACAO_PADRAO_PEDIDO, 30), ("(sem observação)", 25)]
    linhas = [{"observa": t, "pedidos": int(n * porte * (0.8 + (h >> k) % 40 / 100)),
               "primeiro": hoje - timedelta(days=365 if "GIRO" in t else 1400 - k * 40), "ultimo": hoje - timedelta(days=k % 5),
               "valor": round(n * porte * 850.0 * (0.7 + (h >> k) % 60 / 100), 2)} for k, (t, n) in enumerate(tipos)]
    return pd.DataFrame(linhas).sort_values("pedidos", ascending=False).reset_index(drop=True)
