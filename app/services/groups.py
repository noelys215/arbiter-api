from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.group import Group
from app.models.group_membership import GroupMembership
from app.models.group_invite import GroupInvite
from app.models.friendship import Friendship
from app.models.user import User
from app.services.invitations import ensure_invite_active, terminate_invite


@dataclass(frozen=True)
class GroupInviteMutationResult:
    already_member: bool
    changed: bool
    group_id: UUID
    created_by_user_id: UUID
    target_user_id: UUID


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def _is_friend(db: AsyncSession, a: UUID, b: UUID) -> bool:
    low, high = (a, b) if a < b else (b, a)
    q = sa.select(sa.literal(True)).select_from(Friendship).where(
        Friendship.user_low_id == low,
        Friendship.user_high_id == high,
    )
    return (await db.execute(q)).scalar_one_or_none() is True


async def create_group(db: AsyncSession, owner_id: UUID, name: str, member_user_ids: list[UUID]) -> Group:
    # de-dupe and remove owner if included
    members = []
    seen: set[UUID] = set()
    for uid in member_user_ids:
        if uid == owner_id:
            continue
        if uid in seen:
            continue
        seen.add(uid)
        members.append(uid)

    # validate all are accepted friends
    for uid in members:
        ok = await _is_friend(db, owner_id, uid)
        if not ok:
            raise ValueError(f"User {uid} is not an accepted friend")

    group = Group(name=name, owner_id=owner_id)
    db.add(group)
    await db.flush()  # get group.id

    # owner membership
    db.add(GroupMembership(group_id=group.id, user_id=owner_id))

    # friend memberships
    for uid in members:
        db.add(GroupMembership(group_id=group.id, user_id=uid))

    await db.commit()
    await db.refresh(group)
    return group


async def list_groups_for_user(db: AsyncSession, user_id: UUID) -> list[dict]:
    # return (group + member_count)
    gm = aliased(GroupMembership)
    q = (
        sa.select(
            Group.id,
            Group.name,
            Group.owner_id,
            Group.created_at,
            sa.func.count(gm.id).label("member_count"),
        )
        .join(GroupMembership, GroupMembership.group_id == Group.id)
        .join(gm, gm.group_id == Group.id)
        .where(GroupMembership.user_id == user_id)
        .group_by(Group.id)
        .order_by(Group.created_at.desc())
    )
    rows = (await db.execute(q)).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "owner_id": r.owner_id,
            "created_at": r.created_at,
            "member_count": int(r.member_count),
        }
        for r in rows
    ]


async def _ensure_membership(db: AsyncSession, group_id: UUID, user_id: UUID) -> None:
    q = sa.select(GroupMembership.id).where(
        GroupMembership.group_id == group_id,
        GroupMembership.user_id == user_id,
    )
    if (await db.execute(q)).scalar_one_or_none() is None:
        raise PermissionError("Not a member of this group")


async def get_group_detail(db: AsyncSession, group_id: UUID, user_id: UUID) -> dict:
    await _ensure_membership(db, group_id, user_id)

    g = (await db.execute(sa.select(Group).where(Group.id == group_id))).scalar_one()

    q = (
        sa.select(User)
        .join(GroupMembership, GroupMembership.user_id == User.id)
        .where(GroupMembership.group_id == group_id)
        .order_by(User.username.asc())
    )
    members = (await db.execute(q)).scalars().all()

    return {
        "id": g.id,
        "name": g.name,
        "owner_id": g.owner_id,
        "created_at": g.created_at,
        "members": members,
    }


async def update_group_name(
    db: AsyncSession,
    *,
    group_id: UUID,
    owner_id: UUID,
    name: str,
) -> Group:
    group = (
        await db.execute(sa.select(Group).where(Group.id == group_id))
    ).scalar_one_or_none()
    if group is None:
        raise ValueError("not_found")
    if group.owner_id != owner_id:
        raise PermissionError("Only the group owner can change the group name")

    group.name = name
    await db.flush()
    return group


