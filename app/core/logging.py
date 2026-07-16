from __future__ import annotations

import logging
import re


_INVITE_TOKEN_PATH = re.compile(
    r"(?P<prefix>/invites?/(?:friend|group)/)[A-Za-z0-9_-]{43}"
)


def redact_invite_tokens(value: str) -> str:
    return _INVITE_TOKEN_PATH.sub(r"\g<prefix><redacted>", value)


class InviteTokenRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_invite_tokens(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_invite_tokens(value) if isinstance(value, str) else value
                for value in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                key: redact_invite_tokens(value) if isinstance(value, str) else value
                for key, value in record.args.items()
            }
        return True


def configure_sensitive_path_redaction() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    if not any(
        isinstance(existing, InviteTokenRedactionFilter)
        for existing in access_logger.filters
    ):
        access_logger.addFilter(InviteTokenRedactionFilter())
