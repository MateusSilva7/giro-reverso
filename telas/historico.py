"""Histórico Giro Reverso — todos os pedidos de devolução da loja que o ERP marca como Giro Reverso."""
import pandas as pd
import streamlit as st

from data import estado
from domain.acompanhamento import fmt_duracao
from ui.cache import ERRO_BANCO, devolucoes_giro_reverso, devolucoes_por_tipo, mensagem_sem_banco
from ui.ficha import kv
from ui.sessao import escolher_loja, pode_ver_valor, usuario_atual
from ui.tema import aviso_esboco, esc, grafico_barras, num_br, tabela_br

COR = "#006F73"
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

st.title("Histórico Giro Reverso", anchor=False)
st.markdown('<p class="gr-lead">Todos os pedidos de devolução da loja ao CD que o ERP marca como Giro Reverso (pela '
            'observação do pedido): quanto foi devolvido, quando e quanto tempo a loja levou pra separar e conferir.</p>',
            unsafe_allow_html=True)
nome_loja, cd = escolher_loja()
papel = usuario_atual()["papel"]
ver_valor = pode_ver_valor(papel)  # valor, custo e preço (R$) dos pedidos
inicio = estado.data_inicio_operacao()  # a Gestão define na Visão de gestão; None = ainda não definida

try:
    with st.spinner("Lendo os pedidos de devolução no ERP..."):
        pedidos, itens = devolucoes_giro_reverso(cd)
        por_tipo = devolucoes_por_tipo(cd)
except ERRO_BANCO as e:
    mensagem_sem_banco(e)
    st.stop()
if pedidos.empty:
    st.info("Nenhum pedido de devolução Giro Reverso desta loja no ERP.")
    st.stop()

# Desde o início da operação. O histórico completo,
# e a visão ampla da loja por tipo de devolução, só para a Gestão. Sem data definida, mostra tudo como antes.
completo = inicio is None
if inicio is not None:
    if papel == "gestor":
        completo = st.toggle(f"Mostrar o histórico completo (e não só desde {inicio:%d/%m/%Y})", key="hist_completo")
    if not completo:
        pedidos = pedidos[pedidos["emissao"] >= pd.Timestamp(inicio)].reset_index(drop=True)
        itens = itens.merge(pedidos[["num_pedido", "serie"]], on=["num_pedido", "serie"])
        st.caption(f"Pedidos emitidos desde {inicio:%d/%m/%Y}, o início da operação.")
        if pedidos.empty:
            st.info(f"Nenhum pedido Giro Reverso emitido desde {inicio:%d/%m/%Y}.")
            st.stop()


def horas(a: pd.Series, b: pd.Series) -> pd.Series:
    return ((b - a).dt.total_seconds() / 3600).dropna()


def dur(h) -> str:
    return "—" if h is None or pd.isna(h) else fmt_duracao(float(h))


def txt(v) -> str:
    return "—" if v is None or (not isinstance(v, str) and pd.isna(v)) or v == "" else esc(v)


pecas_ped = itens.groupby(["num_pedido", "serie"])["qtde"].sum().rename("pecas")
n_itens = itens.groupby(["num_pedido", "serie"]).size().rename("n_itens")
pedidos = pedidos.merge(pecas_ped, on=["num_pedido", "serie"], how="left").merge(n_itens, on=["num_pedido", "serie"], how="left")
pedidos["pecas"] = pedidos["pecas"].fillna(0)
pedidos["n_itens"] = pedidos["n_itens"].fillna(0).astype(int)
pedidos["h_conf"] = (pedidos["fim_conferencia"] - pedidos["emissao"]).dt.total_seconds() / 3600
sem_conf = pedidos["fim_conferencia"].isna().sum()
total_dev = int(por_tipo["pedidos"].sum())

c = iter(st.columns(4 if ver_valor else 3))
next(c).metric("Pedidos Giro Reverso", num_br(len(pedidos), 0),
               delta=f"de {num_br(total_dev, 0)} devoluções da loja ao CD" if completo else None, delta_color="off")
