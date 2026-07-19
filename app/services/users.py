from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.friendship import Friendship
from app.models.group_membership import GroupMembership
from app.models.user import User


async def username_exists(db: AsyncSession, username: str) -> bool:
    result = await db.execute(
        sa.select(User.id).where(
            sa.func.lower(User.username) == username.strip().lower()
        )
    )
    return result.scalar_one_or_none() is not None


async def ensure_username_available(
    db: AsyncSession,
    *,
    username: str,
) -> None:
    query = sa.select(User.id).where(
        sa.func.lower(User.username) == username.strip().lower()
    )
    if (await db.execute(query)).scalar_one_or_none() is not None:
        raise ValueError("username_taken")


async def find_user_by_friend_identifier(
    db: AsyncSession,
    identifier: str,
) -> User | None:
    normalized = identifier.strip().lower()
    if not normalized:
        return None

    if normalized.startswith("@"):
        username = normalized[1:]
        if not username:
            return None
        return (
            await db.execute(
                sa.select(User).where(sa.func.lower(User.username) == username)
            )
        ).scalar_one_or_none()

    if "@" in normalized:
        return (
            await db.execute(
                sa.select(User).where(sa.func.lower(User.email) == normalized)
            )
        ).scalar_one_or_none()

    return (
        await db.execute(
            sa.select(User).where(sa.func.lower(User.username) == normalized)
        )
    ).scalar_one_or_none()


async def update_display_name(
    db: AsyncSession,
    *,
    user: User,
    display_name: str,
) -> User:
    user.display_name = display_name
    await db.flush()
    return user


async def list_profile_update_recipient_ids(
    db: AsyncSession,
    user_id: UUID,
) -> set[UUID]:
    recipients = {user_id}

    friendships = await db.execute(
        sa.select(Friendship.user_low_id, Friendship.user_high_id).where(
            sa.or_(
                Friendship.user_low_id == user_id,
                Friendship.user_high_id == user_id,
            )
        )
    )
    for low_id, high_id in friendships.all():
        recipients.add(high_id if low_id == user_id else low_id)

    group_ids = sa.select(GroupMembership.group_id).where(
        GroupMembership.user_id == user_id
    )
    group_members = await db.execute(
        sa.select(GroupMembership.user_id).where(
            GroupMembership.group_id.in_(group_ids)
        )
    )
    recipients.update(group_members.scalars().all())
    return recipients
