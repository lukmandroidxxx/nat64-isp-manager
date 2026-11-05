from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import BACKEND_DIR, settings
from app.core.errors import Conflict, NotFound, PermissionDenied, Unauthorized, ValidationFailed
from app.core.security import (
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    validate_password_policy,
    verify_password,
)
from app.models import AuthSession, Role, User
from app.models.enums import RoleName
from app.services.audit import write_audit

log = logging.getLogger(__name__)

ROLE_PERMISSIONS: dict[str, list[str]] = {
    RoleName.VIEWER.value: [
        "read",
    ],
    RoleName.OPERATOR.value: [
        "read",
        "pools.write",
        "prefixes.write",
        "clients.write",
        "sessions.export",
        "changes.request",
        "backups.create",
        "dns64.write",
        "network.write",
    ],
    RoleName.ADMIN.value: [
        "read",
        "pools.write",
        "prefixes.write",
        "clients.write",
        "sessions.export",
        "changes.request",
        "changes.approve",
        "changes.execute",
        "users.write",
        "backups.create",
        "backups.restore",
        "backups.delete",
        "settings.write",
        "dns64.write",
        "firewall.write",
        "network.write",
        "jool.write",
        "audit.read",
    ],
    RoleName.SUPERADMIN.value: ["*"],
}


def _now() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def ensure_roles(db: Session) -> dict[str, Role]:
    roles: dict[str, Role] = {}
    for name, permissions in ROLE_PERMISSIONS.items():
        role = db.scalar(select(Role).where(Role.name == name))
        if not role:
            role = Role(name=name, description=name.capitalize(), permissions=permissions)
            db.add(role)
            db.flush()
        else:
            role.permissions = permissions
        roles[name] = role
    return roles


def bootstrap(db: Session) -> dict:
    """Idempotent first-run bootstrap. Never overwrites an existing admin password."""
    from app.models import (
        Dns64Settings,
        FirewallSettings,
        HaSettings,
        InstallationState,
        NetworkSettings,
    )

    roles = ensure_roles(db)
    created_admin = False
    temp_password = None
    admin = db.scalar(select(User).where(User.username == settings.default_admin_username))
    if not admin and settings.bootstrap_admin:
        temp_password = settings.default_admin_password
        if not temp_password:
            # Guarantee policy compliance without logging a low-entropy value.
            temp_password = f"{secrets.token_urlsafe(18)}Aa1!"
        errors = validate_password_policy(temp_password)
        if errors:
            raise ValidationFailed("DEFAULT_ADMIN_PASSWORD does not satisfy the password policy", errors)
        admin = User(
            username=settings.default_admin_username,
            email=settings.default_admin_email or None,
            full_name="Initial Administrator",
            password_hash=hash_password(temp_password),
            role_id=roles[RoleName.SUPERADMIN.value].id,
            is_active=True,
            is_superuser=True,
            must_change_password=not bool(settings.default_admin_password),
            password_changed_at=_now(),
        )
        db.add(admin)
        db.flush()
        created_admin = True
        if not settings.default_admin_password:
            path = Path(BACKEND_DIR) / "data" / "bootstrap-admin.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "NAT64 ISP Manager initial administrator\n"
                f"username: {settings.default_admin_username}\n"
                f"password: {temp_password}\n"
                "Change this password after the first login and delete this file.\n",
                encoding="utf-8",
            )
            try:
                path.chmod(0o600)
            except OSError:
                pass
            log.warning(
                "Generated a bootstrap administrator password. Read it from %s and change it after login.", path
            )

    if not db.get(NetworkSettings, 1):
        network = NetworkSettings(
            id=1,
            nat64_prefix="64:ff9b::/96",
            dns_server=None,
            mtu=1500,
            ipv4_forwarding=True,
            ipv6_forwarding=True,
        )
        db.add(network)
    if not db.get(Dns64Settings, 1):
        db.add(
            Dns64Settings(
                id=1,
                enabled=True,
                prefix="64:ff9b::/96",
                upstreams=list(settings.dns_default_upstreams),
                listen_address=settings.dns_listen_address,
                service_name=settings.dns_service_name,
                managed_config_path=settings.dns_managed_config,
                test_domain=settings.dns_test_domain,
                exclude_prefixes=["64:ff9b::/96", "fe80::/10", "::1/128"],
                forward_only=True,
                dnssec_validation=True,
            )
        )
    if not db.get(FirewallSettings, 1):
        db.add(
            FirewallSettings(
                id=1,
                enabled=False,
                default_forward_policy="accept",
                default_input_policy="accept",
                management_allowlist=[],
                management_ports=[22, 443],
                protect_control_plane=True,
            )
        )
    if not db.get(InstallationState, 1):
        db.add(InstallationState(id=1, current_step=1, completed=False, steps={}, last_check={}))
    if not db.get(HaSettings, 1):
        db.add(HaSettings(id=1, enabled=settings.ha_enabled, priority=100))
    db.flush()
    return {
        "roles": sorted(roles),
        "admin_created": created_admin,
        "admin_username": settings.default_admin_username if created_admin else None,
        "generated_password_file": str(Path(BACKEND_DIR) / "data" / "bootstrap-admin.txt")
        if created_admin and not settings.default_admin_password
        else None,
    }


