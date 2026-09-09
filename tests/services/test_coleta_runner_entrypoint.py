# -*- coding: utf-8 -*-
"""O processo filho da coleta BB sobe — o `main()` não pode morrer no logging.

Em 09/09/2026 a coleta das 03:00 (run 241) morreu no PRIMEIRO SEGUNDO com
`TypeError: format requires a mapping` e rc=1. A causa era o próprio formato
do log do filho:

    format="%(asctime)s ... [coleta-filho run=%s] %%(message)s" % args.run_id

`%(asctime)s` e `%(levelname)s` são placeholders de MAPPING do logging;
interpolar a string inteira com um inteiro faz o Python tentar resolver esses
nomes num int e explodir. O supervisor fez o certo (detectou rc=1, fechou o run
com a verdade e mandou e-mail) — mas nenhuma notificação do BB foi coletada.

O teste chama `main()` de verdade, com o miolo da coleta trocado por um dublê:
se o entrypoint voltar a quebrar antes de chamar a coleta, ele falha aqui e
não às 3 da manhã.
"""
import logging

import pytest

from app.services.distribuidos_bb import coleta_runner


@pytest.fixture
def coleta_falsa(monkeypatch):
    chamadas = []
    monkeypatch.setattr(
        "app.services.distribuidos_bb.coleta_service.executar_coleta_background",
        lambda run_id, **kw: chamadas.append((run_id, kw)),
    )
    return chamadas


def test_main_sobe_e_chama_a_coleta(coleta_falsa):
    rc = coleta_runner.main(["--run-id", "241", "--data-inicial", "06/09/2026",
                             "--data-final", "09/09/2026"])

    assert rc == 0
    assert coleta_falsa == [(241, {"data_inicial": "06/09/2026",
                                   "data_final": "09/09/2026",
                                   "coletar_envolvidos": True})]


def test_sem_envolvidos_desliga_a_captura_da_capa(coleta_falsa):
    coleta_runner.main(["--run-id", "7", "--sem-envolvidos"])

    assert coleta_falsa[0][1]["coletar_envolvidos"] is False


def test_o_formato_do_log_e_valido_de_verdade(coleta_falsa, caplog):
    """A regressão exata: o formato tem que FORMATAR, não só existir.

    `basicConfig` só configura o root uma vez por processo, então aqui o
    formato é exercitado direto no Formatter — que é onde o TypeError nascia.
    """
    coleta_runner.main(["--run-id", "241"])

    fmt = logging.Formatter(
        "%(asctime)s - %(levelname)s - [coleta-filho run=241] %(message)s"
    )
    saida = fmt.format(logging.LogRecord("x", logging.INFO, __file__, 1, "oi", None, None))
    assert "[coleta-filho run=241] oi" in saida
