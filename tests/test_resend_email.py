from __future__ import annotations

import pytest

pytestmark = pytest.mark.anyio


async def test_shared_resend_transport_preserves_headers_timeout_and_idempotency(
    monkeypatch,
):
    from app.services import resend_email

    monkeypatch.setattr(resend_email.settings, "resend_api_key", "test-api-key")
    captured = {}

    class Response:
        @staticmethod
        def raise_for_status():
            return None

    class Client:
        def __init__(self, *, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, *, json, headers):
            captured.update(url=url, payload=json, headers=headers)
            return Response()

    monkeypatch.setattr(resend_email.httpx, "AsyncClient", Client)
    payload = {
        "from": "Arbiter <feedback@example.com>",
        "to": ["recipient@example.com"],
        "subject": "Test",
        "text": "Test body",
    }
    await resend_email.send_resend_email(
        payload,
        idempotency_key="feedback/00000000-0000-4000-8000-000000000000",
    )

    assert captured == {
        "timeout": 10,
        "url": "https://api.resend.com/emails",
        "payload": payload,
        "headers": {
            "Authorization": "Bearer test-api-key",
            "Content-Type": "application/json",
            "Idempotency-Key": "feedback/00000000-0000-4000-8000-000000000000",
        },
    }


async def test_magic_link_payload_is_unchanged_after_transport_extraction(monkeypatch):
    from app.services import magic_link_email

    monkeypatch.setattr(
        magic_link_email.settings,
        "resend_from_email",
        "Arbiter <login@example.com>",
    )
    monkeypatch.setattr(magic_link_email.settings, "magic_link_expire_minutes", 15)
    sent = []

    async def record(payload, *, idempotency_key=None):
        sent.append((payload, idempotency_key))

    monkeypatch.setattr(magic_link_email, "send_resend_email", record)
    await magic_link_email.send_magic_link_email(
        to_email="user@example.com",
        magic_link_url="https://www.arbitertv.com/auth/magic-link/verify?token=test",
    )

    payload, idempotency_key = sent[0]
    assert payload == {
        "from": "Arbiter <login@example.com>",
        "to": ["user@example.com"],
        "subject": "Your Arbiter login link",
        "html": (
            "<p>Use this secure link to sign in to Arbiter:</p>"
            '<p><a href="https://www.arbitertv.com/auth/magic-link/verify?token=test">'
            "Sign in to Arbiter</a></p>"
            "<p>If you did not request this, you can ignore this email. "
            "This link expires in 15 minutes.</p>"
        ),
        "text": (
            "Use this secure link to sign in to Arbiter:\n"
            "https://www.arbitertv.com/auth/magic-link/verify?token=test\n\n"
            "If you did not request this, ignore this email. "
            "This link expires in 15 minutes."
        ),
    }
    assert idempotency_key is None
