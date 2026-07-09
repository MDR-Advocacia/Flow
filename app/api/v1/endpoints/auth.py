# app/api/v1/endpoints/auth.py

import base64
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import auth
from app.core.config import settings
from app.core.dependencies import get_db
from app.models.legal_one import LegalOneUser
from app.api.v1 import schemas

router = APIRouter()

logger = logging.getLogger(__name__)


def _validate_via_oauth2_proxy(cookie: str) -> "tuple[str, str]":
    """Valida a sessão do oauth2-proxy server-side: chama settings.sso_validate_url
    (/oauth2/auth) repassando o cookie do usuário e lê a identidade dos headers
    da resposta. Substitui o forward-auth do Traefik (que o Coolify aplicava no
    domínio inteiro, derrubando o acesso). Retorna (email, id_token); ('', '')
    se não houver sessão.

    SEGURANÇA: o oauth2-proxy valida o cookie (assinado) e só então devolve a
    identidade — nada é forjável pelo cliente."""
    if not settings.sso_validate_url or not cookie:
        return "", ""
    try:
        req = urllib.request.Request(
            settings.sso_validate_url,
            headers={"Cookie": cookie},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            email = (resp.headers.get(settings.sso_email_header) or "").strip().lower()
            id_token = resp.headers.get(settings.sso_id_token_header) or ""
            return email, id_token
    except urllib.error.HTTPError:
        # 401/403 → sem sessão SSO válida (usuário não logou no Entra).
        return "", ""
    except Exception:
        logger.warning("Falha ao validar sessão no oauth2-proxy", exc_info=True)
        return "", ""


def _name_from_id_token(authorization: str) -> "str | None":
    """Extrai o claim `name` (nome completo do Entra) do ID token que o
    oauth2-proxy injeta no header Authorization (Bearer <jwt>) quando
    OAUTH2_PROXY_SET_AUTHORIZATION_HEADER=true. Decodifica só o payload, SEM
    verificar assinatura — o token chega pelo proxy confiável, mesmo modelo de
    confiança dos headers X-Auth-Request-*. Retorna None se não vier, não for
    um JWT, ou não tiver `name`."""
    raw = (authorization or "").strip()
    for prefix in ("Bearer ", "bearer "):
        if raw.startswith(prefix):
            raw = raw[len(prefix):].strip()
            break
    parts = raw.split(".")
    if len(parts) != 3:
        return None
    try:
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)  # padding base64url
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
        name = (claims.get("name") or "").strip()
        return name or None
    except Exception:
        return None


@router.post("/token", response_model=schemas.Token)
def login_for_access_token(
    db: Session = Depends(get_db),
    form_data: OAuth2PasswordRequestForm = Depends()
):
    """
    Login por senha (break-glass / fallback). O acesso padrão passou a ser via
    SSO (Entra) em /sso/session. Recebe e-mail (como username) e senha.
    Retorna um token de acesso JWT em caso de sucesso.
    """
    # 1. Busca o usuário pelo e-mail no banco de dados.
    user = db.query(LegalOneUser).filter(LegalOneUser.email == form_data.username).first()

    # 2. Verifica se o usuário existe e se a senha está correta.
    if not user or not user.hashed_password or not auth.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-mail ou senha incorretos",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3. Cria o token de acesso (sub = e-mail; inclui role/permissões).
    access_token = auth.create_access_token(
        data={"sub": user.email},
        role=user.role,
        can_schedule_batch=user.can_schedule_batch,
        can_use_publications=user.can_use_publications,
        can_use_prazos_iniciais=getattr(user, "can_use_prazos_iniciais", False),
        can_use_onerequest=getattr(user, "can_use_onerequest", False),
        can_manage_distribuidos_bb=getattr(user, "can_manage_distribuidos_bb", False),
        must_change_password=user.must_change_password,
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "must_change_password": user.must_change_password,
    }


@router.get("/sso/session", response_model=schemas.Token)
def sso_session(request: Request, db: Session = Depends(get_db)):
    """
    Estabelece a sessão a partir da identidade injetada pelo proxy reverso
    (oauth2-proxy + Microsoft Entra ID). Lê o e-mail de um header confiável,
    faz "acha-ou-cria" (JIT) do usuário e devolve o MESMO JWT do login por
    senha. O frontend chama isto no boot quando não há token salvo.

    SEGURANÇA: confia no header de e-mail. Só funciona se
    `settings.sso_header_auth_enabled` for True E o app estiver ATRÁS do proxy
    (Traefik/oauth2-proxy sobrescreve qualquer header forjado pelo cliente).
    NUNCA expor o app publicamente sem o proxy.
    """
    if not settings.sso_header_auth_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SSO desativado")

    # Identidade: 1) header injetado por forward-auth (se houver); senão
    # 2) validação server-side do cookie do oauth2-proxy (caminho atual — não
    # depende de middleware no Traefik).
    email = (request.headers.get(settings.sso_email_header) or "").strip().lower()
    id_token = request.headers.get(settings.sso_id_token_header) or ""
    if not email:
        email, id_token = _validate_via_oauth2_proxy(request.headers.get("cookie") or "")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessão Microsoft não encontrada. Entre pelo botão da Microsoft.",
        )

    # Match case-insensitive pra não duplicar usuário por diferença de caixa.
    user = (
        db.query(LegalOneUser)
        .filter(func.lower(LegalOneUser.email) == email)
        .first()
    )

    if user is None:
        # Provisionamento JIT: cria usuário PENDENTE — sem vínculo com o Legal
        # One (external_id NULL) e SEM nenhuma permissão. O admin libera/vincula
        # depois. A equipe sincronizada do Legal One cai no match acima.
        # Nome do pendente (usado na busca por similaridade): preferimos o
        # claim `name` do ID token (nome completo do Entra); senão o header de
        # nome; por último o local-part do e-mail.
        display_name = (
            _name_from_id_token(id_token)
            or (request.headers.get(settings.sso_name_header) or "").strip()
            or email.split("@")[0]
        )
        user = LegalOneUser(
            email=email,
            name=display_name,
            external_id=None,
            is_active=True,
            hashed_password=None,
            must_change_password=False,
            role="user",
            can_schedule_batch=False,
            can_use_publications=False,
            can_use_prazos_iniciais=False,
        )
        db.add(user)
        try:
            db.commit()
            db.refresh(user)
        except IntegrityError:
            # Corrida: outra request criou o mesmo usuário em paralelo.
            db.rollback()
            user = (
                db.query(LegalOneUser)
                .filter(func.lower(LegalOneUser.email) == email)
                .first()
            )
            if user is None:
                raise

    # Carimba o login SSO (selo "Entra ID" no admin) + zera o "trocar senha
    # provisória": quem entra pelo Entra não usa senha, então esse fluxo não se
    # aplica (senão o usuário trava numa troca de senha que ele não tem).
    user.last_sso_at = datetime.now(timezone.utc)
    user.must_change_password = False
    try:
        db.commit()
    except Exception:
        db.rollback()

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Conta inativa. Contate o administrador.",
        )

    access_token = auth.create_access_token(
        data={"sub": user.email},
        role=user.role,
        can_schedule_batch=user.can_schedule_batch,
        can_use_publications=user.can_use_publications,
        can_use_prazos_iniciais=getattr(user, "can_use_prazos_iniciais", False),
        can_use_onerequest=getattr(user, "can_use_onerequest", False),
        can_manage_distribuidos_bb=getattr(user, "can_manage_distribuidos_bb", False),
        must_change_password=user.must_change_password,
    )
    return {"access_token": access_token, "token_type": "bearer"}
