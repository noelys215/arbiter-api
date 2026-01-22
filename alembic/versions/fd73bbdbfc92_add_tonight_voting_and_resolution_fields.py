"""add tonight voting and resolution fields

Revision ID: fd73bbdbfc92
Revises: 0bbf91ea8901
Create Date: 2026-01-21 18:48:38

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "fd73bbdbfc92"
down_revision: Union[str, None] = "0bbf91ea8901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade():
    # tonight_sessions fields
    op.add_column("tonight_sessions", sa.Column("status", sa.String(20), nullable=False, server_default="active"))
    op.add_column("tonight_sessions", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "tonight_sessions",
        sa.Column(
            "result_watchlist_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("watchlist_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    op.create_index("ix_tonight_sessions_status", "tonight_sessions", ["status"])
    op.create_index("ix_tonight_sessions_ends_at", "tonight_sessions", ["ends_at"])

    op.create_table(
        "tonight_votes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tonight_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("watchlist_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("watchlist_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vote", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("vote IN ('yes','no')", name="ck_tonight_votes_vote"),
        sa.UniqueConstraint("session_id", "user_id", name="uq_tonight_votes_session_user"),
    )
    op.create_index("ix_tonight_votes_session_id", "tonight_votes", ["session_id"])
    op.create_index("ix_tonight_votes_session_item", "tonight_votes", ["session_id", "watchlist_item_id"])

def downgrade():
    op.drop_index("ix_tonight_votes_session_item", table_name="tonight_votes")
    op.drop_index("ix_tonight_votes_session_id", table_name="tonight_votes")
    op.drop_table("tonight_votes")

    op.drop_index("ix_tonight_sessions_ends_at", table_name="tonight_sessions")
    op.drop_index("ix_tonight_sessions_status", table_name="tonight_sessions")
    op.drop_column("tonight_sessions", "result_watchlist_item_id")
    op.drop_column("tonight_sessions", "completed_at")
    op.drop_column("tonight_sessions", "status")
