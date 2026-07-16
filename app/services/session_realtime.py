from __future__ import annotations

import asyncio
from collections import defaultdict
from uuid import UUID

from fastapi import WebSocket


class SessionConnection:
    def __init__(self, *, user_id: UUID, group_id: UUID) -> None:
        self.user_id = user_id
        self.group_id = group_id


class SessionRealtimeHub:
    def __init__(self) -> None:
        self._connections: dict[UUID, dict[WebSocket, SessionConnection]] = defaultdict(dict)
        self._lock = asyncio.Lock()

    async def connect(
        self,
        session_id: UUID,
        user_id: UUID,
        group_id: UUID,
        websocket: WebSocket,
    ) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[session_id][websocket] = SessionConnection(
                user_id=user_id,
                group_id=group_id,
            )

    async def disconnect(self, session_id: UUID, websocket: WebSocket) -> None:
        async with self._lock:
            sockets = self._connections.get(session_id)
            if not sockets:
                return
            sockets.pop(websocket, None)
            if not sockets:
                self._connections.pop(session_id, None)

    async def broadcast_session_updated(self, session_id: UUID, *, reason: str) -> None:
        async with self._lock:
            sockets = list(self._connections.get(session_id, {}))

        if not sockets:
            return

        payload = {
            "type": "session_updated",
            "session_id": str(session_id),
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
                current = self._connections.get(session_id)
                if not current:
                    return
                for websocket in disconnected:
                    current.pop(websocket, None)
                if not current:
                    self._connections.pop(session_id, None)

    async def disconnect_group_user(self, group_id: UUID, user_id: UUID) -> None:
        async with self._lock:
            sockets = [
                (session_id, websocket)
                for session_id, current in self._connections.items()
                for websocket, connection in current.items()
                if connection.group_id == group_id and connection.user_id == user_id
            ]
        for session_id, websocket in sockets:
            try:
                await websocket.close(code=1008)
            except Exception:
                pass
            await self.disconnect(session_id, websocket)

    async def disconnect_group(self, group_id: UUID) -> None:
        async with self._lock:
            sockets = [
                (session_id, websocket)
                for session_id, current in self._connections.items()
                for websocket, connection in current.items()
                if connection.group_id == group_id
            ]
        for session_id, websocket in sockets:
            try:
                await websocket.close(code=1008)
            except Exception:
                pass
            await self.disconnect(session_id, websocket)


session_realtime_hub = SessionRealtimeHub()
