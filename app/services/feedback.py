from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parseaddr
from urllib.parse import unquote, urlsplit

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.group_membership import GroupMembership
from app.models.user import User
from app.schemas.feedback import FeedbackDiagnostics, FeedbackRequest
from app.services.resend_email import send_resend_email

_EMAIL_ADAPTER = TypeAdapter(EmailStr)
_INVITE_ROUTE = re.compile(r"^/invite/(friend|group)/[^/]+(?:/.*)?$", re.IGNORECASE)

_TYPE_LABELS = {
    "feedback": "General feedback",
    "bug": "Bug report",
    "feature": "Feature idea",
}
_SUBJECTS = {
    "feedback": "[Arbiter Feedback] New feedback",
    "bug": "[Arbiter Bug] New bug report",
    "feature": "[Arbiter Feature] New feature idea",
}


def feedback_email_configured() -> bool:
    values = (
        settings.resend_api_key,
        settings.feedback_recipient_email,
        settings.feedback_from_email,
    )
    if not all(value and value.strip() for value in values):
        return False
    try:
        _EMAIL_ADAPTER.validate_python(settings.feedback_recipient_email)
        _, sender_address = parseaddr(settings.feedback_from_email or "")
        _EMAIL_ADAPTER.validate_python(sender_address)
    except ValidationError:
        return False
    return True


def sanitize_feedback_route(value: str) -> str:
    decoded = value.strip()
    for _ in range(2):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    path = urlsplit(decoded).path or "/"
    match = _INVITE_ROUTE.match(path)
    if match:
        return f"/invite/{match.group(1).lower()}/[redacted]"
    if not path.startswith("/"):
        return "/[redacted]"
    return path[:180]


async def _verified_group_id(
    db: AsyncSession,
    *,
    user: User | None,
    diagnostics: FeedbackDiagnostics | None,
) -> str | None:
    if user is None or diagnostics is None or diagnostics.selected_group_id is None:
        return None
    membership = await db.scalar(
        select(GroupMembership.id).where(
            GroupMembership.group_id == diagnostics.selected_group_id,
            GroupMembership.user_id == user.id,
        )
    )
    return str(diagnostics.selected_group_id) if membership is not None else None


def _resolve_reply_email(payload: FeedbackRequest, user: User | None) -> str | None:
    if not payload.allow_contact:
        if payload.contact_email is not None:
            raise ValueError("invalid_contact_state")
        return None
    if user is not None:
        if payload.contact_email is not None:
            raise ValueError("invalid_contact_state")
        return user.email
    if payload.contact_email is None:
        raise ValueError("contact_email_required")
    return str(payload.contact_email)


async def build_feedback_email(
    db: AsyncSession,
    *,
    payload: FeedbackRequest,
    user: User | None,
) -> tuple[dict[str, object], str]:
    reply_email = _resolve_reply_email(payload, user)
    submitted = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
    lines = [
        f"Type: {_TYPE_LABELS[payload.type]}",
        f"Submitted: {submitted}",
        f"Contact allowed: {'Yes' if payload.allow_contact else 'No'}",
    ]
    if reply_email:
        lines.append(f"Reply email: {reply_email}")
    lines.extend(["", "Message:", payload.message])

    diagnostics = payload.diagnostics if payload.include_diagnostics else None
    if diagnostics is not None:
        group_id = await _verified_group_id(db, user=user, diagnostics=diagnostics)
        lines.extend(
            [
                "",
                "Technical details:",
                f"Route: {sanitize_feedback_route(diagnostics.route)}",
                f"Browser: {diagnostics.browser}",
                f"OS: {diagnostics.operating_system}",
                f"Viewport: {diagnostics.viewport_width} x {diagnostics.viewport_height}",
                f"App version: {diagnostics.app_version}",
                f"Source: {diagnostics.source}",
                f"Client timestamp: {diagnostics.submitted_at.isoformat()}",
            ]
        )
        if diagnostics.online is not None:
            lines.append(f"Online: {'Yes' if diagnostics.online else 'No'}")
        if user is not None:
            lines.append(f"Authenticated user ID: {user.id}")
        if group_id:
            lines.append(f"Selected group ID: {group_id}")

    email: dict[str, object] = {
        "from": settings.feedback_from_email,
        "to": [settings.feedback_recipient_email],
        "subject": _SUBJECTS[payload.type],
        "text": "\n".join(lines),
    }
    if reply_email:
        email["reply_to"] = reply_email
    return email, f"feedback/{payload.submission_id}"


async def send_feedback_email(
    db: AsyncSession,
    *,
    payload: FeedbackRequest,
    user: User | None,
) -> None:
    email, idempotency_key = await build_feedback_email(db, payload=payload, user=user)
    await send_resend_email(email, idempotency_key=idempotency_key)
