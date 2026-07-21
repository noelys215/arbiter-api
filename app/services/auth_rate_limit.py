from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import Request
from redis.exceptions import RedisError

from app.core.config import settings
from app.services import feedback_rate_limit as shared_rate_limit

AuthRateLimitAction = Literal["login", "register", "magic_link", "oauth_start"]

_POLICIES: dict[AuthRateLimitAction, tuple[int, int, int]] = {
    # window seconds, per-IP limit, per-identifier limit (0 means none)
    "login": (15 * 60, 30, 10),
    "register": (60 * 60, 5, 0),
    "magic_link": (15 * 60, 10, 3),
    "oauth_start": (15 * 60, 20, 0),
}

_CHECK_LIMIT_SCRIPT = """
local retry_after = 0
for index = 1, #KEYS do
  local current = tonumber(redis.call('GET', KEYS[index]) or '0')
  local limit = tonumber(ARGV[index + 1])
  if current >= limit then
    local ttl = redis.call('TTL', KEYS[index])
    if ttl < 1 then ttl = tonumber(ARGV[1]) end
    if ttl > retry_after then retry_after = ttl end
  end
end
if retry_after > 0 then return {0, retry_after} end
for index = 1, #KEYS do
  local current = redis.call('INCR', KEYS[index])
  if current == 1 then redis.call('EXPIRE', KEYS[index], ARGV[1]) end
end
return {1, 0}
"""


class AuthRateLimitUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthRateLimitDecision:
    allowed: bool
    retry_after: int = 0


async def check_auth_rate_limit(
    request: Request,
    *,
    action: AuthRateLimitAction,
    subject: str | None = None,
) -> AuthRateLimitDecision:
    client = shared_rate_limit.get_rate_limit_redis()
    if client is None:
        if settings.is_local_env():
            return AuthRateLimitDecision(allowed=True)
        raise AuthRateLimitUnavailable("rate limit configuration unavailable")

    window, ip_limit, subject_limit = _POLICIES[action]
    ip_key = shared_rate_limit.opaque_rate_limit_identifier(
        f"auth:{action}:ip", shared_rate_limit.client_ip(request)
    )
    keys = [f"auth:rate:{action}:ip:{ip_key}"]
    limits = [ip_limit]
    if subject_limit and subject:
        subject_key = shared_rate_limit.opaque_rate_limit_identifier(
            f"auth:{action}:subject", subject.strip().casefold()
        )
        keys.append(f"auth:rate:{action}:subject:{subject_key}")
        limits.append(subject_limit)

    try:
        result = await client.eval(
            _CHECK_LIMIT_SCRIPT,
            len(keys),
            *keys,
            window,
            *limits,
        )
    except RedisError as exc:
        raise AuthRateLimitUnavailable("rate limit service unavailable") from exc

    allowed, retry_after = (int(value) for value in result)
    return AuthRateLimitDecision(bool(allowed), max(retry_after, 0))
