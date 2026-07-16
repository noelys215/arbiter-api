from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings

RESEND_API_URL = "https://api.resend.com/emails"


async def send_resend_email(
    payload: dict[str, Any],
    *,
    idempotency_key: str | None = None,
) -> None:
    headers = {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Content-Type": "application/json",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            response = await client.post(RESEND_API_URL, json=payload, headers=headers)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            message = "Email provider rejected the request"
            try:
                body = exc.response.json()
                if isinstance(body, dict) and isinstance(body.get("message"), str):
                    parsed = body["message"].strip()
                    if parsed:
                        message = parsed
            except ValueError:
                raw = exc.response.text.strip()
                if raw:
                    message = raw
            raise RuntimeError(message) from exc
        except httpx.RequestError as exc:
            raise RuntimeError("Could not reach email provider") from exc
