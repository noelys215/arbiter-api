from __future__ import annotations

from urllib.parse import quote_plus

import httpx

from app.core.config import settings

RESEND_API_URL = "https://api.resend.com/emails"


def magic_link_email_configured() -> bool:
    return bool((settings.resend_api_key or "").strip() and (settings.resend_from_email or "").strip())


def build_magic_link(token: str) -> str:
    base = settings.magic_link_verify_url.strip()
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}token={quote_plus(token)}"


async def send_magic_link_email(*, to_email: str, magic_link_url: str) -> None:
    payload = {
        "from": settings.resend_from_email,
        "to": [to_email],
        "subject": "Your Arbiter login link",
        "html": (
            "<p>Use this secure link to sign in to Arbiter:</p>"
            f"<p><a href=\"{magic_link_url}\">Sign in to Arbiter</a></p>"
            f"<p>If you did not request this, you can ignore this email. "
            f"This link expires in {settings.magic_link_expire_minutes} minutes.</p>"
        ),
        "text": (
            "Use this secure link to sign in to Arbiter:\n"
            f"{magic_link_url}\n\n"
            f"If you did not request this, ignore this email. "
            f"This link expires in {settings.magic_link_expire_minutes} minutes."
        ),
    }

    headers = {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Content-Type": "application/json",
    }

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
