from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.usernames import canonicalize_username, is_valid_username
from app.schemas.users import AvatarFields, AvatarSource


class RegisterRequest(BaseModel):
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


class LoginRequest(BaseModel):
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


class LocalAuthBypassRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class MagicLinkRequest(BaseModel):
    email: EmailStr


class MagicLinkRequestResponse(BaseModel):
    ok: bool


class LogoutResponse(BaseModel):
    ok: bool


class AvatarUpdateRequest(BaseModel):
    avatar_source: AvatarSource
    avatar_style: str | None = Field(default=None, max_length=32)
    avatar_seed: str | None = Field(default=None, max_length=128)


class ProfileUpdateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)

    @field_validator("display_name")
    @classmethod
    def clean_display_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Display name is required")
        return cleaned


class MeResponse(AvatarFields):
    id: str
    email: EmailStr
    username: str
    display_name: str
