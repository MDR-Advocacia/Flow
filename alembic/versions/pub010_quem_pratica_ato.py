"""pub010 — de quem é o ato, e se ele exige providência nossa.

NASCE DE UMA MEDIÇÃO, NÃO DE UM PALPITE
---------------------------------------
A regra `r5_default` do shadow (nenhuma regra dispara → agenda por omissão)
responde por 80% do volume, com 68,6% de acerto. Os 869 erros dela, abertos
pelo motivo que o operador DECLAROU ao ignorar (motivo é obrigatório desde a
pub006, então 99,8% preenchem e o dado presta):

    parte_adversa   388  (45%)  ─┐  compreensão de texto
    informativa     315  (36%)  ─┘  = 83% dos erros
    ja_agendado     147  (17%)      consulta ao L1
    outros           19

O gargalo NÃO era falta de contexto que exigisse investigação — hipótese que
eu havia defendido e que a medição derrubou. É que ninguém perguntava ao
modelo QUEM tem que praticar o ato. Ele classificava o assunto e parava aí.

A prova pelo avesso estava na própria política: `r2_parte_adversa` acerta
96,3%, mas disparou 27 vezes enquanto 388 casos de parte adversa vazaram pro
default. O problema dela nunca foi precisão, foi ALCANCE — ela depende de
comparar polo do ato com polo do escritório, e o polo do ato só existe quando
a IA o emite.

O QUE O LOTE DE VALIDAÇÃO MEDIU (2.781 publicações, Haiku 4.5, ~US$ 2)
---------------------------------------------------------------------
Rodado contra gabarito real — a decisão que o humano já tinha tomado, gravada
em `publicacao_shadow_decisao.real`. Células escolhidas na metade de treino e
medidas cegas na metade de validação, senão seria escolher o corte e medir no
mesmo dado:

    quem_pratica_ato  exige      n     ignorou de fato
    nos               sim     1197        15,0%
    juizo             não     1087        35,2%   ← moeda ao ar
    parte_adversa     não      321        80,1%   ← a única célula limpa
    juizo             sim      144        27,1%
    parte_adversa     sim       18        50,0%

Só UMA célula presta. Aplicada cega na validação: recupera 29,9% dos erros
com 2,78% de falso-ignorar, e a acurácia no r5_default vai de 68,8% p/ 76,2%.

Corroboração que vale mais que o número: dentro dessa célula, quando o
operador ignorou, o motivo que ele declarou foi `parte_adversa` em 210 dos
266 casos. O modelo não acerta o resultado por acaso — acerta pelo MESMO
motivo que a pessoa.

DUAS COISAS QUE A MEDIÇÃO ENSINOU E FICARAM NO DESENHO
------------------------------------------------------
1. `juizo` era um balde envenenado: 44% do volume e 35% de pureza. Na
   primeira derivação eu o havia posto na condição de ignorar e o
   falso-ignorar foi a 46,6% — reprovando a regra inteira. O erro foi meu,
   de tradução, não do modelo. Por isso o enum agora SEPARA
   `juizo_expediente` (conclusos, mero expediente) de `juizo_determina` (o
   juízo mandou fazer algo). Essa separação é HIPÓTESE, ainda não validada:
   nenhuma regra a usa, ela existe pra o próximo lote poder medi-la.
2. `confianca` da IA é imprestável como filtro — 2.769 de 2.776 respostas
   vieram "alta", inclusive nas células que são moeda ao ar. Não gravamos
   confiança destes campos: seria dar régua a quem não sabe medir.

Colunas nullable: publicação classificada antes desta migration fica com NULL
e a `r6` simplesmente não dispara nela — sem backfill, sem reclassificar
nada, sem custo.
"""
from alembic import op
import sqlalchemy as sa


revision = "pub010_quem_pratica_ato"
down_revision = "pub009_usage_classificacao"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # De quem é o ato: nos | parte_adversa | juizo_expediente |
    # juizo_determina | indeterminado. String e não enum porque o vocabulário
    # ainda está em teste — a separação do juizo é hipótese aberta, e enum no
    # Postgres custa migration pra cada valor novo.
    op.add_column(
        "publicacao_registros",
        sa.Column("quem_pratica_ato", sa.String(length=20), nullable=True),
    )
    # A publicação exige providência NOSSA, com prazo? NULL = não perguntado
    # (classificação anterior a esta migration), distinto de false.
    op.add_column(
        "publicacao_registros",
        sa.Column("exige_providencia_nossa", sa.Boolean(), nullable=True),
    )
    # A r6 filtra pela combinação dos dois; o índice serve também à análise
    # por célula, que é como toda decisão desta família foi tomada até aqui.
    op.create_index(
        "ix_pub_reg_quem_pratica",
        "publicacao_registros",
        ["quem_pratica_ato", "exige_providencia_nossa"],
    )


def downgrade() -> None:
    op.drop_index("ix_pub_reg_quem_pratica", table_name="publicacao_registros")
    op.drop_column("publicacao_registros", "exige_providencia_nossa")
    op.drop_column("publicacao_registros", "quem_pratica_ato")
