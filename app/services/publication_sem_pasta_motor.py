"""MOTOR de classificação da fila SEM PASTA — separado do motor de publicações.

Decisão do operador (03/09/2026): DOIS motores. O de publicações, que já
existe, fica INTOCADO; este aqui é novo, roda em execução própria e trata
só publicação sem processo vinculado. Nada daqui é importado pelo motor
antigo, e o antigo só ganhou uma trava para não pegar o que agora é deste.

O objetivo também é outro. O motor antigo classifica a PUBLICAÇÃO para
propor a providência. Este classifica o CASO: diz O QUE É (embargos à
execução, agravo de instrumento, obrigação de fazer...), extrai a FICHA que
a equipe precisa para cadastrar/vincular a pasta e manda para um tratamento
especializado. Cadastrada a pasta, a publicação seguinte daquele processo
cai na fila comum — e este motor nunca mais a vê.

Por isso ele tem:
  - taxonomia PRÓPRIA, em código (TIPOS), fora de `classification_categories`;
  - prompt de identificação próprio + prompts de FICHA por tipo crítico;
  - regra que dispensa a IA: pauta coletiva (21+ CNJs) sai por regra —
    descartada, por decisão do operador ("pauta podemos jogar pro saco");
  - execução própria (`executar`), online e sequencial, com orçamento de
    tempo, registro em `publicacao_sem_pasta_runs` e isolamento de erro por
    publicação. Volume medido: ~136 individuais/dia útil, cabe folgado.

Medições que sustentam o desenho estão em `publication_sem_pasta` (contexto,
índice CNJ→pasta e regra de pauta, que este motor reaproveita).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
import unicodedata
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.services.publication_sem_pasta import (
    CATEGORIA_PAUTA,
    CATEGORIA_RESIDUAL,
    CLIENTES_CARTEIRA,
    ROTULO_RITO,
    anexar_contexto,
    cliente_do_caminho,
    detectar_clientes,
    detectar_rito,
    rito_do_orgao,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════ taxonomia ═══════════════════════════════
# Em CÓDIGO, não em tabela: é a taxonomia deste motor, e só dele. Ordem =
# importância operacional (os dois primeiros são o motivo desta fila existir).
TIPO_EMBARGOS_EXECUCAO = "Embargos à Execução"
TIPO_AGRAVO_INSTRUMENTO = "Agravo de Instrumento"
TIPO_OBRIGACAO_FAZER = "Obrigação de Fazer / Astreintes"
TIPO_CUMPRIMENTO = "Cumprimento de Sentença / Execução"
TIPO_NAO_E_DA_CARTEIRA = "Não é da carteira"

TIPOS: list[tuple[str, str]] = [
    (
        TIPO_EMBARGOS_EXECUCAO,
        "Ação incidental do executado contra uma execução nossa (número novo, "
        "vinculado à execução). Sinais: 'EMBARGOS À EXECUÇÃO', 'embargante'/"
        "'embargado', 'embargos do devedor/do executado'. Concentra-se em BB Autor.",
    ),
    (
        TIPO_AGRAVO_INSTRUMENTO,
        "Recurso contra decisão interlocutória, autuado no 2º grau com número "
        "próprio. Sinais: 'AGRAVO DE INSTRUMENTO', 'agravante'/'agravado', "
        "'processo originário'/'origem'. Aparece em todos os escritórios.",
    ),
    (
        TIPO_OBRIGACAO_FAZER,
        "Processo ou fase em que se exige do cliente uma OBRIGAÇÃO DE FAZER / NÃO "
        "FAZER, com ou sem multa diária (astreintes): intimação para cumprir, "
        "majoração de multa, execução da multa, descumprimento. Sinais: "
        "'[Obrigação de Fazer / Não Fazer]', 'astreinte', 'multa diária', 'multa "
        "cominatória', 'cumpra a obrigação'. Prioridade máxima: a multa corre.",
    ),
    (
        TIPO_CUMPRIMENTO,
        "Fase executiva ou execução autônoma SEM obrigação de fazer: intimação "
        "para pagamento, penhora, bloqueio, leilão, alvará, impugnação ao "
        "cumprimento, execução de título extrajudicial.",
    ),
    (
        "Agravo Interno / Regimental",
        "Agravo contra decisão monocrática do relator, julgado pelo colegiado "
        "do mesmo tribunal. Sinais: 'agravo interno', 'agravo regimental'.",
    ),
    (
        "Embargos de Declaração",
        "Pedido de esclarecimento de decisão (omissão, contradição, obscuridade, "
        "erro material), inclusive intimação para contrarrazoar embargos.",
    ),
    (
        "Embargos de Terceiro",
        "Terceiro defende bem constrito em execução ou cumprimento de sentença.",
    ),
    (
        "Recurso aos Tribunais Superiores",
        "Recurso especial, extraordinário, agravo em REsp/RE (AREsp/ARE), juízo "
        "de admissibilidade, decisão de presidência/vice-presidência de tribunal.",
    ),
    (
        "Apelação",
        "Recurso contra sentença: interposição, contrarrazões, julgamento da "
        "apelação (acórdão) quando o texto se refere a UM processo.",
    ),
    (
        "Ação de Conhecimento (1º grau)",
        "Processo de conhecimento em 1ª instância sem enquadramento acima: "
        "citação, despacho, decisão, sentença, audiência.",
    ),
    (
        "Juizado Especial / Recurso Inominado",
        "Rito dos juizados: recurso inominado, turma recursal, procedimento do "
        "juizado especial cível.",
    ),
    (
        "Trabalhista",
        "Justiça do Trabalho: reclamação trabalhista, ATOrd, RO, AIRR, execução "
        "trabalhista.",
    ),
    (
        "Ação Autônoma (MS, Reclamação, Rescisória)",
        "Mandado de segurança, reclamação constitucional, ação rescisória, "
        "habeas corpus e outras ações autônomas de impugnação.",
    ),
    (
        CATEGORIA_PAUTA,
        "Lista de sessão de julgamento com dezenas de processos. Sai por REGRA "
        "(21+ CNJs), sem IA.",
    ),
    (
        TIPO_NAO_E_DA_CARTEIRA,
        "Nenhum cliente da carteira (Banco do Brasil, Banco Master, Ativos, "
        "Banese, Bradesco, Santander) é parte; o advogado aparece por homonímia "
        "ou em causa alheia. O operador confirma antes de descartar.",
    ),
    (
        CATEGORIA_RESIDUAL,
        "Texto insuficiente para dizer o que é.",
    ),
]
NOMES_TIPOS: list[str] = [nome for nome, _ in TIPOS]
DESCRICAO_TIPO: dict[str, str] = dict(TIPOS)

# Tipos que ganham FICHA (segunda chamada, prompt específico): são os que a
# equipe vai CADASTRAR, então a IA já traz os dados do cadastro.
TIPOS_COM_FICHA: frozenset[str] = frozenset({
    TIPO_EMBARGOS_EXECUCAO,
    TIPO_AGRAVO_INSTRUMENTO,
    TIPO_OBRIGACAO_FAZER,
    TIPO_CUMPRIMENTO,
})


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


_INDICE_TIPOS: dict[str, str] = {_norm(n): n for n in NOMES_TIPOS}


def normalizar_tipo(nome: Optional[str]) -> str:
    """Nome canônico do tipo; acento/caixa não importam. Fora da lista →
    residual (a IA NUNCA cria tipo — é a garantia de que template casa)."""
    if not nome:
        return CATEGORIA_RESIDUAL
    return _INDICE_TIPOS.get(_norm(nome), CATEGORIA_RESIDUAL)


def arvore_para_ui() -> dict[str, list[str]]:
    """Formato que a UI de templates e o filtro da triagem consomem."""
    return {nome: [] for nome in NOMES_TIPOS}


# ═══════════════════════════════ settings ════════════════════════════════
# "Pauta podemos jogar pro saco" (operador, 03/09/2026). Fica atrás de um
# setting porque é uma decisão reversível — e as pautas citam processos
# nossos (69 CNJs do Master na amostra) que nunca recebem a pauta pela pasta.
SETTING_DESCARTAR_PAUTA = "publicacoes_sem_pasta_descartar_pauta"


def descartar_pauta_ativo() -> bool:
    from app.services.app_settings import get_setting

    v = (get_setting(SETTING_DESCARTAR_PAUTA) or "true").strip().lower()
    return v in ("1", "true", "sim", "on", "yes")


# ═══════════════════════════ chamada à IA ════════════════════════════════
# Cliente PRÓPRIO, de propósito: o do motor antigo valida a resposta como
# classificação — exige "categoria" — e recusaria a FICHA, que não tem esse
# campo. Mesmo endpoint, mesmos headers, mesmo cache de prompt (breakpoint
# no fim do system, que é o prefixo estável); só sem o contrato dele.
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"


class ClienteIA:
    """Pergunta → JSON. Retry em 429; erro HTTP levanta."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ):
        from app.core.config import settings

        self.api_key = api_key or settings.anthropic_api_key
        self.model = model or settings.classifier_model
        self.max_tokens = max_tokens or settings.classifier_max_tokens
        self.cache = bool(getattr(settings, "classifier_prompt_cache_enabled", False))
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY não configurada.")

    async def perguntar(self, system: str, user: str) -> Any:
        import httpx

        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": 0,
            "system": (
                [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
                if self.cache else system
            ),
            "messages": [{"role": "user", "content": user}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            for tentativa in range(4):
                resp = await client.post(_ANTHROPIC_URL, headers=headers, json=payload)
                if resp.status_code == 429:
                    try:
                        espera = int(resp.headers.get("retry-after") or 0) + 1
                    except ValueError:
                        espera = 0
                    espera = max(espera, 5 * (tentativa + 1))
                    logger.warning("Sem pasta: 429 da Anthropic, esperando %ss.", espera)
                    await asyncio.sleep(espera)
                    continue
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"Anthropic HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                data = resp.json()
                blocos = data.get("content") or []
                texto = "".join(b.get("text", "") for b in blocos if b.get("type") == "text")
                u = data.get("usage") or {}
                if u:
                    logger.info(
                        "Sem pasta — usage: entrada=%s saída=%s cache_leitura=%s cache_gravação=%s",
                        u.get("input_tokens"), u.get("output_tokens"),
                        u.get("cache_read_input_tokens", 0),
                        u.get("cache_creation_input_tokens", 0),
                    )
                return _extrair_json(texto)
        raise RuntimeError("Anthropic: rate limit persistente após 4 tentativas.")


def _extrair_json(texto: str) -> Any:
    """JSON da resposta, tolerando cerca de código e texto em volta."""
    t = (texto or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except (ValueError, TypeError):
        pass
    inicios = [i for i in (t.find("{"), t.find("[")) if i >= 0]
    if not inicios:
        raise ValueError(f"Resposta sem JSON: {t[:120]!r}")
    ini = min(inicios)
    fim = max(t.rfind("}"), t.rfind("]"))
    return json.loads(t[ini:fim + 1])


# ═══════════════════════════════ prompts ═════════════════════════════════
_CLIENTES = "Banco do Brasil, Banco Master, Ativos, Banese, Bradesco e Santander"


def _lista_tipos_texto() -> str:
    return "\n".join(f'- "{nome}" — {desc}' for nome, desc in TIPOS)


_PROMPT_IDENTIFICACAO: Optional[str] = None


def prompt_identificacao() -> str:
    """System prompt da identificação. Estável (bom para cache de prompt)."""
    global _PROMPT_IDENTIFICACAO
    if _PROMPT_IDENTIFICACAO:
        return _PROMPT_IDENTIFICACAO

    # Pergunta "de quem é o ato" reaproveitada do fluxo normal (pub010): mesmo
    # texto, mesmo vocabulário. Import de leitura — não altera o módulo de lá.
    try:
        from app.services.classifier.prompts import QUEM_PRATICA_ATO_ADDENDUM
    except Exception:  # noqa: BLE001
        QUEM_PRATICA_ATO_ADDENDUM = ""

    _PROMPT_IDENTIFICACAO = f"""Você identifica O QUE É uma publicação judicial que chegou ao escritório SEM pasta de processo vinculada no sistema.

NÃO decida providência, nem prazo, nem responsável: a equipe vai criar ou vincular a pasta, e a publicação seguinte desse processo cairá no fluxo normal. Sua tarefa é dizer o TIPO de processo/recurso e extrair os números que permitem localizar a pasta de origem.

Contexto que você deve saber:
- Essas publicações chegaram porque o nome de um advogado do escritório aparece no texto. Em geral a causa É nossa; o que falta é a pasta.
- {_CLIENTES} são CLIENTES do escritório — nós os representamos. NUNCA os descreva como "parte contrária" ou "adversa"; a parte contrária é quem litiga contra eles.
- Os tipos MAIS IMPORTANTES são "{TIPO_EMBARGOS_EXECUCAO}", "{TIPO_AGRAVO_INSTRUMENTO}" e "{TIPO_OBRIGACAO_FAZER}". Os dois primeiros nascem com número novo ligado a um processo que já temos; o terceiro tem multa correndo. Havendo sinal textual, prefira-os.
- Uma publicação de agravo costuma citar o processo de origem ("PROCESSO ORIGINÁRIO", "origem", "autos principais"). Uma de embargos à execução costuma citar só o número dos embargos.
- "{TIPO_OBRIGACAO_FAZER}" vence "{TIPO_CUMPRIMENTO}" quando o texto fala em obrigação de fazer/não fazer, astreintes ou multa diária.

# TIPOS (campo "categoria" — use EXATAMENTE um destes nomes)
{_lista_tipos_texto()}

# CAMPOS A EXTRAIR
- "cnj_origem": o CNJ COMPLETO do processo de origem/principal quando o texto o citar (ex.: "PROCESSO ORIGINÁRIO: Nº 4002440-03.2026.8.26.0126"); senão null. NUNCA devolva o número da própria publicação como origem.
- "natureza_processo": nome livre do tipo processual (ex.: "Embargos à Execução", "Agravo de Instrumento").
- "cliente": qual cliente da carteira REPRESENTAMOS neste processo (nome curto: "Banco do Brasil", "Banco Master", "Ativos", "Banese", "Bradesco", "Santander"), ou null.
- "cliente_evidencia": o que no texto mostra que o representamos — papel processual dele e, se houver, o advogado do escritório constituído por ele. Cite o trecho. null quando não houver base.
- "polo": "ativo" se o cliente é autor/exequente/credor/embargado numa execução nossa; "passivo" se é réu/executado/requerido; "ambos" quando não der para dizer.
- "quem_pratica_ato" e "exige_providencia_nossa": conforme a seção abaixo.

# REGRAS
1. Responda EXCLUSIVAMENTE com um único objeto JSON válido, sem texto antes ou depois.
2. Campos: "categoria", "subcategoria" (sempre "-"), "polo", "cliente", "cliente_evidencia", "natureza_processo", "cnj_origem", "confianca" ("alta"|"media"|"baixa"), "justificativa" (uma frase; cite o trecho que revelou o tipo), "quem_pratica_ato", "exige_providencia_nossa".
3. **PRIMEIRO decida de quem é a causa, DEPOIS o tipo.** Se NENHUM dos clientes da carteira ({_CLIENTES}) é parte, a categoria é "{TIPO_NAO_E_DA_CARTEIRA}" — mesmo que o tipo processual seja óbvio, e mesmo que outro banco apareça. Atenção: "Banco Mercantil do Brasil", "Caixa Econômica Federal", "Itaú", "Bradescard" e afins NÃO são a nossa carteira; só valem os nomes listados acima. Nesse caso "cliente" é null. O operador confirma antes de descartar.
4. Só depois de confirmar que há cliente da carteira como parte, escolha o tipo processual.
5. Sem informação suficiente: "{CATEGORIA_RESIDUAL}".

# EXEMPLOS

Texto: "Poder Judiciário TJPA ... Classe e Assunto: EMBARGOS À EXECUÇÃO (172) - Efeito Suspensivo / Impugnação / Embargos à Execução EMBARGANTE: MELISSA ... EMBARGADO: BANCO DO BRASIL S.A. ... intimação para contrarrazões"
Resposta: {{"categoria": "{TIPO_EMBARGOS_EXECUCAO}", "subcategoria": "-", "polo": "ativo", "cliente": "Banco do Brasil", "cliente_evidencia": "BANCO DO BRASIL S.A. figura como EMBARGADO e o advogado do escritório está constituído por ele", "natureza_processo": "Embargos à Execução", "cnj_origem": null, "confianca": "alta", "justificativa": "Classe 'EMBARGOS À EXECUÇÃO' com Banco do Brasil como embargado — execução nossa", "quem_pratica_ato": "juizo_determina", "exige_providencia_nossa": true}}

Texto: "Agravo de Instrumento Nº 4081163-26.2026.8.26.0000/SP PROCESSO ORIGINÁRIO: Nº 4002440-03.2026.8.26.0126/SP RELATOR: ... AGRAVANTE: ALESSANDRO ... AGRAVADO: BANCO MASTER S/A ... Vistos. Recebo o agravo sem efeito suspensivo."
Resposta: {{"categoria": "{TIPO_AGRAVO_INSTRUMENTO}", "subcategoria": "-", "polo": "passivo", "cliente": "Banco Master", "cliente_evidencia": "BANCO MASTER S/A figura como AGRAVADO", "natureza_processo": "Agravo de Instrumento", "cnj_origem": "4002440-03.2026.8.26.0126", "confianca": "alta", "justificativa": "Cabeçalho 'Agravo de Instrumento' com processo originário citado; Banco Master é agravado", "quem_pratica_ato": "juizo_determina", "exige_providencia_nossa": null}}

Texto: "TJPA 1ª VARA CUMULATIVA DA COMARCA DE BREVES [Obrigação de Fazer / Não Fazer] 0001116-64.2011.8.14.0010 REQUERENTE: JOSE LIMA VIEIRA REQUERIDO: BANCO DO BRASIL ... intime-se o requerido para cumprir a obrigação no prazo de 15 dias, sob pena de multa diária de R$ 500,00"
Resposta: {{"categoria": "{TIPO_OBRIGACAO_FAZER}", "subcategoria": "-", "polo": "passivo", "cliente": "Banco do Brasil", "cliente_evidencia": "BANCO DO BRASIL S.A. figura como REQUERIDO e é quem foi intimado a cumprir", "natureza_processo": "Obrigação de Fazer / Não Fazer", "cnj_origem": null, "confianca": "alta", "justificativa": "Classe '[Obrigação de Fazer / Não Fazer]' com intimação para cumprir sob pena de multa diária; Banco do Brasil é requerido", "quem_pratica_ato": "juizo_determina", "exige_providencia_nossa": true}}

Texto: "TRT 7ª REGIÃO ... ATOrd 0000589-91.2023.5.07.0025 RECLAMANTE: LEILA ... RECLAMADO: MUNICIPIO DE CRATEUS E OUTROS ... intimado para ciência do despacho"
Resposta: {{"categoria": "{TIPO_NAO_E_DA_CARTEIRA}", "subcategoria": "-", "polo": "ambos", "cliente": null, "cliente_evidencia": null, "natureza_processo": "Reclamação Trabalhista", "cnj_origem": null, "confianca": "media", "justificativa": "Reclamação trabalhista contra município, sem cliente da carteira como parte", "quem_pratica_ato": "juizo_determina", "exige_providencia_nossa": false}}
"""
    _PROMPT_IDENTIFICACAO += QUEM_PRATICA_ATO_ADDENDUM
    return _PROMPT_IDENTIFICACAO


# Ficha por tipo: campos comuns + específicos. Quem lê é a pessoa que vai
# CADASTRAR a pasta, então os campos são os do cadastro (juízo, partes,
# cliente, polo) mais o que define a urgência daquele tipo.
_FICHA_COMUM: list[tuple[str, str]] = [
    ("cnj", "CNJ completo do processo desta publicação"),
    ("juizo", "vara/câmara/turma + comarca + UF, como no cabeçalho"),
    ("tribunal", "sigla do tribunal (TJPA, TRF3, TJSP...)"),
    ("cliente", "cliente da carteira que é parte (nome curto) ou null"),
    ("polo_cliente", "'ativo' ou 'passivo' — posição do cliente NESTA ação (ex.: embargado nos embargos = passivo, mesmo sendo exequente na execução de origem)"),
    ("parte_contraria", "nome da(s) parte(s) contrária(s) ao cliente"),
    ("ato", "o que esta publicação comunica, em até 12 palavras (ex.: 'intimação para contraminuta em 15 dias')"),
    ("prazo_mencionado", "prazo citado no texto, literal (ex.: '15 dias úteis'), ou null"),
    ("resumo", "uma frase para quem vai cadastrar a pasta entender o caso"),
]
FICHA_CAMPOS: dict[str, list[tuple[str, str]]] = {
    TIPO_AGRAVO_INSTRUMENTO: _FICHA_COMUM + [
        ("cnj_origem", "CNJ do processo originário (o de 1º grau), se citado"),
        ("agravante", "quem agrava"),
        ("agravado", "quem é agravado"),
        ("efeito_suspensivo", "'concedido', 'negado', 'não mencionado'"),
    ],
    TIPO_EMBARGOS_EXECUCAO: _FICHA_COMUM + [
        ("cnj_execucao", "CNJ da execução embargada, se citado (raro)"),
        ("embargante", "quem embarga (normalmente o executado)"),
        ("embargado", "quem é embargado (normalmente o exequente)"),
        ("valor_mencionado", "valor citado no texto, literal, ou null"),
    ],
    TIPO_OBRIGACAO_FAZER: _FICHA_COMUM + [
        ("obrigacao", "o que exatamente se exige que seja feito / não feito"),
        ("prazo_para_cumprir", "prazo para cumprir a obrigação, literal, ou null"),
        ("multa_diaria", "valor da multa diária/astreinte, literal, ou null"),
        ("situacao", "'intimação para cumprir', 'descumprimento apontado', 'majoração de multa', 'execução da multa' ou outra, em poucas palavras"),
    ],
    TIPO_CUMPRIMENTO: _FICHA_COMUM + [
        ("exequente", "quem executa"),
        ("executado", "quem é executado"),
        ("valor_mencionado", "valor citado no texto, literal, ou null"),
        ("fase", "'pagamento voluntário', 'penhora/bloqueio', 'leilão', 'impugnação', 'alvará' ou outra, em poucas palavras"),
    ],
}

_PROMPTS_FICHA: dict[str, str] = {}


def prompt_ficha(tipo: str) -> str:
    """System prompt da ficha do tipo. Um por tipo, estável (cacheável)."""
    if tipo in _PROMPTS_FICHA:
        return _PROMPTS_FICHA[tipo]
    campos = FICHA_CAMPOS[tipo]
    lista = "\n".join(f'- "{k}": {desc}' for k, desc in campos)
    texto = f"""Você monta a FICHA DE CADASTRO de um caso do tipo "{tipo}" a partir de uma publicação judicial. A publicação chegou ao escritório sem pasta de processo vinculada; quem vai ler a ficha é a pessoa que vai cadastrar (ou localizar) a pasta no sistema e encaminhar o caso.

{_CLIENTES} são CLIENTES do escritório — nós os representamos. Nunca os descreva como parte contrária.

Extraia SOMENTE o que está no texto. Não invente: campo ausente é null. Nomes de partes e números de processo devem ser copiados literalmente (CNJ no formato NNNNNNN-DD.AAAA.J.TR.OOOO).

# CAMPOS (responda um único objeto JSON com exatamente estas chaves)
{lista}

Responda EXCLUSIVAMENTE com o JSON, sem texto antes ou depois."""
    _PROMPTS_FICHA[tipo] = texto
    return texto


def _mensagem_usuario(rec: Any) -> str:
    texto = (rec.description or "").strip()
    # Pauta já saiu por regra; o resto é curto. Guarda contra texto anômalo.
    if len(texto) > 60_000:
        texto = texto[:60_000] + "\n[...texto truncado]"
    return f"Texto da publicação:\n{texto}"


# ═══════════════════════════ leitura da resposta ═════════════════════════
_VALID_POLO = {"ativo", "passivo", "ambos"}
_VALID_QUEM = {"nos_mesmos", "parte_adversa", "juizo_determina", "indeterminado"}
_VALID_CONF = {"alta", "media", "baixa"}


def _str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _cnj_canonico(v: Any) -> Optional[str]:
    d = re.sub(r"\D", "", str(v or ""))
    if len(d) != 20 or d[13] not in "123456789":
        return None
    return f"{d[:7]}-{d[7:9]}.{d[9:13]}.{d[13]}.{d[14:16]}.{d[16:20]}"


def _limpar_identificacao(payload: Any) -> dict[str, Any]:
    """Sanitiza o JSON da identificação. Nunca levanta por campo ruim: o que
    não bate no vocabulário vira None e a publicação segue classificável."""
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if not isinstance(payload, dict):
        payload = {}
    polo = (_str(payload.get("polo")) or "").lower()
    quem = (_str(payload.get("quem_pratica_ato")) or "").lower()
    conf = (_str(payload.get("confianca")) or "").lower()
    exige = payload.get("exige_providencia_nossa")
    return {
        "categoria": normalizar_tipo(_str(payload.get("categoria"))),
        "subcategoria": "-",
        "polo": polo if polo in _VALID_POLO else "ambos",
        "cliente": _str(payload.get("cliente")),
        "cliente_evidencia": _str(payload.get("cliente_evidencia")),
        "natureza_processo": _str(payload.get("natureza_processo")),
        "cnj_origem": _cnj_canonico(payload.get("cnj_origem")),
        "confianca": conf if conf in _VALID_CONF else "baixa",
        "justificativa": _str(payload.get("justificativa")) or "",
        "quem_pratica_ato": quem if quem in _VALID_QUEM else None,
        "exige_providencia_nossa": exige if isinstance(exige, bool) else None,
    }


def _limpar_ficha(payload: Any, tipo: str) -> dict[str, Any]:
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if not isinstance(payload, dict):
        payload = {}
    out: dict[str, Any] = {}
    for chave, _ in FICHA_CAMPOS[tipo]:
        v = _str(payload.get(chave))
        if chave.startswith("cnj"):
            v = _cnj_canonico(v) if v else None
        out[chave] = v
    return out


# ═══════════════════ quem representamos + confiabilidade ═════════════════
def _normalizar_cliente(nome: Optional[str]) -> Optional[str]:
    """Nome livre da IA → nome canônico da carteira (ou None)."""
    if not nome:
        return None
    alvo = _norm(nome)
    for canonico, padrao in CLIENTES_CARTEIRA.items():
        if _norm(canonico) in alvo or re.search(padrao, alvo, re.IGNORECASE):
            return canonico
    return None


def avaliar_cliente(
    texto: Optional[str],
    ctx: dict[str, Any],
    cliente_ia: Optional[str] = None,
    evidencia_ia: Optional[str] = None,
) -> dict[str, Any]:
    """Quem representamos no processo + o quanto dá para confiar nisso.

    A confiabilidade NÃO é a que o modelo declara — é montada de evidência
    verificável, porque a auto-confiança da IA já se mostrou imprestável
    (medição do pub010). Três fontes, em ordem de força:

      1. PASTA CITADA — um CNJ do texto bate com pasta nossa; o escritório
         dela diz a carteira. É a única fonte que não depende de leitura.
      2. NOME NO TEXTO — cliente da carteira nomeado, com o papel processual
         e o advogado da casa logo depois (63% dos casos trazem os dois).
      3. LEITURA DA IA — vale como desempate e como corroboração; sozinha,
         nunca passa de "baixa".

    O DataJud não entra: consultado em 03/09/2026 com CNJ real da amostra,
    ele devolve classe, assuntos, órgão julgador, movimentos e grau — e
    NENHUM campo de partes. Não serve para dizer quem representamos.
    """
    detectados = detectar_clientes(texto)
    por_nome = {d["cliente"] for d in detectados}

    # 1) o que a nossa base diz, pelos CNJs citados
    da_pasta: list[dict[str, Any]] = []
    for n in (ctx.get("nossos") or []):
        c = cliente_do_caminho(n.get("office_path"))
        if c:
            da_pasta.append({"cliente": c, "cnj": n["cnj"], "office_path": n["office_path"]})
    clientes_pasta = {d["cliente"] for d in da_pasta}

    da_ia = _normalizar_cliente(cliente_ia)
    evidencias: list[str] = []
    alternativas = sorted(por_nome | clientes_pasta | ({da_ia} if da_ia else set()))

    # ── decide o cliente e a confiança ──
    cliente: Optional[str]
    confianca: str
    fonte: str

    if len(clientes_pasta) == 1:
        cliente = next(iter(clientes_pasta))
        hit = da_pasta[0]
        evidencias.append(
            f"O processo {hit['cnj']} citado no texto é pasta nossa em {hit['office_path']}."
        )
        # Pasta + nome no texto concordando é o teto de certeza que temos.
        if cliente in por_nome:
            confianca, fonte = "alta", "pasta_citada+nome_no_texto"
            evidencias.append(f'"{cliente}" também aparece nomeado no texto.')
        elif por_nome and cliente not in por_nome:
            # A pasta diz um, o texto nomeia outro: não é erro necessariamente
            # (a contrária pode ser banco também), mas exige olho humano.
            confianca, fonte = "media", "pasta_citada_com_divergencia"
            evidencias.append(
                "Atenção: o texto nomeia " + ", ".join(sorted(por_nome)) + ", diferente da pasta."
            )
        else:
            confianca, fonte = "alta", "pasta_citada"
    elif len(clientes_pasta) > 1:
        cliente = None
        confianca, fonte = "baixa", "varias_pastas_citadas"
        evidencias.append(
            "O texto cita pastas nossas de mais de uma carteira: " + ", ".join(sorted(clientes_pasta)) + "."
        )
    elif len(por_nome) == 1:
        cliente = next(iter(por_nome))
        d = next(x for x in detectados if x["cliente"] == cliente)
        com_advogado = d["advogado_da_casa"]
        com_papel = bool(d["papel"])
        if com_advogado and com_papel:
            confianca, fonte = "alta", "nome_papel_advogado"
            evidencias.append(
                f'"{cliente}" aparece como parte ({d["papel"]}) e o advogado do escritório '
                "está logo em seguida, como constituído dele."
            )
        elif com_advogado or com_papel:
            confianca, fonte = "media", "nome_no_texto"
            evidencias.append(
                f'"{cliente}" aparece no texto'
                + (f" como parte ({d['papel']})" if com_papel else "")
                + (" com o advogado do escritório em seguida" if com_advogado else "")
                + ", mas sem os dois sinais juntos."
            )
        else:
            confianca, fonte = "baixa", "nome_solto"
            evidencias.append(
                f'"{cliente}" é citado no texto, mas sem papel processual claro nem o '
                "advogado do escritório em seguida — pode ser menção de passagem."
            )
        if d.get("trecho"):
            evidencias.append(f'Trecho: "…{d["trecho"]}…"')
    elif len(por_nome) > 1:
        # Ambíguo: dois bancos da carteira no mesmo texto (16% da amostra).
        # Deixa a IA desempatar, mas a confiança não passa de baixa.
        cliente = da_ia if da_ia in por_nome else None
        confianca, fonte = "baixa", "varios_clientes_no_texto"
        evidencias.append(
            "Mais de um cliente da carteira aparece no texto: " + ", ".join(sorted(por_nome)) + "."
        )
        if cliente:
            evidencias.append(f'A leitura da IA aponta "{cliente}" como o nosso.')
    elif da_ia:
        cliente = da_ia
        confianca, fonte = "baixa", "so_leitura_da_ia"
        evidencias.append(
            f'Nenhum cliente da carteira foi encontrado por busca literal; a IA leu "{da_ia}".'
        )
    else:
        cliente = None
        confianca, fonte = "nenhuma", "nao_identificado"
        evidencias.append("Nenhum cliente da carteira identificado no texto nem por pasta citada.")

    if evidencia_ia:
        evidencias.append(f"Leitura da IA: {evidencia_ia}")

    return {
        "cliente": cliente,
        "confianca": confianca,
        "fonte": fonte,
        "evidencias": evidencias,
        "alternativas": [a for a in alternativas if a != cliente],
        "cliente_ia": da_ia,
    }


def avaliar_rito(texto: Optional[str], cnjs: Optional[list[str]] = None) -> dict[str, Any]:
    """Justiça comum, juizado especial ou trabalhista — e de onde veio.

    O rito muda o tratamento (recurso inominado x apelação, custas, prazos),
    então o card diz qual é. Ordem das fontes:

      1. TEXTO — resolve 58% (medido em produção). Instantâneo e sem custo.
      2. DATAJUD — só quando o texto cala. Ele devolve `orgaoJulgador` e
         `classe`, que separam "2ª TURMA RECURSAL - JUIZADOS ESPECIAIS" de
         "14ª VARA CÍVEL". É best-effort: falha, 429 ou processo ausente
         apenas deixam o rito indefinido — nunca derrubam a classificação.

    (Vale contrastar: para saber QUEM representamos o DataJud não serve, pois
    não devolve partes. Para o rito, serve.)
    """
    achado = detectar_rito(texto)
    if achado["rito"]:
        return achado

    for cnj in (cnjs or [])[:1]:  # só o primeiro: é o do cabeçalho
        try:
            from app.services.citacoes_bm.datajud import get_client
            from app.services.citacoes_bm.tribunal_alias import cnj_digits, resolve_tribunal_alias

            alias = resolve_tribunal_alias(cnj)
            digitos = cnj_digits(cnj)
            if not alias or not digitos:
                continue
            resp = get_client().search_processes(
                alias, {"size": 1, "query": {"match": {"numeroProcesso": digitos}}},
            )
            hits = (resp.get("hits") or {}).get("hits") or []
            if not hits:
                continue
            src = hits[0].get("_source") or {}
            og = src.get("orgaoJulgador") or {}
            cl = src.get("classe") or {}
            nome_og = og.get("nome") if isinstance(og, dict) else og
            nome_cl = cl.get("nome") if isinstance(cl, dict) else cl
            rito = rito_do_orgao(nome_og, nome_cl)
            if rito:
                return {
                    "rito": rito,
                    "fonte": "datajud",
                    "evidencia": f"DataJud: {nome_og or '?'}"
                                 + (f" · classe {nome_cl}" if nome_cl else ""),
                }
        except Exception as exc:  # noqa: BLE001
            logger.info("Sem pasta: DataJud não resolveu o rito de %s (%s).", cnj, exc)

    return {"rito": None, "fonte": None, "evidencia": None}


# ═══════════════════════ classificação de um registro ════════════════════
def _gravar_sem_pasta(rec: Any, **campos: Any) -> None:
    """Mescla campos em raw_relationships['_sem_pasta'] (flag_modified incluso)."""
    from sqlalchemy.orm.attributes import flag_modified

    raw = dict(rec.raw_relationships) if isinstance(rec.raw_relationships, dict) else {}
    ctx = dict(raw.get("_sem_pasta") or {})
    ctx.update(campos)
    raw["_sem_pasta"] = ctx
    rec.raw_relationships = raw
    flag_modified(rec, "raw_relationships")


def _tratar_pauta(db: Session, rec: Any, ctx: dict[str, Any]) -> str:
    """Pauta coletiva: sem IA. Descartada (IGNORADO) quando o setting manda;
    senão fica CLASSIFICADA como pauta para o operador decidir."""
    from app.services.publication_search_service import (
        RECORD_STATUS_CLASSIFIED,
        RECORD_STATUS_IGNORED,
    )

    nossos = ctx.get("nossos") or []
    n = ctx.get("n_cnj", 0)
    if nossos:
        lista = ", ".join(x["cnj"] for x in nossos[:5]) + (", …" if len(nossos) > 5 else "")
        detalhe = f"cita {len(nossos)} processo(s) da nossa base: {lista}"
    else:
        detalhe = "nenhum CNJ reconhecido na nossa base (ela cobre ~70% das pastas)"
    justificativa = f"Lista coletiva de sessão com {n} processos; {detalhe}."

    rec.category = CATEGORIA_PAUTA
    rec.subcategory = "-"
    rec.polo = "ambos"
    rec.natureza_processo = "Pauta de Julgamento"
    rec.quem_pratica_ato = "juizo_determina"
    rec.classifications = [{
        "categoria": CATEGORIA_PAUTA, "subcategoria": "-", "polo": "ambos",
        "confianca": "alta", "justificativa": justificativa,
        "natureza_processo": "Pauta de Julgamento",
    }]
    _gravar_sem_pasta(rec, motor="regra", tipo=CATEGORIA_PAUTA)

    if descartar_pauta_ativo():
        agora = datetime.now(timezone.utc)
        rec.status = RECORD_STATUS_IGNORED
        rec.ignored_at = agora
        rec.ignored_by_name = "Motor sem pasta (regra: pauta coletiva)"
        rec.ignore_reason = "pauta_coletiva"
        rec.ignore_reason_note = justificativa[:2000]
        rec.updated_at = agora
        # CIÊNCIA no Legal One (decisão do operador, 03/09/2026): descartar
        # aqui não basta — a publicação continuaria na caixa do L1. Entra na
        # fila do Tratamento Web como "sem providência", que é para onde
        # IGNORADO já mapeia; o RPA de sempre dá a ciência. Volume: as pautas
        # são ~75% da fila (até 950 num dia de sessão), então isso engrossa a
        # fila do Tratamento Web — é o preço de limpar a caixa.
        _enfileirar_ciencia(db, rec)
        return "pauta_descartada"
    rec.status = RECORD_STATUS_CLASSIFIED
    return "pauta"


def _enfileirar_ciencia(db: Session, rec: Any) -> None:
    """Manda o registro para a fila do Tratamento Web (ciência no L1).

    Best-effort: falhar aqui não pode desfazer o descarte — a publicação já
    está resolvida no Flow; sem a ciência ela só continua aparecendo no L1,
    e a próxima rodada do motor a reenfileira."""
    try:
        from app.services.publication_treatment_service import PublicationTreatmentService

        PublicationTreatmentService(db).sync_item_from_record(rec, commit=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Sem pasta #%s: não consegui enfileirar a ciência no L1 (%s).", rec.id, exc,
        )


async def classificar_registro(db: Session, ai: Any, rec: Any) -> str:
    """Um registro: regra → identificação → ficha (tipos críticos).

    Devolve o que aconteceu: 'pauta_descartada' | 'pauta' | 'ia' | 'ficha'."""
    from app.services.publication_search_service import RECORD_STATUS_CLASSIFIED

    ctx = anexar_contexto(db, rec)
    if ctx.get("pauta_coletiva"):
        return _tratar_pauta(db, rec, ctx)

    ident = _limpar_identificacao(
        await ai.perguntar(prompt_identificacao(), _mensagem_usuario(rec))
    )
    tipo = ident["categoria"]

    rec.category = tipo
    rec.subcategory = "-"
    rec.polo = ident["polo"]
    rec.natureza_processo = ident["natureza_processo"]
    rec.quem_pratica_ato = ident["quem_pratica_ato"]
    rec.exige_providencia_nossa = ident["exige_providencia_nossa"]
    rec.classifications = [{
        "categoria": tipo, "subcategoria": "-", "polo": ident["polo"],
        "confianca": ident["confianca"], "justificativa": ident["justificativa"],
        "natureza_processo": ident["natureza_processo"],
        "quem_pratica_ato": ident["quem_pratica_ato"],
        "exige_providencia_nossa": ident["exige_providencia_nossa"],
    }]
    rec.status = RECORD_STATUS_CLASSIFIED
    # Origem que a IA leu entra no contexto (e vira "pasta nossa" se estiver
    # na base); cliente e motor ficam registrados.
    ctx = anexar_contexto(db, rec, cnj_origem=ident["cnj_origem"])
    # Quem representamos + o quanto dá pra confiar: evidência verificável
    # (pasta citada, nome no texto, papel, advogado da casa) cruzada com a
    # leitura da IA. É informação para o executor avaliar, não veredito.
    cliente_info = avaliar_cliente(
        rec.description, ctx,
        cliente_ia=ident["cliente"], evidencia_ia=ident["cliente_evidencia"],
    )
    rito_info = avaliar_rito(rec.description, ctx.get("cnjs"))
    if rito_info["rito"]:
        rito_info["rotulo"] = ROTULO_RITO.get(rito_info["rito"], rito_info["rito"])
    _gravar_sem_pasta(
        rec, motor="ia", tipo=tipo,
        cliente=cliente_info["cliente"], cliente_info=cliente_info,
        rito=rito_info,
    )

    if tipo not in TIPOS_COM_FICHA:
        return "ia"

    # Ficha: segunda chamada, prompt do tipo. Falha aqui NÃO desfaz a
    # identificação — a publicação já está classificada; a ficha é bônus.
    try:
        ficha = _limpar_ficha(
            await ai.perguntar(prompt_ficha(tipo), _mensagem_usuario(rec)), tipo
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Sem pasta #%s: ficha de %s falhou: %s", rec.id, tipo, exc)
        return "ia"
    _gravar_sem_pasta(rec, ficha=ficha)
    return "ficha"


# ═══════════════════════════════ execução ════════════════════════════════
def _coletar_pendentes(db: Session, limite: int) -> list[Any]:
    from app.models.publication_search import PublicationRecord
    from app.services.publication_search_service import RECORD_STATUS_NEW

    return (
        db.query(PublicationRecord)
        .filter(PublicationRecord.status == RECORD_STATUS_NEW)
        .filter(PublicationRecord.is_duplicate == False)  # noqa: E712
        .filter(PublicationRecord.linked_lawsuit_id.is_(None))
        .filter(PublicationRecord.description.isnot(None))
        .filter(PublicationRecord.description != "")
        .order_by(PublicationRecord.id)
        .limit(limite)
        .all()
    )


def _nova_run(db: Session, requested_by: str, automation_run_id: Optional[int], total: int) -> Any:
    from app.models.publication_sem_pasta import PublicacaoSemPastaRun

    run = PublicacaoSemPastaRun(
        requested_by=requested_by,
        automation_run_id=automation_run_id,
        status="running",
        total_alvo=total,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def executar(
    db: Session,
    *,
    limite: int = 500,
    orcamento_s: float = 1800.0,
    pausa_s: float = 1.0,
    requested_by: str = "manual",
    automation_run_id: Optional[int] = None,
    ai: Any = None,
    run: Any = None,
    on_progress: Any = None,
) -> dict[str, Any]:
    """Roda o motor sobre as publicações NOVAS sem pasta.

    Sequencial e online (não usa a Batches API): o volume (~136/dia) não
    pede lote, e ficar fora da máquina de batches do motor antigo é parte da
    separação. Cada publicação é uma transação: erro em uma não derruba as
    outras nem a rodada. `orcamento_s` limita o tempo total — o que sobrar
    fica NOVO e sai na próxima execução.

    `on_progress(processados, total)` é chamado a cada 30s. Existe por causa
    do reaper da automação noturna: ele marca como órfã (e RETOMA) qualquer
    run sem heartbeat há 15 min. Sem bater esse sino, uma rodada longa desta
    fila faria a madrugada inteira reiniciar no meio."""
    ai = ai or ClienteIA()
    registros = _coletar_pendentes(db, limite)
    if run is None:
        run = _nova_run(db, requested_by, automation_run_id, len(registros))
    else:
        run.total_alvo = len(registros)
        db.commit()

    contadores = {
        "total_alvo": len(registros), "processados": 0, "pautas": 0,
        "classificados": 0, "fichas": 0, "erros": 0,
    }
    inicio = time.monotonic()
    ultimo_sinal = inicio

    def _bater_sino() -> None:
        """Heartbeat pra quem chamou (a automação noturna tem reaper de 15 min)."""
        nonlocal ultimo_sinal
        if on_progress is None:
            return
        agora = time.monotonic()
        if agora - ultimo_sinal < 30.0:
            return
        ultimo_sinal = agora
        try:
            on_progress(contadores["processados"], contadores["total_alvo"])
        except Exception:  # noqa: BLE001
            logger.warning("Sem pasta: callback de progresso falhou (ignorado).", exc_info=True)

    async def _rodar() -> None:
        for rec in registros:
            if time.monotonic() - inicio > orcamento_s:
                logger.info("Sem pasta: orçamento de tempo esgotado; o resto fica para a próxima.")
                break
            try:
                resultado = await classificar_registro(db, ai, rec)
                db.commit()
                if resultado.startswith("pauta"):
                    contadores["pautas"] += 1
                else:
                    contadores["classificados"] += 1
                    if resultado == "ficha":
                        contadores["fichas"] += 1
                    await asyncio.sleep(pausa_s)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                contadores["erros"] += 1
                run.ultimo_erro = f"#{rec.id}: {exc}"[:2000]
                logger.warning("Sem pasta #%s: falhou (%s) — segue NOVO.", rec.id, exc)
            contadores["processados"] += 1
            if contadores["processados"] % 5 == 0:
                _atualizar_run(db, run, contadores)
            _bater_sino()

    try:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_rodar())
        finally:
            loop.close()
        run.status = "done"
    except Exception as exc:  # noqa: BLE001
        logger.exception("Sem pasta: rodada abortou.")
        run.status = "failed"
        run.ultimo_erro = str(exc)[:2000]
        contadores["erro_fatal"] = str(exc)
    run.finished_at = datetime.now(timezone.utc)
    _atualizar_run(db, run, contadores)
    logger.info(
        "Sem pasta: rodada %s — %s alvo, %s pautas, %s classificadas (%s com ficha), %s erros.",
        run.id, contadores["total_alvo"], contadores["pautas"],
        contadores["classificados"], contadores["fichas"], contadores["erros"],
    )
    return {"run_id": run.id, **contadores}


def _atualizar_run(db: Session, run: Any, c: dict[str, Any]) -> None:
    run.processados = c["processados"]
    run.pautas = c["pautas"]
    run.classificados = c["classificados"]
    run.fichas = c["fichas"]
    run.erros = c["erros"]
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()


def iniciar_em_background(requested_by: str, limite: int = 500) -> dict[str, Any]:
    """Cria a run e dispara `executar` numa thread com sessão própria. O
    endpoint devolve na hora; a tela acompanha por `listar_runs`."""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        total = len(_coletar_pendentes(db, limite))
        run = _nova_run(db, requested_by, None, total)
        run_id = run.id
    finally:
        db.close()

    def _worker() -> None:
        from app.models.publication_sem_pasta import PublicacaoSemPastaRun

        sess = SessionLocal()
        try:
            r = sess.query(PublicacaoSemPastaRun).filter_by(id=run_id).first()
            executar(sess, limite=limite, requested_by=requested_by, run=r)
        except Exception:  # noqa: BLE001
            logger.exception("Sem pasta: worker em background falhou.")
        finally:
            sess.close()

    threading.Thread(target=_worker, name=f"sem-pasta-run-{run_id}", daemon=True).start()
    return {"run_id": run_id, "total_alvo": total}


def listar_runs(db: Session, limite: int = 5) -> list[dict[str, Any]]:
    from app.models.publication_sem_pasta import PublicacaoSemPastaRun

    rows = (
        db.query(PublicacaoSemPastaRun)
        .order_by(PublicacaoSemPastaRun.id.desc())
        .limit(limite)
        .all()
    )
    return [
        {
            "id": r.id,
            "status": r.status,
            "requested_by": r.requested_by,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "total_alvo": r.total_alvo,
            "processados": r.processados,
            "pautas": r.pautas,
            "classificados": r.classificados,
            "fichas": r.fichas,
            "erros": r.erros,
            "ultimo_erro": r.ultimo_erro,
        }
        for r in rows
    ]
