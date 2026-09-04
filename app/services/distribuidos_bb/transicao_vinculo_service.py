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

    from app.services.prazos_iniciais.legacy_task_http_cancellation_service import (
        LegacyTaskHttpCancellationService,
    )

    svc = LegacyTaskHttpCancellationService()
    resposta = svc._http.get(
        f"{svc._web_base_url()}/processos/Processos/details/{lawsuit_id}",
        cookies=svc._ensure_session(),
        timeout=60,
    )
    if resposta.status_code != 200:
        return False
    texto = _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", resposta.text)))
    achado = re.search(r"Respons[áa]vel principal:\s*(.{3,80}?)\s+(?:[ÓO]rg[ãa]o|A[çc][ãa]o|Status)", texto)
    return bool(achado) and _norm(achado.group(1)) == _norm(nome_destino)


def _norm(valor: Optional[str]) -> str:
    import unicodedata

    s = unicodedata.normalize("NFKD", (valor or "").strip().lower())
    return " ".join("".join(c for c in s if not unicodedata.combining(c)).split())


def _post_troca(lawsuit_ids: list[int], external_id: int, nome_destino: str) -> None:
    """O POST em lote. Levanta TransicaoErro quando o L1 não aceita."""
    from app.services.prazos_iniciais.legacy_task_http_cancellation_service import (
        LegacyTaskHttpCancellationService,
    )

    svc = LegacyTaskHttpCancellationService()
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
    resposta = svc._http.post(
        f"{svc._web_base_url()}/processos/processos/ModalChangeInvolvedInBatch",
        json=payload,
        cookies=svc._ensure_session(),
        timeout=180,
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "*/*",
        },
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


