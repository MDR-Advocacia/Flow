from datetime import datetime, timedelta, timezone
from typing import Optional
import secrets
import string

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.api.v1.schemas import TokenData
from app.core.config import settings
from app.core.dependencies import get_db
from app.models.legal_one import LegalOneUser
from typing import Literal

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def generate_temp_password(length: int = 12) -> str:
    """
    Generate a temporary random password.
    Excludes confusing characters like 0/O and 1/l.
    """
    # Remove confusing characters: 0, O, l, 1, I
    alphabet = string.ascii_letters + string.digits
    alphabet = alphabet.replace('0', '').replace('O', '').replace('l', '').replace('1', '').replace('I', '')
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def validate_password(pwd: str) -> None:
    """
    Validate password strength.
    Raises HTTPException if password is invalid.
    """
    if len(pwd) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Senha deve ter no mínimo 8 caracteres.",
        )


def create_access_token(
    data: dict,
    role: str = "user",
    can_schedule_batch: bool = False,
    can_use_publications: bool = True,
    can_use_prazos_iniciais: bool = False,
    can_use_onerequest: bool = False,
    can_manage_distribuidos_bb: bool = False,
    must_change_password: bool = False,
    expires_delta: Optional[timedelta] = None
) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    to_encode.update({
        "exp": expire,
        "role": role,
        "can_schedule_batch": can_schedule_batch,
        "can_use_publications": can_use_publications,
        "can_use_prazos_iniciais": can_use_prazos_iniciais,
        "can_use_onerequest": can_use_onerequest,
        "can_manage_distribuidos_bb": can_manage_distribuidos_bb,
        "must_change_password": must_change_password,
    })
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


def get_current_user(
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
) -> LegalOneUser:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Nao foi possivel validar as credenciais",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        username = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except JWTError as exc:
        raise credentials_exception from exc

    user = db.query(LegalOneUser).filter(LegalOneUser.email == token_data.username).first()
    if user is None or not user.is_active:
        raise credentials_exception

    return user


def require_permission(
    permission: Literal[
        "schedule_batch", "publications", "prazos_iniciais", "onerequest",
        "manage_distribuidos_bb",
    ],
):
    """
    Dependency factory to check if user has specific permission.
    Usage: Depends(require_permission("schedule_batch"))
    """
    async def check_permission(current_user: LegalOneUser = Depends(get_current_user)):
        # Admins bypass all permission checks
        if getattr(current_user, "role", "user") == "admin":
            return current_user
        if permission == "schedule_batch":
            if not current_user.can_schedule_batch:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Usuário não tem permissão para agendar automatizações.",
                )
        elif permission == "publications":
            if not current_user.can_use_publications:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Usuário não tem permissão para usar publicações.",
                )
        elif permission == "prazos_iniciais":
            if not getattr(current_user, "can_use_prazos_iniciais", False):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Usuário não tem permissão para usar Prazos Iniciais.",
                )
        elif permission == "onerequest":
            if not getattr(current_user, "can_use_onerequest", False):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Usuário não tem permissão para usar o OneRequest.",
                )
        elif permission == "manage_distribuidos_bb":
            if not getattr(current_user, "can_manage_distribuidos_bb", False):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Usuário não tem permissão para o módulo Distribuídos BB.",
                )
        return current_user

    return check_permission
