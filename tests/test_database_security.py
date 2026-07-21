from __future__ import annotations

from app.db.session import engine_options


def test_production_engine_requires_tls_and_bounds_connections():
    options = engine_options("production")
    assert options["connect_args"] == {
        "ssl": "require",
        "command_timeout": 30,
        "server_settings": {"statement_timeout": "30000"},
    }
    assert options["pool_size"] == 5
    assert options["max_overflow"] == 5
