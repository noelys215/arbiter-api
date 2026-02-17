from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.watchlist import AddWatchlistRequest, WatchlistItemOut, WatchlistPatchRequest, TitleOut
from app.services.tmdb import fetch_tmdb_title_taxonomy
from app.services.watchlist import (
    add_watchlist_item_tmdb,
    add_watchlist_item_manual,
    list_watchlist,
    patch_watchlist_item,
    UNSET
)

router = APIRouter(tags=["watchlist"])


async def _title_out_with_taxonomy(t) -> TitleOut:
    tmdb_genres: list[str] = []
    tmdb_genre_ids: list[int] = []
    if t.source == "tmdb" and t.source_id:
        try:
            tmdb_id = int(t.source_id)
        except (TypeError, ValueError):
            tmdb_id = None
        if tmdb_id is not None:
            genres, _, genre_ids = await fetch_tmdb_title_taxonomy(
                tmdb_id=tmdb_id,
                media_type=t.media_type,
            )
            tmdb_genres = sorted(genres)
            tmdb_genre_ids = sorted(genre_ids)

    return TitleOut(
        id=t.id,
        source=t.source,
        source_id=t.source_id,
        media_type=t.media_type,
        name=t.name,
        release_year=t.release_year,
        poster_path=t.poster_path,
        overview=t.overview,
        runtime_minutes=t.runtime_minutes,
        tmdb_genres=tmdb_genres,
        tmdb_genre_ids=tmdb_genre_ids,
    )


async def to_out(item, already_exists: bool = False) -> WatchlistItemOut:
    t = item.title
    u = item.added_by_user
    title_out = await _title_out_with_taxonomy(t)
    return WatchlistItemOut(
        id=item.id,
        group_id=item.group_id,
        added_by_user=(
            {
                "id": u.id,
                "email": u.email,
                "username": u.username,
                "display_name": u.display_name,
                "avatar_url": u.avatar_url,
            }
            if u
            else None
        ),
        status=item.status,
        snoozed_until=item.snoozed_until,
        created_at=item.created_at,
        title=title_out,
        already_exists=already_exists,
    )


@router.post("/groups/{group_id}/watchlist", response_model=WatchlistItemOut, status_code=201)
async def add_watchlist_route(
    group_id: UUID,
    payload: AddWatchlistRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        if payload.type == "tmdb":
            item, already = await add_watchlist_item_tmdb(
                db,
                group_id=group_id,
                user_id=user.id,
                tmdb_id=payload.tmdb_id,
                media_type=payload.media_type,
                title=payload.title,
                year=payload.year,
                poster_path=payload.poster_path,
            )
            await db.commit()
            return await to_out(item, already_exists=already)

        # manual
        item = await add_watchlist_item_manual(
            db,
            group_id=group_id,
            user_id=user.id,
            title=payload.title,
            media_type=payload.media_type,
            year=payload.year,
            poster_path=payload.poster_path,
            overview=getattr(payload, "overview", None),
        )
        await db.commit()
        return await to_out(item, already_exists=False)

    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/groups/{group_id}/watchlist", response_model=list[WatchlistItemOut])
async def list_watchlist_route(
    group_id: UUID,
    status: str | None = Query(default=None, pattern="^(watchlist|watched)$"),
    tonight: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        items = await list_watchlist(db, group_id=group_id, user_id=user.id, status=status, tonight=tonight)
        return await asyncio.gather(*[to_out(i) for i in items])
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.patch("/watchlist-items/{item_id}", response_model=dict, status_code=200)
async def patch_watchlist_route(
    item_id: UUID,
    payload: WatchlistPatchRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        # IMPORTANT: only update fields the client actually sent
        data = payload.model_dump(exclude_unset=True)

        snoozed_arg = data["snoozed_until"] if "snoozed_until" in data else UNSET

        removed = await patch_watchlist_item(
            db,
            item_id=item_id,
            user_id=user.id,
            status=data.get("status"),
            snoozed_until=snoozed_arg,
            remove=data.get("remove"),
)
        await db.commit()
        return {"ok": True, "removed": bool(removed)}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
