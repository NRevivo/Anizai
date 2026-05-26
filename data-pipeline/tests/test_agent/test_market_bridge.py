"""
Gate 1 unit tests for agent/agents/market_bridge.py (Sprint 19 T19.4).

Pure-mock tests: patch agent.tools.market_tools and agent.tools.mapping_tools
at the market_bridge import binding sites. No DB, no real embeddings.

Coverage map (spec §8.4.3):
    - Step 1a: fetch_latest("polymarket", slug) — Tier 1
      (test_run_tier_1_polymarket_populated)
    - Step 1b: fetch_time_series with hours=720
      (test_run_polymarket_fetches_30_day_history)
    - Tier 2: polymarket_slug=None → polymarket: None
      (test_run_tier_2_polymarket_none)
    - Step 2: cross-platform linkage
      (test_run_linked_sources_populated, test_run_linked_sources_empty,
       test_run_linked_sources_skipped_when_no_canonical)
    - Step 3: FRED anomalies, days=14 forwarded
      (test_run_fred_anomalies_passes_days_14)
    - Step 4: Google Trends per entity
      (test_run_google_trends_one_fetch_per_entity,
       test_run_google_trends_skips_unknown_entity)
    - Drift C: indicator_name lookup
      (test_run_fred_indicator_name_lookup_known,
       test_run_fred_indicator_name_falls_back_to_series_id)
    - Drift E: trend_direction derivation
      (test_run_trend_direction_rising_falling_stable)
    - OQ-5: whale alerts extracted from time-series
      (test_run_polymarket_whale_alerts_extracted)
    - OQ-7: empty short-circuit semantics
      (test_run_empty_only_when_all_sources_empty,
       test_run_empty_false_when_only_fred_populated)
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from agent.agents import market_bridge


# ==========================================================
# Fixtures
# ==========================================================

NOW = datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)


def _polymarket_latest(
    *,
    current_value: float = 0.65,
    change_24h: float = 0.02,
    change_7d: float = 0.05,
    change_30d: float = 0.10,
) -> dict:
    return {
        "metric_id": "pm-mid-001",
        "canonical_event_id": "ev-fed-rate-cut",
        "source_name": "polymarket",
        "external_reference_id": "fed-rate-cut-may-2026",
        "current_value": current_value,
        "unit": "probability",
        "timestamp_utc": NOW,
        "change_24h": change_24h,
        "change_7d": change_7d,
        "change_30d": change_30d,
        "is_new_market": False,
        "metadata_extension": {
            "liquidity_pool_tvl": 12345.67,
            "whale_alert": False,
        },
    }


def _polymarket_history_row(
    *,
    timestamp: datetime,
    value: float,
    whale_alert: bool = False,
) -> dict:
    return {
        "metric_id": "pm-mid-X",
        "source_name": "polymarket",
        "external_reference_id": "fed-rate-cut-may-2026",
        "current_value": value,
        "unit": "probability",
        "timestamp_utc": timestamp,
        "change_24h": 0.0,
        "change_7d": 0.0,
        "change_30d": 0.0,
        "is_new_market": False,
        "metadata_extension": {"whale_alert": whale_alert},
    }


def _fred_anomaly_row(
    *,
    series_id: str = "T10Y2Y",
    current_value: float = -0.15,
    change_7d: float = -0.08,
    impact_level: int = 4,
    flags: list[str] | None = None,
) -> dict:
    return {
        "metric_id": "fred-001",
        "source_name": "fred",
        "external_reference_id": series_id,
        "current_value": current_value,
        "unit": "percent",
        "timestamp_utc": NOW,
        "change_24h": 0.0,
        "change_7d": change_7d,
        "change_30d": 0.0,
        "is_new_market": False,
        "metadata_extension": {
            "series_id": series_id,
            "anomaly_flags": flags if flags is not None else ["Yield_Curve_Inversion"],
            "impact_level": impact_level,
            "trigger_reason": "10y < 2y",
        },
    }


def _googletrends_row(
    *,
    keyword: str = "Federal Reserve",
    current_value: float = 78.0,
    change_24h: float = 12.0,
    public_hype_alert: bool = True,
) -> dict:
    return {
        "metric_id": "gt-001",
        "source_name": "googletrends",
        "external_reference_id": keyword,
        "current_value": current_value,
        "unit": "score_0_100",
        "timestamp_utc": NOW,
        "change_24h": change_24h,
        "change_7d": 0.0,
        "change_30d": 0.0,
        "is_new_market": False,
        "metadata_extension": {
            "keyword": keyword,
            "geo": "US",
            "public_hype_alert": public_hype_alert,
            "abs_change_24h_pts": abs(change_24h),
        },
    }


def _mapping_row(*, platform: str, platform_specific_id: str) -> dict:
    return {
        "mapping_id": "map-1",
        "canonical_event_id": "ev-fed-rate-cut",
        "platform": platform,
        "platform_specific_id": platform_specific_id,
        "similarity_score": 0.95,
    }


# ==========================================================
# Tier 1: Polymarket populated
# ==========================================================

def test_run_tier_1_polymarket_populated():
    latest = _polymarket_latest()
    history = [_polymarket_history_row(timestamp=NOW, value=0.6)]
    with (
        patch.object(
            market_bridge.market_tools, "fetch_latest", return_value=latest
        ),
        patch.object(
            market_bridge.market_tools, "fetch_time_series", return_value=history
        ),
        patch.object(
            market_bridge.market_tools, "fetch_fred_anomalies", return_value=[]
        ),
        patch.object(
            market_bridge.mapping_tools, "lookup_by_canonical", return_value=[]
        ),
    ):
        result = market_bridge.run(polymarket_slug="fed-rate-cut-may-2026")

    pm = result["polymarket"]
    assert pm is not None
    assert pm["current_odds"] == 0.65
    assert pm["momentum"] == {"change_24h": 0.02, "change_7d": 0.05, "change_30d": 0.10}
    assert pm["market_slug"] == "fed-rate-cut-may-2026"
    assert pm["price_history"] == [{"timestamp": NOW.isoformat(), "value": 0.6}]


def test_run_polymarket_fetches_30_day_history():
    """Spec §8.4.3 step 1b: hours=720 forwarded explicitly."""
    with (
        patch.object(
            market_bridge.market_tools, "fetch_latest", return_value=_polymarket_latest()
        ),
        patch.object(
            market_bridge.market_tools, "fetch_time_series", return_value=[]
        ) as mock_history,
        patch.object(
            market_bridge.market_tools, "fetch_fred_anomalies", return_value=[]
        ),
        patch.object(
            market_bridge.mapping_tools, "lookup_by_canonical", return_value=[]
        ),
    ):
        market_bridge.run(polymarket_slug="fed-rate-cut-may-2026")

    mock_history.assert_called_once_with(
        source_name="polymarket",
        external_reference_id="fed-rate-cut-may-2026",
        hours=720,
    )


def test_run_polymarket_whale_alerts_extracted():
    """OQ-5: each time-series row with whale_alert=True surfaces as one entry."""
    history = [
        _polymarket_history_row(timestamp=NOW, value=0.5, whale_alert=False),
        _polymarket_history_row(timestamp=NOW, value=0.7, whale_alert=True),
        _polymarket_history_row(timestamp=NOW, value=0.55, whale_alert=False),
    ]
    with (
        patch.object(
            market_bridge.market_tools, "fetch_latest", return_value=_polymarket_latest()
        ),
        patch.object(
            market_bridge.market_tools, "fetch_time_series", return_value=history
        ),
        patch.object(
            market_bridge.market_tools, "fetch_fred_anomalies", return_value=[]
        ),
        patch.object(
            market_bridge.mapping_tools, "lookup_by_canonical", return_value=[]
        ),
    ):
        result = market_bridge.run(polymarket_slug="fed-rate-cut-may-2026")

    alerts = result["polymarket"]["whale_alerts"]
    assert len(alerts) == 1
    assert alerts[0]["current_value"] == 0.7


# ==========================================================
# Tier 2: no polymarket_slug
# ==========================================================

def test_run_tier_2_polymarket_none():
    with (
        patch.object(
            market_bridge.market_tools, "fetch_latest"
        ) as mock_latest,
        patch.object(
            market_bridge.market_tools, "fetch_time_series"
        ) as mock_history,
        patch.object(
            market_bridge.market_tools, "fetch_fred_anomalies", return_value=[]
        ),
        patch.object(
            market_bridge.mapping_tools, "lookup_by_canonical", return_value=[]
        ),
    ):
        result = market_bridge.run(polymarket_slug=None)

    assert result["polymarket"] is None
    # Tier 2 must skip Polymarket-specific fetches entirely
    mock_latest.assert_not_called()
    mock_history.assert_not_called()


def test_run_polymarket_returns_none_when_vault_miss():
    """Tier 1 with vault miss → polymarket: None (not partial dict)."""
    with (
        patch.object(
            market_bridge.market_tools, "fetch_latest", return_value=None
        ),
        patch.object(
            market_bridge.market_tools, "fetch_time_series", return_value=[]
        ),
        patch.object(
            market_bridge.market_tools, "fetch_fred_anomalies", return_value=[]
        ),
        patch.object(
            market_bridge.mapping_tools, "lookup_by_canonical", return_value=[]
        ),
    ):
        result = market_bridge.run(polymarket_slug="not-in-vault")

    assert result["polymarket"] is None


# ==========================================================
# Cross-platform linkage
# ==========================================================

def test_run_linked_sources_populated():
    links = [_mapping_row(platform="kalshi", platform_specific_id="KSI-FED-MAY")]
    kalshi_latest = {
        "current_value": 0.62,
        "unit": "probability",
        "change_24h": 0.01,
        "change_7d": 0.04,
        "change_30d": 0.09,
        "metadata_extension": {},
    }
    with (
        patch.object(
            market_bridge.mapping_tools, "lookup_by_canonical", return_value=links
        ),
        patch.object(
            market_bridge.market_tools, "fetch_latest", return_value=kalshi_latest
        ),
        patch.object(
            market_bridge.market_tools, "fetch_time_series", return_value=[]
        ),
        patch.object(
            market_bridge.market_tools, "fetch_fred_anomalies", return_value=[]
        ),
    ):
        result = market_bridge.run(canonical_event_id="ev-fed-rate-cut")

    assert len(result["linked_sources"]) == 1
    link = result["linked_sources"][0]
    assert link == {
        "platform": "kalshi",
        "external_id": "KSI-FED-MAY",
        "latest_value": 0.62,
        "unit": "probability",
        "momentum": {"change_24h": 0.01, "change_7d": 0.04, "change_30d": 0.09},
    }


def test_run_linked_sources_empty():
    """Sprint 19 D4: empty linked_sources is the expected default pre-Phase-7."""
    with (
        patch.object(
            market_bridge.mapping_tools, "lookup_by_canonical", return_value=[]
        ),
        patch.object(
            market_bridge.market_tools, "fetch_latest"
        ) as mock_latest,
        patch.object(
            market_bridge.market_tools, "fetch_fred_anomalies", return_value=[]
        ),
    ):
        result = market_bridge.run(canonical_event_id="ev-no-links")

    assert result["linked_sources"] == []
    mock_latest.assert_not_called()


def test_run_linked_sources_skipped_when_no_canonical():
    """canonical_event_id=None → no mapping_dict lookup."""
    with (
        patch.object(
            market_bridge.mapping_tools, "lookup_by_canonical"
        ) as mock_lookup,
        patch.object(
            market_bridge.market_tools, "fetch_latest", return_value=None
        ),
        patch.object(
            market_bridge.market_tools, "fetch_fred_anomalies", return_value=[]
        ),
    ):
        result = market_bridge.run(canonical_event_id=None)

    mock_lookup.assert_not_called()
    assert result["linked_sources"] == []


# ==========================================================
# FRED anomalies
# ==========================================================

def test_run_fred_anomalies_passes_days_14():
    with (
        patch.object(
            market_bridge.market_tools, "fetch_fred_anomalies", return_value=[]
        ) as mock_fred,
        patch.object(
            market_bridge.mapping_tools, "lookup_by_canonical", return_value=[]
        ),
    ):
        market_bridge.run()

    mock_fred.assert_called_once_with(days=14)


def test_run_fred_indicator_name_lookup_known():
    """Drift C: known series_id → human-readable name."""
    rows = [_fred_anomaly_row(series_id="T10Y2Y")]
    with (
        patch.object(
            market_bridge.market_tools, "fetch_fred_anomalies", return_value=rows
        ),
        patch.object(
            market_bridge.mapping_tools, "lookup_by_canonical", return_value=[]
        ),
    ):
        result = market_bridge.run()

    assert result["fred_anomalies"][0]["indicator_name"] == (
        "10-Year vs 2-Year Treasury Yield Spread"
    )


def test_run_fred_indicator_name_falls_back_to_series_id():
    """Drift C: unknown series_id → indicator_name == series_id."""
    rows = [_fred_anomaly_row(series_id="FAKE_SERIES_999")]
    with (
        patch.object(
            market_bridge.market_tools, "fetch_fred_anomalies", return_value=rows
        ),
        patch.object(
            market_bridge.mapping_tools, "lookup_by_canonical", return_value=[]
        ),
    ):
        result = market_bridge.run()

    item = result["fred_anomalies"][0]
    assert item["series_id"] == "FAKE_SERIES_999"
    assert item["indicator_name"] == "FAKE_SERIES_999"


# ==========================================================
# Google Trends
# ==========================================================

def test_run_google_trends_one_fetch_per_entity():
    """Spec §8.4.3 step 4: one fetch_latest call per entity."""

    def fake_fetch(source_name: str, external_reference_id: str):
        return _googletrends_row(keyword=external_reference_id)

    with (
        patch.object(
            market_bridge.market_tools, "fetch_latest", side_effect=fake_fetch
        ) as mock_latest,
        patch.object(
            market_bridge.market_tools, "fetch_fred_anomalies", return_value=[]
        ),
        patch.object(
            market_bridge.mapping_tools, "lookup_by_canonical", return_value=[]
        ),
    ):
        result = market_bridge.run(entities=["Federal Reserve", "Powell", "FOMC"])

    assert mock_latest.call_count == 3
    keywords = [item["keyword"] for item in result["google_trends"]]
    assert keywords == ["Federal Reserve", "Powell", "FOMC"]


def test_run_google_trends_skips_unknown_entity():
    """Entities with no vault hit are dropped, not stubbed."""

    def fake_fetch(source_name: str, external_reference_id: str):
        if external_reference_id == "Powell":
            return _googletrends_row(keyword="Powell")
        return None

    with (
        patch.object(
            market_bridge.market_tools, "fetch_latest", side_effect=fake_fetch
        ),
        patch.object(
            market_bridge.market_tools, "fetch_fred_anomalies", return_value=[]
        ),
        patch.object(
            market_bridge.mapping_tools, "lookup_by_canonical", return_value=[]
        ),
    ):
        result = market_bridge.run(entities=["UnknownEntity", "Powell"])

    assert len(result["google_trends"]) == 1
    assert result["google_trends"][0]["keyword"] == "Powell"


def test_run_trend_direction_rising_falling_stable():
    """Drift E: change_24h ±5.0 thresholds."""
    rows = {
        "rising": _googletrends_row(keyword="rising", change_24h=12.0),
        "falling": _googletrends_row(keyword="falling", change_24h=-12.0),
        "stable": _googletrends_row(keyword="stable", change_24h=2.0),
    }

    def fake_fetch(source_name: str, external_reference_id: str):
        return rows[external_reference_id]

    with (
        patch.object(
            market_bridge.market_tools, "fetch_latest", side_effect=fake_fetch
        ),
        patch.object(
            market_bridge.market_tools, "fetch_fred_anomalies", return_value=[]
        ),
        patch.object(
            market_bridge.mapping_tools, "lookup_by_canonical", return_value=[]
        ),
    ):
        result = market_bridge.run(entities=["rising", "falling", "stable"])

    by_kw = {item["keyword"]: item["trend_direction"] for item in result["google_trends"]}
    assert by_kw == {"rising": "rising", "falling": "falling", "stable": "stable"}


# ==========================================================
# OQ-7: empty short-circuit
# ==========================================================

def test_run_empty_only_when_all_sources_empty():
    """All four sources empty → empty=True."""
    with (
        patch.object(
            market_bridge.market_tools, "fetch_latest", return_value=None
        ),
        patch.object(
            market_bridge.market_tools, "fetch_time_series", return_value=[]
        ),
        patch.object(
            market_bridge.market_tools, "fetch_fred_anomalies", return_value=[]
        ),
        patch.object(
            market_bridge.mapping_tools, "lookup_by_canonical", return_value=[]
        ),
    ):
        result = market_bridge.run()

    assert result == {
        "polymarket": None,
        "linked_sources": [],
        "fred_anomalies": [],
        "google_trends": [],
        "empty": True,
    }


def test_run_empty_false_when_only_fred_populated():
    """
    Any single populated source must flip empty to False — OQ-7 explicit
    clarification: synthesis should not misinterpret partial results.
    """
    rows = [_fred_anomaly_row()]
    with (
        patch.object(
            market_bridge.market_tools, "fetch_latest", return_value=None
        ),
        patch.object(
            market_bridge.market_tools, "fetch_time_series", return_value=[]
        ),
        patch.object(
            market_bridge.market_tools, "fetch_fred_anomalies", return_value=rows
        ),
        patch.object(
            market_bridge.mapping_tools, "lookup_by_canonical", return_value=[]
        ),
    ):
        result = market_bridge.run()

    assert result["empty"] is False
    assert result["polymarket"] is None
    assert result["linked_sources"] == []
    assert len(result["fred_anomalies"]) == 1
    assert result["google_trends"] == []


# ==========================================================
# Sprint 22 T22.2 — Polymarket fuzzy-match resolver wiring
# ==========================================================

def _polymarket_resolver_row(
    *,
    external_reference_id: str = "0xabc_condition_id",
    current_value: float = 0.62,
    match_score: float = 0.91,
) -> dict:
    """
    Resolver-shaped row — same columns as fetch_latest plus the added
    `match_score` (T22.1). The condition_id-shaped external_reference_id
    mirrors what production rows look like (see Sprint 22 D1).
    """
    return {
        "metric_id": "pm-resolver-001",
        "canonical_event_id": "ev-resolver-fixture",
        "source_name": "polymarket",
        "external_reference_id": external_reference_id,
        "current_value": current_value,
        "unit": "probability",
        "timestamp_utc": NOW,
        "change_24h": 0.03,
        "change_7d": 0.07,
        "change_30d": 0.12,
        "is_new_market": False,
        "metadata_extension": {
            "question": "Will the Fed cut rates before June 2026?",
            "liquidity_pool_tvl": 99000.0,
            "whale_alert": False,
        },
        "ingested_at": NOW,
        "match_score": match_score,
    }


class TestPolymarketFuzzyMatchResolverWiring:
    """
    Sprint 22 T22.2: market_bridge.run() must invoke the resolver when
    `has_market_question_intent=True` AND `raw_question` is provided AND
    no explicit `polymarket_slug` is given. On hit, the polymarket
    payload uses the resolver's row + a fresh 720-hour history keyed on
    the row's external_reference_id.
    """

    def test_resolver_hit_populates_polymarket_payload(self):
        """
        Resolver returns a row → polymarket payload built from the
        resolver row (current_odds, momentum) + fetch_time_series for
        the resolver's external_reference_id (price_history,
        whale_alerts). market_slug = the external_reference_id.
        """
        resolver_row = _polymarket_resolver_row(
            external_reference_id="0xfed_condition_id", current_value=0.62,
        )
        history = [_polymarket_history_row(timestamp=NOW, value=0.61)]
        with (
            patch.object(
                market_bridge.market_tools,
                "find_polymarket_market_by_question",
                return_value=resolver_row,
            ) as mock_resolver,
            patch.object(
                market_bridge.market_tools,
                "fetch_time_series",
                return_value=history,
            ) as mock_history,
            patch.object(
                market_bridge.market_tools, "fetch_latest", return_value=None,
            ) as mock_fetch_latest,
            patch.object(
                market_bridge.market_tools, "fetch_fred_anomalies", return_value=[],
            ),
            patch.object(
                market_bridge.mapping_tools, "lookup_by_canonical", return_value=[],
            ),
        ):
            result = market_bridge.run(
                raw_question="Will the Fed cut rates before June 2026?",
                has_market_question_intent=True,
            )

        pm = result["polymarket"]
        assert pm is not None
        assert pm["current_odds"] == 0.62
        assert pm["momentum"] == {"change_24h": 0.03, "change_7d": 0.07, "change_30d": 0.12}
        assert pm["market_slug"] == "0xfed_condition_id"
        assert pm["price_history"] == [{"timestamp": NOW.isoformat(), "value": 0.61}]
        mock_resolver.assert_called_once_with(
            "Will the Fed cut rates before June 2026?"
        )
        mock_history.assert_called_once_with(
            source_name="polymarket",
            external_reference_id="0xfed_condition_id",
            hours=720,
        )
        # The resolver row carries the same columns as fetch_latest, so the
        # redundant fetch_latest call site must be skipped for efficiency.
        mock_fetch_latest.assert_not_called()

    def test_resolver_hit_with_empty_history_still_tier_1(self):
        """
        Edge case: resolver finds the market but the 720-hour history is
        empty. Stay Tier 1 — current_odds + market_comparison still render
        on the frontend even with no trend chart. Do NOT downgrade to
        polymarket=None.
        """
        with (
            patch.object(
                market_bridge.market_tools,
                "find_polymarket_market_by_question",
                return_value=_polymarket_resolver_row(),
            ),
            patch.object(
                market_bridge.market_tools, "fetch_time_series", return_value=[],
            ),
            patch.object(
                market_bridge.market_tools, "fetch_latest", return_value=None,
            ),
            patch.object(
                market_bridge.market_tools, "fetch_fred_anomalies", return_value=[],
            ),
            patch.object(
                market_bridge.mapping_tools, "lookup_by_canonical", return_value=[],
            ),
        ):
            result = market_bridge.run(
                raw_question="Will the Fed cut rates before June 2026?",
                has_market_question_intent=True,
            )

        pm = result["polymarket"]
        assert pm is not None
        assert pm["current_odds"] == 0.62
        assert pm["price_history"] == []
        assert pm["whale_alerts"] == []

    def test_resolver_miss_returns_polymarket_none(self):
        """
        Resolver returns None (no row passed the 0.85 threshold) →
        polymarket=None. Tier 2 path preserved exactly as it was pre-T22.2.
        fetch_time_series must NOT be called.
        """
        with (
            patch.object(
                market_bridge.market_tools,
                "find_polymarket_market_by_question",
                return_value=None,
            ),
            patch.object(
                market_bridge.market_tools, "fetch_time_series",
            ) as mock_history,
            patch.object(
                market_bridge.market_tools, "fetch_fred_anomalies", return_value=[],
            ),
            patch.object(
                market_bridge.mapping_tools, "lookup_by_canonical", return_value=[],
            ),
        ):
            result = market_bridge.run(
                raw_question="Will something obscure happen by 2030?",
                has_market_question_intent=True,
            )

        assert result["polymarket"] is None
        mock_history.assert_not_called()

    def test_intent_false_does_not_call_resolver(self):
        """
        QU classified the question as non-market-intent (open-ended
        explainer, "Why is X happening?", etc.). The resolver must not
        be called at all — saves a DB round-trip on Tier 2 questions.
        """
        with (
            patch.object(
                market_bridge.market_tools,
                "find_polymarket_market_by_question",
            ) as mock_resolver,
            patch.object(
                market_bridge.market_tools, "fetch_fred_anomalies", return_value=[],
            ),
            patch.object(
                market_bridge.mapping_tools, "lookup_by_canonical", return_value=[],
            ),
        ):
            result = market_bridge.run(
                raw_question="Why is inflation rising?",
                has_market_question_intent=False,
            )

        assert result["polymarket"] is None
        mock_resolver.assert_not_called()

    def test_empty_raw_question_does_not_call_resolver(self):
        """
        Even when intent is True, an empty raw_question must skip the
        resolver. The resolver's own empty-string guard would also
        short-circuit, but skipping at the caller is cleaner and matches
        the gate documented in run()'s docstring.
        """
        with (
            patch.object(
                market_bridge.market_tools,
                "find_polymarket_market_by_question",
            ) as mock_resolver,
            patch.object(
                market_bridge.market_tools, "fetch_fred_anomalies", return_value=[],
            ),
            patch.object(
                market_bridge.mapping_tools, "lookup_by_canonical", return_value=[],
            ),
        ):
            result = market_bridge.run(
                raw_question="",
                has_market_question_intent=True,
            )

        assert result["polymarket"] is None
        mock_resolver.assert_not_called()

    def test_slug_takes_precedence_over_raw_question(self):
        """
        Forward-compat: if a future QU auto-pick populates
        `polymarket_slug`, that slug-keyed lookup wins over the fuzzy
        match against `raw_question`. Confirms the resolution-order
        contract in run()'s docstring.
        """
        latest = _polymarket_latest()
        with (
            patch.object(
                market_bridge.market_tools,
                "find_polymarket_market_by_question",
            ) as mock_resolver,
            patch.object(
                market_bridge.market_tools, "fetch_latest", return_value=latest,
            ),
            patch.object(
                market_bridge.market_tools, "fetch_time_series", return_value=[],
            ),
            patch.object(
                market_bridge.market_tools, "fetch_fred_anomalies", return_value=[],
            ),
            patch.object(
                market_bridge.mapping_tools, "lookup_by_canonical", return_value=[],
            ),
        ):
            result = market_bridge.run(
                polymarket_slug="fed-rate-cut-may-2026",
                raw_question="Will the Fed cut rates before June 2026?",
                has_market_question_intent=True,
            )

        assert result["polymarket"] is not None
        assert result["polymarket"]["market_slug"] == "fed-rate-cut-may-2026"
        mock_resolver.assert_not_called()

    def test_resolver_row_with_empty_external_reference_id_returns_none(self):
        """
        Defensive: the schema has external_reference_id NOT NULL, but if
        the resolver ever returns a row with an empty string, treat it
        as a miss rather than calling fetch_time_series with an empty
        key (which would return [] anyway, but loudly succeeding hides
        the data integrity issue).
        """
        bad_row = _polymarket_resolver_row(external_reference_id="")
        with (
            patch.object(
                market_bridge.market_tools,
                "find_polymarket_market_by_question",
                return_value=bad_row,
            ),
            patch.object(
                market_bridge.market_tools, "fetch_time_series",
            ) as mock_history,
            patch.object(
                market_bridge.market_tools, "fetch_fred_anomalies", return_value=[],
            ),
            patch.object(
                market_bridge.mapping_tools, "lookup_by_canonical", return_value=[],
            ),
        ):
            result = market_bridge.run(
                raw_question="Will the Fed cut rates before June 2026?",
                has_market_question_intent=True,
            )

        assert result["polymarket"] is None
        mock_history.assert_not_called()
