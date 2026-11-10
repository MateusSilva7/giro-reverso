"""Acompanhamento por etapa — quadro no padrão kanban + painel de detalhe."""
from datetime import datetime

import pandas as pd
import polars as pl
import streamlit as st

import config
from data import estado
from domain import acompanhamento as ac
from ui.cache import ERRO_BANCO, mensagem_sem_banco, pedido_devolucao, status_devolucoes
from ui.ficha import kv
from ui.kanban import quadro_kanban
from ui.sessao import (
    nomes_por_login,
    pode_agir_livre,
    pode_remover_pedido,
    pode_ver_valor,
    usuario_atual,
)
from ui.tema import (
    CSS,
    aviso_esboco,
    badge_exemplo,
    badge_parado,
    callout,
    esc,
    num_br,
    stepper_html,
    tabela_br,
)

u = usuario_atual()
# Só na demonstração: a visão de gestão pode mover qualquer pedido pra qualquer etapa, livre da
# ordem sequencial e do ERP, pra testar o fluxo inteiro. Some sozinho se MODO_ESBOCO virar False.
gestao_livre = pode_agir_livre(u["papel"])
ver_valor = pode_ver_valor(u["papel"])  # valor, custo e preço (R$) do pedido no ERP
st.title("Acompanhamento por etapa", anchor=False)
st.markdown(
    '<p class="gr-lead">Cada pedido de devolução do Giro Reverso, da emissão à reintegração ao estoque no CD. Emitido e '
    'Separação vêm direto do ERP; as demais etapas são confirmadas por aqui. '
    f'Parado há mais de {config.LIMITE_ALERTA_HORAS // 24} dias numa etapa fica em atenção.</p>',
    unsafe_allow_html=True,
)

# ── Filtros (fora do fragmento: mudam por clique, não pelo relógio) ─────────
f1, f2, f3 = st.columns([3, 2, 2])
if u["papel"] == "log_loja":
    lojas_sel = [u["cd_loja"]]
    f1.markdown(f'<div class="gr-rotulo">Loja</div><div class="gr-valor">{esc(config.NOME_LOJA[u["cd_loja"]])}</div>',
                unsafe_allow_html=True)
elif len(config.LOJAS) == 1:
    lojas_sel = list(config.LOJAS.values())
    f1.markdown(f'<div class="gr-rotulo">Loja</div><div class="gr-valor">{esc(next(iter(config.LOJAS)))}</div>',
                unsafe_allow_html=True)
else:
    nomes_lojas = f1.multiselect("Lojas", list(config.LOJAS), default=list(config.LOJAS), key="ac_lojas")
    lojas_sel = [config.LOJAS[n] for n in nomes_lojas]
so_atencao = f2.toggle("Só pedidos em atenção", key="ac_atencao")
busca = f3.text_input("Buscar pedido", placeholder="Lote, nº ou série ERP", key="ac_busca").strip().upper()


def _carregar_estado(agora: datetime):
    """Vínculos + status ao vivo do ERP + confirmações manuais — as três fontes
    que domain/acompanhamento.py::estado_pedidos une numa linha por pedido."""
    vinculos = estado.listar_pedidos_gr()
    status = status_devolucoes(vinculos)
    # Só o que foi emitido a partir do início da operação (a Gestão define a data na Visão de gestão).
    vinculos, status, confirmacoes = ac.desde_o_inicio(vinculos, status, estado.listar_confirmacoes(),
                                                       estado.data_inicio_operacao())
    return vinculos, status, confirmacoes, ac.estado_pedidos(vinculos, status, confirmacoes, agora)


def _estado_filtrado(agora: datetime):
    vinculos, status, confirmacoes, est = _carregar_estado(agora)
    if est.height:
        est = est.filter((pl.col("tipo") == "giro_reverso") & pl.col("cd_loja").is_in(lojas_sel))
        if so_atencao:
            est = est.filter(pl.col("em_atencao"))
        if busca:
            est = est.filter(
                pl.col("codigo").str.to_uppercase().str.contains(busca, literal=True)
                | pl.col("num_pedido").str.to_uppercase().str.contains(busca, literal=True)
                | pl.col("serie").str.to_uppercase().str.contains(busca, literal=True)
            )
    return vinculos, status, confirmacoes, est


