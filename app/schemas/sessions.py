from __future__ import annotations
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field
from app.schemas.tonight_constraints import TonightConstraints
from app.schemas.watchlist import TitleOut

class CreateSessionRequest(BaseModel):
    constraints: dict = Field(default_factory=dict)  # we validate using TonightConstraints in service
    text: str | None = None
    confirm_ready: bool | None = None
    duration_seconds: int = Field(default=90, ge=15, le=600)
    candidate_count: int = Field(default=12, ge=1, le=30)

class SessionCandidateOut(BaseModel):
    watchlist_item_id: UUID
    position: int
    title: TitleOut
    reason: str | None = None

class CreateSessionResponse(BaseModel):
    session_id: UUID
    ends_at: datetime
    constraints: TonightConstraints
    ai_used: bool
    ai_why: str | None
    phase: str = "collecting"
    round: int = 0
    user_locked: bool = False
    user_seconds_left: int = 0
    tie_break_required: bool = False
    tie_break_candidate_ids: list[UUID] = Field(default_factory=list)
    ended_by_leader: bool = False
    candidates: list[SessionCandidateOut]
    personal_candidates: list[SessionCandidateOut] = Field(default_factory=list)

class VoteRequest(BaseModel):
    watchlist_item_id: UUID
    vote: str = Field(pattern="^(yes|no)$")

class SessionStateResponse(BaseModel):
    session_id: UUID
    status: str
    phase: str = "round1"
    round: int = 1
    user_locked: bool = False
    user_seconds_left: int = 0
    tie_break_required: bool = False
    tie_break_candidate_ids: list[UUID] = Field(default_factory=list)
    ended_by_leader: bool = False
    ends_at: datetime
    completed_at: datetime | None
    result_watchlist_item_id: UUID | None
    mutual_candidate_ids: list[UUID] = Field(default_factory=list)
    shortlist: list[UUID] = Field(default_factory=list)
    candidates: list[SessionCandidateOut]
