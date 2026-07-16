from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.api.deps import get_current_user, get_db
from app.api.http_errors import permission_error, value_error
from app.api.presenters.users import public_user_from_user
from app.models.user import User
from app.schemas.groups import (
    CreateGroupRequest,
    UpdateGroupRequest,
    GroupListItem,
    GroupDetailResponse,
    GroupInviteResponse,
    CreateGroupInviteRequest,
    GroupLinkInviteResponse,
    AcceptGroupInviteRequest,
    LeaveGroupResponse,
    DeleteGroupResponse,
    AddGroupMembersRequest,
    AddGroupMembersResponse,
)
from app.services.groups import (
    create_group,
    list_groups_for_user,
    get_group_detail,
    create_group_invite,
    create_group_link_invite,
    accept_group_invite,
    leave_group,
    delete_group,
    add_group_members,
    list_group_member_ids,
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
        g = await create_group(db, user.id, payload.name, payload.member_user_ids)
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
            member_count=1 + len({uid for uid in payload.member_user_ids if uid != user.id}),
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


@router.post("/{group_id}/invite", response_model=GroupInviteResponse, status_code=201)
async def create_group_invite_route(
    group_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        inv = await create_group_invite(db, group_id, user.id, ttl_minutes=60)
        await db.commit()
        return GroupInviteResponse(
            code=inv.code,
            expires_at=inv.expires_at,
            max_uses=inv.max_uses,
            uses_count=inv.uses_count,
        )
    except PermissionError as e:
        await db.rollback()
        raise permission_error(e) from e


@router.post(
    "/{group_id}/invites",
    response_model=GroupLinkInviteResponse,
    status_code=201,
)
async def create_group_link_invite_route(
    group_id: UUID,
    payload: CreateGroupInviteRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        invite, token = await create_group_link_invite(
            db,
            group_id=group_id,
            creator_id=user.id,
            target_user_id=payload.target_user_id,
            max_uses=payload.max_uses,
        )
        await db.commit()
        if invite.target_user_id is not None:
            await publish_group_invite_update(
                [user.id, invite.target_user_id],
                reason="targeted_invite_created",
                group_id=group_id,
            )
        return GroupLinkInviteResponse(
            id=invite.id,
            token=token,
            code=invite.code,
            group_id=invite.group_id,
            target_user_id=invite.target_user_id,
            expires_at=invite.expires_at,
            max_uses=invite.max_uses,
            uses_count=invite.uses_count,
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
            },
            default_detail="Could not create invitation",
        ) from exc


@router.post("/accept-invite", status_code=200)
async def accept_group_invite_route(
    payload: AcceptGroupInviteRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        result = await accept_group_invite(db, user.id, payload.code)
        member_ids = (
            await list_group_member_ids(db, result.group_id)
            if result.changed
            else []
        )
        await db.commit()
        if result.changed:
            await publish_group_invite_update(
                [user.id, result.created_by_user_id],
                reason="invite_accepted",
                group_id=result.group_id,
            )
            await publish_group_update(
                member_ids,
                reason="membership_created",
                group_id=result.group_id,
                member_user_id=user.id,
            )
        return {"ok": True}
    except ValueError as e:
        await db.rollback()
        raise value_error(
            e,
            code_statuses={
                "invalid_invite": 404,
                "expired_invite": 410,
                "revoked_invite": 410,
                "used_invite": 409,
            },
        ) from e


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


@router.post("/{group_id}/members", response_model=AddGroupMembersResponse, status_code=200)
async def add_group_members_route(
    group_id: UUID,
    payload: AddGroupMembersRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        added, skipped = await add_group_members(
            db,
            group_id=group_id,
            owner_id=user.id,
            member_user_ids=payload.member_user_ids,
        )
        member_ids = await list_group_member_ids(db, group_id)
        await db.commit()
        for added_user_id in added:
            await publish_group_update(
                member_ids,
                reason="membership_created",
                group_id=group_id,
                member_user_id=added_user_id,
            )
        return AddGroupMembersResponse(ok=True, added_user_ids=added, skipped_user_ids=skipped)
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
