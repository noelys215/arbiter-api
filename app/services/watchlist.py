from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.models.group_membership import GroupMembership
from app.models.title import Title
from app.models.watchlist_item import WatchlistItem
from app.services.tmdb import fetch_tmdb_title_details, fetch_tmdb_title_taxonomy
UNSET = object()


@dataclass
class WatchlistPage:
    items: list[WatchlistItem]
    next_cursor: str | None
    total_count: int

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
        if existing.runtime_minutes is None or not existing.overview:
            details = await fetch_tmdb_title_details(tmdb_id=tmdb_id, media_type=media_type)
            runtime_minutes = details.get("runtime_minutes") if isinstance(details, dict) else None
            overview = details.get("overview") if isinstance(details, dict) else None
            if isinstance(runtime_minutes, int) and runtime_minutes > 0 and existing.runtime_minutes != runtime_minutes:
                existing.runtime_minutes = runtime_minutes
            if isinstance(overview, str) and overview.strip() and not existing.overview:
                existing.overview = overview
        return existing

    details = await fetch_tmdb_title_details(tmdb_id=tmdb_id, media_type=media_type)
    runtime_minutes = details.get("runtime_minutes") if isinstance(details, dict) else None
    overview = details.get("overview") if isinstance(details, dict) else None
    t = Title(
        source=source,
        source_id=source_id,
        media_type=media_type,
        name=name,
        release_year=year,
        poster_path=poster_path,
        overview=overview if isinstance(overview, str) and overview.strip() else None,
        runtime_minutes=runtime_minutes if isinstance(runtime_minutes, int) and runtime_minutes > 0 else None,
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
    q: str | None = None,
    media_type: str | None = None,
    sort: str = "recent",
) -> list[WatchlistItem]:
    await assert_user_in_group(db, group_id, user_id)

    stmt = _build_watchlist_stmt(
        group_id=group_id,
        status=status,
        tonight=tonight,
        q=q,
        media_type=media_type,
        sort=sort,
        include_options=True,
        include_sort=True,
    )
    return (await db.execute(stmt)).scalars().all()


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        value = int(str(cursor).strip())
    except (TypeError, ValueError):
        return 0
    return max(0, value)


def _build_watchlist_stmt(
    *,
    group_id: uuid.UUID,
    status: str | None,
    tonight: bool,
    q: str | None,
    media_type: str | None,
    sort: str,
    include_options: bool,
    include_sort: bool,
):
    stmt = select(WatchlistItem)
    if include_options:
        stmt = stmt.options(
            selectinload(WatchlistItem.title),
            selectinload(WatchlistItem.added_by_user),
        )

    stmt = stmt.where(WatchlistItem.group_id == group_id)
    joined_title = False

    def _ensure_title_join():
        nonlocal stmt, joined_title
        if joined_title:
            return
        stmt = stmt.join(WatchlistItem.title)
        joined_title = True

    if status:
        stmt = stmt.where(WatchlistItem.status == status)

    if tonight:
        now = datetime.now(timezone.utc)
        stmt = stmt.where(WatchlistItem.status == "watchlist").where(
            sa.or_(WatchlistItem.snoozed_until.is_(None), WatchlistItem.snoozed_until <= now)
        )

    search_term = (q or "").strip()
    if search_term:
        _ensure_title_join()
        stmt = stmt.where(Title.name.ilike(f"%{search_term}%"))

    if media_type in {"movie", "tv"}:
        _ensure_title_join()
        stmt = stmt.where(Title.media_type == media_type)

    if include_sort:
        if sort == "alpha":
            _ensure_title_join()
            stmt = stmt.order_by(sa.func.lower(Title.name).asc(), WatchlistItem.id.asc())
        elif sort == "oldest":
            stmt = stmt.order_by(WatchlistItem.created_at.asc(), WatchlistItem.id.asc())
        else:
            stmt = stmt.order_by(WatchlistItem.created_at.desc(), WatchlistItem.id.desc())

    return stmt


