"""
Giro Reverso — ponto de entrada da demonstração publicada (o Streamlit Community Cloud procura este arquivo).

Rede, produtos, vendas e pedidos são sintéticos (data/erp.py). `streamlit run streamlit_app.py`
"""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).parent / "app.py"), run_name="__main__")
