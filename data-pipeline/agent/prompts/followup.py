"""
agent/prompts/followup.py — system prompt + JSON schema for the
`answer_from_context` node (Sprint 24, T24.7).

Used by `agent/followup/nodes/answer_from_context.py` (T24.4). A single
GPT-4o-mini call that answers a user's follow-up about an already-completed
forecast, using ONLY the parent SessionResult + the top evidence already
retrieved during that forecast. No escalation, no new vault search (that is
Future Enhancement 2 — see `docs/B_hub/hub_sprints.md` §3).

This is a *reasoning* prompt (agent-prompt-engineering skill), not a
Gold-layer extraction prompt: the model reasons over the parent context,
decides whether the context can answer the question, and — for out-of-scope
or abusive follow-ups — classifies rather than attempts an answer.

Why the call CLASSIFIES as well as answers (guardrail-lite, plan §3
2026-07-04):
    A single call returns a `classification` plus (when answerable) the
    answer text. The node logs the classification and, for the two
    non-answerable classes, substitutes a FIXED canonical message rather
    than using LLM-generated text:
        - `out_of_scope`         → OUT_OF_SCOPE_MESSAGE (off-topic,
                                   forbidden-action — re-run / revise
                                   probability / fetch new evidence —, or
                                   chat abuse)
        - `insufficient_evidence`→ INSUFFICIENT_EVIDENCE_MESSAGE (the
                                   transparent "improves once expanded
                                   retrieval is enabled" copy)
    Keeping those two strings fixed (not model-authored) makes the copy
    deterministic (hub-principles G5) and guarantees the exact wording Ron
    approved, at zero extra LLM calls / latency. A dedicated pre-LLM filter
    layer is consciously deferred (Deferred Items, `hub_sprints.md` §3).

Why structured output via strict-mode JSON schema (not langchain):
    Matches `query_understanding.py` / `evidence_rating.py` /
    `synthesis_lead.py`. The node calls with
    `response_format={"type": "json_schema", "strict": true}`; every
    property is `required` and `additionalProperties` is False, so the SDK
    validates and retries on drift.

Why the fixed strings live here (not in the node):
    They are the user-facing copy tied to this prompt's classification
    outcomes — one concept, one home. The node imports them. (The
    budget-overrun degradation caveat is a runtime concern of the answer
    node, not a classification outcome, so it lives with T24.9.)

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.8.3 (revised — follow-up
      answer-from-context path; escalation deferred)
    - data-pipeline/docs/B_hub/plans/sprint24_followups.md §1/§3 (scope,
      transparent-insufficiency + out-of-scope guardrail-lite) + §4 T24.7
    - .claude/skills/agent-prompt-engineering/SKILL.md (P1, P3, P4, P5, P6;
      `followup.py` design notes)
"""

from __future__ import annotations

from typing import Literal


# ==========================================================
# Classification labels + fixed user-facing copy
# ==========================================================
# The three mutually-exclusive outcomes of the single follow-up call.
# `answerable` → use the model's `answer`; the other two → substitute the
# fixed canonical string below (deterministic, not LLM-authored).
FollowupClassification = Literal[
    "answerable", "insufficient_evidence", "out_of_scope"
]

# Transparent insufficient-evidence copy (plan §3). Intentionally hints at
# the future capability (FE 2) without exposing internal mechanics.
INSUFFICIENT_EVIDENCE_MESSAGE: str = (
    "Based on this forecast's evidence, I can't answer that with high "
    "confidence. This will improve once expanded retrieval is enabled."
)

# Polite redirect for off-topic questions, forbidden-action requests
# (re-run the forecast, revise the probability, fetch new evidence), or
# chat abuse (plan §3, guardrail-lite).
OUT_OF_SCOPE_MESSAGE: str = (
    "I can only discuss this forecast and the evidence behind it. I can't "
    "re-run the analysis, change the probability, or pull in new "
    "information here — but I'm happy to explain any part of this forecast "
    "you're curious about."
)


