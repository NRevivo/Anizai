"""
agent/prompts/query_understanding.py — system prompt + JSON schema for Node 1.

Used by `agent/nodes/query_understand.py` (T19.5). Pairs an explicit system
prompt with a strict OpenAI structured-output JSON schema so the model is
forced to return a parsable response shaped exactly like the contract in
spec §8.3.2 "Node 1 (query_understand) output contract".

Why structured output (D9):
- Eliminates the malformed-JSON branch in error handling (`strict: true`
  guarantees the response conforms or the API errors out).
- Forces the model to populate every field — no missing keys to defend against.
- Closed enums (`intent`, `domain`) prevent vocabulary drift across runs.

Why top-3 candidates (D10):
- The model returns a ranked list. The node auto-picks rank 1 when its
  self-reported confidence clears `AUTO_PICK_THRESHOLD` and beats rank 2
  by `MARGIN_THRESHOLD`. Otherwise it surfaces all three to the user as
  clarification candidates.

Why polymarket_search_terms (not polymarket_slug) — see spec §8.3.2
"Correction (T19.5, 2026-04-30)" — the LLM is not given an authoritative
list of live polymarket markets in-context, so a single proposed slug
would either be invented or stale. The node produces *retrieval candidates*
and `vault_query` (Node 3) does the actual market resolution.

Why has_market_question_intent (not question_tier):
- True tier is unknowable until vault_query has tried to match a polymarket
  market. Node 1 only signals the user's *intent* to ask a market-resolvable
  question; the routing decision is made downstream.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.3.2 (Node 1 output contract)
    - data-pipeline/docs/agentic_hub_spec.md §8.5.4 (clarification trigger)
"""

from __future__ import annotations

# ==========================================================
# Closed enums (OQ-5, OQ-6)
# ==========================================================
# These match the eight data-source domains the platform actively ingests
# plus a "general" fallback. Adding a new value here is a deliberate spec
# change — do not silently extend.
DOMAIN_VALUES: list[str] = [
    "finance",
    "macro",
    "crypto",
    "tech",
    "geopolitics",
    "weather",
    "aviation",
    "general",
]

# Four user-facing question shapes. "forecast" maps to the forecast-graph
# happy path; "explain" / "summarize" / "compare" run the same retrieval
# but Synthesis (Sprint 20) tunes its prompt by intent.
INTENT_VALUES: list[str] = [
    "forecast",
    "explain",
    "summarize",
    "compare",
]

# Top-N candidate count surfaced to the user when the parse is ambiguous.
MAX_CANDIDATES: int = 3


# ==========================================================
# System prompt
# ==========================================================
SYSTEM_PROMPT = """\
You are the Query Understanding node of a financial/macroeconomic forecasting \
agent. Your only job is to parse a user's question into structured intent.

You will receive a single user question. You must return up to 3 ranked \
candidate interpretations, each with: intent, domain, entities, \
polymarket_search_terms (or null), has_market_question_intent, confidence, \
too_broad, rejected.

Rules:
1. Rank candidates by your own confidence in the parse. Candidate 1 is your \
best guess.
2. `entities` must be 1–5 canonical names (e.g., "Federal Reserve", \
"Bitcoin", "NVIDIA"). Use the most widely recognized name. Lowercase, \
slugs, or tickers are NOT canonical.
3. `polymarket_search_terms` is the list of keywords/phrases a downstream \
retrieval step would use to search for a matching prediction market. \
Populate ONLY when `has_market_question_intent` is true. Otherwise return \
null. Do NOT invent specific market slugs — provide search terms only.
4. `has_market_question_intent` is true ONLY when the question asks about \
a discrete, market-resolvable outcome (e.g., "Will the Fed cut rates by \
December?", "Will Bitcoin hit $100k in 2026?"). Open-ended explanatory \
questions ("Why is inflation rising?") are false.
5. `confidence` is your self-rated probability that THIS candidate is the \
correct interpretation, in [0.0, 1.0]. Calibrate honestly — if the \
question is ambiguous, no candidate should have confidence > 0.7.
6. `too_broad` is true if the question lacks any resolvable criterion \
(e.g., "Tell me about the economy"). Set this on candidate 1 and skip \
candidates 2-3 if true.
7. `rejected` is true for nonsensical, malicious, or out-of-domain \
questions (e.g., recipes, code generation requests). Set this on \
candidate 1 and skip candidates 2-3 if true.
8. If the question is clear and unambiguous, return a single candidate \
with high confidence rather than padding with weak alternatives.
"""


# ==========================================================
# JSON schema (OpenAI structured output, strict mode)
# ==========================================================
# `strict: true` requires every property to be marked required and
# additionalProperties: false on every object. Optional fields are modeled
# as nullable types (e.g., {"type": ["array", "null"]}).
RESPONSE_SCHEMA: dict = {
    "name": "QueryUnderstandingResponse",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "candidates": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_CANDIDATES,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "intent": {"type": "string", "enum": INTENT_VALUES},
                        "domain": {"type": "string", "enum": DOMAIN_VALUES},
                        "entities": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 5,
                            "items": {"type": "string"},
                        },
                        "polymarket_search_terms": {
                            "type": ["array", "null"],
                            "items": {"type": "string"},
                        },
                        "has_market_question_intent": {"type": "boolean"},
                        "confidence": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                        },
                        "too_broad": {"type": "boolean"},
                        "rejected": {"type": "boolean"},
                    },
                    "required": [
                        "intent",
                        "domain",
                        "entities",
                        "polymarket_search_terms",
                        "has_market_question_intent",
                        "confidence",
                        "too_broad",
                        "rejected",
                    ],
                },
            }
        },
        "required": ["candidates"],
    },
}


def build_user_message(question: str) -> str:
    """
    Wrap the raw question in a minimal envelope for the user-role message.

    Keeping the envelope dead simple ("Question: <text>") makes it easy to
    audit prompts in logs and reduces token spend vs. JSON-encoded inputs.
    """
    return f"Question: {question}"
