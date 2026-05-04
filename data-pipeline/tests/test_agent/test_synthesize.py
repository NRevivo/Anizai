"""
Gate 1 tests for `agent/nodes/synthesize.py` (Sprint 20 T20.3).

Strategy: pure-mock at the OpenAI client boundary (matches Sprint 19
query_understand pattern). Fake `chat.completions.create()` responses
shaped like openai>=1.x; no real GPT-4o calls.

Coverage:
- happy path: Pydantic-validated output → §8.7.2 SessionResult shape
- AGENT_VERSION pin for Sprint 20 (`0.3.0-...`)
- empty raw_question → AgentProcessingError
- empty evidence_trail → still produces a SessionResult (cold-start)
- evidence_overlay merged onto evidence_trail items by evidence_id
- post-synthesis ranking: top-5 by impact_magnitude → is_key_evidence
- rank assigned 1..N for used items only, in impact_magnitude order
- ties on impact_magnitude broken by relevance_score (deterministic)
- KG-PHASE8-12: marketProbability=None, marketComparison=[],
  marketComparisonInsight uses the no-market caption regardless of LLM
- evidence-volume label derives from used-in-answer count, not list len
- OpenAI SDK raises → AgentProcessingError
- malformed JSON content → AgentProcessingError
- malformed response shape → AgentProcessingError
- prompt + schema forwarded to OpenAI verbatim (regression guard)
- llm_calls_count + total_tokens_used aggregated
- tier="tier_1" pinned (Sprint 20 D3)
- raw_question routed through to user-message
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent import firestore_client
from agent.errors import AgentProcessingError
from agent.nodes import synthesize
from agent.prompts.synthesis_lead import RESPONSE_SCHEMA, SYSTEM_PROMPT


# ==========================================================
# Helpers — fake LLM output
# ==========================================================
def _make_synthesis_output(
    *,
    final_probability: float = 0.7,
    confidence: float = 0.8,
    consensus_score: float = 0.75,
    bottom_line_answer: str = "Likely yes.",
    detailed_explanation: str = "Detailed reasoning here.",
    summary_markdown: str = "**Summary**: forecast is likely yes.",
    market_comparison_insight: str = "Markets agree.",
    sentiment_analysis_insight: str = "Sentiment is positive.",
    evidence_feed_summary: str = "12 items reviewed.",
    what_i_didnt_find: list[str] | None = None,
    key_factors: list[dict] | None = None,
    reasoning_chain: list[dict] | None = None,
    evidence_overlay: list[dict] | None = None,
) -> dict:
    if key_factors is None:
        key_factors = [
            {"label": "Factor A", "description": "Drives up.",
             "weight": 0.4, "direction": "increases", "evidence_ids": []},
            {"label": "Factor B", "description": "Mild down.",
             "weight": 0.2, "direction": "decreases", "evidence_ids": []},
            {"label": "Factor C", "description": "Drives up.",
             "weight": 0.3, "direction": "increases", "evidence_ids": []},
        ]
    if reasoning_chain is None:
        reasoning_chain = [
            {"step": 1, "title": "Identify question", "description": "Parsed the resolution criterion."},
            {"step": 2, "title": "Review evidence", "description": "Read 12 items."},
            {"step": 3, "title": "Weigh factors", "description": "Identified 3 drivers."},
            {"step": 4, "title": "Produce forecast", "description": "Final probability calibrated."},
        ]
    return {
        "final_probability": final_probability,
        "confidence": confidence,
        "consensus_score": consensus_score,
        "bottom_line_answer": bottom_line_answer,
        "detailed_explanation": detailed_explanation,
        "summary_markdown": summary_markdown,
        "market_comparison_insight": market_comparison_insight,
        "sentiment_analysis_insight": sentiment_analysis_insight,
        "evidence_feed_summary": evidence_feed_summary,
        "what_i_didnt_find": what_i_didnt_find or [],
        "key_factors": key_factors,
        "reasoning_chain": reasoning_chain,
        "evidence_overlay": evidence_overlay or [],
    }


def _make_response(*, output: dict, total_tokens: int = 1500) -> SimpleNamespace:
    """openai>=1.x ChatCompletion shape with a synthesis output payload."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(output))
            )
        ],
        usage=SimpleNamespace(total_tokens=total_tokens),
    )


def _client_returning(response: SimpleNamespace) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create = MagicMock(return_value=response)
    return client


def _state(**overrides) -> dict:
    base = {"raw_question": "Will the Fed cut rates in 2026?"}
    base.update(overrides)
    return base


