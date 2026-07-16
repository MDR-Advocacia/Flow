"""Motor de distribuição (porta do `gerar_planilha.py`, agora no backend).

Substitui os hardcodes e o `random.shuffle` por:
  - roteamento de escritório configurável (tabela `bbd_escritorios`);
  - round-robin de responsáveis PERSISTIDO (tabela `bbd_distribuicao_estado`),
    que equilibra a carga ENTRE execuções, não só dentro de uma;
  - regras de observação (Ajuizamento / Reterceirizado / Cadastro).

Tudo é logado em `bbd_eventos` (seção "Distribuição") pra auditoria.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models.distribuidos_bb import (
    BbConfig,
    BbDistribuicaoEstado,
    BbEscritorio,
    BbGrupoAjuizamento,
    BbProcesso,
    BbRegraObservacao,
    BbResponsavel,
    NIVEL_AVISO,
    NIVEL_SUCESSO,
    PROC_DISTRIBUIDO,
    SECAO_DISTRIBUICAO,
)
from app.services.distribuidos_bb.log_service import registrar_evento

_CHAVE_PONTEIRO_AJUIZAMENTO = "ajuizamento_ultimo_indice"


def _escolher_escritorio(db: Session, processo: BbProcesso) -> Optional[BbEscritorio]:
    """Escolhe o escritório/fila pelo critério de natureza (1º) ou polo (2º)."""
    escritorios = (
        db.query(BbEscritorio)
        .filter(BbEscritorio.ativo.is_(True))
        .order_by(BbEscritorio.ordem, BbEscritorio.id)
        .all()
    )

    natureza = (processo.natureza or "").strip().lower()
    polo = (processo.polo or "").strip().lower()

    # 1º) natureza específica (ex.: Trabalhista) tem prioridade
    if natureza:
        for esc in escritorios:
            if esc.criterio_natureza and esc.criterio_natureza.strip().lower() == natureza:
                return esc
    # 2º) roteamento por polo
    if polo:
        for esc in escritorios:
            if esc.criterio_polo and esc.criterio_polo.strip().lower() == polo:
                return esc
    return None


def _proximo_responsavel_rr(db: Session, escritorio: BbEscritorio) -> Optional[int]:
    """Round-robin persistido: devolve o próximo user_id da fila do escritório."""
    fila = (
        db.query(BbResponsavel)
        .filter(
            BbResponsavel.escritorio_id == escritorio.id,
            BbResponsavel.ativo.is_(True),
        )
        .order_by(BbResponsavel.ordem, BbResponsavel.id)
        .all()
    )
    if not fila:
        return None

    estado = db.get(BbDistribuicaoEstado, escritorio.id)
    if estado is None:
        estado = BbDistribuicaoEstado(escritorio_id=escritorio.id, ultimo_indice=-1)
        db.add(estado)

    proximo_indice = (estado.ultimo_indice + 1) % len(fila)
    escolhido = fila[proximo_indice]

    estado.ultimo_indice = proximo_indice
    estado.ultimo_responsavel_id = escolhido.user_id
    return escolhido.user_id


def _avaliar_observacao(db: Session, processo: BbProcesso, escritorio: BbEscritorio) -> Optional[str]:
    """Observação decidida pelas REGRAS editáveis (bbd_regras_observacao).

    Avalia as regras ativas por `ordem`; a 1ª que casar (cliente, posição, natureza
    e presença/ausência de CNJ) vence. Sem regra → cai na observação padrão do
    escritório. Substitui o if/else hardcoded do script legado.

    O `criterio_cliente` é o que separa os clientes: a regra "Réu → Cadastro" é do
    Banco do Brasil e não pode casar com um processo do Ativos (que tem termo
    próprio pra disparar o workflow dele no L1).
    """
    cliente = (processo.cliente or "").strip().lower()
    posicao = (processo.posicao or "").strip().lower()
    natureza = (processo.natureza or "").strip().lower()
    tem_cnj = bool(processo.cnj)

    regras = (
        db.query(BbRegraObservacao)
        .filter(BbRegraObservacao.ativo.is_(True))
        .order_by(BbRegraObservacao.ordem, BbRegraObservacao.id)
        .all()
    )
    for r in regras:
        if r.criterio_cliente and r.criterio_cliente.strip().lower() != cliente:
            continue
        if r.criterio_posicao and r.criterio_posicao.strip().lower() != posicao:
            continue
        if r.criterio_natureza and r.criterio_natureza.strip().lower() != natureza:
            continue
        if r.criterio_cnj == "com" and not tem_cnj:
            continue
        if r.criterio_cnj == "sem" and tem_cnj:
            continue
        return r.texto
    return escritorio.observacao_padrao


def _proximo_grupo_ajuizamento(db: Session) -> Optional[int]:
    """Rodízio dos grupos de ajuizamento (ponteiro persistido em bbd_config)."""
    grupos = (
        db.query(BbGrupoAjuizamento)
        .filter(BbGrupoAjuizamento.ativo.is_(True))
        .order_by(BbGrupoAjuizamento.ordem, BbGrupoAjuizamento.id)
        .all()
    )
    if not grupos:
        return None
    ponteiro = db.get(BbConfig, _CHAVE_PONTEIRO_AJUIZAMENTO)
    if ponteiro is None:
        ponteiro = BbConfig(chave=_CHAVE_PONTEIRO_AJUIZAMENTO, valor="-1")
        db.add(ponteiro)
    try:
        ultimo = int(ponteiro.valor)
    except (TypeError, ValueError):
        ultimo = -1
    proximo = (ultimo + 1) % len(grupos)
    ponteiro.valor = str(proximo)
    return grupos[proximo].id


def distribuir_processo(db: Session, processo: BbProcesso, *, run_id: Optional[int] = None) -> BbProcesso:
    """Define escritório, responsável (fixo ou round-robin) e observação.

    Muta o `processo` e registra eventos de auditoria. Não commita.
    """
    escritorio = _escolher_escritorio(db, processo)
    if escritorio is None:
        registrar_evento(
            db,
            secao=SECAO_DISTRIBUICAO,
            nivel=NIVEL_AVISO,
            acao="Sem escritório",
            mensagem=(
                "Nenhum escritório configurado casou com este processo "
                f"(polo={processo.polo or '—'}, natureza={processo.natureza or '—'})."
            ),
            dados={"polo": processo.polo, "natureza": processo.natureza},
            processo_id=processo.id,
            run_id=run_id,
        )
        return processo

    # Responsável: fixo do escritório, senão round-robin
    if escritorio.responsavel_fixo_user_id:
        responsavel_id = escritorio.responsavel_fixo_user_id
        modo = "responsável fixo"
    else:
        responsavel_id = _proximo_responsavel_rr(db, escritorio)
        modo = "rodízio (round-robin)"

    processo.escritorio_id = escritorio.id
    processo.escritorio_path = escritorio.escritorio_path
    processo.responsavel_user_id = responsavel_id
    processo.observacao = _avaliar_observacao(db, processo, escritorio)
    processo.status = PROC_DISTRIBUIDO

    # Ajuizamento → atribui o grupo da vez (rodízio), gravado no processo.
    if (processo.observacao or "").strip().lower() == "ajuizamento":
        processo.grupo_ajuizamento_id = _proximo_grupo_ajuizamento(db)
    else:
        processo.grupo_ajuizamento_id = None

    if responsavel_id is None:
        registrar_evento(
            db,
            secao=SECAO_DISTRIBUICAO,
            nivel=NIVEL_AVISO,
            acao="Sem responsável",
            mensagem=(
                f"Escritório '{escritorio.nome}' não tem responsáveis ativos na fila; "
                "processo ficou sem responsável."
            ),
            dados={"escritorio": escritorio.nome},
            processo_id=processo.id,
            run_id=run_id,
        )
    else:
        registrar_evento(
            db,
            secao=SECAO_DISTRIBUICAO,
            nivel=NIVEL_SUCESSO,
            acao="Distribuído",
            mensagem=(
                f"Encaminhado ao escritório '{escritorio.nome}' via {modo}; "
                f"observação: {processo.observacao or '—'}."
            ),
            dados={
                "escritorio": escritorio.nome,
                "escritorio_path": escritorio.escritorio_path,
                "responsavel_user_id": responsavel_id,
                "modo": modo,
                "observacao": processo.observacao,
            },
            processo_id=processo.id,
            run_id=run_id,
        )

    return processo
