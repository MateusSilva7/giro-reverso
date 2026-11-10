"""Configuração comum dos testes."""
# O polars traz o próprio alocador de memória (mimalloc), que guarda qual foi a thread que o iniciou. Se o primeiro
# `import polars` acontecer numa thread de script do AppTest (que termina logo depois), o processo morre com
# "access violation" no Windows depois de algumas centenas de execuções de página. Importar aqui, na thread principal
# do pytest, mantém isso fora das threads de teste mesmo quando se roda um arquivo sozinho.
import polars  # noqa: F401
