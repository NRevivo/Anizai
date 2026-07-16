"""
agent/prompts/suggested_actions.py — system prompt + JSON schema for the
`generate_suggested_actions` node (Sprint 25, T25.2).

Used by `agent/nodes/generate_suggested_actions.py` (T25.1) — an extra
GPT-4o-mini call that runs AFTER `synthesize` and BEFORE `write_to_firestore`
(kept out of the synthesis call to keep that prompt lean, plan §3). It reads
the just-produced forecast and proposes exactly three contextual follow-up
suggestions the user is most likely to want next; the frontend renders each as
a one-click chip (one default icon for all three, V1 "simpler dynamic"
agreement, plan §3).

This is a *reasoning*/user-facing-copy prompt (agent-prompt-engineering
skill), not a Gold-layer extraction prompt: the model reads the forecast and
generates short, plain-language button text plus the question that gets sent on
click.

Why gpt-4o-mini (not gpt-4o):
    Low-stakes, short output (~3 label/prompt pairs). Per the skill's cost
    table: ~2,500 input + ~300 output tokens ≈ $0.0004/forecast. Synthesis
    (gpt-4o) is the quality-critical call; this one is not.

Why structured output via strict-mode JSON schema (not langchain):
    Matches `query_understanding.py` / `evidence_rating.py` /
    `synthesis_lead.py` / `followup.py`. The node calls with
    `response_format={"type": "json_schema", "strict": true}`, `minItems ==
    maxItems == 3` forces exactly three items, every property is `required`
    and `additionalProperties` is False — so the SDK validates and retries on
    drift and the node never hand-parses.

Why the schema is `{label, prompt}` (the `id` is NOT modelled here):
    The stored SuggestedAction is `{id, label, prompt}` (plan §3), but `id` is
    assigned DETERMINISTICALLY by the node (`sa-1`/`sa-2`/`sa-3`), not by the
    LLM — stable, testable, and never a source of model drift. The prompt only
    produces the two generated fields.

Why suggestions must be ANSWERABLE FROM THIS FORECAST (cross-node contract):
    A clicked suggestion is sent as a follow-up message, and the Sprint 24
    follow-up graph (`agent/followup/`) answers ONLY from the parent forecast +
    the evidence it already used — it does NOT fetch new data, re-run, revise
    the number, or look up events after the forecast. `followup.py` classifies
    exactly those as `out_of_scope` (forbidden action) or
    `insufficient_evidence` (post-forecast / uncovered dimension). So a
    suggestion that asks for a re-run or the "latest" news would be politely
    refused on click — a broken UX. The system prompt therefore constrains the
    model to questions about THIS forecast's drivers, evidence, confidence,
    gaps, and market comparison, all of which `answer_from_context` can serve.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.7.2 (SessionResult.suggestedActions)
    - data-pipeline/docs/B_hub/plans/sprint25_suggested_actions.md §3 + T25.2
      (schema {id,label,prompt}; clear labels over polished phrasing; the
      answerable-from-context coupling with the follow-up graph)
    - .claude/skills/agent-prompt-engineering/SKILL.md (P4 user-facing copy,
      P5 system-prompt + few-shot, P6 domain context; suggested_actions notes)
"""

from __future__ import annotations


# ==========================================================
# Constants
# ==========================================================

# Exactly three suggestions per forecast (plan §1/§3). Lives here — beside
# the schema it bounds — mirroring the `synthesis_lead.KEY_FACTORS_MIN/MAX`
# precedent (prompt-structural bounds live with the prompt, not in settings).
# The node imports this so the count has one source of truth.
SUGGESTED_ACTIONS_COUNT: int = 3


