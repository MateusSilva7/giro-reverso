"""Regra da lista (domain/regra.py): previsão, estoque de segurança, alvo, múltiplos, exclusões e motivos."""
from statistics import NormalDist

import numpy as np
import pandas as pd
import pytest

from domain.regra import (
    COLS_VENDA,
    EXCLUSOES,
    MOTIVOS,
    STATUS_NA_LISTA,
    Parametros,
    calcular,
    metricas,
    previsao_ses,
)


def _item(estoque=10.0, vendas=0.0, **campos):
    hist = vendas if isinstance(vendas, (list, tuple)) else [vendas] * 12
    base = {"cd_loja": "05", "sku": "000001", "estoque": estoque, "custo_unit": 50.0, "multiplo": 1.0, "exposicao_min": 0.0,
            "lead_time_dias": 6.0, "dias_na_loja": 500.0, "sazonal": False, "em_promocao": False, "cmv_mes": 3000.0,
            **dict(zip(COLS_VENDA, map(float, hist)))}
    base.update(campos)
    return base


def _um(p=None, **campos):
    return calcular(pd.DataFrame([_item(**campos)]), p).iloc[0]


# ── previsão ──────────────────────────────────────────────────────────────────
def test_ses_de_serie_constante_preve_a_constante_com_desvio_zero():
    mu, sigma = previsao_ses(np.full((1, 12), 6.0), 0.3)
    assert mu[0] == pytest.approx(6.0) and sigma[0] == pytest.approx(0.0)


def test_ses_reage_mais_ao_mes_recente_com_alfa_maior():
    hist = np.array([[2.0] * 11 + [20.0]])
    lento, _ = previsao_ses(hist, 0.1)
    rapido, _ = previsao_ses(hist, 0.6)
    assert 2 < lento[0] < rapido[0] < 20


def test_ses_bate_com_a_recursao_escrita_a_mao():
    hist = np.array([[3, 5, 4, 6, 2, 7, 5, 4, 8, 6, 5, 9]], dtype=float)
    nivel, erros = hist[0, :3].mean(), []
    for x in hist[0, 3:]:
        erros.append(x - nivel)
        nivel = 0.3 * x + 0.7 * nivel
    mu, sigma = previsao_ses(hist, 0.3)
    assert mu[0] == pytest.approx(nivel) and sigma[0] == pytest.approx(np.std(erros, ddof=1))


# ── alvo ──────────────────────────────────────────────────────────────────────
def test_alvo_e_a_formula_de_revisao_periodica_com_piso_de_poisson():
    p = Parametros(nivel_servico=0.95, cobertura_dias=30)
    r = _um(p, estoque=40.0, vendas=6.0)                       # série constante: σ dos erros é 0, vale o piso √μ
    mu, h = 6.0, 30 + 6.0
    esperado = np.ceil(mu / 30 * h + NormalDist().inv_cdf(0.95) * np.sqrt(mu) * np.sqrt(h / 30))
    assert r["alvo"] == esperado and r["qtde_retirar"] == 40.0 - esperado and r["qtde_fica"] == esperado


def test_nivel_de_servico_e_cobertura_maiores_seguram_mais_na_loja():
    base = dict(estoque=60.0, vendas=[4, 6, 5, 7, 3, 8, 5, 6, 4, 7, 5, 6])
    alvos = [_um(Parametros(nivel_servico=ns, cobertura_dias=c), **base)["alvo"] for ns, c in ((0.8, 15), (0.95, 15), (0.95, 60))]
    assert alvos[0] < alvos[1] < alvos[2]


def test_alvo_respeita_o_minimo_de_exposicao_e_o_multiplo():
    assert _um(estoque=10.0, vendas=0.0, exposicao_min=3.0)["alvo"] == 3.0
    r = _um(estoque=20.0, vendas=0.0, exposicao_min=3.0, multiplo=4.0)
    assert r["alvo"] == 4.0 and r["qtde_retirar"] == 16.0          # alvo sobe para o múltiplo; retirar também em múltiplos