async def _filter_watchlist_by_genre(
    *,
    items: list[WatchlistItem],
    genre_id: int,
) -> list[WatchlistItem]:
    if not items:
        return []

    item_to_key: dict[uuid.UUID, tuple[int, str]] = {}
    unique_keys: set[tuple[int, str]] = set()
    for item in items:
        title = item.title
        if not title or title.source != "tmdb" or not title.source_id:
            continue
        try:
            tmdb_id = int(title.source_id)
        except (TypeError, ValueError):
            continue
        key = (tmdb_id, title.media_type)
        item_to_key[item.id] = key
        unique_keys.add(key)

    if not unique_keys:
        return []

    async def _load_genre_ids(key: tuple[int, str]) -> tuple[tuple[int, str], set[int]]:
        tmdb_id, media_type = key
        taxonomy = await fetch_tmdb_title_taxonomy(tmdb_id=tmdb_id, media_type=media_type)
        if isinstance(taxonomy, tuple) and len(taxonomy) == 3:
            _, _, genre_ids = taxonomy
        else:
            genre_ids = set()
        return key, set(genre_ids)

    genre_map = dict(await asyncio.gather(*[_load_genre_ids(key) for key in unique_keys]))
    out: list[WatchlistItem] = []
    for item in items:
        key = item_to_key.get(item.id)
        if not key:
            continue
        if genre_id in genre_map.get(key, set()):
            out.append(item)
    return out


async def list_watchlist_page(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    status: str | None,
    tonight: bool = False,
    q: str | None = None,
    media_type: str | None = None,
    genre_id: int | None = None,
    sort: str = "recent",
    limit: int = 24,
    cursor: str | None = None,
) -> WatchlistPage:
    await assert_user_in_group(db, group_id, user_id)

    offset = _decode_cursor(cursor)
    page_limit = max(1, min(limit, 100))

    if genre_id is None:
        count_base = _build_watchlist_stmt(
            group_id=group_id,
            status=status,
            tonight=tonight,
            q=q,
            media_type=media_type,
            sort=sort,
            include_options=False,
            include_sort=False,
        )
        count_stmt = select(sa.func.count()).select_from(count_base.subquery())
        total_count = int((await db.execute(count_stmt)).scalar() or 0)
        if offset >= total_count:
            return WatchlistPage(items=[], next_cursor=None, total_count=total_count)

        page_stmt = (
            _build_watchlist_stmt(
                group_id=group_id,
                status=status,
                tonight=tonight,
                q=q,
                media_type=media_type,
                sort=sort,
                include_options=True,
                include_sort=True,
            )
            .offset(offset)
            .limit(page_limit + 1)
        )
        rows = (await db.execute(page_stmt)).scalars().all()
        has_more = len(rows) > page_limit
        items = rows[:page_limit]
        next_cursor = str(offset + page_limit) if has_more else None
        return WatchlistPage(items=items, next_cursor=next_cursor, total_count=total_count)

    # Genre filtering is dynamic (from TMDB taxonomy), so we filter in memory.
    base_stmt = _build_watchlist_stmt(
        group_id=group_id,
        status=status,
        tonight=tonight,
        q=q,
        media_type=media_type,
        sort=sort,
        include_options=True,
        include_sort=True,
    )
    base_rows = (await db.execute(base_stmt)).scalars().all()
    filtered = await _filter_watchlist_by_genre(items=base_rows, genre_id=genre_id)
    total_count = len(filtered)
    if offset >= total_count:
        return WatchlistPage(items=[], next_cursor=None, total_count=total_count)

    items = filtered[offset : offset + page_limit]
    next_cursor = str(offset + page_limit) if (offset + page_limit) < total_count else None
    return WatchlistPage(items=items, next_cursor=next_cursor, total_count=total_count)


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
