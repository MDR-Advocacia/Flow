"""Executa a transição das pastas residuais do cenário 1 pra Equipe Mista.

O cenário 1 é: a parte do processo NOVO já tinha ação nossa conduzida por
alguém de fora da equipe especializada. O novo vai pro rodízio da Equipe Mista
e os antigos ficam `transicao_pendente` — historicamente o supervisor trocava
o responsável na mão, no Legal One, e voltava aqui só pra marcar. Este módulo
faz a troca de verdade, a partir do painel.

Método: `POST /processos/processos/ModalChangeInvolvedInBatch` (endpoint WEB do
L1 — a API REST recusa trocar o responsável principal por regra de negócio).
Validado em produção em 28/08/2026 num tombo de 108 pastas, 108/108 conferidas;
o método está documentado em `docs/legalone-trocar-responsavel-pasta.md`.

Três lições daquele tombo, aplicadas aqui:
  1. `Success: true` NÃO é confirmação — confirma-se relendo os participantes;
  2. a fila do L1 é assíncrona: em lote pequeno reflete em segundos, mas a
     releitura precisa de tentativas espaçadas antes de acusar divergência;
  3. recurso/incidente dá 404 nas DUAS entidades REST (/Lawsuits e
     /Litigations) — nesses a confirmação sai pela via web (`details/{id}`).
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.distribuidos_bb import (
    NIVEL_AVISO,
    NIVEL_ERRO,
    NIVEL_SUCESSO,
    SECAO_DISTRIBUICAO,
    BbProcesso,
    BbVinculo,
)
from app.models.legal_one import LegalOneUser
from app.services.distribuidos_bb.log_service import registrar_evento

logger = logging.getLogger("distribuidos_bb.transicao")

# Espera entre as tentativas de releitura (a fila do L1 é assíncrona).
_ESPERAS_CONFIRMACAO = (3, 6, 12)


class TransicaoErro(RuntimeError):
    """Falha ao transferir uma pasta — mantém o vínculo pendente."""


def _external_id(db: Session, user_id: Optional[int]) -> Optional[int]:
    """`external_id` do LegalOneUser — é ele que vai no ToUserId, não o id interno."""
    if not user_id:
        return None
    u = db.get(LegalOneUser, user_id)
    return u.external_id if u else None


def _resolver_lawsuit_id(db: Session, vinculo: BbVinculo) -> Optional[int]:
    """Descobre a pasta no L1: o id já casado, senão pelo CNJ."""
    if vinculo.l1_lawsuit_id:
        return int(vinculo.l1_lawsuit_id)
    cnj = re.sub(r"\D", "", vinculo.cnj or "")
    if len(cnj) != 20:
        return None
    from app.services.legal_one_client import LegalOneApiClient

    formatado = f"{cnj[:7]}-{cnj[7:9]}.{cnj[9:13]}.{cnj[13:14]}.{cnj[14:16]}.{cnj[16:]}"
    try:
        achados = LegalOneApiClient().search_lawsuits_by_cnj_numbers([formatado]) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Transição: busca por CNJ falhou (vínculo %s): %s", vinculo.id, exc)
        return None
    for valor in achados.values():
        if valor.get("id"):
            # Guarda pro próximo uso (e pro link do painel).
            vinculo.l1_lawsuit_id = int(valor["id"])
            return int(valor["id"])
    return None


def _responsavel_atual_no_l1(lawsuit_id: int) -> tuple[Optional[int], Optional[str], bool]:
    """Lê o responsável principal da pasta. Devolve (contactId, nome, legivel).

    `legivel=False` quando a pasta é recurso/incidente: nesse caso o REST
    responde 404 nas duas entidades e a confirmação tem que sair pela web.
    """
    from app.services.legal_one_client import LegalOneApiClient

    api = LegalOneApiClient()
    for entidade in ("Lawsuits", "Litigations"):
        try:
            resposta = api._request_with_retry(
                "GET", f"{api.base_url}/{entidade}/{lawsuit_id}/Participants"
            )
            participantes = resposta.json().get("value", [])
        except Exception:  # noqa: BLE001
            continue
        principal = next(
            (
                p
                for p in participantes
                if p.get("type") == "PersonInCharge" and p.get("isMainParticipant")
            ),
            None,
        )
        if principal is not None:
            return principal.get("contactId"), principal.get("contactName"), True
    return None, None, False


def _confirmou_pela_web(lawsuit_id: int, nome_destino: str) -> bool:
    """Confirmação de recurso/incidente: lê `details/{id}` e procura o nome.

    `edit/{id}` dá 404 nesses casos — é `details/` que responde (mesma lição
    das etiquetas em lote).
    """
    import html as _html

    from app.services.distribuidos_bb.l1_web import get_l1_web

    # Pelo helper: com a sessão morta esta leitura voltaria a página de LOGIN
    # com HTTP 200, o nome não seria achado e a transferência seria reportada
    # como "não confirmada" mesmo tendo funcionado.
    pagina = get_l1_web(f"/processos/Processos/details/{lawsuit_id}", timeout=60)
    if not pagina:
        return False
    texto = _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", pagina)))
    achado = re.search(r"Respons[áa]vel principal:\s*(.{3,80}?)\s+(?:[ÓO]rg[ãa]o|A[çc][ãa]o|Status)", texto)
    return bool(achado) and _norm(achado.group(1)) == _norm(nome_destino)


def _norm(valor: Optional[str]) -> str:
    import unicodedata

    s = unicodedata.normalize("NFKD", (valor or "").strip().lower())
    return " ".join("".join(c for c in s if not unicodedata.combining(c)).split())


def _post_troca(lawsuit_ids: list[int], external_id: int, nome_destino: str) -> None:
    """O POST em lote. Levanta TransicaoErro quando o L1 não aceita."""
    from app.services.distribuidos_bb.l1_web import post_l1_web

    payload = {
        "InvolvementStatusId": "",
        "InvolvementMainStatusId": "1",   # alvo é o envolvido principal
        "InvolvedPositionId": "",
        "InvolvedPositionText": "",
        "FromInvolvedId": "",
        "FromInvolvedText": "",
        "FromUserId": "",
        "FromUserText": "",
        "ToInvolvedId": "",
        "ToInvolvedText": "",
        "ToUserId": str(external_id),
        "ToUserText": nome_destino,
        "RowsPerPage": "18",
        "TypeOfInvolvement": "0",         # envolvido é usuário
        "selectionViewModel": {
            "SelectAll": False,
            "SelectFirsts": False,
            "UseStringIds": False,
            "SelectedIds": [str(x) for x in lawsuit_ids],
            "UnselectedIds": [],
            # O servidor só exige um JSON deserializável aqui (§4 do doc).
            "SearchModelSerialized": "{}",
        },
    }
    # Pelo helper: sessão morta volta 403 mudo ("You do not have permission"),
    # e aí ele reloga e repete. Sem isso a correção em lote de 17 processos
    # falhou INTEIRA em 04/09/2026, com uma sessão de 23 minutos.
    resposta = post_l1_web(
        "/processos/processos/ModalChangeInvolvedInBatch",
        json=payload,
        timeout=180,
        headers={"Content-Type": "application/json; charset=UTF-8"},
    )
    if resposta.status_code != 200:
        raise TransicaoErro(f"L1 respondeu HTTP {resposta.status_code} no POST da troca.")
    try:
        corpo = resposta.json() or {}
    except Exception as exc:  # noqa: BLE001
        raise TransicaoErro("L1 devolveu resposta ilegível no POST da troca.") from exc
    if not corpo.get("Success"):
        raise TransicaoErro(f"L1 recusou a troca: {str(corpo.get('Message'))[:200]}")


def responsavel_sugerido(
    db: Session, proc: BbProcesso, *, consumir_rodizio: bool = False
) -> tuple[Optional[int], Optional[str]]:
    """Pra quem o motor de vínculos diz que o processo NOVO deveria ir.

    Cenário 2: o MESMO responsável que já conduz a parte (vem do vínculo
    marcado `na_equipe_mista`) — é o ponto do módulo, não repartir a mesma
    parte entre advogados. Determinístico: consultar dez vezes dá o mesmo nome.
    Cenário 1: o próximo do rodízio da Equipe Mista.

    `consumir_rodizio` decide o que fazer com a fila no cenário 1:
      - False (default) — só ESPIA (`peek_responsavel_rr`). É o que o painel
        usa pra mostrar o nome no botão. Espiar não pode mexer na fila, senão
        cada refresh da tela rouba a vez de alguém;
      - True — AVANÇA de verdade. Só no clique que executa a transferência.
    Sem essa separação o painel prometia um nome e o POST mandava pra outro
    (o rodízio andava entre a exibição e a execução).

    Devolve (user_id, nome) ou (None, None) quando não há sugestão — processo
    sem cenário, ou fila da equipe vazia.
    """
    from app.models.distribuidos_bb import EQUIPE_MISTA_NOME, VINCULO_CENARIO_2, BbEscritorio, BbResponsavel

    if not proc.vinculo_cenario:
        return None, None

    if proc.vinculo_cenario == VINCULO_CENARIO_2:
        vinc = (
            db.query(BbVinculo)
            .filter(
                BbVinculo.processo_id == proc.id,
                BbVinculo.na_equipe_mista.is_(True),
                BbVinculo.responsavel_atual_user_id.isnot(None),
            )
            .first()
        )
        if vinc is None:
            return None, None
        u = db.get(LegalOneUser, vinc.responsavel_atual_user_id)
        return vinc.responsavel_atual_user_id, (u.name if u else vinc.responsavel_atual_nome)

    # Cenário 1: rodízio da fila especializada.
    esc = (
        db.query(BbEscritorio)
        .filter(BbEscritorio.nome == EQUIPE_MISTA_NOME, BbEscritorio.ativo.is_(True))
        .first()
    )
    if esc is None:
        return None, None

    # ANTES do rodízio: o processo já está com alguém da equipe?
    #
    # Desde que a coleta passou a pesquisar vínculos ANTES de distribuir, o
    # processo do cenário 1 já nasce com a advogada certa (o override entra na
    # própria distribuição). Puxar o rodízio nesse caso mandaria a pasta pra
    # PRÓXIMA da fila — trocando o responsável de um processo que já estava
    # correto, e ainda gastando uma vez do rodízio à toa. Pior: as pastas
    # antigas iriam junto pra essa outra pessoa, e a parte, que já estava
    # inteira com uma advogada, mudaria de mão sem motivo.
    #
    # Quem manda é o roster: se o responsável atual está na fila da equipe, ele
    # é o destino. O rodízio existe pra escolher alguém quando o processo veio
    # de FORA da equipe (o passivo distribuído antes do motor funcionar).
    if proc.responsavel_user_id:
        na_equipe = (
            db.query(BbResponsavel.id)
            .filter(
                BbResponsavel.escritorio_id == esc.id,
                BbResponsavel.user_id == proc.responsavel_user_id,
                BbResponsavel.ativo.is_(True),
            )
            .first()
        )
        if na_equipe is not None:
            u = db.get(LegalOneUser, proc.responsavel_user_id)
            return proc.responsavel_user_id, (u.name if u else None)

    from app.services.distribuidos_bb.distribuicao_service import (
        _proximo_responsavel_rr,
        peek_responsavel_rr,
    )

    uid = _proximo_responsavel_rr(db, esc) if consumir_rodizio else peek_responsavel_rr(db, esc)
    if not uid:
        return None, None
    u = db.get(LegalOneUser, uid)
    return uid, (u.name if u else None)


def _destino_do_processo(
    db: Session, proc: Optional[BbProcesso], cache: dict
) -> tuple[Optional[int], Optional[str]]:
    """Destino das pastas de UM processo, calculado uma única vez.

    O rodízio do cenário 1 só pode avançar uma vez por processo, não por pasta.
    """
    if proc is None:
        return None, None
    if proc.id not in cache:
        cache[proc.id] = responsavel_sugerido(db, proc, consumir_rodizio=True)
    return cache[proc.id]


def transferir_conjunto(
    db: Session, processo_id: int, *, solicitante: Optional[str] = None
) -> dict[str, Any]:
    """Move o processo NOVO **e** as pastas ANTIGAS da parte pro MESMO responsável.

    É o único caminho de transferência do painel, e isso é regra de negócio, não
    simplificação de tela. A carteira NERC existe pra concentrar tudo de uma
    mesma parte numa advogada só; oferecer botões separados (um pro processo
    novo, outro pras pastas antigas) permitia mandar metade pra uma e metade pra
    outra — exatamente o que o módulo existe pra impedir.

    Havia um segundo problema, pior porque silencioso: cada botão resolvia o
    destino por conta própria com `consumir_rodizio=True`. No cenário 1 a fila
    andava DUAS vezes e os dois cliques caíam em pessoas diferentes, mesmo com o
    operador achando que estava juntando as pastas. Aqui o destino é resolvido
    UMA vez, no topo, e vale pro conjunto inteiro.

    Uma pasta que já está com o destino (troca feita na mão no L1, por exemplo)
    é fechada sem POST — quem decide isso é a releitura anterior à escrita, o
    que torna dispensável o antigo botão "só marcar".

    Devolve {ok, para, transferidas, ja_estavam, falhas, itens, erro}.
    Não commita.
    """
    vazio: dict[str, Any] = {
        "ok": False, "para": None, "transferidas": 0, "ja_estavam": 0,
        "falhas": 0, "itens": [], "erro": None,
    }
    proc = db.get(BbProcesso, processo_id)
    if proc is None:
        return {**vazio, "erro": "Processo não encontrado."}

    # Uma resolução só, consumindo o rodízio uma única vez.
    destino_id, destino_nome = responsavel_sugerido(db, proc, consumir_rodizio=True)
    if not destino_id:
        return {**vazio, "erro": (
            "O motor de vínculos não tem sugestão de responsável pra este processo "
            "(sem cenário, ou fila da Equipe Mista vazia)."
        )}
    external = _external_id(db, destino_id)
    if not external:
        return {**vazio, "para": destino_nome,
                "erro": f"'{destino_nome}' não tem external_id no cadastro do L1."}

    agora = datetime.now(timezone.utc)
    res: dict[str, Any] = {
        "ok": False, "para": destino_nome, "transferidas": 0, "ja_estavam": 0,
        "falhas": 0, "itens": [], "erro": None,
    }
    # (tipo, objeto, lawsuit_id)
    alvos: list[tuple[str, Any, int]] = []

    # -- 1) o processo novo -------------------------------------------------
    rot_proc = proc.l1_folder or proc.npj or proc.cnj or f"#{proc.id}"
    if proc.responsavel_user_id == destino_id:
        res["ja_estavam"] += 1
        res["itens"].append({"tipo": "processo", "rotulo": rot_proc, "ok": True, "ja_estava": True})
    elif not proc.l1_lawsuit_id:
        res["falhas"] += 1
        res["itens"].append({"tipo": "processo", "rotulo": rot_proc, "ok": False,
                             "erro": "O processo ainda não tem pasta no Legal One."})
    else:
        alvos.append(("processo", proc, int(proc.l1_lawsuit_id)))

    # -- 2) as pastas antigas com transição em aberto ------------------------
    vincs = (
        db.query(BbVinculo)
        .filter(BbVinculo.processo_id == proc.id, BbVinculo.transicao_pendente.is_(True))
        .all()
    )
    for v in vincs:
        rot = v.l1_folder or v.npj or v.cnj or f"#{v.id}"
        if v.responsavel_atual_user_id == destino_id:
            v.transicao_pendente = False
            v.transicao_concluida_em = agora
            v.transicao_para_user_id = destino_id
            v.transicao_erro = None
            res["ja_estavam"] += 1
            res["itens"].append({"tipo": "vinculo", "vinculo_id": v.id, "rotulo": rot,
                                 "ok": True, "ja_estava": True})
            continue
        lid = _resolver_lawsuit_id(db, v)
        if not lid:
            erro = "Pasta não encontrada no Legal One (sem id e sem CNJ resolvível)."
            v.transicao_erro = erro
            res["falhas"] += 1
            res["itens"].append({"tipo": "vinculo", "vinculo_id": v.id, "rotulo": rot,
                                 "ok": False, "erro": erro})
            continue
        alvos.append(("vinculo", v, int(lid)))

    if not alvos:
        res["ok"] = res["falhas"] == 0 and res["ja_estavam"] > 0
        if not res["ok"] and not res["erro"]:
            res["erro"] = next((i.get("erro") for i in res["itens"] if i.get("erro")), None)
        return res

    # -- 3) releitura ANTES de escrever -------------------------------------
    # Serve pra duas coisas: guardar o responsável anterior (auditoria) e tirar
    # do POST quem já está no destino — o que substitui o antigo "só marcar",
    # escape pra quem trocou na mão direto no L1.
    anteriores: dict[int, tuple[Optional[int], Optional[str]]] = {}
    restantes: list[tuple[str, Any, int]] = []
    for tipo, obj, lid in alvos:
        atual_id, atual_nome, _legivel = _responsavel_atual_no_l1(lid)
        anteriores[lid] = (atual_id, atual_nome)
        if tipo == "vinculo" and atual_nome:
            obj.responsavel_atual_nome = atual_nome
        if atual_id is not None and atual_id == external:
            rot = obj.l1_folder or obj.npj or obj.cnj or f"#{obj.id}"
            if tipo == "vinculo":
                obj.transicao_pendente = False
                obj.transicao_concluida_em = agora
                obj.transicao_para_user_id = destino_id
                obj.transicao_erro = None
            else:
                obj.responsavel_user_id = destino_id
            res["ja_estavam"] += 1
            res["itens"].append({"tipo": tipo, "rotulo": rot, "ok": True, "ja_estava": True,
                                 "lawsuit_id": lid})
            continue
        restantes.append((tipo, obj, lid))

    if restantes:
        # -- 4) UM POST pro conjunto inteiro --------------------------------
        ids = sorted({lid for _t, _o, lid in restantes})
        try:
            _post_troca(ids, external, destino_nome)
        except TransicaoErro as exc:
            for tipo, obj, lid in restantes:
                rot = obj.l1_folder or obj.npj or obj.cnj or f"#{obj.id}"
                if tipo == "vinculo":
                    obj.transicao_erro = str(exc)[:400]
                res["falhas"] += 1
                res["itens"].append({"tipo": tipo, "rotulo": rot, "ok": False,
                                     "lawsuit_id": lid, "erro": str(exc)[:200]})
            registrar_evento(
                db, secao=SECAO_DISTRIBUICAO, nivel=NIVEL_ERRO,
                acao="Transferência do conjunto falhou",
                mensagem=f"O L1 recusou a troca de {len(ids)} pasta(s) pra {destino_nome}: {exc}",
                dados={"lawsuit_ids": ids, "destino": destino_id},
                processo_id=proc.id,
            )
            res["erro"] = str(exc)[:300]
            return res

        # -- 5) confirmação por releitura — `Success` não basta -------------
        confirmados: set[int] = set()
        pendentes_ids = {lid for _t, _o, lid in restantes}
        for espera in _ESPERAS_CONFIRMACAO:
            if not pendentes_ids - confirmados:
                break
            time.sleep(espera)
            for lid in sorted(pendentes_ids - confirmados):
                atual, _nome, legivel = _responsavel_atual_no_l1(lid)
                if legivel and atual == external:
                    confirmados.add(lid)
                elif not legivel and _confirmou_pela_web(lid, destino_nome):
                    # Recurso/incidente: 404 no REST, confere pela web.
                    confirmados.add(lid)

        for tipo, obj, lid in restantes:
            rot = obj.l1_folder or obj.npj or obj.cnj or f"#{obj.id}"
            _ant_id, ant_nome = anteriores.get(lid, (None, None))
            if lid in confirmados:
                if tipo == "vinculo":
                    obj.transicao_pendente = False
                    obj.transicao_concluida_em = agora
                    obj.transicao_para_user_id = destino_id
                    obj.transicao_erro = None
                else:
                    obj.responsavel_user_id = destino_id
                res["transferidas"] += 1
                res["itens"].append({"tipo": tipo, "rotulo": rot, "ok": True,
                                     "lawsuit_id": lid, "de": ant_nome, "para": destino_nome})
            else:
                erro = ("O L1 aceitou o pedido mas a pasta ainda não refletiu a troca "
                        "(a fila do L1 pode demorar). Confira no L1 e tente de novo.")
                if tipo == "vinculo":
                    obj.transicao_erro = erro
                res["falhas"] += 1
                res["itens"].append({"tipo": tipo, "rotulo": rot, "ok": False,
                                     "lawsuit_id": lid, "erro": erro})

    # -- 6) um evento só pro conjunto ---------------------------------------
    from app.models.distribuidos_bb import VINCULO_CENARIO_2

    total_ok = res["transferidas"] + res["ja_estavam"]
    res["ok"] = res["falhas"] == 0 and total_ok > 0
    motivo = ("mesma condutora da parte" if proc.vinculo_cenario == VINCULO_CENARIO_2
              else "rodízio da Equipe Mista")
    if res["falhas"] == 0:
        registrar_evento(
            db, secao=SECAO_DISTRIBUICAO, nivel=NIVEL_SUCESSO,
            acao="Conjunto da parte transferido",
            mensagem=(
                f"{total_ok} pasta(s) da parte agora sob {destino_nome} ({motivo}): "
                f"o processo novo e {len(vincs)} pasta(s) antiga(s)"
                + (f", por {solicitante}." if solicitante else ".")
            ),
            dados={"para": destino_id, "pastas": total_ok, "cenario": proc.vinculo_cenario},
            processo_id=proc.id,
        )
    else:
        registrar_evento(
            db, secao=SECAO_DISTRIBUICAO, nivel=NIVEL_AVISO,
            acao="Conjunto da parte transferido em parte",
            mensagem=(
                f"{total_ok} pasta(s) foram pra {destino_nome} e {res['falhas']} não. "
                "As que faltaram continuam pendentes, com o motivo na linha."
            ),
            dados={"para": destino_id, "ok": total_ok, "falhas": res["falhas"]},
            processo_id=proc.id,
        )
    return res
