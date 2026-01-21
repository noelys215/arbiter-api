from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator


FormatType = Literal["movie", "tv", "any"]
EnergyType = Literal["low", "med", "high"]


class TonightConstraints(BaseModel):
    moods: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)

    max_runtime: int | None = Field(default=None, ge=30, le=600)
    format: FormatType = Field(default="any")
    energy: EnergyType | None = Field(default=None)

    free_text: str = Field(default="")
    parsed_by_ai: bool = Field(default=False)
    ai_version: str | None = Field(default=None)

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

    @field_validator("free_text")
    @classmethod
    def normalize_text(cls, v: str) -> str:
        return (v or "").strip()

    @model_validator(mode="after")
    def validate_ai_fields(self):
        if self.parsed_by_ai and not self.ai_version:
            # optional but recommended
            raise ValueError("ai_version is required when parsed_by_ai is true")
        if not self.parsed_by_ai:
            # keep it consistent: if not parsed by AI, ai_version must be null
            self.ai_version = None
        return self
