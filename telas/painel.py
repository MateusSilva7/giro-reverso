"""Visão de gestão — indicadores do ciclo de devolução."""
import pandas as pd
import polars as pl
import streamlit as st

import config
from data import estado
from data.erp import ler_status_para_vinculos
from domain import acompanhamento as ac
from ui.cache import ERRO_BANCO, mensagem_sem_banco
from ui.sessao import nomes_por_login, pode_remover_pedido, usuario_atual
from ui.tema import aviso_esboco, num_br, tabela_br

u = usuario_atual()
st.title("Visão de gestão", anchor=False)
st.markdown(
    '<p class="gr-lead">Como está o ciclo de devolução ao CD: quanto tempo leva, onde trava e o que está parado.</p>',
    unsafe_allow_html=True,
)

# ── Parâmetros da operação (antes de ler o ERP: dá para ajustar mesmo com o ERP fora) ──
st.subheader("Parâmetros da operação", anchor=False)
inicio_atual = estado.data_inicio_operacao()
regra_atual = estado.parametros_regra()
parametros = estado.listar_parametros()
nomes_login = nomes_por_login()


def _ultima_alteracao(chave: str) -> str:
    achado = parametros[parametros["chave"] == chave] if not parametros.empty else parametros
    if achado.empty:
        return "ainda não gravado (vale o padrão)"
    r = achado.iloc[0]
    return (f'gravado por {nomes_login.get(r["atualizado_por"], r["atualizado_por"])} '
            f'em {r["atualizado_em"]:%d/%m/%Y %H:%M}')



def _vinculos_com_problema(problemas: pd.DataFrame) -> None:
    """Vínculos cujo pedido está cancelado (ou não foi achado) no ERP: somem do quadro e da Fila e prendem os itens do lote. A Gestão
    remove o vínculo (os itens voltam para "Pedidos a emitir") e informa o pedido certo no Ciclo."""
    st.subheader("Vínculos com problema no ERP", anchor=False)
    if problemas.empty:
        st.caption("Nenhum vínculo com pedido cancelado ou não achado no ERP.")
        return
    emitidos = estado.listar_itens_emitidos()
    st.warning(f"{num_br(len(problemas), 0)} vínculo(s) apontam para um pedido cancelado ou que o ERP não achou. Esses pedidos não "
               "aparecem no Acompanhamento nem na Fila, e os itens do lote ficam presos a eles. Remova o vínculo para os itens voltarem "
               "a \"Pedidos a emitir\" e informe o pedido certo no Ciclo diário.")
    for r in problemas.itertuples():
        presos = int((emitidos["pedido_id"] == r.id).sum()) if not emitidos.empty else 0
        with st.container(border=True):
            st.markdown(f"**{r.codigo}** · pedido {r.num_pedido}/{r.serie} · {config.NOME_LOJA.get(r.cd_loja, r.cd_loja)} · "
                        f"{r.problema} · {num_br(presos, 0)} item(ns) presos")
            if not pode_remover_pedido(u["papel"]):
                continue
            with st.form(f"vinculo-problema-{r.id}"):
                motivo = st.text_input("Motivo da remoção", value=f"Pedido {r.problema}", key=f"vinculo-problema-motivo-{r.id}")
                if st.form_submit_button("Remover o vínculo"):
                    if not motivo.strip():
                        st.error("Informe o motivo da remoção.")
                    else:
                        removeu = estado.remover_vinculo(int(r.id), u["login"], motivo)
                        st.toast(f"Vínculo {r.codigo} removido: os itens voltaram para \"Pedidos a emitir\"." if removeu
                                 else "Outra sessão já removeu este vínculo.", icon=None if removeu else "⚠️")
                        st.rerun()


p1, p2 = st.columns([1, 2])
with p1, st.container(border=True):
    with st.form("param-inicio"):
        inicio = st.date_input("A operação começa em", value=inicio_atual or config.agora().date(), format="DD/MM/YYYY",
                               help="Pedido emitido antes desta data não entra no acompanhamento, no vínculo nem no "
                                    "histórico (só a Gestão vê o histórico completo).")
        salvar_inicio = st.form_submit_button("Salvar a data de início")
    st.caption(f"Hoje: {inicio_atual:%d/%m/%Y}" if inicio_atual else "Hoje: sem data definida — o app mostra todos os "
               "pedidos já vinculados.")
    st.caption(_ultima_alteracao(estado.PARAM_DATA_INICIO))
