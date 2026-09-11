# app/services/task_creation_service.py

import logging
from pydantic import BaseModel, Field
from typing import List, Dict, Any
from sqlalchemy.orm import Session, joinedload
from datetime import datetime, timezone

from app.services.legal_one_client import LegalOneApiClient
from app.models.legal_one import LegalOneOffice, LegalOneTaskType, LegalOneTaskSubType

# --- Dicionário de Mapeamento de Status ---
# Mapeia o ID interno (usado no nosso sistema/frontend) para o ID externo (esperado pelo Legal One)

DEFAULT_STATUS_ID = 0 # ID externo para "Pendente"

# --- Exceções e DTOs (inalterados) ---
class LawsuitNotFoundError(Exception):
    def __init__(self, cnj: str):
        self.cnj = cnj
        super().__init__(f"Nenhum processo (lawsuit) encontrado para o CNJ: {cnj}")

class TaskCreationError(Exception):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(f"Falha ao criar a tarefa no Legal One: {detail}")

class TaskLinkingError(Exception):
    pass
    
class InvalidDataError(Exception):
    pass

class TaskParticipant(BaseModel):
    contact_id: int = Field(..., description="ID EXTERNO do contato (usuário) no Legal One.")
    is_responsible: bool = False
    is_requester: bool = False
    is_executer: bool = True

class TaskCreationRequest(BaseModel):
    cnj_number: str
    task_payload: Dict[str, Any]
    participants: List[TaskParticipant]

class TaskCreationService:
    def __init__(self, db: Session):
        self.db = db
        self.legal_one_client = LegalOneApiClient()
        self.logger = logging.getLogger(__name__)

    def _build_final_payload(self, request: TaskCreationRequest) -> Dict[str, Any]:
        """
        Constrói o payload final com a estrutura e ORDEM corretas,
        traduzindo IDs internos para externos.
        """
        source_payload = request.task_payload.copy()
        
        payload = {}

        # 1. Validação e Tradução de TypeId e SubTypeId
        internal_type_id = source_payload.get('typeId')
        internal_subtype_id = source_payload.get('subTypeId')

        if not internal_type_id or not internal_subtype_id:
            raise InvalidDataError("Os campos 'typeId' e 'subTypeId' (IDs internos) são obrigatórios.")

        sub_type = self.db.query(LegalOneTaskSubType).options(
            joinedload(LegalOneTaskSubType.parent_type)
        ).filter(LegalOneTaskSubType.id == internal_subtype_id).first()

        if not sub_type:
            raise InvalidDataError(f"Subtipo de tarefa com ID interno {internal_subtype_id} não encontrado.")
        
        if sub_type.parent_type.id != internal_type_id:
            raise InvalidDataError(f"O subtipo {internal_subtype_id} não pertence ao tipo {internal_type_id}.")

        payload['typeId'] = sub_type.parent_type.external_id
        payload['subTypeId'] = sub_type.external_id

        # 2. Campos de texto
        payload['description'] = source_payload.get('description', 'Tarefa criada via sistema.')
        if len(payload['description']) < 3:
            payload['description'] += " (auto)"
        payload['priority'] = source_payload.get('priority', 'Normal')

        # 3. Grupo de Datas
        now_utc = datetime.now(timezone.utc)
        payload['publishDate'] = now_utc.isoformat().replace('+00:00', 'Z')
        payload['startDateTime'] = source_payload.get('startDateTime')
        payload['endDateTime'] = source_payload.get('endDateTime')

        # 4. Validação e Tradução de IDs de Referência (Offices)
        internal_office_id = source_payload.get('originOfficeId')
        office = self.db.query(LegalOneOffice).filter(LegalOneOffice.id == internal_office_id).first()
        if not office:
            raise InvalidDataError(f"Escritório com ID interno {internal_office_id} não encontrado.")
        payload['originOfficeId'] = office.external_id
        payload['responsibleOfficeId'] = source_payload.get('responsibleOfficeId')

        # --- SEÇÃO DE STATUS ATUALIZADA ---
        # 5. Status e Participantes
        internal_status_id = source_payload.get('status', {}).get('id')
        status_id_to_send = internal_status_id if internal_status_id is not None else DEFAULT_STATUS_ID
        payload['status'] = { "id": status_id_to_send }

        if not request.participants:
            raise InvalidDataError("A requisição deve conter pelo menos um participante.")
        payload['participants'] = [
            {
                "contact": {"id": p.contact_id},
                "isResponsible": p.is_responsible,
                "isRequester": p.is_requester,
                "isExecuter": p.is_executer
            }
            for p in request.participants
        ]

        return payload

    def create_full_task_process(self, request: TaskCreationRequest) -> Dict[str, Any]:
        self.logger.info(f"Iniciando processo de criação de tarefa para o CNJ: {request.cnj_number}")
        
        lawsuit = self.legal_one_client.search_lawsuit_by_cnj(request.cnj_number)
        if not lawsuit or 'id' not in lawsuit:
            raise LawsuitNotFoundError(request.cnj_number)
        
        lawsuit_id = lawsuit['id']
        self.logger.info(f"Processo ID {lawsuit_id} encontrado.")

        if 'responsibleOfficeId' in lawsuit and lawsuit['responsibleOfficeId']:
            request.task_payload['responsibleOfficeId'] = lawsuit['responsibleOfficeId']
        else:
            raise InvalidDataError("O processo encontrado não possui um escritório responsável.")

        final_payload = self._build_final_payload(request)
        
        self.logger.info(f"Criando tarefa com payload definitivo e ordenado: {final_payload}")
        from app.services.legal_one_vinculo_tarefa import (
            TarefaSemVinculoError,
            criar_tarefa_na_pasta,
        )

        try:
            # Sai vinculada: vínculo que não pega cancela e reenvia; só vira
            # erro esgotado — e aí sem tarefa solta no L1.
            created_task = criar_tarefa_na_pasta(self.legal_one_client, final_payload, lawsuit_id)
        except TarefaSemVinculoError as exc:
            self.logger.error(f"Falha crítica ao vincular a tarefa ao processo {lawsuit_id}: {exc}")
            raise TaskLinkingError(str(exc)) from exc
        if not created_task or 'id' not in created_task:
            raise TaskCreationError("A resposta da API na criação da tarefa não continha um ID válido.")

        task_id = created_task['id']
        self.logger.info(f"TAREFA CRIADA E VINCULADA! ID: {task_id}")

        return {
            "message": "Tarefa criada e vinculada com sucesso!",
            "created_task": created_task
        }