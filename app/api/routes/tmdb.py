from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.tmdb import TMDBSearchItem
from app.services.tmdb import TMDB_SEARCH_QUERY_MAX_LENGTH, tmdb_search_multi
from app.services.tmdb_rate_limit import (
    TMDBRateLimitUnavailable,
    check_tmdb_rate_limit,
)

router = APIRouter(prefix="/tmdb", tags=["tmdb"])


@router.get("/search", response_model=list[TMDBSearchItem])
async def tmdb_search_route(
    request: Request,
    q: str = Query(..., min_length=1, max_length=TMDB_SEARCH_QUERY_MAX_LENGTH),
    type: str = Query("multi", pattern="^(multi)$"),
    user: User = Depends(get_current_user),
):
    try:
        decision = await check_tmdb_rate_limit(request, user=user)
    except TMDBRateLimitUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Movie search is temporarily unavailable. Please try again.",
        ) from exc
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail="You've searched several times recently. Please try again shortly.",
            headers={"Retry-After": str(max(decision.retry_after, 1))},
        )
    return await tmdb_search_multi(q)
