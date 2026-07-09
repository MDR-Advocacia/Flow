"""Seed das tabelas editáveis a partir dos hardcodes do RPA legado.

Roda uma vez (idempotente): se já houver escritórios, não faz nada. Cria os 4
escritórios/filas do BB com seus caminhos e critérios, e tenta mapear as filas
de responsáveis por NOME → LegalOneUser. Quem não casar por nome vira um
evento de aviso (a UI de configuração permite ajustar depois).
"""
from __future__ import annotations

import unicodedata
from typing import Optional

from sqlalchemy.orm import Session

from app.models.distribuidos_bb import (
    BbEscritorio,
    BbResponsavel,
    NIVEL_AVISO,
    NIVEL_SUCESSO,
    SECAO_CONFIGURACAO,
)
from app.models.legal_one import LegalOneUser
from app.services.distribuidos_bb.log_service import registrar_evento

_BASE = "MDR Advocacia / Área operacional / Banco do Brasil"

# Filas legadas (nomes exatamente como no gerar_planilha.py)
_RESP_REU = [
    "Christiane Serejo Cardoso",
    "Arthur Augusto Alves de Almeida",
    "Enzo Pinto Bagatoli Carriço",
    "Maria Luisa de Brito Ferreira",
    "Ingrid Quirino Ribeiro",
    "Álvaro José da silva Aguiar",
    "Thays Mendes Oliveira da Cunha",
]
_RESP_AUTOR = [
    "Maria Victória Pereira Dantas",
    "Rayana Aider Felix Felipe",
    "Michelle Dantas Ferreira",
    "BRÍGIDA BRENDA FAUSTINO DE OLIVEIRA",
    "Andrehelly Amanda Oleinik dos Santos",
    "Marcos Vinicius Cruz Bezerra",
]
_RESP_TRABALHISTA_FIXO = "Antônio Uemerson de Carvalho"


def _chave_nome(nome: str) -> str:
    """Normaliza pra comparar nomes de forma robusta (sem acento, sem caixa).

    Não depende do `lower()` do banco (o do SQLite não é unicode-aware); a
    comparação é feita em Python pra funcionar igual em Postgres e nos testes.
    """
    texto = unicodedata.normalize("NFKD", nome or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(texto.strip().lower().split())


def _resolver_user_id(db: Session, nome: str) -> Optional[int]:
    """Casa um nome com LegalOneUser (case- e acento-insensitive)."""
    alvo = _chave_nome(nome)
    for user in db.query(LegalOneUser).filter(LegalOneUser.name.isnot(None)).all():
        if _chave_nome(user.name) == alvo:
            return user.id
    return None


def _criar_escritorio(
    db: Session,
    *,
    nome: str,
    sufixo: str,
    criterio_polo: Optional[str] = None,
    criterio_natureza: Optional[str] = None,
    responsavel_fixo_id: Optional[int] = None,
    observacao_padrao: Optional[str] = None,
    ordem: int = 0,
) -> BbEscritorio:
    esc = BbEscritorio(
        nome=nome,
        escritorio_path=f"{_BASE} / {sufixo}",
        criterio_polo=criterio_polo,
        criterio_natureza=criterio_natureza,
        responsavel_fixo_user_id=responsavel_fixo_id,
        observacao_padrao=observacao_padrao,
        ordem=ordem,
        ativo=True,
    )
    db.add(esc)
    db.flush()  # garante esc.id pra ligar responsáveis
    return esc


def _povoar_fila(db: Session, escritorio: BbEscritorio, nomes: list[str]) -> list[str]:
    """Cria bbd_responsaveis pra fila; devolve nomes não resolvidos."""
    nao_resolvidos: list[str] = []
    for ordem, nome in enumerate(nomes):
        user_id = _resolver_user_id(db, nome)
        if user_id is None:
            nao_resolvidos.append(nome)
            continue
        # Evita duplicar caso o mesmo user apareça 2x
        ja = (
            db.query(BbResponsavel)
            .filter(
                BbResponsavel.escritorio_id == escritorio.id,
                BbResponsavel.user_id == user_id,
            )
            .first()
        )
        if ja:
            continue
        db.add(BbResponsavel(escritorio_id=escritorio.id, user_id=user_id, ordem=ordem, ativo=True))
    return nao_resolvidos


def seed_all(db: Session, *, forcar: bool = False) -> dict:
    """Cria os escritórios/filas default. Idempotente (pula se já houver dados)."""
    ja_existe = db.query(BbEscritorio).count() > 0
    if ja_existe and not forcar:
        return {"criado": False, "motivo": "escritórios já existem"}

    nao_resolvidos: list[str] = []

    esc_reu = _criar_escritorio(
        db, nome="Réu", sufixo="Réu", criterio_polo="Passivo",
        observacao_padrao="Cadastro", ordem=1,
    )
    nao_resolvidos += _povoar_fila(db, esc_reu, _RESP_REU)

    esc_autor = _criar_escritorio(
        db, nome="Autor", sufixo="Autor", criterio_polo="Ativo", ordem=2,
    )
    nao_resolvidos += _povoar_fila(db, esc_autor, _RESP_AUTOR)

    esc_interessado = _criar_escritorio(
        db, nome="Interessado", sufixo="Interessado", criterio_polo="Neutro", ordem=3,
    )
    # Interessado reusa a fila de Autor no legado
    nao_resolvidos += _povoar_fila(db, esc_interessado, _RESP_AUTOR)

    fixo_id = _resolver_user_id(db, _RESP_TRABALHISTA_FIXO)
    if fixo_id is None:
        nao_resolvidos.append(_RESP_TRABALHISTA_FIXO)
    _criar_escritorio(
        db, nome="Trabalhista", sufixo="Trabalhista", criterio_natureza="Trabalhista",
        responsavel_fixo_id=fixo_id, observacao_padrao="Cadastro", ordem=4,
    )

    registrar_evento(
        db,
        secao=SECAO_CONFIGURACAO,
        nivel=NIVEL_AVISO if nao_resolvidos else NIVEL_SUCESSO,
        acao="Configuração inicial",
        mensagem=(
            "Escritórios/filas do BB criados a partir dos padrões do robô legado."
            + (
                f" {len(nao_resolvidos)} nome(s) não casaram com usuários do Legal One "
                "e precisam de ajuste manual na tela de configuração."
                if nao_resolvidos else ""
            )
        ),
        dados={"nao_resolvidos": nao_resolvidos},
    )
    db.commit()
    return {"criado": True, "nao_resolvidos": nao_resolvidos}
