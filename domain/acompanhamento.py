"""
Regras do acompanhamento por etapa. Funções puras (Polars/pandas), sem Streamlit.

Emitido e Separação (que já inclui a conferência da loja) vêm ao vivo do ERP —
sem histórico próprio, é só o status atual do pedido lá. Saída da loja, chegada
no CD, conferência Qualidade e reintegração ao estoque ainda não têm campo confirmado no
ERP, então vêm de confirmação manual (data/estado.py::confirmacao_manual),
guardada como um histórico que só cresce — igual ao restante do modelo.

_eventos_efetivos() une as duas fontes numa mesma forma (pedido_id, etapa,
ocorrido_em) para que "etapa atual = último evento" e "tempo parado = agora
menos esse evento" funcionem do mesmo jeito nos dois casos.
"""
from datetime import datetime

import numpy as np
import pandas as pd
import polars as pl

from config import ETAPA_FINAL, ETAPAS, LIMITE_ALERTA_HORAS, ORDEM_ETAPA

_ORDEM = pl.DataFrame({"etapa": list(ORDEM_ETAPA), "ordem": list(ORDEM_ETAPA.values())})

# Etapas que ainda não têm campo confirmado no ERP — só essas aceitam
# confirmação manual. Emitido/Separação são só leitura (vêm de lá).
ETAPAS_MANUAIS = {"saida_loja", "chegada_cd", "conferencia", "reintegracao"}


_CHAVE_ERP = ["cd_loja", "num_pedido", "serie"]


