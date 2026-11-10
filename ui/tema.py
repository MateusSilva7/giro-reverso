"""
Tema visual e componentes HTML pequenos (badges, stepper, avisos) e o formato brasileiro dos números: num_br para texto e
métrica, tabela_br no lugar do st.dataframe e grafico_barras no lugar do st.bar_chart (ponto de milhar, vírgula decimal).

Contraste medido contra o fundo da página (#F6F7F7) e contra branco (cards):
  texto primário #2E2D2C  ~12,9:1 · secundário #595C5C ~6,3:1
  terciário #666969 ~5,1:1 · marca #006F73 ~5,5:1
Todos passam no WCAG AA. O ink-500 (#7A7C7C) dos mockups antigos dá 3,9:1 e
reprova — por isso não é usado aqui.

Regra de HTML no st.markdown: cada pedaço numa linha só. Indentação de 4+ espaços
vira bloco de código no markdown do Streamlit em vez de HTML.
"""
import html
import numbers
import re

import pandas as pd
import streamlit as st
from pandas.api import types as tipos
from pandas.io.formats.style import Styler

import config
from config import ETAPAS

CSS = """
<style>
:root {
  --fg-primary: #2E2D2C; --fg-secondary: #595C5C; --fg-tertiary: #666969;
  --brand: #006F73; --navy: #1B314D;
  --surface: #FFFFFF; --surface-muted: #F0F2F2; --border: #DDE2E1;
  --ok-bg: #E6F5EF; --ok-fg: #1D6B4F; --ok-edge: #1F7A58;
  --alert-bg: #FBE9E7; --alert-fg: #A5322A; --alert-edge: #C0392B;
  --done-bg: #EEF0F0; --done-fg: #4A4948;
}
h1, h2, h3 { color: var(--brand) !important; }
[data-testid="stMainBlockContainer"] { padding-top: 2.2rem; }
[data-testid="stCaptionContainer"] { color: var(--fg-secondary) !important; opacity: 1 !important; }
[data-testid="stMetric"] { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 10px 14px; }
/* O valor encolhe em janela estreita em vez de virar "R$ 1,60…" (reticências escondem o número) */
[data-testid="stMetricValue"] { color: var(--brand) !important; font-size: clamp(1.45rem, 2.1vw, 2.25rem) !important; }

.gr-lead { color: var(--fg-secondary); font-size: .95rem; margin: -.4rem 0 .8rem; }
/* Altura fixa: "Conferência" quebra em 2 linhas e desalinhava as colunas */
.gr-colhead { display: flex; justify-content: space-between; align-items: flex-end; gap: 6px; min-height: 2.9em;
  font-size: .78rem; font-weight: 800; text-transform: uppercase; letter-spacing: .03em; color: var(--navy);
  padding: 4px 2px 8px; border-bottom: 2px solid var(--border); margin-bottom: 8px; }
.gr-count { background: var(--surface-muted); color: var(--fg-secondary); border-radius: 999px; padding: 1px 9px; font-size: .72rem; }
.gr-card-cod { font-weight: 800; font-size: .92rem; color: var(--fg-primary); }
.gr-card-sub { font-size: .78rem; color: var(--fg-secondary); margin: 2px 0 6px; }
.gr-card-foot { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; font-size: .74rem; color: var(--fg-tertiary); }
.gr-vazio { font-size: .8rem; color: var(--fg-tertiary); text-align: center; padding: 14px 0; }

.badge { display: inline-flex; align-items: center; gap: 4px; padding: 2px 10px; border-radius: 999px;
  font-size: .74rem; font-weight: 700; white-space: nowrap; line-height: 1.45; }
.badge svg { flex: none; }
.badge-ok      { background: var(--ok-bg); color: var(--ok-fg); }
.badge-alerta  { background: var(--alert-bg); color: var(--alert-fg); }
.badge-fim     { background: var(--done-bg); color: var(--done-fg); }
.badge-exemplo { background: #FDF3E2; color: #7A4E0E; }

.stepper { display: flex; align-items: flex-start; margin: 18px 0 8px; }
.step { flex: 1; display: flex; flex-direction: column; align-items: center; text-align: center; position: relative; }
.step-line { position: absolute; top: 15px; left: -50%; width: 100%; height: 2px; background: #B8BEBD; z-index: 0; }
.step-line.feito { background: var(--ok-edge); }
.step-dot { width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
  font-weight: 800; font-size: .8rem; z-index: 1; box-sizing: border-box; }
.step.feito .step-dot    { background: var(--ok-edge); color: #FFFFFF; }
.step.atual .step-dot    { background: var(--brand); color: #FFFFFF; }
.step.pendente .step-dot { background: var(--surface); color: var(--fg-tertiary); border: 2px solid var(--fg-tertiary); }
.step-nome { font-size: .78rem; margin-top: 6px; }
.step.feito .step-nome    { color: var(--fg-secondary); font-weight: 600; }
.step.atual .step-nome    { color: var(--brand); font-weight: 800; }
.step.pendente .step-nome { color: var(--fg-tertiary); font-weight: 600; }
.step-meta { font-size: .7rem; color: var(--fg-tertiary); margin-top: 2px; line-height: 1.35; }

.callout { display: flex; gap: 8px; align-items: flex-start; padding: 11px 14px; border-radius: 8px; font-size: .9rem; line-height: 1.45; margin: 8px 0; }
.callout svg { flex: none; margin-top: 2px; }
.callout-ok     { background: var(--ok-bg); color: var(--ok-fg); border: 1px solid #BFE4D5; }
.callout-alerta { background: var(--alert-bg); color: var(--alert-fg); border: 1px solid #F0C6C1; }
.callout-info   { background: var(--surface); color: var(--fg-secondary); border: 1px solid var(--border); }
.callout-esboco { background: #FDF3E2; color: #6B4710; border: 1px solid #F0D9A8; }

.gr-rotulo { color: var(--fg-secondary); font-size: .72rem; text-transform: uppercase; letter-spacing: .03em; font-weight: 700; }
.gr-valor  { color: var(--fg-primary); font-size: .95rem; margin-top: 2px; }
/* Ficha: pares rótulo/valor em grade que se ajusta à largura */
.gr-kv { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 12px 18px; margin: 4px 0 10px; }

/* Fila de separação: a linha inteira do Giro Reverso se destaca */
.gr-fila table { border-collapse: separate; border-spacing: 0; width: 100%; font-size: .88rem; color: var(--fg-primary);
  background: var(--surface); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.gr-fila table th { background: var(--surface-muted); color: var(--fg-secondary); text-transform: uppercase; font-size: .72rem;
  letter-spacing: .03em; text-align: left; padding: 9px 12px; border: none; border-bottom: 1px solid var(--border); }
.gr-fila table td { padding: 10px 12px; border: none; border-bottom: 1px solid #EEF0F0; vertical-align: middle; }
.gr-fila table tr.linha-dl td { background: #E6F2F2; }
.gr-fila table tr.linha-dl td:first-child { box-shadow: inset 5px 0 0 var(--brand); font-weight: 800; }
</style>
"""


