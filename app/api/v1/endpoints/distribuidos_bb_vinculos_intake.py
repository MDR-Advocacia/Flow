"""Intake do RPA de Vínculos BB (roda no servidor AWS da casa).

Por que existe: o WAF do BB devolve 403 (HTML de erro) pra qualquer chamada à
API JSON do PAJ saindo do container — requests e Playwright, com ou sem proxy.
Só o Chrome real com `undetected-chromedriver` passa, e ele já roda no host da
AWS no repo RPA_encerramentos (modo `--vinculos`). O RPA consome a fila daqui,
faz as 3 consultas no portal com a sessão OneLog e devolve as linhas CRUAS; a
regra de negócio (situação excluída, dedupe, cenário 1/2, reatribuição) fica
toda no Flow, em `vinculos_service.aplicar_resultado_rpa`.

Autenticação por API key no header `X-VinculosBB-Api-Key`
(env DISTRIBUIDOS_BB_VINCULOS_INTAKE_API_KEY, CSV pra rotação) — mesmo padrão
do intake da Análise de Risco. Registrado SEM protected_dependencies em main.py.

Mudou o contrato aqui? Atualiza o modo --vinculos do RPA_encerramentos junto.

Contrato:
  GET  /distribuidos-bb/vinculos/intake/fila?limit=N
       -> {"total": N, "advogado_mdr": 8706512, "itens": [
            {"processo_id", "cnj", "npj",
             "partes": [{"envolvido_id", "doc", "nome"}]}]}
       Fila dinâmica: processos do BB coletados nos últimos
       `distribuidos_bb_vinculos_fila_dias` dias, com envolvidos capturados e
       `vinculos_verificado_em` NULL. Sai da fila quando o resultado chega.
  POST /distribuidos-bb/vinculos/intake/resultados
       {"resultados": [{"processo_id": 1, "erro": null,
         "partes": [{"envolvido_id", "doc", "nome", "numero_pessoa",
                     "processos": [<linha crua do consulta-parte-envolvida
                                    + "indicador_polo": "A"|"P"|null>]}]}]}
       - erro preenchido -> NÃO marca verificado (fica na fila), evento AVISO
       - sem erro        -> aplica decisão/persistência; marca verificado
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_db
from app.models.distribuidos_bb import (
    CLIENTE_BB,
    NIVEL_AVISO,
    SECAO_DISTRIBUICAO,
    BbEnvolvido,
    BbProcesso,
)
from app.services.distribuidos_bb.log_service import registrar_evento
from app.services.distribuidos_bb.normalizacao import apenas_digitos

logger = logging.getLogger(__name__)

intake_router = APIRouter(
    prefix="/distribuidos-bb/vinculos/intake", tags=["Vínculos BB (Intake)"]
)

# CNPJ do próprio Banco do Brasil — nunca vai pra fila como "parte".
_CNPJ_BB = "00000000000191"


def _validate_intake_api_key(
    x_vinculosbb_api_key: Optional[str] = Header(
        default=None, alias="X-VinculosBB-Api-Key"
    ),
) -> str:
    valid_keys = settings.distribuidos_bb_vinculos_intake_api_keys
    if not valid_keys:
        logger.error(
            "DISTRIBUIDOS_BB_VINCULOS_INTAKE_API_KEY não configurada — intake rejeitado."
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Endpoint de intake dos Vínculos BB não configurado.",
        )
    if not x_vinculosbb_api_key or x_vinculosbb_api_key not in valid_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key inválida ou ausente no header X-VinculosBB-Api-Key.",
        )
    return x_vinculosbb_api_key


class ParteFila(BaseModel):
    envolvido_id: int
    doc: str
    nome: Optional[str] = None


class FilaItem(BaseModel):
    processo_id: int
    cnj: Optional[str] = None
    npj: Optional[str] = None
    partes: List[ParteFila]


class FilaResponse(BaseModel):
    total: int
    advogado_mdr: int
    itens: List[FilaItem]


class ParteResultado(BaseModel):
    envolvido_id: Optional[int] = None
    doc: str
    nome: Optional[str] = None
    # None = pessoa sem cadastro no BB (resultado legítimo, conta como verificado).
    numero_pessoa: Optional[int] = None
    # Linhas CRUAS do consulta-parte-envolvida, já filtradas por ativo+advogado
    # no RPA (pra poupar consultas de polo), + "indicador_polo" por linha.
    processos: List[Dict[str, Any]] = Field(default_factory=list)


class ResultadoItem(BaseModel):
    processo_id: int
    # Falha na pesquisa (sessão caiu, portal fora): a linha CONTINUA na fila.
    erro: Optional[str] = None
    partes: List[ParteResultado] = Field(default_factory=list)


class ResultadosRequest(BaseModel):
    resultados: List[ResultadoItem]


class ResultadosResponse(BaseModel):
    aplicados: int
    com_vinculo: int
    reatribuidos: int
    erros: int
    ignorados: List[int]


def _fila_query(db: Session):
    """Processos do BB aguardando a pesquisa de vínculos pelo RPA.

    Dinâmica de propósito (nenhuma coluna nova): entra quem tem envolvido com
    documento capturado e `vinculos_verificado_em` NULL dentro da janela; sai
    quando o resultado chega (o aplicar seta o verificado). Erro do RPA não
    seta — o item volta naturalmente na próxima varredura.
    """
    corte = datetime.now(timezone.utc) - timedelta(
        days=settings.distribuidos_bb_vinculos_fila_dias
    )
    return (
        db.query(BbProcesso)
        .filter(
            BbProcesso.cliente == CLIENTE_BB,
            BbProcesso.vinculos_verificado_em.is_(None),
            BbProcesso.created_at >= corte,
            db.query(BbEnvolvido.id)
            .filter(
                BbEnvolvido.processo_id == BbProcesso.id,
                BbEnvolvido.cpf_cnpj.isnot(None),
            )
            .exists(),
        )
    )


@intake_router.get(
    "/fila",
    response_model=FilaResponse,
    summary="Fila de processos aguardando pesquisa de vínculos no portal BB",
)
def intake_fila(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: str = Depends(_validate_intake_api_key),
):
    from app.services.distribuidos_bb.vinculos_service import _advogado_mdr

    base = _fila_query(db)
    total = base.count()
    # Mais novos primeiro: são os que ainda estão NOVOS no pool, onde a
    # reatribuição automática ainda alcança a planilha.
    rows = base.order_by(BbProcesso.id.desc()).limit(limit).all()

    itens: List[FilaItem] = []
    for p in rows:
        envolvidos = (
            db.query(BbEnvolvido)
            .filter(BbEnvolvido.processo_id == p.id, BbEnvolvido.cpf_cnpj.isnot(None))
            .all()
        )
        partes: List[ParteFila] = []
        vistos: set = set()
        for e in envolvidos:
            d = apenas_digitos(e.cpf_cnpj)
            if not d or d == _CNPJ_BB or len(d) not in (11, 14) or d in vistos:
                continue
            vistos.add(d)
            partes.append(ParteFila(envolvido_id=e.id, doc=d, nome=e.nome))
        if not partes:
            # Só o BB como envolvido: nada a pesquisar — marca verificado com 0
            # pra sair da fila em vez de voltar a cada varredura do RPA.
            p.vinculos_qtd = 0
            p.vinculos_verificado_em = datetime.now(timezone.utc)
            continue
        itens.append(FilaItem(processo_id=p.id, cnj=p.cnj, npj=p.npj, partes=partes))
    db.commit()
    return FilaResponse(total=total, advogado_mdr=_advogado_mdr(db), itens=itens)


@intake_router.post(
    "/resultados",
    response_model=ResultadosResponse,
    summary="Recebe do RPA os vínculos pesquisados e aplica a decisão",
)
def intake_resultados(
    payload: ResultadosRequest,
    db: Session = Depends(get_db),
    _: str = Depends(_validate_intake_api_key),
):
    from app.services.distribuidos_bb.vinculos_service import aplicar_resultado_rpa

    aplicados = com_vinculo = reatribuidos = erros = 0
    ignorados: List[int] = []
    for item in payload.resultados:
        proc = db.get(BbProcesso, item.processo_id)
        if proc is None:
            ignorados.append(item.processo_id)
            continue
        if item.erro:
            erros += 1
            registrar_evento(
                db, secao=SECAO_DISTRIBUICAO, nivel=NIVEL_AVISO,
                acao="Vínculos — erro no RPA",
                mensagem=(
                    f"O RPA não conseguiu pesquisar os vínculos ({item.erro[:300]}). "
                    "O processo continua na fila."
                ),
                processo_id=proc.id,
            )
            db.commit()
            continue
        try:
            resultado = aplicar_resultado_rpa(
                db, proc, [pt.model_dump() for pt in item.partes]
            )
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception(
                "Vínculos intake: falha ao aplicar resultado do processo %s.",
                item.processo_id,
            )
            erros += 1
            continue
        aplicados += 1
        if resultado.get("cenario"):
            com_vinculo += 1
        if resultado.get("reatribuido"):
            reatribuidos += 1

    resposta = ResultadosResponse(
        aplicados=aplicados,
        com_vinculo=com_vinculo,
        reatribuidos=reatribuidos,
        erros=erros,
        ignorados=ignorados,
    )
    logger.info("Vínculos BB intake/resultados: %s", resposta.model_dump())
    return resposta
