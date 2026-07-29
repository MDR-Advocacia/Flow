"""Permissão efetiva do usuário — RBAC por CARGO + exceção individual.

Modelo espelhado do Painel Financeiro (projeto irmão), adaptado à realidade do
Flow, que tem uma dimensão a mais: as EQUIPES do Minha Equipe.

    permissão efetiva = política do CARGO, com a EXCEÇÃO do usuário por cima
    (exceção com true concede, com false revoga). Admin bypassa tudo.

Por que as colunas booleanas continuam existindo
------------------------------------------------
Uns 30 pontos do backend leem `current_user.can_use_X` direto (o
`auth.require_permission`, os gates do Minha Equipe, etc.). Reescrever todos
seria risco desnecessário, então as colunas viraram **cache materializado**:
esta é a única camada que escreve nelas, sempre a partir de cargo+exceção.
Mexeu no cargo ou na exceção → `aplicar_*` recalcula e grava.

Regra das equipes (`equipes_modo` do cargo):
    nenhuma        → nenhuma equipe
    lista          → exatamente as do campo `equipes`
    todas          → todas as equipes ativas do catálogo
    supervisionadas → as equipes em que a pessoa é supervisora hoje
                      (perf_pessoa.is_supervisor), unidas às de `equipes`
O modo `supervisionadas` é o que dá aos supervisores acesso diferenciado sem
lista manual: mudou de equipe, o acesso acompanha sozinho.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Chave = nome da própria coluna booleana (evita mapa de tradução).
MODULOS: list[dict] = [
    {"key": "can_schedule_batch", "label": "LegalOne (agendamentos)", "abbr": "L1"},
    {"key": "can_use_publications", "label": "Publicações", "abbr": "Pub"},
    {"key": "can_use_prazos_iniciais", "label": "Prazos Processuais", "abbr": "PI"},
    {"key": "can_use_onerequest", "label": "OneRequest", "abbr": "OR"},
    {"key": "can_use_minha_equipe", "label": "Minha Equipe", "abbr": "ME"},
    {"key": "can_manage_distribuidos_bb", "label": "Cadastro de Processo", "abbr": "CP"},
    {"key": "notify_onerequest_errors", "label": "Notificação OneRequest", "abbr": "Notif"},
]
MODULO_KEYS = [m["key"] for m in MODULOS]

MODO_NENHUMA = "nenhuma"
MODO_LISTA = "lista"
MODO_TODAS = "todas"
MODO_SUPERVISIONADAS = "supervisionadas"
MODOS_EQUIPE = [MODO_NENHUMA, MODO_LISTA, MODO_TODAS, MODO_SUPERVISIONADAS]


def _equipes_supervisionadas(db: Session, nome_usuario: Optional[str]) -> list:
    """Equipes em que a pessoa é supervisora AGORA (join por nome normalizado —
    é a mesma chave que o resto do Minha Equipe usa pra casar L1 × roster)."""
    if not nome_usuario:
        return []
    try:
        from app.services.performance.seed import norm

        rows = db.execute(
            text(
                "SELECT DISTINCT equipe FROM perf_pessoa "
                "WHERE ativo AND is_supervisor AND nome_norm = :n AND equipe IS NOT NULL"
            ),
            {"n": norm(nome_usuario)},
        ).fetchall()
        return [r.equipe for r in rows]
    except Exception:  # noqa: BLE001
        logger.warning("Não consegui resolver equipes supervisionadas de %s.", nome_usuario,
                       exc_info=True)
        return []


def _todas_equipes() -> list:
    from app.services.performance.teams import listar

    return [t["key"] for t in listar()]


def base_cargo(db: Session, user: Any) -> dict:
    """O que o CARGO concede, ANTES da exceção do usuário.

    Serve pra `efetivas` e pros setters: quando o admin marca um checkbox, o
    valor só vira exceção se DIFERIR daqui (se bater com o cargo, a exceção é
    removida — assim exceção não acumula lixo).
    """
    cargo = None
    cargo_id = getattr(user, "cargo_id", None)
    if cargo_id:
        cargo = db.execute(
            text("SELECT id, nome, modulos, equipes_modo, equipes, ativo "
                 "FROM flow_cargo WHERE id = :id"),
            {"id": cargo_id},
        ).fetchone()

    # Cargo inativo não concede nada — a política some junto.
    modulos = {k: False for k in MODULO_KEYS}
    equipes: list = []
    if cargo is not None and cargo.ativo:
        base = cargo.modulos or {}
        for k in MODULO_KEYS:
            modulos[k] = bool(base.get(k, False))
        modo = cargo.equipes_modo or MODO_NENHUMA
        fixas = list(cargo.equipes or [])
        if modo == MODO_TODAS:
            equipes = _todas_equipes()
        elif modo == MODO_LISTA:
            equipes = list(fixas)
        elif modo == MODO_SUPERVISIONADAS:
            equipes = sorted(set(fixas) | set(_equipes_supervisionadas(db, getattr(user, "name", None))))
    return {
        "cargo_id": cargo.id if cargo is not None else None,
        "cargo_nome": cargo.nome if cargo is not None else None,
        "modulos": modulos,
        "equipes": equipes,
    }


def efetivas(db: Session, user: Any) -> dict:
    """Permissão efetiva = cargo + exceção. NÃO grava (quem materializa é `aplicar`)."""
    is_admin = (getattr(user, "role", "user") or "user") == "admin"
    base = base_cargo(db, user)
    modulos = dict(base["modulos"])
    equipes = list(base["equipes"])

    # Exceção do usuário por cima (true concede, false revoga).
    for k, v in (getattr(user, "modulos_extra", None) or {}).items():
        if k in modulos:
            modulos[k] = bool(v)
    eq = set(equipes)
    for k, v in (getattr(user, "equipes_extra", None) or {}).items():
        if v:
            eq.add(k)
        else:
            eq.discard(k)
    equipes = sorted(eq)

    return {
        "admin": is_admin,
        "cargo_id": base["cargo_id"],
        "cargo_nome": base["cargo_nome"],
        "modulos": modulos,
        "equipes": equipes,
    }


def aplicar(db: Session, user: Any, commit: bool = True) -> dict:
    """Materializa a permissão efetiva nas colunas que os gates leem.

    Devolve {"mudou": bool, "diffs": {campo: (antes, depois)}} — a validação da
    migração usa isto pra provar que ninguém mudou de acesso na virada.
    """
    # Admin bypassa TODOS os gates (auth.require_permission, _require_minha_equipe,
    # require_team_access) — o cache não muda nada pra ele. Materializar só geraria
    # ruído: o cargo "Administrador" concede todas as equipes e sobrescreveria a
    # lista que o operador deixou lá. Se um dia virar 'user', o próximo aplicar
    # ajusta. (Pego pela validação local em 29/07: o admin tinha 1 equipe e o
    # recálculo expandia pra 15.)
    if (getattr(user, "role", "user") or "user") == "admin":
        return {"mudou": False, "diffs": {}, "pulado": "admin"}

    ef = efetivas(db, user)
    diffs: dict = {}

    for k in MODULO_KEYS:
        antes = bool(getattr(user, k, False))
        depois = bool(ef["modulos"][k])
        if antes != depois:
            diffs[k] = (antes, depois)
            setattr(user, k, depois)

    csv_depois = ",".join(ef["equipes"])
    csv_antes = getattr(user, "minha_equipe_equipes", None) or ""
    if sorted(x for x in csv_antes.split(",") if x) != ef["equipes"]:
        diffs["minha_equipe_equipes"] = (csv_antes, csv_depois)
        user.minha_equipe_equipes = csv_depois or None

    if diffs and commit:
        db.commit()
    return {"mudou": bool(diffs), "diffs": diffs}


def aplicar_em_todos(db: Session, cargo_id: Optional[int] = None) -> dict:
    """Recalcula todo mundo (ou só quem é de um cargo). Usado depois de editar
    um cargo — a política mudou pra todos que a herdam."""
    from app.models.legal_one import LegalOneUser

    q = db.query(LegalOneUser)
    if cargo_id is not None:
        q = q.filter(LegalOneUser.cargo_id == cargo_id)
    alterados = []
    for u in q.all():
        r = aplicar(db, u, commit=False)
        if r["mudou"]:
            alterados.append({"user_id": u.id, "email": u.email, "diffs": r["diffs"]})
    if alterados:
        db.commit()
    return {"total_alterados": len(alterados), "alterados": alterados}


# ── Setters: escrita direta vira EXCEÇÃO sobre o cargo ────────────────────
# O admin continua marcando checkbox de módulo/equipe como sempre; por baixo
# isso só é gravado como exceção quando DIFERE do cargo. É o que permite a
# "exceção específica" sem inventar uma segunda tela.

def definir_modulos(db: Session, user: Any, valores: dict) -> dict:
    base = base_cargo(db, user)["modulos"]
    extra = dict(getattr(user, "modulos_extra", None) or {})
    for k, v in (valores or {}).items():
        if k not in MODULO_KEYS:
            continue
        if bool(v) == bool(base.get(k, False)):
            extra.pop(k, None)
        else:
            extra[k] = bool(v)
    user.modulos_extra = extra or None
    return aplicar(db, user)


def definir_equipes(db: Session, user: Any, equipes: list) -> dict:
    """`equipes` = lista final desejada. Vira exceção só onde difere do cargo."""
    base = set(base_cargo(db, user)["equipes"])
    alvo = {e for e in (equipes or []) if e}
    extra = {}
    for e in alvo - base:
        extra[e] = True          # concedida além do cargo
    for e in base - alvo:
        extra[e] = False         # revogada apesar do cargo
    user.equipes_extra = extra or None
    return aplicar(db, user)


def definir_cargo(db: Session, user: Any, cargo_id: Optional[int]) -> dict:
    """Troca o cargo PRESERVANDO a permissão efetiva atual como exceção — assim
    mudar de cargo nunca tira acesso de ninguém sem o admin perceber. As
    exceções que passarem a coincidir com o novo cargo são descartadas."""
    ef_antes = efetivas(db, user)
    user.cargo_id = cargo_id
    user.modulos_extra = None
    user.equipes_extra = None
    definir_modulos(db, user, ef_antes["modulos"])
    return definir_equipes(db, user, ef_antes["equipes"])
