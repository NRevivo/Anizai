"""
agent/nodes/write_to_firestore.py — Node 7 of the forecast graph
(Sprint 20 T20.5).

The terminal node before END. Persists the synthesized forecast to
Firestore in the contract-mandated order (subcollections first, then
sessionResults, then status transitions to 'done') so the frontend's
real-time listener never reads a `done` session before its supporting
subcollections exist.

Write order (frontend-integration skill — preserves frontend invariants):
    1. evidence subcollection           (sessions/{id}/evidence/{eid})
    2. predictionSeries subcollection   (sessions/{id}/predictionSeries)
    3. sentimentTimeSeries subcollection(sessions/{id}/sentimentTimeSeries)
    4. sessionResults top-level doc     (sessionResults/{id})
    5. session.status = 'done'          (sessions/{id})
    6. forecastQueries.status = 'done'  (forecastQueries/{id})

Why this order matters:
    The frontend listens on session.status. When status flips to 'done'
    it reads sessionResults + the four subcollections. Writing the
    status FIRST would let the frontend read an empty subcollection
    before our writes land — breaks the "done = ready to render"
    contract. Subcollections → result → status keeps that contract.

Why sessionResults is top-level, not under sessions/{id}:
    Per design D6: the partner's server reads sessionResults as a
    top-level collection (server/src/repositories/session.repository.ts:171,
    `collection('sessionResults').doc(sessionId)`). Sprint 18 already
    matched this. The spec patch §8.7.1 wording ("under sessions/{id}/")
    is documentation drift; KG-PHASE8-13 is opened at Sprint 20 close
    to reconcile. write_to_firestore follows the server's actual
    contract.

Sprint 22 T22.4 — predictionSeries wiring:
    When `market_evidence.polymarket` is populated (Tier 1 via the
    fuzzy-match resolver), the Polymarket price_history is shaped at
    the write site and persisted as docs in the predictionSeries
    subcollection. Tier 2 (no polymarket payload) continues to pass
    `[]` — the FE renders an empty state.

Sprint 22 T22.6 — sentimentTimeSeries wiring (closes KG-PHASE8-22):
    The bucketing helper at `agent/utils/sentiment_bucketing` is invoked
    twice (Expert from `researcher_evidence.articles`, Public from
    `pulse_evidence.community_discussion`) and the two streams are
    merged into one doc per bucket-date. Per D6: a doc is emitted when
    EITHER source has data on that day; the missing side is written as
    `null`. Per D6 limitation note: the server's `mapSentimentDoc`
    applies `?? 0` to missing values, rendering null as 0.0 on the FE.
    Documented limitation flagged for FE work (Future Enhancement 5).
    Per D3-pattern: the agent in-state scale is `[-1, 1]`; the wire
    shape is `[0, 1]` per the FE comment in App.tsx:177. Normalization
    `(x+1)/2` happens at this write boundary.

Sprint 22 T22.7 — canonicalKey on session doc:
    Pre-emptive infrastructure for Future Enhancement 3 (cross-user
    cache). On the success transition, `canonicalKey` is set on the
    session doc to either the resolved Polymarket market identifier
    (Tier 1) or explicit `None` (Tier 2). Explicit None overwrites any
    candidate UUID Express may have written during a clarification
    cycle, so the future cache query (spec §8.7.3) correctly classifies
    Tier 2 sessions as cacheless. The `canonical_key` kwarg on
    `update_session_status` uses the UNSET sentinel pattern so
    non-success transitions (failed, awaiting_clarification) don't
    clobber Express's prior writes.

Why this node raises rather than degrades:
    Per implementation-plan error matrix: "Firestore batch write fails
    (network blip) → Re-raise from write_to_firestore; runner's
    _mark_failed flips both docs to failed. Partial subcollection
    writes possible — acceptable for now (reactive_search Sprint 22
    will tighten this)." Synthesis already produced a forecast; the
    only thing this node can fail at is the persistence step itself,
    which is fundamentally non-degradeable (a half-written session is
    worse than a failed-and-retryable one).

Service isolation (CLAUDE.md §3.3):
    Talks to Firestore only via firestore_client (CLAUDE.md §3.2 —
    centralized Firebase access). No LLM calls, no vault reads, no
    cross-node calls. State-mediated input (agent-design P2).

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.3.2 (Node 7 in topology)
    - data-pipeline/docs/agentic_hub_spec.md §8.7.1 (subcollection paths)
    - data-pipeline/docs/agentic_hub_spec.md §8.7.2 (SessionResult schema)
    - data-pipeline/docs/anizai_handoff_consolidated.md §5.1 (write order
      contract)
    - .claude/skills/frontend-integration/SKILL.md (status transitions
      are the public API; write subcollections before status='done')
    - .claude/skills/evidence-handling/SKILL.md
      (SOURCE_TYPE_TO_FRONTEND_TYPE mapping for subcollection items)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from agent import firestore_client
from agent.errors import AgentProcessingError
from agent.schemas import SOURCE_TYPE_TO_FRONTEND_TYPE
from agent.utils.sentiment_bucketing import bucket_sentiment_by_time

logger = logging.getLogger(__name__)


# ==========================================================
# Public node function
# ==========================================================
def run(state: dict) -> dict:
    """
    Persist the synthesized forecast to Firestore.

    Args:
        state: Running ForecastState. Must contain `session_id` and
               `synthesis_result`. Reads `evidence_trail` (default
               empty list) for the evidence subcollection;
               predictionSeries/sentimentTimeSeries are empty for
               Sprint 20 (D6 + Q5).

    Returns:
        Empty partial state dict. write_to_firestore is a sink — it
        writes side effects but produces no new state fields. The graph
        terminates at END after this node.

    Raises:
        AgentProcessingError: when session_id or synthesis_result are
            missing (upstream-node bug). Firestore SDK exceptions
            propagate unwrapped — process_query._mark_failed catches
            them and writes session=failed/query=failed.
    """
    session_id = state.get("session_id")
    if not session_id:
        raise AgentProcessingError(
            "write_to_firestore: missing session_id in state — "
            "claim_session should have populated it"
        )

    synthesis_result = state.get("synthesis_result")
    if not synthesis_result:
        raise AgentProcessingError(
            "write_to_firestore: missing synthesis_result in state — "
            "synthesize should have populated it"
        )

    evidence_trail: list[dict] = list(state.get("evidence_trail") or [])

    # Sprint 22 T22.4: shape the Polymarket price_history into the
    # predictionSeries doc schema the Express BFF reads (PredictionPoint:
    # ts/probability/confidence/reasonType/evidenceIds). Tier 2 — no
    # polymarket payload — passes []. See _shape_prediction_series.
    polymarket_payload = (state.get("market_evidence") or {}).get("polymarket")
    prediction_series_points: list[dict] = (
        _shape_prediction_series(polymarket_payload.get("price_history") or [])
        if polymarket_payload else []
    )

    # Sprint 22 T22.6: bucket + merge Expert (researcher) + Public (pulse)
    # sentiment streams into one doc per bucket-date (D6 — a1 logic).
    sentiment_time_series_points: list[dict] = _shape_sentiment_time_series(
        researcher_evidence=state.get("researcher_evidence"),
        pulse_evidence=state.get("pulse_evidence"),
    )

    # 1. Evidence subcollection.
    evidence_docs = _shape_evidence_for_firestore(evidence_trail)
    firestore_client.write_evidence_batch(session_id, evidence_docs)

    # 2. predictionSeries (empty for Sprint 20).
    firestore_client.write_prediction_series(session_id, prediction_series_points)

    # 3. sentimentTimeSeries (empty for Sprint 20).
    firestore_client.write_sentiment_time_series(
        session_id, sentiment_time_series_points,
    )

    # 4. sessionResults — top-level collection (server contract D6).
    firestore_client.write_session_result(session_id, synthesis_result)

    # 5. session.status = done (frontend's "render now" signal).
    # Sprint 21 T21.8: include tier on the session doc so the frontend's
    # session doc listener sees the tier without reading sessionResults.
    # Per frontend-integration skill: sessions/{id}.tier is set by the hub.
    # Sprint 22 T22.7: include canonicalKey — the resolved Polymarket
    # market identifier on Tier 1, or explicit None on Tier 2. Always
    # passed on the success transition so a clarification-resume's
    # candidate UUID (Express-written) is overwritten with the agent's
    # final resolution. Pre-emptive infrastructure for Future
    # Enhancement 3 (cross-user cache).
    tier: Optional[str] = state.get("tier")
    market_slug = (
        polymarket_payload.get("market_slug") if polymarket_payload else None
    )
    canonical_key: Optional[str] = market_slug or None  # "" → None defensively
    firestore_client.update_session_status(
        session_id, "done",
        tier=tier,
        canonical_key=canonical_key,
    )

    # 6. forecastQueries.status = done (clear out of the worker queue).
    # By server contract session_id == query_doc_id (session.repository.ts:347).
    firestore_client.update_query_status(session_id, "done")

    logger.info(
        "write_to_firestore: session_id=%s evidence=%d done",
        session_id, len(evidence_docs),
    )

    # LangGraph requires every node to write at least one state field.
    # write_to_firestore is conceptually a sink (its product is the
    # Firestore writes), so we echo the existing `errors` list as an
    # identity write. This satisfies LangGraph's InvalidUpdateError
    # check without actually mutating state. (A future state schema
    # extension could add `persistence_complete: bool` here; for
    # Sprint 20 the identity-echo is the smallest-footprint fix.)
    return {"errors": list(state.get("errors") or [])}


# ==========================================================
# Helpers
# ==========================================================
def _shape_evidence_for_firestore(evidence_trail: list[dict]) -> list[dict]:
    """
    Augment each EvidenceItem dict with the frontend display `type`
    field per the §8.5.5 source_type → type mapping. The frontend's
    filter-tab UI reads this `type` field to bucket evidence into
    news/social/expert/market columns.

    Why add the field here (not in rate_evidence):
        The mapping is a frontend-rendering concern, not a rating
        concern. Keeping it in write_to_firestore means the in-graph
        evidence_trail stays platform-agnostic and only acquires
        frontend-shaped fields at the persistence boundary.

    Why log + skip on unknown source_type:
        Defensive — every Patch 10 enum value is in the mapping
        (test_schemas.py pins this), but a future enum extension could
        slip through. Better to drop a single item and log than to
        write a doc with a silently-empty `type` that breaks the
        filter UI.
    """
    out: list[dict] = []
    for item in evidence_trail:
        source_type = item.get("source_type", "")
        frontend_type = SOURCE_TYPE_TO_FRONTEND_TYPE.get(source_type)
        if frontend_type is None:
            logger.warning(
                "write_to_firestore: dropping evidence item with unknown "
                "source_type=%r (evidence_id=%s)",
                source_type, item.get("evidence_id"),
            )
            continue
        out.append({**item, "type": frontend_type})
    return out


# Sprint 22 T22.4: Polymarket price-history adapter.
# Pinned via the FE PredictionPoint interface + the Express BFF's
# getPredictionSeries decoder (server/src/repositories/session.repository.ts:274).
#
# Constants are surfaced at module level so the rationale is greppable
# without re-reading the helper body.
#
# Why reasonType = "market" for every point:
#   The FE PredictionPoint.reasonType enum is 'news' | 'market' |
#   'model_update'. Polymarket price observations ARE market signals —
#   the consensus probability the market itself reported at a moment in
#   time. They're not derived from news events and they're not agent
#   model updates.
#
# Why confidence = 1.0 for every point:
#   The PredictionPoint.confidence field was designed for agent-generated
#   probability snapshots (Future Enhancement: agent's own probability
#   over delta-refresh runs). For market observations, point-level
#   confidence has no clean semantic meaning. 1.0 is chosen because the
#   field is required (FE interface declares confidence as a non-optional
#   number — omitting writes `undefined` which breaks .toFixed() etc. in
#   the chart renderer). Sprint 22 D3. Flag for re-visit if the FE starts
#   rendering per-point confidence bands.
#
# Why evidenceIds = []:
#   No per-observation evidence linkage — the price IS the recorded
#   value. The server's decoder defaults missing/null to [] but explicit
#   is clearer.
_PREDICTION_POINT_REASON_TYPE: str = "market"
_PREDICTION_POINT_CONFIDENCE: float = 1.0


def _shape_prediction_series(price_history: list[dict]) -> list[dict]:
    """
    Transform `market_evidence.polymarket.price_history` entries into
    the doc shape the Express BFF's `getPredictionSeries` decoder reads
    (PredictionPoint: ts/probability/confidence/reasonType/evidenceIds).

    Why transform at the write site (not upstream):
        Market Bridge's `_price_history_point` returns the in-state
        shape `{timestamp: ISO-str, value: float}` — chart-friendly and
        used by multiple consumers in `_pack_polymarket_payload`. The
        Firestore-specific renaming + injection of confidence/reasonType
        is a persistence-layer concern; keeping it at the write site
        leaves the in-state shape platform-agnostic per
        frontend-integration skill.

    Why `ts` becomes a tz-aware Python datetime (not an ISO string):
        Verified 2026-05-23 against server/src/services/firebase.service.ts:16
        — toISOString() does `timestamp?.toDate?.()`. If the doc field is
        a string, `toDate` is undefined and the optional chain
        short-circuits to null. The server's getPredictionSeries decoder
        would then return `ts: ''` for every point. Firestore Python SDK
        auto-converts tz-aware datetimes to Firestore Timestamps —
        that's the format the server's `toDate()` needs.

    Defensive shape:
        - Empty/missing `timestamp` field on a point → skip (don't crash
          the whole subcollection write).
        - Unparseable ISO string → skip (same).
        - Naive datetime from `fromisoformat` → coerced to UTC. Production
          path produces tz-aware strings (psycopg2 TIMESTAMPTZ → tz-aware
          datetime → .isoformat() includes offset), but the guard catches
          any future caller bypassing _price_history_point.

    Args:
        price_history: List of `{timestamp: ISO-str, value: float}` dicts
                       as produced by `agent.agents.market_bridge.
                       _price_history_point`. Empty list is allowed.

    Returns:
        List of dicts ready for `firestore_client.write_prediction_series`.
        Each dict matches the PredictionPoint schema minus `id` (the
        Firestore auto-id supplies it on read).
    """
    shaped: list[dict] = []
    for point in price_history:
        ts_str = point.get("timestamp") or ""
        if not ts_str:
            continue
        try:
            ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            logger.warning(
                "write_to_firestore: dropping predictionSeries point with "
                "unparseable timestamp=%r", ts_str,
            )
            continue
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        shaped.append({
            "ts": ts_dt,
            "probability": float(point.get("value") or 0.0),
            "confidence": _PREDICTION_POINT_CONFIDENCE,
            "reasonType": _PREDICTION_POINT_REASON_TYPE,
            "evidenceIds": [],
        })
    return shaped


# Sprint 22 T22.6: sentimentTimeSeries adapter.
# Pinned via the FE SentimentDataPoint interface + the Express BFF's
# getSentimentTimeSeries decoder
# (server/src/repositories/session.repository.ts:366).
#
# Why one doc per bucket-date (NOT one per source):
#   The server's mapSentimentDoc reads `expertSentiment` AND
#   `publicSentiment` from the same doc. The two source streams (Expert
#   from researcher, Public from pulse) must be MERGED by bucket-start
#   before writing.
#
# Why scale normalization (x+1)/2:
#   Agent in-state sentiment is [-1, 1] (researcher.sentiment_score and
#   pulse.community_sentiment per their respective prompts/docstrings).
#   The FE expects [0, 1] (client/src/App.tsx:177 comment + mockData
#   values like 0.65, 0.42). Normalization at the write boundary keeps
#   the upstream evidence shapes platform-agnostic.
#
# Why emit doc when EITHER source has data (D6 — a1):
#   Researcher typically has 5-10 items over 14 days; Pulse has 3-7. A
#   strict both-sources gate (a3) would produce 2-4 sparse dots on a
#   14-day chart that LOOKS broken to users. Emitting docs on every
#   bucket where at least one source has data renders a continuous
#   timeline. Trade-off: missing sides land as Firestore null, which
#   the server's `?? 0` coerces to 0.0 on read — known limitation,
#   flagged for FE-side null discrimination as a Future Enhancement 5
#   item. V1 prioritises continuous-timeline UX over per-day per-source
#   accuracy.


def _normalize_sentiment_to_unit_interval(value: Optional[float]) -> Optional[float]:
    """
    Map agent in-state sentiment in [-1, 1] to FE wire shape [0, 1].
    None passes through (avg_sentiment is None when a bucket had zero
    samples for that source — see bucket_sentiment_by_time docstring).

    Examples:
        -1.0 (strong bearish)  → 0.0
         0.0 (neutral)         → 0.5
         1.0 (strong bullish)  → 1.0
    """
    if value is None:
        return None
    return (value + 1.0) / 2.0


def _shape_sentiment_time_series(
    *,
    researcher_evidence: Optional[dict],
    pulse_evidence: Optional[dict],
    now: Optional[datetime] = None,
) -> list[dict]:
    """
    Bucket Expert + Public sentiment streams by calendar day, merge by
    bucket-date, and shape into the SentimentDataPoint docs the Express
    BFF reads (server/src/repositories/session.repository.ts:366).

    Args:
        researcher_evidence: `state.researcher_evidence` — dict with
                             `articles` list of items carrying
                             `sentiment_score` ([-1, 1]) and
                             `published_at` (ISO string). None or empty
                             evidence-package treated as zero items.
        pulse_evidence:      `state.pulse_evidence` — dict with
                             `community_discussion` list of HackerNews
                             items carrying `community_sentiment`
                             ([-1, 1]) and `published_at` (Sprint 22 D5
                             propagation). None or empty treated as
                             zero items.
        now:                 Test injection — defaults to UTC now. The
                             bucketing helper uses this to anchor
                             calendar-day boundaries.

    Returns:
        List of docs ready for
        `firestore_client.write_sentiment_time_series`, ordered by
        `ts` ascending. Per D6 (a1):
            - A doc is emitted for every bucket-date where at least
              one source has data (sample_count >= 1).
            - When only one source has data, the missing side is
              written as None (server coerces to 0 on read — D6
              documented limitation).
            - When BOTH sources are empty for a bucket, no doc is
              emitted (gap in the FE chart).

        Doc shape:
            {
                "ts":              datetime,  # tz-aware UTC, bucket START
                "date":            "YYYY-MM-DD",  # ISO calendar date label
                "expertSentiment": Optional[float],  # [0, 1] or None
                "publicSentiment": Optional[float],  # [0, 1] or None
                "expertUpper":     None,   # confidence bands deferred
                "expertLower":     None,   # to Future Enhancement 5
            }

    Both source streams are bucketed with the same window/bucket params
    and the same `now` — that guarantees both lists are length-aligned
    and contain identical bucket-start dates in the same order, so the
    `zip` over them is safe.
    """
    researcher_articles: list[dict] = []
    if researcher_evidence is not None:
        articles = researcher_evidence.get("articles") or []
        if isinstance(articles, list):
            researcher_articles = articles

    pulse_discussion: list[dict] = []
    if pulse_evidence is not None:
        discussion = pulse_evidence.get("community_discussion") or []
        if isinstance(discussion, list):
            pulse_discussion = discussion

    expert_buckets = bucket_sentiment_by_time(
        researcher_articles,
        sentiment_field="sentiment_score",
        time_field="published_at",
        now=now,
    )
    public_buckets = bucket_sentiment_by_time(
        pulse_discussion,
        sentiment_field="community_sentiment",
        time_field="published_at",
        now=now,
    )

    docs: list[dict] = []
    for expert, public in zip(expert_buckets, public_buckets):
        if expert["avg_sentiment"] is None and public["avg_sentiment"] is None:
            continue
        bucket_date: datetime = expert["date"]
        docs.append({
            "ts": bucket_date,
            "date": bucket_date.strftime("%Y-%m-%d"),
            "expertSentiment": _normalize_sentiment_to_unit_interval(
                expert["avg_sentiment"]
            ),
            "publicSentiment": _normalize_sentiment_to_unit_interval(
                public["avg_sentiment"]
            ),
            "expertUpper": None,
            "expertLower": None,
        })
    return docs
