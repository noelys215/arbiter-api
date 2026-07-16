from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field, field_validator
from uuid import UUID
from typing import List, Literal
from app.schemas.users import AvatarFields, InvitePublicUser


class CreateGroupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    member_user_ids: List[UUID] = Field(default_factory=list)  # friends to add (excluding owner is allowed)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Group name is required")
        return cleaned


class UpdateGroupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Group name is required")
        return cleaned


class GroupListItem(BaseModel):
    id: UUID
    name: str
    owner_id: UUID
    created_at: datetime
    member_count: int


class GroupMember(AvatarFields):
    id: UUID
    email: str
    username: str
    display_name: str


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


class CreateGroupInviteRequest(BaseModel):
    target_user_id: UUID | None = None
    max_uses: int = Field(default=25, ge=1, le=50)


class GroupLinkInviteResponse(GroupInviteResponse):
    id: UUID
    token: str
    group_id: UUID
    target_user_id: UUID | None = None


class GroupInvitePreview(BaseModel):
    group_id: UUID
    group_name: str
    inviter: InvitePublicUser
    member_count: int
    expires_at: datetime
    targeted: bool


class GroupInvitationListItem(BaseModel):
    id: UUID
    group_id: UUID
    group_name: str
    inviter: InvitePublicUser
    target: InvitePublicUser | None = None
    expires_at: datetime
    max_uses: int
    uses_count: int
    targeted: bool


class GroupInviteDecisionRequest(BaseModel):
    decision: Literal["accept", "decline"]


class GroupInviteDecisionResponse(BaseModel):
    ok: bool
    decision: Literal["accepted", "declined"]
    already_member: bool = False


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
