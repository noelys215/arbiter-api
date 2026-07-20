from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import Request
from redis.exceptions import RedisError

from app.core.config import settings
from app.models.user import User
from app.services import feedback_rate_limit as shared_rate_limit

SocialRateLimitAction = Literal["friend_request", "group_invite"]

WINDOW_SECONDS = 60 * 60
FRIEND_REQUEST_ACCOUNT_LIMIT = 10
FRIEND_REQUEST_IP_LIMIT = 30
GROUP_INVITE_ACCOUNT_LIMIT = 30
GROUP_INVITE_IP_LIMIT = 60

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


class SocialRateLimitUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class SocialRateLimitDecision:
    allowed: bool
    retry_after: int = 0


def _limits(action: SocialRateLimitAction) -> tuple[int, int]:
    if action == "friend_request":
        return FRIEND_REQUEST_ACCOUNT_LIMIT, FRIEND_REQUEST_IP_LIMIT
    return GROUP_INVITE_ACCOUNT_LIMIT, GROUP_INVITE_IP_LIMIT


async def check_social_rate_limit(
    request: Request,
    *,
    user: User,
    action: SocialRateLimitAction,
) -> SocialRateLimitDecision:
    client = shared_rate_limit.get_rate_limit_redis()
    if client is None:
        if settings.is_local_env():
            return SocialRateLimitDecision(allowed=True)
        raise SocialRateLimitUnavailable("rate limit configuration unavailable")

    account_limit, ip_limit = _limits(action)
    account_key = shared_rate_limit.opaque_rate_limit_identifier(
        f"social:{action}:account", str(user.id)
    )
    ip_key = shared_rate_limit.opaque_rate_limit_identifier(
        f"social:{action}:ip", shared_rate_limit.client_ip(request)
    )
    keys = [
        f"social:rate:{action}:account:{account_key}",
        f"social:rate:{action}:ip:{ip_key}",
    ]

    try:
        result = await client.eval(
            _CHECK_LIMIT_SCRIPT,
            len(keys),
            *keys,
            WINDOW_SECONDS,
            account_limit,
            ip_limit,
        )
    except RedisError as exc:
        raise SocialRateLimitUnavailable("rate limit service unavailable") from exc

    allowed, retry_after = (int(value) for value in result)
    return SocialRateLimitDecision(
        allowed=bool(allowed), retry_after=max(retry_after, 0)
    )
