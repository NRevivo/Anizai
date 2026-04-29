"""
Gate 2 (Gold) — Logic Gate: OpenWeather Gold Automation Triggers & Momentum.

Validates the Gold Job's OpenWeather branch using mock payloads — no live
Flink cluster, no Kafka, no OWM API, no OpenAI (fully deterministic math).

What Gate 2 Gold covers (Section 9.3 Gate 2):

  ROUTING & LAYER PROMOTION
  [01] Valid Silver metric + empty history  → (GOLD_STRUCTURED_METRICS, gold_record)
  [02] Gold record has layer == "Gold"       (Silver layer promoted)
  [03] process_openweather_gold_message() always returns GOLD_STRUCTURED_METRICS

  COLD-START GUARD (is_new_market) — plan T8 design decision
  [04] Empty price_history → is_new_market == True
  [05] Empty price_history → triggers suppressed (anomaly_flags == [])
  [06] Empty price_history → impact_level == 1 (baseline, no triggers fired)
  [07] Empty price_history → trigger_reason == None
  [08] Empty price_history → change_24h == 0.0

  MOMENTUM BLOCK — WITH PRIOR HISTORY (_now injection for determinism)
  [09] Prior temp 25h ago → change_24h computed correctly
  [10] Prior temp 12h ago → change_24h == 0.0 (more recent than now-24h cutoff)
  [11] Prior temp 8d ago → change_7d computed correctly
  [12] Prior temp 31d ago → change_30d computed correctly
  [13] is_new_market == False when price_history is non-empty

  NATURAL_DISASTER_ALERT (condition_severity ≥ 4 → impact_level=5)
  [14] severity == 4 at non-tech tag  → Natural_Disaster_Alert fires, impact_level == 5
  [15] severity == 3                  → Natural_Disaster_Alert does NOT fire
  [16] severity == 5                  → Natural_Disaster_Alert fires

  TECH_SUPPLY_RISK (severity ≥ 4 AND strategic_tag ∈ {taiwan_semiconductor, japan_tech})
  [17] severity == 4 + taiwan_semiconductor → both Natural_Disaster_Alert AND Tech_Supply_Risk
  [18] severity == 4 + strait_of_hormuz     → only Natural_Disaster_Alert (not Tech_Supply_Risk)
  [19] severity == 3 + taiwan_semiconductor → neither trigger fires

  POTENTIAL_SHIPPING_DELAY (wind_speed_knots > 50 AND strategic_tag ∈ maritime tags)
  [20] wind == 55 kt + strait_of_hormuz → Potential_Shipping_Delay fires, impact_level == 4
  [21] wind == 50 kt + strait_of_hormuz → does NOT fire (threshold is strictly > 50)
  [22] wind == 55 kt + taiwan_semiconductor → does NOT fire (wrong strategic tag)

  IMPACT_LEVEL max() ACROSS TRIGGERS
  [23] Natural_Disaster_Alert (5) + Tech_Supply_Risk (4) → max impact_level == 5
  [24] Potential_Shipping_Delay alone (4) → impact_level == 4

  TRIGGER_REASON
  [25] trigger_reason is pipe-separated string when multiple triggers fire
  [26] trigger_reason is None when no triggers fire

  IMMUTABILITY
  [27] Original silver_metric is not mutated (deep copy check)

  _now INJECTION
  [28] _now parameter enables deterministic delta assertions

  METADATA EXTENSION PRESERVATION
  [29] Silver metadata_extension fields survive Gold enrichment:
       wind_speed_knots, pressure_hpa, condition_severity, strategic_tag, coordinates

Test strategy:
    _make_silver_metric() builds realistic Silver records by calling
    map_openweather_to_silver() directly — same shape the Silver Job produces.
    All momentum assertions use _now injection to prevent wall-clock failures.

References:
    - Section 4.2B: Structural Enrichment — Momentum Block
    - Section B.6:  OpenWeather automation triggers (Natural_Disaster_Alert,
                    Tech_Supply_Risk, Potential_Shipping_Delay)
    - Section C.2:  Silver/Gold Structured Metric schema
    - Section 9.3:  Triple-Gate Test Matrix — Gate 2
    - Section 4.4:  Mock-Driven Development (no live API calls)
"""

