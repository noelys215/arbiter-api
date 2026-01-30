from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field
from uuid import UUID
from typing import List


class CreateGroupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    member_user_ids: List[UUID] = Field(default_factory=list)  # friends to add (excluding owner is allowed)


class GroupListItem(BaseModel):
    id: UUID
    name: str
    owner_id: UUID
    created_at: datetime
    member_count: int


class GroupMember(BaseModel):
    id: UUID
    email: str
    username: str
    display_name: str
    avatar_url: str | None


class GroupDetailResponse(BaseModel):
    id: UUID
    name: str
    owner_id: UUID
    created_at: datetime
    members: List[GroupMember]


class GroupInviteResponse(BaseModel):
    code: str
    expires_at: datetime
    max_uses: int
    uses_count: int


class AcceptGroupInviteRequest(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class LeaveGroupResponse(BaseModel):
    ok: bool


class DeleteGroupResponse(BaseModel):
    ok: bool


class AddGroupMembersRequest(BaseModel):
    member_user_ids: List[UUID] = Field(default_factory=list)


class AddGroupMembersResponse(BaseModel):
    ok: bool
    added_user_ids: List[UUID]
    skipped_user_ids: List[UUID]