def aplicar_tema() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def esc(texto) -> str:
    return html.escape("" if texto is None else str(texto))


VAZIO = "—"
# Acima disso a tabela segue como veio: o Styler monta o texto de cada célula (~0,6 s por 80 mil) e o próprio Streamlit
# recusa um Styler com mais células que `styler.render.max_elements` (262.144 por padrão).
LIMITE_CELULAS_TABELA = 250_000
_PRINTF = re.compile(r"([^%]*)%(?:\.(\d+))?[dfi]([^%]*)")


def _texto_br(valor, casas: int) -> str:
    texto = f"{valor:,.{casas}f}".replace(",", "_").replace(".", ",").replace("_", ".")
    # -0,04 com 1 casa daria "-0,0": sem algarismo diferente de zero, o sinal sai
    return texto[1:] if texto.startswith("-") and not any(c in "123456789" for c in texto) else texto


def num_br(valor, casas: int = 1) -> str:
    """8.6 → "8,6", 1234567 → "1.234.567" (casas=0): vírgula decimal e ponto de milhar, como a operação lê. Vazio vira "—"."""
    return VAZIO if valor is None or pd.isna(valor) else _texto_br(valor, casas)


def casas_auto(valor) -> int:
    """Sem casas para número inteiro (1690.0), duas para o fracionário (8.64): a regra de quem não pediu um formato."""
    return 0 if abs(float(valor) - round(float(valor))) < 1e-9 else 2


def _de_printf(formato) -> tuple[str, int, str] | None:
    """O printf do column_config em partes: "R$ %.2f" → ("R$ ", 2, ""), "%d" → ("", 0, ""), "%.1f%%" → ("", 1, "%").
    Outro texto (data, "localized") → None."""
    achado = _PRINTF.fullmatch(formato.replace("%%", "\0")) if isinstance(formato, str) else None
    if achado is None:
        return None
    antes, casas, depois = achado.groups()
    return antes.replace("\0", "%"), int(casas or 0), depois.replace("\0", "%")


