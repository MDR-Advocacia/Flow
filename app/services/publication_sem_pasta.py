"""Fila de publicações SEM PASTA vinculada — identificar O QUE É e sanear a pasta.

Contexto (estudo de 02–03/09/2026, produção): ~400 publicações por dia útil
chegam do L1 sem processo vinculado e eram DESCARTADAS pela rotina noturna.
Entre elas estão os dois tipos mais críticos da operação — EMBARGOS À
EXECUÇÃO e AGRAVOS DE INSTRUMENTO — que nascem com número novo e por isso
nunca casam com a nossa base pelo CNJ. Em 91% dessas publicações o advogado
da banca está no texto: a causa é nossa, o que falta é a PASTA.

Decisão do operador (03/09/2026): DOIS MOTORES. O de publicações fica
intocado; esta fila tem MOTOR PRÓPRIO (`publication_sem_pasta_motor`), que
não classifica providência — classifica O CASO ("Embargos à Execução",
"Agravo de Instrumento", "Obrigação de Fazer"...), extrai a ficha de
cadastro e manda para a equipe criar/vincular a pasta. A partir daí a
publicação seguinte cai na linha de classificação normal.

Este módulo é a parte COMPARTILHADA entre o motor e a tela (constantes,
índice CNJ→pasta, contexto por publicação):

  1. O ESCRITÓRIO FICTÍCIO (`SEM_PASTA_OFFICE_ID = -1`): linha em
     `legal_one_offices` só para dar às publicações sem pasta uma área de
     templates e um card no hub. Id negativo de propósito: o L1 nunca emite,
     e o sync/vigia pulam ids negativos. A taxonomia NÃO vai para
     `classification_categories` — vive em código, no motor.
  2. O ÍNDICE CNJ→pasta e o CONTEXTO: todo CNJ citado é resolvido contra a
     base para o operador ver "cita 2 processos nossos: ..." — o que liga o
     agravo à pasta de origem (34 de 66 agravos da amostra religam assim).
     Pauta coletiva (21+ CNJs, ~177 mil caracteres cada, 75% do volume) é
     detectada por contagem e nunca vai para a IA.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── identidade do escritório fictício ────────────────────────────────
SEM_PASTA_OFFICE_ID = -1
SEM_PASTA_POLO = "sem_pasta"
SEM_PASTA_OFFICE_NAME = "Publicações sem pasta"
SEM_PASTA_OFFICE_PATH = "MDR Advocacia / Área operacional / Publicações sem pasta"

# ── settings (app_settings) ──────────────────────────────────────────
# Liga a captura noturna das publicações sem pasta (default DESLIGADA: são
# ~400/dia e a equipe precisa combinar quem trata antes de abrir a torneira).
SETTING_CAPTURA_NOTURNA = "publicacoes_capturar_sem_pasta"
# Escritório REAL do L1 que recebe a tarefa de saneamento (o fictício não
# existe lá). Default 1 = raiz "MDR Advocacia": a pasta ainda não tem dono.
SETTING_OFFICE_L1_TAREFA = "publicacoes_sem_pasta_office_l1"
OFFICE_L1_TAREFA_DEFAULT = 1

# Lista com 21+ CNJs é pauta de sessão, não publicação de um processo.
# Corte do estudo: 1.193 das 1.602 sem pasta tinham 21+ (todas pautas).
LIMIAR_PAUTA_COLETIVA = 21

CATEGORIA_PAUTA = "Pauta de Julgamento (lista coletiva)"
CATEGORIA_RESIDUAL = "Para Análise"

# A taxonomia (TIPOS) vive no MOTOR: app/services/publication_sem_pasta_motor.py.
# Aqui ficam só os nomes que a regra de pauta e o contexto precisam.

# ── quem é o nosso cliente no processo ───────────────────────────────
# Detecção DETERMINÍSTICA, por leitura do texto. Existe separada da IA de
# propósito: a confiança auto-declarada pelo modelo já se mostrou imprestável
# (medição do pub010), então a confiabilidade que vai para a tela é montada de
# evidência verificável — nome no texto, papel processual, advogado da casa
# colado no nome, e o cruzamento com a nossa própria base de pastas.
#
# Medido na amostra de produção (409 individuais, 03/09/2026):
#   252 (62%) citam EXATAMENTE UM cliente da carteira  → sinal limpo
#    65 (16%) citam DOIS OU MAIS                        → ambíguo, precisa desempate
#    92 (22%) não citam nenhum                          → a leitura não resolve
#   257 (63%) trazem o advogado da casa logo depois do nome do cliente
#   192 mostram o cliente em papel passivo, 51 em papel ativo

# nome canônico → regex das variações como aparecem no diário.
CLIENTES_CARTEIRA: dict[str, str] = {
    "Banco do Brasil": r"banco\s+do\s+brasil",
    "Banco Master": r"banco\s+master",
    "Ativos": r"ativos\s+s\.?\s*[/.]?\s*a\b|ativos\s+securitiz\w*",
    "Banese": r"banese|banco\s+do\s+estado\s+de\s+sergipe",
    "Bradesco": r"bradesco",
    "Santander": r"santander",
}

# Advogados da casa cujo nome faz a publicação chegar até nós. O sócio
# responde por 91% das capturas (medido); a OAB dele entra como reforço.
ADVOGADOS_DA_CASA = r"marcos\s+delli|RN\s*0?5553"

# Rótulos processuais imediatamente antes do nome da parte.
_PAPEL_PASSIVO = r"embargad|agravad|apelad|requerid|reclamad|r[ée]u|executad|impugnad|promovid|recorrid"
_PAPEL_ATIVO = r"exequent|autor|requerent|embargant|agravant|apelant|credor|reclamant|recorrent|promovent"

# Janela em que o advogado ainda conta como "advogado DESTE cliente".
_JANELA_ADVOGADO = 250


def detectar_clientes(texto: Optional[str]) -> list[dict[str, Any]]:
    """Clientes da carteira citados no texto, com a evidência de cada um.

    Para cada um devolve o papel processual (pelo rótulo que vem antes) e se
    o advogado da casa aparece logo depois do nome — que é o que separa "o
    banco é citado" de "o banco é nosso constituinte neste processo"."""
    if not texto:
        return []
    achados: list[dict[str, Any]] = []
    for nome, padrao in CLIENTES_CARTEIRA.items():
        m = re.search(padrao, texto, re.IGNORECASE)
        if not m:
            continue
        ini, fim = m.span()
        antes = texto[max(0, ini - 60):ini]
        depois = texto[fim:fim + _JANELA_ADVOGADO]
        if re.search(_PAPEL_PASSIVO, antes, re.IGNORECASE):
            papel = "passivo"
        elif re.search(_PAPEL_ATIVO, antes, re.IGNORECASE):
            papel = "ativo"
        else:
            papel = None
        achados.append({
            "cliente": nome,
            "papel": papel,
            "advogado_da_casa": bool(re.search(ADVOGADOS_DA_CASA, depois, re.IGNORECASE)),
            "trecho": re.sub(r"\s+", " ", texto[max(0, ini - 60):fim + 80]).strip(),
        })
    return achados


