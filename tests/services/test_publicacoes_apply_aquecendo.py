"""O apply automático não pode morrer na fase de aquecimento do cache.

Incidente (22/08/2026): com o cache em duas fases (pub011) o lote NASCE em
AQUECENDO e só ganha `anthropic_batch_id` quando o aquecimento fecha. O laço de
polling da automação chamava `refresh_batch_status` de cara, que levanta
"Batch N sem anthropic_batch_id", e a etapa `classify` inteira morria: as
publicações voltavam classificadas da Anthropic e ficavam esperando alguém
clicar em aplicar.

A correlação em produção era perfeita — todo lote que aqueceu (150/151/152)
precisou de apply manual; todo lote que não aqueceu (143, 145–149) aplicou
sozinho em segundos.
"""
import pytest

from app.services.publication_batch_classifier import PublicationBatchClassifier


class _BatchFake:
    """Lote que nasce AQUECENDO e ganha o id só depois de promovido."""

    def __init__(self, promove_na_tentativa: int):
        self.id = 152
        self.anthropic_batch_id = None
        self.anthropic_status = None
        self.succeeded_count = 0
        self.errored_count = 0
        self._tentativas = 0
        self._promove_em = promove_na_tentativa

    def promover(self):
        self._tentativas += 1
        if self._tentativas >= self._promove_em:
            self.anthropic_batch_id = "msgbatch_real_123"


def test_refresh_ainda_levanta_sem_id():
    """A pré-condição continua valendo — o conserto é no CHAMADOR, não aqui."""
    svc = PublicationBatchClassifier.__new__(PublicationBatchClassifier)
    b = _BatchFake(promove_na_tentativa=1)
    with pytest.raises(ValueError, match="sem anthropic_batch_id"):
        import asyncio

        asyncio.run(svc.refresh_batch_status(b))


def test_laco_espera_a_promocao_em_vez_de_estourar():
    """Reproduz o laço corrigido: enquanto não há id, promove e espera."""
    b = _BatchFake(promove_na_tentativa=3)
    voltas = 0
    aplicou = False

    for _ in range(10):  # no lugar do deadline
        voltas += 1
        if not b.anthropic_batch_id:
            b.promover()          # promover_aquecidos()
            if not b.anthropic_batch_id:
                continue          # dorme e tenta de novo
        # daqui pra frente é o polling normal
        b.anthropic_status = "ended"
        aplicou = True
        break

    assert aplicou, "o laço desistiu antes de o lote ser promovido"
    assert b.anthropic_batch_id == "msgbatch_real_123"
    assert voltas == 3, f"esperava 3 voltas até promover, foram {voltas}"


def test_o_codigo_real_trata_a_fase_de_aquecimento():
    """Guarda contra alguém remover o tratamento e o bug voltar em silêncio."""
    import inspect

    from app.services.scheduled_automation_service import ScheduledAutomationService

    src = inspect.getsource(ScheduledAutomationService)
    assert "promover_aquecidos" in src, "o laço não promove o lote aquecido"
    assert "if not batch.anthropic_batch_id:" in src, "o laço não checa o id antes de pollar"
    assert "45 * 60" in src, "o teto precisa cobrir aquecimento + lote real"
