from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

class TonightSession(Base):
    __tablename__ = "tonight_sessions"

    # ─────────────────────────────────────────────
    # Identity
    # ─────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ─────────────────────────────────────────────
    # Canonical constraints (Phase 5.1)
    # Always validated via TonightConstraints schema
    # ─────────────────────────────────────────────
    constraints: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )

    # ─────────────────────────────────────────────
    # Lifecycle / locking
    # ─────────────────────────────────────────────
    locked_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )

    # ─────────────────────────────────────────────
    # Relationships (future phases)
    # ─────────────────────────────────────────────
    group = relationship("Group", lazy="joined")
    created_by = relationship(
        "User",
        lazy="joined",
        foreign_keys=[created_by_user_id],
    )

    # These will come in Phase 5.2+
    # votes = relationship("TonightVote", back_populates="session")
    # timer = relationship("TonightTimer", uselist=False)

    ends_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    duration_seconds: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="90")
    candidate_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="12")

    ai_why: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    ai_used: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.text("false"))

    # app/models/tonight_session.py
    candidates = relationship(
        "TonightSessionCandidate",
        back_populates="session",
        foreign_keys="TonightSessionCandidate.session_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="TonightSessionCandidate.position",
    )

    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, server_default="active")
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    winner_selected_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    group_name_snapshot: Mapped[str | None] = mapped_column(
        sa.String(120), nullable=True
    )
    criteria_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    winner_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey(
            "tonight_session_candidates.id",
            name="tonight_sessions_winner_candidate_id_fkey",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
    )
    decision_duration_seconds: Mapped[int | None] = mapped_column(
        sa.Integer, nullable=True
    )
    winner_unanimous: Mapped[bool | None] = mapped_column(sa.Boolean, nullable=True)
    had_tie: Mapped[bool | None] = mapped_column(sa.Boolean, nullable=True)
    tie_resolution: Mapped[str | None] = mapped_column(sa.String(30), nullable=True)
    watched_status: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, server_default="unconfirmed"
    )
    watched_confirmed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    watched_confirmed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    teleparty_shared_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    teleparty_handoff_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )

    result_watchlist_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("watchlist_items.id", ondelete="SET NULL"),
        nullable=True,
    )

    result_watchlist_item = relationship("WatchlistItem", lazy="joined", foreign_keys=[result_watchlist_item_id])

    watch_party_url: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    watch_party_set_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )
    watch_party_set_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    watch_party_set_by = relationship(
        "User",
        lazy="joined",
        foreign_keys=[watch_party_set_by_user_id],
    )

    votes = relationship("TonightVote", back_populates="session")
    participant_snapshots = relationship(
        "TonightSessionParticipant",
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    vote_snapshots = relationship(
        "TonightSessionVoteSnapshot",
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('setup','active','winner_selected','completed','cancelled','complete')",
            name="ck_tonight_sessions_status",
        ),
        sa.CheckConstraint(
            "watched_status IN ('unconfirmed','watched','not_watched')",
            name="ck_tonight_sessions_watched_status",
        ),
        sa.Index(
            "ix_tonight_sessions_group_completed",
            "group_id",
            "completed_at",
        ),
        sa.Index(
            "uq_tonight_sessions_open_group",
            "group_id",
            unique=True,
            postgresql_where=sa.text(
                "status IN ('setup','active','winner_selected')"
            ),
            sqlite_where=sa.text("status IN ('setup','active','winner_selected')"),
        ),
    )
