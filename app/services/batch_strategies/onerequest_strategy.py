# app/services/batch_strategies/onerequest_strategy.py
import logging
import asyncio
from datetime import datetime, timezone, time
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from sqlalchemy.exc import NoResultFound, MultipleResultsFound

from app.api.v1.schemas import BatchTaskCreationRequest
from app.models.batch_execution import BatchExecution, BatchExecutionItem
from app.models.legal_one import LegalOneUser
from .base_strategy import BaseStrategy
from app.services.mail_service import send_failure_report

# --- PONTO DE CONFIGURAÇÃO CENTRAL ---
SECTOR_TASK_MAPPING = {
    "BB Réu": (15, 967),
    "BB Autor": (28, 969),
    "BB Recurso": (19, 968),
    "BB Execução e Encerramento": (20, 1058)
}
# --- VALORES PADRÃO ---
DEFAULT_TYPE_ID = 15
DEFAULT_SUBTYPE_ID = 967 
DEFAULT_TASK_STATUS_ID = 0  # 0 = Pendente

class OnerequestStrategy(BaseStrategy):
    """
    Estratégia para criar tarefas em lote a partir da fonte Onerequest.
    Refatorada para suportar reprocessamento resiliente (salvando input_data).
    """

    def _parse_and_format_deadline(self, date_str: str) -> str:
        """
        Converte 'DD/MM/YYYY' para ISO 8601 UTC (final do dia em Brasília).
        """
        try:
            local_deadline_date = datetime.strptime(date_str, "%d/%m/%Y")
            local_tz = ZoneInfo("America/Sao_Paulo")
            aware_deadline = datetime.combine(local_deadline_date.date(), time(16, 59, 59)).replace(tzinfo=local_tz)
            utc_deadline = aware_deadline.astimezone(timezone.utc)
            return utc_deadline.isoformat().replace('+00:00', 'Z')
        except (ValueError, TypeError) as e:
            raise ValueError(f"Prazo inválido: '{date_str}'")

    def _get_task_type_ids(self, sector_name: str) -> tuple[int, int]:
        """
        Busca o Tipo e o Subtipo de Tarefa com base no nome do setor.
        """
        mapping = SECTOR_TASK_MAPPING.get(sector_name) 
        if not mapping:
            logging.warning(f"Setor '{sector_name}' não encontrado no mapeamento. Usando IDs padrão.")
            return DEFAULT_TYPE_ID, DEFAULT_SUBTYPE_ID
        return mapping 

    def _build_task_description(self, item_payload: dict) -> str:
        """
        Constrói a descrição da tarefa.
        """
        vencimento = item_payload.get("vencimento", "N/A") 
        titulo = item_payload.get("titulo", "N/A")
        npj_direcionador = item_payload.get("npj_direcionador", "N/A")
        numero_solicitacao = item_payload.get("numero_solicitacao", "N/A")

        description = f" PF: {vencimento} | {titulo} | NPJ: {npj_direcionador} | DMI: {numero_solicitacao}"

        API_LIMIT = 250
        if len(description) > API_LIMIT:
            description = description[:(API_LIMIT - 3)] + "..."

        return description.strip()

    def _build_task_notes(self, item_payload: dict) -> str:
        """
        Constrói a observação da tarefa.
        """
        anotacao = item_payload.get("anotacao", "")
        texto_dmi = item_payload.get("texto_dmi", "")
        
        anotacao_formatada = f"ANOTAÇÃO ONEREQUEST:\n{anotacao}\n\n" if anotacao else ""
        dmi_formatado = f"TEXTO DMI:\n{texto_dmi}" if texto_dmi else ""
        
        task_notes = (anotacao_formatada + dmi_formatado).strip()
        
        API_NOTES_LIMIT = 4000 
        if len(task_notes) > API_NOTES_LIMIT:
            task_notes = task_notes[:(API_NOTES_LIMIT - 3)] + "..."
            
        return task_notes

    def _get_failure_notification_recipients(self) -> list[str]:
        rows = (
            self.db.query(LegalOneUser.email)
            .filter(
                LegalOneUser.is_active == True,  # noqa: E712
                LegalOneUser.notify_onerequest_errors == True,  # noqa: E712
                LegalOneUser.email.isnot(None),
            )
            .order_by(LegalOneUser.email)
            .all()
        )
        recipients: list[str] = []
        for (email,) in rows:
            normalized = (email or "").strip()
            if normalized and normalized not in recipients:
                recipients.append(normalized)
        return recipients

    async def process_batch(self, request: BatchTaskCreationRequest, execution_log: BatchExecution) -> dict: 
        """
        Orquestra o processamento do lote: Salva no BD -> Chama processador individual.
        """
        logging.info(f"Processando lote 'Onerequest' com {len(request.processos)} itens.")
        success_count = 0
        failed_items = []
        
        for item_model in request.processos:
            # 1. Converter Payload para Dict (para salvar no JSON)
            try:
                item_payload = item_model.model_dump(exclude_unset=True) 
            except AttributeError:
                item_payload = item_model.dict(exclude_unset=True) 

            # Extração segura do CNJ para o log inicial
            process_number = "N/A"
            if isinstance(item_payload, dict):
                process_number = item_payload.get("numero_processo", "N/A")

            # 2. Criar e Persistir o Item com 'input_data' (AQUI ESTÁ A MÁGICA DO RETRY)
            log_item = BatchExecutionItem(
                process_number=process_number, 
                execution_id=execution_log.id,
                input_data=item_payload,  # <--- Salvamos os dados originais
                status="PENDENTE"
            )
            self.db.add(log_item)
            self.db.commit() # Garante que o item existe no banco antes de processar
            
            # 3. Chamar o Processador Individual
            success = await self.process_single_item(log_item, item_payload)
            
            if success:
                success_count += 1
            else:
                failed_items.append({
                    "cnj": process_number,
                    "motivo": log_item.error_message,
                    "execution_id": execution_log.id,
                    "item_id": log_item.id,
                })
            
            await asyncio.sleep(0.05) # Yield para não bloquear

        # --- ENVIO DE E-MAIL DE ALERTA ---
        if failed_items:
            logging.info(f"Detectadas {len(failed_items)} falhas. Enviando alerta...")
            try:
                recipients = self._get_failure_notification_recipients()
                if not recipients:
                    logging.warning(
                        "Falhas OneRequest detectadas, mas nenhum usuario ativo "
                        "esta com Notificacao OneRequest habilitada."
                    )
                else:
                    await asyncio.to_thread(
                        send_failure_report,
                        failed_items,
                        "OneRequest",
                        recipients=recipients,
                    )
            except Exception as e:
                logging.error(f"Erro ao enviar e-mail de falha: {e}")

        logging.info(f"Processamento 'Onerequest' concluído: {success_count} sucessos, {len(failed_items)} falhas.")
        return {"sucesso": success_count, "falhas": len(failed_items), "detalhes_falhas": failed_items}

    async def process_single_item(self, log_item: BatchExecutionItem, item_payload: dict, caches: dict = None) -> bool:
        """
        Processa um único item. Usado tanto pelo lote inicial quanto pelo Retry.
        """
        try:
            publish_datetime_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

            if not isinstance(item_payload, dict):
                raise ValueError(f"Item inválido (esperado dict, recebeu {type(item_payload)}).")

            # --- 1. Extração e Validação ---
            numero_processo = item_payload.get("numero_processo")
            responsible_user_id = item_payload.get("id_responsavel")
            prazo_str = item_payload.get("prazo")
            vencimento_str = item_payload.get("vencimento") 
            setor = item_payload.get("setor") 

            if not all([numero_processo, responsible_user_id, prazo_str, vencimento_str, setor]):
                raise ValueError("Campos obrigatórios faltando (numero_processo, id_responsavel, prazo, vencimento, setor).")
            
            # --- 2. Busca e Mapeamento ---
            type_id, task_subtype_id = self._get_task_type_ids(setor)
            
            end_datetime_iso = self._parse_and_format_deadline(prazo_str)
            start_datetime_iso = end_datetime_iso 
            
            task_description = self._build_task_description(item_payload)
            task_notes = self._build_task_notes(item_payload)

            # --- 3. Execução (Busca no Legal One) ---
            lawsuit_data = self.client.search_lawsuit_by_cnj(numero_processo)
            if not lawsuit_data or not lawsuit_data.get('id'):
                raise Exception(f"Processo (CNJ: {numero_processo}) não encontrado no Legal One.")
            
            lawsuit_id = lawsuit_data['id']
            office_id = lawsuit_data.get('responsibleOfficeId')
            if not office_id:
                raise Exception(f"Processo {numero_processo} não possui 'responsibleOfficeId'.")
            
            # --- 4. Montagem do Payload ---
            task_payload = {
                "description": task_description,      
                "startDateTime": start_datetime_iso,  
                "endDateTime": end_datetime_iso,      
                "publishDate": publish_datetime_iso, 
                "status": { "id": DEFAULT_TASK_STATUS_ID },
                "typeId": type_id,                    
                "subTypeId": task_subtype_id,         
                "responsibleOfficeId": office_id,     
                "originOfficeId": office_id,          
                "participants": [
                    {
                        "contact": {"id": responsible_user_id},
                        "isResponsible": True, "isExecuter": True, "isRequester": True
                    }
                ]
            }
            
            if task_notes:
                task_payload['notes'] = task_notes
            
            # --- 5. Criação na API (sai vinculada; sem vínculo, cancela e reenvia) ---
            from app.services.legal_one_vinculo_tarefa import criar_tarefa_na_pasta

            created_task = criar_tarefa_na_pasta(self.client, task_payload, lawsuit_id)
            if not created_task or not created_task.get('id'):
                raise Exception("Falha na criação da tarefa (resposta inválida da API).")

            task_id = created_task['id']

            # --- 6. Sucesso ---
            log_item.status = "SUCESSO"
            log_item.created_task_id = task_id
            log_item.error_message = None 
            self.db.commit()
            return True

        except Exception as e:
            # --- 7. Falha ---
            error_msg = str(e)
            log_item.status = "FALHA"
            log_item.error_message = error_msg
            self.db.commit()
            logging.error(f"Falha ao processar OneRequest (CNJ: {item_payload.get('numero_processo', 'N/A')}): {error_msg}")
            return False
