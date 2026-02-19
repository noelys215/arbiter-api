from __future__ import annotations

from collections.abc import Mapping

from fastapi import HTTPException


def permission_error(exc: PermissionError) -> HTTPException:
    return HTTPException(status_code=403, detail=str(exc))


def value_error(
    exc: ValueError,
    *,
    phrase_statuses: Mapping[str, int] | None = None,
    code_statuses: Mapping[str, int] | None = None,
    detail_overrides: Mapping[str, str] | None = None,
    default_status: int = 400,
    default_detail: str | None = None,
) -> HTTPException:
    raw_detail = str(exc)

    if code_statuses and raw_detail in code_statuses:
        detail = (
            detail_overrides[raw_detail]
            if detail_overrides and raw_detail in detail_overrides
            else raw_detail
        )
        return HTTPException(status_code=code_statuses[raw_detail], detail=detail)

    lowered = raw_detail.lower()
    if phrase_statuses:
        # Keep matching simple and explicit: first matching phrase wins.
        for phrase, status in phrase_statuses.items():
            if phrase in lowered:
                detail = (
                    detail_overrides[phrase]
                    if detail_overrides and phrase in detail_overrides
                    else raw_detail
                )
                return HTTPException(status_code=status, detail=detail)

    return HTTPException(
        status_code=default_status,
        detail=default_detail if default_detail is not None else raw_detail,
    )
