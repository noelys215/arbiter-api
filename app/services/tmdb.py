from __future__ import annotations

import time
from typing import Any

import httpx

from app.core.config import settings

# Search results and taxonomy payloads share this in-memory cache.
_CACHE: dict[str, tuple[float, Any]] = {}
_TTL_SECONDS = 600


def _cache_get(key: str):
    hit = _CACHE.get(key)
    if not hit:
        return None
    expires_at, value = hit
    if time.time() > expires_at:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value):
    _CACHE[key] = (time.time() + _TTL_SECONDS, value)


async def tmdb_search_multi(q: str) -> list[dict[str, Any]]:
    q = q.strip()
    if not q:
        return []

    key = f"multi:{q.lower()}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    headers = {
        "Authorization": f"Bearer {settings.tmdb_token}",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(base_url="https://api.themoviedb.org/3", timeout=10) as client:
        r = await client.get("/search/multi", params={"query": q}, headers=headers)
        r.raise_for_status()
        data = r.json()

    out: list[dict[str, Any]] = []
    for item in data.get("results", []):
        media_type = item.get("media_type")
        if media_type not in ("movie", "tv"):
            continue

        tmdb_id = item.get("id")
        poster_path = item.get("poster_path")
        raw_genre_ids = item.get("genre_ids")
        genre_ids = [
            int(v)
            for v in (raw_genre_ids if isinstance(raw_genre_ids, list) else [])
            if isinstance(v, int)
        ]

        if media_type == "movie":
            title = item.get("title") or item.get("original_title") or ""
            date = item.get("release_date") or ""
        else:
            title = item.get("name") or item.get("original_name") or ""
            date = item.get("first_air_date") or ""

        year = None
        if isinstance(date, str) and len(date) >= 4 and date[:4].isdigit():
            year = int(date[:4])

        if not title or not tmdb_id:
            continue

        out.append(
            {
                "tmdb_id": tmdb_id,
                "media_type": media_type,
                "title": title,
                "year": year,
                "poster_path": poster_path,
                "genre_ids": genre_ids,
            }
        )

    _cache_set(key, out)
    return out


def _normalize_term(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


async def fetch_tmdb_title_taxonomy(
    *,
    tmdb_id: int,
    media_type: str,
) -> tuple[set[str], set[str], set[int]]:
    if media_type not in {"movie", "tv"}:
        return set(), set(), set()
    if settings.env == "test":
        return set(), set(), set()

    key = f"taxonomy:{media_type}:{tmdb_id}"
    cached = _cache_get(key)
    if cached is not None:
        genres = {
            _normalize_term(v)
            for v in cached.get("genres", [])
            if isinstance(v, str) and v.strip()
        }
        keywords = {
            _normalize_term(v)
            for v in cached.get("keywords", [])
            if isinstance(v, str) and v.strip()
        }
        genre_ids = {
            int(v)
            for v in cached.get("genre_ids", [])
            if isinstance(v, int) or (isinstance(v, str) and v.isdigit())
        }
        return genres, keywords, genre_ids

    headers = {
        "Authorization": f"Bearer {settings.tmdb_token}",
        "Accept": "application/json",
    }

    path = f"/{media_type}/{tmdb_id}"
    params = {"append_to_response": "keywords"}

    try:
        async with httpx.AsyncClient(base_url="https://api.themoviedb.org/3", timeout=6) as client:
            r = await client.get(path, params=params, headers=headers)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError):
        return set(), set(), set()

    genre_rows = [g for g in data.get("genres", []) if isinstance(g, dict)]
    genre_names = [g.get("name") for g in genre_rows]
    genre_ids = {
        int(g["id"])
        for g in genre_rows
        if isinstance(g.get("id"), int)
    }

    keywords_node = data.get("keywords", {})
    keyword_rows: list[dict[str, Any]] = []
    if isinstance(keywords_node, dict):
        maybe_keywords = keywords_node.get("keywords")
        maybe_results = keywords_node.get("results")
        if isinstance(maybe_keywords, list):
            keyword_rows = [k for k in maybe_keywords if isinstance(k, dict)]
        elif isinstance(maybe_results, list):
            keyword_rows = [k for k in maybe_results if isinstance(k, dict)]

    keyword_names = [k.get("name") for k in keyword_rows]

    genres = {
        _normalize_term(v)
        for v in genre_names
        if isinstance(v, str) and v.strip()
    }
    keywords = {
        _normalize_term(v)
        for v in keyword_names
        if isinstance(v, str) and v.strip()
    }

    _cache_set(
        key,
        {
            "genres": sorted(genres),
            "keywords": sorted(keywords),
            "genre_ids": sorted(genre_ids),
        },
    )
    return genres, keywords, genre_ids


def _runtime_from_tmdb_payload(*, media_type: str, data: dict[str, Any]) -> int | None:
    if media_type == "movie":
        runtime = data.get("runtime")
        if isinstance(runtime, int) and runtime > 0:
            return runtime
        return None

    episode_runtime = data.get("episode_run_time")
    if isinstance(episode_runtime, list):
        for value in episode_runtime:
            if isinstance(value, int) and value > 0:
                return value
    return None


async def fetch_tmdb_title_details(*, tmdb_id: int, media_type: str) -> dict[str, Any]:
    if media_type not in {"movie", "tv"}:
        return {}
    if settings.env == "test":
        return {}

    key = f"details:{media_type}:{tmdb_id}"
    cached = _cache_get(key)
    if isinstance(cached, dict):
        return dict(cached)

    headers = {
        "Authorization": f"Bearer {settings.tmdb_token}",
        "Accept": "application/json",
    }

    path = f"/{media_type}/{tmdb_id}"
    try:
        async with httpx.AsyncClient(base_url="https://api.themoviedb.org/3", timeout=6) as client:
            r = await client.get(path, headers=headers)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError):
        return {}

    runtime_minutes = _runtime_from_tmdb_payload(media_type=media_type, data=data)
    overview = data.get("overview")
    details = {
        "runtime_minutes": runtime_minutes,
        "overview": overview if isinstance(overview, str) and overview.strip() else None,
    }
    _cache_set(key, details)
    return details
