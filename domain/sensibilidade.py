"""
Sensibilidade da lista aos parâmetros da regra: quanto volta ao CD em cada combinação de nível de serviço × cobertura-alvo.

Mostra o trade-off que a Gestão decide: um nível de serviço mais alto (menos ruptura) ou uma cobertura maior seguram mais estoque
na loja e devolvem menos capital ao CD. Função pura sobre a base já lida do ERP (sem nova leitura).
"""
from dataclasses import replace

import pandas as pd

from domain.regra import Parametros, calcular, na_lista


def grade(base: pd.DataFrame, niveis, coberturas, base_params: Parametros | None = None) -> pd.DataFrame:
    """Uma linha por (nível de serviço, cobertura): itens, peças e valor a retirar, e quanto do estoque o valor representa."""
    p0 = base_params or Parametros()
    valor_estoque = float((pd.to_numeric(base["estoque"], errors="coerce").clip(lower=0)
                           * pd.to_numeric(base["custo_unit"], errors="coerce")).sum()) if len(base) else 0.0
    linhas = []
    for ns in niveis:
        for cob in coberturas:
            lista = na_lista(calcular(base, replace(p0, nivel_servico=float(ns), cobertura_dias=int(cob))))
            valor = float(lista["valor_retirar"].sum())
            linhas.append({"nivel_servico": float(ns), "cobertura_dias": int(cob), "itens": int(len(lista)),
                           "pecas": float(lista["qtde_retirar"].sum()), "valor": valor,
                           "pct_estoque": valor / valor_estoque * 100 if valor_estoque else 0.0})
    return pd.DataFrame(linhas)


def matriz(g: pd.DataFrame, valor: str = "valor") -> pd.DataFrame:
    """A grade como tabela: nível de serviço nas linhas, cobertura nas colunas."""
    return g.pivot(index="nivel_servico", columns="cobertura_dias", values=valor).sort_index()
