from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, joinedload

from app.models.legal_one import LegalOneTaskSubType
from app.models.prazo_inicial import (
    INTAKE_SOURCE_EXTERNAL_API,
    INTAKE_STATUS_AWAITING_TEMPLATE_CONFIG,
    INTAKE_STATUS_CLASSIFIED,
    INTAKE_STATUS_COMPLETED_WITHOUT_PROVIDENCE,
    INTAKE_STATUS_DEVOLUCAO_PENDING,
    INTAKE_STATUS_IN_REVIEW,
    INTAKE_STATUS_SCHEDULE_ERROR,
    INTAKE_STATUS_SCHEDULED,
    SUGESTAO_REVIEW_APPROVED,
    SUGESTAO_REVIEW_EDITED,
    SUGESTAO_REVIEW_PENDING,
    SUGESTAO_REVIEW_REJECTED,
    PrazoInicialIntake,
    PrazoInicialSugestao,
)
from app.core.config import settings
from app.services.legal_one_client import (
    LegalOneApiClient,
    LegalOneGedUploadError,
)
from app.services.squad_assistant_resolver import (
    AssistantResolutionResult,
    resolve_assistant,
    resolve_target,
)
from app.services.prazos_iniciais import storage as pdf_storage
from app.services.prazos_iniciais.legacy_task_helpers import (
    DEFAULT_LEGACY_TASK_SUBTYPE_EXTERNAL_ID,
    DEFAULT_LEGACY_TASK_TYPE_EXTERNAL_ID,
)
from app.services.prazos_iniciais.legacy_task_queue_service import (
    PrazosIniciaisLegacyTaskQueueService,
)

logger = logging.getLogger(__name__)

# Limite rigoroso do campo `description` na API de Tarefa do L1. Acima
# disso o servidor responde 400. Mesmo limite usado no módulo
# Publicações (publication_search_service.py).
_L1_DESCRIPTION_MAX_CHARS = 250


VALID_CONFIRM_REVIEW_STATUSES = {
    SUGESTAO_REVIEW_APPROVED,
    SUGESTAO_REVIEW_EDITED,
}


@dataclass(frozen=True)
class ConfirmedSuggestionInput:
    suggestion_id: int
    created_task_id: Optional[int] = None
    review_status: Optional[str] = None
    # Overrides opcionais — quando o operador edita campos da sugestao
    # no modal de Agendar (Modal B), os valores novos vem aqui. Sao
    # aplicados na sugestao no banco antes de _build_l1_task_payload
    # (rastreabilidade: a sugestao reflete o que foi efetivamente
    # agendado no L1). Espelha `payload_overrides` de publicacoes.
    override_task_subtype_external_id: Optional[int] = None
    override_responsible_user_external_id: Optional[int] = None
    override_data_base: Optional[date] = None
    override_data_final_calculada: Optional[date] = None
    # Hora da audiencia editavel direto no modal. Valor None = nao
    # alterar (mantem o que veio da IA). Vai virar `sugestao.audiencia_hora`
    # na hora de confirmar.
    override_audiencia_hora: Optional[dtime] = None
    override_prazo_dias: Optional[int] = None
    override_prazo_tipo: Optional[str] = None  # util | corrido
    override_priority: Optional[str] = None
    override_description: Optional[str] = None
    override_notes: Optional[str] = None

    def has_any_override(self) -> bool:
        return any(
            getattr(self, name) is not None
            for name in (
                "override_task_subtype_external_id",
                "override_responsible_user_external_id",
                "override_data_base",
                "override_data_final_calculada",
                "override_audiencia_hora",
                "override_prazo_dias",
                "override_prazo_tipo",
                "override_priority",
                "override_description",
                "override_notes",
            )
        )


@dataclass(frozen=True)
class CustomTaskInput:
    """
    Tarefa avulsa adicionada pelo operador no modal de Confirmar
    Agendamento — nao casa com sugestao da IA, vai direto pra criacao
    no L1. Espelha o padrao de "tarefa avulsa" de publicacoes.

    Persistida como sugestao sintetica (tipo_prazo='AVULSA',
    review_status='aprovado', created_task_id preenchido) pra manter
    rastreabilidade na pagina de detalhe.
    """
    task_subtype_external_id: int
    responsible_user_external_id: int
    description: str
    due_date: date  # vira startDateTime/endDateTime no L1
    priority: str = "Normal"
    notes: Optional[str] = None
    # Quando True, o `responsible_user_external_id` informado e' tratado
    # como o "responsavel principal/lider" e a tarefa e' redirecionada pro
    # assistente da squad dele via `resolve_assistant`. Equivalente a
    # `target_role='assistente'` no template.
    assign_to_assistant: bool = False


# Tipo "sintetico" pra sugestao avulsa — fica como tipo_prazo da
# sugestao persistida, evitando precisar de migracao pra novo enum.
TIPO_PRAZO_AVULSA = "AVULSA"