def cliente_do_caminho(office_path: Optional[str]) -> Optional[str]:
    """Carteira a partir do caminho do escritório da pasta.

    "MDR Advocacia / Área operacional / Banco Master / Réu" → "Banco Master".
    Devolve None para escritórios que não são de um cliente da carteira
    (ex.: "Recuperação de Honorários", onde o cliente varia)."""
    if not office_path:
        return None
    for nome, padrao in CLIENTES_CARTEIRA.items():
        for parte in office_path.split("/"):
            if re.fullmatch(rf"\s*{padrao}\s*", parte, re.IGNORECASE):
                return nome
    return None


# ── rito: justiça comum, juizado especial ou trabalhista ─────────────
# Muda o jogo do tratamento (recurso inominado x apelação, custas, prazos),
# então vale dizer no card. Duas fontes, medidas em produção (03/09/2026,
# 409 individuais): o TEXTO resolve 237 (58%) — 42 juizado, 175 comum, 38
# trabalhista, com só 5 casos citando os dois; os outros 172 (42%) ficam
# mudos e caem no DataJud, que devolve `orgaoJulgador` e `classe`
# ("2ª TURMA RECURSAL - JUIZADOS ESPECIAIS" x "14ª VARA CÍVEL").
RITO_JUIZADO = "juizado"
RITO_COMUM = "comum"
RITO_TRABALHISTA = "trabalhista"

