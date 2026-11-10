"""Ciclo diário — fila, lote do dia, distribuição por pessoa e emissão dos pedidos."""
import re
from urllib.parse import quote, urlsplit

import pandas as pd
import streamlit as st

import config
from data import erp, estado
from data.erp import OBSERVACAO_GIRO_REVERSO, ler_pedido_devolucao, ler_revalidacao
from domain import ciclo as dc
from ui.cache import ERRO_BANCO, identificacao_loja, mensagem_sem_banco
from ui.sessao import escolher_loja, pode_agir_livre, pode_montar_lista, usuario_atual
from ui.tema import aviso_esboco, callout, esc, num_br, tabela_br

u = usuario_atual()
TEXTO_GIRO_REVERSO = OBSERVACAO_GIRO_REVERSO.strip("%")  # o que a observação do pedido no ERP deve trazer
TODOS = "Todos"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
LISTA_ERP = "Identificação de agora (ERP)"
COLS_ITEM = {"sku": "SKU", "descricao": "Descrição", "ref_fab": "Ref. fabricante", "fabricante": "Fabricante", "estoque": "Estoque",
             "alvo": "Alvo", "qtde_fica": "Qtde fica", "qtde_retirar": "Qtde a retirar", "origem": "Origem", "motivo": "Motivo"}


def _tabela_itens(df: pd.DataFrame, estoque: str = "Estoque") -> None:
    cols = [c for c in COLS_ITEM if c in df.columns]
    tabela_br(df[cols].rename(columns={**COLS_ITEM, "estoque": estoque}), hide_index=True, width="stretch", cru=("SKU", "Ref. fabricante"))


def _guardar_pessoa() -> None:
    """O comprador trocou o filtro "Ver a lista de": lembra a escolha na sessão e no endereço (?pessoa=Nome)."""
    escolha = st.session_state.get("ver_lista_de")
    if escolha in (None, TODOS):
        st.session_state.pop("pessoa_preferida", None)
        if "pessoa" in st.query_params:
            del st.query_params["pessoa"]
    else:
        st.session_state["pessoa_preferida"] = escolha
        st.query_params["pessoa"] = escolha


def _escolher_pessoa(pessoas: list[str]) -> str | None:
    """Filtro "Ver a lista de": cada comprador abre só os pedidos dele. Começa pelo que veio no link
    (?pessoa=Nome) ou pela escolha anterior da sessão; sem nenhum dos dois, todos. Devolve a pessoa do ciclo ou None (todos)."""
    opcoes = [TODOS, *pessoas]
    alvo = (dc.achar_pessoa(st.query_params.get("pessoa"), pessoas)
            or dc.achar_pessoa(st.session_state.get("pessoa_preferida"), pessoas))
    if st.session_state.get("ver_lista_de") not in opcoes:
        st.session_state.pop("ver_lista_de", None)  # o ciclo mudou de pessoas: o valor velho não serve mais
    escolha = st.selectbox("Ver a lista de", opcoes, index=opcoes.index(alvo) if alvo else 0, key="ver_lista_de",
                           on_change=_guardar_pessoa,
                           help="Cada comprador escolhe o próprio nome para ver só os pedidos dele. A escolha fica no "
                                "endereço (?pessoa=Nome): dá para guardar ou mandar o link.")
    return None if escolha == TODOS else escolha


def _base_do_app() -> str:
    """O endereço do app como o navegador o abriu (http://IP:porta), para montar o link de cada comprador. Sem endereço, ou
    com localhost (que os outros setores não alcançam), devolve um texto para a Gestão trocar. Com GR_URL_BASE no .env
    (servidor atrás de um proxy, o app num caminho), vale ela: o navegador não diz ao Streamlit o caminho do proxy."""
    if config.URL_BASE:
        return config.URL_BASE
    try:
        partes = urlsplit(st.context.url or "")
    except Exception:  # o contexto pode faltar fora do navegador (testes); o link vira um modelo
        partes = urlsplit("")
    if partes.netloc and partes.hostname not in ("localhost", "127.0.0.1", "::1"):
        return f"{partes.scheme}://{partes.netloc}"
    return "http://<endereço do app>"


