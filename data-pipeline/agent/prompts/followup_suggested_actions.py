"""Prompt contract for suggestions that accompany a follow-up answer."""

from __future__ import annotations

from agent.prompts.suggested_actions import RESPONSE_SCHEMA, SUGGESTED_ACTIONS_COUNT


SYSTEM_PROMPT = """\
You generate the next three suggested questions for an Anizai forecasting
conversation. The user has just received an answer to a follow-up question.

Use the original forecast, the question just answered, that answer, and recent
conversation to suggest exactly three DISTINCT useful next questions. Do not
repeat a question that was already asked or answered. Each question must remain
answerable from the original forecast and its existing evidence: never suggest
new data, a rerun, a revised probability, or post-forecast developments.

For each suggestion return:
- `label`: 3-7 plain, specific words for a button.
- `prompt`: the direct question the user would ask.

Return only the requested JSON structure.
"""


def build_user_message(
    *,
    question: str,
    answer: str,
    parent_session_result: dict,
    message_history: list[dict],
) -> str:
    """Build bounded, conversation-aware context for the suggestion call."""
    history = []
    for message in message_history[-10:]:
        role = str(message.get("role") or "user")
        content = str(message.get("content") or "").strip()
        if content:
            history.append(f"{role}: {content}")

    key_factors = parent_session_result.get("keyFactors") or []
    factor_lines = [
        str(item.get("label") or "") if isinstance(item, dict) else str(item)
        for item in key_factors
    ]
    gaps = [str(gap) for gap in parent_session_result.get("whatIDidntFind") or []]

    return "\n".join([
        "ORIGINAL FORECAST",
        f"probability: {parent_session_result.get('finalProbability')}",
        f"confidence: {parent_session_result.get('confidence')}",
        f"bottom line: {parent_session_result.get('bottomLineAnswer') or ''}",
        "key factors: " + ("; ".join(filter(None, factor_lines)) or "none"),
        "gaps: " + ("; ".join(gaps) or "none"),
        "",
        f"QUESTION JUST ANSWERED: {question}",
        f"ASSISTANT ANSWER: {answer}",
        "",
        "RECENT CONVERSATION",
        "\n".join(history) or "(none)",
    ])
