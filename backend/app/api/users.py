from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_permission
from app.core.routing import APIRouter
from app.models import User
from app.schemas import UserCreate, UserUpdate
from app.services import auth as auth_service

router = APIRouter(prefix="/users", tags=["users"])
roles_router = APIRouter(prefix="/roles", tags=["users"])

READ = require_permission("users.write")


@router.get("")
def list_users(
    _: User = Depends(READ),
    db: Session = Depends(get_db),
) -> dict:
    return {"items": auth_service.list_users(db)}


@router.post("", status_code=201)
def create_user(
    payload: UserCreate,
    actor: User = Depends(READ),
    db: Session = Depends(get_db),
) -> dict:
    user = auth_service.create_user(
        db,
        username=payload.username,
        password=payload.password,
        role=payload.role,
        email=payload.email,
        full_name=payload.full_name,
        is_active=payload.is_active,
        actor=actor,
    )
    return auth_service.user_dict(user)


@router.get("/{user_id}")
def get_user(
    user_id: int,
    _: User = Depends(READ),
    db: Session = Depends(get_db),
) -> dict:
    return auth_service.user_dict(auth_service.get_user(db, user_id))


@router.patch("/{user_id}")
def update_user(
    user_id: int,
    payload: UserUpdate,
    actor: User = Depends(READ),
    db: Session = Depends(get_db),
) -> dict:
    changes = payload.model_dump(exclude_unset=True)
    user = auth_service.update_user(db, user_id, actor=actor, **changes)
    return auth_service.user_dict(user)


@roles_router.get("")
def list_roles(
    _: User = Depends(READ),
    db: Session = Depends(get_db),
) -> dict:
    return {"items": auth_service.list_roles(db)}
