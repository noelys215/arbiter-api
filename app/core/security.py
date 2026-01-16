from __future__ import annotations

from datetime import datetime, timedelta, timezone
from jose import jwt
import bcrypt

from app.core.config import settings


_BCRYPT_MAX_BYTES = 72


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


def create_access_token(subject: str) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.access_token_expire_minutes)

    payload = {
        "sub": subject,                  # user id
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