def test_retirar_arredonda_para_baixo_no_multiplo():
    r = _um(estoque=11.0, vendas=0.0, multiplo=4.0)
    assert r["alvo"] == 0.0 and r["qtde_retirar"] == 8.0 and r["qtde_fica"] == 3.0   # não se abre a caixa


def test_fica_mais_retirar_e_sempre_o_estoque():
    rng = np.random.default_rng(1)
    linhas = [_item(estoque=float(rng.integers(0, 50)), vendas=list(rng.poisson(rng.uniform(0, 8), 12)), sku=f"{i:06d}",
                    multiplo=float(rng.choice([1, 2, 4])), exposicao_min=float(rng.integers(0, 3))) for i in range(500)]
    out = calcular(pd.DataFrame(linhas))
    assert np.allclose(out["qtde_fica"] + out["qtde_retirar"], out["estoque"])
    assert (out["qtde_retirar"] >= 0).all() and (out["qtde_retirar"] % pd.DataFrame(linhas)["multiplo"] == 0).all()


# ── exclusões e motivos ───────────────────────────────────────────────────────
@pytest.mark.parametrize("campos,decisao", [
    ({"estoque": 2.0, "vendas": 6.0}, "nada a retirar"),
    ({"dias_na_loja": 40.0}, "lançamento"),
    ({"sazonal": True}, "sazonal"),
    ({"em_promocao": True}, "em promoção"),
    ({"estoque": 1.0, "custo_unit": 5.0}, "abaixo do valor mínimo"),
    ({}, "retornar"),
])
def test_exclusoes(campos, decisao):
    r = _um(**campos)
    assert r["decisao"] == decisao and (r["status"] == STATUS_NA_LISTA) == (decisao == "retornar")
    if decisao != "retornar":
        assert r["qtde_retirar"] == 0 and r["qtde_fica"] == r["estoque"] and r["valor_retirar"] == 0


def test_vale_a_primeira_exclusao_e_sem_data_de_entrada_nao_e_lancamento():
    assert _um(dias_na_loja=10.0, sazonal=True, em_promocao=True)["decisao"] == "lançamento"
    assert _um(dias_na_loja=np.nan)["decisao"] == "retornar"
    assert _um(Parametros(dias_min_loja=0), dias_na_loja=10.0)["decisao"] == "retornar"
    assert set(EXCLUSOES) | {"retornar"} >= set(calcular(pd.DataFrame([_item()]))["decisao"])


@pytest.mark.parametrize("vendas,motivo", [
    ([0.0] * 12, MOTIVOS[0]),
    ([5.0] * 6 + [0.0] * 6, MOTIVOS[0]),
    ([5.0] * 8 + [1.0] + [0.0] * 3, MOTIVOS[1]),
    ([5.0] * 12, MOTIVOS[2]),
])
def test_motivo(vendas, motivo):
    assert _um(estoque=200.0, vendas=vendas)["motivo"] == motivo


def test_parametros_invalidos_sao_recusados():
    for kw in ({"nivel_servico": 1.0}, {"nivel_servico": 0.3}, {"cobertura_dias": -1}, {"alfa": 0}):
        with pytest.raises(ValueError):
            Parametros(**kw)


def test_a_regra_nao_altera_o_dataframe_recebido():
    df = pd.DataFrame([_item()])
    antes = df.copy()
    calcular(df)
    pd.testing.assert_frame_equal(df, antes)


def test_metricas_da_loja():
    df = calcular(pd.DataFrame([_item(sku="000001", estoque=10.0), _item(sku="000002", estoque=2.0, vendas=6.0)]))
    m = metricas(df)
    assert m["skus"] == 2 and m["skus_lista"] == 1 and m["pecas_retirar"] == 10.0 and m["valor_retirar"] == 500.0
    assert m["dias_estoque"] == pytest.approx(600 / 3000 * 30) and m["dias_depois"] == pytest.approx(100 / 3000 * 30)
