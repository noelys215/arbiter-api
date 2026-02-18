#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.session import AsyncSessionLocal
from app.models.title import Title
from app.services.tmdb import fetch_tmdb_title_details

logger = logging.getLogger("backfill_tmdb_title_details")

DetailsFetcher = Callable[..., Awaitable[dict[str, Any]]]


@dataclass
class BackfillStats:
    scanned: int = 0
    would_update: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped_invalid_source_id: int = 0
    fetch_errors: int = 0


def _parse_tmdb_id(source_id: str | None) -> int | None:
    if source_id is None:
        return None
    raw = str(source_id).strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _is_blank_text(value: str | None) -> bool:
    return not isinstance(value, str) or not value.strip()


def _derive_patch(
    *,
    current_runtime: int | None,
    current_overview: str | None,
    details: dict[str, Any],
    fill_runtime: bool,
    fill_overview: bool,
) -> tuple[bool, int | None, str | None]:
    changed = False
    runtime_out = current_runtime
    overview_out = current_overview

    if fill_runtime and current_runtime is None:
        runtime = details.get("runtime_minutes")
        if isinstance(runtime, int) and runtime > 0:
            runtime_out = runtime
            changed = True

    if fill_overview and _is_blank_text(current_overview):
        overview = details.get("overview")
        if isinstance(overview, str):
            normalized = overview.strip()
            if normalized:
                overview_out = normalized
                changed = True

    return changed, runtime_out, overview_out


def _missing_filter_clause(*, fill_runtime: bool, fill_overview: bool):
    clauses = []
    if fill_runtime:
        clauses.append(Title.runtime_minutes.is_(None))
    if fill_overview:
        clauses.append(
            sa.or_(
                Title.overview.is_(None),
                sa.func.length(sa.func.trim(Title.overview)) == 0,
            )
        )
    if not clauses:
        raise ValueError("At least one fill target must be enabled")
    return sa.or_(*clauses)


async def _load_batch(
    db: AsyncSession,
    *,
    after_id: UUID | None,
    batch_size: int,
    filter_clause,
) -> list[Title]:
    q = (
        select(Title)
        .where(
            Title.source == "tmdb",
            Title.source_id.is_not(None),
            filter_clause,
        )
        .order_by(Title.id.asc())
        .limit(batch_size)
    )
    if after_id is not None:
        q = q.where(Title.id > after_id)
    return list((await db.execute(q)).scalars())


async def run_backfill(
    db: AsyncSession,
    *,
    apply: bool,
    batch_size: int,
    max_items: int | None,
    sleep_ms: int,
    fill_runtime: bool,
    fill_overview: bool,
    verbose: bool,
    details_fetcher: DetailsFetcher = fetch_tmdb_title_details,
) -> BackfillStats:
    if batch_size <= 0:
        raise ValueError("--batch-size must be greater than 0")
    if max_items is not None and max_items <= 0:
        raise ValueError("--max-items must be greater than 0 when provided")

    stats = BackfillStats()
    filter_clause = _missing_filter_clause(fill_runtime=fill_runtime, fill_overview=fill_overview)
    after_id: UUID | None = None
    sleep_seconds = max(0, sleep_ms) / 1000

    done = False
    while not done:
        batch = await _load_batch(
            db,
            after_id=after_id,
            batch_size=batch_size,
            filter_clause=filter_clause,
        )
        if not batch:
            break

        for title in batch:
            if max_items is not None and stats.scanned >= max_items:
                done = True
                break

            stats.scanned += 1
            tmdb_id = _parse_tmdb_id(title.source_id)
            if tmdb_id is None:
                stats.skipped_invalid_source_id += 1
                if verbose:
                    logger.info("skip invalid source_id title_id=%s source_id=%r", title.id, title.source_id)
                continue

            try:
                details = await details_fetcher(tmdb_id=tmdb_id, media_type=title.media_type)
            except Exception:
                stats.fetch_errors += 1
                logger.exception("tmdb fetch failed title_id=%s tmdb_id=%s", title.id, tmdb_id)
                continue

            changed, runtime_out, overview_out = _derive_patch(
                current_runtime=title.runtime_minutes,
                current_overview=title.overview,
                details=details if isinstance(details, dict) else {},
                fill_runtime=fill_runtime,
                fill_overview=fill_overview,
            )
            if not changed:
                stats.unchanged += 1
            else:
                stats.would_update += 1
                if verbose:
                    logger.info(
                        "candidate update title_id=%s tmdb_id=%s runtime=%r overview=%r",
                        title.id,
                        tmdb_id,
                        runtime_out,
                        bool(overview_out),
                    )
                if apply:
                    title.runtime_minutes = runtime_out
                    title.overview = overview_out
                    stats.updated += 1

            if sleep_seconds > 0:
                await asyncio.sleep(sleep_seconds)

        after_id = batch[-1].id
        if apply:
            try:
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        else:
            # End the read transaction and release any snapshot state.
            await db.rollback()

    return stats


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill missing TMDB title runtime_minutes and overview in titles table."
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--apply",
        action="store_true",
        help="Persist changes. Without this flag, the script runs in dry-run mode.",
    )
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicitly run in dry-run mode (default behavior).",
    )
    parser.add_argument("--batch-size", type=int, default=100, help="Number of titles processed per batch.")
    parser.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="Optional cap for number of rows scanned.",
    )
    parser.add_argument(
        "--sleep-ms",
        type=int,
        default=100,
        help="Optional sleep between TMDB requests in milliseconds.",
    )
    parser.add_argument(
        "--only-missing-runtime",
        action="store_true",
        help="Only fill runtime_minutes for rows where runtime is null.",
    )
    parser.add_argument(
        "--only-missing-overview",
        action="store_true",
        help="Only fill overview for rows where overview is blank or null.",
    )
    parser.add_argument("--verbose", action="store_true", help="Log row-level actions.")
    args = parser.parse_args()

    if args.only_missing_runtime and args.only_missing_overview:
        parser.error("Choose at most one of --only-missing-runtime or --only-missing-overview.")

    return args


def _print_summary(*, apply: bool, stats: BackfillStats) -> None:
    mode = "apply" if apply else "dry-run"
    print("TMDB title details backfill complete")
    print(f"mode: {mode}")
    print(f"scanned: {stats.scanned}")
    print(f"would_update: {stats.would_update}")
    print(f"updated: {stats.updated}")
    print(f"unchanged: {stats.unchanged}")
    print(f"skipped_invalid_source_id: {stats.skipped_invalid_source_id}")
    print(f"fetch_errors: {stats.fetch_errors}")


async def _main_async(args: argparse.Namespace) -> BackfillStats:
    fill_runtime = not args.only_missing_overview
    fill_overview = not args.only_missing_runtime

    async with AsyncSessionLocal() as db:
        return await run_backfill(
            db,
            apply=args.apply,
            batch_size=args.batch_size,
            max_items=args.max_items,
            sleep_ms=args.sleep_ms,
            fill_runtime=fill_runtime,
            fill_overview=fill_overview,
            verbose=args.verbose,
        )


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    stats = asyncio.run(_main_async(args))
    _print_summary(apply=args.apply, stats=stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
