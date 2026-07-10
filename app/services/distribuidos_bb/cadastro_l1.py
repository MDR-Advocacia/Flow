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
import unicodedata
from datetime import datetime
from typing import Any, Optional

from app.services.contatos_legalone import l1_contacts

logger = logging.getLogger("distribuidos_bb.cadastro")

# Status (LitigationStatus) — mapa fixo do tenant (do handoff).
STATUS_MAP = {"ativo": 1, "suspenso": 2, "baixado": 3, "arquivado": 4}

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
                nome = it.get("name")
                if nome:
                    _NATURES_CACHE[_chave(nome)] = it.get("id")
        except Exception:  # noqa: BLE001
            logger.exception("Cadastro: falha ao carregar /LitigationNatures.")
    return _NATURES_CACHE.get(_chave(natureza))


# ─── Resolvers de catálogo genéricos (nome → id), cacheados ──────────────

_CACHE_CATALOGO: dict[str, dict[str, int]] = {}
_CACHE_AREAS: Optional[dict[str, int]] = None


def _chave(s: Optional[str]) -> str:
    """Normaliza (sem acento, minúsculo, espaços colapsados) pra comparar."""
    t = unicodedata.normalize("NFKD", s or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(t.strip().lower().split())


def _chave_path(s: Optional[str]) -> str:
    """Normaliza um path (cada segmento entre '/'), pra casar escritório."""
    t = unicodedata.normalize("NFKD", s or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return "/".join(seg.strip().lower() for seg in t.split("/"))


def _catalogo_por_nome(client: Any, recurso: str) -> dict[str, int]:
    """Carrega GET /{recurso} → {nome_normalizado: id} (cacheado)."""
    if recurso not in _CACHE_CATALOGO:
        mapa: dict[str, int] = {}
        try:
            resp = client._authenticated_request("GET", f"{client.base_url}/{recurso}")
            for it in resp.json().get("value", []):
                nome = it.get("name")
                if nome and it.get("id") is not None:
                    mapa[_chave(nome)] = it["id"]
        except Exception:  # noqa: BLE001
            logger.exception("Cadastro: falha ao carregar /%s.", recurso)
        _CACHE_CATALOGO[recurso] = mapa
    return _CACHE_CATALOGO[recurso]


def resolver_office_por_path(client: Any, path: Optional[str]) -> Optional[int]:
    """Escritório responsável/origem: casa o path completo com uma área do L1."""
    global _CACHE_AREAS
    if not path:
        return None
    if _CACHE_AREAS is None:
        _CACHE_AREAS = {}
        try:
            for a in client.get_all_allocatable_areas():
                if a.get("path"):
                    _CACHE_AREAS[_chave_path(a["path"])] = a.get("id")
        except Exception:  # noqa: BLE001
            logger.exception("Cadastro: falha ao carregar /areas.")
    return _CACHE_AREAS.get(_chave_path(path))


def resolver_state_id(client: Any, uf_ou_nome: Optional[str]) -> Optional[int]:
    """UF (sigla) ou nome do estado → stateId."""
    if not uf_ou_nome:
        return None
    alvo = _chave(uf_ou_nome)
    try:
        resp = client._authenticated_request("GET", f"{client.base_url}/States")
        for it in resp.json().get("value", []):
            if _chave(it.get("name")) == alvo or _chave(it.get("stateCode")) == alvo:
                return it.get("id")
    except Exception:  # noqa: BLE001
        logger.exception("Cadastro: falha ao resolver estado %s.", uf_ou_nome)
    return None


def resolver_justice_id(client: Any, nome: Optional[str]) -> Optional[int]:
    return _catalogo_por_nome(client, "LitigationJustices").get(_chave(nome)) if nome else None


def resolver_action_type_id(client: Any, nome: Optional[str]) -> Optional[int]:
    if not nome:
        return None
    return _catalogo_por_nome(client, "LitigationActionAppealProceduralIssueTypes").get(_chave(nome))


def resolver_status_id(nome: Optional[str]) -> Optional[int]:
    return STATUS_MAP.get(_chave(nome)) if nome else None


def resolver_position_id(client: Any, nome: Optional[str]) -> Optional[int]:
    """Posição do participante (Réu/Autor/Parte…) → positionId."""
    if not nome:
        return None
    return _catalogo_por_nome(client, "LitigationParticipantPositions").get(_chave(nome))


def resolver_proximo_folder(client: Any, incremento: int = 0) -> Optional[str]:
    """Próximo Proc sequencial: (maior folder existente + 1 + incremento).

    A API do L1 EXIGE o `folder` e não auto-numera (só o cadastro manual numera).
    Formato 'Proc - NNNNNNN'. Ignora folders "fake" altos (>= 9.000.000).
    Num lote, passe incremento=0,1,2… pra sequenciar; retry se colidir.
    """
    maxn = 0
    try:
        url = f"{client.base_url}/Lawsuits?$select=id,folder&$orderby=folder desc&$top=10"
        resp = client._authenticated_request("GET", url)
        for v in (resp.json().get("value", []) if resp.status_code == 200 else []):
            m = re.search(r"(\d{3,})", v.get("folder") or "")
            if m:
                n = int(m.group(1))
                if n < 9_000_000 and n > maxn:
                    maxn = n
    except Exception:  # noqa: BLE001
        logger.exception("Cadastro: falha ao obter o próximo folder (Proc).")
        return None
    return f"Proc - {maxn + 1 + incremento:07d}" if maxn else None


def _buscar_lawsuits_por_cnj(client: Any, cnj: str) -> list[dict[str, Any]]:
    """Busca lawsuits por CNJ (máscara e dígitos), com guarda anti-lixo.

    Filtra o resultado pra garantir que o identifierNumber bate mesmo (o filtro
    OData às vezes devolve página não-filtrada; comparamos por dígitos).
    """
    alvo = _digitos(cnj)
    for termo in (cnj.strip(), alvo):
        if not termo:
            continue
        lit = termo.replace("'", "''")
        url = (
            f"{client.base_url}/Lawsuits?$filter=identifierNumber eq '{lit}'"
            "&$select=id,folder,identifierNumber,responsibleOfficeId,type&$top=30"
        )
        try:
            resp = client._authenticated_request("GET", url)
            vals = resp.json().get("value", []) if resp.status_code == 200 else []
        except Exception:  # noqa: BLE001
            vals = []
        vals = [v for v in vals if _digitos(v.get("identifierNumber")) == alvo]
        if vals:
            return vals
    return []


def verificar_duplicado(client: Any, cnj: Optional[str], office_id: Optional[int]) -> dict[str, Any]:
    """Trava anti-duplicado por (CNJ + escritório responsável).

    Mesmo CNJ pode ter DUAS pastas se em escritórios DIFERENTES (ex.: BB e
    Ativos no mesmo processo). Só é duplicado se já existir pasta principal do
    mesmo CNJ NO MESMO escritório. Devolve
    {duplicado, no_mesmo_escritorio: [...], em_outros_escritorios: [...]}.
    """
    if not cnj:
        return {"duplicado": False, "no_mesmo_escritorio": [], "em_outros_escritorios": []}
    existentes = _buscar_lawsuits_por_cnj(client, cnj)
    def dto(v):
        return {"id": v.get("id"), "folder": v.get("folder"),
                "office": v.get("responsibleOfficeId"), "type": v.get("type")}
    mesmo = [dto(v) for v in existentes if v.get("responsibleOfficeId") == office_id]
    outros = [dto(v) for v in existentes if v.get("responsibleOfficeId") != office_id]
    return {"duplicado": bool(mesmo), "no_mesmo_escritorio": mesmo, "em_outros_escritorios": outros}


def buscar_lawsuit_por_npj(client: Any, npj: Optional[str]) -> list[dict[str, Any]]:
    """Acha a(s) pasta(s) pelo NPJ — que o IMPORT grava como `title` da pasta.

    Essencial pra confirmar processo SEM CNJ (pré-judicial): o campo `title` é
    filtrável (`title eq '<npj>'`, validado ao vivo) e o custom field 3687 vem
    VAZIO em pasta criada por import. Devolve dtos {id, folder, office, cnj}.
    """
    if not npj or not npj.strip():
        return []
    lit = npj.strip().replace("'", "''")
    url = (
        f"{client.base_url}/Lawsuits?$filter=title eq '{lit}'"
        "&$select=id,folder,identifierNumber,responsibleOfficeId,title&$top=10"
    )
    try:
        resp = client._authenticated_request("GET", url)
        vals = resp.json().get("value", []) if resp.status_code == 200 else []
    except Exception:  # noqa: BLE001
        vals = []
    # Guarda: só os que o title bate mesmo (defensivo contra página não-filtrada).
    alvo = npj.strip()
    return [
        {
            "id": v.get("id"), "folder": v.get("folder"),
            "office": v.get("responsibleOfficeId"), "cnj": v.get("identifierNumber"),
        }
        for v in vals
        if (v.get("title") or "").strip() == alvo
    ]


def resolver_contato_por_nome(client: Any, nome: Optional[str]) -> Optional[int]:
    """Contato (Individual) pelo NOME COMPLETO exato → contactId.

    Usado pro PersonInCharge (responsável). Sem homônimos na base (mesmo
    padrão dos outros módulos), então pega o 1º match exato.
    """
    if not nome or not nome.strip():
        return None
    try:
        lit = nome.strip().replace("'", "''")
        url = f"{client.base_url}/Individuals?$filter=name eq '{lit}'&$top=1"
        resp = client._authenticated_request("GET", url)
        vals = resp.json().get("value", []) if resp.status_code == 200 else []
        return vals[0].get("id") if vals else None
    except Exception:  # noqa: BLE001
        logger.exception("Cadastro: falha ao resolver contato do responsável %r.", nome)
        return None


# ─── Parse da Tramitação (BB) → cidade / UF / órgão ──────────────────────


def parse_tramitacao(tramitacao: Optional[str]) -> dict[str, Optional[str]]:
    """'Pimenta Bueno/RO - TJE-JECC - 01 Juizado...' → {cidade, uf, orgao}."""
    if not tramitacao:
        return {"cidade": None, "uf": None, "orgao": None}
    partes = [p.strip() for p in str(tramitacao).split(" - ")]
    cidade = uf = None
    if partes and "/" in partes[0]:
        cidade, uf = partes[0].rsplit("/", 1)
        cidade, uf = cidade.strip(), uf.strip()
    orgao = partes[-1] if len(partes) > 1 else None
    return {"cidade": cidade, "uf": uf, "orgao": orgao}


def _data_iso(valor: Optional[str]) -> Optional[str]:
    """'DD/MM/AAAA' ou 'AAAA-MM-DD…' → ISO com Z (pro L1)."""
    if not valor:
        return None
    v = str(valor).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(v[:10], fmt).strftime("%Y-%m-%dT00:00:00Z")
        except ValueError:
            continue
    return None


def _config(db, chave: str) -> Optional[str]:
    from app.models.distribuidos_bb import BbConfig

    c = db.get(BbConfig, chave)
    return c.valor if c else None


def montar_payload_lawsuit(
    db, client: Any, processo, *, criar_contatos: bool = False, folder_incremento: int = 0,
) -> dict[str, Any]:
    """Monta o LawsuitModel a partir do distribuído + config + envolvidos.

    Devolve {payload, resolucao, avisos}. `resolucao` reporta cada campo
    resolvido (valor) ou faltando (motivo) — é o que o dry-run mostra.
    `criar_contatos=False` (dry-run) só RESOLVE contatos (não cria).
    """
    from app.models.distribuidos_bb import BbEnvolvido

    resolucao: dict[str, Any] = {}
    avisos: list[str] = []

    tram = parse_tramitacao(processo.tramitacao)
    cidade, uf = tram["cidade"], tram["uf"]

    office_id = resolver_office_por_path(client, processo.escritorio_path)
    origem_id = resolver_office_por_path(client, _config(db, "escritorio_origem")) or 1

    # TRAVA anti-duplicado: (CNJ + escritório responsável).
    dedup = verificar_duplicado(client, processo.cnj, office_id)
    resolucao["dedup"] = dedup
    if dedup["duplicado"]:
        avisos.append(
            "DUPLICADO: já existe pasta principal deste CNJ no mesmo escritório "
            f"({dedup['no_mesmo_escritorio']}) — NÃO cadastrar."
        )
    elif dedup["em_outros_escritorios"]:
        avisos.append(
            f"Existe este CNJ em OUTRO escritório ({dedup['em_outros_escritorios']}) — "
            "ok criar (2 pastas, escritórios diferentes)."
        )
    nature_id = resolver_nature_id(client, processo.natureza)
    action_id = resolver_action_type_id(client, processo.acao)
    state_id = resolver_state_id(client, uf)
    city_id = None
    if cidade and uf:
        city_id, _ = l1_contacts.resolve_city_id(client, cidade, uf)
    status_id = resolver_status_id(_config(db, "status") or "Ativo")
    valor = float(processo.valor_causa) if processo.valor_causa is not None else None

    # Participantes
    participantes: list[dict[str, Any]] = []
    # Customer (BB)
    cliente_contact_id = _config(db, "cliente_contact_id")
    pos_cliente = resolver_position_id(client, processo.posicao) or POSICAO_CLIENTE
    if cliente_contact_id:
        participantes.append({"type": "Customer", "contactId": int(cliente_contact_id),
                              "isMainParticipant": True, "positionId": pos_cliente})
        resolucao["cliente"] = {"contactId": int(cliente_contact_id), "positionId": pos_cliente}
    else:
        avisos.append("cliente_contact_id não configurado (Valores Padrão).")

    # PersonInCharge (responsável) — contato resolvido pelo NOME COMPLETO.
    from app.models.legal_one import LegalOneUser

    resp_nome = None
    resp_contact_id = None
    if processo.responsavel_user_id:
        u = db.get(LegalOneUser, processo.responsavel_user_id)
        resp_nome = u.name if u else None
        resp_contact_id = resolver_contato_por_nome(client, resp_nome)
    if resp_contact_id:
        participantes.append({"type": "PersonInCharge", "contactId": resp_contact_id,
                              "isMainParticipant": True, "positionId": POSICAO_RESPONSAVEL})
    else:
        avisos.append(f"Responsável '{resp_nome or '—'}' sem contato no L1 (PersonInCharge não resolvido).")
    resolucao["responsavel"] = {"user_id": processo.responsavel_user_id, "nome": resp_nome, "contactId": resp_contact_id}

    # OtherParty: envolvidos reais com CPF/CNPJ — EXCETO o próprio cliente (BB).
    doc_cliente = _digitos(_config(db, "cliente_cpf_cnpj"))
    envolvidos = db.query(BbEnvolvido).filter(BbEnvolvido.processo_id == processo.id).all()
    partes_report = []
    for e in envolvidos:
        if not e.cpf_cnpj:
            continue
        if doc_cliente and _digitos(e.cpf_cnpj) == doc_cliente:
            continue  # é o Banco do Brasil (já entra como Customer)
        res = resolver_ou_criar_contato(client, nome=e.nome, cpf_cnpj=e.cpf_cnpj, criar_se_faltar=criar_contatos)
        if cliente_contact_id and res.get("contact_id") == int(cliente_contact_id):
            continue  # resolveu pro contato do cliente — não é adverso
        partes_report.append({"nome": e.nome, "cpf_cnpj": e.cpf_cnpj, **{k: res[k] for k in ("status", "contact_id")}})
        if res.get("contact_id"):
            participantes.append({"type": "OtherParty", "contactId": res["contact_id"],
                                  "isMainParticipant": False, "positionId": POSICAO_PARTE_CONTRARIA})
    resolucao["partes_contrarias"] = partes_report

    folder = resolver_proximo_folder(client, folder_incremento)
    if not folder:
        avisos.append("Não consegui calcular o próximo Proc (folder) — a API exige.")

    payload: dict[str, Any] = {
        "type": _config(db, "tipo") or "Judicial",
        "folder": folder,
        "title": processo.npj,
        "identifierNumber": processo.cnj,
        "statusId": status_id,
        "natureId": nature_id,
        "actionTypeId": action_id,
        "originOfficeId": origem_id,
        "responsibleOfficeId": office_id,
        "stateId": state_id,
        "cityId": city_id,
        "distributionDate": _data_iso(processo.data_ajuizamento),
        "notes": processo.observacao,  # ← gatilho do workflow no L1
        "monetaryAmount": {"value": valor, "code": "BRL"} if valor is not None else None,
        "customFields": [
            {"customFieldId": CF_NPJ, "textValue": processo.npj or ""},
            {"customFieldId": CF_DATA_TERCEIRIZACAO,
             "dateValue": _data_iso(processo.created_at.isoformat() if processo.created_at else None)
             or datetime.utcnow().strftime("%Y-%m-%dT00:00:00Z")},
        ],
        "participants": participantes,
    }
    # remove chaves None (o L1 não gosta de nulos em alguns campos)
    payload = {k: v for k, v in payload.items() if v is not None}

    resolucao.update({
        "folder": folder,
        "responsibleOfficeId": {"path": processo.escritorio_path, "id": office_id},
        "originOfficeId": origem_id,
        "natureId": {"nome": processo.natureza, "id": nature_id},
        "actionTypeId": {"nome": processo.acao, "id": action_id},
        "stateId": {"uf": uf, "id": state_id},
        "cityId": {"cidade": cidade, "id": city_id},
        "statusId": status_id,
        "valor_causa": valor,
        "observacao_notes": processo.observacao,
        "comarca_tramitacao": tram,
    })
    # Campos que faltaram (pra alertar no dry-run)
    faltando = [nome for nome, val in [
        ("responsibleOfficeId", office_id), ("natureId", nature_id), ("actionTypeId", action_id),
        ("stateId", state_id), ("cityId", city_id), ("statusId", status_id),
    ] if val is None]
    if faltando:
        avisos.append("Não resolvidos: " + ", ".join(faltando))

    return {"payload": payload, "resolucao": resolucao, "avisos": avisos}
