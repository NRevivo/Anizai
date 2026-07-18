"""
Gate 2 subgraph integration tests for Sprint 21 — Tier 2 + Clarification
Flow (T21.10).

Runs the compiled LangGraph end-to-end with mocks at the system boundaries
(no real OpenAI, Firestore, or network calls). Tests the three Sprint 21
paths:

A. Clear Tier 1 auto-pick path (existing behavior, verified with Tier 1 data)
B. Ambiguous clarification path: ambiguous question → write_clarification →
   status=awaiting_clarification → graph ends (synthesis NOT reached)
C. Resume-on-clarify path: skip_matching_step=True + structured_intent
   pre-populated → query_understand early-returns → full forecast runs
D. Tier 2 freeform path: clear question with no polymarket market →
   tier_2 synthesis

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.2.3 (clarification flow)
    - data-pipeline/docs/agentic_hub_spec.md §8.3.2 (graph topology)
    - data-pipeline/docs/agentic_hub_implementation.md T21.10
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.graph import graph


# ==========================================================
# Shared builder helpers (mirrors test_graph_integration.py)
# ==========================================================

def _claim_payload(session_id="doc1", question="Will the Fed cut rates in 2026?", user_id="u1"):
    return {
        "queryId": session_id,
        "sessionId": session_id,
        "userId": user_id,
        "question": question,
        "status": "pending",
        "createdAt": MagicMock(),
        "claimedAt": None,
        "claimedBy": None,
    }


def _make_candidate(*, confidence=0.9, entities=None, search_terms=None,
                    too_broad=False, rejected=False):
    return {
        "intent": "forecast",
        "domain": "macro",
        "entities": entities or ["Federal Reserve"],
        "polymarket_search_terms": search_terms,
        "has_market_question_intent": True,
        "confidence": confidence,
        "too_broad": too_broad,
        "rejected": rejected,
    }


def _make_chat_response(*, candidates, total_tokens=120):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps({"candidates": candidates})
        ))],
        usage=SimpleNamespace(total_tokens=total_tokens),
    )


def _make_embedding_response(dim=1536, total_tokens=8):
    return SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.01] * dim)],
        usage=SimpleNamespace(total_tokens=total_tokens),
    )


def _make_synthesis_response(final_probability=0.7, confidence=0.65, total_tokens=1500):
    payload = {
        "final_probability": final_probability,
        "confidence": confidence,
        "consensus_score": 0.6,
        "bottom_line_answer": "Moderate chance.",
        "detailed_explanation": "Based on signals.",
        "summary_markdown": "## Summary\nModerate.",
        "market_comparison_insight": "Market suggests caution.",
        "sentiment_analysis_insight": "Mixed.",
        "evidence_feed_summary": "16 items analyzed.",
        "key_factors": [],
        "what_i_didnt_find": [],
        "reasoning_chain": [],
        "evidence_overlay": [],
    }
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
        usage=SimpleNamespace(total_tokens=total_tokens),
    )


def _make_rate_response():
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"ratings": []})))],
        usage=SimpleNamespace(total_tokens=50),
    )


@contextmanager
def _mocked_graph(qu_candidates=None, polymarket_market=None):
    """
    Context manager that patches all system boundaries for an end-to-end
    graph run with clean defaults.

    Args:
        qu_candidates: list of candidate dicts for query_understand to return
                       (default: single high-confidence clear candidate)
        polymarket_market: value for market_bridge to return as polymarket
                           (default: None → Tier 2)
    """
    if qu_candidates is None:
        qu_candidates = [_make_candidate(confidence=0.95)]

    with (
        patch("agent.firestore_client.claim_query") as mock_claim,
        patch("agent.firestore_client.update_session_status") as mock_status,
        patch("agent.firestore_client.update_query_status") as mock_query_status,
        patch("agent.firestore_client.write_session_result") as mock_write_result,
        patch("agent.firestore_client.write_evidence_batch") as mock_write_evidence,
        patch("agent.firestore_client.write_prediction_series") as mock_write_prediction,
        patch("agent.firestore_client.write_sentiment_time_series") as mock_write_sentiment,
        patch("agent.nodes.query_understand._get_default_client") as mock_qu_factory,
        patch("agent.nodes.build_embedding._get_default_client") as mock_emb_factory,
        patch("agent.nodes.rate_evidence._get_default_client") as mock_rate_factory,
        patch("agent.nodes.synthesize._get_default_client") as mock_synth_factory,
        patch("agent.nodes.generate_suggested_actions._get_default_client") as mock_sa_factory,
        patch("agent.agents.researcher.run") as mock_researcher,
        patch("agent.agents.pulse_analyst.run") as mock_pulse,
        patch("agent.agents.market_bridge.run") as mock_market,
    ):
        mock_claim.return_value = _claim_payload()
        mock_status.return_value = None
        mock_query_status.return_value = None
        mock_write_result.return_value = None
        mock_write_evidence.return_value = 0
        mock_write_prediction.return_value = 0
        mock_write_sentiment.return_value = 0

        qu_client = MagicMock()
        qu_client.chat.completions.create.return_value = _make_chat_response(
            candidates=qu_candidates
        )
        mock_qu_factory.return_value = qu_client

        emb_client = MagicMock()
        emb_client.embeddings.create.return_value = _make_embedding_response()
        mock_emb_factory.return_value = emb_client

        rate_client = MagicMock()
        rate_client.chat.completions.create.return_value = _make_rate_response()
        mock_rate_factory.return_value = rate_client

        synth_client = MagicMock()
        synth_client.chat.completions.create.return_value = _make_synthesis_response()
        mock_synth_factory.return_value = synth_client

        # Sprint 25: generate_suggested_actions (Node 6.5) — valid 3-action
        # response so the node runs its success path hermetically.
        sa_client = MagicMock()
        sa_client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                "actions": [
                    {"label": "Why so confident?", "prompt": "What drives the confidence?"},
                    {"label": "Strongest driver", "prompt": "Which evidence mattered most?"},
                    {"label": "Compare to the market", "prompt": "How does this compare to the market?"},
                ]
            })))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        )
        mock_sa_factory.return_value = sa_client

        mock_researcher.return_value = {
            "articles": [], "source_diversity": {}, "recency_range": None, "empty": True
        }
        mock_pulse.return_value = {
            "market_consensus": [], "community_discussion": [],
            "overall_sentiment": 0.0, "empty": True,
        }
        mock_market.return_value = {
            "polymarket": polymarket_market,
            "linked_sources": [], "fred_anomalies": [], "google_trends": [], "empty": True,
        }

        yield SimpleNamespace(
            status=mock_status,
            query_status=mock_query_status,
            write_result=mock_write_result,
            researcher=mock_researcher,
            pulse=mock_pulse,
            market=mock_market,
        )


# ==========================================================
# Path A — Clear Tier 1 auto-pick (with polymarket data → tier_1)
# ==========================================================

def test_gate2_clear_tier1_path_with_polymarket_data(mock_reactive_producer):
    """Clear question + polymarket data → tier_1 synthesis, full pipeline."""
    polymarket_data = {"current_odds": 0.72, "market_slug": "fed-cut-q2-2026"}
    with _mocked_graph(
        qu_candidates=[_make_candidate(confidence=0.95)],
        polymarket_market=polymarket_data,
    ) as mocks:
        final = graph.invoke({"session_id": "doc1"})

    assert "synthesis_result" in final
    assert final["synthesis_result"]["tier"] == "tier_1"
    assert final["tier"] == "tier_1"
    mocks.researcher.assert_called_once()
    mocks.pulse.assert_called_once()
    mocks.market.assert_called_once()
    mocks.write_result.assert_called_once()
    statuses = [c.args[1] for c in mocks.status.call_args_list]
    assert "done" in statuses
    assert "awaiting_clarification" not in statuses


# ==========================================================
# Path B — Ambiguous clarification path
# ==========================================================

def test_gate2_ambiguous_routes_to_write_clarification():
    """Ambiguous question → write_clarification fires → graph ends early."""
    with _mocked_graph(
        qu_candidates=[
            _make_candidate(confidence=0.78, entities=["Fed Funds Rate"]),
            _make_candidate(confidence=0.74, entities=["Fed Balance Sheet"]),
        ]
    ) as mocks:
        final = graph.invoke({"session_id": "doc1"})

    assert final["awaiting_clarification"] is True
    # Graph ended at write_clarification — synthesis never ran.
    assert "synthesis_result" not in final or final.get("synthesis_result") is None
    mocks.researcher.assert_not_called()
    mocks.write_result.assert_not_called()


def test_gate2_clarification_candidates_are_shaped():
    """clarificationCandidates written to Firestore have the shaped format."""
    with _mocked_graph(
        qu_candidates=[
            _make_candidate(confidence=0.78, entities=["Bitcoin"]),
            _make_candidate(confidence=0.74, entities=["Ethereum"]),
        ]
    ) as mocks:
        graph.invoke({"session_id": "doc1"})

    # find the awaiting_clarification call to update_session_status
    clarification_call = None
    for c in mocks.status.call_args_list:
        if c.args[1] == "awaiting_clarification":
            clarification_call = c
            break
    assert clarification_call is not None

    candidates = clarification_call.kwargs.get("clarification_candidates")
    assert candidates is not None
    assert len(candidates) == 2
    # Each candidate must have the ClarificationCandidate fields
    for c in candidates:
        assert "id" in c
        assert "label" in c
        assert c["source"] == "polymarket"
        assert "matchConfidence" in c
        # KG-B-8 (Sprint 26 T26.2, Gate 2): the strip survives the full
        # query_understand → write_clarification → Firestore path — ONLY the 5
        # spec fields reach the write; no hub-internal fields leak.
        assert set(c.keys()) == {"id", "label", "source", "description", "matchConfidence"}
        for leaked in ("intent", "domain", "entities", "polymarket_search_terms"):
            assert leaked not in c


def test_gate2_ambiguous_writes_awaiting_clarification_on_both_docs():
    """Both session doc and forecastQueries doc get awaiting_clarification."""
    with _mocked_graph(
        qu_candidates=[
            _make_candidate(confidence=0.78),
            _make_candidate(confidence=0.74),
        ]
    ) as mocks:
        graph.invoke({"session_id": "doc1"})

    session_statuses = [c.args[1] for c in mocks.status.call_args_list]
    query_statuses = [c.args[1] for c in mocks.query_status.call_args_list]
    assert "awaiting_clarification" in session_statuses
    assert "awaiting_clarification" in query_statuses


# ==========================================================
# Path C — Resume-on-clarify (skip_matching_step=True)
# ==========================================================

def test_gate2_resume_on_clarify_skips_llm_call_and_completes_forecast(mock_reactive_producer):
    """skip_matching_step=True + structured_intent pre-populated → no LLM call
    in query_understand, full forecast runs and produces synthesis_result."""
    resume_state = {
        "session_id": "doc1",
        "skip_matching_step": True,
        "chosen_candidate_id": "uuid-fed-rate",
        "structured_intent": {
            "intent": "forecast",
            "domain": "macro",
            "entities": ["Federal Reserve"],
            "polymarket_search_terms": ["fed rate cut"],
            "has_market_question_intent": True,
            "confidence": 0.82,
            "too_broad": False,
            "rejected": False,
        },
    }
    with _mocked_graph() as mocks:
        final = graph.invoke(resume_state)

    # Full forecast completed.
    assert "synthesis_result" in final
    assert final["awaiting_clarification"] is False
    # query_understand did NOT call the LLM (no qu_client mock interaction).
    # Evidence pipeline ran.
    mocks.researcher.assert_called_once()
    mocks.write_result.assert_called_once()
    statuses = [c.args[1] for c in mocks.status.call_args_list]
    assert "done" in statuses


def test_gate2_resume_on_clarify_null_means_tier2_freeform():
    """skip_matching_step=True + chosen_candidate_id=None → Tier 2 synthesis."""
    resume_state = {
        "session_id": "doc1",
        "skip_matching_step": True,
        "chosen_candidate_id": None,
        "structured_intent": {
            "intent": "forecast",
            "domain": "general",
            "entities": [],
            "polymarket_search_terms": None,
            "has_market_question_intent": False,
            "confidence": 0.5,
            "too_broad": False,
            "rejected": False,
        },
    }
    with _mocked_graph() as mocks:
        final = graph.invoke(resume_state)

    # No polymarket in market_bridge mock → tier_2
    assert final.get("tier") == "tier_2"
    assert final["synthesis_result"]["tier"] == "tier_2"
    mocks.write_result.assert_called_once()


# ==========================================================
# Path D — Tier 2 freeform (no polymarket, first-time run)
# ==========================================================

def test_gate2_tier2_freeform_clear_question_produces_tier2_synthesis(mock_reactive_producer):
    """Clear but non-Polymarket question → single candidate, auto-pick,
    market_bridge returns polymarket=None → tier_2 synthesis."""
    with _mocked_graph(
        qu_candidates=[
            _make_candidate(
                confidence=0.90,
                entities=["Tokyo Earthquake"],
                search_terms=None,  # no polymarket terms
            )
        ],
        polymarket_market=None,  # no polymarket data
    ) as mocks:
        final = graph.invoke({"session_id": "doc1"})

    assert "synthesis_result" in final
    assert final["synthesis_result"]["tier"] == "tier_2"
    assert final["synthesis_result"]["marketComparisonInsight"] == (
        "No canonical market available — freeform analysis."
    )
    mocks.write_result.assert_called_once()
    statuses = [c.args[1] for c in mocks.status.call_args_list]
    assert "done" in statuses


def test_gate2_tier2_marketprobability_is_null(mock_reactive_producer):
    """Tier 2 SessionResult must have marketProbability=None."""
    with _mocked_graph(polymarket_market=None) as mocks:
        final = graph.invoke({"session_id": "doc1"})

    assert final["synthesis_result"]["marketProbability"] is None
    assert final["synthesis_result"]["marketComparison"] == []
