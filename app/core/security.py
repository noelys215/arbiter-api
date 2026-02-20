from __future__ import annotations

from datetime import datetime, timedelta, timezone
from jose import ExpiredSignatureError, JWTError, jwt
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


def create_magic_link_token(email: str) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.magic_link_expire_minutes)
    payload = {
        "sub": email.lower().strip(),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "typ": "magic_login",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_magic_link_token(token: str) -> tuple[str | None, str | None]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except ExpiredSignatureError:
        return None, "expired"
    except JWTError:
        return None, "invalid"

    token_type = payload.get("typ")
    subject = payload.get("sub")
    if token_type != "magic_login" or not isinstance(subject, str):
        return None, "invalid"

    email = subject.strip().lower()
    if not email:
        return None, "invalid"
    return email, None
