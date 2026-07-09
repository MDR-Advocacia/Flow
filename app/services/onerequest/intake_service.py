"""Serviço de intake do OneRequest.

Recebe os dados que o MOTOR RPA externo empurra (números e detalhes das DMIs)
e mantém a tabela `onr_solicitacoes` sincronizada. Toda a lógica de diff
(o que respondeu, o que é novo, o que reabriu) — que no OneRequest legado vivia
no SQLite da própria RPA — passa a viver AQUI, deixando a RPA como um scraper
fino que só posta.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.onerequest import (
    OnerequestSolicitacao,
    STATUS_SISTEMA_ABERTO,
    STATUS_SISTEMA_RESPONDIDO,
    STATUS_TRATAMENTO_NOVO,
)
from app.services.app_settings import set_setting

logger = logging.getLogger(__name__)

# Heartbeat da última ingestão (qualquer intake). Exibido no painel pros
# operadores vigiarem se a RPA parou de mandar dados.
LAST_INGEST_KEY = "onerequest_last_ingest_at"

# Atualizações em chunks pra não montar IN clauses gigantes no Postgres.
_UPDATE_CHUNK = 500


def _chunk(seq: list, size: int) -> Iterable[list]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


# ── Helpers do sync da fonte (Postgres do OneRequest) ─────────────────
# Campos CAPTURADOS pela RPA que o Flow espelha (o resto = tratamento do Flow,
# preservado). status_sistema e recebido_em tratados à parte.
_SOURCE_CAPTURED = ("titulo", "npj_direcionador", "prazo", "numero_processo", "polo")


def _src_clean(value):
    """Normaliza string da fonte: None/''/'N/A' -> None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.upper() == "N/A":
        return None
    return s


def _src_status(value) -> str:
    """'Aberto'/'Respondido' (texto da fonte) -> ABERTO/RESPONDIDO."""
    s = str(value).strip().lower() if value is not None else ""
    return STATUS_SISTEMA_RESPONDIDO if s.startswith("respond") else STATUS_SISTEMA_ABERTO