@st.fragment(run_every=config.ATUALIZAR_QUADRO_S)
def quadro() -> None:
    agora = config.agora()
    try:
        _, _, _, est = _estado_filtrado(agora)
    except ERRO_BANCO as e:
        mensagem_sem_banco(e)
        return
    k = ac.kpis(est)
    itens = estado.listar_itens_emitidos()
    n_itens = itens.groupby("pedido_id").size().to_dict() if not itens.empty else {}

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Em andamento", num_br(k["andamento"], 0))
    m2.metric("Em atenção", num_br(k["atencao"], 0), help=f"Parados há mais de {config.LIMITE_ALERTA_HORAS // 24} dias na mesma etapa")
    m3.metric("Concluídos", num_br(k["concluidos"], 0))
    ciclo = "—" if k["ciclo_medio_dias"] is None else f'{num_br(k["ciclo_medio_dias"])} dias'
    m4.metric("Ciclo médio", ciclo, help=f"Pedidos concluídos. Meta: até {num_br(config.META_CICLO_DIAS)} dias (1 a 1,5 semana)")
    dica_arraste = "arraste um card pra qualquer etapa (ferramenta de gestão)" if gestao_livre \
        else "arraste um card para a próxima etapa"
    st.caption(f"Atualizado às {agora:%H:%M:%S} · o quadro se atualiza sozinho a cada {config.ATUALIZAR_QUADRO_S} s "
               f"· {dica_arraste}, ou clique em \"Abrir detalhe\"")

    colunas = _montar_colunas(est, n_itens)
    resultado = quadro_kanban(colunas, estilo=CSS, selecionado=st.session_state.get("pedido_sel"), key="kanban")
    _processar_acao(resultado, est)


def _montar_colunas(est: pl.DataFrame, n_itens: dict) -> list[dict]:
    colunas = []
    for i, etapa in enumerate(config.ETAPAS):
        cards = est.filter(pl.col("ordem") == i) if est.height else est
        if cards.height:
            cards = cards.sort(["em_atencao", "horas_parado"], descending=[True, True])
        limite = 6 if etapa["chave"] == config.ETAPA_FINAL else 50
        incluidos = cards.head(limite)
        dados_cards = [_card_dado(r, n_itens.get(r["id"], 0)) for r in incluidos.iter_rows(named=True)]
        nota_extra = f"e mais {cards.height - limite} concluídos" if cards.height > limite else None
        colunas.append({"nome": esc(etapa["nome"]), "cards": dados_cards, "nota_extra": nota_extra,
                        "total": cards.height})
    return colunas


def _card_dado(r: dict, n_itens: int) -> dict:
    status = "fim" if r["concluido"] else ("atencao" if r["em_atencao"] else "ok")
    if r["concluido"]:
        selo = badge_parado(f'ciclo {ac.fmt_duracao(r["duracao_ciclo_h"])}', False, concluido=True)
        rodape = "Concluído"
    else:
        selo = badge_parado(f'{ac.fmt_duracao(r["horas_parado"])} parado', r["em_atencao"])
        prox = ac.proxima_etapa(r["ordem"])
        rodape = f'Próx.: {config.PAPEIS[prox["papel"]]}'
    exemplo = f" {badge_exemplo()}" if r["exemplo"] else ""
    # Pedido sem lote vinculado no Ciclo (os de exemplo): os itens existem, mas só no ERP.
    itens_txt = f'{n_itens} {"item" if n_itens == 1 else "itens"}' if n_itens else "itens no ERP"
    # Nº/série é o que a Logística e a Qualidade buscam no ERP — em destaque.
    html = (
        f'<div class="gr-card-cod">{esc(r["num_pedido"])} / {esc(r["serie"])}{exemplo}</div>'
        f'<div class="gr-card-sub">{esc(config.NOME_LOJA.get(r["cd_loja"], r["cd_loja"]))} · lote {esc(r["codigo"])} · '
        f'{itens_txt}</div>'
        f'<div class="gr-card-foot">{selo}<span>{esc(rodape)}</span></div>'
    )
    return {"id": r["id"], "status": status, "html": html, "rotulo": f'{r["num_pedido"]}/{r["serie"]}'}