import copy
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from config.kafka_topics import GOLD_STRUCTURED_METRICS
from processing.gold_job import (
    _OW_DISASTER_SEVERITY_THRESHOLD,
    _OW_WIND_SHIP_THRESHOLD,
    _OPENWEATHER_TECH_SUPPLY_TAGS,
    _OPENWEATHER_MARITIME_TAGS,
    apply_openweather_automation_triggers,
    build_gold_structured_metric,
    compute_momentum_block,
    process_openweather_gold_message,
)
from processing.silver_job import (
    CONDITION_ID_TO_SEVERITY,
    _MS_TO_KNOTS,
    map_openweather_to_silver,
)
from utils.kafka_utils import build_bronze_message
from ingestion.openweather_producer import SOURCE_NAME, OWM_BASE_URL


# ==========================================================
# Helpers
# ==========================================================

_FAKE_NOW = datetime(2026, 4, 2, 10, 0, 0, tzinfo=timezone.utc)

_LAT = 25.033
_LON = 121.5654
_ENDPOINT = f"{OWM_BASE_URL}?lat={_LAT}&lon={_LON}&units=metric"


def _ts(delta_hours: float) -> str:
    """Return an ISO8601 UTC string offset from _FAKE_NOW by delta_hours."""
    return (_FAKE_NOW - timedelta(hours=delta_hours)).isoformat()


def _make_silver_metric(
    hotspot_name: str = "Taipei",
    strategic_tag: str = "taiwan_semiconductor",
    temperature_celsius: float = 22.4,
    wind_speed_ms: float = 8.7,
    pressure_hpa: float = 1008.0,
    humidity_pct: float = 91.0,
    weather_id: int = 502,
    lat: float = _LAT,
    lon: float = _LON,
    mode: str = "static",
    fetch_timestamp: str = "2026-04-02T10:00:00+00:00",
) -> dict:
    """
    Build an OpenWeather Silver Structured Metric by calling map_openweather_to_silver()
    directly — produces exactly the same shape as the Silver Job outputs in production.
    """
    raw = {
        "hotspot_name":        hotspot_name,
        "lat":                 lat,
        "lon":                 lon,
        "strategic_tag":       strategic_tag,
        "temperature_celsius": temperature_celsius,
        "wind_speed_ms":       wind_speed_ms,
        "pressure_hpa":        pressure_hpa,
        "humidity_pct":        humidity_pct,
        "weather_id":          weather_id,
        "weather_main":        "Rain",
        "weather_description": "heavy intensity rain",
        "official_alerts":     [],
        "fetch_timestamp":     fetch_timestamp,
        "mode":                mode,
    }
    envelope = build_bronze_message(
        source_name=SOURCE_NAME,
        source_endpoint=_ENDPOINT,
        raw_payload=raw,
    )
    return map_openweather_to_silver(raw, envelope)


def _silver_with_wind_knots(wind_speed_knots: float, strategic_tag: str = "strait_of_hormuz") -> dict:
    """
    Build a Silver metric where the metadata_extension.wind_speed_knots value
    matches a specific knot value for Shipping_Delay trigger boundary tests.

    Back-calculates wind_speed_ms from the desired knot value to ensure the
    Silver converter produces the exact value needed (avoiding floating-point
    drift in the round-trip).
    """
    # Reverse the conversion: ms = knots / 1.94384
    wind_ms = wind_speed_knots / _MS_TO_KNOTS
    # Use a severity-3 weather_id so Natural_Disaster_Alert doesn't confound the test
    return _make_silver_metric(
        wind_speed_ms=wind_ms,
        weather_id=500,   # light rain → severity 3 (no Disaster trigger)
        strategic_tag=strategic_tag,
        hotspot_name="Strait_of_Hormuz" if strategic_tag == "strait_of_hormuz" else "Taipei",
    )


# ==========================================================
# [01–03] Routing & Layer Promotion
# ==========================================================

