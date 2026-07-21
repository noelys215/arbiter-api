from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import Request
from redis.exceptions import RedisError

from app.core.config import settings
from app.models.user import User
from app.services import feedback_rate_limit as shared_rate_limit

MutationRateLimitAction = Literal["group_create", "session_setup", "vote"]

_POLICIES: dict[MutationRateLimitAction, tuple[int, int, int]] = {
    # window seconds, per-account limit, per-IP limit
    "group_create": (60 * 60, 10, 30),
    "session_setup": (60 * 60, 30, 90),
    "vote": (60, 180, 540),
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


class MutationRateLimitUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class MutationRateLimitDecision:
    allowed: bool
    retry_after: int = 0


async def check_mutation_rate_limit(
    request: Request,
    *,
    user: User,
    action: MutationRateLimitAction,
) -> MutationRateLimitDecision:
    client = shared_rate_limit.get_rate_limit_redis()
    if client is None:
        if settings.is_local_env():
            return MutationRateLimitDecision(allowed=True)
        raise MutationRateLimitUnavailable("rate limit configuration unavailable")

    window, account_limit, ip_limit = _POLICIES[action]
    account_key = shared_rate_limit.opaque_rate_limit_identifier(
        f"mutation:{action}:account", str(user.id)
    )
    ip_key = shared_rate_limit.opaque_rate_limit_identifier(
        f"mutation:{action}:ip", shared_rate_limit.client_ip(request)
    )
    keys = [
        f"mutation:rate:{action}:account:{account_key}",
        f"mutation:rate:{action}:ip:{ip_key}",
    ]
    try:
        result = await client.eval(
            _CHECK_LIMIT_SCRIPT,
            len(keys),
            *keys,
            window,
            account_limit,
            ip_limit,
        )
    except RedisError as exc:
        raise MutationRateLimitUnavailable("rate limit service unavailable") from exc

    allowed, retry_after = (int(value) for value in result)
    return MutationRateLimitDecision(bool(allowed), max(retry_after, 0))
