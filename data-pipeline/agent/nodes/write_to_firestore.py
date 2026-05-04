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

Why empty predictionSeries / sentimentTimeSeries for Sprint 20:
    KG-PHASE8-12: no canonical Polymarket market → no price-history
    time series → no predictionSeries data. Q5 resolution: empty
    sentimentTimeSeries is acceptable for Sprint 20; Sprint 22+
    reactive search may add points. The helpers in firestore_client
    handle empty lists by writing zero docs (the BI cards render their
    empty states).

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

from agent import firestore_client
from agent.errors import AgentProcessingError
from agent.schemas import SOURCE_TYPE_TO_FRONTEND_TYPE

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

    # Sprint 20: KG-PHASE8-12 + Q5 → both empty.
    prediction_series_points: list[dict] = []
    sentiment_time_series_points: list[dict] = []

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
    firestore_client.update_session_status(session_id, "done")

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
