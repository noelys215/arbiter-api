"""add watch party fields to tonight_sessions

Revision ID: c8f9a2d1b4e7
Revises: 1f2b3c4d5e6f
Create Date: 2026-02-19 13:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "c8f9a2d1b4e7"
down_revision: Union[str, None] = "1f2b3c4d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tonight_sessions",
        sa.Column("watch_party_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "tonight_sessions",
        sa.Column("watch_party_set_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tonight_sessions",
        sa.Column("watch_party_set_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "tonight_sessions_watch_party_set_by_user_id_fkey",
        "tonight_sessions",
        "users",
        ["watch_party_set_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "tonight_sessions_watch_party_set_by_user_id_fkey",
        "tonight_sessions",
        type_="foreignkey",
    )
    op.drop_column("tonight_sessions", "watch_party_set_by_user_id")
    op.drop_column("tonight_sessions", "watch_party_set_at")
    op.drop_column("tonight_sessions", "watch_party_url")
