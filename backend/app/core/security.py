from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from cryptography.fernet import Fernet, InvalidToken
from pwdlib import PasswordHash

from app.core.config import settings

password_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password, password_hash)
    except Exception:
        return False


def validate_password_policy(password: str) -> list[str]:
    errors: list[str] = []
    if len(password) < settings.password_min_length:
        errors.append(f"Password must be at least {settings.password_min_length} characters")
    if not any(c.isupper() for c in password):
        errors.append("Password must contain an uppercase letter")
    if not any(c.islower() for c in password):
        errors.append("Password must contain a lowercase letter")
    if not any(c.isdigit() for c in password):
        errors.append("Password must contain a digit")
    if not any(c in "!@#$%^&*()-_=+[]{};:,.?/\\|~`" for c in password):
        errors.append("Password must contain a symbol")
    return errors


def _now() -> datetime:
    return datetime.now(UTC)


def create_access_token(user: Any, extra: dict | None = None) -> tuple[str, int]:
    expires_in = settings.access_token_expire_minutes * 60
    now = _now()
    payload: dict[str, Any] = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role_name,
        "type": "access",
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
        "jti": secrets.token_urlsafe(12),
    }
    if extra:
        payload.update(extra)
    token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
    return token, expires_in


def decode_access_token(token: str) -> dict:
    return jwt.decode(
        token,
        settings.secret_key,
        algorithms=["HS256"],
        options={"require": ["exp", "sub", "iat", "type"]},
    )


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _fernet() -> Fernet:
    if settings.app_encryption_key:
        raw = settings.app_encryption_key.encode()
        try:
            return Fernet(raw)
        except ValueError:
            pass
        key = hashlib.sha256(raw).digest()
        import base64

        return Fernet(base64.urlsafe_b64encode(key))
    # Development fallback is deterministic; production startup warns loudly.
    key = hashlib.sha256(settings.secret_key.encode()).digest()
    import base64

    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def constant_time_compare(a: str, b: str) -> bool:
    return secrets.compare_digest(a, b)
