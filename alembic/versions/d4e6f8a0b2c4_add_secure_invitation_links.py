"""add secure invitation links

Revision ID: d4e6f8a0b2c4
Revises: a7c9d2e4f6b8
Create Date: 2026-07-15

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d4e6f8a0b2c4"
down_revision: str | None = "a7c9d2e4f6b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("friend_invites") as batch_op:
        batch_op.add_column(sa.Column("token_hash", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_index(
            "ix_friend_invites_token_hash", ["token_hash"], unique=True
        )

    with op.batch_alter_table("group_invites") as batch_op:
        batch_op.add_column(sa.Column("token_hash", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("target_user_id", sa.Uuid(as_uuid=True), nullable=True)
        )
        batch_op.create_index(
            "ix_group_invites_token_hash", ["token_hash"], unique=True
        )
        batch_op.create_index(
            "ix_group_invites_target_user_id", ["target_user_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_group_invites_target_user_id_users",
            "users",
            ["target_user_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    with op.batch_alter_table("group_invites") as batch_op:
        batch_op.drop_constraint(
            "fk_group_invites_target_user_id_users", type_="foreignkey"
        )
        batch_op.drop_index("ix_group_invites_target_user_id")
        batch_op.drop_index("ix_group_invites_token_hash")
        batch_op.drop_column("target_user_id")
        batch_op.drop_column("revoked_at")
        batch_op.drop_column("token_hash")

    with op.batch_alter_table("friend_invites") as batch_op:
        batch_op.drop_index("ix_friend_invites_token_hash")
        batch_op.drop_column("revoked_at")
        batch_op.drop_column("token_hash")
