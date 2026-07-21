from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.usernames import canonicalize_username, is_valid_username
from app.schemas.users import AvatarFields, AvatarSource


class StrictAuthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterRequest(StrictAuthRequest):
    email: EmailStr
    username: str = Field(min_length=3, max_length=50)
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=128)  # allow chars, enforce bytes below

    @field_validator("username", mode="before")
    @classmethod
    def clean_username(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        username = canonicalize_username(value)
        if not is_valid_username(username):
            raise ValueError(
                "Username must use 3-50 lowercase letters, numbers, or underscores"
            )
        return username

    @field_validator("display_name")
    @classmethod
    def clean_display_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Display name is required")
        return cleaned

    @field_validator("password")
    @classmethod
    def password_bcrypt_bytes(cls, v: str) -> str:
        if len(v.encode("utf-8")) > 72:
            raise ValueError("password must be 72 bytes or fewer (bcrypt limit)")
        return v




class RegisterResponse(BaseModel):
    id: str


class LoginRequest(StrictAuthRequest):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)  # allow chars, enforce bytes below

    @field_validator("password")
    @classmethod
    def password_bcrypt_bytes(cls, v: str) -> str:
        if len(v.encode("utf-8")) > 72:
            raise ValueError("password must be 72 bytes or fewer (bcrypt limit)")
        return v

class LoginResponse(BaseModel):
    ok: bool


class LocalAuthBypassRequest(StrictAuthRequest):
    token: str = Field(min_length=1, max_length=512)


class MagicLinkRequest(StrictAuthRequest):
    email: EmailStr


class MagicLinkRequestResponse(BaseModel):
    ok: bool


class MagicLinkVerifyRequest(StrictAuthRequest):
    grant: str = Field(min_length=32, max_length=512, strict=True)


class LogoutResponse(BaseModel):
    ok: bool


class DeleteAccountRequest(StrictAuthRequest):
    confirmation: Literal["DELETE"]


class AvatarUpdateRequest(StrictAuthRequest):
    avatar_source: AvatarSource
    avatar_style: str | None = Field(default=None, max_length=32)
    avatar_seed: str | None = Field(default=None, max_length=128)


class ProfileUpdateRequest(StrictAuthRequest):
    display_name: str = Field(min_length=1, max_length=120)

    @field_validator("display_name")
    @classmethod
    def clean_display_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Display name is required")
        return cleaned


class OnboardingTourUpdateRequest(StrictAuthRequest):
    version: int = Field(ge=1, le=10_000, strict=True)
    status: Literal["completed", "skipped"]


class MeResponse(AvatarFields):
    id: str
    email: EmailStr
    username: str
    display_name: str
    onboarding_tour_version: int | None = None
    onboarding_tour_status: Literal["completed", "skipped"] | None = None
    onboarding_tour_updated_at: datetime | None = None
