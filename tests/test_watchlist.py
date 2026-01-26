from datetime import datetime, timedelta, timezone

import pytest


@pytest.mark.anyio
async def test_tmdb_search_returns_compact_shape(async_client, monkeypatch, user_factory, login_helper):
    from app.api.routes import tmdb as tmdb_routes

    async def fake_search(q: str):
        assert q == "matrix"
        return [
            {"tmdb_id": 603, "media_type": "movie", "title": "The Matrix", "year": 1999, "poster_path": "/m.jpg"},
            {"tmdb_id": 604, "media_type": "tv", "title": "Matrix TV", "year": 2003, "poster_path": None},
        ]

    monkeypatch.setattr(tmdb_routes, "tmdb_search_multi", fake_search)

    user = await user_factory(async_client, display_name="A")
    await login_helper(async_client, email=user["email"], password=user["password"])

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
async def test_watchlist_membership_required(async_client, user_factory, login_helper):
    # A creates group
    user_a = await user_factory(async_client, display_name="A1")
    await login_helper(async_client, email=user_a["email"], password=user_a["password"])
    r = await async_client.post("/groups", json={"name": "Solo", "member_user_ids": []})
    assert r.status_code in (200, 201), r.text
    g = r.json()
    group_id = g["id"]

    # B tries to access watchlist
    user_b = await user_factory(async_client, display_name="B1")
    await login_helper(async_client, email=user_b["email"], password=user_b["password"])
    r = await async_client.get(f"/groups/{group_id}/watchlist")
    assert r.status_code in (401, 403)


@pytest.mark.anyio
async def test_watchlist_tmdb_add_and_duplicate_returns_existing(async_client, user_factory, login_helper):
    user = await user_factory(async_client, display_name="A")
    await login_helper(async_client, email=user["email"], password=user["password"])

    r = await async_client.post("/groups", json={"name": "G", "member_user_ids": []})
    assert r.status_code in (200, 201), r.text
    g = r.json()
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
async def test_watchlist_manual_add(async_client, user_factory, login_helper):
    user = await user_factory(async_client, display_name="A")
    await login_helper(async_client, email=user["email"], password=user["password"])

    r = await async_client.post("/groups", json={"name": "G", "member_user_ids": []})
    assert r.status_code in (200, 201), r.text
    g = r.json()
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
async def test_tonight_filter_excludes_watched_and_snoozed(async_client, user_factory, login_helper):
    user = await user_factory(async_client, display_name="A")
    await login_helper(async_client, email=user["email"], password=user["password"])

    r = await async_client.post("/groups", json={"name": "G", "member_user_ids": []})
    assert r.status_code in (200, 201), r.text
    g = r.json()
    group_id = g["id"]

    # Add 3 items
    def tmdb_payload(tmdb_id: int, title: str):
        return {"type": "tmdb", "tmdb_id": tmdb_id, "media_type": "movie", "title": title, "year": 2000, "poster_path": None}

    r = await async_client.post(f"/groups/{group_id}/watchlist", json=tmdb_payload(1, "A"))
    assert r.status_code == 201, r.text
    i1 = r.json()
    r = await async_client.post(f"/groups/{group_id}/watchlist", json=tmdb_payload(2, "B"))
    assert r.status_code == 201, r.text
    i2 = r.json()
    r = await async_client.post(f"/groups/{group_id}/watchlist", json=tmdb_payload(3, "C"))
    assert r.status_code == 201, r.text
    i3 = r.json()

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
async def test_unsnooze_brings_item_back(async_client, user_factory, login_helper):
    user = await user_factory(async_client, display_name="A")
    await login_helper(async_client, email=user["email"], password=user["password"])

    r = await async_client.post("/groups", json={"name": "G", "member_user_ids": []})
    assert r.status_code in (200, 201), r.text
    g = r.json()
    group_id = g["id"]

    r = await async_client.post(
        f"/groups/{group_id}/watchlist",
        json={"type": "tmdb", "tmdb_id": 11, "media_type": "movie", "title": "X", "year": 2001, "poster_path": None},
    )
    assert r.status_code == 201, r.text
    item = r.json()

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
async def test_remove_deletes_item(async_client, user_factory, login_helper):
    user = await user_factory(async_client, display_name="A")
    await login_helper(async_client, email=user["email"], password=user["password"])

    r = await async_client.post("/groups", json={"name": "G", "member_user_ids": []})
    assert r.status_code in (200, 201), r.text
    g = r.json()
    group_id = g["id"]

    r = await async_client.post(
        f"/groups/{group_id}/watchlist",
        json={"type": "manual", "title": "Delete Me", "year": 2020, "media_type": "movie"},
    )
    assert r.status_code == 201, r.text
    item = r.json()

    r = await async_client.patch(f"/watchlist-items/{item['id']}", json={"remove": True})
    assert r.status_code == 200
    assert r.json()["removed"] is True

    r = await async_client.get(f"/groups/{group_id}/watchlist")
    ids = {x["id"] for x in r.json()}
    assert item["id"] not in ids


@pytest.mark.anyio
async def test_tonight_filter_includes_when_snooze_expired(async_client, user_factory, login_helper):
    user = await user_factory(async_client, display_name="A")
    await login_helper(async_client, email=user["email"], password=user["password"])

    r = await async_client.post("/groups", json={"name": "G", "member_user_ids": []})
    assert r.status_code in (200, 201), r.text
    g = r.json()
    group_id = g["id"]

    r = await async_client.post(
        f"/groups/{group_id}/watchlist",
        json={"type": "tmdb", "tmdb_id": 99, "media_type": "movie", "title": "Expired", "year": 2000, "poster_path": None},
    )
    assert r.status_code == 201, r.text
    item = r.json()

    # Snooze in the past -> should still appear in tonight pool
    snooze_until = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    r = await async_client.patch(f"/watchlist-items/{item['id']}", json={"snoozed_until": snooze_until})
    assert r.status_code == 200

    r = await async_client.get(f"/groups/{group_id}/watchlist", params={"tonight": "true"})
    assert r.status_code == 200
    assert item["id"] in {x["id"] for x in r.json()}


@pytest.mark.anyio
async def test_patch_empty_body_rejected(async_client, user_factory, login_helper):
    user = await user_factory(async_client, display_name="A")
    await login_helper(async_client, email=user["email"], password=user["password"])

    r = await async_client.post("/groups", json={"name": "G", "member_user_ids": []})
    assert r.status_code in (200, 201), r.text
    g = r.json()
    group_id = g["id"]

    r = await async_client.post(
        f"/groups/{group_id}/watchlist",
        json={"type": "manual", "title": "X", "year": 2020, "media_type": "movie"},
    )
    assert r.status_code == 201, r.text
    item = r.json()

    r = await async_client.patch(f"/watchlist-items/{item['id']}", json={})
    assert r.status_code in (400, 422)
