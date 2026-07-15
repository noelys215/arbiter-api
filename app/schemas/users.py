from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel

AvatarSource = Literal["provider", "generated", "initials"]

AVATAR_SOURCE_VALUES: set[str] = {"provider", "generated", "initials"}
DICEBEAR_AVATAR_STYLES: set[str] = {
    "notionists",
    "adventurer",
    "open-peeps",
    "lorelei",
}
BORING_AVATAR_STYLES: set[str] = {
    "boring-beam",
    "boring-bauhaus",
    "boring-marble",
}
AVATAR_STYLE_VALUES: set[str] = DICEBEAR_AVATAR_STYLES | BORING_AVATAR_STYLES


class AvatarFields(BaseModel):
    avatar_url: str | None = None
    avatar_source: AvatarSource | None = None
    avatar_style: str | None = None
    avatar_seed: str | None = None


class PublicUser(AvatarFields):
    id: UUID
    email: str
    username: str
    display_name: str
