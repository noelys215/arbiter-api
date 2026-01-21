from __future__ import annotations

from fastapi import APIRouter, Query

from app.services.tmdb import tmdb_search_multi

router = APIRouter(prefix="/tmdb", tags=["tmdb"])


@router.get("/search")
async def tmdb_search_route(
    q: str = Query(..., min_length=1),
    type: str = Query("multi", pattern="^(multi)$"),
):
    # v1 supports only multi
    return await tmdb_search_multi(q)
