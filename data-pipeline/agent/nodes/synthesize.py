"""
agent/nodes/synthesize.py — Node 6 of the forecast graph (T19.10).

Sprint 19 stub-synthesis node. Produces a §8.7.2-compliant SessionResult
dict from the running ForecastState. The numeric and narrative fields are
still placeholder values (probability 0.5, "stub mode" copy) — Sprint 20
T20.x replaces this body with a real GPT-4o synthesis call that consumes
researcher_evidence / pulse_evidence / market_evidence to produce
calibrated probability + key_factors + executive summary.

Why the helpers live here (not in process_query.py):
    Sprint 18 put `_build_stub_result`, `_derive_label`, `_derive_consensus`,
    and the `_STUB_*` constants inside `agent/process_query.py` because that
    was the only consumer. Sprint 19 introduces the synthesize node as the
    proper home — synthesis is its own concern (agent-design P1, single
    responsibility). T19.11 will replace `process_query.py` with a thin
    graph runner; until then process_query.py imports these helpers back
    from this module so the worker keeps functioning.

Why placeholder values for finalProbability / confidence:
    Sprint 19's job is wiring the retrieval pipeline; real probability
    calibration is Sprint 20. Returning a defensible neutral default (0.5
    / 0.5) keeps the §8.7.2 contract intact and the frontend render path
    working. The `whatIDidntFind` caption explicitly tells the user this
    is stub-mode output.

Why tier_1 (with marketProbability=null + marketComparison=[]):
    With market_evidence present but the polymarket-resolver still
    deferred (KG-PHASE8-12), Sprint 19 has no canonical Polymarket market
    to compare against. tier_1 + null marketProbability triggers the
    Patch 4 "no canonical market" empty state in the frontend's Tier 1
    render path. Sprint 18 prep aligned on tier_1 to test the dominant
    path before Tier 2 UI is built out. Sprint 21+ will flip this once
    the resolver lands.

Service isolation (CLAUDE.md §3.3):
    Reads only from state. No I/O. Firestore writes happen in the runner
    (process_query.py until T19.11) or in the future Node 7
    (`write_to_firestore`, Sprint 20+).

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.3.2 (Node 6 in topology)
    - data-pipeline/docs/agentic_hub_spec.md §8.7.2 (SessionResult schema)
    - data-pipeline/docs/agentic_hub_spec.md §8.6 (synthesis contract)
    - data-pipeline/docs/anizai_handoff_consolidated.md §5.1 (frontend contract)
"""

from __future__ import annotations

import logging

from agent.errors import AgentProcessingError
from agent.firestore_client import SERVER_TIMESTAMP

logger = logging.getLogger(__name__)


# ==========================================================
# AGENT_VERSION — bumped each sprint (D8)
# ==========================================================
# Module-level so the value appears in every result and can be grepped from
# logs / Firestore for which agent build produced a result.
AGENT_VERSION = "0.2.0-sprint19-retrieval-stub-synthesis"


# ==========================================================
# Stub constants
# ==========================================================
# Defined as named values rather than inline literals so they're
# discoverable and easy to remove when Sprint 20 swaps in real synthesis.
_STUB_FINAL_PROBABILITY = 0.5
_STUB_CONFIDENCE = 0.5
_STUB_TIER = "tier_1"
_STUB_WHAT_I_DIDNT_FIND = (
    "Sprint 19 stub-synthesis mode — retrieval is wired (Researcher, Pulse "
    "Analyst, Market Bridge all run) but real probability calibration and "
    "narrative generation are not yet implemented. Expect substantive "
    "answers from Sprint 20 onward."
)


# ==========================================================
# Label derivation — §8.7.2 lines 860-862
# ==========================================================
def _derive_label(value: float) -> str:
    """confidenceLabel from confidence: <0.5 Low, [0.5,0.8) Moderate, >=0.8 High.

    Also used for evidenceVolumeLabel (same Low/Moderate/High vocabulary
    per §8.7.2 line 830, distinct from consensusStrength's vocabulary).
    """
    if value >= 0.8:
        return "High"
    if value >= 0.5:
        return "Moderate"
    return "Low"


def _derive_consensus(value: float) -> str:
    """consensusStrength only. Vocabulary: Weak/Mixed/Strong (per §8.7.2
    line 829). Same threshold band shape as _derive_label.

    evidenceVolumeLabel uses Low/Moderate/High (§8.7.2 line 830) and is
    derived via _derive_label, NOT this function.
    """
    if value >= 0.8:
        return "Strong"
    if value >= 0.5:
        return "Mixed"
    return "Weak"


