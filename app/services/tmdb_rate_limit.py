from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from redis.exceptions import RedisError

from app.core.config import settings
from app.models.user import User
from app.services import feedback_rate_limit as shared_rate_limit


WINDOW_SECONDS = 60
ACCOUNT_LIMIT = 60
IP_LIMIT = 180

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

if retry_after > 0 then
  return {0, retry_after}
end

for index = 1, #KEYS do
  local current = redis.call('INCR', KEYS[index])
  if current == 1 then
    redis.call('EXPIRE', KEYS[index], ARGV[1])
  end
end

return {1, 0}
"""


class TMDBRateLimitUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class TMDBRateLimitDecision:
    allowed: bool
    retry_after: int = 0


async def check_tmdb_rate_limit(
    request: Request, *, user: User
) -> TMDBRateLimitDecision:
    client = shared_rate_limit.get_rate_limit_redis()
    if client is None:
        if settings.is_local_env():
            return TMDBRateLimitDecision(allowed=True)
        raise TMDBRateLimitUnavailable("rate limit configuration unavailable")

    account_key = shared_rate_limit.opaque_rate_limit_identifier(
        "tmdb:search:account", str(user.id)
    )
    ip_key = shared_rate_limit.opaque_rate_limit_identifier(
        "tmdb:search:ip", shared_rate_limit.client_ip(request)
    )
    keys = [
        f"tmdb:rate:search:account:{account_key}",
        f"tmdb:rate:search:ip:{ip_key}",
    ]

    try:
        result = await client.eval(
            _CHECK_LIMIT_SCRIPT,
            len(keys),
            *keys,
            WINDOW_SECONDS,
            ACCOUNT_LIMIT,
            IP_LIMIT,
        )
    except RedisError as exc:
        raise TMDBRateLimitUnavailable("rate limit service unavailable") from exc

    allowed, retry_after = (int(value) for value in result)
    return TMDBRateLimitDecision(
        allowed=bool(allowed), retry_after=max(retry_after, 0)
    )
