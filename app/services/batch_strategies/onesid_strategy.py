import logging
import asyncio
from datetime import datetime, timezone, time
from zoneinfo import ZoneInfo
from app.api.v1.schemas import BatchTaskCreationRequest
from app.models.batch_execution import BatchExecution, BatchExecutionItem
from .base_strategy import BaseStrategy
from app.services.mail_service import send_failure_report

# --- CONSTANTES PADRÃO DO ONESID ---
# Tipo "OneSid" / Subtipo "Retorno de Subsídios" no Legal One.
ONESID_DEFAULT_TYPE_ID = 30
ONESID_DEFAULT_SUBTYPE_ID = 1300
DEFAULT_TASK_STATUS_ID = 0

class OnesidStrategy(BaseStrategy):
    """
    Estratégia para criar tarefas a partir do Onesid.
    Refatorada para suportar retry resiliente salvando o payload original.
    """

    def _parse_onesid_date(self, date_str: str) -> str:
        """
        Converte datas do formato DD/MM/YYYY para ISO 8601 UTC (Final do dia em Brasília).
        """
        try:
            local_date = datetime.strptime(date_str, "%d/%m/%Y")
            local_tz = ZoneInfo("America/Sao_Paulo")
            # Define para 23:59:59 horário de Brasília
            aware_date = datetime.combine(local_date.date(), time(23, 59, 59)).replace(tzinfo=local_tz)
            return aware_date.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
        except (ValueError, TypeError):
            raise ValueError(f"Data inválida (esperado DD/MM/YYYY): {date_str}")

    def _same_day_deadline_iso(self) -> str:
        local_tz = ZoneInfo("America/Sao_Paulo")
        current_date = datetime.now(local_tz).date()
        aware_date = datetime.combine(current_date, time(23, 59, 59)).replace(tzinfo=local_tz)
        return aware_date.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    async def process_batch(self, request: BatchTaskCreationRequest, execution_log: BatchExecution) -> dict:
        """
        Recebe o lote, salva os dados crus no banco e dispara o processamento item a item.
        """
        logging.info(f"Processando lote 'Onesid' com {len(request.processos)} itens.")
        success_count = 0
        failed_items = []
        
        for item_model in request.processos:
            # 1. Converter para dicionário
            try:
                item_payload = item_model.model_dump(exclude_unset=True)
            except AttributeError:
                item_payload = item_model.dict(exclude_unset=True)

            cnj = item_payload.get("numero_processo", "N/A")

            # 2. Criar e Persistir o Item com 'input_data' (FUNDAMENTAL PARA O RETRY)
            log_item = BatchExecutionItem(
                process_number=cnj, 
                execution_id=execution_log.id,
                input_data=item_payload,  # <--- Salva os dados originais aqui
                status="PENDENTE"
            )
            self.db.add(log_item)
            self.db.commit()

            # 3. Processar item isolado
            success = await self.process_single_item(log_item, item_payload)
            
            if success:
                success_count += 1
            else:
                failed_items.append({"cnj": cnj, "motivo": log_item.error_message})
            
            await asyncio.sleep(0.05)

        # Envio de email em caso de falhas
        if failed_items:
            try:
                await asyncio.to_thread(send_failure_report, failed_items, "Onesid")
            except Exception as e:
                logging.error(f"Erro ao enviar relatório de falhas Onesid: {e}")

        return {"sucesso": success_count, "falhas": len(failed_items), "detalhes_falhas": failed_items}

    async def process_single_item(self, log_item: BatchExecutionItem, item_payload: dict, caches: dict = None) -> bool:
        """
        Lógica isolada para processar um único item do Onesid.
        Chamado pelo process_batch (fluxo normal) e pelo retry_failed_items (fluxo de erro).
        """
        try:
            # --- 1. Extração e Validação ---
            cnj = item_payload.get("numero_processo")
            descricao = item_payload.get("observacao") or item_payload.get("descricao") or ""
            id_responsavel_onesid = item_payload.get("id_responsavel")

            if not cnj:
                raise ValueError("Campo obrigatorio 'numero_processo' esta faltando.")
            if id_responsavel_onesid in (None, ""):
                raise ValueError("Campo obrigatorio 'id_responsavel' esta faltando.")

            # --- 2. Preparação de Dados ---
            deadline_iso = self._same_day_deadline_iso()
            publish_date = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

            # --- 3. Busca no Legal One ---
            lawsuit = self.client.search_lawsuit_by_cnj(cnj)
            if not lawsuit or not lawsuit.get('id'):
                raise Exception(f"Processo {cnj} não encontrado no Legal One.")
            
            lawsuit_id = lawsuit['id']
            office_id = lawsuit.get('responsibleOfficeId')
            if not office_id:
                raise Exception("Processo sem escritório responsável (responsibleOfficeId).")

            # --- 4. Montagem do Payload ---
            task_payload = {
                "description": f"ONESID: {descricao}" if descricao else "ONESID",
                "priority": "Normal",
                "startDateTime": deadline_iso,
                "endDateTime": deadline_iso,
                "publishDate": publish_date,
                "status": { "id": DEFAULT_TASK_STATUS_ID },
                "typeId": ONESID_DEFAULT_TYPE_ID,
                "subTypeId": ONESID_DEFAULT_SUBTYPE_ID,
                "responsibleOfficeId": office_id,
                "originOfficeId": office_id,
                "participants": []
            }

            task_payload["participants"].append({
                "contact": {"id": id_responsavel_onesid},
                "isResponsible": True,
                "isExecuter": True,
                "isRequester": True
            })

            # --- 5/6. Criação da Tarefa já vinculada ao Processo ---
            # (vínculo que não pega cancela e reenvia; esgotou, cai no except)
            from app.services.legal_one_vinculo_tarefa import criar_tarefa_na_pasta

            created_task = criar_tarefa_na_pasta(self.client, task_payload, lawsuit_id)
            if not created_task or not created_task.get('id'):
                raise Exception("API retornou sucesso mas sem ID da tarefa.")

            # --- 7. Sucesso ---
            log_item.status = "SUCESSO"
            log_item.created_task_id = created_task['id']
            log_item.error_message = None
            self.db.commit()
            return True

        except Exception as e:
            # --- 8. Falha ---
            log_item.status = "FALHA"
            log_item.error_message = str(e)
            self.db.commit()
            logging.error(f"Falha item Onesid (CNJ: {item_payload.get('numero_processo')}): {e}")
            return False
