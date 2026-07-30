"""
Testes da contingência que manda o L1 gerar o relatório de publicações.

O que precisa estar garantido:
  - a janela é sempre D-1 → D0 (foi a regra combinada com a operação);
  - as duas datas entram no corpo do POST sem estragar os outros 915 campos;
  - o polling respeita o contrato de status (7/8 trabalhando, 1 pronto);
  - relatório que volta sem a coluna `Id` NÃO importa nada — é o guarda contra
    alguém editar o modelo 789 no Legal One.
"""
import datetime
import io
from urllib.parse import unquote_plus

import openpyxl
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.legal_one import LegalOneOffice
from app.services import publication_l1_report_fallback as fb


PATH_ESCRITORIO = "MDR Advocacia / Área operacional / Ativos / Trabalhista"


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    s.add(LegalOneOffice(external_id=61, name="Trabalhista", path=PATH_ESCRITORIO))
    s.commit()
    yield s
    s.close()


class _Resp:
    def __init__(self, *, text="", payload=None, content=b""):
        self.text = text
        self._payload = payload
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        if self._payload is None:
            raise ValueError("sem json")
        return self._payload


class _FakeSession:
    """Sessão de mentira que encena a geração do relatório."""

    def __init__(self, *, status_seq=None, planilha=b"", id_novo=13430, ids_antes=(13200,)):
        self.status_seq = list(status_seq or [7, 8, 1])
        self.planilha = planilha
        self.id_novo = id_novo
        self.ids_antes = list(ids_antes)
        self.postou_form = None
        self.gets = []
        self._disparou = False

    def get(self, url, **kw):
        self.gets.append(url)
        if "ReportProcessos/Search" in url:
            ids = self.ids_antes + ([self.id_novo] if self._disparou else [])
            html = "".join(f'<a href="/shared/ReportShared/GetFile/{i}">x</a>' for i in ids)
            return _Resp(text=html)
        if "GenericReport" in url:
            return _Resp(text="<form></form>")
        if "GetFile" in url:
            return _Resp(content=self.planilha)
        return _Resp()

    def post(self, url, **kw):
        if "GenericReport" in url:
            self.postou_form = kw.get("data")
            self._disparou = True
            return _Resp(text="ok")
        if "DocumentIsLoaded" in url:
            st = self.status_seq.pop(0) if self.status_seq else 1
            return _Resp(payload=[{"Id": self.id_novo, "Status": st, "ErrorMessage": None}])
        return _Resp()


def _planilha(com_id=True):
    cab = ["Escritório responsável", "Nº do processo", "Pasta",
           "Andamentos / Data/hora", "Andamentos / Descrição", "Andamentos / Tipo",
           "Data do cadastro"]
    linha = [PATH_ESCRITORIO, "0000967-85.2024.5.05.0019", "Proc - 0003902",
             "2026-07-30", "PODER JUDICIARIO ...", "Publicação", "2025-03-10"]
    if com_id:
        cab.append("Id")
        linha.append(3903)
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(cab); ws.append(linha)
    buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()


# ── Janela ─────────────────────────────────────────────────────────────

def test_janela_e_sempre_de_ontem_ate_hoje():
    inicio, fim = fb.janela_padrao()
    d0 = datetime.datetime.strptime(fim, "%d/%m/%Y").date()
    d1 = datetime.datetime.strptime(inicio, "%d/%m/%Y").date()
    assert (d0 - d1).days == 1


def test_janela_aceita_mais_dias_atras():
    inicio, fim = fb.janela_padrao(dias_atras=3)
    d0 = datetime.datetime.strptime(fim, "%d/%m/%Y").date()
    d1 = datetime.datetime.strptime(inicio, "%d/%m/%Y").date()
    assert (d0 - d1).days == 3


# ── Corpo do formulário ────────────────────────────────────────────────

def test_corpo_do_formulario_troca_so_as_datas():
    corpo = fb.corpo_do_formulario("29/07/2026", "30/07/2026")
    pares = [p.split("=", 1) for p in corpo.split("&") if "=" in p]
    achados = {
        unquote_plus(k): unquote_plus(v)
        for k, v in pares
        if unquote_plus(k) in ("Andamento.DataCadastroInicio", "Andamento.DataCadastroFinal")
    }
    assert achados == {
        "Andamento.DataCadastroInicio": "29/07/2026",
        "Andamento.DataCadastroFinal": "30/07/2026",
    }
    # Nenhum placeholder pode sobrar, e o formulário tem que continuar inteiro.
    assert "__DATA_" not in corpo
    assert len(pares) > 900


