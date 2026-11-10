"""
Constantes do Giro Reverso.
Tudo que é "número mágico" ou configuração fica aqui, nunca espalhado pelo código.

Versão de portfólio: a rede, as lojas, os códigos e os dados são fictícios (o ERP é sintético, ver data/erp.py).
"""
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# ============================================================
# REDE FICTÍCIA
# ============================================================

# Lojas da rede: nome → cd_loja (o CD é o 01)
LOJAS_TODAS = {
    "Centro": "03",
    "Norte":  "04",
    "Sul":    "05",
    "Leste":  "06",
    "Oeste":  "07",
}
LOJAS_CDS = tuple(LOJAS_TODAS.values())
NOME_LOJA = {cd: nome for nome, cd in LOJAS_TODAS.items()}

# Horário da operação, seja qual for o fuso do servidor onde o app roda: as datas do ERP chegam sem fuso e o alerta de
# 48 h e as gravações precisam falar a mesma hora.
FUSO = ZoneInfo("America/Sao_Paulo")


def agora() -> datetime:
    """Agora, no horário da operação e sem fuso (naive), como as datas do ERP."""
    return datetime.now(FUSO).replace(tzinfo=None)


# Cache da identificação (uma leitura das 5 lojas).
TTL = 1800

# ============================================================
# NOVO — acompanhamento, papéis e ciclo
# ============================================================

MODO_ESBOCO = True  # mostra os avisos de "dado de exemplo"

# Modo piloto: GR_LOJA_PILOTO=Sul restringe o app a uma loja (identificação, lista, ciclo, fila e acompanhamento só dela).
# LOJAS_CDS e NOME_LOJA seguem completos: o produto pode ter vindo de outra loja.
LOJA_PILOTO = os.getenv("GR_LOJA_PILOTO", "").strip()
if LOJA_PILOTO and LOJA_PILOTO not in LOJAS_TODAS:
    raise ValueError(f"GR_LOJA_PILOTO inválida: {LOJA_PILOTO!r} (use uma de {', '.join(LOJAS_TODAS)})")
LOJAS = {nome: cd for nome, cd in LOJAS_TODAS.items() if not LOJA_PILOTO or nome == LOJA_PILOTO}

# Operação (sem exemplos): nenhum pedido de exemplo, a gestão só acompanha e o banco é próprio do ambiente. Vale no piloto
# (uma loja) e no modo Rede (GR_DADOS_REAIS=1, as 5 lojas). Sem nenhum dos dois é a demonstração: pedidos de exemplo e
# gestão livre (pode agir como qualquer setor). Se os dois forem pedidos, vale o piloto.
DADOS_REAIS = bool(LOJA_PILOTO) or os.getenv("GR_DADOS_REAIS", "").strip() == "1"
AMBIENTE = "piloto" if LOJA_PILOTO else ("rede" if DADOS_REAIS else "esboco")
NOME_AMBIENTE = {"piloto": f"Piloto {LOJA_PILOTO}", "rede": "Rede", "esboco": "Demonstração"}[AMBIENTE]
TITULO_PAGINA = "Giro Reverso" + {"piloto": f" — {LOJA_PILOTO}", "rede": " — Rede", "esboco": ""}[AMBIENTE]

# Versão do app (arquivo VERSION) e rótulo opcional do ambiente (GR_ROTULO, ex.: "Demonstração"): aparecem na
# tela de entrada e na barra lateral, para ninguém confundir um banco real com uma demonstração.
try:
    VERSAO = (Path(__file__).parent / "VERSION").read_text(encoding="utf-8").strip() or "dev"
except OSError:
    VERSAO = "dev"
ROTULO = os.getenv("GR_ROTULO", "").strip()
ETIQUETA = " · ".join(filter(None, [ROTULO, NOME_AMBIENTE, f"v{VERSAO}"]))

# ============================================================
# REGRA DA LISTA (domain/regra.py): o que volta da loja para o CD
# ============================================================
# Estoque-alvo por revisão periódica: S = d·(C + L) + z·σ·√((C + L)/30), com d a demanda diária prevista (suavização exponencial
# simples das vendas mensais), C a cobertura-alvo, L o prazo de reposição da loja, σ o desvio-padrão mensal do erro de previsão e z o
# quantil normal do nível de serviço. O que passa do alvo (em múltiplos de transferência) volta ao CD. A Gestão ajusta os três
# primeiros na Visão de gestão; estes são os valores de partida.
NIVEL_SERVICO_PADRAO = 0.95       # probabilidade de não faltar o item na loja até a próxima reposição
COBERTURA_DIAS_PADRAO = 30        # dias de venda que a loja mantém além do prazo de reposição
VALOR_MINIMO_PADRAO = 20.0        # R$: item com valor a retirar abaixo disso não compensa o manuseio
ALFA_SUAVIZACAO = 0.3             # peso do mês mais recente na previsão (suavização exponencial simples)
MESES_HISTORICO = 12              # janela de vendas mensais que a previsão lê

# Tempo mínimo de loja: item que entrou há menos que isso ainda não tem histórico de venda suficiente para a previsão.
# GR_DIAS_MIN_NA_LOJA troca o número (0 desliga).
try:
    DIAS_MIN_NA_LOJA = int(os.getenv("GR_DIAS_MIN_NA_LOJA", "").strip() or "90")
except ValueError:
    raise ValueError("GR_DIAS_MIN_NA_LOJA inválida: use o número de dias, inteiro (0 desliga)") from None
