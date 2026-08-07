"""Shadow mode — prevê agendar/ignorar sem executar nada.

A política aqui é a que a operação DECLAROU na entrevista de 06/08/2026,
codificada e ordenada da regra mais forte pra mais fraca:

  1. Já coberto  — existe tarefa da MESMA FAMÍLIA na pasta (aberta ou
     concluída há pouco). O operador confere isso no próprio modal do Flow.
     No histórico: 8,7% dos ignores contra 1,1% dos agendamentos.
  2. Parte adversa — o ato é dirigido a quem não representamos (polo da
     publicação ≠ lado do escritório). ~60% de ignore no histórico. EXCEÇÃO
     medida: quando a parte adversa PETICIONA nasce dever nosso de responder
     ("Manifestação das Partes" adversa = 99,6% agendada), então a regra só
     dispara em intimação dirigida a ela.
  3. Célula histórica — categoria×subcategoria×escritório com comportamento
     estável (≥90% num dos lados, com massa) decide por frequência.
  4. Default agendar — na dúvida, agendar: perder prazo é grave, tarefa a
     mais é cancelável (regra explícita do operador sobre prazos).

O que este módulo NÃO faz: executar. Ele só grava o que teria feito.

Custo: zero token. É política determinística de propósito — primeiro medimos
o piso, depois decidimos se vale gastar LLM na zona cinzenta.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.publication_shadow import (
    CONF_ALTA, CONF_BAIXA, CONF_MEDIA,
    PublicacaoShadowDecisao, SHADOW_AGENDAR, SHADOW_IGNORAR,
)

logger = logging.getLogger(__name__)

# Quanto tempo uma tarefa concluída ainda "cobre" a providência. O operador
# disse que tarefa igual concluída há pouco conta como coberta.
JANELA_CONCLUIDA_DIAS = 30

# Massa mínima pra uma célula histórica poder decidir sozinha.
CELULA_MIN = 20
CELULA_LIMITE = 0.90

# Marcas textuais de que a parte adversa PETICIONOU (nasce dever nosso de
# responder) — bloqueiam a regra 2. Derivadas do achado "Manifestação das
# Partes adversa = 99,6% agendada".
_PECA_ADVERSA = re.compile(
    r"\b(r[ée]plica|contestaç|impugnaç|embargos|apelaç|recurso|"
    r"contrarraz|manifestaç[aã]o da parte|petiç[aã]o (da|do) (autor|r[ée]u)|"
    r"juntada de (petiç|documento))", re.IGNORECASE,
)

# Marcas de publicação puramente informativa (regra 3 do operador). Conservador
# de propósito: só dispara quando NÃO há nenhuma marca de prazo/providência.
_INFORMATIVA = re.compile(
    r"\b(decorrido o prazo|certid[aã]o de decurso|mero expediente|"
    r"car[áa]ter meramente informativo|saneamento cadastral|"
    r"sem incid[êe]ncia de prazo)", re.IGNORECASE,
)
_TEM_PRAZO = re.compile(
    r"\b(prazo de|no prazo|intim[ae]|manifest|apresent|cumpra|"
    r"\d{1,3}\s*\(?\s*\w*\s*\)?\s*dias)", re.IGNORECASE,
)


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
        """Família = o prefixo antes do ' - ' no nome do subtipo.

        'Análise Recursal Réu - BB Defesa' → 'Análise Recursal Réu'. O
        operador disse que a cobertura é por FAMÍLIA, não por subtipo exato.
        """
        if not nome:
            return None
        return nome.split(" - ")[0].strip() or None

    def _sinais(self, rec, as_of: Optional[datetime] = None) -> dict[str, Any]:
        # `as_of` existe pro BACKTEST: sem congelar o relógio, a checagem de
        # cobertura enxerga a tarefa que o próprio operador criou a partir
        # desta publicação — o sinal passa a "prever" o que já aconteceu e a
        # regra dispara em tudo (medido: 12% de acerto num backtest ingênuo).
        # Ao vivo o valor natural é agora, porque a previsão acontece antes
        # de qualquer tarefa nascer.
        agora = as_of or datetime.now(timezone.utc)
        sinais: dict[str, Any] = {
            "polo": rec.polo,
            "categoria": rec.category,
            "subcategoria": rec.subcategory,
            "office_id": rec.linked_office_id,
        }

        # escritório → lado que representamos
        path = self.db.execute(text(
            "select path from legal_one_offices where external_id = :o"
        ), {"o": rec.linked_office_id}).scalar() if rec.linked_office_id else None
        sinais["nosso_lado"] = _lado_do_escritorio(path)

        # subtipo que o template proporia (define a família a checar)
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

        # regra 1: tarefa da mesma família na pasta (aberta OU concluída há
        # pouco). Sem família resolvida, não dá pra afirmar cobertura.
        coberto = False
        if familia and rec.linked_lawsuit_cnj:
            coberto = bool(self.db.execute(text("""
                select 1 from perf_l1_tarefa
                 where cnj = :cnj
                   and subtipo like :fam
                   -- só o que JÁ EXISTIA no instante da previsão
                   and cadastrado_em < :agora
                   and (concluido_em is null or concluido_em > :limite)
                 limit 1
            """), {
                "cnj": rec.linked_lawsuit_cnj,
                "fam": f"{familia}%",
                "agora": agora,
                "limite": agora - timedelta(days=JANELA_CONCLUIDA_DIAS),
            }).first())
        sinais["ja_coberto"] = coberto

        # regra 2: polo da publicação vs lado que representamos
        texto = rec.description or ""
        adversa = bool(
            rec.polo in ("ativo", "passivo")
            and sinais["nosso_lado"]
            and rec.polo != sinais["nosso_lado"]
        )
        sinais["parte_adversa"] = adversa
        sinais["adversa_peticionou"] = bool(adversa and _PECA_ADVERSA.search(texto))

        # regra 3: informativa (só quando não há marca de prazo/providência)
        sinais["informativa"] = bool(
            _INFORMATIVA.search(texto) and not _TEM_PRAZO.search(texto)
        )

        # célula histórica
        cel = self.db.execute(text("""
            select count(*) as n,
                   count(*) filter (where status='IGNORADO')::float
                     / nullif(count(*),0) as taxa_ig
              from publicacao_registros
             where is_duplicate = false
               and status in ('AGENDADO','IGNORADO')
               and category = :c
               and coalesce(subcategory,'') = coalesce(:s,'')
               and linked_office_id is not distinct from :o
               -- histórico ANTERIOR à previsão (no backtest, não vale olhar
               -- decisões futuras; ao vivo, `agora` é o presente)
               and updated_at < :agora
               and updated_at >= :agora - interval '120 days'
        """), {"c": rec.category, "s": rec.subcategory,
               "o": rec.linked_office_id, "agora": agora}).first()
        sinais["celula_n"] = int(cel.n or 0) if cel else 0
        sinais["celula_taxa_ignore"] = (
            round(float(cel.taxa_ig), 3) if cel and cel.taxa_ig is not None else None
        )
        return sinais

    # ── política ────────────────────────────────────────────────────────

    def decidir(self, sinais: dict) -> tuple[str, Optional[str], str, str]:
        """(previsto, motivo, confiança, regra) — ordem = força da regra."""
        # ATENÇÃO — achado que derrubou a hipótese original (backtest 06/08,
        # 600 decisões): ter tarefa da mesma FAMÍLIA na pasta NÃO prediz
        # ignorar; prediz AGENDAR. Taxa de ignore quando existe família aberta
        # = 7,2%, criada nos últimos 7 dias = 5,2%, contra 37,3% na base.
        # Faz sentido: pasta com tarefa viva é pasta ATIVA, que recebe
        # publicação que exige ação. A checagem que o operador faz no modal é
        # semântica ("isto aqui já cobre ESTA publicação?"), e nenhum casamento
        # estrutural por família reproduz isso — proxy grosso demais inverte o
        # sinal. Então `ja_coberto` deixa de ser gatilho de ignorar e vira
        # REFORÇO de agendar.
        if sinais.get("ja_coberto"):
            return SHADOW_AGENDAR, None, CONF_ALTA, "r1_pasta_ativa"

        if sinais.get("parte_adversa") and not sinais.get("adversa_peticionou"):
            return SHADOW_IGNORAR, "parte_adversa", CONF_MEDIA, "r2_parte_adversa"

        if sinais.get("informativa"):
            return SHADOW_IGNORAR, "informativa", CONF_BAIXA, "r3_informativa"

        n = sinais.get("celula_n") or 0
        taxa = sinais.get("celula_taxa_ignore")
        if n >= CELULA_MIN and taxa is not None:
            if taxa <= (1 - CELULA_LIMITE):
                return (SHADOW_AGENDAR, None, CONF_ALTA, "r4_celula_agenda")
            if taxa >= CELULA_LIMITE:
                # Existe no papel; medido em 06/08 não há célula assim com
                # massa. Se aparecer, é sinal de mudança de política.
                return (SHADOW_IGNORAR, "informativa", CONF_MEDIA,
                        "r4_celula_ignora")

        # Default: agendar. Prazo perdido é grave; tarefa a mais é cancelável.
        conf = CONF_MEDIA if sinais.get("tem_template") else CONF_BAIXA
        return SHADOW_AGENDAR, None, conf, "r5_default_agendar"

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
        """Prevê em lote. Best-effort: um registro problemático não derruba
        o lote — shadow é observação, nunca pode atrapalhar a operação."""
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
        linha.acertou = (linha.previsto == real)
