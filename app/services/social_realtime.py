from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from app.services.account_realtime import (
    friendship_updated_event,
    group_invite_updated_event,
    group_updated_event,
    notify_account_users,
    profile_updated_event,
)
from app.services.session_realtime import session_realtime_hub
from app.services.watchlist_realtime import watchlist_realtime_hub


async def publish_friendship_update(
    user_ids: Iterable[UUID], *, reason: str
) -> None:
    await notify_account_users(user_ids, friendship_updated_event(reason=reason))


async def publish_profile_update(
    user_ids: Iterable[UUID], *, user_id: UUID
) -> None:
    await notify_account_users(user_ids, profile_updated_event(user_id=user_id))


async def publish_group_invite_update(
    user_ids: Iterable[UUID], *, reason: str, group_id: UUID
) -> None:
    await notify_account_users(
        user_ids,
        group_invite_updated_event(reason=reason, group_id=group_id),
    )


async def publish_group_update(
    user_ids: Iterable[UUID],
    *,
    reason: str,
    group_id: UUID,
    member_user_id: UUID | None = None,
) -> None:
    await notify_account_users(
        user_ids,
        group_updated_event(
            reason=reason,
            group_id=group_id,
            member_user_id=member_user_id,
        ),
    )


async def revoke_group_socket_access(group_id: UUID, user_id: UUID) -> None:
    await watchlist_realtime_hub.disconnect_user(group_id, user_id)
    await session_realtime_hub.disconnect_group_user(group_id, user_id)


async def close_deleted_group_sockets(group_id: UUID) -> None:
    await watchlist_realtime_hub.disconnect_group(group_id)
    await session_realtime_hub.disconnect_group(group_id)
