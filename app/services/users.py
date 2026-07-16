from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.friendship import Friendship
from app.models.group_membership import GroupMembership
from app.models.user import User


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
