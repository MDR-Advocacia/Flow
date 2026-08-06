import logging

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.legal_one import (
    LegalOneOffice,
    LegalOneTaskSubType,
    LegalOneTaskType,
    LegalOneUser,
)
from app.services.legal_one_client import LegalOneApiClient

logging.basicConfig(level=logging.INFO)


class MetadataSyncService:
    def __init__(self, db: Session):
        self.db = db
        self.legal_one_client = LegalOneApiClient()
        self.logger = logging.getLogger(__name__)

    def sync_all_metadata(self) -> dict:
        self.logger.info("Iniciando sincronizacao completa de metadados...")
        summary = {
            "offices": False,
            "users": False,
            "task_types": False,
        }

        try:
            summary["offices"] = self.sync_offices()
            summary["users"] = self.sync_users()
            summary["task_types"] = self.sync_task_types_and_subtypes()
        except Exception as exc:
            self.logger.error("Erro critico durante a sincronizacao de metadados: %s", exc, exc_info=True)
            raise

        if all(summary.values()):
            self.logger.info("Sincronizacao completa de metadados concluida com sucesso.")
        else:
            self.logger.warning("Sincronizacao concluida com pendencias: %s", summary)

        return summary

    def sync_offices(self) -> bool:
        self.logger.info("Sincronizando escritorios (Offices)...")
        try:
            offices_data = self.legal_one_client.get_all_allocatable_areas()
            if not offices_data:
                self.logger.warning("Nenhum escritorio alocavel encontrado na API do Legal One.")
                return False

            with self.db.begin_nested():
                existing_offices = {office.external_id: office for office in self.db.query(LegalOneOffice).all()}

                for office_data in offices_data:
                    external_id = office_data.get("id")
                    if not external_id:
                        continue

                    office = existing_offices.get(external_id)
                    if office:
                        office.name = office_data.get("name")
                        office.path = office_data.get("path")
                        office.is_active = True
                    else:
                        self.db.add(
                            LegalOneOffice(
                                external_id=external_id,
                                name=office_data.get("name"),
                                path=office_data.get("path"),
                                is_active=True,
                            )
                        )

                active_external_ids = {office["id"] for office in offices_data if office.get("id")}
                for external_id, office in existing_offices.items():
                    if external_id not in active_external_ids:
                        office.is_active = False

            self.db.commit()
            self.logger.info("Sincronizacao de escritorios concluida.")
            return True
        except Exception as exc:
            self.db.rollback()
            self.logger.error("Erro ao sincronizar escritorios: %s", exc, exc_info=True)
            return False

    def sync_users(self) -> bool:
        self.logger.info("Sincronizando usuarios (Users)...")
        try:
            users_data = self.legal_one_client.get_all_users()
            if not users_data:
                self.logger.warning("Nenhum usuario encontrado na API do Legal One.")
                return False

            with self.db.begin_nested():
                existing_users = {user.external_id: user for user in self.db.query(LegalOneUser).all()}

                # Índice secundário por email para detectar usuários criados
                # manualmente (ex.: admin com external_id=0) e vinculá-los ao
                # external_id real do Legal One sem gerar UniqueViolation.
                existing_by_email = {u.email: u for u in existing_users.values() if u.email}

                for user_data in users_data:
                    external_id = user_data.get("id")
                    if not external_id:
                        continue

                    email = user_data.get("email")
                    user = existing_users.get(external_id)

                    if not user and email:
                        # Fallback: talvez exista pelo email (criado manualmente).
                        user = existing_by_email.get(email)
                        if user:
                            # Vincula ao external_id real; preserva role, senha
                            # e permissões que foram configurados manualmente.
                            user.external_id = external_id
                            existing_users[external_id] = user

                    if user:
                        user.name = user_data.get("name")
                        # NÃO sincroniza o e-mail de quem já tem login via Entra/SSO
                        # (last_sso_at preenchido). O e-mail virou IDENTIDADE DE LOGIN
                        # e passou a ser gerido só pelo Entra ID: sobrescrever com o
                        # e-mail do cadastro do Legal One revertia o login (ex.:
                        # institucional -> e-mail antigo), recriava conta pendente e
                        # jogava o usuário pra tela de espera a cada sync. Demais
                        # campos (nome/ativo) seguem sincronizando para os outros
                        # módulos — a sync continua intocada fora deste vínculo.
                        if user.last_sso_at is None:
                            user.email = email
                            user.is_active = user_data.get("isActive", False)
                        # Quem já loga por Entra: o status da CADEIRA do L1 não
                        # manda mais no acesso ao Flow. Caso real (06/08/2026):
                        # o usuário 10276 foi desativado no L1 na migração de
                        # e-mails (cadastro antigo em gmail), mas a pessoa segue
                        # na banca logando por SSO — o sync derrubava o acesso
                        # dela a cada rodada e o admin reativava na mão, num
                        # cabo de guerra. Desligamento de verdade é gerido no
                        # Entra (conta Microsoft morre) e no admin do Flow.
                    else:
                        new_user = LegalOneUser(
                            external_id=external_id,
                            name=user_data.get("name"),
                            email=email,
                            is_active=user_data.get("isActive", False),
                        )
                        self.db.add(new_user)
                        if email:
                            existing_by_email[email] = new_user

                active_external_ids = {
                    user["id"]
                    for user in users_data
                    if user.get("id") and user.get("isActive")
                }
                for external_id, user in existing_users.items():
                    # Usuário nascido do LOGIN SSO não existe no L1
                    # (external_id=None) — a varredura o desativava em TODA
                    # sincronização, e o admin reativava na mão até o sync
                    # seguinte derrubar de novo. Mesma razão acima pra quem tem
                    # last_sso_at: o L1 deixou de ser dono do acesso ao Flow.
                    if external_id is None or user.last_sso_at is not None:
                        continue
                    if external_id not in active_external_ids:
                        user.is_active = False

            self.db.commit()
            self.logger.info("Sincronizacao de usuarios concluida.")
            return True
        except Exception as exc:
            self.db.rollback()
            self.logger.error("Erro ao sincronizar usuarios: %s", exc, exc_info=True)
            return False

    def sync_task_types_and_subtypes(self) -> bool:
        self.logger.info("Iniciando sincronizacao de tipos e subtipos de tarefas...")
        try:
            self.logger.info("Buscando todos os tipos de tarefa (pais)...")
            parent_types_data = self.legal_one_client._paginated_catalog_loader(
                "/UpdateAppointmentTaskTypes",
                {"$filter": "isTaskType eq true", "$select": "id,name"},
            )
            self.logger.info("Encontrados %s tipos de tarefa pai.", len(parent_types_data))

            self.logger.info("Buscando todos os subtipos de tarefa (filhos)...")
            all_subtypes_data = self.legal_one_client._paginated_catalog_loader(
                "/UpdateAppointmentTaskSubtypes",
                {"$select": "id,name,parentTypeId"},
            )
            self.logger.info("Encontrados %s subtipos de tarefa.", len(all_subtypes_data))

            if not parent_types_data:
                self.logger.warning(
                    "Sincronizacao de tipos abortada: nenhum tipo pai foi retornado. O catalogo local foi preservado."
                )
                return False

            with self.db.begin_nested():
                self.logger.info("Atualizando tipos e subtipos sem remover registros referenciados...")

                existing_types = {
                    task_type.external_id: task_type
                    for task_type in self.db.query(LegalOneTaskType).all()
                }
                existing_subtypes = {
                    subtype.external_id: subtype
                    for subtype in self.db.query(LegalOneTaskSubType).all()
                }

                active_parent_ids: set[int] = set()
                created_parent_count = 0
                updated_parent_count = 0

                for parent_data in parent_types_data:
                    external_id = parent_data.get("id")
                    name = parent_data.get("name")
                    if external_id is None or not name:
                        continue

                    active_parent_ids.add(external_id)
                    task_type = existing_types.get(external_id)
                    if task_type:
                        task_type.name = name
                        task_type.is_active = True
                        updated_parent_count += 1
                    else:
                        task_type = LegalOneTaskType(
                            external_id=external_id,
                            name=name,
                            is_active=True,
                        )
                        self.db.add(task_type)
                        existing_types[external_id] = task_type
                        created_parent_count += 1

                for external_id, task_type in existing_types.items():
                    if external_id not in active_parent_ids:
                        task_type.is_active = False

                self.db.flush()

                active_subtype_ids: set[int] = set()
                created_subtype_count = 0
                updated_subtype_count = 0
                skipped_subtype_count = 0

                for child_data in all_subtypes_data:
                    external_id = child_data.get("id")
                    name = child_data.get("name")
                    parent_id = child_data.get("parentTypeId")
                    if external_id is None or not name or parent_id is None:
                        skipped_subtype_count += 1
                        continue
                    if parent_id not in active_parent_ids:
                        skipped_subtype_count += 1
                        continue

                    active_subtype_ids.add(external_id)
                    subtype = existing_subtypes.get(external_id)
                    if subtype:
                        subtype.name = name
                        subtype.parent_type_external_id = parent_id
                        subtype.is_active = True
                        updated_subtype_count += 1
                    else:
                        subtype = LegalOneTaskSubType(
                            external_id=external_id,
                            name=name,
                            parent_type_external_id=parent_id,
                            is_active=True,
                        )
                        self.db.add(subtype)
                        existing_subtypes[external_id] = subtype
                        created_subtype_count += 1

                for external_id, subtype in existing_subtypes.items():
                    if external_id not in active_subtype_ids:
                        subtype.is_active = False

                self.logger.info(
                    "Tipos sincronizados: %s criados, %s atualizados, %s inativados.",
                    created_parent_count,
                    updated_parent_count,
                    len(existing_types) - len(active_parent_ids),
                )
                self.logger.info(
                    "Subtipos sincronizados: %s criados, %s atualizados, %s inativados, %s ignorados.",
                    created_subtype_count,
                    updated_subtype_count,
                    len(existing_subtypes) - len(active_subtype_ids),
                    skipped_subtype_count,
                )

            self.db.commit()
            self.logger.info("Sincronizacao de tipos e subtipos concluida com sucesso.")
            return True
        except Exception as exc:
            self.db.rollback()
            self.logger.error("Erro ao sincronizar tipos e subtipos: %s", exc, exc_info=True)
            return False


def run_metadata_sync_job() -> None:
    db = SessionLocal()
    try:
        service = MetadataSyncService(db=db)
        service.sync_all_metadata()
    finally:
        db.close()
