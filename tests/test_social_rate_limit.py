from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from redis.exceptions import ConnectionError
from starlette.requests import Request

from app.services import feedback_rate_limit as shared_rate_limit
from app.services import social_rate_limit as limiter

pytestmark = pytest.mark.anyio


class FakeRedis:
    def __init__(self):
        self.values: dict[str, int] = {}
        self.expirations: dict[str, int] = {}
        self.keys_seen: list[str] = []

    async def eval(self, script, key_count, *values):
        del script
        keys = list(values[:key_count])
        window, *limits = (int(value) for value in values[key_count:])
        self.keys_seen.extend(keys)

        retry_after = 0
        for key, limit in zip(keys, limits, strict=True):
            if self.values.get(key, 0) >= limit:
                retry_after = max(
                    retry_after, self.expirations.get(key, window)
                )
        if retry_after:
            return [0, retry_after]

        for key in keys:
            self.values[key] = self.values.get(key, 0) + 1
            self.expirations.setdefault(key, window)
        return [1, 0]


class FailingRedis:
    async def eval(self, *args, **kwargs):
        raise ConnectionError("private connection details")


def request_for(ip: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/friends/requests",
            "headers": [(b"x-forwarded-for", f"spoofed, {ip}".encode())],
            "client": ("127.0.0.1", 1234),
        }
    )


@pytest.fixture
def redis_limiter(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(
        shared_rate_limit.settings, "rate_limit_redis_url", "redis://test"
    )
    monkeypatch.setattr(shared_rate_limit, "_redis_client", fake)
    return fake


async def test_friend_request_limit_is_per_account_and_hides_identifiers(
    redis_limiter,
):
    request = request_for("203.0.113.24")
    user = SimpleNamespace(id=uuid4())
    decisions = [
        await limiter.check_social_rate_limit(
            request, user=user, action="friend_request"
        )
        for _ in range(limiter.FRIEND_REQUEST_ACCOUNT_LIMIT + 1)
    ]

    assert all(decision.allowed for decision in decisions[:-1])
    assert decisions[-1].allowed is False
    assert decisions[-1].retry_after == limiter.WINDOW_SECONDS
    assert all("203.0.113.24" not in key for key in redis_limiter.keys_seen)
    assert all(str(user.id) not in key for key in redis_limiter.keys_seen)


async def test_group_invite_uses_the_larger_group_limit(redis_limiter):
    request = request_for("198.51.100.7")
    user = SimpleNamespace(id=uuid4())
    decisions = [
        await limiter.check_social_rate_limit(
            request, user=user, action="group_invite"
        )
        for _ in range(limiter.GROUP_INVITE_ACCOUNT_LIMIT + 1)
    ]

    assert all(decision.allowed for decision in decisions[:-1])
    assert decisions[-1].allowed is False


async def test_social_limiter_fails_closed_in_production(monkeypatch):
    monkeypatch.setattr(shared_rate_limit, "_redis_client", None)
    monkeypatch.setattr(
        shared_rate_limit.settings, "rate_limit_redis_url", None
    )
    monkeypatch.setattr(limiter.settings, "env", "production")

    with pytest.raises(limiter.SocialRateLimitUnavailable):
        await limiter.check_social_rate_limit(
            request_for("203.0.113.3"),
            user=SimpleNamespace(id=uuid4()),
            action="friend_request",
        )


async def test_social_limiter_reports_redis_failure(monkeypatch):
    monkeypatch.setattr(
        shared_rate_limit.settings, "rate_limit_redis_url", "redis://test"
    )
    monkeypatch.setattr(shared_rate_limit, "_redis_client", FailingRedis())

    with pytest.raises(limiter.SocialRateLimitUnavailable):
        await limiter.check_social_rate_limit(
            request_for("203.0.113.9"),
            user=SimpleNamespace(id=uuid4()),
            action="group_invite",
        )