class PrazosIniciaisSchedulingService:
    def __init__(
        self,
        db: Session,
        l1_client: Optional[LegalOneApiClient] = None,
    ):
        self.db = db
        self.queue_service = PrazosIniciaisLegacyTaskQueueService(db)
        # Lazy: instancia o client L1 apenas quando for preciso criar task,
        # pra nao exigir auth OAuth em testes que nao agendam.
        self._l1_client = l1_client

    @property
    def l1_client(self) -> LegalOneApiClient:
        if self._l1_client is None:
            self._l1_client = LegalOneApiClient()
        return self._l1_client

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    # Timezone do Brasil (BRT). O L1 IGNORA o offset ISO ao parsear
    # datetime - trata o numero literal como UTC e depois renderiza em
    # BRT (subtrai 3h). Por isso converte-se local BRT -> UTC com `Z`
    # antes de mandar. Caso contrario, 23:59Z aparece como 20:59 na
    # tela do L1. Mesma logica de Publications (publication_search_service).
    _BR_TZ = ZoneInfo("America/Sao_Paulo")

    @classmethod
    def _brt_to_utc_iso(
        cls, due_date: date, due_time: Optional[dtime] = None
    ) -> str:
        """
        Combina date + time (interpretados em BRT) e converte pra ISO
        UTC com `Z`. Default `due_time=None` -> 23:59:59 BRT (fim do
        expediente, padrao usado pra prazos sem horario especifico).
        """
        local_time = due_time if due_time is not None else dtime(23, 59, 59)
        local_dt = datetime.combine(due_date, local_time).replace(
            tzinfo=cls._BR_TZ,
        )
        return local_dt.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ",
        )

    def _load_intake(self, intake_id: int) -> Optional[PrazoInicialIntake]:
        return (
            self.db.query(PrazoInicialIntake)
            .options(joinedload(PrazoInicialIntake.sugestoes))
            .filter(PrazoInicialIntake.id == intake_id)
            .first()
        )

    # ──────────────────────────────────────────────
    # Criação de tarefa no Legal One (Onda 1)
    # ──────────────────────────────────────────────

    def _resolve_parent_type_id(self, subtype_external_id: int) -> Optional[int]:
        """
        Resolve o `typeId` (tipo-pai do SubType) a partir do catálogo local
        de L1 — populado pelos syncs do `legal_one_catalog_service`.
        """
        subtype = (
            self.db.query(LegalOneTaskSubType)
            .options(joinedload(LegalOneTaskSubType.parent_type))
            .filter(LegalOneTaskSubType.external_id == int(subtype_external_id))
            .first()
        )
        if subtype and subtype.parent_type:
            return subtype.parent_type.external_id
        return None

    def _resolve_final_responsible(
        self,
        *,
        candidate_user_external_id: int,
        task_subtype_external_id: Optional[int],
        target_role: str,
        office_external_id: Optional[int] = None,
        target_squad_id: Optional[int] = None,
    ) -> tuple[int, Optional[AssistantResolutionResult]]:
        """
        Resolve o user que vai virar `participants[0].contact.id` no L1.
        Cobre os 4 cenarios via `resolve_target`:

        - principal sem target_squad: candidato direto
        - assistente sem target_squad: assistente da squad principal
        - principal com target_squad: leader da squad de suporte
        - assistente com target_squad: assistente da support (round-robin)

        `commit=True` avanca o round-robin (last_assigned_at).
        """
        result = resolve_target(
            self.db,
            target_role=target_role,
            responsible_user_external_id=int(candidate_user_external_id),
            target_squad_id=target_squad_id,
            task_subtype_external_id=task_subtype_external_id,
            office_external_id=office_external_id,
            commit=True,
        )
        # resolution=None quando e' "principal sem squad" (passa direto)
        if target_role == "principal" and target_squad_id is None:
            return result.user_external_id, None
        return result.user_external_id, result

    def _lookup_template_target(
        self, sugestao: PrazoInicialSugestao,
    ) -> tuple[str, Optional[int]]:
        """Lookup de `target_role` + `target_squad_id` via
        `payload_proposto.template_id`. Default ('principal', None) se
        a sugestao nao casou com template ou template foi removido."""
        from app.models.prazo_inicial_task_template import (
            PrazoInicialTaskTemplate,
        )

        payload = sugestao.payload_proposto or {}
        template_id = payload.get("template_id")
        if not template_id:
            return "principal", None
        template = (
            self.db.query(PrazoInicialTaskTemplate)
            .filter(PrazoInicialTaskTemplate.id == int(template_id))
            .one_or_none()
        )
        if template is None:
            return "principal", None
        return (
            template.target_role or "principal",
            getattr(template, "target_squad_id", None),
        )

    # Backwards-compat shim — alguns lugares ainda chamam o nome antigo.
    def _lookup_template_target_role(self, sugestao: PrazoInicialSugestao) -> str:
        return self._lookup_template_target(sugestao)[0]

    def _build_l1_task_payload(
        self,
        *,
        sugestao: PrazoInicialSugestao,
        intake: PrazoInicialIntake,
    ) -> dict[str, Any]:
        """
        Constrói o payload de criação de Tarefa no L1 a partir da sugestão
        (já casada com template via classifier) e do intake pai.

        Campos obrigatórios do L1:
            - description, priority, startDateTime, endDateTime, publishDate
            - typeId, subTypeId
            - status, responsibleOfficeId, originOfficeId
            - participants (com responsável)

        Pré-condições que a sugestão precisa atender; se falhar, levanta
        ValueError explícito pra que o confirm_intake_scheduling aborte
        atomicamente sem enfileirar cancelamento da legada.
        """
        if not sugestao.task_subtype_id:
            raise ValueError(
                f"Sugestão {sugestao.id} sem task_subtype_id. "
                "Template não casou — verifique prazo_inicial_task_templates."
            )
        if not sugestao.responsavel_sugerido_id:
            raise ValueError(
                f"Sugestão {sugestao.id} sem responsável sugerido. "
                "Template precisa ter responsible_user_external_id preenchido."
            )
        if not sugestao.data_final_calculada:
            raise ValueError(
                f"Sugestão {sugestao.id} sem data_final_calculada. "
                "Prazo não foi calculado — revise a classificação."
            )

        type_id = self._resolve_parent_type_id(sugestao.task_subtype_id)
        if not type_id:
            raise ValueError(
                f"Tipo-pai do subTypeId={sugestao.task_subtype_id} não encontrado "
                "no catálogo local — sincronize o catálogo de tasks do L1."
            )

        # startDateTime/endDateTime: data_final_calculada + audiencia_hora
        # (quando houver), interpretado em BRT e convertido pra UTC com `Z`.
        # Caso contrario o L1 trata o numero literal como UTC e renderiza
        # com -3h na tela (ex.: 23:59Z apareceria como 20:59 BRT - bug que
        # afetava audiencias antes desse ajuste). Sugestoes sem horario
        # (contestar, liminar, manifestacao) caem em 23:59:59 BRT.
        # Mesma logica de Publications.
        due_date: date = sugestao.data_final_calculada
        due_iso = self._brt_to_utc_iso(due_date, sugestao.audiencia_hora)

        # publishDate: L1 exige quando SubTypeId esta preenchido. Usa a data
        # base da sugestao (origem do prazo) ou cai na due_date - sempre
        # 00:00:00 BRT convertido pra UTC.
        base_date: date = sugestao.data_base or due_date
        publish_iso = self._brt_to_utc_iso(base_date, dtime(0, 0, 0))

        # Preserva description/notes/priority já renderizados pelo template
        # via _apply_template_to_sugestao. Se não houver, gera fallback
        # seguro (tarefa ainda assim cria — mas fica genérica).
        rendered = dict(sugestao.payload_proposto or {})
        description = (rendered.get("description") or "").strip()
        if not description:
            description = f"{sugestao.tipo_prazo} · CNJ {intake.cnj_number or '?'}"
        if len(description) > _L1_DESCRIPTION_MAX_CHARS:
            description = description[: _L1_DESCRIPTION_MAX_CHARS - 1].rstrip() + "…"

        notes = rendered.get("notes")
        priority = rendered.get("priority") or "Normal"

        office_id = intake.office_id

        # Resolve responsavel final (aplicando regra do assistente se o
        # template marcou target_role='assistente'). Override manual do
        # operador (entry.override_responsible_user_external_id) ja' foi
        # aplicado em `sugestao.responsavel_sugerido_id` antes desta
        # chamada — entao o resolver opera sobre o valor "atual" da
        # sugestao, que e' o que o operador realmente quer ver na squad.
        target_role, target_squad_id = self._lookup_template_target(sugestao)
        original_target_role = target_role
        # Override manual: se o operador trocou o responsavel no modal,
        # respeita a escolha dele — desliga regra do assistente E desliga
        # roteamento pra squad de suporte (operador na ponta vence).
        overridden = bool(
            (sugestao.payload_proposto or {}).get("responsible_overridden")
        )
        if overridden:
            target_role = "principal"
            target_squad_id = None
        try:
            final_responsible_id, _resolution = self._resolve_final_responsible(
                candidate_user_external_id=int(sugestao.responsavel_sugerido_id),
                task_subtype_external_id=int(sugestao.task_subtype_id),
                target_role=target_role,
                office_external_id=intake.office_id,
                target_squad_id=target_squad_id,
            )
        except ValueError as exc:
            raise ValueError(
                f"Sugestão {sugestao.id} (target_role={target_role!r}): {exc}"
            ) from exc

        # Log estruturado da resolucao do responsavel — chave pra
        # diagnosticar "tarefa caiu no lider em vez do assistente". Logado
        # como INFO em cada criacao real (intake confirmado). Procurar
        # nos logs por `prazos_iniciais.routing` quando algo parecer fora.
        if (
            original_target_role != "principal"
            or _resolution is not None
            or overridden
        ):
            logger.info(
                "prazos_iniciais.routing intake=%s sugestao=%s "
                "template_target_role=%s template_target_squad_id=%s "
                "overridden=%s candidate=%s final=%s "
                "fallback_reason=%s squad=%s",
                intake.id, sugestao.id,
                original_target_role, target_squad_id,
                overridden,
                sugestao.responsavel_sugerido_id, final_responsible_id,
                getattr(_resolution, "fallback_reason", None) if _resolution else None,
                getattr(_resolution, "squad_name", None) if _resolution else None,
            )

        payload: dict[str, Any] = {
            "description": description,
            "priority": priority,
            "startDateTime": due_iso,
            "endDateTime": due_iso,
            "publishDate": publish_iso,
            "status": {"id": 0},
            "typeId": int(type_id),
            "subTypeId": int(sugestao.task_subtype_id),
            "participants": [
                {
                    "contact": {"id": final_responsible_id},
                    "isResponsible": True,
                    "isExecuter": True,
                    "isRequester": True,
                }
            ],
        }
        if notes:
            payload["notes"] = notes
        if office_id:
            payload["responsibleOfficeId"] = int(office_id)
            payload["originOfficeId"] = int(office_id)
        return payload

    def _apply_overrides_to_sugestao(
        self,
        sugestao: PrazoInicialSugestao,
        entry: ConfirmedSuggestionInput,
    ) -> None:
        """
        Aplica overrides do operador (campos editaveis no Modal de
        Agendar) na sugestao no banco. Chamado ANTES de
        _build_l1_task_payload — garante que a task no L1 reflete o
        que o operador editou. Marca review_status='editado' implicito
        (o caller pode sobrescrever via entry.review_status).
        """
        if entry.override_task_subtype_external_id is not None:
            sugestao.task_subtype_id = int(
                entry.override_task_subtype_external_id,
            )
        if entry.override_responsible_user_external_id is not None:
            new_id = int(entry.override_responsible_user_external_id)
            # Se operador trocou pra ID diferente do que estava na sugestao,
            # marca o flag pra `_build_l1_task_payload` desligar a regra do
            # assistente. Operador na ponta da operacao tem ultima palavra.
            if int(sugestao.responsavel_sugerido_id or 0) != new_id:
                payload = dict(sugestao.payload_proposto or {})
                payload["responsible_overridden"] = True
                sugestao.payload_proposto = payload
            sugestao.responsavel_sugerido_id = new_id
        if entry.override_data_base is not None:
            sugestao.data_base = entry.override_data_base
        if entry.override_data_final_calculada is not None:
            sugestao.data_final_calculada = entry.override_data_final_calculada
        if entry.override_audiencia_hora is not None:
            sugestao.audiencia_hora = entry.override_audiencia_hora
        if entry.override_prazo_dias is not None:
            sugestao.prazo_dias = int(entry.override_prazo_dias)
        if entry.override_prazo_tipo is not None:
            sugestao.prazo_tipo = entry.override_prazo_tipo

        # Campos que vivem dentro de payload_proposto (priority,
        # description, notes). Preserva os outros campos do payload
        # (template_id, template_name, observacoes_ia, etc.).
        if (
            entry.override_priority is not None
            or entry.override_description is not None
            or entry.override_notes is not None
        ):
            payload = dict(sugestao.payload_proposto or {})
            if entry.override_priority is not None:
                payload["priority"] = entry.override_priority
            if entry.override_description is not None:
                # description tem cap de 250 chars no L1 — backend
                # _build_l1_task_payload trunca, mas tambem normalizamos
                # aqui pra payload_proposto refletir o que vai pro L1.
                payload["description"] = entry.override_description.strip()
            if entry.override_notes is not None:
                payload["notes"] = entry.override_notes.strip() or None
            sugestao.payload_proposto = payload

    def _create_task_in_legal_one(
        self,
        *,
        sugestao: PrazoInicialSugestao,
        intake: PrazoInicialIntake,
    ) -> int:
        """
        Cria a tarefa no L1 e vincula ao processo (quando há lawsuit_id).
        Retorna o task_id. Em falha, levanta exceção.
        """
        payload = self._build_l1_task_payload(sugestao=sugestao, intake=intake)
        logger.info(
            "prazos_iniciais.create_task: intake=%s sugestao=%s subType=%s cnj=%s",
            intake.id, sugestao.id, sugestao.task_subtype_id, intake.cnj_number,
        )
        # Com processo, a tarefa sai VINCULADA: vínculo que não pega cancela a
        # tarefa e reenvia (legal_one_vinculo_tarefa). Antes seguia com warning
        # e a tarefa ficava sem pasta na agenda (11/09/2026). Sem lawsuit_id,
        # fica avulsa no L1 — raro mas legítimo (intake sem match CNJ).
        if intake.lawsuit_id:
            from app.services.legal_one_vinculo_tarefa import criar_tarefa_na_pasta

            created = criar_tarefa_na_pasta(self.l1_client, payload, int(intake.lawsuit_id))
        else:
            created = self.l1_client.create_task(payload)
        if not created or not created.get("id"):
            # Mensagem humanizada do L1 quando disponível (ex: "Campos
            # obrigatórios não enviados: Data de publicação"). Fallback
            # pro genérico com identificador da sugestão.
            l1_detail = self.l1_client.format_last_create_task_error()
            raise RuntimeError(
                l1_detail
                or f"Legal One recusou a criação da tarefa (sugestão #{sugestao.id})."
            )
        return int(created["id"])

    def _create_custom_task(
        self,
        *,
        intake: PrazoInicialIntake,
        custom_task: "CustomTaskInput",
        confirmed_by_email: str,
        now: datetime,
    ) -> tuple[PrazoInicialSugestao, int]:
        """
        Cria tarefa avulsa no L1 (vinculada ao processo se houver
        lawsuit_id) e persiste sugestao sintetica pra rastreabilidade.
        Retorna (sugestao_pendente_de_db_add, task_id_criado_no_L1).

        Levanta ValueError ou RuntimeError em falha — caller faz
        rollback transacional.
        """
        if not custom_task.task_subtype_external_id:
            raise ValueError("Tarefa avulsa: task_subtype_external_id obrigatorio.")
        if not custom_task.responsible_user_external_id:
            raise ValueError("Tarefa avulsa: responsavel obrigatorio.")
        if not custom_task.description or not custom_task.description.strip():
            raise ValueError("Tarefa avulsa: description obrigatoria.")
        if custom_task.due_date is None:
            raise ValueError("Tarefa avulsa: due_date obrigatoria.")

        type_id = self._resolve_parent_type_id(
            custom_task.task_subtype_external_id,
        )
        if not type_id:
            raise ValueError(
                f"Tipo-pai do subTypeId={custom_task.task_subtype_external_id} "
                "nao encontrado no catalogo local — sincronize o catalogo de "
                "tasks do L1."
            )

        # Tarefa avulsa nao tem horario especifico - usa fim do dia BRT
        # (23:59:59) convertido pra UTC. publishDate em 00:00:00 BRT.
        # Mesma logica do path de sugestao via template.
        due_iso = self._brt_to_utc_iso(custom_task.due_date, None)
        publish_iso = self._brt_to_utc_iso(custom_task.due_date, dtime(0, 0, 0))

        description = custom_task.description.strip()
        if len(description) > _L1_DESCRIPTION_MAX_CHARS:
            description = (
                description[: _L1_DESCRIPTION_MAX_CHARS - 1].rstrip() + "…"
            )

        priority = custom_task.priority or "Normal"

        # Aplica regra do assistente quando o operador marcou o checkbox
        # "Atribuir ao assistente do responsavel" no modal de Tarefa
        # Avulsa. O `responsible_user_external_id` informado e' o lider/
        # responsavel principal — `resolve_assistant` traduz pro assistente
        # da squad dele.
        target_role = "assistente" if custom_task.assign_to_assistant else "principal"
        try:
            final_responsible_id, _resolution = self._resolve_final_responsible(
                candidate_user_external_id=int(custom_task.responsible_user_external_id),
                task_subtype_external_id=int(custom_task.task_subtype_external_id),
                target_role=target_role,
                office_external_id=intake.office_id,
            )
        except ValueError as exc:
            raise ValueError(
                f"Tarefa avulsa (target_role={target_role!r}): {exc}"
            ) from exc

        payload: dict[str, Any] = {
            "description": description,
            "priority": priority,
            "startDateTime": due_iso,
            "endDateTime": due_iso,
            "publishDate": publish_iso,
            "status": {"id": 0},
            "typeId": int(type_id),
            "subTypeId": int(custom_task.task_subtype_external_id),
            "participants": [
                {
                    "contact": {"id": final_responsible_id},
                    "isResponsible": True,
                    "isExecuter": True,
                    "isRequester": True,
                }
            ],
        }
        if custom_task.notes:
            payload["notes"] = custom_task.notes
        if intake.office_id:
            payload["responsibleOfficeId"] = int(intake.office_id)
            payload["originOfficeId"] = int(intake.office_id)

        logger.info(
            "prazos_iniciais.create_custom_task: intake=%s subType=%s cnj=%s",
            intake.id, custom_task.task_subtype_external_id, intake.cnj_number,
        )
        if intake.lawsuit_id:
            from app.services.legal_one_vinculo_tarefa import criar_tarefa_na_pasta

            # Mesma regra de _create_task_in_legal_one: sai vinculada ou reenvia.
            created = criar_tarefa_na_pasta(self.l1_client, payload, int(intake.lawsuit_id))
        else:
            created = self.l1_client.create_task(payload)
        if not created or not created.get("id"):
            l1_detail = self.l1_client.format_last_create_task_error()
            raise RuntimeError(
                l1_detail
                or f"Legal One recusou a criacao da tarefa avulsa (intake {intake.id})."
            )
        task_id = int(created["id"])

        # Sugestao sintetica — review_status=APROVADO ja na criacao
        # porque o operador acabou de submeter. created_task_id
        # preenchido pra refletir que a task ja existe no L1.
        sugestao = PrazoInicialSugestao(
            intake_id=intake.id,
            tipo_prazo=TIPO_PRAZO_AVULSA,
            data_base=custom_task.due_date,
            data_final_calculada=custom_task.due_date,
            task_subtype_id=int(custom_task.task_subtype_external_id),
            responsavel_sugerido_id=int(custom_task.responsible_user_external_id),
            payload_proposto={
                "is_custom_task": True,
                "priority": priority,
                "description": description,
                "notes": custom_task.notes,
            },
            review_status=SUGESTAO_REVIEW_APPROVED,
            reviewed_by_email=confirmed_by_email,
            reviewed_at=now,
            created_task_id=task_id,
        )
        return sugestao, task_id

    # ──────────────────────────────────────────────
    # Upload do PDF da habilitação no GED (Onda 3)
    # ──────────────────────────────────────────────

    def _upload_habilitacao_to_ged(
        self,
        intake: PrazoInicialIntake,
    ) -> int:
        """
        Upload do PDF da habilitação no GED do L1 vinculado ao processo.
        Retorna o `document_id`. Levanta LegalOneGedUploadError em falha.

        Idempotente: se `intake.ged_document_id` já existe, retorna ele
        sem fazer nada (evita duplicar upload em retries).

        Pré-condições:
          - `intake.lawsuit_id` preenchido (o GED L1 exige vínculo)
          - `intake.pdf_path` apontando pra arquivo físico ainda presente
        """
        if intake.ged_document_id:
            return int(intake.ged_document_id)

        if not intake.lawsuit_id:
            raise LegalOneGedUploadError(
                f"Intake {intake.id} não tem lawsuit_id — GED requer vínculo a processo."
            )

        # Cascade pro PDF da habilitacao:
        #   - habilitacao_pdf_path (USER_UPLOAD: campo dedicado).
        #   - pdf_path (EXTERNAL_API: pdf principal ja eh a habilitacao).
        # Antes desse fix (2026-05-06) o codigo so olhava pdf_path,
        # entao em USER_UPLOAD o GED upload pegava a integra do processo
        # ou falhava — ambos errados.
        source_path = (
            getattr(intake, "habilitacao_pdf_path", None)
            or intake.pdf_path
        )
        if not source_path:
            raise LegalOneGedUploadError(
                f"Intake {intake.id} sem PDF de habilitacao "
                f"(pdf_path={intake.pdf_path!r}, habilitacao_pdf_path="
                f"{getattr(intake, 'habilitacao_pdf_path', None)!r}). "
                "Arquivo já foi limpo ou nunca chegou."
            )

        try:
            absolute = pdf_storage.resolve_pdf_path(source_path)
        except ValueError as exc:
            raise LegalOneGedUploadError(
                f"path do PDF inválido no intake {intake.id}: {exc}"
            ) from exc

        if not absolute.exists():
            raise LegalOneGedUploadError(
                f"PDF físico não encontrado em {absolute} (intake {intake.id})."
            )

        file_bytes = absolute.read_bytes()
        if not file_bytes:
            raise LegalOneGedUploadError(
                f"Arquivo PDF vazio (intake {intake.id}, path={source_path})."
            )

        # Upload via API ECM oficial. typeId formato "type_N" descoberto
        # empiricamente em 2026-05-04 — mapa completo no comentario de
        # legal_one_client.upload_document_to_ged. Default em settings:
        # "type_48" (Documento / Habilitação).
        archive_name = (
            f"Habilitação — {intake.cnj_number}.pdf" if intake.cnj_number
            else f"Habilitação intake #{intake.id}.pdf"
        )
        description = (
            f"Habilitação nos autos (intake Flow #{intake.id}) — "
            f"CNJ {intake.cnj_number or '?'}"
        )

        document_id = LegalOneApiClient().upload_document_to_ged(
            file_bytes=file_bytes,
            file_name=absolute.name,
            litigation_id=int(intake.lawsuit_id),
            type_id=settings.prazos_iniciais_ged_type_id,
            archive_name=archive_name,
            description=description,
        )
        logger.info(
            "GED API upload OK: intake=%s lawsuit=%s document_id=%s size=%d",
            intake.id,
            intake.lawsuit_id,
            document_id,
            len(file_bytes),
        )
        return document_id

    def _cleanup_local_pdf(self, intake: PrazoInicialIntake) -> None:
        """
        Deleta o PDF local e zera `pdf_path` + `pdf_bytes`. Chamado logo
        após upload GED bem-sucedido. Best-effort — se a deleção falhar,
        loga mas não quebra o fluxo (cron de cleanup pega depois).
        """
        if not intake.pdf_path:
            return
        try:
            pdf_storage.delete_pdf(intake.pdf_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Falha ao apagar PDF local do intake %s (%s): %s",
                intake.id, intake.pdf_path, exc,
            )
            return
        intake.pdf_path = None
        intake.pdf_bytes = None

    def confirm_intake_scheduling(
        self,
        *,
        intake_id: int,
        confirmed_suggestions: Optional[list[ConfirmedSuggestionInput]],
        confirmed_by_email: str,
        confirmed_by_user_id: Optional[int] = None,
        confirmed_by_name: Optional[str] = None,
        custom_tasks: Optional[list[CustomTaskInput]] = None,
        enqueue_legacy_task_cancellation: bool = True,
        legacy_task_type_external_id: int = DEFAULT_LEGACY_TASK_TYPE_EXTERNAL_ID,
        legacy_task_subtype_external_id: int = DEFAULT_LEGACY_TASK_SUBTYPE_EXTERNAL_ID,
        create_tasks_in_l1: bool = True,
    ) -> dict:
        intake = self._load_intake(intake_id)
        if intake is None:
            raise ValueError("Intake não encontrado.")

        allowed_statuses = {
            INTAKE_STATUS_IN_REVIEW,
            INTAKE_STATUS_CLASSIFIED,
            INTAKE_STATUS_SCHEDULED,
            INTAKE_STATUS_SCHEDULE_ERROR,
        }
        if intake.status not in allowed_statuses:
            raise RuntimeError(
                f"Confirmação permitida apenas em EM_REVISAO / CLASSIFICADO / AGENDADO / ERRO_AGENDAMENTO. Status atual: {intake.status}."
            )

        by_id = {s.id: s for s in intake.sugestoes or []}
        now = self._utcnow()

        if confirmed_suggestions:
            selected: list[tuple[PrazoInicialSugestao, ConfirmedSuggestionInput]] = []
            for entry in confirmed_suggestions:
                sugestao = by_id.get(entry.suggestion_id)
                if sugestao is None:
                    raise ValueError(
                        f"Sugestão {entry.suggestion_id} não pertence ao intake {intake_id}."
                    )
                selected.append((sugestao, entry))
        elif custom_tasks:
            # Operador escolheu APENAS tarefas avulsas (desmarcou todas as
            # sugestoes da IA, ou nao havia sugestao agendavel). NAO roda
            # o fallback "agenda todas nao-rejeitadas" — respeita a
            # escolha explicita. Antes desse guard (2026-05-08), o
            # fallback puxava qualquer sugestao com
            # data_final_calculada=null pro lote e _build_l1_task_payload
            # explodia com "Sugestao N sem data_final_calculada".
            selected = []
        else:
            # Fallback historico (cliente nao passou nem sugestao nem
            # tarefa avulsa): agenda todas as sugestoes nao-rejeitadas.
            # Mantido pra compat com chamadores programaticos que ainda
            # dependem desse default.
            selected = [
                (s, ConfirmedSuggestionInput(suggestion_id=s.id))
                for s in intake.sugestoes or []
                if s.review_status != SUGESTAO_REVIEW_REJECTED
            ]

        if not selected and not custom_tasks:
            raise RuntimeError(
                "Nenhuma sugestão ou tarefa avulsa para confirmar o agendamento deste intake."
            )

        # ── FASE 1 — valida review_status de todas antes de tocar no L1 ──
        # Falha cedo em erro estrutural (sem review_status válido).
        for sugestao, entry in selected:
            target_status = entry.review_status or sugestao.review_status or SUGESTAO_REVIEW_PENDING
            if target_status == SUGESTAO_REVIEW_PENDING:
                target_status = SUGESTAO_REVIEW_APPROVED
            if target_status not in VALID_CONFIRM_REVIEW_STATUSES:
                raise ValueError(
                    f"review_status inválido para confirmação: {target_status!r}."
                )

        # ── FASE 2 — cria as tasks no L1 ATOMICAMENTE ──
        # Política Onda 1 (definida com a TI do MDR):
        #   - Se qualquer criação falhar, interrompe a leva inteira.
        #   - Sugestões que já tiverem created_task_id (vindo do frontend ou
        #     de uma tentativa anterior) NÃO são recriadas — idempotência.
        #   - Em caso de falha, intake vira ERRO_AGENDAMENTO, error_message
        #     guarda contexto, e a fila de cancelamento da task legada
        #     NÃO é enfileirada (evita cenário pior: cancela antiga e não
        #     cria nova → processo órfão).
        l1_created_ids: list[int] = []  # só novos, pra log
        if create_tasks_in_l1:
            try:
                for sugestao, entry in selected:
                    # Aplica overrides do operador (modal de Agendar)
                    # ANTES de qualquer decisao — assim a sugestao
                    # reflete o que foi efetivamente enviado pro L1.
                    if entry.has_any_override():
                        self._apply_overrides_to_sugestao(sugestao, entry)
                    # Prioridade: se o caller já passou created_task_id, usa
                    # (compat com quem criava via outro caminho); senão, se
                    # a sugestão já tem task persistido, também respeita.
                    preset = entry.created_task_id
                    if preset is not None:
                        sugestao.created_task_id = int(preset)
                        continue
                    if sugestao.created_task_id is not None:
                        continue
                    # Template "no-op" (pin014): sugestao casou template
                    # marcado pra finalizar caso sem providencia. Pula a
                    # criacao no L1 — o intake ainda sobe habilitacao e
                    # cancela a legacy task no dispatch_treatment_web.
                    if (sugestao.payload_proposto or {}).get("skip_task_creation"):
                        continue
                    # Cria no L1 de verdade
                    task_id = self._create_task_in_legal_one(
                        sugestao=sugestao, intake=intake,
                    )
                    sugestao.created_task_id = task_id
                    l1_created_ids.append(task_id)
            except Exception as exc:  # noqa: BLE001
                # Rollback lógico: reverte os review_status que ainda não
                # mexemos (FASE 3 não rodou) e marca intake como erro.
                # As sugestões que JÁ tinham created_task_id ficam — se
                # uma task de fato foi criada no L1 antes do erro, ela
                # existe lá; não dá pra deletar "pela metade".
                self.db.rollback()
                intake = self._load_intake(intake_id)  # recarrega pós-rollback
                if intake is not None:
                    intake.status = INTAKE_STATUS_SCHEDULE_ERROR
                    intake.error_message = (
                        f"Falha ao criar tarefa no Legal One: {str(exc)[:500]}"
                    )
                    self.db.commit()
                logger.exception(
                    "prazos_iniciais.confirm_intake_scheduling: falhou ao criar "
                    "task no L1 (intake_id=%s, criadas_antes_do_erro=%s): %s",
                    intake_id, l1_created_ids, exc,
                )
                raise RuntimeError(
                    f"Falha ao criar tarefa no Legal One (intake {intake_id}): {exc}"
                ) from exc

        # ── FASE 2.5 — DESACOPLADO (Onda 3 #5) ──
        # GED upload + cleanup do PDF local migrou pra
        # `dispatch_treatment_web` (chamado manualmente da Tratamento Web ou
        # automaticamente pelo worker periódico). Aqui só marcamos
        # `dispatch_pending=True` no fim da FASE 3 — o disparo acontece
        # depois, em transação separada.

        # ── FASE 2.6 — Tarefas avulsas (custom_tasks) ──
        # Operador adicionou tarefas no modal de Confirmar Agendamento que
        # nao casam com sugestao da IA. Sao criadas direto no L1 e
        # persistidas como sugestao sintetica (tipo_prazo='AVULSA',
        # review_status='aprovado', created_task_id preenchido) pra
        # rastreabilidade. Mesma politica atomica da FASE 2: falha em
        # qualquer uma → rollback + ERRO_AGENDAMENTO.
        custom_created_ids: list[int] = []
        if custom_tasks:
            try:
                for ct in custom_tasks:
                    new_sug, task_id = self._create_custom_task(
                        intake=intake,
                        custom_task=ct,
                        confirmed_by_email=confirmed_by_email,
                        now=now,
                    )
                    self.db.add(new_sug)
                    custom_created_ids.append(task_id)
                    l1_created_ids.append(task_id)
            except Exception as exc:  # noqa: BLE001
                self.db.rollback()
                intake = self._load_intake(intake_id)
                if intake is not None:
                    intake.status = INTAKE_STATUS_SCHEDULE_ERROR
                    intake.error_message = (
                        f"Falha ao criar tarefa avulsa: {str(exc)[:500]}"
                    )
                    self.db.commit()
                logger.exception(
                    "prazos_iniciais.confirm_intake_scheduling: falha em "
                    "tarefa avulsa (intake_id=%s): %s",
                    intake_id, exc,
                )
                raise RuntimeError(
                    f"Falha ao criar tarefa avulsa (intake {intake_id}): {exc}"
                ) from exc

        # ── FASE 3 — persiste review_status e promove intake ──
        # Só chega aqui se a FASE 2 completou sem exceção.
        confirmed_ids: list[int] = []
        created_task_ids: list[int] = list(custom_created_ids)
        for sugestao, entry in selected:
            # Se o operador editou QUALQUER campo no Modal de Agendar
            # (overrides), forca review_status='editado' — sobrescreve
            # default e qualquer "aprovado" implicito. Operador pode
            # passar review_status explicito pra sobrescrever isso.
            if entry.review_status:
                target_status = entry.review_status
            elif entry.has_any_override():
                target_status = SUGESTAO_REVIEW_EDITED
            else:
                target_status = sugestao.review_status or SUGESTAO_REVIEW_PENDING
                if target_status == SUGESTAO_REVIEW_PENDING:
                    target_status = SUGESTAO_REVIEW_APPROVED

            sugestao.review_status = target_status
            sugestao.reviewed_by_email = confirmed_by_email
            sugestao.reviewed_at = now
            if sugestao.created_task_id is not None:
                created_task_ids.append(int(sugestao.created_task_id))
            confirmed_ids.append(sugestao.id)

        # Template "no-op" (pin014): se TODAS as sugestoes confirmadas
        # vieram de template marcado pra finalizar sem providencia (sem
        # task criada no L1) E nao houve tarefa avulsa, o intake termina
        # como CONCLUIDO_SEM_PROVIDENCIA. Tarefa avulsa adiciona task ao
        # L1, entao SEMPRE promove pra AGENDADO.
        all_no_op = (
            not custom_created_ids
            and all(
                (s.created_task_id is None)
                and bool((s.payload_proposto or {}).get("skip_task_creation"))
                for s, _ in selected
            )
        )
        intake.status = (
            INTAKE_STATUS_COMPLETED_WITHOUT_PROVIDENCE
            if all_no_op
            else INTAKE_STATUS_SCHEDULED
        )
        intake.error_message = None
        # Registra QUEM tratou finalisticamente o intake (pin011)
        intake.treated_by_user_id = confirmed_by_user_id
        intake.treated_by_email = confirmed_by_email
        intake.treated_by_name = confirmed_by_name
        intake.treated_at = now

        # Onda 3 #5 — Disparo desacoplado: GED + enqueue cancel acontecem
        # depois, via `dispatch_treatment_web`. Marca pendente aqui.
        # O parâmetro `enqueue_legacy_task_cancellation` é mantido na
        # assinatura por compat, mas vira no-op aqui (o disparo é sempre
        # diferido). Se alguém setar False explicitamente, ainda assim
        # marcamos pendente — o operador decide depois se aciona.
        intake.dispatch_pending = True
        intake.dispatched_at = None
        intake.dispatch_error_message = None

        self.db.commit()
        self.db.refresh(intake)

        return {
            "intake": intake,
            "confirmed_suggestion_ids": confirmed_ids,
            "created_task_ids": created_task_ids,
            "legacy_task_cancellation_item": None,  # diferido — Onda 3 #5
        }

    # ──────────────────────────────────────────────
    # Finalizar sem providência (Caminho A)
    # ──────────────────────────────────────────────
    # Caso operacional: operador analisou o processo e determinou que
    # o banco NÃO precisa tomar nenhuma providência (ex.: sentença de
    # improcedência já transitada, extinção sem resolução do mérito,
    # processo arquivado). O fluxo é:
    #   1. Sobe habilitação no GED do L1 (reusa Onda 3 — idempotente).
    #   2. Apaga PDF local.
    #   3. Marca intake como CONCLUIDO_SEM_PROVIDENCIA.
    #   4. Enfileira cancelamento da task legada "Agendar Prazos".
    # NÃO cria nenhuma task nova no L1.

    # Status elegíveis pra finalizar sem providência. AGENDADO fica de
    # fora — se já tem task criada, finalizar sem providência seria
    # inconsistente (a task existiria sem cobrir a publicação original).
    _FINALIZE_WITHOUT_PROVIDENCE_ALLOWED_STATUSES = frozenset({
        INTAKE_STATUS_IN_REVIEW,
        INTAKE_STATUS_CLASSIFIED,
        INTAKE_STATUS_AWAITING_TEMPLATE_CONFIG,
        INTAKE_STATUS_SCHEDULE_ERROR,
        INTAKE_STATUS_COMPLETED_WITHOUT_PROVIDENCE,  # idempotente
    })

    def finalize_without_scheduling(
        self,
        *,
        intake_id: int,
        confirmed_by_email: str,
        confirmed_by_user_id: Optional[int] = None,
        confirmed_by_name: Optional[str] = None,
        notes: Optional[str] = None,
        enqueue_legacy_task_cancellation: bool = True,
        legacy_task_type_external_id: int = DEFAULT_LEGACY_TASK_TYPE_EXTERNAL_ID,
        legacy_task_subtype_external_id: int = DEFAULT_LEGACY_TASK_SUBTYPE_EXTERNAL_ID,
    ) -> dict:
        """
        Finaliza o intake sem criar tarefa no L1. Sobe habilitação pro
        GED, cancela a task legada, marca intake como
        CONCLUIDO_SEM_PROVIDENCIA.

        Idempotente: se já está nesse status, reexecuta os passos que
        ainda não terminaram (GED upload via `ged_document_id`, cleanup
        local via `pdf_path`, enfileiramento da legada).

        Levanta ValueError se intake não existe, RuntimeError se o
        status atual não permite essa operação ou se o GED falhar.
        """
        intake = self._load_intake(intake_id)
        if intake is None:
            raise ValueError("Intake não encontrado.")

        if intake.status not in self._FINALIZE_WITHOUT_PROVIDENCE_ALLOWED_STATUSES:
            raise RuntimeError(
                "Finalizar sem providência só é permitido nos status "
                f"{sorted(self._FINALIZE_WITHOUT_PROVIDENCE_ALLOWED_STATUSES)}. "
                f"Status atual: {intake.status}."
            )

        if not intake.lawsuit_id:
            raise RuntimeError(
                "Intake sem lawsuit_id — GED do L1 exige vínculo a processo. "
                "Reprocesse o CNJ antes ou cancele o intake."
            )

        # ── Fase 1 — DESACOPLADO (Onda 3 #5) ──
        # GED upload migrou pra `dispatch_treatment_web`. Aqui só
        # transicionamos o status + marcamos `dispatch_pending=True`.

        # ── Fase 2 — Status + metadados de auditoria ──
        now = self._utcnow()
        intake.status = INTAKE_STATUS_COMPLETED_WITHOUT_PROVIDENCE
        intake.error_message = None
        # Registra QUEM tratou finalisticamente o intake (pin011)
        intake.treated_by_user_id = confirmed_by_user_id
        intake.treated_by_email = confirmed_by_email
        intake.treated_by_name = confirmed_by_name
        intake.treated_at = now
        # Anexa notas de auditoria em metadata_json sem sobrescrever
        # o que já tem lá (metadata livre da automação externa).
        if notes:
            meta = dict(intake.metadata_json or {})
            finalize_log = meta.setdefault("finalize_without_providence", [])
            if isinstance(finalize_log, list):
                finalize_log.append({
                    "at": now.isoformat(),
                    "by": confirmed_by_email,
                    "notes": notes[:500],
                })
                intake.metadata_json = meta

        # ── Fase 3 — Marca pendente de disparo (Onda 3 #5) ──
        # Fila de cancelamento da legada migrou pra `dispatch_treatment_web`.
        intake.dispatch_pending = True
        intake.dispatched_at = None
        intake.dispatch_error_message = None

        self.db.commit()
        self.db.refresh(intake)

        logger.info(
            "intake %s finalizado sem providencia por %s — dispatch_pending=True",
            intake_id, confirmed_by_email,
        )

        return {
            "intake": intake,
            "legacy_task_cancellation_item": None,  # diferido — Onda 3 #5
        }

    # ──────────────────────────────────────────────
    # Onda 3 #5 — Disparo desacoplado: Tratamento Web
    # ──────────────────────────────────────────────
    # Após o operador confirmar (ou finalizar sem providência) o intake
    # no HITL, o status vai pra AGENDADO/CONCLUIDO_SEM_PROVIDENCIA com
    # `dispatch_pending=True`. O disparo de GED + cancel da legada
    # acontece aqui — chamado:
    #   • Manualmente, via botão "Disparar agora" na Tratamento Web
    #   • Automaticamente, via worker periódico (Onda 3 #6) com batch_limit
    #
    # Idempotente: GED upload pula se `ged_document_id` já existe;
    # enqueue cancel é idempotente via queue_service.sync_item_from_intake.
    # Se algum passo falhar, mantém `dispatch_pending=True` e grava o erro
    # em `dispatch_error_message` pra retry posterior.

    def dispatch_treatment_web(
        self,
        *,
        intake_id: int,
        legacy_task_type_external_id: int = DEFAULT_LEGACY_TASK_TYPE_EXTERNAL_ID,
        legacy_task_subtype_external_id: int = DEFAULT_LEGACY_TASK_SUBTYPE_EXTERNAL_ID,
    ) -> dict:
        """Executa o disparo desacoplado: GED upload + enqueue cancel.

        Pré-condições:
          - intake.dispatch_pending == True (caso contrário, no-op)
          - intake.lawsuit_id setado (GED exige vínculo a processo)
          - intake.status em {AGENDADO, CONCLUIDO_SEM_PROVIDENCIA} —
            os únicos terminais que disparam tratamento web

        Comportamento:
          - GED upload (idempotente via ged_document_id)
          - Cleanup do PDF local após GED
          - Enqueue na fila de cancelamento da legada (force_queue=True)
          - Marca dispatch_pending=False, dispatched_at=now
          - Em erro: dispatch_pending fica True, dispatch_error_message
            recebe o motivo, exception é re-levantada
        """
        intake = self._load_intake(intake_id)
        if intake is None:
            raise ValueError("Intake não encontrado.")

        if not intake.dispatch_pending:
            # Idempotente: já disparado.
            return {
                "intake": intake,
                "legacy_task_cancellation_item": None,
                "skipped": True,
                "reason": "dispatch_pending=False (já disparado)",
            }

        if intake.status not in (
            INTAKE_STATUS_SCHEDULED,
            INTAKE_STATUS_COMPLETED_WITHOUT_PROVIDENCE,
            INTAKE_STATUS_DEVOLUCAO_PENDING,
        ):
            raise RuntimeError(
                "Disparo só é permitido em AGENDADO, "
                "CONCLUIDO_SEM_PROVIDENCIA ou DEVOLUCAO_PENDENTE. "
                f"Status atual: {intake.status}."
            )

        if not intake.lawsuit_id:
            raise RuntimeError(
                "Intake sem lawsuit_id — GED do L1 exige vínculo a processo."
            )

        # ── Fase 1 — Upload GED (idempotente) ──
        try:
            if not intake.ged_document_id:
                document_id = self._upload_habilitacao_to_ged(intake)
                intake.ged_document_id = int(document_id)
                intake.ged_uploaded_at = self._utcnow()
                self._cleanup_local_pdf(intake)
                self.db.commit()
        except LegalOneGedUploadError as exc:
            self.db.rollback()
            intake = self._load_intake(intake_id)
            if intake is not None:
                intake.dispatch_error_message = (
                    f"GED upload falhou: {str(exc)[:500]}"
                )
                self.db.commit()
            logger.exception(
                "dispatch_treatment_web: GED falhou (intake_id=%s): %s",
                intake_id, exc,
            )
            raise RuntimeError(
                f"Falha no upload da habilitação no GED do Legal One: {exc}"
            ) from exc

        # ── Fase 2 — Enqueue cancel da legada (idempotente) ──
        # USER_UPLOAD pula essa fase: o operador subiu o PDF do processo
        # manualmente, então NÃO existe task legada "Agendar Prazos" no
        # L1 pra cancelar — fazer GED já é suficiente. Antes desse guard
        # (2026-05-06) o dispatch enfileirava mesmo assim, batia
        # task_not_found no L1, e o item ficava acumulando na fila.
        queue_item = None
        if intake.source == INTAKE_SOURCE_EXTERNAL_API:
            try:
                queue_item = self.queue_service.sync_item_from_intake(
                    intake,
                    commit=False,
                    legacy_task_type_external_id=legacy_task_type_external_id,
                    legacy_task_subtype_external_id=legacy_task_subtype_external_id,
                    force_queue=True,
                )
            except Exception as exc:  # noqa: BLE001
                self.db.rollback()
                intake = self._load_intake(intake_id)
                if intake is not None:
                    intake.dispatch_error_message = (
                        f"Enqueue cancel falhou: {str(exc)[:500]}"
                    )
                    self.db.commit()
                logger.exception(
                    "dispatch_treatment_web: enqueue cancel falhou (intake_id=%s): %s",
                    intake_id, exc,
                )
                raise RuntimeError(
                    f"Falha ao enfileirar cancelamento da legada: {exc}"
                ) from exc
        else:
            logger.info(
                "dispatch_treatment_web: USER_UPLOAD intake_id=%s — pulando "
                "enqueue cancel (não há task legada no L1).",
                intake_id,
            )

        # ── Fase 3 — Marca como disparado ──
        intake.dispatch_pending = False
        intake.dispatched_at = self._utcnow()
        intake.dispatch_error_message = None

        self.db.commit()
        self.db.refresh(intake)
        if queue_item is not None:
            self.db.refresh(queue_item)

        logger.info(
            "dispatch_treatment_web ok (intake_id=%s, ged=%s, legacy_queue=%s)",
            intake_id,
            intake.ged_document_id,
            queue_item.id if queue_item else None,
        )

        return {
            "intake": intake,
            "legacy_task_cancellation_item": (
                self.queue_service._item_to_dict(queue_item) if queue_item else None
            ),
            "skipped": False,
        }
