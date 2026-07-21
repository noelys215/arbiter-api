from __future__ import annotations

from collections.abc import AsyncGenerator
import uuid

from fastapi import Cookie, Depends, HTTPException, status
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.session import get_db_session
from app.models.auth_session import AuthSession
from app.models.user import User

COOKIE_NAME = "access_token"


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_db_session():
        yield session


async def get_user_from_access_token(db: AsyncSession, access_token: str | None) -> User:
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    claims = decode_access_token(access_token)
    if claims is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    sub, jti = claims

    try:
        user_id = uuid.UUID(sub)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token")

    result = await db.execute(
        select(User)
        .join(AuthSession, AuthSession.user_id == User.id)
        .where(
            User.id == user_id,
            AuthSession.jti == jti,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > sa.func.now(),
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid session")

    return user


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    access_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> User:
    return await get_user_from_access_token(db, access_token)


async def get_optional_user(
    db: AsyncSession,
    access_token: str | None,
) -> User | None:
    if not access_token:
        return None
    try:
        return await get_user_from_access_token(db, access_token)
    except HTTPException:
        return None
