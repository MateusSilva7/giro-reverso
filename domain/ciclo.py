"""
Regras do ciclo diário: montar a fila, revalidar e distribuir o lote do dia.
Funções puras (sem Streamlit, sem banco) — testadas em tests/.

A revalidação do dia não tem regra própria: chama `domain.regra.calcular`, a mesma da identificação, com os parâmetros em vigor.
"""
import io
import unicodedata

import pandas as pd

from config import agora
from domain import origem as og
from domain.regra import Parametros, calcular, na_lista


def _chave_do_nome(nome: str) -> str:
    """Nome sem maiúscula, acento e espaço nas pontas, para comparar o que veio de um link com o que foi digitado."""
    sem_acento = unicodedata.normalize("NFKD", nome.strip()).encode("ascii", "ignore").decode("ascii")
    return sem_acento.casefold()


def achar_pessoa(nome: str | None, pessoas: list[str]) -> str | None:
    """A pessoa do ciclo que o nome pedido (link `?pessoa=Ana`, escolha anterior) indica: sem diferenciar maiúscula,
    acento nem espaço nas pontas. None se o nome não existir no ciclo (ou vier vazio)."""
    if not nome or not str(nome).strip():
        return None
    alvo = _chave_do_nome(str(nome))
    return next((p for p in pessoas if _chave_do_nome(p) == alvo), None)


