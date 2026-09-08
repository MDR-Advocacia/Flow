"""Cache do RITO por processo (comum × juizado × trabalhista).

Uma linha por CNJ, não por publicação: **rito de processo não muda**. Um
processo não migra de juizado para vara comum no meio do caminho, então a
resposta vale para sempre e para todas as publicações daquele número — as
66.627 publicações com pasta se reduzem a 34.718 CNJs distintos, e o
DataJud só precisa ser perguntado uma vez por processo na vida.

`rito = NULL` com a linha existindo é resposta, não ausência: significa "já
perguntei e não deu" (tribunal fora do DataJud, processo não indexado). É o
que impede de reperguntar eternamente pelo mesmo CNJ. Ausência de linha é
que significa "nunca perguntei".

Migration: pub016.
"""
from sqlalchemy import Column, DateTime, String
from sqlalchemy.sql import func

from app.db.session import Base


class ProcessoRito(Base):
    __tablename__ = "processo_rito"

    # Só dígitos: o CNJ chega com máscara, sem máscara e com OCR sujo.
    cnj_digitos = Column(String(20), primary_key=True)
    # juizado | comum | trabalhista | NULL (perguntamos e não resolveu)
    rito = Column(String(16), nullable=True, index=True)
    # texto | datajud — de onde veio a resposta que está gravada.
    fonte = Column(String(16), nullable=False)
    # Órgão julgador que o DataJud devolveu, guardado como evidência do
    # porquê: sem isso o operador vê "juizado" e não tem como conferir.
    orgao = Column(String(255), nullable=True)
    atualizado_em = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
