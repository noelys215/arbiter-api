from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base


class GroupInvite(Base):
    __tablename__ = "group_invites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    code: Mapped[str] = mapped_column(sa.String(32), unique=True, nullable=False, index=True)

    group_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("groups.id"), nullable=False, index=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False, index=True)

    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False, index=True)

    max_uses: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("1"))
    uses_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("0"))

    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)

    __table_args__ = (
        sa.CheckConstraint("max_uses >= 1", name="ck_group_invites_max_uses"),
        sa.CheckConstraint("uses_count >= 0", name="ck_group_invites_uses_count"),
    )

    group = relationship("Group", back_populates="invites")
