"""Exceção de roteamento por ETIQUETA do processo (sqd005).

POR QUE EXISTE
--------------
No Autor, as tarefas de publicação são distribuídas por rodízio entre dois
grandes grupos — assistentes e advogados —, configurados como squads de
suporte nos templates. A Equipe Mista (etiqueta NERC no L1) acumula os dois
polos e é exceção a essa regra: processo NERC fica com o advogado da equipe e
com o assistente dele, fora do rodízio geral. Pedido do operador em set/2026.

O QUE FAZ
---------
Quando o template tem `excecao_etiqueta` e o processo tem essa etiqueta no
cache do L1, a squad de suporte e o responsável fixo do template deixam de
valer e a tarefa vai para a equipe marcada com a etiqueta, com "a mesma
lógica dos templates comuns":
  - papel ADVOGADO ('principal')    → o responsável da pasta;
  - papel ASSISTENTE ('assistente') → rodízio entre os assistentes da squad
    marcada com a etiqueta em que o responsável da pasta é membro.

O PAPEL NA EQUIPE É DO TEMPLATE, NÃO DO target_role
---------------------------------------------------
`excecao_papel` diz quem da equipe recebe. Não dá para deduzir do
`target_role`: no BB Autor o grupo de ADVOGADOS roda como 'assistente' numa
squad de suporte, porque só o papel de assistente tem rodízio no resolvedor.
Deduzir pelo target_role mandaria tarefa de advogado para o assistente da
equipe. Vazio = segue o target_role (template salvo por fora da tela).

POR QUE A SQUAD PRECISA DECLARAR A ETIQUETA
-------------------------------------------
O resolvedor comum escolhe a squad do responsável pelo escritório da tarefa
e, sem casamento, fica com a de menor id. Em produção (set/2026) as squads
NERC estão cadastradas no BB Réu, e uma das advogadas também é membro da
CELULA 2, de outro escritório: numa tarefa do BB Autor nenhuma casa pelo
escritório e o desempate escolheria a CELULA 2 — a tarefa de assistente iria
para a pessoa errada. A marcação explícita tira essa decisão do acaso.

NOME, NÃO ID
------------
O id da etiqueta NERC mudou no L1 em 04/09/2026 (7 → 83); o nome ficou.
A comparação é pelo nome normalizado (sem acento, sem caixa, espaços
colapsados). O NOME também já mudou uma vez — a 83 se chamava "BASE NERC" —,
e por isso o seletor da tela só oferece nome lido recentemente
(`etiquetas_em_uso`).

FALHAR ALTO
-----------
Etiqueta presente mas responsável da pasta fora de squad marcada é processo
que ainda não passou para a equipe — vale para os dois papéis. Isso NÃO cai em
silêncio no rodízio geral: levanta `ValueError` com mensagem para o operador,
que escolhe o responsável na mão — e a escolha manual sempre vence.
"""
from __future__ import annotations

import json
import logging
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# (db, lawsuit_id) -> external_id do responsável da pasta, ou None.
BuscarResponsavel = Callable[[Session, int], Optional[int]]

PAPEIS = ("principal", "assistente")

# Janela do vocabulário oferecido na tela. O job relê todo processo com
# publicação nos últimos 3 dias; 7 dias cobre fim de semana e feriado.
JANELA_VOCABULARIO_DIAS = 7


def normalizar_etiqueta(nome: Optional[str]) -> str:
    """Forma comparável: sem acento, maiúsculas, espaços colapsados."""
    texto = unicodedata.normalize("NFD", (nome or "").strip())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(texto.upper().split())


def _como_lista(valor: Any) -> list[dict]:
    """Coluna `etiquetas` do cache como lista de dicts, tolerando texto."""
    if isinstance(valor, str):
        try:
            valor = json.loads(valor)
        except ValueError:
            return []
    if not isinstance(valor, list):
        return []
    return [item for item in valor if isinstance(item, dict)]


def etiquetas_do_processo(db: Session, lawsuit_id: int) -> list[dict]:
    """Etiquetas do processo segundo o cache do L1.

    Pelo ORM de propósito: `etiquetas_por_lawsuit` usa `= ANY(...)`, que só
    existe no Postgres, e esta regra precisa ser testável no SQLite.

    Processo fora do cache conta como "sem etiqueta". O cache é renovado pelo
    job de etiquetas (validade de 20h), o que a operação considerou suficiente
    para esta regra."""
    from app.models.publication_search import PublicationL1EtiquetaCache

    linha = (
        db.query(PublicationL1EtiquetaCache)
        .filter(PublicationL1EtiquetaCache.lawsuit_id == int(lawsuit_id))
        .first()
    )
    return _como_lista(linha.etiquetas) if linha is not None else []


