"""
Wrapper Python do componente de kanban com arrastar-e-soltar (ui/kanban_component/).
Componente "declarado" apontando pra uma pasta estática — sem build, sem pacote npm.
"""
from pathlib import Path

import streamlit.components.v1 as components

_component = components.declare_component("dl_kanban", path=str(Path(__file__).parent / "kanban_component"))


def quadro_kanban(colunas: list[dict], estilo: str, selecionado: int | None, key: str) -> dict | None:
    """colunas: [{"nome": str, "cards": [{"id", "status", "html", "rotulo"}], "nota_extra": str|None}].
    "html" é HTML (quem chama escapa com esc()); "rotulo" é texto puro, só o nome acessível do botão.
    Devolve None ou {"acao": "abrir"|"mover", "pedido_id": int, "seq": int, ...}."""
    # ui/tema.py::CSS traz as tags <style>/</style> literais (feita pra st.markdown).
    # O componente injeta isso como o TEXTO de dentro de um <style> real — se as tags
    # vierem juntas, o parser CSS descarta o bloco :root{...} inteiro (token sobrando
    # antes do primeiro seletor). Manda só o miolo.
    estilo_sem_tags = estilo.replace("<style>", "").replace("</style>", "")
    return _component(colunas=colunas, estilo=estilo_sem_tags, selecionado=selecionado, key=key, default=None)
