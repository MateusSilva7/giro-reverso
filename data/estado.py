"""
Banco do ACOMPANHAMENTO — só o que o ERP não sabe fazer sozinho.

Emitido e Separação (que já inclui a conferência da loja) vêm ao vivo do ERP
(data/erp.py::ler_status_devolucoes), a partir do vínculo abaixo. As etapas
seguintes (saída da loja, chegada no CD, conferência Qualidade, reintegração ao estoque)
ainda não têm um campo confirmado no ERP — ficam com confirmação manual até
serem migradas pra derivação automática também.

Por que um banco separado: o ERP que o app lê é uma réplica somente leitura.
Nada disso pode ser gravado lá.

- Demonstração: arquivo SQLite local (dados_locais/acompanhamento.db). Os pedidos
  do ERP sintético (data/erp.py) ficam no mesmo arquivo, em tabelas à parte.
- Produção: um Postgres gravável — basta preencher ESTADO_DB_URL.  O modelo (tabelas abaixo) é o mesmo nos dois.
"""
import json
import os
import threading
import uuid
import weakref
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    and_,
    create_engine,
    event,
    exists,
    func,
    insert,
    inspect,
    literal,
    select,
    update,
)
from sqlalchemy.exc import DBAPIError, IntegrityError

import config
from data import erp
from domain.regra import Parametros

metadata = MetaData()

tipo_pedido = Table(
    "tipo_pedido", metadata,
    Column("chave", String(30), primary_key=True),
    Column("nome", String(60), nullable=False),
)

usuario = Table(
    "usuario", metadata,
    Column("login", String(40), primary_key=True),
    Column("nome", String(80), nullable=False),
    Column("papel", String(20), nullable=False),
    Column("cd_loja", String(2)),  # só para logística de loja
)

ciclo = Table(
    "ciclo", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("cd_loja", String(2), nullable=False),
    Column("aberto_em", DateTime, nullable=False),
    Column("aberto_por", String(40), nullable=False),
    Column("pessoas", Text, nullable=False),          # JSON: lista de nomes
    Column("pedidos_por_pessoa", Integer, nullable=False),
    Column("skus_por_pedido", Integer, nullable=False),
    Column("dia_atual", Integer, nullable=False, default=1),
    Column("encerrado_em", DateTime),
)
# Um ciclo aberto por loja, garantido pelo banco: duas sessões abrindo ao mesmo tempo, uma perde.
Index("uq_ciclo_aberto_por_loja", ciclo.c.cd_loja, unique=True,
      sqlite_where=ciclo.c.encerrado_em.is_(None), postgresql_where=ciclo.c.encerrado_em.is_(None))

ciclo_item = Table(
    "ciclo_item", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ciclo_id", Integer, ForeignKey("ciclo.id"), nullable=False),
    Column("posicao", Integer, nullable=False),        # ordem na fila
    Column("sku", String(12), nullable=False),
    Column("ref_fab", String(40)),
    Column("fabricante", String(60)),
    Column("descricao", String(120)),
    Column("estoque", Float),
    Column("alvo", Float),                             # estoque-alvo da regra (o que fica na loja)
    Column("qtde_fica", Float),
    Column("qtde_retirar", Float),
    Column("origem", String(80)),                      # de onde veio a última entrada do item (CD, transferência, compra direta)
    Column("motivo", String(40)),
    Column("situacao", String(12), nullable=False, default="fila"),  # fila | lote | invalidado
    Column("dia", Integer),
    Column("responsavel", String(80)),
    Column("grupo", Integer),                          # nº do pedido dentro da pessoa/dia
    Column("pedido_id", Integer, ForeignKey("pedido_gr.id")),
)

# O único vínculo que o ERP não sabe fazer sozinho: liga um lote nosso ao pedido de devolução do ERP.
# (cd_loja, num_pedido, serie) juntos são a chave de lá — ver data/erp.py::ler_status_devolucoes. O resto
# (separação e conferência na loja) é lido de lá ao vivo, nunca gravado aqui.
pedido_gr = Table(
    "pedido_gr", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("codigo", String(30), nullable=False, unique=True),
    Column("cd_loja", String(2), nullable=False),
    Column("num_pedido", String(20), nullable=False),
    Column("serie", String(5), nullable=False),
    Column("tipo", String(30), ForeignKey("tipo_pedido.chave"), nullable=False),
    Column("ciclo_id", Integer, ForeignKey("ciclo.id")),
    Column("criado_em", DateTime, nullable=False),
    Column("criado_por", String(40), nullable=False),
    Column("exemplo", Boolean, nullable=False, default=False),
    UniqueConstraint("cd_loja", "num_pedido", "serie", name="uq_pedido_dl_chave_erp"),
)

