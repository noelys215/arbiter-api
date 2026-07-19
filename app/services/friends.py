from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID
import sqlalchemy as sa
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


@dataclass(frozen=True)
class FriendRequestCreationResult:
    invite: FriendInvite | None
    target_user_id: UUID | None
    changed: bool


@dataclass(frozen=True)
class FriendRequestDecisionResult:
    invite: FriendInvite
    inviter_user_id: UUID
    target_user_id: UUID
    changed: bool
    already_friends: bool


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


async def create_friend_request(
    db: AsyncSession,
    current_user_id: UUID,
    email: str,
    *,
    ttl_days: int = 7,
) -> FriendRequestCreationResult:
    normalized_email = email.strip().casefold()
    target = (
        await db.execute(
            select(User).where(sa.func.lower(User.email) == normalized_email)
        )
    ).scalar_one_or_none()

    # Keep account lookup private: an unknown address behaves like a successful no-op.
    if target is None:
        return FriendRequestCreationResult(None, None, False)
    if target.id == current_user_id:
        raise ValueError("cannot_friend_self")

    low, high = _pair(current_user_id, target.id)
    friendship = (
        await db.execute(
            select(Friendship.id).where(
                Friendship.user_low_id == low,
                Friendship.user_high_id == high,
            )
        )
    ).scalar_one_or_none()
    if friendship is not None:
        raise ValueError("already_friends")

    now = datetime.now(timezone.utc)
    pending = (
        await db.execute(
            select(FriendInvite.id).where(
                FriendInvite.target_user_id.is_not(None),
                FriendInvite.revoked_at.is_(None),
                FriendInvite.expires_at > now,
                FriendInvite.uses_count < FriendInvite.max_uses,
                sa.or_(
                    sa.and_(
                        FriendInvite.created_by_user_id == current_user_id,
                        FriendInvite.target_user_id == target.id,
                    ),
                    sa.and_(
                        FriendInvite.created_by_user_id == target.id,
                        FriendInvite.target_user_id == current_user_id,
                    ),
                ),
            )
        )
    ).scalar_one_or_none()
    if pending is not None:
        raise ValueError("request_already_pending")

    expires_at = now + timedelta(days=ttl_days)
    for _ in range(10):
        code = _make_code()
        code_exists = (
            await db.execute(select(FriendInvite.id).where(FriendInvite.code == code))
        ).scalar_one_or_none()
        if code_exists is not None:
            continue
        invite = FriendInvite(
            code=code,
            created_by_user_id=current_user_id,
            target_user_id=target.id,
            expires_at=expires_at,
            max_uses=1,
            uses_count=0,
        )
        db.add(invite)
        await db.flush()
        return FriendRequestCreationResult(invite, target.id, True)

    raise RuntimeError("Failed to generate unique friend request")


async def list_friend_requests(
    db: AsyncSession,
    current_user_id: UUID,
) -> tuple[list[tuple[FriendInvite, User]], list[tuple[FriendInvite, User]]]:
    now = datetime.now(timezone.utc)
    active = (
        FriendInvite.target_user_id.is_not(None),
        FriendInvite.revoked_at.is_(None),
        FriendInvite.expires_at > now,
        FriendInvite.uses_count < FriendInvite.max_uses,
    )
    incoming = (
        await db.execute(
            select(FriendInvite, User)
            .join(User, User.id == FriendInvite.created_by_user_id)
            .where(*active, FriendInvite.target_user_id == current_user_id)
            .order_by(FriendInvite.created_at.desc())
        )
    ).all()
    outgoing = (
        await db.execute(
            select(FriendInvite, User)
            .join(User, User.id == FriendInvite.target_user_id)
            .where(*active, FriendInvite.created_by_user_id == current_user_id)
            .order_by(FriendInvite.created_at.desc())
        )
    ).all()
    return list(incoming), list(outgoing)


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
    if invite.target_user_id is not None and invite.target_user_id != current_user_id:
        raise PermissionError("This friend request belongs to another user")
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


async def decide_friend_request(
    db: AsyncSession,
    current_user_id: UUID,
    invite_id: UUID,
    decision: str,
) -> FriendRequestDecisionResult:
    invite = (
        await db.execute(
            select(FriendInvite)
            .where(FriendInvite.id == invite_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if invite is None or invite.target_user_id is None:
        raise ValueError("request_not_found")
    if invite.target_user_id != current_user_id:
        raise PermissionError("This friend request belongs to another user")

    if decision == "accept":
        low, high = _pair(invite.created_by_user_id, current_user_id)
        existing = (
            await db.execute(
                select(Friendship.id).where(
                    Friendship.user_low_id == low,
                    Friendship.user_high_id == high,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            request_was_active = (
                invite.revoked_at is None
                and invite.expires_at > datetime.now(timezone.utc)
                and invite.uses_count < invite.max_uses
            )
            if request_was_active:
                terminate_invite(invite)
                await db.flush()
            return FriendRequestDecisionResult(
                invite=invite,
                inviter_user_id=invite.created_by_user_id,
                target_user_id=current_user_id,
                changed=request_was_active,
                already_friends=True,
            )

    ensure_invite_active(invite)

    if decision == "decline":
        terminate_invite(invite)
        await db.flush()
        return FriendRequestDecisionResult(
            invite=invite,
            inviter_user_id=invite.created_by_user_id,
            target_user_id=current_user_id,
            changed=True,
            already_friends=False,
        )

    already_friends = await _accept_friend_invite_record(
        db, current_user_id, invite
    )
    return FriendRequestDecisionResult(
        invite=invite,
        inviter_user_id=invite.created_by_user_id,
        target_user_id=current_user_id,
        changed=not already_friends,
        already_friends=already_friends,
    )


async def cancel_friend_request(
    db: AsyncSession,
    current_user_id: UUID,
    invite_id: UUID,
) -> FriendRequestDecisionResult:
    invite = (
        await db.execute(
            select(FriendInvite)
            .where(FriendInvite.id == invite_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if invite is None or invite.target_user_id is None:
        raise ValueError("request_not_found")
    if invite.created_by_user_id != current_user_id:
        raise PermissionError("Only the sender can cancel this friend request")
    ensure_invite_active(invite)
    terminate_invite(invite)
    await db.flush()
    return FriendRequestDecisionResult(
        invite=invite,
        inviter_user_id=current_user_id,
        target_user_id=invite.target_user_id,
        changed=True,
        already_friends=False,
    )


async def accept_friend_invite(
    db: AsyncSession, current_user_id: UUID, code: str
) -> tuple[bool, UUID]:
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
        already_friends = await _accept_friend_invite_record(db, current_user_id, invite)
        return already_friends, invite.created_by_user_id
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
) -> tuple[bool, UUID]:
    invite = await get_friend_invite_by_token(db, token, lock=True)
    already_friends = await _accept_friend_invite_record(db, current_user_id, invite)
    return already_friends, invite.created_by_user_id


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
