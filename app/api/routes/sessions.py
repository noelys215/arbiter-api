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
from app.schemas.sessions import CreateSessionRequest, CreateSessionResponse, SessionCandidateOut
from app.schemas.tonight_constraints import TonightConstraints
from app.schemas.watchlist import TitleOut
from app.services.sessions import create_tonight_session

router = APIRouter(prefix="/groups", tags=["sessions"])


@router.post("/{group_id}/sessions", response_model=CreateSessionResponse, status_code=201)
async def create_session_route(
    group_id: UUID,
    payload: CreateSessionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        sess, _ = await create_tonight_session(
            db,
            group_id=group_id,
            user_id=user.id,
            constraints_payload=payload.constraints,
            text=payload.text,
            duration_seconds=payload.duration_seconds,
            candidate_count=payload.candidate_count,
        )
        await db.commit()

        # Load frozen deck in final order
        q = (
            select(TonightSessionCandidate)
            .options(
                selectinload(TonightSessionCandidate.watchlist_item).selectinload(WatchlistItem.title)
            )
            .where(TonightSessionCandidate.session_id == sess.id)
            .order_by(TonightSessionCandidate.position.asc())
        )
        rows = (await db.execute(q)).scalars().all()

        # Build response
        constraints = TonightConstraints.model_validate(sess.constraints)

        candidates_out: list[SessionCandidateOut] = []
        for c in rows:
            wi = c.watchlist_item
            t = wi.title
            candidates_out.append(
                SessionCandidateOut(
                    watchlist_item_id=wi.id,
                    position=c.position,
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
            candidates=candidates_out,
        )

    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
