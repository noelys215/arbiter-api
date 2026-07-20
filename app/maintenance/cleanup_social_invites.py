from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal, engine
from app.models.friend_invite import FriendInvite
from app.models.group_invite import GroupInvite


@dataclass(frozen=True)
class CleanupResult:
    friend_invites_deleted: int
    group_invites_deleted: int


async def cleanup_social_invites(
    db: AsyncSession, *, retention_days: int = 30
) -> CleanupResult:
    if retention_days < 1:
        raise ValueError("retention_days must be at least 1")
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    friend_result = await db.execute(
        sa.delete(FriendInvite).where(
            sa.or_(
                FriendInvite.expires_at < cutoff,
                sa.and_(
                    FriendInvite.created_at < cutoff,
                    sa.or_(
                        FriendInvite.revoked_at.is_not(None),
                        FriendInvite.uses_count >= FriendInvite.max_uses,
                    ),
                ),
            )
        )
    )
    group_result = await db.execute(
        sa.delete(GroupInvite).where(
            sa.or_(
                GroupInvite.expires_at < cutoff,
                sa.and_(
                    GroupInvite.created_at < cutoff,
                    sa.or_(
                        GroupInvite.revoked_at.is_not(None),
                        GroupInvite.uses_count >= GroupInvite.max_uses,
                    ),
                ),
            )
        )
    )
    await db.commit()
    return CleanupResult(
        friend_invites_deleted=friend_result.rowcount or 0,
        group_invites_deleted=group_result.rowcount or 0,
    )


async def _run(retention_days: int) -> None:
    try:
        async with AsyncSessionLocal() as db:
            result = await cleanup_social_invites(
                db, retention_days=retention_days
            )
        print(
            "Expired social invite cleanup complete: "
            f"{result.friend_invites_deleted} friend requests and "
            f"{result.group_invites_deleted} group invitations removed."
        )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete expired or terminal social invitations after retention."
    )
    parser.add_argument("--retention-days", type=int, default=30)
    args = parser.parse_args()
    asyncio.run(_run(args.retention_days))


if __name__ == "__main__":
    main()
