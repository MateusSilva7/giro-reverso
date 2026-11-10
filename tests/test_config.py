"""Horário da operação (config.agora): Rede, seja qual for o fuso do servidor."""
from datetime import datetime
from zoneinfo import ZoneInfo

import config


def test_agora_e_horario_de_brasilia_sem_fuso():
    esperado = datetime.now(ZoneInfo("America/Sao_Paulo")).replace(tzinfo=None)
    assert config.agora().tzinfo is None
    assert abs((config.agora() - esperado).total_seconds()) < 5


def test_agora_vem_do_fuso_configurado_e_nao_do_relogio_do_servidor(monkeypatch):
    monkeypatch.setattr(config, "FUSO", ZoneInfo("UTC"))  # servidor "em UTC": seria 3 h à frente da rede
    em_utc = datetime.now(ZoneInfo("UTC")).replace(tzinfo=None)
    assert abs((config.agora() - em_utc).total_seconds()) < 5
