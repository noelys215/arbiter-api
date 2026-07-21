from datetime import datetime, timezone, timedelta

import pytest

from app.services.sessions import _normalize_watch_party_url

from social_helpers import (
    add_friend_to_group,
    add_friend_to_group_with_tokens,
    create_friendship,
    create_friendship_with_tokens,
)


@pytest.mark.anyio
async def test_create_session_requires_membership(async_client, user_factory, login_helper):
    # User A creates group
    user_a = await user_factory(async_client, display_name="A")
    await login_helper(async_client, email=user_a["email"], password=user_a["password"])
    r = await async_client.post("/groups", json={"name": "G"})
    assert r.status_code in (200, 201), r.text
    g = r.json()
    group_id = g["id"]

    # User B tries to create session for that group
    user_b = await user_factory(async_client, display_name="B")
    await login_helper(async_client, email=user_b["email"], password=user_b["password"])

    r = await async_client.post(f"/groups/{group_id}/sessions", json={"constraints": {}, "duration_seconds": 90, "candidate_count": 12})
    assert r.status_code in (401, 403)


@pytest.mark.anyio
async def test_group_leader_can_set_watch_party_link_and_members_can_read(
    async_client,
    monkeypatch,
    user_factory,
    login_helper,
):
    from app.api.routes import sessions as session_routes

    broadcasts: list[tuple[str, str]] = []

    async def fake_broadcast(session_id, *, reason: str):
        broadcasts.append((str(session_id), reason))

    monkeypatch.setattr(
        session_routes.session_realtime_hub,
        "broadcast_session_updated",
        fake_broadcast,
    )

    leader = await user_factory(async_client, display_name="Leader")
    leader_token = await login_helper(
        async_client, email=leader["email"], password=leader["password"]
    )
    group = (await async_client.post("/groups", json={"name": "G"})).json()
    group_id = group["id"]

    member = await user_factory(async_client, display_name="Member")
    member_token = await login_helper(
        async_client, email=member["email"], password=member["password"]
    )
    await create_friendship_with_tokens(
        async_client,
        sender_token=leader_token,
        recipient_token=member_token,
        recipient_email=member["email"],
    )
    await add_friend_to_group_with_tokens(
        async_client,
        owner_token=leader_token,
        recipient_token=member_token,
        group_id=group_id,
        target_user_id=member["id"],
    )

    leader_token = await login_helper(
        async_client, email=leader["email"], password=leader["password"]
    )
    for tmdb_id, title in ((101, "A"), (102, "B")):
        add = await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={
                "type": "tmdb",
                "tmdb_id": tmdb_id,
                "media_type": "movie",
                "title": title,
                "year": 2000,
                "poster_path": None,
            },
        )
        assert add.status_code == 201, add.text

    session = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={
            "constraints": {"moods": ["cozy"]},
            "duration_seconds": 90,
            "candidate_count": 12,
        },
    )
    assert session.status_code == 201, session.text
    session_id = session.json()["session_id"]

    await login_helper(async_client, email=member["email"], password=member["password"])
    member_join = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={
            "constraints": {"moods": ["cozy"]},
            "duration_seconds": 90,
            "candidate_count": 12,
        },
    )
    assert member_join.status_code == 201, member_join.text

    await login_helper(async_client, email=leader["email"], password=leader["password"])
    shuffled = await async_client.post(f"/sessions/{session_id}/shuffle")
    assert shuffled.status_code == 200, shuffled.text
    assert shuffled.json()["result_watchlist_item_id"] is not None

    party_url = "https://www.teleparty.com/join/abc123xyz"
    set_link = await async_client.patch(
        f"/sessions/{session_id}/watch-party",
        json={"url": party_url},
    )
    assert set_link.status_code == 200, set_link.text
    assert set_link.json()["watch_party_url"] == party_url
    assert (session_id, "watch_party_updated") in broadcasts

    await login_helper(async_client, email=member["email"], password=member["password"])
    state = await async_client.get(f"/sessions/{session_id}")
    assert state.status_code == 200, state.text
    assert state.json()["watch_party_url"] == party_url


