"""
Stub processing flow for the Agentic Hub (Sprint 18, transitional Sprint 19).

This module implements the end-to-end processing entry point that the worker
(T6) calls after picking up a forecastQueries doc from Firestore. Sprint 18
delivered a fully functional procedural flow:

    claim_query  -> sessions/{id}: claimed
                 -> sessions/{id}: running
                 -> sessionResults/{id}: stub payload
                 -> sessions/{id}: done
                 -> forecastQueries/{id}: done

If anything raises mid-flight, _mark_failed updates both docs to 'failed'.

T19.10 transitional state:
    The §8.7.2 stub-result builder and its helpers (`_build_stub_result`,
    `_derive_label`, `_derive_consensus`, `_STUB_*`, `AGENT_VERSION`) have
    moved to `agent/nodes/synthesize.py` — that's their proper home now
    that the synthesize node exists. This module imports them back so the
    worker keeps functioning during the transition. T19.11 will replace
    this entire module with a thin graph runner that calls
    `graph.invoke()` and the helpers go away from here.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.7.2 (SessionResult schema)
    - data-pipeline/docs/agentic_hub_spec.md §8.8 (worker pattern)
    - data-pipeline/docs/anizai_handoff_consolidated.md §5.1 (frontend contract)
"""

import logging
from typing import Optional

from agent.config import settings
from agent.firestore_client import (
    claim_query,
    update_query_status,
    update_session_status,
    write_session_result,
)
from agent.nodes.synthesize import _build_stub_result

logger = logging.getLogger(__name__)


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
