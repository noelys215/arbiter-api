from __future__ import annotations

from pydantic import BaseModel, Field
from datetime import datetime
from uuid import UUID
from app.schemas.users import AvatarFields, InvitePublicUser


class FriendInviteCreateResponse(BaseModel):
    code: str
    expires_at: datetime


class FriendLinkInviteCreateResponse(FriendInviteCreateResponse):
    id: UUID
    token: str
    max_uses: int
    uses_count: int


class FriendInvitePreview(BaseModel):
    inviter: InvitePublicUser
    expires_at: datetime


class FriendInviteAcceptResponse(BaseModel):
    ok: bool
    already_friends: bool = False


class FriendAcceptRequest(BaseModel):
    code: str = Field(min_length=4, max_length=32)


class FriendAcceptResponse(BaseModel):
    ok: bool


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
