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
_WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"
_WIKIDATA_COMPANY_PROPERTY_IDS = ("P272", "P750", "P449")
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


async def fetch_tmdb_title_people_names(
    *,
    tmdb_id: int,
    media_type: str,
) -> set[str]:
    if media_type not in {"movie", "tv"}:
        return set()
    if settings.env == "test":
        return set()

    key = f"people:{media_type}:{tmdb_id}"
    cached = _cache_get(key)
    if isinstance(cached, dict):
        raw = cached.get("names", [])
        if isinstance(raw, list):
            return {
                _normalize_term(value)
                for value in raw
                if isinstance(value, str) and value.strip()
            }

    headers = {
        "Authorization": f"Bearer {settings.tmdb_token}",
        "Accept": "application/json",
    }
    credits_path = (
        f"/movie/{tmdb_id}/credits"
        if media_type == "movie"
        else f"/tv/{tmdb_id}/aggregate_credits"
    )

    try:
        async with httpx.AsyncClient(base_url="https://api.themoviedb.org/3", timeout=6) as client:
            r = await client.get(credits_path, headers=headers)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError):
        return set()

    names: list[str] = []
    cast_rows = data.get("cast")
    if isinstance(cast_rows, list):
        for row in cast_rows[:40]:
            if not isinstance(row, dict):
                continue
            raw_name = row.get("name")
            if isinstance(raw_name, str) and raw_name.strip():
                names.append(raw_name)

    crew_rows = data.get("crew")
    if isinstance(crew_rows, list):
        for row in crew_rows[:60]:
            if not isinstance(row, dict):
                continue
            raw_name = row.get("name")
            if not isinstance(raw_name, str) or not raw_name.strip():
                continue

            include = False
            known_for = _normalize_term(str(row.get("known_for_department") or ""))
            if known_for in {"directing", "writing", "production", "acting"}:
                include = True

            job = _normalize_term(str(row.get("job") or ""))
            if job in {
                "creator",
                "director",
                "screenplay",
                "writer",
                "executive producer",
                "producer",
                "host",
                "presenter",
            }:
                include = True

            jobs = row.get("jobs")
            if isinstance(jobs, list):
                for item in jobs:
                    if not isinstance(item, dict):
                        continue
                    role = _normalize_term(str(item.get("job") or ""))
                    if role in {
                        "creator",
                        "director",
                        "screenplay",
                        "writer",
                        "executive producer",
                        "producer",
                        "host",
                        "presenter",
                    }:
                        include = True
                        break

            if include:
                names.append(raw_name)

    normalized = {
        _normalize_term(name)
        for name in names
        if isinstance(name, str) and name.strip()
    }
    _cache_set(key, {"names": sorted(normalized)})
    return normalized


async def fetch_tmdb_title_locale_tokens(
    *,
    tmdb_id: int,
    media_type: str,
) -> set[str]:
    if media_type not in {"movie", "tv"}:
        return set()
    if settings.env == "test":
        return set()

    key = f"locale:{media_type}:{tmdb_id}"
    cached = _cache_get(key)
    if isinstance(cached, dict):
        raw = cached.get("tokens", [])
        if isinstance(raw, list):
            return {
                _normalize_term(value)
                for value in raw
                if isinstance(value, str) and value.strip()
            }

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
        return set()

    tokens: set[str] = set()

    original_language = data.get("original_language")
    if isinstance(original_language, str) and original_language.strip():
        tokens.add(_normalize_term(original_language))

    spoken = data.get("spoken_languages")
    if isinstance(spoken, list):
        for row in spoken:
            if not isinstance(row, dict):
                continue
            for key_name in ("iso_639_1", "english_name", "name"):
                raw_value = row.get(key_name)
                if isinstance(raw_value, str) and raw_value.strip():
                    tokens.add(_normalize_term(raw_value))

    origin_country = data.get("origin_country")
    if isinstance(origin_country, list):
        for value in origin_country:
            if isinstance(value, str) and value.strip():
                tokens.add(_normalize_term(value))

    production_countries = data.get("production_countries")
    if isinstance(production_countries, list):
        for row in production_countries:
            if not isinstance(row, dict):
                continue
            for key_name in ("iso_3166_1", "name"):
                raw_value = row.get(key_name)
                if isinstance(raw_value, str) and raw_value.strip():
                    tokens.add(_normalize_term(raw_value))

    _cache_set(key, {"tokens": sorted(tokens)})
    return tokens


async def fetch_tmdb_title_company_names(
    *,
    tmdb_id: int,
    media_type: str,
) -> set[str]:
    if media_type not in {"movie", "tv"}:
        return set()
    if settings.env == "test":
        return set()

    key = f"companies:{media_type}:{tmdb_id}"
    cached = _cache_get(key)
    if isinstance(cached, dict):
        raw = cached.get("names", [])
        if isinstance(raw, list):
            return {
                _normalize_term(value)
                for value in raw
                if isinstance(value, str) and value.strip()
            }

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
        return set()

    names: set[str] = set()
    production_companies = data.get("production_companies")
    if isinstance(production_companies, list):
        for row in production_companies:
            if not isinstance(row, dict):
                continue
            raw_name = row.get("name")
            if isinstance(raw_name, str) and raw_name.strip():
                names.add(_normalize_term(raw_name))

    networks = data.get("networks")
    if isinstance(networks, list):
        for row in networks:
            if not isinstance(row, dict):
                continue
            raw_name = row.get("name")
            if isinstance(raw_name, str) and raw_name.strip():
                names.add(_normalize_term(raw_name))

    _cache_set(key, {"names": sorted(names)})
    return names


