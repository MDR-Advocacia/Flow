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


def _acoes_por_usuario(
    db: Session, desde: date,
) -> dict[int, dict[str, Any]]:
    """Conta ações efetivas por usuário, varrendo os rastros de autoria.

    Fonte que não existe no banco é PULADA em silêncio: o relatório roda em
    instalações onde nem todo módulo foi criado, e uma tabela ausente não pode
    derrubar a tela inteira do administrativo.
    """
    acumulado: dict[int, dict[str, Any]] = {}

    for tabela, col_id, col_email, col_data, rotulo in _FONTES:
        if not _tabela_existe(db, tabela):
            continue

        # O join por e-mail cobre os módulos que não guardam o id. É seguro
        # porque e-mail é único em legal_one_users.
        if col_id and col_email:
            ligacao = f"(t.{col_id} = u.id or lower(t.{col_email}) = lower(u.email))"
        elif col_id:
            ligacao = f"t.{col_id} = u.id"
        else:
            ligacao = f"lower(t.{col_email}) = lower(u.email)"

        sql = text(f"""
            select u.id as user_id, count(*) as n, max(t.{col_data}) as ultima
              from {tabela} t
              join legal_one_users u on {ligacao}
             where t.{col_data} >= :desde
             group by u.id
        """)
        try:
            linhas = db.execute(sql, {"desde": desde}).fetchall()
        except Exception as exc:  # noqa: BLE001
            logger.warning("uso: fonte %s ignorada (%s)", tabela, str(exc)[:120])
            db.rollback()
            continue

        for uid, n, ultima in linhas:
            reg = acumulado.setdefault(
                int(uid), {"total": 0, "por_tipo": {}, "ultima": None})
            reg["total"] += int(n)
            reg["por_tipo"][rotulo] = reg["por_tipo"].get(rotulo, 0) + int(n)
            if ultima and (reg["ultima"] is None or ultima > reg["ultima"]):
                reg["ultima"] = ultima

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

    acoes = _acoes_por_usuario(db, desde)
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
                                  ac.get("ultima")) if d]
        candidatos = [
            d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
            for d in candidatos
        ]
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
