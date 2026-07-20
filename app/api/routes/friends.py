from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.http_errors import permission_error, value_error
from app.api.presenters.users import invite_user_from_user, public_user_from_user
from app.models.user import User
from app.schemas.friends import (
    FriendListItem,
    BlockedUserListItem,
    BlockUserResponse,
    FriendRequestCreate,
    FriendRequestCreateResponse,
    FriendRequestDecision,
    FriendRequestDecisionResponse,
    FriendRequestListItem,
    FriendRequestListResponse,
    UnfriendRequest,
    UnfriendResponse,
)
from app.services.blocks import block_user, list_blocked_users, unblock_user
from app.services.friends import (
    cancel_friend_request,
    create_friend_request,
    list_friends,
    list_friend_requests,
    decide_friend_request,
    unfriend,
)
from app.services.social_realtime import (
    publish_group_invite_update,
    publish_friend_request_update,
    publish_friendship_update,
)
from app.api.social_rate_limits import enforce_social_rate_limit
from uuid import UUID

router = APIRouter(prefix="/friends", tags=["friends"])


@router.post(
    "/requests",
    response_model=FriendRequestCreateResponse,
    status_code=201,
)
async def send_friend_request(
    request: Request,
    payload: FriendRequestCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        await enforce_social_rate_limit(
            request, user=user, action="friend_request"
        )
        result = await create_friend_request(db, user.id, payload.identifier)
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
                "account_not_found": 404,
            },
            detail_overrides={
                "cannot_friend_self": "You cannot send a friend request to yourself.",
                "already_friends": "You are already friends.",
                "request_already_pending": "A friend request is already pending between you.",
                "account_not_found": "No Arbiter account uses that username.",
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


@router.get("/blocked", response_model=list[BlockedUserListItem])
async def get_blocked_users(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = await list_blocked_users(db, user.id)
    return [
        BlockedUserListItem(
            **invite_user_from_user(blocked_user),
            blocked_at=block.created_at,
        )
        for block, blocked_user in rows
    ]


@router.post("/{user_id}/block", response_model=BlockUserResponse)
async def block_user_route(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        result = await block_user(db, user.id, user_id)
        await db.commit()
        if result.changed:
            recipients = [user.id, result.target_user_id]
            if result.friendship_removed:
                await publish_friendship_update(
                    recipients, reason="friendship_removed"
                )
            if result.friend_requests_closed:
                await publish_friend_request_update(
                    recipients, reason="request_cancelled"
                )
            for group_id in result.affected_group_ids:
                await publish_group_invite_update(
                    recipients,
                    reason="invite_revoked",
                    group_id=group_id,
                )
        return BlockUserResponse(already_blocked=not result.changed)
    except ValueError as exc:
        await db.rollback()
        raise value_error(
            exc,
            code_statuses={"cannot_block_self": 400, "user_not_found": 404},
            detail_overrides={
                "cannot_block_self": "You cannot block yourself.",
                "user_not_found": "User not found.",
            },
        ) from exc


@router.delete("/{user_id}/block", response_model=BlockUserResponse)
async def unblock_user_route(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    changed = await unblock_user(db, user.id, user_id)
    await db.commit()
    if changed:
        await publish_friendship_update(
            [user.id, user_id], reason="block_removed"
        )
    return BlockUserResponse(already_blocked=False)
