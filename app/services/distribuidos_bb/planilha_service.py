"""Geração da planilha de migração do Legal One (abas Processos + Envolvidos).

Porta o `gerar_planilha.py` legado, mas puxando de `bbd_processos` (coletados +
distribuídos) e da config EDITÁVEL (escritórios, valores padrão, equipes,
grupos de ajuizamento). O operador exporta e importa no L1 — o import dispara
o workflow nativo (que o POST /Lawsuits da API REST não dispara).

Colunas (0-indexadas) da aba Processos seguem o MODELO LEGAL ONE.xlsx.
"""
from __future__ import annotations

import io
from copy import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import openpyxl
from sqlalchemy.orm import Session

from app.models.distribuidos_bb import (
    PLANILHA_MANUAL,
    POOL_NOVO,
    POOL_PLANILHA_GERADA,
    PROC_DISTRIBUIDO,
    BbConfig,
    BbEnvolvido,
    BbPlanilha,
    BbProcesso,
)
from app.models.legal_one import LegalOneUser
from app.services.distribuidos_bb import normalizacao as norm
from app.services.distribuidos_bb.cadastro_l1 import parse_tramitacao
from app.services.distribuidos_bb.envolvidos_equipe import montar_envolvidos_equipe

_TEMPLATE = Path(__file__).parent / "templates" / "MODELO_LEGAL_ONE.xlsx"
_TZ_BR = ZoneInfo("America/Sao_Paulo")


def _cfg(db: Session, chave: str, default: str = "") -> str:
    c = db.get(BbConfig, chave)
    return c.valor if (c and c.valor is not None) else default


def gerar_planilha(
    db: Session,
    *,
    processo_ids: Optional[list[int]] = None,
    status: Optional[str] = "DISTRIBUIDO",
) -> tuple[io.BytesIO, int]:
    """Monta o xlsx a partir dos processos. Devolve (BytesIO, total_processos)."""
    q = db.query(BbProcesso)
    if processo_ids:
        q = q.filter(BbProcesso.id.in_(processo_ids))
    elif status:
        q = q.filter(BbProcesso.status == status)
    processos = q.order_by(BbProcesso.id).all()

    wb = openpyxl.load_workbook(_TEMPLATE)
    ws_p = wb["Processos"]
    ws_e = wb["Envolvidos"]

    # Estilo da linha-modelo (row 2) e limpeza (remove exemplo + dados antigos)
    modelo_p = list(ws_p.iter_rows(min_row=2, max_row=2))[0]
    modelo_e = list(ws_e.iter_rows(min_row=2, max_row=2))[0]
    estilo_p = [c._style if c.has_style else None for c in modelo_p]
    estilo_e = [c._style if c.has_style else None for c in modelo_e]
    if ws_p.max_row >= 2:
        ws_p.delete_rows(2, ws_p.max_row)
    if ws_e.max_row >= 2:
        ws_e.delete_rows(2, ws_e.max_row)

    # Constantes editáveis (Valores Padrão)
    cliente_nome = _cfg(db, "cliente_nome", "Banco do Brasil S.A.")
    cliente_cpf = _cfg(db, "cliente_cpf_cnpj", "00.000.000/0001-91")
    cliente_tipo = _cfg(db, "cliente_tipo", "PJ")
    tipo_registro = _cfg(db, "tipo_registro", "Processo")
    tipo = _cfg(db, "tipo", "Judicial")
    status_v = _cfg(db, "status", "Ativo")
    origem = _cfg(db, "escritorio_origem", "MDR Advocacia")
    situacao_env = _cfg(db, "situacao_envolvido", "Outros")
    doc_cliente = norm.apenas_digitos(cliente_cpf)

    # Nomes dos responsáveis
    resp_ids = {p.responsavel_user_id for p in processos if p.responsavel_user_id}
    nomes = dict(
        db.query(LegalOneUser.id, LegalOneUser.name)
        .filter(LegalOneUser.id.in_(resp_ids or {0}))
        .all()
    )

    def _aplica_estilo(ws, row, estilos):
        for ci, st in enumerate(estilos, start=1):
            if st is not None:
                ws.cell(row=row, column=ci)._style = copy(st)

    row_p = 2
    row_e = 2
    chave = 1
    for p in processos:
        tram = parse_tramitacao(p.tramitacao)
        # Contrário principal (1º envolvido com doc que NÃO seja o cliente/BB)
        adverso_cpf = adverso_tipo = None
        for e in db.query(BbEnvolvido).filter(BbEnvolvido.processo_id == p.id).all():
            if e.cpf_cnpj and norm.apenas_digitos(e.cpf_cnpj) != doc_cliente:
                adverso_cpf, adverso_tipo = e.cpf_cnpj, e.tipo_pessoa
                break

        linha = [None] * 31
        linha[0] = chave
        linha[2] = tipo_registro
        linha[3] = p.cnj
        linha[4] = tipo
        linha[5] = status_v
        linha[6] = p.natureza
        linha[7] = cliente_nome
        linha[8] = p.posicao
        linha[9] = cliente_cpf
        linha[10] = cliente_tipo
        linha[11] = p.adverso_principal
        linha[12] = adverso_cpf
        linha[13] = adverso_tipo
        linha[15] = p.data_ajuizamento
        linha[16] = p.acao
        linha[17] = p.npj
        linha[19] = tram["uf"]
        linha[20] = tram["cidade"]
        linha[21] = tram["orgao"]
        linha[24] = float(p.valor_causa) if p.valor_causa is not None else None
        linha[26] = nomes.get(p.responsavel_user_id)
        linha[27] = p.escritorio_path
        linha[28] = origem
        linha[29] = p.observacao

        _aplica_estilo(ws_p, row_p, estilo_p)
        for ci in range(1, 32):
            ws_p.cell(row=row_p, column=ci, value=linha[ci - 1])
        row_p += 1

        # Envolvidos (equipe do responsável + grupo de ajuizamento)
        for env in montar_envolvidos_equipe(db, p):
            _aplica_estilo(ws_e, row_e, estilo_e)
            ws_e.cell(row=row_e, column=1, value=chave)
            ws_e.cell(row=row_e, column=2, value=env["nome"])
            ws_e.cell(row=row_e, column=5, value=situacao_env)
            ws_e.cell(row=row_e, column=6, value=env["classificacao"])
            row_e += 1

        chave += 1

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf, len(processos)


