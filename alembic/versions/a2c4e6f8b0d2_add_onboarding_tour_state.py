"""add onboarding tour state

Revision ID: a2c4e6f8b0d2
Revises: f1b3d5e7a9c1
Create Date: 2026-07-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a2c4e6f8b0d2"
down_revision: str | None = "f1b3d5e7a9c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("onboarding_tour_version", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("onboarding_tour_status", sa.String(length=16), nullable=True))
        batch_op.add_column(
            sa.Column("onboarding_tour_updated_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_users_onboarding_tour_status",
            "onboarding_tour_status IS NULL OR onboarding_tour_status IN ('completed', 'skipped')",
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("ck_users_onboarding_tour_status", type_="check")
        batch_op.drop_column("onboarding_tour_updated_at")
        batch_op.drop_column("onboarding_tour_status")
        batch_op.drop_column("onboarding_tour_version")
