from __future__ import annotations

from app.models.user import User


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
        "avatar_source": user.avatar_source,
        "avatar_style": user.avatar_style,
        "avatar_seed": user.avatar_seed,
    }


def public_user_from_user(user: User) -> dict[str, object | None]:
    return {
        "id": str(user.id),
        "email": user.email,
        "username": user.username,
        "display_name": user.display_name,
        **avatar_fields_from_user(user),
    }
