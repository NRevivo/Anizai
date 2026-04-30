"""
Gate 1 unit tests for agent/agents/pulse_analyst.py (Sprint 19 T19.3).

Pure-mock tests: patch agent.tools.social_tools at the pulse_analyst's
import binding site. No DB, no real embeddings.

Coverage map (spec §8.4.2):
    - Step 1: spec args forwarded explicitly (limit=10, no filters)
      (test_run_passes_spec_args)
    - Step 2: split by source_platform (test_run_polymarket_only,
      test_run_hackernews_only, test_run_mixed_rows)
    - Step 5: consensus-extreme drill-down threshold
      (test_run_drilldown_high_extreme,
       test_run_drilldown_low_extreme,
       test_run_no_drilldown_mid_range,
       test_run_no_drilldown_for_hackernews)
    - Step 6: evidence_weight bounds + Polymarket > HackerNews
      (test_run_evidence_weight_in_unit_interval,
       test_run_polymarket_outweighs_hackernews_with_equal_factors)
    - Step 7: overall_sentiment weighted average
      (test_run_overall_sentiment_weighted_average,
       test_run_overall_sentiment_zero_when_no_weights)
    - Empty short-circuit (test_run_empty_when_zero_rows)
    - T19.3 corrections: community_sentiment float + key_arguments empty
      (test_run_community_sentiment_returned_as_raw_float,
       test_run_polymarket_key_arguments_returned_empty_lists)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from agent.agents import pulse_analyst


# ==========================================================
# Fixtures
# ==========================================================

NOW = datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def fake_embedding() -> list[float]:
    return [0.0] * 1536


def _polymarket_row(
    *,
    signal_id: str = "pm-1",
    silver_data_ref: str = "pm-doc-1",
    similarity: float = 0.85,
    consensus_rating: float = 0.5,
    comment_volume_analyzed: int = 50,
    aggregation_window_hours: int = 4,
    market_id_ref: str = "ev-fed-rate-cut",
    executive_summary: str = "Polymarket consensus summary.",
    published_at: datetime = NOW,
) -> dict:
    return {
        "signal_id": signal_id,
        "canonical_event_id": "ev-1",
        "silver_data_ref": silver_data_ref,
        "source_platform": "polymarket",
        "entry_type": "market_consensus",
        "published_at": published_at,
        "content_vitals": {"title": "PM Title", "url": ""},
        "enrichment_ai": {
            "executive_summary": executive_summary,
            "impact_level": 3,
            "reliability_score": 0.7,
            "sentiment_score": 0.0,
        },
        "social_context": {},
        "platform_logic": {
            "entry_type": "market_consensus",
            "aggregation_window_hours": aggregation_window_hours,
            "comment_volume_analyzed": comment_volume_analyzed,
            "consensus_rating": consensus_rating,
            "market_id_ref": market_id_ref,
            "has_raw_source": True,
            "uncertainty_index": 0.2,
            "whale_alert": False,
        },
        "similarity": similarity,
    }


def _hackernews_row(
    *,
    signal_id: str = "hn-1",
    silver_data_ref: str = "hn-doc-1",
    similarity: float = 0.85,
    points: int = 80,
    title: str = "HN Story Title",
    top_technical_insights: list[str] | None = None,
    community_sentiment: float = 0.4,
    published_at: datetime = NOW,
) -> dict:
    return {
        "signal_id": signal_id,
        "canonical_event_id": "ev-1",
        "silver_data_ref": silver_data_ref,
        "source_platform": "hackernews",
        "entry_type": "hackernews_story_summary",
        "published_at": published_at,
        "content_vitals": {"title": title, "url": "https://news.ycombinator.com/item?id=1"},
        "enrichment_ai": {
            "executive_summary": "HN summary.",
            "impact_level": 3,
            "reliability_score": 0.7,
            "sentiment_score": community_sentiment,
        },
        "social_context": {},
        "platform_logic": {
            "entry_type": "hackernews_story_summary",
            "story_type": "discussion",
            "points": points,
            "top_technical_insights": top_technical_insights if top_technical_insights is not None else ["insight-A"],
            "external_link_ref": "",
            "community_sentiment": community_sentiment,
            "has_raw_source": True,
        },
        "similarity": similarity,
    }


# ==========================================================
# Step 1: spec args
# ==========================================================

def test_run_passes_spec_args(fake_embedding):
    """
    Spec §8.4.2 step 1: similarity_search(limit=10) — no platform/impact/
    reliability filters at the agent layer. Splits happen post-search.
    """
    with patch.object(
        pulse_analyst.social_tools, "similarity_search", return_value=[]
    ) as mock_search:
        pulse_analyst.run(fake_embedding, now=NOW)

    mock_search.assert_called_once_with(
        query_embedding=fake_embedding,
        limit=10,
    )


# ==========================================================
# Empty short-circuit
# ==========================================================

def test_run_empty_when_zero_rows(fake_embedding):
    with (
        patch.object(
            pulse_analyst.social_tools, "similarity_search", return_value=[]
        ),
        patch.object(
            pulse_analyst.social_tools, "fetch_raw_comments"
        ) as mock_drill,
    ):
        result = pulse_analyst.run(fake_embedding, now=NOW)

    assert result == {
        "market_consensus": [],
        "community_discussion": [],
        "overall_sentiment": 0.0,
        "empty": True,
    }
    mock_drill.assert_not_called()


# ==========================================================
# Step 2: split by source_platform
# ==========================================================

def test_run_polymarket_only(fake_embedding):
    rows = [_polymarket_row(signal_id="pm-1"), _polymarket_row(signal_id="pm-2")]
    with (
        patch.object(
            pulse_analyst.social_tools, "similarity_search", return_value=rows
        ),
        patch.object(
            pulse_analyst.social_tools, "fetch_raw_comments"
        ),
    ):
        result = pulse_analyst.run(fake_embedding, now=NOW)

    assert len(result["market_consensus"]) == 2
    assert result["community_discussion"] == []
    assert result["empty"] is False


def test_run_hackernews_only(fake_embedding):
    rows = [_hackernews_row(signal_id="hn-1"), _hackernews_row(signal_id="hn-2")]
    with (
        patch.object(
            pulse_analyst.social_tools, "similarity_search", return_value=rows
        ),
        patch.object(
            pulse_analyst.social_tools, "fetch_raw_comments"
        ) as mock_drill,
    ):
        result = pulse_analyst.run(fake_embedding, now=NOW)

    assert result["market_consensus"] == []
    assert len(result["community_discussion"]) == 2
    mock_drill.assert_not_called()


def test_run_mixed_rows(fake_embedding):
    rows = [_polymarket_row(signal_id="pm-1"), _hackernews_row(signal_id="hn-1")]
    with (
        patch.object(
            pulse_analyst.social_tools, "similarity_search", return_value=rows
        ),
        patch.object(
            pulse_analyst.social_tools, "fetch_raw_comments"
        ),
    ):
        result = pulse_analyst.run(fake_embedding, now=NOW)

    assert len(result["market_consensus"]) == 1
    assert len(result["community_discussion"]) == 1
    assert result["market_consensus"][0]["signal_id"] == "pm-1"
    assert result["community_discussion"][0]["signal_id"] == "hn-1"


# ==========================================================
# Step 5: consensus-extreme drill-down
# ==========================================================

def test_run_drilldown_high_extreme(fake_embedding):
    """consensus_rating > 0.8 → drill-down fires with silver_data_ref."""
    row = _polymarket_row(silver_data_ref="EXTREME-DOC", consensus_rating=0.92)
    with (
        patch.object(
            pulse_analyst.social_tools, "similarity_search", return_value=[row]
        ),
        patch.object(
            pulse_analyst.social_tools, "fetch_raw_comments", return_value=None
        ) as mock_drill,
    ):
        pulse_analyst.run(fake_embedding, now=NOW)

    mock_drill.assert_called_once_with("EXTREME-DOC")


def test_run_drilldown_low_extreme(fake_embedding):
    """consensus_rating < 0.2 → drill-down fires."""
    row = _polymarket_row(silver_data_ref="EXTREME-DOC", consensus_rating=0.05)
    with (
        patch.object(
            pulse_analyst.social_tools, "similarity_search", return_value=[row]
        ),
        patch.object(
            pulse_analyst.social_tools, "fetch_raw_comments", return_value=None
        ) as mock_drill,
    ):
        pulse_analyst.run(fake_embedding, now=NOW)

    mock_drill.assert_called_once_with("EXTREME-DOC")


def test_run_no_drilldown_mid_range(fake_embedding):
    """consensus_rating in [0.2, 0.8] → no drill-down."""
    row = _polymarket_row(consensus_rating=0.5)
    with (
        patch.object(
            pulse_analyst.social_tools, "similarity_search", return_value=[row]
        ),
        patch.object(
            pulse_analyst.social_tools, "fetch_raw_comments"
        ) as mock_drill,
    ):
        pulse_analyst.run(fake_embedding, now=NOW)

    mock_drill.assert_not_called()


def test_run_no_drilldown_for_hackernews(fake_embedding):
    """HackerNews rows never trigger drill-down regardless of values."""
    row = _hackernews_row()
    with (
        patch.object(
            pulse_analyst.social_tools, "similarity_search", return_value=[row]
        ),
        patch.object(
            pulse_analyst.social_tools, "fetch_raw_comments"
        ) as mock_drill,
    ):
        pulse_analyst.run(fake_embedding, now=NOW)

    mock_drill.assert_not_called()


# ==========================================================
# Step 6: evidence_weight
# ==========================================================

def test_run_evidence_weight_in_unit_interval(fake_embedding):
    """All evidence_weights must land in [0, 1]."""
    rows = [
        _polymarket_row(signal_id=f"pm-{i}", similarity=0.5 + 0.05 * i, comment_volume_analyzed=20 * i)
        for i in range(3)
    ] + [
        _hackernews_row(signal_id=f"hn-{i}", similarity=0.5 + 0.05 * i, points=20 * i)
        for i in range(3)
    ]
    with (
        patch.object(
            pulse_analyst.social_tools, "similarity_search", return_value=rows
        ),
        patch.object(
            pulse_analyst.social_tools, "fetch_raw_comments"
        ),
    ):
        result = pulse_analyst.run(fake_embedding, now=NOW)

    for item in result["market_consensus"]:
        assert 0.0 <= item["evidence_weight"] <= 1.0
    for item in result["community_discussion"]:
        assert 0.0 <= item["evidence_weight"] <= 1.0


def test_run_polymarket_outweighs_hackernews_with_equal_factors(fake_embedding):
    """
    Spec §8.4.2 step 6: with similarity, volume, recency held equal, a
    Polymarket item must outweigh a HackerNews item (skin-in-the-game bias
    via PLATFORM_WEIGHT_POLYMARKET / PLATFORM_WEIGHT_HACKERNEWS).
    """
    pm = _polymarket_row(similarity=0.7, comment_volume_analyzed=50, published_at=NOW)
    hn = _hackernews_row(similarity=0.7, points=50, published_at=NOW)
    with (
        patch.object(
            pulse_analyst.social_tools, "similarity_search", return_value=[pm, hn]
        ),
        patch.object(
            pulse_analyst.social_tools, "fetch_raw_comments"
        ),
    ):
        result = pulse_analyst.run(fake_embedding, now=NOW)

    pm_weight = result["market_consensus"][0]["evidence_weight"]
    hn_weight = result["community_discussion"][0]["evidence_weight"]
    assert pm_weight > hn_weight


# ==========================================================
# Step 7: overall_sentiment
# ==========================================================

def test_run_overall_sentiment_weighted_average(fake_embedding):
    """
    Deterministic check: with a Polymarket consensus_rating=1.0 (→ +1.0)
    and a HackerNews community_sentiment=-1.0, both with equal evidence_weight,
    the weighted average should land in (-1, +1) and be biased by the
    Polymarket multiplier (PLATFORM_WEIGHT_POLYMARKET > PLATFORM_WEIGHT_HACKERNEWS).
    """
    pm = _polymarket_row(consensus_rating=1.0, similarity=0.5, comment_volume_analyzed=50)
    hn = _hackernews_row(community_sentiment=-1.0, similarity=0.5, points=50)
    with (
        patch.object(
            pulse_analyst.social_tools, "similarity_search", return_value=[pm, hn]
        ),
        patch.object(
            pulse_analyst.social_tools, "fetch_raw_comments"
        ),
    ):
        result = pulse_analyst.run(fake_embedding, now=NOW)

    sentiment = result["overall_sentiment"]
    assert -1.0 <= sentiment <= 1.0
    # Polymarket carries higher weight, so the bullish polymarket signal
    # should dominate the bearish HackerNews signal.
    assert sentiment > 0.0


def test_run_overall_sentiment_zero_when_all_weights_zero(fake_embedding):
    """
    If similarity=0, volume=0, recency≈0 (very old), every evidence_weight
    is 0; overall_sentiment must return 0.0 rather than divide-by-zero.
    """
    very_old = NOW - timedelta(days=365 * 5)  # recency ~ 0
    pm = _polymarket_row(
        similarity=0.0,
        comment_volume_analyzed=0,
        consensus_rating=0.5,
        published_at=very_old,
    )
    with (
        patch.object(
            pulse_analyst.social_tools, "similarity_search", return_value=[pm]
        ),
        patch.object(
            pulse_analyst.social_tools, "fetch_raw_comments"
        ),
    ):
        result = pulse_analyst.run(fake_embedding, now=NOW)

    assert result["overall_sentiment"] == 0.0


# ==========================================================
# T19.3 corrections
# ==========================================================

def test_run_community_sentiment_returned_as_raw_float(fake_embedding):
    """
    T19.3 / Drift B: spec corrected from str to float; agent must return
    the raw float in [-1.0, 1.0] without bucketing.
    """
    row = _hackernews_row(community_sentiment=0.4321)
    with (
        patch.object(
            pulse_analyst.social_tools, "similarity_search", return_value=[row]
        ),
        patch.object(
            pulse_analyst.social_tools, "fetch_raw_comments"
        ),
    ):
        result = pulse_analyst.run(fake_embedding, now=NOW)

    item = result["community_discussion"][0]
    assert isinstance(item["community_sentiment"], float)
    assert item["community_sentiment"] == 0.4321


def test_run_polymarket_key_arguments_returned_empty_lists(fake_embedding):
    """
    T19.3 / KG-PHASE8-11: key_arguments_pro / key_arguments_con are not
    populated by gold_job; agent returns [] for both until that gap closes.
    """
    row = _polymarket_row()
    with (
        patch.object(
            pulse_analyst.social_tools, "similarity_search", return_value=[row]
        ),
        patch.object(
            pulse_analyst.social_tools, "fetch_raw_comments"
        ),
    ):
        result = pulse_analyst.run(fake_embedding, now=NOW)

    item = result["market_consensus"][0]
    assert item["key_arguments_pro"] == []
    assert item["key_arguments_con"] == []
