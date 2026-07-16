import logging

from app.core.logging import InviteTokenRedactionFilter, redact_invite_tokens


def test_redact_invite_tokens_from_backend_and_frontend_paths():
    token = "a" * 43

    assert redact_invite_tokens(f"/invites/friend/{token}/accept") == (
        "/invites/friend/<redacted>/accept"
    )
    assert redact_invite_tokens(f"/invite/group/{token}") == (
        "/invite/group/<redacted>"
    )


def test_access_log_filter_redacts_path_argument():
    token = "b" * 43
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1", "GET", f"/invites/group/{token}", "1.1", 200),
        exc_info=None,
    )

    assert InviteTokenRedactionFilter().filter(record) is True
    assert token not in record.getMessage()
    assert "/invites/group/<redacted>" in record.getMessage()