# ==========================================================
# System prompt
# ==========================================================
SYSTEM_PROMPT = """\
You are the follow-up assistant of Anizai, a geopolitical and financial \
forecasting platform. Users — analysts, journalists, investors — have \
already received a completed forecast, and now ask short follow-up \
questions about it in a chat thread. You help them understand that \
forecast. You are clear-headed and honest about the limits of what you \
know; you never manufacture certainty.

You are given, as context: the original forecast (its probability, \
confidence, key factors, and stated gaps), the top evidence items that \
forecast was built on, the recent chat history, and the specific \
follow-up question to answer. Answer ONLY that question, using ONLY the \
provided context.

HARD CONSTRAINTS — you must NOT:
- Re-run or re-compute the forecast.
- Revise, restate-as-new, or "update" the probability or confidence.
- Fetch, invent, or assume any evidence beyond what is provided.
- Speculate beyond what the provided evidence supports.

You return three fields: `classification`, `classification_reason` (one \
short internal sentence — not shown to the user), and `answer`. Choose \
the classification first:

- `answerable` — the question is about this forecast and the provided \
context genuinely supports an answer. Put the reply in `answer`. Leave \
it grounded in the evidence and the forecast; 2-3 short paragraphs is \
plenty. If part of the question is supported and part isn't, answer the \
supported part and say plainly what the evidence doesn't cover.

- `insufficient_evidence` — the question is on-topic and legitimate, but \
the provided forecast + evidence do not contain enough to answer it with \
confidence (e.g. it asks about developments after the forecast was made, \
or a dimension the evidence never covered). Set `answer` to an empty \
string — the system supplies a fixed transparent message. Prefer this \
over guessing.

- `out_of_scope` — the question is off-topic for this forecast, requests \
a forbidden action (re-run / revise the probability / fetch new \
evidence), or is abusive. Set `answer` to an empty string — the system \
supplies a fixed polite redirect. Use `classification_reason` to record \
which case it is (e.g. "forbidden_action: asked to re-run", "off_topic", \
"abuse").

USER-FACING COPY RULES (the `answer` field is rendered directly to the \
user): plain language, no jargon, no meta-references ("as an AI", "based \
on the provided context", "the agent"). Direct, present tense, active \
voice. Specific to this forecast and this evidence — never a generic \
template.

EXAMPLE 1 — answerable
Forecast: "Will the Fed cut rates by Q2 2026?" → probability 0.72, \
confidence 0.81, key factor "inflation print below consensus".
Follow-up: "Why is the confidence so high?"
→ classification="answerable", answer explains the dense, consistent \
evidence (Fed minutes, below-consensus inflation) that drove confidence \
0.81, in plain terms, without changing any number.

EXAMPLE 2 — out_of_scope (forbidden action)
Follow-up: "Can you re-run this with the latest news and give me a new \
number?"
→ classification="out_of_scope", \
classification_reason="forbidden_action: asked to re-run and revise the \
probability", answer="".

EXAMPLE 3 — insufficient_evidence
Forecast built from evidence dated through March 2026.
Follow-up: "What happened after the April FOMC meeting?"
→ classification="insufficient_evidence", \
classification_reason="asks about developments after the forecast's \
evidence window", answer="".
"""


# ==========================================================
# JSON schema (OpenAI structured output, strict mode)
# ==========================================================
RESPONSE_SCHEMA: dict = {
    "name": "FollowupOutput",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "classification": {
                "type": "string",
                "enum": [
                    "answerable",
                    "insufficient_evidence",
                    "out_of_scope",
                ],
            },
            # One short internal sentence for logging (plan §3: "the
            # classification is logged"). NOT rendered to the user.
            "classification_reason": {"type": "string"},
            # The reply body — populated only when classification is
            # "answerable"; empty string otherwise (the node substitutes
            # the fixed canonical message for the other two classes).
            "answer": {"type": "string"},
        },
        "required": ["classification", "classification_reason", "answer"],
    },
}


