import pytest
from pydantic import ValidationError

from app.schemas.tonight_constraints import TonightConstraints


def test_defaults_are_canonical():
    c = TonightConstraints()
    assert c.moods == []
    assert c.avoid == []
    assert c.max_runtime is None
    assert c.format == "any"
    assert c.energy is None
    assert c.free_text == ""
    assert c.parsed_by_ai is False
    assert c.ai_version is None


def test_moods_and_avoid_trim_dedupe():
    c = TonightConstraints(moods=[" Cozy ", "cozy", "", "  "], avoid=[" Gore", "gore "])
    assert c.moods == ["Cozy"]
    assert c.avoid == ["Gore"]


def test_ai_version_required_when_parsed_by_ai():
    with pytest.raises(ValidationError):
        TonightConstraints(parsed_by_ai=True, free_text="something")


def test_ai_version_forced_null_when_not_parsed_by_ai():
    c = TonightConstraints(parsed_by_ai=False, ai_version="gpt-x")
    assert c.ai_version is None


def test_runtime_bounds():
    with pytest.raises(ValidationError):
        TonightConstraints(max_runtime=10)
    with pytest.raises(ValidationError):
        TonightConstraints(max_runtime=9999)