_RE_JUIZADO = (
    r"juizado especial|turma recursal|\bJEC\b|recurso inominado|juizado c[ií]vel|"
    r"lei\s*9\.?099|justi[çc]a\s*4\.0|colégio recursal|colegio recursal"
)
_RE_TRABALHISTA = r"vara do trabalho|justi[çc]a do trabalho|\bTRT\b|\bATOrd\b|reclama[çc][ãa]o trabalhista"
_RE_COMUM = (
    r"vara c[ií]vel|vara [úu]nica|vara empresarial|vara de fam[ íi]|vara da fazenda|"
    r"c[âa]mara c[ií]vel|vara federal|vara de execu|vara cumulativa|"
    r"tribunal de justi[çc]a|tribunal regional federal|"
    # "desembargador" e "câmara" só existem na justiça comum — na turma
    # recursal quem relata é juiz. Medido: +21 casos de cobertura (235→256
    # das 409 individuais). Os 3 textos que citam desembargador E juizado
    # caem em juizado, porque o juizado é testado antes.
    r"desembargador|\bc[âa]mara\b"
)

ROTULO_RITO = {
    RITO_JUIZADO: "Juizado Especial",
    RITO_COMUM: "Justiça Comum",
    RITO_TRABALHISTA: "Justiça do Trabalho",
}


def _primeiro_trecho(texto: str, padrao: str) -> Optional[str]:
    m = re.search(padrao, texto, re.IGNORECASE)
    if not m:
        return None
    ini = max(0, m.start() - 45)
    return re.sub(r"\s+", " ", texto[ini:m.end() + 45]).strip()


def detectar_rito(texto: Optional[str]) -> dict[str, Any]:
    """Rito pelo TEXTO. `rito=None` = o texto não disse (aí o DataJud entra).

    Trabalhista vence, depois juizado, depois comum: uma publicação de turma
    recursal costuma citar "Tribunal de Justiça" no cabeçalho, e o sinal mais
    específico é que manda.
    """
    t = texto or ""
    if not t.strip():
        return {"rito": None, "fonte": None, "evidencia": None}
    for rito, padrao in (
        (RITO_TRABALHISTA, _RE_TRABALHISTA),
        (RITO_JUIZADO, _RE_JUIZADO),
        (RITO_COMUM, _RE_COMUM),
    ):
        trecho = _primeiro_trecho(t, padrao)
        if trecho:
            return {"rito": rito, "fonte": "texto", "evidencia": f'"…{trecho}…"'}
    return {"rito": None, "fonte": None, "evidencia": None}


def rito_do_orgao(orgao: Optional[str], classe: Optional[str] = None) -> Optional[str]:
    """Rito a partir do órgão julgador / classe que o DataJud devolve."""
    alvo = f"{orgao or ''} {classe or ''}"
    if not alvo.strip():
        return None
    if re.search(_RE_TRABALHISTA, alvo, re.IGNORECASE):
        return RITO_TRABALHISTA
    if re.search(_RE_JUIZADO, alvo, re.IGNORECASE):
        return RITO_JUIZADO
    if re.search(_RE_COMUM, alvo, re.IGNORECASE):
        return RITO_COMUM
    return None


