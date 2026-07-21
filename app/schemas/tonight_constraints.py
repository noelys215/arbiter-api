from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.mood_cues import MOOD_CUE_IDS


FormatType = Literal["movie", "tv", "any"]
EnergyType = Literal["low", "med", "high"]


class TonightConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mood_cues: list[str] = Field(default_factory=list, max_length=3)
    moods: list[str] = Field(default_factory=list, max_length=20)
    avoid: list[str] = Field(default_factory=list, max_length=20)

    max_runtime: int | None = Field(default=None, ge=30, le=600)
    format: FormatType = Field(default="any")
    energy: EnergyType | None = Field(default=None)

    free_text: str = Field(default="", max_length=500)
    custom_mood_text: str = Field(default="", max_length=240)
    parsed_by_ai: bool = Field(default=False)
    ai_version: str | None = Field(default=None, max_length=100)

    @field_validator("moods", "avoid")
    @classmethod
    def normalize_tags(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for s in v:
            s2 = (s or "").strip()
            if not s2:
                continue
            s2 = s2[:60]
            key = s2.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(s2)
        return cleaned

    @field_validator("mood_cues")
    @classmethod
    def validate_mood_cues(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for cue_id in value:
            normalized = (cue_id or "").strip().lower()
            if normalized not in MOOD_CUE_IDS:
                raise ValueError(f"Unknown mood cue: {normalized or 'empty'}")
            if normalized not in cleaned:
                cleaned.append(normalized)
        if len(cleaned) > 3:
            raise ValueError("Choose no more than 3 mood cues")
        return cleaned

    @field_validator("free_text", "custom_mood_text")
    @classmethod
    def normalize_text(cls, v: str) -> str:
        return (v or "").strip()

    @model_validator(mode="after")
    def validate_ai_fields(self):
        if self.parsed_by_ai and not self.ai_version:
            # optional but recommended
            raise ValueError("ai_version is required when parsed_by_ai is true")
        if not self.parsed_by_ai and self.ai_version is not None:
            # keep it consistent: if not parsed by AI, ai_version must be null
            self.ai_version = None
        return self
