from fastapi import HTTPException, Request, status

from app.models.user import User
from app.services.mutation_rate_limit import (
    MutationRateLimitAction,
    MutationRateLimitUnavailable,
    check_mutation_rate_limit,
)


async def enforce_mutation_rate_limit(
    request: Request,
    *,
    user: User,
    action: MutationRateLimitAction,
) -> None:
    try:
        decision = await check_mutation_rate_limit(
            request,
            user=user,
            action=action,
        )
    except MutationRateLimitUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable",
        ) from exc
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
            headers={"Retry-After": str(max(decision.retry_after, 1))},
        )
