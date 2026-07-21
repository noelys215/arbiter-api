from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.api.deps import COOKIE_NAME, get_current_user, get_db, get_user_from_access_token
from app.api.http_errors import permission_error, value_error
from app.api.presenters.titles import build_title_out_with_taxonomy
from app.api.presenters.users import public_user_from_user
from app.models.user import User
from app.models.watchlist_item import WatchlistItem
from app.schemas.watchlist import (
    AddWatchlistRequest,
    WatchlistItemOut,
    WatchlistPageOut,
    WatchlistPatchRequest,
)
from app.services.watchlist import (
    UNSET,
    add_watchlist_item_manual,
    add_watchlist_item_tmdb,
    assert_user_in_group,
    list_watchlist,
    list_watchlist_page,
    patch_watchlist_item,
)
from app.services.watchlist_realtime import watchlist_realtime_hub
from app.core.websocket_security import reject_disallowed_websocket_origin

router = APIRouter(tags=["watchlist"])


async def to_out(item, already_exists: bool = False) -> WatchlistItemOut:
    t = item.title
    u = item.added_by_user
    title_out = await build_title_out_with_taxonomy(t)
    return WatchlistItemOut(
        id=item.id,
        group_id=item.group_id,
        added_by_user=public_user_from_user(u) if u else None,
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
            out = await to_out(item, already_exists=already)
            if not already:
                await watchlist_realtime_hub.broadcast_watchlist_updated(
                    group_id,
                    reason="item_added",
                )
            return out

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
        out = await to_out(item, already_exists=False)
        await watchlist_realtime_hub.broadcast_watchlist_updated(
            group_id,
            reason="item_added",
        )
        return out

    except PermissionError as e:
        raise permission_error(e) from e
    except ValueError as e:
        raise value_error(e) from e


@router.websocket("/groups/{group_id}/watchlist/ws")
async def watchlist_updates_ws(websocket: WebSocket, group_id: UUID):
    if await reject_disallowed_websocket_origin(websocket):
        return
    access_token = websocket.cookies.get(COOKIE_NAME)
    async for db in get_db():
        try:
            user = await get_user_from_access_token(db, access_token)
            await assert_user_in_group(db, group_id, user.id)
        except Exception:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        break

    if not await watchlist_realtime_hub.connect(group_id, user.id, websocket):
        return
    try:
        await websocket.send_json(
            {
                "type": "watchlist_connected",
                "group_id": str(group_id),
            }
        )
        while True:
            message = await websocket.receive_json()
            if message == {"type": "ping"}:
                await websocket.send_json({"type": "pong", "group_id": str(group_id)})
            else:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
    except WebSocketDisconnect:
        pass
    except (ValueError, UnicodeDecodeError):
        await websocket.close(code=status.WS_1003_UNSUPPORTED_DATA)
    finally:
        await watchlist_realtime_hub.disconnect(group_id, websocket)


@router.get("/groups/{group_id}/watchlist", response_model=list[WatchlistItemOut] | WatchlistPageOut)
async def list_watchlist_route(
    group_id: UUID,
    status: str | None = Query(default=None, pattern="^(watchlist|watched)$"),
    tonight: bool = Query(default=False),
    q: str | None = Query(default=None),
    media_type: str | None = Query(default=None, pattern="^(movie|tv)$"),
    genre_id: int | None = Query(default=None, ge=1),
    sort: str = Query(default="recent", pattern="^(recent|oldest|alpha)$"),
    limit: int = Query(default=24, ge=1, le=100),
    cursor: str | None = Query(default=None),
    paginate: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        if paginate:
            page = await list_watchlist_page(
                db,
                group_id=group_id,
                user_id=user.id,
                status=status,
                tonight=tonight,
                q=q,
                media_type=media_type,
                genre_id=genre_id,
                sort=sort,
                limit=limit,
                cursor=cursor,
            )
            items_out = await asyncio.gather(*[to_out(i) for i in page.items])
            return WatchlistPageOut(
                items=items_out,
                next_cursor=page.next_cursor,
                total_count=page.total_count,
            )

        items = await list_watchlist(
            db,
            group_id=group_id,
            user_id=user.id,
            status=status,
            tonight=tonight,
            q=q,
            media_type=media_type,
            sort=sort,
        )
        return await asyncio.gather(*[to_out(i) for i in items])
    except PermissionError as e:
        raise permission_error(e) from e


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
        item_group_id = (
            await db.execute(select(WatchlistItem.group_id).where(WatchlistItem.id == item_id))
        ).scalar_one_or_none()

        removed = await patch_watchlist_item(
            db,
            item_id=item_id,
            user_id=user.id,
            status=data.get("status"),
            snoozed_until=snoozed_arg,
            remove=data.get("remove"),
        )
        await db.commit()
        if item_group_id is not None:
            await watchlist_realtime_hub.broadcast_watchlist_updated(
                item_group_id,
                reason="item_removed" if removed else "item_updated",
            )
        return {"ok": True, "removed": bool(removed)}
    except PermissionError as e:
        raise permission_error(e) from e
    except ValueError as e:
        raise value_error(e, phrase_statuses={"not found": 404}) from e
