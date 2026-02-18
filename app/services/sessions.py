from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.group_membership import GroupMembership
from app.models.tonight_session import TonightSession
from app.models.tonight_session_candidate import TonightSessionCandidate
from app.models.tonight_vote import TonightVote
from app.models.watchlist_item import WatchlistItem
from app.schemas.tonight_constraints import TonightConstraints
from app.services.ai import AIError, ai_parse_constraints, ai_rerank_candidates
from app.services.tmdb import fetch_tmdb_title_taxonomy
from app.services.watchlist import assert_user_in_group


def _canonicalize_constraints(payload: dict) -> TonightConstraints:
    # Force canonical normalization using your Phase 5.1 model
    c = TonightConstraints.model_validate(payload or {})
    return c


def _stable_seed(value: str) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


_WORD_RE = re.compile(r"[a-z0-9]+")


def _norm_text(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def _tokenize(value: str | None) -> set[str]:
    return set(_WORD_RE.findall(_norm_text(value)))


TAG_PROFILES: dict[str, dict[str, set[str]]] = {
    "mind-bender": {
        "genres": {"science fiction", "mystery", "thriller", "fantasy"},
        "keywords": {
            "mind-bending",
            "time travel",
            "parallel universe",
            "plot twist",
            "twist ending",
            "psychological",
            "surreal",
            "simulation",
            "alternate reality",
        },
    },
    "cozy": {
        "genres": {"family", "animation", "comedy", "romance"},
        "keywords": {
            "comfort",
            "cozy",
            "feel good",
            "heartwarming",
            "slice of life",
            "friendship",
            "small town",
        },
    },
    "feel-good": {
        "genres": {"comedy", "family", "music", "romance"},
        "keywords": {
            "feel good",
            "uplifting",
            "inspirational",
            "heartwarming",
            "friendship",
            "hope",
            "optimism",
        },
    },
    "dark comedy": {
        "genres": {"comedy", "crime", "thriller", "drama"},
        "keywords": {
            "dark comedy",
            "black comedy",
            "satire",
            "absurdism",
            "offbeat",
            "irony",
            "morbid humor",
        },
    },
    "thrilling": {
        "genres": {"thriller", "action", "crime", "mystery"},
        "keywords": {
            "suspense",
            "chase",
            "intense",
            "high stakes",
            "edge of your seat",
            "cat and mouse",
            "conspiracy",
        },
    },
    "slow burn": {
        "genres": {"drama", "mystery", "thriller"},
        "keywords": {
            "slow burn",
            "atmospheric",
            "character study",
            "brooding",
            "moody",
            "simmering tension",
        },
    },
    "heartfelt": {
        "genres": {"drama", "romance", "family"},
        "keywords": {
            "emotional",
            "heartfelt",
            "tender",
            "healing",
            "family relationship",
            "grief",
            "growth",
        },
    },
    "epic": {
        "genres": {"adventure", "action", "fantasy", "war", "history"},
        "keywords": {
            "epic",
            "grand scale",
            "quest",
            "legend",
            "battle",
            "saga",
            "world building",
        },
    },
    "nostalgic": {
        "genres": {"family", "comedy", "drama"},
        "keywords": {
            "nostalgia",
            "coming of age",
            "retro",
            "throwback",
            "childhood",
            "memory",
            "period piece",
        },
    },
    "romantic": {
        "genres": {"romance", "drama", "comedy"},
        "keywords": {
            "romance",
            "love",
            "relationship",
            "date night",
            "heartbreak",
            "meet cute",
        },
    },
    "high energy": {
        "genres": {"action", "adventure", "music", "crime"},
        "keywords": {
            "adrenaline",
            "fast paced",
            "high octane",
            "chaos",
            "race against time",
            "heist",
        },
    },
    "cerebral": {
        "genres": {"mystery", "science fiction", "drama"},
        "keywords": {
            "cerebral",
            "philosophical",
            "intellectual",
            "thought provoking",
            "existential",
            "psychological",
        },
    },
    "scary": {
        "genres": {"horror", "thriller", "mystery"},
        "keywords": {
            "horror",
            "supernatural",
            "haunted",
            "monster",
            "slasher",
            "demon",
            "paranormal",
        },
    },
    "documentary": {
        "genres": {"documentary"},
        "keywords": {"documentary", "true story", "biography", "investigation"},
    },
    "animated": {
        "genres": {"animation", "family", "fantasy", "adventure"},
        "keywords": {"animated", "anime", "cartoon", "pixar", "dreamworks"},
    },
    "under 30 min": {
        "genres": set(),
        "keywords": {
            "under 30 min",
            "under 30 mins",
            "under 30 minutes",
            "short episode",
            "quick watch",
        },
    },
    "under 15 min": {
        "genres": set(),
        "keywords": {
            "under 15 min",
            "under 15 mins",
            "under 15 minutes",
            "micro episode",
            "very short",
        },
    },
}

TAG_GENRE_IDS: dict[str, set[int]] = {
    "mind-bender": {878, 10765, 9648, 53, 14},
    "cozy": {10751, 16, 35, 10762, 10749},
    "feel-good": {35, 10751, 10402, 10749},
    "dark comedy": {35, 80, 53, 18},
    "thrilling": {53, 28, 80, 9648, 10759},
    "slow burn": {18, 9648, 53},
    "heartfelt": {18, 10749, 10751},
    "epic": {12, 28, 14, 10759, 10768, 10752, 36},
    "nostalgic": {10751, 35, 18},
    "romantic": {10749, 18, 35},
    "high energy": {28, 12, 10402, 80, 10759},
    "cerebral": {9648, 878, 18, 10765},
    "scary": {27, 53, 9648},
    "documentary": {99},
    "animated": {16, 10762, 10751, 14, 12, 10765},
}

RUNTIME_TAG_RULES: dict[str, dict[str, Any]] = {
    "under 30 min": {"max_minutes": 30, "media_types": {"tv"}},
    "under 15 min": {"max_minutes": 15, "media_types": {"tv"}},
}


TAG_ALIASES: dict[str, str] = {
    "mind bender": "mind-bender",
    "mind-bending": "mind-bender",
    "mind bending": "mind-bender",
    "cozy": "cozy",
    "feel good": "feel-good",
    "feel-good": "feel-good",
    "dark comedy": "dark comedy",
    "black comedy": "dark comedy",
    "thrilling": "thrilling",
    "thriller": "thrilling",
    "slow burn": "slow burn",
    "heartfelt": "heartfelt",
    "epic": "epic",
    "nostalgic": "nostalgic",
    "romantic": "romantic",
    "high energy": "high energy",
    "energetic": "high energy",
    "cerebral": "cerebral",
    "scary": "scary",
    "horror": "scary",
    "documentary": "documentary",
    "doc": "documentary",
    "animated": "animated",
    "animation": "animated",
    "under 30 min": "under 30 min",
    "under 30 mins": "under 30 min",
    "under 30 minutes": "under 30 min",
    "under 15 min": "under 15 min",
    "under 15 mins": "under 15 min",
    "under 15 minutes": "under 15 min",
    "quick episodes": "under 30 min",
    "quick episode": "under 30 min",
    "short episodes": "under 30 min",
    "very short episodes": "under 15 min",
    "micro episodes": "under 15 min",
}

TMDB_GENRE_DEFINITIONS: dict[str, tuple[set[int], set[str], set[str]]] = {
    "action": (
        {28, 10759},
        {"action", "action & adventure"},
        {"action"},
    ),
    "adventure": (
        {12, 10759},
        {"adventure", "action & adventure"},
        {"adventure"},
    ),
    "action & adventure": (
        {10759, 28, 12},
        {"action & adventure", "action", "adventure"},
        {"action & adventure", "action and adventure"},
    ),
    "animation": (
        {16},
        {"animation"},
        {"animation", "animated"},
    ),
    "comedy": (
        {35},
        {"comedy"},
        {"comedy"},
    ),
    "crime": (
        {80},
        {"crime"},
        {"crime"},
    ),
    "documentary": (
        {99},
        {"documentary"},
        {"documentary", "doc"},
    ),
    "drama": (
        {18},
        {"drama"},
        {"drama"},
    ),
    "family": (
        {10751},
        {"family", "kids"},
        {"family"},
    ),
    "fantasy": (
        {14, 10765},
        {"fantasy", "sci-fi & fantasy"},
        {"fantasy"},
    ),
    "history": (
        {36},
        {"history"},
        {"history", "historical"},
    ),
    "horror": (
        {27},
        {"horror"},
        {"horror"},
    ),
    "kids": (
        {10762},
        {"kids", "family"},
        {"kids", "children"},
    ),
    "music": (
        {10402},
        {"music"},
        {"music", "musical"},
    ),
    "mystery": (
        {9648},
        {"mystery"},
        {"mystery"},
    ),
    "news": (
        {10763},
        {"news"},
        {"news"},
    ),
    "reality": (
        {10764},
        {"reality"},
        {"reality", "reality tv"},
    ),
    "romance": (
        {10749},
        {"romance"},
        {"romance", "romantic"},
    ),
    "science fiction": (
        {878, 10765},
        {"science fiction", "sci-fi & fantasy"},
        {"science fiction", "sci-fi", "sci fi", "scifi"},
    ),
    "sci-fi & fantasy": (
        {10765, 878, 14},
        {"sci-fi & fantasy", "science fiction", "fantasy"},
        {"sci-fi & fantasy", "sci fi & fantasy", "sci-fi and fantasy"},
    ),
    "soap": (
        {10766},
        {"soap"},
        {"soap", "soap opera"},
    ),
    "talk": (
        {10767},
        {"talk"},
        {"talk", "talk show"},
    ),
    "tv movie": (
        {10770},
        {"tv movie"},
        {"tv movie", "television movie"},
    ),
    "thriller": (
        {53},
        {"thriller"},
        {"thriller"},
    ),
    "war": (
        {10752, 10768},
        {"war", "war & politics"},
        {"war"},
    ),
    "war & politics": (
        {10768, 10752},
        {"war & politics", "war", "history"},
        {"war & politics", "war and politics"},
    ),
    "western": (
        {37},
        {"western"},
        {"western"},
    ),
}

for canonical, (genre_ids, genres, aliases) in TMDB_GENRE_DEFINITIONS.items():
    profile = TAG_PROFILES.setdefault(canonical, {"genres": set(), "keywords": set()})
    profile["genres"].update(genres)
    profile["keywords"].update(aliases)
    profile["keywords"].add(canonical)
    TAG_GENRE_IDS.setdefault(canonical, set()).update(genre_ids)
    TAG_ALIASES[canonical] = canonical
    for alias in aliases:
        TAG_ALIASES[alias] = canonical


def _display_mood_name(value: str) -> str:
    if value == "mind-bender":
        return "Mind-Bender"
    if value == "feel-good":
        return "Feel-Good"
    if value == "high energy":
        return "High Energy"
    if value == "dark comedy":
        return "Dark Comedy"
    if value == "slow burn":
        return "Slow Burn"
    if value == "tv movie":
        return "TV Movie"
    if value == "science fiction":
        return "Science Fiction"
    if value == "sci-fi & fantasy":
        return "Sci-Fi & Fantasy"
    if value == "action & adventure":
        return "Action & Adventure"
    if value == "war & politics":
        return "War & Politics"
    if value == "under 30 min":
        return "Under 30 Mins"
    if value == "under 15 min":
        return "Under 15 Mins"
    return value.title()


def _canonicalize_mood(value: str) -> str | None:
    normalized = _norm_text(value)
    if not normalized:
        return None
    if normalized in TAG_ALIASES:
        return TAG_ALIASES[normalized]

    compact = normalized.replace("-", " ")
    if compact in TAG_ALIASES:
        return TAG_ALIASES[compact]

    return None


def _derive_requested_moods(constraints: TonightConstraints) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()

    for mood in constraints.moods:
        canonical = _canonicalize_mood(mood)
        if canonical and canonical not in seen:
            seen.add(canonical)
            selected.append(canonical)

    free_text = _norm_text(constraints.free_text)
    if free_text:
        for alias, canonical in TAG_ALIASES.items():
            if alias in free_text and canonical not in seen:
                seen.add(canonical)
                selected.append(canonical)

    return selected


async def _build_item_tag_matches(
    *,
    items: list[WatchlistItem],
    requested_moods: list[str],
) -> dict[uuid.UUID, list[str]]:
    if not items or not requested_moods:
        return {}

    taxonomy_map: dict[uuid.UUID, tuple[set[str], set[str], set[int]]] = {}

    async def _load_tmdb_taxonomy(it: WatchlistItem):
        t = it.title
        if t.source != "tmdb" or not t.source_id:
            return
        try:
            tmdb_id = int(t.source_id)
        except (TypeError, ValueError):
            return
        taxonomy = await fetch_tmdb_title_taxonomy(
            tmdb_id=tmdb_id,
            media_type=t.media_type,
        )
        # Backward-compatible unpacking for tests that monkeypatch the TMDB fetcher.
        if isinstance(taxonomy, tuple) and len(taxonomy) == 3:
            genres, keywords, genre_ids = taxonomy
        else:
            genres, keywords = taxonomy  # type: ignore[misc]
            genre_ids = set()
        taxonomy_map[it.id] = (genres, keywords, genre_ids)

    await asyncio.gather(*[_load_tmdb_taxonomy(it) for it in items])

    matched: dict[uuid.UUID, list[str]] = {}
    for it in items:
        t = it.title
        text_blob = " ".join([_norm_text(t.name), _norm_text(t.overview)])
        text_tokens = _tokenize(text_blob)
        tmdb_genres, tmdb_keywords, tmdb_genre_ids = taxonomy_map.get(
            it.id,
            (set(), set(), set()),
        )

        score = 0
        hits: list[str] = []
        for mood in requested_moods:
            runtime_rule = RUNTIME_TAG_RULES.get(mood)
            if runtime_rule:
                max_minutes = runtime_rule.get("max_minutes")
                media_types = runtime_rule.get("media_types") or set()
                runtime_minutes = t.runtime_minutes
                media_ok = not media_types or t.media_type in media_types
                if (
                    isinstance(max_minutes, int)
                    and max_minutes > 0
                    and isinstance(runtime_minutes, int)
                    and runtime_minutes > 0
                    and runtime_minutes <= max_minutes
                    and media_ok
                ):
                    score += 8
                    hits.append(mood)
                continue

            profile = TAG_PROFILES.get(mood)
            if not profile:
                continue

            mood_score = 0
            genre_id_hits = tmdb_genre_ids & TAG_GENRE_IDS.get(mood, set())
            if genre_id_hits:
                mood_score += 5

            genre_hits = tmdb_genres & profile["genres"]
            if genre_hits:
                mood_score += 3

            keyword_hits = tmdb_keywords & profile["keywords"]
            if keyword_hits:
                mood_score += min(6, len(keyword_hits) * 2)

            profile_tokens = set()
            for kw in profile["keywords"]:
                profile_tokens.update(_tokenize(kw))
            text_hits = profile_tokens & text_tokens
            if text_hits:
                mood_score += min(3, len(text_hits))

            if mood_score > 0:
                score += mood_score
                hits.append(mood)

        if score > 0 and hits:
            matched[it.id] = hits

    return matched


def _sort_with_mood_matches(
    *,
    items: list[WatchlistItem],
    matched: dict[uuid.UUID, list[str]],
    seed: int,
) -> list[WatchlistItem]:
    if not items:
        return []

    def _score(it: WatchlistItem) -> int:
        return len(matched.get(it.id, []))

    any_positive = any(_score(it) > 0 for it in items)
    if any_positive:
        items = [it for it in items if _score(it) > 0]

    def _tie_break(it: WatchlistItem) -> int:
        h = hashlib.sha256(f"{seed}:{it.id}".encode("utf-8")).digest()
        return int.from_bytes(h[:4], "big")

    return sorted(items, key=lambda it: (-_score(it), _tie_break(it)))


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


SESSION_RUNTIME_KEY = "__session_runtime_v1"
ROUND_TIMER_SECONDS = 60


@dataclass
class SessionStateView:
    session: TonightSession
    candidates: list[TonightSessionCandidate]
    phase: str
    round: int
    user_locked: bool
    user_seconds_left: int
    mutual_candidate_ids: list[uuid.UUID]
    shortlist: list[uuid.UUID]
    tie_break_required: bool
    tie_break_candidate_ids: list[uuid.UUID]
    ended_by_leader: bool


def _dedupe_uuid_sequence(values: list[uuid.UUID]) -> list[uuid.UUID]:
    seen: set[uuid.UUID] = set()
    out: list[uuid.UUID] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_uuid_list(values: Any) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    if not isinstance(values, list):
        return out
    for v in values:
        try:
            out.append(uuid.UUID(str(v)))
        except (TypeError, ValueError):
            continue
    return out


def _session_base_candidate_ids(s: TonightSession) -> list[uuid.UUID]:
    return [c.watchlist_item_id for c in sorted(s.candidates, key=lambda x: x.position)]


def _runtime_round_state(runtime: dict[str, Any], round_num: int) -> dict[str, Any]:
    rounds = runtime.setdefault("rounds", {})
    key = str(round_num)
    state = rounds.get(key)
    if not isinstance(state, dict):
        state = {}
        rounds[key] = state

    started = state.get("user_started_at")
    if not isinstance(started, dict):
        started = {}
        state["user_started_at"] = started

    locked = state.get("user_locked_at")
    if not isinstance(locked, dict):
        locked = {}
        state["user_locked_at"] = locked

    votes = state.get("votes")
    if not isinstance(votes, dict):
        votes = {}
        state["votes"] = votes

    return state


def _ensure_runtime(s: TonightSession) -> dict[str, Any]:
    constraints = dict(s.constraints or {})
    runtime_raw = constraints.get(SESSION_RUNTIME_KEY)
    if isinstance(runtime_raw, dict):
        # Work on a detached copy so in-place mutations don't bypass SQLAlchemy dirty tracking.
        runtime = json.loads(json.dumps(runtime_raw))
    else:
        runtime = {}

    runtime.setdefault("version", 1)
    if not isinstance(runtime.get("round"), int):
        runtime["round"] = 1
    if not isinstance(runtime.get("phase"), str):
        runtime["phase"] = "swiping"

    initial_ids = _parse_uuid_list(runtime.get("initial_candidate_ids"))
    if not initial_ids:
        initial_ids = _session_base_candidate_ids(s)
    runtime["initial_candidate_ids"] = [str(item_id) for item_id in initial_ids]

    mutual_ids = _parse_uuid_list(runtime.get("mutual_candidate_ids"))
    runtime["mutual_candidate_ids"] = [str(item_id) for item_id in mutual_ids]
    tie_break_ids = _parse_uuid_list(runtime.get("tie_break_candidate_ids"))
    runtime["tie_break_candidate_ids"] = [str(item_id) for item_id in tie_break_ids]
    runtime["tie_break_required"] = bool(runtime.get("tie_break_required"))
    runtime["ended_by_leader"] = bool(runtime.get("ended_by_leader"))

    _runtime_round_state(runtime, 1)
    _runtime_round_state(runtime, 2)
    return runtime


def _persist_runtime(s: TonightSession, runtime: dict[str, Any]) -> None:
    constraints = dict(s.constraints or {})
    constraints[SESSION_RUNTIME_KEY] = runtime
    s.constraints = constraints


def _candidate_ids_for_round(s: TonightSession, runtime: dict[str, Any], round_num: int) -> list[uuid.UUID]:
    if round_num == 1:
        ids = _parse_uuid_list(runtime.get("initial_candidate_ids"))
        return ids if ids else _session_base_candidate_ids(s)
    return _parse_uuid_list(runtime.get("mutual_candidate_ids"))


def _seed_round_timers(
    runtime: dict[str, Any],
    *,
    round_num: int,
    member_ids: list[uuid.UUID],
    now: datetime,
) -> None:
    state = _runtime_round_state(runtime, round_num)
    started = state["user_started_at"]
    now_iso = _to_iso(now)
    for member_id in member_ids:
        key = str(member_id)
        if key not in started:
            started[key] = now_iso


def _ensure_user_timer(runtime: dict[str, Any], *, round_num: int, user_id: uuid.UUID, now: datetime) -> None:
    state = _runtime_round_state(runtime, round_num)
    started = state["user_started_at"]
    key = str(user_id)
    if key not in started:
        started[key] = _to_iso(now)


def _lock_user(runtime: dict[str, Any], *, round_num: int, user_id: uuid.UUID, now: datetime) -> None:
    state = _runtime_round_state(runtime, round_num)
    locked = state["user_locked_at"]
    key = str(user_id)
    if key not in locked:
        locked[key] = _to_iso(now)


def _is_user_locked(runtime: dict[str, Any], *, round_num: int, user_id: uuid.UUID) -> bool:
    state = _runtime_round_state(runtime, round_num)
    return str(user_id) in state["user_locked_at"]


def _user_votes_for_round(runtime: dict[str, Any], *, round_num: int, user_id: uuid.UUID) -> dict[str, str]:
    state = _runtime_round_state(runtime, round_num)
    votes = state["votes"]
    key = str(user_id)
    raw = votes.get(key)
    if not isinstance(raw, dict):
        raw = {}
        votes[key] = raw
    return raw


def _seconds_left_for_user(
    runtime: dict[str, Any],
    *,
    round_num: int,
    user_id: uuid.UUID,
    now: datetime,
) -> int:
    if _is_user_locked(runtime, round_num=round_num, user_id=user_id):
        return 0
    state = _runtime_round_state(runtime, round_num)
    started = _from_iso(state["user_started_at"].get(str(user_id)))
    if started is None:
        return ROUND_TIMER_SECONDS
    elapsed = max(0, int((now - started).total_seconds()))
    return max(0, ROUND_TIMER_SECONDS - elapsed)


def _apply_user_auto_lock(
    runtime: dict[str, Any],
    *,
    round_num: int,
    user_id: uuid.UUID,
    candidate_ids: list[uuid.UUID],
    now: datetime,
) -> bool:
    if _is_user_locked(runtime, round_num=round_num, user_id=user_id):
        return True

    if _seconds_left_for_user(runtime, round_num=round_num, user_id=user_id, now=now) <= 0:
        _lock_user(runtime, round_num=round_num, user_id=user_id, now=now)
        return True

    if candidate_ids:
        user_votes = _user_votes_for_round(runtime, round_num=round_num, user_id=user_id)
        if all(str(item_id) in user_votes for item_id in candidate_ids):
            _lock_user(runtime, round_num=round_num, user_id=user_id, now=now)
            return True

    return _is_user_locked(runtime, round_num=round_num, user_id=user_id)


async def _group_member_ids(db: AsyncSession, *, group_id: uuid.UUID) -> list[uuid.UUID]:
    q = select(GroupMembership.user_id).where(GroupMembership.group_id == group_id)
    rows = (await db.execute(q)).all()
    member_ids = [row[0] for row in rows if row and row[0]]
    if not member_ids:
        return []
    return sorted(set(member_ids), key=str)


def _compute_round_winner(
    *,
    session_id: uuid.UUID,
    round_num: int,
    candidate_ids: list[uuid.UUID],
    round_votes: dict[str, dict[str, str]],
) -> uuid.UUID:
    if not candidate_ids:
        raise ValueError("Session has no candidates")

    stats = {str(item_id): {"yes": 0, "no": 0} for item_id in candidate_ids}

    for user_votes in round_votes.values():
        if not isinstance(user_votes, dict):
            continue
        for item_id, vote in user_votes.items():
            if item_id not in stats:
                continue
            if vote == "yes":
                stats[item_id]["yes"] += 1
            elif vote == "no":
                stats[item_id]["no"] += 1

    if all(v["yes"] == 0 and v["no"] == 0 for v in stats.values()):
        rng = random.Random(f"{session_id}:round{round_num}")
        selected = rng.choice(candidate_ids)
        return selected

    max_yes = max(v["yes"] for v in stats.values())
    yes_tied = [item_id for item_id, v in stats.items() if v["yes"] == max_yes]

    if len(yes_tied) == 1:
        return uuid.UUID(yes_tied[0])

    min_no = min(stats[item_id]["no"] for item_id in yes_tied)
    no_tied = [item_id for item_id in yes_tied if stats[item_id]["no"] == min_no]

    if len(no_tied) == 1:
        return uuid.UUID(no_tied[0])

    rng = random.Random(f"{session_id}:round{round_num}:tie")
    return uuid.UUID(rng.choice(sorted(no_tied)))


def _compute_mutual_ids(
    runtime: dict[str, Any],
    *,
    member_ids: list[uuid.UUID],
) -> list[uuid.UUID]:
    round1 = _runtime_round_state(runtime, 1)
    votes = round1["votes"]
    if not member_ids:
        return []

    yes_sets: list[set[str]] = []
    for member_id in member_ids:
        raw_votes = votes.get(str(member_id), {})
        if not isinstance(raw_votes, dict):
            raw_votes = {}
        yes_sets.append({item_id for item_id, vote in raw_votes.items() if vote == "yes"})

    if not yes_sets:
        return []

    mutual = set.intersection(*yes_sets) if yes_sets else set()
    if not mutual:
        return []

    ordered = [
        item_id
        for item_id in runtime.get("initial_candidate_ids", [])
        if isinstance(item_id, str) and item_id in mutual
    ]
    return _parse_uuid_list(ordered)


async def _advance_rounds_if_needed(
    db: AsyncSession,
    *,
    s: TonightSession,
    runtime: dict[str, Any],
    now: datetime,
) -> None:
    if s.status != "active":
        return

    member_ids = await _group_member_ids(db, group_id=s.group_id)
    current_round = int(runtime.get("round") or 1)
    candidate_ids = _candidate_ids_for_round(s, runtime, current_round)
    _seed_round_timers(runtime, round_num=current_round, member_ids=member_ids, now=now)

    locked_count = 0
    for member_id in member_ids:
        if _apply_user_auto_lock(
            runtime,
            round_num=current_round,
            user_id=member_id,
            candidate_ids=candidate_ids,
            now=now,
        ):
            locked_count += 1

    all_locked = member_ids and locked_count == len(member_ids)
    if not all_locked:
        return

    if current_round == 1:
        winner, tied_ids = await _compute_winner_or_tie(db, s)
        if winner is not None:
            runtime["tie_break_required"] = False
            runtime["tie_break_candidate_ids"] = []
            s.status = "complete"
            s.completed_at = now
            s.result_watchlist_item_id = winner
            return

        runtime["phase"] = "tiebreak"
        runtime["tie_break_required"] = True
        runtime["tie_break_candidate_ids"] = [str(item_id) for item_id in tied_ids]
        return

    round2_state = _runtime_round_state(runtime, 2)
    round2_ids = _candidate_ids_for_round(s, runtime, 2)
    if not round2_ids:
        round2_ids = _candidate_ids_for_round(s, runtime, 1)

    winner = _compute_round_winner(
        session_id=s.id,
        round_num=2,
        candidate_ids=round2_ids,
        round_votes=round2_state["votes"],
    )
    s.status = "complete"
    s.completed_at = now
    s.result_watchlist_item_id = winner


async def _load_active_group_session(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
) -> TonightSession | None:
    q = (
        select(TonightSession)
        .options(
            selectinload(TonightSession.candidates)
            .selectinload(TonightSessionCandidate.watchlist_item)
            .selectinload(WatchlistItem.title)
        )
        .where(TonightSession.group_id == group_id, TonightSession.status == "active")
        .order_by(TonightSession.created_at.desc())
        .limit(1)
    )
    return (await db.execute(q)).scalar_one_or_none()


async def _generate_user_deck_items(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    user_id: uuid.UUID | None,
    constraints_payload: dict,
    text: str | None,
    candidate_count: int,
    now: datetime,
) -> tuple[list[WatchlistItem], TonightConstraints, bool, str | None]:
    base = _canonicalize_constraints(constraints_payload)
    refined = base
    if text and text.strip():
        try:
            refined = await ai_parse_constraints(baseline=base, text=text.strip())
        except AIError:
            refined = base
            refined.free_text = text.strip()
            refined.parsed_by_ai = False
            refined.ai_version = None
    else:
        refined.free_text = (text or "").strip()

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
    filtered = _apply_hard_filters(eligible, refined)
    if not filtered:
        return [], refined, False, None

    seed_source = (
        f"{group_id}:{(str(user_id) if user_id else 'anon')}:{now.date().isoformat()}:"
        f"{json.dumps(refined.model_dump(), sort_keys=True)}"
    )
    seed = _stable_seed(seed_source)
    requested_moods = _derive_requested_moods(refined)
    matched_tags_map: dict[uuid.UUID, list[str]] = {}
    if requested_moods:
        matched_tags_map = await _build_item_tag_matches(
            items=filtered,
            requested_moods=requested_moods,
        )
        ranked = _sort_with_mood_matches(
            items=filtered,
            matched=matched_tags_map,
            seed=seed,
        )
        prelim = ranked[:30]
    else:
        prelim = _deterministic_shuffle(filtered, seed=seed)[:30]

    final_n = min(candidate_count, len(prelim))
    if final_n <= 0:
        return [], refined, False, None

    candidates_payload: list[dict[str, Any]] = []
    for it in prelim:
        t = it.title
        candidates_payload.append(
            {
                "id": str(it.id),
                "title": t.name,
                "release_year": t.release_year,
                "media_type": t.media_type,
                "runtime_minutes": t.runtime_minutes,
                "overview": t.overview,
            }
        )

    final_order: list[WatchlistItem] = list(prelim[:final_n])
    ai_why: str | None = None
    ai_used = False
    if final_n > 1:
        try:
            rerank = await ai_rerank_candidates(constraints=refined, candidates=candidates_payload)
            by_id = {str(it.id): it for it in prelim}
            valid_ids = [item_id for item_id in rerank.ordered_ids if item_id in by_id]
            min_valid = min(3, final_n)
            if len(valid_ids) >= min_valid and len(valid_ids) >= (final_n // 2 + 1):
                seen: set[uuid.UUID] = set()
                ordered: list[WatchlistItem] = []
                for item_id in valid_ids:
                    it = by_id.get(item_id)
                    if not it or it.id in seen:
                        continue
                    seen.add(it.id)
                    ordered.append(it)
                    if len(ordered) == final_n:
                        break
                if len(ordered) < final_n:
                    for it in prelim:
                        if it.id in seen:
                            continue
                        seen.add(it.id)
                        ordered.append(it)
                        if len(ordered) == final_n:
                            break
                final_order = ordered
                ai_used = True
                ai_why = rerank.why
        except AIError:
            pass

    return final_order, refined, ai_used, ai_why


def _runtime_collecting_state(runtime: dict[str, Any]) -> dict[str, Any]:
    collecting = runtime.get("collecting")
    if not isinstance(collecting, dict):
        collecting = {}
        runtime["collecting"] = collecting

    user_decks = collecting.get("user_decks")
    if not isinstance(user_decks, dict):
        user_decks = {}
        collecting["user_decks"] = user_decks

    user_constraints = collecting.get("user_constraints")
    if not isinstance(user_constraints, dict):
        user_constraints = {}
        collecting["user_constraints"] = user_constraints

    dealt_at = collecting.get("user_dealt_at")
    if not isinstance(dealt_at, dict):
        dealt_at = {}
        collecting["user_dealt_at"] = dealt_at

    user_ai = collecting.get("user_ai")
    if not isinstance(user_ai, dict):
        user_ai = {}
        collecting["user_ai"] = user_ai

    joined_at = collecting.get("user_joined_at")
    if not isinstance(joined_at, dict):
        joined_at = {}
        collecting["user_joined_at"] = joined_at

    return collecting


async def _replace_session_candidates(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    candidate_ids: list[uuid.UUID],
    notes_by_item_id: dict[uuid.UUID, str] | None = None,
) -> list[TonightSessionCandidate]:
    await db.execute(
        sa.delete(TonightSessionCandidate).where(TonightSessionCandidate.session_id == session_id)
    )
    rows: list[TonightSessionCandidate] = []
    for pos, item_id in enumerate(candidate_ids):
        row = TonightSessionCandidate(
            session_id=session_id,
            watchlist_item_id=item_id,
            position=pos,
            ai_note=(notes_by_item_id or {}).get(item_id),
        )
        db.add(row)
        rows.append(row)
    await db.flush()
    return rows


async def _finalize_collecting_to_swipe(
    db: AsyncSession,
    *,
    s: TonightSession,
    runtime: dict[str, Any],
    member_ids: list[uuid.UUID],
    now: datetime,
) -> list[TonightSessionCandidate]:
    collecting = _runtime_collecting_state(runtime)
    user_decks = collecting["user_decks"]
    user_constraints = collecting["user_constraints"]
    user_dealt_at = collecting["user_dealt_at"]
    user_ai = collecting["user_ai"]

    for member_id in member_ids:
        key = str(member_id)
        raw = user_decks.get(key)
        existing = _parse_uuid_list(raw) if isinstance(raw, list) else []
        if existing:
            continue
        fallback_items, _, ai_used, ai_why = await _generate_user_deck_items(
            db,
            group_id=s.group_id,
            user_id=member_id,
            constraints_payload={},
            text=None,
            candidate_count=max(1, int(s.candidate_count or 12)),
            now=now,
        )
        user_decks[key] = [str(item.id) for item in fallback_items]
        user_ai[key] = {"used": bool(ai_used), "why": ai_why}
        if key not in user_dealt_at:
            user_dealt_at[key] = _to_iso(now)

    ordered_ids: list[uuid.UUID] = []
    moods_by_item: dict[uuid.UUID, set[str]] = {}
    for member_id in member_ids:
        member_key = str(member_id)
        raw = user_decks.get(member_key)
        if isinstance(raw, list):
            item_ids = _parse_uuid_list(raw)
            ordered_ids.extend(item_ids)
            raw_constraints = user_constraints.get(member_key)
            mood_values = raw_constraints.get("moods") if isinstance(raw_constraints, dict) else []
            canonical_moods = [
                canonical
                for canonical in (
                    _canonicalize_mood(mood) for mood in mood_values if isinstance(mood, str)
                )
                if canonical
            ]
            if canonical_moods:
                for item_id in item_ids:
                    bucket = moods_by_item.setdefault(item_id, set())
                    bucket.update(canonical_moods)

    if not ordered_ids:
        ordered_ids = [c.watchlist_item_id for c in sorted(s.candidates, key=lambda c: c.position)]

    combined = _dedupe_uuid_sequence(ordered_ids)
    notes_by_item_id: dict[uuid.UUID, str] = {}
    for item_id, moods in moods_by_item.items():
        if not moods:
            continue
        display = " + ".join(_display_mood_name(mood) for mood in sorted(moods)[:2])
        notes_by_item_id[item_id] = f"Matches: {display}"

    any_ai_used = False
    first_ai_why: str | None = None
    for member_id in member_ids:
        raw = user_ai.get(str(member_id))
        if not isinstance(raw, dict):
            continue
        if bool(raw.get("used")):
            any_ai_used = True
            why = raw.get("why")
            if not first_ai_why and isinstance(why, str) and why.strip():
                first_ai_why = why.strip()
    s.ai_used = any_ai_used
    s.ai_why = first_ai_why

    rows = await _replace_session_candidates(
        db,
        session_id=s.id,
        candidate_ids=combined,
        notes_by_item_id=notes_by_item_id,
    )

    runtime["phase"] = "swiping"
    runtime["round"] = 1
    runtime["initial_candidate_ids"] = [str(item_id) for item_id in combined]
    runtime["mutual_candidate_ids"] = []
    runtime["tie_break_required"] = False
    runtime["tie_break_candidate_ids"] = []
    runtime["ended_by_leader"] = False
    runtime["setup_ends_at"] = collecting.get("ends_at")

    rounds = runtime.setdefault("rounds", {})
    rounds["1"] = {"user_started_at": {}, "user_locked_at": {}, "votes": {}}
    rounds["2"] = {"user_started_at": {}, "user_locked_at": {}, "votes": {}}
    _seed_round_timers(runtime, round_num=1, member_ids=member_ids, now=now)

    s.ends_at = now + timedelta(seconds=ROUND_TIMER_SECONDS)
    s.duration_seconds = ROUND_TIMER_SECONDS
    return rows


async def create_tonight_session(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    constraints_payload: dict,
    text: str | None,
    confirm_ready: bool | None,
    duration_seconds: int,
    candidate_count: int,
) -> tuple[TonightSession, list[TonightSessionCandidate], list[uuid.UUID]]:
    await assert_user_in_group(db, group_id, user_id)
    now = datetime.now(timezone.utc)
    member_ids = await _group_member_ids(db, group_id=group_id)
    has_user_preferences = bool(
        (constraints_payload and len(constraints_payload.keys()) > 0)
        or (text and text.strip())
    )

    active = await _load_active_group_session(db, group_id=group_id)
    if active is None:
        collecting_hold_until = now + timedelta(hours=24)
        sess = TonightSession(
            group_id=group_id,
            created_by_user_id=user_id,
            constraints=_canonicalize_constraints(constraints_payload).model_dump(),
            ends_at=collecting_hold_until,
            duration_seconds=ROUND_TIMER_SECONDS,
            candidate_count=candidate_count,
            ai_used=False,
            ai_why=None,
        )
        db.add(sess)
        await db.flush()

        runtime: dict[str, Any] = {
            "version": 1,
            "phase": "collecting",
            "setup_ends_at": None,
            "round": 1,
            "initial_candidate_ids": [],
            "mutual_candidate_ids": [],
            "tie_break_required": False,
            "tie_break_candidate_ids": [],
            "ended_by_leader": False,
            "collecting": {
                "ends_at": None,
                "user_decks": {},
                "user_constraints": {},
                "user_dealt_at": {},
                "user_joined_at": {},
            },
            "rounds": {
                "1": {"user_started_at": {}, "user_locked_at": {}, "votes": {}},
                "2": {"user_started_at": {}, "user_locked_at": {}, "votes": {}},
            },
        }
        active = sess
    else:
        sess = active
        runtime = _ensure_runtime(sess)
        runtime.setdefault("phase", "collecting")

    collecting = _runtime_collecting_state(runtime)
    phase = str(runtime.get("phase") or "collecting")
    user_key = str(user_id)
    if phase == "collecting" and user_key not in collecting["user_joined_at"]:
        collecting["user_joined_at"][user_key] = _to_iso(now)

    if phase == "collecting" and has_user_preferences:
        deck_items, refined, ai_used, ai_why = await _generate_user_deck_items(
            db,
            group_id=group_id,
            user_id=user_id,
            constraints_payload=constraints_payload,
            text=text,
            candidate_count=candidate_count,
            now=now,
        )
        collecting["user_decks"][str(user_id)] = [str(item.id) for item in deck_items]
        collecting["user_constraints"][str(user_id)] = refined.model_dump()
        collecting["user_ai"][str(user_id)] = {"used": bool(ai_used), "why": ai_why}
        sess.constraints = refined.model_dump()
        sess.ai_used = bool(sess.ai_used or ai_used)
        if ai_used and ai_why:
            sess.ai_why = ai_why

    if phase == "collecting":
        user_key = str(user_id)
        has_personal_deck = bool(_parse_uuid_list(collecting["user_decks"].get(user_key)))
        mark_ready = False
        if has_user_preferences:
            # Backward compatible default: if caller doesn't specify, treat deal as ready.
            mark_ready = confirm_ready is not False
        elif bool(confirm_ready):
            # Explicit confirm call from UI after deal modal.
            mark_ready = True

        if has_personal_deck and mark_ready:
            collecting["user_dealt_at"][user_key] = _to_iso(now)

    all_dealt = bool(member_ids) and all(
        str(member_id) in collecting["user_dealt_at"] for member_id in member_ids
    )

    rows_q = (
        select(TonightSessionCandidate)
        .where(TonightSessionCandidate.session_id == sess.id)
        .order_by(TonightSessionCandidate.position.asc())
    )
    out_candidates = (await db.execute(rows_q)).scalars().all()
    if phase == "collecting" and (all_dealt or len(member_ids) <= 1):
        out_candidates = await _finalize_collecting_to_swipe(
            db,
            s=sess,
            runtime=runtime,
            member_ids=member_ids,
            now=now,
        )
        phase = "swiping"

    personal_preview_ids = _parse_uuid_list(
        collecting["user_decks"].get(str(user_id))
        if isinstance(collecting.get("user_decks"), dict)
        else []
    )

    _persist_runtime(sess, runtime)
    await db.flush()
    return sess, out_candidates, personal_preview_ids



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


def _session_candidates_for_ids(
    s: TonightSession,
    *,
    candidate_ids: list[uuid.UUID],
) -> list[TonightSessionCandidate]:
    ordered = sorted(s.candidates, key=lambda c: c.position)
    if not candidate_ids:
        return ordered
    allowed = {item_id for item_id in candidate_ids}
    return [c for c in ordered if c.watchlist_item_id in allowed]


def _round1_shortlist(runtime: dict[str, Any]) -> list[uuid.UUID]:
    round1 = _runtime_round_state(runtime, 1)
    votes = round1["votes"]
    liked_ids: set[str] = set()
    for user_votes in votes.values():
        if not isinstance(user_votes, dict):
            continue
        for item_id, vote in user_votes.items():
            if vote == "yes":
                liked_ids.add(item_id)
    ordered = [
        item_id
        for item_id in runtime.get("initial_candidate_ids", [])
        if isinstance(item_id, str) and item_id in liked_ids
    ]
    return _parse_uuid_list(ordered)


def _shared_requested_moods(runtime: dict[str, Any]) -> list[str]:
    collecting = runtime.get("collecting")
    if not isinstance(collecting, dict):
        return []

    user_constraints = collecting.get("user_constraints")
    if not isinstance(user_constraints, dict) or not user_constraints:
        return []

    mood_sets: list[set[str]] = []
    for raw in user_constraints.values():
        if not isinstance(raw, dict):
            continue
        moods = raw.get("moods")
        if not isinstance(moods, list):
            continue
        canonical: set[str] = set()
        for mood in moods:
            if not isinstance(mood, str):
                continue
            mapped = _canonicalize_mood(mood)
            if mapped:
                canonical.add(mapped)
        mood_sets.append(canonical)

    if not mood_sets:
        return []
    shared = set.intersection(*mood_sets) if mood_sets else set()
    return sorted(shared)


async def _upsert_legacy_vote_row(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    watchlist_item_id: uuid.UUID,
    vote: str,
    now: datetime,
) -> None:
    q = select(TonightVote).where(TonightVote.session_id == session_id, TonightVote.user_id == user_id)
    existing = (await db.execute(q)).scalar_one_or_none()
    if existing:
        existing.watchlist_item_id = watchlist_item_id
        existing.vote = vote
        existing.updated_at = now
        return

    db.add(
        TonightVote(
            session_id=session_id,
            user_id=user_id,
            watchlist_item_id=watchlist_item_id,
            vote=vote,
            updated_at=now,
        )
    )


async def cast_vote(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    watchlist_item_id: uuid.UUID,
    vote: str,
    ) -> None:
    if vote not in {"yes", "no"}:
        raise ValueError("vote must be yes or no")

    s = await _load_session_with_candidates(db, session_id)
    await assert_user_in_group(db, s.group_id, user_id)
    await _assert_session_active(s)

    now = datetime.now(timezone.utc)
    runtime = _ensure_runtime(s)
    phase = str(runtime.get("phase") or "swiping")
    if phase != "swiping":
        raise ValueError("Deck is not ready for swiping yet")

    current_round = int(runtime.get("round") or 1)
    candidate_ids = _candidate_ids_for_round(s, runtime, current_round)
    allowed = {item_id for item_id in candidate_ids}
    if watchlist_item_id not in allowed:
        raise ValueError("watchlist_item_id is not in this session deck")

    _ensure_user_timer(runtime, round_num=current_round, user_id=user_id, now=now)
    _apply_user_auto_lock(
        runtime,
        round_num=current_round,
        user_id=user_id,
        candidate_ids=candidate_ids,
        now=now,
    )
    if _is_user_locked(runtime, round_num=current_round, user_id=user_id):
        _persist_runtime(s, runtime)
        raise ValueError("Voting window locked for this round")

    user_votes = _user_votes_for_round(runtime, round_num=current_round, user_id=user_id)
    user_votes[str(watchlist_item_id)] = vote

    await _upsert_legacy_vote_row(
        db,
        session_id=session_id,
        user_id=user_id,
        watchlist_item_id=watchlist_item_id,
        vote=vote,
        now=now,
    )

    _apply_user_auto_lock(
        runtime,
        round_num=current_round,
        user_id=user_id,
        candidate_ids=candidate_ids,
        now=now,
    )
    await _advance_rounds_if_needed(db, s=s, runtime=runtime, now=now)
    _persist_runtime(s, runtime)
    await db.flush()


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
    runtime = _ensure_runtime(s)
    _persist_runtime(s, runtime)
    return s


async def _compute_winner(db: AsyncSession, s: TonightSession) -> uuid.UUID:
    winner, tied_ids = await _compute_winner_or_tie(db, s)
    if winner is not None:
        return winner

    runtime = _ensure_runtime(s)
    candidate_ids = _candidate_ids_for_round(s, runtime, 1)
    if not candidate_ids:
        candidate_ids = _session_base_candidate_ids(s)

    if not tied_ids:
        tied_ids = candidate_ids

    if not tied_ids:
        raise ValueError("Session has no candidates")

    rng = random.Random(str(s.id))
    return rng.choice(sorted(tied_ids, key=lambda x: str(x)))


async def _compute_winner_or_tie(
    db: AsyncSession,
    s: TonightSession,
) -> tuple[uuid.UUID | None, list[uuid.UUID]]:
    runtime = _ensure_runtime(s)
    candidate_ids = _candidate_ids_for_round(s, runtime, 1)
    if not candidate_ids:
        candidate_ids = _session_base_candidate_ids(s)
    if not candidate_ids:
        return None, []

    round_state = _runtime_round_state(runtime, 1)
    round_votes = round_state["votes"]
    stats = {item_id: {"yes": 0, "no": 0} for item_id in candidate_ids}
    for user_votes in round_votes.values():
        if not isinstance(user_votes, dict):
            continue
        for item_id_raw, vote in user_votes.items():
            try:
                item_id = uuid.UUID(str(item_id_raw))
            except (TypeError, ValueError):
                continue
            if item_id not in stats:
                continue
            if vote == "yes":
                stats[item_id]["yes"] += 1
            elif vote == "no":
                stats[item_id]["no"] += 1

    if all(v["yes"] == 0 and v["no"] == 0 for v in stats.values()):
        return None, sorted(candidate_ids, key=lambda item_id: str(item_id))

    max_yes = max(v["yes"] for v in stats.values())
    yes_tied = [item_id for item_id, v in stats.items() if v["yes"] == max_yes]
    if len(yes_tied) == 1:
        return yes_tied[0], []
    min_no = min(stats[item_id]["no"] for item_id in yes_tied)
    no_tied = [item_id for item_id in yes_tied if stats[item_id]["no"] == min_no]
    if len(no_tied) == 1:
        return no_tied[0], []

    shared_moods = _shared_requested_moods(runtime)
    if shared_moods:
        q_items = (
            select(WatchlistItem)
            .options(selectinload(WatchlistItem.title))
            .where(WatchlistItem.id.in_(no_tied))
        )
        tied_items = (await db.execute(q_items)).scalars().all()
        if tied_items:
            matched = await _build_item_tag_matches(
                items=tied_items,
                requested_moods=shared_moods,
            )
            if matched:
                score_map = {item.id: len(matched.get(item.id, [])) for item in tied_items}
                best = max(score_map.values())
                filtered = [item_id for item_id in no_tied if score_map.get(item_id, 0) == best]
                if len(filtered) == 1:
                    return filtered[0], []
                if filtered:
                    no_tied = filtered

    return None, sorted(no_tied, key=lambda item_id: str(item_id))


async def shuffle_and_complete(db: AsyncSession, *, session_id: uuid.UUID, user_id: uuid.UUID) -> SessionStateView:
    s = await _load_session_with_candidates(db, session_id)
    await assert_user_in_group(db, s.group_id, user_id)
    await _assert_session_active(s)

    now = datetime.now(timezone.utc)
    runtime = _ensure_runtime(s)
    phase = str(runtime.get("phase") or "swiping")
    if phase == "tiebreak":
        if s.group.owner_id != user_id:
            raise PermissionError("Only the group leader can auto-pick a tied deck")
        deck_item_ids = _parse_uuid_list(runtime.get("tie_break_candidate_ids"))
        if not deck_item_ids:
            deck_item_ids = _candidate_ids_for_round(s, runtime, 1)
        if not deck_item_ids:
            raise ValueError("Session has no candidates")
        rng = random.Random(str(s.id) + ":shuffle:tiebreak")
    elif phase == "swiping":
        await _advance_rounds_if_needed(db, s=s, runtime=runtime, now=now)
        current_round = int(runtime.get("round") or 1)
        deck_item_ids = _candidate_ids_for_round(s, runtime, current_round)
        if not deck_item_ids:
            deck_item_ids = _candidate_ids_for_round(s, runtime, 1)
        if not deck_item_ids:
            raise ValueError("Session has no candidates")
        rng = random.Random(str(s.id) + f":shuffle:round{current_round}")
    else:
        raise ValueError("Deck is not ready for auto-pick yet")

    winner = rng.choice(deck_item_ids)

    s.status = "complete"
    s.completed_at = now
    s.result_watchlist_item_id = winner
    runtime["phase"] = "swiping"
    runtime["tie_break_required"] = False
    runtime["tie_break_candidate_ids"] = []
    _persist_runtime(s, runtime)
    await db.flush()
    return await _build_session_state_view(db, s=s, user_id=user_id, now=now)


async def end_session(db: AsyncSession, *, session_id: uuid.UUID, user_id: uuid.UUID) -> SessionStateView:
    s = await _load_session_with_candidates(db, session_id)
    await assert_user_in_group(db, s.group_id, user_id)
    if s.group.owner_id != user_id:
        raise PermissionError("Only the group leader can end this session")

    now = datetime.now(timezone.utc)
    if s.status == "active":
        runtime = _ensure_runtime(s)
        s.status = "complete"
        s.completed_at = now
        runtime["phase"] = "swiping"
        runtime["tie_break_required"] = False
        runtime["tie_break_candidate_ids"] = []
        runtime["ended_by_leader"] = True
        _persist_runtime(s, runtime)
        await db.flush()
    return await _build_session_state_view(db, s=s, user_id=user_id, now=now)


async def _build_session_state_view(
    db: AsyncSession,
    *,
    s: TonightSession,
    user_id: uuid.UUID,
    now: datetime,
) -> SessionStateView:
    runtime = _ensure_runtime(s)
    member_ids = await _group_member_ids(db, group_id=s.group_id)
    if s.status == "complete":
        shortlist = _round1_shortlist(runtime)
        display_ids = _candidate_ids_for_round(s, runtime, 1)
        display_candidates = _session_candidates_for_ids(s, candidate_ids=display_ids)
        _persist_runtime(s, runtime)
        await db.flush()
        return SessionStateView(
            session=s,
            candidates=display_candidates,
            phase="complete",
            round=1,
            user_locked=True,
            user_seconds_left=0,
            mutual_candidate_ids=[],
            shortlist=shortlist,
            tie_break_required=False,
            tie_break_candidate_ids=[],
            ended_by_leader=bool(runtime.get("ended_by_leader")),
        )

    flow_phase = str(runtime.get("phase") or "swiping")
    if flow_phase == "collecting":
        collecting = _runtime_collecting_state(runtime)
        user_key = str(user_id)
        if user_key not in collecting["user_joined_at"]:
            collecting["user_joined_at"][user_key] = _to_iso(now)

        all_dealt = bool(member_ids) and all(
            str(member_id) in collecting["user_dealt_at"] for member_id in member_ids
        )

        if all_dealt or len(member_ids) <= 1:
            await _finalize_collecting_to_swipe(
                db,
                s=s,
                runtime=runtime,
                member_ids=member_ids,
                now=now,
            )
            await db.refresh(s, attribute_names=["candidates"])
            flow_phase = "swiping"
        else:
            user_dealt = str(user_id) in collecting["user_dealt_at"]
            user_joined = str(user_id) in collecting["user_joined_at"]
            user_seconds_left = ROUND_TIMER_SECONDS
            _persist_runtime(s, runtime)
            await db.flush()
            return SessionStateView(
                session=s,
                candidates=[],
                phase="waiting" if (user_dealt or user_joined) else "collecting",
                round=0,
                user_locked=user_dealt,
                user_seconds_left=user_seconds_left,
                mutual_candidate_ids=[],
                shortlist=[],
                tie_break_required=False,
                tie_break_candidate_ids=[],
                ended_by_leader=False,
            )

    if flow_phase == "tiebreak":
        tie_break_ids = _parse_uuid_list(runtime.get("tie_break_candidate_ids"))
        if not tie_break_ids:
            tie_break_ids = _candidate_ids_for_round(s, runtime, 1)
        display_candidates = _session_candidates_for_ids(s, candidate_ids=tie_break_ids)
        shortlist = _round1_shortlist(runtime)
        _persist_runtime(s, runtime)
        await db.flush()
        return SessionStateView(
            session=s,
            candidates=display_candidates,
            phase="tiebreak",
            round=1,
            user_locked=True,
            user_seconds_left=0,
            mutual_candidate_ids=[],
            shortlist=shortlist,
            tie_break_required=True,
            tie_break_candidate_ids=tie_break_ids,
            ended_by_leader=False,
        )

    current_round = int(runtime.get("round") or 1)
    current_ids = _candidate_ids_for_round(s, runtime, current_round)
    _seed_round_timers(runtime, round_num=current_round, member_ids=member_ids, now=now)
    _ensure_user_timer(runtime, round_num=current_round, user_id=user_id, now=now)

    user_locked = _apply_user_auto_lock(
        runtime,
        round_num=current_round,
        user_id=user_id,
        candidate_ids=current_ids,
        now=now,
    )
    user_seconds_left = _seconds_left_for_user(
        runtime,
        round_num=current_round,
        user_id=user_id,
        now=now,
    )
    user_votes = _user_votes_for_round(runtime, round_num=current_round, user_id=user_id)
    if not user_locked and current_ids and all(str(item_id) in user_votes for item_id in current_ids):
        _lock_user(runtime, round_num=current_round, user_id=user_id, now=now)
        user_locked = True
        user_seconds_left = 0

    await _advance_rounds_if_needed(db, s=s, runtime=runtime, now=now)

    if s.status == "complete":
        _persist_runtime(s, runtime)
        await db.flush()
        shortlist = _round1_shortlist(runtime)
        display_candidates = _session_candidates_for_ids(
            s,
            candidate_ids=_candidate_ids_for_round(s, runtime, 1),
        )
        return SessionStateView(
            session=s,
            candidates=display_candidates,
            phase="complete",
            round=1,
            user_locked=True,
            user_seconds_left=0,
            mutual_candidate_ids=[],
            shortlist=shortlist,
            tie_break_required=False,
            tie_break_candidate_ids=[],
            ended_by_leader=bool(runtime.get("ended_by_leader")),
        )

    current_ids = _candidate_ids_for_round(s, runtime, current_round)
    display_candidates = _session_candidates_for_ids(s, candidate_ids=current_ids)
    shortlist = _round1_shortlist(runtime)
    phase = "waiting" if user_locked else "swiping"

    _persist_runtime(s, runtime)
    await db.flush()
    return SessionStateView(
        session=s,
        candidates=display_candidates,
        phase=phase,
        round=1,
        user_locked=user_locked,
        user_seconds_left=user_seconds_left,
        mutual_candidate_ids=[],
        shortlist=shortlist,
        tie_break_required=False,
        tie_break_candidate_ids=[],
        ended_by_leader=False,
    )


async def get_session_state(db: AsyncSession, *, session_id: uuid.UUID, user_id: uuid.UUID) -> SessionStateView:
    s = await _load_session_with_candidates(db, session_id)
    await assert_user_in_group(db, s.group_id, user_id)

    now = datetime.now(timezone.utc)
    if s.status == "active":
        runtime = _ensure_runtime(s)
        flow_phase = str(runtime.get("phase") or "swiping")
        if flow_phase == "swiping" and s.ends_at <= now:
            await resolve_if_expired(db, session_id=session_id)
            s = await _load_session_with_candidates(db, session_id)

    return await _build_session_state_view(db, s=s, user_id=user_id, now=now)