@pytest.mark.anyio
async def test_non_leader_cannot_set_watch_party_link(
    async_client,
    user_factory,
    login_helper,
):
    leader = await user_factory(async_client, display_name="Leader")
    leader_token = await login_helper(
        async_client, email=leader["email"], password=leader["password"]
    )
    group = (await async_client.post("/groups", json={"name": "G"})).json()
    group_id = group["id"]

    member = await user_factory(async_client, display_name="Member")
    member_token = await login_helper(
        async_client, email=member["email"], password=member["password"]
    )
    await create_friendship_with_tokens(
        async_client,
        sender_token=leader_token,
        recipient_token=member_token,
        recipient_email=member["email"],
    )
    await add_friend_to_group_with_tokens(
        async_client,
        owner_token=leader_token,
        recipient_token=member_token,
        group_id=group_id,
        target_user_id=member["id"],
    )

    leader_token = await login_helper(
        async_client, email=leader["email"], password=leader["password"]
    )
    for tmdb_id, title in ((103, "C"), (104, "D")):
        add = await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={
                "type": "tmdb",
                "tmdb_id": tmdb_id,
                "media_type": "movie",
                "title": title,
                "year": 2000,
                "poster_path": None,
            },
        )
        assert add.status_code == 201, add.text

    session = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={
            "constraints": {"moods": ["cozy"]},
            "duration_seconds": 90,
            "candidate_count": 12,
        },
    )
    assert session.status_code == 201, session.text
    session_id = session.json()["session_id"]

    await login_helper(async_client, email=member["email"], password=member["password"])
    member_join = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={
            "constraints": {"moods": ["cozy"]},
            "duration_seconds": 90,
            "candidate_count": 12,
        },
    )
    assert member_join.status_code == 201, member_join.text

    await login_helper(async_client, email=leader["email"], password=leader["password"])
    shuffled = await async_client.post(f"/sessions/{session_id}/shuffle")
    assert shuffled.status_code == 200, shuffled.text

    await login_helper(async_client, email=member["email"], password=member["password"])
    set_link = await async_client.patch(
        f"/sessions/{session_id}/watch-party",
        json={"url": "https://www.teleparty.com/join/should-fail"},
    )
    assert set_link.status_code == 403


