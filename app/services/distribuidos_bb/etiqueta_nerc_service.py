"""Etiqueta NERC nas pastas que o motor de vínculos identificou.

Regra da operação (04/09/2026): processo com vínculo confirmado é carteira
NERC, e isso precisa ficar visível no Legal One — não só no painel do Flow.
Vale para os DOIS lados do vínculo:

  - o processo NOVO que acabou de entrar com cenário 1 ou 2;
  - as pastas ANTIGAS da mesma parte (os vínculos), que passam a ter relação
    com o novo.

⚠️ TIMING. Na hora da coleta o processo novo AINDA NÃO tem pasta no L1 — ela
nasce depois, no cadastro (medido: coleta 20:00, pasta confirmada 20:19). Por
isso a etiquetagem não vive dentro de `pesquisar_e_decidir`: é uma rotina
idempotente que varre o que está pendente e roda em dois pontos — no fim da
coleta e no monitor de cadastro, que é quem preenche o `l1_lawsuit_id`.

O carimbo `nerc_etiquetado_em` evita reenviar a cada passagem. A etiquetagem
em lote do L1 só sabe ADICIONAR (não existe "Etiquetas (Remover)"), então
reenviar seria inofensivo mas desperdiçaria um POST por coleta pra sempre.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.distribuidos_bb import (
    NIVEL_AVISO,
    NIVEL_SUCESSO,
    SECAO_DISTRIBUICAO,
    BbProcesso,
    BbVinculo,
)
from app.services.distribuidos_bb.log_service import registrar_evento

logger = logging.getLogger("distribuidos_bb.etiqueta_nerc")

# A etiqueta da casa.
#
# ⚠️ O ID MUDOU EM 04/09/2026. Existiam três parecidas: 7 "NERC" (antiga),
# 83 "BASE NERC" (o lote de julho) e 112 "NERC-CRÉD. A RECEBER". A unificação
# foi feita direto no L1 — a 7 foi EXCLUÍDA e a 83 renomeada para "NERC".
#
# Isso importa porque etiquetar com id inexistente é FALHA SILENCIOSA: o
# ModalAlterarEmLote responde 200 "Success: true, inclusão iniciada" e não
# escreve nada. Foi assim que 34 pastas voltaram sem etiqueta apesar do POST
# aceito. Se a etiqueta for reorganizada de novo, conferir aqui primeiro —
# o catálogo vive em GET /config/Tag/LookupTags.
TAG_NERC_ID = 83
TAG_NERC_NOME = "NERC"
# Teto por POST. O modal aguenta mais (100 validado na unificação de 01/09),
# mas o volume por coleta é pequeno e lote menor falha menos feio.
LOTE = 100


def _etiquetar_no_l1(lawsuit_ids: list[int]) -> None:
    """POST em lote da etiqueta. Levanta em caso de recusa do L1."""
    from app.services.distribuidos_bb.l1_web import post_l1_web

    body = [
        ("RequirirNegociacaoDeHonorarioPreenchida", "False"),
        ("ShowJustificationModal", "False"),
        ("CampoText", "Etiquetas (Adicionar)"),
        ("CampoId", "16"),
        ("Tags[0].Id", str(TAG_NERC_ID)),
        ("Tags[0].Value", TAG_NERC_NOME),
        ("selectionViewModel[SelectAll]", "false"),
        ("selectionViewModel[SelectFirsts]", "false"),
        ("selectionViewModel[UseStringIds]", "false"),
        ("selectionViewModel[UnselectedIds]", ""),
        # O servidor só exige um JSON deserializável aqui.
        ("selectionViewModel[SearchModelSerialized]", "{}"),
    ] + [("selectionViewModel[SelectedIds][]", str(x)) for x in sorted(lawsuit_ids)]

    # Mesmo helper da troca: a etiquetagem morreu com 403 no mesmo minuto em
    # que a transferência morreu (04/09/2026) — é a sessão, não o endpoint.
    resposta = post_l1_web(
        "/processos/Processos/ModalAlterarEmLote", data=body, timeout=180,
    )
    if resposta.status_code != 200:
        raise RuntimeError(f"L1 respondeu HTTP {resposta.status_code} na etiquetagem.")
    try:
        corpo = resposta.json() or {}
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("L1 devolveu resposta ilegível na etiquetagem.") from exc
    if not corpo.get("Success"):
        raise RuntimeError(f"L1 recusou a etiquetagem: {str(corpo.get('Message'))[:200]}")


def etiquetar_nerc_pendentes(db: Session, *, limite: int = 500) -> dict[str, Any]:
    """Aplica a NERC em tudo que o motor identificou e ainda não foi etiquetado.

    Idempotente: só pega quem tem `l1_lawsuit_id` e `nerc_etiquetado_em` nulo.
    Best-effort — falha aqui não pode derrubar a coleta nem o monitor.

    Devolve {"processos", "vinculos", "pastas", "erro"}. Não commita.
    """
    resultado: dict[str, Any] = {"processos": 0, "vinculos": 0, "pastas": 0, "erro": None}

    processos = (
        db.query(BbProcesso)
        .filter(
            BbProcesso.vinculo_cenario.isnot(None),
            BbProcesso.l1_lawsuit_id.isnot(None),
            BbProcesso.nerc_etiquetado_em.is_(None),
        )
        .limit(limite)
        .all()
    )
    vinculos = (
        db.query(BbVinculo)
        .filter(
            BbVinculo.l1_lawsuit_id.isnot(None),
            BbVinculo.nerc_etiquetado_em.is_(None),
        )
        .limit(limite)
        .all()
    )
    if not processos and not vinculos:
        return resultado

    # Uma pasta pode aparecer dos dois lados (o vinculado de um processo é o
    # processo de outro): dedup antes de mandar, senão o POST leva id repetido.
    ids: set[int] = {int(p.l1_lawsuit_id) for p in processos}
    ids |= {int(v.l1_lawsuit_id) for v in vinculos}

    agora = datetime.now(timezone.utc)
    lista = sorted(ids)
    try:
        for i in range(0, len(lista), LOTE):
            _etiquetar_no_l1(lista[i:i + LOTE])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Etiqueta NERC: falha ao etiquetar %s pasta(s): %s", len(lista), exc)
        registrar_evento(
            db, secao=SECAO_DISTRIBUICAO, nivel=NIVEL_AVISO, acao="Etiqueta NERC falhou",
            mensagem=(
                f"Não foi possível etiquetar {len(lista)} pasta(s) como NERC ({exc}). "
                "Nada foi marcado como etiquetado — a próxima passagem tenta de novo."
            ),
            dados={"pastas": len(lista)},
        )
        resultado["erro"] = str(exc)[:300]
        return resultado

    # Só carimba depois do L1 aceitar: se o POST falhou, tem que voltar aqui.
    for p in processos:
        p.nerc_etiquetado_em = agora
    for v in vinculos:
        v.nerc_etiquetado_em = agora
    # FLUSH: a sessão é `autoflush=False`, então sem isto uma segunda chamada
    # antes do commit não enxergaria os carimbos e reenviaria tudo pro L1.
    # Mesma armadilha que fez a pesquisa de vínculos rodar 4 dias sem ver as
    # partes recém-gravadas.
    db.flush()
    resultado.update(processos=len(processos), vinculos=len(vinculos), pastas=len(lista))

    logger.info(
        "Etiqueta NERC aplicada: %s pasta(s) (%s processo(s) novo(s), %s vinculada(s)).",
        len(lista), len(processos), len(vinculos),
    )
    registrar_evento(
        db, secao=SECAO_DISTRIBUICAO, nivel=NIVEL_SUCESSO, acao="Etiqueta NERC aplicada",
        mensagem=(
            f"{len(lista)} pasta(s) etiquetada(s) como NERC no Legal One: "
            f"{len(processos)} processo(s) com vínculo e {len(vinculos)} pasta(s) vinculada(s)."
        ),
        dados={"pastas": len(lista), "processos": len(processos), "vinculos": len(vinculos)},
    )
    return resultado
