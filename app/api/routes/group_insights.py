from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.http_errors import permission_error, value_error
from app.models.user import User
from app.schemas.group_insights import GroupInsightsOut, InsightsPeriodKey
from app.services.group_insights import get_group_insights


router = APIRouter(prefix="/groups", tags=["group-insights"])


@router.get("/{group_id}/insights", response_model=GroupInsightsOut)
async def group_insights_route(
    group_id: UUID,
    period: InsightsPeriodKey = Query(default="all_time"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return await get_group_insights(
            db,
            group_id=group_id,
            user_id=user.id,
            period=period,
        )
    except PermissionError as exc:
        raise permission_error(exc) from exc
    except ValueError as exc:
        raise value_error(exc, phrase_statuses={"not found": 404}) from exc
