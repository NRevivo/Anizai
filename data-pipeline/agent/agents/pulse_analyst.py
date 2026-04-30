"""
agent/agents/pulse_analyst.py — Pulse Analyst retrieval agent (spec §8.4.2).

Purpose: surface community sentiment, market consensus, and contrarian
arguments from `social_vectors` + `social_vault` for a given query
embedding. Splits results into Polymarket market_consensus rows and
HackerNews community_discussion rows; drills into raw comments only when
Polymarket consensus is in the "extreme" range (skew detector).

Algorithm (spec §8.4.2):
    1. similarity_search(limit=10), no platform/impact/reliability filters.
    2. Split rows by source_platform: 'polymarket' vs 'hackernews'.
    3. Polymarket: pull consensus_rating, comment_volume_analyzed,
       aggregation_window_hours, market_id_ref from `platform_logic`.
    4. HackerNews: pull points, top_technical_insights, community_sentiment
       from `platform_logic`.
    5. For Polymarket rows where consensus_rating > 0.8 or < 0.2, drill
       down to social_vault via silver_data_ref (low-volume skew check).
    6. Tag every item with evidence_weight; Polymarket-with-volume outranks
       HackerNews discussion (spec: "market participants have financial
       skin in the game").
    7. Compute overall_sentiment as weighted average across both lists.
    8. Return PulseEvidence dict.

Spec corrections / drift handled here (audit §3.2 T19.3 footnote):
    - Drift A: `key_arguments_pro` / `key_arguments_con` are not populated
      anywhere in production. Returned as `[]` and tracked as
      KG-PHASE8-11 in task_plan.md until gold_job is enhanced.
    - Drift B: spec §8.4.2 originally typed `community_sentiment` as `str`;
      production stores float in [-1.0, 1.0]. Spec was corrected in T19.3
      (see "Correction (T19.3, 2026-04-30)" note in agentic_hub_spec.md
      §8.4.2). This module returns the raw float — no bucketing.

Sprint 19 D2 deferral: agentEvents emission and budget tracking deferred to
Sprint 25 / Sprint 22+ (see agent/agents/__init__.py).

Service isolation (CLAUDE.md §3.3): persistence is reached only through
`agent/tools/social_tools` — this module does not import persistence
directly.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.4.2
    - data-pipeline/docs/sprint19_persistence_audit.md §3.2 (T19.3 footnote)
    - .claude/skills/evidence-handling/SKILL.md (recency_weight, evidence_weight)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from agent.tools import social_tools
from agent.utils.recency import compute_recency_weight


# ==========================================================
# Spec constants
# ==========================================================

LIMIT = 10                               # spec §8.4.2 step 1

CONSENSUS_EXTREME_HIGH = 0.8             # spec §8.4.2 step 5 — drill-down trigger
CONSENSUS_EXTREME_LOW = 0.2              # spec §8.4.2 step 5 — drill-down trigger

RECENCY_HALF_LIFE_DAYS = 7               # evidence-handling skill default

# Per-platform evidence_weight multipliers (spec §8.4.2 step 6: "Polymarket
# consensus with high volume gets higher weight than HackerNews — market
# participants have financial skin in the game"). Polymarket caps at 1.0;
# HackerNews caps at 0.6 of the same base score.
PLATFORM_WEIGHT_POLYMARKET = 1.0
PLATFORM_WEIGHT_HACKERNEWS = 0.6

# Volume normalization caps. Saturating both at 100 keeps the weighted sum
# in [0, 1] before the per-platform multiplier is applied. Below the cap,
# more volume → higher weight; above the cap, the volume signal plateaus.
VOLUME_NORMALIZATION_CAP = 100

# evidence_weight component weights (sum = 1.0).
WEIGHT_SIM = 0.5
WEIGHT_VOLUME = 0.3
WEIGHT_RECENCY = 0.2


def run(
    query_embedding: list[float],
    *,
    now: Optional[datetime] = None,
) -> dict:
    """
    Execute the Pulse Analyst retrieval algorithm.

    Args:
        query_embedding: 1536-dim float list. Embedding of the user's question.
        now:             Reference "now" for recency scoring. Injected for
                         deterministic testing; defaults to UTC now.

    Returns:
        PulseEvidence dict per spec §8.4.2 (with the T19.3 corrections
        documented at module-level). Empty short-circuit returns
        market_consensus=[], community_discussion=[], overall_sentiment=0.0,
        empty=True.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)

    rows = social_tools.similarity_search(
        query_embedding=query_embedding,
        limit=LIMIT,
    )

    if not rows:
        return {
            "market_consensus": [],
            "community_discussion": [],
            "overall_sentiment": 0.0,
            "empty": True,
        }

    polymarket_rows = [r for r in rows if r.get("source_platform") == "polymarket"]
    hackernews_rows = [r for r in rows if r.get("source_platform") == "hackernews"]

    market_consensus = [
        _pack_polymarket(row, now=now) for row in polymarket_rows
    ]
    community_discussion = [
        _pack_hackernews(row, now=now) for row in hackernews_rows
    ]

    overall_sentiment = _overall_sentiment(market_consensus, community_discussion)

    return {
        "market_consensus": market_consensus,
        "community_discussion": community_discussion,
        "overall_sentiment": overall_sentiment,
        "empty": False,
    }


