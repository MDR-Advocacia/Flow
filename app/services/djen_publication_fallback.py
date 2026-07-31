"""
Terceira contingência de captura: DJEN/Comunica (CNJ).

É a camada mais escondida da captura, acionada só quando as duas anteriores
falharam:

    1. API do Legal One (`GET /Updates`)      — primária
    2. Relatório gerado no L1 Web             — contingência
    3. DJEN/Comunica (ESTE módulo)            — última rede

A diferença que justifica existir: as camadas 1 e 2 dependem do Legal One (API
e site, respectivamente). Esta não depende do L1 para BUSCAR — a fonte é o
Diário de Justiça Eletrônico Nacional, consultado pela OAB do escritório.

## Herança do trabalho do Codex

O cliente HTTP (paginação, 429 com `Retry-After`, backoff em 5xx, validação do
status semântico, teto oficial de 10.000 resultados por consulta) vem do que o
Codex construiu em `feat/djen-fallback-codex`, que por sua vez derivou do
conector já validado no Lake. Essa parte é conhecimento de API duramente
adquirido e foi portada fielmente.

O que MUDOU em relação ao desenho dele:

- **Sem migration e sem mexer no search service.** O adaptador dele devolvia
  `"id": None` e ele reescreveu o `create_and_run_search` (mais as migrations
  pub007/pub008) para aceitar publicação sem id. Aqui o id é sintetizado
  negativo e determinístico — o mesmo mecanismo já em produção desde 30/07 pelo
  importador de planilha. A publicação entra pelo caminho existente, sem
  duplicar lógica.
- **Resolução da pasta é OFFLINE.** A dele montava a carteira baixando o
  relatório do L1 Web, o que reintroduz a dependência do Legal One justamente na
  camada que existe para sobreviver sem ele.

## Geo-bloqueio

A Comunica é pública mas bloqueia datacenter fora do Brasil: do IP da AWS
responde **403**. Exige `DJEN_PROXY` com saída brasileira — o mesmo MikroTik do
escritório usado pelo Lake e pelo OneLog. Medido em produção: direto 403, pelo
proxy 200.

## O CNJ não identifica a pasta sozinho

1.370 CNJs da base têm MAIS DE UM `lawsuit_id` (apensos e pastas duplicadas), e
o DJEN devolve só o CNJ — não a pasta. Onde a resolução é ambígua, a publicação
entra **sem processo vinculado** em vez de ser chutada numa pasta. Publicação na
pasta errada vira tarefa no processo errado; sem vínculo, vira trabalho de
triagem do operador, que é recuperável.
"""
from __future__ import annotations

import datetime
import logging
import re
import time
import unicodedata
from typing import Any, Iterable, Optional

import requests

logger = logging.getLogger(__name__)

DJEN_ORIGIN = "DJEN"

# Teto oficial de resultados por consulta de OAB. Acima disso o retorno é
# truncado em silêncio — precisa aparecer no log.
LIMITE_OFICIAL_CONSULTA = 10_000
ITENS_POR_PAGINA = 100

try:
    from zoneinfo import ZoneInfo

    _BRT = ZoneInfo("America/Sao_Paulo")
except Exception:  # pragma: no cover
    _BRT = None


class DjenIndisponivel(RuntimeError):
    """A Comunica não respondeu de forma aproveitável."""


def _agora() -> datetime.datetime:
    return datetime.datetime.now(tz=_BRT) if _BRT else datetime.datetime.now()


def janela_padrao(dias_atras: int = 1) -> tuple[datetime.date, datetime.date]:
    hoje = _agora().date()
    return hoje - datetime.timedelta(days=dias_atras), hoje


def so_digitos(valor: Any) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def formatar_cnj(digitos: str) -> str:
    d = so_digitos(digitos)
    if len(d) != 20:
        return d
    return f"{d[:7]}-{d[7:9]}.{d[9:13]}.{d[13]}.{d[14:16]}.{d[16:]}"


def texto_limpo(valor: Any) -> str:
    """Normaliza o corpo da publicação (o DJEN devolve HTML às vezes)."""
    import html as _html

    bruto = _html.unescape(str(valor or ""))
    bruto = re.sub(r"<br\s*/?>", "\n", bruto, flags=re.I)
    bruto = re.sub(r"<[^>]+>", " ", bruto)
    bruto = unicodedata.normalize("NFKC", bruto)
    bruto = re.sub(r"[ \t]+", " ", bruto)
    return re.sub(r"\n{3,}", "\n\n", bruto).strip()


