"""Banco de acompanhamento local: WAL junto no .db, backup em arquivo único e coluna nova em banco antigo."""
import shutil
import sqlite3
import threading
from datetime import datetime

import pandas as pd
import pytest
import streamlit as st
from sqlalchemy import create_engine, event, func, inspect, select
from sqlalchemy.exc import IntegrityError

import config
from data import estado


def _tabelas(caminho) -> list[str]:
    con = sqlite3.connect(caminho)
    try:
        return [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
    finally:
        con.close()


def _contagem(caminho, tabela: str) -> int:
    con = sqlite3.connect(caminho)
    try:
        return con.execute(f"select count(*) from {tabela}").fetchone()[0]
    finally:
        con.close()


def _engine_wal(caminho):
    eng = create_engine(f"sqlite:///{caminho.as_posix()}", connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _wal(dbapi_conn, _rec):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")

    return eng


def _banco_com_wal_cheio(caminho):
    eng = _engine_wal(caminho)
    estado.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(estado.tipo_pedido.insert().values(chave="giro_reverso", nome="Giro Reverso"))
    return eng








def test_subida_do_app_junta_o_wal_no_db(tmp_path, monkeypatch):
    origem = tmp_path / "origem.db"
    eng = _banco_com_wal_cheio(origem)
    # Foto do banco como ele fica quando o processo é morto: .db quase vazio e o dado só no WAL.
    queda = tmp_path / "queda.db"
    for ext in ("", "-wal", "-shm"):
        shutil.copyfile(f"{origem}{ext}", f"{queda}{ext}")
    eng.dispose()
    sozinho_antes = tmp_path / "so_o_db_antes.db"
    shutil.copyfile(queda, sozinho_antes)
    assert "tipo_pedido" not in _tabelas(sozinho_antes)  # o problema: copiar só o .db perde tudo

    monkeypatch.setattr(config, "CAMINHO_ESTADO_LOCAL", queda)
    monkeypatch.delenv("ESTADO_DB_URL", raising=False)
    estado.get_engine_estado.clear()
    try:
        novo = estado.get_engine_estado()
        sozinho_depois = tmp_path / "so_o_db_depois.db"
        shutil.copyfile(queda, sozinho_depois)  # sem o -wal e sem o -shm
        assert _contagem(sozinho_depois, "tipo_pedido") == 1
        novo.dispose()
    finally:
        estado.get_engine_estado.clear()


def _banco_antigo(monkeypatch, tabela: str, coluna: str):
    """Banco criado antes de uma coluna existir: o schema atual menos essa coluna."""
    eng = create_engine("sqlite://")
    estado.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.exec_driver_sql(f"ALTER TABLE {tabela} DROP COLUMN {coluna}")
    monkeypatch.setattr(estado, "get_engine_estado", lambda: eng)
    st.cache_data.clear()
    return eng


def test_inicializar_acrescenta_a_coluna_que_falta(monkeypatch):
    eng = _banco_antigo(monkeypatch, "ciclo_item", "descricao")
    try:
        estado.inicializar()
        assert "descricao" in {c["name"] for c in inspect(eng).get_columns("ciclo_item")}
        assert estado.listar_itens_emitidos().empty  # antes: "no such column: ciclo_item.descricao"
        fila = [{"sku": "000001", "descricao": "PASTILHA", "ref_fab": "A1", "fabricante": "ACME", "estoque": 5.0,
                 "alvo": 1.0, "qtde_fica": 0.0, "qtde_retirar": 5.0, "origem": "CD", "motivo": ""}]
        cid = estado.abrir_ciclo("05", "compras", ["Pessoa 1"], 1, 1, fila)
        assert estado.itens_do_ciclo(cid).iloc[0]["descricao"] == "PASTILHA"
        estado.inicializar()  # de novo: nada a acrescentar, nada quebra
    finally:
        eng.dispose()


def test_inicializar_nao_mexe_em_banco_que_ja_esta_certo(monkeypatch):
    eng = create_engine("sqlite://")
    monkeypatch.setattr(estado, "get_engine_estado", lambda: eng)
    st.cache_data.clear()
    try:
        estado.inicializar()
        antes = {t: [c["name"] for c in inspect(eng).get_columns(t)] for t in inspect(eng).get_table_names()}
        estado._SCHEMA_CONFERIDO.discard(eng)
        estado.inicializar()
        depois = {t: [c["name"] for c in inspect(eng).get_columns(t)] for t in inspect(eng).get_table_names()}
        assert antes == depois
    finally:
        eng.dispose()


def test_coluna_obrigatoria_que_falta_da_erro_explicito(monkeypatch):
    eng = _banco_antigo(monkeypatch, "pedido_removido", "motivo")
    try:
        with pytest.raises(RuntimeError, match=r"pedido_removido\.motivo"):
            estado.inicializar()
    finally:
        eng.dispose()


# ── Unicidade e corridas (várias sessões ao mesmo tempo) ─────────────────────
@pytest.fixture
def banco_em_arquivo(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{(tmp_path / 'estado.db').as_posix()}",
                        connect_args={"check_same_thread": False, "timeout": 15})

    @event.listens_for(eng, "connect")
    def _pragmas(dbapi_conn, _rec):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    monkeypatch.setattr(estado, "get_engine_estado", lambda: eng)
    st.cache_data.clear()
    yield eng
    st.cache_data.clear()
    eng.dispose()


def _iniciar(eng):
    estado.inicializar()
    with eng.begin() as conn:
        estado._remover_exemplos(conn)
    st.cache_data.clear()


def _juntas(n, alvo):
    """Roda alvo(i) em n threads ao mesmo tempo. Devolve (resultados, erros)."""
    barreira, resultados, erros = threading.Barrier(n), [None] * n, []

    def corre(i):
        barreira.wait()
        try:
            resultados[i] = alvo(i)
        except Exception as erro:  # o teste só conta os erros
            erros.append(erro)

    fios = [threading.Thread(target=corre, args=(i,)) for i in range(n)]
    for fio in fios:
        fio.start()
    for fio in fios:
        fio.join()
    return resultados, erros


def test_primeira_carga_com_varias_sessoes_ao_mesmo_tempo(banco_em_arquivo):
    _, erros = _juntas(6, lambda _i: estado.inicializar())
    assert not erros
    with banco_em_arquivo.connect() as conn:
        assert conn.execute(select(func.count()).select_from(estado.usuario)).scalar() == 5
        assert conn.execute(select(func.count()).select_from(estado.tipo_pedido)).scalar() == len(config.TIPOS_PEDIDO)


def test_codigos_de_lote_unicos_com_vinculos_simultaneos(banco_em_arquivo):
    _iniciar(banco_em_arquivo)
    _, erros = _juntas(12, lambda i: estado.criar_vinculo(f"1800{i:02d}", "PP", "05", "compras"))
    assert not erros
    with banco_em_arquivo.connect() as conn:
        linhas = conn.execute(select(estado.pedido_gr.c.id, estado.pedido_gr.c.codigo)).all()
    assert len({codigo for _, codigo in linhas}) == 12
    assert all(codigo.startswith("GR-") and codigo.endswith(f"-{pid:04d}") for pid, codigo in linhas)


def test_confirmar_a_mesma_etapa_ao_mesmo_tempo_grava_uma_vez(banco_em_arquivo):
    _iniciar(banco_em_arquivo)
    pid = estado.criar_vinculo("180001", "PP", "05", "compras")
    gravou, erros = _juntas(8, lambda i: estado.confirmar_etapa(pid, "saida_loja", f"u{i}"))
    assert not erros and sum(bool(g) for g in gravou) == 1
    assert len(estado.listar_confirmacoes()) == 1


def test_confirmar_etapa_repetida_so_com_repetir(banco_em_arquivo):
    _iniciar(banco_em_arquivo)
    pid = estado.criar_vinculo("180001", "PP", "05", "compras")
    assert estado.listar_confirmacoes().empty  # entra no cache...
    assert estado.confirmar_etapa(pid, "saida_loja", "log_loja") is True
    assert len(estado.listar_confirmacoes()) == 1  # ...e a gravação já aparece: o cache é limpo ao gravar
    assert estado.confirmar_etapa(pid, "saida_loja", "log_loja") is False
    assert estado.confirmar_etapa(pid, "chegada_cd", "log_cd") is True  # outra etapa grava
    assert estado.confirmar_etapa(pid, "saida_loja", "gestao", repetir=True) is True  # ferramenta de gestão da demonstração
    assert len(estado.listar_confirmacoes()) == 3


def test_um_ciclo_aberto_por_loja(banco_em_arquivo):
    _iniciar(banco_em_arquivo)
    cid = estado.abrir_ciclo("05", "compras", ["A"], 1, 1, [])
    with pytest.raises(estado.CicloJaAberto):
        estado.abrir_ciclo("05", "compras", ["A"], 1, 1, [])
    estado.abrir_ciclo("03", "compras", ["A"], 1, 1, [])  # outra loja pode
    estado.encerrar_ciclo(cid)
    estado.abrir_ciclo("05", "compras", ["A"], 1, 1, [])  # depois de encerrar, abre outro


def test_ciclos_abertos_ao_mesmo_tempo_so_um_vence(banco_em_arquivo):
    _iniciar(banco_em_arquivo)
    resultados, erros = _juntas(6, lambda _i: estado.abrir_ciclo("05", "compras", ["A"], 1, 1, []))
    assert sum(r is not None for r in resultados) == 1
    assert len(erros) == 5 and all(isinstance(e, estado.CicloJaAberto) for e in erros)


def test_inicializar_recria_o_indice_do_ciclo_aberto_em_banco_antigo(monkeypatch):
    eng = create_engine("sqlite://")
    estado.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.exec_driver_sql("DROP INDEX uq_ciclo_aberto_por_loja")
    monkeypatch.setattr(estado, "get_engine_estado", lambda: eng)
    st.cache_data.clear()
    try:
        estado.inicializar()
        assert "uq_ciclo_aberto_por_loja" in {i["name"] for i in inspect(eng).get_indexes("ciclo")}
    finally:
        eng.dispose()


def test_dados_antigos_com_dois_ciclos_abertos_nao_travam_o_app(monkeypatch):
    eng = create_engine("sqlite://")
    estado.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.exec_driver_sql("DROP INDEX uq_ciclo_aberto_por_loja")
        for _ in range(2):
            conn.exec_driver_sql(
                "INSERT INTO ciclo (cd_loja, aberto_em, aberto_por, pessoas, pedidos_por_pessoa, skus_por_pedido, dia_atual) "
                "VALUES ('05', '2026-10-05 10:00:00', 'compras', '[]', 1, 1, 1)")
    monkeypatch.setattr(estado, "get_engine_estado", lambda: eng)
    st.cache_data.clear()
    try:
        estado.inicializar()  # segue sem a trava em vez de quebrar
        assert "uq_ciclo_aberto_por_loja" not in {i["name"] for i in inspect(eng).get_indexes("ciclo")}
    finally:
        eng.dispose()


# ── aplicar_dia: fila grande sem um UPDATE por item ──────────────────────────
@pytest.fixture
def banco_memoria(monkeypatch):
    eng = create_engine("sqlite://")
    monkeypatch.setattr(estado, "get_engine_estado", lambda: eng)
    st.cache_data.clear()
    estado.inicializar()
    yield eng
    eng.dispose()


def _ciclo_com_fila(n, cd="05"):
    fila = [{"sku": f"{i:06d}", "descricao": f"I{i}", "ref_fab": "x", "fabricante": "f", "estoque": 5.0, "alvo": 1.0,
             "qtde_fica": 4.0, "qtde_retirar": 1.0, "origem": "CD", "motivo": ""} for i in range(n)]
    cid = estado.abrir_ciclo(cd, "compras", ["A"], 1, 1, fila)
    return cid, [int(x) for x in estado.itens_do_ciclo(cid)["id"]]


def _ordem_da_fila(cid):
    itens = estado.itens_do_ciclo(cid)
    return [int(x) for x in itens[itens["situacao"] == "fila"].sort_values("posicao")["id"]]


def _notas_reais():
    """As notas dos vínculos de verdade (este banco nasce com os pedidos de exemplo da demonstração)."""
    pedidos = estado.listar_pedidos_gr()
    return list(pedidos.loc[~pedidos["exemplo"].astype(bool), "num_pedido"])


def test_mesmo_pedido_do_erp_nao_vincula_duas_vezes(banco_memoria):
    estado.criar_vinculo("180001", "PP", "05", "compras")
    with pytest.raises(estado.PedidoJaVinculado):  # chave (loja, nota, série) única, dita em palavras e não como erro técnico
        estado.criar_vinculo("180001", "PP", "05", "compras")
    assert _notas_reais() == ["180001"]


def test_vinculo_nao_sobrescreve_item_que_ja_tem_pedido(banco_memoria):
    """Retorno da área de negócio (ajuste 10): a outra sessão que vincula itens já ligados a um pedido não solta o primeiro pedido."""
    cid, ids = _ciclo_com_fila(3)
    estado.aplicar_dia(cid, 1, [{"id": i, "responsavel": "A", "grupo": 1, "estoque": 5.0, "alvo": 1.0} for i in ids], [], [])
    primeiro = estado.criar_vinculo("180001", "PP", "05", "compras", ciclo_id=cid, ciclo_item_ids=ids[:2])
    with pytest.raises(estado.ItensJaVinculados):
        estado.criar_vinculo("180002", "PP", "05", "compras", ciclo_id=cid, ciclo_item_ids=[ids[1], ids[2]])  # o 2º já tem pedido
    itens = estado.itens_do_ciclo(cid).set_index("id")
    assert itens.loc[ids[0], "pedido_id"] == primeiro and itens.loc[ids[1], "pedido_id"] == primeiro
    assert pd.isna(itens.loc[ids[2], "pedido_id"])                      # nada mudou: o 3º continua livre
    assert _notas_reais() == ["180001"]                                 # e o vínculo novo também voltou atrás


def test_vinculo_de_itens_livres_continua_funcionando(banco_memoria):
    cid, ids = _ciclo_com_fila(2)
    pid = estado.criar_vinculo("180001", "PP", "05", "compras", ciclo_id=cid, ciclo_item_ids=[ids[0], ids[0], ids[1]])  # repetido não atrapalha
    assert set(estado.itens_do_ciclo(cid)["pedido_id"]) == {pid}


def test_aplicar_dia_so_uma_sessao_gera_o_dia(banco_memoria):
    """Retorno da área de negócio (ajuste 9): duas sessões da Gestão no "Gerar Dia 1": a primeira gera, a segunda leva DiaJaGerado e nada é gravado."""
    cid, ids = _ciclo_com_fila(4)

    def lote(i):
        return [{"id": i, "responsavel": "A", "grupo": 1, "estoque": 5.0, "alvo": 1.0}]

    estado.aplicar_dia(cid, 1, lote(ids[0]), [], ids[1:])
    with pytest.raises(estado.DiaJaGerado):
        estado.aplicar_dia(cid, 1, lote(ids[1]), [ids[2]], [ids[3]])  # a outra sessão ainda via o dia 1
    itens = estado.itens_do_ciclo(cid).set_index("id")
    assert itens.loc[ids[0], "situacao"] == "lote" and itens.loc[ids[1], "situacao"] == "fila" and itens.loc[ids[2], "situacao"] == "fila"
    assert estado.ciclo_aberto("05")["dia_atual"] == 2  # o dia avançou uma vez só


def test_aplicar_dia_num_ciclo_encerrado_nao_grava_nada(banco_memoria):
    cid, ids = _ciclo_com_fila(2)
    estado.encerrar_ciclo(cid)
    with pytest.raises(estado.DiaJaGerado):
        estado.aplicar_dia(cid, 1, [{"id": ids[0], "responsavel": "A", "grupo": 1, "estoque": 5.0, "alvo": 1.0}], [], [ids[1]])
    assert (estado.itens_do_ciclo(cid)["situacao"] == "fila").all()


def test_o_proximo_dia_so_vale_com_o_dia_certo(banco_memoria):
    cid, ids = _ciclo_com_fila(3)
    estado.aplicar_dia(cid, 1, [{"id": ids[0], "responsavel": "A", "grupo": 1, "estoque": 5.0, "alvo": 1.0}], [], ids[1:])
    estado.aplicar_dia(cid, 2, [{"id": ids[1], "responsavel": "A", "grupo": 1, "estoque": 5.0, "alvo": 1.0}], [], [ids[2]])
    assert estado.ciclo_aberto("05")["dia_atual"] == 3


def test_codigo_do_lote_leva_a_data_e_o_id(banco_memoria):
    pid = estado.criar_vinculo("180001", "PP", "05", "compras", quando=datetime(2026, 10, 6, 9, 0))
    assert estado.listar_pedidos_gr().set_index("id").loc[pid, "codigo"] == f"GR-2610-{pid:04d}"


def test_sqlite_do_app_exige_chave_estrangeira(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CAMINHO_ESTADO_LOCAL", tmp_path / "estado.db")
    monkeypatch.delenv("ESTADO_DB_URL", raising=False)
    real = estado.get_engine_estado
    real.clear()
    try:
        eng = real()
        estado.metadata.create_all(eng)
        with eng.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        monkeypatch.setattr(estado, "get_engine_estado", lambda: eng)
        with pytest.raises(IntegrityError):
            estado.confirmar_etapa(999, "saida_loja", "log_loja")  # pedido que não existe
        eng.dispose()
    finally:
        real.clear()


def test_aplicar_dia_nao_reescreve_a_fila_inteira(banco_memoria):
    cid, ids = _ciclo_com_fila(2000)
    lote, invalidos, sobra = [0, 1, 3, 4, 5, 6], [2, 7], [8, 9, 10, 11]
    comandos = []
    event.listen(banco_memoria, "before_cursor_execute", lambda *a: comandos.append(a[2]))
    estado.aplicar_dia(cid, 1, [{"id": ids[i], "responsavel": "A", "grupo": 1, "estoque": 5.0, "alvo": 1.0} for i in lote],
                       [ids[i] for i in invalidos], [ids[i] for i in sobra] + ids[12:])
    gastos = len(comandos)
    assert gastos < 20  # antes: ~2.000 UPDATEs, um por item da fila (6 mil por dia na fila real da Sul)
    assert _ordem_da_fila(cid) == [ids[i] for i in sobra] + ids[12:]


def test_aplicar_dia_respeita_qualquer_ordem_pedida(banco_memoria):
    cid, ids = _ciclo_com_fila(10)
    nova = [ids[9], ids[7], ids[0], ids[1], ids[2]] + ids[3:7] + [ids[8]]
    estado.aplicar_dia(cid, 1, [], [], nova)
    assert _ordem_da_fila(cid) == nova


def test_aplicar_dia_grava_as_quantidades_da_revalidacao(banco_memoria):
    cid, ids = _ciclo_com_fila(2)
    estado.aplicar_dia(cid, 1, [
        {"id": ids[0], "responsavel": "A", "grupo": 1, "estoque": 12.0, "alvo": 2.0, "qtde_fica": 2.0, "qtde_retirar": 10.0},
        {"id": ids[1], "responsavel": "A", "grupo": 1, "estoque": 7.0, "alvo": 1.0},  # sem as quantidades: mantém as da fila
    ], [], [])
    item = estado.itens_do_ciclo(cid).set_index("id")
    assert tuple(item.loc[ids[0], ["estoque", "qtde_fica", "qtde_retirar"]]) == (12.0, 2.0, 10.0)
    assert tuple(item.loc[ids[1], ["estoque", "qtde_fica", "qtde_retirar"]]) == (7.0, 4.0, 1.0)
    assert (item["situacao"] == "lote").all() and estado.ciclo_aberto("05")["dia_atual"] == 2
