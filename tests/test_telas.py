"""Telas de verdade (AppTest), com o ERP simulado e o banco de acompanhamento em memória:
falha do ERP, valor/custo por papel e conferência do vínculo no Ciclo."""
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool
from streamlit.testing.v1 import AppTest

import config
import ui.cache as cache
import ui.kanban as kanban
from data import erp, estado

TELAS =Path(__file__).resolve().parent.parent / "telas"
AGORA = datetime.now().replace(microsecond=0)
USUARIOS = {
    "compras": {"login": "compras", "nome": "Compras", "papel": "compras", "cd_loja": None},
    "gestor": {"login": "gestao", "nome": "Gestão", "papel": "gestor", "cd_loja": None},
    "log_loja": {"login": "log_loja", "nome": "Logística da loja", "papel": "log_loja", "cd_loja": "03"},
    "log_cd": {"login": "log_cd", "nome": "Logística do CD", "papel": "log_cd", "cd_loja": None},
    "qualidade": {"login": "qualidade", "nome": "Qualidade", "papel": "qualidade", "cd_loja": None},
}


@pytest.fixture
def banco(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    monkeypatch.setattr(estado, "get_engine_estado", lambda: engine)
    monkeypatch.setattr(kanban, "quadro_kanban", lambda *a, **k: None)  # componente em JS: o AppTest não renderiza
    st.cache_data.clear()
    estado.inicializar()
    with engine.begin() as conn:
        estado._remover_exemplos(conn)
    st.cache_data.clear()
    yield engine
    st.cache_data.clear()
    engine.dispose()


def _abrir(pagina: str, papel: str, **sessao) -> AppTest:
    at = AppTest.from_file(str(TELAS / f"{pagina}.py"), default_timeout=60)
    at.session_state["usuario"] = dict(USUARIOS[papel])
    for chave, valor in sessao.items():
        at.session_state[chave] = valor
    at.run()
    return at


def _tudo(at: AppTest) -> str:
    """Todo texto visível: markdown, legendas, métricas e nomes de coluna das tabelas."""
    partes = [str(e.value) for colecao in (at.markdown, at.caption, at.subheader, at.title) for e in colecao]
    partes += [f"{m.label} {m.value} {m.delta}" for m in at.metric]
    for tabela in at.dataframe:
        partes += [str(c) for c in tabela.value.columns]
    return " | ".join(partes)


# ── ERP simulado ────────────────────────────────────────────────────────────
def _erp_fora(*_a, **_k):
    raise OperationalError("SELECT 1", {}, Exception('connection to server at "10.9.9.9", port 5432 failed: timed out'))


def _status(vinculos):
    linhas = [{"cd_loja": v.cd_loja, "num_pedido": v.num_pedido, "serie": v.serie, "dt_emissao": AGORA - timedelta(hours=10),
               "separado": False, "dt_conferencia": pd.NaT, "com_falta": None, "cancelada": "N"}
              for v in vinculos.itertuples()]
    return pd.DataFrame(linhas, columns=erp.COLS_STATUS_DEVOLUCAO)


def _pedido(cd, nota, serie, cancelada="N", observa="DEVOLUCAO LOJA > CD - GIRO REVERSO", skus=("000001", "000002"), qtde=3.0):
    ped = pd.DataFrame([{
        "num_pedido": nota, "serie": serie, "valor": 1234.5, "custo": 987.65, "volumes": 3.0, "peso": 12.3, "separado_por": "OP12",
        "nf": "123456", "cancelada": cancelada, "observa": observa, "com_falta": None, "emissao": AGORA,
        "inicio_separacao": AGORA, "fim_separacao": pd.NaT, "fim_conferencia": pd.NaT}])
    itens = pd.DataFrame([{"num_pedido": nota, "serie": serie, "nu_item": i + 1, "sku": c, "qtde": qtde, "preco": 10.5,
                           "custo": 7.2, "descricao": f"ITEM {c}", "ref_fab": f"NF{c}", "fabricante": "ACME"}
                          for i, c in enumerate(skus)])
    return ped, itens


@pytest.fixture
def erp_ok(monkeypatch):
    monkeypatch.setattr(cache, "status_devolucoes", _status)
    monkeypatch.setattr(erp, "ler_status_para_vinculos", _status)
    monkeypatch.setattr(cache, "pedido_devolucao", _pedido)
    monkeypatch.setattr(erp, "ler_pedido_devolucao", _pedido)


# ── F01: ERP fora do ar vira aviso, não exceção ─────────────────────────────
@pytest.fixture
def erp_fora(monkeypatch):
    monkeypatch.setattr(cache, "status_devolucoes", _erp_fora)
    monkeypatch.setattr(erp, "ler_status_para_vinculos", _erp_fora)


@pytest.mark.parametrize("pagina,papel,sessao", [
    ("acompanhamento", "qualidade", {}),
    ("acompanhamento", "log_cd", {}),
    ("acompanhamento", "compras", {"pedido_sel": 1}),  # com o detalhe aberto
    ("fila", "gestor", {}),
    ("painel", "gestor", {}),
])
def test_erp_fora_vira_aviso_e_nao_excecao(banco, erp_fora, pagina, papel, sessao):
    estado.criar_vinculo("180001", "DV", "03", "compras")
    at = _abrir(pagina, papel, **sessao)
    assert not at.exception
    assert any("Não foi possível ler o ERP" in e.value for e in at.error)


@pytest.mark.parametrize("papel,ve_o_detalhe", [("qualidade", False), ("log_cd", False), ("compras", False), ("gestor", True)])
def test_detalhe_tecnico_do_erro_so_para_a_gestao(banco, erp_fora, papel, ve_o_detalhe):
    estado.criar_vinculo("180001", "DV", "03", "compras")
    at = _abrir("fila", papel)
    assert any("connection to server" in c.value for c in at.code) == ve_o_detalhe  # host, porta e SQL do banco


# ── F10: erro de banco vira aviso; erro de código continua aparecendo como erro ──
@pytest.mark.parametrize("pagina", ["perfil", "historico", "lista_diaria", "identificacao"])
def test_telas_de_dados_avisam_quando_o_erp_cai(banco, monkeypatch, pagina):
    for nome in ("identificacao_por_loja", "identificacao_loja", "hora_carga", "devolucoes_giro_reverso",
                 "devolucoes_por_tipo", "cmv_mensal"):
        monkeypatch.setattr(cache, nome, _erp_fora)
    at = _abrir(pagina, "compras")
    assert not at.exception
    assert any("Não foi possível ler o ERP" in e.value for e in at.error)


def test_erro_de_codigo_nao_vira_aviso_de_rede(banco, monkeypatch):
    def bug(_cd):
        raise KeyError("coluna_que_nao_existe")

    monkeypatch.setattr(cache, "identificacao_loja", bug)
    at = _abrir("perfil", "compras")
    assert at.exception and not any("ERP" in e.value for e in at.error)


def test_detalhe_do_pedido_segue_quando_so_o_pedido_no_erp_falha(banco, erp_ok, monkeypatch):
    pid = estado.criar_vinculo("180001", "DV", "03", "compras")
    monkeypatch.setattr(cache, "pedido_devolucao", _erp_fora)
    at = _abrir("acompanhamento", "compras", pedido_sel=pid)
    texto = _tudo(at)
    assert not at.exception and "indisponíveis agora" in texto and "O ERP não respondeu agora" in texto


# ── F02: valor, custo e preço só para Compras e Gestão ───────────────────────
@pytest.mark.parametrize("papel,ve", [("compras", True), ("gestor", True), ("log_loja", False), ("log_cd", False),
                                      ("qualidade", False)])
def test_acompanhamento_mostra_valor_e_custo_so_para_compras_e_gestao(banco, erp_ok, papel, ve):
    pid = estado.criar_vinculo("180001", "DV", "03", "compras")
    at = _abrir("acompanhamento", papel, pedido_sel=pid)
    assert not at.exception
    texto = _tudo(at)
    assert "Dados do pedido no ERP" in texto  # o detalhe abriu
    for rotulo in ("Custo", "Preço", "R$ 1.234,50"):
        assert (rotulo in texto) == ve, rotulo


def _devolucoes(cd):
    pedidos = pd.concat([_pedido(cd, f"1{i:05d}", "DV")[0].assign(emissao=AGORA - timedelta(days=30 * i),
                                                                    fim_conferencia=AGORA - timedelta(days=30 * i - 5))
                         for i in range(3)], ignore_index=True)
    itens = pd.concat([_pedido(cd, f"1{i:05d}", "DV")[1] for i in range(3)], ignore_index=True)
    return pedidos, itens


def _por_tipo(cd):
    return pd.DataFrame({"observa": ["DEVOLUCAO LOJA > CD - GIRO REVERSO"], "pedidos": [3], "primeiro": [AGORA.date()],
                         "ultimo": [AGORA.date()], "valor": [1.0e5]})


@pytest.mark.parametrize("papel,ve", [("compras", True), ("log_loja", False)])
def test_historico_mostra_valor_e_custo_so_para_quem_pode(banco, monkeypatch, papel, ve):
    monkeypatch.setattr(cache, "devolucoes_giro_reverso", _devolucoes)
    monkeypatch.setattr(cache, "devolucoes_por_tipo", _por_tipo)
    primeira_linha = {"selection": {"rows": [0], "columns": [], "cells": []}}  # abre o detalhe do pedido
    at = _abrir("historico", papel, hist_tab=primeira_linha)
    assert not at.exception
    texto = _tudo(at)
    assert "Sem conferência no ERP" in texto and "Observação no ERP" in texto  # a tela e o detalhe abriram
    for rotulo in ("Valor devolvido", "Custo", "Preço", "Valor (R$)", "R$ 1.234,50"):
        assert (rotulo in texto) == ve, rotulo


# ── F07: o vínculo confere cancelado e observação ────────────────────────────
def _ciclo_com_um_lote(cd="03", itens=(1, 2)):
    """Um ciclo aberto com os produtos 00000N de `itens`, todos já no lote do dia 1 (Retirar 5 de cada, sem pedido emitido).
    Devolve o id do ciclo e os ids dos itens."""
    fila = [{"sku": f"00000{i}", "descricao": f"ITEM {i}", "ref_fab": "X", "fabricante": "ACME", "estoque": 5.0, "alvo": 0.0,
             "qtde_fica": 0.0, "qtde_retirar": 5.0, "origem": "CD", "motivo": ""} for i in itens]
    cid = estado.abrir_ciclo(cd, "compras", ["A"], 1, 2, fila)
    ids = [int(x) for x in estado.itens_do_ciclo(cid)["id"]]
    estado.aplicar_dia(cid, 1, [{"id": i, "responsavel": "A", "grupo": 1, "estoque": 5.0, "alvo": 0.0} for i in ids], [], [])
    return cid, ids


def _vincular(banco, monkeypatch, aceita=False, **pedido_erp):
    _ciclo_com_um_lote()
    monkeypatch.setattr(erp, "ler_pedido_devolucao", lambda cd, nota, serie: _pedido(cd, nota, serie, **pedido_erp))
    at = _abrir("ciclo", "compras")
    assert not at.exception
    [t for t in at.text_input if t.key == "nunota-1-A-1"][0].set_value("777001")
    [t for t in at.text_input if t.key == "serie-1-A-1"][0].set_value("dv")
    [c for c in at.checkbox if c.label.startswith("Itens bloqueados")][0].set_value(True)
    if aceita:
        [c for c in at.checkbox if c.label.startswith("Vincular mesmo")][0].set_value(True)
    [b for b in at.button if b.label == "Vincular pedido"][0].click()
    at.run()
    with banco.connect() as conn:
        vinculos = conn.execute(select(func.count()).select_from(estado.pedido_gr)).scalar()
    return at, vinculos


def test_vinculo_do_pedido_certo_funciona(banco, monkeypatch):
    at, vinculos = _vincular(banco, monkeypatch)
    assert not at.exception and not at.error and not at.warning
    assert vinculos == 1


@pytest.mark.parametrize("aceita", [False, True])
def test_vinculo_barra_pedido_cancelado_mesmo_com_aval(banco, monkeypatch, aceita):
    at, vinculos = _vincular(banco, monkeypatch, aceita=aceita, cancelada="S")
    assert any("cancelado no ERP" in e.value for e in at.error)
    assert vinculos == 0


def test_vinculo_pede_aval_quando_a_observacao_nao_e_giro_reverso(banco, monkeypatch):
    at, vinculos = _vincular(banco, monkeypatch, observa="DEVOLUCAO LOJA > CD - ERRO DE FABRICA")
    assert any("não traz" in w.value and "GIRO REVERSO" in w.value for w in at.warning)
    assert vinculos == 0


def test_vinculo_com_observacao_diferente_passa_com_aval(banco, monkeypatch):
    at, vinculos = _vincular(banco, monkeypatch, aceita=True, observa="DEVOLUCAO LOJA > CD - ERRO DE FABRICA")
    assert not at.exception and vinculos == 1


def test_vinculo_sem_observacao_no_erp_tambem_pede_aval(banco, monkeypatch):
    at, vinculos = _vincular(banco, monkeypatch, observa=None)
    assert any("vazia" in w.value for w in at.warning)
    assert vinculos == 0


@pytest.mark.parametrize("aceita", [False, True])
def test_vinculo_barra_produto_a_mais_mesmo_com_aval(banco, monkeypatch, aceita):
    """área de negócio: a quantidade pode variar, o SKU não. Produto fora do lote não passa nem com a caixa marcada."""
    at, vinculos = _vincular(banco, monkeypatch, aceita=aceita, skus=("000001", "000002", "999999"))
    assert any("999999" in e.value and "no lote" in e.value for e in at.error)
    assert vinculos == 0


@pytest.mark.parametrize("aceita,esperados", [(False, 0), (True, 1)])
def test_vinculo_com_item_faltando_pede_aval(banco, monkeypatch, aceita, esperados):
    at, vinculos = _vincular(banco, monkeypatch, aceita=aceita, skus=("000001",))
    assert not at.exception and vinculos == esperados
    assert bool(at.warning) == (not aceita) and not at.error
    if not aceita:
        assert any("não bate com o lote" in w.value and "000002" in w.value for w in at.warning)


def test_vinculo_recusa_pedido_emitido_antes_do_inicio_da_operacao(banco, monkeypatch):
    estado.gravar_parametro(estado.PARAM_DATA_INICIO, (AGORA.date() + timedelta(days=1)).isoformat(), "gestao")
    at, vinculos = _vincular(banco, monkeypatch)
    assert any("antes do início da operação" in e.value for e in at.error) and vinculos == 0


def test_vinculo_aceita_pedido_emitido_no_dia_do_inicio_da_operacao(banco, monkeypatch):
    estado.gravar_parametro(estado.PARAM_DATA_INICIO, AGORA.date().isoformat(), "gestao")
    at, vinculos = _vincular(banco, monkeypatch)
    assert not at.exception and not at.error and vinculos == 1


# ── o pedido não pode tirar mais do que o Retirar do lote ──────────
@pytest.mark.parametrize("aceita", [False, True])
def test_vinculo_barra_quantidade_a_mais_do_que_o_retirar_mesmo_com_aval(banco, monkeypatch, aceita):
    """O lote manda retirar 5 de cada produto e o pedido do ERP tira 6: deixaria a loja abaixo do Fica. Nem a caixa marcada deixa passar."""
    at, vinculos = _vincular(banco, monkeypatch, aceita=aceita, qtde=6.0)
    assert not at.exception and vinculos == 0
    [erro] = [e.value for e in at.error]
    assert "tira mais do que o lote" in erro and "000001 (pedido 6, lote 5)" in erro and "000002 (pedido 6, lote 5)" in erro
    assert "abaixo do alvo" in erro


@pytest.mark.parametrize("qtde", [5.0, 4.0, 1.0])
def test_vinculo_com_a_quantidade_do_retirar_ou_menos_passa(banco, monkeypatch, qtde):
    at, vinculos = _vincular(banco, monkeypatch, qtde=qtde)
    assert not at.exception and not at.error and not at.warning and vinculos == 1


def test_produto_fora_do_lote_barra_antes_da_quantidade(banco, monkeypatch):
    at, vinculos = _vincular(banco, monkeypatch, skus=("000001", "000002", "999999"), qtde=6.0)
    assert [e.value for e in at.error if "tira mais do que o lote" in e.value] == [] and any("999999" in e.value for e in at.error)
    assert vinculos == 0


# ── duas sessões ao mesmo tempo ────────────────────────────────────────────
@pytest.fixture
def toasts(monkeypatch):
    """Os avisos que a tela manda por st.toast. Com o st.rerun() logo depois, o AppTest não guarda o toast na árvore final: um espião os pega."""
    avisos = []
    monkeypatch.setattr(st, "toast", lambda corpo, **_k: avisos.append(corpo))
    return avisos


def _outra_sessao_chega_primeiro(monkeypatch, nota_da_outra=None):
    """Troca `estado.criar_vinculo` por uma que, antes de gravar o vínculo desta sessão, grava o de uma outra sessão: o mesmo pedido, ou
    outro pedido (`nota_da_outra`) para os mesmos itens. É a corrida que o Compras em duas telas provoca."""
    real = estado.criar_vinculo

    def corrida(nota, serie, cd, login, **kw):
        real(nota_da_outra or nota, serie, cd, "outra_sessao", **kw)
        return real(nota, serie, cd, login, **kw)

    monkeypatch.setattr(estado, "criar_vinculo", corrida)


def test_o_mesmo_pedido_vinculado_por_duas_sessoes_vira_aviso_e_so_o_primeiro_fica(banco, monkeypatch, toasts):
    _outra_sessao_chega_primeiro(monkeypatch)
    at, vinculos = _vincular(banco, monkeypatch)
    assert not at.exception and vinculos == 1
    assert any("777001/DV acabou de ser vinculado por outra sessão" in t for t in toasts)
    assert list(estado.listar_pedidos_gr()["criado_por"]) == ["outra_sessao"]


def test_item_ja_ligado_a_outro_pedido_vira_aviso_e_o_primeiro_pedido_fica(banco, monkeypatch, toasts):
    _outra_sessao_chega_primeiro(monkeypatch, nota_da_outra="888888")
    at, vinculos = _vincular(banco, monkeypatch)
    assert not at.exception and vinculos == 1  # o pedido desta sessão voltou atrás por inteiro, não ficou meio gravado
    assert any("Algum item deste lote acabou de ser vinculado a outro pedido" in t for t in toasts)
    assert list(estado.listar_pedidos_gr()["num_pedido"]) == ["888888"]
    assert set(estado.itens_do_ciclo(estado.ciclo_aberto("03")["id"])["pedido_id"]) == {estado.listar_pedidos_gr().iloc[0]["id"]}


# ── encerrar o ciclo pede confirmação ─────────────────────────────────────────
def test_encerrar_o_ciclo_pede_confirmacao_e_diz_o_que_fica_abandonado(banco):
    cid, ids = _ciclo_com_um_lote(itens=(1, 2, 3))
    estado.criar_vinculo("180001", "DV", "03", "compras", ciclo_id=cid, ciclo_item_ids=ids[:1])  # 1 com pedido, 2 ainda a emitir
    at = _abrir("ciclo", "gestor")
    assert not at.exception
    aviso = next(w.value for w in at.warning if w.value.startswith("Ao encerrar"))
    assert "0 item(ns) que ainda estão na fila" in aviso and "2 item(ns) de lotes sem pedido emitido" in aviso
    assert "não pode ser reaberto" in aviso
    assert [b.disabled for b in at.button if b.label == "Encerrar ciclo"] == [True]  # sem marcar o "Entendi", o botão não age
    assert estado.ciclo_aberto("03") is not None


def test_encerrar_o_ciclo_so_depois_de_marcar_a_confirmacao(banco):
    cid, _ = _ciclo_com_um_lote()
    at = _abrir("ciclo", "gestor")
    at.checkbox(key=f"encerrar_ciclo_certeza_{cid}").check().run()
    [botao] = [b for b in at.button if b.label == "Encerrar ciclo"]
    assert not botao.disabled and estado.ciclo_aberto("03") is not None  # liberou o botão, mas ainda não encerrou
    botao.click().run()
    assert not at.exception and estado.ciclo_aberto("03") is None


def test_o_compras_nao_tem_o_encerrar_do_ciclo_nem_a_confirmacao(banco):
    _ciclo_com_um_lote()
    at = _abrir("ciclo", "compras")
    assert not at.exception
    assert not [b for b in at.button if b.label == "Encerrar ciclo"] and not [c for c in at.checkbox if (c.key or "").startswith("encerrar_ciclo_certeza")]


# ── Avaliação da área de negócio: histórico, quadro e fila só desde o início da operação ──────────────
def _inicio(dias_a_frente: int):
    estado.gravar_parametro(estado.PARAM_DATA_INICIO, (AGORA.date() + timedelta(days=dias_a_frente)).isoformat(), "gestao")


def test_historico_so_mostra_o_que_veio_depois_do_inicio_da_operacao(banco, monkeypatch):
    monkeypatch.setattr(cache, "devolucoes_giro_reverso", _devolucoes)  # 3 pedidos: hoje, 30 e 60 dias atrás
    monkeypatch.setattr(cache, "devolucoes_por_tipo", _por_tipo)
    estado.gravar_parametro(estado.PARAM_DATA_INICIO, (AGORA - timedelta(days=10)).date().isoformat(), "gestao")
    for papel in ("compras", "log_loja"):
        at = _abrir("historico", papel)
        assert not at.exception
        assert [m.value for m in at.metric if m.label == "Pedidos Giro Reverso"] == ["1"]
        assert "Todas as devoluções da loja ao CD, por tipo" not in [s.value for s in at.subheader]  # visão ampla: Gestão
        assert not at.toggle  # e só a Gestão enxerga o histórico completo


def test_gestao_pode_ver_o_historico_completo(banco, monkeypatch):
    monkeypatch.setattr(cache, "devolucoes_giro_reverso", _devolucoes)
    monkeypatch.setattr(cache, "devolucoes_por_tipo", _por_tipo)
    estado.gravar_parametro(estado.PARAM_DATA_INICIO, (AGORA - timedelta(days=10)).date().isoformat(), "gestao")
    at = _abrir("historico", "gestor")
    assert [m.value for m in at.metric if m.label == "Pedidos Giro Reverso"] == ["1"]
    assert "Todas as devoluções da loja ao CD, por tipo" not in [s.value for s in at.subheader]
    [t for t in at.toggle if t.key == "hist_completo"][0].set_value(True).run()
    assert not at.exception
    assert [m.value for m in at.metric if m.label == "Pedidos Giro Reverso"] == ["3"]
    assert "Todas as devoluções da loja ao CD, por tipo" in [s.value for s in at.subheader]


def test_historico_sem_pedido_depois_do_inicio_avisa(banco, monkeypatch):
    monkeypatch.setattr(cache, "devolucoes_giro_reverso", _devolucoes)
    monkeypatch.setattr(cache, "devolucoes_por_tipo", _por_tipo)
    _inicio(1)
    at = _abrir("historico", "compras")
    assert not at.exception and any("Nenhum pedido Giro Reverso emitido desde" in i.value for i in at.info)


def test_historico_sem_data_de_inicio_mostra_tudo_como_antes(banco, monkeypatch):
    monkeypatch.setattr(cache, "devolucoes_giro_reverso", _devolucoes)
    monkeypatch.setattr(cache, "devolucoes_por_tipo", _por_tipo)
    at = _abrir("historico", "compras")
    assert [m.value for m in at.metric if m.label == "Pedidos Giro Reverso"] == ["3"]
    assert "Todas as devoluções da loja ao CD, por tipo" in [s.value for s in at.subheader] and not at.toggle


def test_quadro_esconde_o_que_foi_emitido_antes_do_inicio_da_operacao(banco, erp_ok, monkeypatch):
    estado.criar_vinculo("180001", "DV", "03", "compras")
    assert sum(len(c["cards"]) for c in _quadro_enviado(monkeypatch)["colunas"]) == 1  # sem data definida, aparece
    _inicio(1)  # a operação só começa amanhã: o pedido de agora é anterior
    assert sum(len(c["cards"]) for c in _quadro_enviado(monkeypatch)["colunas"]) == 0


def test_fila_e_painel_tambem_escondem_o_que_veio_antes(banco, erp_ok):
    estado.criar_vinculo("180001", "DV", "03", "compras")
    fila = _abrir("fila", "gestor")
    assert "180001" in _tudo(fila)
    _inicio(1)
    fila = _abrir("fila", "gestor")
    assert not fila.exception and any("Nenhum pedido de devolução aguardando separação" in i.value for i in fila.info)
    painel = _abrir("painel", "gestor")
    assert not painel.exception and [m.value for m in painel.metric if m.label == "Em andamento"] == ["0"]


def test_gestao_define_a_data_de_inicio_e_os_parametros_da_regra(banco, erp_ok):
    at = _abrir("painel", "gestor")
    assert not at.exception
    at.date_input[0].set_value(date(2026, 10, 13))
    [b for b in at.button if b.label == "Salvar a data de início"][0].click().run()
    assert not at.exception and estado.data_inicio_operacao() == date(2026, 10, 13)
    at.slider[0].set_value(98)
    at.number_input[0].set_value(45)
    at.number_input[1].set_value(50.0)
    [b for b in at.button if b.label == "Salvar os parâmetros da regra"][0].click().run()
    p = estado.parametros_regra()
    assert not at.exception and (p.nivel_servico, p.cobertura_dias, p.valor_minimo) == (0.98, 45, 50.0)
    gravados = estado.listar_parametros().set_index("chave")
    assert gravados.loc[estado.PARAM_COBERTURA, "atualizado_por"] == "gestao"
    assert "gravado por Gestão" in _tudo(_abrir("painel", "gestor"))


def test_salvar_a_regra_nao_define_a_data_de_inicio(banco, erp_ok):
    at = _abrir("painel", "gestor")
    at.number_input[0].set_value(60)
    [b for b in at.button if b.label == "Salvar os parâmetros da regra"][0].click().run()
    assert estado.parametros_regra().cobertura_dias == 60 and estado.data_inicio_operacao() is None


# ── remover pedido do acompanhamento (só a Gestão) ───────────────────────────
def test_gestao_remove_pedido_do_acompanhamento(banco, erp_ok):
    pid = estado.criar_vinculo("180001", "DV", "03", "compras")
    at = _abrir("acompanhamento", "gestor", pedido_sel=pid)
    assert not at.exception
    [t for t in at.text_input if t.key == f"rem-motivo-{pid}"][0].set_value("Pedido de teste de agosto")
    [c for c in at.checkbox if c.key == f"rem-certeza-{pid}"][0].set_value(True)
    [b for b in at.button if b.label == "Remover do acompanhamento"][0].click().run()
    assert not at.exception and estado.listar_pedidos_gr().empty and "pedido_sel" not in at.session_state
    r = estado.listar_pedidos_removidos().iloc[0]
    assert (r["num_pedido"], r["removido_por"], r["motivo"]) == ("180001", "gestao", "Pedido de teste de agosto")


def test_remover_sem_motivo_ou_sem_confirmar_nao_remove(banco, erp_ok):
    pid = estado.criar_vinculo("180001", "DV", "03", "compras")
    at = _abrir("acompanhamento", "gestor", pedido_sel=pid)
    [c for c in at.checkbox if c.key == f"rem-certeza-{pid}"][0].set_value(True)  # confirmou, mas sem motivo
    [b for b in at.button if b.label == "Remover do acompanhamento"][0].click().run()
    assert len(estado.listar_pedidos_gr()) == 1 and "Preencha o motivo" in _tudo(at)
    at = _abrir("acompanhamento", "gestor", pedido_sel=pid)
    [t for t in at.text_input if t.key == f"rem-motivo-{pid}"][0].set_value("errado")  # motivo, mas sem confirmar
    [b for b in at.button if b.label == "Remover do acompanhamento"][0].click().run()
    assert len(estado.listar_pedidos_gr()) == 1 and estado.listar_pedidos_removidos().empty


@pytest.mark.parametrize("papel", ["compras", "log_loja", "log_cd", "qualidade"])
def test_so_a_gestao_ve_o_remover_pedido(banco, erp_ok, papel):
    pid = estado.criar_vinculo("180001", "DV", "03", "compras")
    at = _abrir("acompanhamento", papel, pedido_sel=pid)
    assert not at.exception and "Dados do pedido no ERP" in _tudo(at)  # o detalhe abriu
    assert not any(b.label == "Remover do acompanhamento" for b in at.button)


def test_painel_lista_os_pedidos_removidos(banco, erp_ok):
    pid = estado.criar_vinculo("180001", "DV", "03", "compras")
    estado.remover_vinculo(pid, "gestao", "vinculado por engano")
    at = _abrir("painel", "gestor")
    assert not at.exception
    tabela = [t.value for t in at.dataframe if {"Pedido", "Motivo", "Removido em"} <= set(t.value.columns)]
    assert tabela and list(tabela[0]["Motivo"]) == ["vinculado por engano"] and list(tabela[0]["Por"]) == ["Gestão"]


# ── vínculos cujo pedido o ERP cancelou ou não achou ───────────
def _dois_vinculos_com_problema(monkeypatch):
    """Três vínculos num ciclo de 3 itens: o 180001 (2 itens) foi cancelado no ERP, o 180002 (1 item) o ERP não acha e o 180003 está bem."""
    cid, ids = _ciclo_com_um_lote(itens=(1, 2, 3))
    estado.criar_vinculo("180001", "DV", "03", "compras", ciclo_id=cid, ciclo_item_ids=ids[:2])
    estado.criar_vinculo("180002", "DV", "03", "compras", ciclo_id=cid, ciclo_item_ids=ids[2:])
    estado.criar_vinculo("180003", "DV", "03", "compras")

    def status(vinculos):
        s = _status(vinculos)
        s.loc[s["num_pedido"] == "180001", "cancelada"] = "S"
        return s[s["num_pedido"] != "180002"]

    monkeypatch.setattr(erp, "ler_status_para_vinculos", status)
    return cid, ids


def test_painel_lista_o_vinculo_com_pedido_cancelado_ou_nao_achado_no_erp(banco, monkeypatch):
    _dois_vinculos_com_problema(monkeypatch)
    at = _abrir("painel", "gestor")
    assert not at.exception
    assert "Vínculos com problema no ERP" in [s.value for s in at.subheader]
    assert any(w.value.startswith("2 vínculo(s) apontam para um pedido cancelado ou que o ERP não achou") for w in at.warning)
    linhas = [m.value for m in at.markdown if "pedido 18000" in m.value]
    assert len(linhas) == 2  # o 180003, que o ERP conhece e não cancelou, não aparece
    assert "pedido 180001/DV" in linhas[0] and "cancelado no ERP" in linhas[0] and "2 item(ns) presos" in linhas[0]
    assert "pedido 180002/DV" in linhas[1] and "não achado no ERP" in linhas[1] and "1 item(ns) presos" in linhas[1]


def test_a_gestao_remove_o_vinculo_com_problema_e_os_itens_voltam_para_emitir(banco, monkeypatch):
    cid, ids = _dois_vinculos_com_problema(monkeypatch)
    at = _abrir("painel", "gestor")
    remover = [b for b in at.button if b.label == "Remover o vínculo"]
    assert len(remover) == 2
    remover[0].click().run()  # o 180001, cancelado
    assert not at.exception
    assert list(estado.listar_pedidos_gr()["num_pedido"]) == ["180002", "180003"]
    itens = estado.itens_do_ciclo(cid).set_index("id")
    assert itens.loc[ids[:2], "pedido_id"].isna().all() and itens.loc[ids[2], "pedido_id"] == estado.listar_pedidos_gr().iloc[0]["id"]
    r = estado.listar_pedidos_removidos().iloc[0]
    assert (r["num_pedido"], r["removido_por"], r["motivo"]) == ("180001", "gestao", "Pedido cancelado no ERP")
    assert any(w.value.startswith("1 vínculo(s) apontam") for w in at.warning)  # sobrou o que o ERP não acha


def test_remover_o_vinculo_com_problema_pede_um_motivo(banco, monkeypatch):
    _dois_vinculos_com_problema(monkeypatch)
    at = _abrir("painel", "gestor")
    at.text_input(key=f"vinculo-problema-motivo-{estado.listar_pedidos_gr().iloc[0]['id']}").set_value("   ")
    [b for b in at.button if b.label == "Remover o vínculo"][0].click().run()
    assert not at.exception and "Informe o motivo da remoção." in [e.value for e in at.error]
    assert len(estado.listar_pedidos_gr()) == 3 and estado.listar_pedidos_removidos().empty


def test_so_a_gestao_remove_o_vinculo_com_problema(banco, monkeypatch):
    _dois_vinculos_com_problema(monkeypatch)
    at = _abrir("painel", "compras")  # o Compras até vê o aviso se abrir a tela, mas não remove
    assert not at.exception and any("apontam para um pedido cancelado" in w.value for w in at.warning)
    assert not [b for b in at.button if b.label == "Remover o vínculo"]


def test_painel_sem_vinculo_com_problema_so_diz_que_conferiu(banco, erp_ok):
    estado.criar_vinculo("180001", "DV", "03", "compras")
    at = _abrir("painel", "gestor")
    assert not at.exception and "Vínculos com problema no ERP" in [s.value for s in at.subheader]
    assert any("Nenhum vínculo com pedido cancelado ou não achado no ERP" in c.value for c in at.caption)
    assert not at.warning and not [b for b in at.button if b.label == "Remover o vínculo"]


# ── os parâmetros da Gestão na identificação que todas as telas leem ─────────
def test_a_identificacao_usa_os_parametros_da_gestao_sem_reler_o_erp(banco, monkeypatch):
    vendas = {f"v{m:02d}": 3.0 for m in range(1, 13)}
    base = pd.DataFrame([{"cd_loja": "05", "sku": "000001", "estoque": 12.0, "custo_unit": 50.0, "multiplo": 1.0, "exposicao_min": 0.0,
                          "lead_time_dias": 5.0, "dias_na_loja": 400.0, "sazonal": False, "em_promocao": False, **vendas}])
    monkeypatch.setattr(cache, "_carregar", lambda: (base, AGORA))
    cache._com_regra.clear()
    try:
        estado.gravar_parametro(estado.PARAM_COBERTURA, "15", "gestao")
        curta = cache.identificacao_loja("05").iloc[0]
        estado.gravar_parametro(estado.PARAM_COBERTURA, "90", "gestao")   # cobertura maior: a loja segura mais
        longa = cache.identificacao_loja("05").iloc[0]
        assert curta["status"] == "Retornar" and curta["qtde_retirar"] > 0
        assert longa["alvo"] > curta["alvo"] and longa["qtde_retirar"] < curta["qtde_retirar"]
        assert list(cache.identificacao_por_loja()) == ["05"]
    finally:
        cache._com_regra.clear()


# ── F14: o quadro manda ao componente o nome do botão e só HTML escapado ─────
HOSTIL = '<img src=x onerror="alert(1)">'


def _quadro_enviado(monkeypatch, **sessao):
    """Roda o Acompanhamento e devolve o que ele mandou ao componente do kanban."""
    recebido = {}

    def captura(colunas, estilo, selecionado, key):
        recebido.update(colunas=colunas, selecionado=selecionado)

    monkeypatch.setattr(kanban, "quadro_kanban", captura)
    at = _abrir("acompanhamento", "compras", **sessao)
    assert not at.exception
    return recebido


def test_card_do_quadro_leva_rotulo_em_texto_puro_e_html_escapado(banco, erp_ok, monkeypatch):
    pid = estado.criar_vinculo(HOSTIL, "DV", "03", "compras")
    quadro = _quadro_enviado(monkeypatch, pedido_sel=pid)
    cards = [c for col in quadro["colunas"] for c in col["cards"]]
    assert [c["id"] for c in cards] == [pid] and quadro["selecionado"] == pid
    assert cards[0]["rotulo"] == f"{HOSTIL}/DV"  # texto puro: no componente só vira aria-label (setAttribute)
    assert "<img" not in cards[0]["html"] and "&lt;img" in cards[0]["html"]


def test_todo_card_de_toda_etapa_leva_o_rotulo_do_pedido(banco, erp_ok, monkeypatch):
    ids = {nota: estado.criar_vinculo(nota, "DV", "03", "compras") for nota in ("180001", "180002", "180003")}
    estado.confirmar_etapa(ids["180002"], "saida_loja", "log_loja", quando=AGORA)
    estado.confirmar_etapa(ids["180003"], "reintegracao", "qualidade", quando=AGORA)
    nota_de = {pid: nota for nota, pid in ids.items()}
    colunas = _quadro_enviado(monkeypatch)["colunas"]
    assert [c["nome"] for c in colunas] == [e["nome"] for e in config.ETAPAS]
    assert [len(c["cards"]) for c in colunas] == [1, 0, 1, 0, 0, 1]
    for card in (c for col in colunas for c in col["cards"]):
        assert card["rotulo"] == f"{nota_de[card['id']]}/DV"


# ── toda tela abre, para cada papel que a vê, com o ERP sintético de verdade ─
@pytest.mark.parametrize("papel,chave", [(papel, chave) for papel, chaves in __import__("ui.navegacao", fromlist=["x"]).TELAS_POR_PAPEL.items()
                                         for chave in chaves])
def test_toda_tela_abre_com_o_erp_sintetico(banco, papel, chave):
    from ui.navegacao import TELAS
    cache.recarregar_identificacao()
    at = _abrir(Path(TELAS[chave][0]).stem, papel)
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]


def test_a_ficha_do_item_abre_na_identificacao(banco):
    cache.recarregar_identificacao()
    primeira = {"selection": {"rows": [0], "columns": [], "cells": []}}
    at = _abrir("identificacao", "compras", tab_05=primeira)
    assert not at.exception
    texto = _tudo(at)
    assert "Como a regra chegou ao alvo" in texto and "Vendas mensais na loja" in texto and "Estoque de segurança" in texto
