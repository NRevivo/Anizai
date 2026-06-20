"""
agent/utils/evidence_counting.py — shared raw-signal counting (Sprint 23.5, R2).

`sufficiency_check` (which decides whether the vault is rich enough to
synthesize) and `rate_evidence` (which normalizes the packages into the
unified EvidenceItem shape) both need a single, agreed answer to "how many
signals did the vault return?". If each computed that independently they
could drift — sufficiency could say "enough" while rate_evidence produces a
different count. This helper is the one definition both call (Advisor↔Ron
decision record §2 / R2).

**Raw, pre-normalization** (the constraint in R2): this counts items in the
three raw evidence *packages* as the agents pack them — NOT normalized
`EvidenceItem`s, which only exist downstream inside `rate_evidence`. The
name says `raw_signals` precisely so it can't be mistaken for an
EvidenceItem count. `sufficiency_check` runs BEFORE `rate_evidence`
(decision record §6 / R4), so at sufficiency time normalized EvidenceItems
do not exist yet — the raw packages are all there is to count.

What counts as one signal (mirrors `rate_evidence`'s normalize_* helpers so
the floor decision and the produced trail agree):
    researcher_evidence.articles[]           — each article
    pulse_evidence.market_consensus[]         — each Polymarket consensus row
    pulse_evidence.community_discussion[]     — each HackerNews thread
    market_evidence.fred_anomalies[]          — each FRED anomaly

The Polymarket market anchor in `market_evidence.polymarket` is deliberately
NOT counted — it is the market itself (rendered as marketProbability /
marketComparison), not supporting evidence. This matches `rate_evidence`,
which also excludes it from the evidence trail.

Spec references:
    - data-pipeline/docs/B_hub/sprint23_5_pre26_remediation.md §2 (Track 1)
    - cabinet-outputs/advisor/problem-reports/sprint23_5_advisor-ron-decisions.md §2
"""

from __future__ import annotations

from typing import Optional


def _package_list(package: Optional[dict], *keys: str) -> int:
    """
    Sum the lengths of the named list fields on one evidence package.

    A None/missing package, or one flagged `{"empty": True}` (the agents'
    "I ran but found nothing" sentinel), contributes 0. Non-list fields are
    treated as absent.
    """
    if not package or package.get("empty"):
        return 0
    total = 0
    for key in keys:
        value = package.get(key)
        if isinstance(value, list):
            total += len(value)
    return total


def count_raw_signals(state: dict) -> int:
    """
    Count raw pre-normalization signals across the three evidence packages
    in `state`.

    Reads (all optional, all defensively):
        state["researcher_evidence"]["articles"]
        state["pulse_evidence"]["market_consensus"]
        state["pulse_evidence"]["community_discussion"]
        state["market_evidence"]["fred_anomalies"]

    Returns:
        Total number of raw signal items (int, >= 0). Used by
        `sufficiency_check` against `AGENT_EVIDENCE_MIN_COUNT`.
    """
    researcher = _package_list(state.get("researcher_evidence"), "articles")
    pulse = _package_list(
        state.get("pulse_evidence"), "market_consensus", "community_discussion"
    )
    market = _package_list(state.get("market_evidence"), "fred_anomalies")
    return researcher + pulse + market