if ver_valor:
    next(c).metric("Valor devolvido", f'R$ {num_br(pedidos["valor"].sum() / 1e3, 0)} mil',
                   delta=f'custo R$ {num_br(pedidos["custo"].sum() / 1e3, 0)} mil', delta_color="off")
next(c).metric("Itens / peças", f'{num_br(len(itens), 0)} / {num_br(itens["qtde"].sum(), 0)}',
               delta=f'{num_br(itens["sku"].nunique(), 0)} produtos distintos', delta_color="off")
next(c).metric("Sem conferência no ERP", num_br(sem_conf, 0), delta="emitidos e não conferidos", delta_color="off")

# ── Quando devolveu (série mensal contínua: meses sem pedido aparecem vazios) ─
st.subheader("Quando a loja devolveu", anchor=False)
st.caption("O processo acontece em ondas: meses sem nenhum pedido aparecem vazios.")
mes = pedidos["emissao"].dt.to_period("M")
todos_meses = pd.period_range(mes.min(), mes.max(), freq="M")
serie = pd.DataFrame({"mes": todos_meses}).assign(
    Mês=lambda x: x["mes"].map(lambda p: f"{MESES[p.month - 1]}/{str(p.year)[2:]}"),
    Pedidos=lambda x: x["mes"].map(mes.value_counts()).fillna(0),
    Valor=lambda x: x["mes"].map(pedidos.groupby(mes)["valor"].sum()).fillna(0))
a, b = st.columns(2) if ver_valor else (st.container(), None)
a.markdown("**Pedidos por mês**")
with a:
    grafico_barras(serie, "Mês", "Pedidos", cor=COR, sort=False)
if ver_valor:
    b.markdown("**Valor devolvido por mês (R$)**")
    with b:
        grafico_barras(serie.rename(columns={"Valor": "Valor (R$)"}), "Mês", "Valor (R$)", cor=COR, sort=False, casas=2)

# ── Tempos reais ─────────────────────────────────────────────────────────────
st.subheader("Quanto tempo a loja levou (datas do ERP)", anchor=False)
intervalos = [
    ("Emitido → início da separação", horas(pedidos["emissao"], pedidos["inicio_separacao"])),
    ("Emitido → conferência concluída", horas(pedidos["emissao"], pedidos["fim_conferencia"])),
]
tempos = [{"Intervalo": n, "Pedidos medidos": len(s), "Mediana": dur(s.median() if len(s) else None),
           "Média": dur(s.mean() if len(s) else None), "90% dos pedidos até": dur(s.quantile(.9) if len(s) else None)}
          for n, s in intervalos]
sep_min = horas(pedidos["inicio_separacao"], pedidos["fim_separacao"]) * 60
tempos.append({"Intervalo": "Tempo de separação (início → fim)", "Pedidos medidos": len(sep_min),
               "Mediana": f"{num_br(sep_min.median(), 1)} min" if len(sep_min) else "—",
               "Média": f"{num_br(sep_min.mean(), 1)} min" if len(sep_min) else "—",
               "90% dos pedidos até": f"{num_br(sep_min.quantile(.9), 1)} min" if len(sep_min) else "—"})
tabela_br(pd.DataFrame(tempos), hide_index=True, width="stretch")
st.caption("Só a emissão e a separação/conferência na loja têm data no ERP; as outras etapas são confirmadas no app.")

# ── Pedidos ──────────────────────────────────────────────────────────────────
st.subheader(f"Pedidos ({num_br(len(pedidos), 0)})", anchor=False)
st.caption("Mais recentes primeiro. Clique num pedido para ver itens e linha do tempo.")
recentes = pedidos.sort_values("emissao", ascending=False).reset_index(drop=True)
tabela = pd.DataFrame({
    "Pedido": recentes["num_pedido"] + " / " + recentes["serie"], "Emissão": recentes["emissao"],
    "Itens": recentes["n_itens"], "Peças": recentes["pecas"], "Valor": recentes["valor"], "Custo": recentes["custo"],
    "Volumes": recentes["volumes"], "Peso": recentes["peso"], "Separador": recentes["separado_por"],
    "Conferido em": recentes["fim_conferencia"], "Emissão → conferência": recentes["h_conf"].map(dur),
    "NF": recentes["nf"], "Observação": recentes["observa"]})