async def create_group_invitation(
    db: AsyncSession,
    *,
    group_id: UUID,
    creator_id: UUID,
    target_user_id: UUID,
    ttl_days: int = 7,
) -> GroupInvite:
    group = (
        await db.execute(sa.select(Group).where(Group.id == group_id))
    ).scalar_one_or_none()
    if group is None:
        raise ValueError("group_not_found")
    if group.owner_id != creator_id:
        raise PermissionError("Only the group owner can create invitations")

    if target_user_id == creator_id:
        raise ValueError("already_member")
    if not await _is_friend(db, creator_id, target_user_id):
        raise ValueError("target_not_friend")
    membership = (
        await db.execute(
            sa.select(GroupMembership.id).where(
                GroupMembership.group_id == group_id,
                GroupMembership.user_id == target_user_id,
            )
        )
    ).scalar_one_or_none()
    if membership is not None:
        raise ValueError("already_member")

    pending = (
        await db.execute(
            sa.select(GroupInvite.id).where(
                GroupInvite.group_id == group_id,
                GroupInvite.target_user_id == target_user_id,
                GroupInvite.revoked_at.is_(None),
                GroupInvite.expires_at > _now_utc(),
                GroupInvite.uses_count < GroupInvite.max_uses,
            )
        )
    ).scalar_one_or_none()
    if pending is not None:
        raise ValueError("invite_already_pending")

    invite = GroupInvite(
        group_id=group_id,
        created_by_user_id=creator_id,
        target_user_id=target_user_id,
        expires_at=_now_utc() + timedelta(days=ttl_days),
        max_uses=1,
        uses_count=0,
    )
    db.add(invite)
    await db.flush()
    return invite


