"""
Sprint 22 T22.9 — Gate 2 subgraph integration tests.

Exercises the four-node chain vault_query → rate_evidence → synthesize →
write_to_firestore against mocked persistence-layer wrappers and mocked
OpenAI clients. All Sprint 22 wiring (T22.1 resolver → T22.2 market_bridge
fuzzy match → T22.3 marketProbability + marketComparison → T22.4
predictionSeries adapter → T22.5/T22.6 sentiment bucketing + Expert/Public
merge + scale normalization → T22.7 canonicalKey on session doc) flows
through one real codepath.

Why a manual chain (not the compiled langgraph):
    The full forecast graph has 7 nodes. Sprint 22 touches the last 4.
    Running claim_session + query_understand + build_embedding would just
    add 3 mock surfaces with no coverage benefit. The manual chain pre-
    populates state with what the upstream nodes would have produced and
    invokes the affected nodes directly.

Why mocks at the persistence-tool boundary (not the live DB):
    `agent/tools/*` is the architecturally pinned boundary
    (CLAUDE.md §3.3). Mocking there exercises every line of agent code
    (the agents, the rating, the synthesis prompt builder, the
    write_to_firestore adapters) without the DB. Live DB belongs in
    T22.11 E2E.

Coverage map (3 tests):
    1. Tier 1 — full happy path with resolver hit. All 5 BI cards
       populated correctly; session doc gets canonical_key=market_slug
       and tier=tier_1.
    2. Tier 2 — resolver miss path with has_market_question_intent=False.
       BI cards in Tier 2 empty/null state. Session doc gets
       canonical_key=None and tier=tier_2.
    3. Tier 1-intent → resolver miss → Tier 2 fallback. State has
       has_market_question_intent=True (so market_bridge tries the
       resolver) but the resolver returns None (no row passed threshold).
       Verifies the Tier 2 fallback wiring end-to-end.

References:
    - data-pipeline/docs/agentic_hub_implementation_phase8_revised.md
      §Sprint 22 T22.9
    - tests/test_agent/test_emulator_integration.py (sibling Gate 3
      pattern this file mirrors structure-wise, sans emulator)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import partial
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.nodes import (
    rate_evidence as rate_evidence_node,
    synthesize as synthesize_node,
    vault_query as vault_query_node,
    write_to_firestore as write_to_firestore_node,
)


# ==========================================================
# Fixed reference instant — every test injects this `now`
# ==========================================================
NOW = datetime(2026, 5, 23, 14, 0, 0, tzinfo=timezone.utc)


# ==========================================================
# Helpers — mock vault rows
# ==========================================================

def _knowledge_row(
    *,
    signal_id: str,
    title: str,
    sentiment_score: float,
    published_at: datetime,
    impact_level: int = 3,
    reliability_score: float = 0.7,
    similarity: float = 0.7,
    source_platform: str = "newsapi",
) -> dict:
    """Mirror the knowledge_vectors row shape researcher.run reads."""
    return {
        "signal_id": signal_id,
        "source_platform": source_platform,
        "similarity": similarity,
        "published_at": published_at,
        "canonical_event_id": "",
        "silver_data_ref": None,
        "content_vitals": {
            "title": title,
            "url": f"https://reuters.com/{signal_id}",
        },
        "enrichment_ai": {
            "impact_level": impact_level,
            "reliability_score": reliability_score,
            "sentiment_score": sentiment_score,
            "executive_summary": f"Summary of {signal_id}.",
            "key_findings": [],
        },
    }


def _hackernews_row(
    *,
    signal_id: str,
    community_sentiment: float,
    published_at: datetime,
    points: int = 50,
    similarity: float = 0.6,
) -> dict:
    """Mirror the social_vectors row shape pulse_analyst._pack_hackernews reads."""
    return {
        "signal_id": signal_id,
        "source_platform": "hackernews",
        "similarity": similarity,
        "published_at": published_at,
        "silver_data_ref": None,
        "content_vitals": {
            "title": f"HN discussion {signal_id}",
            "url": f"https://news.ycombinator.com/item?id={signal_id}",
        },
        "platform_logic": {
            "points": points,
            "top_technical_insights": [],
            "community_sentiment": community_sentiment,
        },
    }


def _polymarket_consensus_row(
    *,
    signal_id: str = "pm-consensus-1",
    consensus_rating: float = 0.6,
    published_at: datetime | None = None,
) -> dict:
    """Mirror the social_vectors row shape for the Polymarket consensus path
    (so pulse_analyst exercises both source_platforms during the run)."""
    return {
        "signal_id": signal_id,
        "source_platform": "polymarket",
        "similarity": 0.55,
        "published_at": published_at or NOW,
        "silver_data_ref": None,
        "content_vitals": {"title": "Polymarket consensus"},
        "platform_logic": {
            "consensus_rating": consensus_rating,
            "comment_volume_analyzed": 50,
            "aggregation_window_hours": 24,
            "market_id_ref": "0xpoly_market",
        },
        "enrichment_ai": {"executive_summary": "Polymarket consensus."},
    }


def _resolver_market_row(
    *,
    external_reference_id: str = "0xfed_condition_id",
    current_value: float = 0.62,
) -> dict:
    """Resolver-shaped row — exactly what T22.1's
    find_polymarket_market_by_question returns on hit (see
    test_momentum_vault_resolver.py)."""
    return {
        "metric_id": "pm-resolver-1",
        "canonical_event_id": "ev-resolver",
        "source_name": "polymarket",
        "external_reference_id": external_reference_id,
        "current_value": current_value,
        "unit": "probability",
        "status": "active",
        "timestamp_utc": NOW,
        "change_24h": 0.03,
        "change_7d": 0.07,
        "change_30d": 0.12,
        "is_new_market": False,
        "metadata_extension": {
            "question": "Will the Fed cut rates before June 2026?",
            "whale_alert": False,
        },
        "ingested_at": NOW,
        "match_score": 0.93,
    }


def _price_history_row(*, timestamp: datetime, value: float) -> dict:
    """One momentum_vault row in the 720-hour history."""
    return {
        "metric_id": f"pm-history-{timestamp.isoformat()}",
        "source_name": "polymarket",
        "external_reference_id": "0xfed_condition_id",
        "current_value": value,
        "unit": "probability",
        "timestamp_utc": timestamp,
        "change_24h": 0.0,
        "change_7d": 0.0,
        "change_30d": 0.0,
        "is_new_market": False,
        "metadata_extension": {"whale_alert": False},
    }


# ==========================================================
# Helpers — mock OpenAI responses
# ==========================================================

def _rate_evidence_response(evidence_items: list[dict]) -> SimpleNamespace:
    """
    Build a rate_evidence-shaped response that rates every item with
    relevance_score=0.8, justification="relevant". Items are matched by
    evidence_id (rate_evidence._apply_ratings).
    """
    payload = {
        "ratings": [
            {
                "evidence_id": item["evidence_id"],
                "relevance_score": 0.8,
                "justification": "relevant",
            }
            for item in evidence_items
        ],
    }
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
        usage=SimpleNamespace(total_tokens=200),
    )


def _synthesis_response(evidence_items: list[dict]) -> SimpleNamespace:
    """Build a valid synthesis_lead-shaped response."""
    payload = {
        "final_probability": 0.72,
        "confidence": 0.65,
        "consensus_score": 0.6,
        "bottom_line_answer": "Likely yes.",
        "detailed_explanation": "Detailed reasoning here.",
        "summary_markdown": "**Forecast:** likely yes.",
        "market_comparison_insight": "Anizai 0.72 vs Polymarket 0.62 — agent more bullish.",
        "sentiment_analysis_insight": "Sentiment positive overall.",
        "evidence_feed_summary": "Reviewed evidence.",
        "what_i_didnt_find": [],
        "key_factors": [
            {"label": "Factor A", "description": "Drives up.",
             "weight": 0.4, "direction": "increases", "evidence_ids": []},
            {"label": "Factor B", "description": "Mild down.",
             "weight": 0.2, "direction": "decreases", "evidence_ids": []},
            {"label": "Factor C", "description": "Drives up.",
             "weight": 0.3, "direction": "increases", "evidence_ids": []},
        ],
        "reasoning_chain": [
            {"step": 1, "title": "Identify question",
             "description": "Parsed the resolution criterion."},
            {"step": 2, "title": "Review evidence",
             "description": "Evaluated retrieved evidence."},
            {"step": 3, "title": "Weigh factors",
             "description": "Identified the key drivers."},
            {"step": 4, "title": "Produce forecast",
             "description": "Calibrated final probability."},
        ],
        "evidence_overlay": [
            {
                "evidence_id": item["evidence_id"],
                "used_in_answer": True,
                "impact_on_forecast": "increases",
                "impact_magnitude": 0.5,
            }
            for item in evidence_items
        ],
    }
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
        usage=SimpleNamespace(total_tokens=2000),
    )


# ==========================================================
# Helpers — initial state + firestore mocks
# ==========================================================

def _initial_state(*, has_market_intent: bool, raw_question: str) -> dict:
    """
    Pre-populated state — what claim_session + query_understand +
    build_embedding would have produced before vault_query runs.
    """
    return {
        "session_id": "session-gate2",
        "raw_question": raw_question,
        "structured_intent": {
            "intent": "forecast",
            "domain": "macro",
            "entities": ["Federal Reserve"],
            "polymarket_search_terms": None,
            "has_market_question_intent": has_market_intent,
            "confidence": 0.9,
            "too_broad": False,
            "rejected": False,
        },
        "query_embedding": [0.01] * 1536,
        "llm_calls_count": 0,
        "total_tokens_used": 0,
        "errors": [],
    }


@pytest.fixture
def mocked_firestore_helpers():
    """
    Patch every firestore_client helper invoked by write_to_firestore.run.
    Yields the parent manager (for ordered cross-mock call inspection) plus
    each individual mock for per-call-args assertions.
    """
    with (
        patch("agent.nodes.write_to_firestore.firestore_client.write_evidence_batch") as ev,
        patch("agent.nodes.write_to_firestore.firestore_client.write_prediction_series") as ps,
        patch("agent.nodes.write_to_firestore.firestore_client.write_sentiment_time_series") as sts,
        patch("agent.nodes.write_to_firestore.firestore_client.write_session_result") as sr,
        patch("agent.nodes.write_to_firestore.firestore_client.update_session_status") as ss,
        patch("agent.nodes.write_to_firestore.firestore_client.update_query_status") as qs,
    ):
        ev.return_value = 0
        ps.return_value = 0
        sts.return_value = 0
        manager = MagicMock()
        manager.attach_mock(ev, "evidence")
        manager.attach_mock(ps, "prediction_series")
        manager.attach_mock(sts, "sentiment_time_series")
        manager.attach_mock(sr, "session_result")
        manager.attach_mock(ss, "session_status")
        manager.attach_mock(qs, "query_status")
        yield manager, ev, ps, sts, sr, ss, qs


def _run_subgraph(
    state: dict,
    *,
    rate_client: MagicMock,
    synth_client: MagicMock,
) -> dict:
    """
    Run the four-node chain in order. Each node returns a partial state
    dict; merge into the running state and pass to the next node.
    write_to_firestore is a sink — its return is an identity echo.
    """
    state.update(vault_query_node.run(state, now=NOW))
    state.update(rate_evidence_node.run(state, client=rate_client))
    state.update(synthesize_node.run(state, client=synth_client))
    # write_to_firestore.run() anchors its SentimentTimeSeries bucketing to
    # real UTC now (production-correct — there is no clock injection in prod).
    # Every other node in this subgraph is anchored to the fixed reference
    # instant NOW, and the evidence fixtures carry NOW-relative dates. Inject
    # NOW into the sentiment shaper here so the test stays deterministic:
    # without it the hard-coded May-2026 fixture dates age out of the 14-day
    # bucketing window once the suite is run >14 days later, and
    # SentimentTimeSeries silently comes back empty. (Sprint 23.5 Disposition D
    # — the true root cause of this red was time-drift, not missing seed data.)
    with patch.object(
        write_to_firestore_node,
        "_shape_sentiment_time_series",
        partial(write_to_firestore_node._shape_sentiment_time_series, now=NOW),
    ):
        write_to_firestore_node.run(state)
    return state


# ==========================================================
# Test 1 — Tier 1 full happy path
# ==========================================================

def test_tier_1_subgraph_all_five_cards_populated(mocked_firestore_helpers):
    """
    Resolver hit + real evidence shapes flowing through every Sprint 22
    code path. Every BI card's data lands in the right call_args.
    """
    _, ev, ps, sts, sr, ss, qs = mocked_firestore_helpers

    # 2 researcher articles, both inside the bucketing window.
    knowledge_rows = [
        _knowledge_row(
            signal_id="news-a",
            title="Fed signals dovish",
            sentiment_score=0.4,
            published_at=datetime(2026, 5, 21, 10, 0, 0, tzinfo=timezone.utc),
        ),
        _knowledge_row(
            signal_id="news-b",
            title="Inflation cooling",
            sentiment_score=0.6,
            published_at=datetime(2026, 5, 22, 10, 0, 0, tzinfo=timezone.utc),
        ),
    ]
    # 1 hackernews discussion + 1 polymarket consensus.
    social_rows = [
        _hackernews_row(
            signal_id="hn-a",
            community_sentiment=0.3,
            published_at=datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc),
        ),
        _polymarket_consensus_row(),
    ]
    # Polymarket resolver row + 3-point 720-hour price history.
    resolver_row = _resolver_market_row(current_value=0.62)
    price_history = [
        _price_history_row(
            timestamp=datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc),
            value=0.55,
        ),
        _price_history_row(
            timestamp=datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc),
            value=0.60,
        ),
        _price_history_row(
            timestamp=datetime(2026, 5, 23, 12, 0, 0, tzinfo=timezone.utc),
            value=0.62,
        ),
    ]

    state = _initial_state(
        has_market_intent=True,
        raw_question="Will the Fed cut rates before June 2026?",
    )

    rate_client = MagicMock()
    synth_client = MagicMock()

    with (
        patch(
            "agent.tools.knowledge_tools.similarity_search",
            return_value=knowledge_rows,
        ),
        patch("agent.tools.knowledge_tools.fetch_full_text", return_value=None),
        patch(
            "agent.tools.social_tools.similarity_search",
            return_value=social_rows,
        ),
        patch("agent.tools.social_tools.fetch_raw_comments", return_value=[]),
        patch(
            "agent.tools.market_tools.find_polymarket_market_by_question",
            return_value=resolver_row,
        ),
        patch(
            "agent.tools.market_tools.fetch_time_series",
            return_value=price_history,
        ),
        patch("agent.tools.market_tools.fetch_fred_anomalies", return_value=[]),
        patch("agent.tools.market_tools.fetch_latest", return_value=None),
        patch("agent.tools.mapping_tools.lookup_by_canonical", return_value=[]),
    ):
        # Rate-evidence response can be deferred until items are known.
        # Trick: configure the mock client lazily via side_effect.
        def _rate_create(**kwargs):
            user_msg = kwargs["messages"][1]["content"]
            # rate_evidence's user message lists items by evidence_id —
            # parse them so the response covers exactly the items we got.
            items = [
                {"evidence_id": eid}
                for eid in _extract_evidence_ids_from_rate_prompt(user_msg)
            ]
            return _rate_evidence_response(items)
        rate_client.chat.completions.create.side_effect = _rate_create

        def _synth_create(**kwargs):
            user_msg = kwargs["messages"][1]["content"]
            items = [
                {"evidence_id": eid}
                for eid in _extract_evidence_ids_from_synth_prompt(user_msg)
            ]
            return _synthesis_response(items)
        synth_client.chat.completions.create.side_effect = _synth_create

        state = _run_subgraph(state, rate_client=rate_client, synth_client=synth_client)

    # ----- PredictionOverview card -----
    result = sr.call_args.args[1]
    assert isinstance(result["finalProbability"], float)
    assert 0.0 <= result["finalProbability"] <= 1.0
    assert result["confidenceLabel"] in {"Low", "Moderate", "High"}
    assert result["consensusStrength"] in {"Weak", "Mixed", "Strong"}
    assert result["evidenceVolumeLabel"] in {"Low", "Moderate", "High"}
    assert 3 <= len(result["keyFactors"]) <= 5

    # ----- MarketComparison card -----
    assert result["marketProbability"] == pytest.approx(0.62)
    assert result["marketComparison"] == [
        {"label": "Anizai", "value": pytest.approx(0.72)},
        {"label": "Polymarket", "value": pytest.approx(0.62)},
    ]
    # The NO_MARKET_CAPTION must NOT fire when polymarket is populated.
    assert result["marketComparisonInsight"] != synthesize_node.NO_MARKET_CAPTION
    assert result["tier"] == "tier_1"

    # ----- EvidenceTimeline card -----
    evidence_docs = ev.call_args.args[1]
    assert len(evidence_docs) >= 1
    for doc in evidence_docs:
        assert "type" in doc  # SOURCE_TYPE_TO_FRONTEND_TYPE mapping fired
        assert "relevance_score" in doc

    # ----- PredictionSeries card -----
    prediction_docs = ps.call_args.args[1]
    assert len(prediction_docs) == 3
    for doc in prediction_docs:
        # T22.4 contract: ts is tz-aware datetime, not ISO string.
        assert isinstance(doc["ts"], datetime)
        assert doc["ts"].tzinfo is not None
        assert isinstance(doc["probability"], float)
        assert doc["confidence"] == 1.0
        assert doc["reasonType"] == "market"
        assert doc["evidenceIds"] == []

    # ----- SentimentAnalysis card -----
    sentiment_docs = sts.call_args.args[1]
    assert len(sentiment_docs) >= 1
    for doc in sentiment_docs:
        assert isinstance(doc["ts"], datetime)
        assert doc["ts"].tzinfo is not None
        # date is ISO YYYY-MM-DD
        assert len(doc["date"]) == 10
        assert doc["date"][4] == "-" and doc["date"][7] == "-"
        # Either side may be None (a1), but populated values must be
        # in the FE's [0, 1] scale (T22.6 normalization end-to-end).
        for side in ("expertSentiment", "publicSentiment"):
            if doc[side] is not None:
                assert 0.0 <= doc[side] <= 1.0
        # Confidence bands stay null in V1.
        assert doc["expertUpper"] is None
        assert doc["expertLower"] is None

    # ----- Session doc — tier + canonicalKey (T22.7) -----
    ss.assert_called_once_with(
        "session-gate2", "done",
        tier="tier_1",
        canonical_key="0xfed_condition_id",
    )
    qs.assert_called_once_with("session-gate2", "done")


# ==========================================================
# Test 2 — Tier 2 happy path (no market intent)
# ==========================================================

def test_tier_2_subgraph_no_market_empty_states(mocked_firestore_helpers):
    """
    has_market_question_intent=False → market_bridge skips the resolver
    entirely (T22.2 intent guard). polymarket payload is None throughout.
    All BI cards in Tier 2 empty/null state.
    """
    _, ev, ps, sts, sr, ss, qs = mocked_firestore_helpers

    knowledge_rows = [
        _knowledge_row(
            signal_id="news-a",
            title="General economic news",
            sentiment_score=0.2,
            published_at=datetime(2026, 5, 22, 10, 0, 0, tzinfo=timezone.utc),
        ),
    ]
    social_rows = [
        _hackernews_row(
            signal_id="hn-a",
            community_sentiment=-0.1,
            published_at=datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc),
        ),
    ]

    state = _initial_state(
        has_market_intent=False,
        raw_question="Why is the economy slowing?",
    )

    rate_client = MagicMock()
    synth_client = MagicMock()

    with (
        patch(
            "agent.tools.knowledge_tools.similarity_search",
            return_value=knowledge_rows,
        ),
        patch("agent.tools.knowledge_tools.fetch_full_text", return_value=None),
        patch(
            "agent.tools.social_tools.similarity_search",
            return_value=social_rows,
        ),
        patch("agent.tools.social_tools.fetch_raw_comments", return_value=[]),
        patch(
            "agent.tools.market_tools.find_polymarket_market_by_question",
        ) as mock_resolver,
        patch("agent.tools.market_tools.fetch_time_series", return_value=[]),
        patch("agent.tools.market_tools.fetch_fred_anomalies", return_value=[]),
        patch("agent.tools.market_tools.fetch_latest", return_value=None),
        patch("agent.tools.mapping_tools.lookup_by_canonical", return_value=[]),
    ):
        def _rate_create(**kwargs):
            items = [{"evidence_id": eid}
                     for eid in _extract_evidence_ids_from_rate_prompt(kwargs["messages"][1]["content"])]
            return _rate_evidence_response(items)
        rate_client.chat.completions.create.side_effect = _rate_create
        def _synth_create(**kwargs):
            items = [{"evidence_id": eid}
                     for eid in _extract_evidence_ids_from_synth_prompt(kwargs["messages"][1]["content"])]
            return _synthesis_response(items)
        synth_client.chat.completions.create.side_effect = _synth_create

        state = _run_subgraph(state, rate_client=rate_client, synth_client=synth_client)

        # T22.2 intent guard: resolver MUST NOT be called.
        mock_resolver.assert_not_called()

    result = sr.call_args.args[1]
    # MarketComparison: Tier 2 empty state with canonical caption.
    assert result["marketProbability"] is None
    assert result["marketComparison"] == []
    assert result["marketComparisonInsight"] == synthesize_node.NO_MARKET_CAPTION
    assert result["tier"] == "tier_2"

    # PredictionSeries empty (no polymarket payload → no price_history).
    assert ps.call_args.args[1] == []

    # SentimentAnalysis may still emit docs from the researcher + hackernews
    # evidence (Sprint 22's bucketing operates on agent-retrieved evidence,
    # independent of tier). a1 logic: docs emitted when at least one source
    # has data. Range check still applies.
    sentiment_docs = sts.call_args.args[1]
    for doc in sentiment_docs:
        for side in ("expertSentiment", "publicSentiment"):
            if doc[side] is not None:
                assert 0.0 <= doc[side] <= 1.0

    # Session doc: tier_2 + canonical_key=None (T22.7).
    ss.assert_called_once_with(
        "session-gate2", "done",
        tier="tier_2",
        canonical_key=None,
    )


# ==========================================================
# Test 3 — Tier 1 intent but resolver misses → Tier 2 fallback
# ==========================================================

def test_subgraph_resolver_miss_falls_back_to_tier_2_cleanly(mocked_firestore_helpers):
    """
    has_market_question_intent=True but the resolver returns None (no row
    passed the 0.85 threshold). The fuzzy-match path runs, finds nothing,
    and the agent gracefully degrades to Tier 2 — same SessionResult
    shape as test 2, but with the resolver DOM exercised.
    """
    _, ev, ps, sts, sr, ss, qs = mocked_firestore_helpers

    knowledge_rows = [
        _knowledge_row(
            signal_id="news-a",
            title="Obscure event",
            sentiment_score=0.1,
            published_at=datetime(2026, 5, 22, 10, 0, 0, tzinfo=timezone.utc),
        ),
    ]
    social_rows = []  # Pulse empty for this case to keep the test tight.

    state = _initial_state(
        has_market_intent=True,
        raw_question="Will some obscure event happen by 2030?",
    )

    rate_client = MagicMock()
    synth_client = MagicMock()

    with (
        patch(
            "agent.tools.knowledge_tools.similarity_search",
            return_value=knowledge_rows,
        ),
        patch("agent.tools.knowledge_tools.fetch_full_text", return_value=None),
        patch(
            "agent.tools.social_tools.similarity_search",
            return_value=social_rows,
        ),
        patch("agent.tools.social_tools.fetch_raw_comments", return_value=[]),
        patch(
            "agent.tools.market_tools.find_polymarket_market_by_question",
            return_value=None,   # resolver miss
        ) as mock_resolver,
        patch("agent.tools.market_tools.fetch_time_series", return_value=[]),
        patch("agent.tools.market_tools.fetch_fred_anomalies", return_value=[]),
        patch("agent.tools.market_tools.fetch_latest", return_value=None),
        patch("agent.tools.mapping_tools.lookup_by_canonical", return_value=[]),
    ):
        def _rate_create(**kwargs):
            items = [{"evidence_id": eid}
                     for eid in _extract_evidence_ids_from_rate_prompt(kwargs["messages"][1]["content"])]
            return _rate_evidence_response(items)
        rate_client.chat.completions.create.side_effect = _rate_create
        def _synth_create(**kwargs):
            items = [{"evidence_id": eid}
                     for eid in _extract_evidence_ids_from_synth_prompt(kwargs["messages"][1]["content"])]
            return _synthesis_response(items)
        synth_client.chat.completions.create.side_effect = _synth_create

        state = _run_subgraph(state, rate_client=rate_client, synth_client=synth_client)

        # Resolver MUST be called (intent is True) but returned None.
        mock_resolver.assert_called_once()

    # Same Tier-2-like result shape as test 2 — fallback works.
    result = sr.call_args.args[1]
    assert result["marketProbability"] is None
    assert result["marketComparison"] == []
    assert result["marketComparisonInsight"] == synthesize_node.NO_MARKET_CAPTION
    assert result["tier"] == "tier_2"

    # predictionSeries empty (no polymarket → no price_history).
    assert ps.call_args.args[1] == []

    # Session doc tier_2 + canonical_key=None.
    ss.assert_called_once_with(
        "session-gate2", "done",
        tier="tier_2",
        canonical_key=None,
    )


# ==========================================================
# Internal helpers — pull evidence_ids out of LLM user messages
# ==========================================================

def _extract_evidence_ids_from_rate_prompt(user_msg: str) -> list[str]:
    """
    rate_evidence's user message has lines like `evidence_id: <uuid>`.
    Pulling them lets the mock client construct a matched response
    without knowing the upstream-assigned IDs ahead of time.
    """
    return _grep_evidence_ids(user_msg)


def _extract_evidence_ids_from_synth_prompt(user_msg: str) -> list[str]:
    """Synthesis prompt has evidence_id lines too — same extraction."""
    return _grep_evidence_ids(user_msg)


def _grep_evidence_ids(text: str) -> list[str]:
    """
    Tolerant best-effort: scan the prompt for `evidence_id` followed by
    a UUID-like or hex-like token on the same line. Returns a unique
    ordered list. The exact prompt-line format may differ between
    rate_evidence and synthesize prompts — the regex handles both.
    """
    import re
    pattern = re.compile(
        r"evidence[_-]?id\s*[:=]\s*\"?([A-Za-z0-9_\-]{6,64})\"?",
        re.IGNORECASE,
    )
    seen = set()
    out: list[str] = []
    for match in pattern.findall(text):
        if match not in seen:
            seen.add(match)
            out.append(match)
    return out
