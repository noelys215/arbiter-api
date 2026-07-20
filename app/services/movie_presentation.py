from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.presenters.users import avatar_fields_from_user
from app.models.group import Group
from app.models.title import Title
from app.models.tonight_session import TonightSession
from app.models.tonight_session_candidate import TonightSessionCandidate
from app.models.watchlist_item import WatchlistItem
from app.schemas.movie_presentation import (
    MovieAddedByOut,
    MovieDetailOut,
    MovieHistoryContextOut,
    MovieNightAppearanceOut,
    MoviePersonOut,
    MovieSessionContextOut,
    MovieWatchlistContextOut,
)
from app.services.tmdb import fetch_tmdb_image, fetch_tmdb_presentation_details
from app.services.watchlist import assert_user_in_group


@dataclass
class _MovieBase:
    title_id: uuid.UUID | None
    source: str | None
    source_id: str | None
    media_type: str
    title: str
    release_year: int | None
    runtime_minutes: int | None
    poster_path: str | None
    backdrop_path: str | None
    overview: str | None
    genres: list[str]
    watchlist_item: WatchlistItem | None = None


def _parse_reference(reference: str) -> tuple[str, str]:
    kind, separator, value = reference.partition("-")
    if not separator or kind not in {"watchlist", "title", "history", "tmdb"}:
        raise ValueError("Movie reference is invalid")
    return kind, value


