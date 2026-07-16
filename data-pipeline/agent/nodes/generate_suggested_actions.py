"""
agent/nodes/generate_suggested_actions.py — Node 6.5 of the forecast graph
(Sprint 25, T25.1).

Runs AFTER `synthesize` (Node 6) and BEFORE `write_to_firestore` (Node 7),
with unconditional edges on both sides (no routing). A single GPT-4o-mini call
that reads the just-produced forecast and generates three contextual follow-up
suggestions; `write_to_firestore` (T25.4) injects them into the SessionResult
as `suggestedActions`.

Reads (from state):
    - `synthesis_result` (the camelCase SessionResult dict built by synthesize):
      `finalProbability`, `confidence`, `bottomLineAnswer`, `keyFactors`,
      `whatIDidntFind`.
    - `raw_question`.
Writes (to state):
    - `suggested_actions`: list of `{id, label, prompt}` (or `[]` on degrade).
    - `llm_calls_count`, `total_tokens_used`, `total_cost_usd`: incremented on
      any run where the LLM call actually completed (born instrumented).

Why graceful degradation to `[]` (NOT raise — contrast synthesize):
    Suggested actions are a non-essential UI nicety. Per hub-principles G4,
    every external call is budget-bounded AND degrades gracefully on overrun —
    here that means: on ANY failure (timeout, API error, malformed response)
    the node returns `suggested_actions=[]` and lets the forecast proceed. It
    must never fail the session — the forecast is the product; the chips are
    garnish. This is the deliberate opposite of `synthesize`, which raises
    (there is no useful partial forecast, so a synthesis failure fails the
    session). It also protects the ≤60s p95 main-forecast NFR (KG-B-5): this
    node sits on the critical path just before the write, so a hung call here
    would delay the whole forecast.

Why the budget is a TRUE ceiling (max_retries=0):
    The client is built with `timeout=AGENT_SUGGESTED_ACTIONS_BUDGET_MS` and
    `max_retries=0` (overriding the shared factory's default of 5) so the
    per-call deadline cannot be multiplied by SDK retries past the budget
    (hub-principles G4). Reliability matters far less here than a hard bound.

Why `id` is assigned here (not by the LLM):
    The stored shape is `{id, label, prompt}` (plan §3); the prompt only
    produces `{label, prompt}`. `id` is `sa-1`/`sa-2`/`sa-3`, assigned
    deterministically by position — stable and testable, no model drift
    (hub-principles G5).

Born instrumented (plan §2 / 23.5.11):
    Token usage routes through `agent/utils/llm_cost.record_usage(...,
    site="generate_suggested_actions")` and accumulates into
    `state.total_cost_usd` from this first commit — never retrofitted.

agentEvents (Sprint 25 T25.6):
    Wired MANUALLY (not via the `@events.emits` decorator) so the degrade path
    reports the truth: a timeout / API error / parse failure completes the event
    as **failed** (with `suggested_actions=[]`), while a clean run completes it
    **done**. The distinction is "failed and therefore empty" vs "succeeded and
    returned the list" — the panel is a diagnostic surface (§3: events stored
    permanently for the 26.3 latency analysis / initial-test budget
    calibration), so a degraded suggestions call must be visible, not hidden
    behind a green 'done' (Ron 2026-07-15).

Service isolation (CLAUDE.md §3.3):
    Talks to the OpenAI SDK only. No Firestore writes (those happen in
    write_to_firestore). State-mediated communication only (agent-design P2).

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.7.2 (SessionResult.suggestedActions)
    - data-pipeline/docs/B_hub/plans/sprint25_suggested_actions.md §2 (born
      instrumented) + §3 + T25.1
    - .claude/skills/hub-principles/SKILL.md (G4 budget/degradation, G5 determinism)
    - .claude/skills/agent-prompt-engineering/SKILL.md (suggested_actions notes)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from agent import events
from agent.config import settings
from agent.utils import llm_cost
from agent.prompts.suggested_actions import (
    RESPONSE_SCHEMA,
    SUGGESTED_ACTIONS_COUNT,
    SYSTEM_PROMPT,
    build_user_message,
)

logger = logging.getLogger(__name__)


# ==========================================================
# Constants
# ==========================================================

# Hard wall on the GPT-4o-mini call, from settings (G4 — budget values live
# in config, not the module). Seconds for the SDK `timeout=` kwarg.
TIMEOUT_S: float = settings.AGENT_SUGGESTED_ACTIONS_BUDGET_MS / 1000.0

# Cap on completion tokens. Output is three short {label, prompt} pairs
# (~300 tokens per the agent-prompt-engineering cost table); 500 leaves
# headroom without inviting the model to ramble.
MAX_TOKENS: int = 500


# ==========================================================
# OpenAI client lifecycle (matches the synthesize / Bundle A pattern)
# ==========================================================
_default_client: Optional[Any] = None


def _get_default_client() -> Any:
    """
    Lazy-construct a module-level OpenAI client.

    Lazy so importing this module in an environment without OPENAI_API_KEY
    (test discovery) does not fail. Tests inject their own client via the
    `client` kwarg and never hit this function.

    max_retries=0 (overriding the factory default of 5): the budget must be a
    true ceiling for this non-essential call (G4), so a slow response degrades
    to `[]` rather than retrying up to 5× and eating the forecast's latency.
    """
    global _default_client
    if _default_client is None:
        from utils.openai_client import get_openai_client

        _default_client = get_openai_client(
            api_key=settings.OPENAI_API_KEY,
            timeout=TIMEOUT_S,
            max_retries=0,
        )
    return _default_client


# ==========================================================
# Public node function
# ==========================================================
def run(state: dict, *, client: Optional[Any] = None) -> dict:
    """
    Execute Node 6.5 of the forecast graph.

    Args:
        state:  Running ForecastState. Reads `synthesis_result` (the camelCase
                SessionResult from synthesize) and `raw_question`.
        client: Optional injected OpenAI client (tests).

    Returns:
        Partial state dict for LangGraph to merge:
            - `suggested_actions`: list of `{id, label, prompt}`, or `[]` on
              any failure (graceful degradation — never raises).
            - `llm_calls_count`, `total_tokens_used`, `total_cost_usd`:
              incremented whenever the LLM call itself completed.

    Never raises — suggested actions are non-essential (G4). Every failure
    path returns a valid partial dict with `suggested_actions=[]`.
    """
    # Manual emission (§3-E / Ron 2026-07-15): start now, complete 'failed' on
    # any degrade path, 'done' only on a clean run — so a timed-out/errored
    # suggestions call stays visible in the panel + the 26.3 latency analysis
    # rather than being masked as a green 'done'.
    run_id = state.get("run_id")
    event_id = events.emit_event(
        run_id, "generate_suggested_actions", "Suggesting follow-ups…",
    )

    synthesis_result: dict = state.get("synthesis_result") or {}
    question: str = state.get("raw_question") or ""

    user_message = build_user_message(
        question=question,
        final_probability=synthesis_result.get("finalProbability"),
        confidence=synthesis_result.get("confidence"),
        bottom_line=synthesis_result.get("bottomLineAnswer"),
        key_factors=list(synthesis_result.get("keyFactors") or []),
        gaps=list(synthesis_result.get("whatIDidntFind") or []),
    )

    # --- Acquire the client + make the call. On ANY failure here — client
    #     construction (e.g. a missing key) OR the call itself (timeout /
    #     API error) — degrade to [] with NO cost. This node must NEVER fail
    #     the forecast (G4), so even `_get_default_client()` is inside the try
    #     (unlike synthesize, which is allowed to raise). ---
    try:
        client = client or _get_default_client()
        response = _call_openai(client, user_message)
    except Exception as exc:  # noqa: BLE001 — cosmetic node must never fail the forecast
        logger.warning(
            "generate_suggested_actions: LLM call failed, degrading to [] "
            "(forecast unaffected) — %r",
            exc,
        )
        events.complete_event(run_id, event_id, status="failed")
        return {"suggested_actions": []}

    # The call completed → record cost NOW (born instrumented), so even a
    # downstream parse failure still attributes the spend. record_usage is
    # contractually non-raising (unknown model → 0.0 + warning).
    tokens_used, cost_usd = llm_cost.record_usage(
        settings.OPENAI_MODEL_SUGGESTED_ACTIONS,
        response,
        site="generate_suggested_actions",
    )
    cost_delta = {
        "llm_calls_count": int(state.get("llm_calls_count") or 0) + 1,
        "total_tokens_used": int(state.get("total_tokens_used") or 0) + tokens_used,
        "total_cost_usd": float(state.get("total_cost_usd") or 0.0) + cost_usd,
    }

    # --- Parse + shape. Strict-mode JSON makes malformed output near-
    #     impossible, but a defensive degrade keeps the guarantee absolute. ---
    try:
        actions = _extract_actions(response)
        suggested = [
            {"id": f"sa-{i}", "label": a["label"], "prompt": a["prompt"]}
            for i, a in enumerate(actions, start=1)
        ]
    except Exception as exc:  # noqa: BLE001 — cosmetic node must never fail the forecast
        logger.warning(
            "generate_suggested_actions: response parse failed, degrading to "
            "[] (forecast unaffected) — %r",
            exc,
        )
        events.complete_event(run_id, event_id, status="failed")
        return {"suggested_actions": [], **cost_delta}

    logger.info(
        "generate_suggested_actions: produced %d suggestion(s) cost_usd=%.6f",
        len(suggested), cost_usd,
    )
    events.complete_event(run_id, event_id)
    return {"suggested_actions": suggested, **cost_delta}


# ==========================================================
# OpenAI plumbing
# ==========================================================
def _call_openai(client: Any, user_message: str) -> Any:
    """
    Invoke chat.completions with strict structured output for the three
    suggestions. `strict: true` + `minItems==maxItems==3` on the schema means
    the API returns exactly three conforming `{label, prompt}` items or errors
    — the caller never has to pad, truncate, or hand-repair JSON.

    temperature=0.0 for determinism (hub-principles G5): the same forecast
    yields the same suggestions, which keeps the panel debuggable.
    """
    return client.chat.completions.create(
        model=settings.OPENAI_MODEL_SUGGESTED_ACTIONS,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        response_format={"type": "json_schema", "json_schema": RESPONSE_SCHEMA},
        max_tokens=MAX_TOKENS,
        temperature=0.0,
    )


def _extract_actions(response: Any) -> list[dict]:
    """
    Parse the strict-mode JSON response into the `actions` list.

    Validates the envelope (choices → message → content → JSON object with an
    `actions` array of the expected length) so a malformed SDK-level response
    raises here and is caught by `run`'s degrade path rather than producing a
    half-formed suggestion list.
    """
    content = response.choices[0].message.content
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError(
            f"suggested-actions response root is not an object — got "
            f"{type(parsed).__name__}"
        )
    actions = parsed.get("actions")
    if not isinstance(actions, list) or len(actions) != SUGGESTED_ACTIONS_COUNT:
        raise ValueError(
            f"suggested-actions expected {SUGGESTED_ACTIONS_COUNT} actions, "
            f"got {actions!r}"
        )
    return actions
