"""O componente do kanban é HTML/JS estático, sem build: estes testes travam o que não pode regredir
sem ninguém perceber (o "Abrir detalhe" acionável por teclado e a origem das mensagens). O comportamento
em si foi validado num navegador de verdade — ver F14 em ANALISE_DEBUG_2026-10-06.md."""
import re
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / "ui" / "kanban_component" / "index.html").read_text(encoding="utf-8")


def test_abrir_detalhe_e_um_botao_de_verdade():
    # <button> entra na ordem do Tab e responde a Enter/Espaço; um <div>/<span>/<a> clicável não faz isso
    assert '<button type="button" class="kb-open">Abrir detalhe</button>' in HTML
    assert not re.search(r'<(?!button\b)\w+[^>]*class="kb-open"', HTML)


def test_botao_tem_nome_acessivel_com_o_pedido_e_foco_visivel():
    assert 'setAttribute("aria-label"' in HTML and '"Abrir detalhe do pedido "' in HTML
    assert ".kb-open:focus-visible" in HTML
    assert 'setAttribute("aria-current", "true")' in HTML  # o card aberto no detalhe


def test_rotulo_so_entra_como_atributo_nunca_como_html():
    usos = [linha for linha in HTML.splitlines() if "card.rotulo" in linha]
    assert len(usos) == 1 and 'setAttribute("aria-label"' in usos[0]


def test_so_a_pagina_que_hospeda_o_iframe_redesenha_o_quadro():
    # outra janela (o iframe irmão de um componente de terceiros, por exemplo) não pode mandar "streamlit:render"
    ouvinte = HTML.split('window.addEventListener("message"', 1)[1]
    assert ouvinte.index("event.source !== window.parent") < ouvinte.index("desenhar(")


def test_so_os_pontos_conhecidos_de_innerhtml():
    # Cada innerHTML é um ponto de XSS se receber texto não escapado. Hoje: limpar o quadro, o nome da coluna e o
    # HTML do card — os dois últimos chegam escapados por esc() em telas/acompanhamento.py. Se aparecer outro,
    # confira a origem do texto e então atualize este teste.
    usos = [linha.strip() for linha in HTML.splitlines() if "innerHTML" in linha]
    assert len(usos) == 3, usos
