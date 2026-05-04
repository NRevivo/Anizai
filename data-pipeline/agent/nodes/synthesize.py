"""
agent/nodes/synthesize.py — Node 6 of the forecast graph (Sprint 20 T20.3).

Replaces the Sprint 19 stub-synthesis body with a real GPT-4o synthesis
call. Reads the rated `evidence_trail` (from rate_evidence, T20.1) plus
structured intent, calls the synthesis_lead prompt (T20.4), and produces
a §8.7.2-compliant `SessionResult` dict in `state.synthesis_result`.

Also mutates `state.evidence_trail`: each item's influence fields
(`used_in_answer`, `impact_on_forecast`, `impact_magnitude`) are filled
from the model's `evidence_overlay` output, then `is_key_evidence` and
`rank` are computed deterministically (top 5 used items by
`impact_magnitude` get `is_key_evidence=true`; all used items get
`rank` 1..N sorted by impact_magnitude desc).

Why deterministic post-synthesis ranking (not LLM-driven):
    Per agent-design P5 (determinism where possible) and
    evidence-handling skill: `is_key_evidence` and `rank` are
    "computed deterministically AFTER synthesis" so two forecasts on
    the same evidence pool produce stable, debuggable rankings. The
    LLM picks WHICH items influenced the forecast; the deterministic
    pass picks WHICH influenced ones to surface as key evidence.

Why polymarket_market=None always (KG-PHASE8-12):
    The polymarket auto-pick resolver is deferred to Sprint 21+ T21.6.
    Sprint 19 vault_query passes polymarket_slug=None to Market Bridge
    and structured_intent.polymarket_search_terms is wired but unused.
    Until the resolver lands, every Sprint 20 forecast renders with
    `marketProbability=null` + `marketComparison=[]` + the "no
    canonical market" caption — this triggers the Patch 4 Tier 1
    empty-state in the frontend's MarketComparison BI card.

Why reach AgentProcessingError on LLM failure (not graceful degrade):
    Synthesis is the only place a forecast is actually produced. If
    the GPT-4o call fails, there's no useful "partial forecast" to
    write — better to surface the failure to process_query._mark_failed
    and let the user retry with a fresh session. (This contrasts with
    rate_evidence's per-batch graceful path: rate_evidence can degrade
    to "rating_unavailable" on a subset of items and still produce a
    usable forecast; synthesize cannot.)

Why graph still routes here even with empty evidence_trail:
    Cold-start handling per the implementation plan §6 + skill P3:
    sparse evidence → low confidence + populated what_i_didnt_find,
    NOT a graph short-circuit. The synthesis prompt has explicit
    instructions for the empty-evidence case (Example 2 in the
    few-shot block).

Service isolation (CLAUDE.md §3.3):
    Talks to the OpenAI SDK only. No Firestore writes (those happen
    in write_to_firestore, T20.5). State-mediated communication only
    (agent-design P2).

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.6 (synthesis contract)
    - data-pipeline/docs/agentic_hub_spec.md §8.7.2 (SessionResult schema)
    - data-pipeline/docs/agentic_hub_spec.md §8.3.2 (Node 6 in topology)
    - data-pipeline/docs/anizai_handoff_consolidated.md §5.1 (frontend
      contract for SessionResult fields)
    - .claude/skills/agent-prompt-engineering/SKILL.md (synthesis_lead
      design notes)
    - .claude/skills/evidence-handling/SKILL.md (post-synthesis
      ranking logic)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from agent import labels
from agent.config import settings
from agent.errors import AgentProcessingError
from agent.firestore_client import SERVER_TIMESTAMP
from agent.prompts.synthesis_lead import (
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
    build_user_message,
)

logger = logging.getLogger(__name__)


# ==========================================================
# AGENT_VERSION — bumped per sprint (D10)
# ==========================================================
# Module-level so the value appears in every result and can be grepped
# from logs / Firestore for which agent build produced a result.
# Sprint 18: 0.1.0-sprint18-stub
# Sprint 19: 0.2.0-sprint19-retrieval-stub-synthesis
# Sprint 20: 0.3.0 — real synthesis + Firestore writes
AGENT_VERSION = "0.3.0-sprint20-real-synthesis-and-firestore"


# ==========================================================
# Constants
# ==========================================================

# Hard wall on the GPT-4o call. Synthesis is the slowest LLM call in the
# graph (gpt-4o, 2k+ output tokens). 60s leaves headroom over the
# typical 10-15s observed latency without letting a stuck call hang
# the worker indefinitely.
TIMEOUT_S: float = 60.0

# Cap on completion tokens. Spec says synthesis output is ~2000 tokens;
# 3000 leaves room for verbose reasoning_chain on complex questions.
MAX_TOKENS: int = 3000

# Top-N evidence items that get `is_key_evidence=True` — spec §8.7.2
# "3-5 drivers" governs key_factors; the sibling key_evidence flag
# uses 5 (top quintile of typical 20-30 item pools).
KEY_EVIDENCE_TOP_N: int = 5

# Tier label written into SessionResult.tier. Sprint 20 always tier_1
# (per design D3 + KG-PHASE8-12 deferral). Sprint 21 introduces the
# tier_2 freeform path.
TIER_DEFAULT: str = "tier_1"

# Caption for marketComparisonInsight when no canonical Polymarket
# market is available. Triggers the Patch 4 frontend empty state.
NO_MARKET_CAPTION: str = (
    "No canonical prediction market available for this question — "
    "analysis based on underlying signals."
)


# ==========================================================
# OpenAI client lifecycle (matches Sprint 19 / Bundle A pattern)
# ==========================================================
_default_client: Optional[Any] = None


def _get_default_client() -> Any:
    """
    Lazy-construct a module-level OpenAI client.

    Lazy because importing `openai` and reading the API key at
    module-load time would break test discovery in environments without
    OPENAI_API_KEY set. Tests inject their own client via the `client`
    kwarg and never hit this function.
    """
    global _default_client
    if _default_client is None:
        from openai import OpenAI  # local import keeps cold-start cheap

        _default_client = OpenAI(api_key=settings.OPENAI_API_KEY, timeout=TIMEOUT_S)
    return _default_client


# ==========================================================
# Public node function
# ==========================================================
def run(state: dict, *, client: Optional[Any] = None) -> dict:
    """
    Execute Node 6 of the forecast graph.

    Args:
        state:  Running ForecastState. Must contain `raw_question`.
                Reads `evidence_trail` (default empty list),
                `structured_intent` (default empty dict). Polymarket
                market is None per KG-PHASE8-12 — Sprint 21+ may read
                from `state.polymarket_market`.
        client: Optional injected OpenAI client (tests).

    Returns:
        Partial state dict for LangGraph to merge:
            - `synthesis_result`: §8.7.2-compliant SessionResult dict
            - `evidence_trail`: same items as input, but with influence
              fields filled in (used_in_answer, impact_on_forecast,
              impact_magnitude, is_key_evidence, rank)
            - `key_factors`, `what_i_didnt_find`: also surfaced at
              top-level state for downstream nodes that may want them
              without unpacking synthesis_result
            - `tier`: "tier_1" (Sprint 20)
            - `llm_calls_count`, `total_tokens_used`: incremented

    Raises:
        AgentProcessingError: when raw_question is missing/empty, when
            the GPT-4o call fails, or when the response can't be parsed.
            process_query._mark_failed catches this and writes
            `failed` to Firestore.
    """
    raw_question = state.get("raw_question") or ""
    if not raw_question.strip():
        raise AgentProcessingError(
            "synthesize: missing raw_question in state — claim_session "
            "should have populated it"
        )

    evidence_trail: list[dict] = list(state.get("evidence_trail") or [])
    structured_intent: dict = state.get("structured_intent") or {}
    polymarket_market: Optional[dict] = state.get("polymarket_market")
    # Sprint 20 D3: KG-PHASE8-12 means polymarket_market is always None.
    # The arg is kept on the prompt builder for forward compatibility
    # with Sprint 21+ when the resolver lands.

    client = client or _get_default_client()

    response = _call_openai(
        client=client,
        question=raw_question,
        structured_intent=structured_intent,
        polymarket_market=polymarket_market,
        evidence=evidence_trail,
    )
    output = _extract_output(response)
    tokens_used = _extract_token_usage(response)

    _apply_evidence_overlay(evidence_trail, output.get("evidence_overlay") or [])
    _rank_key_evidence(evidence_trail)

    synthesis_result = _build_session_result(
        synthesis_output=output,
        evidence_trail=evidence_trail,
        polymarket_market=polymarket_market,
    )

    logger.info(
        "synthesize: built SessionResult agent_version=%s "
        "final_probability=%.3f confidence=%.3f n_evidence=%d",
        AGENT_VERSION,
        synthesis_result["finalProbability"],
        synthesis_result["confidence"],
        len(evidence_trail),
    )

    return {
        "synthesis_result": synthesis_result,
        "evidence_trail": evidence_trail,
        "key_factors": synthesis_result["keyFactors"],
        "what_i_didnt_find": synthesis_result["whatIDidntFind"],
        "tier": TIER_DEFAULT,
        "confidence_score": synthesis_result["confidence"],
        "llm_calls_count": int(state.get("llm_calls_count") or 0) + 1,
        "total_tokens_used": int(state.get("total_tokens_used") or 0) + tokens_used,
    }


# ==========================================================
# OpenAI plumbing
# ==========================================================
def _call_openai(
    *,
    client: Any,
    question: str,
    structured_intent: dict,
    polymarket_market: Optional[dict],
    evidence: list[dict],
) -> Any:
    """
    Invoke chat.completions with strict structured output for synthesis.

    Wraps every SDK-level exception in AgentProcessingError so the
    runner only has to catch one type. `strict: true` on the JSON
    schema means we never need to handle malformed JSON — the API
    errors out before returning a non-conforming response.
    """
    user_message = build_user_message(
        question=question,
        structured_intent=structured_intent,
        polymarket_market=polymarket_market,
        evidence=evidence,
    )
    try:
        return client.chat.completions.create(
            model=settings.OPENAI_MODEL_SYNTHESIS,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_schema", "json_schema": RESPONSE_SCHEMA},
            max_tokens=MAX_TOKENS,
            temperature=0.0,
        )
    except AgentProcessingError:
        raise
    except Exception as exc:
        raise AgentProcessingError(
            f"synthesize: OpenAI call failed — {exc!r}"
        ) from exc


def _extract_output(response: Any) -> dict:
    """
    Parse the strict-mode JSON response into the synthesis output dict.

    The API guarantees the shape (because `strict: true`), but we still
    validate the response envelope so a malformed SDK-level response
    raises here rather than crashing _build_session_result downstream.
    """
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError) as exc:
        raise AgentProcessingError(
            f"synthesize: malformed OpenAI response shape — {exc!r}"
        ) from exc

    try:
        parsed = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AgentProcessingError(
            f"synthesize: response not JSON — {exc!r}"
        ) from exc

    if not isinstance(parsed, dict):
        raise AgentProcessingError(
            f"synthesize: response root is not an object — got "
            f"{type(parsed).__name__}"
        )
    return parsed


def _extract_token_usage(response: Any) -> int:
    """Pull total_tokens off the response, defaulting to 0 if absent."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0
    return int(getattr(usage, "total_tokens", 0) or 0)


