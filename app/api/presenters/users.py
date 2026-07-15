from __future__ import annotations

from app.models.user import User
from app.schemas.users import AVATAR_SOURCE_VALUES, AVATAR_STYLE_VALUES

_AVATAR_SEED_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "_-"
)


def _safe_avatar_source(value: object) -> str | None:
    return value if isinstance(value, str) and value in AVATAR_SOURCE_VALUES else None


def _safe_avatar_style(value: object) -> str | None:
    return value if isinstance(value, str) and value in AVATAR_STYLE_VALUES else None


def _safe_avatar_seed(value: object) -> str | None:
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        return None
    return value if all(char in _AVATAR_SEED_CHARS for char in value) else None


def avatar_fields_from_user(user: User | None) -> dict[str, object | None]:
    if user is None:
        return {
            "avatar_url": None,
            "avatar_source": None,
            "avatar_style": None,
            "avatar_seed": None,
        }

    return {
        "avatar_url": user.avatar_url,
        "avatar_source": _safe_avatar_source(user.avatar_source),
        "avatar_style": _safe_avatar_style(user.avatar_style),
        "avatar_seed": _safe_avatar_seed(user.avatar_seed),
    }


def public_user_from_user(user: User) -> dict[str, object | None]:
    return {
        "id": str(user.id),
        "email": user.email,
        "username": user.username,
        "display_name": user.display_name,
        **avatar_fields_from_user(user),
    }