def transferir_vinculos(
    db: Session,
    vinculo_ids: list[int],
    *,
    solicitante: Optional[str] = None,
) -> dict[str, Any]:
    """Transfere as pastas residuais pro responsável do processo novo.

    O destino NÃO é escolhido pelo operador: é quem os vínculos já definiram
    como condutor do processo novo (`BbProcesso.responsavel_user_id`), que no
    cenário 1 é a advogada da Equipe Mista. Isso mantém a decisão no motor e
    deixa pro clique só a execução.

    Devolve {transferidos, falhas, itens:[{vinculo_id, ok, ...}]}. Não commita.
    """
    resultado: dict[str, Any] = {"transferidos": 0, "falhas": 0, "itens": []}
    if not vinculo_ids:
        return resultado

    vinculos = (
        db.query(BbVinculo).filter(BbVinculo.id.in_(list(dict.fromkeys(vinculo_ids)))).all()
    )
    agora = datetime.now(timezone.utc)
    # Destino resolvido UMA vez por processo. Resolver por vínculo faria o
    # rodízio andar a cada pasta e espalharia as pastas da MESMA parte entre
    # advogadas diferentes — o oposto do que o módulo quer.
    destinos: dict[int, tuple] = {}

    # Agrupa por destino: um POST por advogada, não um por pasta.
    por_destino: dict[int, list[BbVinculo]] = {}
    for v in vinculos:
        item: dict[str, Any] = {"vinculo_id": v.id, "npj": v.npj, "ok": False}
        proc = db.get(BbProcesso, v.processo_id)
        # O destino é quem o MOTOR aponta (a advogada da Equipe Mista), não o
        # responsável atual do processo novo. Enquanto o motor rodava dentro da
        # coleta os dois coincidiam, porque o override já era aplicado na
        # distribuição; num processo reprocessado depois, o responsável atual é
        # o do rodízio normal — e mandar a pasta pra ele jogaria o trabalho pra
        # fora da equipe, que é o oposto do que o módulo existe pra fazer.
        # (Caso real: o painel oferecia "transferir para Gabriel/Beatriz", que
        # não estão na fila da Equipe Mista.)
        destino_id, destino_nome_sug = _destino_do_processo(db, proc, destinos)
        if not destino_id:
            item["erro"] = (
                "O motor de vínculos não tem sugestão de responsável pra este processo "
                "(sem cenário, ou fila da Equipe Mista vazia)."
            )
            v.transicao_erro = item["erro"]
            resultado["falhas"] += 1
            resultado["itens"].append(item)
            continue
        if v.responsavel_atual_user_id == destino_id:
            # Já está com quem deveria: fecha sem chamar o L1.
            v.transicao_pendente = False
            v.transicao_concluida_em = agora
            v.transicao_para_user_id = destino_id
            v.transicao_erro = None
            item["ok"] = True
            item["ja_estava"] = True
            resultado["transferidos"] += 1
            resultado["itens"].append(item)
            continue
        lawsuit_id = _resolver_lawsuit_id(db, v)
        if not lawsuit_id:
            item["erro"] = "Pasta não encontrada no Legal One (sem id e sem CNJ resolvível)."
            v.transicao_erro = item["erro"]
            resultado["falhas"] += 1
            resultado["itens"].append(item)
            continue
        item["lawsuit_id"] = lawsuit_id
        por_destino.setdefault(destino_id, []).append(v)

    for destino_id, lista in por_destino.items():
        usuario = db.get(LegalOneUser, destino_id)
        external = _external_id(db, destino_id)
        nome_destino = usuario.name if usuario else str(destino_id)
        if not external:
            for v in lista:
                erro = f"'{nome_destino}' não tem external_id no cadastro do L1."
                v.transicao_erro = erro
                resultado["falhas"] += 1
                resultado["itens"].append({"vinculo_id": v.id, "npj": v.npj, "ok": False, "erro": erro})
            continue

        ids = [int(v.l1_lawsuit_id) for v in lista]
        # Snapshot do responsável anterior ANTES de escrever (rollback/auditoria).
        anteriores: dict[int, tuple[Optional[int], Optional[str]]] = {}
        for v in lista:
            atual_id, atual_nome, _ = _responsavel_atual_no_l1(int(v.l1_lawsuit_id))
            if atual_id is not None:
                v.responsavel_atual_user_id = v.responsavel_atual_user_id or None
                v.responsavel_atual_nome = atual_nome or v.responsavel_atual_nome
            anteriores[int(v.l1_lawsuit_id)] = (atual_id, atual_nome or v.responsavel_atual_nome)

        try:
            _post_troca(ids, external, nome_destino)
        except TransicaoErro as exc:
            for v in lista:
                v.transicao_erro = str(exc)[:400]
                resultado["falhas"] += 1
                resultado["itens"].append(
                    {"vinculo_id": v.id, "npj": v.npj, "ok": False, "erro": str(exc)[:200]}
                )
            registrar_evento(
                db, secao=SECAO_DISTRIBUICAO, nivel=NIVEL_ERRO, acao="Transição de vínculo falhou",
                mensagem=f"O L1 recusou a troca de {len(ids)} pasta(s) pra {nome_destino}: {exc}",
                dados={"lawsuit_ids": ids, "destino": destino_id},
            )
            continue

        # Confirmação por releitura — `Success` não basta (§6.2 do doc).
        pendentes = {int(v.l1_lawsuit_id): v for v in lista}
        confirmados: set[int] = set()
        for espera in _ESPERAS_CONFIRMACAO:
            if not set(pendentes) - confirmados:
                break
            time.sleep(espera)
            for lid, v in pendentes.items():
                if lid in confirmados:
                    continue
                atual, _nome, legivel = _responsavel_atual_no_l1(lid)
                if legivel and atual == external:
                    confirmados.add(lid)
                elif not legivel and _confirmou_pela_web(lid, nome_destino):
                    # Recurso/incidente: 404 no REST, confere pela web.
                    confirmados.add(lid)

        for lid, v in pendentes.items():
            anterior_id, anterior_nome = anteriores.get(lid, (None, None))
            if lid in confirmados:
                v.transicao_pendente = False
                v.transicao_concluida_em = agora
                v.transicao_para_user_id = destino_id
                v.transicao_erro = None
                resultado["transferidos"] += 1
                resultado["itens"].append(
                    {"vinculo_id": v.id, "npj": v.npj, "ok": True, "lawsuit_id": lid,
                     "de": anterior_nome, "para": nome_destino}
                )
                registrar_evento(
                    db, secao=SECAO_DISTRIBUICAO, nivel=NIVEL_SUCESSO,
                    acao="Transição de vínculo executada",
                    mensagem=(
                        f"Pasta {v.l1_folder or v.npj} transferida de "
                        f"{anterior_nome or '—'} para {nome_destino}"
                        + (f" por {solicitante}." if solicitante else ".")
                    ),
                    dados={"lawsuit_id": lid, "de": anterior_id, "para": destino_id,
                           "vinculo_id": v.id},
                    processo_id=v.processo_id,
                )
            else:
                erro = (
                    "O L1 aceitou o pedido mas a pasta ainda não refletiu a troca "
                    "(a fila do L1 pode demorar). Confira no L1 e tente de novo."
                )
                v.transicao_erro = erro
                resultado["falhas"] += 1
                resultado["itens"].append(
                    {"vinculo_id": v.id, "npj": v.npj, "ok": False, "lawsuit_id": lid, "erro": erro}
                )
                registrar_evento(
                    db, secao=SECAO_DISTRIBUICAO, nivel=NIVEL_AVISO,
                    acao="Transição de vínculo não confirmada",
                    mensagem=(
                        f"POST aceito para a pasta {v.l1_folder or v.npj} → {nome_destino}, "
                        "mas a releitura ainda mostra o responsável antigo."
                    ),
                    dados={"lawsuit_id": lid, "para": destino_id, "vinculo_id": v.id},
                    processo_id=v.processo_id,
                )

    return resultado


