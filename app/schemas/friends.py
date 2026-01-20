from __future__ import annotations

from pydantic import BaseModel, Field
from datetime import datetime


class FriendInviteCreateResponse(BaseModel):
    code: str
    expires_at: datetime


class FriendAcceptRequest(BaseModel):
    code: str = Field(min_length=4, max_length=32)


class FriendAcceptResponse(BaseModel):
    ok: bool


class FriendListItem(BaseModel):
    id: str
    email: str
    username: str
    display_name: str
    avatar_url: str | None = None
