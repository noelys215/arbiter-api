from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.friends import FriendRequestCreate, FriendRequestDecision, UnfriendRequest
from app.schemas.groups import (
    CreateGroupInviteRequest,
    CreateGroupRequest,
    GroupInviteDecisionRequest,
    TransferGroupOwnershipRequest,
    UpdateGroupRequest,
)
from app.schemas.session_history import WatchedStatusUpdateRequest
from app.schemas.sessions import CreateSessionRequest, VoteRequest, WatchPartyUpdateRequest
from app.schemas.tonight_constraints import TonightConstraints
from app.schemas.watchlist import AddWatchlistManual, AddWatchlistTMDB, WatchlistPatchRequest


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (FriendRequestCreate, {"identifier": "friend"}),
        (FriendRequestDecision, {"decision": "accept"}),
        (UnfriendRequest, {"user_id": uuid4()}),
        (CreateGroupRequest, {"name": "Group"}),
        (UpdateGroupRequest, {"name": "Group"}),
        (CreateGroupInviteRequest, {"target_user_id": uuid4()}),
        (GroupInviteDecisionRequest, {"decision": "accept"}),
        (TransferGroupOwnershipRequest, {"new_owner_user_id": uuid4()}),
        (WatchedStatusUpdateRequest, {"status": "watched"}),
        (CreateSessionRequest, {"constraints": {}}),
        (VoteRequest, {"watchlist_item_id": uuid4(), "vote": "yes"}),
        (WatchPartyUpdateRequest, {"url": None}),
        (TonightConstraints, {"format": "movie"}),
        (
            AddWatchlistTMDB,
            {"type": "tmdb", "tmdb_id": 1, "media_type": "movie", "title": "Film"},
        ),
        (
            AddWatchlistManual,
            {"type": "manual", "media_type": "movie", "title": "Film"},
        ),
        (WatchlistPatchRequest, {"remove": True}),
    ],
)
def test_request_schemas_forbid_unknown_fields(model, payload):
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        model.model_validate({**payload, "unexpected": True})


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (CreateSessionRequest, {"text": "x" * 501}),
        (TonightConstraints, {"moods": [str(index) for index in range(21)]}),
        (
            AddWatchlistManual,
            {"type": "manual", "media_type": "movie", "title": "x" * 301},
        ),
    ],
)
def test_request_schemas_reject_oversized_values(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)
