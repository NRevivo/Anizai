"""
agent/prompts/synthesis_lead.py — system prompt + JSON schema for the
`synthesize` node (T20.4).

Used by `agent/nodes/synthesize.py` (T20.3). This is the highest-stakes
prompt in the hub: GPT-4o reads the rated evidence pool and produces
the forecast — final probability, confidence, key factors, narrative,
gaps, per-evidence influence — that the user actually sees.

Why GPT-4o (not 4o-mini):
    Quality-critical. Per Patch 14 + Sprint 20 D1: synthesis is the
    only Sprint 20 prompt on gpt-4o. Estimated cost: ~$0.03/forecast
    (6k input + 2k output tokens), within Phase 8B success criterion
    (±20% of $0.03).

Why structured output via strict-mode JSON schema (not langchain):
    Matches Sprint 19 `query_understanding.py` and Sprint 20
    `evidence_rating.py` patterns. The agent-prompt-engineering skill
    accepts both forms — using the SDK's `response_format={"type":
    "json_schema", "strict": true}` keeps node code uniform across
    Sprint 20 and avoids pulling langchain into the LLM-call path.
    Pydantic models in `agent/schemas.py` (KeyFactor, ReasoningStep)
    mirror the schema for type safety in code.

Why two few-shot examples (rich vs sparse):
    Skill P5: examples carry more weight than instructions for
    calibration prompts. The two examples teach the model what
    high-confidence + dense evidence looks like vs. low-confidence +
    populated `what_i_didnt_find`. Without the sparse example the
    model fabricates confidence on thin evidence.

Why explicit calibration anchors:
    Skill P2: 0.1 / 0.5 / 0.9 with concrete examples. Without anchors
    the model drifts toward 0.7-0.8 on every forecast (the "cautiously
    optimistic" middle ground), which is useless.

Why explicit permission to admit gaps:
    Skill P3: "if your confidence is below 0.5, say so clearly. Better
    to say 'I don't have enough information' than to fabricate
    certainty." Without this, the synthesis prompt produces
    overconfident forecasts on sparse evidence — exactly the failure
    mode the rate_evidence relevance threshold + what_i_didnt_find
    field are designed to prevent.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.6 (synthesis contract)
    - data-pipeline/docs/agentic_hub_spec.md §8.7.2 (SessionResult schema)
    - .claude/skills/agent-prompt-engineering/SKILL.md (P1, P2, P3, P5,
      synthesis_lead-specific design notes)
"""

from __future__ import annotations


# ==========================================================
# Constants
# ==========================================================

# Spec §8.7.2: "3-5 drivers, ranked." Lower bound prevents the model
# from skipping the field; upper bound keeps the BI card readable.
KEY_FACTORS_MIN: int = 3
KEY_FACTORS_MAX: int = 5

# Per Q3 of the Sprint 20 plan (resolved): reasoning_chain is a 4-6
# step post-hoc summary. Lower bound forces the model to actually
# describe its reasoning (not "Step 1: Analyzed evidence"); upper
# bound keeps the chat panel scannable.
REASONING_STEPS_MIN: int = 4
REASONING_STEPS_MAX: int = 6

# Calibration anchors documented in the prompt body.
PROBABILITY_ANCHOR_LOW: float = 0.1   # very unlikely
PROBABILITY_ANCHOR_MID: float = 0.5   # ambiguous / coin flip
PROBABILITY_ANCHOR_HIGH: float = 0.9  # very likely


