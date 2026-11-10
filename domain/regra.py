"""
Regra da lista: o que volta da loja para o CD.

Política de estoque por revisão periódica ("order-up-to"). Para cada item de cada loja:

    1. previsão:   μ = nível final da suavização exponencial simples das vendas mensais (α = config.ALFA_SUAVIZACAO)
    2. incerteza:  σ = desvio-padrão dos erros de previsão um passo à frente, com piso de Poisson √μ
    3. alvo:       S = d·(C + L) + z·σ·√((C + L)/30)      d = μ/30, C = cobertura-alvo, L = prazo de reposição, z = Φ⁻¹(nível de serviço)
                   S sobe para o mínimo de exposição (planograma) e para o próximo múltiplo de transferência
    4. retirar:    estoque − S, arredondado PARA BAIXO no múltiplo de transferência (não se abre embalagem)
    5. exclusões, vale a primeira: nada a retirar → lançamento (pouco tempo de loja) → sazonal → em promoção → abaixo do valor mínimo

O motivo descreve o item que entra na lista: sem venda em 6 meses, sem venda em 90 dias ou excesso sobre o alvo.

Funções puras (pandas/NumPy), vetorizadas, sem Streamlit nem banco. A identificação (`ui/cache.py`), a revalidação do ciclo
(`domain/ciclo.py`) e a tela de sensibilidade chamam a mesma `calcular`.
"""
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np
import pandas as pd

import config

STATUS_NA_LISTA = "Retornar"
MOTIVOS = ("Sem venda em 6 meses", "Sem venda em 90 dias", "Excesso sobre o alvo")
EXCLUSOES = ("nada a retirar", "lançamento", "sazonal", "em promoção", "abaixo do valor mínimo")
COLS_VENDA = [f"v{m:02d}" for m in range(1, config.MESES_HISTORICO + 1)]   # v01 = mês mais antigo, v12 = últimos 30 dias
EPS = 1e-9


@dataclass(frozen=True)
class Parametros:
    nivel_servico: float = config.NIVEL_SERVICO_PADRAO
    cobertura_dias: int = config.COBERTURA_DIAS_PADRAO
    valor_minimo: float = config.VALOR_MINIMO_PADRAO
    dias_min_loja: int = config.DIAS_MIN_NA_LOJA
    alfa: float = config.ALFA_SUAVIZACAO

    def __post_init__(self):
        if not 0.5 <= self.nivel_servico < 1:
            raise ValueError("nível de serviço deve estar entre 0,5 e 1 (exclusive)")
        if self.cobertura_dias < 0 or self.valor_minimo < 0 or self.dias_min_loja < 0:
            raise ValueError("cobertura, valor mínimo e dias mínimos não podem ser negativos")
        if not 0 < self.alfa <= 1:
            raise ValueError("alfa deve estar em (0, 1]")

    @property
    def z(self) -> float:
        return NormalDist().inv_cdf(self.nivel_servico)


def _numero(df: pd.DataFrame, coluna: str, padrao: float = 0.0) -> np.ndarray:
    if coluna not in df.columns:
        return np.full(len(df), padrao)
    return pd.to_numeric(df[coluna], errors="coerce").fillna(padrao).to_numpy(dtype=float)


def _flag(df: pd.DataFrame, coluna: str) -> np.ndarray:
    if coluna not in df.columns:
        return np.zeros(len(df), dtype=bool)
    return df[coluna].fillna(False).astype(bool).to_numpy()


def vendas(df: pd.DataFrame) -> np.ndarray:
    """Matriz (itens × meses) das vendas mensais, do mês mais antigo ao mais recente; mês ausente vale 0."""
    return np.column_stack([_numero(df, c) for c in COLS_VENDA]) if len(df) else np.zeros((0, len(COLS_VENDA)))


def previsao_ses(historico: np.ndarray, alfa: float) -> tuple[np.ndarray, np.ndarray]:
    """Suavização exponencial simples, linha a linha (vetorizada nas linhas).
    Nível inicial = média dos 3 primeiros meses. Devolve (previsão para o próximo mês, desvio-padrão dos erros um passo à frente
    a partir do 4º mês). Com menos de 3 erros o desvio volta 0 (quem chama aplica o piso de Poisson)."""
    n, m = historico.shape
    if n == 0:
        return np.zeros(0), np.zeros(0)
    nivel = historico[:, :3].mean(axis=1)
    erros = []
    for t in range(3, m):
        erros.append(historico[:, t] - nivel)
        nivel = alfa * historico[:, t] + (1 - alfa) * nivel
    sigma = np.std(np.column_stack(erros), axis=1, ddof=1) if len(erros) >= 3 else np.zeros(n)
    return nivel, sigma


def _acima(valor: np.ndarray, multiplo: np.ndarray) -> np.ndarray:
    return np.ceil(valor / multiplo - EPS) * multiplo


