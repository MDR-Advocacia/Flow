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
    DATAJUD_PENDENTE,
    LOTE_CONCLUIDO,
    LOTE_ERRO,
    PARTE_A_CLASSIFICAR,
    POOL_NOVO,
    PROC_DISTRIBUIDO,
    BbAtivosLote,
    BbConfig,
    BbEscritorio,
    BbProcesso,
)
from app.services.distribuidos_bb.datajud_ativos import apenas_digitos, formatar_cnj

logger = logging.getLogger("distribuidos_bb.ativos")

# Classes que fazem do Ativos o AUTOR (cobrança ativa). Como o pré-cadastro NÃO
# tem as partes, o polo/escritório vem da CLASSE (determinístico, do DataJud):
# execução de título extrajudicial, monitória, carta precatória, busca e apreensão
# → Autor; o resto (procedimento comum etc.) → Réu. Editável via config
# `ativos_classes_autor` (casa por substring, minúsculo).
_CLASSES_AUTOR_DEFAULT = "execu,monit,precat,busca"


def _cfg(db: Session, chave: str, default: str) -> str:
    c = db.get(BbConfig, chave)
    return c.valor if (c and c.valor is not None) else default


def _classe_para_polo(db: Session, classe: Optional[str]) -> tuple[str, str]:
    """(posicao, polo) a partir da classe. Monitória/execução/precatória → Autor;
    todo o resto (comuns) E classe desconhecida → Réu (default seguro; o DataJud
    refina pra Autor depois se a classe real indicar cobrança ativa)."""
    cl = (classe or "").strip().lower()
    if cl:
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


# A Ativos manda um xlsx com DUAS abas: "PARA CADASTRO" (o que entra) e
# "JÁ CADASTRADO" (já está no Legal One/Espaider — serve só de dedupe). O
# cabeçalho varia entre arquivos (espaço no fim, coluna MOTIVO às vezes ausente),
# então casamos as colunas por NOME, nunca por posição.
def _norm(v: object) -> str:
    return ("" if v is None else str(v)).strip()


def _mapear_colunas(hdr: list) -> dict[str, int]:
    """Aponta cada coluna canônica pelo nome do cabeçalho (robusto à variação)."""
    idx: dict[str, int] = {}
    for i, h in enumerate(hdr):
        H = _norm(h).upper()
        if not H:
            continue
        if H.startswith("PROC"):
            idx["cnj"] = i
        elif H.startswith("UF"):
            idx["uf"] = i
        elif H.startswith("DATA"):
            idx["data"] = i
        elif H.startswith("TIPO"):
            idx["tipo"] = i
        elif H.startswith("REMETENTE"):
            idx["remetente"] = i
        elif "CONTROLE" in H:
            idx["controle"] = i
        elif H.startswith("CLIENTE"):
            idx["parte"] = i  # nome da parte contrária (quando vem preenchido)
        elif H.startswith("MOTIVO"):
            idx["motivo"] = i
        # ESCRITORIO_DESIGNADO/ESCRITORIO = o escritório DELES (sempre MDR) → ignora.
    return idx


def _linha_para_dict(row: tuple, idx: dict[str, int]) -> Optional[dict]:
    """Extrai uma linha da aba PARA CADASTRO. None se não tiver CNJ válido."""
    def cel(k: str) -> Optional[str]:
        i = idx.get(k)
        if i is None or i >= len(row):
            return None
        s = _norm(row[i])
        return s or None

    cnj_raw = cel("cnj")
    digs = apenas_digitos(cnj_raw)
    if len(digs) != 20:
        return None
    return {
        "cnj": formatar_cnj(digs),
        "uf": cel("uf"),
        "data": cel("data"),
        "tipo": cel("tipo"),
        "remetente": cel("remetente"),
        "controle": cel("controle"),
        "parte": cel("parte"),
        "motivo": cel("motivo"),
    }


def parse_planilha_ativos(conteudo: bytes, nome_arquivo: str) -> tuple[list[dict], set[str]]:
    """Lê o arquivo da Ativos.

    Devolve (linhas_para_cadastro, cnjs_ja_cadastrado):
    - linhas_para_cadastro: dicts da aba "PARA CADASTRO" (dedup por CNJ);
    - cnjs_ja_cadastrado: dígitos dos CNJs da aba "JÁ CADASTRADO" (só p/ pular).

    CSV/TXT (sem abas) caem no modo lista-seca: tudo vira PARA CADASTRO, só CNJ.
    """
    low = (nome_arquivo or "").lower()
    linhas: list[dict] = []
    ja: set[str] = set()
    vistos: set[str] = set()

    if low.endswith(".csv") or low.endswith(".txt"):
        try:
            texto = conteudo.decode("utf-8-sig", errors="ignore")
        except Exception:  # noqa: BLE001
            texto = conteudo.decode("latin-1", errors="ignore")
        for cell in re.split(r"[\r\n,;\t]", texto):
            digs = apenas_digitos(cell)
            if len(digs) == 20 and digs not in vistos:
                vistos.add(digs)
                linhas.append({"cnj": formatar_cnj(digs), "uf": None, "data": None,
                               "tipo": None, "remetente": None, "controle": None,
                               "parte": None, "motivo": None})
        return linhas, ja

    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
    for ws in wb.worksheets:
        titulo = (ws.title or "").strip().upper()
        eh_ja = "JÁ CADASTRAD" in titulo or "JA CADASTRAD" in titulo
        eh_para = "PARA CADASTRO" in titulo
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        idx = _mapear_colunas(list(rows[0]))
        if "cnj" not in idx:
            # Aba sem cabeçalho reconhecível: varre CNJs crus (fallback).
            if not eh_ja:
                for r in rows:
                    for v in r:
                        digs = apenas_digitos(v if isinstance(v, str) else _norm(v))
                        if len(digs) == 20 and digs not in vistos:
                            vistos.add(digs)
                            linhas.append({"cnj": formatar_cnj(digs), "uf": None, "data": None,
                                           "tipo": None, "remetente": None, "controle": None,
                                           "parte": None, "motivo": None})
            continue
        for r in rows[1:]:
            if all(c is None for c in r):
                continue
            d = _linha_para_dict(r, idx)
            if not d:
                continue
            digs = apenas_digitos(d["cnj"])
            if eh_ja:
                ja.add(digs)
            elif eh_para or "CADASTRAD" not in titulo:
                # PARA CADASTRO (ou aba única sem rótulo claro que não seja "já").
                if digs not in vistos:
                    vistos.add(digs)
                    linhas.append(d)
    return linhas, ja


