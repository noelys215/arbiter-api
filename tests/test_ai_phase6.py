import uuid

from sqlalchemy import select
import pytest

from app.models.title import Title
from app.services.ai import AIError, AIRerankResult


def _u(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def _register_and_login(client, user_factory, login_helper, *, email: str, username: str):
    user = await user_factory(client, email=email, username=username, display_name="U")
    await login_helper(client, email=user["email"], password=user["password"])


async def _add_tmdb_item(client, *, group_id: str, tmdb_id: int, title: str, media_type: str = "movie"):
    payload = {
        "type": "tmdb",
        "tmdb_id": tmdb_id,
        "media_type": media_type,
        "title": title,
        "year": 2000,
        "poster_path": None,
    }
    r = await client.post(f"/groups/{group_id}/watchlist", json=payload)
    assert r.status_code == 201
    return r.json()


@pytest.mark.anyio
async def test_session_create_ai_parse_applies_constraints_and_marks_ai_used_false_if_rerank_off(
    async_client, monkeypatch, db_session, user_factory, login_helper
):
    from app.services import sessions as sessions_service
    from app.schemas.tonight_constraints import TonightConstraints

    async def fake_parse(*, baseline: TonightConstraints, text: str):
        refined = baseline.model_copy(deep=True)
        refined.format = "movie"
        refined.max_runtime = 120
        refined.free_text = text.strip()
        refined.parsed_by_ai = True
        refined.ai_version = "test-model"
        return refined

    async def fake_rerank(*, constraints: TonightConstraints, candidates: list[dict]):
        raise AIError("rerank failed")

    monkeypatch.setattr(sessions_service, "ai_parse_constraints", fake_parse)
    monkeypatch.setattr(sessions_service, "ai_rerank_candidates", fake_rerank)

    email = f"{_u('a')}@x.com"
    username = _u("a")
    await _register_and_login(async_client, user_factory, login_helper, email=email, username=username)

    group = (await async_client.post("/groups", json={"name": "G", "member_user_ids": []})).json()
    group_id = group["id"]

    movie_ok = await _add_tmdb_item(async_client, group_id=group_id, tmdb_id=1, title="Movie OK", media_type="movie")
    movie_long = await _add_tmdb_item(async_client, group_id=group_id, tmdb_id=2, title="Movie Long", media_type="movie")
    tv_item = await _add_tmdb_item(async_client, group_id=group_id, tmdb_id=3, title="Show", media_type="tv")

    title_ids = {
        uuid.UUID(movie_ok["title"]["id"]): 100,
        uuid.UUID(movie_long["title"]["id"]): 150,
        uuid.UUID(tv_item["title"]["id"]): 90,
    }
    rows = await db_session.execute(select(Title).where(Title.id.in_(title_ids.keys())))
    for t in rows.scalars():
        t.runtime_minutes = title_ids[t.id]
    await db_session.commit()

    payload = {
        "constraints": {},
        "text": "please keep it short",
        "duration_seconds": 90,
        "candidate_count": 5,
    }
    r = await async_client.post(f"/groups/{group_id}/sessions", json=payload)
    assert r.status_code == 201, r.text
    data = r.json()

    assert data["constraints"]["format"] == "movie"
    assert data["constraints"]["max_runtime"] == 120
    assert data["constraints"]["parsed_by_ai"] is True
    assert data["constraints"]["ai_version"] == "test-model"
    assert data["constraints"]["free_text"] == "please keep it short"
    assert data["ai_used"] is False
    assert data["ai_why"] is None

    ids = [c["watchlist_item_id"] for c in data["candidates"]]
    assert movie_ok["id"] in ids
    assert movie_long["id"] not in ids
    assert tv_item["id"] not in ids


@pytest.mark.anyio
async def test_ai_rerank_reorders_candidates_and_stores_why(async_client, monkeypatch, user_factory, login_helper):
    from app.services import sessions as sessions_service
    order_holder: dict[str, list[str]] = {}

    async def fake_rerank(*, constraints, candidates):
        ordered = [c["id"] for c in candidates][::-1]
        order_holder["ordered"] = ordered
        return AIRerankResult(ordered_ids=ordered, top_id=ordered[0], why="Because vibes")

    monkeypatch.setattr(sessions_service, "ai_rerank_candidates", fake_rerank)

    email = f"{_u('b')}@x.com"
    username = _u("b")
    await _register_and_login(async_client, user_factory, login_helper, email=email, username=username)

    group = (await async_client.post("/groups", json={"name": "G", "member_user_ids": []})).json()
    group_id = group["id"]

    i1 = await _add_tmdb_item(async_client, group_id=group_id, tmdb_id=11, title="A")
    i2 = await _add_tmdb_item(async_client, group_id=group_id, tmdb_id=12, title="B")
    i3 = await _add_tmdb_item(async_client, group_id=group_id, tmdb_id=13, title="C")

    r = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={"constraints": {}, "duration_seconds": 90, "candidate_count": 3},
    )
    assert r.status_code == 201
    data = r.json()

    assert data["ai_used"] is True
    assert data["ai_why"] == "Because vibes"
    returned = [c["watchlist_item_id"] for c in data["candidates"]]
    assert returned == order_holder["ordered"][: len(returned)]
    assert set(returned) == {i1["id"], i2["id"], i3["id"]}


