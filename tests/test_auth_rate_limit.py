from __future__ import annotations

import pytest
from redis.exceptions import RedisError
from starlette.requests import Request

from app.services import auth_rate_limit as limiter
from app.services import feedback_rate_limit as shared


class FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def eval(self, _script, number_of_keys, *args):
        keys = list(args[:number_of_keys])
        window = int(args[number_of_keys])
        limits = [int(value) for value in args[number_of_keys + 1 :]]
        for key, limit in zip(keys, limits, strict=True):
            if self.counts.get(key, 0) >= limit:
                return [0, window]
        for key in keys:
            self.counts[key] = self.counts.get(key, 0) + 1
        return [1, 0]


class FailingRedis:
    async def eval(self, *_args):
        raise RedisError("unavailable")


def _request(ip: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/auth/login",
            "headers": [(b"x-forwarded-for", f"{ip}, render-proxy".encode())],
            "client": ("127.0.0.1", 1234),
        }
    )


@pytest.mark.asyncio
async def test_magic_link_limit_is_shared_by_normalized_identifier(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(shared, "_redis_client", fake)
    monkeypatch.setattr(shared.settings, "rate_limit_redis_url", "redis://test")

    for _ in range(3):
        decision = await limiter.check_auth_rate_limit(
            _request("203.0.113.10"),
            action="magic_link",
            subject=" User@Example.com ",
        )
        assert decision.allowed

    blocked = await limiter.check_auth_rate_limit(
        _request("203.0.113.11"),
        action="magic_link",
        subject="user@example.com",
    )
    assert not blocked.allowed
    assert blocked.retry_after == 15 * 60


@pytest.mark.asyncio
async def test_separate_login_identifiers_do_not_share_subject_limit(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(shared, "_redis_client", fake)
    monkeypatch.setattr(shared.settings, "rate_limit_redis_url", "redis://test")

    for index in range(10):
        assert (
            await limiter.check_auth_rate_limit(
                _request(f"203.0.113.{index + 1}"),
                action="login",
                subject="one@example.com",
            )
        ).allowed
    assert not (
        await limiter.check_auth_rate_limit(
            _request("203.0.113.99"),
            action="login",
            subject="one@example.com",
        )
    ).allowed
    assert (
        await limiter.check_auth_rate_limit(
            _request("203.0.113.99"),
            action="login",
            subject="two@example.com",
        )
    ).allowed


@pytest.mark.asyncio
async def test_rate_limit_failure_fails_closed_in_production(monkeypatch):
    monkeypatch.setattr(shared, "_redis_client", FailingRedis())
    monkeypatch.setattr(shared.settings, "rate_limit_redis_url", "redis://test")
    monkeypatch.setattr(limiter.settings, "env", "production")

    with pytest.raises(limiter.AuthRateLimitUnavailable):
        await limiter.check_auth_rate_limit(
            _request("203.0.113.10"), action="register"
        )