# Confirmação manual das etapas que o ERP não registra (saída da loja, chegada no CD, conferência e reintegração).
# Sem falta/avaria por item — o indicador é o campo de falta do pedido no ERP.
confirmacao_manual = Table(
    "confirmacao_manual", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("pedido_id", Integer, ForeignKey("pedido_gr.id"), nullable=False),
    Column("etapa", String(20), nullable=False),
    Column("confirmado_em", DateTime, nullable=False),
    Column("confirmado_por", String(40), nullable=False),
    # Só o que a etapa exige (nº da NF em "saida_loja", confirmação do endereço em
    # "chegada_cd"); as demais confirmam sem detalhe. Não é um "dados" JSON livre.
    Column("detalhe", Text),
)

# Parâmetros que a Gestão ajusta na Visão de gestão (chave → valor em texto), com quem mexeu e quando: a data de início da
# operação (AAAA-MM-DD) e os parâmetros da regra (nível de serviço, cobertura-alvo e valor mínimo).
parametro_operacao = Table(
    "parametro_operacao", metadata,
    Column("chave", String(40), primary_key=True),
    Column("valor", Text, nullable=False),
    Column("atualizado_em", DateTime, nullable=False),
    Column("atualizado_por", String(40), nullable=False),
)

# Pedidos que a Gestão tirou do acompanhamento (vinculados por engano ou errados). O vínculo e as confirmações somem
# das telas e ficam aqui, para auditoria; o pedido no ERP não é tocado. Livra a chave (loja, nº, série): dá para vincular
# o pedido certo de novo.
pedido_removido = Table(
    "pedido_removido", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("codigo", String(30), nullable=False),
    Column("cd_loja", String(2), nullable=False),
    Column("num_pedido", String(20), nullable=False),
    Column("serie", String(5), nullable=False),
    Column("ciclo_id", Integer),
    Column("criado_em", DateTime),
    Column("criado_por", String(40)),
    Column("etapas", Text),                            # JSON: as confirmações manuais que o pedido tinha
    Column("removido_em", DateTime, nullable=False),
    Column("removido_por", String(40), nullable=False),
    Column("motivo", Text, nullable=False),
)

PARAM_DATA_INICIO = "data_inicio_operacao"
PARAM_NIVEL_SERVICO = "nivel_servico"
PARAM_COBERTURA = "cobertura_dias"
PARAM_VALOR_MINIMO = "valor_minimo"
PARAM_LISTA_ORIGEM = "lista_origem_ciclo"   # prefixo: a chave de cada loja é lista_origem_ciclo_<cd_loja> (de onde veio a fila do ciclo dela)


# ============================================================
# CONEXÃO
# ============================================================

@st.cache_resource
def get_engine_estado():
    """Engine do banco de acompanhamento. Criado uma vez por processo."""
    url = os.getenv("ESTADO_DB_URL", "").strip()
    if url:
        return create_engine(url, pool_size=3, max_overflow=2, pool_pre_ping=True, pool_recycle=1800)

    config.CAMINHO_ESTADO_LOCAL.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{config.CAMINHO_ESTADO_LOCAL}",
        connect_args={"check_same_thread": False, "timeout": 15},
    )

    # WAL: leitores não travam enquanto alguém grava. foreign_keys: o SQLite só
    # respeita chave estrangeira se pedir.
    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    # O WAL só vira o arquivo .db quando alguém faz checkpoint: sem isso o .db sozinho abre vazio
    # (copiar só o .db perdia tudo). A cada subida do app junta o WAL no .db. Backup completo: data/backup.py.
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    except DBAPIError:  # banco ocupado por outro processo: segue sem checkpoint
        pass

    return engine


def limpar_cache_estado() -> None:
    """Chame depois de qualquer gravação, para as telas lerem o dado novo."""
    listar_pedidos_gr.clear()
    listar_confirmacoes.clear()
    listar_usuarios.clear()
    listar_itens_emitidos.clear()
    listar_parametros.clear()
    listar_pedidos_removidos.clear()


# ============================================================
# LEITURAS (cache curto: o banco é nosso e pequeno)
# ============================================================

def _df(stmt, params=None) -> pd.DataFrame:
    with get_engine_estado().connect() as conn:
        return pd.read_sql(stmt, conn, params=params)


