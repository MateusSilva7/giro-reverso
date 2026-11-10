"""Ficha de um item da loja: a decisão da regra explicada, o histórico de vendas, o estoque na rede e o cadastro."""
import pandas as pd
import streamlit as st

import config
from data.erp import COLS_VENDA, NOMES_OUTRAS
from domain.regra import STATUS_NA_LISTA
from ui.tema import casas_auto, esc, grafico_barras, num_br, tabela_br

MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
DECISOES = {
    "retornar": "Volta ao CD",
    "nada a retirar": "Fica: o estoque não passa do alvo",
    "lançamento": f"Fica: item com menos de {config.DIAS_MIN_NA_LOJA} dias de loja (histórico curto para prever)",
    "sazonal": "Fica: item sazonal (a demanda volta na temporada)",
    "em promoção": "Fica: item em promoção na loja",
    "abaixo do valor mínimo": "Fica: o valor a retirar não compensa o manuseio",
}


def _vazio(v) -> bool:
    return v is None or v == "" or (not isinstance(v, str) and pd.isna(v))


def _n(v, casas: int | None = None) -> str:
    if _vazio(v) or v == float("inf"):
        return "—"
    f = float(v)
    return num_br(f, casas_auto(f) if casas is None else casas)


def _moeda(v) -> str:
    return "—" if _vazio(v) else "R$ " + num_br(float(v), 2)


def _t(v) -> str:
    return "—" if _vazio(v) else esc(v)


def kv(pares: list[tuple[str, str]]) -> None:
    """pares: (rótulo, valor já formatado e escapado)."""
    html = "".join(f'<div><div class="gr-rotulo">{esc(r)}</div><div class="gr-valor">{v}</div></div>' for r, v in pares)
    st.markdown(f'<div class="gr-kv">{html}</div>', unsafe_allow_html=True)


def _meses_rotulos() -> list[str]:
    hoje = config.agora().date()
    rotulos = []
    for k in range(len(COLS_VENDA), 0, -1):
        m = (pd.Timestamp(hoje.replace(day=1)) - pd.DateOffset(months=k - 1))
        rotulos.append(f"{MESES[m.month - 1]}/{m:%y}")
    return rotulos


def mostrar_ficha(item: pd.Series, cad: pd.Series | None, outras: pd.DataFrame, ver_valor: bool = True) -> None:
    """item: linha da identificação (já com as colunas da regra) · cad: cadastro do item · outras: CD e demais lojas."""
    cad = cad if cad is not None else pd.Series(dtype=object)
    st.subheader(str(item.get("descricao") or item["sku"]), anchor=False)
    st.caption(f'{item["sku"]} · {item.get("fabricante") or "—"} · ref. {item.get("ref_fab") or "—"} · {item.get("categoria") or "—"}')
    volta = item.get("status") == STATUS_NA_LISTA
    st.markdown(f'**{DECISOES.get(item.get("decisao"), "—")}**' + (f' · {item["motivo"]}' if volta else ""))

    st.markdown("##### Como a regra chegou ao alvo")
    pares = [("Estoque na loja", _n(item["estoque"])), ("Previsão de venda/mês", _n(item.get("previsao_mensal"), 1)),
             ("Desvio-padrão/mês", _n(item.get("sigma_mensal"), 1)), ("Prazo de reposição", f'{_n(item.get("lead_time_dias"), 0)} dias'),
             ("Estoque de segurança", _n(item.get("estoque_seguranca"), 1)), ("Mínimo de exposição", _n(item.get("exposicao_min"), 0)),
             ("Múltiplo de transferência", _n(item.get("multiplo"), 0)), ("Alvo (fica na loja)", _n(item.get("alvo"), 0)),
             ("A retirar", _n(item.get("qtde_retirar"), 0)), ("Cobertura atual", f'{_n(item.get("cobertura_atual_dias"), 0)} dias')]
    if ver_valor:
        pares += [("Custo unitário", _moeda(item.get("custo_unit"))), ("Valor a retirar", _moeda(item.get("valor_retirar")))]
    kv(pares)

    st.markdown("##### Vendas mensais na loja")
    serie = pd.DataFrame({"Mês": _meses_rotulos(), "Peças vendidas": [float(item.get(c) or 0) for c in COLS_VENDA]})
    grafico_barras(serie, "Mês", "Peças vendidas", cor="#006F73", sort=False, altura=200)
    st.caption("O último mês são os últimos 30 dias. A previsão é a suavização exponencial dessas vendas; o desvio-padrão vem dos "
               "erros de previsão mês a mês.")

    st.markdown("##### Estoque do mesmo item no CD e nas outras lojas")
    if outras.empty:
        st.caption("Sem registro nas outras lojas.")
    else:
        nomes = {**NOMES_OUTRAS, **config.NOME_LOJA}
        t = outras[outras["cd_loja"] != item.get("cd_loja")]
        t = t.assign(Loja=t["cd_loja"].map(nomes).fillna(t["cd_loja"])).rename(
            columns={"estoque": "Estoque", "venda_media": "Venda média/mês (3 meses)"})
        tabela_br(t[["Loja", "Estoque", "Venda média/mês (3 meses)"]], hide_index=True, width="stretch")

    st.markdown("##### Cadastro")
    kv([("Aplicação", _t(item.get("aplicacao"))), ("Endereço na loja", _t(item.get("endereco"))), ("Origem", _t(item.get("origem"))),
        ("Dias de loja", _n(item.get("dias_na_loja"), 0)), ("Unidade", _t(cad.get("unidade"))),
        ("Referência original", _t(cad.get("ref_original"))), ("Código de barras", _t(cad.get("cod_barras"))),
        ("Peso (kg)", _n(cad.get("peso_kg"), 1)), ("Dimensões (cm)", _t(cad.get("dimensoes_cm"))),
        ("Fora de linha", "Sim" if cad.get("fora_linha") is True else "Não")])
