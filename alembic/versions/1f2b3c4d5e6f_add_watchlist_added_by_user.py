"""add watchlist added_by_user_id

Revision ID: 1f2b3c4d5e6f
Revises: fd73bbdbfc92
Create Date: 2026-01-27 19:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "1f2b3c4d5e6f"
down_revision: Union[str, None] = "fd73bbdbfc92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "watchlist_items",
        sa.Column("added_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_watchlist_items_added_by_user_id",
        "watchlist_items",
        ["added_by_user_id"],
        unique=False,
    )
    op.create_foreign_key(
        "watchlist_items_added_by_user_id_fkey",
        "watchlist_items",
        "users",
        ["added_by_user_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "watchlist_items_added_by_user_id_fkey",
        "watchlist_items",
        type_="foreignkey",
    )
    op.drop_index("ix_watchlist_items_added_by_user_id", table_name="watchlist_items")
    op.drop_column("watchlist_items", "added_by_user_id")
