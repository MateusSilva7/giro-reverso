"""Quem está usando o app nesta sessão (vem do login em app.py)."""
import streamlit as st

import config
from data import estado
from ui.tema import esc


def usuario_atual() -> dict:
    return st.session_state["usuario"]


def escolher_loja(rotulo: str = "Loja") -> tuple[str, str]:
    """(nome, cd_loja) da loja em foco. No modo piloto só há uma — não tem o que
    escolher, então só mostra qual é."""
    if len(config.LOJAS) == 1:
        nome, cd = next(iter(config.LOJAS.items()))
        st.markdown(f'<div class="gr-rotulo">{rotulo}</div><div class="gr-valor">{esc(nome)}</div>',
                    unsafe_allow_html=True)
        return nome, cd
    nome = st.selectbox(rotulo, list(config.LOJAS))
    return nome, config.LOJAS[nome]


def nomes_por_login() -> dict[str, str]:
    u = estado.listar_usuarios()
    nomes = dict(zip(u["login"], u["nome"]))
    nomes.setdefault("erp", "ERP")
    return nomes


def pode_ver_valor(papel: str) -> bool:
    """Valor, custo e preço (R$) só para quem decide compra e preço (config.PAPEIS_VEEM_VALOR).
    Único lugar que decide isso — as telas leem daqui em vez de repetir a condição."""
    return papel in config.PAPEIS_VEEM_VALOR


def pode_montar_lista(papel: str) -> bool:
    """Quem abre o ciclo, gera o dia e encerra o ciclo: a Gestão. O Compras emite os pedidos no ERP e os vincula
    (telas/ciclo.py). Único lugar que decide isso — as telas leem daqui."""
    return papel == "gestor"


def pode_remover_pedido(papel: str) -> bool:
    """Quem tira um pedido vinculado errado do acompanhamento (data/estado.py::remover_vinculo): só a Gestão, que
    responde pela limpeza da lista. Não cancela o pedido no ERP."""
    return papel == "gestor"


def pode_agir_livre(papel: str) -> bool:
    """Só na demonstração: a visão de gestão pode agir livremente (Ciclo, Lista diária,
    Acompanhamento), pra testar o fluxo inteiro sem depender de logar com outro
    papel. Único lugar que decide isso — telas e domain/acompanhamento.py::
    pode_registrar leem daqui em vez de repetir a condição cada um.

    Desligada na operação (piloto e Rede): cada etapa só vale se o setor dono dela confirmou — a gestão só acompanha."""
    return config.MODO_ESBOCO and not config.DADOS_REAIS and papel == "gestor"
