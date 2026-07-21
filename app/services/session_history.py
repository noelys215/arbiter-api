from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.presenters.users import avatar_fields_from_user
from app.models.tonight_session import TonightSession
from app.models.tonight_session_candidate import TonightSessionCandidate
from app.models.tonight_session_participant import TonightSessionParticipant
from app.models.tonight_session_vote_snapshot import TonightSessionVoteSnapshot
from app.models.user import User
from app.models.watchlist_item import WatchlistItem
from app.schemas.session_history import (
    CompletedCandidateOut,
    CompletedParticipantOut,
    CompletedSessionOut,
    GroupMovieNightPage,
)
from app.schemas.tonight_constraints import TonightConstraints
from app.services.watchlist import assert_user_in_group


SESSION_RUNTIME_KEY = "__session_runtime_v1"


def candidate_source_id(candidate: TonightSessionCandidate) -> uuid.UUID:
    return candidate.source_watchlist_item_id or candidate.watchlist_item_id


def _runtime(session: TonightSession) -> dict[str, Any]:
    raw = (session.constraints or {}).get(SESSION_RUNTIME_KEY)
    return raw if isinstance(raw, dict) else {}


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)


def _canonical_criteria(session: TonightSession) -> dict[str, Any]:
    source_constraints = session.constraints or {}
    runtime = _runtime(session)
    collecting = runtime.get("collecting")
    user_constraints = (
        collecting.get("user_constraints") if isinstance(collecting, dict) else None
    )
    host_constraints = (
        user_constraints.get(str(session.created_by_user_id))
        if isinstance(user_constraints, dict)
        else None
    )
    if isinstance(host_constraints, dict):
        source_constraints = host_constraints

    public_constraints = {
        key: value
        for key, value in source_constraints.items()
        if key != SESSION_RUNTIME_KEY
    }
    return TonightConstraints.model_validate(public_constraints).model_dump(mode="json")


def _round_votes(runtime: dict[str, Any], round_number: int) -> dict[str, dict[str, str]]:
    rounds = runtime.get("rounds")
    state = rounds.get(str(round_number)) if isinstance(rounds, dict) else None
    votes = state.get("votes") if isinstance(state, dict) else None
    return votes if isinstance(votes, dict) else {}


def _participant_ids(session: TonightSession, runtime: dict[str, Any]) -> set[uuid.UUID]:
    raw_ids: set[str] = {str(session.created_by_user_id)}
    collecting = runtime.get("collecting")
    if isinstance(collecting, dict):
        for field in ("user_joined_at", "user_dealt_at", "user_constraints", "user_decks"):
            value = collecting.get(field)
            if isinstance(value, dict):
                raw_ids.update(str(key) for key in value)
    for round_number in (1, 2):
        raw_ids.update(_round_votes(runtime, round_number))

    parsed: set[uuid.UUID] = set()
    for raw in raw_ids:
        try:
            parsed.add(uuid.UUID(raw))
        except (TypeError, ValueError):
            continue
    return parsed


async def _ensure_participant_snapshots(
    db: AsyncSession,
    *,
    session: TonightSession,
    runtime: dict[str, Any],
) -> dict[uuid.UUID, TonightSessionParticipant]:
    existing = (
        await db.execute(
            select(TonightSessionParticipant).where(
                TonightSessionParticipant.session_id == session.id
            )
        )
    ).scalars().all()
    by_user = {row.user_id: row for row in existing if row.user_id is not None}

    user_ids = _participant_ids(session, runtime)
    missing_ids = user_ids.difference(by_user)
    users = (
        (
            await db.execute(select(User).where(User.id.in_(missing_ids)))
        ).scalars().all()
        if missing_ids
        else []
    )
    users_by_id = {user.id: user for user in users}
    collecting = runtime.get("collecting") if isinstance(runtime.get("collecting"), dict) else {}
    joined_map = collecting.get("user_joined_at") if isinstance(collecting, dict) else {}
    criteria_map = collecting.get("user_constraints") if isinstance(collecting, dict) else {}

    for user_id in sorted(missing_ids, key=str):
        user = users_by_id.get(user_id)
        if user is None:
            continue
        avatar = avatar_fields_from_user(user)
        row = TonightSessionParticipant(
            session_id=session.id,
            user_id=user_id,
            display_name=user.display_name or user.username,
            avatar_url=avatar["avatar_url"],
            avatar_source=avatar["avatar_source"],
            avatar_style=avatar["avatar_style"],
            avatar_seed=avatar["avatar_seed"],
            joined_at=_parse_timestamp(
                joined_map.get(str(user_id)) if isinstance(joined_map, dict) else None
            ),
            role="host" if user_id == session.created_by_user_id else "participant",
            submitted_votes=any(
                str(user_id) in _round_votes(runtime, round_number)
                and bool(_round_votes(runtime, round_number).get(str(user_id)))
                for round_number in (1, 2)
            ),
            participation_status="participated",
            criteria_snapshot=(
                criteria_map.get(str(user_id))
                if isinstance(criteria_map, dict)
                and isinstance(criteria_map.get(str(user_id)), dict)
                else None
            ),
        )
        db.add(row)
        by_user[user_id] = row
    await db.flush()
    return by_user


