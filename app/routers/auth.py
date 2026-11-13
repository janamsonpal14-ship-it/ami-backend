from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    TokenResponse,
    UserAdminOut,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.services.audit_service import record_audit
from app.services.auth_service import (
    authenticate_user,
    change_user_password,
    get_current_user,
    require_admin,
    user_to_out,
)
from app.utils.security import hash_password

router = APIRouter(prefix="/api/auth", tags=["auth"])
users_router = APIRouter(prefix="/api/users", tags=["users"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    return authenticate_user(db, payload.login_id, payload.password)


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return user_to_out(current_user)


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    change_user_password(db, current_user, payload.current_password, payload.new_password)
    return {"status": "ok", "message": "Password updated successfully."}


@router.post("/logout")
def logout(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    record_audit(db, actor=current_user.login_id, entity_type="session", entity_id=current_user.id, action="logout")
    db.commit()
    return {"status": "ok"}


@users_router.get("", response_model=list[UserAdminOut])
def list_users(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_admin(current_user)
    users = list(db.scalars(select(User).order_by(User.id)).all())
    return [
        UserAdminOut(
            id=u.id,
            login_id=u.login_id,
            full_name=u.full_name,
            role=u.role,
            is_active=u.is_active,
            is_demo=u.is_demo,
            created_at=u.created_at,
        )
        for u in users
    ]


@users_router.post("", response_model=UserAdminOut)
def create_user(
    payload: UserCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_admin(current_user)
    clean_login = payload.login_id.strip()
    if db.scalar(select(User).where(User.login_id == clean_login)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Login ID '{clean_login}' already exists.")
    u = User(
        login_id=clean_login,
        full_name=payload.full_name.strip(),
        role=payload.role,
        password_hash=hash_password(payload.password),
        is_active=True,
        is_demo=False,
    )
    db.add(u)
    db.flush()
    record_audit(db, actor=current_user.login_id, entity_type="user", entity_id=u.id, action="create", after={"login_id": u.login_id, "role": u.role})
    db.commit()
    db.refresh(u)
    return UserAdminOut(
        id=u.id,
        login_id=u.login_id,
        full_name=u.full_name,
        role=u.role,
        is_active=u.is_active,
        is_demo=u.is_demo,
        created_at=u.created_at,
    )


@users_router.patch("/{user_id}", response_model=UserAdminOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_admin(current_user)
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    if u.id == current_user.id and payload.is_active is False:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="You cannot deactivate your own active session account.")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(u, k, v)
    record_audit(db, actor=current_user.login_id, entity_type="user", entity_id=u.id, action="update", after={"role": u.role, "is_active": u.is_active})
    db.commit()
    db.refresh(u)
    return UserAdminOut(
        id=u.id,
        login_id=u.login_id,
        full_name=u.full_name,
        role=u.role,
        is_active=u.is_active,
        is_demo=u.is_demo,
        created_at=u.created_at,
    )

