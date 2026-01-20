from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.friends import (
    FriendInviteCreateResponse,
    FriendAcceptRequest,
    FriendAcceptResponse,
    FriendListItem,
)
from app.services.friends import create_friend_invite, accept_friend_invite, list_friends

router = APIRouter(prefix="/friends", tags=["friends"])


@router.post("/invite", response_model=FriendInviteCreateResponse, status_code=201)
async def generate_invite(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    invite = await create_friend_invite(db, user.id, ttl_minutes=60)
    await db.commit()
    return FriendInviteCreateResponse(code=invite.code, expires_at=invite.expires_at)


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
        code = str(e)
        mapping = {
            "invalid_code": (404, "Invalid invite code"),
            "expired_code": (410, "Invite code expired"),
            "used_code": (409, "Invite code already used"),
            "cannot_friend_self": (400, "You cannot friend yourself"),
        }
        status, msg = mapping.get(code, (400, "Could not accept invite"))
        raise HTTPException(status_code=status, detail=msg)


@router.get("", response_model=list[FriendListItem])
async def get_friends(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    friends = await list_friends(db, user.id)
    return [
        FriendListItem(
            id=str(f.id),
            email=f.email,
            username=f.username,
            display_name=f.display_name,
            avatar_url=f.avatar_url,
        )
        for f in friends
    ]
