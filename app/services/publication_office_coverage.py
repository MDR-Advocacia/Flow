"""Vigia da COBERTURA da captura de publicações.

## Por que isso existe

Em 05/08/2026 a operação achou dois processos com publicação visível na tela do
Legal One que o Flow nunca capturou — e, pior, que não apareciam nem como
descarte na auditoria. A causa: a busca de publicações filtra por escritório, e
pasta cadastrada sem escritório responsável fica no nó raiz ("MDR Advocacia",
id 1), que não entra em busca nenhuma. A publicação não foi ignorada; ela nunca
foi vista. Eram 654 pastas ativas nessa situação, uma delas com prazo de réplica
já decorrido.

O buraco é silencioso por natureza: a rodada termina com sucesso, os
escritórios varridos respondem tudo certo, e o que está fora do mapa não gera
erro nenhum — só ausência. Nenhum alerta existente pegava isso, porque todos
vigiam a execução, e aqui a execução estava perfeita. O que faltava era vigiar
o PERÍMETRO.

## O que é defeito e o que é escolha

- Escritório raiz (id 1): sempre defeito. Pasta sem escritório é falha de
  cadastro, não decisão. Alerta com nome e sobrenome.
- Outros escritórios fora da lista varrida: pode ser intencional (área que não
  opera por publicação). Entra no mesmo e-mail como informação, pro operador
  decidir — sem transformar escolha deliberada em alarme recorrente.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

logger = logging.getLogger("publicacoes.cobertura")

ESCRITORIO_RAIZ_ID = 1
# statusId 1 = Ativo. Pasta encerrada fora da cobertura não interessa: não vai
# receber publicação nova.
_FILTRO_ATIVAS = "statusId eq 1"


def _contar_pastas_ativas(client: Any, office_id: int) -> Optional[int]:
    """Quantas pastas ATIVAS estão nesse escritório, direto da API.

    /Litigations é o guarda-chuva (processo + recurso + incidente); /Lawsuits é
    subconjunto dele. Contar pelos dois somaria o mesmo processo duas vezes.
    """
    try:
        resp = client._request_with_retry(
            "GET",
            f"{client.base_url}/Litigations",
            params={
                "$filter": f"responsibleOfficeId eq {int(office_id)} and {_FILTRO_ATIVAS}",
                "$select": "id",
                "$top": 1,
                "$count": "true",
            },
        )
        if resp.status_code == 200:
            return int(resp.json().get("@odata.count") or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Cobertura: falha ao contar o escritório %s: %s", office_id, exc)
    return None


def verificar_cobertura(
    db: Any, client: Any, office_ids_varridos: Sequence[int]
) -> dict[str, Any]:
    """Mede o que está FORA do perímetro da captura.

    Devolve `{"raiz": N, "fora": [{office_id, path, pastas}], "total_fora": N}`.
    Nunca levanta: vigia que derruba a rodada que deveria proteger é pior que
    vigia nenhum.
    """
    resultado: dict[str, Any] = {"raiz": 0, "fora": [], "total_fora": 0}
    try:
        from app.models.legal_one import LegalOneOffice

        varridos = {int(o) for o in office_ids_varridos if o}
        resultado["raiz"] = _contar_pastas_ativas(client, ESCRITORIO_RAIZ_ID) or 0

        for office in db.query(LegalOneOffice).all():
            ext = getattr(office, "external_id", None)
            if ext is None or int(ext) in varridos or int(ext) == ESCRITORIO_RAIZ_ID:
                continue
            n = _contar_pastas_ativas(client, int(ext))
            if n:
                resultado["fora"].append({
                    "office_id": int(ext),
                    "path": getattr(office, "path", None) or getattr(office, "name", ""),
                    "pastas": n,
                })
        resultado["fora"].sort(key=lambda x: -x["pastas"])
        resultado["total_fora"] = sum(x["pastas"] for x in resultado["fora"])
    except Exception:  # noqa: BLE001
        logger.exception("Cobertura: verificação falhou (ignorada).")
    return resultado


def alertar_se_houver_buraco(
    db: Any, client: Any, office_ids_varridos: Sequence[int]
) -> dict[str, Any]:
    """Verifica e, se houver pasta no escritório raiz, manda o alerta.

    Só o raiz dispara e-mail. Os outros escritórios fora da lista viajam junto
    como contexto — se virassem gatilho, o alerta tocaria todo dia por uma
    decisão que já foi tomada, e alerta que toca todo dia ninguém lê.
    """
    dados = verificar_cobertura(db, client, office_ids_varridos)
    if not dados.get("raiz"):
        logger.info("Cobertura da captura: nenhuma pasta no escritório raiz.")
        return dados
    try:
        from app.services.publication_capture_alerts import alertar_cobertura_furada

        alertar_cobertura_furada(
            pastas_na_raiz=dados["raiz"],
            escritorios_fora=dados["fora"],
            office_ids_varridos=list(office_ids_varridos),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Cobertura: falha ao disparar o alerta (ignorado).")
    return dados
