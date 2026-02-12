from datetime import datetime, timedelta, timezone
import json
import pytest
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.tonight_session import TonightSession


@pytest.mark.anyio
async def test_vote_upserts_and_is_blind(async_client, user_factory, login_helper):
    # user
    user = await user_factory(async_client, display_name="V1")
    await login_helper(async_client, email=user["email"], password=user["password"])
    r = await async_client.post("/groups", json={"name": "G", "member_user_ids": []})
    assert r.status_code in (200, 201), r.text
    g = r.json()
    group_id = g["id"]

    # add 2 items
    r = await async_client.post(
        f"/groups/{group_id}/watchlist",
        json={"type": "tmdb", "tmdb_id": 1, "media_type": "movie", "title": "A", "year": 2000, "poster_path": None},
    )
    assert r.status_code == 201, r.text
    i1 = r.json()
    r = await async_client.post(
        f"/groups/{group_id}/watchlist",
        json={"type": "tmdb", "tmdb_id": 2, "media_type": "movie", "title": "B", "year": 2001, "poster_path": None},
    )
    assert r.status_code == 201, r.text
    i2 = r.json()

    # create session
    r = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={"constraints": {"format": "any"}, "duration_seconds": 90, "candidate_count": 5},
    )
    assert r.status_code == 201, r.text
    s = r.json()
    session_id = s["session_id"]

    # vote yes on i1
    r = await async_client.post(f"/sessions/{session_id}/vote", json={"watchlist_item_id": i1["id"], "vote":"yes"})
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # change vote to yes on i2 (upsert)
    r = await async_client.post(f"/sessions/{session_id}/vote", json={"watchlist_item_id": i2["id"], "vote":"yes"})
    assert r.status_code == 200

    # poll state: should not include tallies
    st = (await async_client.get(f"/sessions/{session_id}")).json()
    assert "tallies" not in st
    assert st["status"] in ("active", "complete")


@pytest.mark.anyio
async def test_resolve_on_expiry_picks_max_yes_then_min_no(
    async_client, client_factory, user_factory, login_helper
):
    async with client_factory() as client_b, client_factory() as client_c:
        # A, B, C in same group
        user_a = await user_factory(async_client, display_name="A")
        await login_helper(async_client, email=user_a["email"], password=user_a["password"])
        user_b = await user_factory(client_b, display_name="B")
        await login_helper(client_b, email=user_b["email"], password=user_b["password"])
        user_c = await user_factory(client_c, display_name="C")
        await login_helper(client_c, email=user_c["email"], password=user_c["password"])

        # back to A
        invite_b = (await async_client.post("/friends/invite")).json()["code"]
        await client_b.post("/friends/accept", json={"code": invite_b})

        invite_c = (await async_client.post("/friends/invite")).json()["code"]
        await client_c.post("/friends/accept", json={"code": invite_c})

        friends = (await async_client.get("/friends")).json()
        b_id = next(f["id"] for f in friends if f["email"] == user_b["email"])
        c_id = next(f["id"] for f in friends if f["email"] == user_c["email"])

        r = await async_client.post("/groups", json={"name": "G", "member_user_ids": [b_id, c_id]})
        assert r.status_code in (200, 201), r.text
        g = r.json()
        group_id = g["id"]

        r = await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 10, "media_type": "movie", "title": "A", "year": 2000, "poster_path": None},
        )
        assert r.status_code == 201, r.text
        i1 = r.json()
        r = await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 11, "media_type": "movie", "title": "B", "year": 2001, "poster_path": None},
        )
        assert r.status_code == 201, r.text
        i2 = r.json()

        # create session with short duration (we will wait it out by forcing a tiny duration)
        body = {"constraints": {"format": "any"}, "duration_seconds": 15, "candidate_count": 5}
        r = await async_client.post(f"/groups/{group_id}/sessions", json=body)
        assert r.status_code == 201, r.text
        session_id = r.json()["session_id"]
        # each user deals into the shared session
        assert (await client_b.post(f"/groups/{group_id}/sessions", json=body)).status_code == 201
        assert (await client_c.post(f"/groups/{group_id}/sessions", json=body)).status_code == 201

        # A votes YES i1
        assert (await async_client.post(
            f"/sessions/{session_id}/vote",
            json={"watchlist_item_id": i1["id"], "vote":"yes"},
        )).status_code == 200

        # B votes YES i2
        assert (await client_b.post(
            f"/sessions/{session_id}/vote",
            json={"watchlist_item_id": i2["id"], "vote":"yes"},
        )).status_code == 200

        # C votes NO i2
        assert (await client_c.post(
            f"/sessions/{session_id}/vote",
            json={"watchlist_item_id": i2["id"], "vote":"no"},
        )).status_code == 200

        # Switch back to A for final state check
        # force expiry in DB
        async with AsyncSessionLocal() as session:
            db_s = (await session.execute(select(TonightSession).where(TonightSession.id == session_id))).scalar_one()
            db_s.ends_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()

        # GET should auto-resolve
        st = (await async_client.get(f"/sessions/{session_id}")).json()
        assert st["status"] == "complete"
        assert st["result_watchlist_item_id"] in (i1["id"], i2["id"])

        # Expect i1: yes=1 no=0 ; i2: yes=1 no=1 -> tie on yes, min no wins => i1
        assert st["result_watchlist_item_id"] == i1["id"]


