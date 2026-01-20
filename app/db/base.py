from app.db.base_class import Base

# Import ALL models so SQLAlchemy registers them
from app.models.user import User  # noqa: F401
from app.models.friend_invite import FriendInvite  # noqa: F401
from app.models.friendship import Friendship  # noqa: F401
from app.models.group import Group  # noqa: F401
from app.models.group_invite import GroupInvite  # noqa: F401
from app.models.group_membership import GroupMembership  # noqa: F401