@st.cache_data(ttl=config.TTL_AO_VIVO, show_spinner=False)
def listar_usuarios() -> pd.DataFrame:
    df = _df(select(usuario).order_by(usuario.c.papel, usuario.c.nome))
    # Loja vazia tem que chegar como None: NaN conta como "verdadeiro" num if.
    # (df.where(..., None) não serve: o pandas põe NaN de volta.)
    df["cd_loja"] = pd.Series([v if isinstance(v, str) and v else None for v in df["cd_loja"]],
                              index=df.index, dtype=object)
    return df


@st.cache_data(ttl=config.TTL_AO_VIVO, show_spinner=False)
def listar_pedidos_gr() -> pd.DataFrame:
    return _df(select(pedido_gr).order_by(pedido_gr.c.id))


@st.cache_data(ttl=config.TTL_AO_VIVO, show_spinner=False)
def listar_confirmacoes() -> pd.DataFrame:
    df = _df(select(confirmacao_manual).order_by(confirmacao_manual.c.pedido_id, confirmacao_manual.c.confirmado_em))
    if not df.empty:
        df["confirmado_em"] = pd.to_datetime(df["confirmado_em"])
    return df


@st.cache_data(ttl=config.TTL_AO_VIVO, show_spinner=False)
def listar_itens_emitidos() -> pd.DataFrame:
    """Itens de todos os pedidos já emitidos (ciclo_item com pedido_id preenchido) —
    os produtos já estavam no ciclo_item, não duplicamos numa tabela própria."""
    return _df(select(ciclo_item).where(ciclo_item.c.pedido_id.isnot(None)))




@st.cache_data(ttl=config.TTL_AO_VIVO, show_spinner=False)
def listar_parametros() -> pd.DataFrame:
    df = _df(select(parametro_operacao))
    if not df.empty:
        df["atualizado_em"] = pd.to_datetime(df["atualizado_em"])
    return df


@st.cache_data(ttl=config.TTL_AO_VIVO, show_spinner=False)
def listar_pedidos_removidos() -> pd.DataFrame:
    df = _df(select(pedido_removido).order_by(pedido_removido.c.removido_em.desc()))
    if not df.empty:
        df["removido_em"] = pd.to_datetime(df["removido_em"])
    return df


def _parametro(chave: str) -> str | None:
    p = listar_parametros()
    achado = p.loc[p["chave"] == chave, "valor"] if not p.empty else []
    return None if len(achado) == 0 else str(achado.iloc[0])


def data_inicio_operacao() -> date | None:
    """Primeiro dia da operação oficial, gravado pela Gestão. Pedido emitido antes dele não entra no acompanhamento,
    no vínculo nem no histórico. None = a Gestão ainda não definiu: nada é escondido."""
    valor = _parametro(PARAM_DATA_INICIO)
    try:
        return date.fromisoformat(valor) if valor else None
    except ValueError:
        return None


def _numero_parametro(chave: str, padrao: float, minimo: float, maximo: float) -> float:
    valor = _parametro(chave)
    try:
        v = float(valor) if valor is not None else padrao
    except ValueError:
        return padrao
    return v if minimo <= v <= maximo else padrao


def parametros_regra() -> Parametros:
    """Os parâmetros da regra em vigor: o que a Gestão gravou na Visão de gestão ou, sem gravação (ou valor inválido), os padrões
    de config.py."""
    return Parametros(
        nivel_servico=_numero_parametro(PARAM_NIVEL_SERVICO, config.NIVEL_SERVICO_PADRAO, 0.5, 0.999),
        cobertura_dias=int(_numero_parametro(PARAM_COBERTURA, config.COBERTURA_DIAS_PADRAO, 0, 365)),
        valor_minimo=_numero_parametro(PARAM_VALOR_MINIMO, config.VALOR_MINIMO_PADRAO, 0, 100000),
    )


def lista_origem(cd_loja: str) -> str | None:
    """De onde veio a fila do último ciclo aberto na loja, como a Gestão gravou ao abrir (a identificação daquele momento e os
    parâmetros da regra). None se nunca foi gravado."""
    return _parametro(f"{PARAM_LISTA_ORIGEM}_{cd_loja}")


def ciclo_aberto(cd_loja: str) -> dict | None:
    with get_engine_estado().connect() as conn:
        row = conn.execute(
            select(ciclo).where(ciclo.c.cd_loja == cd_loja, ciclo.c.encerrado_em.is_(None))
            .order_by(ciclo.c.id.desc())
        ).mappings().first()
    if row is None:
        return None
    dados = dict(row)
    dados["pessoas"] = json.loads(dados["pessoas"])
    return dados