@pytest.mark.anyio
async def test_shuffle_completes_session(async_client, user_factory, login_helper):
    user = await user_factory(async_client, display_name="S")
    await login_helper(async_client, email=user["email"], password=user["password"])
    r = await async_client.post("/groups", json={"name": "G", "member_user_ids": []})
    assert r.status_code in (200, 201), r.text
    g = r.json()
    group_id = g["id"]

    r = await async_client.post(
        f"/groups/{group_id}/watchlist",
        json={"type": "tmdb", "tmdb_id": 100, "media_type": "movie", "title": "A", "year": 2000, "poster_path": None},
    )
    assert r.status_code == 201, r.text
    i1 = r.json()
    r = await async_client.post(
        f"/groups/{group_id}/watchlist",
        json={"type": "tmdb", "tmdb_id": 101, "media_type": "movie", "title": "B", "year": 2001, "poster_path": None},
    )
    assert r.status_code == 201, r.text
    i2 = r.json()

    r = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={"constraints": {"format": "any"}, "duration_seconds": 90, "candidate_count": 5},
    )
    assert r.status_code == 201, r.text
    s = r.json()
    session_id = s["session_id"]

    st = (await async_client.post(f"/sessions/{session_id}/shuffle")).json()
    assert st["status"] == "complete"
    assert st["result_watchlist_item_id"] in (i1["id"], i2["id"])
    assert st["completed_at"] is not None


