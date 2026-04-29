"""
Stub processing flow for the Agentic Hub (Sprint 18).

This module implements the end-to-end processing entry point that the worker
(T6) calls after picking up a forecastQueries doc from Firestore. The Sprint
18 contract is intentionally minimal — no LLM calls, no vault reads, no
reactive search — just enough to prove the Firestore round-trip works:

    claim_query  -> sessions/{id}: claimed
                 -> sessions/{id}: running
                 -> sessionResults/{id}: stub payload
                 -> sessions/{id}: done
                 -> forecastQueries/{id}: done

If anything raises mid-flight, _mark_failed updates both docs to 'failed'.

The stub result obeys the §8.7.2 SessionResult schema fully — every field
is present with a defensible value — so the frontend's render code never
encounters a missing field, even at zero confidence. Field rationale:

    finalProbability=0.5       neutral default; honest "no information"
    confidence=0.5             sits in the [0.5, 0.8) "Moderate" band
    confidenceLabel="Moderate" derived from confidence per §8.7.2 line 861
    consensusStrength="Weak"   evidence count is 0
    evidenceVolumeLabel="Low"  evidence count is 0
    tier="tier_1"              dominant frontend render path; with
                               marketProbability=null + marketComparison=[]
                               the UI shows the Patch 4 "no canonical
                               market" empty state. Sprint 18 prep agreed
                               on tier_1 to test the dominant path first
                               and avoid Tier 2 UI render paths that may
                               not yet be built on the frontend side.
    bottomLineAnswer / detailedExplanation / etc.
                               literal "stub mode" text — never empty
                               strings (defensive: frontend doesn't have
                               to special-case empty fields)
    whatIDidntFind             explains stub status to the user

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.7.2 (SessionResult schema)
    - data-pipeline/docs/agentic_hub_spec.md §8.8 (worker pattern)
    - data-pipeline/docs/anizai_handoff_consolidated.md §5.1 (frontend contract)
"""

import logging
from typing import Optional

from agent.config import settings
from agent.firestore_client import (
    SERVER_TIMESTAMP,
    claim_query,
    update_query_status,
    update_session_status,
    write_session_result,
)

logger = logging.getLogger(__name__)

# Module-level so the value appears in every result and can be grepped from
# logs / Firestore for which agent build produced a result. Bumped on each
# substantive sprint.
AGENT_VERSION = "0.1.0-sprint18-stub"

# Stub constants — defined as named values rather than inline literals so
# they're discoverable and easy to remove when Sprint 19 replaces stub
# logic with real reasoning.
_STUB_FINAL_PROBABILITY = 0.5
_STUB_CONFIDENCE = 0.5
_STUB_TIER = "tier_1"
_STUB_WHAT_I_DIDNT_FIND = (
    "Sprint 18 stub mode — the agent is online and the Firestore round-trip "
    "is working, but real reasoning, evidence retrieval, and market "
    "comparison are not yet implemented. Expect substantive answers from "
    "Sprint 19 onward."
)


# ==========================================================
# Label derivation — §8.7.2 lines 860-862
# ==========================================================

def _derive_label(value: float) -> str:
    """confidenceLabel from confidence: <0.5 Low, [0.5,0.8) Moderate, >=0.8 High."""
    if value >= 0.8:
        return "High"
    if value >= 0.5:
        return "Moderate"
    return "Low"


def _derive_consensus(value: float) -> str:
    """consensusStrength only. Vocabulary: Weak/Mixed/Strong (per §8.7.2
    line 829). Same threshold band shape as _derive_label.

    evidenceVolumeLabel uses Low/Moderate/High (§8.7.2 line 830) and is
    derived via _derive_label, NOT this function."""
    if value >= 0.8:
        return "Strong"
    if value >= 0.5:
        return "Mixed"
    return "Weak"


# ==========================================================
# Stub result builder
# ==========================================================

