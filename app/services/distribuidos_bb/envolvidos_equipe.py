"""Montagem dos envolvidos-de-EQUIPE de um processo (derivados da config).

Diferente dos envolvidos capturados na capa do NPJ (partes reais, tabela
`bbd_envolvidos`), estes são a EQUIPE INTERNA que entra como envolvidos do
processo — como no `data.json`/aba Envolvidos do script legado:

  - a equipe do responsável (bbd_equipe_membros), cada um na sua classificação;
  - quando a observação é "Ajuizamento", o grupo de ajuizamento ATRIBUÍDO ao
    processo (rodízio), advogado + assistente.

São CALCULADOS da config (não duplicados no processo), então refletem edições
na tela de Equipes na hora. Usados na auditoria, na planilha e no cadastro API.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.distribuidos_bb import (
    BbEquipeMembro,
    BbGrupoAjuizamentoMembro,
    BbProcesso,
)
from app.models.legal_one import LegalOneUser


def _nomes(db: Session, user_ids: set[int]) -> dict[int, str]:
    ids = {i for i in user_ids if i}
    if not ids:
        return {}
    return dict(
        db.query(LegalOneUser.id, LegalOneUser.name).filter(LegalOneUser.id.in_(ids)).all()
    )


def montar_envolvidos_equipe(db: Session, processo: BbProcesso) -> list[dict[str, Any]]:
    """Lista de {membro_user_id, nome, classificacao, origem} para o processo."""
    resultado: list[dict[str, Any]] = []

    # 1) Equipe do responsável
    equipe = []
    if processo.responsavel_user_id:
        equipe = (
            db.query(BbEquipeMembro)
            .filter(
                BbEquipeMembro.responsavel_user_id == processo.responsavel_user_id,
                BbEquipeMembro.ativo.is_(True),
            )
            .all()
        )

    # 2) Grupo de ajuizamento atribuído
    grupo_membros = []
    if processo.grupo_ajuizamento_id:
        grupo_membros = (
            db.query(BbGrupoAjuizamentoMembro)
            .filter(
                BbGrupoAjuizamentoMembro.grupo_id == processo.grupo_ajuizamento_id,
                BbGrupoAjuizamentoMembro.ativo.is_(True),
            )
            .order_by(BbGrupoAjuizamentoMembro.ordem)
            .all()
        )

    nomes = _nomes(
        db,
        {m.membro_user_id for m in equipe} | {m.membro_user_id for m in grupo_membros},
    )

    for m in equipe:
        resultado.append({
            "membro_user_id": m.membro_user_id,
            "nome": nomes.get(m.membro_user_id),
            "classificacao": m.classificacao,
            "origem": "equipe",
        })
    for m in grupo_membros:
        resultado.append({
            "membro_user_id": m.membro_user_id,
            "nome": nomes.get(m.membro_user_id),
            "classificacao": m.classificacao,
            "origem": "ajuizamento",
        })
    return resultado