def _wikidata_entity_id_from_claim(claim: Any) -> str | None:
    if not isinstance(claim, dict):
        return None
    main_snak = claim.get("mainsnak")
    if not isinstance(main_snak, dict):
        return None
    data_value = main_snak.get("datavalue")
    if not isinstance(data_value, dict):
        return None
    value = data_value.get("value")
    if not isinstance(value, dict):
        return None
    entity_id = value.get("id")
    if isinstance(entity_id, str) and entity_id.startswith("Q"):
        return entity_id
    return None


def _normalize_wikidata_title(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _pick_wikidata_title_entity(
    *,
    results: list[dict[str, Any]],
    title: str,
    release_year: int | None,
    media_type: str,
) -> str | None:
    if not results:
        return None

    target = _normalize_wikidata_title(title)
    best_id: str | None = None
    best_score = -1

    for row in results:
        if not isinstance(row, dict):
            continue
        entity_id = row.get("id")
        if not isinstance(entity_id, str) or not entity_id.startswith("Q"):
            continue

        label = _normalize_wikidata_title(str(row.get("label") or ""))
        description = _normalize_wikidata_title(str(row.get("description") or ""))
        score = 0
        if label and (label == target or target in label or label in target):
            score += 3
        if release_year and str(release_year) in description:
            score += 2
        if media_type == "movie" and "film" in description:
            score += 2
        if media_type == "tv" and any(term in description for term in ("television", "tv", "series")):
            score += 2

        if score > best_score:
            best_score = score
            best_id = entity_id

    return best_id


async def fetch_web_title_company_names(
    *,
    title: str,
    release_year: int | None,
    media_type: str,
) -> set[str]:
    if media_type not in {"movie", "tv"}:
        return set()
    if settings.env == "test":
        return set()

    normalized_title = _normalize_term(title)
    if not normalized_title:
        return set()

    year_key = str(release_year) if isinstance(release_year, int) else "na"
    key = f"web-companies:{media_type}:{normalized_title}:{year_key}"
    cached = _cache_get(key)
    if isinstance(cached, dict):
        raw = cached.get("names", [])
        if isinstance(raw, list):
            return {
                _normalize_term(value)
                for value in raw
                if isinstance(value, str) and value.strip()
            }

    search_params = {
        "action": "wbsearchentities",
        "search": title,
        "language": "en",
        "format": "json",
        "type": "item",
        "limit": 8,
    }
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            search_response = await client.get(_WIKIDATA_API_URL, params=search_params)
            search_response.raise_for_status()
            search_payload = search_response.json()
    except (httpx.HTTPError, ValueError):
        return set()

    search_rows = search_payload.get("search")
    if not isinstance(search_rows, list):
        return set()

    entity_id = _pick_wikidata_title_entity(
        results=[row for row in search_rows if isinstance(row, dict)],
        title=title,
        release_year=release_year,
        media_type=media_type,
    )
    if not entity_id:
        return set()

    detail_params = {
        "action": "wbgetentities",
        "ids": entity_id,
        "props": "claims",
        "format": "json",
    }
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            detail_response = await client.get(_WIKIDATA_API_URL, params=detail_params)
            detail_response.raise_for_status()
            detail_payload = detail_response.json()
    except (httpx.HTTPError, ValueError):
        return set()

    entities = detail_payload.get("entities")
    if not isinstance(entities, dict):
        return set()
    entity = entities.get(entity_id)
    if not isinstance(entity, dict):
        return set()
    claims = entity.get("claims")
    if not isinstance(claims, dict):
        return set()

    company_entity_ids: set[str] = set()
    for property_id in _WIKIDATA_COMPANY_PROPERTY_IDS:
        claim_rows = claims.get(property_id)
        if not isinstance(claim_rows, list):
            continue
        for claim in claim_rows:
            value_id = _wikidata_entity_id_from_claim(claim)
            if value_id:
                company_entity_ids.add(value_id)

    if not company_entity_ids:
        return set()

    label_params = {
        "action": "wbgetentities",
        "ids": "|".join(sorted(company_entity_ids)),
        "props": "labels",
        "languages": "en",
        "format": "json",
    }
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            label_response = await client.get(_WIKIDATA_API_URL, params=label_params)
            label_response.raise_for_status()
            label_payload = label_response.json()
    except (httpx.HTTPError, ValueError):
        return set()

    label_entities = label_payload.get("entities")
    if not isinstance(label_entities, dict):
        return set()

    names: set[str] = set()
    for company_id in company_entity_ids:
        row = label_entities.get(company_id)
        if not isinstance(row, dict):
            continue
        labels = row.get("labels")
        if not isinstance(labels, dict):
            continue
        en = labels.get("en")
        if not isinstance(en, dict):
            continue
        raw_name = en.get("value")
        if isinstance(raw_name, str) and raw_name.strip():
            names.add(_normalize_term(raw_name))

    _cache_set(key, {"names": sorted(names)})
    return names


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
