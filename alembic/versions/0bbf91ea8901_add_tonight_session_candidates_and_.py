"""add tonight session candidates and lifecycle fields

Revision ID: 0bbf91ea8901
Revises: 680581f9c333
Create Date: 2026-01-21 15:59:57.693072

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0bbf91ea8901'
down_revision: Union[str, None] = '680581f9c333'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


from sqlalchemy.dialects.postgresql import UUID


def upgrade():
    op.add_column(
        "tonight_sessions",
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column(
        "tonight_sessions",
        sa.Column("duration_seconds", sa.Integer(), nullable=False, server_default="90"),
    )
    op.add_column(
        "tonight_sessions",
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="12"),
    )
    op.add_column(
        "tonight_sessions",
        sa.Column("ai_why", sa.Text(), nullable=True),
    )
    op.add_column(
        "tonight_sessions",
        sa.Column(
            "ai_used",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    op.create_table(
        "tonight_session_candidates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tonight_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "watchlist_item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("watchlist_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("ai_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "session_id",
            "watchlist_item_id",
            name="uq_session_candidate_unique",
        ),
        sa.UniqueConstraint(
            "session_id",
            "position",
            name="uq_session_candidate_position",
        ),
    )

    op.create_index(
        "ix_tonight_session_candidates_session_id",
        "tonight_session_candidates",
        ["session_id"],
    )
    op.create_index(
        "ix_tonight_session_candidates_watchlist_item_id",
        "tonight_session_candidates",
        ["watchlist_item_id"],
    )
    op.create_index(
        "ix_session_candidates_session_position",
        "tonight_session_candidates",
        ["session_id", "position"],
    )



def downgrade():
    op.drop_index(
        "ix_session_candidates_session_position",
        table_name="tonight_session_candidates",
    )
    op.drop_index(
        "ix_tonight_session_candidates_watchlist_item_id",
        table_name="tonight_session_candidates",
    )
    op.drop_index(
        "ix_tonight_session_candidates_session_id",
        table_name="tonight_session_candidates",
    )
    op.drop_table("tonight_session_candidates")

    op.drop_column("tonight_sessions", "ai_used")
    op.drop_column("tonight_sessions", "ai_why")
    op.drop_column("tonight_sessions", "candidate_count")
    op.drop_column("tonight_sessions", "duration_seconds")
    op.drop_column("tonight_sessions", "ends_at")