@pytest.mark.anyio
async def test_collecting_waits_then_transitions_to_swiping(
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

        i1 = (
            await async_client.post(
                f"/groups/{group_id}/watchlist",
                json={"type": "tmdb", "tmdb_id": 501, "media_type": "movie", "title": "A", "year": 2000, "poster_path": None},
            )
        ).json()
        i2 = (
            await async_client.post(
                f"/groups/{group_id}/watchlist",
                json={"type": "tmdb", "tmdb_id": 502, "media_type": "movie", "title": "B", "year": 2001, "poster_path": None},
            )
        ).json()

        created = (
            await async_client.post(
                f"/groups/{group_id}/sessions",
                json={"constraints": {"format": "any"}, "duration_seconds": 90, "candidate_count": 5},
            )
        ).json()
        session_id = created["session_id"]

        # A has dealt, B has not: A should see waiting.
        waiting_state = (await async_client.get(f"/sessions/{session_id}")).json()
        assert waiting_state["status"] == "active"
        assert waiting_state["phase"] == "waiting"
        assert waiting_state["round"] == 0

        # B deals as well; session should move into shared swiping.
        assert (
            await client_b.post(
                f"/groups/{group_id}/sessions",
                json={"constraints": {"format": "any"}, "duration_seconds": 90, "candidate_count": 5},
            )
        ).status_code == 201
        swiping_state = (await async_client.get(f"/sessions/{session_id}")).json()
        assert swiping_state["status"] == "active"
        assert swiping_state["phase"] in ("swiping", "waiting")
        assert swiping_state["round"] == 1
        ids = {c["watchlist_item_id"] for c in swiping_state["candidates"]}
        assert i1["id"] in ids
        assert i2["id"] in ids


@pytest.mark.anyio
async def test_timer_lock_prevents_late_vote(async_client, client_factory, user_factory, login_helper):
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

        i1 = (
            await async_client.post(
                f"/groups/{group_id}/watchlist",
                json={"type": "tmdb", "tmdb_id": 601, "media_type": "movie", "title": "A", "year": 2000, "poster_path": None},
            )
        ).json()
        _ = (
            await async_client.post(
                f"/groups/{group_id}/watchlist",
                json={"type": "tmdb", "tmdb_id": 602, "media_type": "movie", "title": "B", "year": 2001, "poster_path": None},
            )
        ).json()

        created = (
            await async_client.post(
                f"/groups/{group_id}/sessions",
                json={"constraints": {"format": "any"}, "duration_seconds": 90, "candidate_count": 5},
            )
        ).json()
        session_id = created["session_id"]
        assert (
            await client_b.post(
                f"/groups/{group_id}/sessions",
                json={"constraints": {"format": "any"}, "duration_seconds": 90, "candidate_count": 5},
            )
        ).status_code == 201
        me = (await async_client.get("/me")).json()
        user_a_id = me["id"]

        # Move A's round-1 timer start into the past so A is locked.
        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(TonightSession).where(TonightSession.id == session_id))).scalar_one()
            constraints = dict(row.constraints or {})
            runtime = json.loads(json.dumps(constraints.get("__session_runtime_v1", {})))
            rounds = runtime.get("rounds", {})
            round1 = rounds.get("1", {})
            started = dict(round1.get("user_started_at", {}))
            started[user_a_id] = (datetime.now(timezone.utc) - timedelta(seconds=65)).isoformat()
            round1["user_started_at"] = started
            rounds["1"] = round1
            runtime["rounds"] = rounds
            constraints["__session_runtime_v1"] = runtime
            row.constraints = constraints
            await db.commit()

        st = (await async_client.get(f"/sessions/{session_id}")).json()
        assert st["status"] == "active"
        assert st["phase"] == "waiting"
        assert st["user_locked"] is True
        assert st["user_seconds_left"] == 0

        vote_after_lock = await async_client.post(
            f"/sessions/{session_id}/vote",
            json={"watchlist_item_id": i1["id"], "vote": "yes"},
        )
        assert vote_after_lock.status_code == 400


@pytest.mark.anyio
async def test_group_leader_can_end_session(async_client, client_factory, user_factory, login_helper):
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
            json={"type": "tmdb", "tmdb_id": 801, "media_type": "movie", "title": "A", "year": 2000, "poster_path": None},
        )
        await async_client.post(
            f"/groups/{group_id}/watchlist",
            json={"type": "tmdb", "tmdb_id": 802, "media_type": "movie", "title": "B", "year": 2001, "poster_path": None},
        )

        created = (
            await async_client.post(
                f"/groups/{group_id}/sessions",
                json={"constraints": {"format": "any"}, "duration_seconds": 90, "candidate_count": 5},
            )
        ).json()
        session_id = created["session_id"]

        denied = await client_b.post(f"/sessions/{session_id}/end")
        assert denied.status_code == 403

        ended = (await async_client.post(f"/sessions/{session_id}/end")).json()
        assert ended["status"] == "complete"