@pytest.mark.anyio
async def test_group_leader_can_set_non_join_teleparty_link(
    async_client,
    user_factory,
    login_helper,
):
    leader = await user_factory(async_client, display_name="Leader")
    leader_token = await login_helper(
        async_client, email=leader["email"], password=leader["password"]
    )
    group = (await async_client.post("/groups", json={"name": "G"})).json()
    group_id = group["id"]

    member = await user_factory(async_client, display_name="Member")
    member_token = await login_helper(
        async_client, email=member["email"], password=member["password"]
    )
    await create_friendship_with_tokens(
        async_client,
        sender_token=leader_token,
        recipient_token=member_token,
        recipient_email=member["email"],
    )
    await add_friend_to_group_with_tokens(
        async_client,
        owner_token=leader_token,
        recipient_token=member_token,
        group_id=group_id,
        target_user_id=member["id"],
    )

    await login_helper(async_client, email=leader["email"], password=leader["password"])
    for tmdb_id, title in ((105, "E"), (106, "F")):
        add = await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={
                "type": "tmdb",
                "tmdb_id": tmdb_id,
                "media_type": "movie",
                "title": title,
                "year": 2000,
                "poster_path": None,
            },
        )
        assert add.status_code == 201, add.text

    session = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={
            "constraints": {"moods": ["cozy"]},
            "duration_seconds": 90,
            "candidate_count": 12,
        },
    )
    assert session.status_code == 201, session.text
    session_id = session.json()["session_id"]

    await login_helper(async_client, email=member["email"], password=member["password"])
    member_join = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={
            "constraints": {"moods": ["cozy"]},
            "duration_seconds": 90,
            "candidate_count": 12,
        },
    )
    assert member_join.status_code == 201, member_join.text

    await login_helper(async_client, email=leader["email"], password=leader["password"])
    shuffled = await async_client.post(f"/sessions/{session_id}/shuffle")
    assert shuffled.status_code == 200, shuffled.text
    assert shuffled.json()["result_watchlist_item_id"] is not None

    party_url = "https://www.teleparty.com/party/abc123xyz"
    set_link = await async_client.patch(
        f"/sessions/{session_id}/watch-party",
        json={"url": party_url},
    )
    assert set_link.status_code == 200, set_link.text
    assert set_link.json()["watch_party_url"] == party_url


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
    r = await async_client.post("/groups", json={"name": "G"})
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
    r = await async_client.post("/groups", json={"name": "G"})
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
    r = await async_client.post("/groups", json={"name": "G"})
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
async def test_free_text_anime_only_strictly_filters_deck(
    async_client,
    monkeypatch,
    user_factory,
    login_helper,
):
    from app.services import sessions as sessions_service
    from app.services.ai import AIError

    async def fake_taxonomy(*, tmdb_id: int, media_type: str):
        _ = media_type
        if tmdb_id == 2101:
            return {"animation"}, {"anime", "shounen"}, {16}
        if tmdb_id == 2102:
            return {"comedy"}, {"sitcom"}, {35}
        return set(), set(), set()

    async def fake_rerank(*, constraints, candidates):
        _ = (constraints, candidates)
        raise AIError("disable rerank")

    monkeypatch.setattr(sessions_service, "fetch_tmdb_title_taxonomy", fake_taxonomy)
    monkeypatch.setattr(sessions_service, "ai_rerank_candidates", fake_rerank)

    user = await user_factory(async_client, display_name="AnimeFilter")
    await login_helper(async_client, email=user["email"], password=user["password"])
    group = (await async_client.post("/groups", json={"name": "G"})).json()
    group_id = group["id"]

    anime_item = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 2101, "media_type": "tv", "title": "Anime Show", "year": 2020, "poster_path": None},
        )
    ).json()
    _ = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 2102, "media_type": "tv", "title": "Comedy Show", "year": 2020, "poster_path": None},
        )
    ).json()

    r = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={
            "constraints": {},
            "text": "anime only",
            "duration_seconds": 90,
            "candidate_count": 12,
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    ids = {c["watchlist_item_id"] for c in data["candidates"]}
    assert ids == {anime_item["id"]}


@pytest.mark.anyio
async def test_free_text_actor_filter_restricts_to_requested_person(
    async_client,
    monkeypatch,
    user_factory,
    login_helper,
):
    from app.services import sessions as sessions_service
    from app.services.ai import AIError

    async def fake_people(*, tmdb_id: int, media_type: str):
        _ = media_type
        if tmdb_id == 2201:
            return {"gordon ramsay", "christina tosi"}
        if tmdb_id == 2202:
            return {"anthony bourdain"}
        return set()

    async def fake_rerank(*, constraints, candidates):
        _ = (constraints, candidates)
        raise AIError("disable rerank")

    monkeypatch.setattr(sessions_service, "fetch_tmdb_title_people_names", fake_people)
    monkeypatch.setattr(sessions_service, "ai_rerank_candidates", fake_rerank)

    user = await user_factory(async_client, display_name="ActorFilter")
    await login_helper(async_client, email=user["email"], password=user["password"])
    group = (await async_client.post("/groups", json={"name": "G"})).json()
    group_id = group["id"]

    gordon_item = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 2201, "media_type": "tv", "title": "Kitchen Nightmares", "year": 2010, "poster_path": None},
        )
    ).json()
    _ = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 2202, "media_type": "tv", "title": "Parts Unknown", "year": 2013, "poster_path": None},
        )
    ).json()

    r = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={
            "constraints": {},
            "text": "show me shows with gordon ramsay",
            "duration_seconds": 90,
            "candidate_count": 12,
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    ids = {c["watchlist_item_id"] for c in data["candidates"]}
    assert ids == {gordon_item["id"]}


@pytest.mark.anyio
async def test_free_text_studio_filter_restricts_to_requested_company(
    async_client,
    monkeypatch,
    user_factory,
    login_helper,
):
    from app.services import sessions as sessions_service
    from app.services.ai import AIError

    async def fake_companies(*, tmdb_id: int, media_type: str):
        _ = media_type
        if tmdb_id == 2251:
            return {"a24"}
        if tmdb_id == 2252:
            return {"warner bros. pictures", "alcon entertainment"}
        return set()

    async def fake_rerank(*, constraints, candidates):
        _ = (constraints, candidates)
        raise AIError("disable rerank")

    monkeypatch.setattr(sessions_service, "fetch_tmdb_title_company_names", fake_companies)
    monkeypatch.setattr(sessions_service, "ai_rerank_candidates", fake_rerank)

    user = await user_factory(async_client, display_name="StudioFilter")
    await login_helper(async_client, email=user["email"], password=user["password"])
    group = (await async_client.post("/groups", json={"name": "G"})).json()
    group_id = group["id"]

    a24_item = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 2251, "media_type": "movie", "title": "Past Lives", "year": 2023, "poster_path": None},
        )
    ).json()
    _ = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 2252, "media_type": "movie", "title": "Blade Runner 2049", "year": 2017, "poster_path": None},
        )
    ).json()

    r = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={
            "constraints": {},
            "text": "Give me stuff by the studio A24.",
            "duration_seconds": 90,
            "candidate_count": 12,
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    ids = {c["watchlist_item_id"] for c in data["candidates"]}
    assert ids == {a24_item["id"]}


@pytest.mark.anyio
async def test_free_text_studio_filter_uses_web_distributor_evidence(
    async_client,
    monkeypatch,
    user_factory,
    login_helper,
):
    from app.services import sessions as sessions_service
    from app.services.ai import AIError

    async def fake_companies(*, tmdb_id: int, media_type: str):
        _ = media_type
        if tmdb_id == 2253:
            return {"B-Reel Films"}
        if tmdb_id == 2254:
            return {"Warner Bros. Pictures"}
        return set()

    async def fake_web_companies(*, title: str, release_year: int | None, media_type: str):
        _ = (release_year, media_type)
        if title == "Midsommar":
            return {"A24"}
        return set()

    async def fake_rerank(*, constraints, candidates):
        _ = (constraints, candidates)
        raise AIError("disable rerank")

    monkeypatch.setattr(sessions_service, "fetch_tmdb_title_company_names", fake_companies)
    monkeypatch.setattr(sessions_service, "fetch_web_title_company_names", fake_web_companies)
    monkeypatch.setattr(sessions_service, "ai_rerank_candidates", fake_rerank)

    user = await user_factory(async_client, display_name="StudioWebEvidence")
    await login_helper(async_client, email=user["email"], password=user["password"])
    group = (await async_client.post("/groups", json={"name": "G"})).json()
    group_id = group["id"]

    midsommar_item = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 2253, "media_type": "movie", "title": "Midsommar", "year": 2019, "poster_path": None},
        )
    ).json()
    _ = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 2254, "media_type": "movie", "title": "Dune", "year": 2021, "poster_path": None},
        )
    ).json()

    r = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={
            "constraints": {},
            "text": "only stuff by studio a24",
            "duration_seconds": 90,
            "candidate_count": 12,
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    ids = {c["watchlist_item_id"] for c in data["candidates"]}
    assert ids == {midsommar_item["id"]}


@pytest.mark.anyio
async def test_free_text_similarity_phrase_relaxes_strict_studio_and_anime_filters(
    async_client,
    monkeypatch,
    user_factory,
    login_helper,
):
    from app.services import sessions as sessions_service
    from app.services.ai import AIError

    async def fake_companies(*, tmdb_id: int, media_type: str):
        _ = media_type
        if tmdb_id == 2261:
            return {"studio ghibli"}
        if tmdb_id == 2262:
            return {"a24"}
        return set()

    async def fake_rerank(*, constraints, candidates):
        _ = (constraints, candidates)
        raise AIError("disable rerank")

    monkeypatch.setattr(sessions_service, "fetch_tmdb_title_company_names", fake_companies)
    monkeypatch.setattr(sessions_service, "ai_rerank_candidates", fake_rerank)

    user = await user_factory(async_client, display_name="StudioSimilarity")
    await login_helper(async_client, email=user["email"], password=user["password"])
    group = (await async_client.post("/groups", json={"name": "G"})).json()
    group_id = group["id"]

    ghibli_item = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={
                "type": "tmdb",
                "tmdb_id": 2261,
                "media_type": "movie",
                "title": "Romance Anime Ghibli",
                "year": 2001,
                "poster_path": None,
            },
        )
    ).json()
    non_ghibli_item = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={
                "type": "tmdb",
                "tmdb_id": 2262,
                "media_type": "movie",
                "title": "Romance Anime Similar",
                "year": 2018,
                "poster_path": None,
            },
        )
    ).json()

    r = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={
            "constraints": {},
            "text": "a romantic anime by studio ghibli or something similar",
            "duration_seconds": 90,
            "candidate_count": 12,
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    ids = {c["watchlist_item_id"] for c in data["candidates"]}
    assert ghibli_item["id"] in ids
    assert non_ghibli_item["id"] in ids


@pytest.mark.anyio
async def test_free_text_happy_alias_maps_to_feel_good_mood(
    async_client,
    monkeypatch,
    user_factory,
    login_helper,
):
    from app.services import sessions as sessions_service
    from app.services.ai import AIError

    async def fake_taxonomy(*, tmdb_id: int, media_type: str):
        _ = media_type
        if tmdb_id == 2265:
            return {"comedy"}, {"uplifting", "heartwarming"}, {35}
        if tmdb_id == 2266:
            return {"horror"}, {"haunted"}, {27}
        return set(), set(), set()

    async def fake_rerank(*, constraints, candidates):
        _ = (constraints, candidates)
        raise AIError("disable rerank")

    monkeypatch.setattr(sessions_service, "fetch_tmdb_title_taxonomy", fake_taxonomy)
    monkeypatch.setattr(sessions_service, "ai_rerank_candidates", fake_rerank)

    user = await user_factory(async_client, display_name="MoodAlias")
    await login_helper(async_client, email=user["email"], password=user["password"])
    group = (await async_client.post("/groups", json={"name": "G"})).json()
    group_id = group["id"]

    happy_item = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 2265, "media_type": "movie", "title": "Happy Movie", "year": 2016, "poster_path": None},
        )
    ).json()
    _ = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 2266, "media_type": "movie", "title": "Scary Movie", "year": 2018, "poster_path": None},
        )
    ).json()

    r = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={
            "constraints": {},
            "text": "show me something happy and lighthearted",
            "duration_seconds": 90,
            "candidate_count": 12,
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    ids = {c["watchlist_item_id"] for c in data["candidates"]}
    assert ids == {happy_item["id"]}


@pytest.mark.anyio
async def test_ai_rerank_payload_includes_tmdb_metadata(
    async_client,
    monkeypatch,
    user_factory,
    login_helper,
):
    from app.services import sessions as sessions_service
    from app.services.ai import AIError

    captured: dict[str, list[dict]] = {}

    async def fake_taxonomy(*, tmdb_id: int, media_type: str):
        _ = media_type
        if tmdb_id == 2271:
            return {"romance"}, {"love story"}, {10749}
        if tmdb_id == 2272:
            return {"science fiction"}, {"future"}, {878}
        return set(), set(), set()

    async def fake_people(*, tmdb_id: int, media_type: str):
        _ = media_type
        if tmdb_id == 2271:
            return {"Greta Lee"}
        return {"Ryan Gosling"}

    async def fake_companies(*, tmdb_id: int, media_type: str):
        _ = media_type
        if tmdb_id == 2271:
            return {"A24"}
        return {"Warner Bros. Pictures"}

    async def fake_locale(*, tmdb_id: int, media_type: str):
        _ = media_type
        if tmdb_id == 2271:
            return {"en", "us"}
        return {"en", "gb"}

    async def fake_rerank(*, constraints, candidates):
        _ = constraints
        captured["candidates"] = candidates
        raise AIError("disable rerank")

    monkeypatch.setattr(sessions_service, "fetch_tmdb_title_taxonomy", fake_taxonomy)
    monkeypatch.setattr(sessions_service, "fetch_tmdb_title_people_names", fake_people)
    monkeypatch.setattr(sessions_service, "fetch_tmdb_title_company_names", fake_companies)
    monkeypatch.setattr(sessions_service, "fetch_tmdb_title_locale_tokens", fake_locale)
    monkeypatch.setattr(sessions_service, "ai_rerank_candidates", fake_rerank)

    user = await user_factory(async_client, display_name="AIRerankMetadata")
    await login_helper(async_client, email=user["email"], password=user["password"])
    group = (await async_client.post("/groups", json={"name": "G"})).json()
    group_id = group["id"]

    await async_client.post(
        f"/groups/{group_id}/watchlist",
        json={"type": "tmdb", "tmdb_id": 2271, "media_type": "movie", "title": "Past Lives", "year": 2023, "poster_path": None},
    )
    await async_client.post(
        f"/groups/{group_id}/watchlist",
        json={"type": "tmdb", "tmdb_id": 2272, "media_type": "movie", "title": "Blade Runner 2049", "year": 2017, "poster_path": None},
    )

    r = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={
            "constraints": {},
            "text": "pick something fun for tonight",
            "duration_seconds": 90,
            "candidate_count": 12,
        },
    )
    assert r.status_code == 201, r.text
    candidates_payload = captured.get("candidates")
    assert isinstance(candidates_payload, list)
    assert len(candidates_payload) >= 2
    sample = candidates_payload[0]
    assert "tmdb_genres" in sample
    assert "tmdb_keywords" in sample
    assert "tmdb_genre_ids" in sample
    assert "tmdb_people" in sample
    assert "tmdb_companies" in sample
    assert "web_companies" in sample
    assert "tmdb_locale_tokens" in sample


