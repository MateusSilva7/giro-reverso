"""ERP sintético (data/erp.py): reprodutível, com as colunas que a regra lê, proporções plausíveis e pedidos de ponta a ponta."""
from datetime import timedelta

import pandas as pd
import pytest
import streamlit as st
from sqlalchemy import create_engine

import config
from data import erp, estado
from domain.regra import calcular, na_lista


@pytest.fixture
def banco(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{(tmp_path / 'estado.db').as_posix()}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(estado, "get_engine_estado", lambda: eng)
    st.cache_data.clear()
    estado.inicializar()
    yield eng
    st.cache_data.clear()
    eng.dispose()


def test_a_base_e_reprodutivel_e_tem_as_colunas_da_regra():
    a, b = erp.ler_base(), erp.ler_base()
    pd.testing.assert_frame_equal(a, b)
    assert list(a.columns) == erp.COLS_BASE and set(a["cd_loja"]) == set(config.LOJAS.values())
    assert a["sku"].str.fullmatch(r"\d{6}").all() and not a.duplicated(["cd_loja", "sku"]).any()
    assert (a[erp.COLS_VENDA] >= 0).all().all() and (a["estoque"] >= 0).all() and (a["multiplo"] >= 1).all()
    assert (a["estoque"] % a["multiplo"] == 0).all()
    assert list(erp.ler_revalidacao(["010000"], "05").columns) == erp.COLS_REVALIDAR


def test_a_lista_pega_uma_parte_plausivel_do_estoque_e_todos_os_motivos():
    df = calcular(erp.ler_base())
    lista = na_lista(df)
    assert 0.15 < len(lista) / len(df) < 0.55
    assert 0.10 < lista["valor_retirar"].sum() / df["valor_estoque"].sum() < 0.45
    assert set(lista["motivo"]) == {"Sem venda em 6 meses", "Sem venda em 90 dias", "Excesso sobre o alvo"}
    assert set(df["decisao"]) == {"retornar", "nada a retirar", "lançamento", "sazonal", "em promoção", "abaixo do valor mínimo"}


def test_os_exemplos_nascem_com_os_pedidos_no_erp(banco):
    vinculos = estado.listar_pedidos_gr()
    status = erp.ler_status_para_vinculos(vinculos)
    assert len(vinculos) == len(status) > 0
    assert status["separado"].sum() == sum(1 for v in estado._VINCULOS_EXEMPLO if v[4] is not None)
    ped, itens = erp.ler_pedido_devolucao("04", "500104", "DV")
    assert len(ped) == 1 and list(ped.columns) == erp.COLS_PEDIDO and not itens.empty and list(itens.columns) == erp.COLS_ITENS


def test_pedido_emitido_na_demonstracao_e_separado_depois_de_alguns_minutos(banco, monkeypatch):
    nota, serie = erp.emitir_pedido("05", [("010000", 2.0), ("010001", 1.0)])
    assert int(nota) >= erp.PRIMEIRO_NUMERO and serie == config.SERIE_PEDIDO
    assert erp.emitir_pedido("05", [("010000", 1.0)])[0] == str(int(nota) + 1)
    ped, itens = erp.ler_pedido_devolucao("05", nota, serie)
    assert ped.iloc[0]["observa"] == config.OBSERVACAO_PADRAO_PEDIDO and pd.isna(ped.iloc[0]["fim_separacao"])
    assert list(itens["qtde"]) == [2.0, 1.0]
    chave = [("05", nota, serie)]
    assert not erp.ler_status_devolucoes(chave).iloc[0]["separado"]
    depois = config.agora() + timedelta(minutes=erp.MINUTOS_ATE_SEPARAR + 1)
    monkeypatch.setattr(config, "agora", lambda: depois)
    assert erp.ler_status_devolucoes(chave).iloc[0]["separado"]
    assert erp.ler_pedido_devolucao("04", nota, serie)[0].empty


def test_historico_junta_o_sintetico_e_os_emitidos(banco):
    antes, _ = erp.ler_devolucoes_giro_reverso("05")
    nota, _ = erp.emitir_pedido("05", [("010000", 1.0)])
    depois, _ = erp.ler_devolucoes_giro_reverso("05")
    assert len(depois) == len(antes) + 1 and nota in set(depois["num_pedido"])
    assert list(depois.columns) == erp.COLS_PEDIDO and depois["emissao"].is_monotonic_increasing
