from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.models.group_membership import GroupMembership
from app.models.title import Title
from app.models.watchlist_item import WatchlistItem
UNSET = object()

async def assert_user_in_group(db: AsyncSession, group_id: uuid.UUID, user_id: uuid.UUID) -> None:
    q = select(GroupMembership.id).where(
        GroupMembership.group_id == group_id,
        GroupMembership.user_id == user_id,
    )
    if (await db.execute(q)).scalar_one_or_none() is None:
        raise PermissionError("Not a member of this group")


async def upsert_tmdb_title(
    db: AsyncSession,
    *,
    tmdb_id: int,
    media_type: str,
    name: str,
    year: int | None,
    poster_path: str | None,
) -> Title:
    source = "tmdb"
    source_id = str(tmdb_id)

    # Try fetch
    q = select(Title).where(
        Title.source == source,
        Title.source_id == source_id,
        Title.media_type == media_type,
    )
    existing = (await db.execute(q)).scalar_one_or_none()
    if existing:
        # Update only if new data is present (don’t blank out fields)
        if name and existing.name != name:
            existing.name = name
        if year is not None and existing.release_year != year:
            existing.release_year = year
        if poster_path is not None and existing.poster_path != poster_path:
            existing.poster_path = poster_path
        return existing

    t = Title(
        source=source,
        source_id=source_id,
        media_type=media_type,
        name=name,
        release_year=year,
        poster_path=poster_path,
    )
    db.add(t)
    await db.flush()  # get id
    return t


async def create_manual_title(
    db: AsyncSession,
    *,
    name: str,
    media_type: str,
    year: int | None,
    poster_path: str | None,
    overview: str | None,
) -> Title:
    t = Title(
        source="manual",
        source_id=None,
        media_type=media_type,
        name=name,
        release_year=year,
        poster_path=poster_path,
        overview=overview,
    )
    db.add(t)
    await db.flush()
    return t


async def add_watchlist_item_tmdb(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    tmdb_id: int,
    media_type: str,
    title: str,
    year: int | None,
    poster_path: str | None,
) -> tuple[WatchlistItem, bool]:
    await assert_user_in_group(db, group_id, user_id)

    t = await upsert_tmdb_title(
        db,
        tmdb_id=tmdb_id,
        media_type=media_type,
        name=title,
        year=year,
        poster_path=poster_path,
    )

    item = WatchlistItem(group_id=group_id, title_id=t.id)
    item.added_by_user_id = user_id
    db.add(item)

    try:
        await db.flush()
        already_exists = False
    except IntegrityError:
        await db.rollback()

        # Re-fetch title in a clean transaction state
        q_title = select(Title).where(
            Title.source == "tmdb",
            Title.source_id == str(tmdb_id),
            Title.media_type == media_type,
        )
        t2 = (await db.execute(q_title)).scalar_one()

        q_item = (
            select(WatchlistItem)
            .options(
                selectinload(WatchlistItem.title),
                selectinload(WatchlistItem.added_by_user),
            )
            .where(WatchlistItem.group_id == group_id, WatchlistItem.title_id == t2.id)
        )
        existing = (await db.execute(q_item)).scalar_one()
        return existing, True

    await db.refresh(item, attribute_names=["title", "added_by_user"])
    already_exists = False
    return item, already_exists


async def add_watchlist_item_manual(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    title: str,
    media_type: str,
    year: int | None,
    poster_path: str | None,
    overview: str | None,
) -> WatchlistItem:
    await assert_user_in_group(db, group_id, user_id)

    t = await create_manual_title(
        db,
        name=title,
        media_type=media_type,
        year=year,
        poster_path=poster_path,
        overview=overview,
    )
    item = WatchlistItem(group_id=group_id, title_id=t.id)
    item.added_by_user_id = user_id
    db.add(item)
    await db.flush()
    await db.refresh(item, attribute_names=["title", "added_by_user"])
    return item


async def list_watchlist(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    status: str | None,
    tonight: bool = False,
) -> list[WatchlistItem]:
    await assert_user_in_group(db, group_id, user_id)

    q = (
        select(WatchlistItem)
        .options(
            selectinload(WatchlistItem.title),
            selectinload(WatchlistItem.added_by_user),
        )
        .where(WatchlistItem.group_id == group_id)
        .order_by(WatchlistItem.created_at.desc())
    )

    if status:
        q = q.where(WatchlistItem.status == status)

    if tonight:
        now = datetime.now(timezone.utc)
        q = q.where(WatchlistItem.status == "watchlist").where(
            sa.or_(WatchlistItem.snoozed_until.is_(None), WatchlistItem.snoozed_until <= now)
        )

    return (await db.execute(q)).scalars().all()


async def patch_watchlist_item(
    db: AsyncSession,
    *,
    item_id: uuid.UUID,
    user_id: uuid.UUID,
    status: str | None,
    snoozed_until=UNSET,  
    remove: bool | None,
) -> bool:
    q = select(WatchlistItem).where(WatchlistItem.id == item_id)
    item = (await db.execute(q)).scalar_one_or_none()
    if not item:
        raise ValueError("Not found")

    await assert_user_in_group(db, item.group_id, user_id)

    if remove:
        await db.delete(item)
        return True

    changed = False

    if status is not None:
        item.status = status
        changed = True

    # Only update snooze if client actually sent the field
    if snoozed_until is not UNSET:
        item.snoozed_until = snoozed_until  # can be datetime or None (unsnooze)
        changed = True

    return changed