def itens_do_ciclo(ciclo_id: int) -> pd.DataFrame:
    return _df(select(ciclo_item).where(ciclo_item.c.ciclo_id == ciclo_id).order_by(ciclo_item.c.posicao))


# ============================================================
# GRAVAÇÕES
# ============================================================

class CicloJaAberto(Exception):
    """Já existe um ciclo aberto para a loja (outra sessão abriu primeiro)."""


class DiaJaGerado(Exception):
    """O dia do ciclo já foi gerado (ou o ciclo foi encerrado) por outra sessão: quem chegou depois não gera de novo."""


class PedidoJaVinculado(Exception):
    """O pedido do ERP (loja, número e série) já está vinculado a um lote: outra sessão vinculou primeiro."""


class ItensJaVinculados(Exception):
    """Algum item do lote já está vinculado a outro pedido (outra sessão vinculou primeiro): vincular de novo o sobrescreveria."""


def _e_chave_do_pedido_repetida(erro: IntegrityError) -> bool:
    """A violação é a da chave única (loja, número, série) do pedido do ERP, e não outra restrição qualquer."""
    texto = str(getattr(erro, "orig", erro)).lower()
    return "uq_pedido_dl_chave_erp" in texto or "pedido_gr.cd_loja" in texto


def codigo_do_lote(pedido_id: int, quando: datetime, prefixo: str = "GR") -> str:
    """GR-AAMM-NNNN, com o id do vínculo: único por construção. O número antigo era "contagem + 1", que
    duas sessões simultâneas calculavam igual (e a segunda levava erro de chave duplicada)."""
    return f"{prefixo}-{quando:%y%m}-{pedido_id:04d}"


def criar_vinculo(num_pedido: str, serie: str, cd_loja: str, login: str, ciclo_id: int | None = None,
                  ciclo_item_ids: list[int] | None = None, exemplo: bool = False,
                  quando: datetime | None = None) -> int:
    """Liga um lote nosso ao pedido de devolução real do ERP. É a única gravação
    manual do começo do fluxo — o resto (separação, conferência da loja) é lido
    de lá, não registrado aqui.

    Duas sessões ao mesmo tempo não se atropelam: o mesmo pedido do ERP levanta `PedidoJaVinculado` e item que já tem pedido levanta
    `ItensJaVinculados`, nos dois casos sem gravar nada (a transação volta inteira)."""
    agora = quando or config.agora()
    with get_engine_estado().begin() as conn:
        # O código leva o id, que só existe depois do insert: entra um provisório e vira o definitivo
        # na mesma transação.
        try:
            pid = conn.execute(insert(pedido_gr).values(
                codigo=f"TMP-{uuid.uuid4().hex[:20]}", cd_loja=cd_loja, num_pedido=num_pedido, serie=serie, tipo="giro_reverso",
                ciclo_id=ciclo_id, criado_em=agora, criado_por=login, exemplo=exemplo,
            )).inserted_primary_key[0]
        except IntegrityError as erro:
            if _e_chave_do_pedido_repetida(erro):
                raise PedidoJaVinculado(f"O pedido {num_pedido}/{serie} já está vinculado a um lote.") from erro
            raise
        conn.execute(update(pedido_gr).where(pedido_gr.c.id == pid).values(codigo=codigo_do_lote(pid, agora)))
        if ciclo_item_ids:
            ids = sorted(set(ciclo_item_ids))
            ligados = conn.execute(update(ciclo_item).where(ciclo_item.c.id.in_(ids), ciclo_item.c.pedido_id.is_(None))
                                   .values(pedido_id=pid)).rowcount
            if ligados != len(ids):  # algum item já tinha pedido: sobrescrevê-lo soltaria o pedido anterior
                raise ItensJaVinculados("Algum item deste lote já está vinculado a outro pedido.")
    limpar_cache_estado()
    return pid


