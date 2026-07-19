"""remove legacy invite credentials

Revision ID: f6a8b0c2d4e6
Revises: e5f7a9c1d3b5
Create Date: 2026-07-19

"""
from typing import Sequence
import uuid

from alembic import op
import sqlalchemy as sa


revision: str = "f6a8b0c2d4e6"
down_revision: str | None = "e5f7a9c1d3b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Public link/code invitations are intentionally discarded. Only targeted,
    # authenticated account requests remain in the product.
    op.execute(sa.text("DELETE FROM friend_invites WHERE target_user_id IS NULL"))
    op.execute(sa.text("DELETE FROM group_invites WHERE target_user_id IS NULL"))

    with op.batch_alter_table("friend_invites") as batch_op:
        batch_op.drop_index("ix_friend_invites_code")
        batch_op.drop_index("ix_friend_invites_token_hash")
        batch_op.alter_column("target_user_id", existing_type=sa.Uuid(), nullable=False)
        batch_op.drop_column("code")
        batch_op.drop_column("token_hash")

    with op.batch_alter_table("group_invites") as batch_op:
        batch_op.drop_index("ix_group_invites_code")
        batch_op.drop_index("ix_group_invites_token_hash")
        batch_op.alter_column("target_user_id", existing_type=sa.Uuid(), nullable=False)
        batch_op.drop_column("code")
        batch_op.drop_column("token_hash")


def _backfill_codes(table_name: str) -> None:
    connection = op.get_bind()
    table = sa.table(
        table_name,
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
    )
    ids = connection.execute(sa.select(table.c.id)).scalars().all()
    for record_id in ids:
        connection.execute(
            table.update()
            .where(table.c.id == record_id)
            .values(code=uuid.uuid4().hex[:16])
        )


def downgrade() -> None:
    with op.batch_alter_table("group_invites") as batch_op:
        batch_op.add_column(sa.Column("code", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("token_hash", sa.String(length=64), nullable=True))
        batch_op.alter_column("target_user_id", existing_type=sa.Uuid(), nullable=True)
    _backfill_codes("group_invites")
    with op.batch_alter_table("group_invites") as batch_op:
        batch_op.alter_column("code", existing_type=sa.String(length=32), nullable=False)
        batch_op.create_index("ix_group_invites_code", ["code"], unique=True)
        batch_op.create_index("ix_group_invites_token_hash", ["token_hash"], unique=True)

    with op.batch_alter_table("friend_invites") as batch_op:
        batch_op.add_column(sa.Column("code", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("token_hash", sa.String(length=64), nullable=True))
        batch_op.alter_column("target_user_id", existing_type=sa.Uuid(), nullable=True)
    _backfill_codes("friend_invites")
    with op.batch_alter_table("friend_invites") as batch_op:
        batch_op.alter_column("code", existing_type=sa.String(length=16), nullable=False)
        batch_op.create_index("ix_friend_invites_code", ["code"], unique=True)
        batch_op.create_index("ix_friend_invites_token_hash", ["token_hash"], unique=True)