def _evidence_item(
    *,
    evidence_id: str = "ev-1",
    relevance_score: float = 0.7,
    used_in_answer: bool = False,
    impact_magnitude: float = 0.0,
) -> dict:
    """Minimal evidence_trail item for synthesize tests. rate_evidence
    produces these in production; here we hand-roll them for control."""
    return {
        "evidence_id": evidence_id,
        "source_type": "vault_news",
        "origin": "knowledge_vault",
        "title": f"Title {evidence_id}",
        "snippet": "snippet",
        "url": None,
        "source_domain": "reuters.com",
        "published_at": None,
        "fetched_at": None,
        "relevance_score": relevance_score,
        "credibility_tier": "tier_1",
        "recency_weight": 0.9,
        "used_in_answer": used_in_answer,
        "impact_on_forecast": "context_only",
        "impact_magnitude": impact_magnitude,
        "is_key_evidence": False,
        "rank": 0,
        "justification": "ok",
    }


# ==========================================================
# AGENT_VERSION pin
# ==========================================================
def test_agent_version_bumped_to_sprint_20():
    assert synthesize.AGENT_VERSION == "0.3.0-sprint20-real-synthesis-and-firestore"


def test_agentversion_appears_in_session_result():
    out = synthesize.run(
        _state(),
        client=_client_returning(_make_response(output=_make_synthesis_output())),
    )
    assert out["synthesis_result"]["agentVersion"] == synthesize.AGENT_VERSION


# ==========================================================
# §8.7.2 SessionResult shape
# ==========================================================
def test_run_returns_full_8_7_2_shape():
    out = synthesize.run(
        _state(),
        client=_client_returning(_make_response(output=_make_synthesis_output())),
    )
    result = out["synthesis_result"]

    # Numerics
    assert isinstance(result["finalProbability"], float)
    assert isinstance(result["confidence"], float)

    # Derived labels (deterministic, not echoed from LLM)
    assert result["confidenceLabel"] in {"Low", "Moderate", "High"}
    assert result["consensusStrength"] in {"Weak", "Mixed", "Strong"}
    assert result["evidenceVolumeLabel"] in {"Low", "Moderate", "High"}

    # List fields default to [] never None
    assert isinstance(result["keyFactors"], list)
    assert isinstance(result["reasoningChain"], list)
    assert isinstance(result["whatIDidntFind"], list)
    assert result["suggestedActions"] == []  # D8: deferred to Sprint 25

    # Metadata
    assert result["tier"] == "tier_1"
    assert result["generatedAt"] is firestore_client.SERVER_TIMESTAMP

    # Narrative fields — non-empty strings
    for key in (
        "bottomLineAnswer", "detailedExplanation", "summaryMarkdown",
        "marketComparisonInsight", "sentimentAnalysisInsight",
        "evidenceFeedSummary",
    ):
        assert isinstance(result[key], str)
        assert result[key]


def test_label_derivation_matches_score():
    """confidence=0.85 → "High" (above 0.8 threshold)."""
    out = synthesize.run(
        _state(),
        client=_client_returning(_make_response(
            output=_make_synthesis_output(confidence=0.85, consensus_score=0.85)
        )),
    )
    assert out["synthesis_result"]["confidenceLabel"] == "High"
    assert out["synthesis_result"]["consensusStrength"] == "Strong"


def test_evidence_volume_label_uses_used_in_answer_count():
    """Spec: evidenceVolumeLabel comes from count of items with
    used_in_answer=True, NOT from raw evidence_trail length."""
    items = [_evidence_item(evidence_id=f"ev-{i}") for i in range(20)]
    overlay = [
        {"evidence_id": f"ev-{i}", "used_in_answer": i < 5,
         "impact_on_forecast": "context_only", "impact_magnitude": 0.0}
        for i in range(20)
    ]
    out = synthesize.run(
        _state(evidence_trail=items),
        client=_client_returning(_make_response(
            output=_make_synthesis_output(evidence_overlay=overlay)
        )),
    )
    # 5 items used_in_answer → Moderate per evidence-handling skill
    assert out["synthesis_result"]["evidenceVolumeLabel"] == "Moderate"


# ==========================================================
# KG-PHASE8-12 empty-state guard
# ==========================================================
def test_marketprobability_null_and_marketcomparison_empty():
    out = synthesize.run(
        _state(),
        client=_client_returning(_make_response(output=_make_synthesis_output())),
    )
    assert out["synthesis_result"]["marketProbability"] is None
    assert out["synthesis_result"]["marketComparison"] == []