def _qtd(v: float) -> str:
    """Quantidade de peças para texto: inteira sem casas, fracionada com duas."""
    return num_br(v, 0 if float(v).is_integer() else 2)


def _vincular(nota: str, serie: str, grupo: pd.DataFrame, aceita_diferenca: bool) -> None:
    """Liga o lote ao pedido que o Compras bateu no ERP — só depois de conferir lá que ele existe
    (devolução ao CD desta loja), que não está cancelado (cancelado sumiria do acompanhamento), que foi emitido a
    partir do início da operação e que não tem produto fora do lote. Produto a mais barra sempre, e quantidade a mais do que o Retirar do
    lote também; menos do que o Retirar passa. Item que
    falta e observação sem "GIRO REVERSO" pedem a confirmação "Vincular mesmo...", porque o operador pode esquecer a observação."""
    ja = estado.listar_pedidos_gr()
    if not ja.empty:
        repetido = ja[(ja["cd_loja"] == cd) & (ja["num_pedido"] == nota) & (ja["serie"].str.upper() == serie)]
        if len(repetido):
            st.error(f'O pedido {nota}/{serie} já está vinculado ao lote {repetido.iloc[0]["codigo"]}.')
            return
    try:
        pedido, itens_erp = ler_pedido_devolucao(cd, nota, serie)
    except ERRO_BANCO as e:
        mensagem_sem_banco(e)
        return
    if pedido.empty:
        st.error(f"Não achei o pedido {nota}/{serie} no ERP como devolução ao CD da loja {nome_loja}. Confira o número "
                 "e a série — e se o pedido já foi batido lá.")
        return
    p = pedido.iloc[0]
    if p["cancelada"] == "S":
        st.error(f"O pedido {nota}/{serie} está cancelado no ERP e não dá para vincular: ele sumiria do acompanhamento. "
                 "Bata o pedido de novo lá e informe o número novo.")
        return
    inicio = estado.data_inicio_operacao()
    if inicio and pd.notna(p["emissao"]) and p["emissao"] < pd.Timestamp(inicio):
        st.error(f"O pedido {nota}/{serie} foi emitido em {p['emissao']:%d/%m/%Y}, antes do início da operação "
                 f"({inicio:%d/%m/%Y}). Bata o pedido de novo no ERP e informe o número novo.")
        return
    no_lote, no_erp = set(grupo["sku"]), set(itens_erp["sku"])
    faltam, sobram = sorted(no_lote - no_erp), sorted(no_erp - no_lote)
    if sobram:
        st.error(f'O pedido {nota}/{serie} tem produto que **não** está no lote: {", ".join(sobram)}. Não dá para '
                 "vincular: o pedido só pode ter produto do lote. Tire o produto no ERP (ou bata o pedido de novo) e informe "
                 "o número.")
        return
    a_mais = dc.quantidades_a_mais(grupo, itens_erp)  # produto a mais ou quantidade a mais barra sempre; menos que o lote pode
    if a_mais:
        detalhe = "; ".join(f'{x["sku"]} (pedido {_qtd(x["pedido"])}, lote {_qtd(x["retirar"])})' for x in a_mais[:8])
        st.error(f"O pedido {nota}/{serie} tira mais do que o lote em: {detalhe}{' ...' if len(a_mais) > 8 else ''}. Isso deixa a loja "
                 "abaixo do alvo, e a reposição automática manda a peça de volta. "
                 "Corrija a quantidade no ERP (menos do que o lote pode) e informe o número.")
        return
    obs = "" if pd.isna(p["observa"]) else str(p["observa"])
    sem_marca = TEXTO_GIRO_REVERSO not in obs.upper()
    if (faltam or sem_marca) and not aceita_diferenca:
        partes = []
        if faltam:
            partes.append(f'no lote e **não** no pedido do ERP: {", ".join(faltam)}')
        if sem_marca:
            partes.append(f'a observação do pedido no ERP ({obs or "vazia"}) não traz "{TEXTO_GIRO_REVERSO}"')
        st.warning("O pedido no ERP não bate com o lote — " + " · ".join(partes) + ". Se foi de propósito, "
                   "marque \"Vincular mesmo com item faltando ou sem a observação...\" e vincule de novo.")
        return
    # Nunca marcar como "exemplo": é um vínculo real, conferido no ERP.
    try:
        estado.criar_vinculo(nota, serie, cd, u["login"], ciclo_id=ciclo["id"], ciclo_item_ids=grupo["id"].tolist(),
                             exemplo=False)
    except estado.PedidoJaVinculado:  # outra sessão vinculou este pedido no meio do caminho
        st.toast(f"O pedido {nota}/{serie} acabou de ser vinculado por outra sessão. A tela foi atualizada.", icon="⚠️")
    except estado.ItensJaVinculados:  # outra sessão vinculou itens deste lote a outro pedido
        st.toast("Algum item deste lote acabou de ser vinculado a outro pedido por outra sessão. A tela foi atualizada.", icon="⚠️")
    st.rerun()


