from datetime import date, datetime, timedelta

import pandas as pd

import config
from domain import acompanhamento as ac

AGORA = datetime(2026, 9, 24, 15, 0)

_COLS_STATUS = ["cd_loja", "num_pedido", "serie", "dt_emissao", "separado", "dt_conferencia", "com_falta", "cancelada"]
_COLS_CONF = ["pedido_id", "etapa", "confirmado_em", "confirmado_por"]


def _vinculos(*ids, tipo="giro_reverso", loja="03"):
    return pd.DataFrame([{"id": i, "codigo": f"P{i}", "cd_loja": loja, "num_pedido": f"N{i}", "serie": "PP",
                          "tipo": tipo, "exemplo": False} for i in ids])


def _status(vinc: pd.DataFrame, horas_emissao: dict, separado: dict | None = None, horas_conferencia: dict | None = None):
    """horas_emissao/horas_conferencia: {id: horas atrás}. separado: {id: bool}, default False."""
    separado = separado or {}
    horas_conferencia = horas_conferencia or {}
    linhas = []
    for r in vinc.itertuples():
        sep = separado.get(r.id, False)
        linhas.append({
            "cd_loja": r.cd_loja, "num_pedido": r.num_pedido, "serie": r.serie,
            "dt_emissao": AGORA - timedelta(hours=horas_emissao[r.id]),
            "separado": sep,
            "dt_conferencia": (AGORA - timedelta(hours=horas_conferencia[r.id])) if sep else None,
            "com_falta": None, "cancelada": "N",
        })
    return pd.DataFrame(linhas, columns=_COLS_STATUS)


def _confirmacoes(pid, etapas_e_horas_atras):
    if not etapas_e_horas_atras:
        return pd.DataFrame(columns=_COLS_CONF)
    return pd.DataFrame([{"pedido_id": pid, "etapa": e, "confirmado_em": AGORA - timedelta(hours=h),
                          "confirmado_por": "alguem"} for e, h in etapas_e_horas_atras])


def test_alerta_passa_de_48h_mesmo_sem_completar_3_dias():
    v = _vinculos(1, 2)
    status = _status(v, {1: 70, 2: 47})
    conf = pd.DataFrame(columns=_COLS_CONF)
    est = ac.estado_pedidos(v, status, conf, AGORA).sort("id")
    assert est["em_atencao"].to_list() == [True, False]  # 2d22h alerta; 1d23h não


def test_etapa_atual_e_concluido():
    v = _vinculos(1)
    status = _status(v, {1: 100}, separado={1: True}, horas_conferencia={1: 90})
    conf = _confirmacoes(1, [("saida_loja", 80), ("chegada_cd", 60), ("conferencia", 40), ("reintegracao", 10)])
    r = ac.estado_pedidos(v, status, conf, AGORA).row(0, named=True)
    assert r["etapa"] == "reintegracao" and r["concluido"] and not r["em_atencao"]
    assert round(r["duracao_ciclo_h"]) == 90


def test_venda_normal_nunca_alerta():
    v = _vinculos(1, tipo="venda_normal")
    status = _status(v, {1: 500})
    conf = pd.DataFrame(columns=_COLS_CONF)
    assert not ac.estado_pedidos(v, status, conf, AGORA)["em_atencao"][0]


def test_emitido_e_separacao_vem_do_erp_sem_confirmacao_manual():
    # Próxima etapa depois de "emitido" é "separação" — lida do ERP, ninguém confirma
    assert not ac.pode_registrar(0, "03", "compras", None)[0]


def test_so_o_dono_da_proxima_etapa_confirma():
    assert ac.pode_registrar(1, "03", "log_loja", "03")[0]           # saída da loja: logística da loja
    assert not ac.pode_registrar(1, "03", "log_loja", "04")[0]       # outra loja
    assert not ac.pode_registrar(1, "03", "qualidade", None)[0]       # não é a vez da Qualidade
    assert ac.pode_registrar(3, "03", "qualidade", None)[0]           # depois da chegada no CD
    assert not ac.pode_registrar(5, "03", "qualidade", None)[0]       # já concluído


def test_fmt_duracao():
    assert ac.fmt_duracao(0.6) == "36 min"
    assert ac.fmt_duracao(5.4) == "5h"
    assert ac.fmt_duracao(70) == "2d 22h"


def test_fmt_duracao_nas_fronteiras():
    assert ac.fmt_duracao(23.99) == "23h" and ac.fmt_duracao(24) == "1d 00h" and ac.fmt_duracao(None) == "—"


