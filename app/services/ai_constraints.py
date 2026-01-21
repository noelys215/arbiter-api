from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.schemas.tonight_constraints import TonightConstraints


@dataclass
class AIRerankResult:
    ordered_ids: list[str]          # watchlist_item_id strings in desired order
    why: str | None = None


async def ai_parse_constraints(
    *,
    baseline: TonightConstraints,
    text: str,
) -> TonightConstraints:
    """
    v1 rule: AI is allowed to *narrow* or *fill blanks*, but not to widen beyond explicit UI constraints.
    - If UI picked format != any, AI cannot change it.
    - If UI set max_runtime, AI cannot increase it.
    - AI can add moods/avoid, set energy, set max_runtime if it was null, set format if it was 'any'.
    """
    text = (text or "").strip()
    if not text:
        return baseline

    # If no key, just store the text deterministically
    if not getattr(settings, "openai_api_key", None):
        out = baseline.model_copy(deep=True)
        out.free_text = text
        out.parsed_by_ai = False
        return out

    # --- Placeholder implementation ---
    # In v1: keep it deterministic + easy to test by monkeypatching this function.
    # When you’re ready, replace this with a real OpenAI call.
    out = baseline.model_copy(deep=True)
    out.free_text = text
    out.parsed_by_ai = True
    out.ai_version = settings.openai_model
    return out


async def ai_rerank_candidates(
    *,
    constraints: TonightConstraints,
    candidates: list[dict[str, Any]],
    pick_n: int,
) -> AIRerankResult:
    """
    candidates items include:
      - watchlist_item_id (str)
      - title (str)
      - year (int|None)
      - media_type (movie|tv)
      - overview (str|None)
    """
    if pick_n <= 0:
        return AIRerankResult(ordered_ids=[], why=None)

    # If no key, return deterministic ordering (already randomized upstream)
    if not getattr(settings, "openai_api_key", None):
        return AIRerankResult(
            ordered_ids=[c["watchlist_item_id"] for c in candidates[:pick_n]],
            why=None,
        )

    # --- Placeholder implementation ---
    # Replace with real OpenAI rerank later.
    return AIRerankResult(
        ordered_ids=[c["watchlist_item_id"] for c in candidates[:pick_n]],
        why="AI picked a balanced set based on your constraints.",
    )