class TestRoutingAndLayerPromotion:
    """Gate 2 Gold checks [01]–[03]: return value and layer field."""

    def test_01_returns_gold_structured_metrics_tuple(self):
        """[01] (GOLD_STRUCTURED_METRICS, gold_record) returned for valid Silver input."""
        silver = _make_silver_metric()
        topic, gold = process_openweather_gold_message(silver, [], _now=_FAKE_NOW)
        assert topic == GOLD_STRUCTURED_METRICS
        assert isinstance(gold, dict)

    def test_02_layer_promoted_to_gold(self):
        """[02] gold_record['layer'] == 'Gold' (was 'Silver' before enrichment)."""
        silver = _make_silver_metric()
        assert silver["layer"] == "Silver"  # confirm input is Silver
        _, gold = process_openweather_gold_message(silver, [], _now=_FAKE_NOW)
        assert gold["layer"] == "Gold"

    def test_03_always_returns_gold_structured_metrics(self):
        """[03] No failure path — always GOLD_STRUCTURED_METRICS for valid Silver input."""
        for temp in [-10.0, 0.0, 22.4, 50.0]:
            silver = _make_silver_metric(temperature_celsius=temp)
            topic, _ = process_openweather_gold_message(silver, [], _now=_FAKE_NOW)
            assert topic == GOLD_STRUCTURED_METRICS, (
                f"temp={temp}: expected GOLD_STRUCTURED_METRICS, got '{topic}'"
            )


# ==========================================================
# [04–08] Cold-Start Guard (is_new_market)
# ==========================================================

class TestColdStartGuard:
    """Gate 2 Gold checks [04]–[08]: empty price_history suppresses all triggers.

    Cold-start guard prevents spurious alerts on pipeline start-up when every
    hotspot fires its first Bronze message (plan T8 design decision).
    """

    def _gold_cold_start(self, **silver_kwargs) -> dict:
        silver = _make_silver_metric(**silver_kwargs)
        _, gold = process_openweather_gold_message(silver, [], _now=_FAKE_NOW)
        return gold

    def test_04_is_new_market_true_when_no_history(self):
        """[04] is_new_market == True when price_history is empty."""
        gold = self._gold_cold_start()
        assert gold["momentum_block"]["is_new_market"] is True

    def test_05_triggers_suppressed_on_cold_start(self):
        """[05] anomaly_flags == [] when is_new_market=True, even at severity=5.

        weather_id=781 → severity 5 (tornado). Without the cold-start guard this
        would fire Natural_Disaster_Alert on every pipeline restart. Triggers must
        be suppressed until a second observation establishes a baseline.
        """
        gold = self._gold_cold_start(weather_id=781, strategic_tag="taiwan_semiconductor")
        assert gold["metadata_extension"]["anomaly_flags"] == [], (
            "No triggers must fire on cold start (is_new_market=True), "
            "even for high-severity conditions"
        )

    def test_06_impact_level_is_baseline_on_cold_start(self):
        """[06] impact_level == 1 (baseline) when is_new_market=True."""
        # severity 5 + tech tag + high wind — all three triggers would fire normally
        wind_ms = (_OW_WIND_SHIP_THRESHOLD + 10) / _MS_TO_KNOTS
        gold = self._gold_cold_start(
            weather_id=781,
            strategic_tag="strait_of_hormuz",
            wind_speed_ms=wind_ms,
            hotspot_name="Strait_of_Hormuz",
        )
        assert gold["metadata_extension"]["impact_level"] == 1, (
            "impact_level must be 1 (baseline) on cold start — no triggers fired"
        )

    def test_07_trigger_reason_is_none_on_cold_start(self):
        """[07] trigger_reason == None when is_new_market=True (no triggers fired)."""
        gold = self._gold_cold_start(weather_id=781)
        assert gold["metadata_extension"]["trigger_reason"] is None

    def test_08_change_24h_is_zero_on_cold_start(self):
        """[08] change_24h == 0.0 when price_history is empty (no prior observation)."""
        gold = self._gold_cold_start()
        assert gold["momentum_block"]["change_24h"] == 0.0


# ==========================================================
# [09–13] Momentum Block — With Prior History
# ==========================================================

