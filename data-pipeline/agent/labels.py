"""
agent/labels.py — Deterministic label-derivation helpers for SessionResult.

Sprint 20 T20.6 — pulls the label/consensus thresholds out of
`agent/nodes/synthesize.py` (where they lived as `_derive_label` /
`_derive_consensus` for Sprint 19's stub) and into a dedicated module
that synthesize, write_to_firestore, and tests can all import without
pulling in the rest of the synthesize node.

Why deterministic (not LLM-driven):
    The §8.7.2 SessionResult fields `confidenceLabel`,
    `consensusStrength`, and `evidenceVolumeLabel` are display-only
    summaries derived from numeric scores and counts. They MUST be
    consistent across every forecast — if the same `confidence=0.7`
    sometimes renders as "Moderate" and sometimes as "High", users lose
    trust. Pure function over numeric input, no LLM, no randomness.
    (agent-design P5: determinism where possible.)

Why three helpers (not one parameterized):
    Each one has a different input domain and vocabulary:
      - confidence/consensus take a 0-1 float
      - evidence_volume takes an integer count
      - consensus uses a different label vocab (Weak/Mixed/Strong) than
        the others (Low/Moderate/High)
    Keeping them as named, single-purpose functions makes the call sites
    self-documenting and the threshold drift impossible.

Why the same 0.5/0.8 boundaries for confidence and consensus:
    Spec §8.7.2 line 858-862 (Patch 11): "Same pattern for
    `evidenceVolumeLabel` and `consensusStrength`." A consistent shape
    across all three fields is the user-facing contract — moving one
    threshold without moving the others would visually scramble the BI
    cards.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.7.2 (label derivation
      rules — Patch 11)
    - .claude/skills/evidence-handling/SKILL.md (evidence-volume
      thresholds: <5 Low / 5-9 Moderate / ≥10 High)
"""

from __future__ import annotations

from typing import Literal


# ==========================================================
# Thresholds — Patch 11 + evidence-handling skill defaults
# ==========================================================
# Confidence: 0-1 float scale per §8.7.2.
CONFIDENCE_HIGH_THRESHOLD: float = 0.8
CONFIDENCE_MODERATE_THRESHOLD: float = 0.5

# Consensus: 0-1 float scale per §8.7.2 ("Same pattern" rule).
CONSENSUS_STRONG_THRESHOLD: float = 0.8
CONSENSUS_MIXED_THRESHOLD: float = 0.5

# Evidence volume: integer count of items with used_in_answer=True.
# Per evidence-handling skill: count >=10 High; 5-9 Moderate; <5 Low.
EVIDENCE_VOLUME_HIGH_THRESHOLD: int = 10
EVIDENCE_VOLUME_MODERATE_THRESHOLD: int = 5


# ==========================================================
# Public helpers
# ==========================================================
def confidence_label_from_score(score: float) -> Literal["Low", "Moderate", "High"]:
    """
    Derive `SessionResult.confidenceLabel` from `SessionResult.confidence`.

    Boundary handling (right-inclusive):
        score >= 0.8           → "High"
        0.5 <= score < 0.8     → "Moderate"
        score < 0.5            → "Low"

    Reused for `evidenceVolumeLabel` derivation? No — that's a count,
    not a 0-1 score. See `evidence_volume_label_from_count` instead.
    """
    if score >= CONFIDENCE_HIGH_THRESHOLD:
        return "High"
    if score >= CONFIDENCE_MODERATE_THRESHOLD:
        return "Moderate"
    return "Low"


def consensus_strength_from_score(
    score: float,
) -> Literal["Weak", "Mixed", "Strong"]:
    """
    Derive `SessionResult.consensusStrength` from a 0-1 consensus score
    produced by the synthesis prompt (the model's own assessment of how
    strongly the evidence agrees on the forecast).

    Boundary handling matches `confidence_label_from_score` — see Patch
    11 ("Same pattern"). Vocabulary differs (Weak/Mixed/Strong) so the
    SessionResult fields are visually distinct in the UI.
    """
    if score >= CONSENSUS_STRONG_THRESHOLD:
        return "Strong"
    if score >= CONSENSUS_MIXED_THRESHOLD:
        return "Mixed"
    return "Weak"


def evidence_volume_label_from_count(
    count: int,
) -> Literal["Low", "Moderate", "High"]:
    """
    Derive `SessionResult.evidenceVolumeLabel` from the count of evidence
    items that synthesis marked `used_in_answer=True`.

    Boundary handling per evidence-handling skill:
        count >= 10  → "High"
        5 <= count < 10  → "Moderate"
        count < 5  → "Low"

    Why `used_in_answer` count, not raw evidence_trail length: the user
    cares about how much evidence the agent actually leaned on, not how
    many items the vault dumped into the pool. A session with 30
    retrieved items but only 2 used_in_answer should display as Low
    volume.
    """
    if count >= EVIDENCE_VOLUME_HIGH_THRESHOLD:
        return "High"
    if count >= EVIDENCE_VOLUME_MODERATE_THRESHOLD:
        return "Moderate"
    return "Low"