def test_market_comparison_insight_overridden_when_no_market():
    """Even if the LLM produces text for market_comparison_insight,
    Sprint 20 forces the no-market caption (Patch 4 empty-state)."""
    out = synthesize.run(
        _state(),
        client=_client_returning(_make_response(
            output=_make_synthesis_output(
                market_comparison_insight="Polymarket says 0.7"
            )
        )),
    )
    assert "no canonical" in out["synthesis_result"]["marketComparisonInsight"].lower()


# ==========================================================
# Evidence overlay merge
# ==========================================================
def test_evidence_overlay_merged_by_id_not_position():
    items = [
        _evidence_item(evidence_id="ev-A"),
        _evidence_item(evidence_id="ev-B"),
        _evidence_item(evidence_id="ev-C"),
    ]
    # Overlay in REVERSE order — must still merge correctly by id
    overlay = [
        {"evidence_id": "ev-C", "used_in_answer": True,
         "impact_on_forecast": "increases", "impact_magnitude": 0.9},
        {"evidence_id": "ev-A", "used_in_answer": True,
         "impact_on_forecast": "decreases", "impact_magnitude": 0.3},
        {"evidence_id": "ev-B", "used_in_answer": False,
         "impact_on_forecast": "neutral", "impact_magnitude": 0.0},
    ]
    out = synthesize.run(
        _state(evidence_trail=items),
        client=_client_returning(_make_response(
            output=_make_synthesis_output(evidence_overlay=overlay)
        )),
    )
    by_id = {item["evidence_id"]: item for item in out["evidence_trail"]}
    assert by_id["ev-C"]["impact_on_forecast"] == "increases"
    assert by_id["ev-C"]["impact_magnitude"] == 0.9
    assert by_id["ev-A"]["impact_on_forecast"] == "decreases"
    assert by_id["ev-B"]["used_in_answer"] is False


def test_overlay_entries_for_unknown_ids_silently_ignored():
    """If the model returns an overlay for an id we never sent, it's
    dropped (not raised). Synthesis still produces a forecast."""
    items = [_evidence_item(evidence_id="ev-real")]
    overlay = [
        {"evidence_id": "ev-real", "used_in_answer": True,
         "impact_on_forecast": "increases", "impact_magnitude": 0.5},
        {"evidence_id": "ev-fabricated", "used_in_answer": True,
         "impact_on_forecast": "increases", "impact_magnitude": 0.5},
    ]
    out = synthesize.run(
        _state(evidence_trail=items),
        client=_client_returning(_make_response(
            output=_make_synthesis_output(evidence_overlay=overlay)
        )),
    )
    # Only the real one is in the trail
    assert len(out["evidence_trail"]) == 1
    assert out["evidence_trail"][0]["evidence_id"] == "ev-real"


def test_items_missing_from_overlay_keep_default_influence_fields():
    """A model that returns a partial overlay leaves the other items
    with their rate_evidence-set defaults — they don't crash, they
    just don't get is_key_evidence."""
    items = [
        _evidence_item(evidence_id="ev-X"),
        _evidence_item(evidence_id="ev-Y"),
    ]
    overlay = [
        {"evidence_id": "ev-X", "used_in_answer": True,
         "impact_on_forecast": "increases", "impact_magnitude": 0.8},
    ]
    out = synthesize.run(
        _state(evidence_trail=items),
        client=_client_returning(_make_response(
            output=_make_synthesis_output(evidence_overlay=overlay)
        )),
    )
    by_id = {item["evidence_id"]: item for item in out["evidence_trail"]}
    assert by_id["ev-Y"]["used_in_answer"] is False
    assert by_id["ev-Y"]["impact_magnitude"] == 0.0


