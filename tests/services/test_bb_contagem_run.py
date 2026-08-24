"""O painel do Cadastro tem que contar o que aconteceu de verdade.

Dois enganos que fizeram o operador achar que tinha perdido processo (24/08/2026):

1. A frase do "Pool atualizado" chamava TODAS as distribuídas de "novas" e logo
   depois mostrava um pool menor. No run 180 leu 57 e disse "57 novos", mas 29
   eram releitura de pendência que o run 178 deixou aberta de propósito (modo
   seguro) e que já estavam cadastradas — novas mesmo eram 28.

2. O run 178 exibia "0 cadastrados" com 29 pastas criadas no L1: o auto-cadastro
   dele estourou e quem importou foi o retry de planilha órfã, 12 min depois,
   fora do contexto do run — ninguém creditava de volta.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.models.distribuidos_bb import (
    CLIENTE_BB,
    POOL_PENDENTE_CADASTRO,
    PROC_DISTRIBUIDO,
    BbPlanilha,
    BbProcesso,
    BbRun,
)


def test_retry_credita_o_cadastro_de_volta_ao_run(db_session, monkeypatch):
    """Retry que salva o import tem que somar no total_cadastrados do run."""
    from app.services.distribuidos_bb import cadastro_monitor_worker as worker

    agora = datetime.now(timezone.utc)
    run = BbRun(status="CONCLUIDO", total_distribuidos=29, total_cadastrados=0,
                iniciado_em=agora - timedelta(hours=1))
    db_session.add(run)
    db_session.flush()

    pl = BbPlanilha(
        run_id=run.id, nome_arquivo="PLANILHA_TESTE.xlsx", conteudo=b"xlsx",
        total_processos=29, tamanho_bytes=4, subido_legalone=False,
        created_at=agora - timedelta(minutes=30),
    )
    db_session.add(pl)
    db_session.flush()

    # O retry só age sobre planilha COM processos vinculados e todos ainda
    # pendentes (all-or-nothing) — sem isso ele pula a planilha em silêncio.
    for i in range(29):
        db_session.add(BbProcesso(
            fingerprint=f"cnj:teste-{i}", cliente=CLIENTE_BB,
            status=PROC_DISTRIBUIDO, planilha_status=POOL_PENDENTE_CADASTRO,
            planilha_id=pl.id, run_id=run.id,
        ))
    db_session.flush()

    # o import volta OK na retentativa
    monkeypatch.setattr(
        "app.services.distribuidos_bb.import_l1_service.cadastrar_planilha",
        lambda *a, **k: {"novos": 29, "descartadas": []},
    )
    monkeypatch.setattr(
        "app.services.distribuidos_bb.planilha_service.cnjs_liberados_da_planilha",
        lambda *a, **k: set(),
    )
    monkeypatch.setattr(
        "app.services.distribuidos_bb.cadastro_descartes.registrar_descartes",
        lambda *a, **k: None,
    )

    worker.retentar_planilhas_orfas(db_session)

    db_session.refresh(run)
    assert run.total_cadastrados == 29, (
        f"o run deveria ter sido creditado com 29, ficou com {run.total_cadastrados}"
    )
    assert pl.subido_legalone is True

    # e o evento tem que aparecer na linha do tempo DO RUN, não solto
    evento = db_session.execute(text(
        "SELECT run_id FROM bbd_eventos WHERE acao = 'Retry do auto-cadastro OK'"
    )).scalar()
    assert evento == run.id, "o evento do retry ficou sem o run de origem"


def test_planilha_do_auto_cadastro_guarda_o_run():
    """A coluna run_id existia e nunca era preenchida — é ela que liga os dois."""
    import inspect

    from app.services.distribuidos_bb import coleta_service

    src = inspect.getsource(coleta_service._auto_cadastrar)
    assert "planilha.run_id = run.id" in src, "a planilha voltou a nascer sem run"


def test_mensagem_do_pool_separa_novo_de_releitura():
    """Guarda contra a frase enganosa voltar."""
    import inspect

    from app.services.distribuidos_bb import coleta_service

    src = inspect.getsource(coleta_service.executar_coleta)
    assert "Reatualizado" in src, "a mensagem não distingue releitura de novo"
    assert "relidos_execucao" in src, "o evento não registra quantos foram releitura"
