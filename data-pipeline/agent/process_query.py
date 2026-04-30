"""
Thin graph runner for the Agentic Hub (Sprint 19, T19.11).

Replaces the Sprint 18 procedural flow with a wrapper that just invokes
the compiled LangGraph (`agent.graph.graph`) and writes the synthesis
result + final status transitions to Firestore.

Flow:
    graph.invoke({"session_id": query_doc_id})
        → claim_session  (Firestore: claimed → running, returns question)
        → query_understand
        → build_embedding
        → vault_query
        → synthesize     (returns SessionResult dict in state)
    runner:
        → write_session_result
        → sessions/{id}: done
        → forecastQueries/{id}: done

Why this is thin:
    Every Firestore touch that the graph itself owns (claim, status →
    claimed/running) lives inside `claim_session`. The runner is left with
    only the post-graph writes — the result + the two `done` transitions —
    plus the `_mark_failed` cleanup path. T19.11 / D5: the procedural
    helpers and stub builders moved out in T19.10; the runner now exists
    purely to orchestrate the graph from the worker boundary.

Why _mark_failed stays in the runner:
    Cleanup-on-failure is a runner concern — it wraps both the session
    and the queue doc in `failed` state, which neither the graph nor the
    nodes know about. Keeping it here keeps node code free of orchestration
    plumbing.

Race-loss handling:
    `claim_session` raises `SessionClaimRaceLostError` (typed subclass of
    `AgentProcessingError`) when another worker already won the claim. The
    runner catches that subclass first and returns quietly — no `failed`
    writes, no traceback. Any other `AgentProcessingError` (or any other
    exception) goes through `_mark_failed` and is re-raised so the worker
    log shows the cause.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.3.2 (Graph Topology)
    - data-pipeline/docs/agentic_hub_spec.md §8.7.2 (SessionResult schema)
    - data-pipeline/docs/agentic_hub_spec.md §8.8 (worker pattern)
"""

from __future__ import annotations

import logging
from typing import Optional

from agent.errors import AgentProcessingError, SessionClaimRaceLostError
from agent.firestore_client import (
    update_query_status,
    update_session_status,
    write_session_result,
)
from agent.graph import graph

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

    Each cleanup write is wrapped in its own try/except so a failure on
    the session-side update does not skip the queue-side update. The
    original processing exception has already been logged by the runner;
    cleanup failures are logged here and swallowed so the worker sees the
    real cause when the runner re-raises.

    `session_id` is `None` when the failure happened before claim_session
    populated it (e.g., the `session_id` validation inside claim_session
    itself fails). In that case only the queue doc gets the failure stamp.
    """
    if session_id is not None:
        try:
            update_session_status(
                session_id,
                "failed",
                error_code="AGENT_PROCESSING_ERROR",
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
    Process one claimed forecastQueries doc end-to-end via the graph.

    Args:
        query_doc_id: doc id under forecastQueries/. Equals sessionId per
                      the server-side write at session.repository.ts:347.
    """
    logger.info(
        "process_query: starting graph processing query_doc_id=%s",
        query_doc_id,
    )

    try:
        final_state = graph.invoke({"session_id": query_doc_id})
    except SessionClaimRaceLostError:
        # Another worker won the claim, or the doc is gone. Quiet no-op —
        # the sibling worker (or the absent doc) is responsible for any
        # state transition. claim_session already logged the details.
        logger.info(
            "process_query: claim race lost or doc missing query_doc_id=%s",
            query_doc_id,
        )
        return
    except Exception as exc:
        # session_id == query_doc_id by server contract, so we can pass
        # query_doc_id straight through as the session id. We don't have a
        # separate session_id from the graph state because the failure may
        # have happened before claim_session returned.
        logger.exception(
            "process_query: graph processing failed query_doc_id=%s",
            query_doc_id,
        )
        _mark_failed(query_doc_id, query_doc_id, str(exc))
        raise

    session_id = final_state["session_id"]
    synthesis_result = final_state["synthesis_result"]

    try:
        write_session_result(session_id, synthesis_result)
        update_session_status(session_id, "done")
        update_query_status(query_doc_id, "done")
    except Exception as exc:
        logger.exception(
            "process_query: post-graph write failed session_id=%s "
            "query_doc_id=%s",
            session_id, query_doc_id,
        )
        _mark_failed(session_id, query_doc_id, str(exc))
        raise

    logger.info(
        "process_query: completed graph processing session_id=%s",
        session_id,
    )
