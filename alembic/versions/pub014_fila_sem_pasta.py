"""pub014 - fila de publicacoes SEM PASTA: escritorio ficticio + runs do motor.

Estudo de 02-03/09/2026 em producao: ~530 publicacoes/dia util chegam sem
processo vinculado e eram descartadas na captura noturna — 75% pautas
coletivas, ~136 individuais, ~31 delas embargos a execucao ou agravo de
instrumento (que nascem com numero novo e por isso nunca casam pelo CNJ).

Decisao do operador (03/09): DOIS motores. O de publicacoes fica intocado;
um MOTOR SEPARADO (app/services/publication_sem_pasta_motor.py) identifica
O QUE E o caso, extrai a ficha de cadastro e manda para tratamento
especializado. A taxonomia desse motor vive em CODIGO, nao nesta migration.

Tres seeds, idempotentes:
  1. legal_one_offices: o escritorio FICTICIO (external_id -1) — so para dar
     as publicacoes sem pasta uma area de templates e um card no hub. Id
     negativo de proposito: o sync de escritorios pula negativos.
  2. app_settings: captura noturna (default DESLIGADA), escritorio real do
     L1 que recebe a tarefa de saneamento, e descarte de pauta (default
     LIGADO, por decisao do operador).
  3. publicacao_sem_pasta_runs: uma linha por execucao do motor (progresso
     e trilha).

Nao mexe em publicacao existente.

Revision ID: pub014
Revises: pub013
"""
from alembic import op
import sqlalchemy as sa

# Valores LITERAIS de propósito — migration não importa app.services.
#
# O alembic carrega TODOS os arquivos de versão para montar o mapa de
# revisões, inclusive no `alembic upgrade head` do boot. Um import de código
# de aplicação aqui transforma qualquer refatoração daquele módulo (renomear
# constante, mover arquivo, import circular) em falha de DEPLOY: o container
# não sobe. Aconteceu em 03/09/2026, num `alembic heads` que morreu com
# ImportError por causa exatamente deste import. Migration é registro
# histórico: congela o valor que valia quando ela foi escrita.
#
# Espelham app/services/publication_sem_pasta.py — se mudarem lá, a migration
# NÃO deve mudar aqui (o passado já rodou com estes).
SEM_PASTA_OFFICE_ID = -1
SEM_PASTA_POLO = "sem_pasta"
SEM_PASTA_OFFICE_NAME = "Publicações sem pasta"
SEM_PASTA_OFFICE_PATH = "MDR Advocacia / Área operacional / Publicações sem pasta"
SETTING_CAPTURA_NOTURNA = "publicacoes_capturar_sem_pasta"
SETTING_OFFICE_L1_TAREFA = "publicacoes_sem_pasta_office_l1"
OFFICE_L1_TAREFA_DEFAULT = 1

revision = "pub014"
down_revision = "pub013"
branch_labels = None
depends_on = None

TABELA_RUNS = "publicacao_sem_pasta_runs"
SETTING_DESCARTAR_PAUTA = "publicacoes_sem_pasta_descartar_pauta"


def upgrade() -> None:
    conn = op.get_bind()

    # 1) escritorio ficticio
    offices = sa.table(
        "legal_one_offices",
        sa.column("external_id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("path", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("polo_scope", sa.String),
    )
    existe = conn.execute(
        sa.select(offices.c.external_id).where(offices.c.external_id == SEM_PASTA_OFFICE_ID)
    ).first()
    if existe is None:
        conn.execute(
            offices.insert().values(
                external_id=SEM_PASTA_OFFICE_ID,
                name=SEM_PASTA_OFFICE_NAME,
                path=SEM_PASTA_OFFICE_PATH,
                is_active=True,
                polo_scope=SEM_PASTA_POLO,
            )
        )
    else:
        conn.execute(
            offices.update()
            .where(offices.c.external_id == SEM_PASTA_OFFICE_ID)
            .values(is_active=True, polo_scope=SEM_PASTA_POLO)
        )

    # 2) settings
    settings = sa.table(
        "app_settings",
        sa.column("key", sa.String),
        sa.column("value", sa.String),
        sa.column("description", sa.String),
    )
    for chave, valor, desc in (
        (
            SETTING_CAPTURA_NOTURNA,
            "false",
            "Publicacoes sem pasta: capturar na rotina noturna e rodar o motor "
            "proprio (fila 'Publicacoes sem pasta'). ~530/dia util.",
        ),
        (
            SETTING_OFFICE_L1_TAREFA,
            str(OFFICE_L1_TAREFA_DEFAULT),
            "Publicacoes sem pasta: escritorio REAL do L1 (external_id) que "
            "recebe a tarefa de saneamento. Default 1 = raiz MDR Advocacia.",
        ),
        (
            SETTING_DESCARTAR_PAUTA,
            "true",
            "Publicacoes sem pasta: pauta coletiva (21+ CNJs) e descartada por "
            "regra (IGNORADO), sem IA. Decisao do operador em 03/09/2026.",
        ),
    ):
        if conn.execute(sa.select(settings.c.key).where(settings.c.key == chave)).first() is None:
            conn.execute(settings.insert().values(key=chave, value=valor, description=desc))

    # 3) runs do motor
    insp = sa.inspect(conn)
    if TABELA_RUNS not in insp.get_table_names():
        op.create_table(
            TABELA_RUNS,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="running"),
            sa.Column("requested_by", sa.String(120), nullable=True),
            sa.Column("automation_run_id", sa.Integer, nullable=True),
            sa.Column("total_alvo", sa.Integer, nullable=False, server_default="0"),
            sa.Column("processados", sa.Integer, nullable=False, server_default="0"),
            sa.Column("pautas", sa.Integer, nullable=False, server_default="0"),
            sa.Column("classificados", sa.Integer, nullable=False, server_default="0"),
            sa.Column("fichas", sa.Integer, nullable=False, server_default="0"),
            sa.Column("erros", sa.Integer, nullable=False, server_default="0"),
            sa.Column("ultimo_erro", sa.Text, nullable=True),
        )
        op.create_index(f"ix_{TABELA_RUNS}_automation_run", TABELA_RUNS, ["automation_run_id"])


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if TABELA_RUNS in insp.get_table_names():
        op.drop_index(f"ix_{TABELA_RUNS}_automation_run", table_name=TABELA_RUNS)
        op.drop_table(TABELA_RUNS)
    settings = sa.table("app_settings", sa.column("key", sa.String))
    conn.execute(
        settings.delete().where(
            settings.c.key.in_([
                SETTING_CAPTURA_NOTURNA, SETTING_OFFICE_L1_TAREFA, SETTING_DESCARTAR_PAUTA,
            ])
        )
    )
    # O escritorio ficticio fica: templates podem apontar pra ele (FK).
