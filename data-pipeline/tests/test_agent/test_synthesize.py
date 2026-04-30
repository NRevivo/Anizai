"""
Gate 1 tests for `agent/nodes/synthesize.py` (T19.10).

Strategy: pure unit tests. No I/O, no mocks — synthesize is a state-only
node. Tests cover:
- node-level contract: run(state) returns {"synthesis_result": ...}
- §8.7.2 SessionResult shape (every required field present, correct types)
- raw_question propagation into summaryMarkdown
- AGENT_VERSION pin (so the bump in T19.10 is locked in)
- AgentProcessingError when raw_question is missing
- _derive_label / _derive_consensus boundary thresholds (off-by-one guard)
- Patch 4 Tier 1 empty-state guard (marketProbability=null, marketComparison=[])
"""

from __future__ import annotations

import pytest

from agent import firestore_client
from agent.errors import AgentProcessingError
from agent.nodes import synthesize


# ==========================================================
# Helpers
# ==========================================================
def _state(**overrides) -> dict:
    base = {"raw_question": "Will the Fed cut rates in 2026?"}
    base.update(overrides)
    return base


# ==========================================================
# run() — node-level contract
# ==========================================================
def test_run_returns_synthesis_result_key():
    out = synthesize.run(_state())
    assert "synthesis_result" in out
    assert isinstance(out["synthesis_result"], dict)


def test_run_reads_raw_question_into_summary_markdown():
    """
    The placeholder summaryMarkdown embeds the user's question. Sprint 20
    will replace this with real synthesized prose, but the question→prose
    plumbing should already be in place so the swap is local.
    """
    question = "Will BTC hit 100k by EOY 2026?"
    out = synthesize.run(_state(raw_question=question))

    assert question in out["synthesis_result"]["summaryMarkdown"]


def test_run_uses_bumped_agent_version():
    """
    Pin the T19.10 version bump. T20.x or later will bump again; that
    sprint should explicitly update this assertion.
    """
    out = synthesize.run(_state())
    assert out["synthesis_result"]["agentVersion"] == "0.2.0-sprint19-retrieval-stub-synthesis"
    assert synthesize.AGENT_VERSION == "0.2.0-sprint19-retrieval-stub-synthesis"


# ==========================================================
# §8.7.2 SessionResult shape
# ==========================================================
def test_run_returns_full_8_7_2_shape():
    """
    Every §8.7.2 field is present at the node layer. Belt-and-braces with
    test_process_query.py's integration-level shape test — this catches a
    field-drop introduced by future synthesize edits before the integration
    test would.
    """
    out = synthesize.run(_state(raw_question="will it rain?"))
    result = out["synthesis_result"]

    # Numerics
    assert result["finalProbability"] == 0.5
    assert result["confidence"] == 0.5

    # Derived labels
    assert result["confidenceLabel"] == "Moderate"
    assert result["consensusStrength"] == "Weak"
    assert result["evidenceVolumeLabel"] == "Low"

    # Empty list fields — frontend never has to null-check
    assert result["keyFactors"] == []
    assert result["reasoningChain"] == []
    assert result["suggestedActions"] == []

    # whatIDidntFind has the stub-mode caption (one entry)
    assert isinstance(result["whatIDidntFind"], list)
    assert len(result["whatIDidntFind"]) == 1
    assert "stub" in result["whatIDidntFind"][0].lower()

    # Metadata
    assert result["tier"] == "tier_1"
    assert result["generatedAt"] is firestore_client.SERVER_TIMESTAMP

    # Narrative fields — non-empty strings
    for key in (
        "bottomLineAnswer",
        "detailedExplanation",
        "summaryMarkdown",
        "marketComparisonInsight",
        "sentimentAnalysisInsight",
        "evidenceFeedSummary",
    ):
        assert isinstance(result[key], str), f"{key} is not a string"
        assert result[key], f"{key} is empty"


def test_run_marketprobability_null_and_marketcomparison_empty():
    """
    Patch 4 Tier 1 empty-state guard: with no resolved Polymarket market
    (KG-PHASE8-12), the frontend's Tier 1 render path expects
    marketProbability=None + marketComparison=[]. Sprint 21+ flips this
    once the resolver lands.
    """
    out = synthesize.run(_state())
    result = out["synthesis_result"]

    assert result["marketProbability"] is None
    assert result["marketComparison"] == []


# ==========================================================
# Input validation
# ==========================================================
@pytest.mark.parametrize("question", ["", "   ", None])
def test_run_raises_on_missing_raw_question(question):
    """
    synthesize is downstream of claim_session, which is responsible for
    populating raw_question. Reaching synthesize without it means an
    upstream bug — fail loudly so the missing field surfaces here rather
    than producing a corrupted result.
    """
    state = {"raw_question": question} if question is not None else {}

    with pytest.raises(AgentProcessingError) as exc_info:
        synthesize.run(state)

    assert "missing raw_question" in str(exc_info.value)


# ==========================================================
# _derive_label boundaries — §8.7.2 lines 860-862
# ==========================================================
@pytest.mark.parametrize(
    "value,expected",
    [
        (0.0, "Low"),
        (0.49, "Low"),
        (0.4999, "Low"),
        (0.5, "Moderate"),
        (0.65, "Moderate"),
        (0.7999, "Moderate"),
        (0.8, "High"),
        (0.95, "High"),
        (1.0, "High"),
    ],
)
def test_derive_label_thresholds(value, expected):
    assert synthesize._derive_label(value) == expected


# ==========================================================
# _derive_consensus boundaries — §8.7.2 line 829
# ==========================================================
@pytest.mark.parametrize(
    "value,expected",
    [
        (0.0, "Weak"),
        (0.49, "Weak"),
        (0.5, "Mixed"),
        (0.7999, "Mixed"),
        (0.8, "Strong"),
        (1.0, "Strong"),
    ],
)
def test_derive_consensus_thresholds(value, expected):
    assert synthesize._derive_consensus(value) == expected


# ==========================================================
# _build_stub_result — direct unit coverage
# ==========================================================
def test_build_stub_result_does_not_set_session_id():
    """
    sessionId is set by the wrapper at write time, not by the builder.
    Pin this so a future edit doesn't accidentally write a duplicate
    sessionId field that drifts from the wrapper's value.
    """
    result = synthesize._build_stub_result("any question")
    assert "sessionId" not in result


def test_build_stub_result_embeds_question_text():
    result = synthesize._build_stub_result("specific Q text 12345")
    assert "specific Q text 12345" in result["summaryMarkdown"]
