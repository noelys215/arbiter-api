from __future__ import annotations

from fastapi import HTTPException, Request

from app.models.user import User
from app.services.social_rate_limit import (
    SocialRateLimitAction,
    SocialRateLimitUnavailable,
    check_social_rate_limit,
)


async def enforce_social_rate_limit(
    request: Request, *, user: User, action: SocialRateLimitAction
) -> None:
    try:
        decision = await check_social_rate_limit(request, user=user, action=action)
    except SocialRateLimitUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Requests are temporarily unavailable. Please try again.",
        ) from exc
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail="You've sent several requests recently. Please try again later.",
            headers={"Retry-After": str(max(decision.retry_after, 1))},
        )