async def _accept_group_invite_record(
    db: AsyncSession,
    user_id: UUID,
    invite: GroupInvite,
) -> GroupInviteMutationResult:
    ensure_invite_active(invite)
    if invite.target_user_id != user_id:
        raise PermissionError("This invitation belongs to another user")

    membership = (
        await db.execute(
            sa.select(GroupMembership.id).where(
                GroupMembership.group_id == invite.group_id,
                GroupMembership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if membership is not None:
        return GroupInviteMutationResult(
            already_member=True,
            changed=False,
            group_id=invite.group_id,
            created_by_user_id=invite.created_by_user_id,
            target_user_id=invite.target_user_id,
        )
    if invite.uses_count >= invite.max_uses:
        raise ValueError("used_invite")

    db.add(GroupMembership(group_id=invite.group_id, user_id=user_id))
    invite.uses_count += 1
    await db.flush()
    return GroupInviteMutationResult(
        already_member=False,
        changed=True,
        group_id=invite.group_id,
        created_by_user_id=invite.created_by_user_id,
        target_user_id=invite.target_user_id,
    )


async def list_group_invitations(
    db: AsyncSession,
    *,
    current_user_id: UUID,
    group_id: UUID | None,
) -> list[tuple[GroupInvite, Group, User, User]]:
    target = aliased(User)
    query = (
        sa.select(GroupInvite, Group, User, target)
        .join(Group, Group.id == GroupInvite.group_id)
        .join(User, User.id == GroupInvite.created_by_user_id)
        .join(target, target.id == GroupInvite.target_user_id)
        .where(
            GroupInvite.revoked_at.is_(None),
            GroupInvite.expires_at > _now_utc(),
            GroupInvite.uses_count < GroupInvite.max_uses,
        )
        .order_by(GroupInvite.created_at.desc())
    )
    if group_id is None:
        query = query.where(GroupInvite.target_user_id == current_user_id)
    else:
        group = (
            await db.execute(sa.select(Group).where(Group.id == group_id))
        ).scalar_one_or_none()
        if group is None:
            raise ValueError("group_not_found")
        if group.owner_id != current_user_id:
            raise PermissionError("Only the group owner can view outgoing invitations")
        query = query.where(GroupInvite.group_id == group_id)
    return list((await db.execute(query)).all())


async def decide_group_invitation(
    db: AsyncSession,
    *,
    current_user_id: UUID,
    invite_id: UUID,
    decision: str,
) -> GroupInviteMutationResult:
    invite = (
        await db.execute(
            sa.select(GroupInvite).where(GroupInvite.id == invite_id).with_for_update()
        )
    ).scalar_one_or_none()
    if invite is None or invite.target_user_id != current_user_id:
        raise ValueError("invalid_invite")
    if decision == "decline":
        if invite.revoked_at is not None:
            return GroupInviteMutationResult(
                already_member=False,
                changed=False,
                group_id=invite.group_id,
                created_by_user_id=invite.created_by_user_id,
                target_user_id=invite.target_user_id,
            )
        ensure_invite_active(invite)
        terminate_invite(invite)
        await db.flush()
        return GroupInviteMutationResult(
            already_member=False,
            changed=True,
            group_id=invite.group_id,
            created_by_user_id=invite.created_by_user_id,
            target_user_id=invite.target_user_id,
        )
    return await _accept_group_invite_record(db, current_user_id, invite)


async def revoke_group_invitation(
    db: AsyncSession,
    *,
    current_user_id: UUID,
    invite_id: UUID,
) -> tuple[GroupInvite, bool]:
    invite = (
        await db.execute(
            sa.select(GroupInvite).where(GroupInvite.id == invite_id).with_for_update()
        )
    ).scalar_one_or_none()
    if invite is None:
        raise ValueError("invalid_invite")
    group = (
        await db.execute(sa.select(Group).where(Group.id == invite.group_id))
    ).scalar_one_or_none()
    if group is None:
        raise ValueError("invalid_invite")
    if invite.created_by_user_id != current_user_id and group.owner_id != current_user_id:
        raise PermissionError("Only the invite creator or group owner can revoke it")
    changed = invite.revoked_at is None
    if changed:
        terminate_invite(invite)
    await db.flush()
    return invite, changed


async def list_group_member_ids(
    db: AsyncSession, group_id: UUID
) -> list[UUID]:
    rows = await db.execute(
        sa.select(GroupMembership.user_id).where(
            GroupMembership.group_id == group_id
        )
    )
    return list(rows.scalars().all())


async def leave_group(db: AsyncSession, group_id: UUID, user_id: UUID) -> None:
    await _ensure_membership(db, group_id, user_id)

    g = (await db.execute(sa.select(Group).where(Group.id == group_id))).scalar_one()
    if g.owner_id == user_id:
        raise ValueError("owner_cannot_leave")

    membership = (
        await db.execute(
            sa.select(GroupMembership).where(
                GroupMembership.group_id == group_id,
                GroupMembership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()

    if membership is None:
        raise PermissionError("Not a member of this group")

    await db.delete(membership)


async def delete_group(db: AsyncSession, group_id: UUID, user_id: UUID) -> None:
    g = (await db.execute(sa.select(Group).where(Group.id == group_id))).scalar_one_or_none()
    if g is None:
        raise ValueError("not_found")
    if g.owner_id != user_id:
        raise PermissionError("Only the group owner can delete the group")

    await db.delete(g)


async def add_group_members(
    db: AsyncSession,
    *,
    group_id: UUID,
    owner_id: UUID,
    member_user_ids: list[UUID],
) -> tuple[list[UUID], list[UUID]]:
    g = (await db.execute(sa.select(Group).where(Group.id == group_id))).scalar_one_or_none()
    if g is None:
        raise ValueError("not_found")
    if g.owner_id != owner_id:
        raise PermissionError("Only the group owner can add members")

    # De-dupe, remove owner, and remove existing members
    seen: set[UUID] = set()
    candidates: list[UUID] = []
    for uid in member_user_ids:
        if uid == owner_id or uid in seen:
            continue
        seen.add(uid)
        candidates.append(uid)

    existing_rows = await db.execute(
        sa.select(GroupMembership.user_id).where(GroupMembership.group_id == group_id)
    )
    existing = {row[0] for row in existing_rows.all()}

    to_add = [uid for uid in candidates if uid not in existing]
    skipped = [uid for uid in candidates if uid in existing]

    # Validate friendship and add memberships
    for uid in to_add:
        ok = await _is_friend(db, owner_id, uid)
        if not ok:
            raise ValueError(f"User {uid} is not an accepted friend")
        db.add(GroupMembership(group_id=group_id, user_id=uid))

    return to_add, skipped
