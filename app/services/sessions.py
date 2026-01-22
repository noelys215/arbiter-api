from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.tonight_session import TonightSession
from app.models.tonight_session_candidate import TonightSessionCandidate
from app.models.tonight_vote import TonightVote
from app.models.watchlist_item import WatchlistItem
from app.schemas.tonight_constraints import TonightConstraints
from app.services.ai import parse_constraints_with_ai, rerank_candidates_with_ai
from app.services.watchlist import assert_user_in_group


def _canonicalize_constraints(payload: dict) -> TonightConstraints:
    # Force canonical normalization using your Phase 5.1 model
    c = TonightConstraints.model_validate(payload or {})
    return c


def _constraints_hash(c: TonightConstraints) -> int:
    # Stable seed for deterministic ordering (non-AI path)
    s = json.dumps(c.model_dump(), sort_keys=True)
    return abs(hash(s)) % (2**31)


def _apply_hard_filters(items: list[WatchlistItem], c: TonightConstraints) -> list[WatchlistItem]:
    out: list[WatchlistItem] = []

    for it in items:
        t = it.title

        # format filter
        if c.format != "any" and t.media_type != c.format:
            continue

        # max_runtime filter (only if runtime known)
        if c.max_runtime is not None and t.runtime_minutes is not None:
            if t.runtime_minutes > c.max_runtime:
                continue

        out.append(it)

    return out


def _deterministic_shuffle(items: list[WatchlistItem], seed: int) -> list[WatchlistItem]:
    # Use a deterministic shuffle for stable tests + predictable behavior.
    # This is NOT cryptographically secure; just stable ordering.
    import random

    r = random.Random(seed)
    items2 = list(items)
    r.shuffle(items2)
    return items2


async def create_tonight_session(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    constraints_payload: dict,
    text: str | None,
    duration_seconds: int,
    candidate_count: int,
) -> tuple[TonightSession, list[TonightSessionCandidate]]:
    # 1) verify membership
    await assert_user_in_group(db, group_id, user_id)

    # 2) baseline constraints from UI fields (canonicalize)
    base = _canonicalize_constraints(constraints_payload)

    # 3) if text present, call AI parse (optional)
    ai_used = False
    refined = base
    if text and text.strip():
        refined = await parse_constraints_with_ai(base=base, text=text.strip())
        # parsed_by_ai is set in ai.py only if AI ran
        ai_used = bool(refined.parsed_by_ai)

    # Always keep free_text consistent:
    refined.free_text = (text or refined.free_text or "").strip()

    # 4) eligible pool from watchlist:
    #    status=watchlist, snoozed_until <= now
    now = datetime.now(timezone.utc)

    q = (
        select(WatchlistItem)
        .options(selectinload(WatchlistItem.title))
        .where(
            WatchlistItem.group_id == group_id,
            WatchlistItem.status == "watchlist",
            sa.or_(WatchlistItem.snoozed_until.is_(None), WatchlistItem.snoozed_until <= now),
        )
        .order_by(WatchlistItem.created_at.desc())
    )
    eligible = (await db.execute(q)).scalars().all()

    # 5) apply hard filters from constraints
    filtered = _apply_hard_filters(eligible, refined)

    pool_size = len(filtered)
    if pool_size == 0:
        raise ValueError("No eligible watchlist items (all watched or snoozed). Add items or unsnooze.")

    ends_at = now + timedelta(seconds=duration_seconds)

    sess = TonightSession(
        group_id=group_id,
        created_by_user_id=user_id,
        constraints=refined.model_dump(),
        ends_at=ends_at,
        duration_seconds=duration_seconds,
        candidate_count=candidate_count,
        ai_used=ai_used,
    )
    db.add(sess)
    await db.flush()  # get session id

    # 6) pick preliminary set (top 30) using deterministic shuffle
    seed = _constraints_hash(refined) ^ abs(hash(str(group_id))) % (2**31)
    prelim = _deterministic_shuffle(filtered, seed=seed)[:30]

    # 7) AI rerank on preliminary set to pick best N for deck
    final_n = min(candidate_count, len(prelim))
    candidates_payload: list[dict[str, Any]] = []
    for idx, it in enumerate(prelim):
        t = it.title
        candidates_payload.append(
            {
                "idx": idx,
                "watchlist_item_id": str(it.id),
                "title": t.name,
                "media_type": t.media_type,
                "year": t.release_year,
                "moods": refined.moods,
                "avoid": refined.avoid,
                "energy": refined.energy,
                "max_runtime": refined.max_runtime,
                "format": refined.format,
            }
        )

    ordered_idxs, why = await rerank_candidates_with_ai(
        constraints=refined,
        candidates=candidates_payload,
        final_n=final_n,
    )

    # If AI returned something (or fallback did), we consider rerank part of AI usage only if parse used AI.
    # You can change this later if you want ai_used to mean "any AI used".
    if why:
        sess.ai_why = why

    # 8) freeze deck in session_candidates
    out_candidates: list[TonightSessionCandidate] = []
    used_watchlist_ids: set[uuid.UUID] = set()

    for pos, idx in enumerate(ordered_idxs):
        it = prelim[idx]
        if it.id in used_watchlist_ids:
            continue
        used_watchlist_ids.add(it.id)

        c = TonightSessionCandidate(
            session_id=sess.id,
            watchlist_item_id=it.id,
            position=pos,
            ai_note=None,
        )
        db.add(c)
        out_candidates.append(c)

    await db.flush()

    # 9) return session + candidates (candidates query w/ joins happens in route)
    return sess, out_candidates



