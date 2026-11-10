"""Fila e lote do dia (domain/ciclo.py): ordem da fila, margem de candidatos, numeração dos pedidos e quantidades."""
import io

import numpy as np
import pandas as pd

from domain import ciclo as dc

VENDAS_ZERO = {f"v{m:02d}": 0.0 for m in range(1, 13)}


def _linha(sku, fabricante, origem="CD", status="Retornar"):
    return {"sku": sku, "descricao": f"ITEM {sku}", "fabricante": fabricante, "ref_fab": f"R{sku}", "status": status,
            "origem": origem, "estoque": 5.0, "alvo": 0.0, "qtde_fica": 0.0, "qtde_retirar": 5.0, "motivo": "Sem venda em 6 meses"}


def _reval(skus, **mudancas):
    """Revalidação em que todo mundo continua sem venda e com estoque (logo, válido)."""
    base = {"estoque": 5.0, "custo_unit": 40.0, "multiplo": 1.0, "exposicao_min": 0.0, "lead_time_dias": 5.0, "dias_na_loja": 500.0,
            "sazonal": False, "em_promocao": False, **VENDAS_ZERO}
    base.update(mudancas)
    return pd.DataFrame([{"sku": c, **base} for c in skus])


def _fila(n):
    return [{"id": i, "sku": f"{i:06d}"} for i in range(n)]


def test_fila_comeca_pelo_cd_e_agrupa_por_fabricante():
    df = pd.DataFrame([_linha("000003", "B"), _linha("000001", "A", origem="Transferência — Sul"), _linha("000002", "A"),
                       _linha("000006", "A", origem="Compra direta"), _linha("000004", "A", status="")])
    # CD (A, depois B), depois transferência, depois compra direta; quem não está na lista fica de fora
    assert [i["sku"] for i in dc.montar_fila(df)] == ["000002", "000003", "000001", "000006"]


def test_dentro_do_bloco_ordena_por_fabricante_e_referencia_e_sem_fabricante_vai_por_ultimo():
    df = pd.DataFrame([_linha("000009", "B"), _linha("000007", "A"), _linha("000001", ""), _linha("000002", "A")])
    assert [i["sku"] for i in dc.montar_fila(df)] == ["000002", "000007", "000009", "000001"]


def test_numeracao_do_pedido_comeca_em_1_e_respeita_skus_por_pedido():
    res = dc.processar_lote(_fila(12), _reval(f"{i:06d}" for i in range(12)), ["Ana", "João"],
                            pedidos_por_pessoa=2, skus_por_pedido=3)  # 2 x 2 x 3 = 12 por dia
    assert [(it["responsavel"], it["grupo"]) for it in res["lote"]] == (
        [("Ana", 1)] * 3 + [("Ana", 2)] * 3 + [("João", 1)] * 3 + [("João", 2)] * 3)


def test_revalida_o_dobro_do_necessario_como_margem():
    res = dc.processar_lote(_fila(50), _reval(f"{i:06d}" for i in range(50)), ["Ana"], 1, 5)  # 5 por dia
    assert res["candidatos"] == 10 and len(res["lote"]) == 5 and len(res["sobra"]) == 5


def test_lote_traz_as_quantidades_da_revalidacao_de_hoje():
    # Entrou na fila com estoque 20 (fica 0, retira 20); hoje o estoque é 12 e voltou a vender: a conta é a de hoje.
    fila = [{"id": 1, "sku": "000001", "estoque": 20.0, "alvo": 0.0, "qtde_fica": 0.0, "qtde_retirar": 20.0}]
    reval = _reval(["000001"], estoque=12.0, **{f"v{m:02d}": 3.0 for m in range(1, 13)})
    item = dc.processar_lote(fila, reval, ["Ana"], 1, 1)["lote"][0]
    assert item["estoque"] == 12.0 and item["alvo"] > 0
    assert item["qtde_fica"] == item["alvo"] and item["qtde_retirar"] == 12.0 - item["alvo"]


def test_item_que_deixou_de_ser_giro_reverso_sai_do_lote():
    fila = _fila(3)
    reval = _reval(["000000", "000002"])  # o 000001 não voltou na revalidação
    res = dc.processar_lote(fila, reval, ["Ana"], 1, 2)
    assert [i["id"] for i in res["lote"]] == [0, 2] and [i["id"] for i in res["invalidos"]] == [1]


# ── lista por comprador: o nome do link (?pessoa=) ou da escolha anterior → a pessoa do ciclo ──────────────
PESSOAS = ["Bruno", "Ana Paula", "João"]