def _src_dt(value):
    """recebido_em (texto) -> datetime aware (UTC). None se não parsear."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


class OnerequestIntakeService:
    def __init__(self, db: Session):
        self.db = db

    def _touch_ingest(self) -> None:
        """Carimba o horário da última ingestão (heartbeat da RPA)."""
        try:
            set_setting(LAST_INGEST_KEY, datetime.now(timezone.utc).isoformat())
        except Exception as e:  # noqa: BLE001
            logger.warning("OneRequest: falha ao registrar last_ingest: %s", e)

    # ──────────────────────────────────────────────────────────────────
    # Robô 1 — sincronização de números (snapshot dos abertos no portal)
    # ──────────────────────────────────────────────────────────────────
    def sync_numeros(self, numeros: list[str]) -> dict:
        """
        Recebe o snapshot COMPLETO dos números de DMI abertos no portal do BB
        e reconcilia com o banco:

          1. Insere os números novos (status ABERTO / tratamento NOVO).
          2. Marca como RESPONDIDO os que estavam ABERTO e sumiram do snapshot.
          3. Reabre (volta a ABERTO) os que voltaram a aparecer no portal.

        Mantém `status_tratamento` intacto (AGENDADO/IGNORADO/etc. são do fluxo
        interno do Flow, independentes do lado do BB).
        """
        snapshot = {n.strip() for n in numeros if n and n.strip()}

        # Guarda de segurança: snapshot vazio quase sempre é glitch da RPA
        # (login falhou, página não carregou). NÃO marcar tudo como respondido.
        if not snapshot:
            logger.warning(
                "OneRequest intake/numeros recebeu snapshot vazio — ignorando "
                "para não marcar a base inteira como respondida."
            )
            return {"recebidos": 0, "novos": 0, "respondidos": 0, "reabertos": 0}

        rows = self.db.query(
            OnerequestSolicitacao.id,
            OnerequestSolicitacao.numero_solicitacao,
            OnerequestSolicitacao.status_sistema,
        ).all()

        existentes = {num for (_id, num, _st) in rows}
        responder_ids = [
            _id
            for (_id, num, st) in rows
            if st == STATUS_SISTEMA_ABERTO and num not in snapshot
        ]
        reabrir_ids = [
            _id
            for (_id, num, st) in rows
            if st == STATUS_SISTEMA_RESPONDIDO and num in snapshot
        ]
        novos = [n for n in snapshot if n not in existentes]

        agora = datetime.now(timezone.utc)

        for numero in novos:
            self.db.add(
                OnerequestSolicitacao(
                    numero_solicitacao=numero,
                    recebido_em=agora,
                    status_sistema=STATUS_SISTEMA_ABERTO,
                    status_tratamento=STATUS_TRATAMENTO_NOVO,
                )
            )

        for chunk in _chunk(responder_ids, _UPDATE_CHUNK):
            (
                self.db.query(OnerequestSolicitacao)
                .filter(OnerequestSolicitacao.id.in_(chunk))
                .update(
                    {OnerequestSolicitacao.status_sistema: STATUS_SISTEMA_RESPONDIDO},
                    synchronize_session=False,
                )
            )

        for chunk in _chunk(reabrir_ids, _UPDATE_CHUNK):
            (
                self.db.query(OnerequestSolicitacao)
                .filter(OnerequestSolicitacao.id.in_(chunk))
                .update(
                    {OnerequestSolicitacao.status_sistema: STATUS_SISTEMA_ABERTO},
                    synchronize_session=False,
                )
            )

        self.db.commit()
        self._touch_ingest()

        resultado = {
            "recebidos": len(snapshot),
            "novos": len(novos),
            "respondidos": len(responder_ids),
            "reabertos": len(reabrir_ids),
        }
        logger.info("OneRequest intake/numeros: %s", resultado)
        return resultado

    # ──────────────────────────────────────────────────────────────────
    # Robô 2 — fila de detalhamento e upsert dos detalhes
    # ──────────────────────────────────────────────────────────────────
    def pendentes_detalhe(self) -> list[str]:
        """Números abertos que ainda não têm título (= sem detalhe capturado)."""
        rows = (
            self.db.query(OnerequestSolicitacao.numero_solicitacao)
            .filter(
                OnerequestSolicitacao.status_sistema == STATUS_SISTEMA_ABERTO,
                OnerequestSolicitacao.titulo.is_(None),
            )
            .order_by(OnerequestSolicitacao.recebido_em.asc())
            .all()
        )
        return [num for (num,) in rows]

    def upsert_detalhes(self, itens: list) -> dict:
        """
        Atualiza os detalhes (título, NPJ, prazo, texto DMI, processo, polo) das
        solicitações já capturadas. `itens` é uma lista de objetos com atributo
        `numero_solicitacao` + os campos de detalhe (Pydantic ou dict-like).
        """
        atualizados = 0
        nao_encontrados: list[str] = []
        agora = datetime.now(timezone.utc)

        for item in itens:
            data = item.model_dump() if hasattr(item, "model_dump") else dict(item)
            numero = (data.get("numero_solicitacao") or "").strip()
            if not numero:
                continue

            row = (
                self.db.query(OnerequestSolicitacao)
                .filter(OnerequestSolicitacao.numero_solicitacao == numero)
                .first()
            )
            if not row:
                nao_encontrados.append(numero)
                continue

            for campo in (
                "titulo",
                "npj_direcionador",
                "prazo",
                "texto_dmi",
                "numero_processo",
                "polo",
            ):
                valor = data.get(campo)
                if valor is not None:
                    setattr(row, campo, valor)
            row.detalhe_capturado_em = agora
            atualizados += 1

        self.db.commit()
        self._touch_ingest()
        resultado = {"atualizados": atualizados, "nao_encontrados": nao_encontrados}
        logger.info(
            "OneRequest intake/detalhes: %s atualizados, %s não encontrados.",
            atualizados,
            len(nao_encontrados),
        )
        return resultado

    # ──────────────────────────────────────────────────────────────────
    # Sync READ-ONLY do Postgres da fonte (OneRequest/RPA). Espelha os campos
    # CAPTURADOS + status_sistema. O TRATAMENTO (responsável/setor/data/anotação)
    # depende do flag `onerequest_sync_espelha_tratamento`:
    #   True: espelha o tratamento da fonte SÓ PREENCHENDO o que está vazio no
    #         Flow (fill-only) — e pula DMI já tratada/agendada no Flow. O Flow
    #         é o DONO do tratamento; o espelho serve pra transição/histórico.
    #   False: o sync não toca no tratamento de jeito nenhum.
    # Em nenhum caso toca em created_task_id/l1_*/scheduled_by. Nunca deleta.
    # `rows` = lista de dicts lidos do Postgres da fonte.
    # ──────────────────────────────────────────────────────────────────
    def sync_from_source(self, rows: list) -> dict:
        from app.core.config import settings

        espelhar = settings.onerequest_sync_espelha_tratamento

        existentes = {
            r.numero_solicitacao: r
            for r in self.db.query(OnerequestSolicitacao).all()
        }

        # A fonte guarda o responsável por NOME — resolve pra id do LegalOneUser.
        # SÓ ATIVOS: se a fonte apontar ex-colaborador, deixa vazio (o L1 recusa
        # tarefa com participante inativo; melhor o operador escolher).
        nome_to_id: dict = {}
        if espelhar:
            from app.models.legal_one import LegalOneUser

            for uid, uname in (
                self.db.query(LegalOneUser.id, LegalOneUser.name)
                .filter(LegalOneUser.is_active.is_(True))
                .all()
            ):
                if uname:
                    nome_to_id.setdefault(uname.strip().lower(), uid)

        def _aplica_tratamento(row, r) -> bool:
            """Espelha responsável/setor/data/anotação da fonte — SÓ PREENCHENDO
            o que está VAZIO no Flow (fill-only). NUNCA sobrescreve nem apaga.
            (Bug corrigido 2026-07-09: o espelho sobrescrevia com 'N/A'/vazio da
            fonte e apagava o tratamento de DMI recém-agendada no Flow — ex.:
            DMI 2026/0000317161, agendada 10:24 e zerada pelo sync das 11h.)
            Retorna True se mudou algo."""
            # DMI já tratada/agendada no Flow: não mexe em NADA do tratamento.
            if (
                getattr(row, "created_task_id", None)
                or getattr(row, "scheduled_at", None)
                or (row.status_tratamento or STATUS_TRATAMENTO_NOVO) != STATUS_TRATAMENTO_NOVO
            ):
                return False
            mudou = False
            nome = _src_clean(r.get("responsavel"))
            resp_id = nome_to_id.get(nome.lower()) if nome else None
            if resp_id and not row.responsavel_user_id:
                row.responsavel_user_id = resp_id
                mudou = True
            for campo in ("setor", "data_agendamento", "anotacao"):
                novo = _src_clean(r.get(campo))
                if novo and not getattr(row, campo):
                    setattr(row, campo, novo)
                    mudou = True
            return mudou

        inseridos = atualizados = 0
        st_count = {STATUS_SISTEMA_ABERTO: 0, STATUS_SISTEMA_RESPONDIDO: 0}

        for r in rows:
            numero = _src_clean(r.get("numero_solicitacao"))
            if not numero:
                continue
            status = _src_status(r.get("status_sistema"))
            st_count[status] += 1
            texto = r.get("texto_dmi") or None

            row = existentes.get(numero)
            if row is None:
                novo = OnerequestSolicitacao(
                    numero_solicitacao=numero,
                    titulo=_src_clean(r.get("titulo")),
                    npj_direcionador=_src_clean(r.get("npj_direcionador")),
                    prazo=_src_clean(r.get("prazo")),
                    texto_dmi=texto,
                    numero_processo=_src_clean(r.get("numero_processo")),
                    polo=_src_clean(r.get("polo")),
                    recebido_em=_src_dt(r.get("recebido_em")),
                    status_sistema=status,
                    status_tratamento=STATUS_TRATAMENTO_NOVO,
                )
                if espelhar:
                    _aplica_tratamento(novo, r)
                self.db.add(novo)
                inseridos += 1
                continue

            # Existente: capturados + status sempre; tratamento só se espelhar.
            changed = False
            for f in _SOURCE_CAPTURED:
                nv = _src_clean(r.get(f))
                if getattr(row, f) != nv:
                    setattr(row, f, nv)
                    changed = True
            if (row.texto_dmi or None) != texto:
                row.texto_dmi = texto
                changed = True
            if row.status_sistema != status:
                row.status_sistema = status
                changed = True
            if espelhar and _aplica_tratamento(row, r):
                changed = True
            if changed:
                atualizados += 1

        self.db.commit()
        self._touch_ingest()
        resultado = {
            "recebidos": len(rows),
            "inseridos": inseridos,
            "atualizados": atualizados,
            "abertos": st_count[STATUS_SISTEMA_ABERTO],
            "respondidos": st_count[STATUS_SISTEMA_RESPONDIDO],
            "espelha_tratamento": espelhar,
        }
        logger.info("OneRequest sync da fonte: %s", resultado)
        return resultado
