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
async def test_watchlist_tmdb_add_populates_runtime_and_overview(async_client, monkeypatch, user_factory, login_helper):
    from app.services import watchlist as watchlist_service

    async def fake_details(*, tmdb_id: int, media_type: str):
        assert tmdb_id == 603
        assert media_type == "movie"
        return {"runtime_minutes": 136, "overview": "A computer hacker learns reality is a simulation."}

    monkeypatch.setattr(watchlist_service, "fetch_tmdb_title_details", fake_details)

    user = await user_factory(async_client, display_name="A")
    await login_helper(async_client, email=user["email"], password=user["password"])
    group_id = (await async_client.post("/groups", json={"name": "G", "member_user_ids": []})).json()["id"]

    payload = {
        "type": "tmdb",
        "tmdb_id": 603,
        "media_type": "movie",
        "title": "The Matrix",
        "year": 1999,
        "poster_path": "/matrix.jpg",
    }
    r = await async_client.post(f"/groups/{group_id}/watchlist", json=payload)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["title"]["runtime_minutes"] == 136
    assert data["title"]["overview"] == "A computer hacker learns reality is a simulation."


@pytest.mark.anyio
async def test_watchlist_tmdb_add_backfills_runtime_for_existing_title(async_client, monkeypatch, user_factory, login_helper):
    from app.services import watchlist as watchlist_service

    async def fake_details_none(*, tmdb_id: int, media_type: str):
        _ = (tmdb_id, media_type)
        return {}

    async def fake_details_runtime(*, tmdb_id: int, media_type: str):
        _ = (tmdb_id, media_type)
        return {"runtime_minutes": 121, "overview": "Backfilled overview"}

    monkeypatch.setattr(watchlist_service, "fetch_tmdb_title_details", fake_details_none)

    user = await user_factory(async_client, display_name="A")
    await login_helper(async_client, email=user["email"], password=user["password"])
    group_1 = (await async_client.post("/groups", json={"name": "G1", "member_user_ids": []})).json()["id"]
    group_2 = (await async_client.post("/groups", json={"name": "G2", "member_user_ids": []})).json()["id"]

    payload = {
        "type": "tmdb",
        "tmdb_id": 7001,
        "media_type": "movie",
        "title": "Legacy Runtime",
        "year": 2001,
        "poster_path": None,
    }

    r1 = await async_client.post(f"/groups/{group_1}/watchlist", json=payload)
    assert r1.status_code == 201, r1.text
    assert r1.json()["title"]["runtime_minutes"] is None

    monkeypatch.setattr(watchlist_service, "fetch_tmdb_title_details", fake_details_runtime)
    r2 = await async_client.post(f"/groups/{group_2}/watchlist", json=payload)
    assert r2.status_code == 201, r2.text
    assert r2.json()["title"]["runtime_minutes"] == 121

    listing = await async_client.get(f"/groups/{group_1}/watchlist")
    assert listing.status_code == 200, listing.text
    assert listing.json()[0]["title"]["runtime_minutes"] == 121


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


@pytest.mark.anyio
async def test_watchlist_paginated_query_and_load_more(async_client, user_factory, login_helper):
    user = await user_factory(async_client, display_name="Pager")
    await login_helper(async_client, email=user["email"], password=user["password"])

    group_id = (await async_client.post("/groups", json={"name": "G", "member_user_ids": []})).json()["id"]

    for title in ["Zulu", "Alpha", "Bravo"]:
        r = await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "manual", "title": title, "year": 2020, "media_type": "movie"},
        )
        assert r.status_code == 201, r.text

    first = await async_client.get(
        f"/groups/{group_id}/watchlist",
        params={"paginate": "true", "limit": 2, "sort": "recent"},
    )
    assert first.status_code == 200, first.text
    first_data = first.json()
    assert "items" in first_data
    assert first_data["total_count"] == 3
    assert len(first_data["items"]) == 2
    assert first_data["next_cursor"] is not None

    second = await async_client.get(
        f"/groups/{group_id}/watchlist",
        params={
            "paginate": "true",
            "limit": 2,
            "sort": "recent",
            "cursor": first_data["next_cursor"],
        },
    )
    assert second.status_code == 200, second.text
    second_data = second.json()
    assert len(second_data["items"]) == 1
    assert second_data["next_cursor"] is None
    assert second_data["total_count"] == 3

    oldest = await async_client.get(
        f"/groups/{group_id}/watchlist",
        params={"paginate": "true", "limit": 10, "sort": "oldest"},
    )
    assert oldest.status_code == 200, oldest.text
    oldest_names = [row["title"]["name"] for row in oldest.json()["items"]]
    assert oldest_names == ["Zulu", "Alpha", "Bravo"]

    searched = await async_client.get(
        f"/groups/{group_id}/watchlist",
        params={"paginate": "true", "q": "alp"},
    )
    assert searched.status_code == 200, searched.text
    searched_data = searched.json()
    assert searched_data["total_count"] == 1
    assert searched_data["items"][0]["title"]["name"] == "Alpha"


@pytest.mark.anyio
async def test_watchlist_paginated_genre_filter(async_client, monkeypatch, user_factory, login_helper):
    from app.services import watchlist as watchlist_service

    async def fake_taxonomy(*, tmdb_id: int, media_type: str):
        _ = media_type
        if tmdb_id == 1101:
            return {"science fiction"}, set(), {878}
        if tmdb_id == 1102:
            return {"romance"}, set(), {10749}
        return set(), set(), set()

    monkeypatch.setattr(watchlist_service, "fetch_tmdb_title_taxonomy", fake_taxonomy)

    user = await user_factory(async_client, display_name="Genre")
    await login_helper(async_client, email=user["email"], password=user["password"])
    group_id = (await async_client.post("/groups", json={"name": "G", "member_user_ids": []})).json()["id"]

    sci = await async_client.post(
        f"/groups/{group_id}/watchlist",
        json={"type": "tmdb", "tmdb_id": 1101, "media_type": "movie", "title": "Sci", "year": 2020, "poster_path": None},
    )
    assert sci.status_code == 201, sci.text
    _ = await async_client.post(
        f"/groups/{group_id}/watchlist",
        json={"type": "tmdb", "tmdb_id": 1102, "media_type": "movie", "title": "Rom", "year": 2020, "poster_path": None},
    )

    r = await async_client.get(
        f"/groups/{group_id}/watchlist",
        params={"paginate": "true", "genre_id": 878, "limit": 24},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total_count"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == sci.json()["id"]
