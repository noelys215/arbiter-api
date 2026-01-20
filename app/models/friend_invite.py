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

    code: Mapped[str] = mapped_column(sa.String(16), nullable=False, unique=True, index=True)

    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False, index=True)

    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False, index=True)

    max_uses: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="1")
    uses_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")

    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)

    created_by = relationship("User", foreign_keys=[created_by_user_id])
