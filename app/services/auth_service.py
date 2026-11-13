from __future__ import annotations

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import JWT_EXPIRE_MINUTES
from app.database import get_db
from app.models import User
from app.schemas import TokenResponse, UserOut
from app.services.audit_service import record_audit
from app.utils.security import create_access_token, decode_access_token, hash_password, verify_password

_bearer_scheme = HTTPBearer(auto_error=False)


def user_to_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        login_id=user.login_id,
        full_name=user.full_name,
        role=user.role,
        is_demo=user.is_demo,
    )


def authenticate_user(db: Session, login_id: str, password: str) -> TokenResponse:
    """Validate login_id and password, then issue a signed JWT."""
    user = db.scalar(select(User).where(User.login_id == login_id.strip()))
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid login ID or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(user_id=user.id, login_id=user.login_id, role=user.role)
    record_audit(db, actor=user.login_id, entity_type="session", entity_id=user.id, action="login")
    db.commit()
    return TokenResponse(
        access_token=token,
        expires_in=JWT_EXPIRE_MINUTES * 60,
        user=user_to_out(user),
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Decode Bearer JWT and return the active user, or raise 401."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please sign in.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid token. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is inactive or missing.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def change_user_password(db: Session, user: User, current_password: str, new_password: str) -> None:
    """Update a user's password after verifying their current password."""
    if not verify_password(current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect.")
    if current_password == new_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New password must differ from current password.")
    user.password_hash = hash_password(new_password)
    record_audit(db, actor=user.login_id, entity_type="user", entity_id=user.id, action="change_password")
    db.commit()


def require_admin(user: User) -> User:
    """Enforce admin-role access control for privileged office operations."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator role required for this action.",
        )
    return user

