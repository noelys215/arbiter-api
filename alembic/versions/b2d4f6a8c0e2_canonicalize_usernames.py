"""canonicalize stored usernames

Revision ID: b2d4f6a8c0e2
Revises: a1c3e5f7b9d1
Create Date: 2026-07-20

"""
from typing import Sequence
import re

from alembic import op
import sqlalchemy as sa


revision: str = "b2d4f6a8c0e2"
down_revision: str | None = "a1c3e5f7b9d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UNSAFE_USERNAME = re.compile(r"[^a-z0-9_]+")


def _canonicalize_legacy_username(value: str) -> str:
    normalized = value.strip()
    if normalized.startswith("@"):
        normalized = normalized[1:]
    normalized = _UNSAFE_USERNAME.sub("_", normalized.lower()).strip("_")
    return (normalized or "user")[:50]


def upgrade() -> None:
    op.drop_index("uq_users_username_lower", table_name="users")
    connection = op.get_bind()
    users = sa.table(
        "users",
        sa.column("id", sa.Uuid()),
        sa.column("username", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    records = connection.execute(
        sa.select(users.c.id, users.c.username).order_by(
            users.c.created_at, users.c.id
        )
    ).all()
    reserved = {_canonicalize_legacy_username(username) for _, username in records}
    used: set[str] = set()

    ordered_records = sorted(
        records,
        key=lambda row: _canonicalize_legacy_username(row.username) != row.username,
    )
    for user_id, username in ordered_records:
        canonical = _canonicalize_legacy_username(username)
        if canonical in used:
            attempt = 2
            while True:
                suffix = f"_{attempt}"
                candidate = f"{canonical[: 50 - len(suffix)]}{suffix}"
                if candidate not in used and candidate not in reserved:
                    canonical = candidate
                    break
                attempt += 1
        used.add(canonical)
        if canonical != username:
            connection.execute(
                users.update()
                .where(users.c.id == user_id)
                .values(username=canonical)
            )
    op.create_index(
        "uq_users_username_lower",
        "users",
        [sa.text("lower(username)")],
        unique=True,
    )


def downgrade() -> None:
    # Canonical casing and removed presentation prefixes cannot be reconstructed.
    pass
