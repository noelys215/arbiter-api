from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from redis.exceptions import ConnectionError
from starlette.requests import Request

from app.services import feedback_rate_limit as shared_rate_limit
from app.services import tmdb as tmdb_service
from app.services import tmdb_rate_limit as limiter


pytestmark = pytest.mark.anyio


class FakeRedis:
    def __init__(self):
        self.values: dict[str, int] = {}
        self.keys_seen: list[str] = []

    async def eval(self, script, key_count, *values):
        del script
        keys = list(values[:key_count])
        window, *limits = (int(value) for value in values[key_count:])
        self.keys_seen.extend(keys)

        if any(
            self.values.get(key, 0) >= limit
            for key, limit in zip(keys, limits, strict=True)
        ):
            return [0, window]
        for key in keys:
            self.values[key] = self.values.get(key, 0) + 1
        return [1, 0]


class FailingRedis:
    async def eval(self, *args, **kwargs):
        raise ConnectionError("private connection details")


def request_for(ip: str = "203.0.113.24") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/tmdb/search",
            "headers": [(b"x-forwarded-for", f"spoofed, {ip}".encode())],
            "client": ("127.0.0.1", 1234),
        }
    )


async def test_tmdb_search_requires_authentication(async_client):
    response = await async_client.get("/tmdb/search", params={"q": "matrix"})

    assert response.status_code == 401


async def test_tmdb_search_rejects_oversized_query(
    async_client, user_factory, login_helper
):
    user = await user_factory(async_client)
    await login_helper(
        async_client, email=user["email"], password=user["password"]
    )

    response = await async_client.get(
        "/tmdb/search",
        params={"q": "x" * (tmdb_service.TMDB_SEARCH_QUERY_MAX_LENGTH + 1)},
    )

    assert response.status_code == 422


async def test_tmdb_search_returns_retry_after_when_limited(
    async_client, user_factory, login_helper, monkeypatch
):
    from app.api.routes import tmdb as tmdb_routes

    user = await user_factory(async_client)
    await login_helper(
        async_client, email=user["email"], password=user["password"]
    )

    async def blocked(request, *, user):
        del request, user
        return limiter.TMDBRateLimitDecision(allowed=False, retry_after=17)

    monkeypatch.setattr(tmdb_routes, "check_tmdb_rate_limit", blocked)

    response = await async_client.get("/tmdb/search", params={"q": "matrix"})

    assert response.status_code == 429
    assert response.headers["retry-after"] == "17"


async def test_tmdb_rate_limit_is_shared_and_opaque(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(
        shared_rate_limit.settings, "rate_limit_redis_url", "redis://test"
    )
    monkeypatch.setattr(shared_rate_limit, "_redis_client", fake)
    user = SimpleNamespace(id=uuid4())
    request = request_for()

    decisions = [
        await limiter.check_tmdb_rate_limit(request, user=user)
        for _ in range(limiter.ACCOUNT_LIMIT + 1)
    ]

    assert all(decision.allowed for decision in decisions[:-1])
    assert decisions[-1].allowed is False
    assert decisions[-1].retry_after == limiter.WINDOW_SECONDS
    assert all("203.0.113.24" not in key for key in fake.keys_seen)
    assert all(str(user.id) not in key for key in fake.keys_seen)


async def test_tmdb_rate_limit_fails_closed_on_redis_error(monkeypatch):
    monkeypatch.setattr(
        shared_rate_limit.settings, "rate_limit_redis_url", "redis://test"
    )
    monkeypatch.setattr(shared_rate_limit, "_redis_client", FailingRedis())

    with pytest.raises(limiter.TMDBRateLimitUnavailable):
        await limiter.check_tmdb_rate_limit(
            request_for(), user=SimpleNamespace(id=uuid4())
        )


async def test_tmdb_cache_evicts_oldest_entry(monkeypatch):
    monkeypatch.setattr(tmdb_service, "_CACHE_MAX_ENTRIES", 2)
    tmdb_service._CACHE.clear()

    tmdb_service._cache_set("first", 1)
    tmdb_service._cache_set("second", 2)
    tmdb_service._cache_set("third", 3)

    assert list(tmdb_service._CACHE) == ["second", "third"]
    tmdb_service._CACHE.clear()
