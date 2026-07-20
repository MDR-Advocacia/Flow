"""Registro dos times (setores/supervisões) do Minha Equipe.

Cada time é um slug + rótulo. A planilha de squads tem uma aba por subgrupo, e
algumas abas se juntam num setor (ex.: BB Réu = BB Defesa + BB Réu + Recursos);
o mapa aba→setor fica no seed (`TAB_TO_SETOR`). Aqui só a lista canônica usada
pelo gate de permissão, pelos endpoints e pelo menu.
"""

TEAMS = [
    {"key": "bb-reu", "label": "BB Réu"},
    {"key": "bb-execucao", "label": "BB Execução & Encerramento"},
    {"key": "bb-acordos", "label": "BB Acordos"},
    {"key": "bb-estrategico", "label": "BB Estratégico"},
    {"key": "bb-cadastro", "label": "BB Cadastro"},
    {"key": "master-reu", "label": "Master Réu"},
    {"key": "ativos-reu", "label": "Ativos Réu"},
    {"key": "trabalhista", "label": "Trabalhista"},
    # Autor (Recuperação de Crédito) — 2026-06-29
    {"key": "bb-autor-processual", "label": "BB Autor — Processual"},
    {"key": "ativos-autor", "label": "Ativos Autor"},
    {"key": "autor-recursal", "label": "Autor — Recursal"},
    {"key": "ajuizamento", "label": "Ajuizamento"},
    {"key": "estrategico-autor", "label": "Estratégico Autor"},
    # Especializada — atende Réu e Autor da MESMA parte (processos vinculados),
    # por isso não entra em nenhum dos dois grupos. 2026-07-20.
    {"key": "equipe-mista", "label": "Equipe Mista"},
]

TEAM_KEYS = {t["key"] for t in TEAMS}
_LABELS = {t["key"]: t["label"] for t in TEAMS}


def team_label(key: str) -> str:
    return _LABELS.get(key, key)


def is_valid_team(key: str) -> bool:
    return key in TEAM_KEYS
