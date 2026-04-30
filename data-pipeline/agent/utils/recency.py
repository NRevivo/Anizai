"""
agent/utils/recency.py — exponential-decay recency weighting.

Single source of truth for the `recency_weight` / `recency_score` value
used by retrieval agents (Researcher §8.4.1, Pulse Analyst §8.4.2) and
later by `rate_evidence` (Sprint 20+).

Defined here (not inside any one agent) per:
    - CLAUDE.md §3.2 DRY: "All shared logic must be centralized."
    - evidence-handling skill: "Computing recency_weight differently per
      node — there's one function, in one place, used by everyone."

Half-life is configurable so question-aware tuning (Sprint 22+) can
shorten/lengthen decay without touching agent code. Default 7 days
matches the evidence-handling skill default.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.4.1 (Researcher composite ranking)
    - .claude/skills/evidence-handling/SKILL.md (recency_weight definition)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def compute_recency_weight(
    published_at: Optional[datetime],
    half_life_days: int = 7,
    now: Optional[datetime] = None,
) -> float:
    """
    Exponentially-decayed recency weight in [0.0, 1.0].

    weight = 0.5 ** (age_days / half_life_days)

        0 days old   → 1.0
        7 days old   → 0.5  (with default half-life)
        14 days old  → 0.25
        30 days old  → ~0.05

    Args:
        published_at:   When the item was published. Tz-aware; if naive,
                        assumed to be UTC. None → returns 0.0 (treat
                        unknown timestamps as fully decayed).
        half_life_days: Decay half-life. Default 7 matches the
                        evidence-handling skill.
        now:            Reference "now" for age calculation. Injected for
                        deterministic testing; defaults to
                        datetime.now(tz=UTC).

    Returns:
        Float in [0.0, 1.0]. Negative ages (future timestamps) are clamped
        to 1.0 — the item is at most "as fresh as right now".
    """
    if published_at is None:
        return 0.0

    if now is None:
        now = datetime.now(tz=timezone.utc)

    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)

    age_seconds = (now - published_at).total_seconds()
    age_days = age_seconds / 86400.0

    if age_days <= 0:
        return 1.0

    return 0.5 ** (age_days / half_life_days)
