from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.http_errors import permission_error, value_error
from app.api.presenters.users import public_user_from_user
from app.models.user import User
from app.schemas.friends import (
    FriendInviteCreateResponse,
    FriendLinkInviteCreateResponse,
    FriendAcceptRequest,
    FriendAcceptResponse,
    FriendListItem,
    UnfriendRequest,
    UnfriendResponse,
)
from app.services.friends import (
    accept_friend_invite,
    create_friend_invite,
    create_friend_link_invite,
    list_friends,
    revoke_friend_invite,
    unfriend,
)
from uuid import UUID

router = APIRouter(prefix="/friends", tags=["friends"])


@router.post("/invite", response_model=FriendInviteCreateResponse, status_code=201)
async def generate_invite(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    invite = await create_friend_invite(db, user.id, ttl_minutes=60)
    await db.commit()
    return FriendInviteCreateResponse(code=invite.code, expires_at=invite.expires_at)


@router.post("/invites", response_model=FriendLinkInviteCreateResponse, status_code=201)
async def create_link_invite(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    invite, token = await create_friend_link_invite(db, user.id)
    await db.commit()
    return FriendLinkInviteCreateResponse(
        id=invite.id,
        token=token,
        code=invite.code,
        expires_at=invite.expires_at,
        max_uses=invite.max_uses,
        uses_count=invite.uses_count,
    )


@router.delete("/invites/{invite_id}", status_code=204)
async def revoke_link_invite(
    invite_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        await revoke_friend_invite(db, user.id, invite_id)
        await db.commit()
    except PermissionError as exc:
        await db.rollback()
        raise permission_error(exc) from exc
    except ValueError as exc:
        await db.rollback()
        raise value_error(exc, code_statuses={"invalid_invite": 404}) from exc


@router.post("/accept", response_model=FriendAcceptResponse)
async def accept_invite(
    payload: FriendAcceptRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        await accept_friend_invite(db, user.id, payload.code)
        await db.commit()
        return FriendAcceptResponse(ok=True)
    except ValueError as e:
        await db.rollback()
        raise value_error(
            e,
            code_statuses={
                "invalid_code": 404,
                "expired_code": 410,
                "revoked_code": 410,
                "used_code": 409,
                "cannot_friend_self": 400,
            },
            detail_overrides={
                "invalid_code": "Invalid invite code",
                "expired_code": "Invite code expired",
                "used_code": "Invite code already used",
                "cannot_friend_self": "You cannot friend yourself",
            },
            default_detail="Could not accept invite",
        ) from e


@router.get("", response_model=list[FriendListItem])
async def get_friends(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    friends = await list_friends(db, user.id)
    return [FriendListItem(**public_user_from_user(f)) for f in friends]


@router.post("/unfriend", response_model=UnfriendResponse, status_code=200)
async def unfriend_route(
    payload: UnfriendRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        await unfriend(db, user.id, payload.user_id)
        await db.commit()
        return UnfriendResponse(ok=True, removed=True)
    except ValueError as e:
        await db.rollback()
        raise value_error(
            e,
            code_statuses={
                "not_found": 404,
                "cannot_unfriend_self": 400,
            },
            detail_overrides={
                "not_found": "Friendship not found",
                "cannot_unfriend_self": "You cannot unfriend yourself",
            },
            default_detail="Could not unfriend user",
        ) from e
