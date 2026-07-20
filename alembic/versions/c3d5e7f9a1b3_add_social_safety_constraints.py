"""add social safety constraints

Revision ID: c3d5e7f9a1b3
Revises: b2d4f6a8c0e2
Create Date: 2026-07-20

"""

from datetime import datetime, timezone
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c3d5e7f9a1b3"
down_revision: str | None = "b2d4f6a8c0e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _friend_pair_key(first: object, second: object) -> str:
    return ":".join(sorted((str(first), str(second))))


def _backfill_friend_pairs_and_close_duplicates() -> None:
    connection = op.get_bind()
    invites = sa.table(
        "friend_invites",
        sa.column("id", sa.Uuid()),
        sa.column("created_by_user_id", sa.Uuid()),
        sa.column("target_user_id", sa.Uuid()),
        sa.column("pair_key", sa.String()),
        sa.column("revoked_at", sa.DateTime(timezone=True)),
        sa.column("uses_count", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    rows = connection.execute(
        sa.select(invites).order_by(invites.c.created_at.desc(), invites.c.id.desc())
    ).mappings()
    active_pairs: set[str] = set()
    now = datetime.now(timezone.utc)
    for row in rows:
        pair_key = _friend_pair_key(
            row["created_by_user_id"], row["target_user_id"]
        )
        values: dict[str, object] = {"pair_key": pair_key}
        is_pending = row["revoked_at"] is None and row["uses_count"] == 0
        if is_pending and pair_key in active_pairs:
            values["revoked_at"] = now
        elif is_pending:
            active_pairs.add(pair_key)
        connection.execute(
            invites.update().where(invites.c.id == row["id"]).values(**values)
        )


def _close_duplicate_group_invites() -> None:
    connection = op.get_bind()
    invites = sa.table(
        "group_invites",
        sa.column("id", sa.Uuid()),
        sa.column("group_id", sa.Uuid()),
        sa.column("target_user_id", sa.Uuid()),
        sa.column("revoked_at", sa.DateTime(timezone=True)),
        sa.column("uses_count", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    rows = connection.execute(
        sa.select(invites).order_by(invites.c.created_at.desc(), invites.c.id.desc())
    ).mappings()
    active_targets: set[tuple[str, str]] = set()
    now = datetime.now(timezone.utc)
    for row in rows:
        target = (str(row["group_id"]), str(row["target_user_id"]))
        is_pending = row["revoked_at"] is None and row["uses_count"] == 0
        if is_pending and target in active_targets:
            connection.execute(
                invites.update()
                .where(invites.c.id == row["id"])
                .values(revoked_at=now)
            )
        elif is_pending:
            active_targets.add(target)


def upgrade() -> None:
    with op.batch_alter_table("friend_invites") as batch_op:
        batch_op.add_column(sa.Column("pair_key", sa.String(length=73), nullable=True))

    _backfill_friend_pairs_and_close_duplicates()
    _close_duplicate_group_invites()

    with op.batch_alter_table("friend_invites") as batch_op:
        batch_op.alter_column(
            "pair_key", existing_type=sa.String(length=73), nullable=False
        )
        batch_op.create_check_constraint(
            "ck_friend_invites_single_use", "max_uses = 1"
        )
        batch_op.create_check_constraint(
            "ck_friend_invites_uses_count", "uses_count >= 0 AND uses_count <= 1"
        )

    op.create_index(
        "uq_friend_invites_pending_pair",
        "friend_invites",
        ["pair_key"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL AND uses_count = 0"),
        sqlite_where=sa.text("revoked_at IS NULL AND uses_count = 0"),
    )

    with op.batch_alter_table("group_invites") as batch_op:
        batch_op.create_check_constraint(
            "ck_group_invites_single_use", "max_uses = 1"
        )
        batch_op.create_check_constraint(
            "ck_group_invites_uses_count", "uses_count >= 0 AND uses_count <= 1"
        )

    op.create_index(
        "uq_group_invites_pending_target",
        "group_invites",
        ["group_id", "target_user_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL AND uses_count = 0"),
        sqlite_where=sa.text("revoked_at IS NULL AND uses_count = 0"),
    )

    op.create_table(
        "user_blocks",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("blocker_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("blocked_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "blocker_user_id <> blocked_user_id", name="ck_user_blocks_not_self"
        ),
        sa.ForeignKeyConstraint(
            ["blocked_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["blocker_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "blocker_user_id", "blocked_user_id", name="uq_user_blocks_pair"
        ),
    )
    op.create_index(
        "ix_user_blocks_blocker_user_id",
        "user_blocks",
        ["blocker_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_blocks_blocked_user_id",
        "user_blocks",
        ["blocked_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_user_blocks_blocked_user_id", table_name="user_blocks")
    op.drop_index("ix_user_blocks_blocker_user_id", table_name="user_blocks")
    op.drop_table("user_blocks")

    op.drop_index(
        "uq_group_invites_pending_target", table_name="group_invites"
    )
    with op.batch_alter_table("group_invites") as batch_op:
        batch_op.drop_constraint("ck_group_invites_uses_count", type_="check")
        batch_op.drop_constraint("ck_group_invites_single_use", type_="check")

    op.drop_index("uq_friend_invites_pending_pair", table_name="friend_invites")
    with op.batch_alter_table("friend_invites") as batch_op:
        batch_op.drop_constraint("ck_friend_invites_uses_count", type_="check")
        batch_op.drop_constraint("ck_friend_invites_single_use", type_="check")
        batch_op.drop_column("pair_key")
