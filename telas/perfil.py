"""Perfil da loja — a loja em números: estoque, CMV, cobertura e onde está concentrado o que volta ao CD."""
import numpy as np
import pandas as pd
import streamlit as st

from domain.regra import EXCLUSOES, metricas, na_lista
from ui.cache import ERRO_BANCO, cmv_mensal, hora_carga, identificacao_loja, mensagem_sem_banco
from ui.sessao import escolher_loja
from ui.tema import grafico_barras, num_br, tabela_br

COR = "#006F73"
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
FAIXAS = [(0, 30, "Até 30 dias"), (30, 60, "30–60 dias"), (60, 90, "60–90 dias"), (90, 180, "90–180 dias"),
          (180, 365, "180–365 dias"), (365, np.inf, "Mais de 1 ano"), (np.inf, np.inf, "Sem venda prevista")]

st.title("Perfil da loja", anchor=False)
st.markdown('<p class="gr-lead">A loja em números: estoque, cobertura, o peso do que volta ao CD e onde ele está concentrado.</p>',
            unsafe_allow_html=True)
nome_loja, cd = escolher_loja()
try:
    df = identificacao_loja(cd)
except ERRO_BANCO as e:
    mensagem_sem_banco(e)
    st.stop()
if df.empty:
    st.info("Sem dados desta loja no ERP agora.")
    st.stop()

m = metricas(df)
lista = na_lista(df)


def pct(a: float, b: float) -> str:
    return f"{num_br(a / b * 100, 1)}%" if b else "—"


def mil(v: float) -> str:
    return f"R$ {num_br(v / 1e6, 2)} mi" if v >= 1e6 else f"R$ {num_br(v / 1e3, 0)} mil"


c = st.columns(5)
c[0].metric("SKUs na loja", num_br(m["skus"], 0))
c[1].metric("Estoque (peças)", num_br(m["pecas"], 0))
c[2].metric("Valor em estoque", mil(m["valor_estoque"]), help="Estoque × custo unitário")
c[3].metric("CMV do mês", mil(m["cmv_mes"]))
c[4].metric("Dias de estoque", num_br(m["dias_estoque"], 0) if m["dias_estoque"] is not None else "—")
c = st.columns(5)
c[0].metric("SKUs a retirar", num_br(m["skus_lista"], 0), delta=f'{pct(m["skus_lista"], m["skus"])} dos SKUs', delta_color="off")
c[1].metric("Peças a retirar", num_br(m["pecas_retirar"], 0), delta=f'{pct(m["pecas_retirar"], m["pecas"])} das peças',
            delta_color="off")
c[2].metric("Valor a retirar", mil(m["valor_retirar"]), delta=f'{pct(m["valor_retirar"], m["valor_estoque"])} do estoque',
            delta_color="off")
c[3].metric("Itens segurados por exclusão", num_br(int(df["decisao"].isin(EXCLUSOES[1:]).sum()), 0),
            help="Lançamento, sazonal, em promoção ou abaixo do valor mínimo: teriam o que devolver, mas a regra segura")
c[4].metric("Dias depois da devolução", num_br(m["dias_depois"], 0) if m["dias_depois"] is not None else "—",
            delta=f'de {num_br(m["dias_estoque"], 0)} dias' if m["dias_estoque"] is not None else None, delta_color="off")

st.subheader("CMV mensal", anchor=False)
st.caption("Custo da mercadoria vendida por mês. O último mês é parcial.")
serie = cmv_mensal(cd)
serie = serie.assign(Mês=pd.to_datetime(serie["mes"]).map(lambda d: f"{MESES[d.month - 1]}/{d:%y}"), CMV=serie["cmv"].astype(float))
grafico_barras(serie, "Mês", "CMV", cor=COR, sort=False, altura=240, casas=2)

a, b = st.columns(2)
with a:
    st.subheader("Valor em estoque por cobertura", anchor=False)
    st.caption("Cobertura = estoque ÷ venda diária prevista. Acima de 90 dias é onde mora o capital parado.")
    cob = df["cobertura_atual_dias"].to_numpy()
    rotulo = np.select([(cob >= lo) & (cob < hi) for lo, hi, _ in FAIXAS[:-1]] + [np.isinf(cob)], [f[2] for f in FAIXAS],
                       default=FAIXAS[-1][2])
    por_faixa = (df.assign(Faixa=rotulo).groupby("Faixa")["valor_estoque"].sum().reindex([f[2] for f in FAIXAS]).fillna(0)
                 .reset_index().rename(columns={"valor_estoque": "Valor em estoque (R$)"}))
    grafico_barras(por_faixa, "Faixa", "Valor em estoque (R$)", cor=COR, horizontal=True, sort=False, altura=240, casas=2)
with b:
    st.subheader("Valor a retirar por motivo", anchor=False)
    por_motivo = lista.groupby("motivo").agg(Valor=("valor_retirar", "sum"), SKUs=("sku", "nunique")).reset_index().rename(
        columns={"motivo": "Motivo", "Valor": "Valor a retirar (R$)"})
    grafico_barras(por_motivo, "Motivo", "Valor a retirar (R$)", cor=COR, horizontal=True, altura=200, casas=2)
    tabela_br(por_motivo, hide_index=True, width="stretch",
              column_config={"Valor a retirar (R$)": st.column_config.NumberColumn(format="R$ %.2f")})


def agregar(coluna: str) -> pd.DataFrame:
    todos = df.groupby(coluna).agg(skus=("sku", "nunique"), estoque=("estoque", "sum"), valor=("valor_estoque", "sum"))
    sai = lista.groupby(coluna).agg(skus_lista=("sku", "nunique"), valor_retirar=("valor_retirar", "sum"))
    t = todos.join(sai, how="left").fillna(0).sort_values("valor_retirar", ascending=False)
    t["pct"] = (t["valor_retirar"] / t["valor"] * 100).round(1)
    return t


FMT = {"skus": st.column_config.NumberColumn("SKUs", format="%d"),
       "estoque": st.column_config.NumberColumn("Estoque", format="%d"),
       "valor": st.column_config.NumberColumn("Valor em estoque", format="R$ %.2f"),
       "skus_lista": st.column_config.NumberColumn("SKUs a retirar", format="%d"),
       "valor_retirar": st.column_config.NumberColumn("Valor a retirar", format="R$ %.2f"),
       "pct": st.column_config.NumberColumn("% do valor", format="%.1f%%")}
st.subheader("Onde está concentrado", anchor=False)
st.caption("Categorias e fabricantes com mais valor a retirar, e quanto isso pesa no estoque de cada um.")
t1, t2 = st.tabs(["Por categoria", "Por fabricante"])
with t1:
    tabela_br(agregar("categoria").rename_axis("Categoria").reset_index(), hide_index=True, width="stretch", column_config=FMT)
with t2:
    tabela_br(agregar("fabricante").rename_axis("Fabricante").reset_index(), hide_index=True, width="stretch", column_config=FMT)
st.caption(f"Dados do ERP lidos às {hora_carga():%d/%m/%Y %H:%M}.")