def etiqueta_do_processo(
    db: Session, lawsuit_id: Optional[int], etiqueta: Optional[str],
) -> Optional[str]:
    """Nome da etiqueta como está no L1 se o processo a tem; senão None."""
    alvo = normalizar_etiqueta(etiqueta)
    if not alvo or not lawsuit_id:
        return None
    for item in etiquetas_do_processo(db, int(lawsuit_id)):
        nome = item.get("name")
        if nome and normalizar_etiqueta(str(nome)) == alvo:
            return str(nome).strip()
    return None


def etiquetas_em_uso(db: Session, *, dias: int = JANELA_VOCABULARIO_DIAS) -> list[str]:
    """Vocabulário ATUAL de etiquetas: nomes lidos no L1 nos últimos `dias`,
    do mais usado para o menos usado.

    Diferente do filtro da Triagem (`etiquetas_distintas`, o cache inteiro) de
    propósito. O cache guarda o nome da época em que cada processo foi lido, e
    processo sem publicação recente não é relido: em 10/09/2026 ele ainda
    mostrava "BASE NERC" — o nome antigo da própria NERC — em 333 processos,
    no topo da lista. Para CONFIGURAR a exceção, oferecer nome que o L1 não
    usa mais é armadilha: nenhum processo relido o tem, e a exceção nunca
    dispararia. Sem nenhuma leitura na janela (job parado), cai para o cache
    inteiro."""
    from app.models.publication_search import PublicationL1EtiquetaCache

    desde = datetime.now(timezone.utc) - timedelta(days=dias)
    consulta = db.query(PublicationL1EtiquetaCache.etiquetas)
    linhas = consulta.filter(PublicationL1EtiquetaCache.fetched_at >= desde).all()
    if not linhas:
        linhas = consulta.all()
    contagem: dict[str, int] = {}
    for (valor,) in linhas:
        nomes = {str(item.get("name")).strip() for item in _como_lista(valor) if item.get("name")}
        for nome in nomes:
            if nome:
                contagem[nome] = contagem.get(nome, 0) + 1
    return [nome for nome, _ in sorted(contagem.items(), key=lambda kv: (-kv[1], kv[0]))]


def responsavel_da_pasta(db: Session, lawsuit_id: int) -> Optional[int]:
    """Responsável da pasta: cache do L1 primeiro, API numa chamada só no miss.

    Não usa `prefetch_lawsuit_responsibles_cache`: ele abre um
    ThreadPoolExecutor, e o container da API tem teto de threads. Aqui é
    sempre UM processo por vez."""
    del db  # o cliente lê o lawsuit_cache com a própria sessão
    lid = int(lawsuit_id)
    try:
        from app.services.legal_one_client import LegalOneApiClient

        cliente = LegalOneApiClient()
    except Exception:  # noqa: BLE001 — sem cliente, o chamador falha alto
        logger.warning(
            "excecao_etiqueta: cliente do Legal One indisponível (lawsuit=%s)",
            lid, exc_info=True,
        )
        return None

    achado: Optional[dict] = None
    try:
        achado = cliente.get_cached_lawsuit_responsibles_batch([lid]).get(lid)
    except Exception:  # noqa: BLE001
        logger.warning(
            "excecao_etiqueta: leitura do cache de responsável falhou (lawsuit=%s)",
            lid, exc_info=True,
        )
    if not achado:
        try:
            achado = cliente.get_lawsuit_responsible_user(lid)
        except Exception:  # noqa: BLE001
            logger.warning(
                "excecao_etiqueta: Legal One não devolveu o responsável (lawsuit=%s)",
                lid, exc_info=True,
            )
            achado = None
    if not isinstance(achado, dict) or achado.get("id") is None:
        return None
    try:
        return int(achado["id"])
    except (TypeError, ValueError):
        return None


def _nome_do_usuario(db: Session, external_id: int) -> str:
    from app.models.legal_one import LegalOneUser

    usuario = (
        db.query(LegalOneUser)
        .filter(LegalOneUser.external_id == int(external_id))
        .first()
    )
    return usuario.name if usuario is not None else f"usuário {external_id}"


