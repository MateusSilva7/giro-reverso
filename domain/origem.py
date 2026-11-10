"""Origem do estoque: de onde veio a última entrada do item na loja (CD, transferência de outra loja ou compra direta).

Devolver ao CD um item que veio do próprio CD é o caminho mais simples (o CD já cadastra e endereça o item); por isso a fila do ciclo
começa por eles. É tag e filtro: não muda a regra nem as quantidades."""
import pandas as pd

CD = "CD"
TRANSFERENCIA = "Transferência"
COMPRA_DIRETA = "Compra direta"
AJUDA = ("De onde veio a última entrada do item na loja: do CD, transferido de outra loja ou de compra direta com o fornecedor. "
         "Devolver ao CD é mais simples quando o item veio de lá.")


def tipo(origem) -> str:
    """O tipo da origem ("Transferência — Norte" → "Transferência")."""
    texto = "" if origem is None or (isinstance(origem, float) and pd.isna(origem)) else str(origem).strip()
    if not texto:
        return COMPRA_DIRETA
    return texto.split("—")[0].strip()


def prioridade(origem) -> int:
    """Ordem da fila: CD primeiro, depois transferência, depois compra direta."""
    return {CD: 0, TRANSFERENCIA: 1}.get(tipo(origem), 2)


def opcoes(frames) -> list[str]:
    """Tipos de origem presentes nas listas, na ordem de prioridade."""
    tipos = {tipo(o) for df in frames if "origem" in df.columns for o in df["origem"]}
    return sorted(tipos, key=lambda t: {CD: 0, TRANSFERENCIA: 1}.get(t, 2))


def filtrar(df: pd.DataFrame, tipos) -> pd.DataFrame:
    """Só as linhas dos tipos escolhidos. Sem escolha, nada é filtrado."""
    if not tipos or "origem" not in df.columns:
        return df
    return df[df["origem"].map(tipo).isin(list(tipos))]
