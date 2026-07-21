from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.models.group import Group
from app.models.tonight_session import TonightSession
from app.models.tonight_session_candidate import TonightSessionCandidate
from app.models.tonight_session_participant import TonightSessionParticipant
from app.models.tonight_session_vote_snapshot import TonightSessionVoteSnapshot
from app.services.group_insights import CALCULATION_VERSION, calculate_group_insights


async def _group_and_user(async_client, user_factory, login_helper):
    user = await user_factory(async_client, display_name="Insight Host")
    await login_helper(async_client, email=user["email"], password=user["password"])
    response = await async_client.post("/groups", json={"name": "Picture House"})
    assert response.status_code == 201, response.text
    return user, response.json()


async def _add_completed_night(
    db_session,
    *,
    group_id: str,
    user_id: str,
    index: int,
    completed_at: datetime,
    watched_status: str = "watched",
    runtime: int | None = 100,
    genres: list[str] | None = None,
    mood_cues: list[str] | None = None,
    duration_seconds: int | None = 480,
    unanimous: bool | None = True,
    yes_count: int = 1,
):
    session = TonightSession(
        group_id=uuid.UUID(group_id),
        created_by_user_id=uuid.UUID(user_id),
        constraints={},
        ends_at=completed_at,
        duration_seconds=90,
        candidate_count=2,
        status="completed",
        started_at=completed_at - timedelta(seconds=duration_seconds or 0),
        winner_selected_at=completed_at,
        completed_at=completed_at,
        group_name_snapshot="Picture House",
        criteria_snapshot={"mood_cues": mood_cues or []},
        decision_duration_seconds=duration_seconds,
        winner_unanimous=unanimous,
        watched_status=watched_status,
    )
    db_session.add(session)
    await db_session.flush()
    winner = TonightSessionCandidate(
        session_id=session.id,
        source_watchlist_item_id=uuid.uuid4(),
        title_source="tmdb",
        title_source_id=str(9000 + index),
        media_type="movie",
        title_name=f"Winner {index}",
        release_year=2020 + index,
        runtime_minutes=runtime,
        genres=genres or [],
        yes_count=yes_count,
        no_count=max(0, 2 - yes_count),
        total_vote_count=2,
        is_winner=True,
        is_finalist=True,
        position=0,
    )
    contender = TonightSessionCandidate(
        session_id=session.id,
        source_watchlist_item_id=uuid.uuid4(),
        title_source="tmdb",
        title_source_id="shared-contender",
        media_type="movie",
        title_name="The Returning Contender",
        runtime_minutes=95,
        genres=["Drama"],
        yes_count=max(0, yes_count - 1),
        no_count=1,
        total_vote_count=2,
        is_winner=False,
        is_finalist=True,
        position=1,
    )
    db_session.add_all([winner, contender])
    await db_session.flush()
    participant = TonightSessionParticipant(
        session_id=session.id,
        user_id=uuid.UUID(user_id),
        display_name="Insight Host",
        avatar_source="initials",
        avatar_seed="Insight Host",
        role="host",
        submitted_votes=True,
        participation_status="participated",
    )
    db_session.add(participant)
    await db_session.flush()
    db_session.add_all(
        [
            TonightSessionVoteSnapshot(
                session_id=session.id,
                participant_id=participant.id,
                candidate_id=winner.id,
                round_number=1,
                vote="yes",
            ),
            TonightSessionVoteSnapshot(
                session_id=session.id,
                participant_id=participant.id,
                candidate_id=contender.id,
                round_number=1,
                vote="no",
            ),
        ]
    )
    session.winner_candidate_id = winner.id
    await db_session.commit()
    return session.id


@pytest.mark.anyio
async def test_empty_group_returns_basic_contract(
    async_client, user_factory, login_helper
):
    _, group = await _group_and_user(async_client, user_factory, login_helper)
    response = await async_client.get(f"/groups/{group['id']}/insights")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["calculation_version"] == CALCULATION_VERSION
    assert body["activity"]["completed_nights"] == 0
    assert body["availability"] == {
        "sample_size": 0,
        "confidence_tier": "empty",
        "personality_available": False,
        "member_highlights_available": False,
        "reason_unavailable": "No completed movie nights yet.",
        "next_tier_at": 1,
    }
    assert body["personality"] is None


