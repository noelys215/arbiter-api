from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

FeedbackType = Literal["feedback", "bug", "feature"]
FeedbackSource = Literal["landing_footer", "account_profile"]

_UNSAFE_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean_diagnostic_text(value: str) -> str:
    cleaned = value.strip()
    if _UNSAFE_CONTROL_CHARACTERS.search(cleaned):
        raise ValueError("Invalid control character")
    return cleaned


class FeedbackDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: str = Field(min_length=1, max_length=180)
    browser: str = Field(min_length=1, max_length=160)
    operating_system: str = Field(min_length=1, max_length=100)
    viewport_width: int = Field(ge=1, le=20_000)
    viewport_height: int = Field(ge=1, le=20_000)
    app_version: str = Field(min_length=1, max_length=32)
    submitted_at: datetime
    source: FeedbackSource
    selected_group_id: UUID | None = None
    online: bool | None = None

    @field_validator("route", "browser", "operating_system", "app_version")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return _clean_diagnostic_text(value)


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_id: UUID
    type: FeedbackType
    message: str = Field(min_length=10, max_length=4_000)
    allow_contact: bool = False
    contact_email: EmailStr | None = None
    include_diagnostics: bool = False
    diagnostics: FeedbackDiagnostics | None = None
    website: str = Field(default="", max_length=200)

    @field_validator("message")
    @classmethod
    def clean_message(cls, value: str) -> str:
        cleaned = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        if len(cleaned) < 10:
            raise ValueError("Message is too short")
        if _UNSAFE_CONTROL_CHARACTERS.search(cleaned):
            raise ValueError("Invalid control character")
        return cleaned

    @field_validator("website")
    @classmethod
    def clean_website(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_consent_state(self) -> "FeedbackRequest":
        if not self.allow_contact and self.contact_email is not None:
            raise ValueError("Contact email requires contact permission")
        if self.include_diagnostics != (self.diagnostics is not None):
            raise ValueError("Diagnostics consent does not match diagnostics")
        return self


class FeedbackResponse(BaseModel):
    ok: bool = True
