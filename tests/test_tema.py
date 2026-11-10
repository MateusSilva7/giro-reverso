"""Escape de HTML e formato brasileiro dos números (ui/tema.py): num_br e a tabela_br, por onde passa toda tabela do app."""
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import streamlit as st

import config
from ui import tema
from ui.tema import casas_auto, esc, num_br, tabela_br

RAIZ = Path(__file__).resolve().parent.parent


def test_esc_escapa_html_e_trata_vazio():
    assert esc('<img src=x onerror="a">&\'') == "&lt;img src=x onerror=&quot;a&quot;&gt;&amp;&#x27;"
    assert esc(None) == "" and esc(5) == "5"


def test_num_br_usa_virgula_decimal_e_ponto_de_milhar():
    assert num_br(1234.5, 2) == "1.234,50"
    assert num_br(8.6) == "8,6"
    assert num_br(1234567, 0) == "1.234.567"


def test_num_br_vazio_vira_travessao_e_negativo_que_arredonda_a_zero_perde_o_sinal():
    assert num_br(None) == num_br(float("nan")) == num_br(pd.NA) == "—"
    assert num_br(-1234.5, 2) == "-1.234,50" and num_br(-0.04, 1) == "0,0" and num_br(-0.4, 0) == "0"
    assert num_br(np.int64(2003), 0) == "2.003" and num_br(np.float64(16.666), 1) == "16,7"


def test_casas_auto_sem_casas_para_inteiro_e_duas_para_fracionario():
    assert casas_auto(1690.0) == 0 and casas_auto(3) == 0 and casas_auto(2.0000000001) == 0
    assert casas_auto(8.64) == 2 and casas_auto(0.5) == 2


@pytest.mark.parametrize("dados_reais", [False, True])
def test_a_nota_da_tela_so_aparece_no_esboco(monkeypatch, dados_reais):
    """Com dados reais (piloto e Rede) o app é de verdade e a tela não carrega comentário de bastidor: a nota some.
    Na demonstração ela leva o rótulo "Demonstração:"."""
    escritos = []
    monkeypatch.setattr(config, "DADOS_REAIS", dados_reais)
    monkeypatch.setattr(st, "markdown", lambda corpo, **_k: escritos.append(corpo))
    tema.aviso_esboco("texto <b>")
    if dados_reais:
        assert escritos == []
    else:
        assert len(escritos) == 1 and "<b>Demonstração:</b> texto &lt;b&gt;" in escritos[0]  # o texto continua escapado


# ── tabela_br ────────────────────────────────────────────────────────────────
@pytest.fixture
def espiao(monkeypatch):
    """Troca o st.dataframe por um espião: guarda o que a tabela_br mandou e devolve um valor reconhecível (a seleção de linha)."""
    chamadas = []

    def falso(dados, **kwargs):
        chamadas.append((dados, kwargs))
        return "devolvido"

    monkeypatch.setattr(st, "dataframe", falso)
    return chamadas


def _exibido(estilo) -> list[list]:
    """O texto de cada célula como o navegador mostra (o que o Streamlit lê do Styler), uma lista por linha."""
    corpo = estilo._translate(True, True)["body"]
    return [[c["display_value"] for c in linha if c["type"] == "td"] for linha in corpo]


def test_tabela_br_formata_em_portugues_e_o_printf_do_column_config_vira_formato_brasileiro(espiao):
    df = pd.DataFrame({"Grupo": ["A", "B"], "SKUs": [2003, 673], "Estoque": [1690.0, 330.0], "Valor": [245310.57, 118420.36],
                       "Pct": [16.66, 11.9], "Dias": [8.64, 1.5]})
    cfg = {"Valor": st.column_config.NumberColumn("Valor em estoque", format="R$ %.2f"),
           "Pct": st.column_config.NumberColumn("% do valor", format="%.1f%%"),
           "SKUs": st.column_config.NumberColumn(format="%d")}
    assert tabela_br(df, hide_index=True, width="stretch", column_config=cfg) == "devolvido"
    dados, kw = espiao[0]
    assert _exibido(dados) == [["A", "2.003", "1.690", "R$ 245.310,57", "16,7%", "8,64"],
                               ["B", "673", "330", "R$ 118.420,36", "11,9%", "1,50"]]
    assert kw["hide_index"] is True and kw["width"] == "stretch"
    # o printf do Streamlit ganharia do Styler ("R$ 245310.57"): sai do que vai ao navegador, o rótulo fica, o original não muda
    enviado = kw["column_config"]
    assert enviado["Valor"]["label"] == "Valor em estoque" and enviado["Valor"]["type_config"]["format"] is None
    assert enviado["Pct"]["type_config"]["format"] is None and enviado["SKUs"]["type_config"]["format"] is None
    assert cfg["Valor"]["type_config"]["format"] == "R$ %.2f"
    # o texto muda, o dado não: a coluna segue número e ordena como número
    assert dados.data["SKUs"].dtype.kind == "i" and dados.data["Valor"].dtype.kind == "f"


