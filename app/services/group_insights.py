"""Deterministic definitions for group Insights.

Eligible nights are completed sessions whose completion timestamp falls inside the
UTC period. Watch time and genre taste use confirmed-watched winners only; unknown
runtimes are reported rather than estimated. Decision metrics use stored server
timestamps and snapshot outcomes. Cancelled and merely winner-selected sessions are
excluded. All ties use stable key ordering, and thresholds live in this module so the
API and personality rules cannot drift independently.
"""

from __future__ import annotations

import statistics
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import lazyload, noload, selectinload

from app.models.group import Group
from app.models.tonight_session import TonightSession
from app.models.tonight_session_candidate import TonightSessionCandidate
from app.models.tonight_session_participant import TonightSessionParticipant
from app.models.tonight_session_vote_snapshot import TonightSessionVoteSnapshot
from app.schemas.group_insights import (
    GroupInsightsOut,
    GroupPersonalityOut,
    InsightsActivityOut,
    InsightsAvailabilityOut,
    InsightsDataQualityOut,
    InsightsDecisionOut,
    InsightsPeriodKey,
    InsightsPeriodOut,
    InsightsRecordOut,
    InsightsTasteOut,
    MemberHighlightOut,
    PersonalityDimensionOut,
    RankedInsightOut,
)
from app.schemas.mood_cues import MOOD_CUES
from app.services.watchlist import assert_user_in_group


CALCULATION_VERSION = "group-insights-v1"
PERSONALITY_MIN_NIGHTS = 5
RICH_INSIGHTS_MIN_NIGHTS = 8
MEMBER_MIN_SESSIONS = 5
MEMBER_MIN_VOTES = 20

MOOD_LABELS = {cue.id: cue.label for cue in MOOD_CUES}


def _winner(session: TonightSession) -> TonightSessionCandidate | None:
    return next((row for row in session.candidates if row.is_winner), None)


def _source_key(candidate: TonightSessionCandidate) -> str:
    if candidate.title_source and candidate.title_source_id:
        return f"{candidate.title_source}:{candidate.title_source_id}"
    if candidate.source_title_id:
        return f"title:{candidate.source_title_id}"
    return f"name:{(candidate.title_name or 'untitled').strip().casefold()}"


def _confidence(sample_size: int) -> tuple[str, int | None]:
    if sample_size == 0:
        return "empty", 1
    if sample_size < 3:
        return "basic", 3
    if sample_size < PERSONALITY_MIN_NIGHTS:
        return "emerging", PERSONALITY_MIN_NIGHTS
    return "established", None


