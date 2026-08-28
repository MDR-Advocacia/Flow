"""O import se recusa a empilhar em cima de uma fila de revisão entupida.

Tombamento do Master, 26 a 28/08/2026: cada planilha respeitava o limite de 500
por arquivo, mas os lotes eram emendados um no outro SEM esperar a fila drenar,
e lote que falhava deixava as linhas lá. A fila somou 1.216 → 4.255 → 5.826 →
8.882 → 12.094; o processador do L1 passou a estourar por tempo (4.176 linhas
com "O processo de importação expirou!") e o cadastro do fluxo diário parou
junto — 86 processos sem pasta e dois dias de tenant lento.

O limite útil nunca foi o tamanho do arquivo: é o quanto a fila aguenta digerir.
"""
import pytest

from app.services.distribuidos_bb import import_l1_service as svc


# `_headers` exige o formato real do token do gateway.
TOK = {"token": "t", "subscriptionKey": "k", "tenancy": "x", "distribution": "y"}


def _status(fila):
    return lambda sess, h: {"revisingLitigationsCount": fila}


def test_recusa_upload_quando_a_fila_esta_acima_do_teto(monkeypatch):
    monkeypatch.setattr(svc, "_import_status", _status(12094))
    monkeypatch.setattr(svc, "_FILA_REVISAO_MAX", 1000)

    with pytest.raises(svc.ImportL1Error) as exc:
        svc._cadastrar_once(b"x", "p.xlsx", firm_id=1, dry_run=False,
                            poll_max_s=10, tok=TOK)

    msg = str(exc.value)
    assert "12094" in msg and "1000" in msg
    assert "revis" in msg.lower(), "a mensagem tem que dizer o que fazer"


def test_deixa_passar_quando_a_fila_esta_curta(monkeypatch):
    """Fila normal não pode virar impedimento — o guard é contra excesso."""
    monkeypatch.setattr(svc, "_import_status", _status(12))
    monkeypatch.setattr(svc, "_FILA_REVISAO_MAX", 1000)
    monkeypatch.setattr(svc, "_listar_staging", lambda *a, **k: [])
    chamou = {"sas": False}

    def _sas(*a, **k):
        chamou["sas"] = True
        raise RuntimeError("parou aqui de propósito")

    monkeypatch.setattr(svc, "_get_sas", _sas)
    with pytest.raises(RuntimeError):
        svc._cadastrar_once(b"x", "p.xlsx", firm_id=1, dry_run=False,
                            poll_max_s=10, tok=TOK)
    assert chamou["sas"], "com a fila curta tem que seguir pro upload"


def test_fila_ilegivel_nao_bloqueia(monkeypatch):
    """Não medir não é o mesmo que estar cheia: leitura falha não trava cadastro."""
    def _explode(sess, h):
        raise RuntimeError("gateway fora")

    monkeypatch.setattr(svc, "_import_status", _explode)
    monkeypatch.setattr(svc, "_listar_staging", lambda *a, **k: [])
    monkeypatch.setattr(svc, "_get_sas",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("segue")))
    with pytest.raises(RuntimeError, match="segue"):
        svc._cadastrar_once(b"x", "p.xlsx", firm_id=1, dry_run=False,
                            poll_max_s=10, tok=TOK)
