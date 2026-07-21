"""complete user deletion referential behavior

Revision ID: f1b3d5e7a9c1
Revises: e9a1b3c5d7f9
Create Date: 2026-07-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f1b3d5e7a9c1"
down_revision: str | None = "e9a1b3c5d7f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace_user_fk(
    table: str,
    constraint: str,
    column: str,
    *,
    ondelete: str | None,
) -> None:
    with op.batch_alter_table(table) as batch_op:
        batch_op.drop_constraint(constraint, type_="foreignkey")
        batch_op.create_foreign_key(
            constraint,
            "users",
            [column],
            ["id"],
            ondelete=ondelete,
        )


def upgrade() -> None:
    _replace_user_fk(
        "friend_invites",
        "friend_invites_created_by_user_id_fkey",
        "created_by_user_id",
        ondelete="CASCADE",
    )
    _replace_user_fk(
        "friendships", "friendships_user_low_id_fkey", "user_low_id", ondelete="CASCADE"
    )
    _replace_user_fk(
        "friendships", "friendships_user_high_id_fkey", "user_high_id", ondelete="CASCADE"
    )
    _replace_user_fk(
        "group_memberships",
        "group_memberships_user_id_fkey",
        "user_id",
        ondelete="CASCADE",
    )
    _replace_user_fk(
        "group_invites",
        "group_invites_created_by_user_id_fkey",
        "created_by_user_id",
        ondelete="CASCADE",
    )
    _replace_user_fk(
        "watchlist_items",
        "watchlist_items_added_by_user_id_fkey",
        "added_by_user_id",
        ondelete="SET NULL",
    )
    with op.batch_alter_table("tonight_sessions") as batch_op:
        batch_op.drop_constraint(
            "tonight_sessions_created_by_user_id_fkey", type_="foreignkey"
        )
        batch_op.alter_column(
            "created_by_user_id", existing_type=sa.Uuid(), nullable=True
        )
        batch_op.create_foreign_key(
            "tonight_sessions_created_by_user_id_fkey",
            "users",
            ["created_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    # A deleted creator cannot be reconstructed. Refuse downgrade if nullable
    # historical creators now exist rather than fabricating ownership.
    connection = op.get_bind()
    missing = connection.execute(
        sa.text(
            "SELECT 1 FROM tonight_sessions WHERE created_by_user_id IS NULL LIMIT 1"
        )
    ).scalar_one_or_none()
    if missing is not None:
        raise RuntimeError(
            "Cannot restore non-null session creators after account deletion"
        )

    with op.batch_alter_table("tonight_sessions") as batch_op:
        batch_op.drop_constraint(
            "tonight_sessions_created_by_user_id_fkey", type_="foreignkey"
        )
        batch_op.alter_column(
            "created_by_user_id", existing_type=sa.Uuid(), nullable=False
        )
        batch_op.create_foreign_key(
            "tonight_sessions_created_by_user_id_fkey",
            "users",
            ["created_by_user_id"],
            ["id"],
        )
    _replace_user_fk(
        "watchlist_items",
        "watchlist_items_added_by_user_id_fkey",
        "added_by_user_id",
        ondelete=None,
    )
    _replace_user_fk(
        "group_invites",
        "group_invites_created_by_user_id_fkey",
        "created_by_user_id",
        ondelete=None,
    )
    _replace_user_fk(
        "group_memberships", "group_memberships_user_id_fkey", "user_id", ondelete=None
    )
    _replace_user_fk(
        "friendships", "friendships_user_high_id_fkey", "user_high_id", ondelete=None
    )
    _replace_user_fk(
        "friendships", "friendships_user_low_id_fkey", "user_low_id", ondelete=None
    )
    _replace_user_fk(
        "friend_invites",
        "friend_invites_created_by_user_id_fkey",
        "created_by_user_id",
        ondelete=None,
    )
