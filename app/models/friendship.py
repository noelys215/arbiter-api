from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
import uuid
from datetime import datetime

from app.db.base_class import Base


class Friendship(Base):
    __tablename__ = "friendships"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    user_low_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False, index=True)
    user_high_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)

    __table_args__ = (
        sa.UniqueConstraint("user_low_id", "user_high_id", name="uq_friendships_pair"),
        sa.CheckConstraint("user_low_id <> user_high_id", name="ck_friendships_not_self"),
    )
