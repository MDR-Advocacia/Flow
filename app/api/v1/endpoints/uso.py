"""Relatório de utilização do sistema (adesão) — só para administradores.

Módulo separado do admin.py de propósito: aquele arquivo já passou de 1.300
linhas e é justamente o tipo em que o Edit truncou código neste projeto.
"""
import csv
import io
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core import auth
from app.core.dependencies import get_db
from app.models.legal_one import LegalOneUser
from app.services import uso_relatorio, uso_service

router = APIRouter()
logger = logging.getLogger(__name__)


def _exige_admin(current_user: LegalOneUser) -> None:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )


@router.get("/uso")
def relatorio_de_uso(
    dias: int = Query(30, ge=1, le=365),
    apenas_supervisores: bool = Query(False),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    """Adesão por usuário: navegação + ação efetiva no período."""
    _exige_admin(current_user)
    # Descarrega o que está em memória antes de ler: sem isto o administrativo
    # abre a tela e não vê o que ele mesmo acabou de fazer, o que faz o
    # relatório inteiro parecer quebrado.
    try:
        uso_service.descarregar()
    except Exception:  # noqa: BLE001
        pass

    dados = uso_relatorio.gerar(
        db, dias=dias, apenas_supervisores=apenas_supervisores)
    itens = dados.pop("itens")
    return {**dados, "total": len(itens), "items": itens[offset:offset + limit]}


@router.get("/uso/export")
def exportar_uso(
    dias: int = Query(30, ge=1, le=365),
    apenas_supervisores: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    """Mesmo relatório em CSV, para levar à reunião."""
    _exige_admin(current_user)
    try:
        uso_service.descarregar()
    except Exception:  # noqa: BLE001
        pass

    dados = uso_relatorio.gerar(
        db, dias=dias, apenas_supervisores=apenas_supervisores)

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Nome", "E-mail", "Cargo", "Supervisor", "Equipes", "Situação",
                "Último acesso", "Dias com acesso", "Requisições",
                "Módulos usados", "Ações efetivas"])
    for i in dados["itens"]:
        w.writerow([
            i["nome"], i["email"], i["cargo"],
            "Sim" if i["supervisor"] else "Não",
            ", ".join(i["equipes"]), i["situacao"],
            (i["ultimo_acesso"] or "")[:16].replace("T", " "),
            i["dias_ativos"], i["requisicoes"],
            ", ".join(i["modulos"]), i["acoes"],
        ])
    buf.seek(0)
    # utf-8-sig: sem o BOM o Excel em pt-BR abre os acentos quebrados.
    return StreamingResponse(
        iter([buf.getvalue().encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="utilizacao_{dias}dias.csv"'},
    )


@router.get("/shadow-publicacoes")
def placar_shadow(
    dias: int = Query(30, ge=1, le=180),
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    """Placar do shadow mode do tratamento de publicações.

    Responde a pergunta que o estudo de 06/08/2026 deixou em aberto com
    número real em vez de estimativa: quanto o sistema concorda com a
    operação, e ONDE ele já é confiável o bastante pra virar automação.

    Só entra par COMPLETO (previsão feita antes + desfecho do operador).
    """
    _exige_admin(current_user)
    from sqlalchemy import text as _t

    corte = f"real_em >= now() - interval '{int(dias)} days'"

    geral = db.execute(_t(f"""
        select count(*) as pares,
               count(*) filter (where acertou) as acertos,
               round(100.0 * count(*) filter (where acertou)
                     / nullif(count(*),0), 1) as pct
          from publicacao_shadow_decisao
         where real is not null and {corte}
    """)).first()

    # Por CONFIANÇA: é o corte que decide o que pode ser automatizado.
    por_confianca = [dict(r._mapping) for r in db.execute(_t(f"""
        select confianca, count(*) as pares,
               count(*) filter (where acertou) as acertos,
               round(100.0 * count(*) filter (where acertou)
                     / nullif(count(*),0), 1) as pct
          from publicacao_shadow_decisao
         where real is not null and {corte}
         group by 1 order by 1
    """))]

    # Por REGRA: mostra qual parte da política acerta e qual erra.
    por_regra = [dict(r._mapping) for r in db.execute(_t(f"""
        select regra, count(*) as pares,
               count(*) filter (where acertou) as acertos,
               round(100.0 * count(*) filter (where acertou)
                     / nullif(count(*),0), 1) as pct
          from publicacao_shadow_decisao
         where real is not null and {corte}
         group by 1 order by 2 desc
    """))]

    # Matriz de confusão — o erro que importa é prever IGNORAR no que o
    # operador AGENDOU (risco de prazo perdido), não o contrário.
    matriz = [dict(r._mapping) for r in db.execute(_t(f"""
        select previsto, real, count(*) as n
          from publicacao_shadow_decisao
         where real is not null and {corte}
         group by 1,2 order by 1,2
    """))]

    # Células candidatas a automação: alta confiança, massa e acerto alto.
    candidatas = [dict(r._mapping) for r in db.execute(_t(f"""
        select s.sinais->>'categoria' as categoria,
               s.sinais->>'subcategoria' as subcategoria,
               (s.sinais->>'office_id') as office_id,
               count(*) as pares,
               round(100.0 * count(*) filter (where s.acertou)
                     / nullif(count(*),0), 1) as pct
          from publicacao_shadow_decisao s
         where s.real is not null and s.confianca = 'alta' and {corte}
         group by 1,2,3
        having count(*) >= 20
           and 100.0 * count(*) filter (where s.acertou) / count(*) >= 95
         order by pares desc limit 40
    """))]

    # Motivos do ignore que a operação registrou (pub006) — o gabarito novo.
    motivos = [dict(r._mapping) for r in db.execute(_t(f"""
        select ignore_reason as motivo, count(*) as n
          from publicacao_registros
         where status = 'IGNORADO' and ignore_reason is not null
           and ignored_at >= now() - interval '{int(dias)} days'
         group by 1 order by 2 desc
    """))]

    pendentes = db.execute(_t(
        "select count(*) from publicacao_shadow_decisao where real is null"
    )).scalar()

    return {
        "periodo_dias": int(dias),
        "pares": int(geral.pares or 0) if geral else 0,
        "acertos": int(geral.acertos or 0) if geral else 0,
        "concordancia_pct": float(geral.pct) if geral and geral.pct else 0.0,
        "previsoes_aguardando_desfecho": int(pendentes or 0),
        "por_confianca": por_confianca,
        "por_regra": por_regra,
        "matriz": matriz,
        "celulas_candidatas_automacao": candidatas,
        "motivos_de_ignore": motivos,
    }