def _abaixo(valor: np.ndarray, multiplo: np.ndarray) -> np.ndarray:
    return np.floor(valor / multiplo + EPS) * multiplo


def calcular(df: pd.DataFrame, p: Parametros | None = None) -> pd.DataFrame:
    """A regra sobre as linhas de `df` (estoque, custo_unit, multiplo, exposicao_min, lead_time_dias, v01..v12, dias_na_loja,
    sazonal, em_promocao). Devolve uma cópia rasa com as colunas de decisão:

    `previsao_mensal`, `sigma_mensal`, `estoque_seguranca`, `alvo`, `cobertura_atual_dias`, `qtde_fica`, `qtde_retirar`,
    `valor_estoque`, `valor_retirar`, `motivo` (só de quem entra), `decisao` ("retornar" ou a exclusão que segurou o item) e
    `status` (`STATUS_NA_LISTA` para quem entra na lista). O `df` recebido não é alterado."""
    p = p or Parametros()
    out = df.copy(deep=False)
    hist = vendas(out)
    est = np.maximum(_numero(out, "estoque"), 0.0)
    custo = _numero(out, "custo_unit")
    mult = np.maximum(_numero(out, "multiplo", 1.0), 1.0)
    expo = np.maximum(_numero(out, "exposicao_min"), 0.0)
    lt = np.maximum(_numero(out, "lead_time_dias", 7.0), 0.0)

    mu, sigma_res = previsao_ses(hist, p.alfa)
    sigma = np.maximum(sigma_res, np.sqrt(np.maximum(mu, 0.0)))          # piso de Poisson: venda é contagem
    horizonte = p.cobertura_dias + lt
    es = p.z * sigma * np.sqrt(horizonte / 30.0)
    alvo = np.maximum(mu / 30.0 * horizonte + es, 0.0)
    alvo = np.maximum(_acima(alvo, np.ones_like(alvo)), expo)             # peça inteira e o mínimo de exposição
    alvo = _acima(alvo, mult)                                             # não se abre embalagem
    retirar = _abaixo(np.maximum(est - alvo, 0.0), mult)
    valor_retirar = retirar * custo

    ultimos3, ultimos6 = hist[:, -3:].sum(axis=1), hist[:, -6:].sum(axis=1)
    dias_loja = _numero(out, "dias_na_loja", np.inf)
    decisao = np.select(
        [retirar <= 0, (p.dias_min_loja > 0) & (dias_loja < p.dias_min_loja), _flag(out, "sazonal"), _flag(out, "em_promocao"),
         valor_retirar < p.valor_minimo],
        list(EXCLUSOES), default="retornar")
    na_lista = decisao == "retornar"
    motivo = np.select([ultimos6 == 0, ultimos3 == 0], list(MOTIVOS[:2]), default=MOTIVOS[2])

    with np.errstate(divide="ignore", invalid="ignore"):
        cobertura = np.where(mu > EPS, est / (mu / 30.0), np.inf)
    out["previsao_mensal"] = mu
    out["sigma_mensal"] = sigma
    out["estoque_seguranca"] = es
    out["alvo"] = alvo
    out["cobertura_atual_dias"] = cobertura
    out["qtde_retirar"] = np.where(na_lista, retirar, 0.0)
    out["qtde_fica"] = est - out["qtde_retirar"].to_numpy()
    out["valor_estoque"] = est * custo
    out["valor_retirar"] = np.where(na_lista, valor_retirar, 0.0)
    out["motivo"] = np.where(na_lista, motivo, "")
    out["decisao"] = decisao
    out["status"] = np.where(na_lista, STATUS_NA_LISTA, "")
    return out


def na_lista(df: pd.DataFrame) -> pd.DataFrame:
    """Só os itens que a regra manda devolver."""
    return df[df["status"] == STATUS_NA_LISTA] if "status" in df.columns else df.iloc[0:0]


def metricas(df: pd.DataFrame) -> dict:
    """Totais de uma loja (ou da rede) depois da regra. Dias de estoque = valor em estoque ÷ CMV mensal × 30."""
    lista = na_lista(df)
    cmv = float(df["cmv_mes"].max()) if "cmv_mes" in df.columns and len(df) else 0.0
    valor_est = float(df["valor_estoque"].sum()) if len(df) else 0.0
    valor_ret = float(lista["valor_retirar"].sum())
    return {
        "skus": int(df["sku"].nunique()) if len(df) else 0,
        "skus_lista": int(lista["sku"].nunique()),
        "pecas": float(df["estoque"].sum()) if len(df) else 0.0,
        "pecas_retirar": float(lista["qtde_retirar"].sum()),
        "valor_estoque": valor_est,
        "valor_retirar": valor_ret,
        "cmv_mes": cmv,
        "dias_estoque": valor_est / cmv * 30 if cmv > 0 else None,
        "dias_depois": (valor_est - valor_ret) / cmv * 30 if cmv > 0 else None,
    }
