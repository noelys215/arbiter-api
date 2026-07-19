from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Iterable
from typing import Literal, TypedDict
from uuid import UUID

from fastapi import WebSocket


logger = logging.getLogger(__name__)


class AccountRealtimeEvent(TypedDict, total=False):
    type: Literal[
        "friendship_updated",
        "friend_request_updated",
        "group_invite_updated",
        "group_updated",
        "profile_updated",
    ]
    reason: str
    group_id: str
    member_user_id: str
    user_id: str


def friendship_updated_event(*, reason: str) -> AccountRealtimeEvent:
    return {"type": "friendship_updated", "reason": reason}


def friend_request_updated_event(*, reason: str) -> AccountRealtimeEvent:
    return {"type": "friend_request_updated", "reason": reason}


def profile_updated_event(*, user_id: UUID) -> AccountRealtimeEvent:
    return {
        "type": "profile_updated",
        "reason": "display_name_updated",
        "user_id": str(user_id),
    }


def group_invite_updated_event(
    *, reason: str, group_id: UUID
) -> AccountRealtimeEvent:
    return {
        "type": "group_invite_updated",
        "reason": reason,
        "group_id": str(group_id),
    }


def group_updated_event(
    *,
    reason: str,
    group_id: UUID,
    member_user_id: UUID | None = None,
) -> AccountRealtimeEvent:
    event: AccountRealtimeEvent = {
        "type": "group_updated",
        "reason": reason,
        "group_id": str(group_id),
    }
    if member_user_id is not None:
        event["member_user_id"] = str(member_user_id)
    return event


class AccountRealtimeHub:
    def __init__(self) -> None:
        self._connections: dict[UUID, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, user_id: UUID, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[user_id].add(websocket)

    async def disconnect(self, user_id: UUID, websocket: WebSocket) -> None:
        async with self._lock:
            sockets = self._connections.get(user_id)
            if not sockets:
                return
            sockets.discard(websocket)
            if not sockets:
                self._connections.pop(user_id, None)

    async def broadcast_to_users(
        self,
        user_ids: Iterable[UUID],
        event: AccountRealtimeEvent,
    ) -> None:
        recipient_ids = set(user_ids)
        if not recipient_ids:
            return
        async with self._lock:
            sockets = [
                (user_id, websocket)
                for user_id in recipient_ids
                for websocket in self._connections.get(user_id, set())
            ]

        disconnected: list[tuple[UUID, WebSocket]] = []
        for user_id, websocket in sockets:
            try:
                await websocket.send_json(event)
            except Exception:
                disconnected.append((user_id, websocket))

        for user_id, websocket in disconnected:
            await self.disconnect(user_id, websocket)


account_realtime_hub = AccountRealtimeHub()


async def notify_account_users(
    user_ids: Iterable[UUID],
    event: AccountRealtimeEvent,
) -> None:
    try:
        await account_realtime_hub.broadcast_to_users(user_ids, event)
    except Exception:
        logger.exception("Account realtime broadcast failed for %s", event["type"])
