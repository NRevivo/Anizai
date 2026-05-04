"""
Gate 1 tests for `agent/labels.py` (Sprint 20 T20.6).

Pure unit tests for the three deterministic label helpers. Exhaustive
boundary coverage — these thresholds are public-facing (the frontend
renders them as "High" / "Moderate" / "Low" pills) so an off-by-one
silently breaks the BI cards.

Migrated from the Sprint 19 stub-mode threshold tests in
test_synthesize.py (`test_derive_label_thresholds`,
`test_derive_consensus_thresholds`).
"""

from __future__ import annotations

import pytest

from agent import labels


# ==========================================================
# confidence_label_from_score — §8.7.2 Patch 11
# ==========================================================
@pytest.mark.parametrize("score,expected", [
    (0.0, "Low"),
    (0.49, "Low"),
    (0.4999, "Low"),
    (0.5, "Moderate"),
    (0.65, "Moderate"),
    (0.7999, "Moderate"),
    (0.8, "High"),
    (0.95, "High"),
    (1.0, "High"),
])
def test_confidence_label_thresholds(score: float, expected: str):
    assert labels.confidence_label_from_score(score) == expected


def test_confidence_label_constants_match_thresholds():
    """If someone tweaks a constant they must move the boundary tests
    above too. Pin the relationship."""
    assert labels.CONFIDENCE_HIGH_THRESHOLD == 0.8
    assert labels.CONFIDENCE_MODERATE_THRESHOLD == 0.5


# ==========================================================
# consensus_strength_from_score — Patch 11 ("same pattern")
# ==========================================================
@pytest.mark.parametrize("score,expected", [
    (0.0, "Weak"),
    (0.49, "Weak"),
    (0.5, "Mixed"),
    (0.7999, "Mixed"),
    (0.8, "Strong"),
    (1.0, "Strong"),
])
def test_consensus_strength_thresholds(score: float, expected: str):
    assert labels.consensus_strength_from_score(score) == expected


def test_consensus_constants_match_confidence_pattern():
    """Patch 11: 'same pattern' for consensusStrength as confidenceLabel.
    Constants must be identical to keep the visual symmetry."""
    assert labels.CONSENSUS_STRONG_THRESHOLD == labels.CONFIDENCE_HIGH_THRESHOLD
    assert labels.CONSENSUS_MIXED_THRESHOLD == labels.CONFIDENCE_MODERATE_THRESHOLD


# ==========================================================
# evidence_volume_label_from_count — evidence-handling skill
# ==========================================================
@pytest.mark.parametrize("count,expected", [
    (0, "Low"),
    (1, "Low"),
    (4, "Low"),
    (5, "Moderate"),
    (7, "Moderate"),
    (9, "Moderate"),
    (10, "High"),
    (50, "High"),
    (1_000_000, "High"),
])
def test_evidence_volume_label_thresholds(count: int, expected: str):
    assert labels.evidence_volume_label_from_count(count) == expected


def test_evidence_volume_constants_match_thresholds():
    assert labels.EVIDENCE_VOLUME_HIGH_THRESHOLD == 10
    assert labels.EVIDENCE_VOLUME_MODERATE_THRESHOLD == 5


# ==========================================================
# Vocabulary distinctness
# ==========================================================
def test_confidence_and_consensus_have_distinct_vocab():
    """Patch 11: confidence uses Low/Moderate/High; consensus uses
    Weak/Mixed/Strong. Don't let a refactor collapse them into one."""
    confidence_outputs = {
        labels.confidence_label_from_score(s) for s in (0.0, 0.5, 0.8)
    }
    consensus_outputs = {
        labels.consensus_strength_from_score(s) for s in (0.0, 0.5, 0.8)
    }
    assert confidence_outputs == {"Low", "Moderate", "High"}
    assert consensus_outputs == {"Weak", "Mixed", "Strong"}
    assert confidence_outputs.isdisjoint(consensus_outputs)


def test_confidence_and_evidence_volume_share_vocab():
    """Patch 11: evidence_volume_label uses the same Low/Moderate/High
    as confidenceLabel. Pin so a future refactor doesn't accidentally
    rename one but not the other."""
    confidence_outputs = {
        labels.confidence_label_from_score(s) for s in (0.0, 0.5, 0.8)
    }
    volume_outputs = {
        labels.evidence_volume_label_from_count(c) for c in (0, 5, 10)
    }
    assert confidence_outputs == volume_outputs == {"Low", "Moderate", "High"}