with p2, st.container(border=True):
    with st.form("param-regra"):
        r1, r2, r3 = st.columns(3)
        nivel = r1.slider("Nível de serviço (%)", 80, 99, int(round(regra_atual.nivel_servico * 100)), step=1,
                          help="Probabilidade de a loja não ficar sem o item até a próxima reposição. Mais alto = mais estoque de "
                               "segurança na loja e menos devolução.")
        cobertura = r2.number_input("Cobertura-alvo (dias)", 0, 180, int(regra_atual.cobertura_dias), step=5,
                                    help="Dias de venda que a loja mantém além do prazo de reposição.")
        minimo = r3.number_input("Valor mínimo por item (R$)", 0.0, 1000.0, float(regra_atual.valor_minimo), step=5.0, format="%.0f",
                                 help="Item com valor a retirar abaixo disso fica na loja: não compensa o manuseio.")
        salvar_regra = st.form_submit_button("Salvar os parâmetros da regra")
    st.caption(f"Hoje: nível de serviço {num_br(regra_atual.nivel_servico * 100, 0)}% · cobertura de {regra_atual.cobertura_dias} dias · "
               f"mínimo de R$ {num_br(regra_atual.valor_minimo, 2)}")
    st.caption(_ultima_alteracao(estado.PARAM_NIVEL_SERVICO) + ". Veja o efeito de cada combinação em Sensibilidade da regra.")
if salvar_inicio:
    estado.gravar_parametro(estado.PARAM_DATA_INICIO, inicio.isoformat(), u["login"])
    st.toast(f"A operação passa a começar em {inicio:%d/%m/%Y}.")
    st.rerun()
if salvar_regra:
    estado.gravar_parametro(estado.PARAM_NIVEL_SERVICO, str(nivel / 100), u["login"])
    estado.gravar_parametro(estado.PARAM_COBERTURA, str(int(cobertura)), u["login"])
    estado.gravar_parametro(estado.PARAM_VALOR_MINIMO, str(float(minimo)), u["login"])
    st.toast("Parâmetros da regra gravados: a lista foi recalculada.")
    st.rerun()

agora = config.agora()
vinculos = estado.listar_pedidos_gr()
try:
    status = ler_status_para_vinculos(vinculos)
except ERRO_BANCO as e:
    mensagem_sem_banco(e)
    st.stop()
# Só o que foi emitido a partir do início da operação.
vinculos, status, confirmacoes = ac.desde_o_inicio(vinculos, status, estado.listar_confirmacoes(), inicio_atual)
est = ac.estado_pedidos(vinculos, status, confirmacoes, agora)
dl = est.filter((pl.col("tipo") == "giro_reverso") & pl.col("cd_loja").is_in(list(config.LOJAS.values()))) if est.height else est
k = ac.kpis(dl)
# Pedido cancelado (ou não achado) depois do vínculo: não tem evento, some do quadro e dos números acima, e os itens ficam presos
problemas = ac.vinculos_com_problema(vinculos[(vinculos["tipo"] == "giro_reverso") & vinculos["cd_loja"].isin(set(config.LOJAS.values()))], status)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Em andamento", num_br(k["andamento"], 0))
m2.metric("Em atenção", num_br(k["atencao"], 0), help=f"Parados há mais de {config.LIMITE_ALERTA_HORAS // 24} dias na mesma etapa")
m3.metric("Concluídos", num_br(k["concluidos"], 0))
if k["ciclo_medio_dias"] is None:
    m4.metric("Ciclo médio", "—")
else:
    delta = k["ciclo_medio_dias"] - config.META_CICLO_DIAS
    sinal = "+" if delta >= 0 else "-"  # hífen ASCII: é por ele que o st.metric reconhece o delta negativo (verde, com inverse)
    m4.metric("Ciclo médio", f'{num_br(k["ciclo_medio_dias"])} dias',
              delta=f"{sinal}{num_br(abs(delta))} dias vs. meta", delta_color="inverse",
              help=f"Meta: até {num_br(config.META_CICLO_DIAS)} dias")
st.caption(f"Meta: ciclo completo, da emissão à reintegração no CD, em até {num_br(config.META_CICLO_DIAS, 0)} dias.")
if not problemas.empty:  # com problema, o bloco sobe para o topo: é o que a Gestão precisa resolver primeiro
    _vinculos_com_problema(problemas)

# ── Onde trava: tempo médio em cada etapa ────────────────────────────────────
st.subheader("Tempo médio em cada etapa", anchor=False)
st.caption("Quanto tempo, em média, o pedido ficou numa etapa até a seguinte ser registrada. O maior é o gargalo.")
vinculos_dl = vinculos[vinculos["tipo"] == "giro_reverso"]
tpe = ac.tempo_por_etapa(vinculos_dl, status, confirmacoes)
if tpe.height:
    nomes = {e["chave"]: e["nome"] for e in config.ETAPAS}
    papeis = {e["chave"]: config.PAPEIS[e["papel"]] for e in config.ETAPAS}
    tabela = tpe.with_columns(
        pl.col("etapa").replace_strict(nomes).alias("Etapa"),
        pl.col("etapa").replace_strict(papeis).alias("Quem registra a etapa"),
        (pl.col("media_h") / 24).round(1).alias("Dias (média)"),
        pl.col("media_h").map_elements(ac.fmt_duracao, return_dtype=pl.Utf8).alias("Tempo médio"),
        pl.col("passagens").alias("Pedidos medidos"),
    ).select("Etapa", "Quem registra a etapa", "Tempo médio", "Dias (média)", "Pedidos medidos")
    maximo = float(tabela["Dias (média)"].max() or 1)
    tabela_br(tabela.to_pandas(), hide_index=True, width="stretch", column_config={
        "Dias (média)": st.column_config.ProgressColumn(format="localized", min_value=0, max_value=maximo),
    })
