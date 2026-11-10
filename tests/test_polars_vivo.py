"""polars_vivo: o polars é iniciado numa thread que não morre (sem isso o servidor cai depois de ~200 execuções)."""
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import polars_vivo

RAIZ = Path(__file__).resolve().parent.parent


def _python(codigo: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", "-c", textwrap.dedent(codigo)], capture_output=True, text=True,
                          timeout=180, env=env)


def test_o_guardiao_e_unico_e_fica_vivo():
    for _ in range(3):  # como o script do Streamlit: cada chamada vem de uma thread nova e curta
        t = threading.Thread(target=polars_vivo.iniciar_polars_em_thread_viva)
        t.start()
        t.join()
    guardioes = [t for t in threading.enumerate() if t.name == "guardiao-polars"]
    assert len(guardioes) == 1
    assert guardioes[0].is_alive() and guardioes[0].daemon  # daemon: não impede o processo de encerrar


def test_o_processo_aguenta_muitas_threads_novas_usando_polars():
    # Num processo à parte: se a proteção falhar, o que cai é o processo inteiro (access violation).
    # Sem ela, o 1º import do polars numa thread curta derruba o processo em ~200 iterações.
    # O 1º import do polars acontece DENTRO da thread curta (como no script do Streamlit), depois do iniciador:
    # se o iniciador não pusesse o polars numa thread viva, é aqui que o processo ficaria exposto.
    p = _python(f"""
        import sys, threading
        sys.path.insert(0, {str(RAIZ)!r})
        import polars_vivo
        ctx = {{}}
        def primeira_execucao():
            polars_vivo.iniciar_polars_em_thread_viva()
            from datetime import datetime
            import pandas as pd
            import polars as pl
            ctx["pl"] = pl
            ctx["df"] = pd.DataFrame({{"pedido_id": [1, 2, 3, 4], "etapa": ["emitido", "separacao", "emitido", "saida"],
                                       "ocorrido_em": pd.to_datetime([datetime(2026, 10, 8, h) for h in (9, 10, 11, 12)])}})
        def trabalho():
            pl, df = ctx["pl"], ctx["df"]
            pl.from_pandas(df).group_by("etapa").agg(pl.col("ocorrido_em").max(), pl.len()).sort("etapa")
        for f in [primeira_execucao] + [trabalho] * 1000:
            t = threading.Thread(target=f); t.start(); t.join()
        print("ok")
    """)
    assert p.returncode == 0 and p.stdout.strip() == "ok", (p.returncode, p.stderr[-600:])


def test_o_app_inicia_o_guardiao_antes_das_telas(tmp_path):
    # Processo à parte: só o app.py pode ter criado a thread (nenhum outro teste interfere).
    env = {**os.environ, "ESTADO_DB_URL": f"sqlite:///{(tmp_path / 'estado.db').as_posix()}", "STREAMLIT_LOGGER_LEVEL": "error"}
    for chave in ("GR_LOJA_PILOTO", "GR_DADOS_REAIS", "GR_ROTULO", "GR_REGRA_LISTA", "GR_LADO_SO_GRID_ZERO"):
        env.pop(chave, None)
    p = _python(f"""
        import sys, threading
        sys.path.insert(0, {str(RAIZ)!r})
        import dotenv
        dotenv.load_dotenv = lambda *a, **k: False  # os testes nunca leem o .env de quem os roda
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file({str(RAIZ / "app.py")!r}, default_timeout=60)
        at.run()
        assert not at.exception, at.exception
        print([t.name for t in threading.enumerate() if t.is_alive()])
    """, env=env)
    assert p.returncode == 0, p.stderr[-800:]
    assert "guardiao-polars" in p.stdout, p.stdout