def _processar_acao(resultado: dict | None, est: pl.DataFrame) -> None:
    if not resultado:
        return
    seq = resultado.get("seq")
    if not seq or seq == st.session_state.get("kanban_seq"):
        return  # já processado — evita reprocessar no redesenho automático (30s)
    st.session_state["kanban_seq"] = seq

    linha = est.filter(pl.col("id") == resultado["pedido_id"])
    if linha.height == 0:
        return
    p = linha.row(0, named=True)

    if resultado["acao"] == "abrir":
        st.session_state["pedido_sel"] = p["id"]
        st.rerun()
        return

    if resultado["acao"] != "mover":
        return

    if gestao_livre:
        # Sem checar ordem sequencial, ERP ou dono da etapa — é a ferramenta de teste da demonstração.
        etapa_alvo = config.ETAPAS[resultado["para"]]["chave"]
        estado.confirmar_etapa(p["id"], etapa_alvo, u["login"], repetir=True)
        st.session_state["pedido_sel"] = p["id"]
        st.toast(f'[Gestão] {p["num_pedido"]}/{p["serie"]} movido para "{config.ETAPAS[resultado["para"]]["nome"]}".')
        st.rerun()
        return

    if p["concluido"]:
        st.toast(f'{p["num_pedido"]}/{p["serie"]} já está concluído.', icon="⚠️")
    elif resultado["para"] != p["ordem"] + 1:
        st.toast(f'Não dá para mover {p["num_pedido"]}/{p["serie"]} direto pra essa coluna — siga a ordem do processo.',
                 icon="⚠️")
    else:
        pode, motivo = ac.pode_registrar(p["ordem"], p["cd_loja"], u["papel"], u["cd_loja"])
        if pode:
            st.session_state["pedido_sel"] = p["id"]
            st.toast(f'Confirme "{ac.proxima_etapa(p["ordem"])["nome"]}" para {p["num_pedido"]}/{p["serie"]}.')
            st.rerun()
        else:
            st.toast(f'Não foi possível mover {p["num_pedido"]}/{p["serie"]}: {motivo}', icon="⚠️")


quadro()

# ============================================================
# PAINEL DE DETALHE
# ============================================================
sel = st.session_state.get("pedido_sel")
if sel is None:
    st.markdown(callout("Clique em <b>Abrir detalhe</b> num card para ver a linha do tempo e confirmar a próxima etapa."),
                unsafe_allow_html=True)
    st.stop()

agora = config.agora()
try:
    vinculos, status, confirmacoes, todos = _carregar_estado(agora)
except ERRO_BANCO:
    st.stop()  # o quadro acima já mostrou o aviso e se atualiza sozinho a cada 30 s
linha = todos.filter(pl.col("id") == sel)
if linha.height == 0:
    st.session_state.pop("pedido_sel", None)
    st.stop()
p = linha.row(0, named=True)
historico = ac.historico_pedido(sel, vinculos, status, confirmacoes)
itens = estado.listar_itens_emitidos()
itens = itens[itens["pedido_id"] == sel]
nomes = nomes_por_login()

st.markdown("---")
cab1, cab2 = st.columns([5, 1])
cab1.subheader(f'Pedido {p["num_pedido"]} / {p["serie"]}', anchor=False)
if cab2.button("Fechar detalhe"):
    st.session_state.pop("pedido_sel", None)
    st.rerun()

