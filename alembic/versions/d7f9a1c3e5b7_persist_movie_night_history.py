"""persist movie night history

Revision ID: d7f9a1c3e5b7
Revises: c3d5e7f9a1b3
Create Date: 2026-07-20

"""

from datetime import datetime, timezone
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d7f9a1c3e5b7"
down_revision: str | None = "c3d5e7f9a1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _close_duplicate_open_sessions() -> None:
    connection = op.get_bind()
    sessions = sa.table(
        "tonight_sessions",
        sa.column("id", sa.Uuid()),
        sa.column("group_id", sa.Uuid()),
        sa.column("status", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("cancelled_at", sa.DateTime(timezone=True)),
    )
    rows = connection.execute(
        sa.select(sessions)
        .where(sessions.c.status.in_(("setup", "active", "winner_selected")))
        .order_by(
            sessions.c.group_id,
            sessions.c.created_at.desc(),
            sessions.c.id.desc(),
        )
    ).mappings()
    seen_groups: set[str] = set()
    now = datetime.now(timezone.utc)
    for row in rows:
        group_key = str(row["group_id"])
        if group_key not in seen_groups:
            seen_groups.add(group_key)
            continue
        connection.execute(
            sessions.update()
            .where(sessions.c.id == row["id"])
            .values(status="cancelled", cancelled_at=now)
        )


def upgrade() -> None:
    with op.batch_alter_table("tonight_session_candidates") as batch_op:
        batch_op.add_column(
            sa.Column("source_watchlist_item_id", sa.Uuid(), nullable=True)
        )
        batch_op.add_column(sa.Column("source_title_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("title_source", sa.String(20), nullable=True))
        batch_op.add_column(sa.Column("title_source_id", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("media_type", sa.String(10), nullable=True))
        batch_op.add_column(sa.Column("title_name", sa.String(300), nullable=True))
        batch_op.add_column(sa.Column("release_year", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("poster_path", sa.String(500), nullable=True))
        batch_op.add_column(sa.Column("backdrop_path", sa.String(500), nullable=True))
        batch_op.add_column(sa.Column("runtime_minutes", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "genres",
                _json_type(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch_op.add_column(sa.Column("overview", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("yes_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("no_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("total_vote_count", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "is_winner", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch_op.add_column(
            sa.Column(
                "is_finalist", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )

    candidates = sa.table(
        "tonight_session_candidates",
        sa.column("watchlist_item_id", sa.Uuid()),
        sa.column("source_watchlist_item_id", sa.Uuid()),
    )
    op.execute(
        candidates.update().values(
            source_watchlist_item_id=candidates.c.watchlist_item_id
        )
    )

    with op.batch_alter_table("tonight_session_candidates") as batch_op:
        batch_op.drop_constraint("uq_session_candidate_unique", type_="unique")
        batch_op.drop_constraint(
            "tonight_session_candidates_watchlist_item_id_fkey", type_="foreignkey"
        )
        batch_op.alter_column(
            "source_watchlist_item_id", existing_type=sa.Uuid(), nullable=False
        )
        batch_op.alter_column(
            "watchlist_item_id", existing_type=sa.Uuid(), nullable=True
        )
        batch_op.create_foreign_key(
            "tonight_session_candidates_watchlist_item_id_fkey",
            "watchlist_items",
            ["watchlist_item_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_unique_constraint(
            "uq_session_candidate_source_unique",
            ["session_id", "source_watchlist_item_id"],
        )
        batch_op.create_index(
            "ix_tonight_session_candidates_source_watchlist_item_id",
            ["source_watchlist_item_id"],
        )

    with op.batch_alter_table("tonight_sessions") as batch_op:
        batch_op.add_column(sa.Column("started_at", sa.DateTime(timezone=True)))
        batch_op.add_column(
            sa.Column("winner_selected_at", sa.DateTime(timezone=True))
        )
        batch_op.add_column(sa.Column("cancelled_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("group_name_snapshot", sa.String(120)))
        batch_op.add_column(sa.Column("criteria_snapshot", _json_type()))
        batch_op.add_column(sa.Column("winner_candidate_id", sa.Uuid()))
        batch_op.add_column(sa.Column("decision_duration_seconds", sa.Integer()))
        batch_op.add_column(sa.Column("winner_unanimous", sa.Boolean()))
        batch_op.add_column(sa.Column("had_tie", sa.Boolean()))
        batch_op.add_column(sa.Column("tie_resolution", sa.String(30)))
        batch_op.add_column(
            sa.Column(
                "watched_status",
                sa.String(20),
                nullable=False,
                server_default="unconfirmed",
            )
        )
        batch_op.add_column(
            sa.Column("watched_confirmed_at", sa.DateTime(timezone=True))
        )
        batch_op.add_column(sa.Column("watched_confirmed_by_user_id", sa.Uuid()))
        batch_op.add_column(
            sa.Column("teleparty_shared_at", sa.DateTime(timezone=True))
        )
        batch_op.add_column(
            sa.Column("teleparty_handoff_at", sa.DateTime(timezone=True))
        )
        batch_op.create_foreign_key(
            "tonight_sessions_winner_candidate_id_fkey",
            "tonight_session_candidates",
            ["winner_candidate_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "tonight_sessions_watched_confirmed_by_user_id_fkey",
            "users",
            ["watched_confirmed_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_check_constraint(
            "ck_tonight_sessions_status",
            "status IN ('setup','active','winner_selected','completed','cancelled','complete')",
        )
        batch_op.create_check_constraint(
            "ck_tonight_sessions_watched_status",
            "watched_status IN ('unconfirmed','watched','not_watched')",
        )
        batch_op.create_index(
            "ix_tonight_sessions_group_completed", ["group_id", "completed_at"]
        )

    _close_duplicate_open_sessions()

    op.create_index(
        "uq_tonight_sessions_open_group",
        "tonight_sessions",
        ["group_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('setup','active','winner_selected')"
        ),
        sqlite_where=sa.text("status IN ('setup','active','winner_selected')"),
    )

    op.create_table(
        "tonight_session_participants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("avatar_url", sa.String(2048), nullable=True),
        sa.Column("avatar_source", sa.String(20), nullable=True),
        sa.Column("avatar_style", sa.String(32), nullable=True),
        sa.Column("avatar_seed", sa.String(128), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("submitted_votes", sa.Boolean(), nullable=False),
        sa.Column(
            "participation_status",
            sa.String(20),
            nullable=False,
            server_default="participated",
        ),
        sa.Column("criteria_snapshot", _json_type(), nullable=True),
        sa.CheckConstraint(
            "role IN ('host','participant')", name="ck_session_participant_role"
        ),
        sa.CheckConstraint(
            "participation_status IN ('participated','left')",
            name="ck_session_participant_status",
        ),
        sa.ForeignKeyConstraint(["session_id"], ["tonight_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id", "user_id", name="uq_session_participant_user"
        ),
    )
    op.create_index(
        "ix_tonight_session_participants_session_id",
        "tonight_session_participants",
        ["session_id"],
    )

    op.create_table(
        "tonight_session_vote_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("participant_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("vote", sa.String(10), nullable=False),
        sa.CheckConstraint("vote IN ('yes','no')", name="ck_session_vote_snapshot_vote"),
        sa.CheckConstraint("round_number > 0", name="ck_session_vote_snapshot_round"),
        sa.ForeignKeyConstraint(["candidate_id"], ["tonight_session_candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["participant_id"], ["tonight_session_participants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["tonight_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id",
            "participant_id",
            "candidate_id",
            "round_number",
            name="uq_session_vote_snapshot",
        ),
    )
    op.create_index(
        "ix_tonight_session_vote_snapshots_session_id",
        "tonight_session_vote_snapshots",
        ["session_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tonight_session_vote_snapshots_session_id",
        table_name="tonight_session_vote_snapshots",
    )
    op.drop_table("tonight_session_vote_snapshots")
    op.drop_index(
        "ix_tonight_session_participants_session_id",
        table_name="tonight_session_participants",
    )
    op.drop_table("tonight_session_participants")

    op.drop_index("uq_tonight_sessions_open_group", table_name="tonight_sessions")

    sessions = sa.table(
        "tonight_sessions",
        sa.column("status", sa.String()),
    )
    op.execute(
        sessions.update()
        .where(sessions.c.status.in_(("winner_selected", "completed", "cancelled")))
        .values(status="complete")
    )
    op.execute(
        sessions.update().where(sessions.c.status == "setup").values(status="active")
    )

    with op.batch_alter_table("tonight_sessions") as batch_op:
        batch_op.drop_index("ix_tonight_sessions_group_completed")
        batch_op.drop_constraint(
            "ck_tonight_sessions_watched_status", type_="check"
        )
        batch_op.drop_constraint("ck_tonight_sessions_status", type_="check")
        batch_op.drop_constraint(
            "tonight_sessions_watched_confirmed_by_user_id_fkey",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "tonight_sessions_winner_candidate_id_fkey", type_="foreignkey"
        )
        for column in (
            "teleparty_handoff_at",
            "teleparty_shared_at",
            "watched_confirmed_by_user_id",
            "watched_confirmed_at",
            "watched_status",
            "tie_resolution",
            "had_tie",
            "winner_unanimous",
            "decision_duration_seconds",
            "winner_candidate_id",
            "criteria_snapshot",
            "group_name_snapshot",
            "cancelled_at",
            "winner_selected_at",
            "started_at",
        ):
            batch_op.drop_column(column)

    with op.batch_alter_table("tonight_session_candidates") as batch_op:
        batch_op.drop_index(
            "ix_tonight_session_candidates_source_watchlist_item_id"
        )
        batch_op.drop_constraint(
            "uq_session_candidate_source_unique", type_="unique"
        )
        batch_op.drop_constraint(
            "tonight_session_candidates_watchlist_item_id_fkey", type_="foreignkey"
        )
        batch_op.alter_column(
            "watchlist_item_id", existing_type=sa.Uuid(), nullable=False
        )
        batch_op.create_foreign_key(
            "tonight_session_candidates_watchlist_item_id_fkey",
            "watchlist_items",
            ["watchlist_item_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_unique_constraint(
            "uq_session_candidate_unique", ["session_id", "watchlist_item_id"]
        )
        for column in (
            "is_finalist",
            "is_winner",
            "total_vote_count",
            "no_count",
            "yes_count",
            "overview",
            "genres",
            "runtime_minutes",
            "backdrop_path",
            "poster_path",
            "release_year",
            "title_name",
            "media_type",
            "title_source_id",
            "title_source",
            "source_title_id",
            "source_watchlist_item_id",
        ):
            batch_op.drop_column(column)
