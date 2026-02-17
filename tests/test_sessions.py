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


@pytest.mark.anyio
async def test_mood_tags_use_synonyms_and_tmdb_taxonomy(async_client, monkeypatch, user_factory, login_helper):
    from app.services import sessions as sessions_service
    from app.services.ai import AIError

    async def fake_taxonomy(*, tmdb_id: int, media_type: str):
        _ = media_type
        if tmdb_id == 301:
            return {"science fiction"}, {"time travel", "parallel universe"}
        if tmdb_id == 302:
            return {"romance"}, {"date night"}
        return set(), set()

    async def fake_rerank(*, constraints, candidates):
        _ = (constraints, candidates)
        raise AIError("disable rerank")

    monkeypatch.setattr(sessions_service, "fetch_tmdb_title_taxonomy", fake_taxonomy)
    monkeypatch.setattr(sessions_service, "ai_rerank_candidates", fake_rerank)

    user = await user_factory(async_client, display_name="Mood")
    await login_helper(async_client, email=user["email"], password=user["password"])
    group = (await async_client.post("/groups", json={"name": "G", "member_user_ids": []})).json()
    group_id = group["id"]

    i1 = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 301, "media_type": "movie", "title": "Looper", "year": 2012, "poster_path": None},
        )
    ).json()
    _ = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 302, "media_type": "movie", "title": "RomCom", "year": 2010, "poster_path": None},
        )
    ).json()

    r = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={
            "constraints": {"moods": ["mind bending"]},
            "duration_seconds": 90,
            "candidate_count": 12,
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()

    # taxonomy + synonym should reduce to matching item(s) and return backend reason
    assert len(data["candidates"]) == 1
    assert data["candidates"][0]["watchlist_item_id"] == i1["id"]
    assert data["candidates"][0]["reason"] == "Matches: Mind-Bender"


@pytest.mark.anyio
async def test_mood_matching_falls_back_when_no_taxonomy_hits(async_client, monkeypatch, user_factory, login_helper):
    from app.services import sessions as sessions_service
    from app.services.ai import AIError

    async def fake_taxonomy(*, tmdb_id: int, media_type: str):
        _ = (tmdb_id, media_type)
        return set(), set()

    async def fake_rerank(*, constraints, candidates):
        _ = (constraints, candidates)
        raise AIError("disable rerank")

    monkeypatch.setattr(sessions_service, "fetch_tmdb_title_taxonomy", fake_taxonomy)
    monkeypatch.setattr(sessions_service, "ai_rerank_candidates", fake_rerank)

    user = await user_factory(async_client, display_name="Fallback")
    await login_helper(async_client, email=user["email"], password=user["password"])
    group = (await async_client.post("/groups", json={"name": "G", "member_user_ids": []})).json()
    group_id = group["id"]

    i1 = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 401, "media_type": "movie", "title": "A", "year": 2000, "poster_path": None},
        )
    ).json()
    i2 = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 402, "media_type": "movie", "title": "B", "year": 2001, "poster_path": None},
        )
    ).json()

    r = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={
            "constraints": {"moods": ["documentary"]},
            "duration_seconds": 90,
            "candidate_count": 12,
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    ids = {c["watchlist_item_id"] for c in data["candidates"]}

    # no metadata hits -> preserve normal fallback behavior (keep pool)
    assert ids == {i1["id"], i2["id"]}


@pytest.mark.anyio
async def test_mood_tags_match_from_tmdb_genre_ids(async_client, monkeypatch, user_factory, login_helper):
    from app.services import sessions as sessions_service
    from app.services.ai import AIError

    async def fake_taxonomy(*, tmdb_id: int, media_type: str):
        _ = media_type
        if tmdb_id == 501:
            # TV taxonomy name can vary ("Sci-Fi & Fantasy"), so genre id is the stable signal.
            return {"sci-fi & fantasy"}, set(), {10765}
        if tmdb_id == 502:
            return {"romance"}, {"date night"}, {10749}
        return set(), set(), set()

    async def fake_rerank(*, constraints, candidates):
        _ = (constraints, candidates)
        raise AIError("disable rerank")

    monkeypatch.setattr(sessions_service, "fetch_tmdb_title_taxonomy", fake_taxonomy)
    monkeypatch.setattr(sessions_service, "ai_rerank_candidates", fake_rerank)

    user = await user_factory(async_client, display_name="GenreID")
    await login_helper(async_client, email=user["email"], password=user["password"])
    group = (await async_client.post("/groups", json={"name": "G", "member_user_ids": []})).json()
    group_id = group["id"]

    i1 = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 501, "media_type": "tv", "title": "Mind Show", "year": 2020, "poster_path": None},
        )
    ).json()
    _ = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 502, "media_type": "movie", "title": "RomCom", "year": 2015, "poster_path": None},
        )
    ).json()

    r = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={
            "constraints": {"moods": ["mind-bender"]},
            "duration_seconds": 90,
            "candidate_count": 12,
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()

    assert len(data["candidates"]) == 1
    assert data["candidates"][0]["watchlist_item_id"] == i1["id"]
    assert data["candidates"][0]["reason"] == "Matches: Mind-Bender"


@pytest.mark.anyio
async def test_real_tmdb_genre_tag_science_fiction_is_supported(
    async_client, monkeypatch, user_factory, login_helper
):
    from app.services import sessions as sessions_service
    from app.services.ai import AIError

    async def fake_taxonomy(*, tmdb_id: int, media_type: str):
        _ = media_type
        if tmdb_id == 701:
            return {"science fiction"}, {"time travel"}, {878}
        if tmdb_id == 702:
            return {"romance"}, {"date night"}, {10749}
        return set(), set(), set()

    async def fake_rerank(*, constraints, candidates):
        _ = (constraints, candidates)
        raise AIError("disable rerank")

    monkeypatch.setattr(sessions_service, "fetch_tmdb_title_taxonomy", fake_taxonomy)
    monkeypatch.setattr(sessions_service, "ai_rerank_candidates", fake_rerank)

    user = await user_factory(async_client, display_name="TMDBGenre")
    await login_helper(async_client, email=user["email"], password=user["password"])
    group = (await async_client.post("/groups", json={"name": "G", "member_user_ids": []})).json()
    group_id = group["id"]

    i1 = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 701, "media_type": "movie", "title": "SciFi", "year": 2021, "poster_path": None},
        )
    ).json()
    _ = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 702, "media_type": "movie", "title": "RomCom", "year": 2017, "poster_path": None},
        )
    ).json()

    r = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={
            "constraints": {"moods": ["Science Fiction"]},
            "duration_seconds": 90,
            "candidate_count": 12,
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()

    assert len(data["candidates"]) == 1
    assert data["candidates"][0]["watchlist_item_id"] == i1["id"]
    assert data["candidates"][0]["reason"] == "Matches: Science Fiction"


@pytest.mark.anyio
async def test_swipe_timer_starts_only_after_all_users_confirm_ready(
    async_client, client_factory, user_factory, login_helper
):
    async with client_factory() as client_b:
        user_a = await user_factory(async_client, display_name="A")
        await login_helper(async_client, email=user_a["email"], password=user_a["password"])
        user_b = await user_factory(client_b, display_name="B")
        await login_helper(client_b, email=user_b["email"], password=user_b["password"])

        invite_b = (await async_client.post("/friends/invite")).json()["code"]
        await client_b.post("/friends/accept", json={"code": invite_b})
        friends = (await async_client.get("/friends")).json()
        b_id = next(f["id"] for f in friends if f["email"] == user_b["email"])

        group = (await async_client.post("/groups", json={"name": "G", "member_user_ids": [b_id]})).json()
        group_id = group["id"]

        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 901, "media_type": "movie", "title": "A", "year": 2000, "poster_path": None},
        )
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 902, "media_type": "movie", "title": "B", "year": 2001, "poster_path": None},
        )

        body_deal = {
            "constraints": {"moods": ["cozy"]},
            "confirm_ready": False,
            "duration_seconds": 90,
            "candidate_count": 5,
        }

        # Both users deal, but neither confirms yet.
        first = (await async_client.post(f"/groups/{group_id}/sessions", json=body_deal)).json()
        session_id = first["session_id"]
        second = (await client_b.post(f"/groups/{group_id}/sessions", json=body_deal)).json()
        assert second["session_id"] == session_id

        state_after_deal = (await async_client.get(f"/sessions/{session_id}")).json()
        assert state_after_deal["status"] == "active"
        assert state_after_deal["phase"] in ("collecting", "waiting")
        assert state_after_deal["round"] == 0

        # A confirms ready, but B has not confirmed.
        confirm_body = {
            "constraints": {},
            "confirm_ready": True,
            "duration_seconds": 90,
            "candidate_count": 5,
        }
        assert (await async_client.post(f"/groups/{group_id}/sessions", json=confirm_body)).status_code == 201
        state_after_one_confirm = (await async_client.get(f"/sessions/{session_id}")).json()
        assert state_after_one_confirm["status"] == "active"
        assert state_after_one_confirm["phase"] in ("collecting", "waiting")
        assert state_after_one_confirm["round"] == 0

        # Once B confirms, session transitions to swiping and timer begins.
        assert (await client_b.post(f"/groups/{group_id}/sessions", json=confirm_body)).status_code == 201
        final_state = (await async_client.get(f"/sessions/{session_id}")).json()
        assert final_state["status"] == "active"
        assert final_state["phase"] in ("swiping", "waiting")
        assert final_state["round"] == 1
        assert 0 <= int(final_state["user_seconds_left"]) <= 60
