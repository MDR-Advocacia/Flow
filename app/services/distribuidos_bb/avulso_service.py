"""Pasta avulsa — criação manual de um processo pelo modal do Cadastro de Processo.

Terceiro caminho de entrada do módulo (os outros: coleta RPA do BB e Importar
lista do Ativos). O operador preenche os campos, escolhe o escritório responsável
(lista completa do L1, a mesma do módulo de Publicações) e o responsável (sugerido
pelo rodízio quando o escritório tem fila configurada). Decisão do operador:
**cadastro imediato** — salvar já gera a planilha de migração (só desta pasta) e
importa no Legal One (o import interno dispara o workflow; o monitor confirma).

O cliente é livre: BB/Ativos (chips que pré-preenchem da config) ou qualquer outro
(nome/CNPJ digitados). A tag `cliente` do processo vira BB/ATIVOS quando o CNPJ
bate com a config, senão OUTRO — e a planilha usa o cliente POR PROCESSO.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.distribuidos_bb import (
    CLIENTE_ATIVOS,
    CLIENTE_BB,
    CLIENTE_OUTRO,
    NIVEL_ERRO,
    NIVEL_SUCESSO,
    POOL_NOVO,
    PROC_DISTRIBUIDO,
    SECAO_CADASTRO,
    BbConfig,
    BbEscritorio,
    BbProcesso,
)
from app.services.distribuidos_bb import normalizacao as norm
from app.services.distribuidos_bb.ativos_service import _montar_tramitacao, _natureza_do_cnj
from app.services.distribuidos_bb.distribuicao_service import (
    _avaliar_observacao,
    _proximo_responsavel_rr,
    peek_responsavel_rr,
)
from app.services.distribuidos_bb.log_service import registrar_evento

logger = logging.getLogger("distribuidos_bb.avulso")

_POSICAO_POLO = {"Réu": "Passivo", "Autor": "Ativo", "Interessado": "Neutro"}


def _cfg(db: Session, chave: str, default: str = "") -> str:
    c = db.get(BbConfig, chave)
    return c.valor if (c and c.valor is not None) else default


def _tag_cliente(db: Session, cpf_cnpj: Optional[str]) -> str:
    """BB/ATIVOS quando o CNPJ bate com a config do módulo; senão OUTRO."""
    doc = norm.apenas_digitos(cpf_cnpj or "")
    if not doc:
        return CLIENTE_OUTRO
    if doc == norm.apenas_digitos(_cfg(db, "cliente_cpf_cnpj", "00.000.000/0001-91")):
        return CLIENTE_BB
    if doc == norm.apenas_digitos(_cfg(db, "ativos_cliente_cpf_cnpj", "05.437.257/0001-29")):
        return CLIENTE_ATIVOS
    return CLIENTE_OUTRO


def _escritorio_por_path(db: Session, path: str) -> Optional[BbEscritorio]:
    return (
        db.query(BbEscritorio)
        .filter(BbEscritorio.escritorio_path == path, BbEscritorio.ativo.is_(True))
        .first()
    )


def sugerir(db: Session, *, escritorio_path: str, cliente_cpf_cnpj: Optional[str],
            posicao: Optional[str], natureza: Optional[str], cnj: Optional[str]) -> dict[str, Any]:
    """Sugestão pro modal: próximo do rodízio (peek, sem avançar) + observação da regra."""
    esc = _escritorio_por_path(db, escritorio_path)
    responsavel_id = None
    observacao = None
    if esc is not None:
        responsavel_id = esc.responsavel_fixo_user_id or peek_responsavel_rr(db, esc)
        fake = BbProcesso(
            cliente=_tag_cliente(db, cliente_cpf_cnpj),
            posicao=posicao, natureza=natureza, cnj=cnj, fingerprint="_",
        )
        observacao = _avaliar_observacao(db, fake, esc)
    nome = None
    if responsavel_id:
        from app.models.legal_one import LegalOneUser

        u = db.get(LegalOneUser, responsavel_id)
        nome = u.name if u else None
    return {
        "tem_fila": esc is not None,
        "responsavel_sugerido_id": responsavel_id,
        "responsavel_sugerido_nome": nome,
        "observacao_sugerida": observacao,
    }


def criar_pasta_avulsa(db: Session, dados: dict[str, Any], *, user_id: Optional[int]) -> BbProcesso:
    """Cria o BbProcesso da pasta avulsa (validado). NÃO cadastra no L1 — quem
    chama encadeia o cadastro imediato e trata a falha sem perder o processo."""
    cnj = (dados.get("cnj") or "").strip() or None
    digs = norm.apenas_digitos(cnj or "")
    if cnj and len(digs) != 20:
        raise ValueError("CNJ inválido: precisa ter 20 dígitos.")

    posicao = (dados.get("posicao") or "").strip()
    if posicao not in _POSICAO_POLO:
        raise ValueError("Posição inválida (use Réu, Autor ou Interessado).")

    cliente_nome = (dados.get("cliente_nome") or "").strip()
    if not cliente_nome:
        raise ValueError("Informe o cliente da pasta.")

    escritorio_path = (dados.get("escritorio_path") or "").strip()
    if not escritorio_path:
        raise ValueError("Escolha o escritório responsável.")

    fingerprint = f"avulso:cnj:{digs}" if digs else f"avulso:{uuid.uuid4().hex}"
    if digs and db.query(BbProcesso).filter(BbProcesso.fingerprint == fingerprint).first():
        raise ValueError(f"Já existe uma pasta avulsa para o CNJ {cnj}.")

    tag = _tag_cliente(db, dados.get("cliente_cpf_cnpj"))
    esc = _escritorio_por_path(db, escritorio_path)

    proc = BbProcesso(
        cliente=tag,
        cliente_nome=cliente_nome,
        cliente_cpf_cnpj=(dados.get("cliente_cpf_cnpj") or "").strip() or None,
        cliente_tipo=(dados.get("cliente_tipo") or "PJ").strip() or "PJ",
        cnj=cnj,
        fingerprint=fingerprint,
        status=PROC_DISTRIBUIDO,
        planilha_status=POOL_NOVO,
        posicao=posicao,
        polo=_POSICAO_POLO[posicao],
        # Natureza = catálogo do L1; vazio → deduz do CNJ (5=Trabalhista, resto Civel).
        # Valor fora do catálogo reprova a validação do import (visto no 1º lote).
        natureza=(dados.get("natureza") or "").strip() or _natureza_do_cnj(digs),
        acao=(dados.get("acao") or "").strip() or None,
        data_ajuizamento=(dados.get("data_ajuizamento") or "").strip() or None,
        valor_causa=dados.get("valor_causa"),
        adverso_principal=(dados.get("adverso_nome") or "").strip() or None,
        tramitacao=_montar_tramitacao(
            dados.get("uf"), dados.get("comarca"), dados.get("orgao")
        ),
        escritorio_id=esc.id if esc else None,
        escritorio_path=escritorio_path,
        observacao=(dados.get("observacao") or "").strip() or None,
        raw={"origem": "pasta_avulsa", "criado_por_user_id": user_id},
    )

    # Responsável: obrigatório (a trava da planilha barraria sem ele de qualquer
    # forma — aqui devolvemos o erro claro no modal). Se o operador manteve a
    # sugestão do rodízio, CONSOME a fila de verdade (avança o ponteiro).
    responsavel_id = dados.get("responsavel_user_id")
    if dados.get("consumir_rodizio") and esc is not None and not esc.responsavel_fixo_user_id:
        responsavel_id = _proximo_responsavel_rr(db, esc) or responsavel_id
    if not responsavel_id:
        raise ValueError("Informe o responsável (ou configure a fila do escritório).")
    proc.responsavel_user_id = responsavel_id

    db.add(proc)
    db.flush()

    # Contrário com documento vira BbEnvolvido (a planilha lê o CPF/CNPJ de lá).
    adverso_doc = (dados.get("adverso_cpf_cnpj") or "").strip()
    if proc.adverso_principal and adverso_doc:
        from app.models.distribuidos_bb import BbEnvolvido

        db.add(BbEnvolvido(
            processo_id=proc.id,
            nome=proc.adverso_principal,
            cpf_cnpj=adverso_doc,
            tipo_pessoa=(dados.get("adverso_tipo") or "PF").strip() or "PF",
        ))

    registrar_evento(
        db, secao=SECAO_CADASTRO, nivel=NIVEL_SUCESSO, acao="Pasta avulsa criada",
        mensagem=(
            f"Pasta avulsa criada pelo modal: cliente {cliente_nome} ({tag}), "
            f"{posicao}, escritório {escritorio_path}."
        ),
        dados={"cnj": cnj, "cliente": tag}, processo_id=proc.id,
    )
    db.commit()
    db.refresh(proc)
    return proc


def cadastrar_imediato(db: Session, proc: BbProcesso) -> dict[str, Any]:
    """Fluxo imediato da avulsa: planilha só desta pasta → import no L1.

    Se o L1 falhar, o processo volta pro pool (NOVO) e o retorno explica — o
    operador pode re-tentar pelo botão de cadastro da planilha depois.
    """
    from app.services.distribuidos_bb.import_l1_service import cadastrar_planilha
    from app.services.distribuidos_bb.planilha_service import gerar_e_persistir

    try:
        planilha = gerar_e_persistir(db, processo_ids=[proc.id])
        if planilha is None:
            return {"cadastrado": False, "erro": "Planilha não foi gerada (processo sem responsável?)."}
        db.commit()
        rel = cadastrar_planilha(bytes(planilha.conteudo), planilha.nome_arquivo, dry_run=False)
        planilha.subido_legalone = True
        planilha.subido_em = datetime.now(timezone.utc)
        registrar_evento(
            db, secao=SECAO_CADASTRO, nivel=NIVEL_SUCESSO, acao="Pasta avulsa enviada ao L1",
            mensagem=(
                f"Import no Legal One enviado ({rel.get('novos', 0)} pasta[s]); "
                f"o monitor confirma nos próximos ciclos."
            ),
            dados={"planilha_id": planilha.id}, processo_id=proc.id,
        )
        db.commit()
        return {"cadastrado": True, "planilha_id": planilha.id}
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("Pasta avulsa: cadastro imediato falhou (processo %s).", proc.id)
        # devolve o processo pro pool pra não ficar preso em PENDENTE sem planilha
        vivo = db.get(BbProcesso, proc.id)
        if vivo is not None:
            vivo.planilha_status = POOL_NOVO
            vivo.planilha_id = None
            vivo.planilha_gerada_em = None
        registrar_evento(
            db, secao=SECAO_CADASTRO, nivel=NIVEL_ERRO, acao="Pasta avulsa: falha no L1",
            mensagem=f"A pasta foi criada, mas o cadastro no Legal One falhou: {exc}. Ela segue no pool.",
            processo_id=proc.id,
        )
        db.commit()
        return {"cadastrado": False, "erro": str(exc)}
