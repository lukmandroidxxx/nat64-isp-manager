from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_client_ip, get_current_user, get_user_agent
from app.core.rate_limit import limiter
from app.core.routing import APIRouter
from app.models import AuthSession, User
from app.schemas import ChangePasswordRequest, LoginRequest, LogoutRequest, RefreshRequest
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _login_limit(request: Request) -> None:
    ip = get_client_ip(request)
    allowed, _remaining, retry = limiter.check(
        f"login:{ip}", settings.login_rate_limit_per_minute, 60
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "rate_limited", "message": "Too many login attempts", "retry_after": retry},
            headers={"Retry-After": str(retry)},
        )


@router.post("/login")
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    _login_limit(request)
    return auth_service.authenticate(
        db,
        username=payload.username,
        password=payload.password,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post("/refresh")
def refresh(payload: RefreshRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    _login_limit(request)
    return auth_service.refresh_tokens(
        db,
        payload.refresh_token,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post("/logout")
def logout(payload: LogoutRequest, db: Session = Depends(get_db)) -> dict:
    if payload.refresh_token:
        auth_service.revoke_refresh_token(db, payload.refresh_token)
    return {"logged_out": True}


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return auth_service.user_dict(user)


@router.get("/sessions")
def my_sessions(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(
        select(AuthSession)
        .where(AuthSession.user_id == user.id)
        .order_by(AuthSession.created_at.desc())
        .limit(100)
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "created_at": row.created_at,
                "last_used_at": row.last_used_at,
                "expires_at": row.expires_at,
                "ip_address": row.ip_address,
                "user_agent": row.user_agent,
                "revoked_at": row.revoked_at,
                "revoke_reason": row.revoke_reason,
            }
            for row in rows
        ]
    }


@router.post("/sessions/revoke-all")
def revoke_all(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    count = auth_service.revoke_all_sessions(db, user, "user_revoked_all")
    return {"revoked": count}


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    auth_service.change_password(
        db, user, payload.current_password, payload.new_password, request=request
    )
    return {
        "changed": True,
        "message": "Password changed. All refresh sessions were revoked; sign in again.",
    }
