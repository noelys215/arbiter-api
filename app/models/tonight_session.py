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

    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id"),
        nullable=False,
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
    created_by = relationship("User", lazy="joined")

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
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="TonightSessionCandidate.position",
    )