def _candidate_by_source(session: TonightSession) -> dict[uuid.UUID, TonightSessionCandidate]:
    return {candidate_source_id(row): row for row in session.candidates}


def _ensure_candidate_metadata_snapshots(session: TonightSession) -> None:
    """Fill migrated active candidates from their still-live watchlist relation."""
    for candidate in session.candidates:
        item = candidate.watchlist_item
        title = item.title if item is not None else None
        if title is None:
            continue
        candidate.source_title_id = candidate.source_title_id or title.id
        candidate.title_source = candidate.title_source or title.source
        candidate.title_source_id = candidate.title_source_id or title.source_id
        candidate.media_type = candidate.media_type or title.media_type
        candidate.title_name = candidate.title_name or title.name
        candidate.release_year = candidate.release_year or title.release_year
        candidate.poster_path = candidate.poster_path or title.poster_path
        candidate.runtime_minutes = candidate.runtime_minutes or title.runtime_minutes
        candidate.overview = candidate.overview or title.overview


async def _ensure_vote_snapshots(
    db: AsyncSession,
    *,
    session: TonightSession,
    runtime: dict[str, Any],
    participants: dict[uuid.UUID, TonightSessionParticipant],
) -> None:
    existing = set(
        (
            await db.execute(
                select(
                    TonightSessionVoteSnapshot.participant_id,
                    TonightSessionVoteSnapshot.candidate_id,
                    TonightSessionVoteSnapshot.round_number,
                ).where(TonightSessionVoteSnapshot.session_id == session.id)
            )
        ).tuples()
    )
    candidates = _candidate_by_source(session)
    for round_number in (1, 2):
        for raw_user_id, user_votes in _round_votes(runtime, round_number).items():
            if not isinstance(user_votes, dict):
                continue
            try:
                user_id = uuid.UUID(str(raw_user_id))
            except ValueError:
                continue
            participant = participants.get(user_id)
            if participant is None:
                continue
            for raw_item_id, vote in user_votes.items():
                if vote not in {"yes", "no"}:
                    continue
                try:
                    item_id = uuid.UUID(str(raw_item_id))
                except ValueError:
                    continue
                candidate = candidates.get(item_id)
                if candidate is None:
                    continue
                key = (participant.id, candidate.id, round_number)
                if key in existing:
                    continue
                db.add(
                    TonightSessionVoteSnapshot(
                        session_id=session.id,
                        participant_id=participant.id,
                        candidate_id=candidate.id,
                        round_number=round_number,
                        vote=vote,
                    )
                )
                existing.add(key)


def _apply_candidate_outcomes(
    session: TonightSession,
    runtime: dict[str, Any],
    winner_source_id: uuid.UUID,
) -> TonightSessionCandidate:
    round_one_votes = _round_votes(runtime, 1)
    finalist_ids: set[uuid.UUID] = set()
    for field in ("mutual_candidate_ids", "tie_break_candidate_ids"):
        raw_values = runtime.get(field)
        if not isinstance(raw_values, list):
            continue
        for value in raw_values:
            try:
                finalist_ids.add(uuid.UUID(str(value)))
            except ValueError:
                continue

    winner: TonightSessionCandidate | None = None
    for candidate in session.candidates:
        source_id = candidate_source_id(candidate)
        votes = [
            user_votes.get(str(source_id))
            for user_votes in round_one_votes.values()
            if isinstance(user_votes, dict)
        ]
        candidate.yes_count = sum(vote == "yes" for vote in votes)
        candidate.no_count = sum(vote == "no" for vote in votes)
        candidate.total_vote_count = candidate.yes_count + candidate.no_count
        candidate.is_winner = source_id == winner_source_id
        candidate.is_finalist = source_id in finalist_ids or candidate.is_winner
        if candidate.is_winner:
            winner = candidate
    if winner is None:
        raise ValueError("Winner is not present in the session candidates")
    return winner


