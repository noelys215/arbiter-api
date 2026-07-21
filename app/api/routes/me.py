from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Response, status
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.http_errors import value_error
from app.api.presenters.users import public_user_from_user
from app.models.user import User
from app.core.config import settings
from app.models.group import Group
from app.schemas.auth import (
    AvatarUpdateRequest,
    DeleteAccountRequest,
    MeResponse,
    ProfileUpdateRequest,
)
from app.schemas.users import AVATAR_STYLE_VALUES
from app.services.social_realtime import publish_profile_update
from app.services.users import (
    list_profile_update_recipient_ids,
    update_display_name,
)
from app.services.account_realtime import account_realtime_hub
from app.services.session_realtime import session_realtime_hub
from app.services.watchlist_realtime import watchlist_realtime_hub

router = APIRouter(tags=["me"])

_AVATAR_SEED_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _validate_avatar_update(payload: AvatarUpdateRequest) -> None:
    if payload.avatar_source != "generated":
        return
    if payload.avatar_style not in AVATAR_STYLE_VALUES:
        raise ValueError("Unsupported avatar style")
    if not payload.avatar_seed or not _AVATAR_SEED_RE.fullmatch(payload.avatar_seed):
        raise ValueError("Invalid avatar seed")


@router.get("/me", response_model=MeResponse)
async def me(user: User = Depends(get_current_user)):
    return MeResponse(**public_user_from_user(user))


@router.patch("/me", response_model=MeResponse)
async def update_profile(
    payload: ProfileUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    recipients = await list_profile_update_recipient_ids(db, user.id)
    await update_display_name(
        db,
        user=user,
        display_name=payload.display_name,
    )
    await db.commit()
    await db.refresh(user)
    await publish_profile_update(recipients, user_id=user.id)
    return MeResponse(**public_user_from_user(user))


@router.patch("/me/avatar", response_model=MeResponse)
async def update_avatar(
    payload: AvatarUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        _validate_avatar_update(payload)
    except ValueError as e:
        raise value_error(e, default_detail="Could not save avatar") from e

    user.avatar_source = payload.avatar_source
    if payload.avatar_source == "generated":
        user.avatar_style = payload.avatar_style
        user.avatar_seed = payload.avatar_seed

    await db.commit()
    await db.refresh(user)
    return MeResponse(**public_user_from_user(user))


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    _payload: DeleteAccountRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    owned_group = (
        await db.execute(sa.select(Group.id).where(Group.owner_id == user.id).limit(1))
    ).scalar_one_or_none()
    if owned_group is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Transfer or delete groups you own before deleting your account.",
        )

    user_id = user.id
    await db.delete(user)
    await db.commit()

    cookie_options: dict[str, object] = {
        "httponly": True,
        "secure": settings.auth_cookie_secure_value(),
        "samesite": settings.auth_cookie_samesite_value(),
        "path": "/",
    }
    if settings.auth_cookie_domain:
        cookie_options["domain"] = settings.auth_cookie_domain.strip()
    response.set_cookie(
        key="access_token",
        value="",
        max_age=0,
        expires=0,
        **cookie_options,
    )
    await account_realtime_hub.disconnect_user(user_id)
    await watchlist_realtime_hub.disconnect_user_everywhere(user_id)
    await session_realtime_hub.disconnect_user_everywhere(user_id)
    return None
