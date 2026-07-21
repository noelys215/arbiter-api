from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.config import settings
from app.core.websocket_security import normalize_websocket_origin

AsgiApp = Callable[
    [
        dict[str, Any],
        Callable[..., Awaitable[dict[str, Any]]],
        Callable[..., Awaitable[None]],
    ],
    Awaitable[None],
]

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class SecurityBoundaryMiddleware:
    """Enforce browser-origin and request-size policy before request parsing."""

    def __init__(self, app: AsgiApp, max_body_bytes: int = 64 * 1024) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        method = str(scope.get("method", "GET")).upper()
        if method in UNSAFE_METHODS and not self._origin_allowed(headers):
            await self._reject(send, 403, "Request origin is not allowed")
            return

        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                if int(raw_length) < 0 or int(raw_length) > self.max_body_bytes:
                    await self._reject(send, 413, "Request is too large")
                    return
            except ValueError:
                await self._reject(send, 413, "Request is too large")
                return

        total = 0

        async def bounded_receive():
            nonlocal total
            message = await receive()
            if message.get("type") == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_body_bytes:
                    raise _RequestTooLarge
            return message

        async def hardened_send(message):
            if message.get("type") == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.extend(
                    [
                        (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                        (b"x-frame-options", b"DENY"),
                    ]
                )
                if not settings.is_local_env():
                    response_headers.append(
                        (b"strict-transport-security", b"max-age=63072000; includeSubDomains")
                    )
                path = str(scope.get("path", ""))
                has_cache_control = any(
                    name.lower() == b"cache-control" for name, _ in response_headers
                )
                if path != "/health" and not has_cache_control:
                    response_headers.append((b"cache-control", b"no-store"))
                message["headers"] = response_headers
            await send(message)

        try:
            await self.app(scope, bounded_receive, hardened_send)
        except _RequestTooLarge:
            await self._reject(send, 413, "Request is too large")

    @staticmethod
    def _origin_allowed(headers: dict[bytes, bytes]) -> bool:
        raw_origin = headers.get(b"origin")
        if raw_origin is None:
            # Tests, health tooling, and local CLI clients need no synthetic Origin.
            return settings.is_local_env()
        try:
            origin = raw_origin.decode("ascii")
        except UnicodeDecodeError:
            return False
        normalized = normalize_websocket_origin(origin)
        allowed = {
            candidate
            for value in settings.cors_origin_list()
            if value != "*"
            if (candidate := normalize_websocket_origin(value)) is not None
        }
        return normalized is not None and normalized in allowed

    @staticmethod
    async def _reject(send, status_code: int, detail: str) -> None:
        body = json.dumps({"detail": detail}).encode()
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store"),
            (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"no-referrer"),
            (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
            (b"x-frame-options", b"DENY"),
        ]
        if not settings.is_local_env():
            headers.append(
                (b"strict-transport-security", b"max-age=63072000; includeSubDomains")
            )
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": headers,
            }
        )
        await send({"type": "http.response.body", "body": body})


class _RequestTooLarge(Exception):
    pass
