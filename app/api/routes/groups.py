from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.api.deps import get_current_user, get_db
from app.api.http_errors import permission_error, value_error
from app.api.social_rate_limits import enforce_social_rate_limit
from app.api.presenters.users import public_user_from_user
from app.models.user import User
from app.schemas.groups import (
    CreateGroupRequest,
    UpdateGroupRequest,
    GroupListItem,
    GroupDetailResponse,
    CreateGroupInviteRequest,
    GroupInviteCreateResponse,
    LeaveGroupResponse,
    DeleteGroupResponse,
    TransferGroupOwnershipRequest,
)
from app.services.groups import (
    create_group,
    list_groups_for_user,
    get_group_detail,
    create_group_invitation,
    leave_group,
    delete_group,
    list_group_member_ids,
    transfer_group_ownership,
    update_group_name,
)
from app.services.social_realtime import (
    close_deleted_group_sockets,
    publish_group_invite_update,
    publish_group_update,
    revoke_group_socket_access,
)

router = APIRouter(prefix="/groups", tags=["groups"])


@router.post("", response_model=GroupListItem, status_code=201)
async def create_group_route(
    payload: CreateGroupRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        g = await create_group(db, user.id, payload.name)
        member_ids = await list_group_member_ids(db, g.id)
        await publish_group_update(
            member_ids,
            reason="membership_created",
            group_id=g.id,
        )
        # return list-shape
        return GroupListItem(
            id=g.id,
            name=g.name,
            owner_id=g.owner_id,
            created_at=g.created_at,
            member_count=1,
        )
    except ValueError as e:
        raise value_error(e) from e


@router.get("", response_model=list[GroupListItem])
async def list_groups_route(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = await list_groups_for_user(db, user.id)
    return [GroupListItem(**r) for r in rows]


@router.get("/{group_id}", response_model=GroupDetailResponse)
async def group_detail_route(
    group_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        data = await get_group_detail(db, group_id, user.id)
        return GroupDetailResponse(
            id=data["id"],
            name=data["name"],
            owner_id=data["owner_id"],
            created_at=data["created_at"],
            members=[public_user_from_user(m) for m in data["members"]],
        )
    except PermissionError as e:
        raise permission_error(e) from e


@router.patch("/{group_id}", response_model=GroupListItem)
async def update_group_route(
    group_id: UUID,
    payload: UpdateGroupRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        group = await update_group_name(
            db,
            group_id=group_id,
            owner_id=user.id,
            name=payload.name,
        )
        member_ids = await list_group_member_ids(db, group_id)
        await db.commit()
        await db.refresh(group)
        await publish_group_update(
            member_ids,
            reason="group_renamed",
            group_id=group_id,
        )
        return GroupListItem(
            id=group.id,
            name=group.name,
            owner_id=group.owner_id,
            created_at=group.created_at,
            member_count=len(member_ids),
        )
    except PermissionError as e:
        await db.rollback()
        raise permission_error(e) from e
    except ValueError as e:
        await db.rollback()
        raise value_error(
            e,
            code_statuses={"not_found": 404},
            detail_overrides={"not_found": "Group not found"},
        ) from e


@router.post(
    "/{group_id}/invites",
    response_model=GroupInviteCreateResponse,
    status_code=201,
)
async def create_group_invitation_route(
    group_id: UUID,
    request: Request,
    payload: CreateGroupInviteRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        await enforce_social_rate_limit(request, user=user, action="group_invite")
        invite = await create_group_invitation(
            db,
            group_id=group_id,
            creator_id=user.id,
            target_user_id=payload.target_user_id,
        )
        await db.commit()
        await publish_group_invite_update(
            [user.id, invite.target_user_id],
            reason="targeted_invite_created",
            group_id=group_id,
        )
        return GroupInviteCreateResponse(
            id=invite.id,
            group_id=invite.group_id,
            target_user_id=invite.target_user_id,
            expires_at=invite.expires_at,
        )
    except PermissionError as exc:
        await db.rollback()
        raise permission_error(exc) from exc
    except ValueError as exc:
        await db.rollback()
        raise value_error(
            exc,
            code_statuses={
                "group_not_found": 404,
                "target_not_friend": 400,
                "already_member": 409,
                "invite_already_pending": 409,
                "target_unavailable": 400,
            },
            default_detail="Could not create invitation",
        ) from exc


@router.post("/{group_id}/leave", response_model=LeaveGroupResponse, status_code=200)
async def leave_group_route(
    group_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        recipient_ids = await list_group_member_ids(db, group_id)
        await leave_group(db, group_id, user.id)
        await db.commit()
        await publish_group_update(
            recipient_ids,
            reason="membership_removed",
            group_id=group_id,
            member_user_id=user.id,
        )
        await revoke_group_socket_access(group_id, user.id)
        return LeaveGroupResponse(ok=True)
    except PermissionError as e:
        await db.rollback()
        raise permission_error(e) from e
    except ValueError as e:
        await db.rollback()
        raise value_error(
            e,
            phrase_statuses={"owner_cannot_leave": 400},
            detail_overrides={
                "owner_cannot_leave": "Group owner cannot leave their own group"
            },
        ) from e


@router.delete("/{group_id}", response_model=DeleteGroupResponse, status_code=200)
async def delete_group_route(
    group_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        recipient_ids = await list_group_member_ids(db, group_id)
        await delete_group(db, group_id, user.id)
        await db.commit()
        await publish_group_update(
            recipient_ids,
            reason="group_deleted",
            group_id=group_id,
        )
        await close_deleted_group_sockets(group_id)
        return DeleteGroupResponse(ok=True)
    except PermissionError as e:
        await db.rollback()
        raise permission_error(e) from e
    except ValueError as e:
        await db.rollback()
        raise value_error(
            e,
            code_statuses={"not_found": 404},
            detail_overrides={"not_found": "Group not found"},
        ) from e


@router.post("/{group_id}/transfer-ownership", response_model=GroupListItem)
async def transfer_group_ownership_route(
    group_id: UUID,
    payload: TransferGroupOwnershipRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        result = await transfer_group_ownership(
            db,
            group_id=group_id,
            current_owner_id=user.id,
            new_owner_id=payload.new_owner_user_id,
        )
        await db.commit()
        await db.refresh(result.group)
        await publish_group_update(
            result.member_user_ids,
            reason="ownership_transferred",
            group_id=group_id,
            member_user_id=payload.new_owner_user_id,
        )
        return GroupListItem(
            id=result.group.id,
            name=result.group.name,
            owner_id=result.group.owner_id,
            created_at=result.group.created_at,
            member_count=len(result.member_user_ids),
        )
    except PermissionError as e:
        await db.rollback()
        raise permission_error(e) from e
    except ValueError as e:
        await db.rollback()
        raise value_error(
            e,
            code_statuses={
                "not_found": 404,
                "already_owner": 409,
                "new_owner_not_member": 400,
            },
            detail_overrides={
                "not_found": "Group not found",
                "already_owner": "That person already owns this group.",
                "new_owner_not_member": "The new owner must already be a group member.",
            },
        ) from e