class TestMomentumWithHistory:
    """Gate 2 Gold checks [09]–[13]: momentum computed from price_history."""

    def test_09_change_24h_computed_correctly(self):
        """[09] Prior temp 25h ago → at or before now-24h → qualifies as 24h reference."""
        ref_temp = 18.0
        current  = 22.4
        history  = [(_ts(25), ref_temp)]   # 25h ago — at or before now-24h cutoff

        silver = _make_silver_metric(temperature_celsius=current)
        _, gold = process_openweather_gold_message(silver, history, _now=_FAKE_NOW)

        expected = (current - ref_temp) / ref_temp
        assert gold["momentum_block"]["change_24h"] == pytest.approx(expected, abs=1e-4)

    def test_10_change_24h_zero_when_prior_too_recent(self):
        """[10] Prior temp 12h ago → NOT at or before now-24h → no 24h reference → 0.0."""
        history = [(_ts(12), 18.0)]  # 12h ago — more recent than now-24h cutoff
        silver  = _make_silver_metric(temperature_celsius=22.4)
        _, gold = process_openweather_gold_message(silver, history, _now=_FAKE_NOW)
        assert gold["momentum_block"]["change_24h"] == 0.0

    def test_11_change_7d_computed_correctly(self):
        """[11] Prior temp 8d ago → at or before now-7d → qualifies as 7d reference."""
        ref_temp = 15.0
        current  = 22.4
        history  = [(_ts(8 * 24), ref_temp)]   # 192h = 8 days ago

        silver = _make_silver_metric(temperature_celsius=current)
        _, gold = process_openweather_gold_message(silver, history, _now=_FAKE_NOW)

        expected_7d = (current - ref_temp) / ref_temp
        assert gold["momentum_block"]["change_7d"] == pytest.approx(expected_7d, abs=1e-4)

    def test_12_change_30d_computed_correctly(self):
        """[12] Prior temp 31d ago → at or before now-30d → qualifies as 30d reference."""
        ref_temp = 10.0
        current  = 22.4
        history  = [(_ts(24 * 31), ref_temp)]  # 744h = 31 days ago

        silver = _make_silver_metric(temperature_celsius=current)
        _, gold = process_openweather_gold_message(silver, history, _now=_FAKE_NOW)

        expected_30d = (current - ref_temp) / ref_temp
        assert gold["momentum_block"]["change_30d"] == pytest.approx(expected_30d, abs=1e-4)

    def test_13_is_new_market_false_when_history_exists(self):
        """[13] is_new_market == False when price_history is non-empty."""
        history = [(_ts(12), 18.0)]
        silver  = _make_silver_metric(temperature_celsius=22.4)
        _, gold = process_openweather_gold_message(silver, history, _now=_FAKE_NOW)
        assert gold["momentum_block"]["is_new_market"] is False


# ==========================================================
# [14–16] Natural_Disaster_Alert Trigger
# ==========================================================

class TestNaturalDisasterAlert:
    """Gate 2 Gold checks [14]–[16]: Natural_Disaster_Alert severity boundary.

    Trigger fires when condition_severity >= _OW_DISASTER_SEVERITY_THRESHOLD (4).
    Uses a non-empty price_history to bypass the cold-start guard.
    """

    def _gold_with_history(self, **silver_kwargs) -> dict:
        """Build Gold with a single prior observation to bypass cold-start guard."""
        silver = _make_silver_metric(**silver_kwargs)
        history = [(_ts(25), 20.0)]  # prior temp 25h ago — valid 24h reference
        _, gold = process_openweather_gold_message(silver, history, _now=_FAKE_NOW)
        return gold

    def test_14_severity_4_fires_disaster_alert_at_kyiv(self):
        """[14] severity=4 at Kyiv (non-tech tag 'ukraine_conflict') → Natural_Disaster_Alert fires."""
        # weather_id=502 → severity 4; strategic_tag is not in tech or maritime sets
        gold = self._gold_with_history(
            hotspot_name="Kyiv", strategic_tag="ukraine_conflict", weather_id=502
        )
        assert "Natural_Disaster_Alert" in gold["metadata_extension"]["anomaly_flags"]
        assert gold["metadata_extension"]["impact_level"] == 5

    def test_15_severity_3_does_not_fire_disaster_alert(self):
        """[15] severity=3 (weather_id=500, light rain) → Natural_Disaster_Alert does NOT fire."""
        gold = self._gold_with_history(weather_id=500)  # severity 3
        assert "Natural_Disaster_Alert" not in gold["metadata_extension"]["anomaly_flags"]

    def test_16_severity_5_fires_disaster_alert(self):
        """[16] severity=5 (weather_id=781, tornado) → Natural_Disaster_Alert fires."""
        gold = self._gold_with_history(weather_id=781)  # severity 5
        assert "Natural_Disaster_Alert" in gold["metadata_extension"]["anomaly_flags"]
        assert gold["metadata_extension"]["impact_level"] == 5


