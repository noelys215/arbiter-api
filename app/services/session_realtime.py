from __future__ import annotations

import asyncio
from collections import defaultdict
from uuid import UUID

from fastapi import WebSocket


class SessionRealtimeHub:
    def __init__(self) -> None:
        self._connections: dict[UUID, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, session_id: UUID, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[session_id].add(websocket)

    async def disconnect(self, session_id: UUID, websocket: WebSocket) -> None:
        async with self._lock:
            sockets = self._connections.get(session_id)
            if not sockets:
                return
            sockets.discard(websocket)
            if not sockets:
                self._connections.pop(session_id, None)

    async def broadcast_session_updated(self, session_id: UUID, *, reason: str) -> None:
        async with self._lock:
            sockets = list(self._connections.get(session_id, set()))

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
                    current.discard(websocket)
                if not current:
                    self._connections.pop(session_id, None)


session_realtime_hub = SessionRealtimeHub()
