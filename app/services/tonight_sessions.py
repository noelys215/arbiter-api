from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.group_membership import GroupMembership
from app.models.tonight_session import TonightSession
from app.models.tonight_session_candidate import TonightSessionCandidate
from app.models.watchlist_item import WatchlistItem
from app.schemas.tonight_constraints import TonightConstraints
from app.services.ai_constraints import ai_parse_constraints, ai_rerank_candidates


async def assert_user_in_group(db: AsyncSession, group_id: uuid.UUID, user_id: uuid.UUID) -> None:
    q = select(GroupMembership.id).where(
        GroupMembership.group_id == group_id,
        GroupMembership.user_id == user_id,
    )
    if (await db.execute(q)).scalar_one_or_none() is None:
        raise PermissionError("Not a member of this group")


def _apply_hard_filters(items: list[WatchlistItem], c: TonightConstraints) -> list[WatchlistItem]:
    out: list[WatchlistItem] = []

    for wi in items:
        t = wi.title

        # format hard filter
        if c.format != "any" and t.media_type != c.format:
            continue

        # max_runtime hard filter (only if runtime known)
        if c.max_runtime is not None and t.runtime_minutes is not None and t.runtime_minutes > c.max_runtime:
            continue

        # "avoid" hard filter (basic v1: string contains in title/overview)
        if c.avoid:
            hay = f"{t.name or ''} {t.overview or ''}".lower()
            blocked = any(a.lower() in hay for a in c.avoid)
            if blocked:
                continue

        out.append(wi)

    return out


def _baseline_pick(items: list[WatchlistItem], top_k: int, seed: str) -> list[WatchlistItem]:
    # deterministic shuffle based on seed
    rng = random.Random(seed)
    items2 = list(items)
    rng.shuffle(items2)
    return items2[:top_k]


async def create_tonight_session(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    constraints: TonightConstraints,
    text: str | None,
    duration_seconds: int,
    candidate_count: int,
) -> tuple[TonightSession, list[WatchlistItem], str | None]:
    await assert_user_in_group(db, group_id, user_id)

    baseline = constraints.model_copy(deep=True)

    # AI parse (optional)
    final_constraints = baseline
    if text and text.strip():
        parsed = await ai_parse_constraints(baseline=baseline, text=text)
        # enforce "narrow only" rules here
        final_constraints = baseline.model_copy(deep=True)
        final_constraints.free_text = parsed.free_text
        final_constraints.parsed_by_ai = parsed.parsed_by_ai
        final_constraints.ai_version = parsed.ai_version

        # moods/avoid can be merged (narrowing)
        final_constraints.moods = list({*(baseline.moods or []), *(parsed.moods or [])})
        final_constraints.avoid = list({*(baseline.avoid or []), *(parsed.avoid or [])})

        # energy can be filled if empty
        if final_constraints.energy is None:
            final_constraints.energy = parsed.energy

        # format can be filled if UI left "any"
        if baseline.format == "any" and parsed.format:
            final_constraints.format = parsed.format

        # max_runtime can be set if UI left null, or lowered (never raised)
        if baseline.max_runtime is None:
            final_constraints.max_runtime = parsed.max_runtime
        else:
            if parsed.max_runtime is not None:
                final_constraints.max_runtime = min(baseline.max_runtime, parsed.max_runtime)

        # re-validate canonical rules
        final_constraints = TonightConstraints.model_validate(final_constraints.model_dump())

    # Pool: watchlist + not snoozed + status=watchlist
    now = datetime.now(timezone.utc)
    q = (
        select(WatchlistItem)
        .options(selectinload(WatchlistItem.title))
        .where(WatchlistItem.group_id == group_id)
        .where(WatchlistItem.status == "watchlist")
        .where(sa.or_(WatchlistItem.snoozed_until.is_(None), WatchlistItem.snoozed_until <= now))
        .order_by(WatchlistItem.created_at.desc())
    )
    pool = (await db.execute(q)).scalars().all()

    # Hard filters are deterministic and enforced after AI parsing
    eligible = _apply_hard_filters(pool, final_constraints)

    # Preselect (top 30 from deterministic shuffle)
    pre = _baseline_pick(eligible, top_k=min(30, len(eligible)), seed=f"{group_id}:{user_id}:{int(now.timestamp())//60}")

    # Prepare rerank payload
    pre_payload = []
    for wi in pre:
        t = wi.title
        pre_payload.append(
            {
                "watchlist_item_id": str(wi.id),
                "title": t.name,
                "year": t.release_year,
                "media_type": t.media_type,
                "overview": t.overview,
            }
        )

    rerank = await ai_rerank_candidates(constraints=final_constraints, candidates=pre_payload, pick_n=candidate_count)

    # Map ordered ids -> WatchlistItem in that order, fall back to pre order
    by_id = {str(wi.id): wi for wi in pre}
    ordered: list[WatchlistItem] = [by_id[i] for i in rerank.ordered_ids if i in by_id]
    if len(ordered) < min(candidate_count, len(pre)):
        # append remaining in pre order, deterministic
        remaining = [wi for wi in pre if str(wi.id) not in set(rerank.ordered_ids)]
        ordered.extend(remaining)

    ordered = ordered[:candidate_count]

    # Create session + candidates rows
    ends_at = now + timedelta(seconds=duration_seconds)

    s = TonightSession(
        group_id=group_id,
        created_by_user_id=user_id,
        constraints=final_constraints.model_dump(),
        ends_at=ends_at,
        duration_seconds=duration_seconds,
        candidate_count=candidate_count,
        ai_why=rerank.why,
        ai_used=bool(final_constraints.parsed_by_ai or rerank.why),
    )
    db.add(s)
    await db.flush()

    for idx, wi in enumerate(ordered):
        db.add(
            TonightSessionCandidate(
                session_id=s.id,
                watchlist_item_id=wi.id,
                position=idx,
            )
        )

    return s, ordered, rerank.why
