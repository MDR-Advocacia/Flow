"""Ingestão do cliente Ativos: lista seca de números → pré-cadastro via DataJud.

O operador sobe uma planilha/CSV com os números (é tudo o que a Ativos manda).
Extraímos os CNJs, e pra cada um o DataJud preenche a capa (classe, assunto,
órgão, comarca, grau, tribunal, data de ajuizamento, movimentos). Vira um
`bbd_processo` com cliente=ATIVOS. **Partes e valor da causa ficam em branco**
(lacuna que o DataJud não cobre) pro operador completar antes do cadastro.

Server-backed com progresso: um `BbAtivosLote` rastreia o andamento.
"""
from __future__ import annotations

import io
import logging
import re
import threading
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.distribuidos_bb import (
    CLIENTE_ATIVOS,
    LOTE_CONCLUIDO,
    LOTE_ERRO,
    POOL_NOVO,
    PROC_DISTRIBUIDO,
    BbAtivosLote,
    BbConfig,
    BbEscritorio,
    BbProcesso,
)
from app.services.distribuidos_bb.datajud_ativos import consultar_capa, formatar_cnj

logger = logging.getLogger("distribuidos_bb.ativos")

# Classes que fazem do Ativos o AUTOR (cobrança ativa). Como o pré-cadastro NÃO
# tem as partes, o polo/escritório vem da CLASSE (determinístico, do DataJud):
# execução de título extrajudicial, monitória, carta precatória → Autor; o resto
# (procedimento comum etc.) → Réu. Editável via config `ativos_classes_autor`.
_CLASSES_AUTOR_DEFAULT = "execu,monit,precat"


def _cfg(db: Session, chave: str, default: str) -> str:
    c = db.get(BbConfig, chave)
    return c.valor if (c and c.valor is not None) else default


