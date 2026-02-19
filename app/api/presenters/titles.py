from __future__ import annotations

from typing import Any

from app.schemas.watchlist import TitleOut
from app.services.tmdb import fetch_tmdb_title_taxonomy, fetch_tmdb_watch_providers


def _normalize_streaming_options(rows: Any) -> list[dict[str, str | None]]:
    if not isinstance(rows, list):
        return []

    out: list[dict[str, str | None]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        provider_name = row.get("provider_name")
        if not isinstance(provider_name, str) or not provider_name.strip():
            continue

        streaming_url = row.get("streaming_url")
        out.append(
            {
                "provider_name": provider_name.strip(),
                "streaming_url": (
                    streaming_url.strip()
                    if isinstance(streaming_url, str) and streaming_url.strip()
                    else None
                ),
            }
        )
    return out


async def build_title_out_with_taxonomy(
    title: Any,
    *,
    include_streaming: bool = False,
) -> TitleOut:
    tmdb_genres: list[str] = []
    tmdb_genre_ids: list[int] = []
    tmdb_streaming_options: list[dict[str, str | None]] = []
    tmdb_streaming_providers: list[str] = []
    tmdb_streaming_link: str | None = None

    if title.source == "tmdb" and title.source_id:
        try:
            tmdb_id = int(title.source_id)
        except (TypeError, ValueError):
            tmdb_id = None

        if tmdb_id is not None:
            genres, _, genre_ids = await fetch_tmdb_title_taxonomy(
                tmdb_id=tmdb_id,
                media_type=title.media_type,
            )
            tmdb_genres = sorted(genres)
            tmdb_genre_ids = sorted(genre_ids)

            if include_streaming:
                # Streaming providers require an extra TMDB request; only include when needed.
                providers_payload = await fetch_tmdb_watch_providers(
                    tmdb_id=tmdb_id,
                    media_type=title.media_type,
                )
                tmdb_streaming_options = _normalize_streaming_options(
                    providers_payload.get("streaming_providers")
                )
                tmdb_streaming_providers = [
                    row["provider_name"] for row in tmdb_streaming_options
                ]
                link = providers_payload.get("link")
                if isinstance(link, str) and link.strip():
                    tmdb_streaming_link = link

    return TitleOut(
        id=title.id,
        source=title.source,
        source_id=title.source_id,
        media_type=title.media_type,
        name=title.name,
        release_year=title.release_year,
        poster_path=title.poster_path,
        overview=title.overview,
        runtime_minutes=title.runtime_minutes,
        tmdb_genres=tmdb_genres,
        tmdb_genre_ids=tmdb_genre_ids,
        tmdb_streaming_options=tmdb_streaming_options,
        tmdb_streaming_providers=tmdb_streaming_providers,
        tmdb_streaming_link=tmdb_streaming_link,
    )
