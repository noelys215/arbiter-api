"""enforce case-insensitive unique account names

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


def _deduplicate_account_names() -> None:
    connection = op.get_bind()
    users = sa.table(
        "users",
        sa.column("id", sa.Uuid()),
        sa.column("username", sa.String()),
        sa.column("display_name", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    records = connection.execute(
        sa.select(
            users.c.id,
            users.c.username,
            users.c.display_name,
        ).order_by(users.c.created_at, users.c.id)
    ).all()

    used_usernames: set[str] = set()
    used_display_names: set[str] = set()
    reserved_usernames = {username.casefold() for _, username, _ in records}
    reserved_display_names = {display_name.casefold() for _, _, display_name in records}
    for user_id, username, display_name in records:
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

        new_display_name = display_name
        if new_display_name.casefold() in used_display_names:
            attempt = 1
            while True:
                attempt += 1
                suffix = f" ({attempt})"
                new_display_name = f"{display_name[: 120 - len(suffix)]}{suffix}"
                if (
                    new_display_name.casefold() not in used_display_names
                    and new_display_name.casefold() not in reserved_display_names
                ):
                    break

        used_usernames.add(new_username.casefold())
        used_display_names.add(new_display_name.casefold())
        if new_username != username or new_display_name != display_name:
            connection.execute(
                users.update()
                .where(users.c.id == user_id)
                .values(username=new_username, display_name=new_display_name)
            )


def upgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    _deduplicate_account_names()
    op.create_index(
        "uq_users_username_lower",
        "users",
        [sa.text("lower(username)")],
        unique=True,
    )
    op.create_index(
        "uq_users_display_name_lower",
        "users",
        [sa.text("lower(display_name)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_users_display_name_lower", table_name="users")
    op.drop_index("uq_users_username_lower", table_name="users")
    op.create_index("ix_users_username", "users", ["username"], unique=True)