async def _load_session_with_candidates(db: AsyncSession, session_id: uuid.UUID) -> TonightSession:
    q = (
        select(TonightSession)
        .options(selectinload(TonightSession.candidates).selectinload(TonightSessionCandidate.watchlist_item).selectinload(WatchlistItem.title))
        .where(TonightSession.id == session_id)
    )
    s = (await db.execute(q)).scalar_one_or_none()
    if not s:
        raise ValueError("Session not found")
    return s


async def _assert_session_active(s: TonightSession) -> None:
    if s.status != "active":
        raise ValueError("Session is complete")
    

async def cast_vote(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    watchlist_item_id: uuid.UUID,
    vote: str,
    ) -> None:
    s = await _load_session_with_candidates(db, session_id)
    await assert_user_in_group(db, s.group_id, user_id)
    await _assert_session_active(s)

    allowed = {c.watchlist_item_id for c in s.candidates}
    if watchlist_item_id not in allowed:
        raise ValueError("watchlist_item_id is not in this session deck")

    q = select(TonightVote).where(TonightVote.session_id == session_id, TonightVote.user_id == user_id)
    existing = (await db.execute(q)).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if existing:
        existing.watchlist_item_id = watchlist_item_id
        existing.vote = vote
        existing.updated_at = now
        return

    v = TonightVote(
        session_id=session_id,
        user_id=user_id,
        watchlist_item_id=watchlist_item_id,
        vote=vote,
        updated_at=now,
    )
    db.add(v)


async def resolve_if_expired(db: AsyncSession, *, session_id: uuid.UUID) -> TonightSession:
    s = await _load_session_with_candidates(db, session_id)

    if s.status != "active":
        return s

    now = datetime.now(timezone.utc)
    if s.ends_at > now:
        return s

    winner_item_id = await _compute_winner(db, s)
    s.status = "complete"
    s.completed_at = now
    s.result_watchlist_item_id = winner_item_id
    return s


async def _compute_winner(db: AsyncSession, s: TonightSession) -> uuid.UUID:
    deck_item_ids = [c.watchlist_item_id for c in sorted(s.candidates, key=lambda x: x.position)]
    if not deck_item_ids:
        raise ValueError("Session has no candidates")

    # Aggregate votes
    q = (
        select(
            TonightVote.watchlist_item_id.label("item_id"),
            func.sum(sa.case((TonightVote.vote == "yes", 1), else_=0)).label("yes_count"),
            func.sum(sa.case((TonightVote.vote == "no", 1), else_=0)).label("no_count"),
        )
        .where(TonightVote.session_id == s.id)
        .group_by(TonightVote.watchlist_item_id)
    )
    rows = (await db.execute(q)).all()

    # Build map for deck items (missing rows => 0/0)
    stats = {item_id: {"yes": 0, "no": 0} for item_id in deck_item_ids}
    for item_id, yes_count, no_count in rows:
        if item_id in stats:
            stats[item_id]["yes"] = int(yes_count or 0)
            stats[item_id]["no"] = int(no_count or 0)

    # If nobody voted at all: deterministic random from deck
    if all(v["yes"] == 0 and v["no"] == 0 for v in stats.values()):
        rng = random.Random(str(s.id))
        return rng.choice(deck_item_ids)

    # winner = max YES
    max_yes = max(v["yes"] for v in stats.values())
    yes_tied = [item_id for item_id, v in stats.items() if v["yes"] == max_yes]

    if len(yes_tied) == 1:
        return yes_tied[0]

    # tie -> min NO among yes_tied
    min_no = min(stats[item_id]["no"] for item_id in yes_tied)
    no_tied = [item_id for item_id in yes_tied if stats[item_id]["no"] == min_no]

    if len(no_tied) == 1:
        return no_tied[0]

    # tie -> deterministic random (seed=session_id)
    rng = random.Random(str(s.id))
    return rng.choice(sorted(no_tied, key=lambda x: str(x)))


async def shuffle_and_complete(db: AsyncSession, *, session_id: uuid.UUID, user_id: uuid.UUID) -> TonightSession:
    s = await _load_session_with_candidates(db, session_id)
    await assert_user_in_group(db, s.group_id, user_id)
    await _assert_session_active(s)

    deck_item_ids = [c.watchlist_item_id for c in sorted(s.candidates, key=lambda x: x.position)]
    if not deck_item_ids:
        raise ValueError("Session has no candidates")

    rng = random.Random(str(s.id) + ":shuffle")
    winner = rng.choice(deck_item_ids)

    now = datetime.now(timezone.utc)
    s.status = "complete"
    s.completed_at = now
    s.result_watchlist_item_id = winner
    return s


async def get_session_state(db: AsyncSession, *, session_id: uuid.UUID, user_id: uuid.UUID) -> TonightSession:
    s = await _load_session_with_candidates(db, session_id)
    await assert_user_in_group(db, s.group_id, user_id)

    # Auto resolve if expired and still active
    if s.status == "active":
        now = datetime.now(timezone.utc)
        if s.ends_at <= now:
            await resolve_if_expired(db, session_id=session_id)

    # reload after possible mutation
    return await _load_session_with_candidates(db, session_id)