# ==========================================================
# Polymarket packing
# ==========================================================

def _pack_polymarket(row: dict, *, now: datetime) -> dict:
    """
    Build one entry of PulseEvidence.market_consensus.

    Drills into social_vault when consensus_rating is in the extreme range
    (spec §8.4.2 step 5) — extreme consensus on low volume is a known
    skew pattern, so we expose the raw comment archive on those rows.
    The drill-down result itself is not currently surfaced in the packed
    item (it would expose Silver-layer raw_data_ref blobs to synthesis),
    but the `fetch_raw_comments` call still fires so a future enhancement
    or audit trail can verify the drill-down occurred.
    """
    enrichment = row.get("enrichment_ai") or {}
    platform_logic = row.get("platform_logic") or {}

    consensus_rating = float(platform_logic.get("consensus_rating") or 0.5)
    comment_volume = int(platform_logic.get("comment_volume_analyzed") or 0)
    aggregation_window = int(platform_logic.get("aggregation_window_hours") or 0)
    market_id_ref = platform_logic.get("market_id_ref") or ""

    silver_data_ref = row.get("silver_data_ref")
    if (
        silver_data_ref
        and (consensus_rating > CONSENSUS_EXTREME_HIGH or consensus_rating < CONSENSUS_EXTREME_LOW)
    ):
        social_tools.fetch_raw_comments(silver_data_ref)

    similarity = float(row.get("similarity") or 0.0)
    recency = compute_recency_weight(
        published_at=row.get("published_at"),
        half_life_days=RECENCY_HALF_LIFE_DAYS,
        now=now,
    )
    volume_norm = min(comment_volume / VOLUME_NORMALIZATION_CAP, 1.0)
    base = WEIGHT_SIM * similarity + WEIGHT_VOLUME * volume_norm + WEIGHT_RECENCY * recency
    evidence_weight = base * PLATFORM_WEIGHT_POLYMARKET

    return {
        "signal_id": row.get("signal_id") or "",
        "market_id_ref": market_id_ref,
        "consensus_rating": consensus_rating,
        "comment_volume_analyzed": comment_volume,
        "aggregation_window_hours": aggregation_window,
        "executive_summary": enrichment.get("executive_summary") or "",
        # KG-PHASE8-11: gold_job does not populate pro/con splits.
        # Returning empty lists keeps the spec contract honest until the
        # consensus prompt + gold_job enhancements land.
        "key_arguments_pro": [],
        "key_arguments_con": [],
        "similarity": similarity,
        "evidence_weight": evidence_weight,
    }


# ==========================================================
# HackerNews packing
# ==========================================================

def _pack_hackernews(row: dict, *, now: datetime) -> dict:
    """
    Build one entry of PulseEvidence.community_discussion.

    `community_sentiment` is returned as the raw float in [-1.0, 1.0]
    per the T19.3 spec correction (see module docstring + spec §8.4.2
    Correction note).
    """
    content_vitals = row.get("content_vitals") or {}
    platform_logic = row.get("platform_logic") or {}

    points = int(platform_logic.get("points") or 0)
    insights = platform_logic.get("top_technical_insights") or []
    if not isinstance(insights, list):
        insights = []
    community_sentiment = float(platform_logic.get("community_sentiment") or 0.0)

    similarity = float(row.get("similarity") or 0.0)
    recency = compute_recency_weight(
        published_at=row.get("published_at"),
        half_life_days=RECENCY_HALF_LIFE_DAYS,
        now=now,
    )
    points_norm = min(points / VOLUME_NORMALIZATION_CAP, 1.0)
    base = WEIGHT_SIM * similarity + WEIGHT_VOLUME * points_norm + WEIGHT_RECENCY * recency
    evidence_weight = base * PLATFORM_WEIGHT_HACKERNEWS

    return {
        "signal_id": row.get("signal_id") or "",
        "platform": "hackernews",
        "title": content_vitals.get("title") or "",
        "points": points,
        "top_technical_insights": insights,
        "community_sentiment": community_sentiment,
        "similarity": similarity,
        "evidence_weight": evidence_weight,
    }


# ==========================================================
# Aggregations
# ==========================================================

def _overall_sentiment(
    market_consensus: list[dict],
    community_discussion: list[dict],
) -> float:
    """
    Weighted average sentiment across both lists in [-1.0, 1.0].

    - Polymarket `consensus_rating` ∈ [0, 1] (bearish → bullish on YES) is
      remapped to [-1, 1] via `2*x - 1`.
    - HackerNews `community_sentiment` is already in [-1, 1].
    - Each item's contribution is weighted by its `evidence_weight`.
    - When both lists are empty (or all evidence weights are zero), returns 0.0.
    """
    weighted_sum = 0.0
    total_weight = 0.0

    for item in market_consensus:
        sentiment = 2.0 * item["consensus_rating"] - 1.0
        weight = item["evidence_weight"]
        weighted_sum += sentiment * weight
        total_weight += weight

    for item in community_discussion:
        sentiment = item["community_sentiment"]
        weight = item["evidence_weight"]
        weighted_sum += sentiment * weight
        total_weight += weight

    if total_weight == 0.0:
        return 0.0
    return weighted_sum / total_weight
