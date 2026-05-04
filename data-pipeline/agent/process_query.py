"""
Thin graph runner for the Agentic Hub (Sprint 20 T20.7).

Sprint 18/19 history: this module used to orchestrate the post-graph
Firestore writes itself (write_session_result → session 'done' → query
'done'). Sprint 20 T20.5 + T20.7 moved those writes into the graph
proper as Node 7 (`write_to_firestore`). The runner is now purely a
lifecycle wrapper around `graph.invoke()` — claim race handling and
cleanup-on-failure, nothing else.

Flow:
    graph.invoke({"session_id": query_doc_id})
        → claim_session       (Firestore: claimed → running)
        → query_understand
        → build_embedding
        → vault_query
        → rate_evidence       (Sprint 20 T20.1)
        → synthesize          (Sprint 20 T20.3, GPT-4o)
        → write_to_firestore  (Sprint 20 T20.5: subcollections →
                               sessionResults → status='done')
    runner:
        (no further writes — happy path is fully done by the graph)

Why the success-path writes moved out of the runner (T20.7 / D9):
    The Sprint 19 runner held three Firestore calls (write result,
    session 'done', query 'done') after `graph.invoke()` returned.
    Splitting graph + runner across the persistence boundary made
    introspection awkward (the lifecycle was half in the graph, half
    in the runner). Moving the writes into Node 7 collapses the full
    forecast into one introspectable LangGraph object and frees the
    runner to be a pure exception-handler.

Why _mark_failed stays in the runner:
    Cleanup-on-failure is fundamentally a runner concern — it wraps
    both the session and the queue doc in `failed` state, which
    neither the graph nor any individual node knows about. The graph
    can't `_mark_failed` itself because the failure scenarios include
    "Node 7 itself raised mid-batch": you cannot recover-by-running-
    Node-7 if Node 7 was the failure. Keeping cleanup outside the
    graph makes it the always-safe fallback.

Race-loss handling:
    `claim_session` raises `SessionClaimRaceLostError` (typed subclass
    of `AgentProcessingError`) when another worker already won the
    claim. The runner catches that subclass first and returns quietly
    — no `failed` writes, no traceback. Any other `AgentProcessingError`
    (or any other exception) goes through `_mark_failed` and is
    re-raised so the worker log shows the cause.

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
    cleanup failures are logged here and swallowed so the worker sees
    the real cause when the runner re-raises.

    `session_id` is `None` when the failure happened before claim_session
    populated it (e.g., the `session_id` validation inside claim_session
    itself fails). In that case only the queue doc gets the failure
    stamp.

    Sprint 20 covers a new failure mode: write_to_firestore (Node 7)
    raising mid-batch. The session may have a partial sessionResults
    doc and partial subcollections. _mark_failed flips status='failed'
    on both docs; the frontend's "render-on-done" contract means the
    half-written sessionResults isn't rendered. Acceptable cleanup
    state for V1; Sprint 22+ may add transactional rollback.
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

    The graph runs the full pipeline including Node 7
    (write_to_firestore), so on the happy path no Firestore writes
    happen here in the runner — graph.invoke returns and the runner
    just exits.

    Args:
        query_doc_id: doc id under forecastQueries/. Equals sessionId
                      per the server-side write at
                      session.repository.ts:347.
    """
    logger.info(
        "process_query: starting graph processing query_doc_id=%s",
        query_doc_id,
    )

    try:
        graph.invoke({"session_id": query_doc_id})
    except SessionClaimRaceLostError:
        # Another worker won the claim, or the doc is gone. Quiet
        # no-op — the sibling worker (or the absent doc) is responsible
        # for any state transition. claim_session already logged the
        # details.
        logger.info(
            "process_query: claim race lost or doc missing query_doc_id=%s",
            query_doc_id,
        )
        return
    except Exception as exc:
        # session_id == query_doc_id by server contract, so we can pass
        # query_doc_id straight through as the session id. We don't
        # have a separate session_id from the graph state because the
        # failure may have happened before claim_session returned.
        logger.exception(
            "process_query: graph processing failed query_doc_id=%s",
            query_doc_id,
        )
        _mark_failed(query_doc_id, query_doc_id, str(exc))
        raise

    logger.info(
        "process_query: completed graph processing query_doc_id=%s",
        query_doc_id,
    )
