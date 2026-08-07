"""Shadow mode — prevê agendar/ignorar/OPERADOR sem executar nada.

Política v5, produto do laboratório noturno de 06→07/08/2026: 5 iterações de
hipótese→teste contra 24.060 decisões reais, com treino (15/06–20/07) e
validação intocada (21/07–06/08). Números finais NA VALIDAÇÃO:

  - concordância global 71,1% (baseline v1: 68,0%)
  - fatia ALTA 91,1% cobrindo 28% — automação NÃO liga por tier, liga por
    CÉLULA ≥95% (o placar /admin/shadow-publicacoes seleciona);
  - risco (previ ignorar, operador agendou) 2,4%;
  - abstenção (OPERADOR) 2,2%, em textos genuinamente insuficientes.

Aprendizados que moldaram as regras (cada um medido, não intuído):
  - Pasta com tarefa da mesma família = pasta ATIVA → prediz AGENDAR
    (7% de ignore), o INVERSO da hipótese original.
  - "Incluído em pauta" → IGNORAR: regra declarada pelo operador em 06/08 e
    confirmada pela validação (84,5% — o hábito antigo de agendar morreu em
    julho; o treino ainda o media, 32%).
  - Casca "Para advogados de X com prazo": se X é cliente nosso o prazo é
    NOSSO → agendar; se é outro nome, o histórico é misto → OPERADOR.
  - Texto insuficiente ("consulte os autos", casca sem conteúdo, <180 chars)
    → OPERADOR: no histórico, 73% viram agendamento após o operador abrir o
    processo — indecidível pelo texto, por definição.
  - Parte adversa só ignora onde a célula histórica tolera ignore (≥20%);
    em célula sempre-agenda, a regra era fonte de risco.
  - O resíduo sem regra tem marcadores no máximo ~70% de pureza (minerado):
    é semântica de verdade — território de LLM, não de regex. Fica pro
    próximo estágio, se o placar justificar o custo.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.publication_shadow import (
    CONF_ALTA, CONF_BAIXA, CONF_MEDIA,
    PublicacaoShadowDecisao, SHADOW_AGENDAR, SHADOW_IGNORAR,
)

logger = logging.getLogger(__name__)

SHADOW_OPERADOR = "OPERADOR"

# Janelas e limiares — todos calibrados no lab (ver docstring).
JANELA_FAMILIA_DIAS = 7
CELULA_JANELA_DIAS = 120
CELULA_MIN_LOCAL = 15
CELULA_MIN_GLOBAL = 40
CELULA_AGENDA = 0.10
CELULA_IGNORA = 0.90
R2_MIN_TOLERANCIA_IGNORE = 0.20
TEXTO_MINIMO = 180

_PECA_ADVERSA = re.compile(
    r"\b(replica|contesta|impugna|embargos|apela|recurso|contrarraz|"
    r"manifestacao da parte|peticao d[oa] (autor|reu)|juntada de (petic|documento))")
_TEXTO_EXTERNO = re.compile(
    r"(multiplos documentos associados|consulte os autos|para visualiza-?los|"
    r"conteudo do documento nao disponivel|processo sigiloso)")
_CASCA = re.compile(
    r"(intimad[ao]s? do evento processual ocorrido|"
    r"referente ao evento (decisao|despacho|ato ordinatorio)\b|"
    r"^para\s+advogados?/curador)")
_PAUTA = re.compile(
    r"(inclu[i]?d[oa] em pauta|pauta de julgamento|"
    r"sessao (virtual|ordinaria) de julgamento)")
_NOSSOS = re.compile(r"(banco do brasil|ativos s|banco master|banese|bb\b)")
_CASCA_PARA = re.compile(
    r"para\s+advogados?/curador/defensor de (.{3,80}?)\s+com prazo")
_AUDIENCIA = re.compile(
    r"(designo audiencia|audiencia (de conciliacao|una|de instrucao)[^.]{0,80}"
    r"(designada|realizada por|no dia)|link de acesso|pauta de audiencia)")


def _norm(s: Optional[str]) -> str:
    return unicodedata.normalize("NFKD", s or "").encode(
        "ascii", "ignore").decode().lower()


def _lado_do_escritorio(path: Optional[str]) -> Optional[str]:
    """'MDR / ... / Banco do Brasil / Réu' → 'passivo'."""
    if not path:
        return None
    folha = path.rsplit(" / ", 1)[-1].strip().lower()
    if folha.startswith("réu") or folha.startswith("reu"):
        return "passivo"
    if folha.startswith("autor"):
        return "ativo"
    return None


class ShadowService:
    def __init__(self, db: Session):
        self.db = db

    # ── sinais (congelados no instante da previsão) ─────────────────────

    def _familia_do_subtipo(self, nome: Optional[str]) -> Optional[str]:
        if not nome:
            return None
        return nome.split(" - ")[0].strip() or None

    def _sinais(self, rec, as_of: Optional[datetime] = None) -> dict[str, Any]:
        # `as_of` congela o relógio (essencial no backtest; ao vivo = agora).
        agora = as_of or datetime.now(timezone.utc)
        sinais: dict[str, Any] = {
            "polo": rec.polo,
            "categoria": rec.category,
            "subcategoria": rec.subcategory,
            "office_id": rec.linked_office_id,
            "texto_len": len(rec.description or ""),
        }

        path = self.db.execute(text(
            "select path from legal_one_offices where external_id = :o"
        ), {"o": rec.linked_office_id}).scalar() if rec.linked_office_id else None
        sinais["nosso_lado"] = _lado_do_escritorio(path)

        subtipo = self.db.execute(text("""
            select s.name
              from task_templates t
              join legal_one_task_subtypes s
                on s.external_id = t.task_subtype_external_id
             where t.is_active and t.category = :c
               and coalesce(t.subcategory,'') = coalesce(:s,'')
               and (t.office_external_id = :o or t.office_external_id is null)
             order by (t.office_external_id is not null) desc
             limit 1
        """), {"c": rec.category, "s": rec.subcategory,
               "o": rec.linked_office_id}).scalar()
        familia = self._familia_do_subtipo(subtipo)
        sinais["template_subtipo"] = subtipo
        sinais["familia"] = familia
        sinais["tem_template"] = subtipo is not None

        # Pasta ATIVA: tarefa da família aberta, ou criada há pouco.
        pasta_ativa = False
        if familia and rec.linked_lawsuit_cnj:
            pasta_ativa = bool(self.db.execute(text("""
                select 1 from perf_l1_tarefa
                 where cnj = :cnj
                   and subtipo like :fam
                   and cadastrado_em < :agora
                   and (concluido_em is null
                        or cadastrado_em >= :recente)
                 limit 1
            """), {
                "cnj": rec.linked_lawsuit_cnj,
                "fam": f"{familia}%",
                "agora": agora,
                "recente": agora - timedelta(days=JANELA_FAMILIA_DIAS),
            }).first())
        sinais["pasta_ativa"] = pasta_ativa

        # célula LOCAL (cat, subcat, office) e GLOBAL (cat, subcat)
        cel = self.db.execute(text("""
            select count(*) as n,
                   count(*) filter (where status='IGNORADO')::float
                     / nullif(count(*),0) as taxa
              from publicacao_registros
             where is_duplicate = false
               and status in ('AGENDADO','IGNORADO')
               and category = :c
               and coalesce(subcategory,'') = coalesce(:s,'')
               and linked_office_id is not distinct from :o
               and updated_at < :agora
               and updated_at >= :agora - interval '%s days'
        """ % CELULA_JANELA_DIAS), {
            "c": rec.category, "s": rec.subcategory,
            "o": rec.linked_office_id, "agora": agora}).first()
        sinais["celula_n"] = int(cel.n or 0) if cel else 0
        sinais["celula_taxa"] = (
            round(float(cel.taxa), 3) if cel and cel.taxa is not None else None)

        celg = self.db.execute(text("""
            select count(*) as n,
                   count(*) filter (where status='IGNORADO')::float
                     / nullif(count(*),0) as taxa
              from publicacao_registros
             where is_duplicate = false
               and status in ('AGENDADO','IGNORADO')
               and category = :c
               and coalesce(subcategory,'') = coalesce(:s,'')
               and updated_at < :agora
               and updated_at >= :agora - interval '%s days'
        """ % CELULA_JANELA_DIAS), {
            "c": rec.category, "s": rec.subcategory, "agora": agora}).first()
        sinais["celula_g_n"] = int(celg.n or 0) if celg else 0
        sinais["celula_g_taxa"] = (
            round(float(celg.taxa), 3) if celg and celg.taxa is not None else None)

        txt = _norm(rec.description)
        m = _CASCA_PARA.search(txt)
        sinais["casca_para"] = (
            "nosso" if (m and _NOSSOS.search(m.group(1)))
            else "outro" if m else None)
        sinais["texto_externo"] = bool(_TEXTO_EXTERNO.search(txt))
        sinais["casca"] = bool(_CASCA.search(txt))
        sinais["pauta"] = bool(
            (rec.subcategory or "").startswith("Inclusão em Pauta")
            or _PAUTA.search(txt))
        adversa = bool(
            rec.polo in ("ativo", "passivo") and sinais["nosso_lado"]
            and rec.polo != sinais["nosso_lado"])
        sinais["parte_adversa"] = adversa
        sinais["adversa_peticionou"] = bool(adversa and _PECA_ADVERSA.search(txt))
        sinais["audiencia"] = bool(_AUDIENCIA.search(txt))
        return sinais

    # ── política v5 ─────────────────────────────────────────────────────

    def decidir(self, sinais: dict) -> tuple[str, Optional[str], str, str]:
        """(previsto, motivo, confiança, regra)."""
        # r0 — viabilidade do texto (pedido do operador 06/08): o que não se
        # decide pelo texto vai pro OPERADOR, nunca pro chute.
        if sinais.get("casca_para") == "nosso":
            return SHADOW_AGENDAR, None, CONF_MEDIA, "r0_casca_prazo_nosso"
        if sinais.get("casca_para") == "outro":
            return SHADOW_OPERADOR, "texto_insuficiente", "n/a", "r0_casca_prazo_outro"
        if sinais.get("texto_externo") or (
                sinais.get("casca") and sinais.get("texto_len", 0) < 420):
            return SHADOW_OPERADOR, "texto_insuficiente", "n/a", "r0_texto_insuficiente"
        if sinais.get("texto_len", 0) < TEXTO_MINIMO:
            return SHADOW_OPERADOR, "texto_insuficiente", "n/a", "r0_texto_curto"

        # r2b — pauta: regra declarada (validação: 84,5%).
        if sinais.get("pauta"):
            return SHADOW_IGNORAR, "informativa", CONF_MEDIA, "r2b_pauta"

        # r1 — pasta ativa prediz agendar (invertida pelo lab).
        if sinais.get("pasta_ativa"):
            return SHADOW_AGENDAR, None, CONF_ALTA, "r1_pasta_ativa"

        taxa_cel = sinais.get("celula_taxa")
        n_cel = sinais.get("celula_n") or 0

        # r2 — parte adversa, com portão de célula.
        if sinais.get("parte_adversa") and not sinais.get("adversa_peticionou") \
                and (n_cel < 8 or (taxa_cel or 0) >= R2_MIN_TOLERANCIA_IGNORE):
            return SHADOW_IGNORAR, "parte_adversa", CONF_MEDIA, "r2_parte_adversa"

        # r4 — célula local decide com massa.
        if n_cel >= CELULA_MIN_LOCAL and taxa_cel is not None:
            if taxa_cel <= CELULA_AGENDA:
                return SHADOW_AGENDAR, None, CONF_ALTA, "r4_celula_agenda"
            if taxa_cel >= CELULA_IGNORA:
                return SHADOW_IGNORAR, "informativa", CONF_MEDIA, "r4_celula_ignora"

        # r4b — audiência designada (média: 67–78% no lab).
        if sinais.get("audiencia"):
            return SHADOW_AGENDAR, None, CONF_MEDIA, "r4b_audiencia"

        # r4g — célula global (sem escritório), sempre média.
        ng = sinais.get("celula_g_n") or 0
        tg = sinais.get("celula_g_taxa")
        if ng >= CELULA_MIN_GLOBAL and tg is not None:
            if tg <= CELULA_AGENDA:
                return SHADOW_AGENDAR, None, CONF_MEDIA, "r4g_celula_global"
            if tg >= CELULA_IGNORA:
                return SHADOW_IGNORAR, "informativa", CONF_MEDIA, "r4g_celula_global_ig"

        # r5 — default agendar (prazo perdido é grave; tarefa a mais é cancelável).
        conf = CONF_MEDIA if sinais.get("tem_template") else CONF_BAIXA
        return SHADOW_AGENDAR, None, conf, "r5_default"

    # ── gravação ────────────────────────────────────────────────────────

    def prever(self, rec, as_of: Optional[datetime] = None) -> Optional[PublicacaoShadowDecisao]:
        """Grava a previsão pra um registro. Idempotente (1 por publicação)."""
        ja = (self.db.query(PublicacaoShadowDecisao)
                  .filter(PublicacaoShadowDecisao.record_id == rec.id).first())
        if ja:
            return ja
        sinais = self._sinais(rec, as_of=as_of)
        previsto, motivo, conf, regra = self.decidir(sinais)
        linha = PublicacaoShadowDecisao(
            record_id=rec.id, previsto=previsto, previsto_motivo=motivo,
            confianca=conf, regra=regra, sinais=sinais,
        )
        self.db.add(linha)
        return linha

    def prever_muitos(self, records) -> int:
        """Prevê em lote. Best-effort: shadow é observação, nunca pode
        atrapalhar a operação."""
        n = 0
        for rec in records:
            try:
                if self.prever(rec) is not None:
                    n += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("shadow: falha ao prever record %s (%s)",
                               getattr(rec, "id", "?"), exc)
        return n

    def registrar_desfecho(
        self, record_id: int, real: str,
        motivo: Optional[str] = None, por: Optional[str] = None,
    ) -> None:
        """Fecha o par previsão×realidade. Sem previsão prévia, não inventa
        uma agora: prever depois do fato seria trapaça."""
        linha = (self.db.query(PublicacaoShadowDecisao)
                     .filter(PublicacaoShadowDecisao.record_id == record_id)
                     .first())
        if not linha or linha.real:
            return
        linha.real = real
        linha.real_motivo = motivo
        linha.real_por = (por or "")[:120] or None
        linha.real_em = datetime.now(timezone.utc)
        # OPERADOR não acerta nem erra — abstenção fica fora do placar de
        # acurácia e entra como cobertura cedida ao humano.
        linha.acertou = (
            (linha.previsto == real)
            if linha.previsto in (SHADOW_AGENDAR, SHADOW_IGNORAR) else None
        )