else:
    st.caption("Ainda sem passagens de etapa registradas.")

# ── Por loja ─────────────────────────────────────────────────────────────────
if len(config.LOJAS) > 1:
    st.subheader("Por loja", anchor=False)
if dl.height and len(config.LOJAS) > 1:
    por_loja = (
        dl.group_by("cd_loja")
        .agg(
            (~pl.col("concluido")).sum().alias("Em andamento"),
            pl.col("em_atencao").sum().alias("Em atenção"),
            pl.col("concluido").sum().alias("Concluídos"),
            (pl.col("duracao_ciclo_h").mean() / 24).round(1).alias("Ciclo médio (dias)"),
        )
        .with_columns(pl.col("cd_loja").replace_strict(config.NOME_LOJA, default="?").alias("Loja"))
        .sort("cd_loja")
        .select("Loja", "Em andamento", "Em atenção", "Concluídos", "Ciclo médio (dias)")
    )
    # sem pedido concluído a média é nula: sai "—" (o st.dataframe mostrava "None")
    tabela = por_loja.to_pandas()
    tabela["Ciclo médio (dias)"] = [num_br(v, 1) for v in tabela["Ciclo médio (dias)"]]
    tabela_br(tabela, hide_index=True, width="stretch")

# ── Pedidos em atenção ───────────────────────────────────────────────────────
st.subheader("Pedidos em atenção", anchor=False)
atencao = dl.filter(pl.col("em_atencao")).sort("horas_parado", descending=True) if dl.height else dl
if atencao.height == 0:
    st.caption("Nenhum pedido parado acima do limite.")
else:
    linhas = []
    for r in atencao.iter_rows(named=True):
        prox = ac.proxima_etapa(r["ordem"])
        linhas.append({"Pedido": r["codigo"], "Loja": config.NOME_LOJA.get(r["cd_loja"]),
                       "Parado em": config.ETAPAS[r["ordem"]]["nome"],
                       "Há": ac.fmt_duracao(r["horas_parado"]),
                       "Quem precisa agir": config.PAPEIS[prox["papel"]] if prox else "—"})
    tabela_br(pl.DataFrame(linhas).to_pandas(), hide_index=True, width="stretch")
    escolha = st.selectbox("Abrir no acompanhamento", [r["Pedido"] for r in linhas])
    if st.button("Abrir detalhe"):
        st.session_state["pedido_sel"] = atencao.filter(pl.col("codigo") == escolha)["id"][0]
        st.switch_page("telas/acompanhamento.py")

if problemas.empty:  # sem problema, só uma linha avisando que a conferência existe
    _vinculos_com_problema(problemas)

# ── Pedidos que a Gestão tirou do acompanhamento ─────────────────────────────
removidos = estado.listar_pedidos_removidos()
with st.expander(f"Pedidos removidos do acompanhamento ({len(removidos)})"):
    if removidos.empty:
        st.caption("Nenhum pedido removido. Para tirar um, abra o detalhe dele no Acompanhamento.")
    else:
        tabela_br(pd.DataFrame({
            "Pedido": removidos["num_pedido"] + " / " + removidos["serie"], "Lote": removidos["codigo"],
            "Loja": removidos["cd_loja"].map(lambda c: config.NOME_LOJA.get(c, c)),
            "Removido em": removidos["removido_em"],
            "Por": removidos["removido_por"].map(lambda x: nomes_login.get(x, x)), "Motivo": removidos["motivo"]}),
            hide_index=True, width="stretch",
            column_config={"Removido em": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm")})
        st.caption("O pedido continua no ERP; só saiu do acompanhamento. Para vincular o certo, use o Ciclo diário.")

# ── Dados de exemplo ─────────────────────────────────────────────────────────
if config.MODO_ESBOCO and not config.DADOS_REAIS:
    st.markdown("---")
    aviso_esboco("os indicadores acima usam os pedidos de exemplo (marcados como “exemplo”) mais os que "
                 "forem emitidos pelo ciclo. As datas dos exemplos são relativas ao momento em que foram criados.")
    if st.button("Recriar pedidos de exemplo com datas de agora"):
        estado.recriar_exemplo()
        st.rerun()
