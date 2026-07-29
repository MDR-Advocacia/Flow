"""Registro dos times (setores/supervisões) do Minha Equipe.

A fonte da verdade é a tabela `perf_equipe` (migration perf012) — o admin cria,
renomeia e desativa equipe pela tela, sem deploy. A lista abaixo é só o
**fallback de bootstrap**: vale antes da migration rodar ou se o banco estiver
indisponível, pra o menu/gate nunca ficarem vazios (o que trancaria todo mundo
fora do módulo).

Cache em processo com TTL curto: estas funções são chamadas em toda request do
Minha Equipe/Balanceador (gate de permissão) e não podem virar um SELECT por
chamada. A escrita no CRUD chama `invalidar_cache()` — quem estiver em outro
worker do uvicorn pega a mudança no fim do TTL.

A planilha de squads tem uma aba por subgrupo, e algumas abas se juntam num
setor (ex.: BB Réu = BB Defesa + BB Réu + Recursos); o mapa aba→setor fica no
seed (`TAB_TO_SETOR`) e só cobre as equipes do Passivo — as demais têm o roster
montado na própria tela.
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

# Fallback de bootstrap — espelha o seed da migration perf012. NÃO é a fonte da
# verdade: só entra em cena se a tabela não existir/responder.
_FALLBACK = [
    {"key": "bb-reu", "label": "BB Réu", "grupo": "Contencioso Passivo"},
    {"key": "bb-execucao", "label": "BB Execução & Encerramento", "grupo": "Contencioso Passivo"},
    {"key": "bb-acordos", "label": "BB Acordos", "grupo": "Contencioso Passivo"},
    {"key": "bb-estrategico", "label": "BB Estratégico", "grupo": "Contencioso Passivo"},
    {"key": "master-reu", "label": "Master Réu", "grupo": "Contencioso Passivo"},
    {"key": "ativos-reu", "label": "Ativos Réu", "grupo": "Contencioso Passivo"},
    {"key": "trabalhista", "label": "Trabalhista", "grupo": "Contencioso Passivo"},
    {"key": "bb-autor-processual", "label": "BB Autor — Processual", "grupo": "Recuperação de Crédito"},
    {"key": "ativos-autor", "label": "Ativos Autor", "grupo": "Recuperação de Crédito"},
    {"key": "autor-recursal", "label": "Autor — Recursal", "grupo": "Recuperação de Crédito"},
    {"key": "ajuizamento", "label": "Ajuizamento", "grupo": "Recuperação de Crédito"},
    {"key": "estrategico-autor", "label": "Estratégico Autor", "grupo": "Recuperação de Crédito"},
    {"key": "cobranca", "label": "Cobrança", "grupo": "Recuperação de Crédito"},
    {"key": "equipe-mista", "label": "Equipe Mista", "grupo": "Especializada"},
    # Controladoria — sucede o antigo "BB Cadastro" (perfil extinto em 2026-07-20).
    # Mantém a MESMA key: as permissões já concedidas (CSV em
    # legal_one_users.minha_equipe_equipes) e as tarefas históricas apontam pra
    # ela; trocar o slug revogaria acesso silenciosamente e orfanaria os dados.
    {"key": "bb-cadastro", "label": "Controladoria", "grupo": "Especializada"},
]

_TTL_S = 60
_lock = threading.Lock()
_cache: list | None = None
_cache_em: float = 0.0


def invalidar_cache() -> None:
    """Chamado pelo CRUD depois de criar/editar/excluir equipe."""
    global _cache, _cache_em
    with _lock:
        _cache = None
        _cache_em = 0.0


def _carregar() -> list:
    """Lê as equipes ATIVAS do banco. Fallback se a tabela ainda não existe."""
    from sqlalchemy import text

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT key, label, grupo FROM perf_equipe "
                "WHERE ativo ORDER BY ordem, label"
            )
        ).fetchall()
        if not rows:
            return list(_FALLBACK)
        return [{"key": r.key, "label": r.label, "grupo": r.grupo} for r in rows]
    except Exception:  # noqa: BLE001
        logger.warning(
            "perf_equipe indisponível (migration perf012 já rodou?) — usando o "
            "fallback embutido de equipes.", exc_info=True,
        )
        return list(_FALLBACK)
    finally:
        db.close()


def listar() -> list:
    """Equipes ativas [{key, label, grupo}], em ordem de exibição (cacheado)."""
    global _cache, _cache_em
    agora = time.time()
    if _cache is not None and (agora - _cache_em) < _TTL_S:
        return _cache
    with _lock:
        if _cache is not None and (time.time() - _cache_em) < _TTL_S:
            return _cache
        _cache = _carregar()
        _cache_em = time.time()
        return _cache


def team_keys() -> set:
    """Keys válidas (gate de permissão/rota)."""
    return {t["key"] for t in listar()}


def team_label(key: str) -> str:
    """Rótulo da equipe. Resolve TAMBÉM as desativadas — histórico e relatório
    antigo precisam do nome, senão o operador vê um slug cru."""
    for t in listar():
        if t["key"] == key:
            return t["label"]
    return _label_inativa(key)


def _label_inativa(key: str) -> str:
    from sqlalchemy import text

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        r = db.execute(
            text("SELECT label FROM perf_equipe WHERE key = :k"), {"k": key}
        ).fetchone()
        if r:
            return r.label
    except Exception:  # noqa: BLE001
        pass
    finally:
        db.close()
    for t in _FALLBACK:
        if t["key"] == key:
            return t["label"]
    return key


def is_valid_team(key: str) -> bool:
    return key in team_keys()


# ── Compat: código antigo importava as constantes direto ──────────────────
# `TEAMS` continua exposto (leitura pontual, ex.: scripts); quem valida em
# request deve usar `team_keys()`, que respeita o cache/TTL.
TEAMS = _FALLBACK
