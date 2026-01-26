from datetime import datetime, timedelta, timezone
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
    assert st["status"] == "active"
    assert st["result_watchlist_item_id"] is None


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
        r = await async_client.post(
            f"/groups/{group_id}/sessions",
            json={"constraints": {"format": "any"}, "duration_seconds": 15, "candidate_count": 5},
        )
        assert r.status_code == 201, r.text
        s = r.json()
        session_id = s["session_id"]

        # A votes YES i1
        await async_client.post(f"/sessions/{session_id}/vote", json={"watchlist_item_id": i1["id"], "vote":"yes"})

        # B votes YES i2
        await client_b.post(f"/sessions/{session_id}/vote", json={"watchlist_item_id": i2["id"], "vote":"yes"})

        # C votes NO i2
        await client_c.post(f"/sessions/{session_id}/vote", json={"watchlist_item_id": i2["id"], "vote":"no"})

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
