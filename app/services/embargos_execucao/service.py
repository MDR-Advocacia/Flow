"""Fluxo Embargos à Execução — cards, agenda, decisões do operador e listagem.

Um card por pasta. Entra pelo relatório do L1 (ou pela planilha do legado),
ganha a data do ajuizamento (conclusão efetiva da tarefa "Protocolar Inicial
- BB Autor") e a agenda em dias úteis. O monitor (monitor.py) consome os cards
com consulta vencida; as partes do BB (partes_runner.py) consomem a fila de
partes. Aqui fica o que é regra do card e o que o operador pode mudar.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.models.embargos_execucao import (
    DECISAO_CONFIRMADO,
    DECISAO_PENDENTE,
    DECISAO_RECUSADO,
    ESTADO_AGUARDANDO_JANELA,
    ESTADO_CONFIRMADO,
    ESTADO_ENCERRADO,
    ESTADO_ENCONTRADO,
    ESTADO_MONITORANDO,
    ESTADO_SEM_CNJ,
    ESTADOS,
    EVT_AVISO,
    EVT_ERRO,
    EVT_INFO,
    NIVEIS_FORTES,
    NIVEL_DESCARTADO,
    ORIGEM_MANUAL,
    PARTES_ERRO,
    PARTES_NAO_APLICA,
    PARTES_OK,
    PARTES_PENDENTE,
    PARTES_SEM_NPJ,
    SECAO_ENTRADA,
    SECAO_OPERADOR,
    SECAO_PARTES,
    EmbCandidato,
    EmbEvento,
    EmbExecucao,
    EmbParte,
)
from app.services.app_settings import get_setting, set_setting
from app.services.embargos_execucao import agenda
from app.services.embargos_execucao.normaliza import (
    cnj_digitos,
    formatar_cnj,
    nome_normalizado,
    npj_normalizado,
    pasta_normalizada,
)

logger = logging.getLogger(__name__)

# Aba do time Controladoria no Minha Equipe (a chave ficou "bb-cadastro").
TEAM_KEY = "bb-cadastro"

# ── Parâmetros ajustáveis pelo operador (app_settings) ───────────────
P_JANELA = "embargos_execucao_janela_dias_uteis"
P_INTERVALO = "embargos_execucao_intervalo_dias_uteis"
P_TETO = "embargos_execucao_teto_dias"
P_EMAILS = "embargos_execucao_aviso_emails"
P_REL_TITULO = "embargos_execucao_relatorio_titulo"
P_REL_MODELO = "embargos_execucao_relatorio_modelo_id"
# Corte do relatório (ISO date): só tarefa com conclusão efetiva a partir daqui
# entra sozinha. Decisão do operador (11/09/2026): o relatório traz todo o
# histórico do subtipo desde 2025 e esses casos antigos NÃO entram no painel
# automaticamente — legado, se entrar, vem por planilha separada.
P_CORTE = "embargos_execucao_corte_relatorio"

_DEFAULTS = {
    P_JANELA: "15",
    P_INTERVALO: "5",
    P_TETO: "365",
    P_EMAILS: "",
    P_REL_TITULO: "ROBÔ - EMBARGOS À EXECUÇÃO",
    P_REL_MODELO: "799",
    P_CORTE: "",
}

PARTES_MAX_TENTATIVAS = 5

try:
    from zoneinfo import ZoneInfo

    _BRT = ZoneInfo("America/Sao_Paulo")
except Exception:  # pragma: no cover
    _BRT = None


def agora() -> datetime:
    return datetime.now(timezone.utc)


def hoje_brt() -> date:
    return datetime.now(_BRT).date() if _BRT else date.today()


def _int_param(chave: str, minimo: int, maximo: int) -> int:
    bruto = get_setting(chave, _DEFAULTS[chave]) or _DEFAULTS[chave]
    try:
        valor = int(str(bruto).strip())
    except ValueError:
        valor = int(_DEFAULTS[chave])
    return max(minimo, min(maximo, valor))


def janela_padrao() -> int:
    valor = _int_param(P_JANELA, 1, 120)
    return valor if valor in agenda.JANELAS_PERMITIDAS else int(_DEFAULTS[P_JANELA])


def intervalo_dias_uteis() -> int:
    return _int_param(P_INTERVALO, 1, 30)


def teto_dias() -> int:
    return _int_param(P_TETO, 30, 1825)


def emails_aviso() -> list[str]:
    bruto = get_setting(P_EMAILS, _DEFAULTS[P_EMAILS]) or ""
    return [e.strip() for e in bruto.replace(";", ",").split(",") if "@" in e]


def relatorio_titulo() -> str:
    return (get_setting(P_REL_TITULO, _DEFAULTS[P_REL_TITULO]) or _DEFAULTS[P_REL_TITULO]).strip()


def relatorio_modelo_id() -> int:
    return _int_param(P_REL_MODELO, 1, 10_000_000)


def corte_relatorio(*, definir_se_vazio: bool = False, hoje: Optional[date] = None) -> Optional[date]:
    """Data de corte do relatório. Vazia + `definir_se_vazio` grava HOJE: a
    primeira passagem do robô marca o começo e nada anterior entra."""
    bruto = (get_setting(P_CORTE, "") or "").strip()
    if bruto:
        try:
            return date.fromisoformat(bruto[:10])
        except ValueError:
            logger.warning("Embargos: corte do relatório inválido em app_settings: %r", bruto)
    if not definir_se_vazio:
        return None
    corte = hoje or hoje_brt()
    set_setting(P_CORTE, corte.isoformat())
    logger.info("Embargos: corte do relatório fixado em %s (primeira passagem).", corte)
    return corte


def parametros() -> dict[str, Any]:
    corte = corte_relatorio()
    return {
        "corte_relatorio": corte.isoformat() if corte else None,
        "janela_dias_uteis": janela_padrao(),
        "janelas_permitidas": list(agenda.JANELAS_PERMITIDAS),
        "intervalo_dias_uteis": intervalo_dias_uteis(),
        "teto_dias": teto_dias(),
        "aviso_emails": emails_aviso(),
        "relatorio_titulo": relatorio_titulo(),
        "relatorio_modelo_id": relatorio_modelo_id(),
    }


def salvar_parametros(dados: dict[str, Any]) -> dict[str, Any]:
    if dados.get("corte_relatorio"):
        try:
            corte = date.fromisoformat(str(dados["corte_relatorio"])[:10])
        except ValueError as exc:
            raise ValueError("Data de corte inválida (use AAAA-MM-DD).") from exc
        set_setting(P_CORTE, corte.isoformat())
    if dados.get("janela_dias_uteis") is not None:
        v = int(dados["janela_dias_uteis"])
        if v not in agenda.JANELAS_PERMITIDAS:
            raise ValueError(f"Janela deve ser {', '.join(map(str, agenda.JANELAS_PERMITIDAS))} dias úteis.")
        set_setting(P_JANELA, str(v))
    if dados.get("intervalo_dias_uteis") is not None:
        v = int(dados["intervalo_dias_uteis"])
        if not 1 <= v <= 30:
            raise ValueError("Intervalo entre consultas deve ficar entre 1 e 30 dias úteis.")
        set_setting(P_INTERVALO, str(v))
    if dados.get("teto_dias") is not None:
        v = int(dados["teto_dias"])
        if not 30 <= v <= 1825:
            raise ValueError("Teto do monitoramento deve ficar entre 30 e 1825 dias.")
        set_setting(P_TETO, str(v))
    if dados.get("aviso_emails") is not None:
        lista = dados["aviso_emails"]
        if isinstance(lista, str):
            lista = lista.replace(";", ",").split(",")
        limpos = [e.strip() for e in lista if e and e.strip()]
        invalidos = [e for e in limpos if "@" not in e]
        if invalidos:
            raise ValueError(f"E-mail inválido: {', '.join(invalidos)}")
        set_setting(P_EMAILS, ",".join(limpos))
    return parametros()


# ── Trilha ───────────────────────────────────────────────────────────
def registrar_evento(
    db: Session,
    secao: str,
    mensagem: str,
    *,
    nivel: str = EVT_INFO,
    execucao_id: Optional[int] = None,
    dados: Optional[dict[str, Any]] = None,
    user_id: Optional[int] = None,
) -> EmbEvento:
    ev = EmbEvento(
        execucao_id=execucao_id, secao=secao, nivel=nivel,
        mensagem=mensagem[:4000], dados=dados, user_id=user_id,
    )
    db.add(ev)
    return ev


# ── Entrada (relatório, planilha, manual) ────────────────────────────
def cliente_do_escritorio(escritorio: Optional[str]) -> Optional[str]:
    n = nome_normalizado(escritorio)
    if not n:
        return None
    if "BANCO DO BRASIL" in n:
        return "BB"
    if "BANESE" in n:
        return "BANESE"
    if "ATIVOS" in n:
        return "ATIVOS"
    return "OUTRO"


def _partes_status_inicial(cliente: Optional[str], npj: Optional[str]) -> str:
    # Sem escritório (planilha/manual) presume BB: o fluxo é do BB Autor.
    if cliente in ("BANESE", "ATIVOS", "OUTRO"):
        return PARTES_NAO_APLICA
    return PARTES_PENDENTE if npj else PARTES_SEM_NPJ


def _agendar(exe: EmbExecucao, hoje: date) -> None:
    exe.inicio_monitoramento = agenda.inicio_monitoramento(exe.data_ajuizamento, exe.dias_uteis_janela)
    exe.proxima_consulta = agenda.primeira_consulta(exe.data_ajuizamento, exe.dias_uteis_janela, hoje)


def _linha_limpa(linha: dict[str, Any]) -> dict[str, Any]:
    cnj = formatar_cnj(linha.get("cnj"))
    return {
        "pasta": pasta_normalizada(linha.get("pasta")),
        "cnj": cnj,
        "cnj_digitos": cnj_digitos(cnj),
        "npj": npj_normalizado(linha.get("npj")),
        "data_ajuizamento": linha.get("data_ajuizamento"),
        "l1_task_id": linha.get("l1_task_id"),
        "escritorio": (linha.get("escritorio") or None),
        "responsavel_nome": (linha.get("responsavel_nome") or None),
        "executante_nome": (linha.get("executante_nome") or None),
        "uf": (linha.get("uf") or None),
    }


def upsert_execucoes(
    db: Session,
    linhas: Iterable[dict[str, Any]],
    *,
    origem: str,
    hoje: Optional[date] = None,
    user_id: Optional[int] = None,
) -> dict[str, Any]:
    """Cria/atualiza os cards. Uma transação, commit no fim.

    Agrupa por pasta: a mesma pasta pode ter várias tarefas de protocolo
    (no relatório de 11/09, 'Proc - 0029647' tinha 3 em 13 minutos). A data do
    ajuizamento é a PRIMEIRA conclusão — a busca de embargos parte dela."""
    hoje = hoje or hoje_brt()
    grupos: dict[str, list[dict[str, Any]]] = {}
    ignoradas = 0
    for bruta in linhas:
        linha = _linha_limpa(bruta)
        chave = linha["pasta"] or (f"CNJ {linha['cnj']}" if linha["cnj"] else None)
        if not chave or not linha["data_ajuizamento"]:
            ignoradas += 1
            continue
        grupos.setdefault(chave, []).append(linha)

    novas = atualizadas = sem_cnj = 0
    janela = janela_padrao()
    for chave, grupo in grupos.items():
        grupo.sort(key=lambda l: l["data_ajuizamento"])
        rep = grupo[0]
        # Campos vazios na 1ª linha vêm das repetidas (ex.: NPJ só numa delas).
        for outra in grupo[1:]:
            for campo, valor in outra.items():
                if valor and not rep.get(campo):
                    rep[campo] = valor
        ids = sorted({int(l["l1_task_id"]) for l in grupo if l.get("l1_task_id")})

        exe = db.query(EmbExecucao).filter(EmbExecucao.pasta == chave).first()
        if exe is None and rep["pasta"] and rep["cnj_digitos"]:
            # Card criado antes só com CNJ: a pasta chegou, adota.
            exe = (
                db.query(EmbExecucao)
                .filter(EmbExecucao.cnj_digitos == rep["cnj_digitos"], EmbExecucao.pasta.like("CNJ %"))
                .first()
            )
            if exe is not None:
                exe.pasta = chave

        if exe is None:
            cliente = cliente_do_escritorio(rep["escritorio"])
            exe = EmbExecucao(
                pasta=chave,
                l1_task_id=ids[-1] if ids else None,
                l1_task_ids=ids or None,
                cnj=rep["cnj"],
                cnj_digitos=rep["cnj_digitos"],
                npj=rep["npj"],
                escritorio=rep["escritorio"],
                cliente=cliente,
                responsavel_nome=rep["responsavel_nome"],
                executante_nome=rep["executante_nome"],
                uf=rep["uf"],
                origem=origem,
                data_ajuizamento=rep["data_ajuizamento"],
                dias_uteis_janela=janela,
                consultas_feitas=0,
                partes_tentativas=0,
                partes_status=_partes_status_inicial(cliente, rep["npj"]),
                estado=ESTADO_AGUARDANDO_JANELA if rep["cnj_digitos"] else ESTADO_SEM_CNJ,
            )
            if exe.estado == ESTADO_SEM_CNJ:
                sem_cnj += 1
            else:
                _agendar(exe, hoje)
            db.add(exe)
            db.flush()
            registrar_evento(
                db, SECAO_ENTRADA,
                f"Entrou no fluxo ({origem.lower()}): ajuizamento em "
                f"{exe.data_ajuizamento:%d/%m/%Y}, janela de {exe.dias_uteis_janela} dias úteis"
                + (f", 1ª consulta em {exe.proxima_consulta:%d/%m/%Y}." if exe.proxima_consulta else " — sem CNJ, aguardando o número."),
                execucao_id=exe.id, user_id=user_id,
                dados={"tarefas_l1": ids},
            )
            novas += 1
            continue

        atualizadas += 1
        mudou_agenda = False
        for campo in ("cnj", "cnj_digitos", "npj", "escritorio", "responsavel_nome", "executante_nome", "uf"):
            if rep.get(campo) and not getattr(exe, campo):
                setattr(exe, campo, rep[campo])
        if rep.get("escritorio") and not exe.cliente:
            exe.cliente = cliente_do_escritorio(exe.escritorio)
        if ids:
            todos = sorted(set(exe.l1_task_ids or []) | set(ids))
            exe.l1_task_ids = todos
            exe.l1_task_id = todos[-1]
        if rep["data_ajuizamento"] < exe.data_ajuizamento and exe.estado in (ESTADO_AGUARDANDO_JANELA, ESTADO_SEM_CNJ):
            exe.data_ajuizamento = rep["data_ajuizamento"]
            mudou_agenda = True
        if exe.estado == ESTADO_SEM_CNJ and exe.cnj_digitos:
            exe.estado = ESTADO_AGUARDANDO_JANELA
            mudou_agenda = True
            registrar_evento(db, SECAO_ENTRADA, f"CNJ recebido ({exe.cnj}) — entra na agenda.", execucao_id=exe.id)
        if mudou_agenda and exe.estado == ESTADO_AGUARDANDO_JANELA:
            _agendar(exe, hoje)
        if exe.partes_status == PARTES_SEM_NPJ and exe.npj:
            exe.partes_status = PARTES_PENDENTE

    db.commit()
    return {
        "linhas": sum(len(g) for g in grupos.values()) + ignoradas,
        "pastas": len(grupos),
        "novas": novas,
        "atualizadas": atualizadas,
        "sem_cnj": sem_cnj,
        "ignoradas": ignoradas,
    }


# ── Partes do BB ─────────────────────────────────────────────────────
def fila_partes(db: Session, limite: int = 25, execucao_id: Optional[int] = None) -> list[EmbExecucao]:
    q = db.query(EmbExecucao)
    if execucao_id:
        q = q.filter(EmbExecucao.id == execucao_id)
    return (
        q
        .filter(
            EmbExecucao.npj.isnot(None),
            EmbExecucao.estado != ESTADO_ENCERRADO,
            or_(
                EmbExecucao.partes_status == PARTES_PENDENTE,
                (EmbExecucao.partes_status == PARTES_ERRO)
                & (EmbExecucao.partes_tentativas < PARTES_MAX_TENTATIVAS),
            ),
        )
        .order_by(EmbExecucao.partes_em.asc().nullsfirst(), EmbExecucao.id.asc())
        .limit(limite)
        .all()
    )


def aplicar_partes(db: Session, exe: EmbExecucao, resultado: Any) -> None:
    """Grava o resultado da coleta de UM NPJ (lista de partes ou exceção)."""
    exe.partes_em = agora()
    if isinstance(resultado, Exception):
        exe.partes_tentativas = (exe.partes_tentativas or 0) + 1
        exe.partes_erro = str(resultado)[:500]
        exe.partes_status = PARTES_ERRO
        esgotou = exe.partes_tentativas >= PARTES_MAX_TENTATIVAS
        registrar_evento(
            db, SECAO_PARTES,
            f"Falha ao ler as partes no portal do BB (tentativa {exe.partes_tentativas}): {exe.partes_erro}"
            + (" — tentativas esgotadas; use 'Coletar partes de novo'." if esgotou else " — volta na próxima passagem."),
            nivel=EVT_ERRO if esgotou else EVT_AVISO, execucao_id=exe.id,
        )
        return

    for parte in [p for p in exe.partes if p.origem == "BB"]:
        exe.partes.remove(parte)
    demandadas = 0
    for linha in resultado:
        if not linha.get("nome"):
            continue
        exe.partes.append(EmbParte(
            origem="BB",
            polo=linha.get("polo"),
            nome=linha["nome"],
            cpf_cnpj=linha.get("cpf_cnpj"),
            tipo_pessoa=linha.get("tipo_pessoa"),
            relacao_bb=linha.get("relacao_bb"),
            demandada=bool(linha.get("demandada")),
            raw=linha.get("raw"),
        ))
        demandadas += 1 if linha.get("demandada") else 0
    exe.partes_status = PARTES_OK
    exe.partes_erro = None
    registrar_evento(
        db, SECAO_PARTES,
        f"Partes lidas no portal do BB: {len(resultado)} envolvido(s), {demandadas} demandado(s).",
        nivel=EVT_INFO if demandadas else EVT_AVISO, execucao_id=exe.id,
    )


# ── Ações do operador ────────────────────────────────────────────────
def _card(db: Session, execucao_id: int) -> EmbExecucao:
    exe = db.get(EmbExecucao, execucao_id)
    if exe is None:
        raise LookupError("Execução não encontrada.")
    return exe


def ajustar(
    db: Session,
    execucao_id: int,
    *,
    dias_uteis_janela: Optional[int] = None,
    anotacao: Optional[str] = None,
    user_id: Optional[int] = None,
    hoje: Optional[date] = None,
) -> EmbExecucao:
    hoje = hoje or hoje_brt()
    exe = _card(db, execucao_id)
    if dias_uteis_janela is not None and int(dias_uteis_janela) != exe.dias_uteis_janela:
        if int(dias_uteis_janela) not in agenda.JANELAS_PERMITIDAS:
            raise ValueError("Janela deve ser 15, 20 ou 25 dias úteis.")
        anterior = exe.dias_uteis_janela
        exe.dias_uteis_janela = int(dias_uteis_janela)
        exe.inicio_monitoramento = agenda.inicio_monitoramento(exe.data_ajuizamento, exe.dias_uteis_janela)
        if exe.estado == ESTADO_AGUARDANDO_JANELA:
            exe.proxima_consulta = agenda.primeira_consulta(exe.data_ajuizamento, exe.dias_uteis_janela, hoje)
        registrar_evento(
            db, SECAO_OPERADOR,
            f"Janela alterada de {anterior} para {exe.dias_uteis_janela} dias úteis"
            + (f" — 1ª consulta em {exe.proxima_consulta:%d/%m/%Y}." if exe.estado == ESTADO_AGUARDANDO_JANELA and exe.proxima_consulta else "."),
            execucao_id=exe.id, user_id=user_id,
        )
    if anotacao is not None:
        exe.anotacao = anotacao.strip() or None
    db.commit()
    return exe


def consultar_agora(db: Session, execucao_id: int, *, user_id: Optional[int] = None,
                    hoje: Optional[date] = None) -> EmbExecucao:
    hoje = hoje or hoje_brt()
    exe = _card(db, execucao_id)
    if exe.estado not in (ESTADO_AGUARDANDO_JANELA, ESTADO_MONITORANDO):
        raise ValueError("Só dá pra antecipar a consulta de card aguardando janela ou monitorando.")
    exe.estado = ESTADO_MONITORANDO
    exe.proxima_consulta = hoje
    registrar_evento(db, SECAO_OPERADOR, "Consulta ao tribunal antecipada para hoje.",
                     execucao_id=exe.id, user_id=user_id)
    db.commit()
    return exe


def recoletar_partes(db: Session, execucao_id: int, *, user_id: Optional[int] = None) -> EmbExecucao:
    exe = _card(db, execucao_id)
    if not exe.npj:
        raise ValueError("A pasta não tem NPJ — não há o que consultar no portal do BB.")
    exe.partes_status = PARTES_PENDENTE
    exe.partes_tentativas = 0
    exe.partes_erro = None
    registrar_evento(db, SECAO_OPERADOR, "Coleta das partes no portal do BB pedida de novo.",
                     execucao_id=exe.id, user_id=user_id)
    db.commit()
    return exe


def decidir_candidato(
    db: Session,
    execucao_id: int,
    candidato_id: int,
    decisao: str,
    *,
    user_id: Optional[int] = None,
    hoje: Optional[date] = None,
) -> EmbExecucao:
    hoje = hoje or hoje_brt()
    exe = _card(db, execucao_id)
    cand = db.get(EmbCandidato, candidato_id)
    if cand is None or cand.execucao_id != exe.id:
        raise LookupError("Candidato não encontrado nesta execução.")
    if decisao not in (DECISAO_CONFIRMADO, DECISAO_RECUSADO):
        raise ValueError("Decisão deve ser CONFIRMADO ou RECUSADO.")

    cand.decisao = decisao
    cand.decidido_por_user_id = user_id
    cand.decidido_em = agora()
    if decisao == DECISAO_CONFIRMADO:
        exe.estado = ESTADO_CONFIRMADO
        exe.confirmado_candidato_id = cand.id
        exe.proxima_consulta = None
        exe.decidido_por_user_id = user_id
        exe.decidido_em = agora()
        registrar_evento(db, SECAO_OPERADOR,
                         f"Vínculo confirmado: embargos {cand.cnj} são desta execução. Monitoramento encerrado.",
                         execucao_id=exe.id, user_id=user_id)
    else:
        registrar_evento(db, SECAO_OPERADOR,
                         f"Candidato {cand.cnj} recusado — não volta a ser oferecido para esta execução.",
                         execucao_id=exe.id, user_id=user_id)
        restantes = [
            c for c in exe.candidatos
            if c.id != cand.id and c.decisao == DECISAO_PENDENTE and c.nivel in NIVEIS_FORTES
        ]
        if exe.estado == ESTADO_ENCONTRADO and not restantes:
            exe.estado = ESTADO_MONITORANDO
            exe.encontrado_em = None
            exe.aviso_enviado_em = None
            exe.proxima_consulta = agenda.proxima_consulta(hoje, intervalo_dias_uteis())
            registrar_evento(db, SECAO_OPERADOR,
                             f"Sem outro candidato forte — monitoramento retomado, próxima consulta em "
                             f"{exe.proxima_consulta:%d/%m/%Y}.",
                             execucao_id=exe.id, user_id=user_id)
    db.commit()
    return exe


def encerrar(db: Session, execucao_id: int, *, motivo: Optional[str] = None,
             user_id: Optional[int] = None) -> EmbExecucao:
    exe = _card(db, execucao_id)
    exe.estado = ESTADO_ENCERRADO
    exe.proxima_consulta = None
    registrar_evento(db, SECAO_OPERADOR,
                     "Tirado do fluxo pelo operador" + (f": {motivo.strip()}" if motivo and motivo.strip() else "."),
                     execucao_id=exe.id, user_id=user_id)
    db.commit()
    return exe


def reabrir(db: Session, execucao_id: int, *, user_id: Optional[int] = None,
            hoje: Optional[date] = None) -> EmbExecucao:
    hoje = hoje or hoje_brt()
    exe = _card(db, execucao_id)
    if not exe.cnj_digitos:
        exe.estado = ESTADO_SEM_CNJ
        exe.proxima_consulta = None
    else:
        exe.estado = ESTADO_AGUARDANDO_JANELA
        exe.encontrado_em = None
        exe.aviso_enviado_em = None
        _agendar(exe, hoje)
    registrar_evento(db, SECAO_OPERADOR, "Card reaberto e devolvido à agenda.",
                     execucao_id=exe.id, user_id=user_id)
    db.commit()
    return exe


def adicionar_manual(
    db: Session,
    *,
    pasta: Optional[str],
    cnj: Optional[str],
    npj: Optional[str],
    data_ajuizamento: date,
    user_id: Optional[int] = None,
    hoje: Optional[date] = None,
) -> dict[str, Any]:
    if not pasta and not cnj_digitos(cnj):
        raise ValueError("Informe a pasta ou um CNJ válido.")
    return upsert_execucoes(
        db,
        [{"pasta": pasta, "cnj": cnj, "npj": npj, "data_ajuizamento": data_ajuizamento}],
        origem=ORIGEM_MANUAL, hoje=hoje, user_id=user_id,
    )


# ── Leitura ──────────────────────────────────────────────────────────
def _iso(valor) -> Optional[str]:
    return valor.isoformat() if valor else None


def _dto(exe: EmbExecucao) -> dict[str, Any]:
    return {
        "id": exe.id,
        "pasta": exe.pasta,
        "cnj": exe.cnj,
        "npj": exe.npj,
        "cliente": exe.cliente,
        "escritorio": exe.escritorio,
        "responsavel_nome": exe.responsavel_nome,
        "executante_nome": exe.executante_nome,
        "uf": exe.uf,
        "origem": exe.origem,
        "l1_task_id": exe.l1_task_id,
        "data_ajuizamento": _iso(exe.data_ajuizamento),
        "estado": exe.estado,
        "dias_uteis_janela": exe.dias_uteis_janela,
        "inicio_monitoramento": _iso(exe.inicio_monitoramento),
        "proxima_consulta": _iso(exe.proxima_consulta),
        "ultima_consulta_em": _iso(exe.ultima_consulta_em),
        "consultas_feitas": exe.consultas_feitas or 0,
        "ultimo_erro": exe.ultimo_erro,
        "partes_status": exe.partes_status,
        "partes_tentativas": exe.partes_tentativas or 0,
        "partes_erro": exe.partes_erro,
        "partes_em": _iso(exe.partes_em),
        "tribunal": (exe.tribunal_alias or "").replace("api_publica_", "").upper() or None,
        "orgao_nome": exe.orgao_nome,
        "encontrado_em": _iso(exe.encontrado_em),
        "aviso_enviado_em": _iso(exe.aviso_enviado_em),
        "incidente_folder": exe.incidente_folder,
        "incidente_cnj": exe.incidente_cnj,
        "incidente_id": exe.incidente_id,
        "confirmado_candidato_id": exe.confirmado_candidato_id,
        "anotacao": exe.anotacao,
        "criado_em": _iso(exe.criado_em),
    }


_ORDENAVEIS = {
    "proxima_consulta": EmbExecucao.proxima_consulta,
    "data_ajuizamento": EmbExecucao.data_ajuizamento,
    "pasta": EmbExecucao.pasta,
    "estado": EmbExecucao.estado,
    "consultas_feitas": EmbExecucao.consultas_feitas,
    "encontrado_em": EmbExecucao.encontrado_em,
    "criado_em": EmbExecucao.criado_em,
}


def listar(
    db: Session,
    *,
    estado: Optional[str] = None,
    cliente: Optional[str] = None,
    partes_status: Optional[str] = None,
    busca: Optional[str] = None,
    ordenar: str = "proxima_consulta",
    direcao: str = "asc",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    q = db.query(EmbExecucao)
    if estado:
        q = q.filter(EmbExecucao.estado == estado)
    if cliente:
        q = q.filter(EmbExecucao.cliente == cliente)
    if partes_status:
        q = q.filter(EmbExecucao.partes_status == partes_status)
    if busca and busca.strip():
        termo = busca.strip()
        like = f"%{termo}%"
        filtros = [
            EmbExecucao.pasta.ilike(like),
            EmbExecucao.cnj.ilike(like),
            EmbExecucao.npj.ilike(like),
            EmbExecucao.responsavel_nome.ilike(like),
        ]
        d = "".join(ch for ch in termo if ch.isdigit())
        if len(d) >= 7:
            filtros.append(EmbExecucao.cnj_digitos.like(f"%{d}%"))
        q = q.filter(or_(*filtros))

    total = q.count()
    col = _ORDENAVEIS.get(ordenar, EmbExecucao.proxima_consulta)
    ordem = col.desc().nullslast() if direcao == "desc" else col.asc().nullslast()
    itens = q.order_by(ordem, EmbExecucao.id.asc()).offset(offset).limit(limit).all()

    ids = [e.id for e in itens]
    contagem: dict[int, tuple[int, int]] = {}
    demandadas: dict[int, int] = {}
    if ids:
        for eid, n, fortes in (
            db.query(
                EmbCandidato.execucao_id,
                # Descartado (embargado de outro credor / embargante de outro processo) não conta.
                func.sum(case((EmbCandidato.nivel != NIVEL_DESCARTADO, 1), else_=0)),
                func.sum(case(
                    ((EmbCandidato.nivel.in_(NIVEIS_FORTES)) & (EmbCandidato.decisao == DECISAO_PENDENTE), 1),
                    else_=0,
                )),
            )
            .filter(EmbCandidato.execucao_id.in_(ids))
            .group_by(EmbCandidato.execucao_id)
        ):
            contagem[eid] = (int(n or 0), int(fortes or 0))
        for eid, n in (
            db.query(EmbParte.execucao_id, func.count(EmbParte.id))
            .filter(EmbParte.execucao_id.in_(ids), EmbParte.demandada.is_(True))
            .group_by(EmbParte.execucao_id)
        ):
            demandadas[eid] = int(n or 0)

    por_estado = {e: 0 for e in ESTADOS}
    for est, n in db.query(EmbExecucao.estado, func.count(EmbExecucao.id)).group_by(EmbExecucao.estado):
        por_estado[est] = int(n)
    partes_erro = (
        db.query(func.count(EmbExecucao.id))
        .filter(EmbExecucao.partes_status == PARTES_ERRO, EmbExecucao.estado != ESTADO_ENCERRADO)
        .scalar()
    ) or 0
    hoje = hoje_brt()
    consultas_hoje = (
        db.query(func.count(EmbExecucao.id))
        .filter(
            EmbExecucao.estado.in_((ESTADO_AGUARDANDO_JANELA, ESTADO_MONITORANDO)),
            EmbExecucao.proxima_consulta <= hoje,
        )
        .scalar()
    ) or 0

    items = []
    for e in itens:
        dto = _dto(e)
        n, fortes = contagem.get(e.id, (0, 0))
        dto["candidatos"] = n
        dto["candidatos_fortes_pendentes"] = fortes
        dto["partes_demandadas"] = demandadas.get(e.id, 0)
        items.append(dto)

    return {
        "total": total,
        "kpis": {
            "por_estado": por_estado,
            "partes_erro": int(partes_erro),
            "consultas_vencidas": int(consultas_hoje),
            "total": sum(por_estado.values()),
        },
        "parametros": parametros(),
        "items": items,
    }


def detalhe(db: Session, execucao_id: int) -> Optional[dict[str, Any]]:
    exe = db.get(EmbExecucao, execucao_id)
    if exe is None:
        return None
    eventos = (
        db.query(EmbEvento)
        .filter(EmbEvento.execucao_id == exe.id)
        .order_by(EmbEvento.criado_em.desc(), EmbEvento.id.desc())
        .limit(200)
        .all()
    )
    from app.services.embargos_execucao import tarefas

    return {
        "execucao": _dto(exe),
        "l1_task_ids": exe.l1_task_ids or [],
        "disparos": tarefas.disparos(db, exe.id),
        "partes": [
            {
                "id": p.id, "origem": p.origem, "polo": p.polo, "nome": p.nome,
                "cpf_cnpj": p.cpf_cnpj, "tipo_pessoa": p.tipo_pessoa,
                "relacao_bb": p.relacao_bb, "demandada": bool(p.demandada),
            }
            for p in exe.partes
        ],
        "candidatos": [
            {
                "id": c.id, "cnj": c.cnj, "data_ajuizamento": _iso(c.data_ajuizamento),
                "classe_nome": c.classe_nome, "orgao_nome": c.orgao_nome,
                "distribuicao_dependencia": bool(c.distribuicao_dependencia),
                "peticao_mesmo_dia": bool(c.peticao_mesmo_dia),
                "nivel": c.nivel, "djen_status": c.djen_status,
                "djen_embargantes": c.djen_embargantes or [],
                "djen_embargados": c.djen_embargados or [],
                "l1_litigation_id": c.l1_litigation_id, "l1_folder": c.l1_folder,
                "djen_nomes_casados": c.djen_nomes_casados or [],
                "djen_trecho": c.djen_trecho, "djen_data": c.djen_data,
                "djen_consultado_em": _iso(c.djen_consultado_em),
                "decisao": c.decisao, "decidido_em": _iso(c.decidido_em),
                "criado_em": _iso(c.criado_em),
            }
            for c in sorted(
                exe.candidatos,
                key=lambda c: (
                    0 if c.decisao == DECISAO_CONFIRMADO else 1,
                    1 if c.nivel == NIVEL_DESCARTADO else 0,
                    0 if c.nivel in NIVEIS_FORTES else 1,
                    c.data_ajuizamento or datetime.max.replace(tzinfo=timezone.utc),
                ),
            )
        ],
        "eventos": [
            {
                "id": ev.id, "secao": ev.secao, "nivel": ev.nivel, "mensagem": ev.mensagem,
                "dados": ev.dados, "user_id": ev.user_id, "criado_em": _iso(ev.criado_em),
            }
            for ev in eventos
        ],
    }
