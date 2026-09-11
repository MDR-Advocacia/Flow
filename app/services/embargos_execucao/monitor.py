"""Monitor do tribunal: consulta os cards com consulta vencida.

Para cada execução em MONITORANDO:
  1. o L1 já tem incidente de embargos na pasta (`Proc - X/00N`)? → JA_CADASTRADO;
  2. capa da execução no DataJud (vara, data, movimentos);
  3. embargos (classe 172) da mesma vara desde a data da execução → candidatos;
  4. cada candidato novo ou ainda indefinido passa pelo DJEN (embargante é parte
     da execução?) e pelos sinais do DataJud (dependência, petição no dia);
  5. candidato forte → ENCONTRADO, para e avisa; senão, próxima consulta em X
     dias úteis — até o teto, que fecha como SEM_EMBARGOS.

Nada aqui decide pelo operador: forte é "vá conferir", a confirmação é dele.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.embargos_execucao import (
    DECISAO_PENDENTE,
    ESTADO_AGUARDANDO_JANELA,
    ESTADO_ENCONTRADO,
    ESTADO_JA_CADASTRADO,
    ESTADO_MONITORANDO,
    ESTADO_SEM_CNJ,
    ESTADO_SEM_EMBARGOS,
    EVT_AVISO,
    EVT_ERRO,
    EVT_INFO,
    NIVEIS_FORTES,
    NIVEL_CONFIRMADO_DJEN,
    NIVEL_DESCARTADO,
    NIVEL_FRACO,
    NIVEL_PROVAVEL,
    SECAO_L1,
    SECAO_TRIBUNAL,
    EmbCandidato,
    EmbExecucao,
    EmbParte,
)
from app.services.embargos_execucao import agenda, datajud_embargos, djen_embargos, service
from app.services.embargos_execucao.normaliza import cnj_digitos, formatar_cnj, nome_normalizado

logger = logging.getLogger(__name__)

# DJEN tem espera de ~3 s por chamada: execução antiga numa vara movimentada
# pode trazer dezenas de candidatos — o resto fica pra próxima consulta.
MAX_DJEN_POR_CONSULTA = 30


def classificar_nivel(
    djen_status: Optional[str],
    dependencia: bool,
    peticao_no_dia: bool,
    *,
    djen_disponivel: bool = True,
) -> str:
    if djen_status == djen_embargos.DJEN_CONFIRMADO:
        return NIVEL_CONFIRMADO_DJEN
    # Embargante de outro processo OU embargado que não é o cliente da execução.
    if djen_status in (djen_embargos.DJEN_NAO_BATEU, djen_embargos.DJEN_EMBARGADO_OUTRO):
        return NIVEL_DESCARTADO
    # Com o DJEN no ar, candidato ainda não conferido (estourou o teto de
    # chamadas da consulta) espera a próxima passagem: no teste real de
    # 11/09/2026, os sinais do DataJud sozinhos deram 1 "provável" falso entre
    # 44 embargos da 3ª Vara Cível de Parauapebas.
    if djen_status is None and djen_disponivel:
        return NIVEL_FRACO
    if dependencia and peticao_no_dia:
        return NIVEL_PROVAVEL
    return NIVEL_FRACO


# Tipo de ação do L1 dos incidentes de embargos cadastrados à mão (medido nos
# incidentes 98774 e 79943). O título NÃO basta: "Proc - 0068696/001" (embargos
# 7011603-39.2026.8.22.0005) está no L1 com title nulo — o teste local de
# 11/09/2026 deixou passar esse incidente quando a regra era só o título.
ACTION_TYPE_EMBARGOS = 67


def incidentes_de_embargos(itens: list[dict[str, Any]], cnjs_conhecidos=()) -> list[dict[str, Any]]:
    """Incidentes da pasta que são os embargos: título, tipo de ação 67 ou o
    CNJ de um candidato já achado no tribunal."""
    conhecidos = {c for c in cnjs_conhecidos if c}
    return [
        i for i in itens
        if "EMBARG" in nome_normalizado(i.get("title"))
        or i.get("actionTypeId") == ACTION_TYPE_EMBARGOS
        or (cnj_digitos(i.get("identifierNumber")) or "") in conhecidos
    ]


def verificar_incidente_l1(db: Session, exe: EmbExecucao, l1_client) -> bool:
    """True se a pasta já tem incidente de embargos no L1 (card vira JA_CADASTRADO)."""
    if not l1_client or not exe.pasta or exe.pasta.startswith("CNJ "):
        return False
    url = f"{l1_client.base_url}/ProceduralIssues"
    params = {
        "$filter": f"startswith(folder,'{exe.pasta.replace(chr(39), '')}/')",
        "$select": "id,folder,title,identifierNumber,actionTypeId,responsibleOfficeId,distributionDate",
        "$top": 30,
    }
    resp = l1_client._request_with_retry("GET", url, params=params)
    itens = (resp.json() or {}).get("value") or []
    exe.l1_incidente_verificado_em = service.agora()
    embargos = incidentes_de_embargos(itens, [c.cnj_digitos for c in exe.candidatos])
    if not embargos:
        return False
    inc = embargos[0]
    exe.estado = ESTADO_JA_CADASTRADO
    exe.proxima_consulta = None
    exe.incidente_folder = inc.get("folder")
    exe.incidente_id = inc.get("id")
    exe.incidente_office_id = inc.get("responsibleOfficeId")
    exe.incidente_cnj = formatar_cnj(inc.get("identifierNumber")) or inc.get("identifierNumber")
    service.registrar_evento(
        db, SECAO_L1,
        f"A pasta já tem incidente de embargos no Legal One ({inc.get('folder')}, "
        f"{exe.incidente_cnj or 'sem CNJ'}) — monitoramento encerrado.",
        execucao_id=exe.id, dados={"incidentes": embargos},
    )
    return True


def _referencias(db: Session, exe: EmbExecucao, djen, djen_client) -> list[str]:
    nomes = [p.nome for p in exe.partes if p.demandada]
    if nomes:
        return nomes
    # Portal do BB sem partes (sem NPJ, cliente sem portal ou coleta falhando):
    # os executados das intimações da própria execução servem de referência.
    try:
        executados = djen.executados_da_execucao(exe.cnj, djen_client, cliente=exe.cliente or "BB")
    except Exception as exc:  # noqa: BLE001
        logger.info("Embargos: executados do DJEN indisponíveis para %s: %s", exe.pasta, exc)
        return []
    for nome in executados:
        exe.partes.append(EmbParte(origem="DJEN", polo="Passivo", nome=nome, demandada=True))
    if executados:
        service.registrar_evento(
            db, SECAO_TRIBUNAL,
            f"Executados lidos nas intimações da execução (DJEN): {', '.join(executados)}.",
            execucao_id=exe.id,
        )
    return executados


def _agendar_proxima(db: Session, exe: EmbExecucao, hoje: date) -> None:
    exe.proxima_consulta = agenda.proxima_consulta(hoje, service.intervalo_dias_uteis())
    if (hoje - exe.data_ajuizamento).days > service.teto_dias():
        exe.estado = ESTADO_SEM_EMBARGOS
        exe.proxima_consulta = None
        service.registrar_evento(
            db, SECAO_TRIBUNAL,
            f"Teto de {service.teto_dias()} dias sem embargos encontrados — monitoramento encerrado.",
            nivel=EVT_AVISO, execucao_id=exe.id,
        )


def _embargos_ja_no_l1(db: Session, exe: EmbExecucao, fortes: list[EmbCandidato], l1_client) -> bool:
    """Candidato forte que já está cadastrado no L1 — como incidente/apenso da
    pasta ou em outra pasta — encerra o card sem aviso.

    Pedido do operador (11/09/2026): "a partir do momento que identifique um
    processo como embargo, já procurar ele no L1". Caso real: os embargos
    7011603-39.2026.8.22.0005 já estavam no L1 como "Proc - 0068696/001" e o
    card foi pro board como encontrado. A busca é por CNJ (/Lawsuits com
    fallback em /Litigations, onde vivem os incidentes)."""
    for cand in fortes:
        try:
            achado = l1_client.search_lawsuit_by_cnj(cand.cnj)
        except Exception as exc:  # noqa: BLE001 — L1 fora não esconde o achado
            logger.warning("Embargos: busca do CNJ %s no L1 falhou: %s", cand.cnj, exc)
            continue
        if not achado or not achado.get("id"):
            continue
        detalhe: dict[str, Any] = {}
        try:
            detalhe = l1_client.get_lawsuit_by_id(
                int(achado["id"]), params={"$select": "id,folder,title,responsibleOfficeId"},
            ) or {}
        except Exception:  # noqa: BLE001 — a pasta é informativa
            detalhe = {}
        pasta = detalhe.get("folder")
        cand.l1_litigation_id = int(achado["id"])
        cand.l1_folder = pasta
        exe.estado = ESTADO_JA_CADASTRADO
        exe.proxima_consulta = None
        exe.encontrado_em = service.agora()
        exe.incidente_id = int(achado["id"])
        exe.incidente_folder = pasta
        exe.incidente_cnj = cand.cnj
        exe.incidente_office_id = detalhe.get("responsibleOfficeId") or achado.get("responsibleOfficeId")
        service.registrar_evento(
            db, SECAO_L1,
            f"Embargos {cand.cnj} identificados e já cadastrados no Legal One"
            + (f" ({pasta})" if pasta else f" (id {achado['id']})")
            + " — monitoramento encerrado, sem aviso.",
            execucao_id=exe.id, dados={"candidato": cand.cnj, "l1": {**achado, **detalhe}},
        )
        return True
    return False


def processar(
    db: Session,
    exe: EmbExecucao,
    hoje: date,
    *,
    datajud=datajud_embargos,
    djen=djen_embargos,
    l1_client=None,
    djen_client=None,
    enviar_email=None,
) -> str:
    if exe.estado == ESTADO_AGUARDANDO_JANELA and exe.proxima_consulta and exe.proxima_consulta <= hoje:
        exe.estado = ESTADO_MONITORANDO
        service.registrar_evento(db, SECAO_TRIBUNAL, "Janela cumprida — começa a consulta ao tribunal.",
                                 execucao_id=exe.id)
    if exe.estado != ESTADO_MONITORANDO:
        return "fora_do_monitoramento"

    if l1_client is not None:
        try:
            if verificar_incidente_l1(db, exe, l1_client):
                return "ja_cadastrado"
        except Exception as exc:  # noqa: BLE001 — L1 fora não impede a consulta
            logger.warning("Embargos: conferência de incidente no L1 falhou (%s): %s", exe.pasta, exc)

    try:
        capa = datajud.consultar_capa(exe.cnj)
    except Exception as exc:  # noqa: BLE001
        exe.ultimo_erro = f"DataJud: {exc}"[:500]
        exe.proxima_consulta = agenda.proxima_consulta(hoje, 1)
        service.registrar_evento(db, SECAO_TRIBUNAL, f"Falha ao consultar o DataJud: {exc} — tenta no próximo dia útil.",
                                 nivel=EVT_ERRO, execucao_id=exe.id)
        return "erro"

    exe.ultima_consulta_em = service.agora()
    exe.consultas_feitas = (exe.consultas_feitas or 0) + 1
    if capa is None:
        exe.ultimo_erro = "Execução ainda não aparece no DataJud (atraso de indexação do tribunal)."
        _agendar_proxima(db, exe, hoje)
        service.registrar_evento(db, SECAO_TRIBUNAL, exe.ultimo_erro, nivel=EVT_AVISO, execucao_id=exe.id)
        return "sem_capa"

    exe.tribunal_alias = capa.alias
    exe.orgao_codigo = capa.orgao_codigo
    exe.orgao_nome = capa.orgao_nome
    exe.datajud_ajuizamento_raw = exe.datajud_ajuizamento_raw or capa.data_ajuizamento_raw
    try:
        achados = datajud.buscar_candidatos(capa, cnj_execucao=exe.cnj, desde_raw=exe.datajud_ajuizamento_raw)
    except Exception as exc:  # noqa: BLE001
        exe.ultimo_erro = f"DataJud (busca na vara): {exc}"[:500]
        exe.proxima_consulta = agenda.proxima_consulta(hoje, 1)
        service.registrar_evento(db, SECAO_TRIBUNAL, f"Falha na busca de embargos na vara: {exc}",
                                 nivel=EVT_ERRO, execucao_id=exe.id)
        return "erro"

    conhecidos = {c.cnj_digitos: c for c in exe.candidatos}
    novos = []
    for a in achados:
        if a.cnj_digitos in conhecidos:
            continue
        cand = EmbCandidato(
            cnj=formatar_cnj(a.cnj_digitos) or a.cnj_digitos,
            cnj_digitos=a.cnj_digitos,
            data_ajuizamento=a.data_ajuizamento,
            classe_nome=a.classe_nome,
            orgao_nome=a.orgao_nome,
            distribuicao_dependencia=bool(a.distribuicao_dependencia),
            peticao_mesmo_dia=False,
            nivel=NIVEL_FRACO,
            # Explícito: o server_default só vale no INSERT, e o filtro de
            # pendentes logo abaixo roda antes do flush.
            decisao=DECISAO_PENDENTE,
        )
        exe.candidatos.append(cand)
        conhecidos[a.cnj_digitos] = cand
        novos.append(cand)

    pendentes = [
        c for c in exe.candidatos
        if c.decisao == DECISAO_PENDENTE and c.nivel not in (NIVEL_CONFIRMADO_DJEN, NIVEL_DESCARTADO)
    ]
    # Vara movimentada estoura o teto de DJEN: confere primeiro quem foi
    # distribuído por dependência e, depois, os mais recentes.
    pendentes.sort(key=lambda c: (
        not c.distribuicao_dependencia,
        -(c.data_ajuizamento.timestamp() if c.data_ajuizamento else 0),
    ))
    djen_fora: Optional[str] = None
    chamadas = 0
    referencias: Optional[list[str]] = None
    for cand in pendentes:
        dia = cand.data_ajuizamento.date() if cand.data_ajuizamento else None
        cand.peticao_mesmo_dia = datajud.peticao_na_execucao_no_dia(capa, dia)
        if djen_fora is None and chamadas < MAX_DJEN_POR_CONSULTA:
            if referencias is None:
                referencias = _referencias(db, exe, djen, djen_client)
            r = djen.confirmar_candidato(cand.cnj, referencias, djen_client, cliente=exe.cliente or "BB")
            chamadas += 1
            if r.status == djen_embargos.DJEN_INDISPONIVEL:
                djen_fora = r.erro or "DJEN indisponível"
            else:
                cand.djen_status = r.status
                cand.djen_embargantes = r.embargantes or None
                cand.djen_embargados = r.embargados or None
                cand.djen_nomes_casados = r.nomes_casados or None
                cand.djen_trecho = r.trecho
                cand.djen_data = r.data_comunicacao
                cand.djen_consultado_em = service.agora()
        cand.nivel = classificar_nivel(
            cand.djen_status, cand.distribuicao_dependencia, cand.peticao_mesmo_dia,
            djen_disponivel=djen_fora is None,
        )

    exe.ultimo_erro = f"DJEN indisponível: {djen_fora}"[:500] if djen_fora else None
    fortes = [c for c in exe.candidatos if c.decisao == DECISAO_PENDENTE and c.nivel in NIVEIS_FORTES]
    descartados = sum(1 for c in exe.candidatos if c.nivel == NIVEL_DESCARTADO)
    resumo = (
        f"Consulta {exe.consultas_feitas} em {capa.orgao_nome or capa.alias}: "
        f"{len(achados)} embargo(s) na vara desde a execução, {len(novos)} novo(s), "
        f"{len(fortes)} forte(s), {descartados} descartado(s) pelo DJEN."
        + (" DJEN indisponível — candidatos sem confirmação de nome." if djen_fora else "")
    )

    if fortes and l1_client is not None and _embargos_ja_no_l1(db, exe, fortes, l1_client):
        service.registrar_evento(db, SECAO_TRIBUNAL, resumo, nivel=EVT_INFO, execucao_id=exe.id,
                                 dados={"candidatos": len(exe.candidatos)})
        return "ja_cadastrado"

    if fortes:
        exe.estado = ESTADO_ENCONTRADO
        exe.encontrado_em = service.agora()
        exe.proxima_consulta = None
        service.registrar_evento(
            db, SECAO_TRIBUNAL, resumo + " Monitoramento pausado para verificação.",
            nivel=EVT_AVISO, execucao_id=exe.id,
            dados={"fortes": [c.cnj for c in fortes]},
        )
        db.flush()
        try:
            from app.services.embargos_execucao import aviso

            aviso.avisar_encontrado(db, exe, fortes, enviar=enviar_email)
        except Exception:  # noqa: BLE001 — aviso nunca desfaz o achado
            logger.exception("Embargos: falha ao avisar o achado da pasta %s.", exe.pasta)
        return "encontrado"

    service.registrar_evento(db, SECAO_TRIBUNAL, resumo, nivel=EVT_INFO, execucao_id=exe.id,
                             dados={"candidatos": len(exe.candidatos)})
    _agendar_proxima(db, exe, hoje)
    return "sem_embargos" if exe.estado == ESTADO_SEM_EMBARGOS else "monitorando"


def resolver_sem_cnj(db: Session, l1_client, hoje: date, limite: int = 50) -> int:
    """Pastas que entraram sem CNJ: busca o número no L1 uma vez por dia."""
    if l1_client is None:
        return 0
    inicio_do_dia = service.agora() - timedelta(hours=20)
    cards = (
        db.query(EmbExecucao)
        .filter(
            EmbExecucao.estado == ESTADO_SEM_CNJ,
            ~EmbExecucao.pasta.like("CNJ %"),
            (EmbExecucao.ultima_consulta_em.is_(None)) | (EmbExecucao.ultima_consulta_em < inicio_do_dia),
        )
        .limit(limite)
        .all()
    )
    if not cards:
        return 0
    achados = l1_client.search_lawsuits_by_folder_numbers([c.pasta for c in cards]) or {}
    resolvidos = 0
    for exe in cards:
        exe.ultima_consulta_em = service.agora()
        item = achados.get(l1_client._folder_lookup_key(exe.pasta)) or {}
        cnj = formatar_cnj(item.get("identifierNumber"))
        if item.get("id"):
            exe.lawsuit_id = item.get("id")
        if not cnj:
            continue
        exe.cnj = cnj
        exe.cnj_digitos = "".join(ch for ch in cnj if ch.isdigit())
        exe.estado = ESTADO_AGUARDANDO_JANELA
        exe.inicio_monitoramento = agenda.inicio_monitoramento(exe.data_ajuizamento, exe.dias_uteis_janela)
        exe.proxima_consulta = agenda.primeira_consulta(exe.data_ajuizamento, exe.dias_uteis_janela, hoje)
        service.registrar_evento(db, SECAO_L1, f"CNJ encontrado no Legal One ({cnj}) — entra na agenda.",
                                 execucao_id=exe.id)
        resolvidos += 1
    db.commit()
    return resolvidos


def fila(db: Session, hoje: date, limite: int) -> list[int]:
    return [
        r[0]
        for r in db.query(EmbExecucao.id)
        .filter(
            EmbExecucao.estado.in_((ESTADO_AGUARDANDO_JANELA, ESTADO_MONITORANDO)),
            EmbExecucao.proxima_consulta.isnot(None),
            EmbExecucao.proxima_consulta <= hoje,
        )
        .order_by(EmbExecucao.proxima_consulta.asc(), EmbExecucao.id.asc())
        .limit(limite)
        .all()
    ]


def _l1_client():
    try:
        from app.services.legal_one_client import LegalOneApiClient

        return LegalOneApiClient()
    except Exception:  # noqa: BLE001
        logger.warning("Embargos: cliente do L1 indisponível — segue sem conferir incidentes.", exc_info=True)
        return None


def processar_um(db: Session, execucao_id: int, hoje: Optional[date] = None, **kwargs: Any) -> str:
    hoje = hoje or service.hoje_brt()
    exe = db.get(EmbExecucao, execucao_id)
    if exe is None:
        return "inexistente"
    kwargs.setdefault("l1_client", _l1_client())
    try:
        resultado = processar(db, exe, hoje, **kwargs)
        db.commit()
        return resultado
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("Embargos: falha ao processar a pasta %s.", execucao_id)
        exe = db.get(EmbExecucao, execucao_id)
        if exe is not None:
            exe.ultimo_erro = str(exc)[:500]
            exe.proxima_consulta = agenda.proxima_consulta(hoje, 1)
            db.commit()
        return "erro"


def tick(db: Session, *, hoje: Optional[date] = None, limite: int = 40, **kwargs: Any) -> dict[str, Any]:
    hoje = hoje or service.hoje_brt()
    l1 = kwargs.pop("l1_client", None) or _l1_client()
    contagem: dict[str, int] = {}
    try:
        contagem["cnj_resolvidos"] = resolver_sem_cnj(db, l1, hoje)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("Embargos: falha ao resolver pastas sem CNJ.")
    for eid in fila(db, hoje, limite):
        r = processar_um(db, eid, hoje, l1_client=l1, **kwargs)
        contagem[r] = contagem.get(r, 0) + 1
    if len(contagem) > 1 or contagem.get("cnj_resolvidos"):
        logger.info("Embargos monitor: %s", contagem)
    return contagem