def parse_oabs(bruto: str | None) -> list[tuple[str, str]]:
    """"5553:RN, 1234:BA" → [("5553","RN"), ("1234","BA")]."""
    saida: list[tuple[str, str]] = []
    for parte in str(bruto or "").split(","):
        parte = parte.strip()
        if not parte:
            continue
        if ":" not in parte:
            logger.warning("OAB em formato inesperado, ignorada: %r", parte)
            continue
        numero, uf = parte.split(":", 1)
        numero, uf = numero.strip(), uf.strip().upper()
        if numero and uf:
            saida.append((numero, uf))
    return saida


# ── Cliente da Comunica ────────────────────────────────────────────────
# Portado do trabalho do Codex (derivado do conector do Lake). O tratamento de
# 429/5xx e o teto de 10.000 são conhecimento de API — não simplificar.

class ComunicaClient:
    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        proxy: Optional[str] = None,
        timeout: Optional[int] = None,
        delay: Optional[float] = None,
        max_paginas: Optional[int] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        from app.core.config import settings

        self.base_url = (base_url or settings.comunica_base_url).rstrip("/")
        self.endpoint = f"{self.base_url}/api/v1/comunicacao"
        self.proxy = (proxy if proxy is not None else settings.djen_proxy) or ""
        self.timeout = int(timeout or settings.comunica_timeout_seconds or 30)
        self.delay = max(
            0.0,
            float(delay if delay is not None else settings.djen_request_delay_seconds),
        )
        self.max_paginas = max(1, int(max_paginas or settings.djen_max_pages or 200))
        self.session = session or requests.Session()
        if self.proxy:
            self.session.proxies.update({"http": self.proxy, "https": self.proxy})

    def _get(self, params: dict[str, Any], tentativas: int = 4) -> dict[str, Any]:
        ultimo_erro: Optional[BaseException] = None
        for tentativa in range(1, tentativas + 1):
            if self.delay:
                time.sleep(self.delay)
            try:
                r = self.session.get(self.endpoint, params=params, timeout=self.timeout)

                if r.status_code == 200:
                    corpo = r.json()
                    if not isinstance(corpo, dict):
                        raise DjenIndisponivel("DJEN devolveu JSON em formato inesperado.")
                    status = str(corpo.get("status") or "").strip().casefold()
                    if status and status not in {"success", "sucesso"}:
                        raise DjenIndisponivel(
                            f"DJEN devolveu HTTP 200 com status {corpo.get('status')!r}."
                        )
                    return corpo

                if r.status_code == 403:
                    # Assinatura do geo-bloqueio: a Comunica recusa IP de
                    # datacenter fora do Brasil. Sem proxy BR não há o que tentar.
                    raise DjenIndisponivel(
                        "DJEN respondeu 403 (geo-bloqueio). Confira o DJEN_PROXY "
                        "— sem saída brasileira a Comunica recusa a consulta."
                    )

                if r.status_code == 429 and tentativa < tentativas:
                    try:
                        espera = int(r.headers.get("Retry-After") or 0)
                    except (TypeError, ValueError):
                        espera = 0
                    # Sem Retry-After, a documentação oficial manda esperar 1 min.
                    time.sleep(max(0, espera) or 60)
                    continue

                if r.status_code in {500, 502, 503, 504} and tentativa < tentativas:
                    time.sleep((2 ** (tentativa - 1)) + 1)
                    continue

                raise DjenIndisponivel(
                    f"DJEN HTTP {r.status_code}: {(r.text or '').strip()[:300]}"
                )
            except DjenIndisponivel:
                raise
            except (requests.RequestException, ValueError) as exc:
                ultimo_erro = exc
                if tentativa >= tentativas:
                    break
                time.sleep(2 ** (tentativa - 1))

        raise DjenIndisponivel(
            f"DJEN indisponível após {tentativas} tentativas: {ultimo_erro}"
        )

    def buscar_por_oabs(
        self,
        *,
        oabs: Iterable[tuple[str, str]],
        data_de: datetime.date,
        data_ate: datetime.date,
        meio: str = "D",
    ) -> tuple[list[dict], dict]:
        itens: list[dict] = []
        paginas = 0
        truncadas: list[str] = []
        incompletas: list[str] = []

        for numero, uf in oabs:
            terminou = False
            for pagina in range(1, self.max_paginas + 1):
                corpo = self._get({
                    "numeroOab": numero,
                    "ufOab": uf,
                    "dataDisponibilizacaoInicio": data_de.isoformat(),
                    "dataDisponibilizacaoFim": data_ate.isoformat(),
                    "meio": meio,
                    "itensPorPagina": ITENS_POR_PAGINA,
                    "pagina": pagina,
                })
                paginas += 1
                if pagina == 1:
                    try:
                        total = int(corpo.get("count") or 0)
                    except (TypeError, ValueError):
                        total = 0
                    if total >= LIMITE_OFICIAL_CONSULTA:
                        truncadas.append(f"{numero}/{uf}")

                lote = corpo.get("items") or []
                if not isinstance(lote, list):
                    raise DjenIndisponivel(f"DJEN devolveu items inválido ({numero}/{uf}).")
                if not lote:
                    terminou = True
                    break
                itens.extend(x for x in lote if isinstance(x, dict))
                if len(lote) < ITENS_POR_PAGINA:
                    terminou = True
                    break
            if not terminou:
                incompletas.append(f"{numero}/{uf}")

        if incompletas:
            # Não levanta: melhor capturar o que veio e registrar o buraco do
            # que perder a rodada inteira nessa camada, que já é a última.
            logger.error(
                "DJEN: limite de paginação atingido sem cobrir o período em %s.",
                ", ".join(incompletas),
            )
        if truncadas:
            logger.warning(
                "DJEN: consulta atingiu o teto oficial de %s resultados em %s — "
                "pode haver publicação fora do retorno.",
                LIMITE_OFICIAL_CONSULTA, ", ".join(truncadas),
            )

        return itens, {
            "paginas": paginas,
            "brutos": len(itens),
            "oabs": [f"{n}/{u}" for n, u in oabs],
            "truncadas": truncadas,
            "incompletas": incompletas,
        }