# ==========================================================
# [17–19] Tech_Supply_Risk Trigger
# ==========================================================

class TestTechSupplyRisk:
    """Gate 2 Gold checks [17]–[19]: Tech_Supply_Risk boundary conditions.

    Trigger requires BOTH condition_severity >= 4 AND strategic_tag in
    {taiwan_semiconductor, japan_tech}. Severity 3 alone is not extreme
    enough for a semiconductor supply chain alert (plan T8 decision).
    """

    def _gold_with_history(self, **silver_kwargs) -> dict:
        silver = _make_silver_metric(**silver_kwargs)
        history = [(_ts(25), 18.0)]
        _, gold = process_openweather_gold_message(silver, history, _now=_FAKE_NOW)
        return gold

    def test_17_severity_4_plus_tech_tag_fires_both_triggers(self):
        """[17] severity=4 + taiwan_semiconductor → Natural_Disaster_Alert AND Tech_Supply_Risk."""
        gold = self._gold_with_history(
            weather_id=502, strategic_tag="taiwan_semiconductor",
            hotspot_name="Taipei",
        )
        flags = gold["metadata_extension"]["anomaly_flags"]
        assert "Natural_Disaster_Alert" in flags, "Natural_Disaster_Alert must fire at severity 4"
        assert "Tech_Supply_Risk" in flags, "Tech_Supply_Risk must fire at severity 4 + tech tag"

    def test_17b_japan_tech_tag_also_fires_tech_supply_risk(self):
        """[17b] severity=4 + japan_tech → Tech_Supply_Risk fires (both tech tags)."""
        gold = self._gold_with_history(
            weather_id=502, strategic_tag="japan_tech",
            hotspot_name="Tokyo",
        )
        assert "Tech_Supply_Risk" in gold["metadata_extension"]["anomaly_flags"]

    def test_18_severity_4_plus_maritime_tag_does_not_fire_tech_supply_risk(self):
        """[18] severity=4 + strait_of_hormuz → only Natural_Disaster_Alert (not Tech_Supply_Risk).

        strait_of_hormuz is a maritime tag, not a tech supply chain tag.
        """
        gold = self._gold_with_history(
            weather_id=502, strategic_tag="strait_of_hormuz",
            hotspot_name="Strait_of_Hormuz",
        )
        flags = gold["metadata_extension"]["anomaly_flags"]
        assert "Natural_Disaster_Alert" in flags
        assert "Tech_Supply_Risk" not in flags, (
            "Tech_Supply_Risk must NOT fire for maritime strategic_tag"
        )

    def test_19_severity_3_plus_tech_tag_does_not_fire_tech_supply_risk(self):
        """[19] severity=3 + taiwan_semiconductor → neither trigger fires.

        Severity 3 (rain/snow) is not extreme enough for semiconductor supply
        chain disruption — threshold is 4 (plan T8 decision to raise from 3 to 4).
        """
        gold = self._gold_with_history(
            weather_id=500, strategic_tag="taiwan_semiconductor",  # 500 → severity 3
            hotspot_name="Taipei",
        )
        flags = gold["metadata_extension"]["anomaly_flags"]
        assert "Natural_Disaster_Alert" not in flags
        assert "Tech_Supply_Risk" not in flags


# ==========================================================
# [20–22] Potential_Shipping_Delay Trigger
# ==========================================================