with st.container(border=True):
    d1, d2, d3, d4 = st.columns(4)
    d1.markdown(f'<div class="gr-rotulo">Lote</div><div class="gr-valor">{esc(p["codigo"])}'
                f'{" " + badge_exemplo() if p["exemplo"] else ""}</div>', unsafe_allow_html=True)
    d2.markdown(f'<div class="gr-rotulo">Loja</div><div class="gr-valor">{esc(config.NOME_LOJA.get(p["cd_loja"]))}</div>',
                unsafe_allow_html=True)
    d3.markdown(f'<div class="gr-rotulo">Nº / Série no ERP</div><div class="gr-valor">{esc(p["num_pedido"])} / {esc(p["serie"])}</div>',
                unsafe_allow_html=True)
    d4.markdown(f'<div class="gr-rotulo">Emitido em</div><div class="gr-valor">{p["inicio"]:%d/%m/%Y %H:%M}</div>',
                unsafe_allow_html=True)

    por_etapa = {e.etapa: (e.ocorrido_em, "ERP (automático)" if e.fonte == "erp" else nomes.get(e.quem, e.quem))
                for e in historico.itertuples()}
    st.markdown(stepper_html(p["ordem"], por_etapa), unsafe_allow_html=True)

    # O ERP marca falta no pedido quando o físico não bate com o sistema na separação.
    if p["com_falta"] not in (None, "", "N", "n", "0"):
        st.markdown(callout("O ERP marca falta neste pedido (sistema diz que tem, físico não bate).", "alerta"),
                    unsafe_allow_html=True)

    etapa_nome = config.ETAPAS[p["ordem"]]["nome"]
    prox = ac.proxima_etapa(p["ordem"])
    if p["concluido"]:
        st.markdown(callout(f'Concluído em {p["entrou_em"]:%d/%m %H:%M} — ciclo de <b>{ac.fmt_duracao(p["duracao_ciclo_h"])}</b> '
                            f'(meta: até {num_br(config.META_CICLO_DIAS)} dias).', "ok"), unsafe_allow_html=True)
    elif p["em_atencao"]:
        st.markdown(callout(f'Parado há <b>{ac.fmt_duracao(p["horas_parado"])}</b> em <b>{esc(etapa_nome)}</b> — acima do limite de '
                            f'{config.LIMITE_ALERTA_HORAS // 24} dias. Próxima etapa: {esc(prox["nome"])} '
                            f'({esc(config.PAPEIS[prox["papel"]])}).', "alerta"), unsafe_allow_html=True)
    else:
        st.markdown(callout(f'Em <b>{esc(etapa_nome)}</b> há {ac.fmt_duracao(p["horas_parado"])}. Próxima etapa: '
                            f'{esc(prox["nome"])} ({esc(config.PAPEIS[prox["papel"]])}).', "ok"), unsafe_allow_html=True)

erp_ok = True
try:
    ped_erp, itens_erp = pedido_devolucao(p["cd_loja"], p["num_pedido"], p["serie"])
except ERRO_BANCO:  # sem conexão o resto do detalhe continua funcionando
    erp_ok = False
    ped_erp, itens_erp = pd.DataFrame(), pd.DataFrame()
    st.caption("Os dados deste pedido no ERP (valor, itens, datas) estão indisponíveis agora.")

if not ped_erp.empty:
    r = ped_erp.iloc[0]
    with st.container(border=True):
        st.markdown("##### Dados do pedido no ERP")
        pares = [("Volumes", "—" if pd.isna(r["volumes"]) else num_br(r["volumes"], 0)),
                 ("Peso", "—" if pd.isna(r["peso"]) else num_br(r["peso"], 1)),
                 ("Separado por", esc(r["separado_por"] or "—")), ("Nota fiscal", esc(r["nf"] or "—")),
                 ("Início da separação", "—" if pd.isna(r["inicio_separacao"]) else f'{r["inicio_separacao"]:%d/%m/%Y %H:%M}'),
                 ("Fim da conferência", "—" if pd.isna(r["fim_conferencia"]) else f'{r["fim_conferencia"]:%d/%m/%Y %H:%M}')]
        if ver_valor:
            pares = [("Valor", "R$ " + num_br(r["valor"], 2)), ("Custo", "R$ " + num_br(r["custo"], 2))] + pares
        kv(pares)
        st.caption(f'Observação no ERP: {r["observa"] or "—"}')