# ── extração de CNJ ──────────────────────────────────────────────────
_CNJ_FORMATADO_RE = re.compile(r"\b(\d{7})-(\d{2})\.(\d{4})\.(\d)\.(\d{2})\.(\d{4})\b")
# PDFs com OCR sujo trazem espaço entre grupos.
_CNJ_FROUXO_RE = re.compile(
    r"\b(\d{7})\s*-\s*(\d{2})\s*\.\s*(\d{4})\s*\.\s*(\d)\s*\.\s*(\d{2})\s*\.\s*(\d{4})\b"
)


def _formatar(d: str) -> str:
    return f"{d[:7]}-{d[7:9]}.{d[9:13]}.{d[13]}.{d[14:16]}.{d[16:20]}"


def extrair_cnjs(texto: Optional[str]) -> list[str]:
    """Todos os CNJs do texto, no formato canônico, sem repetição e na ordem
    em que aparecem. O primeiro costuma ser o do cabeçalho (a publicação em
    si); os seguintes são origem, apenso ou — nas pautas — a lista inteira."""
    if not texto:
        return []
    vistos: set[str] = set()
    out: list[str] = []
    for rx in (_CNJ_FORMATADO_RE, _CNJ_FROUXO_RE):
        for m in rx.finditer(texto):
            digitos = "".join(m.groups())
            if len(digitos) != 20 or digitos[13] not in "123456789":
                continue
            if digitos in vistos:
                continue
            vistos.add(digitos)
            out.append(_formatar(digitos))
    return out


def contar_cnjs(texto: Optional[str]) -> int:
    return len(extrair_cnjs(texto))


# ── índice CNJ → pasta (base local) ──────────────────────────────────
# Uma carga por processo a cada 10 min em vez de uma query por publicação:
# o lote noturno resolve ~1.600 publicações e a pauta traz 700 CNJs cada.
_INDICE_TTL_S = 600.0
_INDICE_LOCK = threading.Lock()
_INDICE: dict[str, dict[str, Any]] = {}
_INDICE_AT = 0.0


