from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from redis.exceptions import ConnectionError
from starlette.requests import Request

from app.services import feedback_rate_limit as limiter

pytestmark = pytest.mark.anyio


class FakeRedis:
    def __init__(self):
        self.values: dict[str, int] = {}
        self.expirations: dict[str, int] = {}
        self.keys_seen: list[str] = []

    async def eval(self, script, key_count, *values):
        del script
        keys = list(values[:key_count])
        arguments = [int(value) for value in values[key_count:]]
        self.keys_seen.extend(keys)
        submission_key, *rate_keys = keys
        window, submission_ttl, *limits = arguments

        if submission_key in self.values:
            return [1, 0, 1]

        retry_after = 0
        for key, limit in zip(rate_keys, limits, strict=True):
            if self.values.get(key, 0) >= limit:
                retry_after = max(retry_after, self.expirations.get(key, window))
        if retry_after:
            return [0, retry_after, 0]

        self.values[submission_key] = 1
        self.expirations[submission_key] = submission_ttl
        for key in rate_keys:
            self.values[key] = self.values.get(key, 0) + 1
            self.expirations.setdefault(key, window)
        return [1, 0, 0]


class FailingRedis:
    async def eval(self, *args, **kwargs):
        raise ConnectionError("private connection details")


def request_for(ip: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/feedback",
            "headers": [(b"x-forwarded-for", f"{ip}, render-proxy".encode())],
            "client": ("127.0.0.1", 1234),
        }
    )


@pytest.fixture
def redis_limiter(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(limiter.settings, "rate_limit_redis_url", "redis://test")
    monkeypatch.setattr(limiter, "_redis_client", fake)
    return fake


async def test_public_limit_blocks_fourth_submission_without_storing_raw_ip(
    redis_limiter,
):
    request = request_for("203.0.113.24")
    decisions = [
        await limiter.check_feedback_rate_limit(
            request,
            user=None,
            submission_id=uuid4(),
        )
        for _ in range(4)
    ]

    assert [decision.allowed for decision in decisions] == [True, True, True, False]
    assert decisions[-1].retry_after == limiter.WINDOW_SECONDS
    assert all("203.0.113.24" not in key for key in redis_limiter.keys_seen)
    assert all("spoofed" not in key for key in redis_limiter.keys_seen)


async def test_duplicate_submission_does_not_consume_another_slot(redis_limiter):
    request = request_for("198.51.100.7")
    submission_id = uuid4()

    first = await limiter.check_feedback_rate_limit(
        request,
        user=None,
        submission_id=submission_id,
    )
    duplicate = await limiter.check_feedback_rate_limit(
        request,
        user=None,
        submission_id=submission_id,
    )
    rate_values = {
        key: value
        for key, value in redis_limiter.values.items()
        if ":rate:" in key
    }

    assert first.allowed is True
    assert duplicate.allowed is True
    assert duplicate.duplicate_submission is True
    assert list(rate_values.values()) == [1]


async def test_authenticated_limit_is_per_account_with_ip_safety_limit(redis_limiter):
    request = request_for("192.0.2.18")
    user = SimpleNamespace(id=uuid4())
    decisions = [
        await limiter.check_feedback_rate_limit(
            request,
            user=user,
            submission_id=uuid4(),
        )
        for _ in range(6)
    ]

    assert [decision.allowed for decision in decisions] == [
        True,
        True,
        True,
        True,
        True,
        False,
    ]


async def test_missing_redis_is_allowed_locally_but_fails_closed_in_production(
    monkeypatch,
):
    monkeypatch.setattr(limiter, "_redis_client", None)
    monkeypatch.setattr(limiter.settings, "rate_limit_redis_url", None)
    monkeypatch.setattr(limiter.settings, "env", "test")
    local = await limiter.check_feedback_rate_limit(
        request_for("203.0.113.3"),
        user=None,
        submission_id=uuid4(),
    )
    assert local.allowed is True

    monkeypatch.setattr(limiter.settings, "env", "production")
    with pytest.raises(limiter.FeedbackRateLimitUnavailable):
        await limiter.check_feedback_rate_limit(
            request_for("203.0.113.3"),
            user=None,
            submission_id=uuid4(),
        )


async def test_redis_failure_is_reported_as_unavailable(monkeypatch):
    monkeypatch.setattr(limiter.settings, "rate_limit_redis_url", "redis://test")
    monkeypatch.setattr(limiter, "_redis_client", FailingRedis())

    with pytest.raises(limiter.FeedbackRateLimitUnavailable):
        await limiter.check_feedback_rate_limit(
            request_for("203.0.113.9"),
            user=None,
            submission_id=uuid4(),
        )
