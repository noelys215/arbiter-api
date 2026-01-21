import uuid
from datetime import datetime, timedelta, timezone

import pytest


def _u(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.mark.anyio
async def test_tmdb_search_returns_compact_shape(async_client, monkeypatch):
    from app.api.routes import tmdb as tmdb_routes

    async def fake_search(q: str):
        assert q == "matrix"
        return [
            {"tmdb_id": 603, "media_type": "movie", "title": "The Matrix", "year": 1999, "poster_path": "/m.jpg"},
            {"tmdb_id": 604, "media_type": "tv", "title": "Matrix TV", "year": 2003, "poster_path": None},
        ]

    monkeypatch.setattr(tmdb_routes, "tmdb_search_multi", fake_search)

    email = f"{_u('a')}@x.com"
    username = _u("a")
    await async_client.post(
        "/auth/register",
        json={"email": email, "username": username, "display_name": "A", "password": "SuperSecret123"},
    )
    r = await async_client.post("/auth/login", json={"email": email, "password": "SuperSecret123"})
    assert r.status_code == 200

    r = await async_client.get("/tmdb/search", params={"q": "matrix", "type": "multi"})
    assert r.status_code == 200
    data = r.json()

    assert isinstance(data, list)
    assert data[0]["tmdb_id"] == 603
    assert data[0]["media_type"] in ("movie", "tv")
    assert "title" in data[0]
    assert "year" in data[0]
    assert "poster_path" in data[0]


@pytest.mark.anyio
async def test_watchlist_membership_required(async_client):
    # A creates group
    a_email = f"{_u('a1')}@x.com"
    a_username = _u("a1")
    await async_client.post(
        "/auth/register",
        json={"email": a_email, "username": a_username, "display_name": "A1", "password": "SuperSecret123"},
    )
    await async_client.post("/auth/login", json={"email": a_email, "password": "SuperSecret123"})
    g = (await async_client.post("/groups", json={"name": "Solo", "member_user_ids": []})).json()
    group_id = g["id"]

    # B tries to access watchlist
    b_email = f"{_u('b1')}@x.com"
    b_username = _u("b1")
    await async_client.post(
        "/auth/register",
        json={"email": b_email, "username": b_username, "display_name": "B1", "password": "SuperSecret123"},
    )
    await async_client.post("/auth/login", json={"email": b_email, "password": "SuperSecret123"})
    r = await async_client.get(f"/groups/{group_id}/watchlist")
    assert r.status_code == 403


@pytest.mark.anyio
async def test_watchlist_tmdb_add_and_duplicate_returns_existing(async_client):
    a_email = f"{_u('a_tmdb')}@x.com"
    a_username = _u("a_tmdb")
    await async_client.post(
        "/auth/register",
        json={"email": a_email, "username": a_username, "display_name": "A", "password": "SuperSecret123"},
    )
    await async_client.post("/auth/login", json={"email": a_email, "password": "SuperSecret123"})

    g = (await async_client.post("/groups", json={"name": "G", "member_user_ids": []})).json()
    group_id = g["id"]

    payload = {
        "type": "tmdb",
        "tmdb_id": 603,
        "media_type": "movie",
        "title": "The Matrix",
        "year": 1999,
        "poster_path": "/matrix.jpg",
    }

    r1 = await async_client.post(f"/groups/{group_id}/watchlist", json=payload)
    assert r1.status_code == 201
    item1 = r1.json()
    assert item1["already_exists"] is False
    assert item1["title"]["source"] == "tmdb"
    assert item1["title"]["source_id"] == "603"
    assert item1["title"]["name"] == "The Matrix"
    assert item1["title"]["release_year"] == 1999
    assert item1["title"]["poster_path"] == "/matrix.jpg"

    r2 = await async_client.post(f"/groups/{group_id}/watchlist", json=payload)
    assert r2.status_code == 201
    item2 = r2.json()
    assert item2["already_exists"] is True
    assert item2["id"] == item1["id"]


@pytest.mark.anyio
async def test_watchlist_manual_add(async_client):
    a_email = f"{_u('a_manual')}@x.com"
    a_username = _u("a_manual")
    await async_client.post(
        "/auth/register",
        json={"email": a_email, "username": a_username, "display_name": "A", "password": "SuperSecret123"},
    )
    await async_client.post("/auth/login", json={"email": a_email, "password": "SuperSecret123"})

    g = (await async_client.post("/groups", json={"name": "G", "member_user_ids": []})).json()
    group_id = g["id"]

    payload = {"type": "manual", "title": "The Thing", "year": 1982, "media_type": "movie"}
    r = await async_client.post(f"/groups/{group_id}/watchlist", json=payload)
    assert r.status_code == 201
    item = r.json()
    assert item["already_exists"] is False
    assert item["title"]["source"] == "manual"
    assert item["title"]["source_id"] is None
    assert item["title"]["name"] == "The Thing"


@pytest.mark.anyio
async def test_tonight_filter_excludes_watched_and_snoozed(async_client):
    a_email = f"{_u('a_pool')}@x.com"
    a_username = _u("a_pool")
    await async_client.post(
        "/auth/register",
        json={"email": a_email, "username": a_username, "display_name": "A", "password": "SuperSecret123"},
    )
    await async_client.post("/auth/login", json={"email": a_email, "password": "SuperSecret123"})

    g = (await async_client.post("/groups", json={"name": "G", "member_user_ids": []})).json()
    group_id = g["id"]

    # Add 3 items
    def tmdb_payload(tmdb_id: int, title: str):
        return {"type": "tmdb", "tmdb_id": tmdb_id, "media_type": "movie", "title": title, "year": 2000, "poster_path": None}

    i1 = (await async_client.post(f"/groups/{group_id}/watchlist", json=tmdb_payload(1, "A"))).json()
    i2 = (await async_client.post(f"/groups/{group_id}/watchlist", json=tmdb_payload(2, "B"))).json()
    i3 = (await async_client.post(f"/groups/{group_id}/watchlist", json=tmdb_payload(3, "C"))).json()

    # Mark i2 watched
    r = await async_client.patch(f"/watchlist-items/{i2['id']}", json={"status": "watched"})
    assert r.status_code == 200

    # Snooze i3 for 2 days
    snooze_until = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    r = await async_client.patch(f"/watchlist-items/{i3['id']}", json={"snoozed_until": snooze_until})
    assert r.status_code == 200

    # Tonight list
    r = await async_client.get(f"/groups/{group_id}/watchlist", params={"tonight": "true"})
    assert r.status_code == 200
    data = r.json()

    # Only i1 should remain
    ids = {x["id"] for x in data}
    assert i1["id"] in ids
    assert i2["id"] not in ids
    assert i3["id"] not in ids


@pytest.mark.anyio
async def test_unsnooze_brings_item_back(async_client):
    a_email = f"{_u('a_uns')}@x.com"
    a_username = _u("a_uns")
    await async_client.post(
        "/auth/register",
        json={"email": a_email, "username": a_username, "display_name": "A", "password": "SuperSecret123"},
    )
    await async_client.post("/auth/login", json={"email": a_email, "password": "SuperSecret123"})

    g = (await async_client.post("/groups", json={"name": "G", "member_user_ids": []})).json()
    group_id = g["id"]

    item = (await async_client.post(
        f"/groups/{group_id}/watchlist",
        json={"type": "tmdb", "tmdb_id": 11, "media_type": "movie", "title": "X", "year": 2001, "poster_path": None},
    )).json()

    snooze_until = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    await async_client.patch(f"/watchlist-items/{item['id']}", json={"snoozed_until": snooze_until})

    r = await async_client.get(f"/groups/{group_id}/watchlist", params={"tonight": "true"})
    assert item["id"] not in {x["id"] for x in r.json()}

    # Unsnooze (explicit null)
    r = await async_client.patch(f"/watchlist-items/{item['id']}", json={"snoozed_until": None})
    assert r.status_code == 200

    r = await async_client.get(f"/groups/{group_id}/watchlist", params={"tonight": "true"})
    assert item["id"] in {x["id"] for x in r.json()}


@pytest.mark.anyio
async def test_remove_deletes_item(async_client):
    a_email = f"{_u('a_del')}@x.com"
    a_username = _u("a_del")
    await async_client.post(
        "/auth/register",
        json={"email": a_email, "username": a_username, "display_name": "A", "password": "SuperSecret123"},
    )
    await async_client.post("/auth/login", json={"email": a_email, "password": "SuperSecret123"})

    g = (await async_client.post("/groups", json={"name": "G", "member_user_ids": []})).json()
    group_id = g["id"]

    item = (await async_client.post(
        f"/groups/{group_id}/watchlist",
        json={"type": "manual", "title": "Delete Me", "year": 2020, "media_type": "movie"},
    )).json()

    r = await async_client.patch(f"/watchlist-items/{item['id']}", json={"remove": True})
    assert r.status_code == 200
    assert r.json()["removed"] is True

    r = await async_client.get(f"/groups/{group_id}/watchlist")
    ids = {x["id"] for x in r.json()}
    assert item["id"] not in ids


@pytest.mark.anyio
async def test_tonight_filter_includes_when_snooze_expired(async_client):
    a_email = f"{_u('a_exp')}@x.com"
    a_username = _u("a_exp")
    await async_client.post(
        "/auth/register",
        json={"email": a_email, "username": a_username, "display_name": "A", "password": "SuperSecret123"},
    )
    await async_client.post("/auth/login", json={"email": a_email, "password": "SuperSecret123"})

    g = (await async_client.post("/groups", json={"name": "G", "member_user_ids": []})).json()
    group_id = g["id"]

    item = (await async_client.post(
        f"/groups/{group_id}/watchlist",
        json={"type": "tmdb", "tmdb_id": 99, "media_type": "movie", "title": "Expired", "year": 2000, "poster_path": None},
    )).json()

    # Snooze in the past -> should still appear in tonight pool
    snooze_until = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    r = await async_client.patch(f"/watchlist-items/{item['id']}", json={"snoozed_until": snooze_until})
    assert r.status_code == 200

    r = await async_client.get(f"/groups/{group_id}/watchlist", params={"tonight": "true"})
    assert r.status_code == 200
    assert item["id"] in {x["id"] for x in r.json()}


@pytest.mark.anyio
async def test_patch_empty_body_rejected(async_client):
    a_email = f"{_u('a_patch')}@x.com"
    a_username = _u("a_patch")
    await async_client.post(
        "/auth/register",
        json={"email": a_email, "username": a_username, "display_name": "A", "password": "SuperSecret123"},
    )
    await async_client.post("/auth/login", json={"email": a_email, "password": "SuperSecret123"})

    g = (await async_client.post("/groups", json={"name": "G", "member_user_ids": []})).json()
    group_id = g["id"]

    item = (await async_client.post(
        f"/groups/{group_id}/watchlist",
        json={"type": "manual", "title": "X", "year": 2020, "media_type": "movie"},
    )).json()

    r = await async_client.patch(f"/watchlist-items/{item['id']}", json={})
    assert r.status_code in (400, 422)
