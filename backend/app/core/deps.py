from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import PermissionDenied, Unauthorized
from app.core.rate_limit import limiter
from app.core.security import decode_access_token
from app.models import User
from app.models.enums import RoleName

ROLE_ORDER = {
    RoleName.VIEWER.value: 0,
    RoleName.OPERATOR.value: 1,
    RoleName.ADMIN.value: 2,
    RoleName.SUPERADMIN.value: 3,
}


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def get_user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


def bearer_token(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("Missing bearer token")
    return authorization.split(" ", 1)[1].strip()


def get_current_user(
    token: str = Depends(bearer_token),
    db: Session = Depends(get_db),
) -> User:
    try:
        payload = decode_access_token(token)
    except Exception as exc:
        raise Unauthorized("Invalid or expired access token") from exc
    if payload.get("type") != "access":
        raise Unauthorized("Invalid token type")
    user = db.get(User, int(payload.get("sub", 0)))
    if not user or not user.is_active:
        raise Unauthorized("User is inactive or missing")
    now = datetime.now(UTC)
    if user.locked_until and user.locked_until.tzinfo is None:
        locked_until = user.locked_until.replace(tzinfo=UTC)
    else:
        locked_until = user.locked_until
    if locked_until and locked_until > now:
        raise PermissionDenied("Account is temporarily locked")
    return user


def require_role(minimum: RoleName):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.is_superuser:
            return user
        if ROLE_ORDER.get(user.role_name, -1) < ROLE_ORDER[minimum.value]:
            raise PermissionDenied(f"Requires {minimum.value} role")
        return user

    return dependency


def require_permission(permission: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.is_superuser:
            return user
        permissions = set(user.role.permissions or [])
        if permission not in permissions and "*" not in permissions:
            raise PermissionDenied(f"Missing permission: {permission}")
        return user

    return dependency


def api_rate_limit(request: Request, authorization: str | None = Header(default=None)) -> None:
    key = f"{get_client_ip(request)}:{request.url.path}"
    allowed, _remaining, retry = limiter.check(key, 120, 60)
    if not allowed:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=429,
            detail={"code": "rate_limited", "message": "Too many requests", "retry_after": retry},
            headers={"Retry-After": str(retry)},
        )