def confirmar_etapa(pedido_id: int, etapa: str, login: str, quando: datetime | None = None,
                    detalhe: str | None = None, repetir: bool = False) -> bool:
    """Confirma manualmente uma das etapas que o ERP ainda não expõe (saída da loja, chegada no CD,
    conferência Qualidade, reintegração ao estoque). Devolve False, sem gravar, se a etapa já tinha sido confirmada
    para o pedido (outra sessão, ou duplo clique). `repetir=True` grava mesmo assim: só a ferramenta de
    gestão da demonstração, que move o pedido livremente (inclusive de volta) e por isso precisa de um novo
    evento da mesma etapa."""
    valores = {"pedido_id": pedido_id, "etapa": etapa, "confirmado_em": quando or config.agora(),
               "confirmado_por": login, "detalhe": detalhe}
    with get_engine_estado().begin() as conn:
        if repetir:
            conn.execute(insert(confirmacao_manual).values(**valores))
            gravou = True
        else:
            # INSERT ... SELECT ... WHERE NOT EXISTS: checar e gravar são um comando só, então duas
            # sessões confirmando a mesma etapa ao mesmo tempo gravam uma vez só.
            colunas = [confirmacao_manual.c[nome] for nome in valores]
            ja_existe = exists().where(and_(confirmacao_manual.c.pedido_id == pedido_id,
                                            confirmacao_manual.c.etapa == etapa))
            novo = select(*[literal(valores[c.name], c.type) for c in colunas]).where(~ja_existe)
            gravou = conn.execute(insert(confirmacao_manual).from_select([c.name for c in colunas], novo)).rowcount > 0
    limpar_cache_estado()
    return gravou


def abrir_ciclo(cd_loja: str, login: str, pessoas: list[str], pedidos_por_pessoa: int,
                skus_por_pedido: int, fila: list[dict]) -> int:
    """Abre o ciclo da loja. Um por vez: se outra sessão abriu primeiro, levanta CicloJaAberto."""
    try:
        with get_engine_estado().begin() as conn:
            cid = conn.execute(insert(ciclo).values(
                cd_loja=cd_loja, aberto_em=config.agora(), aberto_por=login,
                pessoas=json.dumps(pessoas, ensure_ascii=False), pedidos_por_pessoa=pedidos_por_pessoa,
                skus_por_pedido=skus_por_pedido, dia_atual=1,
            )).inserted_primary_key[0]
            if fila:
                conn.execute(insert(ciclo_item), [{**it, "ciclo_id": cid, "posicao": i, "situacao": "fila"}
                                                  for i, it in enumerate(fila)])
    except IntegrityError as erro:
        if ciclo_aberto(cd_loja) is None:
            raise
        raise CicloJaAberto(f"Já existe um ciclo aberto para a loja {cd_loja}.") from erro
    return cid


def aplicar_dia(ciclo_id: int, dia: int, lote: list[dict], invalidados: list[int],
                nova_fila_ids: list[int]) -> None:
    """lote: [{id, responsavel, grupo, estoque, alvo, qtde_fica, qtde_retirar}] · nova_fila_ids: a fila na
    nova ordem (sobra válida primeiro, depois o que não foi candidato).

    Só mexe na posição de quem mudou de lugar. Antes eram um UPDATE por item da fila inteira (~6 mil
    comandos por dia: minutos num Postgres remoto); como a sobra já vem na frente do resto, normalmente
    nenhuma posição muda.

    Trava contra duas sessões da Gestão: só gera o dia quem ainda vê `dia` como o dia atual de um ciclo aberto. A primeira avança o
    dia; a segunda não acha o ciclo naquele dia e levanta `DiaJaGerado`, sem gravar nada (a transação volta inteira)."""
    with get_engine_estado().begin() as conn:
        avancou = conn.execute(update(ciclo).where(ciclo.c.id == ciclo_id, ciclo.c.dia_atual == dia, ciclo.c.encerrado_em.is_(None))
                               .values(dia_atual=dia + 1)).rowcount
        if avancou != 1:
            raise DiaJaGerado(f"O dia {dia} deste ciclo já foi gerado, ou o ciclo foi encerrado, por outra sessão.")
        for it in lote:
            valores = {"situacao": "lote", "dia": dia, "responsavel": it["responsavel"], "grupo": it["grupo"],
                       "estoque": it.get("estoque"), "alvo": it.get("alvo")}
            for campo in ("qtde_fica", "qtde_retirar"):
                if campo in it:
                    valores[campo] = it[campo]
            conn.execute(update(ciclo_item).where(ciclo_item.c.id == it["id"]).values(**valores))
        if invalidados:
            conn.execute(update(ciclo_item).where(ciclo_item.c.id.in_(invalidados))
                         .values(situacao="invalidado", dia=dia))
        _reposicionar_fila(conn, ciclo_id, nova_fila_ids)