def transferir_processo_novo(
    db: Session, processo_id: int, *, solicitante: Optional[str] = None
) -> dict[str, Any]:
    """Troca no L1 o responsável da pasta do processo NOVO, pro que o motor sugeriu.

    Complementa a transição das pastas residuais: aquela move o passado, esta
    move o processo que acabou de entrar. Mesmo método
    (`ModalChangeInvolvedInBatch`) e a mesma disciplina de confirmar relendo.

    Devolve {"ok", "de", "para", "erro"}. Não commita.
    """
    proc = db.get(BbProcesso, processo_id)
    if proc is None:
        return {"ok": False, "erro": "Processo não encontrado."}

    destino_id, destino_nome = responsavel_sugerido(db, proc, consumir_rodizio=True)
    if not destino_id:
        return {"ok": False, "erro": "O motor de vínculos não tem sugestão de responsável pra este processo."}
    if proc.responsavel_user_id == destino_id:
        return {"ok": True, "ja_estava": True, "para": destino_nome,
                "de": destino_nome, "erro": None}

    lawsuit_id = proc.l1_lawsuit_id
    if not lawsuit_id:
        return {"ok": False, "erro": "O processo ainda não tem pasta no Legal One."}

    external = _external_id(db, destino_id)
    if not external:
        return {"ok": False, "erro": f"'{destino_nome}' não tem external_id no cadastro do L1."}

    anterior_id = proc.responsavel_user_id
    u_ant = db.get(LegalOneUser, anterior_id) if anterior_id else None
    anterior_nome = (u_ant.name if u_ant else None)

    try:
        _post_troca([int(lawsuit_id)], external, destino_nome)
    except TransicaoErro as exc:
        registrar_evento(
            db, secao=SECAO_DISTRIBUICAO, nivel=NIVEL_ERRO,
            acao="Transferência do processo novo falhou",
            mensagem=f"O L1 recusou a troca da pasta {proc.l1_folder or lawsuit_id}: {exc}",
            processo_id=proc.id,
        )
        return {"ok": False, "erro": str(exc)[:300], "de": anterior_nome, "para": destino_nome}

    # Confirmação por releitura — a fila do L1 é assíncrona (§6.2 do doc).
    confirmado = False
    for espera in _ESPERAS_CONFIRMACAO:
        time.sleep(espera)
        atual, _nome, legivel = _responsavel_atual_no_l1(int(lawsuit_id))
        if legivel and atual == external:
            confirmado = True
            break
        if not legivel and _confirmou_pela_web(int(lawsuit_id), destino_nome):
            confirmado = True
            break

    if not confirmado:
        erro = ("O L1 aceitou o pedido mas a pasta ainda não refletiu a troca "
                "(a fila do L1 pode demorar). Confira no L1 e tente de novo.")
        registrar_evento(
            db, secao=SECAO_DISTRIBUICAO, nivel=NIVEL_AVISO,
            acao="Transferência do processo novo não confirmada",
            mensagem=f"POST aceito para a pasta {proc.l1_folder or lawsuit_id} → {destino_nome}, "
                     "mas a releitura ainda mostra o responsável antigo.",
            processo_id=proc.id,
        )
        return {"ok": False, "erro": erro, "de": anterior_nome, "para": destino_nome}

    proc.responsavel_user_id = destino_id
    registrar_evento(
        db, secao=SECAO_DISTRIBUICAO, nivel=NIVEL_SUCESSO,
        acao="Processo novo transferido pra equipe especializada",
        mensagem=(
            f"Pasta {proc.l1_folder or lawsuit_id} transferida de "
            f"{anterior_nome or '—'} para {destino_nome} "
            f"({'mesma condutora da parte' if proc.vinculo_cenario == 'CENARIO_2' else 'rodízio da Equipe Mista'})"
            + (f" por {solicitante}." if solicitante else ".")
        ),
        dados={"lawsuit_id": lawsuit_id, "de": anterior_id, "para": destino_id,
               "cenario": proc.vinculo_cenario},
        processo_id=proc.id,
    )
    return {"ok": True, "de": anterior_nome, "para": destino_nome, "erro": None}