def test_acha_a_pessoa_sem_diferenciar_maiuscula_acento_nem_espaco_nas_pontas():
    assert dc.achar_pessoa("bruno", PESSOAS) == "Bruno"
    assert dc.achar_pessoa("  ANA PAULA ", PESSOAS) == "Ana Paula"
    assert dc.achar_pessoa("joao", PESSOAS) == "João"  # o link vem sem acento
    assert dc.achar_pessoa("JOÃO", PESSOAS) == "João"


def test_nome_que_nao_esta_no_ciclo_ou_vazio_nao_acha_ninguem():
    for nome in ("Carlos", "Bru", "Bruno Silva", "", "   ", None):
        assert dc.achar_pessoa(nome, PESSOAS) is None, nome
    assert dc.achar_pessoa("Compras", []) is None


# ── o pedido do ERP não pode tirar mais do que o Retirar do lote ─────────────
def _lote(**retirar):
    return pd.DataFrame([{"sku": c, "qtde_retirar": q} for c, q in retirar.items()])


def _itens_do_pedido(*linhas):
    return pd.DataFrame([{"sku": c, "qtde": q} for c, q in linhas], columns=["sku", "qtde"])


def test_pedido_que_tira_mais_do_que_o_retirar_e_apontado_com_as_duas_quantidades():
    lote = _lote(**{"000001": 5.0, "000002": 5.0})
    assert dc.quantidades_a_mais(lote, _itens_do_pedido(("000001", 6.0), ("000002", 5.0))) == [
        {"sku": "000001", "pedido": 6.0, "retirar": 5.0}]


def test_pedido_com_a_quantidade_do_retirar_ou_menos_passa():
    lote = _lote(**{"000001": 5.0, "000002": 5.0})
    assert dc.quantidades_a_mais(lote, _itens_do_pedido(("000001", 5.0), ("000002", 4.0))) == []   # igual e menor
    assert dc.quantidades_a_mais(lote, _itens_do_pedido(("000001", 5.0 + 1e-12))) == []             # ruído de ponto flutuante não conta


def test_a_quantidade_de_um_produto_soma_as_linhas_dos_dois_lados():
    lote = pd.DataFrame([{"sku": "000001", "qtde_retirar": 2.0}, {"sku": "000001", "qtde_retirar": 3.0}])   # 5 no lote, em duas linhas
    assert dc.quantidades_a_mais(lote, _itens_do_pedido(("000001", 3.0), ("000001", 2.0))) == []
    assert dc.quantidades_a_mais(lote, _itens_do_pedido(("000001", 3.0), ("000001", 2.5))) == [
        {"sku": "000001", "pedido": 5.5, "retirar": 5.0}]


def test_a_conta_ignora_espaco_no_codigo_e_so_olha_o_que_esta_nos_dois_lados():
    assert dc.quantidades_a_mais(_lote(**{"000001": 5.0}), _itens_do_pedido((" 000001 ", 7.0))) == [
        {"sku": "000001", "pedido": 7.0, "retirar": 5.0}]
    # fora do lote é outra trava (produto a mais); lote sem Retirar (antigo, de antes da coluna) não tem com o que comparar
    sem_retirar = pd.DataFrame([{"sku": "000001", "qtde_retirar": np.nan}])
    assert dc.quantidades_a_mais(sem_retirar, _itens_do_pedido(("000001", 9.0), ("999999", 9.0))) == []


def test_lote_ou_pedido_vazio_nao_aponta_nada():
    assert dc.quantidades_a_mais(pd.DataFrame(), _itens_do_pedido(("000001", 1.0))) == []
    assert dc.quantidades_a_mais(_lote(**{"000001": 5.0}), _itens_do_pedido()) == []


def test_planilha_do_dia_traz_as_colunas_do_pedido():
    lote = [{"sku": "000001", "descricao": "A", "responsavel": "Ana", "grupo": 1, "estoque": 8.0, "alvo": 1.0,
             "qtde_fica": 1.0, "qtde_retirar": 7.0, "origem": "CD"},
            {"sku": "000002", "descricao": "B", "responsavel": "Ana", "grupo": 1, "estoque": 3.0, "qtde_fica": 1.0,
             "qtde_retirar": 2.0}]
    planilha = pd.read_excel(io.BytesIO(dc.planilha_dia_xlsx(lote)))
    assert list(planilha["Qtde a retirar"]) == [7.0, 2.0] and list(planilha["Qtde fica"]) == [1.0, 1.0]
    assert list(planilha.columns) == ["Responsável", "Pedido", "SKU", "Descrição", "Ref. fabricante", "Fabricante",
                                      "Estoque revalidado", "Alvo", "Qtde fica", "Qtde a retirar", "Origem"]
