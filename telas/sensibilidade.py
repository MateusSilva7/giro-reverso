"""Sensibilidade — quanto volta ao CD em cada combinação de nível de serviço × cobertura-alvo (só a Gestão)."""
from dataclasses import replace

import pandas as pd
import streamlit as st

import config
from data import estado
from domain import sensibilidade as sb
from ui.cache import ERRO_BANCO, base, mensagem_sem_banco
from ui.tema import grafico_linhas, num_br, tabela_br

st.title("Sensibilidade da regra", anchor=False)
st.markdown('<p class="gr-lead">O trade-off que a Gestão decide: um nível de serviço mais alto (menos ruptura na loja) ou uma '
            'cobertura maior seguram mais estoque na loja e devolvem menos capital ao CD.</p>', unsafe_allow_html=True)

p = estado.parametros_regra()
lojas = st.multiselect("Lojas", list(config.LOJAS), default=list(config.LOJAS))
try:
    dados = base()
except ERRO_BANCO as e:
    mensagem_sem_banco(e)
    st.stop()
dados = dados[dados["cd_loja"].isin([config.LOJAS[n] for n in lojas])]
if dados.empty:
    st.info("Escolha ao menos uma loja.")
    st.stop()


@st.cache_data(ttl=config.TTL, show_spinner="Rodando a regra em cada combinação...")
def _grade(lojas_escolhidas: tuple[str, ...], valor_minimo: float) -> pd.DataFrame:
    return sb.grade(dados, config.NIVEIS_SENSIBILIDADE, config.COBERTURAS_SENSIBILIDADE, replace(p, valor_minimo=valor_minimo))


g = _grade(tuple(lojas), p.valor_minimo)
atual = g[(g["nivel_servico"] == p.nivel_servico) & (g["cobertura_dias"] == p.cobertura_dias)]
if len(atual):
    a = atual.iloc[0]
    c1, c2, c3 = st.columns(3)
    c1.metric("Itens a retirar (regra em vigor)", num_br(a["itens"], 0))
    c2.metric("Valor a retirar", f'R$ {num_br(a["valor"] / 1e3, 0)} mil')
    c3.metric("Do valor em estoque", f'{num_br(a["pct_estoque"], 1)}%')
st.caption(f"Regra em vigor: nível de serviço {num_br(p.nivel_servico * 100, 0)}% e cobertura de {p.cobertura_dias} dias.")

st.subheader("Valor a retirar (R$ mil)", anchor=False)
mat = (sb.matriz(g, "valor") / 1e3).round(0)
mat.index = [f"{num_br(i * 100, 0)}%" for i in mat.index]
mat.columns = [f"{c} dias" for c in mat.columns]
tabela_br(mat.rename_axis("Nível de serviço").reset_index(), hide_index=True, width="stretch")

st.subheader("Itens a retirar", anchor=False)
mat_i = sb.matriz(g, "itens")
mat_i.index = [f"{num_br(i * 100, 0)}%" for i in mat_i.index]
mat_i.columns = [f"{c} dias" for c in mat_i.columns]
tabela_br(mat_i.rename_axis("Nível de serviço").reset_index(), hide_index=True, width="stretch")

st.subheader("Curva de trade-off", anchor=False)
st.caption("Cada linha é uma cobertura-alvo; andar para a direita é pedir mais disponibilidade na loja.")
curva = g.assign(**{"Nível de serviço (%)": g["nivel_servico"] * 100, "Valor a retirar (R$ mil)": g["valor"] / 1e3,
                    "Cobertura": g["cobertura_dias"].astype(str) + " dias"})
grafico_linhas(curva, "Nível de serviço (%)", "Valor a retirar (R$ mil)", "Cobertura",
               ordem_series=[f"{c} dias" for c in config.COBERTURAS_SENSIBILIDADE])
st.caption("A regra em vigor se ajusta na Visão de gestão. Esta tela só simula: nada é gravado.")