def test_tabela_br_vazio_vira_travessao_e_texto_e_booleano_ficam_como_estao(espiao):
    df = pd.DataFrame({"n": [1.5, np.nan], "t": ["x", "y"], "b": [True, False], "i": pd.array([2, pd.NA], dtype="Int64")})
    tabela_br(df)
    exibido = _exibido(espiao[0][0])
    assert [linha[0] for linha in exibido] == ["1,50", "—"] and [linha[3] for linha in exibido] == ["2", "—"]
    assert [linha[1] for linha in exibido] == ["x", "y"] and [linha[2] for linha in exibido] == [True, False]


def test_tabela_br_coluna_que_e_numero_em_object_tambem_e_formatada(espiao):
    df = pd.DataFrame({"x": pd.Series([1, None, 3000], dtype=object), "y": pd.Series(["a", 1, None], dtype=object)})
    tabela_br(df)
    exibido = _exibido(espiao[0][0])
    assert [linha[0] for linha in exibido] == ["1", "—", "3.000"]
    assert [linha[1] for linha in exibido] == [linha[1] for linha in _exibido(df.style)]  # texto misturado com número: intocada


def test_tabela_br_cru_deixa_o_codigo_sem_ponto_de_milhar_e_config_que_nao_e_numero_nao_muda(espiao):
    df = pd.DataFrame({"NF": [123456, 7], "Nota": [555.0, 7.0], "Dias": [1.0, 2.5], "Qtde": [1000, 2000], "Valor": [1000.0, 2.5],
                       "Barra": [1.5, 2.0]})
    cfg = {"Dias": st.column_config.ProgressColumn(format="localized", min_value=0, max_value=3),
           "Qtde": st.column_config.NumberColumn(format="localized"),
           "Valor": st.column_config.NumberColumn(format="R$ %.2f"),
           "Barra": st.column_config.ProgressColumn(min_value=0, max_value=3)}
    tabela_br(df, column_config=cfg, cru=("NF", "Nota"))
    dados, kw = espiao[0]
    exibido = _exibido(dados)
    assert [linha[0] for linha in exibido] == ["123456", "7"] and [linha[1] for linha in exibido] == ["555", "7"]
    assert [linha[4] for linha in exibido] == ["R$ 1.000,00", "R$ 2,50"]
    # a barra de progresso e o "localized" são escolha de quem chamou: seguem como foram pedidos, sem o formato do Styler
    assert kw["column_config"]["Dias"]["type_config"]["format"] == "localized"
    assert kw["column_config"]["Qtde"]["type_config"]["format"] == "localized"
    assert [linha[5] for linha in exibido] == [linha[5] for linha in _exibido(df.style)]


def test_tabela_br_styler_que_ja_vem_pronto_mantem_o_estilo_e_ganha_o_formato(espiao):
    df = pd.DataFrame({"Dias": [177.01, 12.0], "Valor": [1093450.0, 5.0]})
    estilo = df.style.map(lambda v: "color:red" if v > 100 else "", subset=["Dias"])
    tabela_br(estilo, column_config={"Valor": st.column_config.NumberColumn(format="R$ %.2f")})
    dados, _ = espiao[0]
    assert dados is estilo
    assert _exibido(dados) == [["177,01", "R$ 1.093.450,00"], ["12,00", "R$ 5,00"]]
    assert "color: red" in dados.to_html()


def test_tabela_br_repassa_a_selecao_e_os_demais_argumentos(espiao):
    df = pd.DataFrame({"a": [1, 2]})
    assert tabela_br(df, on_select="rerun", selection_mode="single-row", key="k", height=300) == "devolvido"
    assert espiao[0][1] == {"on_select": "rerun", "selection_mode": "single-row", "key": "k", "height": 300, "column_config": None}


def test_tabela_br_entrega_como_veio_o_que_nao_sabe_formatar(espiao, monkeypatch):
    cfg = {"v": st.column_config.NumberColumn(format="R$ %.2f")}
    vazia = pd.DataFrame({"v": pd.Series([], dtype=float)})
    repetida = pd.DataFrame([[1000.0, 2.0]], columns=["v", "v"])
    indice_repetido = pd.DataFrame({"v": [1000.0, 2.0]}, index=[0, 0])
    lista = [{"v": 1000.0}]
    for entrada in (vazia, repetida, indice_repetido, lista):
        tabela_br(entrada, column_config=cfg)
        dados, kw = espiao[-1]
        assert dados is entrada and kw["column_config"]["v"]["type_config"]["format"] == "R$ %.2f"
    monkeypatch.setattr(tema, "LIMITE_CELULAS_TABELA", 3)  # grande demais: o Styler ficaria lento
    grande = pd.DataFrame({"v": [1000.0, 2.0], "w": [1, 2]})
    tabela_br(grande, column_config=cfg)
    assert espiao[-1][0] is grande and espiao[-1][1]["column_config"]["v"]["type_config"]["format"] == "R$ %.2f"


