"""add tonight_sessions

Revision ID: 36d0c16f59a0
Revises: 505d008ab2b5
Create Date: 2026-01-21 10:04:31.119683

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB



# revision identifiers, used by Alembic.
revision: str = '36d0c16f59a0'
down_revision: Union[str, None] = '505d008ab2b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "tonight_sessions",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "group_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "constraints",
            JSONB,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_index("ix_tonight_sessions_group_id", "tonight_sessions", ["group_id"])
    op.create_index(
        "ix_tonight_sessions_created_by_user_id",
        "tonight_sessions",
        ["created_by_user_id"],
    )



def downgrade():
    op.drop_index("ix_tonight_sessions_created_by_user_id", table_name="tonight_sessions")
    op.drop_index("ix_tonight_sessions_group_id", table_name="tonight_sessions")
    op.drop_table("tonight_sessions")
