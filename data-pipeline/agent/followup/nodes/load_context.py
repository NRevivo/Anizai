"""
agent/followup/nodes/load_context.py — Node 1 of the follow-up subgraph
(Sprint 24, T24.3).

Loads the PARENT forecast's context so `answer_from_context` can answer a
follow-up using ONLY what the original forecast already produced (no new
retrieval — escalation is Future Enhancement 2). It reads three things:

    1. parent_session_result — the parent forecast's SessionResult
       (sessionResults/{parent_session_id}).
    2. parent_evidence       — the top-5 evidence items the forecast used,
       by `rank` (rank 1 = most influential; unused items rank 0). This is
       exactly the `is_key_evidence` set that synthesize flags
       deterministically (see agent/nodes/synthesize.py `_rank_key_evidence`).
    3. message_history        — the last 10 thread messages, for
       conversational context (context only — the message being answered is
       `trigger_question`, never a positional slice of this list; 24.14).

Single responsibility (hub-principles G1): this node only LOADS context. It
makes no LLM call, no decision, no Firestore write. All Firestore reads go
through `firestore_client` (P2 — centralized access).

Why graceful defaults instead of raising on a missing parent result:
    The listener (T24.1) only triggers this subgraph for messages whose
    parent session is `done`, so the SessionResult should exist. But if it
    is somehow absent (race, deleted doc), degrading to an empty result +
    empty evidence lets `answer_from_context` classify the follow-up as
    insufficient_evidence and return the transparent message — strictly
    better than crashing the follow-up and leaving the user's message
    `sent` forever.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.8.3 (revised — follow-up path)
    - data-pipeline/docs/B_hub/plans/sprint24_followups.md §4 T24.3
    - .claude/skills/agent-design (single-responsibility node; state-as-contract)
"""

from __future__ import annotations

import logging

from agent import firestore_client

logger = logging.getLogger(__name__)


# ==========================================================
# Constants
# ==========================================================
# Plan §4 T24.2/T24.3: top 5 evidence items by rank, last 10 messages.
TOP_EVIDENCE_N: int = 5
MESSAGE_HISTORY_LIMIT: int = 10


# ==========================================================
# Public node function
# ==========================================================
def run(state: dict) -> dict:
    """
    Load parent context for a follow-up run.

    Args:
        state: FollowupState. Must contain `parent_session_id` (seeded by
               the listener/sweep). `trigger_message_id` / `trigger_question`
               are already on state and are not touched here.

    Returns:
        Partial FollowupState with `parent_session_result`, `parent_evidence`
        (≤5), and `message_history` (≤10). On a missing parent result, returns
        an empty result dict + empty evidence so the answer node degrades to
        the transparent insufficient-evidence path.

    Raises:
        ValueError: `parent_session_id` missing from state — a listener bug
            (the initial state must always carry it).
    """
    parent_session_id = state.get("parent_session_id")
    if not parent_session_id:
        raise ValueError(
            "load_context: missing parent_session_id in state — the "
            "listener/sweep must seed it when building the initial state"
        )

    session_result = firestore_client.get_session_result(parent_session_id)
    if session_result is None:
        logger.warning(
            "load_context: no sessionResults doc for parent_session_id=%s — "
            "degrading to empty context (answer node → insufficient_evidence)",
            parent_session_id,
        )
        session_result = {}

    all_evidence = firestore_client.get_session_evidence(parent_session_id)
    parent_evidence = _top_evidence_by_rank(all_evidence, TOP_EVIDENCE_N)

    message_history = firestore_client.get_recent_messages(
        parent_session_id, MESSAGE_HISTORY_LIMIT
    )

    logger.info(
        "load_context: parent_session_id=%s result=%s evidence=%d/%d messages=%d",
        parent_session_id,
        "present" if session_result else "missing",
        len(parent_evidence),
        len(all_evidence),
        len(message_history),
    )

    return {
        "parent_session_result": session_result,
        "parent_evidence": parent_evidence,
        "message_history": message_history,
    }


# ==========================================================
# Helpers
# ==========================================================
def _top_evidence_by_rank(evidence: list[dict], n: int) -> list[dict]:
    """
    Select the top-`n` evidence items the parent forecast actually used.

    Primary order: `rank` ascending among items with rank >= 1 (rank 1 is the
    most influential item; synthesize assigns 1..N to used items and 0 to
    unused ones — see synthesize `_rank_key_evidence`). This is exactly the
    `is_key_evidence` set, capped at `n`.

    Fallback: if no item carries a rank >= 1 (e.g. an older session written
    before ranking, or a forecast where synthesis used nothing), fall back to
    `relevance_score` descending so the follow-up still sees the most relevant
    items rather than an arbitrary slice.

    Args:
        evidence: all evidence dicts for the session (unordered).
        n:        max items to return.

    Returns:
        Up to `n` evidence dicts, most-influential first.
    """
    ranked = [item for item in evidence if int(item.get("rank") or 0) >= 1]
    if ranked:
        ranked.sort(key=lambda item: int(item.get("rank") or 0))
        return ranked[:n]

    # Fallback: nothing was ranked — order by relevance so we still surface
    # the strongest items instead of dict/stream order.
    by_relevance = sorted(
        evidence,
        key=lambda item: float(item.get("relevance_score") or 0.0),
        reverse=True,
    )
    return by_relevance[:n]
