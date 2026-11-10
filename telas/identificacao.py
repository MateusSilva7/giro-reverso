"""Identificação por loja — o resultado da regra em toda a rede, com filtros e a ficha de cada item."""
import pandas as pd
import streamlit as st

import config
from domain import origem as og
from domain.lista import COLUNAS, tabela_exibicao
from domain.regra import MOTIVOS, metricas, na_lista
from ui.cache import (
    ERRO_BANCO,
    cadastro_produtos,
    hora_carga,
    identificacao_loja,
    identificacao_por_loja,
    mensagem_sem_banco,
    outras_lojas,
    recarregar_identificacao,
)
from ui.ficha import mostrar_ficha
from ui.sessao import pode_ver_valor, usuario_atual
from ui.tema import num_br, tabela_br

u = usuario_atual()
ver_valor = pode_ver_valor(u["papel"])
st.title("Identificação por loja", anchor=False)
st.markdown('<p class="gr-lead">O que a regra manda devolver em cada loja: estoque acima do alvo, calculado pela previsão de demanda, '
            'pelo estoque de segurança e pelo prazo de reposição.</p>', unsafe_allow_html=True)


def _dias(v) -> str:
    return "—" if v is None else num_br(v, 0)


abas = st.tabs(["Resumo da rede"] + list(config.LOJAS))

with abas[0]:
    try:
        por_loja = identificacao_por_loja()
    except ERRO_BANCO as e:
        mensagem_sem_banco(e)
        st.stop()
    linhas = []
    for nome, cd in config.LOJAS.items():
        m = metricas(por_loja.get(cd, pd.DataFrame()))
        linhas.append({"Loja": nome, "SKUs": m["skus"], "SKUs a retirar": m["skus_lista"], "Peças a retirar": m["pecas_retirar"],
                       "Valor a retirar (R$)": m["valor_retirar"], "Dias de estoque": m["dias_estoque"],
                       "Dias depois da devolução": m["dias_depois"], "_valor_est": m["valor_estoque"], "_cmv": m["cmv_mes"]})
    res = pd.DataFrame(linhas)
    cmv, valor_est, valor_ret = res["_cmv"].sum(), res["_valor_est"].sum(), res["Valor a retirar (R$)"].sum()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("SKUs a retirar", num_br(res["SKUs a retirar"].sum(), 0))
    c2.metric("Peças a retirar", num_br(res["Peças a retirar"].sum(), 0))
    c3.metric("Dias de estoque — rede", _dias(valor_est / cmv * 30 if cmv else None))
    c4.metric("Depois da devolução", _dias((valor_est - valor_ret) / cmv * 30 if cmv else None),
              help="Dias de estoque se tudo o que está na lista voltar ao CD")
    tabela = res.drop(columns=["_valor_est", "_cmv"])
    if not ver_valor:
        tabela = tabela.drop(columns=["Valor a retirar (R$)"])
    tabela_br(tabela, hide_index=True, width="stretch", column_config={
        "Valor a retirar (R$)": st.column_config.NumberColumn(format="R$ %.2f"),
        "Dias de estoque": st.column_config.NumberColumn(format="%.0f"),
        "Dias depois da devolução": st.column_config.NumberColumn(format="%.0f")})
    st.caption(f"Dias de estoque = valor em estoque ÷ CMV do mês × 30. Dados do ERP lidos às {hora_carga():%d/%m/%Y %H:%M}.")
    if st.button("Atualizar dados do ERP"):
        recarregar_identificacao()
        st.rerun()

for i, (nome, cd) in enumerate(config.LOJAS.items()):
    with abas[i + 1]:
        try:
            df = identificacao_loja(cd)
        except ERRO_BANCO as e:
            mensagem_sem_banco(e)
            continue
        lista = na_lista(df)
        f1, f2 = st.columns([3, 2])
        busca = f1.text_input("Buscar por SKU, referência, descrição, aplicação ou fabricante", key=f"b_{cd}",
                              placeholder="Ex.: amortecedor hatch").strip().upper()
        motivo = f2.selectbox("Motivo", ["Todos", *MOTIVOS], key=f"m_{cd}")
        g1, g2, g3 = st.columns(3)
        categoria = g1.selectbox("Categoria", ["Todas"] + sorted(lista["categoria"].dropna().unique()), key=f"c_{cd}")
        fabricante = g2.selectbox("Fabricante", ["Todos"] + sorted(lista["fabricante"].dropna().unique()), key=f"f_{cd}")
        origens = g3.multiselect("Origem", og.opcoes([lista]), placeholder="Todas", key=f"o_{cd}", help=og.AJUDA)
        if busca:
            alvo = (lista["sku"] + " " + lista["ref_fab"].fillna("") + " " + lista["descricao"].fillna("") + " "
                    + lista["aplicacao"].fillna("") + " " + lista["fabricante"].fillna("")).str.upper()
            for palavra in busca.split():
                lista = lista[alvo.loc[lista.index].str.contains(palavra, regex=False)]
        if motivo != "Todos":
            lista = lista[lista["motivo"] == motivo]
        if categoria != "Todas":
            lista = lista[lista["categoria"] == categoria]
        if fabricante != "Todos":
            lista = lista[lista["fabricante"] == fabricante]
        lista = og.filtrar(lista, origens).sort_values("valor_retirar", ascending=False).reset_index(drop=True)

        chave_tab = f"tab_{cd}"
        sel = st.session_state.get(chave_tab)
        linhas_sel = (sel.get("selection", {}).get("rows", []) if isinstance(sel, dict) else
                      list(getattr(getattr(sel, "selection", None), "rows", []) or []))
        if linhas_sel and linhas_sel[0] < len(lista):
            item = lista.iloc[linhas_sel[0]]
            with st.container(border=True):
                cod = (str(item["sku"]),)
                cad = cadastro_produtos(cod)
                mostrar_ficha(item, cad.iloc[0] if len(cad) else None, outras_lojas(cod), ver_valor)
        else:
            st.caption("Clique numa linha da tabela para abrir a ficha do item, com a conta da regra e o histórico de vendas.")

        view = tabela_exibicao(lista) if len(lista) else pd.DataFrame(columns=list(COLUNAS.values()))
        if not ver_valor:
            view = view.drop(columns=["Valor a retirar (R$)"], errors="ignore")
        tabela_br(view, hide_index=True, width="stretch", height=460, on_select="rerun", selection_mode="single-row",
                  key=chave_tab, cru=("SKU", "Ref. fabricante", "Endereço"),
                  column_config={"Valor a retirar (R$)": st.column_config.NumberColumn(format="R$ %.2f")})
        r1, r2, r3 = st.columns(3)
        r1.metric("Itens exibidos", num_br(len(lista), 0))
        r2.metric("Peças a retirar", num_br(lista["qtde_retirar"].sum(), 0))
        if ver_valor:
            r3.metric("Valor a retirar", "R$ " + num_br(lista["valor_retirar"].sum(), 2))
        st.download_button("Baixar CSV", lambda v=view: v.to_csv(index=False).encode("utf-8-sig"), f"giro_reverso_{cd}.csv",
                           "text/csv", key=f"dl_{cd}")
