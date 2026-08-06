"""Relatório de utilização do sistema — para o administrativo cobrar adesão.

Duas fontes, deliberadamente:

1. NAVEGAÇÃO (`flow_uso_diario`) — com que frequência a pessoa entra e em que
   módulos trabalha. Só existe a partir do deploy da captura; não há como
   reconstruir o passado, porque nada era registrado antes.

2. AÇÃO EFETIVA — colhida dos rastros de autoria que cada módulo já gravava
   muito antes deste relatório existir. É a parte RETROATIVA: dá pra olhar
   meses atrás.

Por que as duas: supervisor trabalha lendo. Se o relatório contasse só ação,
quem entra todo dia, confere a carga da equipe e não clica em nada apareceria
igual a quem sumiu — e o administrativo cobraria a pessoa errada. Se contasse
só navegação, não distinguiria quem opera de quem só passa o olho.

SUPERVISOR: quem tem Minha Equipe liberado (`can_use_minha_equipe`), com as
equipes em `minha_equipe_equipes`. O cargo não serve pra isso hoje — só uma
pessoa está marcada como Supervisor, enquanto 13 supervisionam de fato.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# (tabela, coluna com o id do usuário, coluna com o e-mail, coluna de data, rótulo)
# Uma das duas colunas de identificação pode ser None — vários módulos gravam
# só o e-mail, outros só o id.
_FONTES: list[tuple[str, str | None, str | None, str, str]] = [
    ("publicacao_tarefa_audit", "scheduled_by_user_id", "scheduled_by_email",
     "scheduled_at", "Agendou tarefa de publicação"),
    ("publicacao_buscas", None, "requested_by_email", "created_at",
     "Rodou busca de publicações"),
    ("publicacao_batches_classificacao", None, "requested_by_email", "created_at",
     "Classificou publicações"),
    ("balanceador_reatribuir_job", "criado_por_id", None, "iniciado_em",
     "Redistribuiu agenda"),
    # `balanceador_log` NÃO entra: é o registro companheiro do MESMO evento de
    # redistribuição (medido em prod: 51 e 51 para a mesma pessoa, 55 e 55 para
    # outra). Contar os dois dobrava a pontuação de quem usa o balanceador — e
    # esse é justamente o público que o relatório existe para avaliar.
    ("lotes_execucao", None, "requested_by_email", "start_time",
     "Rodou lote de execução"),
    ("encerramentos_l1_intake", None, "operador_email", "created_at",
     "Encerramento"),
    ("prazo_inicial_batches", None, "requested_by_email", "created_at",
     "Lote de Prazos Iniciais"),
    ("bbd_runs", "disparado_por_user_id", None, "iniciado_em",
     "Rodada Distribuídos BB"),
    ("bbd_planilhas", "subido_por_user_id", None, "created_at",
     "Subiu planilha do BB"),
    ("base_processual_upload", "uploaded_by_user_id", None, "uploaded_at",
     "Subiu base processual"),
    ("base_processual_export", "requested_by_user_id", None, "requested_at",
     "Exportou base processual"),
    ("classification_feedbacks", None, "created_by_email", "created_at",
     "Feedback de classificação"),
    ("perf_relatorio", "criado_por_id", None, "criado_em",
     "Relatório de Minha Equipe"),
    ("ged_upload_batch", "created_by_user_id", None, "created_at",
     "Envio ao GED"),
    ("classificador_batch", "requested_by_user_id", "requested_by_email",
     "created_at", "Rodada do Classificador"),
    ("contato_atualizacao_batch", "created_by_user_id", None, "created_at",
     "Atualização de contatos"),
    ("admin_notices", "created_by_user_id", None, "created_at",
     "Publicou aviso"),
    ("analise_recursal", "uploaded_by_user_id", "uploaded_by_email",
     "created_at", "Análise recursal"),
]


def _tabela_existe(db: Session, nome: str) -> bool:
    return bool(db.execute(
        text("select to_regclass(:n)"), {"n": f"public.{nome}"}
    ).scalar())


def _coletar_acoes(db: Session, desde: date) -> list[tuple]:
    """Devolve (user_id, dia, tipo, n) de TODAS as fontes, numa consulta só.

    Uma query em vez de 19: a mesma coleta alimenta o total por pessoa, o
    detalhe por tipo e a série do dashboard. Fazer três varreduras separadas
    dos mesmos 19 rastros seria desperdício — e, pior, abriria espaço pros
    três números discordarem entre si.

    Só usuário ATIVO entra, porque é a mesma população da tabela do relatório.
    Sem esse filtro o gráfico somava 11.222 ações e a tabela 11.220: duas
    ações de gente desligada apareciam no total e em linha nenhuma — o tipo de
    diferença que destrói a confiança no relatório inteiro.
    """
    partes: list[str] = []
    for tabela, col_id, col_email, col_data, rotulo in _FONTES:
        if not _tabela_existe(db, tabela):
            continue
        if col_id and col_email:
            ligacao = f"(t.{col_id} = u.id or lower(t.{col_email}) = lower(u.email))"
        elif col_id:
            ligacao = f"t.{col_id} = u.id"
        else:
            ligacao = f"lower(t.{col_email}) = lower(u.email)"
        # O rótulo entra como literal no SQL — vem só da constante _FONTES
        # acima, nunca de entrada do usuário.
        partes.append(f"""
            select u.id as user_id,
                   (t.{col_data} at time zone 'America/Sao_Paulo')::date as dia,
                   '{rotulo}' as tipo,
                   count(*) as n
              from {tabela} t
              join legal_one_users u on {ligacao}
             where t.{col_data} >= :desde and u.is_active
             group by 1, 2
        """)

    if not partes:
        return []
    try:
        return list(db.execute(text(" union all ".join(partes)),
                               {"desde": desde}).fetchall())
    except Exception as exc:  # noqa: BLE001
        logger.warning("uso: coleta de ações falhou (%s)", str(exc)[:200])
        db.rollback()
        return _coletar_acoes_uma_a_uma(db, desde)


def _coletar_acoes_uma_a_uma(db: Session, desde: date) -> list[tuple]:
    """Plano B: fonte a fonte, pulando a que quebrar.

    A união é uma consulta só — se UMA tabela mudar de forma, o relatório
    inteiro cai. Aqui o estrago fica contido na fonte problemática.
    """
    saida: list[tuple] = []
    for tabela, col_id, col_email, col_data, rotulo in _FONTES:
        if not _tabela_existe(db, tabela):
            continue
        if col_id and col_email:
            ligacao = f"(t.{col_id} = u.id or lower(t.{col_email}) = lower(u.email))"
        elif col_id:
            ligacao = f"t.{col_id} = u.id"
        else:
            ligacao = f"lower(t.{col_email}) = lower(u.email)"
        try:
            saida += list(db.execute(text(f"""
                select u.id, (t.{col_data} at time zone 'America/Sao_Paulo')::date,
                       '{rotulo}', count(*)
                  from {tabela} t join legal_one_users u on {ligacao}
                 where t.{col_data} >= :desde and u.is_active group by 1, 2
            """), {"desde": desde}).fetchall())
        except Exception as exc:  # noqa: BLE001
            logger.warning("uso: fonte %s ignorada (%s)", tabela, str(exc)[:120])
            db.rollback()
    return saida


def _ultima_acao_por_usuario(linhas: list[tuple]) -> dict[int, date]:
    """Data da última ação de cada pessoa, SEM recorte de período."""
    ultima: dict[int, date] = {}
    for uid, dia, _tipo, _n in linhas:
        if not dia:
            continue
        atual = ultima.get(int(uid))
        if atual is None or dia > atual:
            ultima[int(uid)] = dia
    return ultima


def _resumir_acoes(linhas: list[tuple]) -> dict[int, dict[str, Any]]:
    """Agrupa a coleta por usuário: total, detalhe por tipo e última data."""
    acumulado: dict[int, dict[str, Any]] = {}
    for uid, dia, tipo, n in linhas:
        reg = acumulado.setdefault(
            int(uid), {"total": 0, "por_tipo": {}, "ultima": None})
        reg["total"] += int(n)
        reg["por_tipo"][tipo] = reg["por_tipo"].get(tipo, 0) + int(n)
        if dia and (reg["ultima"] is None or dia > reg["ultima"]):
            reg["ultima"] = dia
    return acumulado


def _navegacao_por_usuario(
    db: Session, desde: date,
) -> dict[int, dict[str, Any]]:
    """Dias com acesso, requisições e módulos visitados, por usuário."""
    if not _tabela_existe(db, "flow_uso_diario"):
        return {}
    linhas = db.execute(text("""
        select user_id,
               count(distinct dia) as dias,
               sum(requisicoes)    as reqs,
               max(ultima_em)      as ultima,
               array_agg(distinct modulo) as modulos
          from flow_uso_diario
         where dia >= :desde
         group by user_id
    """), {"desde": desde}).fetchall()
    return {
        int(r[0]): {
            "dias_ativos": int(r[1] or 0),
            "requisicoes": int(r[2] or 0),
            "ultima_navegacao": r[3],
            "modulos": sorted(r[4] or []),
        }
        for r in linhas
    }


def _como_instante(d) -> datetime:
    """Uniformiza date/datetime ingênuo/datetime com fuso num instante UTC."""
    if isinstance(d, datetime):
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _serie_diaria(linhas: list[tuple], supervisores: set[int],
                  desde: date, hoje: date) -> list[dict]:
    """Série do dashboard: por dia, ações e QUANTAS PESSOAS distintas agiram.

    Pessoas distintas é a métrica de adesão; volume de ações não é. Uma única
    pessoa redistribuindo agenda cinquenta vezes produz um pico bonito e não
    significa que o time adotou o sistema.
    """
    por_dia: dict[str, dict] = {}
    for uid, dia, _tipo, n in linhas:
        if not dia:
            continue
        chave = dia.isoformat()
        reg = por_dia.setdefault(chave, {"acoes": 0, "pessoas": set(),
                                         "supervisores": set()})
        reg["acoes"] += int(n)
        reg["pessoas"].add(int(uid))
        if int(uid) in supervisores:
            reg["supervisores"].add(int(uid))

    # Dia sem movimento precisa aparecer como zero: buraco no eixo faria o
    # gráfico "pular" o fim de semana e sugerir continuidade que não houve.
    saida: list[dict] = []
    d = desde
    while d <= hoje:
        chave = d.isoformat()
        reg = por_dia.get(chave)
        saida.append({
            "dia": chave,
            "rotulo": f"{d.day:02d}/{d.month:02d}",
            "acoes": reg["acoes"] if reg else 0,
            "pessoas": len(reg["pessoas"]) if reg else 0,
            "supervisores": len(reg["supervisores"]) if reg else 0,
        })
        d += timedelta(days=1)
    return saida


def _situacao(ultimo_acesso: datetime | None, hoje: datetime) -> str:
    if ultimo_acesso is None:
        return "nunca entrou"
    dias = (hoje - ultimo_acesso).days
    if dias <= 7:
        return "ativo"
    if dias <= 30:
        return "pouco ativo"
    return "dormente"


def gerar(db: Session, dias: int = 30, apenas_supervisores: bool = False) -> dict:
    """Monta o relatório de utilização do período."""
    hoje = datetime.now(timezone.utc)
    desde = (hoje - timedelta(days=int(dias))).date()

    # Coleta LARGA e filtra estreito. "Último acesso" é fato de vida da
    # pessoa, não do período: calculado dentro da janela, quem agiu há 45 dias
    # aparecia como "nunca entrou" numa visão de 30 — que se lê como "nunca foi
    # treinado" em vez de "parou de usar", e manda o administrativo cobrar a
    # coisa errada. Uma consulta só: as linhas vêm agrupadas por dia/tipo, então
    # dois anos cabem de sobra na memória.
    desde_amplo = (hoje - timedelta(days=730)).date()
    todas_acoes = _coletar_acoes(db, desde_amplo)
    linhas_acoes = [l for l in todas_acoes if l[1] and l[1] >= desde]

    acoes = _resumir_acoes(linhas_acoes)
    ultima_acao_geral = _ultima_acao_por_usuario(todas_acoes)
    navegacao = _navegacao_por_usuario(db, desde)

    usuarios = db.execute(text("""
        select u.id, u.name, u.email, u.is_active,
               coalesce(c.nome, '(sem cargo)') as cargo,
               u.can_use_minha_equipe, u.minha_equipe_equipes, u.last_sso_at
          from legal_one_users u
          left join flow_cargo c on c.id = u.cargo_id
         where u.is_active
         order by u.name
    """)).fetchall()

    itens: list[dict] = []
    for (uid, nome, email, _ativo, cargo, tem_equipe,
         equipes, last_sso) in usuarios:
        supervisor = bool(tem_equipe)
        if apenas_supervisores and not supervisor:
            continue

        nav = navegacao.get(int(uid), {})
        ac = acoes.get(int(uid), {})

        # O "último acesso" é o mais recente entre o que sabemos: o login por
        # SSO, a navegação capturada e a última ação efetiva. Nenhum dos três
        # sozinho conta a história — login por senha não é carimbado, a
        # navegação só existe do deploy pra cá, e ação só aparece pra quem
        # escreve.
        candidatos = [d for d in (last_sso, nav.get("ultima_navegacao"),
                                  ultima_acao_geral.get(int(uid))) if d]
        # A última ação vem como DATA (a coleta agrupa por dia); as outras vêm
        # como instante. Sem normalizar, o max() compara tipos diferentes e
        # estoura.
        candidatos = [_como_instante(d) for d in candidatos]
        ultimo = max(candidatos) if candidatos else None

        # Só faz sentido cobrar quem tem o que usar: quem não tem nenhum
        # módulo liberado aparece como "sem acesso liberado", não como
        # dormente — culpar essa pessoa por não usar seria erro de leitura.
        sem_permissao = cargo == "Sem acesso" and not supervisor

        itens.append({
            "user_id": int(uid),
            "nome": nome,
            "email": email,
            "cargo": cargo,
            "supervisor": supervisor,
            "equipes": (equipes or "").split(",") if equipes else [],
            "ultimo_acesso": ultimo.isoformat() if ultimo else None,
            "ultimo_login_sso": last_sso.isoformat() if last_sso else None,
            "dias_ativos": nav.get("dias_ativos", 0),
            "requisicoes": nav.get("requisicoes", 0),
            "modulos": nav.get("modulos", []),
            "acoes": ac.get("total", 0),
            "acoes_por_tipo": ac.get("por_tipo", {}),
            "situacao": ("sem acesso liberado" if sem_permissao
                         else _situacao(ultimo, hoje)),
        })

    itens.sort(key=lambda i: (not i["supervisor"], i["ultimo_acesso"] or ""))

    supervisores = [i for i in itens if i["supervisor"]]
    ids_sup = {i["user_id"] for i in supervisores}

    # Gráficos e tabela precisam falar da MESMA população. Com "apenas
    # supervisores" marcado, deixar a série contando a casa inteira punha
    # 11.220 ações no gráfico e 302 na tabela logo abaixo — a tela se
    # contradizendo, que é o jeito mais rápido de perder a confiança do
    # administrativo no relatório.
    ids_visiveis = {i["user_id"] for i in itens}
    linhas_visiveis = [l for l in linhas_acoes if int(l[0]) in ids_visiveis]

    serie = _serie_diaria(linhas_visiveis, ids_sup, desde, hoje.date())

    # Tipos de ação mais executados no período, somando todo mundo. Responde
    # "o que a casa de fato usa" — que costuma ser diferente do que se imagina.
    por_tipo: dict[str, int] = {}
    for _uid, _dia, tipo, n in linhas_visiveis:
        por_tipo[tipo] = por_tipo.get(tipo, 0) + int(n)
    ranking_tipos = [
        {"tipo": t, "acoes": n}
        for t, n in sorted(por_tipo.items(), key=lambda kv: -kv[1])
    ]

    # Ranking de pessoas: só quem agiu, senão a barra fica cheia de zeros.
    ranking_pessoas = [
        {"nome": i["nome"], "primeiro_nome": i["nome"].split(" ")[0],
         "acoes": i["acoes"], "supervisor": i["supervisor"]}
        for i in sorted(itens, key=lambda x: -x["acoes"]) if i["acoes"] > 0
    ][:12]
    def conta(lista, sit):
        return sum(1 for i in lista if i["situacao"] == sit)

    return {
        "periodo_dias": int(dias),
        "desde": desde.isoformat(),
        "gerado_em": hoje.isoformat(),
        # A navegação começou a ser medida no deploy da captura; antes disso
        # só há ação efetiva. A tela precisa dizer isso, senão o período de
        # transição é lido como queda de uso.
        "navegacao_disponivel": bool(navegacao),
        "serie": serie,
        "ranking_tipos": ranking_tipos,
        "ranking_pessoas": ranking_pessoas,
        "resumo": {
            "supervisores": len(supervisores),
            "supervisores_ativos": conta(supervisores, "ativo"),
            "supervisores_pouco_ativos": conta(supervisores, "pouco ativo"),
            "supervisores_dormentes": conta(supervisores, "dormente"),
            "supervisores_nunca_entraram": conta(supervisores, "nunca entrou"),
            "usuarios_avaliados": len(itens),
        },
        "itens": itens,
    }
