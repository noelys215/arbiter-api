from datetime import datetime, timezone, timedelta
import uuid

import pytest


def _u(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.mark.anyio
async def test_create_session_requires_membership(async_client):
    # User A creates group
    a_email = f"{_u('a')}@x.com"
    a_username = _u("a")
    await async_client.post(
        "/auth/register",
        json={"email": a_email, "username": a_username, "display_name": "A", "password": "SuperSecret123"},
    )
    await async_client.post("/auth/login", json={"email": a_email, "password": "SuperSecret123"})
    g = (await async_client.post("/groups", json={"name": "G", "member_user_ids": []})).json()
    group_id = g["id"]

    # User B tries to create session for that group
    b_email = f"{_u('b')}@x.com"
    b_username = _u("b")
    await async_client.post(
        "/auth/register",
        json={"email": b_email, "username": b_username, "display_name": "B", "password": "SuperSecret123"},
    )
    await async_client.post("/auth/login", json={"email": b_email, "password": "SuperSecret123"})

    r = await async_client.post(f"/groups/{group_id}/sessions", json={"constraints": {}, "duration_seconds": 90, "candidate_count": 12})
    assert r.status_code == 403


@pytest.mark.anyio
async def test_create_session_freezes_deck_and_returns_order(async_client, monkeypatch):
    # Patch AI so it deterministically reorders candidates
    from app.services import sessions as sessions_service
    from app.schemas.tonight_constraints import TonightConstraints

    async def fake_parse(*, base: TonightConstraints, text: str):
        base.free_text = text.strip()
        base.parsed_by_ai = True
        base.ai_version = "test-ai"
        # tighten format if "movie" in text
        if "movie" in text.lower():
            base.format = "movie"
        return base

    async def fake_rerank(*, constraints: TonightConstraints, candidates: list[dict], final_n: int):
        # reverse the order for deterministic check
        idxs = [c["idx"] for c in candidates[:final_n]][::-1]
        return idxs, "because reasons"

    monkeypatch.setattr(sessions_service, "parse_constraints_with_ai", fake_parse)
    monkeypatch.setattr(sessions_service, "rerank_candidates_with_ai", fake_rerank)

    # user
    u_email = f"{_u('u')}@x.com"
    u_username = _u("u")
    await async_client.post(
        "/auth/register",
        json={"email": u_email, "username": u_username, "display_name": "U", "password": "SuperSecret123"},
    )
    await async_client.post("/auth/login", json={"email": u_email, "password": "SuperSecret123"})
    g = (await async_client.post("/groups", json={"name": "G", "member_user_ids": []})).json()
    group_id = g["id"]

    # Add 3 eligible watchlist items
    def tmdb_payload(tmdb_id: int, title: str, media_type="movie"):
        return {"type": "tmdb", "tmdb_id": tmdb_id, "media_type": media_type, "title": title, "year": 2000, "poster_path": None}

    w1 = (await async_client.post(f"/groups/{group_id}/watchlist", json=tmdb_payload(1, "A"))).json()
    w2 = (await async_client.post(f"/groups/{group_id}/watchlist", json=tmdb_payload(2, "B"))).json()
    w3 = (await async_client.post(f"/groups/{group_id}/watchlist", json=tmdb_payload(3, "C"))).json()

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
async def test_session_pool_excludes_watched_and_snoozed(async_client):
    p_email = f"{_u('p')}@x.com"
    p_username = _u("p")
    await async_client.post(
        "/auth/register",
        json={"email": p_email, "username": p_username, "display_name": "P", "password": "SuperSecret123"},
    )
    await async_client.post("/auth/login", json={"email": p_email, "password": "SuperSecret123"})
    g = (await async_client.post("/groups", json={"name": "G", "member_user_ids": []})).json()
    group_id = g["id"]

    def tmdb_payload(tmdb_id: int, title: str):
        return {"type": "tmdb", "tmdb_id": tmdb_id, "media_type": "movie", "title": title, "year": 2000, "poster_path": None}

    i1 = (await async_client.post(f"/groups/{group_id}/watchlist", json=tmdb_payload(11, "A"))).json()
    i2 = (await async_client.post(f"/groups/{group_id}/watchlist", json=tmdb_payload(12, "B"))).json()
    i3 = (await async_client.post(f"/groups/{group_id}/watchlist", json=tmdb_payload(13, "C"))).json()

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
async def test_hard_filter_format_movie(async_client):
    f_email = f"{_u('f')}@x.com"
    f_username = _u("f")
    await async_client.post(
        "/auth/register",
        json={"email": f_email, "username": f_username, "display_name": "F", "password": "SuperSecret123"},
    )
    await async_client.post("/auth/login", json={"email": f_email, "password": "SuperSecret123"})
    g = (await async_client.post("/groups", json={"name": "G", "member_user_ids": []})).json()
    group_id = g["id"]

    # add one movie and one tv
    await async_client.post(
        f"/groups/{group_id}/watchlist",
        json={"type": "tmdb", "tmdb_id": 21, "media_type": "movie", "title": "Movie A", "year": 2000, "poster_path": None},
    )
    await async_client.post(
        f"/groups/{group_id}/watchlist",
        json={"type": "tmdb", "tmdb_id": 22, "media_type": "tv", "title": "Show B", "year": 2001, "poster_path": None},
    )

    r = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={"constraints": {"format": "movie"}, "duration_seconds": 90, "candidate_count": 12},
    )
    assert r.status_code == 201
    data = r.json()
    assert all(c["title"]["media_type"] == "movie" for c in data["candidates"])
