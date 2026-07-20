from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.friend_invite import FriendInvite
from app.models.friendship import Friendship
from app.models.group_invite import GroupInvite
from app.models.user import User
from app.models.user_block import UserBlock


@dataclass(frozen=True)
class BlockUserResult:
    target_user_id: UUID
    changed: bool
    friendship_removed: bool
    friend_requests_closed: bool
    affected_group_ids: tuple[UUID, ...]


def _friend_pair(first: UUID, second: UUID) -> tuple[UUID, UUID]:
    return (first, second) if first < second else (second, first)


async def users_are_blocked(
    db: AsyncSession, first_user_id: UUID, second_user_id: UUID
) -> bool:
    block_id = (
        await db.execute(
            sa.select(UserBlock.id).where(
                sa.or_(
                    sa.and_(
                        UserBlock.blocker_user_id == first_user_id,
                        UserBlock.blocked_user_id == second_user_id,
                    ),
                    sa.and_(
                        UserBlock.blocker_user_id == second_user_id,
                        UserBlock.blocked_user_id == first_user_id,
                    ),
                )
            )
        )
    ).scalar_one_or_none()
    return block_id is not None


async def list_blocked_users(
    db: AsyncSession, blocker_user_id: UUID
) -> list[tuple[UserBlock, User]]:
    blocked_user = aliased(User)
    rows = await db.execute(
        sa.select(UserBlock, blocked_user)
        .join(blocked_user, blocked_user.id == UserBlock.blocked_user_id)
        .where(UserBlock.blocker_user_id == blocker_user_id)
        .order_by(UserBlock.created_at.desc())
    )
    return list(rows.all())


async def block_user(
    db: AsyncSession, blocker_user_id: UUID, blocked_user_id: UUID
) -> BlockUserResult:
    if blocker_user_id == blocked_user_id:
        raise ValueError("cannot_block_self")
    target_exists = (
        await db.execute(sa.select(User.id).where(User.id == blocked_user_id))
    ).scalar_one_or_none()
    if target_exists is None:
        raise ValueError("user_not_found")

    existing = (
        await db.execute(
            sa.select(UserBlock).where(
                UserBlock.blocker_user_id == blocker_user_id,
                UserBlock.blocked_user_id == blocked_user_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return BlockUserResult(blocked_user_id, False, False, False, ())

    try:
        async with db.begin_nested():
            db.add(
                UserBlock(
                    blocker_user_id=blocker_user_id,
                    blocked_user_id=blocked_user_id,
                )
            )
            await db.flush()
    except IntegrityError:
        return BlockUserResult(blocked_user_id, False, False, False, ())

    low, high = _friend_pair(blocker_user_id, blocked_user_id)
    friendship = (
        await db.execute(
            sa.select(Friendship).where(
                Friendship.user_low_id == low,
                Friendship.user_high_id == high,
            )
        )
    ).scalar_one_or_none()
    friendship_removed = friendship is not None
    if friendship is not None:
        await db.delete(friendship)

    now = datetime.now(timezone.utc)
    pair_key = f"{low}:{high}"
    friend_invites = (
        await db.execute(
            sa.select(FriendInvite).where(
                FriendInvite.pair_key == pair_key,
                FriendInvite.revoked_at.is_(None),
                FriendInvite.uses_count == 0,
            )
        )
    ).scalars().all()
    for invite in friend_invites:
        invite.revoked_at = now

    group_invites = (
        await db.execute(
            sa.select(GroupInvite).where(
                GroupInvite.revoked_at.is_(None),
                GroupInvite.uses_count == 0,
                sa.or_(
                    sa.and_(
                        GroupInvite.created_by_user_id == blocker_user_id,
                        GroupInvite.target_user_id == blocked_user_id,
                    ),
                    sa.and_(
                        GroupInvite.created_by_user_id == blocked_user_id,
                        GroupInvite.target_user_id == blocker_user_id,
                    ),
                ),
            )
        )
    ).scalars().all()
    affected_group_ids = tuple({invite.group_id for invite in group_invites})
    for invite in group_invites:
        invite.revoked_at = now

    await db.flush()
    return BlockUserResult(
        blocked_user_id,
        True,
        friendship_removed,
        bool(friend_invites),
        affected_group_ids,
    )


async def unblock_user(
    db: AsyncSession, blocker_user_id: UUID, blocked_user_id: UUID
) -> bool:
    block = (
        await db.execute(
            sa.select(UserBlock).where(
                UserBlock.blocker_user_id == blocker_user_id,
                UserBlock.blocked_user_id == blocked_user_id,
            )
        )
    ).scalar_one_or_none()
    if block is None:
        return False
    await db.delete(block)
    await db.flush()
    return True
