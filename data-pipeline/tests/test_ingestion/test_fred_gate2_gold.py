"""
Gate 2 (Gold) — Logic Gate: FRED Gold Processing & Automation Triggers.

Validates the Gold Job's FRED branch using mock payloads — no live Flink
cluster, no Kafka, no FRED API, no OpenAI calls (FRED is deterministic math).

What Gate 2 Gold covers (Section 9.3 Gate 2):
    [1]  Valid Silver metric → routes to GOLD_STRUCTURED_METRICS
    [2]  Gold record has layer="Gold" (Silver stubs replaced)
    [3]  Momentum block is populated from price_history (not 0.0 stubs)
    [4]  No prior history → is_new_market=True, all deltas=0.0
    [5]  With prior history → change_24h/7d/30d computed correctly
    [6]  T10Y2Y < 0 → anomaly_flag "Market_Anomaly", impact_level=5
    [7]  T10Y2Y >= 0 → no flags, impact_level = release_priority (5)
    [8]  VIXCLS > 30 → anomaly_flag "Extreme_Uncertainty", impact_level=5
    [9]  VIXCLS <= 30 → no flags
    [10] DCOILWTICO |change_24h| > 5% → anomaly_flag "Price_Variance_Alert"
    [11] DCOILWTICO |change_24h| <= 5% → no flags
    [12] Non-price series (FEDFUNDS) never triggers Price_Variance_Alert
    [13] process_fred_gold_message() always returns GOLD_STRUCTURED_METRICS
    [14] metadata_extension retains all original FRED fields after trigger enrichment
    [15] _now injection for deterministic delta assertions (Section 9.3 — no wall-clock)

Test strategy:
    build_silver_fred_metric() builds realistic Silver records inline.
    All tests use _now injection (same technique as Polymarket Gate 2 Gold)
    so momentum assertions are deterministic regardless of when the test runs.

References:
    - Section 4.2B: Structural Enrichment — Momentum Block
    - Section B.3:  FRED automation trigger thresholds
    - Section C.2:  Silver/Gold Structured Metric schema
    - Section 9.3:  Triple-Gate Test Matrix — Gate 2
    - Section 4.4:  Mock-Driven Development (no live API calls)
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from config.kafka_topics import GOLD_STRUCTURED_METRICS
from ingestion.fred_producer import INDICATORS
from processing.gold_job import (
    _FRED_PRICE_SERIES,
    _T10Y2Y_INVERSION_THRESHOLD,
    _VIXCLS_FEAR_THRESHOLD,
    apply_fred_automation_triggers,
    build_gold_structured_metric,
    compute_momentum_block,
    process_fred_gold_message,
)
from processing.silver_job import map_fred_observation_to_silver
from utils.kafka_utils import build_bronze_message

# ==========================================================
# Helpers
# ==========================================================

FRED_ENDPOINT = "https://api.stlouisfed.org/fred/series/observations?series_id={}"


def _make_envelope(series_id: str, value: float, obs_date: str = "2026-03-01") -> dict:
    """Build a Bronze envelope for an arbitrary FRED observation."""
    meta = INDICATORS[series_id]
    raw = {
        "series_id":          series_id,
        "observation_date":   obs_date,
        "value":              value,
        "realtime_start":     "2026-03-29",
        "realtime_end":       "2026-03-29",
        "category":           meta["category"],
        "description":        meta["description"],
        "unit":               meta["unit"],
        "release_priority":   meta["release_priority"],
        "seasonal_adjustment": meta["seasonal_adjustment"],
        "fetch_mode":         "pulse",
    }
    return build_bronze_message(
        source_name="fred",
        source_endpoint=FRED_ENDPOINT.format(series_id),
        raw_payload=raw,
    )


def _make_silver(series_id: str, value: float, obs_date: str = "2026-03-01") -> dict:
    """Build a Silver structured metric for a FRED observation."""
    envelope = _make_envelope(series_id, value, obs_date)
    return map_fred_observation_to_silver(envelope["payload"]["raw_payload"], envelope)


def _history_with_value(
    value: float,
    hours_ago: int,
    now: datetime,
) -> list[tuple[str, float]]:
    """Return a one-entry price history at now - hours_ago."""
    ts = (now - timedelta(hours=hours_ago)).isoformat()
    return [(ts, value)]


# Reference timestamp injected into all momentum tests so assertions are
# independent of wall-clock time (Section 9.3 — deterministic test design).
_NOW = datetime(2026, 3, 29, 12, 0, 0, tzinfo=timezone.utc)


# ==========================================================
# Gate 2 Gold — Routing
# ==========================================================

class TestFREDGoldRouting:
    """process_fred_gold_message() always returns GOLD_STRUCTURED_METRICS [1]."""

    @pytest.mark.parametrize("series_id,value", [
        ("FEDFUNDS",          4.33),
        ("CPIAUCSL",          315.8),
        ("UNRATE",            4.1),
        ("T10Y2Y",            0.12),   # positive — no trigger
        ("T10Y2Y",           -0.15),   # negative — triggers Market_Anomaly
        ("VIXCLS",            18.0),   # normal
        ("VIXCLS",            35.0),   # triggers Extreme_Uncertainty
        ("DCOILWTICO",        72.4),
    ])
    def test_always_routes_to_gold_structured_metrics(self, series_id, value):
        """
        FRED Gold enrichment has no failure paths — every structurally valid
        Silver metric must produce (GOLD_STRUCTURED_METRICS, gold_record).

        Unlike Social Pulse (which can fail on OpenAI errors), FRED enrichment
        is deterministic math. DLQ routing only happens for Silver-layer issues,
        which are caught before this function is called.
        """
        silver = _make_silver(series_id, value)
        topic, record = process_fred_gold_message(silver, [], _now=_NOW)
        assert topic == GOLD_STRUCTURED_METRICS
        assert record is not None


# ==========================================================
# Gate 2 Gold — Gold Record Structure
# ==========================================================

class TestFREDGoldStructure:
    """Verifies the Gold record's structural properties [2, 14]."""

    @pytest.fixture(scope="class")
    def gold(self) -> dict:
        silver = _make_silver("FEDFUNDS", 4.33)
        _, record = process_fred_gold_message(silver, [], _now=_NOW)
        return record

    def test_layer_is_gold(self, gold):
        """layer must be 'Gold' (Silver stub replaced) [2]."""
        assert gold["layer"] == "Gold"

    def test_entity_type_unchanged(self, gold):
        """entity_type must remain 'Structured_Metric'."""
        assert gold["entity_type"] == "Structured_Metric"

    def test_source_name_unchanged(self, gold):
        """core_identity.source_name must remain 'fred'."""
        assert gold["core_identity"]["source_name"] == "fred"

    def test_original_fred_fields_preserved_in_metadata(self, gold):
        """
        Original FRED metadata_extension fields (series_id, observation_date,
        release_priority, seasonal_adjustment) must survive trigger enrichment [14].

        apply_fred_automation_triggers() must not overwrite or drop the original
        FRED fields when adding anomaly_flags/impact_level/trigger_reason.
        """
        ext = gold["metadata_extension"]
        assert "series_id" in ext
        assert "observation_date" in ext
        assert "release_priority" in ext
        assert "seasonal_adjustment" in ext

    def test_trigger_fields_added_to_metadata(self, gold):
        """After Gold enrichment, metadata_extension must have trigger fields."""
        ext = gold["metadata_extension"]
        assert "anomaly_flags" in ext
        assert "impact_level" in ext
        assert "trigger_reason" in ext

    def test_anomaly_flags_is_list(self, gold):
        """anomaly_flags must always be a list (empty or non-empty)."""
        assert isinstance(gold["metadata_extension"]["anomaly_flags"], list)