@pytest.mark.parametrize(
    ("count", "tier", "personality_available", "next_tier"),
    [
        (1, "basic", False, 3),
        (2, "basic", False, 3),
        (3, "emerging", False, 5),
        (4, "emerging", False, 5),
        (5, "established", True, None),
        (8, "established", True, None),
    ],
)
def test_sample_thresholds_are_centralized(
    count, tier, personality_available, next_tier
):
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    owner_id = uuid.uuid4()
    group = Group(id=uuid.uuid4(), name="Threshold Club", owner_id=owner_id)
    sessions = [
        TonightSession(
            id=uuid.uuid4(),
            group_id=group.id,
            created_by_user_id=owner_id,
            constraints={},
            ends_at=now,
            duration_seconds=90,
            candidate_count=0,
            status="completed",
            completed_at=now - timedelta(days=index),
            criteria_snapshot={},
            watched_status="unconfirmed",
        )
        for index in range(count)
    ]
    result = calculate_group_insights(
        group=group, sessions=sessions, period="all_time", now=now
    )
    assert result.availability.confidence_tier == tier
    assert result.availability.personality_available is personality_available
    assert result.availability.next_tier_at == next_tier


@pytest.mark.anyio
async def test_insights_distinguish_watched_runtime_and_missing_data(
    async_client, db_session, user_factory, login_helper
):
    user, group = await _group_and_user(async_client, user_factory, login_helper)
    now = datetime.now(timezone.utc)
    await _add_completed_night(
        db_session,
        group_id=group["id"],
        user_id=user["id"],
        index=1,
        completed_at=now - timedelta(days=2),
        runtime=104,
        genres=["Horror", "Mystery"],
        mood_cues=["edge-of-our-seats"],
    )
    await _add_completed_night(
        db_session,
        group_id=group["id"],
        user_id=user["id"],
        index=2,
        completed_at=now - timedelta(days=1),
        runtime=None,
        genres=[],
        mood_cues=[],
    )
    await _add_completed_night(
        db_session,
        group_id=group["id"],
        user_id=user["id"],
        index=3,
        completed_at=now,
        watched_status="unconfirmed",
        runtime=180,
        genres=["Drama"],
    )

    response = await async_client.get(f"/groups/{group['id']}/insights")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["activity"]["completed_nights"] == 3
    assert body["activity"]["confirmed_watched_nights"] == 2
    assert body["activity"]["total_watch_minutes"] == 104
    assert body["activity"]["average_watched_runtime_minutes"] == 104
    assert body["data_quality"]["watched_runtimes_missing"] == 1
    assert body["taste"]["genres"][0]["label"] == "Horror"
    assert all(item["label"] != "Drama" for item in body["taste"]["genres"])
    assert body["availability"]["confidence_tier"] == "emerging"


@pytest.mark.anyio
async def test_personality_is_deterministic_and_supported_by_facts(
    async_client, db_session, user_factory, login_helper
):
    user, group = await _group_and_user(async_client, user_factory, login_helper)
    now = datetime.now(timezone.utc)
    for index in range(5):
        await _add_completed_night(
            db_session,
            group_id=group["id"],
            user_id=user["id"],
            index=index,
            completed_at=now - timedelta(days=5 - index),
            runtime=105,
            genres=["Thriller", "Mystery"],
            mood_cues=["edge-of-our-seats"],
            duration_seconds=720,
            unanimous=index < 4,
        )

    first = (await async_client.get(f"/groups/{group['id']}/insights")).json()
    second = (await async_client.get(f"/groups/{group['id']}/insights")).json()
    assert first["personality"] == second["personality"]
    assert first["personality"]["title"] == "Consensus cinephiles"
    assert first["personality"]["supporting_facts"] == [
        "Mystery appeared in 5 confirmed watched nights.",
        "Your median decision takes 12 min.",
        "Confirmed winners average 105 minutes.",
    ]
    returned = next(
        record for record in first["records"] if record["key"] == "most-considered"
    )
    assert returned["value"] == "The Returning Contender"
    assert returned["detail"] == "Considered in 5 movie nights"
    closest = next(
        record for record in first["records"] if record["key"] == "closest-decision"
    )
    assert closest["value"] == "Won by 1 vote"


