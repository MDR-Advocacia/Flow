"""
Endpoints CRUD para templates de tarefa (classificação × escritório → tarefa).

Rotas:
  GET    /                  → Lista templates (com filtros opcionais)
  GET    /pending-review    → Lista templates v1 pendentes de revisao na v2
  GET    /{id}              → Detalhe de um template
  POST   /                  → Cria novo template
  PUT    /{id}              → Atualiza um template
  POST   /{id}/migrate      → Migra um template v1 pra v2 (revisao do operador)
  DELETE /{id}              → Remove um template
  GET    /meta/categories   → Lista categorias/subcategorias disponíveis
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.dependencies import get_db
from app.models.legal_one import LegalOneOffice, LegalOneTaskSubType, LegalOneTaskType, LegalOneUser
from app.models.task_template import TaskTemplate

router = APIRouter()


# ─── Schemas ────────────────────────────────────

class TaskTemplateBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    category: str
    subcategory: Optional[str] = None
    # None = template global (publicações sem escritório/processo vinculado)
    office_external_id: Optional[int] = None
    task_subtype_external_id: int
    # Opcional: template pode ser criado sem responsável; o modal de
    # criação da tarefa cobra o preenchimento no momento de aplicar.
    responsible_user_external_id: Optional[int] = None
    priority: str = "Normal"
    due_business_days: int = Field(default=3, ge=0, le=365)
    due_date_reference: str = Field(default="publication", pattern=r"^(publication|today)$")
    description_template: Optional[str] = None
    notes_template: Optional[str] = None
    is_active: bool = True
    # 'principal' (default) ou 'assistente'. Quando 'assistente', o frontend
    # de Publicações resolve o assistente via /squads/assistant-of/... antes
    # de mandar o payload pra criação no L1.
    target_role: str = Field(default="principal", pattern="^(principal|assistente)$")
    # Quando setado, aponta pra uma squad de suporte (kind='support').
    # Combinado com target_role: 'principal'=lider, 'assistente'=assistente
    # (round-robin) da squad de suporte.
    target_squad_id: Optional[int] = Field(default=None, ge=1)
    # Exceção por etiqueta do processo (sqd005). Ex.: "NERC" — processo com
    # essa etiqueta sai da regra deste template e fica com o responsável da
    # pasta e com o assistente da squad marcada com a mesma etiqueta.
    excecao_etiqueta: Optional[str] = Field(default=None, max_length=80)
    # Quem da equipe da etiqueta recebe: 'principal' (advogado) ou
    # 'assistente'. Vazio = segue o target_role.
    excecao_papel: Optional[str] = Field(default=None, pattern="^(principal|assistente)$")


class TaskTemplateCreate(TaskTemplateBase):
    pass


class TaskTemplateUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    office_external_id: Optional[int] = None
    task_subtype_external_id: Optional[int] = None
    responsible_user_external_id: Optional[int] = None
    priority: Optional[str] = None
    due_business_days: Optional[int] = Field(default=None, ge=0, le=365)
    due_date_reference: Optional[str] = Field(default=None, pattern=r"^(publication|today)$")
    description_template: Optional[str] = None
    notes_template: Optional[str] = None
    is_active: Optional[bool] = None
    target_role: Optional[str] = Field(default=None, pattern="^(principal|assistente)$")
    target_squad_id: Optional[int] = Field(default=None, ge=1)
    excecao_etiqueta: Optional[str] = Field(default=None, max_length=80)
    excecao_papel: Optional[str] = Field(default=None, pattern="^(principal|assistente)$")


class TaskTemplateResponse(TaskTemplateBase):
    id: int
    office_name: Optional[str] = None
    office_polo_scope: Optional[str] = None
    task_subtype_name: Optional[str] = None
    task_type_name: Optional[str] = None
    responsible_user_name: Optional[str] = None
    # Taxonomy v2 fields (tax003).
    taxonomy_version: str = "v1"
    legacy_label: Optional[str] = None
    needs_taxonomy_review: bool = False

    class Config:
        orm_mode = True


class TaskTemplateMigratePayload(BaseModel):
    """Payload do operador revisando um template v1 pra v2.

    O modal de edicao envia category/subcategory selecionadas via
    ClassificationPicker (regra da casa: combobox searchable). Demais
    campos do template ja estao preservados desde tax007 — operador
    so esta re-apontando pra arvore nova."""

    category: str = Field(..., min_length=1)
    subcategory: Optional[str] = None


# ─── Helpers ────────────────────────────────────

def _to_response(tmpl: TaskTemplate) -> dict:
    office_name = None
    office_polo_scope = None
    subtype_name = None
    type_name = None
    user_name = None

    if tmpl.office:
        # Usa path (hierarquia completa) se disponível, senão name
        office_name = tmpl.office.path or tmpl.office.name
        office_polo_scope = getattr(tmpl.office, "polo_scope", None)
    elif tmpl.office_external_id is None:
        office_name = "✦ Publicações sem processo"  # template global
    if tmpl.task_subtype:
        subtype_name = tmpl.task_subtype.name
        if tmpl.task_subtype.parent_type:
            type_name = tmpl.task_subtype.parent_type.name
    if tmpl.responsible_user:
        user_name = tmpl.responsible_user.name

    return {
        "id": tmpl.id,
        "name": tmpl.name,
        "category": tmpl.category,
        "subcategory": tmpl.subcategory,
        "office_external_id": tmpl.office_external_id,
        "office_name": office_name,
        "office_polo_scope": office_polo_scope,
        "task_subtype_external_id": tmpl.task_subtype_external_id,
        "task_subtype_name": subtype_name,
        "task_type_name": type_name,
        "responsible_user_external_id": tmpl.responsible_user_external_id,
        "responsible_user_name": user_name,
        "priority": tmpl.priority,
        "due_business_days": tmpl.due_business_days,
        "due_date_reference": tmpl.due_date_reference or "publication",
        "description_template": tmpl.description_template,
        "notes_template": tmpl.notes_template,
        "is_active": tmpl.is_active,
        "target_role": getattr(tmpl, "target_role", None) or "principal",
        "target_squad_id": getattr(tmpl, "target_squad_id", None),
        "target_squad_name": _support_squad_name_lookup(tmpl),
        "excecao_etiqueta": getattr(tmpl, "excecao_etiqueta", None),
        "excecao_papel": getattr(tmpl, "excecao_papel", None),
        "taxonomy_version": getattr(tmpl, "taxonomy_version", None) or "v1",
        "legacy_label": getattr(tmpl, "legacy_label", None),
        "needs_taxonomy_review": bool(getattr(tmpl, "needs_taxonomy_review", False)),
    }


def _support_squad_name_lookup(tmpl: TaskTemplate) -> Optional[str]:
    sq_id = getattr(tmpl, "target_squad_id", None)
    if not sq_id:
        return None
    try:
        from app.db.session import SessionLocal
        from app.models.rules import Squad
        with SessionLocal() as s:
            row = s.query(Squad.name).filter(Squad.id == sq_id).first()
            return row[0] if row else None
    except Exception:
        return None


def _invalidate_taxonomy_cache_safe(office_external_id: Optional[int]) -> None:
    """Wrapper que loga (mas nao propaga) erros de invalidacao do cache.

    Cache miss em runtime apenas atrasa a propagacao da mudanca em ate
    60s (TTL natural). Erro aqui nao deve quebrar o CRUD do template.
    Modo arvore enxuta (fase 13)."""
    try:
        from app.services.classifier.taxonomy import (
            invalidate_taxonomy_cache_for_office,
        )
        invalidate_taxonomy_cache_for_office(office_external_id)
    except Exception:  # noqa: BLE001
        # Silencioso — TTL natural sobe a mudanca em ate 60s.
        pass


def _validate_foreign_keys(
    db: Session,
    office_external_id: Optional[int],
    task_subtype_external_id: int,
    responsible_user_external_id: Optional[int],
) -> None:
    # office_external_id pode ser None (template global)
    if office_external_id is not None:
        if not db.query(LegalOneOffice).filter(
            LegalOneOffice.external_id == office_external_id
        ).first():
            raise HTTPException(status_code=400, detail=f"Escritório {office_external_id} não encontrado.")

    if not db.query(LegalOneTaskSubType).filter(
        LegalOneTaskSubType.external_id == task_subtype_external_id
    ).first():
        raise HTTPException(status_code=400, detail=f"Subtipo de tarefa {task_subtype_external_id} não encontrado.")

    # Responsável é opcional; só valida FK se foi informado.
    if responsible_user_external_id is not None:
        if not db.query(LegalOneUser).filter(
            LegalOneUser.external_id == responsible_user_external_id
        ).first():
            raise HTTPException(status_code=400, detail=f"Usuário {responsible_user_external_id} não encontrado.")


# ─── Endpoints ──────────────────────────────────

@router.get("/", response_model=List[TaskTemplateResponse])
def list_templates(
    category: Optional[str] = Query(None),
    subcategory: Optional[str] = Query(None),
    office_external_id: Optional[int] = Query(None),
    is_active: Optional[bool] = Query(None),
    db: Session = Depends(get_db),
):
    """Lista templates com filtros opcionais."""
    q = db.query(TaskTemplate)

    if category is not None:
        q = q.filter(TaskTemplate.category == category)
    if subcategory is not None:
        q = q.filter(TaskTemplate.subcategory == subcategory)
    if office_external_id is not None:
        q = q.filter(TaskTemplate.office_external_id == office_external_id)
    if is_active is not None:
        q = q.filter(TaskTemplate.is_active == is_active)

    # Recém-criados primeiro: quando o operador salva um template novo
    # pela UI, ele espera ver no topo da lista imediatamente. Ordem secundária
    # mantém a organização alfabética por categoria dentro de um mesmo dia.
    templates = q.order_by(
        TaskTemplate.created_at.desc(),
        TaskTemplate.category,
        TaskTemplate.subcategory,
        TaskTemplate.name,
    ).all()
    return [_to_response(t) for t in templates]


@router.get("/coverage")
def get_office_coverage(
    office_external_id: int = Query(
        ...,
        description="ID externo do escritório responsável. Obrigatório.",
    ),
    db: Session = Depends(get_db),
):
    """Dashboard por escritório: arvore aplicavel + status de cobertura por
    categoria/subcategoria. Resposta unica que o frontend usa pra renderizar
    a tela `Por escritorio` (TaskTemplatesPage tab default).

    Estrutura da resposta:
      {
        "office": {external_id, name, path, polo_scope},
        "taxonomy": {active_version, template_driven_mode},
        "tree": [
          {
            "category": "...",
            "polo_scope": "...",  // do registro classification_categories
            "subcategories": [
              {
                "name": "...",
                "templates": [TaskTemplateResponse, ...],
                "pending_templates": [TaskTemplateResponse, ...],
              },
              ...
            ],
            "category_only_templates": [...],   // pra cats sem subs (sub IS NULL)
            "category_only_pending": [...]
          }
        ],
        "summary": {
          total_categories, categories_with_template,
          categories_without_template, pending_review_total
        }
      }

    `tree` ja vem filtrada por polo do escritorio + versao ativa da
    taxonomia. NAO aplica o filtro "modo arvore enxuta" (template-driven)
    aqui — o operador precisa enxergar TODAS as cats do polo pra saber
    o que falta configurar; a UI marca cada linha com "tem template" /
    "sem template" / "pendente".
    """
    from app.services.classifier.taxonomy import (
        _get_active_tree, get_active_taxonomy_version,
        is_template_driven_taxonomy_active,
    )
    from app.models.classification_taxonomy import ClassificationCategory

    office = (
        db.query(LegalOneOffice)
        .filter(LegalOneOffice.external_id == office_external_id)
        .first()
    )
    if office is None:
        raise HTTPException(404, f"Escritório {office_external_id} não encontrado.")

    polo = (getattr(office, "polo_scope", None) or "ambos").strip().lower()
    active_version = get_active_taxonomy_version()

    # Carrega arvore SEM filtro template-driven (operador precisa ver
    # cats sem template pra adicionar).
    #
    # IMPORTANTE: a UI de templates SEMPRE prioriza v2 (a "atual"), mesmo
    # que o toggle global `taxonomy_active_version` esteja em 'v1'. O
    # toggle governa o que a IA emite — mas o operador esta CONFIGURANDO
    # templates, e templates novos sao sempre v2. Mostrar v1 aqui faria
    # com que templates v2 ja migrados aparecessem como "perdidos"
    # (apontariam pra cats que nao existem na arvore v1 exibida).
    #
    # Se a v2 ainda nao esta seedada (DB sem cats v2), cai em v1 — ai
    # nao tem como mostrar diferente mesmo.
    # Escritorio FICTICIO da fila sem pasta (-1, pub014): a arvore e a do
    # MOTOR PROPRIO — 16 tipos, plana, em codigo. Nao passa pela taxonomia
    # v2 nem por polo: sao mundos separados (dois motores). Sem este ramo a
    # cobertura mostraria as cats dos escritorios reais, com que essa fila
    # nunca e classificada — o operador configuraria template que nao casa.
    from app.services.publication_sem_pasta import SEM_PASTA_OFFICE_ID

    if office_external_id == SEM_PASTA_OFFICE_ID:
        from app.services.publication_sem_pasta_motor import arvore_para_ui

        tree_dict = arvore_para_ui()
        used_version = "sem_pasta"
    else:
        polo_filter = polo if polo in ("ativo", "passivo") else None
        tree_dict = _get_active_tree(
            polo_scope=polo_filter,
            taxonomy_version="v2",
        )
        used_version = "v2"
        if not tree_dict:
            # v2 nao seedada — fallback pra v1.
            tree_dict = _get_active_tree(
                polo_scope=polo_filter,
                taxonomy_version="v1",
            )
            used_version = "v1"

    # Carrega todos os templates do escritorio (ativos + pendentes,
    # globais incluidos) com 1 query e mapeia por (cat, sub).
    #
    # Filtra is_active=True: templates desativados pelo botao "remover"
    # da arvore nao podem continuar aparecendo na cobertura — senao a UI
    # parece ignorar a remocao (ja que o desativado volta no proximo
    # refetch). Reativacao continua possivel via aba Auditoria, que tem
    # o seu proprio listing dedicado.
    tmpl_rows = (
        db.query(TaskTemplate)
        .filter(
            (TaskTemplate.office_external_id == office_external_id)
            | (TaskTemplate.office_external_id.is_(None))
        )
        .filter(TaskTemplate.is_active.is_(True))
        .all()
    )
    # Agrupa por (cat, sub) → list of templates
    by_key: dict[tuple[str, Optional[str]], list[TaskTemplate]] = {}
    by_key_pending: dict[tuple[str, Optional[str]], list[TaskTemplate]] = {}
    for t in tmpl_rows:
        key = (t.category, t.subcategory)
        if t.needs_taxonomy_review:
            by_key_pending.setdefault(key, []).append(t)
        else:
            by_key.setdefault(key, []).append(t)

    tree_payload = []
    cats_with_template = 0
    pending_total = 0
    # Polo de cada categoria — busca em uma query so pra evitar N+1.
    polo_by_cat = {
        c.name: c.polo_scope
        for c in db.query(ClassificationCategory)
        .filter(ClassificationCategory.is_active.is_(True))
        .all()
    }

    for cat_name, subs in tree_dict.items():
        cat_node = {
            "category": cat_name,
            "polo_scope": polo_by_cat.get(cat_name),
            "subcategories": [],
            "category_only_templates": [],
            "category_only_pending": [],
        }
        cat_has_template = False

        if subs:
            for sub_name in subs:
                k = (cat_name, sub_name)
                tmpls = by_key.get(k, [])
                pendings = by_key_pending.get(k, [])
                if tmpls:
                    cat_has_template = True
                pending_total += len(pendings)
                cat_node["subcategories"].append({
                    "name": sub_name,
                    "templates": [_to_response(t) for t in tmpls],
                    "pending_templates": [_to_response(t) for t in pendings],
                })
        else:
            # Categoria sem subs: templates apontam pra (cat, NULL)
            k = (cat_name, None)
            tmpls = by_key.get(k, [])
            pendings = by_key_pending.get(k, [])
            if tmpls:
                cat_has_template = True
            pending_total += len(pendings)
            cat_node["category_only_templates"] = [_to_response(t) for t in tmpls]
            cat_node["category_only_pending"] = [_to_response(t) for t in pendings]

        if cat_has_template:
            cats_with_template += 1

        tree_payload.append(cat_node)

    return {
        "office": {
            "external_id": office.external_id,
            "name": office.name,
            "path": office.path or office.name,
            "polo_scope": polo,
        },
        "taxonomy": {
            "active_version": active_version,
            "tree_version_shown": used_version,
            "template_driven_mode": is_template_driven_taxonomy_active(),
        },
        "tree": tree_payload,
        "summary": {
            "total_categories": len(tree_payload),
            "categories_with_template": cats_with_template,
            "categories_without_template": len(tree_payload) - cats_with_template,
            "pending_review_total": pending_total,
        },
    }


@router.get("/pending-review")
def list_pending_review(
    office_external_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Lista templates v1 marcados como pendentes de revisao na taxonomia v2.

    Paginacao obrigatoria pela regra da casa (ver CLAUDE.md). Retorna
    `{ total, items }` no padrao do PublicationsPage / PrazosIniciaisPage.
    Operador acessa essa lista do painel "Templates Pendentes de Revisao"
    em Admin/Templates."""
    base = db.query(TaskTemplate).filter(
        TaskTemplate.needs_taxonomy_review == True
    )
    if office_external_id is not None:
        base = base.filter(TaskTemplate.office_external_id == office_external_id)

    total = base.count()
    rows = (
        base.order_by(
            TaskTemplate.office_external_id.asc().nulls_first(),
            TaskTemplate.category,
            TaskTemplate.name,
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "items": [_to_response(t) for t in rows],
        "limit": limit,
        "offset": offset,
    }


@router.get("/{template_id}", response_model=TaskTemplateResponse)
def get_template(template_id: int, db: Session = Depends(get_db)):
    """Retorna detalhe de um template."""
    tmpl = db.query(TaskTemplate).filter(TaskTemplate.id == template_id).first()
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template não encontrado.")
    return _to_response(tmpl)


@router.post("/", response_model=TaskTemplateResponse, status_code=201)
def create_template(payload: TaskTemplateCreate, db: Session = Depends(get_db)):
    """Cria um novo template de tarefa.

    Quando ja existe um template *inativo* com a mesma chave
    (category+subcategory+office+task_subtype), reativa-o e sobrescreve
    com o payload novo — em vez de bloquear o operador com 409. Motivo:
    a remocao via UI e soft-delete (PUT is_active=False) e o endpoint
    /coverage so mostra ativos. Sem essa reativacao o operador via UI
    vazia mas recebia "ja existe um template" ao tentar criar de novo,
    sem conseguir achar o "fantasma" pra reativar."""
    _validate_foreign_keys(
        db,
        payload.office_external_id,
        payload.task_subtype_external_id,
        payload.responsible_user_external_id,
    )

    # Múltiplos templates por (category, subcategory, office) são permitidos
    # — cada um gera uma tarefa diferente no agendamento (ver docstring do
    # modelo TaskTemplate). Só bloqueamos duplicata *exata*: mesma
    # classificação + mesmo escritório + mesmo subtipo de tarefa, já que
    # dois templates idênticos gerariam a mesma tarefa duas vezes.
    existing = db.query(TaskTemplate).filter(
        TaskTemplate.category == payload.category,
        TaskTemplate.subcategory == payload.subcategory,
        TaskTemplate.office_external_id == payload.office_external_id,
        TaskTemplate.task_subtype_external_id == payload.task_subtype_external_id,
    ).first()
    if existing and existing.is_active:
        raise HTTPException(
            status_code=409,
            detail=(
                "Já existe um template para esta classificação, escritório e "
                "subtipo de tarefa. Para gerar uma segunda tarefa na mesma "
                "classificação, escolha um subtipo de tarefa diferente."
            ),
        )

    if existing and not existing.is_active:
        # Template foi soft-deleted via "remover" — reativa e sobrescreve
        # com o payload novo. Visto do operador, isso e' a criacao normal:
        # ele nao tinha visibilidade pra saber que existia um registro
        # inativo travando a chave.
        for k, v in payload.dict().items():
            setattr(existing, k, v)
        existing.is_active = True
        # Tira o template de "pendente revisao": o operador acabou de
        # reescolher cat/sub pela UI nova (v2), entao a flag legacy nao
        # se aplica mais.
        existing.needs_taxonomy_review = False
        db.commit()
        db.refresh(existing)
        _invalidate_taxonomy_cache_safe(existing.office_external_id)
        return _to_response(existing)

    tmpl = TaskTemplate(**payload.dict())
    db.add(tmpl)
    db.commit()
    db.refresh(tmpl)
    # Modo arvore enxuta: invalida cache de taxonomia do escritorio
    # afetado (e dos globais) pra que a proxima classificacao desse
    # office veja a cat nova ja na arvore enxuta.
    _invalidate_taxonomy_cache_safe(payload.office_external_id)
    return _to_response(tmpl)


@router.put("/{template_id}", response_model=TaskTemplateResponse)
def update_template(
    template_id: int,
    payload: TaskTemplateUpdate,
    db: Session = Depends(get_db),
):
    """Atualiza um template existente."""
    tmpl = db.query(TaskTemplate).filter(TaskTemplate.id == template_id).first()
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template não encontrado.")

    updates = payload.dict(exclude_unset=True)

    # Valida FKs se estiverem sendo atualizadas
    if any(k in updates for k in ("office_external_id", "task_subtype_external_id", "responsible_user_external_id")):
        _validate_foreign_keys(
            db,
            updates.get("office_external_id", tmpl.office_external_id),
            updates.get("task_subtype_external_id", tmpl.task_subtype_external_id),
            updates.get("responsible_user_external_id", tmpl.responsible_user_external_id),
        )

    # Captura escritorios pra invalidar cache: o antigo (caso o operador
    # tenha mudado o office_external_id) e o novo. Modo arvore enxuta
    # depende disso pra refletir a edicao imediatamente.
    old_office = tmpl.office_external_id
    new_office = updates.get("office_external_id", old_office)

    for k, v in updates.items():
        setattr(tmpl, k, v)

    db.commit()
    db.refresh(tmpl)
    _invalidate_taxonomy_cache_safe(old_office)
    if new_office != old_office:
        _invalidate_taxonomy_cache_safe(new_office)
    return _to_response(tmpl)


@router.delete("/{template_id}", status_code=204)
def delete_template(template_id: int, db: Session = Depends(get_db)):
    """Remove um template."""
    tmpl = db.query(TaskTemplate).filter(TaskTemplate.id == template_id).first()
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template não encontrado.")
    office_id = tmpl.office_external_id
    db.delete(tmpl)
    db.commit()
    _invalidate_taxonomy_cache_safe(office_id)
    return None


@router.post("/{template_id}/migrate", response_model=TaskTemplateResponse)
def migrate_template_to_v2(
    template_id: int,
    payload: TaskTemplateMigratePayload,
    db: Session = Depends(get_db),
):
    """Migra um template v1 pra v2 — operador re-aponta a classificacao.

    Fluxo: o painel "Templates Pendentes de Revisao" abre o modal de
    edicao com banner amarelo mostrando `legacy_label`. Operador escolhe
    a (categoria, subcategoria) na arvore v2 via ClassificationPicker
    (filtrada pelo polo_scope do escritorio do template). Ao salvar,
    o frontend chama esse endpoint, que:

      - valida (categoria, subcategoria) contra a arvore v2 do polo
        do escritorio (se nao for 'ambos');
      - atualiza category/subcategory pros valores novos;
      - marca taxonomy_version='v2', needs_taxonomy_review=False;
      - mantem is_active e demais campos como estavam.

    Demais alteracoes (responsavel, prazo, etc.) seguem usando o
    endpoint PUT padrao — `migrate` cuida especificamente do re-apontamento
    da classificacao."""
    from app.services.classifier.taxonomy import (
        validate_classification, repair_classification,
    )

    tmpl = db.query(TaskTemplate).filter(TaskTemplate.id == template_id).first()
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template não encontrado.")

    # Determina o polo da arvore v2 a ser usada como referencia. Se o
    # escritorio do template tem polo_scope='ativo'/'passivo', a arvore
    # filtra por esse polo. Se 'ambos' ou template global, aceita
    # qualquer cat v2 (operador escolhe livremente).
    target_polo: Optional[str] = None
    if tmpl.office_external_id is not None:
        office = (
            db.query(LegalOneOffice)
            .filter(LegalOneOffice.external_id == tmpl.office_external_id)
            .first()
        )
        if office is not None:
            ps = getattr(office, "polo_scope", None)
            if ps in ("ativo", "passivo"):
                target_polo = ps

    # Tenta reparar pares (cat, sub) com erro de digitacao/case usando
    # o reparador da taxonomia v2. Se nao bater, valida_classification
    # rejeita e devolve 400 com a lista de cats validas.
    cat_clean, sub_clean = repair_classification(
        payload.category,
        payload.subcategory or "-",
        polo_scope=target_polo,
        taxonomy_version="v2",
    )
    if not validate_classification(
        cat_clean, sub_clean,
        polo_scope=target_polo,
        taxonomy_version="v2",
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Combinacao (categoria='{payload.category}', subcategoria='{payload.subcategory}') "
                f"nao existe na taxonomia v2 do polo '{target_polo or 'ambos'}'. "
                "Confira a arvore via GET /task-templates/meta/categories?taxonomy_version=v2."
            ),
        )

    tmpl.category = cat_clean
    tmpl.subcategory = sub_clean if sub_clean != "-" else None
    tmpl.taxonomy_version = "v2"
    tmpl.needs_taxonomy_review = False
    db.commit()
    db.refresh(tmpl)
    # Migrar tira o template do estado dormente (needs_taxonomy_review=true)
    # — agora ele vai casar publicacoes do escritorio. Isso muda a arvore
    # enxuta: a cat antes "ausente" agora aparece. Invalida cache.
    _invalidate_taxonomy_cache_safe(tmpl.office_external_id)
    return _to_response(tmpl)