def test_alerta_so_depois_das_48h_exatas():
    v = _vinculos(1, 2, 3)
    status = _status(v, {1: 47.99, 2: 48.0, 3: 48.01})
    est = ac.estado_pedidos(v, status, pd.DataFrame(columns=_COLS_CONF), AGORA).sort("id")
    assert est["em_atencao"].to_list() == [False, False, True]  # "mais de 48 h", como no CLAUDE.md


def test_o_limite_do_alerta_e_de_48_horas():
    assert config.LIMITE_ALERTA_HORAS == 48


def test_pedido_cancelado_no_erp_sai_do_quadro():
    # Comportamento atual (achado F08 da análise): o cancelado some, sem aviso. Se virar uma etapa visível, ajustar aqui.
    v = _vinculos(1, 2)
    status = _status(v, {1: 10, 2: 10})
    status.loc[status["num_pedido"] == "N2", "cancelada"] = "S"
    est = ac.estado_pedidos(v, status, pd.DataFrame(columns=_COLS_CONF), AGORA)
    assert est["id"].to_list() == [1]


def test_kpis_contam_andamento_atencao_e_concluidos():
    v = _vinculos(1, 2, 3)
    status = _status(v, {1: 100, 2: 10, 3: 100}, separado={3: True}, horas_conferencia={3: 90})
    conf = _confirmacoes(3, [("saida_loja", 80), ("chegada_cd", 60), ("conferencia", 40), ("reintegracao", 10)])
    k = ac.kpis(ac.estado_pedidos(v, status, conf, AGORA))
    assert (k["andamento"], k["atencao"], k["concluidos"]) == (2, 1, 1)
    assert round(k["ciclo_medio_dias"] * 24) == 90


def test_tempo_por_etapa_mede_da_etapa_ate_a_seguinte():
    v = _vinculos(1)
    status = _status(v, {1: 60}, separado={1: True}, horas_conferencia={1: 50})
    conf = _confirmacoes(1, [("saida_loja", 40), ("chegada_cd", 30)])
    por_etapa = {r["etapa"]: r["media_h"] for r in ac.tempo_por_etapa(v, status, conf).to_dicts()}
    assert por_etapa == {"emitido": 10.0, "separacao": 10.0, "saida_loja": 10.0}  # a última etapa ainda não terminou


def test_empate_de_horario_fica_com_a_etapa_mais_adiante():
    v = _vinculos(1)
    status = _status(v, {1: 5}, separado={1: True}, horas_conferencia={1: 5})  # emitido e separação no mesmo instante
    est = ac.estado_pedidos(v, status, pd.DataFrame(columns=_COLS_CONF), AGORA).row(0, named=True)
    assert est["etapa"] == "separacao"


def test_empate_de_horario_entre_confirmacoes_fica_com_a_etapa_mais_adiante():
    v = _vinculos(1)
    status = _status(v, {1: 60}, separado={1: True}, horas_conferencia={1: 50})
    conf = _confirmacoes(1, [("chegada_cd", 10), ("saida_loja", 10)])  # mesmo horário, gravadas fora de ordem
    est = ac.estado_pedidos(v, status, conf, AGORA).row(0, named=True)
    assert est["etapa"] == "chegada_cd"


def test_etapa_lida_do_erp_nao_aceita_confirmacao_nem_do_papel_dono_dela():
    # "Separação" vem do ERP: nem a logística da loja (dona da etapa) confirma à mão
    ok, motivo = ac.pode_registrar(0, "03", "log_loja", "03")
    assert not ok and "ERP" in motivo


# ── desde o início da operação ────────────
def _tres_pedidos():
    v = _vinculos(1, 2, 3)
    status = _status(v, {1: 400, 2: 30, 3: 10})  # emitidos em 08/09, 23/09 09:00 e 24/09 05:00
    conf = pd.concat([_confirmacoes(1, [("saida_loja", 300)]), _confirmacoes(2, [("saida_loja", 5)])], ignore_index=True)
    return v, status, conf


def test_desde_o_inicio_deixa_so_o_que_foi_emitido_a_partir_da_data():
    v, status, conf = _tres_pedidos()
    v2, status2, conf2 = ac.desde_o_inicio(v, status, conf, date(2026, 9, 23))
    assert list(v2["id"]) == [2, 3] and set(status2["num_pedido"]) == {"N2", "N3"}
    assert list(conf2["pedido_id"]) == [2]  # a confirmação do pedido antigo sai junto
    est = ac.estado_pedidos(v2, status2, conf2, AGORA)
    assert set(est["id"].to_list()) == {2, 3}


def test_pedido_emitido_na_data_de_inicio_fica():
    v, status, conf = _tres_pedidos()
    v2, _, _ = ac.desde_o_inicio(v, status, conf, date(2026, 9, 24))
    assert list(v2["id"]) == [3]  # 24/09 05:00 já é do dia 24; o de 23/09 09:00 não


