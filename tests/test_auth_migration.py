from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db.base_class import Base
import app.db.base  # noqa: F401


def test_auth_migration_is_in_head_chain_and_matches_registered_tables():
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_current_head() == "a2c4e6f8b0d2"
    assert scripts.get_revision("a2c4e6f8b0d2").down_revision == "f1b3d5e7a9c1"
    assert scripts.get_revision("f1b3d5e7a9c1").down_revision == "e9a1b3c5d7f9"
    assert {
        "auth_sessions",
        "magic_link_grants",
        "oauth_identities",
    } <= set(Base.metadata.tables)


def test_auth_table_constraints_match_security_invariants():
    sessions = Base.metadata.tables["auth_sessions"]
    grants = Base.metadata.tables["magic_link_grants"]
    identities = Base.metadata.tables["oauth_identities"]

    assert sessions.c.jti.unique is True
    assert grants.c.grant_hash.unique is True
    unique_names = {
        constraint.name
        for constraint in identities.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert unique_names == {
        "uq_oauth_identities_provider_subject",
        "uq_oauth_identities_user_provider",
    }
