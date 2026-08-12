"""Orquestração dos vínculos: pesquisa as partes, decide o cenário e o responsável.

Roda DENTRO da coleta, depois da captura dos envolvidos e ANTES da distribuição —
porque o resultado muda o responsável da pasta (o escritório segue o padrão):

  CENÁRIO 1 — a parte tinha processo(s) ativo(s) conosco fora da equipe
    especializada: o novo vai pro PRÓXIMO do rodízio da "Equipe Mista
    Especializada" e os antigos ficam `transicao_pendente` (o supervisor conduz
    a transição manual — o sistema NÃO redistribui o antigo).
  CENÁRIO 2 — a parte já é conduzida pela equipe especializada: o novo vai pro
    MESMO responsável que já cuida dos processos dela.

A identificação de "já é conduzida" é feita consultando NOSSA base (bbd_processos
por CNJ/NPJ → responsável) e conferindo se esse responsável pertence à fila da
Equipe Mista. Falha de pesquisa nunca derruba a coleta (best-effort com evento).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.distribuidos_bb import (
    EQUIPE_MISTA_NOME,
    NIVEL_AVISO,
    NIVEL_INFO,
    NIVEL_SUCESSO,
    SECAO_DISTRIBUICAO,
    VINCULO_CENARIO_1,
    VINCULO_CENARIO_2,
    BbConfig,
    BbEnvolvido,
    BbEscritorio,
    BbProcesso,
    BbResponsavel,
    BbVinculo,
)
from app.services.distribuidos_bb.log_service import registrar_evento
from app.services.distribuidos_bb.vinculos_bb import (
    ADVOGADO_MDR_DEFAULT,
    CNPJ_BB,
    SITUACOES_EXCLUIDAS_DEFAULT,
    apenas_digitos,
    montar_sessao,
    pesquisar_vinculos_parte,
)

logger = logging.getLogger("distribuidos_bb.vinculos")


def _cfg(db: Session, chave: str, default: str) -> str:
    c = db.get(BbConfig, chave)
    return c.valor if (c and c.valor is not None) else default


def _advogado_mdr(db: Session) -> int:
    try:
        return int(_cfg(db, "vinculo_advogado_mdr", str(ADVOGADO_MDR_DEFAULT)))
    except (TypeError, ValueError):
        return ADVOGADO_MDR_DEFAULT


def _fila_equipe_mista(db: Session) -> tuple[Optional[BbEscritorio], set[int]]:
    """Escritório-fila da equipe especializada + user_ids ativos da fila."""
    esc = (
        db.query(BbEscritorio)
        .filter(BbEscritorio.nome == EQUIPE_MISTA_NOME, BbEscritorio.ativo.is_(True))
        .first()
    )
    if esc is None:
        return None, set()
    membros = {
        r.user_id
        for r in db.query(BbResponsavel)
        .filter(BbResponsavel.escritorio_id == esc.id, BbResponsavel.ativo.is_(True))
        .all()
    }
    return esc, membros


def _casar_na_base(db: Session, cnj: Optional[str], npj: Optional[str]) -> dict[str, Any]:
    """Casa o processo vinculado com a NOSSA base (por CNJ, senão por NPJ).

    Devolve o responsável atual e a pasta no L1 (pro link direto do painel).
    Processos antigos que nunca passaram pelo Flow não casam — ficam sem
    responsável conhecido, o que os classifica como "fora da equipe".
    """
    from app.models.legal_one import LegalOneUser

    vazio = {"responsavel_id": None, "responsavel_nome": None,
             "l1_lawsuit_id": None, "l1_folder": None}
    cnj_d = apenas_digitos(cnj or "")
    npj_d = apenas_digitos(npj or "")
    achado = None
    if cnj_d:
        achado = next(
            (p for p in db.query(BbProcesso).filter(BbProcesso.cnj.isnot(None)).all()
             if apenas_digitos(p.cnj) == cnj_d),
            None,
        )
    if achado is None and npj_d:
        achado = next(
            (p for p in db.query(BbProcesso).filter(BbProcesso.npj.isnot(None)).all()
             if apenas_digitos(p.npj) == npj_d),
            None,
        )
    if achado is None:
        return vazio
    u = db.get(LegalOneUser, achado.responsavel_user_id) if achado.responsavel_user_id else None
    return {
        "responsavel_id": achado.responsavel_user_id,
        "responsavel_nome": (u.name if u else None),
        "l1_lawsuit_id": achado.l1_lawsuit_id,
        "l1_folder": achado.l1_folder,
    }


def pesquisar_e_decidir(db: Session, run: Any, proc: BbProcesso, portal: Any) -> dict[str, Any]:
    """Pesquisa vínculos das partes do processo e decide cenário/responsável.

    Devolve {"cenario": None|CENARIO_1|CENARIO_2, "responsavel_override_id": int|None}.
    Persiste BbVinculo + resumo no processo e registra eventos de auditoria.
    """
    resultado: dict[str, Any] = {"cenario": None, "responsavel_override_id": None}

    sessao = getattr(portal, "sessao_onelog", None)
    if not sessao:
        return resultado

    # Partes a pesquisar: envolvidos com documento, exceto o próprio BB.
    envolvidos = (
        db.query(BbEnvolvido)
        .filter(BbEnvolvido.processo_id == proc.id, BbEnvolvido.cpf_cnpj.isnot(None))
        .all()
    )
    partes = []
    docs_vistos: set[str] = set()
    for e in envolvidos:
        d = apenas_digitos(e.cpf_cnpj)
        if not d or d == CNPJ_BB or d in docs_vistos:
            continue
        docs_vistos.add(d)
        partes.append(e)
    if not partes:
        proc.vinculos_qtd = 0
        proc.vinculos_verificado_em = datetime.now(timezone.utc)
        return resultado

    sess = montar_sessao(sessao.get("cookies", []), sessao.get("user_agent", ""))
    advogado = _advogado_mdr(db)
    # Montagem de dossiê não conta: a pasta existe mas o processo ainda não foi
    # distribuído pra nós (provável recuperação de crédito futura).
    excluidas = _cfg(db, "vinculo_situacoes_excluidas", SITUACOES_EXCLUIDAS_DEFAULT)
    proprio_npj = apenas_digitos(proc.npj or "")

    # Pesquisa cada parte e agrega os vínculos (dedupe por NPJ; exclui o próprio
    # processo novo, que também aparece na busca da parte).
    achados: dict[str, dict[str, Any]] = {}
    for e in partes:
        try:
            res = pesquisar_vinculos_parte(
                sess, e.cpf_cnpj, advogado_mdr=advogado, situacoes_excluidas=excluidas,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Vínculos: pesquisa falhou pra parte %s (proc %s): %s",
                           e.cpf_cnpj, proc.id, exc)
            continue
        for v in res["ativos_mdr"]:
            npj_d = apenas_digitos(v["npj"])
            if npj_d == proprio_npj or npj_d in achados:
                continue
            v["_envolvido_id"] = e.id
            v["_doc_parte"] = apenas_digitos(e.cpf_cnpj)
            v["_nome_parte"] = e.nome
            v["_numero_pessoa"] = res["numero_pessoa"]
            achados[npj_d] = v

    # Reprocesso idempotente: apaga os vínculos anteriores deste processo.
    db.query(BbVinculo).filter(BbVinculo.processo_id == proc.id).delete(synchronize_session=False)

    agora = datetime.now(timezone.utc)
    proc.vinculos_qtd = len(achados)
    proc.vinculos_verificado_em = agora

    if not achados:
        proc.vinculo_cenario = None
        return resultado

    esc_mista, membros_mista = _fila_equipe_mista(db)

    # Persiste cada vínculo já com o responsável atual (da nossa base) resolvido.
    vinculos: list[BbVinculo] = []
    responsavel_especializado: Optional[int] = None
    for v in achados.values():
        casado = _casar_na_base(db, v.get("cnj"), v.get("npj"))
        resp_id = casado["responsavel_id"]
        resp_nome = casado["responsavel_nome"]
        na_mista = bool(resp_id and resp_id in membros_mista)
        if na_mista and responsavel_especializado is None:
            responsavel_especializado = resp_id
        vinculos.append(BbVinculo(
            processo_id=proc.id,
            envolvido_id=v.get("_envolvido_id"),
            doc_parte=v.get("_doc_parte"),
            nome_parte=v.get("_nome_parte"),
            numero_pessoa=v.get("_numero_pessoa"),
            npj=v["npj"],
            numero_processo=str(v.get("numero_processo") or ""),
            cnj=v.get("cnj"),
            contrario_nome=v.get("cliente"),
            advogado_bb=v.get("advogado_bb"),
            situacao=v.get("situacao"),
            natureza=v.get("natureza"),
            uja=v.get("uja"),
            polo=v.get("polo"),
            posicao_banco=v.get("posicao_banco"),
            l1_lawsuit_id=casado["l1_lawsuit_id"],
            l1_folder=casado["l1_folder"],
            responsavel_atual_user_id=resp_id,
            responsavel_atual_nome=resp_nome,
            na_equipe_mista=na_mista,
            raw={k: v[k] for k in v if not k.startswith("_")},
        ))

    if responsavel_especializado:
        # CENÁRIO 2 — a parte já é conduzida pela equipe: mesmo responsável.
        cenario = VINCULO_CENARIO_2
        override = responsavel_especializado
    else:
        # CENÁRIO 1 — novo vai pro rodízio da Equipe Mista; antigos sinalizados.
        cenario = VINCULO_CENARIO_1
        override = None
        if esc_mista is not None:
            from app.services.distribuidos_bb.distribuicao_service import _proximo_responsavel_rr

            override = _proximo_responsavel_rr(db, esc_mista)
        for vin in vinculos:
            vin.transicao_pendente = True

    for vin in vinculos:
        db.add(vin)
    proc.vinculo_cenario = cenario

    if override is None:
        # Fila da Equipe Mista vazia/inexistente: NÃO trava — segue o rodízio
        # padrão e avisa alto no painel/log pra configurarem a fila.
        registrar_evento(
            db, secao=SECAO_DISTRIBUICAO, nivel=NIVEL_AVISO, acao="Vínculo sem fila especializada",
            mensagem=(
                f"{len(vinculos)} vínculo(s) ativo(s) do MDR encontrados pra parte, mas a fila "
                f"'{EQUIPE_MISTA_NOME}' está vazia — o processo seguiu o rodízio padrão. "
                f"Configure a fila em Escritórios & Filas."
            ),
            dados={"vinculos": len(vinculos), "cenario": cenario},
            processo_id=proc.id, run_id=getattr(run, "id", None),
        )
    else:
        registrar_evento(
            db, secao=SECAO_DISTRIBUICAO, nivel=NIVEL_SUCESSO,
            acao=("Vínculo — parte já especializada" if cenario == VINCULO_CENARIO_2
                  else "Vínculo — novo caso pra equipe especializada"),
            mensagem=(
                f"Parte com {len(vinculos)} processo(s) ativo(s) conduzido(s) pelo MDR. "
                + ("Novo processo direcionado ao MESMO responsável que já atende a parte."
                   if cenario == VINCULO_CENARIO_2
                   else "Novo processo direcionado ao rodízio da Equipe Mista; processos "
                        "antigos sinalizados pra transição manual pelo supervisor.")
            ),
            dados={"vinculos": len(vinculos), "cenario": cenario, "responsavel_id": override},
            processo_id=proc.id, run_id=getattr(run, "id", None),
        )

    resultado["cenario"] = cenario
    resultado["responsavel_override_id"] = override
    return resultado
