"""
agent/followup/nodes/answer_from_context.py — Node 2 of the follow-up
subgraph (Sprint 24, T24.4 + T24.9).

A single GPT-4o-mini call that answers the follow-up (`trigger_question`)
using ONLY the parent context loaded by `load_context` — the parent
SessionResult, its top-5 evidence, and the recent chat. No new retrieval,
no re-forecast, no probability revision (those are forbidden by the system
prompt and reserved for Future Enhancement 2).

Single responsibility (hub-principles G1): this node produces the reply
TEXT. It reads no Firestore and writes nothing to Firestore — the assistant
message + the atomic `sent → answered` claim are `write_message`'s job
(T24.5). No `agentEvents` emission (that is Sprint 25 / T25.7).

Born instrumented (plan §2 / 23.5.11): the GPT-4o-mini call's token usage
is routed through `agent/utils/llm_cost.record_usage(...)` and accumulated
into `FollowupState.total_cost_usd` from this first commit — a follow-up LLM
node that does not do this is incomplete.

Classification-driven, deterministic fixed copy (plan §3, hub-principles
G5): the model returns a `classification`; only the `answerable` case uses
the model's text. `insufficient_evidence` and `out_of_scope` substitute the
FIXED canonical strings from `agent/prompts/followup.py`, so that copy is
exactly what Ron approved rather than an LLM paraphrase.

Budget / degradation (T24.9): the call is bounded by a per-call timeout
equal to `AGENT_FOLLOWUP_BUDGET_MS` (the answer-node call budget), with
`max_retries=0` so a retry cannot multiply past the deadline. On timeout the
node returns a COMPLETE assistant message carrying an "I ran out of time"
caveat (never a truncated reply) — write_message still writes it and flips
the user message to `answered`, so the user gets a complete response and the
message is not left dangling. A budget overrun is degradation, not a failure.

Why other OpenAI errors RAISE (not degrade):
    A non-timeout failure (auth, malformed response) is not a
    graceful-degradation case. Raising means write_message never runs, so
    the user message stays `sent` and is re-answered on the next listener
    (re)attach — the same recovery contract the main path relies on. Only
    the budget-timeout path produces a caveat.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.8.3 (revised — follow-up path)
    - data-pipeline/docs/anizai_handoff_consolidated.md §6.1 / §7 (complete
      message + caveat on degradation; no streaming)
    - data-pipeline/docs/B_hub/plans/sprint24_followups.md §2/§3 + §4
      T24.4/T24.9
    - .claude/skills/agent-prompt-engineering/SKILL.md (followup.py notes)
    - .claude/skills/agent-design (budget enforcement pattern; G1/G5)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from agent.config import settings
from agent.errors import AgentProcessingError
from agent.utils import llm_cost
from agent.prompts.followup import (
    INSUFFICIENT_EVIDENCE_MESSAGE,
    OUT_OF_SCOPE_MESSAGE,
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
    build_user_message,
)

logger = logging.getLogger(__name__)


# ==========================================================
# Constants
# ==========================================================
# Per-call budget = the answer-node call budget (plan §3). Seconds for the
# OpenAI SDK `timeout` kwarg.
TIMEOUT_S: float = settings.AGENT_FOLLOWUP_BUDGET_MS / 1000.0

# No retries on the follow-up call: a retry would multiply the per-call
# timeout and blow the ≤7s end-to-end budget (plan §3). Budget discipline
# over transient-error resilience — a transient failure degrades/recovers.
FOLLOWUP_MAX_RETRIES: int = 0

# Completion-token cap. Follow-ups are 2-3 short paragraphs (skill cost
# table: ~500 output tokens); 800 leaves headroom without inviting an essay.
MAX_TOKENS: int = 800

# Complete (not truncated) degradation message written on budget overrun
# (T24.9 / handoff §6.1). The user gets a full, coherent reply and can retry.
TIMEOUT_CAVEAT_MESSAGE: str = (
    "I couldn't finish putting together an answer to that in time. Please "
    "ask again and I'll take another look."
)


# ==========================================================
# OpenAI client lifecycle (mirrors the Sprint 19+ node pattern)
# ==========================================================
_default_client: Optional[Any] = None


def _get_default_client() -> Any:
    """
    Lazy-construct a module-level OpenAI client tuned for the follow-up
    budget: timeout == the answer-node call budget, and max_retries=0 so the
    per-call timeout is a true wall-clock ceiling. Lazy so pytest discovery
    without OPENAI_API_KEY doesn't fail at import; tests inject `client`.
    """
    global _default_client
    if _default_client is None:
        from utils.openai_client import get_openai_client

        _default_client = get_openai_client(
            api_key=settings.OPENAI_API_KEY,
            timeout=TIMEOUT_S,
            max_retries=FOLLOWUP_MAX_RETRIES,
        )
    return _default_client


# ==========================================================
# Public node function
# ==========================================================
def run(state: dict, *, client: Optional[Any] = None) -> dict:
    """
    Produce the follow-up reply text.

    Args:
        state:  FollowupState. Reads `trigger_question` (the question to
                answer — NEVER a positional pick from message_history),
                `parent_session_result`, `parent_evidence`, `message_history`.
                Reads `total_cost_usd` (default 0.0) to accumulate onto.
        client: Optional injected OpenAI client (tests).

    Returns:
        Partial FollowupState:
            - `response_text`:  the reply body (answerable → model text;
              insufficient/out-of-scope → fixed canonical copy; budget
              overrun → complete caveat message).
            - `total_cost_usd`: prior cost + this call's cost (0.0 added on
              a budget-timeout, where there is no billable response).

    Raises:
        AgentProcessingError: `trigger_question` missing, a non-timeout
            OpenAI failure, or an unparseable response.
    """
    question = (state.get("trigger_question") or "").strip()
    if not question:
        raise AgentProcessingError(
            "answer_from_context: missing trigger_question in state — the "
            "listener/sweep must seed the triggering message's content"
        )

    client = client or _get_default_client()
    user_message = build_user_message(
        question=question,
        parent_session_result=state.get("parent_session_result") or {},
        parent_evidence=list(state.get("parent_evidence") or []),
        message_history=list(state.get("message_history") or []),
    )

    prior_cost = float(state.get("total_cost_usd") or 0.0)

    try:
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL_FOLLOWUP,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_schema", "json_schema": RESPONSE_SCHEMA},
            max_tokens=MAX_TOKENS,
            temperature=0.0,
            timeout=TIMEOUT_S,
        )
    except Exception as exc:
        if _is_timeout(exc):
            # T24.9 degradation: complete caveat message, not a failure.
            logger.warning(
                "answer_from_context: budget overrun (>%.1fs) — returning "
                "complete caveat message", TIMEOUT_S,
            )
            return {"response_text": TIMEOUT_CAVEAT_MESSAGE}
        raise AgentProcessingError(
            f"answer_from_context: OpenAI call failed — {exc!r}"
        ) from exc

    # Born instrumented: price this call and accumulate.
    #
    # Ordering (record_usage BEFORE _extract_output) is deliberate. The
    # DURABLE cost record is record_usage's structured LOG LINE, which fires
    # here — before the parse can raise. If _extract_output raises, the run
    # fails and FollowupState (including the in-state total_cost_usd) is
    # discarded, but the billed call is already logged; Sprint 26 cost
    # accounting reads that log, not the in-state accumulator. Extracting
    # first would be strictly worse — a parse failure would then skip
    # record_usage entirely, so a genuinely-billed call would go unlogged. We
    # do NOT convert a parse failure (a real bug / API-contract violation)
    # into a silent degrade just to preserve the discarded accumulator.
    tokens_used, cost_usd = llm_cost.record_usage(
        settings.OPENAI_MODEL_FOLLOWUP, response, site="answer_from_context",
    )

    output = _extract_output(response)
    response_text = _resolve_response_text(output)

    logger.info(
        "answer_from_context: classification=%s reason=%r tokens=%d cost=%.6f",
        output.get("classification"),
        output.get("classification_reason"),
        tokens_used,
        cost_usd,
    )

    return {
        "response_text": response_text,
        "total_cost_usd": prior_cost + cost_usd,
    }


# ==========================================================
# Helpers
# ==========================================================
def _resolve_response_text(output: dict) -> str:
    """
    Map the model's classification to the reply text (hub-principles G5 —
    deterministic copy for the two non-answerable classes).

    - answerable            → the model's `answer`. If it is somehow empty,
                              fall back to the transparent insufficient
                              message rather than sending a blank reply.
    - insufficient_evidence → fixed INSUFFICIENT_EVIDENCE_MESSAGE.
    - out_of_scope          → fixed OUT_OF_SCOPE_MESSAGE.
    - anything unexpected   → treat as insufficient (safe default).
    """
    classification = output.get("classification")
    if classification == "answerable":
        answer = (output.get("answer") or "").strip()
        if answer:
            return answer
        logger.warning(
            "answer_from_context: classification=answerable but empty answer "
            "— falling back to insufficient message"
        )
        return INSUFFICIENT_EVIDENCE_MESSAGE
    if classification == "out_of_scope":
        return OUT_OF_SCOPE_MESSAGE
    if classification == "insufficient_evidence":
        return INSUFFICIENT_EVIDENCE_MESSAGE

    logger.warning(
        "answer_from_context: unexpected classification=%r — defaulting to "
        "insufficient message", classification,
    )
    return INSUFFICIENT_EVIDENCE_MESSAGE


def _extract_output(response: Any) -> dict:
    """
    Parse the strict-mode JSON response into the follow-up output dict.

    `strict: true` guarantees the shape, but we still validate the envelope
    so a malformed SDK-level response raises here rather than downstream.
    """
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError) as exc:
        raise AgentProcessingError(
            f"answer_from_context: malformed OpenAI response shape — {exc!r}"
        ) from exc

    try:
        parsed = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AgentProcessingError(
            f"answer_from_context: response not JSON — {exc!r}"
        ) from exc

    if not isinstance(parsed, dict):
        raise AgentProcessingError(
            f"answer_from_context: response root is not an object — got "
            f"{type(parsed).__name__}"
        )
    return parsed


def _is_timeout(exc: Exception) -> bool:
    """
    True if `exc` is the OpenAI SDK's request-timeout error.

    Lazy import so this module (and pytest discovery) doesn't hard-depend on
    the openai package being importable at module load — matching the
    lazy-client convention. Falls back to False if openai isn't importable.
    """
    try:
        from openai import APITimeoutError
    except Exception:
        return False
    return isinstance(exc, APITimeoutError)
