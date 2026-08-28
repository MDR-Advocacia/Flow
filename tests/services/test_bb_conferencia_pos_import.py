"""O import diz o que foi ENVIADO; a conferência diz o que o L1 CRIOU.

Incidente (24/08/2026): planilha 119 com 51 processos e XLSX limpo (51 linhas
distintas) virou 102 pastas no L1 — cada CNJ com duas pastas gêmeas criadas no
mesmo segundo. O import reportou sucesso e ninguém foi avisado; o estrago só
apareceu dias depois pela agenda duplicada.

A conferência precisa acusar isso SEM alarme falso em dois casos legítimos:
pasta pré-existente de meses atrás, e o mesmo CNJ cadastrado de propósito em
escritórios diferentes (processo de outro cliente, via `cnjs_liberados`).
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.models.distribuidos_bb import (
    CLIENTE_BB,
    POOL_PENDENTE_CADASTRO,
    PROC_DISTRIBUIDO,
    BbPlanilha,
    BbProcesso,
)
from app.services.distribuidos_bb.cadastro_conferencia import conferir_duplicacao

AGORA = datetime.now(timezone.utc)
CNJ_A = "3006795-03.2026.8.06.0297"
CNJ_B = "0856280-67.2026.8.15.2001"


class _L1Fake:
    """Devolve as pastas que o L1 'tem' pros CNJs pedidos."""

    def __init__(self, pastas):
        self._pastas = pastas

    @staticmethod
    def _escape_odata_literal(v):
        return str(v).replace("'", "''")

    def _paginated_catalog_loader(self, endpoint, params):
        # O L1 devolve o MESMO registro em /Lawsuits e /Litigations. Sem
        # deduplicar por `id`, a conferência acusa duplicata falsa em 100% dos
        # casos — foi o alarme falso do tombamento de 26/08/2026.
        return list(self._pastas)


def _planilha_com(db, cnjs):
    pl = BbPlanilha(
        nome_arquivo="TESTE.xlsx", conteudo=b"x", total_processos=len(cnjs),
        tamanho_bytes=1, created_at=AGORA - timedelta(minutes=5),
    )
    db.add(pl)
    db.flush()
    for i, cnj in enumerate(cnjs):
        db.add(BbProcesso(
            fingerprint=f"cnj:{cnj}", cliente=CLIENTE_BB, cnj=cnj,
            status=PROC_DISTRIBUIDO, planilha_status=POOL_PENDENTE_CADASTRO,
            planilha_id=pl.id,
        ))
    db.flush()
    return pl


def _pasta(cnj, folder, lid, office=23, quando=None):
    return {
        "id": lid, "identifierNumber": cnj, "folder": folder,
        "responsibleOfficeId": office,
        "creationDate": (quando or AGORA).isoformat(),
    }


def test_acusa_as_pastas_gemeas(db_session):
    """O caso real: duas pastas, mesmo CNJ, mesmo escritório, agora."""
    pl = _planilha_com(db_session, [CNJ_A])
    l1 = _L1Fake([
        _pasta(CNJ_A, "Proc - 0077686", 83533),
        _pasta(CNJ_A, "Proc - 0077687", 83534),
    ])
    r = conferir_duplicacao(db_session, pl, client=l1)

    assert r["duplicados"] == 1
    assert r["pastas_extras"] == 1
    d = r["detalhe"][0]
    assert d["manter"] == "Proc - 0077686"
    assert [e["folder"] for e in d["extras"]] == ["Proc - 0077687"]

    ev = db_session.execute(text(
        "SELECT nivel FROM bbd_eventos WHERE acao = 'Pasta duplicada no L1'"
    )).scalar()
    assert ev == "ERRO", "duplicação tem que gritar, não sussurrar"


def test_pasta_preexistente_no_mesmo_escritorio_E_duplicata(db_session):
    """Duas pastas do mesmo CNJ no mesmo escritório = duplicata, SEJA LÁ QUANDO
    a primeira nasceu.

    A regra antiga filtrava por janela de data ("pasta de meses atrás não foi
    este import") e foi exatamente o que escondeu as 298 pastas em dobro de
    27/08/2026: a primeira pasta tinha minutos, a segunda nascia fora da janela
    visível, e a conferência aprovava. Se existia pasta no mesmo escritório, a
    trava pré-planilha teria VINCULADO e o CNJ nem entraria no lote — se ainda
    assim há duas, alguém duplicou, e a mais ANTIGA é a que fica.
    """
    pl = _planilha_com(db_session, [CNJ_A])
    l1 = _L1Fake([
        _pasta(CNJ_A, "Proc - 0077686", 83533),
        _pasta(CNJ_A, "Proc - 0047656", 47656, quando=AGORA - timedelta(days=200)),
    ])
    r = conferir_duplicacao(db_session, pl, client=l1)
    assert r["duplicados"] == 1
    assert r["detalhe"][0]["manter"] == "Proc - 0047656", "a mais antiga fica"


def test_nao_acusa_mesmo_cnj_em_escritorios_diferentes(db_session):
    """`cnjs_liberados` cadastra o mesmo CNJ pra OUTRO cliente de propósito."""
    pl = _planilha_com(db_session, [CNJ_A])
    l1 = _L1Fake([
        _pasta(CNJ_A, "Proc - 0074593", 74593, office=23),   # BB
        _pasta(CNJ_A, "Proc - 0074664", 74664, office=27),   # Ativos
    ])
    r = conferir_duplicacao(db_session, pl, client=l1)
    assert r["duplicados"] == 0, "escritórios diferentes é cadastro legítimo"


def test_tudo_certo_registra_conferencia_ok(db_session):
    pl = _planilha_com(db_session, [CNJ_A, CNJ_B])
    l1 = _L1Fake([
        _pasta(CNJ_A, "Proc - 0077686", 83533),
        _pasta(CNJ_B, "Proc - 0077680", 83527),
    ])
    r = conferir_duplicacao(db_session, pl, client=l1)
    assert r["duplicados"] == 0
    assert r["conferidos"] == 2
    ev = db_session.execute(text(
        "SELECT nivel FROM bbd_eventos WHERE acao = 'Conferência pós-import'"
    )).scalar()
    assert ev == "INFO"


def test_falha_do_l1_nao_derruba_o_cadastro(db_session):
    """A conferência é best-effort: cadastro que deu certo não pode ser desfeito."""
    class _Quebrado:
        @staticmethod
        def _escape_odata_literal(v):
            return v

        def _paginated_catalog_loader(self, *a, **k):
            raise RuntimeError("L1 fora do ar")

    pl = _planilha_com(db_session, [CNJ_A])
    r = conferir_duplicacao(db_session, pl, client=_Quebrado())
    # Não acusa duplicação (não tem como saber) e se declara PARCIAL —
    # silêncio aqui seria pior que o erro.
    assert r["duplicados"] == 0
    assert r.get("parcial") is True


def test_data_do_l1_com_7_casas_decimais(db_session):
    """O L1 manda 7 casas de fração; o Python 3.10 do container só aceita 6.

    A primeira versão deste módulo engolia o ValueError com um `continue` e
    ficava CEGA justamente pro caso que veio investigar — as gêmeas da 119
    tinham `creationDate` com 7 casas.
    """
    from app.services.distribuidos_bb.cadastro_conferencia import _parse_data

    dt = _parse_data("2026-08-24T09:37:11.6718563-03:00")
    assert dt is not None, "não parseou a data real que o L1 devolve"
    assert dt.year == 2026 and dt.hour == 9 and dt.minute == 37
    assert dt.tzinfo is not None

    pl = _planilha_com(db_session, [CNJ_A])
    l1 = _L1Fake([
        {"id": 83533, "identifierNumber": CNJ_A, "folder": "Proc - 0077686",
         "responsibleOfficeId": 27,
         "creationDate": "2026-08-24T09:37:11.6718563-03:00"},
        {"id": 83534, "identifierNumber": CNJ_A, "folder": "Proc - 0077687",
         "responsibleOfficeId": 27,
         "creationDate": "2026-08-24T09:37:12.557457-03:00"},
    ])
    pl.created_at = datetime(2026, 8, 24, 9, 33, 21, tzinfo=timezone(timedelta(hours=-3)))
    db_session.flush()
    r = conferir_duplicacao(db_session, pl, client=l1)
    assert r["duplicados"] == 1, "voltou a ficar cega pras gêmeas reais"


def test_data_ilegivel_nao_e_descartada(db_session):
    """Data que não dá pra ler vira suspeita, não descarte silencioso."""
    pl = _planilha_com(db_session, [CNJ_A])
    l1 = _L1Fake([
        _pasta(CNJ_A, "Proc - 0077686", 83533),
        {"id": 83534, "identifierNumber": CNJ_A, "folder": "Proc - 0077687",
         "responsibleOfficeId": 23, "creationDate": "data-quebrada"},
    ])
    r = conferir_duplicacao(db_session, pl, client=l1)
    assert r["duplicados"] == 1, "pasta com data ilegível sumiu da conferência"


def test_nao_confunde_os_dois_endpoints_com_duplicata(db_session):
    """A MESMA pasta vista em /Lawsuits e /Litigations não é duplicata.

    Os dois endpoints do L1 devolvem o mesmo registro. Somar as duas listas sem
    deduplicar por `id` faz toda pasta contar duas vezes — e a conferência
    acusaria duplicação em todo lote, sempre.
    """
    pl = _planilha_com(db_session, [CNJ_A, CNJ_B])
    l1 = _L1Fake([
        _pasta(CNJ_A, "Proc - 0077686", 83533),
        _pasta(CNJ_B, "Proc - 0077680", 83527),
    ])
    r = conferir_duplicacao(db_session, pl, client=l1)
    assert r["duplicados"] == 0, (
        "contou a mesma pasta duas vezes (uma por endpoint)"
    )
    assert r.get("com_pasta") == 2


def test_falha_de_um_endpoint_marca_resultado_parcial(db_session):
    """Resultado incompleto tem que se declarar, não passar por 'sem duplicação'.

    A versão anterior abortava no meio (328 de 500 CNJs) e devolvia zero
    duplicados — um 'está tudo bem' que não valia nada.
    """
    pl = _planilha_com(db_session, [CNJ_A])

    class _MeioQuebrado:
        chamadas = 0

        @staticmethod
        def _escape_odata_literal(v):
            return v

        def _paginated_catalog_loader(self, endpoint, params):
            self.chamadas += 1
            if endpoint == "/Litigations":
                raise RuntimeError("429 sem folego")
            return [_pasta(CNJ_A, "Proc - 0077686", 83533)]

    r = conferir_duplicacao(db_session, pl, client=_MeioQuebrado())
    assert r.get("parcial") is True, "falha de endpoint passou como resultado completo"


def test_import_que_nao_criou_pasta_nenhuma_grita(db_session):
    """Import que envia e não cria NADA não pode passar como sucesso.

    Caso real 27/08/2026: a restauração de 25 pastas do Ativos foi dada como
    concluída ("25 processo(s) checado(s) no Legal One, uma pasta cada") e
    NENHUMA existia — o número era quantos CNJs foram checados, não quantos
    tinham pasta. O job do L1 tinha morrido depois de aceitar o envio.
    """
    pl = _planilha_com(db_session, [CNJ_A, CNJ_B])
    l1 = _L1Fake([])                       # o L1 nao criou nada

    r = conferir_duplicacao(db_session, pl, client=l1)

    assert r["conferidos"] == 2
    assert r["com_pasta"] == 0
    assert r["sem_pasta"] == 2
    assert r["duplicados"] == 0

    ev = db_session.execute(text(
        "SELECT nivel FROM bbd_eventos ORDER BY id DESC LIMIT 1"
    )).scalar()
    assert ev == "ERRO", "sem pasta nenhuma tem que gritar, nao virar 'tudo certo'"


def test_pasta_faltando_so_em_alguns_tambem_avisa(db_session):
    """Meio termo é o mais perigoso: parte entra, parte some, e o total engana."""
    pl = _planilha_com(db_session, [CNJ_A, CNJ_B])
    l1 = _L1Fake([_pasta(CNJ_A, "Proc - 0077686", 83533)])

    r = conferir_duplicacao(db_session, pl, client=l1)

    assert (r["com_pasta"], r["sem_pasta"]) == (1, 1)
    ev = db_session.execute(text(
        "SELECT nivel FROM bbd_eventos ORDER BY id DESC LIMIT 1"
    )).scalar()
    assert ev == "ERRO"
