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

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings

from app.models.distribuidos_bb import (
    EQUIPE_MISTA_NOME,
    NIVEL_AVISO,
    NIVEL_ERRO,
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
    VinculoAcessoNegado,
    apenas_digitos,
    obter_browser,
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


def _mascara_cnj(digitos: str) -> Optional[str]:
    """20 dígitos -> '0000000-00.0000.0.00.0000' (a forma como o CNJ é gravado)."""
    d = digitos
    return f"{d[:7]}-{d[7:9]}.{d[9:13]}.{d[13:14]}.{d[14:16]}.{d[16:]}" if len(d) == 20 else None


def _mascara_npj(digitos: str) -> Optional[str]:
    """14 dígitos -> '2003/0095619-000' (máscara NPJ da casa)."""
    d = digitos
    return f"{d[:4]}/{d[4:11]}-{d[11:]}" if len(d) == 14 else None


def _buscar_por_digitos(db: Session, coluna, digitos: str, mascarar) -> Optional[BbProcesso]:
    """Acha o processo comparando por DÍGITOS sem varrer a base em Python.

    Antes daqui saía um `.all()` da tabela inteira (13,5k processos viravam
    objetos ORM) para CADA vínculo de CADA processo. Agora são duas etapas:

      1. igualdade contra as formas conhecidas (máscara da casa + dígitos crus),
         que usa o índice btree da coluna — é o caminho de praticamente todos
         os casos, já que CNJ e NPJ são gravados sempre mascarados;
      2. só se a etapa 1 falhar, `regexp_replace` no servidor: continua sendo um
         seq scan, mas roda no Postgres e devolve UMA linha, em vez de trazer a
         tabela inteira para a memória do processo.
    """
    formas = [f for f in (mascarar(digitos), digitos) if f]
    if formas:
        achado = db.query(BbProcesso).filter(coluna.in_(formas)).first()
        if achado is not None:
            return achado
    return (
        db.query(BbProcesso)
        .filter(coluna.isnot(None), func.regexp_replace(coluna, r"\D", "", "g") == digitos)
        .first()
    )


# Cache do casamento no L1 dentro do processo: várias partes do mesmo processo
# costumam apontar o mesmo vínculo, e a coleta roda dezenas de processos em
# sequência. Chave = CNJ em dígitos. Zerado junto com o navegador (uma vez por
# coleta), pra não servir responsável velho na coleta seguinte.
_cache_l1: dict[str, dict[str, Any]] = {}


def limpar_cache_l1() -> None:
    """Esvazia o cache do casamento — chamado no fim da coleta."""
    _cache_l1.clear()


def _casar_no_l1(db: Session, cnj_d: str) -> Optional[dict[str, Any]]:
    """Pergunta ao PRÓPRIO L1 quem conduz a pasta desse CNJ.

    Por que existe: `_casar_na_base` só enxergava `bbd_processos`, ou seja, o
    que passou pelo fluxo de cadastro do Flow. As 1.341 pastas da Equipe Mista
    vieram da Base Analítica e NÃO estão lá — então o cenário 2 ("a parte já é
    conduzida pela equipe") era inalcançável justamente na carteira que motivou
    o módulo. Medido em 04/09/2026: dos 358 vínculos, só 136 estavam na base do
    Flow, e o cenário 2 deu ZERO em 190 processos; numa amostra de 40 vínculos
    não casados, 40 tinham pasta no L1 e 3 eram conduzidos pela Ingrid.

    Devolve o mesmo formato de `_casar_na_base`, ou None quando não achar.
    """
    from app.models.legal_one import LegalOneUser

    if cnj_d in _cache_l1:
        return _cache_l1[cnj_d]
    mascarado = _mascara_cnj(cnj_d)
    if not mascarado:
        return None
    from app.services.legal_one_client import LegalOneApiClient

    api = LegalOneApiClient()
    try:
        achados = api.search_lawsuits_by_cnj_numbers([mascarado]) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Vínculos: busca do CNJ %s no L1 falhou: %s", mascarado, exc)
        return None
    lawsuit = next((v for v in achados.values() if v.get("id")), None)
    if not lawsuit:
        _cache_l1[cnj_d] = None
        return None
    lid = int(lawsuit["id"])
    try:
        participantes = api.get_lawsuit_participants(lid) or []
    except Exception as exc:  # noqa: BLE001
        # Recurso/incidente dá 404 aqui; sem responsável não dá pra decidir.
        logger.info("Vínculos: participantes da pasta %s indisponíveis (%s).", lid, str(exc)[:80])
        return None
    principal = next(
        (p for p in participantes
         if p.get("type") == "PersonInCharge" and p.get("isMainParticipant")),
        None,
    )
    if not principal:
        _cache_l1[cnj_d] = None
        return None
    # `contactId` do participante é o `external_id` do LegalOneUser — não o id
    # interno (mesma equivalência usada no tombo de pastas).
    contato = principal.get("contactId")
    usuario = (
        db.query(LegalOneUser).filter(LegalOneUser.external_id == contato).first()
        if contato is not None else None
    )
    resultado = {
        "responsavel_id": (usuario.id if usuario else None),
        "responsavel_nome": (usuario.name if usuario else principal.get("contactName")),
        "l1_lawsuit_id": lid,
        "l1_folder": None,
    }
    if len(_cache_l1) > 5000:      # trava de memória: coleta longa não infla
        _cache_l1.clear()
    _cache_l1[cnj_d] = resultado
    return resultado


def _polos_opostos(posicao_novo, posicao_vinculado) -> bool:
    """True só quando o processo novo e o vinculado estão em polos CONTRÁRIOS.

    `posicao` é o lado do BANCO: "Autor" ou "Réu". A Equipe Mista trata a parte
    que aparece dos dois lados da carteira — ser autor contra alguém e réu numa
    ação dele. Mesmo polo é fila normal.

    Polo desconhecido (a consulta do polo no portal falhou) devolve False de
    propósito: sem saber o lado não dá pra afirmar que é oposto, e um falso
    positivo aqui reatribui processo pra equipe errada.
    """
    a = (posicao_novo or "").strip().lower()
    b = (posicao_vinculado or "").strip().lower()
    if not a or not b:
        return False
    return a != b


def _casar_na_base(db: Session, cnj: Optional[str], npj: Optional[str]) -> dict[str, Any]:
    """Descobre quem conduz o processo vinculado.

    Duas fontes, nessa ordem:
      1. `bbd_processos` — o que passou pelo fluxo de cadastro do Flow. É
         local e indexado, então vem primeiro;
      2. o L1 — para tudo que existe na carteira mas nunca passou pelo Flow
         (as pastas migradas da Base Analítica, por exemplo). Custa duas
         chamadas de API, com cache por CNJ.

    Devolve o responsável atual e a pasta no L1 (pro link direto do painel).
    Quando nenhuma das duas acha, o processo fica sem responsável conhecido —
    o que o classifica como "fora da equipe" (cenário 1).
    """
    from app.models.legal_one import LegalOneUser

    vazio = {"responsavel_id": None, "responsavel_nome": None,
             "l1_lawsuit_id": None, "l1_folder": None}
    cnj_d = apenas_digitos(cnj or "")
    npj_d = apenas_digitos(npj or "")
    achado = None
    if cnj_d:
        achado = _buscar_por_digitos(db, BbProcesso.cnj, cnj_d, _mascara_cnj)
    if achado is None and npj_d:
        achado = _buscar_por_digitos(db, BbProcesso.npj, npj_d, _mascara_npj)
    if achado is None:
        # Não passou pelo Flow: pergunta ao L1 (é onde vive a carteira real).
        if cnj_d and settings.distribuidos_bb_vinculos_casar_no_l1:
            no_l1 = _casar_no_l1(db, cnj_d)
            if no_l1:
                return no_l1
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

    # Navegador undetected com a sessão do OneLog — um por coleta, reusado
    # entre os processos (abrir custa ~10s). Se ele não subir, é acesso
    # negado: o processo segue o rodízio e NÃO fica marcado como verificado.
    try:
        browser = obter_browser(sessao)
    except Exception as exc:  # noqa: BLE001
        logger.error("Vínculos: navegador indisponível (proc %s): %s", proc.id, exc)
        registrar_evento(
            db, secao=SECAO_DISTRIBUICAO, nivel=NIVEL_ERRO, acao="Vínculos sem acesso",
            mensagem=(
                f"Não foi possível abrir o navegador da pesquisa de vínculos ({exc}). "
                "O processo seguiu o rodízio padrão e NÃO foi marcado como verificado."
            ),
            processo_id=proc.id, run_id=getattr(run, "id", None),
        )
        proc.vinculos_verificado_em = None
        proc.vinculos_qtd = 0
        return resultado
    advogado = _advogado_mdr(db)
    # Montagem de dossiê não conta: a pasta existe mas o processo ainda não foi
    # distribuído pra nós (provável recuperação de crédito futura).
    excluidas = _cfg(db, "vinculo_situacoes_excluidas", SITUACOES_EXCLUIDAS_DEFAULT)
    proprio_npj = apenas_digitos(proc.npj or "")

    # Pesquisa cada parte e agrega os vínculos (dedupe por NPJ; exclui o próprio
    # processo novo, que também aparece na busca da parte).
    achados: dict[str, dict[str, Any]] = {}
    # Polos de TODAS as pastas ativas da parte no MDR, inclusive as que o filtro
    # de polo oposto descarta. É o que permite saber se a parte já era mista
    # ANTES deste processo (ver `ja_era_mista` em decidir_e_persistir).
    polos_da_parte: set[str] = set()
    for e in partes:
        try:
            res = pesquisar_vinculos_parte(
                browser, e.cpf_cnpj, advogado_mdr=advogado, situacoes_excluidas=excluidas,
            )
        except VinculoAcessoNegado as exc:
            # O portal recusou a consulta: NÃO dá pra afirmar que a parte não
            # tem vínculo. Aborta o processo inteiro sem marcar como verificado
            # — senão ele entra na estatística como "pesquisado, nada achado" e
            # a falha some. Fica pro próximo run, com o erro visível no painel.
            logger.error("Vínculos: acesso negado ao portal (proc %s): %s", proc.id, exc)
            registrar_evento(
                db, secao=SECAO_DISTRIBUICAO, nivel=NIVEL_ERRO, acao="Vínculos sem acesso",
                mensagem=(
                    f"O portal do BB recusou a pesquisa de vínculos ({exc}). O processo seguiu "
                    "o rodízio padrão e NÃO foi marcado como verificado."
                ),
                dados={"parte": (e.cpf_cnpj or "")[:6] + "…"},
                processo_id=proc.id, run_id=getattr(run, "id", None),
            )
            proc.vinculos_verificado_em = None
            proc.vinculos_qtd = 0
            return resultado
        except Exception as exc:  # noqa: BLE001
            logger.warning("Vínculos: pesquisa falhou pra parte %s (proc %s): %s",
                           e.cpf_cnpj, proc.id, exc)
            continue
        for v in res["ativos_mdr"]:
            npj_d = apenas_digitos(v["npj"])
            if npj_d == proprio_npj or npj_d in achados:
                continue
            # REGRA DA EQUIPE MISTA: só interessa quando os polos são OPOSTOS.
            # A equipe existe pra conduzir a parte que é adversa dos dois lados
            # — somos autor contra ela num processo e réu noutro. Dois processos
            # do MESMO polo são trabalho normal da fila, não caso de equipe
            # mista. Sem essa checagem o motor marcou 80 processos em 04/09/2026
            # dos quais só 16 tinham vínculo de polo oposto (95% dos 358
            # vínculos eram Réu×Réu ou Autor×Autor).
            # O polo de TODA pasta da parte é anotado ANTES do filtro — é ele
            # que diz se a parte já era mista antes deste processo chegar (ver
            # `ja_era_mista` em decidir_e_persistir). Como o filtro abaixo só
            # deixa passar o polo OPOSTO, sem anotar aqui essa informação se
            # perderia: entre os vínculos gravados nunca há dois polos.
            polos_da_parte.add((v.get("posicao_banco") or "").strip())
            if not _polos_opostos(proc.posicao, v.get("posicao_banco")):
                continue
            v["_envolvido_id"] = e.id
            v["_doc_parte"] = apenas_digitos(e.cpf_cnpj)
            v["_nome_parte"] = e.nome
            v["_numero_pessoa"] = res["numero_pessoa"]
            achados[npj_d] = v

    return decidir_e_persistir(db, run, proc, achados, polos_da_parte=polos_da_parte)


def decidir_e_persistir(
    db: Session,
    run: Any,
    proc: BbProcesso,
    achados: dict[str, dict[str, Any]],
    *,
    polos_da_parte: Optional[set[str]] = None,
) -> dict[str, Any]:
    """Metade DECISÓRIA do fluxo — recebe os vínculos já pesquisados.

    `achados` é {npj_digitos: vinculo} no formato de `pesquisar_vinculos_parte`
    (+ chaves internas _envolvido_id/_doc_parte/_nome_parte/_numero_pessoa).
    Quem alimenta pode ser a pesquisa inline (requests, modo "inline") ou o
    RPA externo via intake (modo "rpa") — a regra de cenário, a persistência
    de BbVinculo e os eventos são OS MESMOS nos dois caminhos.
    """
    resultado: dict[str, Any] = {"cenario": None, "responsavel_override_id": None}

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

    # A parte JÁ era mista antes deste processo chegar?
    #
    # Não basta uma pasta antiga estar com alguém da fila da equipe: pertencer à
    # fila não quer dizer que aquela pasta foi parar lá POR SER NERC. O que
    # define a carteira é a parte ser adversa dos DOIS lados — logo, a parte só
    # já era mista se as pastas antigas, entre si, já tinham os dois polos.
    #
    # Caso que expôs o erro (K C SERVIÇOS, 04/09/2026): duas pastas antigas, as
    # duas BB Autor, e chegou uma de Réu. O conflito nasceu AGORA. O motor dizia
    # "parte já especializada" porque a Ingrid conduzia uma delas — e como o
    # cenário 2 não marca transição, a outra pasta (com a Letícia) nunca foi
    # sinalizada e ficou fora da equipe. Reação do operador: "tinham dois
    # processos autor e chegou um réu, ele não devia estar no NERC
    # anteriormente, ele deveria ir agora".
    #
    # Sem a informação (caminho do RPA, que não anota os polos), assume-se que
    # NÃO era mista: manda tudo pra equipe com transição. É o lado seguro de
    # errar — a transferência é idempotente e fecha sem POST o que já está no
    # lugar certo.
    polos = {p.strip().lower() for p in (polos_da_parte or set()) if p and p.strip()}
    ja_era_mista = len(polos) >= 2

    if responsavel_especializado and ja_era_mista:
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