# ── Resolução do processo, offline ─────────────────────────────────────

def mapa_cnj_para_processo(db) -> tuple[dict[str, int], set[str]]:
    """Monta CNJ → lawsuit_id a partir do que a base já sabe, sem rede.

    Devolve (mapa_sem_ambiguidade, cnjs_ambiguos).

    A fonte mais confiável é `publicacao_registros`, porque é o histórico da
    própria captura: o vínculo ali foi feito pelo L1. O `lawsuit_cache`
    complementa com o que já foi consultado por outros módulos.

    CNJ que aponta para mais de um `lawsuit_id` fica FORA do mapa — 1.370 CNJs
    da base estão nessa situação (apensos e pastas duplicadas), e chutar
    significaria criar tarefa no processo errado.
    """
    from sqlalchemy import func

    from app.models.lawsuit_cache import LawsuitCache
    from app.models.publication_search import PublicationRecord

    candidatos: dict[str, set[int]] = {}

    def _somar(cnj_bruto: Any, lawsuit_id: Any) -> None:
        d = so_digitos(cnj_bruto)
        if len(d) != 20 or lawsuit_id is None:
            return
        try:
            candidatos.setdefault(d, set()).add(int(lawsuit_id))
        except (TypeError, ValueError):
            return

    linhas = (
        db.query(PublicationRecord.linked_lawsuit_cnj, PublicationRecord.linked_lawsuit_id)
        .filter(PublicationRecord.linked_lawsuit_cnj.isnot(None))
        .filter(PublicationRecord.linked_lawsuit_id.isnot(None))
        .distinct()
        .all()
    )
    for cnj, lid in linhas:
        _somar(cnj, lid)

    for lid, payload in db.query(LawsuitCache.lawsuit_id, LawsuitCache.payload).all():
        if isinstance(payload, dict):
            _somar(payload.get("identifierNumber"), lid)

    mapa = {c: next(iter(v)) for c, v in candidatos.items() if len(v) == 1}
    ambiguos = {c for c, v in candidatos.items() if len(v) > 1}
    logger.info(
        "DJEN: mapa local com %s CNJs resolvidos e %s ambíguos (ficam sem vínculo).",
        len(mapa), len(ambiguos),
    )
    return mapa, ambiguos