async def freeze_winner_result(
    db: AsyncSession,
    *,
    session: TonightSession,
    runtime: dict[str, Any],
    winner_source_id: uuid.UUID,
    now: datetime,
    had_tie: bool | None,
    tie_resolution: str | None,
) -> None:
    _ensure_candidate_metadata_snapshots(session)
    winner = _apply_candidate_outcomes(session, runtime, winner_source_id)
    participants = await _ensure_participant_snapshots(
        db, session=session, runtime=runtime
    )
    await _ensure_vote_snapshots(
        db, session=session, runtime=runtime, participants=participants
    )

    session.status = "winner_selected"
    session.winner_selected_at = session.winner_selected_at or now
    session.started_at = session.started_at or session.locked_at or session.created_at
    session.group_name_snapshot = session.group_name_snapshot or session.group.name
    session.criteria_snapshot = session.criteria_snapshot or _canonical_criteria(session)
    session.winner_candidate_id = winner.id
    session.result_watchlist_item_id = winner_source_id
    session.had_tie = had_tie
    session.tie_resolution = tie_resolution
    if session.started_at:
        session.decision_duration_seconds = max(
            0, int((session.winner_selected_at - session.started_at).total_seconds())
        )

    participant_count = len(participants)
    session.winner_unanimous = (
        participant_count > 0
        and winner.yes_count == participant_count
        and winner.no_count == 0
    )
    await db.flush()


def _completion_load_options():
    return (
        selectinload(TonightSession.candidates).selectinload(
            TonightSessionCandidate.watchlist_item
        ).selectinload(WatchlistItem.title),
        selectinload(TonightSession.participant_snapshots),
    )


async def _locked_session(db: AsyncSession, session_id: uuid.UUID) -> TonightSession:
    query = (
        select(TonightSession)
        .options(*_completion_load_options())
        .where(TonightSession.id == session_id)
        .with_for_update(of=TonightSession)
    )
    row = (await db.execute(query)).scalar_one_or_none()
    if row is None:
        raise ValueError("Session not found")
    return row


