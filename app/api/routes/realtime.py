from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.api.deps import COOKIE_NAME, get_db, get_user_from_access_token
from app.core.websocket_security import reject_disallowed_websocket_origin
from app.services.account_realtime import account_realtime_hub


router = APIRouter(tags=["realtime"])


@router.websocket("/me/ws")
async def account_updates_ws(websocket: WebSocket):
    if await reject_disallowed_websocket_origin(websocket):
        return

    access_token = websocket.cookies.get(COOKIE_NAME)
    async for db in get_db():
        try:
            user = await get_user_from_access_token(db, access_token)
        except Exception:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        break

    await account_realtime_hub.connect(user.id, websocket)
    try:
        await websocket.send_json({"type": "account_connected"})
        while True:
            message = await websocket.receive_json()
            if isinstance(message, dict) and message.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        await account_realtime_hub.disconnect(user.id, websocket)