# ==========================================================
# Gate 2 Gold — Momentum Block
# ==========================================================

class TestFREDMomentumBlock:
    """Verifies momentum delta computation [3, 4, 5, 15]."""

    def test_no_history_is_new_market(self):
        """
        With empty price_history → is_new_market=True, all deltas=0.0 [4].

        Why: the first observation for a series has no prior data to compute
        deltas against. is_new_market=True signals this to the RAG agent so
        it does not interpret 0.0 deltas as "no movement".
        """
        silver = _make_silver("FEDFUNDS", 4.33)
        _, gold = process_fred_gold_message(silver, [], _now=_NOW)
        mb = gold["momentum_block"]
        assert mb["is_new_market"] is True
        assert mb["change_24h"] == 0.0
        assert mb["change_7d"]  == 0.0
        assert mb["change_30d"] == 0.0

    def test_momentum_stubs_replaced_with_computed_values(self):
        """
        With prior history → momentum_block values must be non-stub [3].

        A 25h-old observation at 4.0 and a current value of 4.33 should
        produce a non-zero change_24h. Stubs are 0.0; computed value is not.
        """
        silver = _make_silver("FEDFUNDS", 4.33)
        # Place a history point 25h ago so it falls within the 24h window
        history = _history_with_value(4.0, hours_ago=25, now=_NOW)
        _, gold = process_fred_gold_message(silver, history, _now=_NOW)
        mb = gold["momentum_block"]
        assert mb["is_new_market"] is False
        # change_24h = (4.33 - 4.0) / 4.0 = 0.0825
        assert mb["change_24h"] != 0.0, (
            "change_24h must be non-zero when a reference point exists in the 24h window"
        )

    def test_change_24h_formula(self):
        """
        change_24h = (current - reference) / reference.

        Reference is the most recent observation AT OR BEFORE now-24h.
        With reference=4.0 and current=4.33: expected ≈ 0.0825 (6 sig figs).
        """
        silver  = _make_silver("FEDFUNDS", 4.33)
        history = _history_with_value(4.0, hours_ago=25, now=_NOW)
        _, gold = process_fred_gold_message(silver, history, _now=_NOW)
        expected = round((4.33 - 4.0) / 4.0, 6)
        assert gold["momentum_block"]["change_24h"] == pytest.approx(expected, rel=1e-5)

    def test_layer_gold_with_history(self):
        """layer must be 'Gold' even when price_history is non-empty [2]."""
        silver  = _make_silver("VIXCLS", 22.0)
        history = _history_with_value(20.0, hours_ago=25, now=_NOW)
        _, gold = process_fred_gold_message(silver, history, _now=_NOW)
        assert gold["layer"] == "Gold"


