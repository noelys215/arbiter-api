"""add targeted friend requests

Revision ID: e5f7a9c1d3b5
Revises: d4e6f8a0b2c4
Create Date: 2026-07-19

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e5f7a9c1d3b5"
down_revision: str | None = "d4e6f8a0b2c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("friend_invites") as batch_op:
        batch_op.add_column(
            sa.Column("target_user_id", sa.Uuid(as_uuid=True), nullable=True)
        )
        batch_op.create_index(
            "ix_friend_invites_target_user_id", ["target_user_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_friend_invites_target_user_id_users",
            "users",
            ["target_user_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    with op.batch_alter_table("friend_invites") as batch_op:
        batch_op.drop_constraint(
            "fk_friend_invites_target_user_id_users", type_="foreignkey"
        )
        batch_op.drop_index("ix_friend_invites_target_user_id")
        batch_op.drop_column("target_user_id")