def _reposicionar_fila(conn, ciclo_id: int, ids_na_ordem: list[int]) -> None:
    """Deixa a fila na ordem de `ids_na_ordem` mexendo no mínimo de linhas: a cauda que já está em ordem
    crescente de posição fica como está; o que vem antes dela ganha posições logo abaixo da primeira."""
    if not ids_na_ordem:
        return
    atual = dict(conn.execute(select(ciclo_item.c.id, ciclo_item.c.posicao).where(
        ciclo_item.c.ciclo_id == ciclo_id, ciclo_item.c.situacao == "fila")).all())
    inicio_da_cauda = len(ids_na_ordem)
    while inicio_da_cauda > 0:
        item = ids_na_ordem[inicio_da_cauda - 1]
        if item not in atual or atual[item] is None:
            break
        if inicio_da_cauda < len(ids_na_ordem) and atual[item] >= atual[ids_na_ordem[inicio_da_cauda]]:
            break
        inicio_da_cauda -= 1
    if inicio_da_cauda == 0:
        return  # já está na ordem certa
    cabeca = ids_na_ordem[:inicio_da_cauda]
    base = atual[ids_na_ordem[inicio_da_cauda]] if inicio_da_cauda < len(ids_na_ordem) else len(cabeca)
    for i, item_id in enumerate(cabeca):
        conn.execute(update(ciclo_item).where(ciclo_item.c.id == item_id).values(posicao=base - len(cabeca) + i))


def encerrar_ciclo(ciclo_id: int) -> None:
    with get_engine_estado().begin() as conn:
        conn.execute(update(ciclo).where(ciclo.c.id == ciclo_id).values(encerrado_em=config.agora()))




def gravar_lista_origem(cd_loja: str, texto: str, login: str) -> None:
    """Guarda de onde veio a fila do ciclo que a Gestão acabou de abrir na loja (lido por `lista_origem`)."""
    gravar_parametro(f"{PARAM_LISTA_ORIGEM}_{cd_loja}", texto, login)


def gravar_parametro(chave: str, valor: str, login: str) -> None:
    """Grava (ou troca) um parâmetro da operação. Atualiza primeiro e insere se não existia; se outra sessão inseriu
    no meio, atualiza de novo."""
    valores = {"valor": valor, "atualizado_em": config.agora(), "atualizado_por": login}
    with get_engine_estado().begin() as conn:
        achou = conn.execute(update(parametro_operacao).where(parametro_operacao.c.chave == chave)
                             .values(**valores)).rowcount
    if not achou:
        try:
            with get_engine_estado().begin() as conn:
                conn.execute(insert(parametro_operacao).values(chave=chave, **valores))
        except IntegrityError:  # a outra sessão chegou primeiro
            with get_engine_estado().begin() as conn:
                conn.execute(update(parametro_operacao).where(parametro_operacao.c.chave == chave).values(**valores))
    limpar_cache_estado()


def remover_vinculo(pedido_id: int, login: str, motivo: str, quando: datetime | None = None) -> bool:
    """Tira um pedido do acompanhamento (vinculado por engano, ou errado). O vínculo e as confirmações manuais saem das
    telas e ficam em pedido_removido, com quem tirou, quando e o motivo; os itens do lote voltam para "a emitir" no
    Ciclo. Não toca o ERP. Devolve False se o pedido não existe mais (outra sessão removeu primeiro). O motivo é
    obrigatório."""
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValueError("Informe o motivo da remoção.")
    agora = quando or config.agora()
    with get_engine_estado().begin() as conn:
        v = conn.execute(select(pedido_gr).where(pedido_gr.c.id == pedido_id)).mappings().first()
        if v is None:
            return False
        etapas = [{"etapa": r["etapa"], "em": r["confirmado_em"].isoformat(), "por": r["confirmado_por"],
                   "detalhe": r["detalhe"]}
                  for r in conn.execute(select(confirmacao_manual).where(confirmacao_manual.c.pedido_id == pedido_id)
                                        .order_by(confirmacao_manual.c.confirmado_em)).mappings()]
        conn.execute(insert(pedido_removido).values(
            codigo=v["codigo"], cd_loja=v["cd_loja"], num_pedido=v["num_pedido"], serie=v["serie"], ciclo_id=v["ciclo_id"],
            criado_em=v["criado_em"], criado_por=v["criado_por"], etapas=json.dumps(etapas, ensure_ascii=False),
            removido_em=agora, removido_por=login, motivo=motivo))
        conn.execute(update(ciclo_item).where(ciclo_item.c.pedido_id == pedido_id).values(pedido_id=None))
        conn.execute(confirmacao_manual.delete().where(confirmacao_manual.c.pedido_id == pedido_id))
        conn.execute(pedido_gr.delete().where(pedido_gr.c.id == pedido_id))
    limpar_cache_estado()
    return True