def _num(v):
    """Número do banco (Decimal/NaN/None) → float ou None, para gravar sem susto."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _texto(v) -> str:
    s = "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()
    return "" if s in ("nan", "None") else s


def ordenar(df: pd.DataFrame) -> pd.DataFrame:
    """A ordem da fila: origem (CD primeiro), fabricante (sem fabricante por último), referência e SKU — a ordem em que a loja
    separa fisicamente."""
    chave = df.assign(_prio=df["origem"].map(og.prioridade) if "origem" in df.columns else 0,
                      _sem_fab=df["fabricante"].fillna("").astype(str).str.strip().eq(""),
                      _fab=df["fabricante"].fillna("").astype(str).str.strip(),
                      _ref=df["ref_fab"].fillna("").astype(str) if "ref_fab" in df.columns else "")
    return chave.sort_values(["_prio", "_sem_fab", "_fab", "_ref", "sku"], kind="stable").drop(columns=["_prio", "_sem_fab", "_fab", "_ref"])


def montar_fila(df_loja: pd.DataFrame) -> list[dict]:
    """Da identificação de uma loja para a fila do ciclo: só o que a regra manda devolver, na ordem de `ordenar`."""
    lista = na_lista(df_loja)
    if lista.empty:
        return []
    return [{
        "sku": str(r.sku),
        "descricao": _texto(getattr(r, "descricao", ""))[:120],
        "ref_fab": _texto(getattr(r, "ref_fab", "")),
        "fabricante": _texto(getattr(r, "fabricante", "")),
        "estoque": _num(r.estoque) or 0.0,
        "alvo": _num(getattr(r, "alvo", None)),
        "qtde_fica": _num(r.qtde_fica),
        "qtde_retirar": _num(r.qtde_retirar),
        "origem": _texto(getattr(r, "origem", "")),
        "motivo": _texto(getattr(r, "motivo", "")),
    } for r in ordenar(lista).itertuples(index=False)]


def quantidades_a_mais(lote: pd.DataFrame, itens_erp: pd.DataFrame) -> list[dict]:
    """Produtos em que o pedido do ERP tira MAIS do que o Retirar do lote: deixaria a loja abaixo do alvo e a reposição
    automática mandaria a peça de volta. Menos do que o Retirar passa. Soma as linhas do
    mesmo produto nos dois lados. Item do lote sem Retirar (lote antigo, de antes da coluna) não tem com o que comparar e fica de fora.
    Devolve [{"sku", "pedido", "retirar"}] por código de produto."""
    if lote.empty or itens_erp.empty:
        return []
    pedido = (itens_erp.assign(sku=itens_erp["sku"].astype(str).str.strip(), qtde=pd.to_numeric(itens_erp["qtde"], errors="coerce"))
              .groupby("sku")["qtde"].sum())
    com_retirar = lote.assign(sku=lote["sku"].astype(str).str.strip(), qtde_retirar=pd.to_numeric(lote["qtde_retirar"], errors="coerce"))
    retirar = com_retirar.dropna(subset=["qtde_retirar"]).groupby("sku")["qtde_retirar"].sum()
    comuns = pedido.index.intersection(retirar.index)
    return [{"sku": c, "pedido": float(pedido[c]), "retirar": float(retirar[c])}
            for c in sorted(comuns) if pedido[c] > retirar[c] + 1e-9]


def revalidar(df_revalidado: pd.DataFrame, params: Parametros | None = None) -> pd.DataFrame:
    """A regra aplicada ao estoque e à venda de agora, com os parâmetros em vigor (os mesmos da identificação)."""
    return calcular(df_revalidado, params)


def skus_validos(df_revalidado: pd.DataFrame, params: Parametros | None = None) -> set[str]:
    """Quem continua na lista com o estoque e a venda de agora."""
    if df_revalidado.empty:
        return set()
    return set(na_lista(revalidar(df_revalidado, params))["sku"])


def processar_lote(fila: list[dict], df_revalidado: pd.DataFrame, pessoas: list[str],
                   pedidos_por_pessoa: int, skus_por_pedido: int, params: Parametros | None = None) -> dict:
    """fila: itens na ordem (cada um com "id" e "sku").

    Pega o dobro do necessário como margem, revalida, os válidos viram o lote do dia, a sobra válida volta pro INÍCIO da fila e
    os inválidos saem. As quantidades do item (estoque, alvo, o que fica e o que sai) vêm todas da revalidação de hoje: o item
    pode ter entrado na fila semanas antes, e misturar o estoque de hoje com o alvo de lá mandaria devolver a quantidade errada.
    """
    skus_por_pessoa = pedidos_por_pessoa * skus_por_pedido
    skus_dia = len(pessoas) * skus_por_pessoa
    candidatos = fila[: min(skus_dia * 2, len(fila))]
    reval = revalidar(df_revalidado, params) if not df_revalidado.empty else df_revalidado
    validos_set = set(na_lista(reval)["sku"]) if not reval.empty else set()
    hoje = reval.set_index("sku").to_dict(orient="index") if not reval.empty else {}

    validos = [c for c in candidatos if c["sku"] in validos_set]
    invalidos = [c for c in candidatos if c["sku"] not in validos_set]
    lote_itens, sobra = validos[:skus_dia], validos[skus_dia:]

    lote = []
    for i, item in enumerate(lote_itens):
        info = hoje.get(item["sku"], {})
        lote.append({
            **item,
            "responsavel": pessoas[i // skus_por_pessoa],
            "grupo": (i % skus_por_pessoa) // skus_por_pedido + 1,
            "estoque": _num(info.get("estoque", item.get("estoque"))),
            "alvo": _num(info.get("alvo", item.get("alvo"))),
            "qtde_fica": _num(info.get("qtde_fica", item.get("qtde_fica"))),
            "qtde_retirar": _num(info.get("qtde_retirar", item.get("qtde_retirar"))),
        })

    ids_candidatos = {c["id"] for c in candidatos}
    nova_fila = [s["id"] for s in sobra] + [f["id"] for f in fila if f["id"] not in ids_candidatos]
    return {"lote": lote, "sobra": sobra, "invalidos": invalidos, "nova_fila_ids": nova_fila,
            "candidatos": len(candidatos), "skus_dia": skus_dia}


def agrupar_pedidos(lote: list[dict]) -> list[dict]:
    """Um pedido de devolução por (pessoa, grupo) — como a planilha do dia já dividia."""
    grupos: dict[tuple, list] = {}
    for it in lote:
        grupos.setdefault((it["responsavel"], it["grupo"]), []).append(it)
    return [{"responsavel": r, "grupo": g, "itens": itens} for (r, g), itens in grupos.items()]


def planilha_dia_xlsx(lote: list[dict]) -> bytes:
    """A planilha do dia (Excel) do lote: um bloco por pessoa e pedido, com a quantidade a retirar de cada item."""
    linhas = [{
        "Responsável": it["responsavel"], "Pedido": f"Pedido {it['grupo']:02d}",
        "SKU": it["sku"], "Descrição": it.get("descricao", ""), "Ref. fabricante": it.get("ref_fab", ""),
        "Fabricante": it.get("fabricante", ""), "Estoque revalidado": it.get("estoque", ""), "Alvo": it.get("alvo", ""),
        "Qtde fica": it.get("qtde_fica", ""), "Qtde a retirar": it.get("qtde_retirar", ""), "Origem": it.get("origem", ""),
    } for it in lote]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame(linhas).to_excel(w, index=False, sheet_name="Planilha do Dia")
    return buf.getvalue()


def planilha_invalidados_xlsx(invalidos: list[dict], dia: int) -> bytes:
    linhas = [{"Data": agora().strftime("%d/%m/%Y"), "SKU": s["sku"], "Descrição": s.get("descricao", ""),
               "Ref. fabricante": s.get("ref_fab", ""), "Fabricante": s.get("fabricante", ""), "Estoque": s.get("estoque", ""),
               "Motivo": "Não passa mais na regra com o estoque e a venda de hoje"} for s in invalidos]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame(linhas).to_excel(w, index=False, sheet_name=f"Invalidados Dia {dia}")
    return buf.getvalue()
