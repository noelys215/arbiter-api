from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base


class TonightSessionCandidate(Base):
    __tablename__ = "tonight_session_candidates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tonight_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    watchlist_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("watchlist_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_watchlist_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    source_title_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    title_source: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    title_source_id: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)
    media_type: Mapped[str | None] = mapped_column(sa.String(10), nullable=True)
    title_name: Mapped[str | None] = mapped_column(sa.String(300), nullable=True)
    release_year: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    poster_path: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    backdrop_path: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    runtime_minutes: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    genres: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )
    overview: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    yes_count: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    no_count: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    total_vote_count: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    is_winner: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.text("false")
    )
    is_finalist: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.text("false")
    )

    # 0..N-1 in the final deck order
    position: Mapped[int] = mapped_column(sa.Integer, nullable=False)

    # optional fields you can use later
    ai_note: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )

    session = relationship(
        "TonightSession",
        back_populates="candidates",
        foreign_keys=[session_id],
        lazy="joined",
    )
    watchlist_item = relationship("WatchlistItem", lazy="joined")

    __table_args__ = (
        sa.UniqueConstraint(
            "session_id",
            "source_watchlist_item_id",
            name="uq_session_candidate_source_unique",
        ),
        sa.UniqueConstraint("session_id", "position", name="uq_session_candidate_position"),
        sa.Index("ix_session_candidates_session_position", "session_id", "position"),
    )