# ==========================================================
# Evidence overlay + post-synthesis ranking
# ==========================================================
def _apply_evidence_overlay(evidence_trail: list[dict], overlay: list[dict]) -> None:
    """
    Merge the model's per-evidence influence fields onto the trail
    items, in place. Items not covered by the overlay keep their
    rate_evidence-set defaults (used_in_answer=False,
    impact_on_forecast="context_only", impact_magnitude=0.0).

    Why match by evidence_id, not list index:
        The synthesis prompt is told to return one overlay entry per
        evidence item, but the model can reorder or omit. ID-based
        matching makes the merge robust to either failure mode.
    """
    by_id = {row.get("evidence_id"): row for row in overlay if row.get("evidence_id")}
    for item in evidence_trail:
        row = by_id.get(item["evidence_id"])
        if row is None:
            continue
        item["used_in_answer"] = bool(row.get("used_in_answer"))
        item["impact_on_forecast"] = str(
            row.get("impact_on_forecast") or "context_only"
        )
        item["impact_magnitude"] = float(row.get("impact_magnitude") or 0.0)


def _rank_key_evidence(evidence_trail: list[dict]) -> None:
    """
    Compute `is_key_evidence` and `rank` deterministically post-overlay.

    Algorithm (per evidence-handling skill):
        1. Take items where `used_in_answer == True`.
        2. Sort by `impact_magnitude` DESC, ties broken by
           `relevance_score` DESC (stable order across runs on the
           same evidence pool).
        3. Top KEY_EVIDENCE_TOP_N → `is_key_evidence = True`.
        4. All used items get `rank` 1..N in the sorted order.
        5. Unused items keep `is_key_evidence=False`, `rank=0`.

    Why a stable secondary sort:
        Without it, two items with identical impact_magnitude could
        flip ranks across runs depending on dict iteration order —
        breaks reproducibility (agent-design P5).
    """
    used = [item for item in evidence_trail if item.get("used_in_answer")]
    used.sort(
        key=lambda i: (
            -float(i.get("impact_magnitude") or 0.0),
            -float(i.get("relevance_score") or 0.0),
        )
    )

    for rank_idx, item in enumerate(used, start=1):
        item["rank"] = rank_idx
        item["is_key_evidence"] = rank_idx <= KEY_EVIDENCE_TOP_N