def _e_numero(serie: pd.Series) -> bool:
    if tipos.is_bool_dtype(serie):
        return False
    if tipos.is_numeric_dtype(serie):
        return True
    if serie.dtype != object:
        return False
    valores = serie.dropna()  # lista de dicts com vazio vira object: ainda é número se todo o resto for
    return not valores.empty and all(isinstance(v, numbers.Number) and not isinstance(v, bool) for v in valores)


def _casas_da_coluna(serie: pd.Series) -> int:
    if tipos.is_integer_dtype(serie):
        return 0
    numeros = pd.to_numeric(serie, errors="coerce").dropna()
    return 0 if ((numeros - numeros.round()).abs() < 1e-9).all() else 2


def _formatador(antes: str, casas: int, depois: str):
    def formatar(valor) -> str:
        return VAZIO if pd.isna(valor) else f"{antes}{_texto_br(valor, casas)}{depois}"
    return formatar


def _como_codigo(valor) -> str:
    """Número que é código (nota, pedido): como vem, sem ponto de milhar e sem o ".000000" que o pandas poria."""
    if pd.isna(valor):
        return VAZIO
    return str(int(valor)) if float(valor).is_integer() else str(valor)


def _sem_printf(cfg: dict) -> dict:
    """O column_config com o printf tirado: o do Streamlit ("R$ %.2f" → "R$ 266578.68") vale mais que o do Styler."""
    return {**cfg, "type_config": {**cfg["type_config"], "format": None}}


def tabela_br(dados, *, cru=(), **kwargs):
    """`st.dataframe` com os números no formato brasileiro: ponto de milhar e vírgula decimal ("R$ 245.310,57", "2.003", "16,7%"),
    ainda ordenáveis como número. O `column_config` com NumberColumn(format="R$ %.2f" | "%d" | "%.1f%%") segue valendo, só que
    em português; coluna numérica sem formato fica sem casas se todos os valores são inteiros e com duas se não. `cru`: colunas
    numéricas que são código (nota, pedido) e não levam ponto de milhar. Aceita DataFrame ou Styler, devolve o que o
    `st.dataframe` devolve (a seleção de linha) e repassa os demais argumentos. Toda tabela do app passa por aqui."""
    colunas = dict(kwargs.pop("column_config", None) or {})
    base = dados.data if isinstance(dados, Styler) else dados
    if (isinstance(base, pd.DataFrame) and base.columns.is_unique and base.index.is_unique
            and 0 < base.size <= min(LIMITE_CELULAS_TABELA, int(pd.options.styler.render.max_elements))):
        formatos = {}
        for nome in base.columns:
            if not _e_numero(base[nome]):
                continue
            if nome in cru:
                formatos[nome] = _como_codigo
                continue
            cfg = colunas.get(nome)
            config_coluna = cfg.get("type_config") or {} if isinstance(cfg, dict) else {}
            if config_coluna.get("type", "number") != "number":  # barra de progresso etc.: o formato é o da própria coluna
                continue
            partes = _de_printf(config_coluna["format"]) if config_coluna.get("format") else ("", _casas_da_coluna(base[nome]), "")
            if partes is None:  # "localized", "percent"...: quem chama escolheu, não mexo
                continue
            formatos[nome] = _formatador(*partes)
            if config_coluna.get("format"):
                colunas[nome] = _sem_printf(cfg)
        if formatos:
            dados = (dados if isinstance(dados, Styler) else base.style).format(formatos)
    return st.dataframe(dados, column_config=colunas or None, **kwargs)


def _expr_br(valor: str, casas: int) -> str:
    """Expressão do Vega que escreve `valor` em português: sozinho, o gráfico mostra no formato americano (2,000,000)."""
    return (f"replace(replace(replace(format({valor}, ',.{casas}f'), regexp(',', 'g'), '§'), regexp('[.]', 'g'), ','), "
            "regexp('§', 'g'), '.')")


