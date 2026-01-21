from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.tonight_constraints import TonightConstraints
from app.schemas.watchlist import TitleOut


class CreateSessionRequest(BaseModel):
    # constraints from chips/toggles (Phase 5.1 canonical schema)
    constraints: dict = Field(default_factory=dict)

    # optional chat box text
    text: str | None = None

    duration_seconds: int = Field(default=90, ge=15, le=600)
    candidate_count: int = Field(default=12, ge=5, le=30)


class SessionCandidateOut(BaseModel):
    # NOTE: deck is frozen by watchlist_item_id + position in DB
    watchlist_item_id: UUID
    position: int
    title: TitleOut


class CreateSessionResponse(BaseModel):
    session_id: UUID
    ends_at: datetime

    constraints: TonightConstraints
    ai_used: bool = False
    ai_why: str | None = None

    candidates: list[SessionCandidateOut]
