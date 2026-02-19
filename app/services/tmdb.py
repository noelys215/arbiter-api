from __future__ import annotations

import html
import re
import time
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from app.core.config import settings

# Search results and taxonomy payloads share this in-memory cache.
_CACHE: dict[str, tuple[float, Any]] = {}
_TTL_SECONDS = 600
_STREAMING_BUCKETS = ("flatrate", "ads", "free")
_DEFAULT_PROVIDER_REGION = "US"
_EXCLUDED_STREAMING_PROVIDER_NAMES = {
    "netflix standard with ads",
}
_JUSTWATCH_ANCHOR_RE = re.compile(
    r'<a href="(?P<href>https://click\.justwatch\.com/a\?[^"]+)"[^>]*title="(?P<title>[^"]+)"',
    flags=re.IGNORECASE,
)


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


def _safe_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _dedupe_streaming_providers(region_payload: dict[str, Any]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for bucket in _STREAMING_BUCKETS:
        rows = region_payload.get(bucket)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get("provider_name")
            if not isinstance(name, str) or not name.strip():
                continue
            normalized = name.strip()
            if normalized.lower() in _EXCLUDED_STREAMING_PROVIDER_NAMES:
                continue
            provider_id = _safe_int(row.get("provider_id"))
            key = str(provider_id) if provider_id is not None else normalized.lower()
            if key in deduped:
                continue
            deduped[key] = {
                "provider_id": provider_id,
                "provider_name": normalized,
                "logo_path": row.get("logo_path") if isinstance(row.get("logo_path"), str) else None,
                "display_priority": _safe_int(row.get("display_priority")),
            }

    providers = list(deduped.values())
    providers.sort(
        key=lambda value: (
            value.get("display_priority")
            if isinstance(value.get("display_priority"), int)
            else 9999,
            value.get("provider_name") or "",
        )
    )
    return providers


def _extract_direct_streaming_urls_from_watch_html(markup: str) -> dict[str, str]:
    if not isinstance(markup, str) or not markup.strip():
        return {}

    out: dict[str, str] = {}
    for match in _JUSTWATCH_ANCHOR_RE.finditer(markup):
        title_attr = html.unescape(match.group("title") or "").strip()
        title_lower = title_attr.lower()
        if not title_lower.startswith("watch "):
            continue

        split_at = title_lower.rfind(" on ")
        if split_at < 0:
            continue
        provider_name = title_attr[split_at + 4 :].strip()
        if not provider_name:
            continue

        href = html.unescape(match.group("href") or "").strip()
        if not href:
            continue

        parsed_href = urlparse(href)
        query = parse_qs(parsed_href.query)
        raw_target = query.get("r", [None])[0]
        if not isinstance(raw_target, str) or not raw_target.strip():
            continue

        target = unquote(raw_target).strip()
        if not target.startswith(("http://", "https://")):
            continue

        key = provider_name.lower()
        if key not in out:
            out[key] = target

    return out


async def _fetch_tmdb_watch_page_streaming_links(
    *,
    tmdb_id: int,
    media_type: str,
    region: str,
) -> dict[str, str]:
    normalized_region = (region or _DEFAULT_PROVIDER_REGION).strip().upper() or _DEFAULT_PROVIDER_REGION
    path = f"/{media_type}/{tmdb_id}/watch"
    params = {"locale": normalized_region}
    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "User-Agent": "ArbiterTMDBWatcher/1.0",
    }

    try:
        async with httpx.AsyncClient(
            base_url="https://www.themoviedb.org",
            timeout=8,
            follow_redirects=True,
        ) as client:
            r = await client.get(path, params=params, headers=headers)
            r.raise_for_status()
            markup = r.text
    except httpx.HTTPError:
        return {}

    return _extract_direct_streaming_urls_from_watch_html(markup)


async def fetch_tmdb_watch_providers(
    *,
    tmdb_id: int,
    media_type: str,
    region: str = _DEFAULT_PROVIDER_REGION,
) -> dict[str, Any]:
    if media_type not in {"movie", "tv"}:
        return {"region": region.upper(), "link": None, "streaming_providers": []}
    if settings.env == "test":
        return {"region": region.upper(), "link": None, "streaming_providers": []}

    normalized_region = (region or _DEFAULT_PROVIDER_REGION).strip().upper() or _DEFAULT_PROVIDER_REGION
    key = f"providers:{media_type}:{tmdb_id}:{normalized_region}"
    cached = _cache_get(key)
    if isinstance(cached, dict):
        providers = cached.get("streaming_providers")
        if isinstance(providers, list):
            return {
                "region": normalized_region,
                "link": cached.get("link") if isinstance(cached.get("link"), str) else None,
                "streaming_providers": [dict(row) for row in providers if isinstance(row, dict)],
            }

    headers = {
        "Authorization": f"Bearer {settings.tmdb_token}",
        "Accept": "application/json",
    }
    path = f"/{media_type}/{tmdb_id}/watch/providers"

    try:
        async with httpx.AsyncClient(base_url="https://api.themoviedb.org/3", timeout=6) as client:
            r = await client.get(path, headers=headers)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError):
        return {"region": normalized_region, "link": None, "streaming_providers": []}

    results = data.get("results")
    region_payload = results.get(normalized_region) if isinstance(results, dict) else {}
    if not isinstance(region_payload, dict):
        region_payload = {}

    link = region_payload.get("link") if isinstance(region_payload.get("link"), str) else None
    providers = _dedupe_streaming_providers(region_payload)
    deep_links_by_provider = (
        await _fetch_tmdb_watch_page_streaming_links(
            tmdb_id=tmdb_id,
            media_type=media_type,
            region=normalized_region,
        )
        if providers
        else {}
    )
    for provider in providers:
        name = provider.get("provider_name")
        key = name.lower() if isinstance(name, str) else None
        provider["streaming_url"] = (
            deep_links_by_provider.get(key)
            if isinstance(key, str) and key
            else None
        )

    payload = {
        "region": normalized_region,
        "link": link,
        "streaming_providers": providers,
    }
    _cache_set(key, payload)
    return payload
