"""Fila de separação da loja — pedidos de devolução emitidos aguardando separação."""
import polars as pl
import streamlit as st

import config
from data import estado
from data.erp import ler_status_para_vinculos
from domain import acompanhamento as ac
from ui.cache import ERRO_BANCO, mensagem_sem_banco
from ui.sessao import escolher_loja, usuario_atual
from ui.tema import aviso_esboco, badge_exemplo, badge_parado, esc, num_br

u = usuario_atual()
st.title("Fila de separação", anchor=False)
st.markdown(
    '<p class="gr-lead">Pedidos de devolução emitidos, aguardando serem separados no ERP. O número/série é o '
    'que você procura lá — assim que separar e conferir, some daqui sozinho.</p>',
    unsafe_allow_html=True,
)

if u["papel"] == "log_loja":
    cd = u["cd_loja"]
    st.markdown(f'<div class="gr-rotulo">Loja</div><div class="gr-valor">{esc(config.NOME_LOJA[cd])}</div>',
                unsafe_allow_html=True)
else:
    _, cd = escolher_loja()

agora = config.agora()
vinculos = estado.listar_pedidos_gr()
try:
    status = ler_status_para_vinculos(vinculos)
except ERRO_BANCO as e:
    mensagem_sem_banco(e)
    st.stop()
# Só o que foi emitido a partir do início da operação (a Gestão define a data na Visão de gestão).
vinculos, status, confirmacoes = ac.desde_o_inicio(vinculos, status, estado.listar_confirmacoes(),
                                                   estado.data_inicio_operacao())
est = ac.estado_pedidos(vinculos, status, confirmacoes, agora)
fila = est.filter((pl.col("cd_loja") == cd) & (pl.col("etapa") == "emitido")) if est.height else est

if fila.height == 0:
    st.info("Nenhum pedido de devolução aguardando separação nesta loja.")
    st.stop()

fila = fila.sort("horas_parado", descending=True)
c1, c2 = st.columns(2)
c1.metric("Pedidos na fila", num_br(fila.height, 0))
c2.metric("Em atenção", num_br(fila.filter(pl.col("em_atencao")).height, 0))

linhas = []
for r in fila.iter_rows(named=True):
    espera = badge_parado(ac.fmt_duracao(r["horas_parado"]), r["em_atencao"])
    exemplo = f" {badge_exemplo()}" if r["exemplo"] else ""
    linhas.append(
        f'<tr class="linha-dl"><td>{esc(r["num_pedido"])} / {esc(r["serie"])}{exemplo}</td>'
        f'<td>{esc(r["codigo"])}</td><td>{espera}</td></tr>'
    )
st.markdown(
    '<div class="gr-fila"><table><thead><tr><th>Nº pedido / Série (ERP)</th><th>Lote</th>'
    '<th>Emitido há</th></tr></thead><tbody>' + "".join(linhas) + "</tbody></table></div>",
    unsafe_allow_html=True,
)

if not config.DADOS_REAIS:  # só a demonstração usa pedidos antigos do ERP como se fossem o fluxo de verdade
    aviso_esboco("os vínculos aqui usam números reais de pedidos de devolução já existentes no ERP — é assim que "
                 "o app final vai se comportar, direto do banco.")
