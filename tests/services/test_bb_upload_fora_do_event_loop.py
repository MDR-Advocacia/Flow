# -*- coding: utf-8 -*-
"""Upload do Master e da Ativos lê a planilha fora do event loop.

Os endpoints são `async` por causa do UploadFile, e a leitura da planilha
(openpyxl) mais a criação do lote são síncronas. Rodando direto no loop, uma
Listagem grande segurava todas as outras requisições daquele worker enquanto
lia. A ingestão em si já era numa thread à parte.

Investigado no incidente de 15/09/2026. Não foi a causa (a POST respondeu às
08:48:08 e os workers só foram mortos às 08:49:38), mas era um risco real no
mesmo caminho.
"""
import asyncio
from types import SimpleNamespace

import pytest

from app.api.v1.endpoints import distribuidos_bb as ep
from app.services.distribuidos_bb import ativos_service, master_service

ADMIN = SimpleNamespace(id=1, role="admin")


class _Upload:
    filename = "Listagem_de_Prazos (74).xlsx"

    async def read(self):
        return b"conteudo"


def _fora_do_loop():
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return True
    return False


@pytest.mark.parametrize(
    "endpoint, modulo",
    [(ep.importar_master, master_service), (ep.importar_ativos, ativos_service)],
)
def test_leitura_da_planilha_nao_roda_no_event_loop(monkeypatch, endpoint, modulo):
    chamadas = []

    def disparar(db, *, conteudo, nome_arquivo, user_id):
        chamadas.append((_fora_do_loop(), conteudo, nome_arquivo, user_id))
        return {"lote_id": 40, "total": 23}

    monkeypatch.setattr(modulo, "disparar_ingestao", disparar)

    res = asyncio.run(endpoint(arquivo=_Upload(), db=None, current_user=ADMIN))

    assert res == {"lote_id": 40, "total": 23}
    assert chamadas == [(True, b"conteudo", "Listagem_de_Prazos (74).xlsx", 1)]
