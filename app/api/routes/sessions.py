from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from uuid import UUID

from app.api.deps import get_current_user, get_db
from app.api.http_errors import permission_error, value_error
from app.api.presenters.titles import build_title_out_with_taxonomy
from app.models.watchlist_item import WatchlistItem
from app.models.user import User
from app.schemas.sessions import (
    CreateSessionRequest,
    CreateSessionResponse,
    SessionCandidateOut,
    SessionStateResponse,
    VoteRequest,
    WatchPartyUpdateRequest,
)
from app.schemas.tonight_constraints import TonightConstraints
from app.services.sessions import (
    cast_vote,
    create_tonight_session,
    end_session,
    get_session_state,
    set_session_watch_party_url,
    shuffle_and_complete,
)

router = APIRouter(tags=["sessions"])


def _session_value_error(exc: ValueError, *, include_not_found: bool = False):
    phrase_statuses = {"not found": 404} if include_not_found else None
    return value_error(exc, phrase_statuses=phrase_statuses)


async def _candidate_out(c, *, include_streaming: bool = False) -> SessionCandidateOut:
    t = c.watchlist_item.title
    return SessionCandidateOut(
        watchlist_item_id=c.watchlist_item_id,
        position=c.position,
        reason=c.ai_note,
        title=await build_title_out_with_taxonomy(t, include_streaming=include_streaming),
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
        candidates=await asyncio.gather(
            *[
                _candidate_out(
                    c,
                    include_streaming=bool(
                        winner_item_id and c.watchlist_item_id == winner_item_id
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

        candidates_out: list[SessionCandidateOut] = []
        for c in sorted(view.candidates, key=lambda row: row.position):
            wi = c.watchlist_item
            t = wi.title
            candidates_out.append(
                SessionCandidateOut(
                    watchlist_item_id=wi.id,
                    position=c.position,
                    reason=c.ai_note,
                    title=await build_title_out_with_taxonomy(t),
                )
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

        return CreateSessionResponse(
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

    except PermissionError as e:
        raise permission_error(e) from e
    except ValueError as e:
        raise value_error(e) from e

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
        return {"ok": True}
    except PermissionError as e:
        raise permission_error(e) from e
    except ValueError as e:
        raise _session_value_error(e, include_not_found=True) from e


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
        return await _session_state_response_from_view(view)
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
        return await _session_state_response_from_view(view)
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
        return await _session_state_response_from_view(view)
    except PermissionError as e:
        raise permission_error(e) from e
    except ValueError as e:
        raise _session_value_error(e, include_not_found=True) from e
