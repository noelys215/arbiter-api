from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.http_errors import permission_error, value_error
from app.api.presenters.users import invite_user_from_user
from app.models.user import User
from app.schemas.groups import (
    GroupInvitationListItem,
    GroupInviteDecisionRequest,
    GroupInviteDecisionResponse,
)
from app.services.groups import (
    decide_group_invitation,
    list_group_invitations,
    revoke_group_invitation,
)

router = APIRouter(prefix="/group-invites", tags=["group invitations"])


@router.get("", response_model=list[GroupInvitationListItem])
async def get_group_invitations(
    group_id: UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        rows = await list_group_invitations(
            db,
            current_user_id=user.id,
            group_id=group_id,
        )
        return [
            GroupInvitationListItem(
                id=invite.id,
                group_id=group.id,
                group_name=group.name,
                inviter=invite_user_from_user(inviter),
                target=invite_user_from_user(target) if target else None,
                expires_at=invite.expires_at,
                max_uses=invite.max_uses,
                uses_count=invite.uses_count,
                targeted=invite.target_user_id is not None,
            )
            for invite, group, inviter, target in rows
        ]
    except PermissionError as exc:
        raise permission_error(exc) from exc
    except ValueError as exc:
        raise value_error(exc, code_statuses={"group_not_found": 404}) from exc


@router.post(
    "/{invite_id}/decision",
    response_model=GroupInviteDecisionResponse,
)
async def decide_group_invite(
    invite_id: UUID,
    payload: GroupInviteDecisionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        already_member = await decide_group_invitation(
            db,
            current_user_id=user.id,
            invite_id=invite_id,
            decision=payload.decision,
        )
        await db.commit()
        return GroupInviteDecisionResponse(
            ok=True,
            decision="accepted" if payload.decision == "accept" else "declined",
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
            default_detail="Could not update invitation",
        ) from exc


@router.delete("/{invite_id}", status_code=204)
async def revoke_group_invite(
    invite_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        await revoke_group_invitation(
            db,
            current_user_id=user.id,
            invite_id=invite_id,
        )
        await db.commit()
    except PermissionError as exc:
        await db.rollback()
        raise permission_error(exc) from exc
    except ValueError as exc:
        await db.rollback()
        raise value_error(exc, code_statuses={"invalid_invite": 404}) from exc
