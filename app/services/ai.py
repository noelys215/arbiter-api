from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings
from app.schemas.tonight_constraints import TonightConstraints


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


class AIUnavailable(RuntimeError):
    pass


def _has_openai() -> bool:
    if settings.env == "test":
        return False
    return bool(getattr(settings, "openai_api_key", None))


async def _openai_response(payload: dict[str, Any]) -> dict[str, Any]:
    if not _has_openai():
        raise AIUnavailable("OPENAI_API_KEY not set")

    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(OPENAI_RESPONSES_URL, headers=headers, json=payload)
        r.raise_for_status()
        return r.json()


async def parse_constraints_with_ai(
    *,
    base: TonightConstraints,
    text: str,
) -> TonightConstraints:
    """
    Given UI constraints + freeform text, return refined constraints.
    MUST NOT relax hard constraints in a way that violates schema defaults.
    """
    if not _has_openai():
        # No AI available; treat as not parsed by AI, keep free_text
        base.free_text = (text or "").strip()
        base.parsed_by_ai = False
        return base

    prompt = f"""
You are parsing movie-night preference text into a strict JSON schema.

Schema (JSON):
{{
  "moods": [string],
  "avoid": [string],
  "max_runtime": integer|null,
  "format": "movie"|"tv"|"any",
  "energy": "low"|"med"|"high"|null,
  "free_text": string,
  "parsed_by_ai": boolean,
  "ai_version": string|null
}}

Rules:
- Return ONLY valid JSON for that schema.
- Keep existing UI constraints unless the text clearly tightens them.
- Never invent runtime if not mentioned.
- If user says "no TV" => format="movie". If user says "show" or "series" => format="tv".
- free_text should preserve the raw text.
"""

    # We prefer a deterministic output; temperature 0
    payload = {
        "model": settings.openai_model,
        "input": [
            {"role": "system", "content": prompt.strip()},
            {
                "role": "user",
                "content": {
                    "ui_constraints": base.model_dump(),
                    "text": text,
                },
            },
        ],
        "temperature": 0,
    }

    try:
        data = await _openai_response(payload)
    except (AIUnavailable, httpx.HTTPError):
        base.free_text = (text or "").strip()
        base.parsed_by_ai = False
        return base

    # Responses API output can vary; this is a best-effort extractor.
    # In practice, you’ll likely standardize this later.
    output_text = ""
    try:
        # common shape
        for item in data.get("output", []):
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    output_text += c.get("text", "")
    except Exception:
        output_text = ""

    if not output_text.strip():
        # fallback: no parse
        base.free_text = (text or "").strip()
        base.parsed_by_ai = False
        return base

    # Validate as canonical TonightConstraints
    refined = TonightConstraints.model_validate_json(output_text)
    refined.parsed_by_ai = True
    refined.ai_version = settings.openai_model
    return refined


async def rerank_candidates_with_ai(
    *,
    constraints: TonightConstraints,
    candidates: list[dict[str, Any]],
    final_n: int,
) -> tuple[list[int], str | None]:
    """
    candidates: list of dicts with at least:
    - "idx": stable integer index
    - "title": string
    - "media_type": "movie"|"tv"
    - "year": int|None
    returns: (ordered_idxs, optional why)
    """
    if not _has_openai():
        # no AI: keep order
        return [c["idx"] for c in candidates[:final_n]], None

    prompt = f"""
You are selecting the best {final_n} items for a movie-night deck.

Constraints:
{constraints.model_dump()}

You will receive a list of candidates with an integer "idx". Return JSON ONLY:
{{
  "ordered_idxs": [int],   // length exactly {final_n}
  "why": "short 1-2 sentence summary"
}}

Rules:
- You MUST return exactly {final_n} unique idx values from the candidate list.
- Do not include anything besides JSON.
"""

    payload = {
        "model": settings.openai_model,
        "input": [
            {"role": "system", "content": prompt.strip()},
            {"role": "user", "content": {"candidates": candidates}},
        ],
        "temperature": 0,
    }

    try:
        data = await _openai_response(payload)
    except (AIUnavailable, httpx.HTTPError):
        return [c["idx"] for c in candidates[:final_n]], None

    output_text = ""
    try:
        for item in data.get("output", []):
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    output_text += c.get("text", "")
    except Exception:
        output_text = ""

    if not output_text.strip():
        return [c["idx"] for c in candidates[:final_n]], None

    # Parse returned JSON safely via Pydantic-ish lightweight approach
    import json

    obj = json.loads(output_text)
    ordered = obj.get("ordered_idxs", [])
    why = obj.get("why")

    # Guardrails
    allowed = {c["idx"] for c in candidates}
    ordered = [i for i in ordered if i in allowed]
    dedup: list[int] = []
    seen: set[int] = set()
    for i in ordered:
        if i in seen:
            continue
        seen.add(i)
        dedup.append(i)

    if len(dedup) < final_n:
        # fill deterministically from original order
        for c in candidates:
            if c["idx"] not in seen:
                seen.add(c["idx"])
                dedup.append(c["idx"])
            if len(dedup) == final_n:
                break

    return dedup[:final_n], why