if DIAS_MIN_NA_LOJA < 0:
    raise ValueError(f"GR_DIAS_MIN_NA_LOJA inválida: {DIAS_MIN_NA_LOJA!r} (use o número de dias, ou 0 para desligar)")

# Grade da tela de sensibilidade: níveis de serviço × coberturas-alvo.
NIVEIS_SENSIBILIDADE = (0.80, 0.90, 0.95, 0.98, 0.99)
COBERTURAS_SENSIBILIDADE = (15, 30, 45, 60, 90)

# O que o Compras escreve na observação do pedido no ERP para o pedido ser reconhecido como Giro Reverso (o Ciclo mostra o texto para
# copiar); a conferência do vínculo só exige "GIRO REVERSO" dentro da observação (data/erp.py::OBSERVACAO_GIRO_REVERSO).
OBSERVACAO_PADRAO_PEDIDO = "DEVOLUCAO LOJA > CD - GIRO REVERSO"
SERIE_PEDIDO = "DV"   # série padrão do pedido de devolução no ERP

# Leitura do banco de acompanhamento (nosso, pequeno): quase ao vivo
TTL_AO_VIVO = 15
# O quadro se redesenha sozinho a cada N segundos
ATUALIZAR_QUADRO_S = 30

# Endereço do app como os setores o abrem, para montar o link de cada comprador (?pessoa=Nome). Atrás de um proxy, o app fica num
# caminho (ex.: https://servidor/giro-reverso) e o link montado só com o host abriria a raiz: preencha GR_URL_BASE.
# Vazio = o endereço que o navegador abriu.
URL_BASE = os.getenv("GR_URL_BASE", "").strip().rstrip("/")

# Pedido parado na mesma etapa por mais que isso (horas corridas desde a última mudança) fica em atenção.
LIMITE_ALERTA_HORAS = 48

# Onde o app grava o acompanhamento quando ESTADO_DB_URL está vazio no .env (um arquivo por ambiente, pra não
# misturar o acompanhamento do piloto, o da rede e o da demonstração)
_SUFIXO_BANCO = {"piloto": f"_{LOJA_PILOTO.lower()}", "rede": "_rede", "esboco": ""}[AMBIENTE]
CAMINHO_ESTADO_LOCAL = Path(__file__).parent / "dados_locais" / f"acompanhamento{_SUFIXO_BANCO}.db"

PAPEIS = {
    "compras":  "Compras",
    "log_loja": "Logística da loja",
    "log_cd":   "Logística do CD",
    "qualidade": "Qualidade",
    "gestor":   "Gestão",
}

# Código de acesso por perfil (GR_CODIGO_GESTOR, GR_CODIGO_COMPRAS, GR_CODIGO_LOG_LOJA, GR_CODIGO_LOG_CD, GR_CODIGO_QUALIDADE).
# Não é login de verdade: é um código por setor, compartilhado. Na operação o perfil sem código configurado não entra; na
# demonstração o código só vale se estiver configurado.
CODIGOS_ACESSO = {papel: os.getenv(f"GR_CODIGO_{papel.upper()}", "").strip() for papel in PAPEIS}
EXIGE_CODIGO = DADOS_REAIS

# Quem vê valor, custo e preço (R$). A Logística e a Qualidade operam o pedido (número, série, etapa)
# e não precisam do valor; Compras e Gestão sim. Mudou a regra? É só editar este conjunto.
PAPEIS_VEEM_VALOR = {"compras", "gestor"}

# Tipos de pedido. Aberto de propósito: um tipo novo entra como mais uma linha aqui e na tabela tipo_pedido — nenhuma
# tela depende de só existirem estes dois.
TIPOS_PEDIDO = {
    "giro_reverso":  "Giro Reverso",
    "venda_normal": "Venda normal",
}

# Etapas do acompanhamento, na ordem, com o papel que REGISTRA cada uma. Bloquear o item para venda faz parte do Emitido;
# a Qualidade, no CD, é dona das duas últimas etapas.
ETAPAS = [
    {"chave": "emitido",     "nome": "Emitido",             "papel": "compras",
     "descricao": "Compras bloqueou os itens para venda e emitiu o pedido de devolução no ERP."},
    {"chave": "separacao",   "nome": "Separação",           "papel": "log_loja",
     "descricao": "A logística da loja separou e conferiu — lido direto do ERP, sem confirmação aqui."},
    {"chave": "saida_loja",  "nome": "Saída da loja",       "papel": "log_loja",
     "descricao": "Nota fiscal de transferência emitida e peças despachadas para o CD."},
    {"chave": "chegada_cd",  "nome": "Chegada no CD",       "papel": "log_cd",
     "descricao": "Peças recebidas no CD."},
    {"chave": "conferencia", "nome": "Conferência", "papel": "qualidade",
     "descricao": "Qualidade conferiu quantidade e estado das peças."},
    {"chave": "reintegracao", "nome": "Reintegração ao estoque",    "papel": "qualidade",
     "descricao": "Peças reintegradas ao estoque do CD no ERP, disponíveis para abastecer outras lojas."},
]
ETAPA_POR_CHAVE = {e["chave"]: e for e in ETAPAS}
ORDEM_ETAPA = {e["chave"]: i for i, e in enumerate(ETAPAS)}
ETAPA_FINAL = ETAPAS[-1]["chave"]

# Meta do ciclo completo, da emissão à reintegração (dias corridos)
META_CICLO_DIAS = 10