# ============================================================
# INICIALIZAÇÃO E DADOS DE EXEMPLO
# ============================================================

# Um acesso único por setor — sem nome de pessoa (o login corporativo de
# verdade, com identidade real, entra depois, via TI). "nome" é sempre igual a
# config.PAPEIS[papel]. "Logística da loja" não tem cd_loja fixo aqui: é uma
# credencial só, compartilhada pelas 5 lojas — quem entra escolhe em qual loja
# está atuando na hora (app.py::tela_escolher_loja), não fica gravado no cadastro.
USUARIOS_EXEMPLO = [
    ("compras", "Compras", "compras", None),
    ("log_loja", "Logística da loja", "log_loja", None),
    ("log_cd", "Logística do CD", "log_cd", None),
    ("qualidade", "Qualidade", "qualidade", None),
    ("gestao", "Gestão", "gestor", None),
]

# Vínculos de exemplo: pedidos de devolução que o ERP sintético também conhece (data/erp.py::semear_pedidos), em etapas
# diferentes, para o quadro de acompanhamento nascer com movimento.
# (cd_loja, num_pedido, serie, horas desde a emissão, horas desde a separação ou None, [(etapa confirmada à mão, horas atrás), ...])
_VINCULOS_EXEMPLO = [
    ("06", "500101", "DV", 10, None, []),                                         # Emitido
    ("03", "500102", "DV", 70, None, []),                                         # Emitido (>2 dias — fica em atenção)
    ("03", "500103", "DV", 80, None, []),                                         # Emitido (>2 dias — fica em atenção)
    ("04", "500104", "DV", 50, 30, []),                                           # Separação (vem do ERP)
    ("04", "500105", "DV", 60, 40, [("saida_loja", 20)]),                         # Saída da loja
    ("07", "500106", "DV", 150, 120, [("saida_loja", 90), ("chegada_cd", 30)]),   # Chegada no CD (>2 dias na saída — em atenção)
    ("07", "500107", "DV", 260, 240, [("saida_loja", 200), ("chegada_cd", 150),
                                      ("conferencia", 100), ("reintegracao", 60)]),  # Concluído
]

def _semear_exemplo(conn) -> None:
    agora = config.agora()
    lojas_ativas = set(config.LOJAS.values())
    # Etapa → login: com um acesso único por setor, o nome do papel já é o login.
    login_por_etapa = {"saida_loja": "log_loja", "chegada_cd": "log_cd", "conferencia": "qualidade",
                       "reintegracao": "qualidade"}
    n = 0

    def _vincular(loja: str, num_pedido: str, serie: str) -> int:
        nonlocal n
        n += 1
        return conn.execute(insert(pedido_gr).values(
            codigo=f"EX-{7000 + n}", cd_loja=loja, num_pedido=num_pedido, serie=serie, tipo="giro_reverso",
            criado_em=agora, criado_por="compras", exemplo=True,
        )).inserted_primary_key[0]

    ativos = [v for v in _VINCULOS_EXEMPLO if v[0] in lojas_ativas]
    antigos = select(erp.erp_pedido.c.id).where(erp.erp_pedido.c.num_pedido.in_([v[1] for v in ativos]))
    conn.execute(erp.erp_pedido_item.delete().where(erp.erp_pedido_item.c.pedido_id.in_(antigos)))
    conn.execute(erp.erp_pedido.delete().where(erp.erp_pedido.c.num_pedido.in_([v[1] for v in ativos])))
    erp.semear_pedidos(conn, [(loja, nota, serie, h_em, h_sep) for loja, nota, serie, h_em, h_sep, _ in ativos])
    for loja, num_pedido, serie, _, _, confirmacoes in ativos:
        pid = _vincular(loja, num_pedido, serie)
        for etapa, horas_atras in confirmacoes:
            conn.execute(insert(confirmacao_manual).values(
                pedido_id=pid, etapa=etapa, confirmado_em=agora - timedelta(hours=horas_atras),
                confirmado_por=login_por_etapa[etapa],
            ))


# Engines cujo schema já foi conferido nesta execução (a conferência não precisa rodar a cada rerun).
_SCHEMA_CONFERIDO = weakref.WeakSet()


