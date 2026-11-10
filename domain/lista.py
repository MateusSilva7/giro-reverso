"""Lista diária: o que a regra manda devolver em cada loja, com recorte por motivo, resumo e a tabela da tela."""
import pandas as pd

from domain.regra import MOTIVOS, na_lista

TODA_A_LISTA = "Toda a lista"
OPCOES = (TODA_A_LISTA, *MOTIVOS)
EXPLICACAO = {
    "Sem venda em 6 meses": "Nenhuma venda nos últimos 6 meses: volta tudo o que passa do mínimo de exposição.",
    "Sem venda em 90 dias": "Vendeu entre 4 e 6 meses atrás, mas nada nos últimos 90 dias.",
    "Excesso sobre o alvo": "Vende, mas o estoque passa do alvo (previsão de demanda para a cobertura + estoque de segurança).",
}


def filtrar(df_loja: pd.DataFrame, motivo: str = TODA_A_LISTA) -> pd.DataFrame:
    base = na_lista(df_loja)
    return base if motivo == TODA_A_LISTA else base[base["motivo"] == motivo]


def resumo(df: pd.DataFrame) -> dict:
    return {"produtos": len(df), "pecas": int(df["qtde_retirar"].fillna(0).sum()) if len(df) else 0,
            "valor": float(df["valor_retirar"].fillna(0).sum()) if len(df) else 0.0}


COLUNAS = {"sku": "SKU", "descricao": "Descrição", "categoria": "Categoria", "fabricante": "Fabricante", "ref_fab": "Ref. fabricante",
           "estoque": "Estoque", "alvo": "Alvo", "qtde_retirar": "A retirar", "previsao_mensal": "Previsão/mês",
           "cobertura_atual_dias": "Cobertura (dias)", "motivo": "Motivo", "origem": "Origem", "endereco": "Endereço",
           "valor_retirar": "Valor a retirar (R$)"}


def tabela_exibicao(df: pd.DataFrame) -> pd.DataFrame:
    ok = {k: v for k, v in COLUNAS.items() if k in df.columns}
    t = df[list(ok)].rename(columns=ok).sort_values("Valor a retirar (R$)", ascending=False)
    if "Cobertura (dias)" in t.columns:  # sem venda prevista a cobertura é infinita: na tela vira vazio ("—")
        t["Cobertura (dias)"] = t["Cobertura (dias)"].replace([float("inf")], float("nan")).round(0)
    if "Previsão/mês" in t.columns:
        t["Previsão/mês"] = t["Previsão/mês"].round(1)
    return t