def _office_por_lawsuit(db, lawsuit_ids: set[int]) -> dict[int, int]:
    """Escritório responsável de cada pasta, pelo que a base já tem."""
    from app.models.lawsuit_cache import LawsuitCache
    from app.models.publication_search import PublicationRecord

    saida: dict[int, int] = {}
    if not lawsuit_ids:
        return saida

    for lid, payload in (
        db.query(LawsuitCache.lawsuit_id, LawsuitCache.payload)
        .filter(LawsuitCache.lawsuit_id.in_(lawsuit_ids))
        .all()
    ):
        if isinstance(payload, dict) and payload.get("responsibleOfficeId") is not None:
            try:
                saida[int(lid)] = int(payload["responsibleOfficeId"])
            except (TypeError, ValueError):
                pass

    faltando = lawsuit_ids - set(saida)
    if faltando:
        for lid, oid in (
            db.query(PublicationRecord.linked_lawsuit_id, PublicationRecord.linked_office_id)
            .filter(PublicationRecord.linked_lawsuit_id.in_(faltando))
            .filter(PublicationRecord.linked_office_id.isnot(None))
            .distinct()
            .all()
        ):
            saida.setdefault(int(lid), int(oid))
    return saida


# ── Adaptação para o contrato do Legal One ─────────────────────────────

