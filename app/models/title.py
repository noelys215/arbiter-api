from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base


class Title(Base):
    __tablename__ = "titles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # "tmdb" | "manual"
    source: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    # for TMDB: str(tmdb_id); for manual: None
    source_id: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)

    media_type: Mapped[str] = mapped_column(sa.String(10), nullable=False)  # movie|tv
    name: Mapped[str] = mapped_column(sa.String(300), nullable=False)

    release_year: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    poster_path: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)

    overview: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    runtime_minutes: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)

    watchlist_items = relationship("WatchlistItem", back_populates="title")

    __table_args__ = (
        sa.CheckConstraint("media_type IN ('movie','tv')", name="ck_titles_media_type"),
        sa.UniqueConstraint("source", "source_id", "media_type", name="uq_titles_source_source_id_media_type"),
        sa.Index("ix_titles_source_source_id", "source", "source_id"),
    )