def _build_stub_result(question: str) -> dict:
    """
    Construct a §8.7.2-compliant SessionResult dict with placeholder values.

    Notes on field choices:
      - generatedAt is written here (alongside createdAt/updatedAt added by
        the wrapper). Spec §8.7.2 lists generatedAt; the current server
        getSessionResult reads createdAt/updatedAt only. Writing generatedAt
        too is defensive against future server reads — KG-PHASE8-6 tracks
        the spec/server drift.
      - sessionId is intentionally NOT set here — the wrapper sets it from
        its arg to avoid duplication.
      - All list fields default to [] (not None) so frontend list rendering
        doesn't have to null-check. whatIDidntFind is the only list with a
        populated entry — the stub-mode caption.
    """
    return {
        # Probability + confidence
        "finalProbability": _STUB_FINAL_PROBABILITY,
        "confidence": _STUB_CONFIDENCE,
        "confidenceLabel": _derive_label(_STUB_CONFIDENCE),
        "consensusStrength": _derive_consensus(0.0),
        "evidenceVolumeLabel": _derive_label(0.0),

        # Narrative fields — explicit "stub mode" text, not empty strings
        "bottomLineAnswer": (
            "Stub mode: the agent is online but is not yet producing real "
            "forecasts. This response confirms the integration works."
        ),
        "detailedExplanation": (
            "Sprint 18 wired up the Firestore round-trip end-to-end "
            "(question -> agent worker -> stub result -> frontend). Real "
            "reasoning, evidence gathering, and probability calibration "
            "are scheduled for Sprints 19-23."
        ),
        "summaryMarkdown": (
            "**Stub mode.** The agent received your question "
            f"\u201c{question}\u201d but Sprint 18 only validates the "
            "Firestore plumbing. Substantive answers begin in Sprint 19."
        ),
        "marketComparisonInsight": (
            "Market comparison is not produced in stub mode."
        ),
        "sentimentAnalysisInsight": (
            "Sentiment analysis is not produced in stub mode."
        ),
        "evidenceFeedSummary": (
            "Evidence retrieval is not produced in stub mode."
        ),

        # Market fields — null + empty triggers the Patch 4 "no canonical
        # market" empty state in the Tier 1 UI render path.
        "marketProbability": None,
        "marketComparison": [],

        # Lists — empty in stub mode (whatIDidntFind carries the caption)
        "keyFactors": [],
        "whatIDidntFind": [_STUB_WHAT_I_DIDNT_FIND],
        "reasoningChain": [],
        "suggestedActions": [],

        # Metadata
        "generatedAt": SERVER_TIMESTAMP,
        "agentVersion": AGENT_VERSION,
        "tier": _STUB_TIER,
    }


# ==========================================================
# Failure helper
# ==========================================================

def _mark_failed(
    session_id: Optional[str],
    query_doc_id: str,
    error_message: str,
) -> None:
    """
    Best-effort transition of both docs to 'failed' on processing error.

    Swallows exceptions raised by the wrapper itself — by the time we're
    here, the original processing exception has already been logged, and
    a second exception during cleanup would be noise. The worker (T6)
    decides whether to re-raise, retry, or move on.

    session_id may be None if the failure happened before claim_query
    returned — in that case only the queue doc gets the failure stamp.
    """
    if session_id is not None:
        try:
            update_session_status(
                session_id,
                "failed",
                error_code="STUB_PROCESSING_ERROR",
                error_message=error_message,
            )
        except Exception:
            logger.exception(
                "_mark_failed: failed to update sessions/%s to 'failed'",
                session_id,
            )

    try:
        update_query_status(
            query_doc_id, "failed", error_message=error_message,
        )
    except Exception:
        logger.exception(
            "_mark_failed: failed to update forecastQueries/%s to 'failed'",
            query_doc_id,
        )


# ==========================================================
# Entry point
# ==========================================================

def process_query(query_doc_id: str) -> None:
    """
    Process one claimed forecastQueries doc end-to-end (Sprint 18 stub).

    Flow:
      1. Atomically claim forecastQueries/{query_doc_id} via
         firestore_client.claim_query. If the claim is lost (another worker
         won, or the doc is gone), return — the worker treats this as a
         no-op.
      2. Mark sessions/{sessionId} 'claimed' (visible to the frontend
         onSnapshot listener).
      3. Mark sessions/{sessionId} 'running' (separate write so the user
         sees state progression even though stub work is instantaneous).
      4. Build a §8.7.2-compliant stub result and write it to
         sessionResults/{sessionId}.
      5. Mark sessions/{sessionId} 'done'.
      6. Mark forecastQueries/{query_doc_id} 'done' so the queue doc
         doesn't sit in 'claimed' forever.

    On any exception inside steps 2-6: call _mark_failed and re-raise so
    the worker (T6) sees the failure. Re-raising is intentional — the
    worker logs and moves on, but pushing the exception up keeps the
    failure visible in normal Python tracebacks rather than buried here.

    Args:
        query_doc_id: doc id under forecastQueries/. Equals sessionId per
                      the server-side write at session.repository.ts:347.
    """
    worker_id = settings.AGENT_WORKER_ID

    claimed = claim_query(query_doc_id, worker_id)
    if claimed is None:
        # Race lost or doc missing — quiet no-op. claim_query already logged.
        return

    session_id = claimed["sessionId"]
    question = claimed["question"]

    logger.info(
        "process_query: starting stub processing session_id=%s queryId=%s",
        session_id, claimed.get("queryId"),
    )

    try:
        update_session_status(session_id, "claimed")
        update_session_status(session_id, "running")

        result = _build_stub_result(question)
        write_session_result(session_id, result)

        update_session_status(session_id, "done")
        update_query_status(query_doc_id, "done")

        logger.info(
            "process_query: completed stub processing session_id=%s",
            session_id,
        )
    except Exception as exc:
        logger.exception(
            "process_query: stub processing failed session_id=%s "
            "query_doc_id=%s",
            session_id, query_doc_id,
        )
        _mark_failed(session_id, query_doc_id, str(exc))
        raise