agindo_como_gestao = pode_agir_livre(u["papel"])
monta = pode_montar_lista(u["papel"])                     # abre o ciclo, gera o dia e encerra: a Gestão
emite = u["papel"] == "compras" or agindo_como_gestao     # emite os pedidos no ERP e vincula aqui: o Compras

st.title("Ciclo diário", anchor=False)
st.markdown(
    '<p class="gr-lead">Da lista identificada até os pedidos de devolução: a cada dia o sistema revalida no ERP e '
    'distribui o lote entre as pessoas. A Gestão monta o lote; o Compras emite os pedidos no ERP e vincula aqui.</p>',
    unsafe_allow_html=True,
)
if agindo_como_gestao:
    aviso_esboco("você está agindo como Compras pra testar o fluxo inteiro (ferramenta de gestão da demonstração). "
                 "Na aplicação final, só a Gestão monta o lote e só o Compras emite os pedidos.")
elif monta:
    st.markdown(callout("Você monta o lote do dia. Quem emite os pedidos no ERP e vincula aqui é o Compras."),
                unsafe_allow_html=True)
elif emite:
    st.markdown(callout("Quem abre o ciclo e gera o lote do dia é a Gestão. Aqui você emite os pedidos no ERP e "
                        "vincula."), unsafe_allow_html=True)
else:
    st.markdown(callout("Visão só de consulta — quem monta o lote é a Gestão e quem emite os pedidos é o Compras."),
                unsafe_allow_html=True)

nome_loja, cd = escolher_loja()
ciclo = estado.ciclo_aberto(cd)