def desde_o_inicio(vinculos: pd.DataFrame, status_erp: pd.DataFrame, confirmacoes: pd.DataFrame,
                   inicio) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Deixa só os pedidos emitidos a partir do início da operação (`inicio`, uma data). O que o ERP diz ter sido
    emitido antes sai dos três (vínculos, status e confirmações): é a base poluída de testes anteriores que a área de negócio
    mandou deixar de fora. `inicio` None não esconde nada; pedido sem data no ERP (não
    achado, ou cancelado e fora do status) também fica: quem decide o que fazer com ele é o resto do acompanhamento."""
    if inicio is None or vinculos.empty or status_erp.empty:
        return vinculos, status_erp, confirmacoes
    emissao = pd.to_datetime(status_erp["dt_emissao"])
    antigos = status_erp.loc[emissao < pd.Timestamp(inicio), _CHAVE_ERP].drop_duplicates().assign(_antigo=True)
    if antigos.empty:
        return vinculos, status_erp, confirmacoes
    vinculos = vinculos[vinculos.merge(antigos, on=_CHAVE_ERP, how="left")["_antigo"].isna().to_numpy()]
    status_erp = status_erp[status_erp.merge(antigos, on=_CHAVE_ERP, how="left")["_antigo"].isna().to_numpy()]
    if not confirmacoes.empty:
        confirmacoes = confirmacoes[confirmacoes["pedido_id"].isin(vinculos["id"])]
    return vinculos, status_erp, confirmacoes


def vinculos_com_problema(vinculos: pd.DataFrame, status_erp: pd.DataFrame) -> pd.DataFrame:
    """Vínculos reais cujo pedido, no ERP, está cancelado ou não foi achado.

    Um pedido assim some do quadro do Acompanhamento, da Fila e dos indicadores (`_eventos_efetivos` não tem evento para ele), mas os
    itens do lote continuam presos a ele e não voltam para "Pedidos a emitir". Esta lista é o que a Gestão precisa ver para remover o
    vínculo (`data/estado.py::remover_vinculo`). Pedido de exemplo fica de fora (não existe no ERP). `status_erp` é o que o ERP
    devolveu agora para estes vínculos; quem chama não passa um status vazio por falha de leitura (a tela para antes disso)."""
    colunas = ["id", "codigo", "cd_loja", "num_pedido", "serie", "criado_em", "criado_por", "problema"]
    if vinculos.empty:
        return pd.DataFrame(columns=colunas)
    reais = vinculos[~vinculos["exemplo"].fillna(False).astype(bool)] if "exemplo" in vinculos.columns else vinculos
    if reais.empty:
        return pd.DataFrame(columns=colunas)
    chaves = (status_erp[_CHAVE_ERP + ["cancelada"]].drop_duplicates(subset=_CHAVE_ERP) if not status_erp.empty
              else pd.DataFrame(columns=_CHAVE_ERP + ["cancelada"]))
    junto = reais.merge(chaves, on=_CHAVE_ERP, how="left", indicator=True)
    junto["problema"] = np.where(junto["cancelada"].eq("S"), "cancelado no ERP",
                                 np.where(junto["_merge"].eq("left_only"), "não achado no ERP", ""))
    return junto.loc[junto["problema"] != "", colunas].reset_index(drop=True)


def _eventos_efetivos(vinculos: pd.DataFrame, status_erp: pd.DataFrame, confirmacoes: pd.DataFrame) -> pd.DataFrame:
    """Emitido/Separação: um "evento" sintético por pedido, a partir do status
    atual do ERP (sem histórico — é sempre o estado de agora). Daí em diante,
    o histórico real de confirmacao_manual.

    "fonte" vem marcada na origem (não é recalculada depois pela etapa) — assim
    uma confirmação manual de "emitido"/"separacao" (a ferramenta de gestão do
    demonstração deixa fazer isso) continua rotulada "manual", nunca "erp"."""
    colunas = ["pedido_id", "etapa", "ocorrido_em", "quem", "fonte", "detalhe"]
    linhas = []
    if not vinculos.empty and not status_erp.empty:
        status = (status_erp[status_erp["cancelada"] != "S"]
                  .drop_duplicates(subset=["cd_loja", "num_pedido", "serie"])
                  .set_index(["cd_loja", "num_pedido", "serie"]))
        for v in vinculos.itertuples():
            chave = (v.cd_loja, v.num_pedido, v.serie)
            if chave not in status.index:
                continue
            s = status.loc[chave]
            linhas.append({"pedido_id": v.id, "etapa": "emitido", "ocorrido_em": s["dt_emissao"],
                           "quem": "ERP", "fonte": "erp", "detalhe": None})
            if s["separado"]:
                linhas.append({"pedido_id": v.id, "etapa": "separacao", "ocorrido_em": s["dt_conferencia"],
                               "quem": "ERP", "fonte": "erp", "detalhe": None})
    base = pd.DataFrame(linhas, columns=colunas)
    if not confirmacoes.empty:
        extra = confirmacoes[["pedido_id", "etapa", "confirmado_em", "confirmado_por"]].rename(
            columns={"confirmado_em": "ocorrido_em", "confirmado_por": "quem"})
        extra["fonte"] = "manual"
        extra["detalhe"] = confirmacoes["detalhe"] if "detalhe" in confirmacoes.columns else None
    else:
        extra = pd.DataFrame(columns=colunas)
    eventos = pd.concat([base, extra], ignore_index=True)
    if not eventos.empty:
        eventos["ocorrido_em"] = pd.to_datetime(eventos["ocorrido_em"])
    return eventos


def _eventos_pl(eventos: pd.DataFrame) -> pl.DataFrame:
    # Mesma precisão do "agora" do Python (µs), senão a subtração de datas falha
    return (
        pl.from_pandas(eventos[["pedido_id", "etapa", "ocorrido_em"]])
        .with_columns(pl.col("ocorrido_em").cast(pl.Datetime("us")))
        .join(_ORDEM, on="etapa", how="left")
    )


def fmt_duracao(horas: float | None) -> str:
    """0,6 h → "36 min" · 5,2 h → "5h" · 70 h → "2d 22h"."""
    if horas is None or pd.isna(horas):
        return "—"
    if horas < 1:
        return f"{max(int(round(horas * 60)), 1)} min"
    h = int(horas)
    return f"{h}h" if h < 24 else f"{h // 24}d {h % 24:02d}h"


def estado_pedidos(vinculos: pd.DataFrame, status_erp: pd.DataFrame, confirmacoes: pd.DataFrame,
                   agora: datetime) -> pl.DataFrame:
    """Uma linha por pedido com a situação atual."""
    colunas = ["id", "codigo", "cd_loja", "num_pedido", "serie", "tipo", "exemplo", "com_falta", "etapa", "ordem",
               "entrou_em", "inicio", "horas_parado", "concluido", "em_atencao", "duracao_ciclo_h"]
    if vinculos.empty:
        return pl.DataFrame(schema={c: pl.Utf8 for c in colunas})

    eventos = _eventos_efetivos(vinculos, status_erp, confirmacoes)
    if eventos.empty:
        return pl.DataFrame(schema={c: pl.Utf8 for c in colunas})

    ev = _eventos_pl(eventos)
    resumo = (
        ev.sort(["pedido_id", "ocorrido_em", "ordem"])
        .group_by("pedido_id")
        .agg(
            pl.col("etapa").last(),
            pl.col("ordem").last(),
            pl.col("ocorrido_em").last().alias("entrou_em"),
            pl.col("ocorrido_em").first().alias("inicio"),
        )
    )
    status_unico = (status_erp[["cd_loja", "num_pedido", "serie", "com_falta"]]
                   .drop_duplicates(subset=["cd_loja", "num_pedido", "serie"]) if not status_erp.empty
                   else pd.DataFrame(columns=["cd_loja", "num_pedido", "serie", "com_falta"]))
    info = vinculos[["id", "codigo", "cd_loja", "num_pedido", "serie", "tipo", "exemplo"]].merge(
        status_unico, on=["cd_loja", "num_pedido", "serie"], how="left",
    )
    ped = pl.from_pandas(info)
    df = ped.join(resumo, left_on="id", right_on="pedido_id", how="inner")

    agora_pl = pl.lit(agora)
    return df.with_columns(
        ((agora_pl - pl.col("entrou_em")).dt.total_seconds() / 3600).alias("horas_parado"),
        (pl.col("etapa") == ETAPA_FINAL).alias("concluido"),
    ).with_columns(
        (
            (pl.col("tipo") == "giro_reverso")
            & ~pl.col("concluido")
            & (pl.col("horas_parado") > LIMITE_ALERTA_HORAS)
        ).alias("em_atencao"),
        pl.when(pl.col("concluido"))
        .then((pl.col("entrou_em") - pl.col("inicio")).dt.total_seconds() / 3600)
        .otherwise(None)
        .alias("duracao_ciclo_h"),
    )


def proxima_etapa(ordem_atual: int) -> dict | None:
    return ETAPAS[ordem_atual + 1] if ordem_atual + 1 < len(ETAPAS) else None


def pode_registrar(ordem_atual: int, cd_loja_pedido: str, papel: str, cd_loja_usuario) -> tuple[bool, str]:
    """Quem pode confirmar a próxima etapa deste pedido. Emitido/Separação vêm do
    ERP — não têm confirmação manual. Das etapas seguintes, cada papel só
    confirma a que é dele; a logística da loja, só nos pedidos da própria loja.

    Esta função é o único ponto de controle do fluxo normal — mas não é o único
    caminho de escrita do app: a visão de gestão da demonstração tem um bypass próprio
    (ui/sessao.py::pode_agir_livre, usado por telas/acompanhamento.py) que ignora
    tudo isto de propósito, pra testar o fluxo inteiro. Ver esse ponto também ao
    auditar "quem pode fazer o quê"."""
    prox = proxima_etapa(ordem_atual)
    if prox is None:
        return False, "Pedido concluído."
    if prox["chave"] not in ETAPAS_MANUAIS:
        return False, f'"{prox["nome"]}" é lida automaticamente do ERP — não precisa confirmar aqui.'
    if papel != prox["papel"]:
        return False, f"Próxima etapa ({prox['nome']}) é confirmada por outro setor."
    if papel == "log_loja" and cd_loja_usuario != cd_loja_pedido:
        return False, "Este pedido é de outra loja."
    return True, ""


def tempo_por_etapa(vinculos: pd.DataFrame, status_erp: pd.DataFrame, confirmacoes: pd.DataFrame) -> pl.DataFrame:
    """Tempo médio que os pedidos ficaram EM cada etapa até a seguinte acontecer
    (só passagens já concluídas)."""
    eventos = _eventos_efetivos(vinculos, status_erp, confirmacoes)
    if eventos.empty:
        return pl.DataFrame({"etapa": [], "media_h": [], "passagens": []})
    ev = _eventos_pl(eventos)
    ev = ev.sort(["pedido_id", "ocorrido_em", "ordem"]).with_columns(
        pl.col("ocorrido_em").shift(-1).over("pedido_id").alias("saiu_em")
    )
    return (
        ev.drop_nulls("saiu_em")
        .with_columns(((pl.col("saiu_em") - pl.col("ocorrido_em")).dt.total_seconds() / 3600).alias("horas"))
        .group_by("etapa", "ordem")
        .agg(pl.col("horas").mean().alias("media_h"), pl.len().alias("passagens"))
        .sort("ordem")
    )


def historico_pedido(pedido_id: int, vinculos: pd.DataFrame, status_erp: pd.DataFrame,
                     confirmacoes: pd.DataFrame) -> pd.DataFrame:
    """O histórico de um pedido só, ordenado, com a fonte de cada linha — para a
    linha do tempo e a aba de histórico do detalhe. "fonte" já vem marcada por
    _eventos_efetivos (na origem, não recalculada aqui pela etapa)."""
    eventos = _eventos_efetivos(vinculos, status_erp, confirmacoes)
    return eventos[eventos["pedido_id"] == pedido_id].sort_values("ocorrido_em").copy()


def kpis(estado: pl.DataFrame) -> dict:
    dl = estado.filter(pl.col("tipo") == "giro_reverso") if estado.height else estado
    if dl.height == 0:
        return {"andamento": 0, "atencao": 0, "concluidos": 0, "ciclo_medio_dias": None}
    concl = dl.filter(pl.col("concluido"))
    media = concl["duracao_ciclo_h"].mean() if concl.height else None
    return {
        "andamento": dl.filter(~pl.col("concluido")).height,
        "atencao": dl.filter(pl.col("em_atencao")).height,
        "concluidos": concl.height,
        "ciclo_medio_dias": None if media is None else media / 24,
    }