# ==========================================================
# §8.7.2 SessionResult builder
# ==========================================================
def _build_session_result(
    *,
    synthesis_output: dict,
    evidence_trail: list[dict],
    polymarket_market: Optional[dict],
) -> dict:
    """
    Assemble the §8.7.2-compliant SessionResult dict from the LLM's
    structured output + the rated evidence trail.

    Field-by-field choices:
      - finalProbability/confidence: passed through as floats
      - confidenceLabel/consensusStrength/evidenceVolumeLabel: derived
        deterministically via labels.py — never echoed from the LLM,
        keeps display labels consistent across forecasts
      - marketProbability/marketComparison: None / [] when polymarket
        is absent (KG-PHASE8-12). Sprint 21+ flips this once the
        resolver lands.
      - marketComparisonInsight: synthesis prompt is asked to write
        this, but if polymarket is absent we override with the canonical
        no-market caption to ensure the frontend renders the empty state.
      - keyFactors/reasoningChain/whatIDidntFind: passed through as
        produced by the prompt; their shapes are pinned by the schema.
      - suggestedActions: [] for Sprint 20 per D8 (deferred to Sprint 25).
      - generatedAt: SERVER_TIMESTAMP. createdAt/updatedAt are added by
        firestore_client.write_session_result (KG-PHASE8-6 mitigation).
      - tier: tier_1 always for Sprint 20 (D3).
    """
    used_count = sum(1 for item in evidence_trail if item.get("used_in_answer"))

    confidence = float(synthesis_output["confidence"])
    consensus_score = float(synthesis_output["consensus_score"])

    # Override the model's market_comparison_insight when no market is
    # available — the frontend's Tier 1 render path expects this exact
    # caption to trigger the empty state (Patch 4).
    market_comparison_insight = (
        synthesis_output.get("market_comparison_insight") or ""
    )
    if polymarket_market is None:
        market_comparison_insight = NO_MARKET_CAPTION

    return {
        "finalProbability": float(synthesis_output["final_probability"]),
        "confidence": confidence,
        "confidenceLabel": labels.confidence_label_from_score(confidence),
        "consensusStrength": labels.consensus_strength_from_score(consensus_score),
        "evidenceVolumeLabel": labels.evidence_volume_label_from_count(used_count),

        "bottomLineAnswer": synthesis_output["bottom_line_answer"],
        "detailedExplanation": synthesis_output["detailed_explanation"],
        "summaryMarkdown": synthesis_output["summary_markdown"],
        "marketComparisonInsight": market_comparison_insight,
        "sentimentAnalysisInsight": synthesis_output["sentiment_analysis_insight"],
        "evidenceFeedSummary": synthesis_output["evidence_feed_summary"],

        # KG-PHASE8-12: no canonical market until Sprint 21+ T21.6.
        "marketProbability": None,
        "marketComparison": [],

        "keyFactors": list(synthesis_output.get("key_factors") or []),
        "whatIDidntFind": list(synthesis_output.get("what_i_didnt_find") or []),
        "reasoningChain": list(synthesis_output.get("reasoning_chain") or []),

        # D8: agentEvents + suggestedActions deferred to Sprint 25.
        "suggestedActions": [],

        "generatedAt": SERVER_TIMESTAMP,
        "agentVersion": AGENT_VERSION,
        "tier": TIER_DEFAULT,
    }
