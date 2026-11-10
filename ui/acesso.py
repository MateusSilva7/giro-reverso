"""Código de acesso por perfil (config.CODIGOS_ACESSO).

Ainda não é login de verdade: é um código por setor, compartilhado, guardado no .env, para o "Entrar como" não ficar
aberto a quem alcança o app pela rede. Não identifica pessoa (isso vem com o login corporativo, pelo TI).
"""
import hmac

import config


def exige_codigo(papel: str) -> bool:
    """O perfil pede código: nos dados reais sempre; na demonstração só se houver código configurado para ele."""
    return config.EXIGE_CODIGO or bool(config.CODIGOS_ACESSO.get(papel))


def verificar(papel: str, digitado: str) -> tuple[bool, str]:
    """(pode entrar, motivo se não pode). Nos dados reais, perfil sem código configurado não entra (falha fechada)."""
    esperado = config.CODIGOS_ACESSO.get(papel, "")
    if not esperado:
        if config.EXIGE_CODIGO:
            return False, (f"Este acesso ainda não tem código. Defina GR_CODIGO_{papel.upper()} no .env e reinicie o "
                           "app (veja o .env.example).")
        return True, ""
    if hmac.compare_digest((digitado or "").strip().encode("utf-8"), esperado.encode("utf-8")):
        return True, ""
    return False, "Código de acesso incorreto."
