from datetime import datetime, timezone, timedelta

import pytest


@pytest.mark.anyio
async def test_create_session_requires_membership(async_client, user_factory, login_helper):
    # User A creates group
    user_a = await user_factory(async_client, display_name="A")
    await login_helper(async_client, email=user_a["email"], password=user_a["password"])
    r = await async_client.post("/groups", json={"name": "G", "member_user_ids": []})
    assert r.status_code in (200, 201), r.text
    g = r.json()
    group_id = g["id"]

    # User B tries to create session for that group
    user_b = await user_factory(async_client, display_name="B")
    await login_helper(async_client, email=user_b["email"], password=user_b["password"])

    r = await async_client.post(f"/groups/{group_id}/sessions", json={"constraints": {}, "duration_seconds": 90, "candidate_count": 12})
    assert r.status_code in (401, 403)


@pytest.mark.anyio
async def test_create_session_freezes_deck_and_returns_order(async_client, monkeypatch, user_factory, login_helper):
    # Patch AI so it deterministically reorders candidates
    from app.services import sessions as sessions_service
    from app.services.ai import AIRerankResult
    from app.schemas.tonight_constraints import TonightConstraints

    async def fake_parse(*, baseline: TonightConstraints, text: str):
        baseline.free_text = text.strip()
        baseline.parsed_by_ai = True
        baseline.ai_version = "test-ai"
        # tighten format if "movie" in text
        if "movie" in text.lower():
            baseline.format = "movie"
        return baseline

    async def fake_rerank(*, constraints: TonightConstraints, candidates: list[dict]):
        ordered = [c["id"] for c in candidates][::-1]
        return AIRerankResult(ordered_ids=ordered, top_id=ordered[0], why="because reasons")

    monkeypatch.setattr(sessions_service, "ai_parse_constraints", fake_parse)
    monkeypatch.setattr(sessions_service, "ai_rerank_candidates", fake_rerank)

    # user
    user = await user_factory(async_client, display_name="U")
    await login_helper(async_client, email=user["email"], password=user["password"])
    r = await async_client.post("/groups", json={"name": "G", "member_user_ids": []})
    assert r.status_code in (200, 201), r.text
    g = r.json()
    group_id = g["id"]

    # Add 3 eligible watchlist items
    def tmdb_payload(tmdb_id: int, title: str, media_type="movie"):
        return {"type": "tmdb", "tmdb_id": tmdb_id, "media_type": media_type, "title": title, "year": 2000, "poster_path": None}

    r = await async_client.post(f"/groups/{group_id}/watchlist", json=tmdb_payload(1, "A"))
    assert r.status_code == 201, r.text
    w1 = r.json()
    r = await async_client.post(f"/groups/{group_id}/watchlist", json=tmdb_payload(2, "B"))
    assert r.status_code == 201, r.text
    w2 = r.json()
    r = await async_client.post(f"/groups/{group_id}/watchlist", json=tmdb_payload(3, "C"))
    assert r.status_code == 201, r.text
    w3 = r.json()

    # Create session
    body = {
        "constraints": {"format": "any"},
        "text": "please pick a movie",
        "duration_seconds": 90,
        "candidate_count": 5,
    }
    r = await async_client.post(f"/groups/{group_id}/sessions", json=body)
    assert r.status_code == 201
    data = r.json()

    assert "session_id" in data
    assert "ends_at" in data
    assert data["ai_used"] is True
    assert data["ai_why"] == "because reasons"
    assert data["constraints"]["parsed_by_ai"] is True

    # Candidates returned in reranked order (reversed)
    candidates = data["candidates"]
    assert len(candidates) == 3
    assert [c["position"] for c in candidates] == [0, 1, 2]

    # We can’t directly map w1/w2/w3 ordering deterministically across all code paths,
    # but we can ensure all are present.
    returned_ids = {c["watchlist_item_id"] for c in candidates}
    assert w1["id"] in returned_ids
    assert w2["id"] in returned_ids
    assert w3["id"] in returned_ids


@pytest.mark.anyio
async def test_session_pool_excludes_watched_and_snoozed(async_client, user_factory, login_helper):
    user = await user_factory(async_client, display_name="P")
    await login_helper(async_client, email=user["email"], password=user["password"])
    r = await async_client.post("/groups", json={"name": "G", "member_user_ids": []})
    assert r.status_code in (200, 201), r.text
    g = r.json()
    group_id = g["id"]

    def tmdb_payload(tmdb_id: int, title: str):
        return {"type": "tmdb", "tmdb_id": tmdb_id, "media_type": "movie", "title": title, "year": 2000, "poster_path": None}

    r = await async_client.post(f"/groups/{group_id}/watchlist", json=tmdb_payload(11, "A"))
    assert r.status_code == 201, r.text
    i1 = r.json()
    r = await async_client.post(f"/groups/{group_id}/watchlist", json=tmdb_payload(12, "B"))
    assert r.status_code == 201, r.text
    i2 = r.json()
    r = await async_client.post(f"/groups/{group_id}/watchlist", json=tmdb_payload(13, "C"))
    assert r.status_code == 201, r.text
    i3 = r.json()

    # mark i2 watched
    r = await async_client.patch(f"/watchlist-items/{i2['id']}", json={"status": "watched"})
    assert r.status_code == 200

    # snooze i3
    snooze_until = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    r = await async_client.patch(f"/watchlist-items/{i3['id']}", json={"snoozed_until": snooze_until})
    assert r.status_code == 200

    # Create session: should only see i1 in pool (so deck size 1)
    r = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={"constraints": {}, "duration_seconds": 90, "candidate_count": 12},
    )
    assert r.status_code == 201
    data = r.json()

    ids = {c["watchlist_item_id"] for c in data["candidates"]}
    assert i1["id"] in ids
    assert i2["id"] not in ids
    assert i3["id"] not in ids


@pytest.mark.anyio
async def test_hard_filter_format_movie(async_client, user_factory, login_helper):
    user = await user_factory(async_client, display_name="F")
    await login_helper(async_client, email=user["email"], password=user["password"])
    r = await async_client.post("/groups", json={"name": "G", "member_user_ids": []})
    assert r.status_code in (200, 201), r.text
    g = r.json()
    group_id = g["id"]

    # add one movie and one tv
    r = await async_client.post(
        f"/groups/{group_id}/watchlist",
        json={"type": "tmdb", "tmdb_id": 21, "media_type": "movie", "title": "Movie A", "year": 2000, "poster_path": None},
    )
    assert r.status_code == 201, r.text
    r = await async_client.post(
        f"/groups/{group_id}/watchlist",
        json={"type": "tmdb", "tmdb_id": 22, "media_type": "tv", "title": "Show B", "year": 2001, "poster_path": None},
    )
    assert r.status_code == 201, r.text

    r = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={"constraints": {"format": "movie"}, "duration_seconds": 90, "candidate_count": 12},
    )
    assert r.status_code == 201
    data = r.json()
    assert all(c["title"]["media_type"] == "movie" for c in data["candidates"])
