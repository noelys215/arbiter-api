"""add user avatar preference fields

Revision ID: a7c9d2e4f6b8
Revises: c8f9a2d1b4e7
Create Date: 2026-07-15 12:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a7c9d2e4f6b8"
down_revision: Union[str, None] = "c8f9a2d1b4e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("avatar_source", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("avatar_style", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("avatar_seed", sa.String(length=128), nullable=True))
        batch_op.create_check_constraint(
            "ck_users_avatar_source_allowed",
            "avatar_source IS NULL OR avatar_source IN ('provider', 'generated', 'initials')",
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("ck_users_avatar_source_allowed", type_="check")
        batch_op.drop_column("avatar_seed")
        batch_op.drop_column("avatar_style")
        batch_op.drop_column("avatar_source")
