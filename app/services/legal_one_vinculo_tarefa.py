"""
Tarefa agendada em pasta sai do Flow VINCULADA à pasta — sozinha.

No Legal One, criar tarefa em pasta são duas chamadas: `POST /tasks` (a tarefa
nasce) e `POST /tasks/{id}/relationships` (a tarefa entra na pasta). Até
11/09/2026 todo ponto do Flow que agenda em pasta tratava a segunda como
detalhe — ignorava o `False` de `link_task_to_lawsuit`, ou só logava aviso — e
marcava sucesso. Quando o vínculo falhava, a tarefa ficava pendente na agenda
do executante sem pasta nenhuma, e ninguém sabia de qual processo ela era.

Caso real, reportado pelo operador com a agenda da Supervisão Master (13
tarefas pendentes sem pasta):

- 02/09 11:01 — a publicação apontava para a pasta 67080, que já não existia
  no L1 (404; o CNJ vive na 65577). A tarefa 463601 nasceu, o vínculo foi
  recusado, o `False` foi ignorado e a auditoria gravou "agendado".
- 28/08 13:20–13:29 — o vínculo falhou por minutos seguidos no L1. Erro que
  não é HTTPError sobe do cliente e aborta o pedido DEPOIS de a tarefa existir;
  o banco volta atrás, a tarefa fica. Cada novo clique da operadora criou mais
  uma: 4 audiências e 8 contestações soltas do mesmo processo (e, no mesmo
  intervalo, 6 contrarrazões do BB Defesa).

A regra agora (decisão do operador, 11/09/2026: "criou tarefa sem vínculo?
cancela, não precisa mandar nada pro operador, já reenvia de novo"): vínculo
que falha é conferido no L1 — a resposta pode ter se perdido com o vínculo
feito. Se a tarefa não está mesmo na pasta, ela é CANCELADA (status 3, o mesmo
PATCH que o Flow já usa; cancelada não aparece como pendente em agenda
nenhuma) e a tarefa é criada de novo, até vincular. Quem agendou não vê nada.

Limites, porque "até dar certo" não pode virar laço criando e cancelando
tarefa no L1 sem fim:
- no máximo TENTATIVAS criações por tarefa, com espera crescente entre elas
  (e cada vínculo já passa pelas 8 retentativas do cliente para 429/5xx/rede);
- pasta que não existe mais no L1 não é reenviada — nunca vai vincular;
- se nem o cancelamento passa, para: reenviar criaria outra tarefa solta.
Esgotou: levanta `TarefaSemVinculoError` e o fluxo trata como a falha que é
(publicação continua pendente, linha do lote vira FALHA) — nunca "sucesso"
com tarefa sem pasta.

Tarefa avulsa (sem pasta de propósito — DMI sem processo, publicação sem
pasta) não passa por aqui: quem chama só usa isto quando há pasta.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

# L1 /Tasks: 0 pendente, 1 cumprido, 2 não cumprido, 3 cancelado.
STATUS_TAREFA_CANCELADA = 3
# Espera (segundos) antes de cada reenvio: 5 criações no máximo.
ESPERAS_ENTRE_TENTATIVAS = (2, 5, 10, 20)
TENTATIVAS = len(ESPERAS_ENTRE_TENTATIVAS) + 1
_dormir = time.sleep  # os testes trocam


class TarefaSemVinculoError(RuntimeError):
    """A tarefa não entrou na pasta nem reenviando. `tarefas_soltas` = as que nem cancelar deu."""

    def __init__(self, mensagem: str, *, lawsuit_id: int, tentativas: int, tarefas_soltas: Iterable[int] = ()):
        super().__init__(mensagem)
        self.lawsuit_id = lawsuit_id
        self.tentativas = tentativas
        self.tarefas_soltas = list(tarefas_soltas)


def tarefa_esta_na_pasta(client: Any, task_id: int, lawsuit_id: int) -> bool:
    """True se o L1 mostra a tarefa vinculada ao processo. Na dúvida, False."""
    consultar = getattr(client, "get_task_relationships", None)
    if consultar is None:
        return False
    try:
        vinculos = consultar(int(task_id)) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("Não deu para conferir os vínculos da tarefa %s no L1: %s", task_id, exc)
        return False
    return any(
        isinstance(v, dict)
        and v.get("linkType") == "Litigation"
        and str(v.get("linkId")) == str(lawsuit_id)
        for v in vinculos
    )


def cancelar_tarefas_criadas(client: Any, task_ids: Iterable[int]) -> list[int]:
    """Cancela no L1 tarefas que o próprio Flow criou. Devolve as que NÃO conseguiu cancelar."""
    atualizar = getattr(client, "update_task_status", None)
    nao_canceladas: list[int] = []
    for tid in task_ids:
        ok = False
        if atualizar is not None:
            try:
                ok = bool(atualizar(int(tid), STATUS_TAREFA_CANCELADA))
            except Exception as exc:  # noqa: BLE001
                logger.error("Falha ao cancelar a tarefa %s no L1: %s", tid, exc)
        if not ok:
            nao_canceladas.append(int(tid))
    return nao_canceladas


def _vincular(client: Any, task_id: int, lawsuit_id: int) -> Optional[str]:
    """None = a tarefa está na pasta. Senão, o motivo da falha."""
    motivo = None
    try:
        if client.link_task_to_lawsuit(task_id, {"linkType": "Litigation", "linkId": lawsuit_id}):
            return None
        motivo = getattr(client, "_last_link_error", None)
    except Exception as exc:  # noqa: BLE001 — rede esgotada, 5xx insistente
        motivo = str(exc) or type(exc).__name__
    if tarefa_esta_na_pasta(client, task_id, lawsuit_id):
        logger.warning(
            "Vínculo da tarefa %s ao processo %s deu erro (%s), mas ela está na pasta.",
            task_id, lawsuit_id, motivo,
        )
        return None
    return (motivo or "o Legal One recusou o vínculo")[:300]


def _processo_existe(client: Any, lawsuit_id: int) -> Optional[bool]:
    consultar = getattr(client, "processo_existe", None)
    if consultar is None:
        return None
    try:
        return consultar(lawsuit_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Não deu para conferir se o processo %s existe no L1: %s", lawsuit_id, exc)
        return None


def criar_tarefa_na_pasta(
    client: Any,
    payload: dict,
    lawsuit_id: int,
    *,
    tentativas: int = TENTATIVAS,
) -> Optional[dict]:
    """
    Cria a tarefa e só devolve quando ela está vinculada ao processo.

    Devolve o `created` do L1 da tarefa que ficou na pasta, ou o que
    `create_task` devolveu quando o L1 recusou a CRIAÇÃO (None) — esse caso
    quem chama continua tratando como sempre. Vínculo que não pega: cancela a
    tarefa e cria de novo (ver limites no topo do módulo).
    """
    lawsuit_id = int(lawsuit_id)
    motivo = None
    for tentativa in range(1, tentativas + 1):
        created = client.create_task(payload)
        if not created or not created.get("id"):
            return created
        task_id = int(created["id"])
        motivo = _vincular(client, task_id, lawsuit_id)
        if motivo is None:
            if tentativa > 1:
                logger.warning(
                    "Tarefa %s vinculada ao processo %s na tentativa %s (as anteriores, sem pasta, foram canceladas).",
                    task_id, lawsuit_id, tentativa,
                )
            return created

        logger.warning(
            "Tarefa %s criada sem vínculo ao processo %s (%s) — cancelando e reenviando (tentativa %s/%s).",
            task_id, lawsuit_id, motivo, tentativa, tentativas,
        )
        if cancelar_tarefas_criadas(client, [task_id]):
            raise TarefaSemVinculoError(
                f"A tarefa {task_id} não vinculou ao processo {lawsuit_id} ({motivo}) "
                "e não pôde ser cancelada no Legal One.",
                lawsuit_id=lawsuit_id, tentativas=tentativa, tarefas_soltas=[task_id],
            )
        if _processo_existe(client, lawsuit_id) is False:
            raise TarefaSemVinculoError(
                f"O processo {lawsuit_id} não existe mais no Legal One: a tarefa não "
                "vinculou e foi cancelada.",
                lawsuit_id=lawsuit_id, tentativas=tentativa,
            )
        if tentativa < tentativas:
            _dormir(ESPERAS_ENTRE_TENTATIVAS[min(tentativa, len(ESPERAS_ENTRE_TENTATIVAS)) - 1])

    raise TarefaSemVinculoError(
        f"A tarefa não vinculou ao processo {lawsuit_id} em {tentativas} tentativas "
        f"({motivo}); todas foram canceladas no Legal One.",
        lawsuit_id=lawsuit_id, tentativas=tentativas,
    )