def issue_tokens(
    db: Session,
    user: User,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict:
    access, expires_in = create_access_token(user)
    refresh = new_refresh_token()
    session = AuthSession(
        user_id=user.id,
        refresh_token_hash=hash_refresh_token(refresh),
        ip_address=ip_address,
        user_agent=(user_agent or "")[:512] or None,
        created_at=_now(),
        last_used_at=_now(),
        expires_at=_now() + timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(session)
    db.flush()
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": expires_in,
        "refresh_expires_in": settings.refresh_token_expire_days * 86400,
        "user": user_dict(user),
    }


def user_dict(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role_name,
        "is_active": user.is_active,
        "is_superuser": user.is_superuser,
        "must_change_password": user.must_change_password,
        "last_login_at": user.last_login_at,
        "last_login_ip": user.last_login_ip,
        "permissions": sorted(set(user.role.permissions or [])) if user.role else [],
        "created_at": user.created_at,
    }


def authenticate(
    db: Session,
    *,
    username: str,
    password: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict:
    user = db.scalar(select(User).where(func.lower(User.username) == username.strip().lower()))
    if not user:
        # Perform a dummy hash to reduce user-enumeration timing differences.
        verify_password(password, hash_password("not-a-real-password"))
        write_audit(
            db,
            action="auth.login",
            username=username[:64],
            success=False,
            detail={"reason": "unknown_user", "ip": ip_address},
        )
        db.commit()
        raise Unauthorized("Invalid username or password")
    if not user.is_active:
        write_audit(
            db,
            action="auth.login",
            user=user,
            success=False,
            detail={"reason": "inactive"},
        )
        db.commit()
        raise PermissionDenied("Account is disabled")
    locked_until = as_utc(user.locked_until)
    if locked_until and locked_until > _now():
        write_audit(
            db,
            action="auth.login",
            user=user,
            success=False,
            detail={"reason": "locked", "locked_until": locked_until.isoformat()},
        )
        db.commit()
        raise PermissionDenied(
            f"Account is locked until {locked_until.isoformat()} after repeated failed logins"
        )
    if not verify_password(password, user.password_hash):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        if user.failed_login_count >= settings.login_max_failures:
            user.locked_until = _now() + timedelta(minutes=settings.login_lockout_minutes)
            user.failed_login_count = 0
        write_audit(
            db,
            action="auth.login",
            user=user,
            success=False,
            detail={"reason": "bad_password", "failures": user.failed_login_count},
        )
        db.commit()
        raise Unauthorized("Invalid username or password")
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = _now()
    user.last_login_ip = ip_address
    tokens = issue_tokens(db, user, ip_address=ip_address, user_agent=user_agent)
    write_audit(
        db,
        action="auth.login",
        user=user,
        success=True,
        detail={"ip": ip_address},
    )
    db.flush()
    return tokens


def refresh_tokens(db: Session, refresh_token: str, *, ip_address: str | None = None, user_agent: str | None = None) -> dict:
    token_hash = hash_refresh_token(refresh_token)
    session = db.scalar(select(AuthSession).where(AuthSession.refresh_token_hash == token_hash))
    if not session:
        raise Unauthorized("Invalid refresh token")
    if session.revoked_at:
        raise Unauthorized("Refresh token has been revoked")
    if as_utc(session.expires_at) < _now():
        raise Unauthorized("Refresh token has expired")
    user = db.get(User, session.user_id)
    if not user or not user.is_active:
        raise Unauthorized("User is inactive or missing")
    # Rotate: revoke the old session and issue a new opaque token.
    session.revoked_at = _now()
    session.revoke_reason = "rotated"
    db.flush()
    tokens = issue_tokens(db, user, ip_address=ip_address, user_agent=user_agent)
    write_audit(db, action="auth.refresh", user=user, detail={"rotated": True})
    db.flush()
    return tokens


def revoke_refresh_token(db: Session, refresh_token: str, user: User | None = None, reason: str = "logout") -> None:
    token_hash = hash_refresh_token(refresh_token)
    session = db.scalar(select(AuthSession).where(AuthSession.refresh_token_hash == token_hash))
    if not session:
        return
    if user is not None and session.user_id != user.id:
        return
    session.revoked_at = _now()
    session.revoke_reason = reason
    db.flush()


def revoke_all_sessions(db: Session, user: User, reason: str = "password_changed") -> int:
    count = 0
    for session in db.scalars(
        select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
    ).all():
        session.revoked_at = _now()
        session.revoke_reason = reason
        count += 1
    return count


def change_password(db: Session, user: User, current_password: str, new_password: str, *, request=None) -> None:
    if not verify_password(current_password, user.password_hash):
        raise Unauthorized("Current password is incorrect")
    errors = validate_password_policy(new_password)
    if errors:
        raise ValidationFailed("Password does not satisfy the policy", errors)
    if verify_password(new_password, user.password_hash):
        raise ValidationFailed("New password must differ from the current password")
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.password_changed_at = _now()
    revoked = revoke_all_sessions(db, user)
    write_audit(
        db,
        action="auth.change_password",
        user=user,
        request=request,
        detail={"sessions_revoked": revoked},
    )
    db.flush()


# ---------------------------------------------------------------------------
# user management
# ---------------------------------------------------------------------------


def get_user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if not user:
        raise NotFound("User", user_id)
    return user


def list_users(db: Session) -> list[dict]:
    return [user_dict(user) for user in db.scalars(select(User).order_by(User.username)).all()]


def create_user(
    db: Session,
    *,
    username: str,
    password: str,
    role: str,
    email: str | None = None,
    full_name: str | None = None,
    is_active: bool = True,
    actor: User | None = None,
) -> User:
    username = username.strip()
    if len(username) < 3 or len(username) > 64 or not all(c.isalnum() or c in "._-" for c in username):
        raise ValidationFailed("Username must be 3-64 characters of letters, digits, dot, underscore or hyphen")
    if db.scalar(select(User).where(func.lower(User.username) == username.lower())):
        raise Conflict("Username already exists")
    role_record = db.scalar(select(Role).where(Role.name == role))
    if not role_record:
        raise ValidationFailed(f"Unknown role: {role}")
    if role == RoleName.SUPERADMIN.value and actor is not None and not actor.is_superuser:
        raise PermissionDenied("Only a superadmin can create another superadmin")
    errors = validate_password_policy(password)
    if errors:
        raise ValidationFailed("Password does not satisfy the policy", errors)
    if email and db.scalar(select(User).where(User.email == email)):
        raise Conflict("Email already exists")
    user = User(
        username=username,
        email=email,
        full_name=full_name,
        password_hash=hash_password(password),
        role_id=role_record.id,
        is_active=is_active,
        is_superuser=role == RoleName.SUPERADMIN.value,
        must_change_password=True,
        password_changed_at=_now(),
    )
    db.add(user)
    db.flush()
    write_audit(
        db,
        action="user.create",
        user=actor,
        resource_type="user",
        resource_id=user.id,
        detail={"username": username, "role": role},
    )
    return user


def update_user(
    db: Session,
    user_id: int,
    *,
    actor: User,
    email: str | None = None,
    full_name: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
    password: str | None = None,
) -> User:
    user = get_user(db, user_id)
    if role is not None and role != user.role_name:
        if user.id == actor.id:
            raise ValidationFailed("You cannot change your own role")
        if role == RoleName.SUPERADMIN.value and not actor.is_superuser:
            raise PermissionDenied("Only a superadmin can grant superadmin")
        if user.role_name == RoleName.SUPERADMIN.value and not actor.is_superuser:
            raise PermissionDenied("Only a superadmin can modify a superadmin")
        role_record = db.scalar(select(Role).where(Role.name == role))
        if not role_record:
            raise ValidationFailed(f"Unknown role: {role}")
        user.role_id = role_record.id
        user.is_superuser = role == RoleName.SUPERADMIN.value
    if email is not None:
        duplicate = db.scalar(select(User).where(User.email == email, User.id != user.id))
        if duplicate:
            raise Conflict("Email already exists")
        user.email = email
    if full_name is not None:
        user.full_name = full_name
    if is_active is not None:
        if user.id == actor.id and not is_active:
            raise ValidationFailed("You cannot disable your own account")
        user.is_active = is_active
    if password:
        errors = validate_password_policy(password)
        if errors:
            raise ValidationFailed("Password does not satisfy the policy", errors)
        user.password_hash = hash_password(password)
        user.must_change_password = True
        user.password_changed_at = _now()
        revoke_all_sessions(db, user, "admin_password_reset")
    write_audit(
        db,
        action="user.update",
        user=actor,
        resource_type="user",
        resource_id=user.id,
        detail={"role": user.role_name, "is_active": user.is_active, "password_reset": bool(password)},
    )
    db.flush()
    return user


def list_roles(db: Session) -> list[dict]:
    return [
        {"id": role.id, "name": role.name, "description": role.description, "permissions": role.permissions}
        for role in db.scalars(select(Role).order_by(Role.name)).all()
    ]
