"""Sensibilidade, origem do estoque e lista diária."""
import pandas as pd

from domain import lista as ld
from domain import origem as og
from domain import sensibilidade as sb
from domain.regra import calcular
from tests.test_regra import _item


def _base():
    return pd.DataFrame([_item(sku="000001", estoque=30.0, vendas=[4, 6, 5, 7, 3, 8, 5, 6, 4, 7, 5, 6]),
                         _item(sku="000002", estoque=12.0, vendas=0.0),
                         _item(sku="000003", estoque=25.0, vendas=[2, 3, 2, 4, 3, 2, 3, 4, 2, 3, 3, 2])])


def test_grade_cobre_todas_as_combinacoes_e_e_monotona():
    g = sb.grade(_base(), (0.8, 0.95, 0.99), (15, 30, 60))
    assert len(g) == 9 and set(g.columns) >= {"nivel_servico", "cobertura_dias", "itens", "pecas", "valor", "pct_estoque"}
    m = sb.matriz(g, "pecas")
    assert (m.diff(axis=0).dropna() <= 0).all().all()   # mais nível de serviço, menos peças voltam
    assert (m.diff(axis=1).dropna(axis=1) <= 0).all().all()   # mais cobertura, menos peças voltam


def test_origem_tipo_prioridade_e_filtro():
    assert og.tipo("Transferência — Norte") == og.TRANSFERENCIA and og.tipo("CD") == og.CD and og.tipo(None) == og.COMPRA_DIRETA
    assert og.prioridade("CD") < og.prioridade("Transferência — Sul") < og.prioridade("Compra direta")
    df = pd.DataFrame({"origem": ["CD", "Transferência — Sul", "Compra direta"]})
    assert og.opcoes([df]) == [og.CD, og.TRANSFERENCIA, og.COMPRA_DIRETA]
    assert list(og.filtrar(df, [og.TRANSFERENCIA])["origem"]) == ["Transferência — Sul"] and len(og.filtrar(df, [])) == 3


def test_lista_filtra_por_motivo_e_resume():
    df = calcular(_base().assign(estoque=[60.0, 12.0, 70.0]))
    toda = ld.filtrar(df)
    assert set(toda["motivo"]) == {"Sem venda em 6 meses", "Excesso sobre o alvo"}
    so_sem_venda = ld.filtrar(df, "Sem venda em 6 meses")
    assert list(so_sem_venda["sku"]) == ["000002"]
    r = ld.resumo(toda)
    assert r["produtos"] == len(toda) and r["pecas"] == int(toda["qtde_retirar"].sum())
    t = ld.tabela_exibicao(toda)
    assert list(t["Valor a retirar (R$)"]) == sorted(t["Valor a retirar (R$)"], reverse=True)
    assert t["Cobertura (dias)"].isna().any()   # sem venda prevista: cobertura infinita vira vazio na tela