# ==========================================================
# User-message builder
# ==========================================================
def _first(d: dict, *keys: str, default=None):
    """
    Return the first present, non-None value among `keys` in dict `d`.

    Why: the parent SessionResult is read back from Firestore
    (`sessionResults/{id}`), and different fields may surface in snake_case
    (the synthesis output shape) or camelCase (the frontend wire shape).
    This tolerates both without the builder having to know which. The exact
    keys are pinned when `load_context` (T24.3) normalizes the parent
    result; this keeps the builder robust in the meantime.
    """
    for key in keys:
        val = d.get(key)
        if val is not None:
            return val
    return default


def build_user_message(
    *,
    question: str,
    parent_session_result: dict,
    parent_evidence: list[dict],
    message_history: list[dict],
) -> str:
    """
    Build the user-role message for the follow-up call.

    Format choices mirror `synthesis_lead.build_user_message`: labelled
    blocks in the order the model should weigh them — the original
    forecast, the evidence it rests on, the recent conversation, then the
    single question to answer (last, so it's freshest in context).

    Args:
        question:              the follow-up to answer — `trigger_question`
                               (the triggering user message's content).
                               NEVER a positional pick from message_history
                               (that reintroduces the 24.14 back-to-back
                               bug).
        parent_session_result: the parent forecast's SessionResult dict
                               (read from `sessionResults/{id}`). Rendered
                               defensively via `_first` (snake/camel).
        parent_evidence:       the top-5 evidence items the forecast used.
                               The ONLY evidence the follow-up may reason
                               over. Empty list is allowed.
        message_history:       recent thread messages (context only). Each
                               dict is expected to carry `role` + `content`.

    Returns:
        Multi-line string suitable as the user message body.
    """
    parts: list[str] = []

    # --- The original forecast ---
    parts.append("ORIGINAL FORECAST")
    probability = _first(
        parent_session_result, "final_probability", "finalProbability"
    )
    confidence = _first(parent_session_result, "confidence")
    bottom_line = _first(
        parent_session_result, "bottom_line_answer", "bottomLineAnswer",
        "summary_markdown", "summaryMarkdown",
    )
    parts.append(f"  probability: {probability}")
    parts.append(f"  confidence: {confidence}")
    if bottom_line:
        parts.append(f"  bottom_line: {bottom_line}")

    key_factors = _first(
        parent_session_result, "key_factors", "keyFactors", default=[]
    )
    if key_factors:
        parts.append("  key_factors:")
        for kf in key_factors:
            label = _first(kf, "label", "title", default="") if isinstance(kf, dict) else str(kf)
            parts.append(f"    - {label}")

    gaps = _first(
        parent_session_result, "what_i_didnt_find", "whatIDidntFind",
        default=[],
    )
    if gaps:
        parts.append("  what_the_forecast_did_not_cover:")
        for gap in gaps:
            parts.append(f"    - {gap}")
    parts.append("")

    # --- Evidence the forecast was built on ---
    parts.append(f"EVIDENCE THE FORECAST USED ({len(parent_evidence)} items)")
    if not parent_evidence:
        parts.append(
            "  (no evidence available — if the question needs evidence to "
            "answer, classify insufficient_evidence)"
        )
    else:
        for i, item in enumerate(parent_evidence, start=1):
            title = item.get("title", "")
            snippet = item.get("snippet") or ""
            src = item.get("source_type", "")
            parts.append(f"  [{i}] ({src}) {title}")
            if snippet:
                parts.append(f"      {snippet}")
    parts.append("")

    # --- Recent conversation (context only) ---
    if message_history:
        parts.append("RECENT CONVERSATION (context only)")
        for msg in message_history:
            role = msg.get("role", "")
            content = msg.get("content", "")
            parts.append(f"  {role}: {content}")
        parts.append("")

    # --- The question to answer (last — freshest in context) ---
    parts.append("FOLLOW-UP QUESTION TO ANSWER")
    parts.append(f"  {question}")

    return "\n".join(parts)
