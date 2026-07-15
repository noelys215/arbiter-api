from __future__ import annotations

import re

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.http_errors import value_error
from app.api.presenters.users import public_user_from_user
from app.models.user import User
from app.schemas.auth import AvatarUpdateRequest, MeResponse
from app.schemas.users import AVATAR_STYLE_VALUES

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
