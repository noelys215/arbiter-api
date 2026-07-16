from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import WebSocket, status

from app.core.config import settings


def normalize_websocket_origin(value: str | None) -> str | None:
    if not value or value != value.strip():
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return None
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    return f"{parsed.scheme.lower()}://{host}{f':{port}' if port is not None else ''}"


def websocket_origin_is_allowed(
    origin: str | None,
    *,
    allowed_origins: list[str] | None = None,
) -> bool:
    normalized = normalize_websocket_origin(origin)
    if normalized is None:
        return False
    configured = allowed_origins if allowed_origins is not None else settings.cors_origin_list()
    allowed = {
        candidate
        for value in configured
        if value != "*"
        if (candidate := normalize_websocket_origin(value)) is not None
    }
    return normalized in allowed


async def reject_disallowed_websocket_origin(websocket: WebSocket) -> bool:
    if websocket_origin_is_allowed(websocket.headers.get("origin")):
        return False
    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
    return True
