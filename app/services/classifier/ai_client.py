"""
Wrapper assíncrono para chamadas à API Anthropic.
Usa httpx diretamente (sem SDK extra) para manter o requirements enxuto.
"""

import asyncio
import json
import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_BATCHES_API_URL = "https://api.anthropic.com/v1/messages/batches"
ANTHROPIC_API_VERSION = "2023-06-01"

# Limite prático por publicação para economizar tokens e evitar 200K context
MAX_PUBLICATION_TEXT_CHARS = 8000


class AnthropicClassifierClient:
    """Cliente leve para chamadas de classificação via Anthropic Messages API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ):
        self.api_key = api_key or settings.anthropic_api_key
        self.model = model or settings.classifier_model
        self.max_tokens = max_tokens or settings.classifier_max_tokens
        if not self.api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY não configurada. "
                "Adicione ao .env ou passe como parâmetro."
            )

    def _build_headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
            "content-type": "application/json",
        }

    async def classify(self, system_prompt: str, user_message: str) -> dict[str, Any]:
        """
        Envia uma publicação para classificação e retorna o JSON parseado.
        Implementa retry com exponential backoff em caso de rate limit (429).

        Returns:
            dict com "categoria" e "subcategoria" (e opcionalmente "confianca").
        Raises:
            Exception em caso de erro de API ou resposta inválida.
        """
        # temperature=0 é determinístico — pra classificação contra taxonomia
        # fechada, não queremos a IA "criativa". Reduz reclassificações entre
        # rodadas e melhora aderência aos exemplos few-shot. Mantém o request
        # fora do PII de cache (caching depende só de system+model+tools).
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": 0,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_message},
            ],
        }

        max_retries = 3
        base_wait = 15  # segundos

        for attempt in range(max_retries + 1):
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    ANTHROPIC_API_URL,
                    headers=self._build_headers(),
                    json=payload,
                )

            # Trata rate limit com retry
            if response.status_code == 429:
                if attempt < max_retries:
                    retry_after = response.headers.get("retry-after")
                    if retry_after:
                        wait_time = int(retry_after) + 1
                    else:
                        # Exponential backoff: 15s, 30s, 60s
                        wait_time = base_wait * (2 ** attempt)

                    logger.warning(
                        "Rate limit (429) na tentativa %d/%d. Aguardando %ds...",
                        attempt + 1, max_retries + 1, wait_time,
                    )
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    error_body = response.text
                    logger.error(
                        "Rate limit persistente após %d tentativas. Erro: %s",
                        max_retries + 1, error_body[:500],
                    )
                    raise Exception(
                        f"Taxa limit da API Anthropic após {max_retries + 1} tentativas"
                    )

            # Outros status codes — não retry
            if response.status_code != 200:
                error_body = response.text
                logger.error(
                    "Anthropic API error %s: %s",
                    response.status_code,
                    error_body[:500],
                )
                raise Exception(
                    f"Erro na API Anthropic (HTTP {response.status_code}): {error_body[:200]}"
                )

            # Processa resposta bem-sucedida
            try:
                data = response.json()
                content_blocks = data.get("content", [])
                if not content_blocks:
                    raise Exception("Resposta da API sem conteúdo.")

                # Telemetria de token no caminho síncrono. Aqui só loga (o
                # retorno é contrato usado por vários callers e não vou mudá-lo
                # por causa de medição); o número que interessa pro estudo de
                # custo vem do batch, que é onde está o volume.
                u = data.get("usage") or {}
                if u:
                    logger.info(
                        "Classificação síncrona — usage: entrada=%s saída=%s "
                        "cache_leitura=%s cache_gravação=%s",
                        u.get("input_tokens"), u.get("output_tokens"),
                        u.get("cache_read_input_tokens", 0),
                        u.get("cache_creation_input_tokens", 0),
                    )

                raw_text = content_blocks[0].get("text", "")
                return self._parse_classification_response(raw_text)
            except Exception as exc:
                # Erros de parsing — não retry
                logger.error("Erro ao processar resposta da API: %s", exc)
                raise

    # ─────────────────────────────────────────────────────────────────────
    # Message Batches API (classificação assíncrona em volume)
    # https://docs.claude.com/en/docs/build-with-claude/batch-processing
    # ─────────────────────────────────────────────────────────────────────

    def build_batch_request(
        self,
        custom_id: str,
        system_prompt: str,
        user_message: str,
    ) -> dict[str, Any]:
        """
        Constrói um item individual de um batch, no formato esperado pela API.

        Args:
            custom_id: identificador do item dentro do batch (string única).
                        Vamos usar o ID do PublicationRecord.
            system_prompt: prompt de sistema.
            user_message: mensagem do usuário (processo + texto).

        Returns:
            dict pronto para ir na lista `requests` do batch.
        """
        return {
            "custom_id": custom_id,
            "params": {
                "model": self.model,
                "max_tokens": self.max_tokens,
                # Mesmo motivo da chamada síncrona — taxonomia fechada
                # pede determinismo.
                "temperature": 0,
                "system": system_prompt,
                "messages": [
                    {"role": "user", "content": user_message},
                ],
            },
        }

    async def submit_batch(self, requests: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Envia um lote de classificações para a Message Batches API.

        A API aceita até 100.000 requisições por batch (até 256MB).
        O processamento é assíncrono e os resultados ficam disponíveis
        em até 24 horas (geralmente minutos).

        Args:
            requests: lista de itens construídos via build_batch_request().

        Returns:
            dict com o payload completo da resposta, incluindo:
              - id: identificador do batch (ex: "msgbatch_...")
              - processing_status: "in_progress"
              - request_counts: {"processing": N, ...}
              - created_at, expires_at

        Raises:
            Exception em caso de erro de API.
        """
        if not requests:
            raise ValueError("Lista de requisições vazia.")

        if len(requests) > 100_000:
            raise ValueError(
                f"Batch excede o limite de 100.000 requisições (recebido: {len(requests)})"
            )

        payload = {"requests": requests}

        logger.info(
            "Enviando batch para Anthropic: %d requisições (modelo=%s)",
            len(requests),
            self.model,
        )

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                ANTHROPIC_BATCHES_API_URL,
                headers=self._build_headers(),
                json=payload,
            )

        if response.status_code not in (200, 201):
            error_body = response.text
            logger.error(
                "Erro ao criar batch Anthropic %s: %s",
                response.status_code,
                error_body[:500],
            )
            raise Exception(
                f"Erro ao criar batch (HTTP {response.status_code}): {error_body[:300]}"
            )

        data = response.json()
        logger.info(
            "Batch criado com sucesso: id=%s, status=%s",
            data.get("id"),
            data.get("processing_status"),
        )
        return data

    async def get_batch_status(self, batch_id: str) -> dict[str, Any]:
        """
        Consulta o status atual de um batch.

        Returns:
            dict com o payload completo:
              - id
              - processing_status: "in_progress" | "ended" | "canceling" | "canceled"
              - request_counts: {"processing": N, "succeeded": N, "errored": N, ...}
              - results_url: URL para baixar resultados (presente quando ended)
              - ended_at, created_at, expires_at
        """
        url = f"{ANTHROPIC_BATCHES_API_URL}/{batch_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=self._build_headers())

        if response.status_code != 200:
            error_body = response.text
            logger.error(
                "Erro ao consultar batch %s (HTTP %s): %s",
                batch_id,
                response.status_code,
                error_body[:500],
            )
            raise Exception(
                f"Erro ao consultar batch (HTTP {response.status_code}): {error_body[:200]}"
            )

        return response.json()

    async def get_batch_results(self, results_url: str) -> list[dict[str, Any]]:
        """
        Baixa e parseia os resultados de um batch finalizado.

        O formato de retorno da Anthropic é JSONL (uma linha por resultado).
        Cada linha contém:
          {
            "custom_id": "...",
            "result": {
              "type": "succeeded" | "errored" | "expired" | "canceled",
              "message": {...}  # quando succeeded
              "error": {...}    # quando errored
            }
          }

        Args:
            results_url: URL fornecida no campo results_url do batch ended.

        Returns:
            lista de dicts, um por item do batch.
        """
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.get(results_url, headers=self._build_headers())

        if response.status_code != 200:
            error_body = response.text
            logger.error(
                "Erro ao baixar resultados do batch (HTTP %s): %s",
                response.status_code,
                error_body[:500],
            )
            raise Exception(
                f"Erro ao baixar resultados (HTTP {response.status_code})"
            )

        # Resposta em JSONL (uma linha por resultado)
        results: list[dict[str, Any]] = []
        for line in response.text.strip().split("\n"):
            if not line.strip():
                continue
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError as exc:
                logger.warning("Linha inválida no JSONL: %s", line[:200])
                continue

        logger.info("Resultados do batch baixados: %d itens", len(results))
        return results

    @staticmethod
    def extract_classification_from_batch_result(
        batch_result: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Extrai a classificação de um item individual do resultado do batch.

        Args:
            batch_result: um dict da lista retornada por get_batch_results().

        Returns:
            dict com "categoria", "subcategoria", etc.

        Raises:
            Exception quando o item não foi processado com sucesso ou a
            resposta não é parseável.
        """
        result = batch_result.get("result", {})
        result_type = result.get("type")

        if result_type != "succeeded":
            error_info = result.get("error", {}) or {}
            msg = error_info.get("message") or f"type={result_type}"
            raise Exception(f"Item não processado: {msg}")

        message = result.get("message", {}) or {}
        content_blocks = message.get("content", [])
        if not content_blocks:
            raise Exception("Mensagem sem conteúdo.")

        raw_text = content_blocks[0].get("text", "")
        stop_reason = message.get("stop_reason")
        return AnthropicClassifierClient._parse_classification_response(
            raw_text, stop_reason=stop_reason
        )

    @staticmethod
    def extract_usage_from_batch_result(
        batch_result: dict[str, Any],
    ) -> dict[str, int]:
        """Contadores de token de UM item do batch. Nunca levanta exceção.

        Tolerante de propósito: usage é telemetria, não pode derrubar a
        aplicação de um lote que classificou certo. Item que falhou costuma vir
        sem `message`, e aí devolve tudo zerado — quem chama soma e conta
        quantos itens de fato trouxeram número.

        Os dois campos de cache só ficam diferentes de zero quando o prompt
        caching estiver ligado; hoje vêm 0 e é isso que queremos medir.
        """
        vazio = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
        try:
            usage = (
                (batch_result.get("result") or {}).get("message") or {}
            ).get("usage") or {}
            if not isinstance(usage, dict):
                return vazio
            return {k: int(usage.get(k) or 0) for k in vazio}
        except Exception:  # noqa: BLE001
            return vazio

    @staticmethod
    def _try_repair_truncated_array(text: str) -> list | None:
        """
        Quando `max_tokens` estoura no meio de um array JSON de classificações,
        tenta recuperar os objetos completos fechando o array no último '}' de
        profundidade raiz válido. Percorre o texto rastreando strings (com
        escape) e profundidade de brackets para não se confundir com '}' dentro
        de valores string (ex.: justificativa).
        Retorna a lista recuperada ou None se não houver nenhum objeto completo.
        """
        if not text.startswith("["):
            return None
        depth = 0
        in_string = False
        escape = False
        last_complete_end = -1  # índice do último '}' que fechou objeto no nível raiz

        for i, ch in enumerate(text):
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
                # depth == 1 após um '}' significa: acabou de fechar um objeto
                # raiz do array (o próprio array mantém depth=1 enquanto aberto)
                if ch == "}" and depth == 1:
                    last_complete_end = i

        if last_complete_end == -1:
            return None
        candidate = text[: last_complete_end + 1] + "]"
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _extract_first_json(text):
        """Extrai o primeiro objeto/array JSON BALANCEADO do texto, ignorando
        prosa antes ou depois. A IA as vezes anexa explicacao (ex.: um
        paragrafo 'OBSERVACAO CRITICA: ...' depois do JSON) — isso sobrava no
        json.loads e quebrava o parse. Rastreia strings/escapes pra nao fechar
        em chave dentro de string. Retorna a substring balanceada ou None se
        nao houver objeto/array fechado (ex.: truncado por max_tokens)."""
        start = -1
        for i, ch in enumerate(text):
            if ch == "{" or ch == "[":
                start = i
                break
        if start == -1:
            return None
        open_ch = text[start]
        close_ch = "}" if open_ch == "{" else "]"
        bs = chr(92)
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == bs:
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        return None

    @staticmethod
    def _parse_classification_response(
        raw_text: str, stop_reason: str | None = None
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """
        Extrai JSON da resposta, tolerando markdown code fences.
        Suporta tanto um objeto único quanto um array de classificações.
        Retorna sempre um dict (classificação principal) com campo opcional
        '_extra_classifications' contendo as classificações adicionais.

        Se `stop_reason == "max_tokens"` e a resposta for um array truncado,
        tenta recuperar as classificações que já vieram completas antes de
        desistir, reportando um erro claro caso não seja possível.
        """
        text = raw_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        # A IA as vezes anexa prosa (explicacao) antes/depois do JSON — ex.:
        # um paragrafo "OBSERVACAO CRITICA: ..." DEPOIS do objeto. Extrai o
        # primeiro objeto/array balanceado e ignora o resto, pra nao quebrar
        # o json.loads com texto sobrando. (fix 2026-06)
        _extracted = AnthropicClassifierClient._extract_first_json(text)
        if _extracted is not None:
            text = _extracted

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            # Tenta reparar array truncado por max_tokens
            if stop_reason == "max_tokens" or text.startswith("["):
                recovered = AnthropicClassifierClient._try_repair_truncated_array(text)
                if recovered:
                    logger.warning(
                        "Resposta truncada (stop_reason=%s) — recuperadas %d classificações do array.",
                        stop_reason, len(recovered),
                    )
                    parsed = recovered
                else:
                    logger.warning("Falha ao parsear resposta: %s", text[:300])
                    if stop_reason == "max_tokens":
                        raise Exception(
                            "Resposta truncada (max_tokens atingido) — "
                            "aumente classifier_max_tokens ou reduza o texto de entrada."
                        ) from exc
                    raise Exception(f"Resposta não é JSON válido: {text[:200]}") from exc
            else:
                logger.warning("Falha ao parsear resposta: %s", text[:300])
                raise Exception(f"Resposta não é JSON válido: {text[:200]}") from exc

        # Suporte a múltiplas classificações (array)
        if isinstance(parsed, list):
            if not parsed:
                raise Exception("Array de classificações vazio.")
            # Valida cada item
            for item in parsed:
                if "categoria" not in item:
                    raise Exception(f"Item sem campo 'categoria': {item}")
            # Primeira classificação é a principal; extras ficam em _extra
            primary = parsed[0]
            if len(parsed) > 1:
                primary["_extra_classifications"] = parsed[1:]
            return primary

        if "categoria" not in parsed:
            raise Exception(f"Resposta sem campo 'categoria': {parsed}")

        return parsed
