from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.schemas.users import AvatarFields


class TitleOut(BaseModel):
    id: UUID
    source: str
    source_id: str | None
    media_type: str
    name: str
    release_year: int | None = None
    poster_path: str | None = None
    overview: str | None = None
    runtime_minutes: int | None = None
    tmdb_genres: list[str] = Field(default_factory=list)
    tmdb_genre_ids: list[int] = Field(default_factory=list)
    tmdb_streaming_options: list[dict[str, str | None]] = Field(default_factory=list)
    tmdb_streaming_providers: list[str] = Field(default_factory=list)
    tmdb_streaming_link: str | None = None


class AddWatchlistTMDB(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["tmdb"] = "tmdb"
    tmdb_id: int = Field(ge=1)
    media_type: str = Field(pattern="^(movie|tv)$")

    # v1: provided by frontend from /tmdb/search
    title: str = Field(min_length=1, max_length=300)
    year: int | None = Field(default=None, ge=1870, le=2100)
    poster_path: str | None = Field(default=None, max_length=500)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Title is required")
        return cleaned


class AddWatchlistManual(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["manual"] = "manual"
    title: str = Field(min_length=1, max_length=300)
    year: int | None = Field(default=None, ge=1870, le=2100)
    media_type: str = Field(pattern="^(movie|tv)$")
    poster_path: str | None = Field(default=None, max_length=500)
    overview: str | None = Field(default=None, max_length=5_000)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Title is required")
        return cleaned


AddWatchlistRequest = AddWatchlistTMDB | AddWatchlistManual


class WatchlistAddedBy(AvatarFields):
    id: UUID
    username: str
    display_name: str


class WatchlistItemOut(BaseModel):
    id: UUID
    group_id: UUID
    added_by_user: WatchlistAddedBy | None
    status: str
    snoozed_until: datetime | None
    created_at: datetime
    title: TitleOut
    already_exists: bool = False


class WatchlistPageOut(BaseModel):
    items: list[WatchlistItemOut]
    next_cursor: str | None = None
    total_count: int


class WatchlistPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | None = Field(default=None, pattern="^(watchlist|watched)$")
    snoozed_until: datetime | None = None
    remove: bool | None = None

    @model_validator(mode="after")
    def at_least_one_field(self):
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        return self