def test_corpo_preserva_os_filtros_e_a_coluna_id():
    """Os filtros e as Columns são o que faz o relatório vir certo."""
    corpo = unquote_plus(fb.corpo_do_formulario("29/07/2026", "30/07/2026"))
    assert "Andamento.TiposAndamento[0].Id" in corpo      # tipo Publicação
    assert "Andamento.TipoOrigem[0].Id" in corpo          # diários oficiais
    assert "Andamento.PublicationStatus[0].Id" in corpo   # pendente
    assert "Columns[" in corpo
    assert "Id=789" in corpo.replace(" ", "")


# ── Polling ────────────────────────────────────────────────────────────

def test_polling_espera_status_de_trabalho_e_aceita_o_pronto(monkeypatch):
    monkeypatch.setattr(fb.time, "sleep", lambda *_: None)
    s = _FakeSession(status_seq=[7, 7, 8, 1])
    r = fb.aguardar_ficar_pronto(s, "http://l1", 13430, timeout=60)
    assert r["ok"] is True and r["status"] == fb.STATUS_PRONTO


def test_polling_desiste_no_timeout(monkeypatch):
    monkeypatch.setattr(fb.time, "sleep", lambda *_: None)
    tempos = iter([0] + [i * 30 for i in range(1, 40)])
    monkeypatch.setattr(fb.time, "monotonic", lambda: next(tempos))
    s = _FakeSession(status_seq=[8] * 50)
    r = fb.aguardar_ficar_pronto(s, "http://l1", 13430, timeout=60)
    assert r["ok"] is False and r["motivo"] == "timeout_geracao"


# ── Disparo ────────────────────────────────────────────────────────────

def test_disparo_identifica_o_relatorio_recem_criado(monkeypatch):
    monkeypatch.setattr(fb.time, "sleep", lambda *_: None)
    s = _FakeSession(id_novo=13430, ids_antes=(13200, 13300))
    rid = fb.disparar(s, "http://l1", "29/07/2026", "30/07/2026")
    assert rid == 13430
    assert "Andamento.DataCadastroInicio" in unquote_plus(s.postou_form)


def test_disparo_sem_id_novo_devolve_none(monkeypatch):
    monkeypatch.setattr(fb.time, "sleep", lambda *_: None)
    s = _FakeSession(ids_antes=(13200,))
    s._disparou = True  # ja "existia": nenhum id novo aparece
    s.id_novo = 13200
    assert fb.disparar(s, "http://l1", "29/07/2026", "30/07/2026") is None


# ── Ponta a ponta ──────────────────────────────────────────────────────

def test_captura_devolve_publicacoes_no_contrato_do_l1(db, monkeypatch):
    monkeypatch.setattr(fb.time, "sleep", lambda *_: None)
    monkeypatch.setattr(fb, "_session", lambda: _FakeSession(planilha=_planilha()))
    monkeypatch.setattr(
        "app.services.prazos_iniciais.legacy_task_helpers.web_base_url",
        lambda: "http://l1",
    )
    r = fb.capturar_publicacoes(db)
    assert r["ok"] is True
    assert r["total"] == 1
    pub = r["publicacoes"][0]
    assert pub["relationships"] == [{"linkType": "Litigation", "linkId": 3903}]
    assert pub["date"] == "2026-07-30T00:00:00Z"
    assert pub["id"] < 0


def test_relatorio_sem_coluna_id_nao_importa_nada(db, monkeypatch):
    """Guarda contra alguém editar o modelo 789 e tirar a coluna Id.

    Melhor não capturar do que capturar na pasta errada.
    """
    monkeypatch.setattr(fb.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        fb, "_session", lambda: _FakeSession(planilha=_planilha(com_id=False))
    )
    monkeypatch.setattr(
        "app.services.prazos_iniciais.legacy_task_helpers.web_base_url",
        lambda: "http://l1",
    )
    r = fb.capturar_publicacoes(db)
    assert r["ok"] is False
    assert r["motivo"] == "planilha_invalida"
    assert "Id" in r["detalhe"]
