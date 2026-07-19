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


async def ensure_account_names_available(
    db: AsyncSession,
    *,
    username: str | None = None,
    display_name: str | None = None,
    exclude_user_id: UUID | None = None,
) -> None:
    checks = (
        (User.username, username, "username_taken"),
        (User.display_name, display_name, "display_name_taken"),
    )
    for column, value, error_code in checks:
        if value is None:
            continue
        query = sa.select(User.id).where(
            sa.func.lower(column) == value.strip().lower()
        )
        if exclude_user_id is not None:
            query = query.where(User.id != exclude_user_id)
        if (await db.execute(query)).scalar_one_or_none() is not None:
            raise ValueError(error_code)


async def generate_unique_display_name(db: AsyncSession, seed: str) -> str:
    base = seed.strip()[:120] or "User"
    candidate = base
    for suffix in range(2, 101):
        existing = await db.execute(
            sa.select(User.id).where(
                sa.func.lower(User.display_name) == candidate.lower()
            )
        )
        if existing.scalar_one_or_none() is None:
            return candidate
        suffix_text = f" ({suffix})"
        candidate = f"{base[: 120 - len(suffix_text)]}{suffix_text}"
    raise RuntimeError("Unable to generate a unique display name")


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

    username_match = (
        await db.execute(
            sa.select(User).where(sa.func.lower(User.username) == normalized)
        )
    ).scalar_one_or_none()
    if username_match is not None:
        return username_match

    return (
        await db.execute(
            sa.select(User).where(sa.func.lower(User.display_name) == normalized)
        )
    ).scalar_one_or_none()


async def update_display_name(
    db: AsyncSession,
    *,
    user: User,
    display_name: str,
) -> User:
    await ensure_account_names_available(
        db,
        display_name=display_name,
        exclude_user_id=user.id,
    )
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
