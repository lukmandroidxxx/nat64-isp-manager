from __future__ import annotations

from types import SimpleNamespace

import jwt
import pytest

from app.core.config import settings
from app.core.security import (
    constant_time_compare,
    create_access_token,
    decode_access_token,
    decrypt_secret,
    encrypt_secret,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    validate_password_policy,
    verify_password,
)


def test_password_hash_roundtrip_and_wrong_password():
    digest = hash_password("CorrectHorse1!")
    assert digest.startswith("$argon2")
    assert verify_password("CorrectHorse1!", digest) is True
    assert verify_password("WrongHorse1!", digest) is False
    assert verify_password("CorrectHorse1!", "not-a-hash") is False


def test_password_policy_accepts_strong_and_reports_weak():
    assert validate_password_policy("Str0ngPassword!") == []
    errors = validate_password_policy("abc")
    joined = " ".join(errors)
    assert "at least" in joined
    assert "uppercase" in joined
    assert "digit" in joined
    assert "symbol" in joined


def test_access_token_roundtrip_and_expiry_claims():
    user = SimpleNamespace(id=42, username="alice", role_name="operator")
    token, expires_in = create_access_token(user)
    assert expires_in == settings.access_token_expire_minutes * 60
    payload = decode_access_token(token)
    assert payload["sub"] == "42"
    assert payload["username"] == "alice"
    assert payload["role"] == "operator"
    assert payload["type"] == "access"
    assert payload["exp"] > payload["iat"]


def test_access_token_rejects_tampering_and_wrong_key():
    user = SimpleNamespace(id=1, username="bob", role_name="viewer")
    token, _ = create_access_token(user)
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(token + "x")
    forged = jwt.encode({"sub": "1", "type": "access"}, "a-different-secret-key-12345678901234567890", algorithm="HS256")
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(forged)


def test_refresh_tokens_are_opaque_and_hashed_deterministically():
    token = new_refresh_token()
    assert len(token) >= 48
    assert token != new_refresh_token()
    assert hash_refresh_token(token) == hash_refresh_token(token)
    assert token not in hash_refresh_token(token)


def test_secret_encryption_roundtrip_and_garbage():
    ciphertext = encrypt_secret("vrrp-super-secret")
    assert ciphertext != "vrrp-super-secret"
    assert decrypt_secret(ciphertext) == "vrrp-super-secret"
    assert decrypt_secret("not-a-fernet-token") is None
    assert decrypt_secret(None) is None


def test_constant_time_compare():
    assert constant_time_compare("abc", "abc") is True
    assert constant_time_compare("abc", "abd") is False
