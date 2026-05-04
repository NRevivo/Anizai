"""
agent/prompts/evidence_rating.py — system prompt + JSON schema for the
`rate_evidence` node (T20.2).

Used by `agent/nodes/rate_evidence.py` (T20.1). The node calls OpenAI in
batches of 5-10 evidence items per request; this module supplies the
system prompt, the strict-mode JSON schema, and the user-message builder.

Why structured output (matches Sprint 19 query_understanding pattern):
    Same rationale as `query_understanding.py` (D9): `strict: true` on the
    JSON schema eliminates the malformed-JSON branch, forces every field
    to be populated, and lets the node parse the response with
    `json.loads()` without defensive checks. The agent-prompt-engineering
    skill prefers Pydantic structured output; the existing codebase
    standardizes on raw OpenAI structured-output mode (the same API-level
    enforcement, just at the SDK layer instead of the langchain wrapper).
    Sprint 20 matches the existing pattern; the EvidenceItem Pydantic
    model in `agent/schemas.py` is the type-safety layer around the
    structured-output result.

Why batch-rating (not per-item):
    Cost. Per-item calls would multiply token-overhead (system prompt
    paid every call) by ~30-50x per session. Batches of 8 amortize the
    system-prompt cost while keeping each completion small enough that
    the model stays focused — agent-prompt-engineering "P5: under 800
    words" applies to system prompts, not user payloads, but giving the
    model 30 items at once degrades quality in practice.

Why calibration anchors instead of plain "rate 1-5":
    Per agent-prompt-engineering P2 (calibrate, don't classify). The
    prompt anchors 0.1 / 0.5 / 0.9 with concrete examples so different
    items in different batches produce comparable scores.

Why explicit permission to admit gaps:
    P3: "if you're not sure, choose a value closer to 0.5 and explain
    why in the justification field." A model that's never told it can
    be uncertain produces overconfident ratings.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.5.5 (EvidenceItem schema,
      relevance_score field semantics)
    - .claude/skills/agent-prompt-engineering/SKILL.md (P1, P2, P3, P5)
    - .claude/skills/evidence-handling/SKILL.md (rating logic — relevance
      threshold 0.5 for used_in_answer)
"""

from __future__ import annotations


# ==========================================================
# Constants
# ==========================================================

# Batch size for the rate_evidence node. 8 is the agent-prompt-engineering
# skill's suggested midpoint of the 5-10 range. Larger → cheaper per-item
# but observed quality drop on batches > 12; smaller → more API overhead.
BATCH_SIZE: int = 8

# Anchored relevance-score scale. These three are the calibration points
# the prompt teaches the model. The full continuum 0.0-1.0 is allowed,
# but documenting the anchors means consistent calibration across batches.
RELEVANCE_ANCHOR_LOW: float = 0.1   # mentions topic, doesn't address question
RELEVANCE_ANCHOR_MID: float = 0.5   # adjacent / partial answer
RELEVANCE_ANCHOR_HIGH: float = 0.9  # directly addresses the question


# ==========================================================
# System prompt
# ==========================================================
SYSTEM_PROMPT = """\
You are the Evidence Rating node of Anizai, a geopolitical and financial \
forecasting platform. Your job is to rate how relevant each evidence item \
is to the user's specific question, and write a one-sentence \
user-facing justification for each.

You will receive:
- A user question.
- A batch of 1-8 evidence items, each with: evidence_id, source_type, \
title, snippet, source_domain, published_at.

You must return one rating per item (in the same order):
- relevance_score: float in [0.0, 1.0]. How well does this item address \
the user's specific question?
- justification: ONE plain-language sentence the user will see in the \
chain-of-thought UI.

Calibration (anchored examples):

  0.1 — Item mentions a related topic but does not address the question. \
Example: question is "Will the Fed cut rates by Q2 2026?", item is a \
generic article about US monetary policy history.

  0.5 — Item addresses an adjacent or partial slice of the question. \
Example: same question, item is "Fed signals patience on rate cuts" \
(relevant context but not predictive of Q2 2026 specifically).

  0.9 — Item directly addresses the question with substantive evidence. \
Example: same question, item is "Powell tells Senate committee the Fed \
expects two rate cuts before mid-2026."

Rules:
1. Rate against the user's SPECIFIC question, not the broad topic. \
Topical similarity alone is not enough — an article that's adjacent but \
doesn't answer the question scores below 0.5.
2. If you're uncertain whether an item addresses the question, choose a \
value closer to 0.5 and say so in the justification ("touches on \
inflation but doesn't speak to the rate cut timing"). A calibrated \
mid-score with explicit gaps is better than a fabricated high score.
3. The justification is rendered to the user. Plain language, no jargon, \
no meta-references like "the agent decided" or "based on prompt analysis". \
One sentence. Specific to THIS item and THIS question.
4. The credibility of the source (Reuters vs blog) is NOT your concern \
here — that is set elsewhere from a domain allowlist. Rate purely on \
relevance to the question.
5. Recency is NOT your concern here either — it is computed deterministically \
elsewhere. Rate purely on relevance to the question.
6. Return one rating per item, in the SAME ORDER as the input. Use the \
exact `evidence_id` provided.
"""


# ==========================================================
# JSON schema (OpenAI structured output, strict mode)
# ==========================================================
RESPONSE_SCHEMA: dict = {
    "name": "EvidenceRatingResponse",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "ratings": {
                "type": "array",
                "minItems": 1,
                "maxItems": BATCH_SIZE,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "evidence_id": {"type": "string"},
                        "relevance_score": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                        },
                        "justification": {"type": "string"},
                    },
                    "required": ["evidence_id", "relevance_score", "justification"],
                },
            }
        },
        "required": ["ratings"],
    },
}


# ==========================================================
# User message builder
# ==========================================================
def build_user_message(*, question: str, items: list[dict]) -> str:
    """
    Build the user-role message for one rating batch.

    Each item is rendered as a small block with the four fields the model
    needs to judge relevance. Long snippets are NOT truncated here — the
    rate_evidence node owns truncation when it normalizes evidence
    packages into EvidenceItem shape. Whatever lands in `items[i]["snippet"]`
    is what the model sees.

    Format choices:
    - Markdown-ish item separators (`---`) make it easy to audit prompts
      in logs.
    - `evidence_id` is repeated verbatim in the response (per the schema),
      so the model has it visible at the top of each block to copy.
    - Date is rendered as ISO string; the prompt explicitly tells the
      model to ignore recency, so format doesn't matter much.

    Args:
        question: the user's `raw_question`.
        items:    list of dicts with keys evidence_id, source_type, title,
                  snippet, source_domain, published_at. Caller is
                  responsible for batch-size <= BATCH_SIZE.

    Returns:
        A single multi-line string suitable as the user message body.
    """
    blocks: list[str] = [f"Question: {question}", "", "Evidence to rate:"]
    for item in items:
        blocks.append("---")
        blocks.append(f"evidence_id: {item.get('evidence_id', '')}")
        blocks.append(f"source_type: {item.get('source_type', '')}")
        blocks.append(f"source_domain: {item.get('source_domain', '')}")
        blocks.append(f"published_at: {item.get('published_at', '')}")
        blocks.append(f"title: {item.get('title', '')}")
        snippet = item.get("snippet") or ""
        blocks.append(f"snippet: {snippet}")
    return "\n".join(blocks)
