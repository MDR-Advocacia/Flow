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
        """Mantem o CATALOGO operacional em dia com o Legal One.

        `legal_one_users` acumula DOIS papeis, e a fronteira entre eles e' o
        ponto inteiro desta funcao:

          CATALOGO (vem do Legal One) -> quem PODE ser RESPONSAVEL/EXECUTANTE
            de tarefa. E' o que alimenta o agendamento, o rodizio das filas e o
            seletor de Minha Equipe. Sem a entrada aqui, a pessoa simplesmente
            NAO EXISTE pra operacao, mesmo trabalhando na casa.

          CONTA (vem do Entra ID) -> quem PODE ENTRAR no Flow. Nasce do login
            SSO; papel e permissao quem define e' o gestor.

        Sao independentes: advogado novo precisa entrar no catalogo no dia em
        que e' cadastrado no L1, muito antes de logar no Flow pela primeira vez
        (as vezes nunca loga — nem todo responsavel por tarefa usa o sistema).

        Criar entrada de catalogo NAO da acesso a ninguem: o portao do login e'
        a sessao Microsoft validada no `sso_session`, e a linha nasce sem senha,
        com `role="user"` e TODAS as permissoes em False. Quando/se a pessoa
        logar, o match por e-mail encontra esta mesma linha.

        O que esta funcao faz:
          - CRIA entrada de catalogo pro contato do L1 que ainda nao existe;
          - preenche `external_id` onde esta' VAZIO, casando por e-mail;
          - nunca DESATIVA ninguem (quem tira acesso e' o Entra ou o gestor);
          - nunca SOBRESCREVE e-mail (identidade e' do Entra);
          - nunca mexe em papel, permissao ou nome de quem ja' existe.

        As tres ultimas travas sao cicatriz: o sync antigo espelhava `isActive`
        do L1 e desativava quem nao estivesse na lista, o que derrubou o acesso
        de gente da casa (cadastro antigo em gmail no L1) e gerou usuario
        duplicado — dois registros com o mesmo nome, um sem `external_id`, que
        quebrou EM SILENCIO o seletor de responsavel (caso da Ana Carolina em
        07/08). Manter a criacao SEM tocar em quem ja' existe e' o que permite
        ter catalogo completo sem reabrir aquilo.
        """
        self.logger.info("Vinculando contatos do L1 aos usuarios do Entra...")
        try:
            users_data = self.legal_one_client.get_all_users()
            if not users_data:
                self.logger.warning("Nenhum contato retornado pelo Legal One.")
                return False

            # e-mail (minusculo) -> id do contato no L1
            por_email: dict[str, int] = {}
            for u in users_data:
                email = (u.get("email") or "").strip().lower()
                ext = u.get("id")
                if email and ext and email not in por_email:
                    por_email[email] = int(ext)

            # Ids ja' usados: `external_id` e' unico na tabela, entao vincular
            # dois usuarios ao mesmo contato explodiria a constraint.
            ja_usados = {
                r[0] for r in self.db.query(LegalOneUser.external_id)
                .filter(LegalOneUser.external_id.isnot(None)).all()
            }

            vinculados = 0
            sem_contato: list[str] = []
            for user in (
                self.db.query(LegalOneUser)
                .filter(LegalOneUser.external_id.is_(None))
                .all()
            ):
                email = (user.email or "").strip().lower()
                ext = por_email.get(email)
                if ext and ext not in ja_usados:
                    user.external_id = ext
                    ja_usados.add(ext)
                    vinculados += 1
                    self.logger.info(
                        "Usuario %s vinculado ao contato L1 %s pelo e-mail.",
                        user.email, ext,
                    )
                elif not ext:
                    sem_contato.append(user.email or f"id={user.id}")

            # ── Catalogo: contato do L1 que ainda nao existe aqui ──────
            # Sem isto, advogado recem-cadastrado no L1 nao aparece pra ser
            # escolhido como responsavel — foi o que aconteceu com o Gabriel
            # Rocha (contato 78164) e mais dois em 13/08/2026.
            emails_existentes = {
                (e or "").strip().lower()
                for (e,) in self.db.query(LegalOneUser.email).all() if e
            }
            criados = 0
            for u in users_data:
                ext = u.get("id")
                if not ext or int(ext) in ja_usados:
                    continue
                email = (u.get("email") or "").strip().lower()
                if email and email in emails_existentes:
                    continue  # ja' tem conta; o vinculo acima cuida do external_id
                nome = (u.get("name") or "").strip()
                if not nome:
                    continue
                novo = LegalOneUser(
                    external_id=int(ext),
                    name=nome,
                    email=email or None,
                    # Ativo como CATALOGO (pode receber tarefa). Isso nao e'
                    # acesso: acesso depende de conta no Entra.
                    is_active=True,
                    hashed_password=None,
                    must_change_password=False,
                    role="user",
                    can_schedule_batch=False,
                    can_use_publications=False,
                    can_use_prazos_iniciais=False,
                )
                self.db.add(novo)
                ja_usados.add(int(ext))
                if email:
                    emails_existentes.add(email)
                criados += 1
                self.logger.info(
                    "Catalogo: %s (contato L1 %s) adicionado — pode ser "
                    "responsavel por tarefa. Sem acesso ao Flow.", nome, ext,
                )

            self.db.commit()
            if criados:
                self.logger.info(
                    "Catalogo do Legal One: %s pessoa(s) nova(s) disponivel(is) "
                    "pro agendamento.", criados,
                )
            if sem_contato:
                # Nao e' erro: a pessoa usa o Flow normalmente, so' nao pode ser
                # RESPONSAVEL por tarefa no L1 ate' ter contato la'. O admin ve
                # isso na tela de usuarios.
                self.logger.warning(
                    "%s usuario(s) sem contato correspondente no L1 (nao podem "
                    "ser responsaveis por tarefa): %s",
                    len(sem_contato), ", ".join(sem_contato[:10]),
                )
            self.logger.info(
                "Sincronizacao do catalogo concluida: %s vinculo(s), %s "
                "entrada(s) nova(s).", vinculados, criados,
            )
            return True
        except Exception as exc:
            self.db.rollback()
            self.logger.error("Erro ao vincular contatos do L1: %s", exc, exc_info=True)
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