def adaptar(
    bruto: dict,
    *,
    lawsuit_id: Optional[int],
    office_id: Optional[int],
) -> Optional[dict]:
    """Converte uma comunicação do DJEN no dicionário que o pipeline consome."""
    texto = texto_limpo(bruto.get("texto"))
    if not texto:
        return None

    data = str(
        bruto.get("data_disponibilizacao") or bruto.get("dataDisponibilizacao") or ""
    )[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", data):
        return None

    digitos = so_digitos(bruto.get("numero_processo") or bruto.get("numeroprocessocommascara"))
    cabecalho = " · ".join(
        p for p in (
            str(bruto.get("tipoComunicacao") or "").strip(),
            str(bruto.get("siglaTribunal") or "").strip(),
            str(bruto.get("nomeOrgao") or "").strip(),
        ) if p
    ) or "Publicação DJEN"

    relacionamentos = (
        [{"linkType": "Litigation", "linkId": lawsuit_id}] if lawsuit_id else []
    )
    return {
        # `id` fica pendente: é atribuído em lote por `atribuir_ids`, que precisa
        # olhar o que já está gravado pra não colidir nem duplicar.
        "id": None,
        "originType": "OfficialJournalsCrawler",
        "typeId": 5,
        "description": texto,
        "notes": cabecalho,
        "date": f"{data}T00:00:00Z",
        "creationDate": f"{data}T00:00:00Z",
        "relationships": relacionamentos,
        "_cnj": formatar_cnj(digitos) if len(digitos) == 20 else None,
        "_responsible_office_id": office_id,
        "_lawsuit_creation_date": None,
        "_djen_id": str(bruto.get("hash") or bruto.get("id") or ""),
    }


def atribuir_ids(db, publicacoes: list[dict]) -> None:
    """Dá a cada publicação um `legal_one_update_id` sintético e estável.

    Reusa exatamente o mecanismo já validado em produção pelo importador de
    planilha: negativo (o id real do L1 é positivo) e determinístico, com desvio
    em caso de colisão pra publicação nunca sumir em silêncio.
    """
    from app.services.publication_spreadsheet_import import atribuir_update_ids

    linhas = [
        {
            "lawsuit_id": (p["relationships"][0]["linkId"] if p["relationships"] else 0),
            "publication_date": p["date"],
            "descricao": f"DJEN:{p.get('_djen_id')}:{p['description']}",
        }
        for p in publicacoes
    ]
    atribuir_update_ids(db, linhas)
    for pub, linha in zip(publicacoes, linhas):
        pub["id"] = linha["update_id"]


# ── Ponta a ponta ──────────────────────────────────────────────────────

def capturar_publicacoes(db, *, dias_atras: int = 1) -> dict[str, Any]:
    """Busca no DJEN e devolve publicações prontas pro `create_and_run_search`.

    Mesma assinatura da contingência por relatório, de propósito: o chamador
    trata as duas camadas do mesmo jeito.
    """
    from app.core.config import settings

    oabs = parse_oabs(settings.djen_oabs)
    if not oabs:
        return {"ok": False, "motivo": "sem_oab_configurada"}

    data_de, data_ate = janela_padrao(dias_atras)
    inicio = time.monotonic()

    try:
        cliente = ComunicaClient()
        if not cliente.proxy:
            logger.warning(
                "DJEN sem proxy configurado (DJEN_PROXY). Do IP da AWS a "
                "Comunica responde 403 — a consulta provavelmente vai falhar."
            )
        brutos, meta = cliente.buscar_por_oabs(
            oabs=oabs, data_de=data_de, data_ate=data_ate,
            meio=settings.djen_default_meio,
        )
    except DjenIndisponivel as exc:
        logger.error("DJEN indisponível: %s", exc)
        return {"ok": False, "motivo": "djen_indisponivel", "detalhe": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Falha inesperada consultando o DJEN.")
        return {"ok": False, "motivo": "erro_inesperado", "detalhe": str(exc)}

    if not brutos:
        return {"ok": False, "motivo": "djen_sem_resultado", "janela": (data_de, data_ate)}

    mapa, ambiguos = mapa_cnj_para_processo(db)
    lawsuit_ids = {
        mapa[d] for d in (so_digitos(b.get("numero_processo")) for b in brutos)
        if d in mapa
    }
    offices = _office_por_lawsuit(db, lawsuit_ids)

    publicacoes: list[dict] = []
    vinculadas = sem_vinculo = por_ambiguidade = descartadas = fora_carteira = 0
    for bruto in brutos:
        digitos = so_digitos(
            bruto.get("numero_processo") or bruto.get("numeroprocessocommascara")
        )
        lawsuit_id = mapa.get(digitos)
        conhecido = lawsuit_id is not None or digitos in ambiguos

        # A consulta por OAB traz TUDO que o advogado assina — um
        # superconjunto da carteira do L1. Medido em 31/07/2026: de 1.017
        # comunicações, 192 eram de processos que a base não conhece.
        #
        # Essas ficam de fora: o `/Updates` do L1, que é a fonte primária, é
        # escopado na carteira do tenant e também não as traria. Importá-las
        # encheria a fila de avulsas com publicação que ninguém aqui trata —
        # e contingência que vira ruído deixa de ser usada.
        if settings.djen_somente_carteira and not conhecido:
            fora_carteira += 1
            continue

        if lawsuit_id is None and digitos in ambiguos:
            por_ambiguidade += 1
        pub = adaptar(
            bruto,
            lawsuit_id=lawsuit_id,
            office_id=offices.get(lawsuit_id) if lawsuit_id else None,
        )
        if pub is None:
            descartadas += 1
            continue
        publicacoes.append(pub)
        if lawsuit_id:
            vinculadas += 1
        else:
            sem_vinculo += 1

    if not publicacoes:
        return {"ok": False, "motivo": "nada_aproveitavel", "brutos": len(brutos)}

    atribuir_ids(db, publicacoes)

    logger.info(
        "DJEN: %s publicações (%s com pasta, %s sem vínculo — %s por CNJ ambíguo), "
        "%s fora da carteira ignoradas, janela %s a %s, %s páginas, %.1fs.",
        len(publicacoes), vinculadas, sem_vinculo, por_ambiguidade,
        fora_carteira, data_de, data_ate, meta.get("paginas"),
        time.monotonic() - inicio,
    )
    return {
        "ok": True,
        "publicacoes": publicacoes,
        "total": len(publicacoes),
        "vinculadas": vinculadas,
        "sem_vinculo": sem_vinculo,
        "ambiguas": por_ambiguidade,
        "descartadas": descartadas,
        "fora_carteira": fora_carteira,
        "brutos": len(brutos),
        "data_inicio": data_de.strftime("%d/%m/%Y"),
        "data_fim": data_ate.strftime("%d/%m/%Y"),
        "segundos": round(time.monotonic() - inicio, 1),
        "meta": meta,
    }
