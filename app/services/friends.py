from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.friend_invite import FriendInvite
from app.models.friendship import Friendship
from app.models.user import User


def _make_code(length: int = 10) -> str:
    # URL-safe, easy to paste; trim to length
    return secrets.token_urlsafe(16).replace("-", "").replace("_", "")[:length]


def _pair(a: str, b: str) -> tuple[str, str]:
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


async def accept_friend_invite(db: AsyncSession, current_user_id, code: str) -> None:
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

    now = datetime.now(timezone.utc)
    if invite.expires_at <= now:
        raise ValueError("expired_code")

    if invite.uses_count >= invite.max_uses:
        raise ValueError("used_code")

    if str(invite.created_by_user_id) == str(current_user_id):
        raise ValueError("cannot_friend_self")

    low, high = _pair(str(invite.created_by_user_id), str(current_user_id))

    existing = (
        await db.execute(
            select(Friendship).where(
                and_(Friendship.user_low_id == low, Friendship.user_high_id == high)
            )
        )
    ).scalar_one_or_none()

    if existing:
        # still consume invite? I'd say yes, because it was used.
        invite.uses_count += 1
        await db.flush()
        return

    db.add(Friendship(user_low_id=low, user_high_id=high))
    invite.uses_count += 1
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


async def unfriend(db: AsyncSession, current_user_id, other_user_id) -> None:
    if str(current_user_id) == str(other_user_id):
        raise ValueError("cannot_unfriend_self")

    low, high = _pair(str(current_user_id), str(other_user_id))
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
