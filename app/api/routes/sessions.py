from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from uuid import UUID

from app.api.deps import get_current_user, get_db
from app.models.tonight_session_candidate import TonightSessionCandidate
from app.models.watchlist_item import WatchlistItem
from app.models.user import User
from app.schemas.sessions import (
    CreateSessionRequest,
    CreateSessionResponse,
    SessionCandidateOut,
    SessionStateResponse,
    VoteRequest,
)
from app.schemas.tonight_constraints import TonightConstraints
from app.schemas.watchlist import TitleOut
from app.services.sessions import (
    cast_vote,
    create_tonight_session,
    end_session,
    get_session_state,
    shuffle_and_complete,
)

router = APIRouter(tags=["sessions"])


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
                    title=TitleOut(
                        id=t.id,
                        source=t.source,
                        source_id=t.source_id,
                        media_type=t.media_type,
                        name=t.name,
                        release_year=t.release_year,
                        poster_path=t.poster_path,
                        overview=t.overview,
                        runtime_minutes=t.runtime_minutes,
                    ),
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
                        title=TitleOut(
                            id=t.id,
                            source=t.source,
                            source_id=t.source_id,
                            media_type=t.media_type,
                            name=t.name,
                            release_year=t.release_year,
                            poster_path=t.poster_path,
                            overview=t.overview,
                            runtime_minutes=t.runtime_minutes,
                        ),
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
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _candidate_out(c) -> SessionCandidateOut:
    t = c.watchlist_item.title
    return SessionCandidateOut(
        watchlist_item_id=c.watchlist_item_id,
        position=c.position,
        reason=c.ai_note,
        title=TitleOut(
            id=t.id,
            source=t.source,
            source_id=t.source_id,
            media_type=t.media_type,
            name=t.name,
            release_year=t.release_year,
            poster_path=t.poster_path,
            overview=t.overview,
            runtime_minutes=t.runtime_minutes,
        ),
    )


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
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        msg = str(e).lower()
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/sessions/{session_id}", response_model=SessionStateResponse)
async def session_state_route(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        view = await get_session_state(db, session_id=session_id, user_id=user.id)
        await db.commit()
        s = view.session
        candidates = sorted(view.candidates, key=lambda x: x.position)
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
            mutual_candidate_ids=view.mutual_candidate_ids,
            shortlist=view.shortlist,
            candidates=[_candidate_out(c) for c in candidates],
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        msg = str(e).lower()
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/sessions/{session_id}/shuffle", response_model=SessionStateResponse, status_code=200)
async def shuffle_route(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        view = await shuffle_and_complete(db, session_id=session_id, user_id=user.id)
        await db.commit()
        s = view.session
        candidates = sorted(view.candidates, key=lambda x: x.position)
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
            mutual_candidate_ids=view.mutual_candidate_ids,
            shortlist=view.shortlist,
            candidates=[_candidate_out(c) for c in candidates],
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        msg = str(e).lower()
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/sessions/{session_id}/end", response_model=SessionStateResponse, status_code=200)
async def end_session_route(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        view = await end_session(db, session_id=session_id, user_id=user.id)
        await db.commit()
        s = view.session
        candidates = sorted(view.candidates, key=lambda x: x.position)
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
            mutual_candidate_ids=view.mutual_candidate_ids,
            shortlist=view.shortlist,
            candidates=[_candidate_out(c) for c in candidates],
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        msg = str(e).lower()
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