def contar_pool_novos(db: Session) -> int:
    """Quantos processos estão no pool aguardando planilha (NOVO + distribuídos)."""
    return (
        db.query(BbProcesso)
        .filter(
            BbProcesso.planilha_status == POOL_NOVO,
            BbProcesso.status == PROC_DISTRIBUIDO,
        )
        .count()
    )


def gerar_e_persistir(
    db: Session,
    *,
    processo_ids: Optional[list[int]] = None,
    origem: str = PLANILHA_MANUAL,
) -> Optional[BbPlanilha]:
    """Gera a planilha do POOL e a arquiva em `bbd_planilhas`. Devolve a linha.

    Regra do pool: pega TODOS os processos marcados NOVO (e já distribuídos),
    gera a planilha e os marca como PLANILHA_GERADA (vinculando à planilha).
    A próxima coleta traz os processos novos como NOVO de novo.

    - Se `processo_ids` for dado, usa exatamente esses (subset manual).
    - Devolve `None` quando não há nada no pool (não cria planilha vazia).
    - NÃO faz commit — quem chama controla a transação.
    """
    if processo_ids is None:
        processos = (
            db.query(BbProcesso)
            .filter(
                BbProcesso.planilha_status == POOL_NOVO,
                BbProcesso.status == PROC_DISTRIBUIDO,
            )
            .order_by(BbProcesso.id)
            .all()
        )
    else:
        processos = (
            db.query(BbProcesso).filter(BbProcesso.id.in_(processo_ids or [0])).all()
        )
    if not processos:
        return None

    ids = [p.id for p in processos]
    buf, total = gerar_planilha(db, processo_ids=ids, status=None)
    if total == 0:
        return None

    dados = buf.getvalue()
    carimbo = datetime.now(_TZ_BR).strftime("%Y%m%d_%H%M")
    nome = f"PLANILHA_MIGRACAO_DISTRIBUIDOS_BB_{carimbo}.xlsx"

    planilha = BbPlanilha(
        nome_arquivo=nome,
        conteudo=dados,
        total_processos=total,
        tamanho_bytes=len(dados),
        origem=origem,
        status_origem=PROC_DISTRIBUIDO,
    )
    db.add(planilha)
    db.flush()   # garante planilha.id

    # Marca o pool como PLANILHA_GERADA e vincula à planilha recém-criada.
    agora = datetime.now(timezone.utc)
    for p in processos:
        p.planilha_status = POOL_PLANILHA_GERADA
        p.planilha_id = planilha.id
        p.planilha_gerada_em = agora

    return planilha
