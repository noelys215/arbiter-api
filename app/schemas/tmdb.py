from __future__ import annotations

from pydantic import BaseModel, Field


class TMDBSearchItem(BaseModel):
    tmdb_id: int
    media_type: str = Field(pattern="^(movie|tv)$")
    title: str
    year: int | None = None
    poster_path: str | None = None
