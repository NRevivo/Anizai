"""
agent/followup/nodes/write_message.py — Node 3 (terminal) of the follow-up
subgraph (Sprint 24, T24.5).

Persists the assistant reply and closes out the follow-up idempotently. It
delegates to `firestore_client.claim_and_write_followup_answer`, which does
the atomic work in one transaction:

    if messages/{trigger_message_id}.status == 'sent':
        flip it -> 'answered'         (removes it from the listener filter set)
        write the assistant reply     (role='assistant', replyToMessageId=...)

Why the atomic claim lives in firestore_client, not here (hub-principles G1
+ P2): the transaction is Firestore access, which is centralized. This node
owns the semantics — building the assistant payload and reacting to the
win/lose outcome — not the transaction mechanics.

Why identify the answered message ONLY by `trigger_message_id` (24.14):
    Two rapid `sent` messages must be answered independently. Using a
    positional pick (e.g. message_history[-1]) would answer the newest for
    both runs and drop one. The triggering message is threaded explicitly as
    `trigger_message_id` from the listener/sweep through state to here, and
    it is both the claim target and the `replyToMessageId` linkage.

Why the race-loser path is a no-op success (not an error):
    The listener and the sweep can both process the same message. Whichever
    commits the transaction first wins; the loser sees `status != 'sent'`,
    writes nothing, and returns cleanly. No double-answer, no error — this is
    the exactly-once guarantee working as designed.

No `agentEvents` emission (Sprint 25 / T25.7).

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.8.1 / §8.8.3 (revised)
    - .claude/skills/frontend-integration/SKILL.md (assistant message schema;
      idempotency; status transitions)
    - data-pipeline/docs/B_hub/plans/sprint24_followups.md §3 + §4 T24.5/T24.14
"""

from __future__ import annotations

import logging

from agent import firestore_client
from agent.config import settings
from agent.errors import AgentProcessingError

logger = logging.getLogger(__name__)


# ==========================================================
# Public node function
# ==========================================================
def run(state: dict) -> dict:
    """
    Write the assistant reply and atomically claim the user message.

    Args:
        state: FollowupState. Must contain `parent_session_id`,
               `trigger_message_id`, and `response_text` (set by
               answer_from_context).

    Returns:
        Partial FollowupState echoing `response_text` (LangGraph requires a
        node to write at least one state field; this node's real product is
        the Firestore write). Whether this processor won the atomic claim is
        logged and surfaced by `claim_and_write_followup_answer`'s return —
        it is not added to state (the 8-field FollowupState is fixed).

    Raises:
        AgentProcessingError: `parent_session_id`, `trigger_message_id`, or
            `response_text` missing — an upstream-node/listener bug.
    """
    session_id = state.get("parent_session_id")
    user_message_id = state.get("trigger_message_id")
    response_text = state.get("response_text")
    suggested_actions = state.get("suggested_actions") or []

    if not session_id:
        raise AgentProcessingError(
            "write_message: missing parent_session_id in state"
        )
    if not user_message_id:
        raise AgentProcessingError(
            "write_message: missing trigger_message_id in state — the "
            "listener/sweep must seed the answered message's id (24.14)"
        )
    if not response_text:
        raise AgentProcessingError(
            "write_message: missing response_text in state — "
            "answer_from_context should have populated it"
        )

    assistant_message = {
        "role": "assistant",
        "content": response_text,
        # Explicit answer -> question linkage (24.5 / 24.14). No time-ordering
        # inference anywhere.
        "replyToMessageId": user_message_id,
        "suggestedActions": suggested_actions,
        "agentVersion": settings.AGENT_VERSION,
    }

    answered = firestore_client.claim_and_write_followup_answer(
        session_id, user_message_id, assistant_message
    )

    if answered:
        logger.info(
            "write_message: wrote assistant reply for message_id=%s "
            "session_id=%s", user_message_id, session_id,
        )
    else:
        logger.info(
            "write_message: no-op — message_id=%s session_id=%s already "
            "answered by the other processor (race loser)",
            user_message_id, session_id,
        )

    return {"response_text": response_text}
