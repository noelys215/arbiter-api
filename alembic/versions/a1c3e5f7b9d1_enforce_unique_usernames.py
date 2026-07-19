"""enforce case-insensitive unique account identifiers

Revision ID: a1c3e5f7b9d1
Revises: f6a8b0c2d4e6
Create Date: 2026-07-19

"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a1c3e5f7b9d1"
down_revision: str | None = "f6a8b0c2d4e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _deduplicate_usernames() -> None:
    connection = op.get_bind()
    users = sa.table(
        "users",
        sa.column("id", sa.Uuid()),
        sa.column("username", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    records = connection.execute(
        sa.select(
            users.c.id,
            users.c.username,
        ).order_by(users.c.created_at, users.c.id)
    ).all()

    used_usernames: set[str] = set()
    reserved_usernames = {username.casefold() for _, username in records}
    for user_id, username in records:
        new_username = username
        if new_username.casefold() in used_usernames:
            attempt = 1
            while True:
                attempt += 1
                suffix = f"_{attempt}"
                new_username = f"{username[: 50 - len(suffix)]}{suffix}"
                if (
                    new_username.casefold() not in used_usernames
                    and new_username.casefold() not in reserved_usernames
                ):
                    break

        used_usernames.add(new_username.casefold())
        if new_username != username:
            connection.execute(
                users.update()
                .where(users.c.id == user_id)
                .values(username=new_username)
            )


def upgrade() -> None:
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    _deduplicate_usernames()
    op.create_index(
        "uq_users_email_lower",
        "users",
        [sa.text("lower(email)")],
        unique=True,
    )
    op.create_index(
        "uq_users_username_lower",
        "users",
        [sa.text("lower(username)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_users_username_lower", table_name="users")
    op.drop_index("uq_users_email_lower", table_name="users")
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_username", "users", ["username"], unique=True)
