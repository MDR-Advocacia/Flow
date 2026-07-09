"""Log/auditoria universal do módulo Distribuídos BB.

Ponto ÚNICO de registro: toda seção (Coleta, Extração, Ciência, Distribuição,
Envolvidos, Contatos, Cadastro, Configuração, Sessão) chama `registrar_evento`.
Grava em `bbd_eventos` (visível na tela) e espelha no logger Python (pt-BR).

Nada de termo alienígena: `secao`, `acao`, `mensagem` são rótulos pt-BR que o
operador lê direto. `dados` guarda o dado capturado normalizado (jsonb).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.distribuidos_bb import BbEvento, NIVEL_INFO

logger = logging.getLogger("distribuidos_bb")


def registrar_evento(
    db: Session,
    *,
    secao: str,
    mensagem: str,
    nivel: str = NIVEL_INFO,
    acao: Optional[str] = None,
    dados: Optional[dict[str, Any]] = None,
    processo_id: Optional[int] = None,
    run_id: Optional[int] = None,
    commit: bool = False,
) -> BbEvento:
    """Registra um evento no log/auditoria e espelha no logger Python.

    Não commita por padrão (o chamador controla a transação); passe
    commit=True em pontos soltos (ex.: configuração via UI).
    """
    evento = BbEvento(
        secao=secao,
        acao=acao,
        nivel=nivel,
        mensagem=mensagem,
        dados=dados,
        processo_id=processo_id,
        run_id=run_id,
    )
    db.add(evento)
    if commit:
        db.commit()
        db.refresh(evento)

    # Espelho no logger Python (facilita ver em `docker logs`)
    prefixo = f"[BBD/{secao}]"
    if processo_id:
        prefixo += f"[proc {processo_id}]"
    linha = f"{prefixo} {acao + ': ' if acao else ''}{mensagem}"
    if nivel == "ERRO":
        logger.error(linha)
    elif nivel == "AVISO":
        logger.warning(linha)
    else:
        logger.info(linha)

    return evento
