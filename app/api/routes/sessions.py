from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from uuid import UUID

from app.api.deps import COOKIE_NAME, get_current_user, get_db, get_user_from_access_token
from app.api.http_errors import permission_error, value_error
from app.api.presenters.titles import build_title_out_with_taxonomy
from app.models.watchlist_item import WatchlistItem
from app.models.user import User
from app.models.tonight_session import TonightSession
from app.schemas.sessions import (
    CreateSessionRequest,
    CreateSessionResponse,
    SessionCandidateOut,
    SessionStateResponse,
    VoteRequest,
    WatchPartyUpdateRequest,
)
from app.schemas.watchlist import TitleOut
from app.schemas.session_history import (
    CompletedSessionOut,
    GroupMovieNightPage,
    WatchedStatusUpdateRequest,
)
from app.schemas.mood_cues import MOOD_CUES, MoodCueOut
from app.services.groups import list_group_member_ids
from app.services.session_history import (
    candidate_source_id,
    complete_session,
    completed_session_out,
    get_completed_session,
    list_group_movie_nights,
    mark_watch_party_handoff,
    update_watched_status,
)
from app.services.social_realtime import publish_group_update
from app.schemas.tonight_constraints import TonightConstraints
from app.services.sessions import (
    cast_vote,
    create_tonight_session,
    end_session,
    get_session_state,
    set_session_watch_party_url,
    shuffle_and_complete,
    undo_vote,
)
from app.services.session_realtime import session_realtime_hub
from app.core.websocket_security import reject_disallowed_websocket_origin

router = APIRouter(tags=["sessions"])


def _session_value_error(exc: ValueError, *, include_not_found: bool = False):
    phrase_statuses = {"not found": 404} if include_not_found else None
    return value_error(exc, phrase_statuses=phrase_statuses)


async def _candidate_out(c, *, include_streaming: bool = False) -> SessionCandidateOut:
    if c.title_name and c.source_title_id:
        title = TitleOut(
            id=c.source_title_id,
            source=c.title_source or "manual",
            source_id=c.title_source_id,
            media_type=c.media_type or "movie",
            name=c.title_name,
            release_year=c.release_year,
            poster_path=c.poster_path,
            overview=c.overview,
            runtime_minutes=c.runtime_minutes,
            tmdb_genres=[value for value in (c.genres or []) if isinstance(value, str)],
        )
        if include_streaming and c.watchlist_item is not None:
            title = await build_title_out_with_taxonomy(
                c.watchlist_item.title, include_streaming=True
            )
    elif c.watchlist_item is not None:
        title = await build_title_out_with_taxonomy(
            c.watchlist_item.title, include_streaming=include_streaming
        )
    else:
        raise ValueError("Session candidate snapshot is unavailable")
    return SessionCandidateOut(
        watchlist_item_id=candidate_source_id(c),
        position=c.position,
        reason=c.ai_note,
        title=title,
    )


async def _session_state_response_from_view(view) -> SessionStateResponse:
    s = view.session
    candidates = sorted(view.candidates, key=lambda row: row.position)
    winner_item_id = s.result_watchlist_item_id

    return SessionStateResponse(
        session_id=s.id,
        status=s.status,
        phase=view.phase,
        round=view.round,
        user_locked=view.user_locked,
        user_seconds_left=view.user_seconds_left,
        tie_break_required=view.tie_break_required,
        tie_break_candidate_ids=view.tie_break_candidate_ids,
        ended_by_leader=view.ended_by_leader,
        ends_at=s.ends_at,
        completed_at=s.completed_at,
        result_watchlist_item_id=s.result_watchlist_item_id,
        watch_party_url=s.watch_party_url,
        watch_party_set_at=s.watch_party_set_at,
        watch_party_set_by_user_id=s.watch_party_set_by_user_id,
        mutual_candidate_ids=view.mutual_candidate_ids,
        shortlist=view.shortlist,
        vote_summaries=view.vote_summaries,
        candidates=await asyncio.gather(
            *[
                _candidate_out(
                    c,
                    include_streaming=bool(
                        winner_item_id and candidate_source_id(c) == winner_item_id
                    ),
                )
                for c in candidates
            ]
        ),
    )


