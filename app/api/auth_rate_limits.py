from __future__ import annotations

from fastapi import HTTPException, Request

from app.services.auth_rate_limit import (
    AuthRateLimitAction,
    AuthRateLimitUnavailable,
    check_auth_rate_limit,
)


async def enforce_auth_rate_limit(
    request: Request,
    *,
    action: AuthRateLimitAction,
    subject: str | None = None,
) -> None:
    try:
        decision = await check_auth_rate_limit(
            request,
            action=action,
            subject=subject,
        )
    except AuthRateLimitUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Authentication is temporarily unavailable. Please try again.",
        ) from exc
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Please try again later.",
            headers={"Retry-After": str(max(decision.retry_after, 1))},
        )