# ==========================================================
# System prompt
# ==========================================================
SYSTEM_PROMPT = """\
You are the Synthesis Lead of Anizai, a geopolitical and financial \
forecasting platform. Your output is a calibrated forecast that users \
— analysts, journalists, investors — read directly. You are evaluated \
on accuracy and calibration over time. Honesty about uncertainty is \
worth more than confident-sounding prose.

You receive:
- The user's question
- Structured intent (entities, domain, intent extracted upstream)
- A pool of rated evidence items (each with relevance_score, \
credibility_tier, recency_weight, source_type, title, snippet)
- Optional market context (Polymarket odds, FRED indicators); for \
Sprint 20 the canonical Polymarket market is unavailable, so treat \
market_context as absent unless explicitly populated.

You must produce one calibrated forecast. The output schema is \
enforced by the API; focus on the reasoning.

CALIBRATION

`final_probability` (0.0-1.0): your best estimate that the question \
resolves YES. Anchors:
  0.1 = very unlikely; the evidence weighs strongly against
  0.5 = genuinely ambiguous; could go either way
  0.9 = very likely; the evidence weighs strongly in favor

`confidence` (0.0-1.0): how sure are you of `final_probability`?
  0.1 = the evidence does not support a confident answer
  0.5 = some evidence each way, but enough to commit to a probability
  0.9 = the evidence is comprehensive and consistent

If `confidence < 0.5`, say so clearly in `bottom_line_answer` and \
populate `what_i_didnt_find` with specific gaps. A low-confidence \
answer with explicit gaps is better than a high-confidence answer \
that papers over uncertainty.

`consensus_score` (0.0-1.0): how strongly do the evidence items agree \
on the forecast direction? Used to derive consensusStrength downstream.
  0.0 = sharp disagreement / contradictory signals
  0.5 = mixed; some signals support each direction
  1.0 = uniform agreement

KEY FACTORS

Produce 3-5 ranked drivers. Each is a real, evidence-backed factor — \
not a generic consideration. Each cites at least one evidence item by \
its evidence_id. `direction` is "increases" (pushes probability up) \
or "decreases" (pushes probability down) — neutral context items do \
NOT earn a key_factors slot.

WHAT I DIDN'T FIND

If specific dimensions of the question went unaddressed by the \
evidence, list them here. Be specific: "I couldn't find recent \
academic research on this topic" is better than "limited information \
available". Empty list is acceptable when evidence is comprehensive.

REASONING CHAIN

A 4-6 step post-hoc summary of your reasoning. Numbered 1..N. Each \
step is one sentence describing what you did, not what's true.
Examples of good steps: "Identified the question's resolution \
criterion (Fed cuts before mid-2026).", "Evaluated 14 evidence items, \
of which 8 directly addressed the timing question."
Examples of bad steps: "Analyzed evidence." (too generic), "The \
forecast is 0.6." (output, not reasoning).

EVIDENCE OVERLAY

For every evidence item you saw, return one entry in `evidence_overlay`:
- `used_in_answer`: True only if the item materially influenced your \
forecast. Items below relevance 0.5 should usually be False.
- `impact_on_forecast`: "increases" / "decreases" / "neutral" / \
"context_only" — how the item affected the probability.
- `impact_magnitude` (0.0-1.0): how much it moved the needle. \
Items with `used_in_answer: False` should have impact_magnitude=0.0.

USER-FACING COPY RULES

`bottom_line_answer`, `detailed_explanation`, `summary_markdown`, \
the `description` fields on key_factors and reasoning_chain — these \
are rendered to the user. Plain language. No meta-references like "the \
agent decided", "based on prompt analysis", "I as an AI". Direct, \
present tense, active voice. Specific to this question and this \
evidence — no generic templates.

When market_context is absent (Sprint 20 default), \
`market_comparison_insight` should say so plainly: "No canonical \
prediction market available for this question — analysis based on \
underlying signals."

Examples follow.

EXAMPLE 1 — RICH EVIDENCE

Question: "Will the Fed cut rates by Q2 2026?"
Evidence: 12 items with avg relevance 0.78, including Reuters (Powell \
testimony), FT (Fed minutes), 2 Polymarket consensus rows, 4 FRED \
anomalies on FEDFUNDS, 3 ArXiv papers on monetary policy.
Output highlights: final_probability=0.72, confidence=0.81, \
consensus_score=0.78. key_factors includes "Powell signaled patience \
on rate cuts" (decreases, weight=0.35) and "Inflation print came in \
below consensus" (increases, weight=0.42). what_i_didnt_find=[].

EXAMPLE 2 — SPARSE EVIDENCE

Question: "Will a major earthquake hit Tokyo in 2026?"
Evidence: 3 items, only 1 with relevance > 0.5 (one ArXiv paper on \
Pacific seismology, two off-topic news articles).
Output highlights: final_probability=0.5 (genuinely ambiguous), \
confidence=0.25, consensus_score=0.3. \
bottom_line_answer="The available evidence doesn't support a \
confident forecast. Earthquake prediction at this granularity is \
beyond what current vault data covers." key_factors has 3 weak \
factors. what_i_didnt_find=["recent JMA seismic activity reports", \
"prediction market odds on Japan earthquakes", "expert consensus on \
Tokyo-specific risk".]
"""