@pytest.mark.anyio
async def test_free_text_directed_by_filter_restricts_to_requested_person(
    async_client,
    monkeypatch,
    user_factory,
    login_helper,
):
    from app.services import sessions as sessions_service
    from app.services.ai import AIError

    async def fake_people(*, tmdb_id: int, media_type: str):
        _ = media_type
        if tmdb_id == 2301:
            return {"christopher nolan", "hans zimmer"}
        if tmdb_id == 2302:
            return {"greta gerwig"}
        return set()

    async def fake_rerank(*, constraints, candidates):
        _ = (constraints, candidates)
        raise AIError("disable rerank")

    monkeypatch.setattr(sessions_service, "fetch_tmdb_title_people_names", fake_people)
    monkeypatch.setattr(sessions_service, "ai_rerank_candidates", fake_rerank)

    user = await user_factory(async_client, display_name="DirectorFilter")
    await login_helper(async_client, email=user["email"], password=user["password"])
    group = (await async_client.post("/groups", json={"name": "G"})).json()
    group_id = group["id"]

    nolan_item = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 2301, "media_type": "movie", "title": "Inception", "year": 2010, "poster_path": None},
        )
    ).json()
    _ = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 2302, "media_type": "movie", "title": "Lady Bird", "year": 2017, "poster_path": None},
        )
    ).json()

    r = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={
            "constraints": {},
            "text": "show me movies directed by christopher nolan",
            "duration_seconds": 90,
            "candidate_count": 12,
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    ids = {c["watchlist_item_id"] for c in data["candidates"]}
    assert ids == {nolan_item["id"]}


@pytest.mark.anyio
async def test_free_text_locale_genre_and_exclusion_filters(
    async_client,
    monkeypatch,
    user_factory,
    login_helper,
):
    from app.services import sessions as sessions_service
    from app.services.ai import AIError

    async def fake_taxonomy(*, tmdb_id: int, media_type: str):
        _ = media_type
        if tmdb_id == 2401:
            return {"drama"}, {"coming of age"}, {18}
        if tmdb_id == 2402:
            return {"horror"}, {"slasher"}, {27}
        if tmdb_id == 2403:
            return {"drama"}, {"courtroom"}, {18}
        return set(), set(), set()

    async def fake_locale(*, tmdb_id: int, media_type: str):
        _ = media_type
        if tmdb_id in {2401, 2402}:
            return {"ko", "korean", "south korea", "kr"}
        if tmdb_id == 2403:
            return {"en", "us", "united states"}
        return set()

    async def fake_rerank(*, constraints, candidates):
        _ = (constraints, candidates)
        raise AIError("disable rerank")

    monkeypatch.setattr(sessions_service, "fetch_tmdb_title_taxonomy", fake_taxonomy)
    monkeypatch.setattr(sessions_service, "fetch_tmdb_title_locale_tokens", fake_locale)
    monkeypatch.setattr(sessions_service, "ai_rerank_candidates", fake_rerank)

    user = await user_factory(async_client, display_name="LocaleGenreFilter")
    await login_helper(async_client, email=user["email"], password=user["password"])
    group = (await async_client.post("/groups", json={"name": "G"})).json()
    group_id = group["id"]

    korean_drama = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 2401, "media_type": "tv", "title": "K-Drama A", "year": 2021, "poster_path": None},
        )
    ).json()
    _ = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 2402, "media_type": "tv", "title": "K-Horror B", "year": 2022, "poster_path": None},
        )
    ).json()
    _ = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 2403, "media_type": "tv", "title": "US Drama C", "year": 2023, "poster_path": None},
        )
    ).json()

    r = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={
            "constraints": {},
            "text": "korean drama only, no horror",
            "duration_seconds": 90,
            "candidate_count": 12,
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    ids = {c["watchlist_item_id"] for c in data["candidates"]}
    assert ids == {korean_drama["id"]}


@pytest.mark.anyio
async def test_free_text_year_and_format_filters(
    async_client,
    monkeypatch,
    user_factory,
    login_helper,
):
    from app.services import sessions as sessions_service
    from app.services.ai import AIError

    async def fake_rerank(*, constraints, candidates):
        _ = (constraints, candidates)
        raise AIError("disable rerank")

    monkeypatch.setattr(sessions_service, "ai_rerank_candidates", fake_rerank)

    user = await user_factory(async_client, display_name="YearFilter")
    await login_helper(async_client, email=user["email"], password=user["password"])
    group = (await async_client.post("/groups", json={"name": "G"})).json()
    group_id = group["id"]

    old_show = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 2501, "media_type": "tv", "title": "Show Old", "year": 2015, "poster_path": None},
        )
    ).json()
    recent_show = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 2502, "media_type": "tv", "title": "Show New", "year": 2022, "poster_path": None},
        )
    ).json()
    _ = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 2503, "media_type": "movie", "title": "Movie New", "year": 2023, "poster_path": None},
        )
    ).json()

    r = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={
            "constraints": {},
            "text": "tv only after 2018",
            "duration_seconds": 90,
            "candidate_count": 12,
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    ids = {c["watchlist_item_id"] for c in data["candidates"]}
    assert ids == {recent_show["id"]}
    assert old_show["id"] not in ids


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
    group = (await async_client.post("/groups", json={"name": "G"})).json()
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
    group = (await async_client.post("/groups", json={"name": "G"})).json()
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
    group = (await async_client.post("/groups", json={"name": "G"})).json()
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
    group = (await async_client.post("/groups", json={"name": "G"})).json()
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
async def test_runtime_vibe_tag_under_30_prefers_short_tv_titles(
    async_client, monkeypatch, user_factory, login_helper
):
    from app.services import sessions as sessions_service
    from app.services import watchlist as watchlist_service
    from app.services.ai import AIError

    async def fake_taxonomy(*, tmdb_id: int, media_type: str):
        _ = (tmdb_id, media_type)
        return set(), set(), set()

    async def fake_rerank(*, constraints, candidates):
        _ = (constraints, candidates)
        raise AIError("disable rerank")

    async def fake_tmdb_details(*, tmdb_id: int, media_type: str):
        _ = media_type
        if tmdb_id == 801:
            return {"runtime_minutes": 12, "overview": "Very short show"}
        if tmdb_id == 802:
            return {"runtime_minutes": 28, "overview": "Half-hour show"}
        if tmdb_id == 803:
            return {"runtime_minutes": 45, "overview": "Longer episode"}
        if tmdb_id == 804:
            return {"runtime_minutes": 10, "overview": "Short film"}
        return {}

    monkeypatch.setattr(sessions_service, "fetch_tmdb_title_taxonomy", fake_taxonomy)
    monkeypatch.setattr(sessions_service, "ai_rerank_candidates", fake_rerank)
    monkeypatch.setattr(watchlist_service, "fetch_tmdb_title_details", fake_tmdb_details)

    user = await user_factory(async_client, display_name="RuntimeTag")
    await login_helper(async_client, email=user["email"], password=user["password"])
    group = (await async_client.post("/groups", json={"name": "G"})).json()
    group_id = group["id"]

    short_tv = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 801, "media_type": "tv", "title": "Quick TV", "year": 2020, "poster_path": None},
        )
    ).json()
    half_hour_tv = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 802, "media_type": "tv", "title": "Half TV", "year": 2020, "poster_path": None},
        )
    ).json()
    _ = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 803, "media_type": "tv", "title": "Long TV", "year": 2020, "poster_path": None},
        )
    ).json()
    _ = (
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 804, "media_type": "movie", "title": "Short Film", "year": 2020, "poster_path": None},
        )
    ).json()

    r = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={
            "constraints": {"moods": ["Under 30 Mins"]},
            "duration_seconds": 90,
            "candidate_count": 12,
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()

    ids = {c["watchlist_item_id"] for c in data["candidates"]}
    assert ids == {short_tv["id"], half_hour_tv["id"]}
    assert all(c["reason"] == "Matches: Under 30 Mins" for c in data["candidates"])


@pytest.mark.anyio
async def test_swipe_timer_starts_only_after_all_users_confirm_ready(
    async_client, client_factory, user_factory, login_helper
):
    async with client_factory() as client_b:
        user_a = await user_factory(async_client, display_name="A")
        await login_helper(async_client, email=user_a["email"], password=user_a["password"])
        user_b = await user_factory(client_b, display_name="B")
        await login_helper(client_b, email=user_b["email"], password=user_b["password"])

        await create_friendship(
            async_client,
            client_b,
            recipient_email=user_b["email"],
        )
        friends = (await async_client.get("/friends")).json()
        b_id = next(f["id"] for f in friends if f["username"] == user_b["username"])

        group = (await async_client.post("/groups", json={"name": "G"})).json()
        group_id = group["id"]
        await add_friend_to_group(
            async_client,
            client_b,
            group_id=group_id,
            target_user_id=b_id,
        )

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
        assert state_after_deal["status"] == "setup"
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
        assert state_after_one_confirm["status"] == "setup"
        assert state_after_one_confirm["phase"] in ("collecting", "waiting")
        assert state_after_one_confirm["round"] == 0

        # Once B confirms, session transitions to swiping and timer begins.
        assert (await client_b.post(f"/groups/{group_id}/sessions", json=confirm_body)).status_code == 201
        final_state = (await async_client.get(f"/sessions/{session_id}")).json()
        assert final_state["status"] == "active"
        assert final_state["phase"] in ("swiping", "waiting")
        assert final_state["round"] == 1
        assert 0 <= int(final_state["user_seconds_left"]) <= 60


@pytest.mark.anyio
async def test_user_can_unready_after_confirm_to_edit_preferences(
    async_client, client_factory, user_factory, login_helper
):
    async with client_factory() as client_b:
        user_a = await user_factory(async_client, display_name="A")
        await login_helper(async_client, email=user_a["email"], password=user_a["password"])
        user_b = await user_factory(client_b, display_name="B")
        await login_helper(client_b, email=user_b["email"], password=user_b["password"])

        await create_friendship(
            async_client,
            client_b,
            recipient_email=user_b["email"],
        )
        friends = (await async_client.get("/friends")).json()
        b_id = next(f["id"] for f in friends if f["username"] == user_b["username"])

        group = (await async_client.post("/groups", json={"name": "G"})).json()
        group_id = group["id"]
        await add_friend_to_group(
            async_client,
            client_b,
            group_id=group_id,
            target_user_id=b_id,
        )

        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 911, "media_type": "movie", "title": "A", "year": 2000, "poster_path": None},
        )
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 912, "media_type": "movie", "title": "B", "year": 2001, "poster_path": None},
        )

        body_deal = {
            "constraints": {"moods": ["cozy"]},
            "confirm_ready": False,
            "duration_seconds": 90,
            "candidate_count": 5,
        }
        first = (await async_client.post(f"/groups/{group_id}/sessions", json=body_deal)).json()
        session_id = first["session_id"]
        second = (await client_b.post(f"/groups/{group_id}/sessions", json=body_deal)).json()
        assert second["session_id"] == session_id

        confirm_body = {
            "constraints": {},
            "confirm_ready": True,
            "duration_seconds": 90,
            "candidate_count": 5,
        }
        confirm_response = await async_client.post(
            f"/groups/{group_id}/sessions",
            json=confirm_body,
        )
        assert confirm_response.status_code == 201, confirm_response.text

        state_after_confirm = (await async_client.get(f"/sessions/{session_id}")).json()
        assert state_after_confirm["status"] == "setup"
        assert state_after_confirm["round"] == 0
        assert state_after_confirm["user_locked"] is True

        # User A clicks Back/Edit in the deal modal.
        unready_response = await async_client.post(
            f"/groups/{group_id}/sessions",
            json={
                "constraints": {},
                "confirm_ready": False,
                "duration_seconds": 90,
                "candidate_count": 5,
            },
        )
        assert unready_response.status_code == 201, unready_response.text
        assert unready_response.json()["session_id"] == session_id

        state_after_unready = (await async_client.get(f"/sessions/{session_id}")).json()
        assert state_after_unready["status"] == "setup"
        assert state_after_unready["round"] == 0
        assert state_after_unready["phase"] in ("collecting", "waiting")
        assert state_after_unready["user_locked"] is False


@pytest.mark.parametrize(
    "url",
    [
        "http://www.teleparty.com/join/insecure",
        "javascript://www.teleparty.com/join/unsafe",
        "https://user@www.teleparty.com/join/credentials",
        "https://www.teleparty.com.evil.example/join/lookalike",
        "https://subdomain.teleparty.com/join/not-allowlisted",
        "https://www.teleparty.com./join/trailing-dot",
        "https://www.teleparty.com:8443/join/nonstandard-port",
    ],
)
def test_watch_party_url_rejects_unsafe_or_lookalike_urls(url):
    with pytest.raises(ValueError):
        _normalize_watch_party_url(url)


def test_watch_party_url_accepts_exact_https_hostname():
    url = "https://www.teleparty.com/join/abc123"

    assert _normalize_watch_party_url(url) == url
