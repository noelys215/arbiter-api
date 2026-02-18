from scripts.backfill_tmdb_title_details import _derive_patch, _is_blank_text, _parse_tmdb_id


def test_parse_tmdb_id_accepts_positive_int_string():
    assert _parse_tmdb_id("603") == 603
    assert _parse_tmdb_id("  42  ") == 42


def test_parse_tmdb_id_rejects_invalid_values():
    assert _parse_tmdb_id(None) is None
    assert _parse_tmdb_id("") is None
    assert _parse_tmdb_id("abc") is None
    assert _parse_tmdb_id("-10") is None
    assert _parse_tmdb_id("0") is None


def test_is_blank_text():
    assert _is_blank_text(None) is True
    assert _is_blank_text("") is True
    assert _is_blank_text("   ") is True
    assert _is_blank_text("text") is False


def test_derive_patch_fills_only_missing_fields():
    changed, runtime_out, overview_out = _derive_patch(
        current_runtime=None,
        current_overview=None,
        details={"runtime_minutes": 136, "overview": "  Matrix overview  "},
        fill_runtime=True,
        fill_overview=True,
    )
    assert changed is True
    assert runtime_out == 136
    assert overview_out == "Matrix overview"


def test_derive_patch_does_not_overwrite_existing_values():
    changed, runtime_out, overview_out = _derive_patch(
        current_runtime=100,
        current_overview="Keep existing",
        details={"runtime_minutes": 136, "overview": "Replace me"},
        fill_runtime=True,
        fill_overview=True,
    )
    assert changed is False
    assert runtime_out == 100
    assert overview_out == "Keep existing"


def test_derive_patch_respects_runtime_only_mode():
    changed, runtime_out, overview_out = _derive_patch(
        current_runtime=None,
        current_overview=None,
        details={"runtime_minutes": 121, "overview": "Some overview"},
        fill_runtime=True,
        fill_overview=False,
    )
    assert changed is True
    assert runtime_out == 121
    assert overview_out is None