# ==========================================================
# Post-synthesis ranking (deterministic)
# ==========================================================
def test_top_5_used_items_by_impact_get_is_key_evidence():
    items = [_evidence_item(evidence_id=f"ev-{i}") for i in range(10)]
    # 7 items used; impact_magnitude varies
    overlay = [
        {"evidence_id": "ev-0", "used_in_answer": True, "impact_on_forecast": "increases", "impact_magnitude": 0.95},
        {"evidence_id": "ev-1", "used_in_answer": True, "impact_on_forecast": "increases", "impact_magnitude": 0.85},
        {"evidence_id": "ev-2", "used_in_answer": True, "impact_on_forecast": "decreases", "impact_magnitude": 0.75},
        {"evidence_id": "ev-3", "used_in_answer": True, "impact_on_forecast": "increases", "impact_magnitude": 0.65},
        {"evidence_id": "ev-4", "used_in_answer": True, "impact_on_forecast": "decreases", "impact_magnitude": 0.55},
        {"evidence_id": "ev-5", "used_in_answer": True, "impact_on_forecast": "neutral", "impact_magnitude": 0.45},
        {"evidence_id": "ev-6", "used_in_answer": True, "impact_on_forecast": "neutral", "impact_magnitude": 0.35},
        {"evidence_id": "ev-7", "used_in_answer": False, "impact_on_forecast": "context_only", "impact_magnitude": 0.0},
        {"evidence_id": "ev-8", "used_in_answer": False, "impact_on_forecast": "context_only", "impact_magnitude": 0.0},
        {"evidence_id": "ev-9", "used_in_answer": False, "impact_on_forecast": "context_only", "impact_magnitude": 0.0},
    ]
    out = synthesize.run(
        _state(evidence_trail=items),
        client=_client_returning(_make_response(
            output=_make_synthesis_output(evidence_overlay=overlay)
        )),
    )
    by_id = {item["evidence_id"]: item for item in out["evidence_trail"]}

    # Top 5 by impact_magnitude → is_key_evidence
    assert by_id["ev-0"]["is_key_evidence"] is True
    assert by_id["ev-4"]["is_key_evidence"] is True
    assert by_id["ev-5"]["is_key_evidence"] is False  # rank 6, just outside top 5
    assert by_id["ev-7"]["is_key_evidence"] is False  # not used


def test_rank_assigned_only_to_used_items_in_impact_order():
    items = [_evidence_item(evidence_id=f"ev-{i}") for i in range(4)]
    overlay = [
        {"evidence_id": "ev-0", "used_in_answer": True, "impact_on_forecast": "increases", "impact_magnitude": 0.5},
        {"evidence_id": "ev-1", "used_in_answer": True, "impact_on_forecast": "increases", "impact_magnitude": 0.9},
        {"evidence_id": "ev-2", "used_in_answer": False, "impact_on_forecast": "context_only", "impact_magnitude": 0.0},
        {"evidence_id": "ev-3", "used_in_answer": True, "impact_on_forecast": "increases", "impact_magnitude": 0.7},
    ]
    out = synthesize.run(
        _state(evidence_trail=items),
        client=_client_returning(_make_response(
            output=_make_synthesis_output(evidence_overlay=overlay)
        )),
    )
    by_id = {item["evidence_id"]: item for item in out["evidence_trail"]}
    # Ranks: ev-1 (impact 0.9) → 1, ev-3 (0.7) → 2, ev-0 (0.5) → 3, ev-2 (unused) → 0
    assert by_id["ev-1"]["rank"] == 1
    assert by_id["ev-3"]["rank"] == 2
    assert by_id["ev-0"]["rank"] == 3
    assert by_id["ev-2"]["rank"] == 0  # unused stays at default


def test_rank_tiebreaker_uses_relevance_score():
    """Two items with identical impact_magnitude should rank by
    relevance_score desc — pin determinism."""
    items = [
        _evidence_item(evidence_id="low-rel", relevance_score=0.4),
        _evidence_item(evidence_id="hi-rel", relevance_score=0.9),
    ]
    overlay = [
        {"evidence_id": "low-rel", "used_in_answer": True, "impact_on_forecast": "increases", "impact_magnitude": 0.5},
        {"evidence_id": "hi-rel", "used_in_answer": True, "impact_on_forecast": "increases", "impact_magnitude": 0.5},
    ]
    out = synthesize.run(
        _state(evidence_trail=items),
        client=_client_returning(_make_response(
            output=_make_synthesis_output(evidence_overlay=overlay)
        )),
    )
    by_id = {item["evidence_id"]: item for item in out["evidence_trail"]}
    assert by_id["hi-rel"]["rank"] == 1
    assert by_id["low-rel"]["rank"] == 2