@router.get("/meta/task-types")
def list_task_types(db: Session = Depends(get_db)):
    """Lista tipos e subtipos de tarefa com seus external_ids (para uso no formulário de template)."""
    from sqlalchemy.orm import joinedload
    types = (
        db.query(LegalOneTaskType)
        .options(joinedload(LegalOneTaskType.subtypes))
        .filter(LegalOneTaskType.is_active == True)
        .order_by(LegalOneTaskType.name)
        .all()
    )
    return [
        {
            "external_id": t.external_id,
            "name": t.name,
            "subtypes": [
                {"external_id": s.external_id, "name": s.name}
                for s in sorted(t.subtypes, key=lambda x: x.name)
                if s.is_active
            ],
        }
        for t in types
    ]


@router.get("/meta/users")
def list_users(db: Session = Depends(get_db)):
    """Lista usuários ativos com seus external_ids (para uso no formulário de template)."""
    users = (
        db.query(LegalOneUser)
        .filter(LegalOneUser.is_active == True)
        .order_by(LegalOneUser.name)
        .all()
    )
    return [{"external_id": u.external_id, "name": u.name, "email": u.email} for u in users]


@router.get("/meta/etiquetas")
def list_etiquetas(db: Session = Depends(get_db)):
    """Etiquetas do L1 em uso — alimenta a exceção por etiqueta do template e
    a etiqueta atendida da squad (sqd005). Só nomes lidos recentemente: o
    cache inteiro ainda guarda nome antigo (ver `etiquetas_em_uso`)."""
    from app.services.excecao_etiqueta import etiquetas_em_uso

    return etiquetas_em_uso(db)


