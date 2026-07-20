from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.maintenance.cleanup_social_invites import cleanup_social_invites
from app.models.friend_invite import FriendInvite
from app.models.group_invite import GroupInvite


pytestmark = pytest.mark.anyio


def _pair_key(first: str, second: str) -> str:
    return ":".join(sorted((first, second)))


async def test_database_rejects_duplicate_pending_social_requests(
    client, db_session, user_factory, login_helper
):
    owner = await user_factory(client)
    target = await user_factory(client)
    await login_helper(client, email=owner["email"], password=owner["password"])
    group = (await client.post("/groups", json={"name": "Constraint Club"})).json()

    owner_id = UUID(owner["id"])
    target_id = UUID(target["id"])
    now = datetime.now(timezone.utc)
    db_session.add(
        FriendInvite(
            created_by_user_id=owner_id,
            target_user_id=target_id,
            pair_key=_pair_key(owner["id"], target["id"]),
            expires_at=now + timedelta(days=1),
        )
    )
    db_session.add(
        GroupInvite(
            group_id=UUID(group["id"]),
            created_by_user_id=owner_id,
            target_user_id=target_id,
            expires_at=now + timedelta(days=1),
        )
    )
    await db_session.commit()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                FriendInvite(
                    created_by_user_id=target_id,
                    target_user_id=owner_id,
                    pair_key=_pair_key(owner["id"], target["id"]),
                    expires_at=now + timedelta(days=1),
                )
            )
            await db_session.flush()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                GroupInvite(
                    group_id=UUID(group["id"]),
                    created_by_user_id=owner_id,
                    target_user_id=target_id,
                    expires_at=now + timedelta(days=1),
                )
            )
            await db_session.flush()


async def test_cleanup_removes_only_social_invites_past_retention(
    client, db_session, user_factory, login_helper
):
    owner = await user_factory(client)
    target = await user_factory(client)
    await login_helper(client, email=owner["email"], password=owner["password"])
    group = (await client.post("/groups", json={"name": "Cleanup Club"})).json()

    owner_id = UUID(owner["id"])
    target_id = UUID(target["id"])
    old = datetime.now(timezone.utc) - timedelta(days=45)
    friend_invite = FriendInvite(
        created_by_user_id=owner_id,
        target_user_id=target_id,
        pair_key=_pair_key(owner["id"], target["id"]),
        expires_at=old,
        revoked_at=old,
        created_at=old,
    )
    group_invite = GroupInvite(
        group_id=UUID(group["id"]),
        created_by_user_id=owner_id,
        target_user_id=target_id,
        expires_at=old,
        created_at=old,
    )
    db_session.add_all([friend_invite, group_invite])
    await db_session.commit()
    friend_invite_id = friend_invite.id
    group_invite_id = group_invite.id

    result = await cleanup_social_invites(db_session, retention_days=30)

    assert result.friend_invites_deleted >= 1
    assert result.group_invites_deleted >= 1
    assert await db_session.scalar(
        sa.select(FriendInvite.id).where(FriendInvite.id == friend_invite_id)
    ) is None
    assert await db_session.scalar(
        sa.select(GroupInvite.id).where(GroupInvite.id == group_invite_id)
    ) is None
