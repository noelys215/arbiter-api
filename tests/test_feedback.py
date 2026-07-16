from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.services.feedback import sanitize_feedback_route

pytestmark = pytest.mark.anyio


def feedback_payload(**overrides):
    payload = {
        "submission_id": str(uuid4()),
        "type": "feedback",
        "message": "Arbiter made choosing tonight's movie much easier.",
        "allow_contact": False,
        "contact_email": None,
        "include_diagnostics": False,
        "diagnostics": None,
        "website": "",
    }
    payload.update(overrides)
    return payload


def configure_feedback(monkeypatch):
    from app.services import feedback as feedback_service

    monkeypatch.setattr(feedback_service.settings, "resend_api_key", "test-key")
    monkeypatch.setattr(
        feedback_service.settings,
        "feedback_recipient_email",
        "private-recipient@example.com",
    )
    monkeypatch.setattr(
        feedback_service.settings,
        "feedback_from_email",
        "Arbiter Feedback <feedback@example.com>",
    )


async def test_signed_out_feedback_sends_plain_text_without_private_response_data(
    client,
    monkeypatch,
):
    from app.services import feedback as feedback_service

    configure_feedback(monkeypatch)
    sent = []

    async def record(payload, *, idempotency_key=None):
        sent.append((payload, idempotency_key))

    monkeypatch.setattr(feedback_service, "send_resend_email", record)
    response = await client.post("/feedback", json=feedback_payload())

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}
    assert "private-recipient@example.com" not in response.text
    email, key = sent[0]
    assert email["subject"] == "[Arbiter Feedback] New feedback"
    assert "html" not in email
    assert "reply_to" not in email
    assert "Reply email:" not in email["text"]
    assert key.startswith("feedback/")


async def test_contact_consent_is_explicit_for_signed_out_user(client, monkeypatch):
    from app.services import feedback as feedback_service

    configure_feedback(monkeypatch)
    sent = []

    async def record(payload, *, idempotency_key=None):
        sent.append(payload)

    monkeypatch.setattr(feedback_service, "send_resend_email", record)

    missing = await client.post(
        "/feedback",
        json=feedback_payload(allow_contact=True),
    )
    assert missing.status_code == 422

    accepted = await client.post(
        "/feedback",
        json=feedback_payload(
            allow_contact=True,
            contact_email="reply@example.com",
        ),
    )
    assert accepted.status_code == 200
    assert sent[0]["reply_to"] == "reply@example.com"
    assert "Reply email: reply@example.com" in sent[0]["text"]


async def test_authenticated_contact_uses_server_email_only_after_consent(
    client,
    user_factory,
    login_helper,
    monkeypatch,
):
    from app.services import feedback as feedback_service

    configure_feedback(monkeypatch)
    user = await user_factory(client, email="account-contact@example.com")
    await login_helper(client, email=user["email"], password=user["password"])
    sent = []

    async def record(payload, *, idempotency_key=None):
        sent.append(payload)

    monkeypatch.setattr(feedback_service, "send_resend_email", record)
    no_contact = await client.post("/feedback", json=feedback_payload())
    with_contact = await client.post(
        "/feedback",
        json=feedback_payload(allow_contact=True),
    )
    inconsistent = await client.post(
        "/feedback",
        json=feedback_payload(
            allow_contact=True,
            contact_email="other@example.com",
        ),
    )

    assert no_contact.status_code == 200
    assert "account-contact@example.com" not in sent[0]["text"]
    assert "reply_to" not in sent[0]
    assert with_contact.status_code == 200
    assert sent[1]["reply_to"] == "account-contact@example.com"
    assert inconsistent.status_code == 422


async def test_honeypot_returns_success_without_delivery(client, monkeypatch):
    from app.api.routes import feedback as feedback_route

    called = False

    async def record(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(feedback_route, "send_feedback_email", record)
    response = await client.post(
        "/feedback",
        json=feedback_payload(website="https://spam.example"),
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert called is False


@pytest.mark.parametrize(
    "changes",
    [
        {"type": "complaint"},
        {"message": "Too short"},
        {"message": "Valid text with a bad control \x00 character"},
        {"allow_contact": False, "contact_email": "reply@example.com"},
        {"allow_contact": True, "contact_email": "not-an-email"},
        {"unexpected": "value"},
    ],
)
async def test_invalid_feedback_states_return_generic_validation(
    client,
    changes,
):
    response = await client.post("/feedback", json=feedback_payload(**changes))

    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid feedback submission"}


async def test_oversized_raw_body_is_rejected_before_validation(client):
    body = json.dumps(feedback_payload(message="x" * 20_000)).encode()
    response = await client.post(
        "/feedback",
        content=body,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "Feedback submission is too large"}


async def test_oversized_chunked_body_is_rejected(client):
    body = json.dumps(feedback_payload(message="x" * 20_000)).encode()

    async def chunks():
        for offset in range(0, len(body), 1024):
            yield body[offset : offset + 1024]

    response = await client.post(
        "/feedback",
        content=chunks(),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "Feedback submission is too large"}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/invite/friend/secret-token", "/invite/friend/[redacted]"),
        ("/invite/group/secret-token?source=email#join", "/invite/group/[redacted]"),
        ("/invite%2Ffriend%2Fencoded-secret", "/invite/friend/[redacted]"),
        ("/app/session?private=value#vote", "/app/session"),
    ],
)
def test_feedback_route_sanitization(value, expected):
    assert sanitize_feedback_route(value) == expected