def _carregar_indice(db: Session) -> dict[str, dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT lc.lawsuit_id,
                   lc.payload->>'identifierNumber' AS cnj,
                   oli.office_id,
                   o.path
              FROM lawsuit_cache lc
              JOIN office_lawsuit_index oli ON oli.lawsuit_id = lc.lawsuit_id
              LEFT JOIN legal_one_offices o ON o.external_id = oli.office_id
             WHERE lc.payload->>'identifierNumber' IS NOT NULL
            """
        )
    ).fetchall()
    idx: dict[str, dict[str, Any]] = {}
    for lawsuit_id, cnj, office_id, path in rows:
        digitos = re.sub(r"\D", "", cnj or "")
        if len(digitos) != 20:
            continue
        # Mesmo CNJ em duas pastas (raro, mas existe): fica a primeira e a
        # tela mostra a pasta; o operador confere no L1.
        idx.setdefault(digitos, {
            "lawsuit_id": int(lawsuit_id),
            "office_id": int(office_id) if office_id is not None else None,
            "office_path": path,
        })
    return idx


def indice_cnj_base(db: Session, forcar: bool = False) -> dict[str, dict[str, Any]]:
    """Índice {cnj_digitos: {lawsuit_id, office_id, office_path}} com cache.

    É PISO, não teto: o cache de pastas tem CNJ para ~69% do índice (45% no
    Master). "Não achou" significa "não reconheci", nunca "não é nosso"."""
    global _INDICE, _INDICE_AT
    agora = time.monotonic()
    if not forcar and _INDICE and (agora - _INDICE_AT) < _INDICE_TTL_S:
        return _INDICE
    with _INDICE_LOCK:
        if not forcar and _INDICE and (agora - _INDICE_AT) < _INDICE_TTL_S:
            return _INDICE
        try:
            _INDICE = _carregar_indice(db)
            _INDICE_AT = agora
            logger.info("Índice CNJ→pasta carregado: %s CNJs.", len(_INDICE))
        except Exception:  # noqa: BLE001
            logger.exception("Falha carregando índice CNJ→pasta (segue com o anterior).")
    return _INDICE


def resolver_cnjs_na_base(db: Session, cnjs: list[str]) -> list[dict[str, Any]]:
    """Quais dos CNJs citados são pastas nossas. Mantém a ordem do texto."""
    if not cnjs:
        return []
    idx = indice_cnj_base(db)
    out: list[dict[str, Any]] = []
    for cnj in cnjs:
        hit = idx.get(re.sub(r"\D", "", cnj))
        if hit:
            out.append({"cnj": cnj, **hit})
    return out


# ── contexto da publicação sem pasta ─────────────────────────────────
def montar_contexto(db: Session, texto: Optional[str]) -> dict[str, Any]:
    """O que dá para saber SEM IA: quantos CNJs, quais são nossos, se é pauta."""
    cnjs = extrair_cnjs(texto)
    nossos = resolver_cnjs_na_base(db, cnjs)
    return {
        "n_cnj": len(cnjs),
        # Nas pautas a lista passa de 700; guardamos o começo e os nossos.
        "cnjs": cnjs[:30],
        "nossos": nossos[:50],
        "pauta_coletiva": len(cnjs) >= LIMIAR_PAUTA_COLETIVA,
        "cnj_origem": None,
    }


def anexar_contexto(db: Session, rec: Any, cnj_origem: Optional[str] = None) -> dict[str, Any]:
    """Grava o contexto em `raw_relationships['_sem_pasta']` (JSON já
    existente — sem migration) e devolve o dict. `cnj_origem` vem da IA."""
    from sqlalchemy.orm.attributes import flag_modified

    ctx = montar_contexto(db, rec.description)
    if cnj_origem:
        ctx["cnj_origem"] = cnj_origem
        # Origem citada que É nossa: entra nos "nossos" mesmo que o texto a
        # traga num formato que a extração não pegou.
        if not any(x["cnj"] == cnj_origem for x in ctx["nossos"]):
            ctx["nossos"] = resolver_cnjs_na_base(db, [cnj_origem]) + ctx["nossos"]
    raw = rec.raw_relationships if isinstance(rec.raw_relationships, dict) else {}
    raw = dict(raw)
    raw["_sem_pasta"] = ctx
    rec.raw_relationships = raw
    flag_modified(rec, "raw_relationships")
    return ctx


def contexto_do_record(rec: Any) -> Optional[dict[str, Any]]:
    raw = getattr(rec, "raw_relationships", None)
    if isinstance(raw, dict):
        ctx = raw.get("_sem_pasta")
        return ctx if isinstance(ctx, dict) else None
    return None


# ── settings ─────────────────────────────────────────────────────────
def captura_noturna_ativa() -> bool:
    from app.services.app_settings import get_setting

    v = (get_setting(SETTING_CAPTURA_NOTURNA) or "false").strip().lower()
    return v in ("1", "true", "sim", "on", "yes")


def office_l1_para_tarefa() -> int:
    """Escritório REAL do L1 que recebe a tarefa de saneamento."""
    from app.services.app_settings import get_setting

    raw = get_setting(SETTING_OFFICE_L1_TAREFA)
    try:
        v = int(raw) if raw is not None else OFFICE_L1_TAREFA_DEFAULT
    except (TypeError, ValueError):
        v = OFFICE_L1_TAREFA_DEFAULT
    return v if v > 0 else OFFICE_L1_TAREFA_DEFAULT


def e_escritorio_ficticio(office_external_id: Optional[int]) -> bool:
    try:
        return office_external_id is not None and int(office_external_id) < 0
    except (TypeError, ValueError):
        return False