# ==========================================================
# Gate 2 Gold — Automation Triggers (Section B.3)
# ==========================================================

class TestFREDAutomationTriggers:
    """
    Verifies each of the three automation trigger rules from Section B.3
    [6, 7, 8, 9, 10, 11, 12].
    """

    # ----------------------------------------------------------
    # T10Y2Y — Yield Curve Inversion
    # ----------------------------------------------------------

    def test_t10y2y_inversion_triggers_market_anomaly(self):
        """
        T10Y2Y < 0 → "Market_Anomaly" in anomaly_flags, impact_level=5 [6].

        Yield curve inversion (2-year > 10-year) is a historically reliable
        recession leading indicator (Section B.3).
        """
        silver = _make_silver("T10Y2Y", -0.15)
        _, gold = process_fred_gold_message(silver, [], _now=_NOW)
        ext = gold["metadata_extension"]
        assert "Market_Anomaly" in ext["anomaly_flags"]
        assert ext["impact_level"] == 5
        assert ext["trigger_reason"] is not None
        assert "T10Y2Y" in ext["trigger_reason"]

    def test_t10y2y_positive_no_flags(self):
        """
        T10Y2Y >= 0 → no anomaly flags, impact_level = release_priority (5) [7].

        The release_priority for T10Y2Y is 5 (Section B.3 Indicator Matrix),
        so impact_level == 5 even without a trigger — but anomaly_flags is [].
        """
        silver = _make_silver("T10Y2Y", 0.12)
        _, gold = process_fred_gold_message(silver, [], _now=_NOW)
        ext = gold["metadata_extension"]
        assert "Market_Anomaly" not in ext["anomaly_flags"]
        assert ext["anomaly_flags"] == []
        assert ext["trigger_reason"] is None
        # impact_level inherits release_priority=5 (no trigger override needed)
        assert ext["impact_level"] == INDICATORS["T10Y2Y"]["release_priority"]

    def test_t10y2y_exactly_zero_no_trigger(self):
        """
        T10Y2Y == 0.0 must NOT trigger Market_Anomaly — the rule is strictly < 0.

        Zero spread is unusual but not an inversion. The trigger activates only
        when the curve is negative.
        """
        silver = _make_silver("T10Y2Y", 0.0)
        _, gold = process_fred_gold_message(silver, [], _now=_NOW)
        assert "Market_Anomaly" not in gold["metadata_extension"]["anomaly_flags"]

    # ----------------------------------------------------------
    # VIXCLS — Fear Index
    # ----------------------------------------------------------

    def test_vixcls_above_30_triggers_extreme_uncertainty(self):
        """
        VIXCLS > 30 → "Extreme_Uncertainty" in anomaly_flags, impact_level=5 [8].
        """
        silver = _make_silver("VIXCLS", 35.0)
        _, gold = process_fred_gold_message(silver, [], _now=_NOW)
        ext = gold["metadata_extension"]
        assert "Extreme_Uncertainty" in ext["anomaly_flags"]
        assert ext["impact_level"] == 5
        assert "VIXCLS" in ext["trigger_reason"]

    def test_vixcls_exactly_30_no_trigger(self):
        """
        VIXCLS == 30 must NOT trigger Extreme_Uncertainty — rule is strictly > 30.
        """
        silver = _make_silver("VIXCLS", 30.0)
        _, gold = process_fred_gold_message(silver, [], _now=_NOW)
        assert "Extreme_Uncertainty" not in gold["metadata_extension"]["anomaly_flags"]

    def test_vixcls_below_30_no_flags(self):
        """VIXCLS <= 30 → no flags, impact_level = release_priority (5) [9]."""
        silver = _make_silver("VIXCLS", 18.45)
        _, gold = process_fred_gold_message(silver, [], _now=_NOW)
        ext = gold["metadata_extension"]
        assert ext["anomaly_flags"] == []
        assert ext["impact_level"] == INDICATORS["VIXCLS"]["release_priority"]

    # ----------------------------------------------------------
    # Price Variance — Commodity Series
    # ----------------------------------------------------------

    def test_oil_price_variance_above_5pct_triggers_alert(self):
        """
        DCOILWTICO |change_24h| > 5% → "Price_Variance_Alert" [10].

        A 6.2% 24h swing on crude oil prices is a geopolitical/supply shock
        indicator that warrants immediate grounding (Section B.3).
        """
        # current=72.4, prior_25h=68.0 → change_24h = (72.4-68.0)/68.0 ≈ 6.47%
        silver  = _make_silver("DCOILWTICO", 72.4)
        history = _history_with_value(68.0, hours_ago=25, now=_NOW)
        _, gold = process_fred_gold_message(silver, history, _now=_NOW)
        ext = gold["metadata_extension"]
        assert "Price_Variance_Alert" in ext["anomaly_flags"], (
            f"Expected Price_Variance_Alert. anomaly_flags={ext['anomaly_flags']}, "
            f"change_24h={gold['momentum_block']['change_24h']:.4f}"
        )
        assert ext["trigger_reason"] is not None
        assert "DCOILWTICO" in ext["trigger_reason"]

    def test_oil_price_variance_below_5pct_no_alert(self):
        """
        DCOILWTICO |change_24h| <= 5% → no Price_Variance_Alert [11].
        """
        # current=72.4, prior_25h=71.0 → change_24h = (72.4-71.0)/71.0 ≈ 1.97%
        silver  = _make_silver("DCOILWTICO", 72.4)
        history = _history_with_value(71.0, hours_ago=25, now=_NOW)
        _, gold = process_fred_gold_message(silver, history, _now=_NOW)
        assert "Price_Variance_Alert" not in gold["metadata_extension"]["anomaly_flags"]

    def test_all_price_series_can_trigger(self):
        """
        GASREGW and DHHNGSP must also be capable of triggering
        Price_Variance_Alert — not just DCOILWTICO [10].
        """
        for series_id, current, prior in [
            ("GASREGW",          3.62, 3.37),   # ~7.4% change
            ("DHHNGSP", 3.62, 3.37),      # ~7.4% change
        ]:
            silver  = _make_silver(series_id, current)
            history = _history_with_value(prior, hours_ago=25, now=_NOW)
            _, gold = process_fred_gold_message(silver, history, _now=_NOW)
            assert "Price_Variance_Alert" in gold["metadata_extension"]["anomaly_flags"], (
                f"Expected Price_Variance_Alert for {series_id}"
            )

    def test_non_price_series_never_triggers_price_alert(self):
        """
        FEDFUNDS, CPIAUCSL, UNRATE etc. must NEVER trigger Price_Variance_Alert,
        even with a large 24h change [12].

        Price_Variance_Alert is only meaningful for commodity spot prices.
        A 10% month-over-month CPI change is significant but not a same-day
        price shock — the trigger semantics don't apply.
        """
        non_price_series = set(INDICATORS.keys()) - _FRED_PRICE_SERIES
        for series_id in non_price_series:
            meta  = INDICATORS[series_id]
            value = 100.0  # arbitrary current
            silver  = _make_silver(series_id, value)
            # Large 24h history change (50%) — must not trigger price alert
            history = _history_with_value(50.0, hours_ago=25, now=_NOW)
            _, gold = process_fred_gold_message(silver, history, _now=_NOW)
            assert "Price_Variance_Alert" not in gold["metadata_extension"]["anomaly_flags"], (
                f"Non-price series '{series_id}' must not trigger Price_Variance_Alert"
            )

    def test_price_series_with_no_history_no_alert(self):
        """
        A price series with no history (first observation) cannot have a
        change_24h, so Price_Variance_Alert must not fire.

        is_new_market=True → change_24h=0.0 → 0% < 5% → no alert.
        """
        silver = _make_silver("DCOILWTICO", 72.4)
        _, gold = process_fred_gold_message(silver, [], _now=_NOW)
        assert "Price_Variance_Alert" not in gold["metadata_extension"]["anomaly_flags"]

    # ----------------------------------------------------------
    # Trigger constants exposure (used by Flink operator for config)
    # ----------------------------------------------------------

    def test_trigger_constants_are_correct(self):
        """
        The exported trigger threshold constants must match Section B.3 values.

        These constants are also read by the Flink FREDGoldMetricsFunction
        operator — incorrect values would silently produce wrong trigger rates.
        """
        assert _T10Y2Y_INVERSION_THRESHOLD == 0.0,  "T10Y2Y threshold must be 0.0 (strictly < 0)"
        assert _VIXCLS_FEAR_THRESHOLD      == 30.0, "VIXCLS threshold must be 30.0 (strictly > 30)"
        assert _FRED_PRICE_SERIES          == {"DCOILWTICO", "GASREGW", "DHHNGSP"}
