from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base


class TonightSessionVoteSnapshot(Base):
    __tablename__ = "tonight_session_vote_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tonight_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    participant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tonight_session_participants.id", ondelete="CASCADE"),
        nullable=False,
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("tonight_session_candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    round_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    vote: Mapped[str] = mapped_column(sa.String(10), nullable=False)

    session = relationship("TonightSession", back_populates="vote_snapshots")
    participant = relationship("TonightSessionParticipant", lazy="joined")
    candidate = relationship("TonightSessionCandidate", lazy="joined")

    __table_args__ = (
        sa.UniqueConstraint(
            "session_id",
            "participant_id",
            "candidate_id",
            "round_number",
            name="uq_session_vote_snapshot",
        ),
        sa.CheckConstraint("vote IN ('yes','no')", name="ck_session_vote_snapshot_vote"),
        sa.CheckConstraint("round_number > 0", name="ck_session_vote_snapshot_round"),
    )
