"""Tests for the semantic tag allow-list."""
from __future__ import annotations

from presentations.variables.semantic_tags import (
    SEMANTIC_TAGS_V0,
    all_tags,
    describe_tag,
    is_valid_tag,
    tag_description,
)


def test_known_tags_included():
    for tag in ("as_of_time", "currency", "maturity", "branch", "other"):
        assert tag in SEMANTIC_TAGS_V0


def test_is_valid_tag_positive():
    assert is_valid_tag("currency") is True


def test_is_valid_tag_negative():
    assert is_valid_tag("currrrency") is False  # typo
    assert is_valid_tag("") is False
    assert is_valid_tag(None) is False


def test_describe_tag_returns_label():
    label = describe_tag("currency")
    assert "Para birimi" in label


def test_describe_tag_unknown_returns_input():
    assert describe_tag("not_a_tag") == "not_a_tag"


def test_tag_description_for_other_explains_escape_hatch():
    desc = tag_description("other")
    assert "Phase 7" in desc or "Diğer" in describe_tag("other")


def test_all_tags_lists_every_tag():
    listed = {t["tag"] for t in all_tags()}
    assert listed == SEMANTIC_TAGS_V0


def test_other_appears_last_in_all_tags():
    listing = all_tags()
    assert listing[-1]["tag"] == "other"
