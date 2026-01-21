from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


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


class AddWatchlistTMDB(BaseModel):
    type: Literal["tmdb"] = "tmdb"
    tmdb_id: int
    media_type: str = Field(pattern="^(movie|tv)$")

    # v1: provided by frontend from /tmdb/search
    title: str
    year: int | None = None
    poster_path: str | None = None


class AddWatchlistManual(BaseModel):
    type: Literal["manual"] = "manual"
    title: str
    year: int | None = None
    media_type: str = Field(pattern="^(movie|tv)$")
    poster_path: str | None = None
    overview: str | None = None


AddWatchlistRequest = AddWatchlistTMDB | AddWatchlistManual


class WatchlistItemOut(BaseModel):
    id: UUID
    group_id: UUID
    status: str
    snoozed_until: datetime | None
    created_at: datetime
    title: TitleOut
    already_exists: bool = False


class WatchlistPatchRequest(BaseModel):
    status: str | None = Field(default=None, pattern="^(watchlist|watched)$")
    snoozed_until: datetime | None = None
    remove: bool | None = None

    @model_validator(mode="after")
    def at_least_one_field(self):
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        return self
