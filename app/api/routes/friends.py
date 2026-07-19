from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.http_errors import permission_error, value_error
from app.api.presenters.users import invite_user_from_user, public_user_from_user
from app.models.user import User
from app.schemas.friends import (
    FriendInviteCreateResponse,
    FriendLinkInviteCreateResponse,
    FriendAcceptRequest,
    FriendAcceptResponse,
    FriendListItem,
    FriendRequestCreate,
    FriendRequestCreateResponse,
    FriendRequestDecision,
    FriendRequestDecisionResponse,
    FriendRequestListItem,
    FriendRequestListResponse,
    UnfriendRequest,
    UnfriendResponse,
)
from app.services.friends import (
    accept_friend_invite,
    cancel_friend_request,
    create_friend_request,
    create_friend_invite,
    create_friend_link_invite,
    list_friends,
    list_friend_requests,
    decide_friend_request,
    revoke_friend_invite,
    unfriend,
)
from app.services.social_realtime import (
    publish_friend_request_update,
    publish_friendship_update,
)
from uuid import UUID

router = APIRouter(prefix="/friends", tags=["friends"])


@router.post(
    "/requests",
    response_model=FriendRequestCreateResponse,
    status_code=201,
)
async def send_friend_request(
    payload: FriendRequestCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        result = await create_friend_request(db, user.id, str(payload.email))
        await db.commit()
        if result.changed and result.target_user_id is not None:
            await publish_friend_request_update(
                [user.id, result.target_user_id],
                reason="request_created",
            )
        return FriendRequestCreateResponse(ok=True)
    except ValueError as exc:
        await db.rollback()
        raise value_error(
            exc,
            code_statuses={
                "cannot_friend_self": 400,
                "already_friends": 409,
                "request_already_pending": 409,
            },
            detail_overrides={
                "cannot_friend_self": "You cannot send a friend request to yourself.",
                "already_friends": "You are already friends.",
                "request_already_pending": "A friend request is already pending between you.",
            },
            default_detail="Could not send friend request",
        ) from exc


@router.get("/requests", response_model=FriendRequestListResponse)
async def get_friend_requests(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    incoming, outgoing = await list_friend_requests(db, user.id)
    return FriendRequestListResponse(
        incoming=[
            FriendRequestListItem(
                id=invite.id,
                direction="incoming",
                user=invite_user_from_user(other_user),
                created_at=invite.created_at,
                expires_at=invite.expires_at,
            )
            for invite, other_user in incoming
        ],
        outgoing=[
            FriendRequestListItem(
                id=invite.id,
                direction="outgoing",
                user=invite_user_from_user(other_user),
                created_at=invite.created_at,
                expires_at=invite.expires_at,
            )
            for invite, other_user in outgoing
        ],
    )


@router.post(
    "/requests/{request_id}/decision",
    response_model=FriendRequestDecisionResponse,
)
async def decide_pending_friend_request(
    request_id: UUID,
    payload: FriendRequestDecision,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        result = await decide_friend_request(
            db, user.id, request_id, payload.decision
        )
        await db.commit()
        recipients = [result.inviter_user_id, result.target_user_id]
        if result.changed:
            await publish_friend_request_update(
                recipients,
                reason=(
                    "request_accepted"
                    if payload.decision == "accept"
                    else "request_declined"
                ),
            )
        if (
            payload.decision == "accept"
            and result.changed
            and not result.already_friends
        ):
            await publish_friendship_update(
                recipients,
                reason="friendship_created",
            )
        return FriendRequestDecisionResponse(
            ok=True,
            decision=(
                "accepted" if payload.decision == "accept" else "declined"
            ),
            already_friends=result.already_friends,
        )
    except PermissionError as exc:
        await db.rollback()
        raise permission_error(exc) from exc
    except ValueError as exc:
        await db.rollback()
        raise value_error(
            exc,
            code_statuses={
                "request_not_found": 404,
                "expired_invite": 410,
                "revoked_invite": 410,
                "used_invite": 409,
            },
            default_detail="Could not update friend request",
        ) from exc


@router.delete(
    "/requests/{request_id}",
    response_model=FriendRequestDecisionResponse,
)
async def cancel_pending_friend_request(
    request_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        result = await cancel_friend_request(db, user.id, request_id)
        await db.commit()
        await publish_friend_request_update(
            [result.inviter_user_id, result.target_user_id],
            reason="request_cancelled",
        )
        return FriendRequestDecisionResponse(ok=True, decision="cancelled")
    except PermissionError as exc:
        await db.rollback()
        raise permission_error(exc) from exc
    except ValueError as exc:
        await db.rollback()
        raise value_error(
            exc,
            code_statuses={
                "request_not_found": 404,
                "expired_invite": 410,
                "revoked_invite": 410,
                "used_invite": 409,
            },
            default_detail="Could not cancel friend request",
        ) from exc


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
        already_friends, inviter_id = await accept_friend_invite(
            db, user.id, payload.code
        )
        await db.commit()
        if not already_friends:
            await publish_friendship_update(
                [user.id, inviter_id],
                reason="friendship_created",
            )
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
        await publish_friendship_update(
            [user.id, payload.user_id],
            reason="friendship_removed",
        )
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
