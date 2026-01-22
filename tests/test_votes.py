from datetime import datetime, timedelta, timezone
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.tonight_session import TonightSession


def _u(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"

@pytest.mark.anyio
async def test_vote_upserts_and_is_blind(async_client):
    # user
    v_email = f"{_u('v')}@x.com"
    v_username = _u("v")
    await async_client.post("/auth/register", json={"email": v_email, "username": v_username, "display_name": "V1", "password": "SuperSecret123"})
    await async_client.post("/auth/login", json={"email": v_email, "password": "SuperSecret123"})
    g = (await async_client.post("/groups", json={"name":"G","member_user_ids":[]})).json()
    group_id = g["id"]

    # add 2 items
    i1 = (await async_client.post(f"/groups/{group_id}/watchlist", json={"type":"tmdb","tmdb_id":1,"media_type":"movie","title":"A","year":2000,"poster_path":None})).json()
    i2 = (await async_client.post(f"/groups/{group_id}/watchlist", json={"type":"tmdb","tmdb_id":2,"media_type":"movie","title":"B","year":2001,"poster_path":None})).json()

    # create session
    s = (await async_client.post(f"/groups/{group_id}/sessions", json={"constraints":{"format":"any"}, "duration_seconds":90, "candidate_count":5})).json()
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
async def test_resolve_on_expiry_picks_max_yes_then_min_no(async_client):
    transport_b = ASGITransport(app=async_client._transport.app)
    transport_c = ASGITransport(app=async_client._transport.app)
    async with AsyncClient(transport=transport_b, base_url="http://test") as client_b, AsyncClient(transport=transport_c, base_url="http://test") as client_c:
        # A, B, C in same group
        a_email = f"{_u('a')}@x.com"
        a_username = _u("a")
        b_email = f"{_u('b')}@x.com"
        b_username = _u("b")
        c_email = f"{_u('c')}@x.com"
        c_username = _u("c")
        await async_client.post("/auth/register", json={"email": a_email, "username": a_username, "display_name": "A", "password": "SuperSecret123"})
        await async_client.post("/auth/login", json={"email": a_email, "password": "SuperSecret123"})
        await client_b.post("/auth/register", json={"email": b_email, "username": b_username, "display_name": "B", "password": "SuperSecret123"})
        await client_b.post("/auth/login", json={"email": b_email, "password": "SuperSecret123"})
        await client_c.post("/auth/register", json={"email": c_email, "username": c_username, "display_name": "C", "password": "SuperSecret123"})
        await client_c.post("/auth/login", json={"email": c_email, "password": "SuperSecret123"})

        # back to A
        await async_client.post("/auth/login", json={"email": a_email, "password": "SuperSecret123"})
        invite_b = (await async_client.post("/friends/invite")).json()["code"]
        await client_b.post("/friends/accept", json={"code": invite_b})

        invite_c = (await async_client.post("/friends/invite")).json()["code"]
        await client_c.post("/friends/accept", json={"code": invite_c})

        await async_client.post("/auth/login", json={"email": a_email, "password": "SuperSecret123"})
        friends = (await async_client.get("/friends")).json()
        b_id = next(f["id"] for f in friends if f["email"] == b_email)
        c_id = next(f["id"] for f in friends if f["email"] == c_email)

        g = (await async_client.post("/groups", json={"name":"G","member_user_ids":[b_id, c_id]})).json()
        group_id = g["id"]

        i1 = (await async_client.post(f"/groups/{group_id}/watchlist", json={"type":"tmdb","tmdb_id":10,"media_type":"movie","title":"A","year":2000,"poster_path":None})).json()
        i2 = (await async_client.post(f"/groups/{group_id}/watchlist", json={"type":"tmdb","tmdb_id":11,"media_type":"movie","title":"B","year":2001,"poster_path":None})).json()

        # create session with short duration (we will wait it out by forcing a tiny duration)
        r = await async_client.post(f"/groups/{group_id}/sessions", json={"constraints":{"format":"any"}, "duration_seconds":15, "candidate_count":5})
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
        await async_client.post("/auth/login", json={"email": a_email, "password": "SuperSecret123"})

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
async def test_shuffle_completes_session(async_client):
    s_email = f"{_u('shuf')}@x.com"
    s_username = _u("shuf")
    await async_client.post("/auth/register", json={"email": s_email, "username": s_username, "display_name": "S", "password": "SuperSecret123"})
    await async_client.post("/auth/login", json={"email": s_email, "password": "SuperSecret123"})
    g = (await async_client.post("/groups", json={"name":"G","member_user_ids":[]})).json()
    group_id = g["id"]

    i1 = (await async_client.post(f"/groups/{group_id}/watchlist", json={"type":"tmdb","tmdb_id":100,"media_type":"movie","title":"A","year":2000,"poster_path":None})).json()
    i2 = (await async_client.post(f"/groups/{group_id}/watchlist", json={"type":"tmdb","tmdb_id":101,"media_type":"movie","title":"B","year":2001,"poster_path":None})).json()

    s = (await async_client.post(f"/groups/{group_id}/sessions", json={"constraints":{"format":"any"}, "duration_seconds":90, "candidate_count":5})).json()
    session_id = s["session_id"]

    st = (await async_client.post(f"/sessions/{session_id}/shuffle")).json()
    assert st["status"] == "complete"
    assert st["result_watchlist_item_id"] in (i1["id"], i2["id"])
    assert st["completed_at"] is not None
