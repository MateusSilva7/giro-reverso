"""Parâmetros da operação (data de início e parâmetros da regra) e a remoção de pedido do acompanhamento (data/estado.py)."""
import json
from datetime import date, datetime

import pytest
import streamlit as st
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.pool import StaticPool

import config
from data import estado


@pytest.fixture
def banco(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    monkeypatch.setattr(estado, "get_engine_estado", lambda: eng)
    st.cache_data.clear()
    estado.inicializar()
    with eng.begin() as conn:
        estado._remover_exemplos(conn)
    st.cache_data.clear()
    yield eng
    st.cache_data.clear()
    eng.dispose()


# ── parâmetros ───────────────────────────────────────────────────────────────
def test_sem_nada_gravado_vale_o_padrao(banco):
    assert estado.data_inicio_operacao() is None  # ninguém é escondido enquanto a Gestão não definir
    p = estado.parametros_regra()
    assert (p.nivel_servico, p.cobertura_dias, p.valor_minimo) == (config.NIVEL_SERVICO_PADRAO, config.COBERTURA_DIAS_PADRAO,
                                                                   config.VALOR_MINIMO_PADRAO)
    assert estado.listar_parametros().empty


def test_gravar_e_ler_de_volta(banco):
    estado.gravar_parametro(estado.PARAM_DATA_INICIO, "2026-10-13", "gestao")
    estado.gravar_parametro(estado.PARAM_NIVEL_SERVICO, "0.98", "gestao")
    estado.gravar_parametro(estado.PARAM_COBERTURA, "45", "gestao")
    assert estado.data_inicio_operacao() == date(2026, 10, 13)
    assert (estado.parametros_regra().nivel_servico, estado.parametros_regra().cobertura_dias) == (0.98, 45)
    estado.gravar_parametro(estado.PARAM_COBERTURA, "60", "outra")  # trocar atualiza a mesma linha
    assert estado.parametros_regra().cobertura_dias == 60
    p = estado.listar_parametros().set_index("chave")
    assert len(p) == 3 and p.loc[estado.PARAM_COBERTURA, "atualizado_por"] == "outra"
    assert p.loc[estado.PARAM_DATA_INICIO, "atualizado_por"] == "gestao"


@pytest.mark.parametrize("chave,valor", [("PARAM_NIVEL_SERVICO", "muito"), ("PARAM_NIVEL_SERVICO", "1.5"),
                                         ("PARAM_COBERTURA", "-4"), ("PARAM_VALOR_MINIMO", "abc")])
def test_valor_estragado_ou_fora_da_faixa_cai_no_padrao_em_vez_de_quebrar(banco, chave, valor):
    estado.gravar_parametro(estado.PARAM_DATA_INICIO, "amanha", "gestao")
    estado.gravar_parametro(getattr(estado, chave), valor, "gestao")
    assert estado.data_inicio_operacao() is None and estado.parametros_regra() == estado.Parametros()


def test_a_origem_da_lista_do_ciclo_e_de_cada_loja(banco):
    assert estado.lista_origem("05") is None  # nenhum ciclo aberto ainda: sem registro
    estado.gravar_lista_origem("05", "Identificação de agora (ERP), 21/10/2026 08:00", "gestao")
    estado.gravar_lista_origem("03", "Identificação de agora (ERP), 22/10/2026 09:00", "gestao")
    assert estado.lista_origem("05") == "Identificação de agora (ERP), 21/10/2026 08:00"
    assert estado.lista_origem("03") == "Identificação de agora (ERP), 22/10/2026 09:00" and estado.lista_origem("07") is None
    estado.gravar_lista_origem("05", "Identificação de agora (ERP), 23/10/2026 08:00", "outra")  # o ciclo seguinte troca o registro
    assert estado.lista_origem("05").startswith("Identificação de agora") and estado.lista_origem("03").startswith("Identificação")


def test_banco_antigo_ganha_as_tabelas_novas(monkeypatch):
    """Um banco criado antes desta versão não tem parametro_operacao nem pedido_removido: a subida cria."""
    eng = create_engine("sqlite://")
    estado.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.exec_driver_sql("DROP TABLE parametro_operacao")
        conn.exec_driver_sql("DROP TABLE pedido_removido")
    monkeypatch.setattr(estado, "get_engine_estado", lambda: eng)
    st.cache_data.clear()
    try:
        estado.inicializar()
        assert {"parametro_operacao", "pedido_removido"} <= set(inspect(eng).get_table_names())
    finally:
        st.cache_data.clear()
        eng.dispose()


# ── remover o pedido do acompanhamento ───────────────────────────────────────
def _ciclo_com_pedido(nota="180001"):
    fila = [{"sku": f"00000{i}", "descricao": f"ITEM {i}", "ref_fab": "X", "fabricante": "ACME", "estoque": 5.0,
             "alvo": 1.0, "qtde_fica": 0.0, "qtde_retirar": 5.0, "origem": "CD", "motivo": ""}
            for i in (1, 2)]
    cid = estado.abrir_ciclo("05", "gestao", ["A"], 1, 2, fila)
    ids = [int(x) for x in estado.itens_do_ciclo(cid)["id"]]
    estado.aplicar_dia(cid, 1, [{"id": i, "responsavel": "A", "grupo": 1, "estoque": 5.0, "alvo": 1.0} for i in ids], [], [])
    pid = estado.criar_vinculo(nota, "PP", "05", "compras", ciclo_id=cid, ciclo_item_ids=ids)
    return cid, ids, pid


def test_remover_tira_o_pedido_das_telas_e_guarda_para_auditoria(banco):
    cid, ids, pid = _ciclo_com_pedido()
    estado.confirmar_etapa(pid, "saida_loja", "log_loja", detalhe="NF de devolução: 555", quando=datetime(2026, 10, 14, 9, 0))
    codigo = estado.listar_pedidos_gr().iloc[0]["codigo"]
    assert estado.remover_vinculo(pid, "gestao", "  Vinculado ao lote errado  ", quando=datetime(2026, 10, 15, 8, 30)) is True

    assert estado.listar_pedidos_gr().empty and estado.listar_confirmacoes().empty
    itens = estado.itens_do_ciclo(cid)
    assert itens["pedido_id"].isna().all() and set(itens["situacao"]) == {"lote"}  # voltam para "a emitir"
    r = estado.listar_pedidos_removidos().iloc[0]
    assert (r["num_pedido"], r["serie"], r["cd_loja"], r["codigo"]) == ("180001", "PP", "05", codigo)
    assert (r["removido_por"], r["motivo"], r["criado_por"]) == ("gestao", "Vinculado ao lote errado", "compras")
    assert r["removido_em"] == datetime(2026, 10, 15, 8, 30)
    etapas = json.loads(r["etapas"])
    assert [(e["etapa"], e["por"], e["detalhe"]) for e in etapas] == [("saida_loja", "log_loja", "NF de devolução: 555")]


def test_depois_de_remover_da_para_vincular_o_pedido_certo_de_novo(banco):
    cid, ids, pid = _ciclo_com_pedido()
    estado.remover_vinculo(pid, "gestao", "errado")
    novo = estado.criar_vinculo("180001", "PP", "05", "compras", ciclo_id=cid, ciclo_item_ids=ids)  # a chave voltou a ser livre
    assert len(estado.listar_pedidos_gr()) == 1 and len(estado.listar_pedidos_removidos()) == 1
    assert set(estado.itens_do_ciclo(cid)["pedido_id"]) == {novo}  # os itens do lote estão no vínculo novo


def test_remover_duas_vezes_ou_um_pedido_que_nao_existe_devolve_false(banco):
    _, _, pid = _ciclo_com_pedido()
    assert estado.remover_vinculo(pid, "gestao", "errado") is True
    assert estado.remover_vinculo(pid, "gestao", "errado") is False  # outra sessão já tinha removido
    assert estado.remover_vinculo(9999, "gestao", "errado") is False
    assert len(estado.listar_pedidos_removidos()) == 1


@pytest.mark.parametrize("motivo", ["", "   ", None])
def test_remover_exige_o_motivo(banco, motivo):
    _, _, pid = _ciclo_com_pedido()
    with pytest.raises(ValueError, match="motivo"):
        estado.remover_vinculo(pid, "gestao", motivo)
    assert len(estado.listar_pedidos_gr()) == 1
    with banco.connect() as conn:
        assert conn.execute(select(func.count()).select_from(estado.pedido_removido)).scalar() == 0
