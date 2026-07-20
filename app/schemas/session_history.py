from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.users import AvatarFields


WatchedStatus = Literal["unconfirmed", "watched", "not_watched"]


class CompletedParticipantOut(AvatarFields):
    id: UUID
    user_id: UUID | None
    display_name: str
    joined_at: datetime | None
    role: Literal["host", "participant"]
    submitted_votes: bool
    participation_status: Literal["participated", "left"]
    criteria: dict | None = None


class CompletedVoteOut(BaseModel):
    participant_id: UUID
    round: int
    vote: Literal["yes", "no"]


class CompletedCandidateOut(BaseModel):
    id: UUID
    source_watchlist_item_id: UUID
    source: str | None
    source_id: str | None
    media_type: str | None
    title: str
    release_year: int | None
    poster_path: str | None
    backdrop_path: str | None
    runtime_minutes: int | None
    genres: list[str] = Field(default_factory=list)
    overview: str | None
    position: int
    yes_count: int | None
    no_count: int | None
    total_vote_count: int | None
    is_winner: bool
    is_finalist: bool
    votes: list[CompletedVoteOut] = Field(default_factory=list)


class CompletedSessionOut(BaseModel):
    session_id: UUID
    group_id: UUID
    group_name: str
    status: Literal["winner_selected", "completed"]
    created_at: datetime
    started_at: datetime | None
    winner_selected_at: datetime
    completed_at: datetime | None
    criteria: dict
    winner_candidate_id: UUID
    decision_duration_seconds: int | None
    winner_unanimous: bool | None
    had_tie: bool | None
    tie_resolution: str | None
    watched_status: WatchedStatus
    watched_confirmed_at: datetime | None
    watched_confirmed_by_user_id: UUID | None
    teleparty_was_shared: bool
    teleparty_shared_at: datetime | None
    teleparty_handoff_at: datetime | None
    participants: list[CompletedParticipantOut]
    candidates: list[CompletedCandidateOut]


class GroupMovieNightPage(BaseModel):
    items: list[CompletedSessionOut]
    next_cursor: str | None = None


class WatchedStatusUpdateRequest(BaseModel):
    status: WatchedStatus
