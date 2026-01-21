"""add tonight voting and resolution fields

Revision ID: fd73bbdbfc92
Revises: 0bbf91ea8901
Create Date: 2026-01-21 18:47:49.905880

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fd73bbdbfc92'
down_revision: Union[str, None] = '0bbf91ea8901'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