t_itens, t_erp, t_hist = st.tabs(["Itens do pedido", "Itens no ERP", "Histórico"])
with t_itens:
    if itens.empty:
        st.caption("Pedido sem lote vinculado no Ciclo diário — os itens dele estão na aba ao lado, direto do ERP.")
    tabela = itens.copy()
    tabela["Qtde a devolver"] = (tabela["estoque"].fillna(0) - tabela["qtde_fica"].fillna(0)).clip(lower=0)
    tabela_br(
        tabela.rename(columns={"sku": "SKU", "descricao": "Descrição", "ref_fab": "Ref. fabricante",
                               "fabricante": "Fabricante"})
        [["SKU", "Descrição", "Ref. fabricante", "Fabricante", "Qtde a devolver"]],
        hide_index=True, width="stretch",
    )
with t_erp:
    if not erp_ok:
        st.caption("O ERP não respondeu agora.")
    elif itens_erp.empty:
        st.caption("O ERP não tem itens para este número/série (ou ele ainda não existe lá).")
    else:
        cols_erp = ["sku", "descricao", "ref_fab", "fabricante", "qtde"] + (["preco", "custo"] if ver_valor else [])
        tabela_br(
            itens_erp[cols_erp].rename(columns={
                "sku": "SKU", "descricao": "Descrição", "ref_fab": "Ref. fabricante", "fabricante": "Fabricante",
                "qtde": "Qtde", "preco": "Preço", "custo": "Custo"}),
            hide_index=True, width="stretch",
            column_config={"Preço": st.column_config.NumberColumn(format="R$ %.2f"),
                           "Custo": st.column_config.NumberColumn(format="R$ %.2f")} if ver_valor else None)
with t_hist:
    hist = [{"Quando": e.ocorrido_em.strftime("%d/%m/%Y %H:%M"), "Etapa": config.ETAPA_POR_CHAVE[e.etapa]["nome"],
            "Fonte": "ERP (automático)" if e.fonte == "erp" else "Confirmação manual",
            "Quem": "—" if e.fonte == "erp" else nomes.get(e.quem, e.quem),
            "Detalhes": e.detalhe or "—"}
           for e in historico.itertuples()]
    tabela_br(pd.DataFrame(hist), hide_index=True, width="stretch")

# ── Remover o pedido do acompanhamento (só a Gestão) ─────────────────────────
if pode_remover_pedido(u["papel"]):
    with st.expander("Remover este pedido do acompanhamento"):
        st.caption("Para pedido vinculado por engano ou errado. Ele sai do quadro e fica registrado na Visão de gestão "
                   "(quem tirou, quando e por quê); os itens voltam para \"Pedidos a emitir\" no Ciclo. Isto não "
                   "cancela o pedido no ERP: se for o caso, o grupo cancela lá.")
        with st.form(f"remover-{sel}"):
            motivo_remocao = st.text_input("Motivo (obrigatório)", key=f"rem-motivo-{sel}")
            certeza = st.checkbox("Confirmo: este pedido não deve ficar no acompanhamento", key=f"rem-certeza-{sel}")
            remover = st.form_submit_button("Remover do acompanhamento")
        if remover:
            if not motivo_remocao.strip() or not certeza:
                st.markdown(callout("Preencha o motivo e marque a confirmação.", "alerta"), unsafe_allow_html=True)
            else:
                removeu = estado.remover_vinculo(sel, u["login"], motivo_remocao)
                st.session_state.pop("pedido_sel", None)
                if removeu:
                    st.toast(f'Pedido {p["num_pedido"]}/{p["serie"]} removido do acompanhamento.')
                else:
                    st.toast("Este pedido já tinha sido removido por outra pessoa.", icon="⚠️")
                st.rerun()

