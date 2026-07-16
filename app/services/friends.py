from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.friend_invite import FriendInvite
from app.models.friendship import Friendship
from app.models.user import User
from app.services.invitations import (
    ensure_invite_active,
    hash_invite_token,
    new_invite_token,
    terminate_invite,
)


def _make_code(length: int = 10) -> str:
    # URL-safe, easy to paste; trim to length
    return secrets.token_urlsafe(16).replace("-", "").replace("_", "")[:length]


def _pair(a: UUID, b: UUID) -> tuple[UUID, UUID]:
    return (a, b) if a < b else (b, a)


async def create_friend_invite(db: AsyncSession, user_id, ttl_minutes: int = 60) -> FriendInvite:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)

    # Try a few times to avoid rare code collisions
    for _ in range(10):
        code = _make_code()
        invite = FriendInvite(
            code=code,
            created_by_user_id=user_id,
            expires_at=expires_at,
            max_uses=1,
            uses_count=0,
        )
        db.add(invite)
        try:
            await db.flush()  # will raise on unique collision
            return invite
        except Exception:
            await db.rollback()
            continue

    raise RuntimeError("Failed to generate unique invite code")


async def create_friend_link_invite(
    db: AsyncSession,
    user_id: UUID,
    *,
    ttl_days: int = 7,
) -> tuple[FriendInvite, str]:
    expires_at = datetime.now(timezone.utc) + timedelta(days=ttl_days)
    token, token_hash = new_invite_token()

    for _ in range(10):
        code = _make_code()
        code_exists = (
            await db.execute(select(FriendInvite.id).where(FriendInvite.code == code))
        ).scalar_one_or_none()
        if code_exists is not None:
            continue
        invite = FriendInvite(
            code=code,
            token_hash=token_hash,
            created_by_user_id=user_id,
            expires_at=expires_at,
            max_uses=1,
            uses_count=0,
        )
        db.add(invite)
        await db.flush()
        return invite, token

    raise RuntimeError("Failed to generate unique invite code")


async def get_friend_invite_by_token(
    db: AsyncSession,
    token: str,
    *,
    lock: bool = False,
) -> FriendInvite:
    query = select(FriendInvite).where(
        FriendInvite.token_hash == hash_invite_token(token)
    )
    if lock:
        query = query.with_for_update()
    invite = (await db.execute(query)).scalar_one_or_none()
    if invite is None:
        raise ValueError("invalid_invite")
    ensure_invite_active(invite)
    return invite


async def preview_friend_invite(db: AsyncSession, token: str) -> tuple[FriendInvite, User]:
    invite = await get_friend_invite_by_token(db, token)
    inviter = (
        await db.execute(select(User).where(User.id == invite.created_by_user_id))
    ).scalar_one()
    return invite, inviter


async def _accept_friend_invite_record(
    db: AsyncSession,
    current_user_id: UUID,
    invite: FriendInvite,
) -> bool:
    ensure_invite_active(invite)
    if invite.created_by_user_id == current_user_id:
        raise ValueError("cannot_friend_self")

    low, high = _pair(invite.created_by_user_id, current_user_id)
    existing = (
        await db.execute(
            select(Friendship).where(
                and_(Friendship.user_low_id == low, Friendship.user_high_id == high)
            )
        )
    ).scalar_one_or_none()
    if existing:
        return True
    if invite.uses_count >= invite.max_uses:
        raise ValueError("used_invite")

    db.add(Friendship(user_low_id=low, user_high_id=high))
    invite.uses_count += 1
    await db.flush()
    return False


async def accept_friend_invite(db: AsyncSession, current_user_id: UUID, code: str) -> bool:
    # Lock the invite row so uses_count is atomic
    invite = (
        await db.execute(
            select(FriendInvite)
            .where(FriendInvite.code == code)
            .with_for_update()
        )
    ).scalar_one_or_none()

    if not invite:
        raise ValueError("invalid_code")
    try:
        return await _accept_friend_invite_record(db, current_user_id, invite)
    except ValueError as exc:
        code_map = {
            "expired_invite": "expired_code",
            "revoked_invite": "revoked_code",
            "used_invite": "used_code",
        }
        raise ValueError(code_map.get(str(exc), str(exc))) from exc


async def accept_friend_link_invite(
    db: AsyncSession,
    current_user_id: UUID,
    token: str,
) -> bool:
    invite = await get_friend_invite_by_token(db, token, lock=True)
    return await _accept_friend_invite_record(db, current_user_id, invite)


async def revoke_friend_invite(
    db: AsyncSession,
    current_user_id: UUID,
    invite_id: UUID,
) -> None:
    invite = (
        await db.execute(
            select(FriendInvite).where(FriendInvite.id == invite_id).with_for_update()
        )
    ).scalar_one_or_none()
    if invite is None:
        raise ValueError("invalid_invite")
    if invite.created_by_user_id != current_user_id:
        raise PermissionError("Only the invite creator can revoke this invitation")
    terminate_invite(invite)
    await db.flush()


async def list_friends(db: AsyncSession, current_user_id):
    # friendship row can contain you in either low/high
    f = Friendship
    u = aliased(User)

    q = (
        select(u)
        .join(
            f,
            ((f.user_low_id == current_user_id) & (u.id == f.user_high_id))
            | ((f.user_high_id == current_user_id) & (u.id == f.user_low_id)),
        )
        .order_by(u.username.asc())
    )

    rows = (await db.execute(q)).scalars().all()
    return rows


async def unfriend(db: AsyncSession, current_user_id: UUID, other_user_id: UUID) -> None:
    if current_user_id == other_user_id:
        raise ValueError("cannot_unfriend_self")

    low, high = _pair(current_user_id, other_user_id)
    existing = (
        await db.execute(
            select(Friendship).where(
                and_(Friendship.user_low_id == low, Friendship.user_high_id == high)
            )
        )
    ).scalar_one_or_none()

    if not existing:
        raise ValueError("not_found")

    await db.delete(existing)