class TestPotentialShippingDelay:
    """Gate 2 Gold checks [20]–[22]: Potential_Shipping_Delay wind boundary.

    Trigger requires wind_speed_knots > 50 (strictly greater, not >=)
    AND strategic_tag ∈ {strait_of_hormuz, suez_canal, port_of_houston}.
    """

    def _gold_with_wind(
        self,
        wind_speed_knots: float,
        strategic_tag: str = "strait_of_hormuz",
    ) -> dict:
        """Build Gold bypassing cold-start, with the specified wind (in knots)."""
        silver = _silver_with_wind_knots(wind_speed_knots, strategic_tag=strategic_tag)
        history = [(_ts(25), 20.0)]
        _, gold = process_openweather_gold_message(silver, history, _now=_FAKE_NOW)
        return gold

    def test_20_wind_55_knots_at_maritime_tag_fires_shipping_delay(self):
        """[20] wind=55 kt + strait_of_hormuz → Potential_Shipping_Delay fires, impact_level=4."""
        gold = self._gold_with_wind(wind_speed_knots=55.0)
        flags = gold["metadata_extension"]["anomaly_flags"]
        assert "Potential_Shipping_Delay" in flags, (
            "Potential_Shipping_Delay must fire at 55 knots at a maritime chokepoint"
        )
        assert gold["metadata_extension"]["impact_level"] >= 4

    def test_21_wind_exactly_50_knots_does_not_fire_shipping_delay(self):
        """[21] wind=50 kt → does NOT fire (threshold is strictly > 50, not >=).

        Beaufort force 10 starts at 48.4 kt; the trigger uses 50 kt as a
        conservative boundary for 'sustained winds preventing safe transit'.
        Exactly 50 kt is borderline — the strict > prevents false positives.
        """
        gold = self._gold_with_wind(wind_speed_knots=50.0)
        assert "Potential_Shipping_Delay" not in gold["metadata_extension"]["anomaly_flags"], (
            "Potential_Shipping_Delay must NOT fire at exactly 50 knots (threshold is > 50)"
        )

    def test_22_wind_55_knots_at_tech_tag_does_not_fire_shipping_delay(self):
        """[22] wind=55 kt + taiwan_semiconductor → Potential_Shipping_Delay does NOT fire.

        taiwan_semiconductor is not in _OPENWEATHER_MARITIME_TAGS.
        High wind at a semiconductor fab doesn't trigger a shipping delay alert.
        """
        gold = self._gold_with_wind(wind_speed_knots=55.0, strategic_tag="taiwan_semiconductor")
        assert "Potential_Shipping_Delay" not in gold["metadata_extension"]["anomaly_flags"], (
            "Potential_Shipping_Delay must NOT fire for non-maritime strategic_tag"
        )


# ==========================================================
# [23–24] Impact Level max() Across Triggers
# ==========================================================

class TestImpactLevelMax:
    """Gate 2 Gold checks [23]–[24]: impact_level == max() across all fired triggers."""

    def test_23_disaster_plus_tech_supply_impact_level_is_5(self):
        """[23] Natural_Disaster_Alert (5) + Tech_Supply_Risk (4) → max impact_level == 5."""
        # weather_id=502 → severity 4; taiwan_semiconductor fires both Disaster + Tech
        silver = _make_silver_metric(
            weather_id=502, strategic_tag="taiwan_semiconductor", hotspot_name="Taipei"
        )
        history = [(_ts(25), 18.0)]
        _, gold = process_openweather_gold_message(silver, history, _now=_FAKE_NOW)

        flags = gold["metadata_extension"]["anomaly_flags"]
        assert "Natural_Disaster_Alert" in flags
        assert "Tech_Supply_Risk" in flags
        assert gold["metadata_extension"]["impact_level"] == 5, (
            "impact_level must be 5 (max of 5 and 4) when both triggers fire"
        )

    def test_24_shipping_delay_alone_sets_impact_level_4(self):
        """[24] Potential_Shipping_Delay alone → impact_level == 4."""
        gold_record = _silver_with_wind_knots(55.0, strategic_tag="strait_of_hormuz")
        history     = [(_ts(25), 20.0)]
        _, gold = process_openweather_gold_message(gold_record, history, _now=_FAKE_NOW)

        # weather_id=500 → severity 3 → no disaster trigger; only shipping delay
        flags = gold["metadata_extension"]["anomaly_flags"]
        assert "Potential_Shipping_Delay" in flags
        assert "Natural_Disaster_Alert" not in flags
        assert gold["metadata_extension"]["impact_level"] == 4


# ==========================================================
# [25–26] Trigger Reason
# ==========================================================