def grafico_barras(dados: pd.DataFrame, x: str, y: str, *, cor: str, horizontal: bool = False, sort: bool = True,
                   altura: int = 240, casas: int = 0) -> None:
    """Barras como as do `st.bar_chart` (mesmo tema), com o eixo e a dica em português: "2.000.000", não "2,000,000".
    `x` é a categoria e `y` o valor, como no st.bar_chart; `sort=False` mantém a ordem dos dados (meses); `casas` vale na dica."""
    categoria = {"field": x, "type": "ordinal", "title": x, "sort": "ascending" if sort else None}
    valor = {"field": y, "type": "quantitative", "title": y, "axis": {"labelExpr": _expr_br("datum.value", 0), "tickMinStep": 1}}
    if not horizontal:
        categoria["axis"] = {"labelAngle": -90}
    spec = {
        "transform": [{"calculate": _expr_br(f"datum['{y}']", casas), "as": "_valor_br"}],
        "mark": {"type": "bar", "color": cor},
        "encoding": {"y": categoria, "x": valor} if horizontal else {"x": categoria, "y": valor},
        "height": altura,
    }
    spec["encoding"]["tooltip"] = [{"field": x, "type": "ordinal", "title": x}, {"field": "_valor_br", "type": "nominal", "title": y}]
    st.vega_lite_chart(dados[[x, y]], spec, width="stretch")


def grafico_linhas(dados: pd.DataFrame, x: str, y: str, serie: str, *, ordem_series: list[str] | None = None, altura: int = 300,
                   casas: int = 0) -> None:
    """Linhas com pontos, uma por `serie`, com os eixos e a dica em português (como `grafico_barras`)."""
    spec = {
        "transform": [{"calculate": _expr_br(f"datum['{y}']", casas), "as": "_valor_br"},
                      {"calculate": _expr_br(f"datum['{x}']", 0), "as": "_x_br"}],
        "mark": {"type": "line", "point": True},
        "encoding": {
            "x": {"field": x, "type": "quantitative", "title": x, "scale": {"zero": False},
                  "axis": {"labelExpr": _expr_br("datum.value", 0)}},
            "y": {"field": y, "type": "quantitative", "title": y, "axis": {"labelExpr": _expr_br("datum.value", 0)}},
            "color": {"field": serie, "type": "nominal", "title": serie, **({"sort": ordem_series} if ordem_series else {})},
            "tooltip": [{"field": serie, "type": "nominal"}, {"field": "_x_br", "type": "nominal", "title": x},
                        {"field": "_valor_br", "type": "nominal", "title": y}],
        },
        "height": altura,
    }
    st.vega_lite_chart(dados[[x, y, serie]], spec, width="stretch")


def icone_alerta(tamanho: int = 12) -> str:
    # Único ícone de status do app: sinaliza pedido parado. Linha fina, sem emoji.
    return (
        f'<svg width="{tamanho}" height="{tamanho}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/>'
        '<path d="M12 9v4"/><path d="M12 17h.01"/></svg>'
    )


def badge_parado(texto: str, alerta: bool, concluido: bool = False) -> str:
    if concluido:
        return f'<span class="badge badge-fim">{esc(texto)}</span>'
    if alerta:
        return f'<span class="badge badge-alerta">{icone_alerta()}{esc(texto)}</span>'
    return f'<span class="badge badge-ok">{esc(texto)}</span>'


def badge_exemplo() -> str:
    return '<span class="badge badge-exemplo">exemplo</span>'


def callout(texto_html: str, tipo: str = "info") -> str:
    icone = icone_alerta(16) if tipo == "alerta" else ""
    papel = ' role="alert"' if tipo == "alerta" else ""
    return f'<div class="callout callout-{tipo}"{papel}>{icone}<div>{texto_html}</div></div>'


def aviso_esboco(texto: str) -> None:
    """Nota explicativa da tela, só da demonstração: leva o rótulo "Demonstração:". Com dados reais (piloto e Rede) não aparece
    nada: o app é de verdade e a tela não carrega comentário de bastidor."""
    if config.DADOS_REAIS:
        return
    st.markdown(callout(f"<b>Demonstração:</b> {esc(texto)}", "esboco"), unsafe_allow_html=True)


def stepper_html(indice_atual: int, eventos_por_etapa: dict) -> str:
    """eventos_por_etapa: {chave_etapa: (datahora, usuario)} das etapas já registradas."""
    blocos = []
    for i, etapa in enumerate(ETAPAS):
        estado = "feito" if i < indice_atual else ("atual" if i == indice_atual else "pendente")
        marcador = "✓" if estado == "feito" else str(i + 1)
        registro = eventos_por_etapa.get(etapa["chave"])
        meta = f"{registro[0]:%d/%m %H:%M}<br>{esc(registro[1])}" if registro else "—"
        linha = f'<div class="step-line{" feito" if i <= indice_atual else ""}"></div>' if i > 0 else ""
        blocos.append(
            f'<div class="step {estado}">{linha}<div class="step-dot">{marcador}</div>'
            f'<div class="step-nome">{esc(etapa["nome"])}</div><div class="step-meta">{meta}</div></div>'
        )
    return '<div class="stepper">' + "".join(blocos) + "</div>"