cfg_tabela = {"Emissão": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm"),
              "Conferido em": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm")}
if ver_valor:
    cfg_tabela |= {"Valor": st.column_config.NumberColumn(format="R$ %.2f"),
                   "Custo": st.column_config.NumberColumn(format="R$ %.2f")}
else:
    tabela = tabela.drop(columns=["Valor", "Custo"])
evento = tabela_br(
    tabela, hide_index=True, width="stretch", height=420, on_select="rerun", selection_mode="single-row", key="hist_tab",
    column_config=cfg_tabela, cru=("NF", "Separador"))
linhas = evento.selection.rows if evento and evento.selection else []
if linhas:
    r = recentes.iloc[linhas[0]]
    with st.container(border=True):
        st.subheader(f'Pedido {r["num_pedido"]} / {r["serie"]}', anchor=False)
        pares = [("Volumes", "—" if pd.isna(r["volumes"]) else num_br(r["volumes"], 0)),
                 ("Peso", "—" if pd.isna(r["peso"]) else num_br(r["peso"], 1)),
                 ("Separado por", txt(r["separado_por"])), ("Nota fiscal", txt(r["nf"])),
                 ("Falta", txt(r["com_falta"])), ("Cancelada", txt(r["cancelada"]))]
        if ver_valor:
            pares = [("Valor", f'R$ {num_br(r["valor"], 2)}'), ("Custo", f'R$ {num_br(r["custo"], 2)}')] + pares
        kv(pares)
        st.caption(f'Observação no ERP: {r["observa"] if not pd.isna(r["observa"]) else "—"}')
        t1, t2 = st.columns([2, 3])
        eventos = [("Emitido", r["emissao"]), ("Início da separação", r["inicio_separacao"]),
                   ("Fim da separação", r["fim_separacao"]), ("Fim da conferência", r["fim_conferencia"])]
        with t1:
            tabela_br(pd.DataFrame([{"Etapa": e, "Quando": q} for e, q in eventos]), hide_index=True, width="stretch",
                      column_config={"Quando": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm")})
        its = itens[(itens["num_pedido"] == r["num_pedido"]) & (itens["serie"] == r["serie"])]
        cols_itens = ["sku", "descricao", "ref_fab", "fabricante", "qtde"] + (["preco", "custo"] if ver_valor else [])
        with t2:
            tabela_br(its[cols_itens].rename(columns={
                "sku": "SKU", "descricao": "Descrição", "ref_fab": "Ref. fabricante", "fabricante": "Fabricante",
                "qtde": "Qtde", "preco": "Preço", "custo": "Custo"}), hide_index=True, width="stretch",
                column_config={"Preço": st.column_config.NumberColumn(format="R$ %.2f"),
                               "Custo": st.column_config.NumberColumn(format="R$ %.2f")} if ver_valor else None)

# ── Todas as devoluções por tipo (visão ampla da loja: só quando o histórico está completo) ──
if completo:
    st.subheader("Todas as devoluções da loja ao CD, por tipo", anchor=False)
    st.caption(f"Agrupadas pela observação do pedido no ERP ({num_br(total_dev, 0)} pedidos). Os Giro Reverso estão destacados.")
    tipos = por_tipo.rename(columns={"observa": "Observação no pedido", "pedidos": "Pedidos", "primeiro": "Primeiro",
                                     "ultimo": "Último", "valor": "Valor (R$)"})
    if not ver_valor:
        tipos = tipos.drop(columns=["Valor (R$)"])
    tabela_br(
        tipos.style.apply(lambda s: ["background-color:#E6F2F2" if "GIRO REVERSO" in str(s["Observação no pedido"]).upper()
                                     else "" for _ in s], axis=1),
        hide_index=True, width="stretch", height=380,
        column_config={"Valor (R$)": st.column_config.NumberColumn(format="R$ %.2f")} if ver_valor else None)

aviso_esboco("“Giro Reverso” aqui significa que a observação do pedido de devolução contém essas palavras — é como o ERP "
             "permite separar esses pedidos das outras devoluções (erro de fábrica, avaria etc.).")
