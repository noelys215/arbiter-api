from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from redis.exceptions import ConnectionError
from starlette.requests import Request

from app.services import feedback_rate_limit as shared_rate_limit
from app.services import mutation_rate_limit as limiter

pytestmark = pytest.mark.anyio


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.expirations: dict[str, int] = {}
        self.keys_seen: list[str] = []

    async def eval(self, _script, key_count, *values):
        keys = list(values[:key_count])
        window, *limits = (int(value) for value in values[key_count:])
        self.keys_seen.extend(keys)

        for key, limit in zip(keys, limits, strict=True):
            if self.values.get(key, 0) >= limit:
                return [0, self.expirations.get(key, window)]

        for key in keys:
            self.values[key] = self.values.get(key, 0) + 1
            self.expirations.setdefault(key, window)
        return [1, 0]


class FailingRedis:
    async def eval(self, *_args, **_kwargs):
        raise ConnectionError("private connection details")


def request_for(ip: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/groups",
            "headers": [(b"x-forwarded-for", f"{ip}, render-proxy".encode())],
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


async def test_group_creation_limit_is_per_account_and_hides_identifiers(
    redis_limiter,
):
    request = request_for("203.0.113.24")
    user = SimpleNamespace(id=uuid4())
    decisions = [
        await limiter.check_mutation_rate_limit(
            request, user=user, action="group_create"
        )
        for _ in range(11)
    ]

    assert all(decision.allowed for decision in decisions[:-1])
    assert decisions[-1].allowed is False
    assert decisions[-1].retry_after == 60 * 60
    assert all("203.0.113.24" not in key for key in redis_limiter.keys_seen)
    assert all(str(user.id) not in key for key in redis_limiter.keys_seen)


async def test_mutation_limits_are_isolated_by_account(redis_limiter):
    request = request_for("198.51.100.7")
    first_user = SimpleNamespace(id=uuid4())
    second_user = SimpleNamespace(id=uuid4())

    for _ in range(10):
        assert (
            await limiter.check_mutation_rate_limit(
                request, user=first_user, action="group_create"
            )
        ).allowed

    assert (
        await limiter.check_mutation_rate_limit(
            request_for("198.51.100.8"),
            user=second_user,
            action="group_create",
        )
    ).allowed


async def test_vote_policy_uses_short_burst_window(redis_limiter):
    request = request_for("192.0.2.8")
    user = SimpleNamespace(id=uuid4())

    for _ in range(180):
        assert (
            await limiter.check_mutation_rate_limit(
                request, user=user, action="vote"
            )
        ).allowed

    blocked = await limiter.check_mutation_rate_limit(
        request, user=user, action="vote"
    )
    assert blocked.allowed is False
    assert blocked.retry_after == 60


async def test_mutation_limiter_fails_closed_in_production(monkeypatch):
    monkeypatch.setattr(shared_rate_limit, "_redis_client", FailingRedis())
    monkeypatch.setattr(
        shared_rate_limit.settings, "rate_limit_redis_url", "redis://test"
    )
    monkeypatch.setattr(limiter.settings, "env", "production")

    with pytest.raises(limiter.MutationRateLimitUnavailable):
        await limiter.check_mutation_rate_limit(
            request_for("203.0.113.9"),
            user=SimpleNamespace(id=uuid4()),
            action="session_setup",
        )
