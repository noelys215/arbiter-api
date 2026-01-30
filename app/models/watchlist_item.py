from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    group_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("groups.id"), nullable=False, index=True)
    title_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("titles.id"), nullable=False, index=True)
    added_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id"),
        nullable=True,
        index=True,
    )

    # "watchlist" | "watched"
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, server_default="watchlist")
    snoozed_until: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)

    title = relationship("Title", back_populates="watchlist_items")
    added_by_user = relationship("User", lazy="joined")

    __table_args__ = (
        sa.CheckConstraint("status IN ('watchlist','watched')", name="ck_watchlist_items_status"),
        sa.UniqueConstraint("group_id", "title_id", name="uq_watchlist_items_group_title"),
        sa.Index("ix_watchlist_items_group_status", "group_id", "status"),
        sa.Index("ix_watchlist_items_group_snoozed", "group_id", "snoozed_until"),
    )
