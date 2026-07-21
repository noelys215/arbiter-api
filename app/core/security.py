from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import secrets

import bcrypt
import jwt
from jwt.exceptions import InvalidTokenError

from app.core.config import settings


_BCRYPT_MAX_BYTES = 72
_JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    if not isinstance(password, str):
        raise TypeError("password must be a string")

    password_bytes = password.encode("utf-8")
    # bcrypt max: 72 bytes; safest is to enforce pre-validation in schema,
    # but we guard here too to avoid 500s.
    if len(password_bytes) > _BCRYPT_MAX_BYTES:
        raise ValueError("password must be 72 bytes or fewer")

    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if not isinstance(password, str) or not isinstance(password_hash, str):
        return False
    password_bytes = password.encode("utf-8")
    if len(password_bytes) > _BCRYPT_MAX_BYTES:
        return False
    return bcrypt.checkpw(password_bytes, password_hash.encode("utf-8"))


def create_access_token(
    subject: str,
    *,
    jti: str,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    now = now or datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.access_token_expire_minutes)

    payload = {
        "sub": subject,
        "jti": jti,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=_JWT_ALGORITHM)
    return token, expire


def decode_access_token(token: str) -> tuple[str, str] | None:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[_JWT_ALGORITHM],
            options={"require": ["sub", "jti", "type", "iat", "exp"]},
        )
    except InvalidTokenError:
        return None

    subject = payload.get("sub")
    jti = payload.get("jti")
    if (
        payload.get("type") != "access"
        or not isinstance(subject, str)
        or not subject
        or not isinstance(jti, str)
        or not jti
    ):
        return None
    return subject, jti


def generate_auth_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_auth_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()
