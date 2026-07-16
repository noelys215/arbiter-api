from __future__ import annotations

import asyncio
from collections import defaultdict
from uuid import UUID

from fastapi import WebSocket


class WatchlistRealtimeHub:
    def __init__(self) -> None:
        self._connections: dict[UUID, dict[WebSocket, UUID]] = defaultdict(dict)
        self._lock = asyncio.Lock()

    async def connect(
        self, group_id: UUID, user_id: UUID, websocket: WebSocket
    ) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[group_id][websocket] = user_id

    async def disconnect(self, group_id: UUID, websocket: WebSocket) -> None:
        async with self._lock:
            sockets = self._connections.get(group_id)
            if not sockets:
                return
            sockets.pop(websocket, None)
            if not sockets:
                self._connections.pop(group_id, None)

    async def broadcast_watchlist_updated(self, group_id: UUID, *, reason: str) -> None:
        async with self._lock:
            sockets = list(self._connections.get(group_id, {}))

        if not sockets:
            return

        payload = {
            "type": "watchlist_updated",
            "group_id": str(group_id),
            "reason": reason,
        }
        disconnected: list[WebSocket] = []
        for websocket in sockets:
            try:
                await websocket.send_json(payload)
            except RuntimeError:
                disconnected.append(websocket)

        if disconnected:
            async with self._lock:
                current = self._connections.get(group_id)
                if not current:
                    return
                for websocket in disconnected:
                    current.pop(websocket, None)
                if not current:
                    self._connections.pop(group_id, None)

    async def disconnect_user(self, group_id: UUID, user_id: UUID) -> None:
        async with self._lock:
            current = self._connections.get(group_id, {})
            sockets = [socket for socket, connected_user_id in current.items() if connected_user_id == user_id]
        for websocket in sockets:
            try:
                await websocket.close(code=1008)
            except Exception:
                pass
            await self.disconnect(group_id, websocket)

    async def disconnect_group(self, group_id: UUID) -> None:
        async with self._lock:
            sockets = list(self._connections.get(group_id, {}))
        for websocket in sockets:
            try:
                await websocket.close(code=1008)
            except Exception:
                pass
            await self.disconnect(group_id, websocket)


watchlist_realtime_hub = WatchlistRealtimeHub()
