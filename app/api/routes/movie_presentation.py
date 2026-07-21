from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.http_errors import permission_error, value_error
from app.models.user import User
from app.schemas.movie_presentation import MovieDetailOut
from app.services.movie_presentation import get_movie_detail, get_movie_night_artwork


router = APIRouter(tags=["movies"])


@router.get(
    "/groups/{group_id}/movie-details/{reference}", response_model=MovieDetailOut
)
async def movie_detail_route(
    group_id: UUID,
    reference: str,
    session_id: UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return await get_movie_detail(
            db,
            group_id=group_id,
            user_id=user.id,
            reference=reference,
            session_id=session_id,
        )
    except PermissionError as exc:
        raise permission_error(exc) from exc
    except ValueError as exc:
        raise value_error(exc) from exc


@router.get("/groups/{group_id}/movie-night-artwork/{candidate_id}")
async def movie_night_artwork_route(
    group_id: UUID,
    candidate_id: UUID,
    kind: Literal["poster", "backdrop"] = Query(default="poster"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        content, media_type = await get_movie_night_artwork(
            db,
            group_id=group_id,
            user_id=user.id,
            candidate_id=candidate_id,
            artwork_kind=kind,
        )
    except PermissionError as exc:
        raise permission_error(exc) from exc
    except ValueError as exc:
        raise value_error(exc) from exc
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
        },
    )
