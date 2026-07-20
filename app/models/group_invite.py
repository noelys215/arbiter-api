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

    group_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("groups.id"), nullable=False, index=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False, index=True)
    target_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    max_uses: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("1"))
    uses_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("0"))

    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)

    __table_args__ = (
        sa.CheckConstraint("max_uses = 1", name="ck_group_invites_single_use"),
        sa.CheckConstraint(
            "uses_count >= 0 AND uses_count <= 1",
            name="ck_group_invites_uses_count",
        ),
        sa.Index(
            "uq_group_invites_pending_target",
            "group_id",
            "target_user_id",
            unique=True,
            postgresql_where=sa.text("revoked_at IS NULL AND uses_count = 0"),
            sqlite_where=sa.text("revoked_at IS NULL AND uses_count = 0"),
        ),
    )

    group = relationship("Group", back_populates="invites")