async def complete_session(
    db: AsyncSession, *, session_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[TonightSession, bool]:
    session = await _locked_session(db, session_id)
    await assert_user_in_group(db, session.group_id, user_id)
    if session.status == "completed":
        return session, False
    if session.status not in {"winner_selected", "complete"}:
        raise ValueError("Session does not have a winner to complete")
    if session.result_watchlist_item_id is None:
        raise ValueError("Session does not have a winner to complete")

    runtime = _runtime(session)
    if session.winner_candidate_id is None:
        await freeze_winner_result(
            db,
            session=session,
            runtime=runtime,
            winner_source_id=session.result_watchlist_item_id,
            now=session.completed_at or datetime.now(timezone.utc),
            had_tie=None,
            tie_resolution="legacy" if session.status == "complete" else None,
        )

    now = datetime.now(timezone.utc)
    session.status = "completed"
    session.completed_at = session.completed_at or now
    session.teleparty_shared_at = session.teleparty_shared_at or session.watch_party_set_at
    await db.flush()
    return session, True


async def mark_watch_party_handoff(
    db: AsyncSession, *, session_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[TonightSession, bool]:
    session = await _locked_session(db, session_id)
    await assert_user_in_group(db, session.group_id, user_id)
    if not session.watch_party_url or session.result_watchlist_item_id is None:
        raise ValueError("Teleparty handoff is not available")
    if session.teleparty_handoff_at is not None:
        return session, False
    session.teleparty_handoff_at = datetime.now(timezone.utc)
    await db.flush()
    return session, True


async def update_watched_status(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    watched_status: str,
) -> tuple[TonightSession, bool]:
    if watched_status not in {"unconfirmed", "watched", "not_watched"}:
        raise ValueError("Invalid watched status")
    session = await _locked_session(db, session_id)
    await assert_user_in_group(db, session.group_id, user_id)
    if session.status != "completed":
        raise ValueError("Complete the movie night before confirming it")
    if user_id not in {session.created_by_user_id, session.group.owner_id}:
        raise PermissionError("Only the host or group leader can confirm this")
    if session.watched_status == watched_status:
        return session, False
    session.watched_status = watched_status
    session.watched_confirmed_at = datetime.now(timezone.utc)
    session.watched_confirmed_by_user_id = user_id
    await db.flush()
    return session, True


async def get_completed_session(
    db: AsyncSession, *, session_id: uuid.UUID, user_id: uuid.UUID
) -> TonightSession:
    query = (
        select(TonightSession)
        .options(*_completion_load_options())
        .where(TonightSession.id == session_id)
    )
    session = (await db.execute(query)).scalar_one_or_none()
    if session is None:
        raise ValueError("Session not found")
    await assert_user_in_group(db, session.group_id, user_id)
    if session.status not in {"winner_selected", "completed"}:
        raise ValueError("Completed movie night not found")
    return session


async def list_group_movie_nights(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    limit: int,
    cursor: str | None,
) -> GroupMovieNightPage:
    await assert_user_in_group(db, group_id, user_id)
    offset = int(cursor) if cursor and cursor.isdigit() else 0
    page_limit = max(1, min(limit, 50))
    query = (
        select(TonightSession)
        .options(*_completion_load_options())
        .where(
            TonightSession.group_id == group_id,
            TonightSession.status == "completed",
        )
        .order_by(TonightSession.completed_at.desc(), TonightSession.id.desc())
        .offset(offset)
        .limit(page_limit + 1)
    )
    rows = (await db.execute(query)).scalars().unique().all()
    has_more = len(rows) > page_limit
    items = rows[:page_limit]
    return GroupMovieNightPage(
        items=[completed_session_out(row) for row in items],
        next_cursor=str(offset + page_limit) if has_more else None,
    )


def completed_session_out(session: TonightSession) -> CompletedSessionOut:
    candidates = sorted(session.candidates, key=lambda row: row.position)
    winner_selected_at = session.winner_selected_at or session.completed_at
    if winner_selected_at is None or session.winner_candidate_id is None:
        raise ValueError("Completed movie night snapshot is incomplete")
    return CompletedSessionOut(
        session_id=session.id,
        group_id=session.group_id,
        group_name=session.group_name_snapshot or session.group.name,
        status=session.status,
        created_at=session.created_at,
        started_at=session.started_at,
        winner_selected_at=winner_selected_at,
        completed_at=session.completed_at,
        criteria=session.criteria_snapshot or _canonical_criteria(session),
        winner_candidate_id=session.winner_candidate_id,
        decision_duration_seconds=session.decision_duration_seconds,
        winner_unanimous=session.winner_unanimous,
        had_tie=session.had_tie,
        tie_resolution=session.tie_resolution,
        watched_status=session.watched_status,
        watched_confirmed_at=session.watched_confirmed_at,
        watched_confirmed_by_user_id=session.watched_confirmed_by_user_id,
        teleparty_was_shared=bool(session.teleparty_shared_at),
        teleparty_shared_at=session.teleparty_shared_at,
        teleparty_handoff_at=session.teleparty_handoff_at,
        participants=[
            CompletedParticipantOut(
                id=row.id,
                user_id=row.user_id,
                display_name=row.display_name,
                avatar_url=row.avatar_url,
                avatar_source=row.avatar_source,
                avatar_style=row.avatar_style,
                avatar_seed=row.avatar_seed,
                joined_at=row.joined_at,
                role=row.role,
                submitted_votes=row.submitted_votes,
                participation_status=row.participation_status,
                criteria=row.criteria_snapshot,
            )
            for row in sorted(session.participant_snapshots, key=lambda item: (item.role != "host", item.display_name))
        ],
        candidates=[
            CompletedCandidateOut(
                id=row.id,
                source_title_id=row.source_title_id,
                source=row.title_source,
                source_id=row.title_source_id,
                media_type=row.media_type,
                title=row.title_name or "Untitled",
                release_year=row.release_year,
                poster_path=row.poster_path,
                backdrop_path=row.backdrop_path,
                runtime_minutes=row.runtime_minutes,
                genres=[value for value in (row.genres or []) if isinstance(value, str)],
                overview=row.overview,
                position=row.position,
                yes_count=row.yes_count,
                no_count=row.no_count,
                total_vote_count=row.total_vote_count,
                is_winner=row.is_winner,
                is_finalist=row.is_finalist,
            )
            for row in candidates
        ],
    )