@pytest.mark.anyio
async def test_this_year_uses_utc_completion_boundary(
    async_client, db_session, user_factory, login_helper
):
    user, group = await _group_and_user(async_client, user_factory, login_helper)
    year = datetime.now(timezone.utc).year
    await _add_completed_night(
        db_session,
        group_id=group["id"],
        user_id=user["id"],
        index=1,
        completed_at=datetime(year - 1, 12, 31, 23, 59, tzinfo=timezone.utc),
    )
    await _add_completed_night(
        db_session,
        group_id=group["id"],
        user_id=user["id"],
        index=2,
        completed_at=datetime(year, 1, 1, 0, 0, tzinfo=timezone.utc),
    )
    response = await async_client.get(
        f"/groups/{group['id']}/insights?period=this_year"
    )
    assert response.status_code == 200
    assert response.json()["activity"]["completed_nights"] == 1
    assert response.json()["period"]["starts_at"].startswith(f"{year}-01-01")


@pytest.mark.anyio
async def test_member_highlights_require_sufficient_shared_history_and_votes(
    async_client, db_session, user_factory, login_helper
):
    user, group = await _group_and_user(async_client, user_factory, login_helper)
    now = datetime.now(timezone.utc)
    for index in range(10):
        await _add_completed_night(
            db_session,
            group_id=group["id"],
            user_id=user["id"],
            index=index,
            completed_at=now - timedelta(days=index),
            genres=["Drama"],
        )
    response = await async_client.get(f"/groups/{group['id']}/insights")
    assert response.status_code == 200, response.text
    highlights = response.json()["member_highlights"]
    assert highlights == [
        {
            "user_id": user["id"],
            "display_name": "Insight Host",
            "avatar_url": None,
            "avatar_source": "initials",
            "avatar_style": None,
            "avatar_seed": "Insight Host",
            "title": "Reliable regular",
            "explanation": "Joined 10 completed movie nights.",
        }
    ]


@pytest.mark.anyio
async def test_insights_require_group_membership(
    async_client, client_factory, user_factory, login_helper
):
    _, group = await _group_and_user(async_client, user_factory, login_helper)
    async with client_factory() as outsider:
        user = await user_factory(outsider, display_name="Outsider")
        await login_helper(outsider, email=user["email"], password=user["password"])
        denied = await outsider.get(f"/groups/{group['id']}/insights")
        assert denied.status_code == 403


@pytest.mark.anyio
async def test_cancelled_sessions_are_excluded_and_invalid_period_is_rejected(
    async_client, db_session, user_factory, login_helper
):
    user, group = await _group_and_user(async_client, user_factory, login_helper)
    cancelled = TonightSession(
        group_id=uuid.UUID(group["id"]),
        created_by_user_id=uuid.UUID(user["id"]),
        constraints={},
        ends_at=datetime.now(timezone.utc),
        duration_seconds=90,
        candidate_count=5,
        status="cancelled",
        cancelled_at=datetime.now(timezone.utc),
    )
    db_session.add(cancelled)
    await db_session.commit()
    body = (await async_client.get(f"/groups/{group['id']}/insights")).json()
    assert body["activity"]["completed_nights"] == 0
    invalid = await async_client.get(
        f"/groups/{group['id']}/insights?period=last_week"
    )
    assert invalid.status_code == 422


@pytest.mark.anyio
async def test_calculator_tie_breaking_is_stable(db_session):
    group = Group(id=uuid.uuid4(), name="Stable Club", owner_id=uuid.uuid4())
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    first = calculate_group_insights(group=group, sessions=[], period="all_time", now=now)
    second = calculate_group_insights(group=group, sessions=[], period="all_time", now=now)
    assert first == second