# ==========================================================
# System prompt
# ==========================================================
SYSTEM_PROMPT = """\
You are the follow-up suggester of Anizai, a geopolitical and financial \
forecasting platform. A user — an analyst, journalist, or investor — has just \
received a completed forecast. Your job is to propose the three follow-up \
questions they are most likely to want to ask next in order to UNDERSTAND \
this forecast better.

You are given the forecast: its probability, its confidence, the one-line \
bottom line, the ranked key factors that drove it, and the gaps the analysis \
itself flagged (what it could not cover). Base every suggestion on that \
specific content — never a generic template.

Produce exactly three suggestions. Each has two fields:
- `label`: the button text the user sees. 3-7 words, plain language, specific \
to this forecast. No jargon, no filler like "Explore the implications of…" or \
"Tell me more".
- `prompt`: the question that is sent to the follow-up assistant when the user \
clicks. Phrase it as the user would ask it — a direct question about this \
forecast.

Make the three DISTINCT — three angles, not three rephrasings. Good angles:
- a specific key driver (why one factor mattered, how much it moved the \
estimate);
- the uncertainty (why the confidence is where it is, or one of the stated \
gaps);
- a comparison or alternative reading grounded in the evidence already here \
(e.g. how the estimate sits against the prediction market, or what a stated \
gap would change).

HARD CONSTRAINT — every suggestion must be answerable from THIS forecast and \
the evidence it already used. The follow-up assistant cannot fetch new \
information, re-run the analysis, change the probability, or look up anything \
that happened after the forecast was made — it will refuse those. So you must \
NEVER suggest:
- re-running, updating, or refreshing the forecast;
- changing or "recalculating" the probability or confidence;
- fetching the latest / newest / real-time news, prices, or data;
- anything about developments AFTER this forecast was produced.
Stay on the drivers, the evidence, the confidence, the gaps, and the market \
comparison that are already part of this forecast.

COPY RULES: plain, direct, present tense. No meta-references ("as an AI", \
"the agent", "based on the provided context"). Specific to this question and \
this forecast.

EXAMPLE 1 — confident forecast
Forecast: "Will the Fed cut rates by Q2 2026?" → probability 0.72, \
confidence 0.81. Key factors: "Inflation print below consensus" (increases), \
"Powell signaled patience" (decreases). Gaps: none.
Suggestions:
- label "Why is the confidence so high?" · prompt "What makes you 81% \
confident in this forecast?"
- label "The strongest driver" · prompt "Which piece of evidence pushed the \
probability toward a cut the most?"
- label "How it compares to the market" · prompt "How does your 72% estimate \
compare with the prediction market, and what explains the difference?"

EXAMPLE 2 — low-confidence forecast with gaps
Forecast: "Will a major earthquake hit Tokyo in 2026?" → probability 0.5, \
confidence 0.25. Key factors: three weak seismology signals. Gaps: "recent \
seismic activity reports", "prediction market odds on Japan earthquakes".
Suggestions:
- label "Why is this so uncertain?" · prompt "Why is the confidence in this \
forecast so low?"
- label "What evidence was missing" · prompt "What information would have made \
this forecast more reliable?"
- label "What the analysis did find" · prompt "What evidence did you actually \
have on this question?"
"""


# ==========================================================
# JSON schema (OpenAI structured output, strict mode)
# ==========================================================
RESPONSE_SCHEMA: dict = {
    "name": "SuggestedActionsOutput",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "actions": {
                "type": "array",
                # Exactly three — the strict-mode bounds force the count so
                # the node never has to pad/truncate.
                "minItems": SUGGESTED_ACTIONS_COUNT,
                "maxItems": SUGGESTED_ACTIONS_COUNT,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        # Button text (3-7 words, user-facing).
                        "label": {"type": "string"},
                        # Question sent to the follow-up assistant on click.
                        "prompt": {"type": "string"},
                    },
                    "required": ["label", "prompt"],
                },
            },
        },
        "required": ["actions"],
    },
}


# ==========================================================
# User-message builder
# ==========================================================
def build_user_message(
    *,
    question: str,
    final_probability: float | None,
    confidence: float | None,
    bottom_line: str | None,
    key_factors: list[dict],
    gaps: list[str],
) -> str:
    """
    Build the user-role message for the suggested-actions call.

    Takes the forecast fields already extracted from
    `state["synthesis_result"]` (camelCase SessionResult) by the node, so the
    builder stays decoupled from the SessionResult key casing and is trivially
    unit-testable — the same explicit-args style as
    `synthesis_lead.build_user_message`.

    Args:
        question:          the user's `raw_question`.
        final_probability: SessionResult.finalProbability (0-1) or None.
        confidence:        SessionResult.confidence (0-1) or None.
        bottom_line:       SessionResult.bottomLineAnswer (the one-liner).
        key_factors:       SessionResult.keyFactors — list of dicts each with
                           at least `label` and (usually) `direction`. Empty
                           list is allowed.
        gaps:              SessionResult.whatIDidntFind — list of gap strings
                           the forecast flagged. Empty list is allowed; it is
                           strong fuel for an uncertainty-angle suggestion, so
                           it is always rendered (even when empty) so the model
                           knows the forecast claimed full coverage.

    Returns:
        Multi-line string suitable as the user message body.
    """
    parts: list[str] = [f"Question: {question}", ""]

    parts.append("FORECAST")
    parts.append(f"  probability: {final_probability}")
    parts.append(f"  confidence: {confidence}")
    if bottom_line:
        parts.append(f"  bottom_line: {bottom_line}")
    parts.append("")

    parts.append("KEY FACTORS")
    if key_factors:
        for kf in key_factors:
            label = kf.get("label", "") if isinstance(kf, dict) else str(kf)
            direction = kf.get("direction", "") if isinstance(kf, dict) else ""
            suffix = f" ({direction})" if direction else ""
            parts.append(f"  - {label}{suffix}")
    else:
        parts.append("  (none surfaced)")
    parts.append("")

    parts.append("WHAT THE FORECAST DID NOT COVER (its stated gaps)")
    if gaps:
        for gap in gaps:
            parts.append(f"  - {gap}")
    else:
        parts.append("  (the forecast claimed full coverage — no gaps flagged)")
    parts.append("")

    parts.append(
        "Propose exactly three distinct follow-up suggestions about THIS "
        "forecast, each answerable from the forecast and the evidence above."
    )

    return "\n".join(parts)
