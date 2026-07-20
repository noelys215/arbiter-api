import pytest


async def _group_movie(async_client, user_factory, login_helper):
    user = await user_factory(async_client, display_name="Presentation Host")
    await login_helper(async_client, email=user["email"], password=user["password"])
    group = (await async_client.post("/groups", json={"name": "Screening Room"})).json()
    item = (
        await async_client.post(
            f"/groups/{group['id']}/watchlist",
            json={
                "type": "tmdb",
                "tmdb_id": 603,
                "media_type": "movie",
                "title": "The Matrix",
                "year": 1999,
                "poster_path": "/matrix.jpg",
            },
        )
    ).json()
    return user, group, item


@pytest.mark.anyio
async def test_movie_detail_combines_safe_group_and_tmdb_context(
    async_client, monkeypatch, user_factory, login_helper
):
    from app.services import movie_presentation

    async def fake_details(*, tmdb_id: int, media_type: str):
        assert (tmdb_id, media_type) == (603, "movie")
        return {
            "title": "The Matrix",
            "release_year": 1999,
            "release_date": "1999-03-31",
            "runtime_minutes": 136,
            "poster_path": "/matrix.jpg",
            "backdrop_path": "/matrix-wide.jpg",
            "overview": "A hacker discovers the nature of reality.",
            "genres": ["Action", "Science Fiction"],
            "directors": ["Lana Wachowski", "Lilly Wachowski"],
            "cast": [{"name": "Keanu Reeves", "role": "Neo"}],
            "certification": "R",
            "trailer_url": "https://www.youtube.com/watch?v=example",
        }

    monkeypatch.setattr(movie_presentation, "fetch_tmdb_presentation_details", fake_details)
    user, group, item = await _group_movie(
        async_client, user_factory, login_helper
    )

    response = await async_client.get(
        f"/groups/{group['id']}/movie-details/watchlist-{item['id']}"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "The Matrix"
    assert body["backdrop_path"] == "/matrix-wide.jpg"
    assert body["directors"] == ["Lana Wachowski", "Lilly Wachowski"]
    assert body["watchlist"]["added_by"]["id"] == user["id"]
    assert "email" not in body["watchlist"]["added_by"]
    assert body["history"]["appearance_count"] == 0

    search_reference = await async_client.get(
        f"/groups/{group['id']}/movie-details/tmdb-movie-603"
    )
    assert search_reference.status_code == 200, search_reference.text
    assert search_reference.json()["watchlist"]["item_id"] == item["id"]


@pytest.mark.anyio
async def test_movie_detail_requires_group_membership(
    async_client, client_factory, user_factory, login_helper
):
    _, group, item = await _group_movie(async_client, user_factory, login_helper)
    async with client_factory() as outsider:
        outsider_user = await user_factory(outsider, display_name="Outsider")
        await login_helper(
            outsider,
            email=outsider_user["email"],
            password=outsider_user["password"],
        )
        response = await outsider.get(
            f"/groups/{group['id']}/movie-details/watchlist-{item['id']}"
        )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_movie_detail_survives_optional_tmdb_failure(
    async_client, monkeypatch, user_factory, login_helper
):
    from app.services import movie_presentation

    async def unavailable_details(*, tmdb_id: int, media_type: str):
        _ = (tmdb_id, media_type)
        return {}

    monkeypatch.setattr(
        movie_presentation, "fetch_tmdb_presentation_details", unavailable_details
    )
    _, group, item = await _group_movie(async_client, user_factory, login_helper)
    response = await async_client.get(
        f"/groups/{group['id']}/movie-details/watchlist-{item['id']}"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "The Matrix"
    assert body["poster_path"] == "/matrix.jpg"
    assert body["directors"] == []


@pytest.mark.anyio
async def test_movie_detail_session_context_excludes_vote_data(
    async_client, user_factory, login_helper
):
    _, group, item = await _group_movie(async_client, user_factory, login_helper)
    created = await async_client.post(
        f"/groups/{group['id']}/sessions",
        json={
            "constraints": {"mood_cues": ["easygoing"]},
            "duration_seconds": 90,
            "candidate_count": 5,
        },
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["session_id"]

    response = await async_client.get(
        f"/groups/{group['id']}/movie-details/watchlist-{item['id']}",
        params={"session_id": session_id},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["session"]["session_id"] == session_id
    assert body["session"]["mood_cue_ids"] == ["easygoing"]
    assert "votes" not in body["session"]
    assert "vote_summaries" not in body["session"]


@pytest.mark.anyio
async def test_completed_winner_artwork_is_authorized_and_proxied(
    async_client, monkeypatch, user_factory, login_helper
):
    from app.services import movie_presentation

    _, group, _ = await _group_movie(async_client, user_factory, login_helper)
    second = await async_client.post(
        f"/groups/{group['id']}/watchlist",
        json={
            "type": "tmdb",
            "tmdb_id": 604,
            "media_type": "movie",
            "title": "The Matrix Reloaded",
            "year": 2003,
            "poster_path": "/reloaded.jpg",
        },
    )
    assert second.status_code == 201, second.text
    created = await async_client.post(
        f"/groups/{group['id']}/sessions",
        json={"constraints": {}, "duration_seconds": 90, "candidate_count": 5},
    )
    winner = await async_client.post(f"/sessions/{created.json()['session_id']}/shuffle")
    completed = await async_client.post(
        f"/sessions/{created.json()['session_id']}/completion"
    )
    assert winner.status_code == 200, winner.text
    assert completed.status_code == 200, completed.text

    winner_row = next(row for row in completed.json()["candidates"] if row["is_winner"])

    async def fake_image(*, path: str, size: str):
        assert path in {"/matrix.jpg", "/reloaded.jpg"}
        assert size == "w780"
        return b"poster-bytes", "image/jpeg"

    monkeypatch.setattr(movie_presentation, "fetch_tmdb_image", fake_image)
    response = await async_client.get(
        f"/groups/{group['id']}/movie-night-artwork/{winner_row['id']}"
    )
    assert response.status_code == 200, response.text
    assert response.content == b"poster-bytes"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, max-age=86400"
