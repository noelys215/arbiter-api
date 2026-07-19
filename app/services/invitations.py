from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class InvitationRecord(Protocol):
    expires_at: datetime
    revoked_at: datetime | None


def ensure_invite_active(invite: InvitationRecord) -> None:
    if invite.revoked_at is not None:
        raise ValueError("revoked_invite")
    if invite.expires_at <= datetime.now(timezone.utc):
        raise ValueError("expired_invite")


def terminate_invite(invite: InvitationRecord) -> None:
    # This release intentionally uses one terminal state for creator revocation
    # and recipient decline. The database does not distinguish the actor.
    if invite.revoked_at is None:
        invite.revoked_at = datetime.now(timezone.utc)
