"""convert tonight_sessions.constraints to jsonb

Revision ID: 680581f9c333
Revises: 36d0c16f59a0
Create Date: 2026-01-21 10:40:37.265516

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = "680581f9c333"
down_revision: str | None = "36d0c16f59a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.alter_column(
        "tonight_sessions",
        "constraints",
        type_=JSONB,
        postgresql_using="constraints::jsonb",
    )


def downgrade():
    op.alter_column(
        "tonight_sessions",
        "constraints",
        type_=sa.JSON,
        postgresql_using="constraints::json",
    )