def _uuid_reference(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ValueError("Movie reference is invalid") from exc


def _title_base(title: Title, watchlist_item: WatchlistItem | None = None) -> _MovieBase:
    return _MovieBase(
        title_id=title.id,
        source=title.source,
        source_id=title.source_id,
        media_type=title.media_type,
        title=title.name,
        release_year=title.release_year,
        runtime_minutes=title.runtime_minutes,
        poster_path=title.poster_path,
        backdrop_path=None,
        overview=title.overview,
        genres=[],
        watchlist_item=watchlist_item,
    )


def _candidate_base(candidate: TonightSessionCandidate) -> _MovieBase:
    return _MovieBase(
        title_id=candidate.source_title_id,
        source=candidate.title_source,
        source_id=candidate.title_source_id,
        media_type=candidate.media_type or "movie",
        title=candidate.title_name or "Untitled",
        release_year=candidate.release_year,
        runtime_minutes=candidate.runtime_minutes,
        poster_path=candidate.poster_path,
        backdrop_path=candidate.backdrop_path,
        overview=candidate.overview,
        genres=[value for value in (candidate.genres or []) if isinstance(value, str)],
        watchlist_item=candidate.watchlist_item,
    )


async def _resolve_movie_base(
    db: AsyncSession, *, group_id: uuid.UUID, reference: str
) -> _MovieBase:
    kind, value = _parse_reference(reference)
    if kind == "watchlist":
        item = (
            await db.execute(
                select(WatchlistItem)
                .options(
                    selectinload(WatchlistItem.title),
                    selectinload(WatchlistItem.added_by_user),
                )
                .where(
                    WatchlistItem.id == _uuid_reference(value),
                    WatchlistItem.group_id == group_id,
                )
            )
        ).scalar_one_or_none()
        if item is None:
            raise ValueError("Movie is not available in this group")
        return _title_base(item.title, item)

    if kind == "title":
        title_id = _uuid_reference(value)
        title = (
            await db.execute(
                select(Title)
                .join(WatchlistItem, WatchlistItem.title_id == Title.id)
                .where(Title.id == title_id, WatchlistItem.group_id == group_id)
            )
        ).scalar_one_or_none()
        if title is None:
            candidate = (
                await db.execute(
                    select(TonightSessionCandidate)
                    .join(TonightSession, TonightSession.id == TonightSessionCandidate.session_id)
                    .where(
                        TonightSessionCandidate.source_title_id == title_id,
                        TonightSession.group_id == group_id,
                    )
                    .order_by(TonightSessionCandidate.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if candidate is None:
                raise ValueError("Movie is not available in this group")
            return _candidate_base(candidate)
        return _title_base(title)

    if kind == "history":
        candidate = (
            await db.execute(
                select(TonightSessionCandidate)
                .join(TonightSession, TonightSession.id == TonightSessionCandidate.session_id)
                .where(
                    TonightSessionCandidate.id == _uuid_reference(value),
                    TonightSession.group_id == group_id,
                    TonightSession.status == "completed",
                )
            )
        ).scalar_one_or_none()
        if candidate is None:
            raise ValueError("Movie night entry is unavailable")
        return _candidate_base(candidate)

    media_type, separator, raw_tmdb_id = value.partition("-")
    if not separator or media_type not in {"movie", "tv"} or not raw_tmdb_id.isdigit():
        raise ValueError("Movie reference is invalid")
    existing_item = (
        await db.execute(
            select(WatchlistItem)
            .join(Title, Title.id == WatchlistItem.title_id)
            .options(
                selectinload(WatchlistItem.title),
                selectinload(WatchlistItem.added_by_user),
            )
            .where(
                WatchlistItem.group_id == group_id,
                Title.source == "tmdb",
                Title.source_id == raw_tmdb_id,
                Title.media_type == media_type,
            )
        )
    ).scalar_one_or_none()
    if existing_item is not None:
        return _title_base(existing_item.title, existing_item)
    details = await fetch_tmdb_presentation_details(
        tmdb_id=int(raw_tmdb_id), media_type=media_type
    )
    if not details.get("title"):
        raise ValueError("Movie details are temporarily unavailable")
    return _MovieBase(
        title_id=None,
        source="tmdb",
        source_id=raw_tmdb_id,
        media_type=media_type,
        title=str(details["title"]),
        release_year=details.get("release_year"),
        runtime_minutes=details.get("runtime_minutes"),
        poster_path=details.get("poster_path"),
        backdrop_path=details.get("backdrop_path"),
        overview=details.get("overview"),
        genres=list(details.get("genres") or []),
    )


async def _history_context(
    db: AsyncSession, *, group_id: uuid.UUID, base: _MovieBase
) -> MovieHistoryContextOut:
    identity = []
    if base.title_id is not None:
        identity.append(TonightSessionCandidate.source_title_id == base.title_id)
    if base.source and base.source_id:
        identity.append(
            (TonightSessionCandidate.title_source == base.source)
            & (TonightSessionCandidate.title_source_id == base.source_id)
            & (TonightSessionCandidate.media_type == base.media_type)
        )
    if not identity:
        return MovieHistoryContextOut(appearance_count=0, win_count=0)

    rows = (
        await db.execute(
            select(TonightSessionCandidate, TonightSession)
            .join(TonightSession, TonightSession.id == TonightSessionCandidate.session_id)
            .where(
                TonightSession.group_id == group_id,
                TonightSession.status == "completed",
                or_(*identity),
            )
            .order_by(TonightSession.completed_at.desc())
        )
    ).all()
    appearances = [
        MovieNightAppearanceOut(
            session_id=session.id,
            completed_at=session.completed_at,
            won=bool(candidate.is_winner),
            watched_status=session.watched_status,
        )
        for candidate, session in rows
        if session.completed_at is not None
    ]
    last_watched_at = next(
        (
            appearance.completed_at
            for appearance in appearances
            if appearance.won and appearance.watched_status == "watched"
        ),
        None,
    )
    return MovieHistoryContextOut(
        appearance_count=len(appearances),
        win_count=sum(appearance.won for appearance in appearances),
        last_considered_at=appearances[0].completed_at if appearances else None,
        last_watched_at=last_watched_at,
        recent_movie_nights=appearances[:3],
    )


async def get_movie_detail(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    reference: str,
    session_id: uuid.UUID | None,
) -> MovieDetailOut:
    await assert_user_in_group(db, group_id, user_id)
    group = await db.get(Group, group_id)
    if group is None:
        raise ValueError("Group not found")
    base = await _resolve_movie_base(db, group_id=group_id, reference=reference)

    enriched: dict[str, Any] = {}
    if base.source == "tmdb" and base.source_id and base.source_id.isdigit():
        enriched = await fetch_tmdb_presentation_details(
            tmdb_id=int(base.source_id), media_type=base.media_type
        )

    watchlist = None
    item = base.watchlist_item
    if item is None and base.title_id is not None:
        item = (
            await db.execute(
                select(WatchlistItem)
                .options(selectinload(WatchlistItem.added_by_user))
                .where(
                    WatchlistItem.group_id == group_id,
                    WatchlistItem.title_id == base.title_id,
                )
            )
        ).scalar_one_or_none()
    if item is not None:
        added_by = None
        if item.added_by_user is not None:
            added_by = MovieAddedByOut(
                id=item.added_by_user.id,
                username=item.added_by_user.username,
                display_name=item.added_by_user.display_name,
                **avatar_fields_from_user(item.added_by_user),
            )
        watchlist = MovieWatchlistContextOut(
            item_id=item.id,
            status=item.status,
            added_at=item.created_at,
            added_by=added_by,
        )

    session_context = None
    if session_id is not None:
        session = (
            await db.execute(
                select(TonightSession)
                .options(selectinload(TonightSession.candidates))
                .where(
                    TonightSession.id == session_id,
                    TonightSession.group_id == group_id,
                )
            )
        ).scalar_one_or_none()
        if session is None:
            raise ValueError("Session not found")
        candidate = next(
            (
                row
                for row in session.candidates
                if (base.title_id and row.source_title_id == base.title_id)
                or (
                    base.source_id
                    and row.title_source_id == base.source_id
                    and row.media_type == base.media_type
                )
            ),
            None,
        )
        if candidate is not None:
            constraints = session.criteria_snapshot or session.constraints or {}
            raw_cues = constraints.get("mood_cues", constraints.get("mood_cue_ids", []))
            session_context = MovieSessionContextOut(
                session_id=session.id,
                status=session.status,
                match_reason=candidate.ai_note,
                mood_cue_ids=[str(value) for value in raw_cues if isinstance(value, str)],
            )

    title = str(enriched.get("title") or base.title)
    return MovieDetailOut(
        reference=reference,
        group_id=group_id,
        group_name=group.name,
        title_id=base.title_id,
        source=base.source,
        source_id=base.source_id,
        media_type=base.media_type,
        title=title,
        release_year=enriched.get("release_year") or base.release_year,
        release_date=enriched.get("release_date"),
        runtime_minutes=enriched.get("runtime_minutes") or base.runtime_minutes,
        poster_path=enriched.get("poster_path") or base.poster_path,
        backdrop_path=enriched.get("backdrop_path") or base.backdrop_path,
        overview=enriched.get("overview") or base.overview,
        genres=list(enriched.get("genres") or base.genres),
        directors=list(enriched.get("directors") or []),
        cast=[MoviePersonOut(**row) for row in enriched.get("cast", [])],
        certification=enriched.get("certification"),
        trailer_url=enriched.get("trailer_url"),
        watchlist=watchlist,
        session=session_context,
        history=await _history_context(db, group_id=group_id, base=base),
    )


async def get_movie_night_artwork(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    candidate_id: uuid.UUID,
) -> tuple[bytes, str]:
    await assert_user_in_group(db, group_id, user_id)
    candidate = (
        await db.execute(
            select(TonightSessionCandidate)
            .join(TonightSession, TonightSession.id == TonightSessionCandidate.session_id)
            .where(
                TonightSessionCandidate.id == candidate_id,
                TonightSessionCandidate.is_winner.is_(True),
                TonightSession.group_id == group_id,
                TonightSession.status == "completed",
            )
        )
    ).scalar_one_or_none()
    if candidate is None or not candidate.poster_path:
        raise ValueError("Movie artwork is unavailable")
    return await fetch_tmdb_image(path=candidate.poster_path, size="w780")
