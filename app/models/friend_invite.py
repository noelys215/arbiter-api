from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
import uuid
from datetime import datetime

from app.db.base_class import Base


class FriendInvite(Base):
    __tablename__ = "friend_invites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    target_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    pair_key: Mapped[str] = mapped_column(sa.String(73), nullable=False)

    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    max_uses: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="1")
    uses_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")

    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)

    __table_args__ = (
        sa.CheckConstraint("max_uses = 1", name="ck_friend_invites_single_use"),
        sa.CheckConstraint(
            "uses_count >= 0 AND uses_count <= 1",
            name="ck_friend_invites_uses_count",
        ),
        sa.Index(
            "uq_friend_invites_pending_pair",
            "pair_key",
            unique=True,
            postgresql_where=sa.text("revoked_at IS NULL AND uses_count = 0"),
            sqlite_where=sa.text("revoked_at IS NULL AND uses_count = 0"),
        ),
    )

    created_by = relationship("User", foreign_keys=[created_by_user_id])
    target = relationship("User", foreign_keys=[target_user_id])
