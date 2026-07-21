from __future__ import annotations

import hashlib
import hmac
import ipaddress
from dataclasses import dataclass
from uuid import UUID

from fastapi import Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.models.user import User

WINDOW_SECONDS = 15 * 60
SUBMISSION_TTL_SECONDS = 24 * 60 * 60
PUBLIC_LIMIT = 3
AUTHENTICATED_LIMIT = 5
AUTHENTICATED_IP_LIMIT = 10

_CHECK_LIMIT_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  return {1, 0, 1}
end

local retry_after = 0
for index = 2, #KEYS do
  local current = tonumber(redis.call('GET', KEYS[index]) or '0')
  local limit = tonumber(ARGV[index + 1])
  if current >= limit then
    local ttl = redis.call('TTL', KEYS[index])
    if ttl < 1 then ttl = tonumber(ARGV[1]) end
    if ttl > retry_after then retry_after = ttl end
  end
end

if retry_after > 0 then
  return {0, retry_after, 0}
end

redis.call('SET', KEYS[1], '1', 'EX', ARGV[2])
for index = 2, #KEYS do
  local current = redis.call('INCR', KEYS[index])
  if current == 1 then
    redis.call('EXPIRE', KEYS[index], ARGV[1])
  end
end

return {1, 0, 0}
"""


class FeedbackRateLimitUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class FeedbackRateLimitDecision:
    allowed: bool
    retry_after: int = 0
    duplicate_submission: bool = False


_redis_client: Redis | None = None


def get_rate_limit_redis() -> Redis | None:
    global _redis_client
    url = (settings.rate_limit_redis_url or "").strip()
    if not url:
        return None
    if _redis_client is None:
        _redis_client = Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )
    return _redis_client


async def close_feedback_rate_limiter() -> None:
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None


def opaque_rate_limit_identifier(kind: str, value: str) -> str:
    digest = hmac.new(
        settings.jwt_secret.encode("utf-8"),
        f"feedback:{kind}:{value}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    # Render documents the first X-Forwarded-For entry as the real client IP.
    # Later entries can include infrastructure proxies and are not stable keys.
    candidate = forwarded.split(",", 1)[0].strip() if forwarded else ""
    if not candidate and request.client is not None:
        candidate = request.client.host
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return "unknown"


def _rate_keys(request: Request, user: User | None) -> list[tuple[str, int]]:
    ip_key = opaque_rate_limit_identifier("ip", client_ip(request))
    if user is None:
        return [(f"feedback:rate:public:{ip_key}", PUBLIC_LIMIT)]
    user_key = opaque_rate_limit_identifier("user", str(user.id))
    return [
        (f"feedback:rate:user:{user_key}", AUTHENTICATED_LIMIT),
        (f"feedback:rate:authenticated-ip:{ip_key}", AUTHENTICATED_IP_LIMIT),
    ]


async def check_feedback_rate_limit(
    request: Request,
    *,
    user: User | None,
    submission_id: UUID,
) -> FeedbackRateLimitDecision:
    client = get_rate_limit_redis()
    if client is None:
        if settings.is_local_env():
            return FeedbackRateLimitDecision(allowed=True)
        raise FeedbackRateLimitUnavailable("rate limit configuration unavailable")

    rate_keys = _rate_keys(request, user)
    submission_key = (
        "feedback:submission:"
        f"{opaque_rate_limit_identifier('submission', str(submission_id))}"
    )
    keys = [submission_key, *(key for key, _ in rate_keys)]
    arguments = [
        WINDOW_SECONDS,
        SUBMISSION_TTL_SECONDS,
        *(limit for _, limit in rate_keys),
    ]

    try:
        result = await client.eval(
            _CHECK_LIMIT_SCRIPT,
            len(keys),
            *keys,
            *arguments,
        )
    except RedisError as exc:
        raise FeedbackRateLimitUnavailable("rate limit service unavailable") from exc

    allowed, retry_after, duplicate = (int(value) for value in result)
    return FeedbackRateLimitDecision(
        allowed=bool(allowed),
        retry_after=max(retry_after, 0),
        duplicate_submission=bool(duplicate),
    )
