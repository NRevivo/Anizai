"""
agent/nodes/write_clarification.py — Clarification branch node (Sprint 21 T21.2).

When `query_understand` detects that multiple Polymarket candidate markets
have similar confidence scores (awaiting_clarification=True), the graph's
`_route_after_query_understand` conditional edge routes here instead of
proceeding to `build_embedding`. This node:

  1. Writes the `clarificationCandidates` list to the user-facing session doc.
  2. Transitions session.status → 'awaiting_clarification'.
  3. Transitions forecastQueries.status → 'awaiting_clarification'.
  4. Returns a state echo. The graph then terminates at END.

The hub does NOT call POST /sessions/:id/clarify — that is the Express BFF's
endpoint (friend 2's server). The hub only writes the candidates and stops.
When the user picks a candidate, Express writes a new forecastQueries doc
with `chosenCandidateId`; any available worker claims it and resumes via
the skip_matching_step path (T21.4/T21.5).

agentEvents (Sprint 25 T25.6):
    This node now emits a start/complete pair like every main-graph node (via
    the `@events.emits` decorator). The clarification picker UI is still
    triggered by session.status == 'awaiting_clarification'
    (anizai_handoff_consolidated.md §6.3 step 3), not by the event — the event
    just feeds the reasoning panel. On this terminal branch the graph ends
    without write_to_firestore's pre-`done` drain, so these events are flushed
    by process_query's finally-drain (§3 failure-path / awaiting_clarification).

Service isolation (CLAUDE.md §3.3):
    Talks to Firestore only via firestore_client (CLAUDE.md §3.2 —
    centralized Firebase access). No LLM calls, no vault reads.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.2.3 (ambiguous detection)
    - data-pipeline/docs/agentic_hub_spec.md §8.2.4 (ClarificationCandidate)
    - data-pipeline/docs/agentic_hub_spec.md §8.3.3 (conditional edge logic)
    - data-pipeline/docs/anizai_handoff_consolidated.md §6.3 (clarification flow)
    - .claude/skills/frontend-integration/SKILL.md (status transitions are
      the public API; every status change is one atomic Firestore write)
"""

from __future__ import annotations

import logging

from agent import events, firestore_client
from agent.errors import AgentProcessingError

logger = logging.getLogger(__name__)


@events.emits("write_clarification", "Preparing clarification options…")
def run(state: dict) -> dict:
    """
    Execute the clarification branch node.

    Reads:
        state["session_id"]                — sessions/ + forecastQueries/ doc id
        state["clarification_candidates"]  — list of ClarificationCandidate dicts
                                             (shaped by T21.1's
                                             _build_clarification_candidates())

    Writes to state:
        errors  — identity echo (LangGraph requires at least one field returned)

    Firestore side effects (in order):
        1. sessions/{session_id}.status = 'awaiting_clarification'
           + clarificationCandidates = [...]   (single atomic update)
        2. forecastQueries/{session_id}.status = 'awaiting_clarification'

    Returns:
        Partial state dict (state echo only — this node is a Firestore sink).

    Raises:
        AgentProcessingError: when session_id or clarification_candidates are
            missing (upstream-node bug). Firestore SDK exceptions propagate
            unwrapped — process_query._mark_failed catches them and writes
            session=failed/query=failed.
    """
    session_id = state.get("session_id")
    if not session_id:
        raise AgentProcessingError(
            "write_clarification: missing session_id in state — "
            "claim_session should have populated it"
        )

    candidates: list[dict] = list(state.get("clarification_candidates") or [])
    if not candidates:
        raise AgentProcessingError(
            "write_clarification: missing clarification_candidates in state — "
            "query_understand should have populated them on the ambiguous path"
        )

    # Write candidates + status in one atomic update per the frontend-integration
    # skill: "Status changes are first-class events. Every status change must be
    # a single Firestore write. Include any context the frontend needs."
    # update_session_status already accepts clarification_candidates and
    # writes it as clarificationCandidates (Sprint 20 forward-flag in
    # firestore_client.py lines 225-266 already implemented this field).
    firestore_client.update_session_status(
        session_id,
        "awaiting_clarification",
        clarification_candidates=candidates,
    )

    # Mirror queue doc status so the worker listener doesn't re-attempt this
    # doc and so the stale-claim recovery timer starts fresh on the resume doc.
    firestore_client.update_query_status(session_id, "awaiting_clarification")

    logger.info(
        "write_clarification: session_id=%s candidates=%d "
        "status=awaiting_clarification — waiting for user pick",
        session_id, len(candidates),
    )

    # LangGraph requires every node to write at least one state field.
    # This node is a Firestore sink; echo errors for the identity write
    # (same pattern as write_to_firestore, Sprint 20 T20.5).
    return {"errors": list(state.get("errors") or [])}