def criar_lote(db: Session, *, nome_arquivo: str, total: int, user_id: Optional[int]) -> BbAtivosLote:
    lote = BbAtivosLote(
        nome_arquivo=nome_arquivo, total=total, disparado_por_user_id=user_id,
    )
    db.add(lote)
    db.commit()
    db.refresh(lote)
    return lote


def _fingerprint_ativos(cnj: str) -> str:
    # Prefixo por cliente: o mesmo CNJ pode existir pra BB e pra Ativos (pastas
    # diferentes), então não pode colidir no unique de fingerprint.
    return f"ativos:cnj:{cnj}"


# "TIPO." mistura classe processual (Procedimento Comum/Juizado) com tipo de
# comunicação (carta de citação) e tags de sistema (PJE/PJD/GEJUR). Estas últimas
# NÃO são classe — viram None (o DataJud traz a classe real depois).
_TIPO_NAO_CLASSE = {"PJE", "PJD", "TJD", "GEJUR"}


def _limpar_tipo(tipo: Optional[str]) -> Optional[str]:
    t = (tipo or "").strip()
    if not t:
        return None
    u = t.upper()
    if u in _TIPO_NAO_CLASSE or u.startswith("CARTA"):
        # Tags de sistema e tipos de comunicação (carta de citação/intimação) NÃO
        # são classe processual → deixa vazio; o DataJud traz a classe real depois.
        return None
    return t


def _montar_tramitacao(uf: Optional[str], comarca: Optional[str] = None,
                       orgao: Optional[str] = None) -> Optional[str]:
    """Formata no padrão que `parse_tramitacao` espera: 'Comarca/UF - Orgao'."""
    uf = (uf or "").strip()
    comarca = (comarca or "").strip()
    orgao = (orgao or "").strip()
    if not (uf or comarca or orgao):
        return None
    base = f"{comarca}/{uf}" if uf else comarca
    return f"{base} - {orgao}" if orgao else base


def ingerir_lote_background(lote_id: int, linhas: list[dict], ja_cadastrado: set[str]) -> None:
    """Cria os processos a partir da PLANILHA (fonte primária). O DataJud é
    diferido: cada processo entra com `datajud_status=pendente` e o worker de
    reconsulta completa classe/assunto/órgão/comarca depois (o recém-distribuído
    pode ainda não estar indexado). Roda em thread própria."""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        lote = db.get(BbAtivosLote, lote_id)
        if lote is None:
            return
        for linha in linhas:
            cnj = linha.get("cnj")
            try:
                digs = apenas_digitos(cnj)
                if len(digs) != 20:
                    lote.invalidos += 1
                    lote.processados += 1
                    db.commit()
                    continue

                # Já está no Legal One (aba "JÁ CADASTRADO") → não recadastra.
                if digs in ja_cadastrado:
                    lote.duplicados += 1
                    lote.processados += 1
                    db.commit()
                    continue

                fp = _fingerprint_ativos(cnj)
                if db.query(BbProcesso).filter(BbProcesso.fingerprint == fp).first():
                    lote.duplicados += 1
                    lote.processados += 1
                    db.commit()
                    continue

                classe = _limpar_tipo(linha.get("tipo"))
                proc = BbProcesso(
                    cliente=CLIENTE_ATIVOS,
                    cnj=cnj,
                    fingerprint=fp,
                    status=PROC_DISTRIBUIDO,
                    planilha_status=POOL_NOVO,
                    natureza=classe,
                    acao=classe,
                    data_ajuizamento=linha.get("data"),
                    adverso_principal=(linha.get("parte") or PARTE_A_CLASSIFICAR),
                    tramitacao=_montar_tramitacao(linha.get("uf")),
                    datajud_status=DATAJUD_PENDENTE,
                    raw={"ativos_planilha": linha, "datajud": None},
                )

                # Polo + escritório pela CLASSE da ação (o pré-cadastro Ativos não
                # tem partes): monitória/execução/precatória → Autor; comuns → Réu.
                posicao, polo = _classe_para_polo(db, classe)
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
        logger.info("Ativos: lote %s concluído (criados=%s, dup=%s).",
                    lote_id, lote.criados if lote else "?", lote.duplicados if lote else "?")
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
    """Lê a planilha (aba PARA CADASTRO), cria o lote e dispara a ingestão em background."""
    linhas, ja = parse_planilha_ativos(conteudo, nome_arquivo)
    if not linhas:
        raise ValueError("Nenhum número de processo (CNJ) válido encontrado na aba PARA CADASTRO.")
    lote = criar_lote(db, nome_arquivo=nome_arquivo, total=len(linhas), user_id=user_id)
    thread = threading.Thread(
        target=ingerir_lote_background, args=(lote.id, linhas, ja), daemon=True,
    )
    thread.start()
    return {"lote_id": lote.id, "total": len(linhas), "ja_cadastrado": len(ja)}
