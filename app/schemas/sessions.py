from __future__ import annotations
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field
from app.schemas.tonight_constraints import TonightConstraints
from app.schemas.watchlist import TitleOut

class CreateSessionRequest(BaseModel):
    constraints: dict = Field(default_factory=dict)  # we validate using TonightConstraints in service
    text: str | None = None
    duration_seconds: int = Field(default=90, ge=15, le=600)
    candidate_count: int = Field(default=12, ge=1, le=30)

class SessionCandidateOut(BaseModel):
    watchlist_item_id: UUID
    position: int
    title: TitleOut

class CreateSessionResponse(BaseModel):
    session_id: UUID
    ends_at: datetime
    constraints: TonightConstraints
    ai_used: bool
    ai_why: str | None
    candidates: list[SessionCandidateOut]

class VoteRequest(BaseModel):
    watchlist_item_id: UUID
    vote: str = Field(pattern="^(yes|no)$")

class SessionStateResponse(BaseModel):
    session_id: UUID
    status: str
    ends_at: datetime
    completed_at: datetime | None
    result_watchlist_item_id: UUID | None
    candidates: list[SessionCandidateOut]
