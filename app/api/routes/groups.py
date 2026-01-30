from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.groups import (
    CreateGroupRequest,
    GroupListItem,
    GroupDetailResponse,
    GroupInviteResponse,
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
    accept_group_invite,
    leave_group,
    delete_group,
    add_group_members,
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
        # return list-shape
        return GroupListItem(
            id=g.id,
            name=g.name,
            owner_id=g.owner_id,
            created_at=g.created_at,
            member_count=1 + len({uid for uid in payload.member_user_ids if uid != user.id}),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


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
            members=[
                {
                    "id": m.id,
                    "email": m.email,
                    "username": m.username,
                    "display_name": m.display_name,
                    "avatar_url": m.avatar_url,
                }
                for m in data["members"]
            ],
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/{group_id}/invite", response_model=GroupInviteResponse, status_code=201)
async def create_group_invite_route(
    group_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        inv = await create_group_invite(db, group_id, user.id, ttl_minutes=60)
        return GroupInviteResponse(
            code=inv.code,
            expires_at=inv.expires_at,
            max_uses=inv.max_uses,
            uses_count=inv.uses_count,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/accept-invite", status_code=200)
async def accept_group_invite_route(
    payload: AcceptGroupInviteRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        await accept_group_invite(db, user.id, payload.code)
        return {"ok": True}
    except ValueError as e:
        msg = str(e).lower()
        if "invalid" in msg:
            raise HTTPException(status_code=404, detail=str(e))
        if "expired" in msg:
            raise HTTPException(status_code=410, detail=str(e))
        if "used" in msg:
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{group_id}/leave", response_model=LeaveGroupResponse, status_code=200)
async def leave_group_route(
    group_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        await leave_group(db, group_id, user.id)
        await db.commit()
        return LeaveGroupResponse(ok=True)
    except PermissionError as e:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        await db.rollback()
        msg = str(e).lower()
        if "owner_cannot_leave" in msg:
            raise HTTPException(status_code=400, detail="Group owner cannot leave their own group")
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{group_id}", response_model=DeleteGroupResponse, status_code=200)
async def delete_group_route(
    group_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        await delete_group(db, group_id, user.id)
        await db.commit()
        return DeleteGroupResponse(ok=True)
    except PermissionError as e:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        await db.rollback()
        if str(e) == "not_found":
            raise HTTPException(status_code=404, detail="Group not found")
        raise HTTPException(status_code=400, detail=str(e))


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
        await db.commit()
        return AddGroupMembersResponse(ok=True, added_user_ids=added, skipped_user_ids=skipped)
    except PermissionError as e:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        await db.rollback()
        if str(e) == "not_found":
            raise HTTPException(status_code=404, detail="Group not found")
        raise HTTPException(status_code=400, detail=str(e))

