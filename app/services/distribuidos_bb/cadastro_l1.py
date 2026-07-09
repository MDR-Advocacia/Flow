"""Camada de cadastro no Legal One (POST /Lawsuits) + resolução de contatos.

Reusa `l1_contacts.find_contact` (busca por CPF/CNPJ) e adiciona o que faltava:
criar contato (PF /Individuals, PJ /Companies) quando não existe, e criar o
processo (`POST /Lawsuits`, já validado ao vivo — lawsuit 78502).

Bloqueio de honorário/centro de custo: DESLIGADO no tenant em 2026-07-09, então
a criação real passa. `countryId` e `costCenters` são PROIBIDOS no create.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from app.services.contatos_legalone import l1_contacts

logger = logging.getLogger("distribuidos_bb.cadastro")

# Posições de participante (LitigationParticipantPositions) — do handoff/cobaia.
POSICAO_CLIENTE = 2        # Customer
POSICAO_RESPONSAVEL = 6    # PersonInCharge
POSICAO_PARTE_CONTRARIA = 1  # OtherParty

# Custom fields obrigatórios do tenant.
CF_NPJ = 3687              # texto
CF_DATA_TERCEIRIZACAO = 3691  # data

# Contato do Banco do Brasil no L1 (Customer fixo dos distribuídos).
BB_CNPJ = "00000000000191"

CONTATO_RESOLVIDO = "RESOLVIDO"
CONTATO_CRIADO = "CRIADO"
CONTATO_AMBIGUO = "AMBIGUO"
CONTATO_ERRO = "ERRO"


def _digitos(doc: Optional[str]) -> str:
    return re.sub(r"\D", "", doc or "")


def formatar_documento(doc: Optional[str]) -> Optional[str]:
    """Dígitos → máscara (CPF 000.000.000-00 / CNPJ 00.000.000/0000-00).

    O L1 guarda `identificationNumber` COM máscara — o filtro precisa dela.
    """
    d = _digitos(doc)
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    return None


def _resource_por_doc(doc: Optional[str]) -> Optional[str]:
    d = _digitos(doc)
    if len(d) == 11:
        return l1_contacts.RESOURCE_INDIVIDUALS
    if len(d) == 14:
        return l1_contacts.RESOURCE_COMPANIES
    return None


def criar_contato(client: Any, resource: str, nome: str, doc_mascarado: str) -> Optional[int]:
    """POST /{Individuals|Companies} com o mínimo (name + identificationNumber)."""
    url = f"{client.base_url}/{resource}"
    payload = {"name": nome.strip(), "identificationNumber": doc_mascarado}
    resp = client._authenticated_request(
        "POST", url, json=payload, headers={"Accept": "application/json"}
    )
    if resp.status_code not in (200, 201):
        raise l1_contacts.ContatoL1Error(
            f"POST /{resource} falhou (HTTP {resp.status_code}): {(resp.text or '')[:400]}"
        )
    try:
        return resp.json().get("id")
    except ValueError:
        return None


def resolver_ou_criar_contato(
    client: Any, *, nome: str, cpf_cnpj: Optional[str], criar_se_faltar: bool = True
) -> dict[str, Any]:
    """Resolve um envolvido → contactId (cria PF/PJ se não existir).

    Devolve {contact_id, resource, status, nome}. status em RESOLVIDO/CRIADO/
    AMBIGUO/ERRO. Sem CPF/CNPJ válido → ERRO (não dá pra deduplicar/criar).
    """
    resource = _resource_por_doc(cpf_cnpj)
    doc_mask = formatar_documento(cpf_cnpj)
    if not resource or not doc_mask:
        return {"contact_id": None, "resource": None, "status": CONTATO_ERRO,
                "nome": nome, "motivo": "sem CPF/CNPJ válido"}

    achados = l1_contacts.find_contact(client, resource, doc_mask)
    if len(achados) == 1:
        return {"contact_id": achados[0].get("id"), "resource": resource,
                "status": CONTATO_RESOLVIDO, "nome": nome}
    if len(achados) > 1:
        return {"contact_id": None, "resource": resource, "status": CONTATO_AMBIGUO,
                "nome": nome, "motivo": f"{len(achados)} contatos com o mesmo documento"}

    if not criar_se_faltar:
        return {"contact_id": None, "resource": resource, "status": CONTATO_ERRO,
                "nome": nome, "motivo": "contato não existe (criação desligada)"}

    novo_id = criar_contato(client, resource, nome, doc_mask)
    return {"contact_id": novo_id, "resource": resource, "status": CONTATO_CRIADO, "nome": nome}


def create_lawsuit(client: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """POST /Lawsuits com validação local. Devolve {ok, id, status_code, erros, resposta}.

    Valida antes de bater na API: participantes Customer+PersonInCharge, custom
    fields 3687+3691, e remove countryId/costCenters (proibidos no create).
    """
    erros = _validar_payload(payload)
    if erros:
        return {"ok": False, "id": None, "status_code": None, "erros": erros, "resposta": None}

    limpo = dict(payload)
    limpo.pop("countryId", None)
    limpo.pop("costCenters", None)

    resp = client._authenticated_request("POST", f"{client.base_url}/lawsuits", json=limpo)
    if resp.status_code in (200, 201):
        try:
            body = resp.json()
        except ValueError:
            body = {}
        return {"ok": True, "id": body.get("id"), "status_code": resp.status_code,
                "erros": [], "resposta": body}

    detalhes = []
    try:
        detalhes = [
            f"{d.get('code')}: {d.get('target') or ''} {d.get('message', '')}".strip()
            for d in resp.json().get("error", {}).get("details", [])
        ]
    except ValueError:
        detalhes = [(resp.text or "")[:300]]
    return {"ok": False, "id": None, "status_code": resp.status_code,
            "erros": detalhes, "resposta": None}


def _validar_payload(payload: dict[str, Any]) -> list[str]:
    erros: list[str] = []
    participantes = payload.get("participants") or []
    tipos = {p.get("type") for p in participantes}
    if "Customer" not in tipos:
        erros.append("Falta participante Customer (cliente).")
    if "PersonInCharge" not in tipos:
        erros.append("Falta participante PersonInCharge (responsável).")
    if any(p.get("contactId") is None for p in participantes):
        erros.append("Há participante sem contactId resolvido.")
    cfs = {c.get("customFieldId") for c in (payload.get("customFields") or [])}
    if CF_NPJ not in cfs:
        erros.append(f"Falta custom field {CF_NPJ} (NPJ).")
    if CF_DATA_TERCEIRIZACAO not in cfs:
        erros.append(f"Falta custom field {CF_DATA_TERCEIRIZACAO} (Data de Terceirização).")
    return erros


# ─── Resolvers de catálogo (nome/path → id) ──────────────────────────────

_NATURES_CACHE: Optional[dict[str, int]] = None


def resolver_nature_id(client: Any, natureza: Optional[str]) -> Optional[int]:
    """Nome da natureza → natureId (GET /LitigationNatures, cacheado)."""
    global _NATURES_CACHE
    if not natureza:
        return None
    if _NATURES_CACHE is None:
        _NATURES_CACHE = {}
        try:
            resp = client._authenticated_request("GET", f"{client.base_url}/LitigationNatures")
            for it in resp.json().get("value", []):
                nome = (it.get("name") or "").strip().lower()
                if nome:
                    _NATURES_CACHE[nome] = it.get("id")
        except Exception:  # noqa: BLE001
            logger.exception("Cadastro: falha ao carregar /LitigationNatures.")
    return _NATURES_CACHE.get(natureza.strip().lower())
