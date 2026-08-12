"""Captura de utilização do sistema (navegação) para o relatório de adesão.

DESENHO: acumula em memória e descarrega no banco a cada 60s, em vez de gravar
a cada requisição. Uma escrita por requisição colocaria o banco no caminho
crítico de TODA chamada autenticada do Flow — inclusive das telas que se
auto-atualizam — e o custo não se paga: o relatório é lido uma vez por semana
pelo administrativo. O preço é perder até 60s de contagem se o processo morrer
de repente, o que para medir adesão não muda conclusão nenhuma.

A captura NUNCA pode derrubar uma requisição. Todo o caminho é try/except
silencioso: relatório gerencial não é motivo para ninguém tomar 500.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Prefixo da rota → nome de negócio. A ordem importa: o primeiro que casar
# vence, então prefixos mais específicos vêm antes dos genéricos.
_MAPA_MODULOS: list[tuple[str, str]] = [
    ("/api/v1/publications/treatment", "Publicações"),
    ("/api/v1/publications/performance", "Publicações"),
    ("/api/v1/publications", "Publicações"),
    ("/api/v1/prazos-iniciais", "Prazos Iniciais"),
    ("/api/v1/prazos_iniciais", "Prazos Iniciais"),
    ("/api/v1/performance", "Minha Equipe"),
    ("/api/v1/balanceador", "Balanceamento de Agenda"),
    ("/api/v1/distribuidos-bb", "Distribuídos BB"),
    ("/api/v1/distribuidos_bb", "Distribuídos BB"),
    ("/api/v1/onerequest", "OneRequest"),
    ("/api/v1/encerramentos", "Encerramentos"),
    ("/api/v1/classificador", "Classificador"),
    ("/api/v1/classifier", "Classificação"),
    ("/api/v1/recursal", "Análise Recursal"),
    ("/api/v1/base-processual", "Base Processual"),
    ("/api/v1/base_processual", "Base Processual"),
    ("/api/v1/citacoes", "Citações BM"),
    ("/api/v1/contatos", "Contatos LegalOne"),
    ("/api/v1/ged", "GED LegalOne"),
    ("/api/v1/ajus", "AJUS"),
    ("/api/v1/task-templates", "Templates de Tarefa"),
    ("/api/v1/automations", "Automações"),
    ("/api/v1/squads", "Squads"),
    ("/api/v1/dashboard", "Dashboard"),
    ("/api/v1/admin", "Administração"),
    ("/api/v1/tasks", "Tarefas"),
    ("/api/v1/users", "Administração"),
]

# Rotas que NÃO contam como uso: são chamadas de infraestrutura que o front
# dispara sozinho ao abrir qualquer tela. Contá-las faria todo mundo parecer
# ativo em "Administração" só por ter feito login.
_IGNORAR = (
    "/api/v1/auth",
    "/api/v1/me",
    "/api/v1/notices",
    "/api/v1/admin/notices",
    "/health",
    "/metrics",
)

INTERVALO_FLUSH_S = 60

_lock = threading.Lock()
# (user_id, dia_iso, modulo) -> {"n": int, "primeira": dt, "ultima": dt}
_buffer: dict[tuple[int, str, str], dict] = {}
_ultimo_flush = datetime.now(timezone.utc)


def modulo_da_rota(path: str) -> str | None:
    """Traduz o caminho da requisição no nome do módulo. None = não contar."""
    if not path:
        return None
    for ignorado in _IGNORAR:
        if path.startswith(ignorado):
            return None
    for prefixo, nome in _MAPA_MODULOS:
        if path.startswith(prefixo):
            return nome
    return None


def registrar(user_id: int, path: str) -> None:
    """Contabiliza uma requisição. Barato: só mexe em dicionário na memória."""
    try:
        modulo = modulo_da_rota(path)
        if not modulo or not user_id:
            return
        agora = datetime.now(timezone.utc)
        # O dia é o de Brasília: o relatório é lido por gente daqui, e o corte
        # em UTC jogaria o fim da tarde pro dia seguinte.
        dia = (agora - timedelta(hours=3)).date().isoformat()
        chave = (int(user_id), dia, modulo)
        with _lock:
            item = _buffer.get(chave)
            if item is None:
                _buffer[chave] = {"n": 1, "primeira": agora, "ultima": agora}
            else:
                item["n"] += 1
                item["ultima"] = agora
        _talvez_descarregar(agora)
    except Exception:  # noqa: BLE001
        # Silencioso de propósito: ver nota no topo do módulo.
        pass


def _talvez_descarregar(agora: datetime) -> None:
    global _ultimo_flush
    if (agora - _ultimo_flush).total_seconds() < INTERVALO_FLUSH_S:
        return
    _ultimo_flush = agora
    # Em thread: o flush escreve no banco e não pode segurar a resposta de
    # quem por acaso foi a requisição que cruzou o intervalo.
    threading.Thread(target=descarregar, daemon=True,
                     name="uso-flush").start()


def descarregar() -> int:
    """Grava o acumulado no banco. Devolve quantas linhas foram tocadas."""
    with _lock:
        if not _buffer:
            return 0
        pendente = dict(_buffer)
        _buffer.clear()

    try:
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from app.db.session import SessionLocal
        from app.models.uso import UsoDiario

        db = SessionLocal()
        try:
            for (uid, dia, modulo), v in pendente.items():
                stmt = pg_insert(UsoDiario).values(
                    user_id=uid, dia=dia, modulo=modulo,
                    requisicoes=v["n"],
                    primeira_em=v["primeira"], ultima_em=v["ultima"],
                )
                # Soma em vez de sobrescrever: o mesmo par usuário/módulo é
                # descarregado várias vezes ao longo do dia, e cada worker tem
                # seu próprio buffer.
                stmt = stmt.on_conflict_do_update(
                    index_elements=["user_id", "dia", "modulo"],
                    set_={
                        "requisicoes": UsoDiario.requisicoes + v["n"],
                        "ultima_em": stmt.excluded.ultima_em,
                    },
                )
                db.execute(stmt)
            db.commit()
            return len(pendente)
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("uso: falha ao gravar o acumulado (%s)", exc)
        # O que não foi gravado é descartado: reencaixar no buffer arriscaria
        # dobrar a contagem se o commit tiver passado antes do erro.
        return 0