@router.get("/meta/categories")
def list_categories(
    office_external_id: Optional[int] = Query(
        None,
        description="Se informado, aplica os overrides (excluir / adicionar customizada) do escritório.",
    ),
    polo_scope: Optional[str] = Query(
        None,
        pattern="^(ativo|passivo|ambos)$",
        description="Filtra a arvore por polo (taxonomy v2). 'ativo'/'passivo' inclui tambem cats marcadas como 'ambos'.",
    ),
    taxonomy_version: Optional[str] = Query(
        None,
        pattern="^(v1|v2)$",
        description="Filtra por versao da taxonomia. Default: sem filtro (retorna v1 + v2 misturado se ambas estiverem seedadas).",
    ),
    db: Session = Depends(get_db),
):
    """Lista as categorias e subcategorias disponíveis.

    - Sem `office_external_id`: retorna a taxonomia base do classificador.
      Quando `polo_scope`/`taxonomy_version` sao informados, a arvore e filtrada
      no DB antes de aplicar overrides.
    - Com `office_external_id`: retorna a taxonomia **efetiva** do escritório,
      ou seja, já com exclusões removidas e include_custom adicionados. Se o
      escritorio tem `polo_scope` configurado e o caller nao passou um polo
      explicito, usa o polo do proprio escritorio (regra "arvore do polo do
      escritorio responsavel" — fluxo principal da v2).
    """
    # Escritorio FICTICIO da fila sem pasta (-1, pub014): a taxonomia e a do
    # MOTOR PROPRIO, em codigo — nao passa pela arvore v2 nem por overrides.
    try:
        from app.services.publication_sem_pasta import SEM_PASTA_OFFICE_ID
        from app.services.publication_sem_pasta_motor import arvore_para_ui

        if office_external_id == SEM_PASTA_OFFICE_ID:
            return {
                "categories": [
                    {"category": cat, "subcategories": []} for cat in arvore_para_ui()
                ],
                "polo_scope_applied": "sem_pasta",
                "taxonomy_version_applied": None,
            }
    except Exception:
        pass

    try:
        from app.services.classifier.taxonomy import _get_active_tree  # noqa: WPS433
    except Exception:
        return {"categories": []}

    try:
        # Quando o caller nao passa polo_scope mas o escritorio tem um,
        # usa o do escritorio. Mantem retro-compatibilidade pra callers
        # que ainda nao foram migrados pra mandar polo_scope.
        effective_polo = polo_scope
        if effective_polo is None and office_external_id is not None:
            office_row = (
                db.query(LegalOneOffice)
                .filter(LegalOneOffice.external_id == office_external_id)
                .first()
            )
            if office_row is not None:
                ps = getattr(office_row, "polo_scope", None)
                if ps and ps != "ambos":
                    effective_polo = ps

        tree = {
            k: list(v)
            for k, v in _get_active_tree(
                polo_scope=effective_polo, taxonomy_version=taxonomy_version
            ).items()
        }

        if office_external_id is not None:
            try:
                from app.services.classifier.prompts import load_office_overrides  # noqa: WPS433

                excluded, custom_additions = load_office_overrides(
                    db, office_external_id, taxonomy_version=taxonomy_version
                )

                # Exclusões: subcategory=None remove a categoria inteira
                cats_to_remove: set[str] = set()
                for cat, sub in excluded:
                    if sub is None:
                        cats_to_remove.add(cat)
                    elif cat in tree and sub in tree[cat]:
                        tree[cat].remove(sub)
                for cat in cats_to_remove:
                    tree.pop(cat, None)

                # Adições customizadas
                for item in custom_additions:
                    cat = item.get("category", "")
                    sub = item.get("subcategory")
                    if not cat:
                        continue
                    if cat not in tree:
                        tree[cat] = []
                    if sub and sub not in tree[cat]:
                        tree[cat].append(sub)
            except Exception:
                # Falha ao aplicar overrides: mantem a arvore base sem quebrar.
                pass

        return {
            "categories": [
                {"category": cat, "subcategories": list(subs) if subs else []}
                for cat, subs in tree.items()
            ],
            "polo_scope_applied": effective_polo,
            "taxonomy_version_applied": taxonomy_version,
        }
    except Exception:
        return {"categories": []}
