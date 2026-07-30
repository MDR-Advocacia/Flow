"""Intake do Sistema de Encerramentos: encerrar processo no Legal One.

O Sistema de Encerramentos (MDR) chama este endpoint quando o operador
encerra um processo lá — o Flow executa o encerramento no Legal One via
API oficial. Autenticado por API key no header `X-Encerramentos-Api-Key`
(env `ENCERRAMENTOS_INTAKE_API_KEY`), SEM JWT — mesmo padrão dos intakes
de Prazos Iniciais, Classificador e OneRequest. Registrado sem
`protected_dependencies` em main.py.

Regras de negócio (decididas com a operação em 30/07/2026):
- `closingReason` (MotivoEncerramento no L1) recebe o valor pronto vindo
  do Encerramentos: NOME COMPLETO de quem encerrou + data e hora
  (convenção que a equipe já usava manualmente no fluxo da UI).
- O responsável da pasta NÃO é alterado.
- Campos de resultado (result/resultType/resultReason/datas) não são
  tocados — seguem preenchidos pela equipe no L1.

Contrato completo: docs/integracao-flow-legalone.md no repo Encerramentos.
"""

import logging
from typing import Optional

import requests
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.dependencies import get_api_client
from app.services.legal_one_client import LegalOneApiClient

logger = logging.getLogger(__name__)

intake_router = APIRouter(prefix="/legalone", tags=["Encerramentos (Intake)"])


def _validate_intake_api_key(
    x_encerramentos_api_key: Optional[str] = Header(
        default=None, alias="X-Encerramentos-Api-Key"
    ),
) -> str:
    """
    Autentica o Sistema de Encerramentos por header `X-Encerramentos-Api-Key`.

    Aceita múltiplas chaves em `ENCERRAMENTOS_INTAKE_API_KEY` (separadas por
    vírgula) pra rotação sem downtime. Se nenhuma chave estiver configurada,
    o endpoint fica explicitamente bloqueado (503) — evita rota aberta em
    produção por esquecimento de config.
    """
    valid_keys = settings.encerramentos_intake_api_keys
    if not valid_keys:
        logger.error(
            "ENCERRAMENTOS_INTAKE_API_KEY não configurada — encerramento rejeitado."
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Endpoint de encerramento não configurado.",
        )
    if not x_encerramentos_api_key or x_encerramentos_api_key not in valid_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key inválida ou ausente no header X-Encerramentos-Api-Key.",
        )
    return x_encerramentos_api_key


class EncerramentoPayload(BaseModel):
    numero_cnj: str = Field(..., min_length=10, description="CNJ do processo")
    data_encerramento: str = Field(
        ..., description="Data do encerramento (yyyy-mm-dd)"
    )
    motivo_encerramento: str = Field(
        ...,
        min_length=1,
        description="Valor pronto pro MotivoEncerramento do L1 "
        "(NOME COMPLETO - dd/mm/aaaa hh:mm)",
    )
    # Contexto/auditoria (não escritos no L1 diretamente)
    origem: str = Field(default="encerramentos")
    encerrado_em: Optional[str] = None
    operador_nome: Optional[str] = None
    operador_email: Optional[str] = None
    justificativa: Optional[str] = None


@intake_router.post("/encerramento")
def encerrar_processo_legalone(
    payload: EncerramentoPayload,
    api_key: str = Depends(_validate_intake_api_key),
    client: LegalOneApiClient = Depends(get_api_client),
):
    """
    Encerra o processo no Legal One (closed + closingDate + closingReason).

    Respostas:
    - 200 {status: "ok"|"ja_encerrado", lawsuit_id}
    - 404 CNJ sem correspondência no L1
    - 409 já encerrado no L1 com dados divergentes dos enviados
    - 502 falha na API do Legal One
    """
    lawsuit = client.search_lawsuit_by_cnj(payload.numero_cnj)
    if not lawsuit:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Processo {payload.numero_cnj} não encontrado no Legal One.",
        )
    lawsuit_id = int(lawsuit["id"])

    # Idempotência: retry do Encerramentos não pode virar erro nem sobrescrever
    try:
        atual = client.get_lawsuit_by_id(
            lawsuit_id, params={"$select": "id,closed,closingDate,closingReason"}
        )
    except requests.exceptions.HTTPError as exc:
        logger.error("Falha lendo lawsuit %s antes do encerramento: %s", lawsuit_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Falha ao consultar o processo no Legal One.",
        ) from exc

    if atual.get("closed"):
        reason_atual = (atual.get("closingReason") or "").strip()
        if reason_atual == payload.motivo_encerramento.strip():
            logger.info(
                "Lawsuit %s já encerrado com o mesmo motivo — idempotente.",
                lawsuit_id,
            )
            return {"status": "ja_encerrado", "lawsuit_id": lawsuit_id}
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Processo já encerrado no Legal One com dados divergentes "
                f"(closingReason atual: '{reason_atual}' | "
                f"closingDate: {atual.get('closingDate')})."
            ),
        )

    try:
        resultado = client.close_lawsuit(
            lawsuit_id=lawsuit_id,
            closing_date=payload.data_encerramento,
            closing_reason=payload.motivo_encerramento,
        )
    except requests.exceptions.HTTPError as exc:
        body = exc.response.text[:300] if exc.response is not None else str(exc)
        logger.error("L1 recusou o encerramento do lawsuit %s: %s", lawsuit_id, body)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Legal One recusou o encerramento: {body}",
        ) from exc

    logger.info(
        "Lawsuit %s (%s) encerrado via Encerramentos por %s.",
        lawsuit_id, payload.numero_cnj, payload.operador_nome or "?",
    )
    return {"status": "ok", "lawsuit_id": lawsuit_id, "entity": resultado.get("entity")}