class TestTriggerReason:
    """Gate 2 Gold checks [25]–[26]: trigger_reason content."""

    def test_25_trigger_reason_pipe_separated_for_multiple_triggers(self):
        """[25] trigger_reason is a pipe-separated string when multiple triggers fire."""
        silver  = _make_silver_metric(weather_id=502, strategic_tag="taiwan_semiconductor")
        history = [(_ts(25), 18.0)]
        _, gold = process_openweather_gold_message(silver, history, _now=_FAKE_NOW)

        reason = gold["metadata_extension"]["trigger_reason"]
        assert reason is not None, "trigger_reason must be non-None when triggers fire"
        assert "|" in reason, (
            f"trigger_reason must be pipe-separated for multiple triggers; got: '{reason}'"
        )

    def test_26_trigger_reason_is_none_when_no_triggers_fire(self):
        """[26] trigger_reason == None when no triggers fire (severity 3, no maritime wind)."""
        silver  = _make_silver_metric(weather_id=500)  # severity 3 → no triggers
        history = [(_ts(25), 18.0)]
        _, gold = process_openweather_gold_message(silver, history, _now=_FAKE_NOW)

        assert gold["metadata_extension"]["trigger_reason"] is None


# ==========================================================
# [27] Immutability
# ==========================================================

class TestImmutability:
    """Gate 2 Gold check [27]: original Silver record not mutated."""

    def test_27_silver_metric_not_mutated(self):
        """[27] Original silver_metric is unchanged after process_openweather_gold_message()."""
        silver = _make_silver_metric(weather_id=502, strategic_tag="taiwan_semiconductor")
        silver_before = copy.deepcopy(silver)

        history = [(_ts(25), 18.0)]
        process_openweather_gold_message(silver, history, _now=_FAKE_NOW)

        assert silver == silver_before, (
            "process_openweather_gold_message() mutated the input silver_metric"
        )


# ==========================================================
# [28] _now Injection
# ==========================================================

class TestNowInjection:
    """Gate 2 Gold check [28]: _now parameter enables deterministic assertions."""

    def test_28_now_injection_makes_deltas_deterministic(self):
        """[28] Two calls with identical _now and history produce identical momentum blocks."""
        history = [(_ts(25), 18.0)]
        silver  = _make_silver_metric(temperature_celsius=22.4)

        _, gold_a = process_openweather_gold_message(silver, history, _now=_FAKE_NOW)
        _, gold_b = process_openweather_gold_message(silver, history, _now=_FAKE_NOW)

        assert gold_a["momentum_block"] == gold_b["momentum_block"], (
            "_now injection must produce identical momentum blocks on repeated calls"
        )


# ==========================================================
# [29] Metadata Extension Preservation
# ==========================================================

class TestMetadataPreservation:
    """Gate 2 Gold check [29]: Silver metadata_extension fields survive Gold enrichment."""

    def test_29_silver_metadata_fields_preserved_in_gold(self):
        """[29] wind_speed_knots, pressure_hpa, condition_severity, strategic_tag,
        coordinates all carried through to the Gold record's metadata_extension.
        """
        silver = _make_silver_metric(
            hotspot_name="Taipei",
            strategic_tag="taiwan_semiconductor",
            temperature_celsius=22.4,
            wind_speed_ms=8.7,
            pressure_hpa=1008.0,
            humidity_pct=91.0,
            weather_id=502,
        )
        history = [(_ts(25), 18.0)]
        _, gold = process_openweather_gold_message(silver, history, _now=_FAKE_NOW)

        meta = gold["metadata_extension"]

        # Silver fields must survive the build_gold_structured_metric deep copy
        expected_knots = round(8.7 * _MS_TO_KNOTS, 4)
        assert meta["wind_speed_knots"] == pytest.approx(expected_knots, abs=1e-4)
        assert meta["pressure_hpa"]     == pytest.approx(1008.0)
        assert meta["humidity_pct"]     == pytest.approx(91.0)
        assert meta["condition_code"]   == 502
        assert meta["condition_severity"] == CONDITION_ID_TO_SEVERITY[502]   # 4
        assert meta["strategic_tag"]    == "taiwan_semiconductor"
        assert meta["coordinates"]      == {"lat": pytest.approx(_LAT), "lon": pytest.approx(_LON)}

        # Gold-added fields also present
        assert "anomaly_flags"    in meta
        assert "impact_level"     in meta
        assert "trigger_reason"   in meta
