import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal
from app.models.tonight_session import TonightSession
from app.models.tonight_session_participant import TonightSessionParticipant
from app.models.tonight_session_vote_snapshot import TonightSessionVoteSnapshot
from social_helpers import add_friend_to_group, create_friendship


async def _create_winner(
    async_client, user_factory, login_helper, *, constraints: dict | None = None
):
    user = await user_factory(async_client, display_name="History Host")
    await login_helper(
        async_client, email=user["email"], password=user["password"]
    )
    group = (await async_client.post("/groups", json={"name": "Archive Club"})).json()
    item_ids: list[str] = []
    for tmdb_id, title in ((7301, "First Choice"), (7302, "Second Choice")):
        response = await async_client.post(
            f"/groups/{group['id']}/watchlist",
            json={
                "type": "tmdb",
                "tmdb_id": tmdb_id,
                "media_type": "movie",
                "title": title,
                "year": 2026,
                "poster_path": f"/{tmdb_id}.jpg",
            },
        )
        assert response.status_code == 201, response.text
        item_ids.append(response.json()["id"])

    created = await async_client.post(
        f"/groups/{group['id']}/sessions",
        json={
            "constraints": constraints or {"moods": ["cozy"], "format": "movie"},
            "duration_seconds": 90,
            "candidate_count": 5,
        },
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["session_id"]
    winner = await async_client.post(f"/sessions/{session_id}/shuffle")
    assert winner.status_code == 200, winner.text
    assert winner.json()["status"] == "winner_selected"
    return user, group, item_ids, session_id


@pytest.mark.anyio
async def test_mood_catalogue_and_completed_session_preserve_structured_context(
    async_client, user_factory, login_helper
):
    catalogue = await async_client.get("/mood-cues")
    assert catalogue.status_code == 200
    cue_ids = {cue["id"] for cue in catalogue.json()}
    assert {"easygoing", "mind-bending", "date-night"}.issubset(cue_ids)

    criteria = {
        "mood_cues": ["date-night", "easygoing"],
        "moods": ["Romance"],
        "max_runtime": 120,
        "format": "movie",
        "custom_mood_text": "Something romantic but not cheesy.",
    }
    _, _, _, session_id = await _create_winner(
        async_client,
        user_factory,
        login_helper,
        constraints=criteria,
    )
    completed = await async_client.post(f"/sessions/{session_id}/completion")
    assert completed.status_code == 200, completed.text
    saved = completed.json()["criteria"]
    assert saved["mood_cues"] == ["date-night", "easygoing"]
    assert saved["custom_mood_text"] == "Something romantic but not cheesy."
    assert saved["max_runtime"] == 120


@pytest.mark.anyio
async def test_invalid_mood_cue_is_rejected(async_client, user_factory, login_helper):
    user = await user_factory(async_client, display_name="Mood Host")
    await login_helper(async_client, email=user["email"], password=user["password"])
    group = (await async_client.post("/groups", json={"name": "Mood Club"})).json()
    response = await async_client.post(
        f"/groups/{group['id']}/sessions",
        json={
            "constraints": {"mood_cues": ["invented-cue"]},
            "duration_seconds": 90,
            "candidate_count": 5,
        },
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_completion_is_idempotent_and_survives_watchlist_deletion(
    async_client, user_factory, login_helper
):
    _, group, item_ids, session_id = await _create_winner(
        async_client, user_factory, login_helper
    )

    first = await async_client.post(f"/sessions/{session_id}/completion")
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["status"] == "completed"
    assert first_body["group_name"] == "Archive Club"
    assert first_body["criteria"]["moods"] == ["cozy"]
    assert len(first_body["participants"]) == 1
    assert len(first_body["candidates"]) == 2
    assert sum(candidate["is_winner"] for candidate in first_body["candidates"]) == 1
    assert all("votes" not in candidate for candidate in first_body["candidates"])
    assert all(
        "source_watchlist_item_id" not in candidate
        for candidate in first_body["candidates"]
    )

    second = await async_client.post(f"/sessions/{session_id}/completion")
    assert second.status_code == 200, second.text
    assert second.json()["completed_at"] == first_body["completed_at"]
    assert second.json()["winner_candidate_id"] == first_body["winner_candidate_id"]

    removed = await async_client.patch(
        f"/watchlist-items/{item_ids[0]}", json={"remove": True}
    )
    assert removed.status_code == 200, removed.text

    persisted = await async_client.get(f"/sessions/{session_id}/completion")
    assert persisted.status_code == 200, persisted.text
    assert len(persisted.json()["candidates"]) == 2
    assert {row["title"] for row in persisted.json()["candidates"]} == {
        "First Choice",
        "Second Choice",
    }

    history = await async_client.get(f"/groups/{group['id']}/movie-nights")
    assert history.status_code == 200, history.text
    assert [row["session_id"] for row in history.json()["items"]] == [session_id]


@pytest.mark.anyio
async def test_group_history_is_newest_first_and_cursor_paginated(
    async_client, user_factory, login_helper
):
    _, group, _, first_session_id = await _create_winner(
        async_client, user_factory, login_helper
    )
    assert (
        await async_client.post(f"/sessions/{first_session_id}/completion")
    ).status_code == 200

    second = await async_client.post(
        f"/groups/{group['id']}/sessions",
        json={
            "constraints": {"mood_cues": ["easygoing"]},
            "duration_seconds": 90,
            "candidate_count": 5,
        },
    )
    assert second.status_code == 201, second.text
    second_session_id = second.json()["session_id"]
    assert (
        await async_client.post(f"/sessions/{second_session_id}/shuffle")
    ).status_code == 200
    assert (
        await async_client.post(f"/sessions/{second_session_id}/completion")
    ).status_code == 200

    first_page = await async_client.get(
        f"/groups/{group['id']}/movie-nights?limit=1"
    )
    assert first_page.status_code == 200
    assert [item["session_id"] for item in first_page.json()["items"]] == [
        second_session_id
    ]
    assert first_page.json()["next_cursor"] == "1"

    second_page = await async_client.get(
        f"/groups/{group['id']}/movie-nights?limit=1&cursor=1"
    )
    assert second_page.status_code == 200
    assert [item["session_id"] for item in second_page.json()["items"]] == [
        first_session_id
    ]
    assert second_page.json()["next_cursor"] is None


@pytest.mark.anyio
async def test_completion_requires_group_membership(
    async_client, client_factory, user_factory, login_helper
):
    _, _, _, session_id = await _create_winner(
        async_client, user_factory, login_helper
    )
    async with client_factory() as outsider:
        user = await user_factory(outsider, display_name="Outsider")
        await login_helper(
            outsider, email=user["email"], password=user["password"]
        )
        denied = await outsider.post(f"/sessions/{session_id}/completion")
        assert denied.status_code == 403
        denied_get = await outsider.get(f"/sessions/{session_id}/completion")
        assert denied_get.status_code == 403


@pytest.mark.anyio
async def test_watched_confirmation_is_authorized_and_idempotent(
    async_client, user_factory, login_helper
):
    _, _, _, session_id = await _create_winner(
        async_client, user_factory, login_helper
    )
    assert (await async_client.post(f"/sessions/{session_id}/completion")).status_code == 200

    watched = await async_client.patch(
        f"/sessions/{session_id}/completion/watched", json={"status": "watched"}
    )
    assert watched.status_code == 200, watched.text
    body = watched.json()
    assert body["watched_status"] == "watched"
    assert body["watched_confirmed_at"] is not None

    repeated = await async_client.patch(
        f"/sessions/{session_id}/completion/watched", json={"status": "watched"}
    )
    assert repeated.status_code == 200
    assert repeated.json()["watched_confirmed_at"] == body["watched_confirmed_at"]


@pytest.mark.anyio
async def test_cancelled_session_cannot_be_completed(
    async_client, user_factory, login_helper
):
    user = await user_factory(async_client, display_name="Host")
    await login_helper(
        async_client, email=user["email"], password=user["password"]
    )
    group = (await async_client.post("/groups", json={"name": "G"})).json()
    for tmdb_id in (7401, 7402):
        await async_client.post(
            f"/groups/{group['id']}/watchlist",
            json={
                "type": "tmdb",
                "tmdb_id": tmdb_id,
                "media_type": "movie",
                "title": f"Title {tmdb_id}",
                "year": 2026,
                "poster_path": None,
            },
        )
    created = (
        await async_client.post(
            f"/groups/{group['id']}/sessions",
            json={"constraints": {}, "duration_seconds": 90, "candidate_count": 5},
        )
    ).json()
    ended = await async_client.post(f"/sessions/{created['session_id']}/end")
    assert ended.status_code == 200
    assert ended.json()["status"] == "cancelled"
    complete = await async_client.post(
        f"/sessions/{created['session_id']}/completion"
    )
    assert complete.status_code == 400


@pytest.mark.anyio
async def test_teleparty_history_keeps_facts_not_url(
    async_client, user_factory, login_helper
):
    _, _, _, session_id = await _create_winner(
        async_client, user_factory, login_helper
    )
    url = "https://www.teleparty.com/join/history-test"
    shared = await async_client.patch(
        f"/sessions/{session_id}/watch-party", json={"url": url}
    )
    assert shared.status_code == 200
    handoff = await async_client.post(
        f"/sessions/{session_id}/watch-party/handoff"
    )
    assert handoff.status_code == 204

    completed = await async_client.post(f"/sessions/{session_id}/completion")
    assert completed.status_code == 200, completed.text
    body = completed.json()
    assert body["teleparty_was_shared"] is True
    assert body["teleparty_shared_at"] is not None
    assert body["teleparty_handoff_at"] is not None
    assert url not in completed.text

    state = await async_client.get(f"/sessions/{session_id}")
    assert state.status_code == 200
    assert state.json()["watch_party_url"] == url


@pytest.mark.anyio
async def test_concurrent_completion_returns_one_canonical_record(
    async_client, client_factory, user_factory, login_helper
):
    user, _, _, session_id = await _create_winner(
        async_client, user_factory, login_helper
    )
    async with client_factory() as second_client:
        await login_helper(
            second_client, email=user["email"], password=user["password"]
        )
        first, second = await asyncio.gather(
            async_client.post(f"/sessions/{session_id}/completion"),
            second_client.post(f"/sessions/{session_id}/completion"),
        )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["winner_candidate_id"] == second.json()["winner_candidate_id"]
    assert first.json()["completed_at"] == second.json()["completed_at"]


@pytest.mark.anyio
async def test_participant_and_group_snapshots_survive_later_changes(
    async_client, client_factory, user_factory, login_helper
):
    user_a = await user_factory(async_client, display_name="Original Host")
    await login_helper(
        async_client, email=user_a["email"], password=user_a["password"]
    )
    async with client_factory() as client_b:
        user_b = await user_factory(client_b, display_name="Original Guest")
        await login_helper(
            client_b, email=user_b["email"], password=user_b["password"]
        )
        await create_friendship(
            async_client, client_b, recipient_email=user_b["email"]
        )
        friend_id = next(
            row["id"]
            for row in (await async_client.get("/friends")).json()
            if row["username"] == user_b["username"]
        )
        group = (await async_client.post("/groups", json={"name": "Old Name"})).json()
        await add_friend_to_group(
            async_client,
            client_b,
            group_id=group["id"],
            target_user_id=friend_id,
        )
        for tmdb_id in (7501, 7502):
            await async_client.post(
                f"/groups/{group['id']}/watchlist",
                json={
                    "type": "tmdb",
                    "tmdb_id": tmdb_id,
                    "media_type": "movie",
                    "title": f"Title {tmdb_id}",
                    "year": 2026,
                    "poster_path": None,
                },
            )
        host_body = {
            "constraints": {
                "mood_cues": ["date-night"],
                "custom_mood_text": "Something romantic but not cheesy.",
            },
            "confirm_ready": False,
            "duration_seconds": 90,
            "candidate_count": 5,
        }
        guest_body = {
            **host_body,
            "constraints": {
                "mood_cues": ["make-us-laugh"],
                "custom_mood_text": "Something light after a long week.",
            },
        }
        created = await async_client.post(
            f"/groups/{group['id']}/sessions", json=host_body
        )
        session_id = created.json()["session_id"]
        assert (
            await client_b.post(
                f"/groups/{group['id']}/sessions", json=guest_body
            )
        ).status_code == 201
        confirm = {**host_body, "constraints": {}, "confirm_ready": True}
        assert (
            await async_client.post(f"/groups/{group['id']}/sessions", json=confirm)
        ).status_code == 201
        assert (
            await client_b.post(f"/groups/{group['id']}/sessions", json=confirm)
        ).status_code == 201
        async with AsyncSessionLocal() as db:
            session = (
                await db.execute(
                    select(TonightSession).where(TonightSession.id == session_id)
                )
            ).scalar_one()
            session.ends_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await db.commit()
        resolved = await async_client.get(f"/sessions/{session_id}")
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["status"] == "winner_selected"
        completed = await async_client.post(f"/sessions/{session_id}/completion")
        assert completed.status_code == 200, completed.text
        assert {row["display_name"] for row in completed.json()["participants"]} == {
            "Original Host",
            "Original Guest",
        }
        assert completed.json()["criteria"]["mood_cues"] == ["date-night"]
        assert (
            completed.json()["criteria"]["custom_mood_text"]
            == "Something romantic but not cheesy."
        )
        participant_criteria = {
            row["display_name"]: row["criteria"]
            for row in completed.json()["participants"]
        }
        assert participant_criteria["Original Guest"]["mood_cues"] == [
            "make-us-laugh"
        ]

        denied = await client_b.patch(
            f"/sessions/{session_id}/completion/watched",
            json={"status": "watched"},
        )
        assert denied.status_code == 403

        assert (await client_b.patch("/me", json={"display_name": "New Guest"})).status_code == 200
        assert (
            await async_client.patch(
                f"/groups/{group['id']}", json={"name": "New Name"}
            )
        ).status_code == 200
        assert (await client_b.post(f"/groups/{group['id']}/leave")).status_code == 200

        persisted = await async_client.get(f"/sessions/{session_id}/completion")
        assert persisted.status_code == 200
        assert persisted.json()["group_name"] == "Old Name"
        assert "Original Guest" in {
            row["display_name"] for row in persisted.json()["participants"]
        }


@pytest.mark.anyio
async def test_legacy_complete_session_is_snapshotted_on_first_completion(
    async_client, user_factory, login_helper
):
    _, _, _, session_id = await _create_winner(
        async_client, user_factory, login_helper
    )
    async with AsyncSessionLocal() as db:
        session = (
            await db.execute(
                select(TonightSession).where(TonightSession.id == session_id)
            )
        ).scalar_one()
        await db.execute(
            delete(TonightSessionVoteSnapshot).where(
                TonightSessionVoteSnapshot.session_id == session.id
            )
        )
        await db.execute(
            delete(TonightSessionParticipant).where(
                TonightSessionParticipant.session_id == session.id
            )
        )
        session.status = "complete"
        session.completed_at = datetime.now(timezone.utc)
        session.winner_candidate_id = None
        session.winner_selected_at = None
        session.group_name_snapshot = None
        session.criteria_snapshot = None
        await db.commit()

    completed = await async_client.post(f"/sessions/{session_id}/completion")
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert completed.json()["winner_candidate_id"]
    assert completed.json()["participants"]


@pytest.mark.anyio
async def test_completion_emits_semantic_realtime_events(
    async_client, user_factory, login_helper, monkeypatch
):
    from app.api.routes import sessions as session_routes

    _, _, _, session_id = await _create_winner(
        async_client, user_factory, login_helper
    )
    session_events: list[str] = []
    group_events: list[str] = []

    async def fake_session_event(_session_id, *, reason: str):
        session_events.append(reason)

    async def fake_group_event(_member_ids, *, reason: str, group_id):
        del group_id
        group_events.append(reason)

    monkeypatch.setattr(
        session_routes.session_realtime_hub,
        "broadcast_session_updated",
        fake_session_event,
    )
    monkeypatch.setattr(session_routes, "publish_group_update", fake_group_event)

    response = await async_client.post(f"/sessions/{session_id}/completion")
    assert response.status_code == 200, response.text
    assert session_events == ["session_completed"]
    assert group_events == ["session_completed"]
