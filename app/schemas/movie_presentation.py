from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.users import AvatarFields


class MoviePersonOut(BaseModel):
    name: str
    role: str | None = None


class MovieAddedByOut(AvatarFields):
    id: UUID
    username: str
    display_name: str


class MovieWatchlistContextOut(BaseModel):
    item_id: UUID
    status: str
    added_at: datetime
    added_by: MovieAddedByOut | None = None


class MovieSessionContextOut(BaseModel):
    session_id: UUID
    status: str
    match_reason: str | None = None
    mood_cue_ids: list[str] = Field(default_factory=list)


class MovieNightAppearanceOut(BaseModel):
    session_id: UUID
    completed_at: datetime
    won: bool
    watched_status: str


class MovieHistoryContextOut(BaseModel):
    appearance_count: int
    win_count: int
    last_considered_at: datetime | None = None
    last_watched_at: datetime | None = None
    recent_movie_nights: list[MovieNightAppearanceOut] = Field(default_factory=list)


class MovieDetailOut(BaseModel):
    reference: str
    group_id: UUID
    group_name: str
    title_id: UUID | None = None
    source: str | None = None
    source_id: str | None = None
    media_type: str
    title: str
    release_year: int | None = None
    release_date: str | None = None
    runtime_minutes: int | None = None
    poster_path: str | None = None
    backdrop_path: str | None = None
    overview: str | None = None
    genres: list[str] = Field(default_factory=list)
    directors: list[str] = Field(default_factory=list)
    cast: list[MoviePersonOut] = Field(default_factory=list)
    certification: str | None = None
    trailer_url: str | None = None
    watchlist: MovieWatchlistContextOut | None = None
    session: MovieSessionContextOut | None = None
    history: MovieHistoryContextOut
