from __future__ import annotations

import uuid
from datetime import datetime
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

class TonightVote(Base):
    __tablename__ = "tonight_votes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("tonight_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    watchlist_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("watchlist_items.id", ondelete="CASCADE"), nullable=False, index=True)

    vote: Mapped[str] = mapped_column(sa.String(10), nullable=False)  # yes|no

    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)

    session = relationship("TonightSession", back_populates="votes", lazy="joined")
    user = relationship("User", lazy="joined")
    watchlist_item = relationship("WatchlistItem", lazy="joined")

    __table_args__ = (
        sa.CheckConstraint("vote IN ('yes','no')", name="ck_tonight_votes_vote"),
        sa.UniqueConstraint("session_id", "user_id", name="uq_tonight_votes_session_user"),
        sa.Index("ix_tonight_votes_session_item", "session_id", "watchlist_item_id"),
    )
