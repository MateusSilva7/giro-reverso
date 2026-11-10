"""Lista diária — o que volta ao CD em cada loja, por motivo; a Gestão monta o lote e o Compras emite os pedidos pelo Ciclo diário."""
import pandas as pd
import streamlit as st

import config
from data import estado
from domain import lista as ld
from domain import origem as og
from ui.cache import (
    ERRO_BANCO,
    hora_carga,
    identificacao_loja,
    mensagem_sem_banco,
    recarregar_identificacao,
)
from ui.sessao import escolher_loja, pode_ver_valor, usuario_atual
from ui.tema import num_br, tabela_br

u = usuario_atual()
ver_valor = pode_ver_valor(u["papel"])
st.title("Lista diária", anchor=False)
st.markdown('<p class="gr-lead">O que deve voltar ao CD em cada loja hoje: itens sem giro e estoque acima do alvo.</p>',
            unsafe_allow_html=True)

motivo = st.radio("Mostrar", ld.OPCOES, horizontal=True, key="lista_motivo")
if motivo in ld.EXPLICACAO:
    st.caption(ld.EXPLICACAO[motivo])

try:
    por_loja = {cd: ld.filtrar(identificacao_loja(cd), motivo) for cd in config.LOJAS.values()}
    carga = hora_carga()
except ERRO_BANCO as e:
    mensagem_sem_banco(e)
    st.stop()

origens = st.multiselect("Origem", og.opcoes(por_loja.values()), placeholder="Todas", help=og.AJUDA)
por_loja = {cd: og.filtrar(df, origens) for cd, df in por_loja.items()}

resumo = []
for nome, cd in config.LOJAS.items():
    r = ld.resumo(por_loja[cd])
    resumo.append({"Loja": nome, "Itens": r["produtos"], "Peças a retirar": r["pecas"], "Valor a retirar (R$)": r["valor"]})
tabela = pd.DataFrame(resumo)
if not ver_valor:
    tabela = tabela.drop(columns=["Valor a retirar (R$)"])
tabela_br(tabela, hide_index=True, width="stretch", column_config={"Valor a retirar (R$)": st.column_config.NumberColumn(format="R$ %.2f")})
p = estado.parametros_regra()
st.caption(f"Regra em vigor: nível de serviço {num_br(p.nivel_servico * 100, 0)}%, cobertura-alvo de {p.cobertura_dias} dias e valor "
           f"mínimo de R$ {num_br(p.valor_minimo, 2)} por item (a Gestão ajusta na Visão de gestão). "
           f"Dados do ERP lidos às {carga:%d/%m/%Y %H:%M}.")

st.markdown("#### Lista da loja")
nome_loja, cd = escolher_loja()
df = por_loja[cd]
view = ld.tabela_exibicao(df) if len(df) else pd.DataFrame(columns=list(ld.COLUNAS.values()))
if not ver_valor:
    view = view.drop(columns=["Valor a retirar (R$)"], errors="ignore")
tabela_br(view, hide_index=True, width="stretch", height=380, cru=("SKU", "Ref. fabricante", "Endereço"),
          column_config={"Valor a retirar (R$)": st.column_config.NumberColumn(format="R$ %.2f")})
if df.empty:
    st.caption("Nada na lista desta loja hoje.")
st.download_button("Baixar a lista (CSV)", lambda v=view: v.to_csv(index=False).encode("utf-8-sig"),
                   f"giro_reverso_{nome_loja.lower()}_{config.agora():%Y-%m-%d}.csv", "text/csv")

if u["papel"] in ("compras", "gestor") and st.button("Atualizar dados do ERP agora"):
    recarregar_identificacao()
    st.rerun()
