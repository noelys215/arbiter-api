from __future__ import annotations

import re
from urllib.parse import urlsplit


_INVITE_RETURN_PATH = re.compile(
    r"^/invite/(?:friend|group)/[A-Za-z0-9_-]{43}$"
)


def validate_invite_return_path(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if "%" in value or "\\" in value or value.startswith("//"):
        return None
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return None
    return value if _INVITE_RETURN_PATH.fullmatch(parsed.path) else None
