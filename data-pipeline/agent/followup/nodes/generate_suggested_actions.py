"""Generate non-essential next-question suggestions for a follow-up reply."""

from __future__ import annotations

import logging
from typing import Any, Optional

from agent.config import settings
from agent.nodes import generate_suggested_actions as shared_actions
from agent.prompts.followup_suggested_actions import SYSTEM_PROMPT, build_user_message
from agent.utils import llm_cost

logger = logging.getLogger(__name__)


def run(state: dict, *, client: Optional[Any] = None) -> dict:
    """Return three suggestions, or an empty list without blocking the reply."""
    question = (state.get("trigger_question") or "").strip()
    answer = (state.get("response_text") or "").strip()
    if not question or not answer:
        logger.warning("followup suggestions: missing question or answer; degrading to []")
        return {"suggested_actions": []}

    user_message = build_user_message(
        question=question,
        answer=answer,
        parent_session_result=state.get("parent_session_result") or {},
        message_history=list(state.get("message_history") or []),
    )

    try:
        client = client or shared_actions._get_default_client()
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL_SUGGESTED_ACTIONS,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_schema", "json_schema": shared_actions.RESPONSE_SCHEMA},
            max_tokens=shared_actions.MAX_TOKENS,
            temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001 - suggestions must not block an answer
        logger.warning("followup suggestions: LLM call failed; degrading to [] - %r", exc)
        return {"suggested_actions": []}

    tokens_used, cost_usd = llm_cost.record_usage(
        settings.OPENAI_MODEL_SUGGESTED_ACTIONS,
        response,
        site="followup_generate_suggested_actions",
    )
    cost_delta = {"total_cost_usd": float(state.get("total_cost_usd") or 0.0) + cost_usd}

    try:
        actions = shared_actions._extract_actions(response)
    except Exception as exc:  # noqa: BLE001 - suggestions must not block an answer
        logger.warning("followup suggestions: invalid response; degrading to [] - %r", exc)
        return {"suggested_actions": [], **cost_delta}

    suggested_actions = [
        {"id": f"fu-sa-{index}", "label": action["label"], "prompt": action["prompt"]}
        for index, action in enumerate(actions, start=1)
    ]
    logger.info(
        "followup suggestions: produced %d suggestion(s), tokens=%d cost=%.6f",
        len(suggested_actions), tokens_used, cost_usd,
    )
    return {"suggested_actions": suggested_actions, **cost_delta}
