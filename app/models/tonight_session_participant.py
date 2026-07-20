from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base


class TonightSessionParticipant(Base):
    __tablename__ = "tonight_session_participants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tonight_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    display_name: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(sa.String(2048), nullable=True)
    avatar_source: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    avatar_style: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    avatar_seed: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    joined_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    role: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    submitted_votes: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    participation_status: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, server_default="participated"
    )
    criteria_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    session = relationship("TonightSession", back_populates="participant_snapshots")

    __table_args__ = (
        sa.UniqueConstraint("session_id", "user_id", name="uq_session_participant_user"),
        sa.CheckConstraint("role IN ('host','participant')", name="ck_session_participant_role"),
        sa.CheckConstraint(
            "participation_status IN ('participated','left')",
            name="ck_session_participant_status",
        ),
    )
