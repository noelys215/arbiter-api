from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from uuid import UUID
from app.schemas.users import AvatarFields, InvitePublicUser


class FriendRequestCreate(BaseModel):
    identifier: str = Field(min_length=1, max_length=320)

    @field_validator("identifier")
    @classmethod
    def clean_identifier(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Email or username is required")
        return cleaned


class FriendRequestCreateResponse(BaseModel):
    ok: bool = True


class FriendRequestListItem(BaseModel):
    id: UUID
    direction: Literal["incoming", "outgoing"]
    user: InvitePublicUser
    created_at: datetime
    expires_at: datetime


class FriendRequestListResponse(BaseModel):
    incoming: list[FriendRequestListItem]
    outgoing: list[FriendRequestListItem]


class FriendRequestDecision(BaseModel):
    decision: Literal["accept", "decline"]


class FriendRequestDecisionResponse(BaseModel):
    ok: bool = True
    decision: Literal["accepted", "declined", "cancelled"]
    already_friends: bool = False


class FriendListItem(AvatarFields):
    id: str
    email: str
    username: str
    display_name: str


class UnfriendRequest(BaseModel):
    user_id: UUID


class UnfriendResponse(BaseModel):
    ok: bool
    removed: bool


class BlockedUserListItem(AvatarFields):
    id: UUID
    username: str
    display_name: str
    blocked_at: datetime


class BlockUserResponse(BaseModel):
    ok: bool = True
    already_blocked: bool = False