# ── Ferramenta de gestão (demonstração): definir a etapa livremente ────────────────
if gestao_livre:
    st.markdown("#### Ferramenta de gestão")
    aviso_esboco("só a visão de gestão vê isto — define a etapa do pedido livremente, pra testar o fluxo "
                 "inteiro. Na aplicação final, só as etapas manuais (a partir de Saída da loja) aceitam "
                 "confirmação, e só de quem é dono dela.")
    opcoes_etapa = {e["nome"]: e["chave"] for e in config.ETAPAS}
    with st.form(f"gestao-etapa-{sel}"):
        escolha_nome = st.selectbox("Etapa", list(opcoes_etapa), index=p["ordem"], key=f"gestao-sel-{sel}")
        definir = st.form_submit_button("Definir etapa", type="primary")
    if definir:
        estado.confirmar_etapa(sel, opcoes_etapa[escolha_nome], u["login"], repetir=True)
        st.toast(f'[Gestão] Etapa de {p["num_pedido"]}/{p["serie"]} definida como "{escolha_nome}".')
        st.rerun()
    st.stop()

# ── Confirmar a próxima etapa (só as que ainda não vêm do ERP) ──────────────
if p["concluido"]:
    st.stop()
pode, motivo = ac.pode_registrar(p["ordem"], p["cd_loja"], u["papel"], u["cd_loja"])
if not pode:
    # Só mostra o bloqueio — nada de convidar a confirmar uma etapa que a linha
    # de baixo diz que não dá (ex.: "Confirmar: Separação" numa etapa do ERP).
    st.markdown(callout(esc(motivo)), unsafe_allow_html=True)
    st.stop()

st.markdown(f"#### Confirmar: {prox['nome']}")
st.caption(prox["descricao"])

with st.form(f"confirmar-{sel}-{prox['chave']}"):
    nf, guardado = None, None
    if prox["chave"] == "saida_loja":
        nf = st.text_input("Nº da nota fiscal de devolução", key=f"nf-{sel}")
    elif prox["chave"] == "chegada_cd":
        guardado = st.checkbox("Peças já guardadas no endereço do CD", key=f"guardado-{sel}")
    st.write(f'Confirma que "{prox["nome"]}" já aconteceu para este pedido?')
    confirmar = st.checkbox(f"Sim, {prox['nome'].lower()} confirmado(a)", key=f"conf-{sel}-{prox['chave']}")
    enviar = st.form_submit_button(f"Confirmar {prox['nome']}", type="primary")

if enviar:
    faltando = []
    if not confirmar:
        faltando.append("a confirmação")
    if prox["chave"] == "saida_loja" and not (nf or "").strip():
        faltando.append("o nº da nota fiscal de devolução")
    if prox["chave"] == "chegada_cd" and not guardado:
        faltando.append("a confirmação de que as peças foram guardadas no endereço")
    if faltando:
        st.markdown(callout("Falta preencher " + " e ".join(faltando) + ".", "alerta"), unsafe_allow_html=True)
    else:
        if prox["chave"] == "saida_loja":
            detalhe = f"NF de devolução: {nf.strip()}"
        elif prox["chave"] == "chegada_cd":
            detalhe = "Guardado no endereço do CD"
        else:
            detalhe = None
        if estado.confirmar_etapa(sel, prox["chave"], u["login"], detalhe=detalhe):
            st.toast(f'"{prox["nome"]}" confirmado(a) para {p["num_pedido"]}/{p["serie"]}.')
        else:
            st.toast(f'"{prox["nome"]}" de {p["num_pedido"]}/{p["serie"]} já tinha sido confirmado(a) por outra pessoa.',
                     icon="⚠️")
        st.rerun()