def _ranked(counter: Counter[str], denominator: int, *, labels: dict[str, str] | None = None, limit: int = 5) -> list[RankedInsightOut]:
    if denominator <= 0:
        return []
    labels = labels or {}
    return [
        RankedInsightOut(
            key=key,
            label=labels.get(key, key),
            count=count,
            percentage=round((count / denominator) * 100, 1),
        )
        for key, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def _format_duration(seconds: int) -> str:
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"{minutes} min"
    hours, remainder = divmod(minutes, 60)
    return f"{hours}h {remainder}m" if remainder else f"{hours}h"


def _runtime_band(minutes: int) -> str:
    if minutes < 90:
        return "under-90"
    if minutes < 120:
        return "feature-length"
    return "epic"


RUNTIME_LABELS = {
    "under-90": "Under 90 minutes",
    "feature-length": "90–119 minutes",
    "epic": "Two hours or more",
}


def _personality(
    sessions: list[TonightSession],
    *,
    genre_ranking: list[RankedInsightOut],
    mood_ranking: list[RankedInsightOut],
    watched_runtimes: list[int],
    decision_times: list[int],
    unanimous_values: list[bool],
) -> GroupPersonalityOut | None:
    if len(sessions) < PERSONALITY_MIN_NIGHTS:
        return None

    median_seconds = round(statistics.median(decision_times)) if decision_times else None
    pace_value = 0.5 if median_seconds is None else min(1.0, median_seconds / 900)
    pace = "quick" if pace_value <= 0.34 else "deliberate" if pace_value >= 0.67 else "measured"

    average_runtime = round(statistics.mean(watched_runtimes)) if watched_runtimes else None
    runtime_value = 0.5 if average_runtime is None else min(1.0, max(0.0, (average_runtime - 80) / 80))
    runtime = "short" if runtime_value <= 0.3 else "epic" if runtime_value >= 0.7 else "balanced"

    genre_total = sum(item.count for item in genre_ranking)
    top_genre_share = genre_ranking[0].count / genre_total if genre_total and genre_ranking else 0
    variety_value = min(1.0, len(genre_ranking) / 5) * (1 - min(0.8, top_genre_share))
    variety = "exploratory" if variety_value >= 0.45 else "loyal"

    unanimous_rate = (
        sum(unanimous_values) / len(unanimous_values) if unanimous_values else None
    )
    consensus_value = unanimous_rate if unanimous_rate is not None else 0.5
    consensus = "aligned" if consensus_value >= 0.6 else "spirited" if consensus_value <= 0.3 else "balanced"

    top_genre = genre_ranking[0] if genre_ranking else None
    top_mood = mood_ranking[0] if mood_ranking else None
    intense_genres = {"Horror", "Thriller", "Mystery", "Crime"}
    comfort_moods = {"easygoing", "comfort-watch", "something-hopeful", "nostalgic"}

    if consensus == "aligned":
        title = "Consensus cinephiles"
    elif pace == "deliberate" and top_genre and top_genre.label in intense_genres:
        title = "Patient thrill seekers"
    elif runtime == "short" and pace == "quick":
        title = "Short-and-sharp selectors"
    elif variety == "exploratory" and consensus == "spirited":
        title = "Beautiful-chaos voters"
    elif variety == "exploratory":
        title = "Genre explorers"
    elif top_mood and top_mood.key in comfort_moods:
        title = "Comfort-watch regulars"
    elif top_genre:
        title = f"{top_genre.label} regulars"
    else:
        title = "Curious film club"

    facts: list[str] = []
    if top_genre:
        facts.append(
            f"{top_genre.label} appeared in {top_genre.count} confirmed watched {'night' if top_genre.count == 1 else 'nights'}."
        )
    if median_seconds is not None:
        facts.append(f"Your median decision takes {_format_duration(median_seconds)}.")
    if average_runtime is not None:
        facts.append(f"Confirmed winners average {average_runtime} minutes.")
    if unanimous_rate is not None:
        facts.append(f"{round(unanimous_rate * 100)}% of recorded decisions were unanimous.")

    descriptors = {
        "quick": "moves quickly",
        "measured": "takes a measured approach",
        "deliberate": "rarely rushes a choice",
    }
    taste = (
        f" and keeps returning to {top_genre.label.lower()}"
        if top_genre
        else " while exploring the watchlist"
    )
    return GroupPersonalityOut(
        title=title,
        description=f"Your group {descriptors[pace]}{taste}.",
        supporting_facts=facts[:3],
        dimensions=[
            PersonalityDimensionOut(key="pace", label="Decision pace", value=round(pace_value, 2), interpretation=pace),
            PersonalityDimensionOut(key="runtime", label="Runtime", value=round(runtime_value, 2), interpretation=runtime),
            PersonalityDimensionOut(key="variety", label="Variety", value=round(variety_value, 2), interpretation=variety),
            PersonalityDimensionOut(key="consensus", label="Consensus", value=round(consensus_value, 2), interpretation=consensus),
        ],
        sample_size=len(sessions),
        confidence_tier="established",
    )


def _member_highlights(sessions: list[TonightSession]) -> list[MemberHighlightOut]:
    if len(sessions) < RICH_INSIGHTS_MIN_NIGHTS:
        return []
    attendance: Counter[uuid.UUID] = Counter()
    votes: Counter[uuid.UUID] = Counter()
    winner_yes_sessions: set[tuple[uuid.UUID, uuid.UUID]] = set()
    snapshots: dict[uuid.UUID, TonightSessionParticipant] = {}
    participants_by_id: dict[uuid.UUID, TonightSessionParticipant] = {}

    for session in sessions:
        for participant in session.participant_snapshots:
            participants_by_id[participant.id] = participant
            if participant.user_id and participant.participation_status == "participated":
                attendance[participant.user_id] += 1
                snapshots[participant.user_id] = participant
        winner = _winner(session)
        for vote in session.vote_snapshots:
            participant = participants_by_id.get(vote.participant_id)
            if not participant or not participant.user_id:
                continue
            votes[participant.user_id] += 1
            if winner and vote.candidate_id == winner.id and vote.vote == "yes":
                winner_yes_sessions.add((participant.user_id, session.id))

    eligible = [
        user_id
        for user_id in snapshots
        if attendance[user_id] >= MEMBER_MIN_SESSIONS and votes[user_id] >= MEMBER_MIN_VOTES
    ]
    if not eligible:
        return []

    best_attendance = max(attendance[user_id] for user_id in eligible)
    regulars = sorted(
        (user_id for user_id in eligible if attendance[user_id] == best_attendance),
        key=str,
    )
    highlights: list[MemberHighlightOut] = []
    if len(regulars) == 1:
        user_id = regulars[0]
        row = snapshots[user_id]
        highlights.append(
            MemberHighlightOut(
                user_id=user_id,
                display_name=row.display_name,
                avatar_url=row.avatar_url,
                avatar_source=row.avatar_source,
                avatar_style=row.avatar_style,
                avatar_seed=row.avatar_seed,
                title="Reliable regular",
                explanation=f"Joined {attendance[user_id]} completed movie nights.",
            )
        )

    alignment = {
        user_id: sum(item[0] == user_id for item in winner_yes_sessions)
        / max(1, attendance[user_id])
        for user_id in eligible
    }
    best_alignment = max(alignment.values())
    compasses = sorted(
        (user_id for user_id, value in alignment.items() if value == best_alignment),
        key=str,
    )
    if len(compasses) == 1 and compasses[0] not in regulars:
        user_id = compasses[0]
        row = snapshots[user_id]
        highlights.append(
            MemberHighlightOut(
                user_id=user_id,
                display_name=row.display_name,
                avatar_url=row.avatar_url,
                avatar_source=row.avatar_source,
                avatar_style=row.avatar_style,
                avatar_seed=row.avatar_seed,
                title="Group compass",
                explanation=f"Backed the winner in {sum(item[0] == user_id for item in winner_yes_sessions)} recorded nights.",
            )
        )
    return highlights[:2]


def calculate_group_insights(
    *,
    group: Group,
    sessions: Iterable[TonightSession],
    period: InsightsPeriodKey,
    now: datetime,
) -> GroupInsightsOut:
    rows = list(sessions)
    confidence_tier, next_tier_at = _confidence(len(rows))
    watched = [row for row in rows if row.watched_status == "watched"]
    watched_winners = [winner for row in watched if (winner := _winner(row))]
    watched_runtimes = [row.runtime_minutes for row in watched_winners if row.runtime_minutes and row.runtime_minutes > 0]
    decision_times = [row.decision_duration_seconds for row in rows if row.decision_duration_seconds is not None and row.decision_duration_seconds >= 0]
    unanimous_values = [row.winner_unanimous for row in rows if row.winner_unanimous is not None]

    genre_counts: Counter[str] = Counter()
    genre_sessions = 0
    for winner in watched_winners:
        genres = {value.strip() for value in (winner.genres or []) if isinstance(value, str) and value.strip()}
        if genres:
            genre_sessions += 1
            genre_counts.update(genres)
    mood_counts: Counter[str] = Counter()
    mood_sessions = 0
    for row in rows:
        cues = {
            value
            for value in (row.criteria_snapshot or {}).get("mood_cues", [])
            if isinstance(value, str) and value in MOOD_LABELS
        }
        if cues:
            mood_sessions += 1
            mood_counts.update(cues)
    runtime_counts = Counter(_runtime_band(value) for value in watched_runtimes)

    genre_ranking = _ranked(
        genre_counts,
        genre_sessions,
        labels={key: key.title() for key in genre_counts},
    )
    mood_ranking = _ranked(mood_counts, mood_sessions, labels=MOOD_LABELS)
    runtime_ranking = _ranked(runtime_counts, len(watched_runtimes), labels=RUNTIME_LABELS)

    records: list[InsightsRecordOut] = []
    timed = [row for row in rows if row.decision_duration_seconds is not None and row.decision_duration_seconds >= 0]
    if timed:
        fastest = min(timed, key=lambda row: (row.decision_duration_seconds or 0, str(row.id)))
        longest = max(timed, key=lambda row: (row.decision_duration_seconds or 0, str(row.id)))
        records.extend([
            InsightsRecordOut(key="fastest-decision", label="Fastest decision", value=_format_duration(fastest.decision_duration_seconds or 0), detail=(_winner(fastest).title_name if _winner(fastest) else None), session_id=fastest.id),
            InsightsRecordOut(key="longest-decision", label="Most considered decision", value=_format_duration(longest.decision_duration_seconds or 0), detail=(_winner(longest).title_name if _winner(longest) else None), session_id=longest.id),
        ])
    comparable: list[tuple[TonightSession, TonightSessionCandidate, int, float]] = []
    for row in rows:
        winner = _winner(row)
        if not winner or winner.yes_count is None or winner.total_vote_count in {None, 0}:
            continue
        other_yes = [
            candidate.yes_count
            for candidate in row.candidates
            if not candidate.is_winner and candidate.yes_count is not None
        ]
        if not other_yes:
            continue
        comparable.append(
            (
                row,
                winner,
                winner.yes_count - max(other_yes),
                winner.yes_count / (winner.total_vote_count or 1),
            )
        )
    if len(comparable) >= 2:
        closest = min(comparable, key=lambda item: (item[2], str(item[0].id)))
        lead = closest[2]
        records.append(
            InsightsRecordOut(
                key="closest-decision",
                label="Closest decision",
                value="Tie resolved" if lead == 0 else f"Won by {lead} {'vote' if lead == 1 else 'votes'}",
                detail=closest[1].title_name,
                session_id=closest[0].id,
            )
        )
        strongest = max(comparable, key=lambda item: (item[3], str(item[0].id)))
        if strongest[0].id != closest[0].id or strongest[3] > closest[3]:
            records.append(
                InsightsRecordOut(
                    key="largest-consensus",
                    label="Largest consensus",
                    value=f"{round(strongest[3] * 100)}% approval",
                    detail=strongest[1].title_name,
                    session_id=strongest[0].id,
                )
            )
    considered: dict[str, list[TonightSessionCandidate]] = defaultdict(list)
    for row in rows:
        for candidate in row.candidates:
            considered[_source_key(candidate)].append(candidate)
    repeated = sorted(
        ((key, values) for key, values in considered.items() if len(values) > 1),
        key=lambda item: (-len(item[1]), item[0]),
    )
    if repeated:
        _, candidates = repeated[0]
        records.append(
            InsightsRecordOut(
                key="most-considered",
                label="Most returned title",
                value=candidates[0].title_name or "Untitled",
                detail=f"Considered in {len(candidates)} movie nights",
            )
        )

    unique_winners = {_source_key(winner) for row in rows if (winner := _winner(row))}
    missing_watched_runtimes = len(watched_winners) - len(watched_runtimes)
    notes: list[str] = []
    if missing_watched_runtimes:
        notes.append(f"{missing_watched_runtimes} watched {'night was' if missing_watched_runtimes == 1 else 'nights were'} excluded from watch-time totals because runtime was unavailable.")
    sessions_with_votes = sum(bool(row.vote_snapshots) for row in rows)
    if sessions_with_votes < len(rows):
        notes.append("Decision traits use only nights with preserved vote data.")
    if len(rows) < PERSONALITY_MIN_NIGHTS:
        notes.append(f"Complete {PERSONALITY_MIN_NIGHTS - len(rows)} more {'night' if PERSONALITY_MIN_NIGHTS - len(rows) == 1 else 'nights'} to reveal an initial group personality.")

    starts_at = datetime(now.year, 1, 1, tzinfo=timezone.utc) if period == "this_year" else None
    member_highlights = _member_highlights(rows)
    return GroupInsightsOut(
        group_id=group.id,
        group_name=group.name,
        calculation_version=CALCULATION_VERSION,
        period=InsightsPeriodOut(key=period, label="This year" if period == "this_year" else "All time", starts_at=starts_at, ends_at=now),
        availability=InsightsAvailabilityOut(
            sample_size=len(rows),
            confidence_tier=confidence_tier,
            personality_available=len(rows) >= PERSONALITY_MIN_NIGHTS,
            member_highlights_available=bool(member_highlights),
            reason_unavailable="No completed movie nights yet." if not rows else None,
            next_tier_at=next_tier_at,
        ),
        activity=InsightsActivityOut(
            completed_nights=len(rows),
            confirmed_watched_nights=len(watched),
            total_watch_minutes=sum(watched_runtimes),
            average_watched_runtime_minutes=round(statistics.mean(watched_runtimes)) if watched_runtimes else None,
            unique_winners=len(unique_winners),
            unique_genres_explored=len(genre_counts),
        ),
        decision=InsightsDecisionOut(
            average_seconds=round(statistics.mean(decision_times)) if decision_times else None,
            median_seconds=round(statistics.median(decision_times)) if decision_times else None,
            average_candidate_count=round(statistics.mean(len(row.candidates) for row in rows), 1) if rows else None,
            unanimous_rate=round(sum(unanimous_values) / len(unanimous_values), 3) if unanimous_values else None,
            unanimous_sample_size=len(unanimous_values),
        ),
        taste=InsightsTasteOut(genres=genre_ranking, moods=mood_ranking, runtime_bands=runtime_ranking),
        records=records,
        personality=_personality(rows, genre_ranking=genre_ranking, mood_ranking=mood_ranking, watched_runtimes=watched_runtimes, decision_times=decision_times, unanimous_values=unanimous_values),
        member_highlights=member_highlights,
        data_quality=InsightsDataQualityOut(
            watched_runtimes_known=len(watched_runtimes),
            watched_runtimes_missing=missing_watched_runtimes,
            decisions_timed=len(decision_times),
            unanimity_known=len(unanimous_values),
            sessions_with_vote_snapshots=sessions_with_votes,
            notes=notes,
        ),
    )


async def get_group_insights(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    period: InsightsPeriodKey,
    now: datetime | None = None,
) -> GroupInsightsOut:
    await assert_user_in_group(db, group_id, user_id)
    group = await db.get(Group, group_id)
    if group is None:
        raise ValueError("Group not found")
    current_time = now or datetime.now(timezone.utc)
    query = (
        select(TonightSession)
        .options(
            lazyload("*"),
            selectinload(TonightSession.candidates).options(
                noload(TonightSessionCandidate.session),
                noload(TonightSessionCandidate.watchlist_item),
            ),
            selectinload(TonightSession.participant_snapshots).options(
                noload(TonightSessionParticipant.session)
            ),
            selectinload(TonightSession.vote_snapshots).options(
                noload(TonightSessionVoteSnapshot.session),
                noload(TonightSessionVoteSnapshot.participant),
                noload(TonightSessionVoteSnapshot.candidate),
            ),
        )
        .where(
            TonightSession.group_id == group_id,
            TonightSession.status == "completed",
            TonightSession.completed_at.is_not(None),
        )
        .order_by(TonightSession.completed_at.asc(), TonightSession.id.asc())
    )
    if period == "this_year":
        query = query.where(
            TonightSession.completed_at >= datetime(current_time.year, 1, 1, tzinfo=timezone.utc)
        )
    rows = (await db.execute(query)).scalars().unique().all()
    return calculate_group_insights(group=group, sessions=rows, period=period, now=current_time)
