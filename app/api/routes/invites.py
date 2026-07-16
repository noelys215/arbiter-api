from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.http_errors import permission_error, value_error
from app.api.presenters.users import invite_user_from_user
from app.models.user import User
from app.schemas.friends import FriendInviteAcceptResponse, FriendInvitePreview
from app.schemas.groups import GroupInviteDecisionResponse, GroupInvitePreview
from app.services.friends import accept_friend_link_invite, preview_friend_invite
from app.services.groups import accept_group_link_invite, preview_group_invite

router = APIRouter(prefix="/invites", tags=["invitations"])


@router.get("/friend/{token}", response_model=FriendInvitePreview)
async def friend_invite_preview(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    try:
        invite, inviter = await preview_friend_invite(db, token)
        return FriendInvitePreview(
            inviter=invite_user_from_user(inviter),
            expires_at=invite.expires_at,
        )
    except ValueError as exc:
        raise value_error(
            exc,
            code_statuses={
                "invalid_invite": 404,
                "expired_invite": 410,
                "revoked_invite": 410,
            },
            default_detail="Invitation unavailable",
        ) from exc


@router.post("/friend/{token}/accept", response_model=FriendInviteAcceptResponse)
async def friend_invite_accept(
    token: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        already_friends = await accept_friend_link_invite(db, user.id, token)
        await db.commit()
        return FriendInviteAcceptResponse(ok=True, already_friends=already_friends)
    except ValueError as exc:
        await db.rollback()
        raise value_error(
            exc,
            code_statuses={
                "invalid_invite": 404,
                "expired_invite": 410,
                "revoked_invite": 410,
                "used_invite": 409,
                "cannot_friend_self": 400,
            },
            default_detail="Could not accept invitation",
        ) from exc


@router.get("/group/{token}", response_model=GroupInvitePreview)
async def group_invite_preview(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    try:
        invite, group, inviter, member_count = await preview_group_invite(db, token)
        return GroupInvitePreview(
            group_id=group.id,
            group_name=group.name,
            inviter=invite_user_from_user(inviter),
            member_count=member_count,
            expires_at=invite.expires_at,
            targeted=invite.target_user_id is not None,
        )
    except ValueError as exc:
        raise value_error(
            exc,
            code_statuses={
                "invalid_invite": 404,
                "expired_invite": 410,
                "revoked_invite": 410,
            },
            default_detail="Invitation unavailable",
        ) from exc


@router.post("/group/{token}/accept", response_model=GroupInviteDecisionResponse)
async def group_invite_accept(
    token: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        already_member = await accept_group_link_invite(db, user.id, token)
        await db.commit()
        return GroupInviteDecisionResponse(
            ok=True,
            decision="accepted",
            already_member=already_member,
        )
    except PermissionError as exc:
        await db.rollback()
        raise permission_error(exc) from exc
    except ValueError as exc:
        await db.rollback()
        raise value_error(
            exc,
            code_statuses={
                "invalid_invite": 404,
                "expired_invite": 410,
                "revoked_invite": 410,
                "used_invite": 409,
            },
            default_detail="Could not join group",
        ) from exc
