"""
Exporta a base do ERP sintético para a demonstração estática (GitHub Pages), que roda a mesma regra em JavaScript no navegador.

    python ferramentas/exportar_demo_estatica.py PASTA_DE_SAIDA

Grava PASTA/dados.js (window.GR_DADOS = {...}) e PASTA/referencia.json (o resultado da regra em Python com os parâmetros padrão,
para conferir que a versão em JavaScript dá os mesmos números).
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from data import erp  # noqa: E402
from data.estado import _VINCULOS_EXEMPLO  # noqa: E402
from domain.regra import calcular  # noqa: E402


def _num(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    f = float(v)
    return int(f) if f.is_integer() else round(f, 2)


def main(saida: Path) -> None:
    saida.mkdir(parents=True, exist_ok=True)
    base = erp.ler_base()
    itens = [{
        "l": r.cd_loja, "s": r.sku, "d": r.descricao, "c": r.categoria, "f": r.fabricante, "r": r.ref_fab, "a": r.aplicacao,
        "en": r.endereco, "o": r.origem, "e": _num(r.estoque), "u": _num(r.custo_unit), "m": _num(r.multiplo),
        "x": _num(r.exposicao_min), "t": _num(r.lead_time_dias), "v": [int(getattr(r, c)) for c in erp.COLS_VENDA],
        "n": _num(r.dias_na_loja), "z": int(bool(r.sazonal)), "p": int(bool(r.em_promocao)),
    } for r in base.itertuples(index=False)]
    cmv = {cd: _num(g["cmv_mes"].max()) for cd, g in base.groupby("cd_loja")}
    cmv_mensal = {cd: [[str(m), _num(v)] for m, v in erp.ler_cmv_mensal(cd).itertuples(index=False)] for cd in config.LOJAS.values()}
    pedidos = []
    for k, (cd, num, serie, h_em, h_sep, confirmacoes) in enumerate(_VINCULOS_EXEMPLO):
        eventos = [["emitido", h_em]] + ([["separacao", h_sep]] if h_sep is not None else []) + [list(c) for c in confirmacoes]
        pedidos.append({"loja": cd, "numero": num, "serie": serie, "lote": f"EX-{7001 + k}", "eventos": eventos,
                        "itens": [[s, q] for s, q in erp._itens_do_lote(cd, 3 + k % 4, semente=k)]})
    dados = {
        "gerado_em": config.agora().strftime("%Y-%m-%d"),
        "lojas": config.NOME_LOJA, "cmv": cmv, "cmv_mensal": cmv_mensal,
        "padrao": {"nivel_servico": config.NIVEL_SERVICO_PADRAO, "cobertura_dias": config.COBERTURA_DIAS_PADRAO,
                   "valor_minimo": config.VALOR_MINIMO_PADRAO, "dias_min_loja": config.DIAS_MIN_NA_LOJA, "alfa": config.ALFA_SUAVIZACAO},
        "etapas": [{"chave": e["chave"], "nome": e["nome"], "papel": config.PAPEIS[e["papel"]]} for e in config.ETAPAS],
        "limite_alerta_h": config.LIMITE_ALERTA_HORAS, "meta_ciclo_dias": config.META_CICLO_DIAS,
        "pedidos": pedidos, "itens": itens,
    }
    texto = json.dumps(dados, ensure_ascii=False, separators=(",", ":"))
    (saida / "dados.js").write_text(f"window.GR_DADOS = {texto};\n", encoding="utf-8")
    ref = calcular(base)
    referencia = {f"{r.cd_loja}-{r.sku}": [round(float(r.alvo), 6), round(float(r.qtde_retirar), 6), r.decisao, r.motivo]
                  for r in ref.itertuples(index=False)}
    (saida / "referencia.json").write_text(json.dumps(referencia), encoding="utf-8")
    print(f"{len(itens)} itens · dados.js {len(texto) / 1024:.0f} KB")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "demo_estatica"))
