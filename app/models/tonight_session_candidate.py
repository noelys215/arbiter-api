from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
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

    watchlist_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("watchlist_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
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

    session = relationship("TonightSession", back_populates="candidates", lazy="joined")
    watchlist_item = relationship("WatchlistItem", lazy="joined")

    __table_args__ = (
        sa.UniqueConstraint("session_id", "watchlist_item_id", name="uq_session_candidate_unique"),
        sa.UniqueConstraint("session_id", "position", name="uq_session_candidate_position"),
        sa.Index("ix_session_candidates_session_position", "session_id", "position"),
    )
