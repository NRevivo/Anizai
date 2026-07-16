"""
agent/nodes/claim_session.py — Node 0 of the forecast graph (T19.9).

Atomically claims the forecastQueries doc for this worker, transitions the
user-visible session status `claimed → running`, and pulls the question /
user_id from the claimed payload into state for downstream nodes.

Why a graph node (and not procedural in the runner):
    The spec §8.3.2 topology lists `claim_session` as the first graph node
    rather than a pre-graph step. Keeping it inside the graph means the
    full lifecycle (claim → reasoning → write_to_firestore in Sprint 20+)
    is introspectable as a single LangGraph object — useful for tracing
    and for the eventual reactive-search loop that may want to revisit
    the claim state. T19.11 will replace `process_query.py` with a thin
    runner that just calls `graph.invoke({"session_id": query_doc_id})`.

Why `query_doc_id == session_id`:
    The server-side write at `session.repository.ts:347` uses the same
    UUID for both the queue doc id and the session id (verified in
    `firestore_client.py:21-29`). The graph input only needs `session_id`;
    `claim_query` accepts the same value as `query_doc_id`.

Race-loss semantics:
    `claim_query` returns None when another worker already claimed the doc
    or the doc no longer exists. The Sprint 18 procedural runner treated
    this as a silent no-op. T19.11's thin graph runner catches the typed
    `SessionClaimRaceLostError` and reproduces that quiet-skip behaviour
    at the runner boundary — distinct from real processing failures, which
    raise plain `AgentProcessingError` and are escalated to `failed` writes.

Service isolation (CLAUDE.md §3.3):
    Talks only to `firestore_client`. No vault, no OpenAI.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.3.2 (Node 0 in topology)
    - data-pipeline/docs/agentic_hub_spec.md §8.7.1 (Firestore document model)
    - data-pipeline/docs/agentic_hub_spec.md §8.8.1 (worker pattern, atomic claim)
"""

from __future__ import annotations

import logging
import uuid

from agent.config import settings
from agent.errors import AgentProcessingError, SessionClaimRaceLostError
from agent import events, firestore_client

logger = logging.getLogger(__name__)


# ==========================================================
# Public node function
# ==========================================================
def run(state: dict) -> dict:
    """
    Execute Node 0 of the forecast graph.

    Args:
        state: Running ForecastState. Must contain:
                - `session_id: str` — the forecastQueries / sessions doc id
                  (identical per server contract).

    Returns:
        Partial state dict containing `raw_question`, `user_id`,
        `session_id` (the resolved actual session id), and `run_id` — the
        single-writer run identifier minted here (Sprint 25 T25.6).

    Sprint 25 T25.6 — this node is also the emitter BOOTSTRAP: after the claim
    resolves the real session id it mints `run_id`, writes `currentRunId` onto
    the 'running' transition, calls `events.init_run`, and emits one one-shot
    'done' bootstrap event. It cannot emit a start/complete pair for its own
    claim work — the run context does not exist until `init_run` — so it is the
    one main-graph node that emits a single event rather than a pair (§3-E,
    Flag #1).

    Raises:
        AgentProcessingError: when session_id is missing or a Firestore
            status update fails.
        SessionClaimRaceLostError: when `claim_query` returns None
            (another worker already claimed the doc, or the doc is gone).
            The runner catches this as a quiet no-op rather than a failure.
    """
    session_id = state.get("session_id") or ""
    if not session_id.strip():
        raise AgentProcessingError("claim_session: missing session_id in state")

    worker_id = settings.AGENT_WORKER_ID

    try:
        claimed = firestore_client.claim_query(session_id, worker_id)
    except AgentProcessingError:
        raise
    except Exception as exc:
        raise AgentProcessingError(
            f"claim_session: claim_query raised — {exc!r}"
        ) from exc

    if claimed is None:
        raise SessionClaimRaceLostError(
            f"claim_session: session not claimable (race lost or doc missing) "
            f"session_id={session_id}"
        )

    # G1 fix (Sprint 21): For resume-on-clarify, Express creates a NEW
    # forecastQueries doc with a fresh UUID as its doc id (not the session id).
    # The actual sessions/{id} document is identified by the claimed doc's
    # "sessionId" field. For first-time queries, sessionId == session_id.
    # server/src/repositories/session.repository.ts:428 (requeueClarifiedSession).
    actual_session_id: str = claimed.get("sessionId") or session_id

    # Sprint 25 T25.6 — mint run_id BEFORE the 'running' transition so it rides
    # onto that same write as currentRunId (no extra session-doc write). run_id
    # is the single-writer ForecastState field (plan §3 findings #2) and
    # namespaces this run's agentEvents.
    run_id = uuid.uuid4().hex

    try:
        firestore_client.update_session_status(actual_session_id, "claimed")
        # currentRunId lands on the 'running' transition — it MUST precede the
        # first emitted event so the panel's `runId == currentRunId` filter
        # never drops early events (ordering contract, plan §3).
        firestore_client.update_session_status(
            actual_session_id, "running", current_run_id=run_id,
        )
    except AgentProcessingError:
        raise
    except Exception as exc:
        raise AgentProcessingError(
            f"claim_session: status update failed — {exc!r}"
        ) from exc

    # Initialize the emitter's run context ONLY now — after the real session id
    # is resolved and currentRunId has landed (§3 ordering). No event may be
    # emitted before this point (findings #3). A failure ABOVE this line leaves
    # no run context, so process_query's fail_event safely no-ops (heads-up #2).
    events.init_run(actual_session_id, run_id)

    # Flag #1 bootstrap: a single one-shot 'done' event — the panel's first
    # line at first paint (an empty panel under status 'running' reads as
    # stuck). NOT a start/complete pair: the claim is instantaneous, so a pair
    # would write a meaningless durationMs.
    events.emit_done_event(run_id, "claim_session", "Analyzing your question…")

    logger.info(
        "claim_session: claimed and running session_id=%s actual_session_id=%s "
        "run_id=%s queryId=%s",
        session_id, actual_session_id, run_id, claimed.get("queryId"),
    )

    return {
        "session_id": actual_session_id,
        "raw_question": claimed["question"],
        "user_id": claimed["userId"],
        "run_id": run_id,
    }
