"""Inicia o polars numa thread que vive até o fim do processo.

No Streamlit cada execução do script roda numa thread nova, que termina no fim da execução. Se o polars for iniciado
(o primeiro `import polars`) numa delas, o processo morre sem mensagem ("access violation", código 0xC0000005, no
Windows) depois de algumas centenas de execuções: o servidor cai e todo mundo é desconectado. Isso foi medido;
a causa provável é o alocador de memória que o polars traz (mimalloc) ficar ligado à thread que o iniciou. Importar o
polars aqui, numa thread que fica parada até o processo acabar, evita isso.

Chame `iniciar_polars_em_thread_viva()` uma vez, no começo do app.py, antes de qualquer `import polars` feito dentro
do script. Nos testes quem faz esse papel é o tests/conftest.py (import na thread principal do pytest)."""
import threading

_NOME = "guardiao-polars"
_ESPERA_MAXIMA_S = 60
_TRAVA = threading.Lock()


def _guardiao(pronto: threading.Event) -> None:
    try:
        import polars  # noqa: F401  (o import é o que inicia o alocador)
    finally:
        pronto.set()  # mesmo se o import falhar, quem chamou não fica esperando; o erro aparece no import da tela
    threading.Event().wait()  # nunca é liberado: a thread vive até o processo terminar


def iniciar_polars_em_thread_viva() -> None:
    """Idempotente: se o guardião já existe, não faz nada."""
    with _TRAVA:
        if any(t.name == _NOME for t in threading.enumerate()):
            return
        pronto = threading.Event()
        threading.Thread(target=_guardiao, args=(pronto,), name=_NOME, daemon=True).start()
    pronto.wait(_ESPERA_MAXIMA_S)