async def test_unverified_selected_group_is_omitted(
    client,
    user_factory,
    login_helper,
    monkeypatch,
):
    from app.services import feedback as feedback_service

    configure_feedback(monkeypatch)
    user = await user_factory(client)
    await login_helper(client, email=user["email"], password=user["password"])
    sent = []

    async def record(payload, *, idempotency_key=None):
        sent.append(payload)

    monkeypatch.setattr(feedback_service, "send_resend_email", record)
    diagnostics = {
        "route": "/invite/group/private-token",
        "browser": "Test Browser",
        "operating_system": "Test OS",
        "viewport_width": 1280,
        "viewport_height": 800,
        "app_version": "1.2.0",
        "submitted_at": "2026-07-16T16:00:00Z",
        "source": "account_profile",
        "selected_group_id": str(uuid4()),
        "online": True,
    }
    response = await client.post(
        "/feedback",
        json=feedback_payload(
            type="bug",
            include_diagnostics=True,
            diagnostics=diagnostics,
        ),
    )

    assert response.status_code == 200
    assert "Route: /invite/group/[redacted]" in sent[0]["text"]
    assert "Selected group ID:" not in sent[0]["text"]
    assert f"Authenticated user ID: {user['id']}" in sent[0]["text"]


async def test_provider_failure_and_missing_config_are_generic(
    client,
    monkeypatch,
    caplog,
):
    from app.api.routes import feedback as feedback_route
    from app.services import feedback as feedback_service

    configure_feedback(monkeypatch)

    async def fail(*args, **kwargs):
        raise RuntimeError("provider details that must remain private")

    monkeypatch.setattr(feedback_route, "send_feedback_email", fail)
    message = "A private message that must not appear in application logs."
    failed = await client.post("/feedback", json=feedback_payload(message=message))
    assert failed.status_code == 503
    assert failed.json() == {"detail": "Feedback is currently unavailable"}
    assert "provider details" not in failed.text
    assert message not in caplog.text

    monkeypatch.setattr(feedback_service.settings, "feedback_recipient_email", None)
    unavailable = await client.post("/feedback", json=feedback_payload())
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "Feedback is currently unavailable"}
    assert "FEEDBACK_RECIPIENT_EMAIL" not in unavailable.text


async def test_production_feature_gate_disables_unprotected_public_feedback(
    client,
    monkeypatch,
):
    from app.api.routes import feedback as feedback_route

    monkeypatch.setattr(feedback_route.settings, "env", "production")
    monkeypatch.setattr(feedback_route.settings, "feedback_public_enabled", False)

    response = await client.post("/feedback", json=feedback_payload())

    assert response.status_code == 503
    assert response.json() == {"detail": "Feedback is currently unavailable"}


async def test_same_submission_id_uses_same_resend_idempotency_key(
    client,
    monkeypatch,
):
    from app.services import feedback as feedback_service

    configure_feedback(monkeypatch)
    keys = []

    async def record(payload, *, idempotency_key=None):
        keys.append(idempotency_key)

    monkeypatch.setattr(feedback_service, "send_resend_email", record)
    payload = feedback_payload()
    assert (await client.post("/feedback", json=payload)).status_code == 200
    assert (await client.post("/feedback", json=payload)).status_code == 200

    assert keys == [keys[0], keys[0]]
    assert keys[0] == f"feedback/{payload['submission_id']}"


async def test_script_content_remains_plain_text(client, monkeypatch):
    from app.services import feedback as feedback_service

    configure_feedback(monkeypatch)
    sent = []

    async def record(payload, *, idempotency_key=None):
        sent.append(payload)

    monkeypatch.setattr(feedback_service, "send_resend_email", record)
    message = "Please investigate <script>alert('x')</script> in the title list."
    response = await client.post("/feedback", json=feedback_payload(message=message))

    assert response.status_code == 200
    assert sent[0]["text"].endswith(message)
    assert "html" not in sent[0]