@pytest.mark.anyio
async def test_ai_rerank_invalid_ids_falls_back_deterministic(async_client, monkeypatch, user_factory, login_helper):
    from app.services import sessions as sessions_service

    async def fake_rerank(*, constraints, candidates):
        return AIRerankResult(ordered_ids=["not-a-real-id"], top_id=None, why="Nope")

    monkeypatch.setattr(sessions_service, "ai_rerank_candidates", fake_rerank)

    email = f"{_u('c')}@x.com"
    username = _u("c")
    await _register_and_login(async_client, user_factory, login_helper, email=email, username=username)

    group = (await async_client.post("/groups", json={"name": "G", "member_user_ids": []})).json()
    group_id = group["id"]

    i1 = await _add_tmdb_item(async_client, group_id=group_id, tmdb_id=21, title="A")
    i2 = await _add_tmdb_item(async_client, group_id=group_id, tmdb_id=22, title="B")
    i3 = await _add_tmdb_item(async_client, group_id=group_id, tmdb_id=23, title="C")

    r = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={"constraints": {}, "duration_seconds": 90, "candidate_count": 3},
    )
    assert r.status_code == 201
    data = r.json()

    assert data["ai_used"] is False
    assert data["ai_why"] is None
    returned = {c["watchlist_item_id"] for c in data["candidates"]}
    assert returned == {i1["id"], i2["id"], i3["id"]}


@pytest.mark.anyio
async def test_missing_openai_key_does_not_break_session_creation(async_client, monkeypatch, user_factory, login_helper):
    from app.core.config import settings

    monkeypatch.setattr(settings, "openai_api_key", None)

    email = f"{_u('d')}@x.com"
    username = _u("d")
    await _register_and_login(async_client, user_factory, login_helper, email=email, username=username)

    group = (await async_client.post("/groups", json={"name": "G", "member_user_ids": []})).json()
    group_id = group["id"]

    await _add_tmdb_item(async_client, group_id=group_id, tmdb_id=31, title="A")
    await _add_tmdb_item(async_client, group_id=group_id, tmdb_id=32, title="B")

    r = await async_client.post(
        f"/groups/{group_id}/sessions",
        json={"constraints": {}, "text": "something fun", "duration_seconds": 90, "candidate_count": 2},
    )
    assert r.status_code == 201
    data = r.json()

    assert data["constraints"]["parsed_by_ai"] is False
    assert data["ai_used"] is False