# ============================================================
# SEM CICLO ABERTO → abrir a partir da identificação (sem CSV)
# ============================================================
if ciclo is None:
    try:
        with st.spinner("Lendo a identificação no ERP..."):
            ident = identificacao_loja(cd)
            fila = dc.montar_fila(ident)
    except ERRO_BANCO as e:
        mensagem_sem_banco(e)
        st.stop()

    st.subheader(f"Abrir ciclo — {nome_loja}", anchor=False)
    p = estado.parametros_regra()
    origem_da_lista = (f"{LISTA_ERP}, {config.agora():%d/%m/%Y %H:%M} · nível de serviço {num_br(p.nivel_servico * 100, 0)}%, "
                       f"cobertura {p.cobertura_dias} dias")
    if not fila:
        st.info("Nada a devolver nesta loja agora.")
        st.stop()

    c1, c2, c3 = st.columns(3)
    n_pessoas = c1.number_input("Nº de pessoas", 1, 20, 3, disabled=not monta)
    ped_pessoa = c2.number_input("Pedidos por pessoa/dia", 1, 50, 2, disabled=not monta)
    skus_ped = c3.number_input("SKUs por pedido", 1, 50, 10, disabled=not monta,
                               help="Pedidos menores são mais fáceis de conferir e corrigir no ERP.")
    cols = st.columns(min(int(n_pessoas), 5))
    pessoas = [cols[i % 5].text_input(f"Pessoa {i + 1}", value=f"Pessoa {i + 1}", key=f"pessoa_{i}",
                                      disabled=not monta,
                                      help="O nome do comprador que vai emitir os pedidos desta pessoa." if i == 0 else None)
               for i in range(int(n_pessoas))]
    st.caption("Use o nome real de cada comprador: é por ele que cada um abre a própria lista (filtro \"Ver a lista de\" "
               "no Ciclo, ou o link ?pessoa=Nome).")
    skus_dia = int(n_pessoas * ped_pessoa * skus_ped)
    total_dias = -(-len(fila) // skus_dia)
    m1, m2, m3 = st.columns(3)
    m1.metric("SKUs na fila", num_br(len(fila), 0))
    m2.metric("SKUs por dia", num_br(skus_dia, 0))
    m3.metric("Duração estimada", f"{num_br(total_dias, 0)} dias")
    st.caption("A fila começa pelos itens que vieram do CD, depois os transferidos de outra loja e por fim os de compra direta; dentro "
               "de cada bloco, por fabricante e referência, a ordem em que a loja separa fisicamente.")
    _tabela_itens(pd.DataFrame(fila[:15]))
    if monta and st.button("Abrir ciclo", type="primary"):
        try:
            estado.abrir_ciclo(cd, u["login"], [p.strip() or f"Pessoa {i + 1}" for i, p in enumerate(pessoas)],
                               int(ped_pessoa), int(skus_ped), fila)
            estado.gravar_lista_origem(cd, origem_da_lista, u["login"])
        except estado.CicloJaAberto:
            st.toast("Outra pessoa acabou de abrir o ciclo desta loja.", icon="⚠️")
        st.rerun()
    st.stop()

# ============================================================
# CICLO ABERTO
# ============================================================
itens = estado.itens_do_ciclo(ciclo["id"])
na_fila = itens[itens["situacao"] == "fila"].sort_values("posicao")
no_lote = itens[itens["situacao"] == "lote"]
invalid = itens[itens["situacao"] == "invalidado"]
a_emitir = no_lote[no_lote["pedido_id"].isna()]
skus_dia = len(ciclo["pessoas"]) * ciclo["pedidos_por_pessoa"] * ciclo["skus_por_pedido"]

st.subheader(f"Ciclo aberto — {nome_loja}", anchor=False)
st.caption(f'Aberto em {pd.Timestamp(ciclo["aberto_em"]):%d/%m/%Y %H:%M} · pessoas: {", ".join(ciclo["pessoas"])} · '
           f'{ciclo["pedidos_por_pessoa"]} pedidos por pessoa, {ciclo["skus_por_pedido"]} SKUs por pedido'
           + (f' · lista: {estado.lista_origem(cd)}' if estado.lista_origem(cd) else ""))
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Próximo dia", f'Dia {ciclo["dia_atual"]}')
m2.metric("Na fila", num_br(len(na_fila), 0))
m3.metric("Distribuídos", num_br(len(no_lote), 0))
m4.metric("Invalidados", num_br(len(invalid), 0))
m5.metric("Pedidos emitidos", num_br(no_lote["pedido_id"].dropna().nunique(), 0))

g1, g2 = st.columns([2, 3])
if na_fila.empty:
    g1.markdown(callout("Fila zerada — todos os produtos foram distribuídos ou invalidados.", "ok"),
                unsafe_allow_html=True)
elif monta and g1.button(f'Gerar Dia {ciclo["dia_atual"]}', type="primary"):
    fila_dicts = na_fila.to_dict(orient="records")
    candidatos = fila_dicts[: min(skus_dia * 2, len(fila_dicts))]
    try:
        with st.spinner("Revalidando estoque e vendas no ERP..."):
            reval = ler_revalidacao([c["sku"] for c in candidatos], cd)
    except ERRO_BANCO as e:
        mensagem_sem_banco(e)
        st.stop()
    res = dc.processar_lote(fila_dicts, reval, ciclo["pessoas"], ciclo["pedidos_por_pessoa"], ciclo["skus_por_pedido"],
                            estado.parametros_regra())
    try:
        estado.aplicar_dia(ciclo["id"], ciclo["dia_atual"], res["lote"], [i["id"] for i in res["invalidos"]],
                           res["nova_fila_ids"])
    except estado.DiaJaGerado:  # outra sessão da Gestão gerou este dia (ou encerrou o ciclo) antes: nada foi gravado aqui
        st.toast("Outra sessão já gerou este dia, ou encerrou o ciclo. A tela foi atualizada.", icon="⚠️")
        st.rerun()
    st.session_state["dia_gerado"] = {
        "loja": cd, "dia": ciclo["dia_atual"],
        "resumo": f'{num_br(res["candidatos"], 0)} revalidados · {num_br(len(res["lote"]), 0)} no lote do dia · '
                  f'{num_br(len(res["sobra"]), 0)} válidos voltaram pro início da fila · {num_br(len(res["invalidos"]), 0)} invalidados',
        "xls_dia": dc.planilha_dia_xlsx(res["lote"]),
        "xls_inv": dc.planilha_invalidados_xlsx(res["invalidos"], ciclo["dia_atual"]),
    }
    st.rerun()
g2.caption("Pega o dobro do necessário como margem, revalida no ERP com a mesma regra da identificação e "
           "distribui os que continuam na lista. A sobra válida volta pro início da fila.")

gerado = st.session_state.get("dia_gerado")
if gerado and gerado["loja"] == cd:
    st.markdown(callout(f'Dia {gerado["dia"]} gerado — {esc(gerado["resumo"])}.', "ok"), unsafe_allow_html=True)
    d1, d2, _ = st.columns([1, 1, 3])
    d1.download_button(f'Planilha do Dia {gerado["dia"]}', gerado["xls_dia"], f'dia{gerado["dia"]:02d}_{cd}.xlsx', XLSX)
    d2.download_button("Invalidados", gerado["xls_inv"], f'invalidados_dia{gerado["dia"]:02d}_{cd}.xlsx', XLSX)

# ── Emissão dos pedidos (Compras) ────────────────────────────────────────────
st.markdown("#### Pedidos a emitir")
if a_emitir.empty:
    st.caption("Nenhum pedido pendente de emissão.")
else:
    st.caption("Emita o pedido de devolução no ERP (bloqueando os itens para venda) e informe "
               "aqui o número dele. A partir daí, separação e conferência da loja aparecem sozinhas no "
               "acompanhamento, direto do ERP — não precisa digitar de novo.")
    ver = _escolher_pessoa(list(ciclo["pessoas"]))
    visiveis = a_emitir if ver is None else a_emitir[a_emitir["responsavel"] == ver]
    if visiveis.empty:
        st.caption(f"Nenhum pedido pendente de emissão para {ver}.")
    else:
        registros = [{**r, "grupo": int(r["grupo"])} for r in visiveis.to_dict("records")]
        arquivo = "pedidos_a_emitir_" + (re.sub(r"\W+", "_", ver).strip("_") if ver else "todos") + f"_{cd}.xlsx"
        st.download_button("Baixar estes pedidos (Excel)", dc.planilha_dia_xlsx(registros), arquivo, XLSX,
                           help="A planilha do dia só destes pedidos, com a quantidade a retirar de cada produto.")
    grupos = visiveis.groupby(["dia", "responsavel", "grupo"], sort=True)
    for (dia, resp, grupo), g in grupos:
        dia, grupo = int(dia), int(grupo)
        with st.expander(f"Dia {dia} · {resp} · Pedido {grupo:02d} — {len(g)} SKUs"):
            st.caption("Observação do pedido no ERP (copie e cole):")
            st.code(config.OBSERVACAO_PADRAO_PEDIDO, language=None)
            _tabela_itens(g.drop(columns=["motivo"], errors="ignore"), estoque="Estoque revalidado")
            if not emite:
                continue
            with st.form(f"emitir-{dia}-{resp}-{grupo}"):
                c1, c2 = st.columns([3, 1])
                num_pedido = c1.text_input("Nº do pedido no ERP", key=f"nunota-{dia}-{resp}-{grupo}")
                serie = c2.text_input("Série", key=f"serie-{dia}-{resp}-{grupo}",
                                      value=config.SERIE_PEDIDO, help="Junto com o número, é a chave do pedido no ERP.")
                bloqueio_ok = st.checkbox("Itens bloqueados para venda na loja")
                aceita_diferenca = st.checkbox("Vincular mesmo com item faltando ou sem a observação GIRO REVERSO no pedido")
                b1, b2 = st.columns([1, 2]) if not config.DADOS_REAIS else (st.container(), None)
                if b1.form_submit_button("Vincular pedido", type="primary"):
                    nota, ser = num_pedido.strip(), serie.strip().upper()
                    if not nota or not ser or not bloqueio_ok:
                        st.error("Preencha o número, a série e confirme o bloqueio dos itens para venda.")
                    else:
                        _vincular(nota, ser, g, aceita_diferenca)
                # Demonstração: não há ERP de verdade para o Compras bater o pedido. Este botão faz as duas coisas: grava no ERP
                # sintético um pedido com os itens e as quantidades do lote e vincula (a mesma conferência do vínculo manual).
                if b2 is not None and b2.form_submit_button("Emitir no ERP simulado e vincular",
                                                            help="Só na demonstração: cria o pedido no ERP sintético com os itens do lote."):
                    if not bloqueio_ok:
                        st.error("Confirme o bloqueio dos itens para venda.")
                    else:
                        itens = [(r["sku"], r["qtde_retirar"]) for r in g.to_dict("records") if r["qtde_retirar"] and r["qtde_retirar"] > 0]
                        nota, ser = erp.emitir_pedido(cd, itens)
                        _vincular(nota, ser, g, aceita_diferenca)

if config.MODO_ESBOCO:
    aviso_esboco("o vínculo com o pedido do ERP é a única informação digitada aqui — é a única coisa que o "
                 "ERP não sabe ligar sozinho a este lote do Giro Reverso. Na hora de vincular o app confere no ERP "
                 "se o pedido existe (devolução ao CD desta loja), se não está cancelado, se foi emitido a partir do "
                 "início da operação, se não tem produto fora do lote nem quantidade maior que a do lote (barra sempre) e se a "
                 "observação traz \"GIRO REVERSO\".")

if monta:
    with st.expander("Links diretos por comprador"):
        base = _base_do_app()
        st.caption("Cada comprador abre o app por este endereço, entra com o código do Compras e cai direto no Ciclo, "
                   "com a lista dele. O nome tem de ser o mesmo que está no ciclo."
                   + (" Troque o trecho entre < > pelo endereço que os setores usam (ex.: `http://IP-da-máquina:8501`)."
                      if "<" in base else ""))
        st.code("\n".join(f"{nome}: {base}/?pessoa={quote(nome)}" for nome in ciclo["pessoas"]), language=None)

with st.expander(f"Próximos da fila ({num_br(len(na_fila), 0)})"):
    _tabela_itens(na_fila.head(30))

if monta:
    st.markdown("---")
    with st.expander("Encerrar o ciclo"):
        # Encerrar abandona o que sobrou e não tem volta: pede a confirmação
        st.warning(f"Ao encerrar, ficam abandonados {num_br(len(na_fila), 0)} item(ns) que ainda estão na fila e {num_br(len(a_emitir), 0)} "
                   "item(ns) de lotes sem pedido emitido, e o ciclo não pode ser reaberto: para continuar, abra um ciclo novo.")
        certeza = st.checkbox("Entendi: quero encerrar este ciclo", key=f"encerrar_ciclo_certeza_{ciclo['id']}")  # uma confirmação por ciclo (por loja)
        if st.button("Encerrar ciclo", disabled=not certeza):
            estado.encerrar_ciclo(ciclo["id"])
            st.session_state.pop("dia_gerado", None)
            st.rerun()