@router.post("/groups/{group_id}/sessions", response_model=CreateSessionResponse, status_code=201)
async def create_session_route(
    group_id: UUID,
    payload: CreateSessionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        sess, _, personal_preview_ids = await create_tonight_session(
            db,
            group_id=group_id,
            user_id=user.id,
            constraints_payload=payload.constraints,
            text=payload.text,
            confirm_ready=payload.confirm_ready,
            duration_seconds=payload.duration_seconds,
            candidate_count=payload.candidate_count,
        )
        view = await get_session_state(db, session_id=sess.id, user_id=user.id)
        await db.commit()

        constraints = TonightConstraints.model_validate(sess.constraints)

        candidates_out = await asyncio.gather(
            *[_candidate_out(c) for c in sorted(view.candidates, key=lambda row: row.position)]
        )

        personal_out: list[SessionCandidateOut] = []
        if personal_preview_ids:
            q_items = (
                select(WatchlistItem)
                .options(selectinload(WatchlistItem.title))
                .where(WatchlistItem.id.in_(personal_preview_ids))
            )
            items = (await db.execute(q_items)).scalars().all()
            by_id = {it.id: it for it in items}
            for pos, item_id in enumerate(personal_preview_ids):
                wi = by_id.get(item_id)
                if not wi or not wi.title:
                    continue
                t = wi.title
                personal_out.append(
                    SessionCandidateOut(
                        watchlist_item_id=wi.id,
                        position=pos,
                        reason=None,
                        title=await build_title_out_with_taxonomy(t),
                    )
                )

        response = CreateSessionResponse(
            session_id=sess.id,
            ends_at=sess.ends_at,
            constraints=constraints,
            ai_used=bool(sess.ai_used),
            ai_why=sess.ai_why,
            phase=view.phase,
            round=view.round,
            user_locked=view.user_locked,
            user_seconds_left=view.user_seconds_left,
            tie_break_required=view.tie_break_required,
            tie_break_candidate_ids=view.tie_break_candidate_ids,
            ended_by_leader=view.ended_by_leader,
            candidates=candidates_out,
            personal_candidates=personal_out,
        )
        await session_realtime_hub.broadcast_session_updated(sess.id, reason="session_changed")
        return response

    except PermissionError as e:
        raise permission_error(e) from e
    except ValueError as e:
        raise value_error(e) from e


@router.get("/mood-cues", response_model=list[MoodCueOut])
async def mood_cues_route():
    return list(MOOD_CUES)

@router.post("/sessions/{session_id}/vote", status_code=200)
async def vote_route(
    session_id: UUID,
    payload: VoteRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        await cast_vote(
            db,
            session_id=session_id,
            user_id=user.id,
            watchlist_item_id=payload.watchlist_item_id,
            vote=payload.vote,
        )
        await db.commit()
        await session_realtime_hub.broadcast_session_updated(session_id, reason="vote_cast")
        return {"ok": True}
    except PermissionError as e:
        raise permission_error(e) from e
    except ValueError as e:
        raise _session_value_error(e, include_not_found=True) from e


async def _publish_history_update(
    db: AsyncSession, *, session: TonightSession, reason: str
) -> None:
    group_id = session.group_id
    member_ids = await list_group_member_ids(db, group_id)
    await session_realtime_hub.broadcast_session_updated(session.id, reason=reason)
    await publish_group_update(member_ids, reason=reason, group_id=group_id)


@router.post(
    "/sessions/{session_id}/completion",
    response_model=CompletedSessionOut,
    status_code=200,
)
async def complete_session_route(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        session, changed = await complete_session(
            db, session_id=session_id, user_id=user.id
        )
        await db.commit()
        await db.refresh(
            session,
            attribute_names=["candidates", "participant_snapshots", "vote_snapshots"],
        )
        response = completed_session_out(session)
        if changed:
            await _publish_history_update(
                db, session=session, reason="session_completed"
            )
        return response
    except PermissionError as exc:
        await db.rollback()
        raise permission_error(exc) from exc
    except ValueError as exc:
        await db.rollback()
        raise _session_value_error(exc, include_not_found=True) from exc


@router.get(
    "/sessions/{session_id}/completion", response_model=CompletedSessionOut
)
async def completed_session_route(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        session = await get_completed_session(
            db, session_id=session_id, user_id=user.id
        )
        return completed_session_out(session)
    except PermissionError as exc:
        raise permission_error(exc) from exc
    except ValueError as exc:
        raise _session_value_error(exc, include_not_found=True) from exc


@router.patch(
    "/sessions/{session_id}/completion/watched",
    response_model=CompletedSessionOut,
)
async def update_watched_status_route(
    session_id: UUID,
    payload: WatchedStatusUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        session, changed = await update_watched_status(
            db,
            session_id=session_id,
            user_id=user.id,
            watched_status=payload.status,
        )
        await db.commit()
        response = completed_session_out(session)
        if changed:
            await _publish_history_update(
                db, session=session, reason="session_history_updated"
            )
        return response
    except PermissionError as exc:
        await db.rollback()
        raise permission_error(exc) from exc
    except ValueError as exc:
        await db.rollback()
        raise _session_value_error(exc, include_not_found=True) from exc


@router.post("/sessions/{session_id}/watch-party/handoff", status_code=204)
async def mark_watch_party_handoff_route(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        _, changed = await mark_watch_party_handoff(
            db, session_id=session_id, user_id=user.id
        )
        await db.commit()
        if changed:
            await session_realtime_hub.broadcast_session_updated(
                session_id, reason="watch_party_handoff"
            )
    except PermissionError as exc:
        await db.rollback()
        raise permission_error(exc) from exc
    except ValueError as exc:
        await db.rollback()
        raise _session_value_error(exc, include_not_found=True) from exc


@router.get(
    "/groups/{group_id}/movie-nights", response_model=GroupMovieNightPage
)
async def group_movie_nights_route(
    group_id: UUID,
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return await list_group_movie_nights(
            db,
            group_id=group_id,
            user_id=user.id,
            limit=limit,
            cursor=cursor,
        )
    except PermissionError as exc:
        raise permission_error(exc) from exc


@router.delete("/sessions/{session_id}/vote/{watchlist_item_id}", status_code=200)
async def undo_vote_route(
    session_id: UUID,
    watchlist_item_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        await undo_vote(
            db,
            session_id=session_id,
            user_id=user.id,
            watchlist_item_id=watchlist_item_id,
        )
        await db.commit()
        await session_realtime_hub.broadcast_session_updated(session_id, reason="vote_undone")
        return {"ok": True}
    except PermissionError as e:
        raise permission_error(e) from e
    except ValueError as e:
        raise _session_value_error(e, include_not_found=True) from e


@router.websocket("/sessions/{session_id}/ws")
async def session_updates_ws(websocket: WebSocket, session_id: UUID):
    if await reject_disallowed_websocket_origin(websocket):
        return
    access_token = websocket.cookies.get(COOKIE_NAME)
    async for db in get_db():
        try:
            user = await get_user_from_access_token(db, access_token)
            view = await get_session_state(db, session_id=session_id, user_id=user.id)
        except Exception:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        break

    await session_realtime_hub.connect(
        session_id,
        user.id,
        view.session.group_id,
        websocket,
    )
    try:
        await websocket.send_json(
            {
                "type": "session_connected",
                "session_id": str(session_id),
            }
        )
        while True:
            message = await websocket.receive_json()
            if isinstance(message, dict) and message.get("type") == "ping":
                await websocket.send_json({"type": "pong", "session_id": str(session_id)})
    except WebSocketDisconnect:
        pass
    finally:
        await session_realtime_hub.disconnect(session_id, websocket)


@router.get("/sessions/{session_id}", response_model=SessionStateResponse)
async def session_state_route(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        view = await get_session_state(db, session_id=session_id, user_id=user.id)
        await db.commit()
        return await _session_state_response_from_view(view)
    except PermissionError as e:
        raise permission_error(e) from e
    except ValueError as e:
        raise _session_value_error(e, include_not_found=True) from e


@router.post("/sessions/{session_id}/shuffle", response_model=SessionStateResponse, status_code=200)
async def shuffle_route(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        view = await shuffle_and_complete(db, session_id=session_id, user_id=user.id)
        await db.commit()
        response = await _session_state_response_from_view(view)
        await session_realtime_hub.broadcast_session_updated(session_id, reason="shuffle_completed")
        return response
    except PermissionError as e:
        raise permission_error(e) from e
    except ValueError as e:
        raise _session_value_error(e, include_not_found=True) from e


@router.post("/sessions/{session_id}/end", response_model=SessionStateResponse, status_code=200)
async def end_session_route(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        view = await end_session(db, session_id=session_id, user_id=user.id)
        await db.commit()
        response = await _session_state_response_from_view(view)
        await session_realtime_hub.broadcast_session_updated(session_id, reason="session_ended")
        return response
    except PermissionError as e:
        raise permission_error(e) from e
    except ValueError as e:
        raise _session_value_error(e, include_not_found=True) from e


@router.patch("/sessions/{session_id}/watch-party", response_model=SessionStateResponse, status_code=200)
async def update_watch_party_route(
    session_id: UUID,
    payload: WatchPartyUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        view = await set_session_watch_party_url(
            db,
            session_id=session_id,
            user_id=user.id,
            url=payload.url,
        )
        await db.commit()
        response = await _session_state_response_from_view(view)
        await session_realtime_hub.broadcast_session_updated(session_id, reason="watch_party_updated")
        return response
    except PermissionError as e:
        raise permission_error(e) from e
    except ValueError as e:
        raise _session_value_error(e, include_not_found=True) from e
