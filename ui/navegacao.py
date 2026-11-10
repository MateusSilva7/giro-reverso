"""Telas do app e quem vê cada uma. Mora fora do app.py (um script que o Streamlit roda a cada interação) para os testes
lerem a mesma tabela que a navegação usa."""

# chave -> (arquivo, título no menu, url_path)
TELAS = {
    "painel":         ("telas/painel.py", "Visão de gestão", "painel"),
    "acompanhamento": ("telas/acompanhamento.py", "Acompanhamento", "acompanhamento"),
    "fila":           ("telas/fila.py", "Fila de separação", "fila"),
    "ciclo":          ("telas/ciclo.py", "Ciclo diário", "ciclo"),
    "lista":          ("telas/lista_diaria.py", "Lista diária", "lista-diaria"),
    "sensibilidade":  ("telas/sensibilidade.py", "Sensibilidade da regra", "sensibilidade"),
    "identificacao":  ("telas/identificacao.py", "Identificação por loja", "identificacao"),
    "perfil":         ("telas/perfil.py", "Perfil da loja", "perfil"),
    "historico":      ("telas/historico.py", "Histórico de devoluções", "historico"),
}

# O Perfil da loja e a Sensibilidade (visão ampla, com valor) são só da Gestão.
TELAS_POR_PAPEL = {
    "gestor":    ["painel", "perfil", "sensibilidade", "historico", "acompanhamento", "fila", "ciclo", "lista", "identificacao"],
    "compras":   ["acompanhamento", "ciclo", "lista", "identificacao", "historico"],
    "log_loja":  ["fila", "acompanhamento", "historico"],
    "log_cd":    ["acompanhamento"],
    "qualidade": ["acompanhamento"],
}