# ==========================================================
# Cold-start: empty evidence_trail
# ==========================================================
def test_empty_evidence_trail_still_produces_session_result():
    """Spec cold-start: vault returned nothing, synthesis still runs
    and produces a (low-confidence) forecast."""
    low_conf_output = _make_synthesis_output(
        final_probability=0.5, confidence=0.3, consensus_score=0.2,
        what_i_didnt_find=["No vault evidence retrieved"],
    )
    out = synthesize.run(
        _state(),  # no evidence_trail key
        client=_client_returning(_make_response(output=low_conf_output)),
    )
    assert out["synthesis_result"]["confidence"] == 0.3
    assert out["synthesis_result"]["confidenceLabel"] == "Low"
    assert out["synthesis_result"]["evidenceVolumeLabel"] == "Low"
    assert "No vault evidence retrieved" in out["synthesis_result"]["whatIDidntFind"]


def test_empty_evidence_trail_does_not_skip_llm_call():
    """Cold-start case still calls the LLM (synthesis prompt handles
    empty evidence via Example 2). Don't accidentally short-circuit."""
    client = _client_returning(_make_response(output=_make_synthesis_output()))
    synthesize.run(_state(), client=client)
    client.chat.completions.create.assert_called_once()


# ==========================================================
# Input validation
# ==========================================================
@pytest.mark.parametrize("question", ["", "   ", None])
def test_run_raises_on_missing_raw_question(question):
    state = {"raw_question": question} if question is not None else {}
    client = MagicMock()
    with pytest.raises(AgentProcessingError, match="raw_question"):
        synthesize.run(state, client=client)
    # No LLM call when validation fails
    client.chat.completions.create.assert_not_called()


# ==========================================================
# OpenAI failure paths
# ==========================================================
def test_openai_sdk_raises_wrapped_in_agent_processing_error():
    client = MagicMock()
    client.chat.completions.create = MagicMock(side_effect=RuntimeError("rate limited"))
    with pytest.raises(AgentProcessingError, match="OpenAI call failed"):
        synthesize.run(_state(), client=client)


def test_malformed_response_shape_raises():
    bad_response = SimpleNamespace(choices=[], usage=SimpleNamespace(total_tokens=0))
    client = _client_returning(bad_response)
    with pytest.raises(AgentProcessingError, match="malformed"):
        synthesize.run(_state(), client=client)


def test_non_json_content_raises():
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))],
        usage=SimpleNamespace(total_tokens=0),
    )
    client = _client_returning(response)
    with pytest.raises(AgentProcessingError, match="response not JSON"):
        synthesize.run(_state(), client=client)


def test_non_object_root_raises():
    """LLM returns a JSON array instead of object → caught."""
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="[1, 2, 3]"))],
        usage=SimpleNamespace(total_tokens=0),
    )
    client = _client_returning(response)
    with pytest.raises(AgentProcessingError, match="response root is not an object"):
        synthesize.run(_state(), client=client)


# ==========================================================
# OpenAI call shape (regression guard)
# ==========================================================
def test_openai_call_uses_synthesis_model_and_strict_schema():
    client = _client_returning(_make_response(output=_make_synthesis_output()))
    synthesize.run(_state(), client=client)
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["response_format"] == {"type": "json_schema", "json_schema": RESPONSE_SCHEMA}
    assert kwargs["temperature"] == 0.0
    assert kwargs["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}


def test_openai_user_message_includes_question():
    client = _client_returning(_make_response(output=_make_synthesis_output()))
    synthesize.run(_state(raw_question="Will BTC hit 100k?"), client=client)
    user_msg = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "Will BTC hit 100k?" in user_msg


# ==========================================================
# Token / call-count accumulation
# ==========================================================
def test_llm_calls_count_incremented_by_one():
    out = synthesize.run(
        _state(llm_calls_count=2),
        client=_client_returning(_make_response(output=_make_synthesis_output())),
    )
    assert out["llm_calls_count"] == 3


def test_total_tokens_used_accumulates():
    out = synthesize.run(
        _state(total_tokens_used=100),
        client=_client_returning(_make_response(
            output=_make_synthesis_output(),
            total_tokens=2000,
        )),
    )
    assert out["total_tokens_used"] == 2100


# ==========================================================
# State surface
# ==========================================================
def test_top_level_state_fields_surfaced_alongside_session_result():
    """Synthesize writes key_factors, what_i_didnt_find, tier,
    confidence_score, evidence_trail (mutated) at top-level state too,
    not only inside synthesis_result."""
    out = synthesize.run(
        _state(),
        client=_client_returning(_make_response(output=_make_synthesis_output())),
    )
    assert "key_factors" in out
    assert "what_i_didnt_find" in out
    assert out["tier"] == "tier_1"
    assert out["confidence_score"] == out["synthesis_result"]["confidence"]
    assert "evidence_trail" in out
