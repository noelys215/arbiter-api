from __future__ import annotations

import re


USERNAME_MAX_LENGTH = 50
USERNAME_PATTERN = re.compile(r"^[a-z0-9_]{3,50}$")


def canonicalize_username(value: str) -> str:
    normalized = value.strip()
    if normalized.startswith("@"):
        normalized = normalized[1:]
    return normalized.lower()


def is_valid_username(value: str) -> bool:
    return USERNAME_PATTERN.fullmatch(value) is not None
