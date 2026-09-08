"""pub016 - rito do processo (comum x juizado x trabalhista) como CAMPO.

Pedido do operador em 08/09/2026, depois de a equipe cobrar: "tem que trazer
essa informacao pra todo mundo e mostrar na interface, e criar filtro disso".

O rito ja existia, mas so na fila SEM PASTA (motor proprio, pub014). Na fila
comum ele nao existia em lugar nenhum: a taxonomia v2 e toda de PROVIDENCIA
("Recursos e Julgamentos em 2 Grau", "Manifestacoes, Prazos e Providencias"),
e rito e ortogonal a isso — muda o QUE se faz com a mesma providencia
(recurso inominado x apelacao, custas, prazo em dobro que o juizado nao tem).
Por isso entra como CAMPO da publicacao, e nao como categoria: como categoria
obrigaria a duplicar a taxonomia inteira em duas.

Duas mudancas, as duas aditivas:

  1. publicacao_registros ganha `rito` (indexado, porque vira FILTRO) e
     `rito_fonte`. Preenchidos pelo TEXTO no nascimento da publicacao, de
     graca, do mesmo jeito que o `uf` ja e.
  2. Tabela `processo_rito`: cache por CNJ, nao por publicacao. Rito de
     processo NAO MUDA, entao uma resposta do DataJud vale para sempre —
     66.627 publicacoes com pasta se reduzem a 34.718 CNJs distintos.
     Linha com `rito = NULL` e resposta ("perguntei e nao deu"), o que
     impede reperguntar; ausencia de linha e que significa "nunca perguntei".

Nao mexe em publicacao existente: as colunas nascem NULL e o backfill e
passo separado.

Revision ID: pub016
Revises: pub015
"""
from alembic import op
import sqlalchemy as sa

# Literais congelados de proposito — migration nao importa app.services.
# O alembic carrega TODOS os arquivos de versao no boot do container, entao
# um import de codigo de aplicacao aqui transforma qualquer refatoracao
# daquele modulo em falha de DEPLOY. Ja aconteceu em 03/09/2026.
TABELA_PUBS = "publicacao_registros"
TABELA_RITO = "processo_rito"

revision = "pub016"
down_revision = "pub015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)

    # 1) colunas na publicacao
    colunas = {c["name"] for c in insp.get_columns(TABELA_PUBS)}
    if "rito" not in colunas:
        op.add_column(TABELA_PUBS, sa.Column("rito", sa.String(16), nullable=True))
        op.create_index(f"ix_{TABELA_PUBS}_rito", TABELA_PUBS, ["rito"])
    if "rito_fonte" not in colunas:
        op.add_column(TABELA_PUBS, sa.Column("rito_fonte", sa.String(16), nullable=True))

    # 2) cache por CNJ
    if TABELA_RITO not in insp.get_table_names():
        op.create_table(
            TABELA_RITO,
            sa.Column("cnj_digitos", sa.String(20), primary_key=True),
            sa.Column("rito", sa.String(16), nullable=True),
            sa.Column("fonte", sa.String(16), nullable=False),
            sa.Column("orgao", sa.String(255), nullable=True),
            sa.Column(
                "atualizado_em", sa.DateTime(timezone=True),
                server_default=sa.func.now(), nullable=False,
            ),
        )
        op.create_index(f"ix_{TABELA_RITO}_rito", TABELA_RITO, ["rito"])


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)

    if TABELA_RITO in insp.get_table_names():
        op.drop_index(f"ix_{TABELA_RITO}_rito", table_name=TABELA_RITO)
        op.drop_table(TABELA_RITO)

    colunas = {c["name"] for c in insp.get_columns(TABELA_PUBS)}
    if "rito_fonte" in colunas:
        op.drop_column(TABELA_PUBS, "rito_fonte")
    if "rito" in colunas:
        op.drop_index(f"ix_{TABELA_PUBS}_rito", table_name=TABELA_PUBS)
        op.drop_column(TABELA_PUBS, "rito")
