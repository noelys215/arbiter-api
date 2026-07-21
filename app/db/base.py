from app.db.base_class import Base  # noqa: F401

# Import ALL models so SQLAlchemy registers them
from app.models.user import User  # noqa: F401
from app.models.auth_session import AuthSession  # noqa: F401
from app.models.magic_link_grant import MagicLinkGrant  # noqa: F401
from app.models.oauth_identity import OAuthIdentity  # noqa: F401
from app.models.friend_invite import FriendInvite  # noqa: F401
from app.models.friendship import Friendship  # noqa: F401
from app.models.user_block import UserBlock  # noqa: F401
from app.models.group import Group  # noqa: F401
from app.models.group_invite import GroupInvite  # noqa: F401
from app.models.group_membership import GroupMembership  # noqa: F401
from app.models.title import Title  # noqa: F401
from app.models.watchlist_item import WatchlistItem  # noqa: F401
from app.models.tonight_session import TonightSession  # noqa: F401

from app.models.tonight_session_candidate import TonightSessionCandidate  # noqa: F401
from app.models.tonight_session_participant import TonightSessionParticipant  # noqa: F401
from app.models.tonight_session_vote_snapshot import TonightSessionVoteSnapshot  # noqa: F401