def _migrar_colunas_novas(engine) -> None:
    """create_all não mexe em tabela que já existe: um banco criado antes de uma coluna nova (como
    ciclo_item.descricao) quebra a leitura e a gravação. Acrescenta as colunas que faltam — só
    aditivo, nada é apagado nem alterado. Coluna obrigatória e sem valor padrão no banco não dá
    para acrescentar sozinha: nesse caso o erro é explícito."""
    insp = inspect(engine)
    prep = engine.dialect.identifier_preparer
    for tabela in metadata.sorted_tables:
        existentes = {c["name"] for c in insp.get_columns(tabela.name)}
        for col in tabela.columns:
            if col.name in existentes:
                continue
            if not col.nullable and col.server_default is None:
                raise RuntimeError(
                    f"Banco de acompanhamento desatualizado: falta a coluna obrigatória {tabela.name}.{col.name}. "
                    "Recrie o banco (dados_locais/) ou acrescente a coluna à mão.")
            ddl = (f"ALTER TABLE {prep.format_table(tabela)} ADD COLUMN {prep.format_column(col)} "
                   f"{col.type.compile(dialect=engine.dialect)}")
            try:
                with engine.begin() as conn:
                    conn.exec_driver_sql(ddl)
            except DBAPIError:  # outra sessão acrescentou a coluna primeiro
                if col.name not in {c["name"] for c in inspect(engine).get_columns(tabela.name)}:
                    raise


def _criar_indices_novos(engine) -> None:
    """create_all só cria o índice junto com a tabela: um banco que já existia não ganha o índice novo
    (como o do ciclo aberto por loja). Se os dados antigos já violam a unicidade, segue sem a trava
    em vez de travar o app."""
    for tabela in metadata.sorted_tables:
        for indice in tabela.indexes:
            try:
                indice.create(engine, checkfirst=True)
            except DBAPIError:
                pass


# As sessões do Streamlit são threads do mesmo processo. No SQLite cada CREATE TABLE do create_all confirma
# sozinho, então duas sessões subindo juntas num banco novo se atropelavam tabela a tabela (e semeavam
# usuários duas vezes). A trava serializa a inicialização dentro do processo.
_TRAVA_INICIALIZAR = threading.Lock()


def inicializar() -> None:
    """Cria as tabelas e, se estiverem vazias, os usuários e vínculos de exemplo."""
    engine = get_engine_estado()
    with _TRAVA_INICIALIZAR:
        try:
            _inicializar(engine)
        except DBAPIError:
            # Outro PROCESSO subindo junto num banco novo (a trava só vale dentro do processo): na
            # segunda tentativa já está tudo pronto.
            _inicializar(engine)


def _inicializar(engine) -> None:
    metadata.create_all(engine)
    erp.criar_tabelas(engine)
    if engine not in _SCHEMA_CONFERIDO:
        _migrar_colunas_novas(engine)
        _criar_indices_novos(engine)
        _SCHEMA_CONFERIDO.add(engine)
    with engine.begin() as conn:
        if not conn.execute(select(func.count()).select_from(tipo_pedido)).scalar():
            conn.execute(insert(tipo_pedido), [{"chave": k, "nome": v} for k, v in config.TIPOS_PEDIDO.items()])
        if not conn.execute(select(func.count()).select_from(usuario)).scalar():
            conn.execute(insert(usuario), [{"login": lg, "nome": nm, "papel": pp, "cd_loja": lj}
                                           for lg, nm, pp, lj in USUARIOS_EXEMPLO])
        if config.DADOS_REAIS:
            # Teste com dados reais (piloto ou Rede): nenhum pedido de exemplo. Só entra pedido vinculado
            # de verdade no Ciclo — e limpa os de exemplo que uma execução anterior possa ter deixado.
            _remover_exemplos(conn)
        elif not conn.execute(select(func.count()).select_from(pedido_gr)).scalar():
            _semear_exemplo(conn)


def _remover_exemplos(conn) -> None:
    """Apaga os vínculos marcados como exemplo e as confirmações deles. Os de verdade ficam."""
    ids = [r[0] for r in conn.execute(select(pedido_gr.c.id).where(pedido_gr.c.exemplo.is_(True)))]
    if ids:
        conn.execute(confirmacao_manual.delete().where(confirmacao_manual.c.pedido_id.in_(ids)))
        conn.execute(pedido_gr.delete().where(pedido_gr.c.id.in_(ids)))


def recriar_exemplo() -> None:
    """Apaga só os vínculos de exemplo e cria de novo (confirmações relativas a agora)."""
    engine = get_engine_estado()
    with engine.begin() as conn:
        _remover_exemplos(conn)
        _semear_exemplo(conn)
    limpar_cache_estado()
