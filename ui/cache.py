"""
Carga da identificação, compartilhada por todas as telas.

cache_resource e não cache_data: cache_data devolve uma cópia a cada chamada, e na base
real (centenas de milhares de linhas) copiar a cada clique sairia mais caro que a própria
consulta. Ninguém escreve nesse resultado — quem filtra faz .copy().
"""
from dataclasses import astuple
from datetime import datetime

import pandas as pd
import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from config import TTL, agora
from data import erp, estado
from data.erp import ler_status_para_vinculos as _ler_status_para_vinculos
from domain.regra import Parametros, calcular

# Quantas consultas diferentes (produtos de uma ficha, pedido aberto no detalhe) cada cache guarda ao mesmo tempo. Cada
# ficha ou pedido aberto vira uma entrada nova; sem teto elas ficam na memória do servidor até o ttl vencer.
MAX_FICHAS = 64


@st.cache_resource(ttl=TTL, show_spinner=False)
def _carregar() -> tuple[pd.DataFrame, datetime]:
    """A base do ERP (estoque, custo, reposição e 12 meses de venda de cada item de cada loja). Cara: lê o ERP. Cache de 30 min."""
    return erp.ler_base(), agora()


@st.cache_resource(ttl=TTL, show_spinner=False, max_entries=4)
def _com_regra(params: tuple, carga: datetime) -> dict[str, pd.DataFrame]:
    """A regra sobre a base, por loja. Barata (vetorizada): trocar um parâmetro na Visão de gestão não relê o ERP. `carga` entra
    na chave para uma recarga da base nunca ficar presa a um resultado velho."""
    df = calcular(_carregar()[0], Parametros(*params))
    return {cd: g.reset_index(drop=True) for cd, g in df.groupby("cd_loja", sort=False)}


def base() -> pd.DataFrame:
    """A base crua do ERP (para a tela de sensibilidade, que roda a regra com outros parâmetros)."""
    return _carregar()[0]


def identificacao_por_loja() -> dict[str, pd.DataFrame]:
    return _com_regra(astuple(estado.parametros_regra()), _carregar()[1])


def identificacao_loja(cd: str) -> pd.DataFrame:
    por_loja = identificacao_por_loja()
    if cd in por_loja:
        return por_loja[cd]
    modelo = next(iter(por_loja.values()), None)
    return modelo.iloc[0:0] if modelo is not None else pd.DataFrame()


def hora_carga() -> datetime:
    return _carregar()[1]


def recarregar_identificacao() -> None:
    _carregar.clear()
    _com_regra.clear()


@st.cache_data(ttl=2, show_spinner=False, max_entries=16)
def status_devolucoes(vinculos: pd.DataFrame) -> pd.DataFrame:
    """Mesmo resultado ao vivo de data/erp.py::ler_status_para_vinculos — só que
    com um TTL curtinho (2s) pra reaproveitar quando o quadro (fragmento) e o
    painel de detalhe pedem os mesmos vínculos na mesma renderização, em vez de
    consultar o ERP 2x. 2s não compromete o "quase ao vivo": o quadro já se
    atualiza sozinho a cada 30s (config.ATUALIZAR_QUADRO_S)."""
    return _ler_status_para_vinculos(vinculos)


@st.cache_data(ttl=TTL, show_spinner=False, max_entries=MAX_FICHAS)
def cadastro_produtos(skus: tuple[str, ...]) -> pd.DataFrame:
    return erp.ler_cadastro_produtos(list(skus))


@st.cache_data(ttl=TTL, show_spinner=False, max_entries=MAX_FICHAS)
def outras_lojas(skus: tuple[str, ...]) -> pd.DataFrame:
    return erp.ler_outras_lojas(list(skus))


@st.cache_data(ttl=TTL, show_spinner=False)
def cmv_mensal(cd: str) -> pd.DataFrame:
    return erp.ler_cmv_mensal(cd)


@st.cache_data(ttl=TTL, show_spinner=False)
def devolucoes_giro_reverso(cd: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    return erp.ler_devolucoes_giro_reverso(cd)


@st.cache_data(ttl=TTL, show_spinner=False)
def devolucoes_por_tipo(cd: str) -> pd.DataFrame:
    return erp.ler_devolucoes_por_tipo(cd)


@st.cache_data(ttl=15, show_spinner=False, max_entries=MAX_FICHAS)
def pedido_devolucao(cd: str, num_pedido: str, serie: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Quase ao vivo: é o pedido que o time está acompanhando agora."""
    return erp.ler_pedido_devolucao(cd, num_pedido, serie)


# Falha de banco (ERP fora, rede caída, credencial errada). Bug de código (KeyError, TypeError...)
# fica de fora de propósito: continua aparecendo como erro em vez de virar "verifique a conexão".
ERRO_BANCO = SQLAlchemyError


def mensagem_sem_banco(erro: Exception) -> None:
    st.error("Não foi possível ler o ERP. Tente de novo em instantes.")
    # O texto do erro traz host, porta e SQL do banco: só a Gestão (quem investiga) vê.
    if st.session_state.get("usuario", {}).get("papel") == "gestor":
        with st.expander("Detalhe do erro"):
            st.code(f"{type(erro).__name__}: {erro}")
