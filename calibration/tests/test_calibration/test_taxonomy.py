"""
Gate 1 — Polymarket tag -> category mapping.

The load-bearing case is blocklist precedence: a market tagged both "Politics"
and "NBA" must be rejected. Including it would depress the calibration score
for a reason unrelated to the agent's reasoning — which is the exact class of
misleading number this system exists to avoid producing.
"""

from __future__ import annotations

import pytest

from calibration.polymarket import taxonomy


# ==========================================================
# Normalization
# ==========================================================

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Politics", "politics"),
        ("  POLITICS  ", "politics"),
        ("US-Current-Affairs", "us-current-affairs"),
        ("US Current Affairs", "us-current-affairs"),
        ("us_current_affairs", "us-current-affairs"),
        ("Crypto  Prices", "crypto-prices"),
    ],
)
def test_normalize_tag_collapses_casing_and_separators(raw, expected):
    """Gamma returns the same concept with four different spellings."""
    assert taxonomy.normalize_tag(raw) == expected


# ==========================================================
# Classification
# ==========================================================

@pytest.mark.parametrize(
    "tags,expected",
    [
        (["Politics"], "geopolitical"),
        (["Geopolitics"], "geopolitical"),
        (["Elections"], "geopolitical"),
        (["Macroeconomy"], "financial"),
        (["Business"], "financial"),
        (["AI"], "ai"),
        (["Tech"], "ai"),
        (["Technology"], "ai"),
    ],
)
def test_allowed_tags_map_to_categories(tags, expected):
    assert taxonomy.classify(tags) == expected


def test_unknown_tag_is_not_classified():
    assert taxonomy.classify(["Weather"]) is None


def test_empty_tags_are_not_classified():
    assert taxonomy.classify([]) is None


# ==========================================================
# Blocklist precedence — the important behaviour
# ==========================================================

@pytest.mark.parametrize("blocked", ["Sports", "NBA", "Entertainment", "Crypto Prices", "Bitcoin"])
def test_blocked_tag_rejects_the_market(blocked):
    assert taxonomy.is_blocked([blocked]) is True
    assert taxonomy.classify([blocked]) is None


def test_blocklist_beats_allowlist():
    """
    A market tagged both Politics and NBA is a basketball market with a
    political framing. Its outcome is driven by a game, about which the
    agent's vaults carry no signal.
    """
    tags = ["Politics", "NBA"]
    assert taxonomy.is_blocked(tags) is True
    assert taxonomy.classify(tags) is None


def test_blocklist_is_case_insensitive():
    assert taxonomy.classify(["Politics", "sports"]) is None
    assert taxonomy.classify(["Politics", "SPORTS"]) is None


# ==========================================================
# Precedence when several allowed tags match
# ==========================================================

def test_multiple_allowed_categories_resolve_deterministically():
    """
    Order is a convention, not a judgement. What matters is that the same
    market always lands in the same category, so cohort reports are stable
    across discovery runs.
    """
    first = taxonomy.classify(["Politics", "Macroeconomy"])
    second = taxonomy.classify(["Macroeconomy", "Politics"])
    assert first == second == "geopolitical"


def test_ai_wins_only_when_no_higher_precedence_tag_present():
    assert taxonomy.classify(["AI"]) == "ai"
    assert taxonomy.classify(["AI", "Macroeconomy"]) == "financial"


# ==========================================================
# Tag extraction — absorbing Gamma's shape drift
# ==========================================================

def test_extract_tags_handles_list_of_strings():
    assert taxonomy.extract_tags({"tags": ["Politics", "Elections"]}) == ["Politics", "Elections"]


def test_extract_tags_handles_list_of_objects():
    market = {"tags": [{"label": "Politics"}, {"slug": "elections"}, {"name": "AI"}]}
    assert taxonomy.extract_tags(market) == ["Politics", "elections", "AI"]


def test_extract_tags_picks_up_bare_category_field():
    assert "Macroeconomy" in taxonomy.extract_tags({"category": "Macroeconomy"})


def test_extract_tags_survives_unexpected_shape():
    """
    An unrecognised shape must yield no tags, not raise. No tags means the
    market is skipped — the safe outcome. Raising would abort the whole
    discovery run over one malformed payload.
    """
    assert taxonomy.extract_tags({"tags": "Politics"}) == []
    assert taxonomy.extract_tags({"tags": [123, None]}) == []
    assert taxonomy.extract_tags({}) == []


def test_mixed_shapes_combine():
    market = {"tags": [{"label": "Politics"}], "category": "Elections"}
    assert taxonomy.classify(taxonomy.extract_tags(market)) == "geopolitical"
