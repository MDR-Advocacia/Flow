"""Rito do processo (justiça comum × juizado especial × trabalhista).

POR QUE É CAMPO, E NÃO CATEGORIA
--------------------------------
Rito não é providência. Ele muda o QUE se faz com a mesma providência —
recurso inominado no juizado × apelação na comum, prazo em dobro que não
existe no juizado, custas e preparo diferentes — mas é ortogonal à
classificação. Empurrar rito para dentro da taxonomia obrigaria a duplicar
cada categoria em duas, e foi por isso que a taxonomia v2 nunca o teve: as
categorias da fila comum são todas de providência ("Recursos e Julgamentos
em 2º Grau", "Manifestações, Prazos e Providências"), e o rito simplesmente
não aparecia em lugar nenhum.

DE ONDE VEM
-----------
Duas fontes, nesta ordem, e a ordem não é arbitrária:

  1. O TEXTO da publicação — grátis, instantâneo, sem depender de ninguém.
     Resolve ~63% sozinho (medição de 03/09/2026 sobre as 409 individuais).
     Roda no momento em que a publicação nasce, junto do `uf`.
  2. O DataJud, pelo CNJ — preenche o resto pelo `orgaoJulgador`/`classe`.
     Custa uma requisição, então NÃO roda na captura: é um segundo passo,
     em lote. Para saber QUEM representamos o DataJud não serve (testado:
     ele não devolve partes), mas para RITO serve bem.

O cache é permanente de propósito: **rito de processo não muda**. Um
processo não migra de juizado para vara comum, então uma resposta do DataJud
vale para sempre e para todas as publicações daquele CNJ — 66.627
publicações com pasta se reduzem a 34.718 CNJs distintos. Linha com
`rito = NULL` também é resposta: significa "já perguntei e não deu", e
impede de perguntar de novo para sempre.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from sqlalchemy.orm import Session

# As primitivas nasceram na fila sem pasta e são puras (regex sobre texto).
# Ficam importadas de lá em vez de copiadas: duplicar regex de detecção é
# como duas ficam diferentes sem ninguém perceber. Este módulo é o endereço
# público do rito — quem precisar deve importar daqui, não de lá.
from app.services.publication_sem_pasta import (  # noqa: F401
    RITO_COMUM,
    RITO_JUIZADO,
    RITO_TRABALHISTA,
    ROTULO_RITO,
    detectar_rito,
    rito_do_orgao,
)

logger = logging.getLogger(__name__)

RITOS_VALIDOS = (RITO_JUIZADO, RITO_COMUM, RITO_TRABALHISTA)

FONTE_TEXTO = "texto"
FONTE_DATAJUD = "datajud"


def digitos(cnj: Optional[str]) -> Optional[str]:
    """Só os números do CNJ — é a chave do cache. CNJ chega com máscara,
    sem máscara e com OCR sujo; 20 dígitos é o único formato estável."""
    d = re.sub(r"\D", "", cnj or "")
    return d if len(d) == 20 else None


# ── cache por CNJ ────────────────────────────────────────────────────
def rito_em_cache(db: Session, cnj: Optional[str]) -> Optional[dict[str, Any]]:
    """Devolve `{"rito", "fonte", "orgao"}` se este CNJ já foi resolvido.

    `None` = nunca perguntamos. Um dict com `rito=None` = perguntamos e o
    DataJud não soube — o que é diferente, e é o que evita reperguntar."""
    d = digitos(cnj)
    if not d:
        return None
    from app.models.publication_rito import ProcessoRito

    linha = db.query(ProcessoRito).filter(ProcessoRito.cnj_digitos == d).first()
    if not linha:
        return None
    return {"rito": linha.rito, "fonte": linha.fonte, "orgao": linha.orgao}


def guardar_rito(
    db: Session,
    cnj: Optional[str],
    rito: Optional[str],
    fonte: str,
    orgao: Optional[str] = None,
    commit: bool = False,
) -> None:
    """Grava (ou atualiza) o rito de um CNJ. Best-effort: falhar aqui não
    pode derrubar quem chamou — o cache é aceleração, não verdade."""
    d = digitos(cnj)
    if not d:
        return
    try:
        from app.models.publication_rito import ProcessoRito

        linha = db.query(ProcessoRito).filter(ProcessoRito.cnj_digitos == d).first()
        if linha:
            linha.rito, linha.fonte, linha.orgao = rito, fonte, orgao
        else:
            db.add(ProcessoRito(cnj_digitos=d, rito=rito, fonte=fonte, orgao=orgao))
        # FLUSH obrigatorio: o SessionLocal da casa e autoflush=False, entao
        # sem isto a linha recem-adicionada fica invisivel para o
        # `rito_em_cache` da proxima publicacao dentro da MESMA transacao — e
        # o lote reperguntaria ao DataJud o mesmo CNJ ate o commit seguinte,
        # que e exatamente o que o cache existe para evitar.
        db.flush()
        if commit:
            db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("Rito: não consegui gravar o cache de %s.", cnj, exc_info=True)
        if commit:
            db.rollback()


# ── resolução ────────────────────────────────────────────────────────
def rito_pelo_texto(texto: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """`(rito, fonte)` a partir do texto. Não toca no banco nem na rede —
    é o que permite rodar no nascimento da publicação sem custo."""
    achado = detectar_rito(texto)
    if achado.get("rito"):
        return achado["rito"], FONTE_TEXTO
    return None, None


def resolver_rito(
    db: Session,
    texto: Optional[str],
    cnj: Optional[str] = None,
    consultar_datajud: bool = False,
) -> dict[str, Any]:
    """Rito por todas as fontes disponíveis, da mais barata para a mais cara.

    `consultar_datajud=False` (default) é o modo da captura: texto + cache,
    nada de rede. O passo em lote chama com `True`.
    """
    rito, fonte = rito_pelo_texto(texto)
    if rito:
        # O texto resolveu: aproveita e alimenta o cache do CNJ, porque a
        # próxima publicação desse processo pode vir sem os mesmos sinais.
        if cnj and rito_em_cache(db, cnj) is None:
            guardar_rito(db, cnj, rito, FONTE_TEXTO)
        return {"rito": rito, "fonte": fonte, "evidencia": detectar_rito(texto).get("evidencia")}

    em_cache = rito_em_cache(db, cnj)
    if em_cache is not None:
        return {
            "rito": em_cache["rito"], "fonte": em_cache["fonte"],
            "evidencia": f"DataJud: {em_cache['orgao']}" if em_cache.get("orgao") else None,
        }

    if not consultar_datajud or not cnj:
        return {"rito": None, "fonte": None, "evidencia": None}

    return consultar_datajud_e_cachear(db, cnj)


def consultar_datajud_e_cachear(db: Session, cnj: str) -> dict[str, Any]:
    """Pergunta ao DataJud e guarda a resposta.

    Mesmo caminho que a fila sem pasta já usa em produção (índice por alias
    de tribunal + match no número): não inventa consulta nova para não ter
    dois jeitos de perguntar a mesma coisa.

    Guarda TAMBÉM quando não resolve (`rito=None`) — é o que impede
    reperguntar eternamente pelo mesmo CNJ. A exceção é falha de REDE, que
    não é "não sei": aí não cacheia nada e a próxima passagem tenta de novo.
    """
    try:
        from app.services.citacoes_bm.datajud import get_client
        from app.services.citacoes_bm.tribunal_alias import (
            cnj_digits, resolve_tribunal_alias,
        )

        alias = resolve_tribunal_alias(cnj)
        d = cnj_digits(cnj)
        if not alias or not d:
            # Tribunal fora do DataJud: é resposta definitiva, cacheia.
            guardar_rito(db, cnj, None, FONTE_DATAJUD)
            return {"rito": None, "fonte": None, "evidencia": None}

        resp = get_client().search_processes(
            alias, {"size": 1, "query": {"match": {"numeroProcesso": d}}},
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("Rito: DataJud falhou para %s (%s).", cnj, exc)
        return {"rito": None, "fonte": None, "evidencia": None}

    hits = (resp.get("hits") or {}).get("hits") or []
    if not hits:
        guardar_rito(db, cnj, None, FONTE_DATAJUD)
        return {"rito": None, "fonte": None, "evidencia": None}

    src = hits[0].get("_source") or {}
    og, cl = src.get("orgaoJulgador") or {}, src.get("classe") or {}
    orgao = og.get("nome") if isinstance(og, dict) else og
    classe = cl.get("nome") if isinstance(cl, dict) else cl

    rito = rito_do_orgao(orgao, classe)
    guardar_rito(db, cnj, rito, FONTE_DATAJUD, orgao=orgao)
    return {
        "rito": rito,
        "fonte": FONTE_DATAJUD if rito else None,
        "evidencia": f"DataJud: {orgao}" if rito and orgao else None,
    }