# ==========================================================
# JSON schema (OpenAI structured output, strict mode)
# ==========================================================
RESPONSE_SCHEMA: dict = {
    "name": "SynthesisOutput",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "final_probability": {
                "type": "number", "minimum": 0.0, "maximum": 1.0,
            },
            "confidence": {
                "type": "number", "minimum": 0.0, "maximum": 1.0,
            },
            "consensus_score": {
                "type": "number", "minimum": 0.0, "maximum": 1.0,
            },
            "bottom_line_answer": {"type": "string"},
            "detailed_explanation": {"type": "string"},
            "summary_markdown": {"type": "string"},
            "market_comparison_insight": {"type": "string"},
            "sentiment_analysis_insight": {"type": "string"},
            "evidence_feed_summary": {"type": "string"},
            "what_i_didnt_find": {
                "type": "array",
                "items": {"type": "string"},
            },
            "key_factors": {
                "type": "array",
                "minItems": KEY_FACTORS_MIN,
                "maxItems": KEY_FACTORS_MAX,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "label": {"type": "string"},
                        "description": {"type": "string"},
                        "weight": {
                            "type": "number", "minimum": 0.0, "maximum": 1.0,
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["increases", "decreases"],
                        },
                        "evidence_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "label", "description", "weight",
                        "direction", "evidence_ids",
                    ],
                },
            },
            "reasoning_chain": {
                "type": "array",
                "minItems": REASONING_STEPS_MIN,
                "maxItems": REASONING_STEPS_MAX,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "step": {"type": "integer", "minimum": 1},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["step", "title", "description"],
                },
            },
            "evidence_overlay": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "evidence_id": {"type": "string"},
                        "used_in_answer": {"type": "boolean"},
                        "impact_on_forecast": {
                            "type": "string",
                            "enum": [
                                "increases", "decreases",
                                "neutral", "context_only",
                            ],
                        },
                        "impact_magnitude": {
                            "type": "number", "minimum": 0.0, "maximum": 1.0,
                        },
                    },
                    "required": [
                        "evidence_id", "used_in_answer",
                        "impact_on_forecast", "impact_magnitude",
                    ],
                },
            },
        },
        "required": [
            "final_probability",
            "confidence",
            "consensus_score",
            "bottom_line_answer",
            "detailed_explanation",
            "summary_markdown",
            "market_comparison_insight",
            "sentiment_analysis_insight",
            "evidence_feed_summary",
            "what_i_didnt_find",
            "key_factors",
            "reasoning_chain",
            "evidence_overlay",
        ],
    },
}


# ==========================================================
# User-message builder
# ==========================================================
def build_user_message(
    *,
    question: str,
    structured_intent: dict,
    polymarket_market: dict | None,
    evidence: list[dict],
) -> str:
    """
    Build the user-role message for the synthesis call.

    Format choices:
    - Question first, structured intent next, market context, then
      evidence list. Same order the model is expected to weigh them.
    - Evidence items are numbered + dashed-separated for legibility in
      logs and so the model has consistent visual structure.
    - The full evidence_id appears verbatim so the model can copy it
      into evidence_overlay and key_factors.evidence_ids.

    Args:
        question:           the user's `raw_question`.
        structured_intent:  query_understanding output (intent, domain,
                            entities). Empty dict if missing.
        polymarket_market:  None per Sprint 20 D3 (KG-PHASE8-12). When
                            populated in Sprint 21+, render slug + odds.
        evidence:           list of EvidenceItem dicts (post-rating).
                            Empty list is allowed — synthesis prompt
                            handles the cold-start "no evidence" case.

    Returns:
        Multi-line string suitable as the user message body.
    """
    parts: list[str] = [f"Question: {question}", ""]

    parts.append("Structured intent:")
    if structured_intent:
        for key in ("intent", "domain", "entities"):
            if key in structured_intent:
                parts.append(f"  {key}: {structured_intent[key]}")
    else:
        parts.append("  (none extracted)")
    parts.append("")

    parts.append("Market context:")
    if polymarket_market:
        parts.append(f"  polymarket_slug: {polymarket_market.get('market_slug', '')}")
        parts.append(f"  current_odds: {polymarket_market.get('current_odds', '')}")
    else:
        parts.append("  (no canonical market available — Sprint 20 limitation)")
    parts.append("")

    parts.append(f"Evidence ({len(evidence)} items):")
    if not evidence:
        parts.append("  (no evidence retrieved — produce a low-confidence")
        parts.append("   forecast and populate what_i_didnt_find with the")
        parts.append("   gaps the question implies.)")
    else:
        for i, item in enumerate(evidence, start=1):
            parts.append("---")
            parts.append(f"[{i}] evidence_id: {item.get('evidence_id', '')}")
            parts.append(f"    source_type: {item.get('source_type', '')}")
            parts.append(f"    credibility_tier: {item.get('credibility_tier', '')}")
            parts.append(f"    relevance_score: {item.get('relevance_score', 0.0)}")
            parts.append(f"    recency_weight: {item.get('recency_weight', 0.0)}")
            parts.append(f"    title: {item.get('title', '')}")
            snippet = item.get("snippet") or ""
            parts.append(f"    snippet: {snippet}")

    return "\n".join(parts)
