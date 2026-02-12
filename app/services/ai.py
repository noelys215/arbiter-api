from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings
from app.schemas.tonight_constraints import TonightConstraints


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_TIMEOUT_SECONDS = 8.0

logger = logging.getLogger(__name__)


class AIError(RuntimeError):
    pass


@dataclass
class AIRerankResult:
    ordered_ids: list[str]
    top_id: str | None
    why: str | None


def _auth_headers() -> dict[str, str]:
    if not settings.openai_api_key:
        raise AIError("OPENAI_API_KEY missing")
    return {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }


def _extract_output_text(data: dict[str, Any]) -> str:
    chunks: list[str] = []
    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    chunks.append(c.get("text", ""))
    if chunks:
        return "".join(chunks)
    output_text = data.get("output_text")
    if isinstance(output_text, str):
        return output_text
    return ""


def _log_failure(correlation_id: str, message: str, exc: Exception | None = None) -> None:
    if exc:
        logger.warning("%s correlation_id=%s", message, correlation_id, exc_info=exc)
    else:
        logger.warning("%s correlation_id=%s", message, correlation_id)


async def _post_openai_json(payload: dict[str, Any]) -> dict[str, Any]:
    correlation_id = str(uuid.uuid4())
    try:
        headers = _auth_headers()
    except AIError as exc:
        _log_failure(correlation_id, str(exc), exc)
        raise

    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=OPENAI_TIMEOUT_SECONDS) as client:
                resp = await client.post(OPENAI_RESPONSES_URL, headers=headers, json=payload)

            status = resp.status_code
            if status in (408, 429) or status >= 500:
                raise AIError(f"OpenAI transient error {status}")
            if status >= 400:
                raise AIError(f"OpenAI request failed with status {status}")

            data = resp.json()
            output_text = _extract_output_text(data).strip()
            if not output_text:
                raise ValueError("Empty OpenAI output")

            return json.loads(output_text)
        except AIError as exc:
            is_transient = "transient" in str(exc)
            _log_failure(correlation_id, "OpenAI request failed", exc)
            if attempt == 0 and is_transient:
                continue
            raise AIError(f"{exc} (correlation_id={correlation_id})") from exc
        except (json.JSONDecodeError, ValueError) as exc:
            _log_failure(correlation_id, "OpenAI returned invalid JSON", exc)
            if attempt == 0:
                continue
            raise AIError(f"OpenAI returned invalid JSON (correlation_id={correlation_id})") from exc
        except httpx.RequestError as exc:
            _log_failure(correlation_id, "OpenAI request error", exc)
            if attempt == 0:
                continue
            raise AIError(f"OpenAI request error (correlation_id={correlation_id})") from exc

    raise AIError(f"OpenAI request failed (correlation_id={correlation_id})")


async def ai_parse_constraints(*, baseline: TonightConstraints, text: str) -> TonightConstraints:
    prompt = """
You are parsing preference text into a strict JSON object.

Return ONLY JSON with optional keys from this set:
- "moods": [string]
- "avoid": [string]
- "max_runtime": integer|null
- "format": "movie"|"tv"|"any"
- "energy": "low"|"med"|"high"|null

Rules:
- Never return keys outside the allowed set.
- Do not invent runtime unless explicitly suggested.
- Be concise; no extra text.
"""

    user_payload = json.dumps(
        {
            "ui_constraints": baseline.model_dump(),
            "text": text,
        },
        ensure_ascii=True,
    )

    payload = {
        "model": settings.openai_model,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": prompt.strip()}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": user_payload}],
            },
        ],
        "temperature": 0.2,
    }

    data = await _post_openai_json(payload)
    if not isinstance(data, dict):
        raise AIError("OpenAI returned unexpected JSON shape")

    merged = baseline.model_dump()
    for key in ("moods", "avoid", "max_runtime", "format", "energy"):
        if key in data:
            merged[key] = data[key]

    merged["free_text"] = (text or "").strip()
    merged["parsed_by_ai"] = True
    merged["ai_version"] = settings.openai_model

    try:
        return TonightConstraints.model_validate(merged)
    except Exception as exc:
        raise AIError("AI constraints failed validation") from exc


async def ai_rerank_candidates(
    *, constraints: TonightConstraints, candidates: list[dict[str, Any]]
) -> AIRerankResult:
    if not candidates:
        raise AIError("No candidates to rerank")

    candidate_ids = [str(c.get("id")) for c in candidates if c.get("id") is not None]
    if not candidate_ids:
        raise AIError("Candidates missing ids")

    prompt = """
You are ranking watchlist items for a session.

Return ONLY JSON in this exact shape:
{
  "ordered_ids": ["<id>", "..."],
  "top_id": "<id-or-null>",
  "why": "short 1-2 sentences or null"
}

Rules:
- ordered_ids MUST contain only ids from the provided candidate list.
- Do not include anything other than JSON.
"""

    user_payload = json.dumps(
        {
            "constraints": constraints.model_dump(),
            "candidates": candidates,
        },
        ensure_ascii=True,
    )

    payload = {
        "model": settings.openai_model,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": prompt.strip()}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": user_payload}],
            },
        ],
        "temperature": 0.2,
    }

    data = await _post_openai_json(payload)
    if not isinstance(data, dict):
        raise AIError("OpenAI returned unexpected JSON shape")

    ordered_ids = data.get("ordered_ids", [])
    top_id = data.get("top_id")
    why = data.get("why")

    if not isinstance(ordered_ids, list):
        raise AIError("ordered_ids missing or invalid")

    allowed = set(candidate_ids)
    filtered: list[str] = []
    seen: set[str] = set()
    for item_id in ordered_ids:
        if not isinstance(item_id, str):
            continue
        if item_id not in allowed or item_id in seen:
            continue
        seen.add(item_id)
        filtered.append(item_id)

    min_valid = min(3, len(candidate_ids))
    if len(filtered) < min_valid or len(filtered) < (len(candidate_ids) // 2 + 1):
        raise AIError("AI rerank returned too few valid ids")

    if not isinstance(top_id, str) or top_id not in allowed:
        top_id = None

    if not isinstance(why, str):
        why = None
    elif len(why) > 280:
        why = why[:280]

    return AIRerankResult(ordered_ids=filtered, top_id=top_id, why=why)