def squad_da_etiqueta(db: Session, etiqueta: str, responsavel_external_id: int):
    """Squad ATIVA, marcada com a etiqueta, em que o responsável é membro."""
    from app.models.legal_one import LegalOneUser
    from app.models.rules import Squad, SquadMember

    alvo = normalizar_etiqueta(etiqueta)
    candidatas = (
        db.query(Squad)
        .join(SquadMember, SquadMember.squad_id == Squad.id)
        .join(LegalOneUser, LegalOneUser.id == SquadMember.legal_one_user_id)
        .filter(
            Squad.is_active.is_(True),
            Squad.etiqueta.isnot(None),
            LegalOneUser.external_id == int(responsavel_external_id),
        )
        .order_by(Squad.id)
        .all()
    )
    marcadas = []
    for squad in candidatas:
        if normalizar_etiqueta(squad.etiqueta) == alvo and squad not in marcadas:
            marcadas.append(squad)
    if len(marcadas) > 1:
        logger.warning(
            "excecao_etiqueta: responsável %s é membro de %d squads marcadas com %r; "
            "usando a de menor id (%s).",
            responsavel_external_id, len(marcadas), etiqueta, marcadas[0].id,
        )
    return marcadas[0] if marcadas else None


def aplicar_excecao_etiqueta(
    db: Session,
    *,
    template_id: Optional[int],
    lawsuit_id: Optional[int],
    target_role: str,
    commit: bool = False,
    buscar_responsavel: Optional[BuscarResponsavel] = None,
):
    """Aplica a exceção por etiqueta do template, se couber.

    Devolve `None` quando a exceção NÃO se aplica — sem processo, sem
    template, template sem exceção, processo sem a etiqueta. Aí o chamador
    segue a regra normal do template.

    Quando se aplica, devolve `(AssistantResolutionResult, motivo)`, com o
    motivo em português para a tela e a trilha de auditoria.

    Levanta `ValueError` com mensagem para o operador quando a etiqueta está
    lá mas não há como rotear com segurança.

    `target_role` é o papel do template, usado só quando o template não diz o
    papel na equipe (`excecao_papel`). `commit=True` avança o rodízio da squad
    (mesma semântica do resolvedor comum); a transação é do chamador.
    """
    if not template_id or not lawsuit_id:
        return None

    from app.models.task_template import TaskTemplate
    from app.services.squad_assistant_resolver import (
        AssistantResolutionResult,
        _resolve_in_squad,
    )

    tmpl = db.query(TaskTemplate).filter(TaskTemplate.id == int(template_id)).first()
    etiqueta = ((tmpl.excecao_etiqueta if tmpl is not None else None) or "").strip()
    if not etiqueta:
        return None

    nome_no_l1 = etiqueta_do_processo(db, lawsuit_id, etiqueta)
    if not nome_no_l1:
        return None

    papel = (tmpl.excecao_papel or "").strip() or target_role

    buscar = buscar_responsavel or responsavel_da_pasta
    responsavel = buscar(db, int(lawsuit_id))
    if not responsavel:
        raise ValueError(
            f"Processo com a etiqueta {nome_no_l1}: não foi possível ler o "
            "responsável da pasta no Legal One agora. Tente de novo em instantes "
            "ou escolha o responsável manualmente."
        )

    nome_responsavel = _nome_do_usuario(db, responsavel)
    squad = squad_da_etiqueta(db, etiqueta, int(responsavel))
    if squad is None:
        raise ValueError(
            f"Processo com a etiqueta {nome_no_l1}, mas o responsável da pasta "
            f"({nome_responsavel}) não é membro de nenhuma squad marcada com "
            f"{nome_no_l1}. Se o processo ainda não passou para a equipe, escolha "
            "o responsável manualmente; se já passou, marque a squad dele com a "
            "etiqueta em Admin > Squads."
        )

    if papel != "assistente":
        logger.info(
            "excecao_etiqueta: lawsuit=%s template=%s etiqueta=%s papel=advogado "
            "squad=%s -> %s",
            lawsuit_id, template_id, nome_no_l1, squad.id, responsavel,
        )
        return (
            AssistantResolutionResult(
                user_external_id=int(responsavel),
                squad_id=squad.id,
                squad_name=squad.name,
            ),
            f"Exceção por etiqueta {nome_no_l1}: tarefa de advogado vai para o "
            f"responsável da pasta ({nome_responsavel}), da squad {squad.name}, "
            "fora da regra do template.",
        )

    # O mesmo rodízio das squads de suporte, reusado de propósito: não pode
    # existir um segundo rodízio com regra própria.
    resultado = _resolve_in_squad(
        db, squad_id=squad.id, target_role="assistente", commit=commit,
    )
    logger.info(
        "excecao_etiqueta: lawsuit=%s template=%s etiqueta=%s papel=assistente "
        "squad=%s -> %s",
        lawsuit_id, template_id, nome_no_l1, squad.id, resultado.user_external_id,
    )
    return (
        resultado,
        f"Exceção por etiqueta {nome_no_l1}: tarefa de assistente vai para o "
        f"rodízio da squad {squad.name}, fora da squad do template.",
    )