# ── grafico_barras ───────────────────────────────────────────────────────────
@pytest.fixture
def espiao_grafico(monkeypatch):
    chamadas = []
    monkeypatch.setattr(st, "vega_lite_chart", lambda dados, spec, **kw: chamadas.append((dados, spec, kw)))
    return chamadas


def test_grafico_barras_manda_so_x_e_y_e_escreve_eixo_e_dica_em_portugues(espiao_grafico):
    df = pd.DataFrame({"Mês": ["jan/26", "fev/26"], "CMV": [2000000.0, 1500000.5], "mes": pd.period_range("2026-01", periods=2, freq="M")})
    tema.grafico_barras(df, "Mês", "CMV", cor="#006F73", sort=False, altura=260, casas=2)
    dados, spec, kw = espiao_grafico[0]
    assert list(dados.columns) == ["Mês", "CMV"] and kw == {"width": "stretch"}  # a coluna de período ficou de fora do JSON
    assert spec["mark"] == {"type": "bar", "color": "#006F73"} and spec["height"] == 260
    codificacao = spec["encoding"]
    assert codificacao["x"]["field"] == "Mês" and codificacao["x"]["sort"] is None  # a ordem dos dados (meses)
    assert codificacao["y"]["field"] == "CMV" and codificacao["y"]["axis"]["tickMinStep"] == 1
    # o gráfico escreveria 2,000,000; a expressão troca para 2.000.000 no eixo e na dica (conferido no navegador)
    rotulo = codificacao["y"]["axis"]["labelExpr"]
    assert "format(datum.value, ',.0f')" in rotulo and rotulo.startswith("replace(replace(replace(") and "'.'" in rotulo
    calculo = spec["transform"][0]
    assert calculo["as"] == "_valor_br" and "format(datum['CMV'], ',.2f')" in calculo["calculate"]
    assert codificacao["tooltip"][1] == {"field": "_valor_br", "type": "nominal", "title": "CMV"}


def test_grafico_barras_horizontal_e_ordenado_pelo_rotulo_como_o_st_bar_chart(espiao_grafico):
    df = pd.DataFrame({"Motivo": ["B", "A"], "Valor a retirar (R$)": [10.0, 20.0]})
    tema.grafico_barras(df, "Motivo", "Valor a retirar (R$)", cor="#006F73", horizontal=True)
    _, spec, _ = espiao_grafico[0]
    codificacao = spec["encoding"]
    assert codificacao["y"]["field"] == "Motivo" and codificacao["y"]["sort"] == "ascending"
    assert codificacao["x"]["field"] == "Valor a retirar (R$)" and "labelExpr" in codificacao["x"]["axis"]
    assert "datum['Valor a retirar (R$)']" in spec["transform"][0]["calculate"] and "format(datum['Valor a retirar (R$)'], ',.0f')" in \
        spec["transform"][0]["calculate"]
    assert "axis" not in codificacao["y"]  # os rótulos de uma barra deitada ficam na horizontal; só a vertical gira o texto


def test_toda_tabela_do_app_passa_pela_tabela_br():
    """Quem chama st.dataframe direto volta a mostrar "R$ 245310.57": as telas e a ficha usam ui.tema.tabela_br."""
    fora = [f"{pasta}/{arquivo.name}" for pasta in ("telas", "ui") for arquivo in sorted((RAIZ / pasta).glob("*.py"))
            if arquivo.name != "tema.py" and re.search(r"\.dataframe\(|st\.table\(|data_editor\(", arquivo.read_text(encoding="utf-8"))]
    assert not fora, f"troque por tabela_br (ui/tema.py): {fora}"


def test_todo_grafico_do_app_passa_pelo_grafico_barras():
    """O st.bar_chart escreve o eixo e a dica como 2,000,000: as telas usam ui.tema.grafico_barras."""
    fora = [f"{pasta}/{arquivo.name}" for pasta in ("telas", "ui") for arquivo in sorted((RAIZ / pasta).glob("*.py"))
            if arquivo.name != "tema.py" and re.search(r"\.(bar|line|area|scatter)_chart\(|altair_chart\(|vega_lite_chart\(|plotly_chart\(",
                                                       arquivo.read_text(encoding="utf-8"))]
    assert not fora, f"troque por grafico_barras (ui/tema.py), que escreve o número em português: {fora}"