# ==========================================================
# Stub result builder
# ==========================================================
def _build_stub_result(question: str) -> dict:
    """
    Construct a §8.7.2-compliant SessionResult dict with placeholder values.

    Notes on field choices:
      - generatedAt is written here (alongside createdAt/updatedAt added by
        the wrapper). Spec §8.7.2 lists generatedAt; the current server
        getSessionResult reads createdAt/updatedAt only. Writing generatedAt
        too is defensive against future server reads — KG-PHASE8-6 tracks
        the spec/server drift.
      - sessionId is intentionally NOT set here — the wrapper sets it from
        its arg to avoid duplication.
      - All list fields default to [] (not None) so frontend list rendering
        doesn't have to null-check. whatIDidntFind is the only list with a
        populated entry — the stub-mode caption.
    """
    return {
        # Probability + confidence
        "finalProbability": _STUB_FINAL_PROBABILITY,
        "confidence": _STUB_CONFIDENCE,
        "confidenceLabel": _derive_label(_STUB_CONFIDENCE),
        "consensusStrength": _derive_consensus(0.0),
        "evidenceVolumeLabel": _derive_label(0.0),

        # Narrative fields — explicit "stub mode" text, not empty strings
        "bottomLineAnswer": (
            "Stub-synthesis mode: retrieval ran end-to-end but real "
            "probability calibration is not yet implemented. This response "
            "confirms the Sprint 19 retrieval pipeline is online."
        ),
        "detailedExplanation": (
            "Sprint 19 wired the partial LangGraph retrieval pipeline "
            "(claim_session → query_understand → build_embedding → "
            "vault_query → synthesize). Researcher, Pulse Analyst, and "
            "Market Bridge agents all queried the vault. Real reasoning, "
            "probability calibration, key_factors extraction, and "
            "marketComparison are scheduled for Sprints 20-23."
        ),
        "summaryMarkdown": (
            "**Stub-synthesis mode.** The agent received your question "
            f"\u201c{question}\u201d and ran the full Sprint 19 retrieval "
            "pipeline against the vault. Substantive synthesis begins in "
            "Sprint 20."
        ),
        "marketComparisonInsight": (
            "Market comparison is not produced in stub-synthesis mode."
        ),
        "sentimentAnalysisInsight": (
            "Sentiment analysis is not produced in stub-synthesis mode."
        ),
        "evidenceFeedSummary": (
            "Evidence feed summary is not produced in stub-synthesis mode."
        ),

        # Market fields — null + empty triggers the Patch 4 "no canonical
        # market" empty state in the Tier 1 UI render path. Will flip to
        # populated values once KG-PHASE8-12 (polymarket resolver) lands
        # in Sprint 21+ T21.6.
        "marketProbability": None,
        "marketComparison": [],

        # Lists — empty in stub mode (whatIDidntFind carries the caption)
        "keyFactors": [],
        "whatIDidntFind": [_STUB_WHAT_I_DIDNT_FIND],
        "reasoningChain": [],
        "suggestedActions": [],

        # Metadata
        "generatedAt": SERVER_TIMESTAMP,
        "agentVersion": AGENT_VERSION,
        "tier": _STUB_TIER,
    }


# ==========================================================
# Public node function
# ==========================================================
def run(state: dict) -> dict:
    """
    Build a §8.7.2-compliant synthesis_result from the running state.

    Sprint 19 contract: returns a `synthesis_result` dict matching the
    §8.7.2 SessionResult shape. Numeric and narrative fields are
    placeholders (Sprint 20 swaps in real synthesis).

    Args:
        state: Running ForecastState. Must contain `raw_question: str`
               (set by claim_session, Node 0).

    Returns:
        Partial state dict with `synthesis_result` populated.

    Raises:
        AgentProcessingError: when raw_question is missing or empty.
            Reaching synthesize without raw_question means an upstream bug
            (claim_session is supposed to populate it) — fail loudly so
            the missing field surfaces here rather than silently producing
            a corrupted result.
    """
    raw_question = state.get("raw_question") or ""
    if not raw_question.strip():
        raise AgentProcessingError(
            "synthesize: missing raw_question in state — claim_session "
            "should have populated it"
        )

    result = _build_stub_result(raw_question)

    logger.info(
        "synthesize: stub result built agent_version=%s tier=%s",
        AGENT_VERSION, result["tier"],
    )

    return {"synthesis_result": result}
