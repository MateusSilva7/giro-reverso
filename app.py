"""
Giro Reverso — logística reversa de estoque parado (loja → CD) numa rede de autopeças.
Ponto de entrada: tema, login e navegação por papel. Cada tela mora em telas/.
"""
import sys
import time
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

RAIZ = Path(__file__).parent
sys.path.insert(0, str(RAIZ))
load_dotenv(dotenv_path=RAIZ / ".env")

import polars_vivo  # noqa: E402

# Antes de qualquer import de polars nas telas: sem isso o servidor cai depois de ~200 execuções (ver polars_vivo.py).
polars_vivo.iniciar_polars_em_thread_viva()

import config  # noqa: E402
from data import estado  # noqa: E402
from ui import acesso  # noqa: E402
from ui.navegacao import TELAS as TELAS_DO_APP  # noqa: E402
from ui.navegacao import TELAS_POR_PAPEL  # noqa: E402
from ui.tema import aplicar_tema, esc  # noqa: E402

st.set_page_config(page_title=config.TITULO_PAGINA, page_icon="🔄", layout="wide")
aplicar_tema()
estado.inicializar()

# Link de comprador (?pessoa=Nome): guardado já na primeira execução, antes do login, para o Compras cair direto na lista
# dele no Ciclo (a URL perde o ?pessoa= quando se troca de página, e a tela de login vem antes).
if "pessoa_do_link" not in st.session_state:
    st.session_state["pessoa_do_link"] = (st.query_params.get("pessoa") or "").strip() or None
    if st.session_state["pessoa_do_link"]:
        st.session_state["pessoa_preferida"] = st.session_state["pessoa_do_link"]

# Telas e quem vê cada uma (ui/navegacao.py)
TELAS = {chave: st.Page(arquivo, title=titulo, url_path=url) for chave, (arquivo, titulo, url) in TELAS_DO_APP.items()}


def tela_login() -> None:
    st.title("Giro Reverso", anchor=False)
    alvo = f"da loja {config.LOJA_PILOTO}" if config.LOJA_PILOTO else "das lojas da rede"
    st.markdown(f'<p class="gr-lead">Reequilíbrio de estoque {alvo} — da identificação à reintegração ao estoque no CD.</p>',
                unsafe_allow_html=True)
    if not config.DADOS_REAIS:
        st.info("Demonstração com uma rede fictícia de autopeças (5 lojas e 1 CD) e um ERP sintético: nenhum dado é real. "
                "Entre como **Gestão** para ver tudo, ou como cada setor para ver só a parte dele.", icon="ℹ️")
    st.caption(f"Ambiente: {config.ETIQUETA}")
    usuarios = estado.listar_usuarios()
    # Só o papel identifica o acesso (r.nome já é o nome do papel) — sem nome de
    # pessoa, até o login corporativo entrar de verdade.
    rotulos = {r.nome + (f' ({config.NOME_LOJA[r.cd_loja]})' if r.cd_loja else ""): r.login
               for r in usuarios.itertuples()}
    with st.container(border=True):
        escolha = st.selectbox("Entrar como", list(rotulos))
        r = usuarios[usuarios["login"] == rotulos[escolha]].iloc[0]
        codigo = ""
        if acesso.exige_codigo(r["papel"]):
            codigo = st.text_input("Código de acesso", type="password", key="codigo_acesso",
                                   help="O código deste perfil, que a Gestão passou a você.")
        if st.button("Entrar", type="primary"):
            pode_entrar, motivo = acesso.verificar(r["papel"], codigo)
            if not pode_entrar:
                time.sleep(1)  # atrapalha a tentativa em série
                st.error(motivo)
            else:
                cd_loja = r["cd_loja"]
                if r["papel"] == "log_loja" and not cd_loja and len(config.LOJAS) == 1:
                    cd_loja = next(iter(config.LOJAS.values()))  # piloto: só há uma loja pra escolher
                st.session_state["usuario"] = {"login": r["login"], "nome": r["nome"], "papel": r["papel"],
                                               "cd_loja": cd_loja}
                st.rerun()


def tela_escolher_loja() -> None:
    """"Logística da loja" é um acesso único (sem pessoa, sem loja fixa no
    cadastro) — quem entra escolhe em qual loja está atuando nesta sessão."""
    st.title("Escolher loja", anchor=False)
    st.markdown('<p class="gr-lead">Acesso de Logística da loja — escolha em qual loja você está atuando agora.</p>',
                unsafe_allow_html=True)
    with st.container(border=True):
        nome_loja = st.selectbox("Loja", list(config.LOJAS))
        if st.button("Confirmar", type="primary"):
            st.session_state["usuario"]["cd_loja"] = config.LOJAS[nome_loja]
            st.rerun()


usuario = st.session_state.get("usuario")
if not usuario:
    st.navigation([st.Page(tela_login, title="Entrar")], position="hidden").run()
    st.stop()

if usuario["papel"] == "log_loja" and not usuario["cd_loja"]:
    st.navigation([st.Page(tela_escolher_loja, title="Escolher loja")], position="hidden").run()
    st.stop()

with st.sidebar:
    # usuario["nome"] já é o nome do papel (ex.: "Gestão") — sem pessoa, sem
    # repetir a mesma palavra duas vezes.
    loja = f' ({config.NOME_LOJA[usuario["cd_loja"]]})' if usuario["cd_loja"] else ""
    st.markdown(f'<div class="gr-rotulo">Conectado como</div><div class="gr-valor">{esc(usuario["nome"])}{esc(loja)}</div>',
                unsafe_allow_html=True)
    st.markdown(f'<div class="gr-rotulo">Ambiente</div><div class="gr-valor">{esc(config.ETIQUETA)}</div>',
                unsafe_allow_html=True)
    if usuario["papel"] == "log_loja" and len(config.LOJAS) > 1 and st.button("Trocar loja"):
        st.session_state["usuario"]["cd_loja"] = None
        st.rerun()
    if st.button("Trocar usuário"):
        st.session_state.clear()
        st.rerun()
    if config.MODO_ESBOCO and not config.DADOS_REAIS:  # só a demonstração avisa que há pedido fictício; com dados reais a barra não leva comentário
        st.caption("Demonstração — rede, produtos, vendas e pedidos são sintéticos. Os pedidos marcados como exemplo já nascem "
                   "em etapas diferentes do acompanhamento.")

chaves = list(TELAS_POR_PAPEL[usuario["papel"]])
if usuario["papel"] == "compras" and st.session_state.get("pessoa_do_link") and "ciclo" in chaves:
    chaves.insert(0, chaves.pop(chaves.index("ciclo")))  # entrou pelo link de um comprador: cai direto no Ciclo (1ª tela)
paginas = [TELAS[k] for k in chaves]
st.navigation(paginas).run()
