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
    get_query_doc,
    get_session_doc,
    update_query_status,
    update_session_status,
)
from agent.followup.listener import sweep_done_session
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

def _build_initial_state(query_doc_id: str) -> dict:
    """
    Build the initial LangGraph state dict for a forecastQueries doc.

    Normal (first-time) runs return just `{"session_id": query_doc_id}`.

    Resume-on-clarify runs pre-populate skip_matching_step + chosen_candidate_id
    + structured_intent so query_understand skips the OpenAI call. Detection
    is based on the forecastQueries doc's `sessionId` field differing from the
    doc's own id — the server writes a fresh UUID as the resume doc id, distinct
    from the original session id (G1 fix, Sprint 21).

    Production resume contract (server/src/repositories/session.repository.ts
    requeueClarifiedSession, line 428):
        forecastQueries/{fresh-uuid}:
            sessionId = original_session_id  ← key field for resume detection
            question, userId (same as original)
            NO chosenCandidateId field
        sessions/{original-session-id}:
            status = 'queued'
            canonicalKey = chosen_candidate.id | null  ← freeform if null
            clarificationCandidates = null  ← CLEARED by Express before requeue

    Why `session_id` stays as `query_doc_id` in the returned dict:
        claim_session (Node 0) needs to claim the forecastQueries/{fresh-uuid}
        doc — it calls claim_query(state["session_id"]). If we set
        state["session_id"] = actual_session_id here, claim_session would try
        to claim the wrong (non-existent) forecastQueries doc. claim_session
        handles the translation itself (reading claimed["sessionId"] and
        overwriting state["session_id"] in its return value).

    Why `_resume_session_id` is set:
        _mark_failed in process_query() needs the actual session id for
        cleanup when the graph raises mid-execution. Since state["session_id"]
        stays as query_doc_id here, we pass the actual id as a side-channel
        hint. process_query() pops it before calling graph.invoke().
    """
    initial_state: dict = {"session_id": query_doc_id}

    query_doc = get_query_doc(query_doc_id)
    if query_doc is None:
        return initial_state

    # Resume detection: Express writes forecastQueries.sessionId = original_session_id.
    # For first-time queries, Express writes forecastQueries/{sessionId} so
    # doc_id == sessionId. On resume, doc_id (fresh UUID) != sessionId (original).
    session_id_from_doc: str = query_doc.get("sessionId") or query_doc_id
    if session_id_from_doc == query_doc_id:
        return initial_state  # first-time query — no resume

    # Resume path: read the actual session doc (NOT using query_doc_id —
    # that would look up sessions/{fresh-uuid} which doesn't exist).
    session_doc = get_session_doc(session_id_from_doc)
    # canonicalKey is set by Express to the chosen candidate's id on resume.
    # null means the user clicked "None of these — freeform analysis".
    canonical_key: Optional[str] = (session_doc or {}).get("canonicalKey")

    initial_state["skip_matching_step"] = True
    initial_state["chosen_candidate_id"] = canonical_key
    # Hint for _mark_failed — popped by process_query before graph.invoke.
    initial_state["_resume_session_id"] = session_id_from_doc

    if canonical_key is None:
        # User clicked "None of these — freeform analysis" (handoff §6.3).
        initial_state["structured_intent"] = {
            "intent": "forecast",
            "domain": "general",
            "entities": [],
            "polymarket_search_terms": None,
            "has_market_question_intent": False,
            "confidence": 0.5,
            "too_broad": False,
            "rejected": False,
        }
        logger.info(
            "process_query: resume-on-clarify freeform "
            "query_doc_id=%s actual_session_id=%s",
            query_doc_id, session_id_from_doc,
        )
    else:
        # User chose a specific candidate. Express cleared clarificationCandidates
        # before requeuing (KG-PHASE8-20), so we cannot recover polymarket_search_terms.
        # KG-PHASE8-12 means there's no vault-based market resolver anyway —
        # the pipeline runs as effective Tier 2 regardless.
        initial_state["structured_intent"] = {
            "intent": "forecast",
            "domain": "general",
            "entities": [],
            "polymarket_search_terms": None,
            "has_market_question_intent": True,
            "confidence": 0.75,
            "too_broad": False,
            "rejected": False,
        }
        logger.info(
            "process_query: resume-on-clarify candidate "
            "canonical_key=%s query_doc_id=%s actual_session_id=%s",
            canonical_key, query_doc_id, session_id_from_doc,
        )

    return initial_state


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

    initial_state = _build_initial_state(query_doc_id)
    # Pop the resume-session hint before the graph sees it — it's a
    # runner-internal signal, not a ForecastState field.
    mark_failed_session_id: str = initial_state.pop("_resume_session_id", query_doc_id)

    try:
        graph.invoke(initial_state)
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
        # For resume-on-clarify queries, mark_failed_session_id is the
        # actual sessions/{id} doc (not the fresh-UUID forecastQueries
        # doc id). For first-time queries they are equal.
        logger.exception(
            "process_query: graph processing failed query_doc_id=%s",
            query_doc_id,
        )
        _mark_failed(mark_failed_session_id, query_doc_id, str(exc))
        raise

    logger.info(
        "process_query: completed graph processing query_doc_id=%s",
        query_doc_id,
    )

    # Sprint 24 T24.15: done-transition safety-net sweep. A follow-up message
    # written in the narrow window BEFORE the session flipped to 'done' was
    # delivered to the follow-up listener while its parent-done check failed,
    # and the listener never re-delivers it. Now that the forecast has
    # completed, sweep any still-`sent` user messages for this session.
    #
    # Runner seam, not a graph node — the main graph never imports the
    # followup package (24.15 "without coupling"). Uses mark_failed_session_id
    # (the real sessions/{id} doc, == query_doc_id for first-time queries; the
    # resume-session id on clarify-resume). sweep_done_session self-guards on
    # status=='done' (so an awaiting_clarification terminus is skipped) and
    # never raises — a sweep hiccup must not fail an already-successful
    # forecast.
    sweep_done_session(mark_failed_session_id)