def _classe_para_polo(db: Session, classe: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """(posicao, polo) a partir da classe. (None, None) se a classe é desconhecida."""
    cl = (classe or "").strip().lower()
    if not cl:
        return None, None
    kws = _cfg(db, "ativos_classes_autor", _CLASSES_AUTOR_DEFAULT)
    if any(k.strip() and k.strip() in cl for k in kws.split(",")):
        return "Autor", "Ativo"
    return "Réu", "Passivo"


def _escritorio_ativos(db: Session, posicao: str) -> BbEscritorio:
    """Get-or-create do escritório Ativos - Réu/Autor. O path é placeholder editável
    na tela de Configuração (o operador ajusta pro path real do L1)."""
    nome = f"Ativos - {posicao}"
    esc = db.query(BbEscritorio).filter(BbEscritorio.nome == nome).first()
    if esc is None:
        esc = BbEscritorio(
            nome=nome,
            escritorio_path=f"MDR Advocacia / Área operacional / Ativos / {posicao}",
            criterio_polo=("Ativo" if posicao == "Autor" else "Passivo"),
            ativo=True,
            ordem=90,
        )
        db.add(esc)
        db.commit()
        db.refresh(esc)
    return esc


def extrair_cnjs(conteudo: bytes, nome_arquivo: str) -> list[str]:
    """Acha os CNJs (20 dígitos) em qualquer célula do arquivo (xlsx/csv/txt)."""
    celulas: list[str] = []
    low = (nome_arquivo or "").lower()
    if low.endswith(".csv") or low.endswith(".txt"):
        try:
            texto = conteudo.decode("utf-8-sig", errors="ignore")
        except Exception:  # noqa: BLE001
            texto = conteudo.decode("latin-1", errors="ignore")
        celulas = re.split(r"[\r\n,;\t]", texto)
    else:
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                for v in row:
                    if v is not None:
                        celulas.append(str(v))

    vistos: set[str] = set()
    cnjs: list[str] = []
    for cell in celulas:
        digs = re.sub(r"\D", "", cell or "")
        if len(digs) == 20 and digs not in vistos:
            vistos.add(digs)
            cnjs.append(formatar_cnj(digs))
    return cnjs


def criar_lote(db: Session, *, nome_arquivo: str, cnjs: list[str], user_id: Optional[int]) -> BbAtivosLote:
    lote = BbAtivosLote(
        nome_arquivo=nome_arquivo, total=len(cnjs), disparado_por_user_id=user_id,
    )
    db.add(lote)
    db.commit()
    db.refresh(lote)
    return lote


def _fingerprint_ativos(cnj: str) -> str:
    # Prefixo por cliente: o mesmo CNJ pode existir pra BB e pra Ativos (pastas
    # diferentes), então não pode colidir no unique de fingerprint.
    return f"ativos:cnj:{cnj}"


def ingerir_lote_background(lote_id: int, cnjs: list[str]) -> None:
    """Processa a lista: DataJud + cria os processos. Roda em thread própria."""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        lote = db.get(BbAtivosLote, lote_id)
        if lote is None:
            return
        for cnj in cnjs:
            try:
                digs = re.sub(r"\D", "", cnj)
                if len(digs) != 20:
                    lote.invalidos += 1
                    lote.processados += 1
                    db.commit()
                    continue

                fp = _fingerprint_ativos(cnj)
                if db.query(BbProcesso).filter(BbProcesso.fingerprint == fp).first():
                    lote.duplicados += 1
                    lote.processados += 1
                    db.commit()
                    continue

                capa = consultar_capa(cnj)
                proc = BbProcesso(
                    cliente=CLIENTE_ATIVOS,
                    cnj=cnj,
                    fingerprint=fp,
                    status=PROC_DISTRIBUIDO,
                    planilha_status=POOL_NOVO,
                )
                if capa:
                    orgao = capa.get("orgao_julgador") or ""
                    uf = capa.get("uf") or ""
                    proc.natureza = capa.get("classe")
                    proc.acao = capa.get("assunto") or capa.get("classe")
                    proc.situacao = capa.get("assunto")
                    proc.data_ajuizamento = capa.get("data_ajuizamento")
                    proc.tramitacao = (f"{orgao} · {uf}".strip(" ·")) or None
                    proc.raw = {"datajud": capa}
                    lote.encontrados += 1
                else:
                    proc.situacao = "Sem capa no DataJud"
                    proc.raw = {"datajud": None}
                    lote.nao_encontrados += 1

                # Polo + escritório pela CLASSE (o pré-cadastro Ativos não tem partes):
                # execução/monitória/precatória → Autor; comuns → Réu.
                posicao, polo = _classe_para_polo(db, proc.natureza)
                if posicao:
                    esc = _escritorio_ativos(db, posicao)
                    proc.posicao = posicao
                    proc.polo = polo
                    proc.escritorio_id = esc.id
                    proc.escritorio_path = esc.escritorio_path

                db.add(proc)
                lote.criados += 1
                lote.processados += 1
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
                logger.exception("Ativos: falha ao ingerir CNJ %s (lote %s).", cnj, lote_id)
                lote = db.get(BbAtivosLote, lote_id)
                if lote:
                    lote.processados += 1
                    db.commit()

        lote = db.get(BbAtivosLote, lote_id)
        if lote:
            lote.status = LOTE_CONCLUIDO
            lote.concluido_em = datetime.now(timezone.utc)
            db.commit()
        logger.info("Ativos: lote %s concluído.", lote_id)
    except Exception:  # noqa: BLE001
        logger.exception("Ativos: erro geral no lote %s.", lote_id)
        try:
            lote = db.get(BbAtivosLote, lote_id)
            if lote:
                lote.status = LOTE_ERRO
                lote.erro = "Erro inesperado na ingestão."
                db.commit()
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()


def disparar_ingestao(db: Session, *, conteudo: bytes, nome_arquivo: str, user_id: Optional[int]) -> dict:
    """Extrai os CNJs, cria o lote e dispara o enriquecimento em background."""
    cnjs = extrair_cnjs(conteudo, nome_arquivo)
    if not cnjs:
        raise ValueError("Nenhum número de processo (CNJ) válido encontrado no arquivo.")
    lote = criar_lote(db, nome_arquivo=nome_arquivo, cnjs=cnjs, user_id=user_id)
    thread = threading.Thread(
        target=ingerir_lote_background, args=(lote.id, cnjs), daemon=True,
    )
    thread.start()
    return {"lote_id": lote.id, "total": len(cnjs)}
