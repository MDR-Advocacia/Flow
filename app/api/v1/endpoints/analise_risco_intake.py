"""Intake do RPA de Análise de Risco BB Réu (roda no servidor AWS da casa).

Decisão do supervisor: o acesso ao portal do BB pra conferir a análise NÃO sai
do Coolify — o RPA externo (servidor AWS de testes) consome a fila daqui,
consulta a pendência no portal com a própria sessão OneLog e devolve o
resultado. Autenticação por API key no header `X-AnaliseRisco-Api-Key`
(env ANALISE_RISCO_INTAKE_API_KEY, CSV pra rotação) — mesmo padrão do intake
do OneRequest. Registrado SEM protected_dependencies em main.py.

O RPA vive no repo próprio (github.com/MDR-Advocacia/RPA_AnaliseRisco),
deployado pelo Coolify do servidor de testes. Mudou o contrato aqui?
Atualiza o README de lá junto.

Contrato:
  GET  /analise-risco/intake/fila?limit=N
       -> {"total": <tamanho da fila>, "itens": [{id, l1_task_id, npj, cnj,
           verif_tentativas}]}  (ordem round-robin; npj pode vir mascarado
           "2024/0116713-000" — o RPA normaliza)
  POST /analise-risco/intake/resultados
       {"resultados": [{"id": 1, "pendencia_aberta": false, "estado": null,
                        "exito": null, "erro": null}, ...]}
       - erro preenchido       -> tentativa falha (fica na fila, re-tenta)
       - pendencia_aberta bool -> verificada (aberta = divergente)
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_db
from app.models.analise_risco import AnaliseRiscoTarefa, VERIF_ERRO, VERIF_NA_FILA

logger = logging.getLogger(__name__)

intake_router = APIRouter(prefix="/analise-risco/intake", tags=["Análise de Risco (Intake)"])


def _validate_intake_api_key(
    x_analise_risco_api_key: Optional[str] = Header(
        default=None, alias="X-AnaliseRisco-Api-Key"
    ),
) -> str:
    valid_keys = settings.analise_risco_intake_api_keys
    if not valid_keys:
        logger.error("ANALISE_RISCO_INTAKE_API_KEY não configurada — intake rejeitado.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Endpoint de intake da Análise de Risco não configurado.",
        )
    if not x_analise_risco_api_key or x_analise_risco_api_key not in valid_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key inválida ou ausente no header X-AnaliseRisco-Api-Key.",
        )
    return x_analise_risco_api_key


class FilaItem(BaseModel):
    id: int
    l1_task_id: int
    npj: Optional[str] = None
    cnj: Optional[str] = None
    verif_tentativas: int = 0


class FilaResponse(BaseModel):
    total: int
    itens: List[FilaItem]


class ResultadoItem(BaseModel):
    id: int
    # Sucesso: pendencia_aberta obrigatório (True = análise NÃO feita = divergente).
    pendencia_aberta: Optional[bool] = None
    estado: Optional[str] = None
    exito: Optional[str] = None
    # NPJ resolvido pelo RPA (quando a fila só tinha CNJ) — persistido.
    npj: Optional[str] = None
    # Falha: motivo (a linha continua na fila).
    erro: Optional[str] = None


class ResultadosRequest(BaseModel):
    resultados: List[ResultadoItem] = Field(..., description="Um por item verificado")


class ResultadosResponse(BaseModel):
    verificadas: int
    divergentes: int
    erros: int
    ignoradas: List[int]


@intake_router.get(
    "/fila",
    response_model=FilaResponse,
    summary="Fila de tarefas cumpridas aguardando verificação no portal BB",
)
def intake_fila(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: str = Depends(_validate_intake_api_key),
):
    base = db.query(AnaliseRiscoTarefa).filter(
        AnaliseRiscoTarefa.verif_status.in_([VERIF_NA_FILA, VERIF_ERRO])
    )
    total = base.count()
    rows = (
        base.order_by(
            AnaliseRiscoTarefa.portal_verificado_em.asc().nullsfirst(),
            AnaliseRiscoTarefa.id.asc(),
        )
        .limit(limit)
        .all()
    )
    return FilaResponse(
        total=total,
        itens=[
            FilaItem(
                id=r.id,
                l1_task_id=r.l1_task_id,
                npj=r.npj,
                cnj=r.cnj,
                verif_tentativas=r.verif_tentativas or 0,
            )
            for r in rows
        ],
    )


@intake_router.post(
    "/resultados",
    response_model=ResultadosResponse,
    summary="Recebe do RPA o resultado das verificações no portal",
)
def intake_resultados(
    payload: ResultadosRequest,
    db: Session = Depends(get_db),
    _: str = Depends(_validate_intake_api_key),
):
    from app.services.analise_risco.service import (
        aplicar_erro_verificacao,
        aplicar_verificacao,
    )

    verificadas = divergentes = erros = 0
    ignoradas: List[int] = []
    for item in payload.resultados:
        row = (
            db.query(AnaliseRiscoTarefa)
            .filter(AnaliseRiscoTarefa.id == item.id)
            .first()
        )
        if not row:
            ignoradas.append(item.id)
            continue
        if item.npj:
            row.npj = item.npj
        if item.erro:
            aplicar_erro_verificacao(row, item.erro)
            erros += 1
        elif item.pendencia_aberta is not None:
            aplicar_verificacao(
                row,
                pendencia_aberta=item.pendencia_aberta,
                estado=item.estado,
                exito=item.exito,
            )
            verificadas += 1
            if item.pendencia_aberta:
                divergentes += 1
        else:
            # Nem erro nem veredito — não altera a linha.
            ignoradas.append(item.id)

    db.commit()
    resultado = ResultadosResponse(
        verificadas=verificadas, divergentes=divergentes, erros=erros, ignoradas=ignoradas
    )
    logger.info("Análise de Risco intake/resultados: %s", resultado.model_dump())
    return resultado