def test_sem_data_de_inicio_nada_e_escondido():
    v, status, conf = _tres_pedidos()
    v2, status2, conf2 = ac.desde_o_inicio(v, status, conf, None)
    assert v2 is v and status2 is status and conf2 is conf


def test_data_antes_de_todos_os_pedidos_nao_esconde_ninguem():
    v, status, conf = _tres_pedidos()
    v2, status2, conf2 = ac.desde_o_inicio(v, status, conf, date(2026, 1, 1))
    assert list(v2["id"]) == [1, 2, 3] and len(status2) == 3 and len(conf2) == 2


def test_pedido_que_o_erp_nao_devolveu_continua_nos_vinculos():
    v, status, conf = _tres_pedidos()
    status = status[status["num_pedido"] != "N1"]  # o ERP não achou o 1 (ou está cancelado): sem data, não dá para saber
    v2, _, _ = ac.desde_o_inicio(v, status, conf, date(2026, 9, 23))
    assert list(v2["id"]) == [1, 2, 3]


def test_sem_vinculos_ou_sem_status_devolve_o_que_recebeu():
    v, status, conf = _tres_pedidos()
    vazio = v.iloc[0:0]
    assert ac.desde_o_inicio(vazio, status, conf, date(2026, 9, 23))[0] is vazio
    sem_status = status.iloc[0:0]
    assert ac.desde_o_inicio(v, sem_status, conf, date(2026, 9, 23))[0] is v


# ── retorno da área de negócio, ajuste 2: vínculo cujo pedido o ERP cancelou ou não achou ──────────────────────
def _com_autoria(v: pd.DataFrame) -> pd.DataFrame:
    """Os vínculos como `estado.listar_pedidos_gr()` os devolve: com quem e quando criou."""
    return v.assign(criado_em=AGORA, criado_por="compras")


def test_vinculo_com_pedido_cancelado_ou_nao_achado_no_erp_aparece_com_o_motivo():
    v = _com_autoria(_vinculos(1, 2, 3, 4))
    status = _status(v, {1: 5, 2: 5, 3: 5, 4: 5})
    status.loc[status["num_pedido"] == "N2", "cancelada"] = "S"   # o 2 foi cancelado no ERP
    status = status[status["num_pedido"] != "N3"]                  # o 3 o ERP nem achou; o 1 e o 4 estão bem
    p = ac.vinculos_com_problema(v, status)
    assert list(p["id"]) == [2, 3] and list(p["codigo"]) == ["P2", "P3"]
    assert list(p["problema"]) == ["cancelado no ERP", "não achado no ERP"]
    assert list(p.columns) == ["id", "codigo", "cd_loja", "num_pedido", "serie", "criado_em", "criado_por", "problema"]
    assert list(p["num_pedido"]) == ["N2", "N3"] and set(p["criado_por"]) == {"compras"}


def test_vinculo_sem_problema_nao_aparece():
    v = _com_autoria(_vinculos(1, 2))
    p = ac.vinculos_com_problema(v, _status(v, {1: 5, 2: 5}))
    assert p.empty and "problema" in p.columns


def test_pedido_de_exemplo_nao_e_problema_mesmo_sem_existir_no_erp():
    v = _com_autoria(_vinculos(1, 2))
    v.loc[v["id"] == 1, "exemplo"] = True                      # exemplo não existe no ERP: é de mentira
    assert list(ac.vinculos_com_problema(v, _status(v, {1: 5, 2: 5}).iloc[0:0])["id"]) == [2]


def test_a_chave_do_pedido_no_erp_inclui_a_loja():
    v = _com_autoria(_vinculos(1, loja="03"))
    status = _status(_vinculos(1, loja="05"), {1: 5})          # a nota N1 existe, mas é de outra loja
    assert list(ac.vinculos_com_problema(v, status)["problema"]) == ["não achado no ERP"]


def test_status_repetido_no_erp_nao_duplica_o_vinculo():
    v = _com_autoria(_vinculos(1))
    status = _status(v, {1: 5})
    status["cancelada"] = "S"
    assert len(ac.vinculos_com_problema(v, pd.concat([status, status], ignore_index=True))) == 1


def test_sem_vinculos_ou_sem_status_a_lista_de_problemas_e_coerente():
    um = _com_autoria(_vinculos(1))
    status = _status(um, {1: 5})
    assert ac.vinculos_com_problema(pd.DataFrame(columns=um.columns), status).empty
    todos = ac.vinculos_com_problema(_com_autoria(_vinculos(1, 2)), status.iloc[0:0])  # ERP sem nenhuma linha: nenhum foi achado
    assert list(todos["problema"]) == ["não achado no ERP"] * 2
