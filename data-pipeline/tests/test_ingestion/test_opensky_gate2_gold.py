"""
Gate 2 (Gold) — Logic Gate: OpenSky Gold Automation Triggers & Momentum.

Validates the Gold Job's OpenSky branch using mock payloads — no live
Flink cluster, no Kafka, no OpenSky API, no OpenAI (fully deterministic math).

What Gate 2 Gold covers (Section 9.3 Gate 2):

  ROUTING & LAYER PROMOTION
  [01] Valid Silver metric + empty history  → (GOLD_STRUCTURED_METRICS, gold_record)
  [02] Gold record has layer == "Gold"       (Silver layer promoted)
  [03] process_opensky_gold_message() always returns GOLD_STRUCTURED_METRICS

  COLD-START GUARD (is_new_market) — design decision 2026-04-04
  [04] Empty price_history → is_new_market == True
  [05] Empty price_history → Aerial_Escalation_Risk suppressed (anomaly_flags == [])
  [06] Empty price_history → Transponder_Deactivation_Alert suppressed (even with silence_events > 0)
  [07] Empty price_history → impact_level == 1 (no triggers)
  [08] Empty price_history → trigger_reason == None
  [09] Empty price_history → change_30d == 0.0

  MOMENTUM BLOCK — WITH PRIOR HISTORY (_now injection for determinism)
  [10] Prior density 25h ago → change_24h computed correctly (fractional)
  [11] Prior density 12h ago → change_24h == 0.0 (more recent than now-24h cutoff)
  [12] Prior density 8d ago  → change_7d computed correctly
  [13] Prior density 31d ago → change_30d computed correctly
  [14] is_new_market == False when price_history is non-empty

  AERIAL_ESCALATION_RISK (change_30d > 0.30 → impact_level=4)
  [15] change_30d == 0.40 → Aerial_Escalation_Risk fires, impact_level == 4
  [16] change_30d == 0.30 → does NOT fire (threshold is strictly > 0.30)
  [17] change_30d == 0.29 → does NOT fire
  [18] change_30d == 1.50 → Aerial_Escalation_Risk fires (large spike)

  TRANSPONDER_DEACTIVATION_ALERT (silence_events > 0 AND bounding_box_id ∈ TENSION_ZONES)
  [19] silence=1 + taiwan_strait        → Transponder_Deactivation_Alert fires, impact_level=4
  [20] silence=1 + washington_dc_airspace → does NOT fire (not in TENSION_ZONES)
  [21] silence=0 + taiwan_strait        → does NOT fire (no silence events)
  [22] silence=3 + iranian_borders      → fires (any tension-zone box)
  [23] silence=1 + strait_of_hormuz     → fires
  [24] silence=1 + bab_al_mandab        → fires
  [25] silence=1 + eastern_mediterranean → fires
  [26] silence=1 + polish_ukrainian_border → fires

  BOTH TRIGGERS FIRE SIMULTANEOUSLY
  [27] change_30d=0.40 + silence=1 + taiwan_strait →
       both Aerial_Escalation_Risk AND Transponder_Deactivation_Alert
  [28] impact_level = max(4, 4) == 4 when both fire

  TRIGGER_REASON
  [29] trigger_reason is pipe-separated string when both triggers fire
  [30] trigger_reason is None when no triggers fire

  ANOMALY_SCORE
  [31] change_30d = 0.40  → anomaly_score == 0.40
  [32] change_30d = -0.20 → anomaly_score == 0.0  (negative → clamped to 0)
  [33] change_30d = 1.80  → anomaly_score == 1.0  (capped at 1.0)
  [34] Cold-start: anomaly_score == 0.0 (no deviation without baseline)

  IMMUTABILITY
  [35] Original silver_metric is not mutated (deep copy check)

  _now INJECTION
  [36] _now parameter enables deterministic delta assertions

  METADATA EXTENSION PRESERVATION
  [37] Silver metadata_extension fields survive Gold enrichment:
       bounding_box_id, aircraft_density_count, transponder_silence_events,
       strategic_tag, bounding_box_coords

Test strategy:
    _make_silver_metric() builds realistic Silver records by calling
    map_opensky_to_silver() directly — same shape the Silver Job produces.
    All momentum assertions use _now injection to prevent wall-clock failures.

References:
    - Section 4.2B: Structural Enrichment — Momentum Block
    - Section B.7:  OpenSky automation triggers (Aerial_Escalation_Risk,
                    Transponder_Deactivation_Alert)
    - Section C.2:  Silver/Gold Structured Metric schema
    - Section 9.3:  Triple-Gate Test Matrix — Gate 2
    - Section 4.4:  Mock-Driven Development (no live API calls)
"""

import copy
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from config.kafka_topics import GOLD_STRUCTURED_METRICS
from ingestion.opensky_producer import BOUNDING_BOXES, OPENSKY_BASE_URL, SOURCE_NAME
from processing.gold_job import (
    _OPENSKY_DENSITY_THRESHOLD,
    _OPENSKY_TENSION_ZONES,
    apply_opensky_automation_triggers,
    process_opensky_gold_message,
)
from processing.silver_job import map_opensky_to_silver
from utils.kafka_utils import build_bronze_message

# ==========================================================
# Reference time — all _now injections use this anchor.
# ==========================================================

_NOW = datetime(2026, 4, 4, 12, 0, 0, tzinfo=timezone.utc)
_TS  = _NOW.isoformat()                          # "2026-04-04T12:00:00+00:00"


# ==========================================================
# Helpers
# ==========================================================

def _make_raw_payload(
    bounding_box_id: str  = "taiwan_strait",
    strategic_tag: str    = "taiwan_strait",
    aircraft_count: int   = 47,
    transponder_silence_events: int = 0,
    states_compact: list  = None,
) -> dict:
    """Build a minimal raw_payload."""
    # Build states_compact with the requested number of silence events
    sc = states_compact
    if sc is None:
        sc = []
        for i in range(transponder_silence_events):
            sc.append({
                "icao24": f"silence{i:04d}",
                "callsign": f"SIL{i:04d}",
                "lat": None, "lon": None,
                "on_ground": False,
                "last_contact": 1743760800,
            })
    return {
        "bounding_box_id":  bounding_box_id,
        "strategic_tag":    strategic_tag,
        "lat_min": 22.0, "lat_max": 27.0,
        "lon_min": 118.0, "lon_max": 123.0,
        "aircraft_count":   aircraft_count,
        "states_compact":   sc,
        "observation_time": 1743760800,
        "fetch_timestamp":  _TS,
        "mode": "static",
    }


def _make_silver_metric(
    bounding_box_id: str  = "taiwan_strait",
    strategic_tag: str    = "taiwan_strait",
    aircraft_count: int   = 47,
    transponder_silence_events: int = 0,
) -> dict:
    """Build a Silver Structured Metric via map_opensky_to_silver()."""
    raw = _make_raw_payload(
        bounding_box_id=bounding_box_id,
        strategic_tag=strategic_tag,
        aircraft_count=aircraft_count,
        transponder_silence_events=transponder_silence_events,
    )
    lat_min, lat_max = raw["lat_min"], raw["lat_max"]
    lon_min, lon_max = raw["lon_min"], raw["lon_max"]
    endpoint = (
        f"{OPENSKY_BASE_URL}"
        f"?lamin={lat_min}&lamax={lat_max}&lomin={lon_min}&lomax={lon_max}"
    )
    envelope = build_bronze_message(
        source_name=SOURCE_NAME,
        source_endpoint=endpoint,
        raw_payload=raw,
    )
    return map_opensky_to_silver(raw, envelope)


def _history_at(hours_ago: int, value: float) -> list[tuple[str, float]]:
    """Build a one-entry price history with a reading `hours_ago` hours before _NOW."""
    ts = (_NOW - timedelta(hours=hours_ago)).isoformat()
    return [(ts, value)]


# ==========================================================
# [01–03] Routing & Layer Promotion
# ==========================================================

class TestRoutingAndPromotion:
    """Gate 2 Gold [01]–[03]: routing and layer field."""

    def test_01_returns_gold_structured_metrics_tuple(self):
        """[01] Valid Silver metric + empty history → (GOLD_STRUCTURED_METRICS, gold_record)."""
        silver = _make_silver_metric()
        topic, record = process_opensky_gold_message(silver, [], _now=_NOW)
        assert topic == GOLD_STRUCTURED_METRICS
        assert record is not None

    def test_02_layer_is_gold(self):
        """[02] Gold record has layer == 'Gold'."""
        silver = _make_silver_metric()
        _, record = process_opensky_gold_message(silver, [], _now=_NOW)
        assert record["layer"] == "Gold"

    def test_03_always_returns_gold_topic(self):
        """[03] process_opensky_gold_message() always returns GOLD_STRUCTURED_METRICS."""
        for count in (0, 1, 100, 500):
            silver = _make_silver_metric(aircraft_count=count)
            topic, _ = process_opensky_gold_message(silver, [], _now=_NOW)
            assert topic == GOLD_STRUCTURED_METRICS


# ==========================================================
# [04–09] Cold-Start Guard
# ==========================================================

class TestColdStartGuard:
    """Gate 2 Gold [04]–[09]: empty price_history suppresses all triggers."""

    def test_04_empty_history_sets_is_new_market_true(self):
        """[04] Empty price_history → is_new_market == True."""
        silver = _make_silver_metric()
        _, record = process_opensky_gold_message(silver, [], _now=_NOW)
        assert record["momentum_block"]["is_new_market"] is True

    def test_05_cold_start_suppresses_aerial_escalation_risk(self):
        """[05] is_new_market=True → Aerial_Escalation_Risk suppressed (anomaly_flags==[])."""
        # With a large density change that would normally trigger — but no history
        silver = _make_silver_metric(aircraft_count=999)
        _, record = process_opensky_gold_message(silver, [], _now=_NOW)
        assert record["metadata_extension"]["anomaly_flags"] == []

    def test_06_cold_start_suppresses_transponder_alert_even_with_silence(self):
        """[06] is_new_market=True → Transponder_Deactivation_Alert suppressed even with silence_events>0."""
        silver = _make_silver_metric(
            bounding_box_id="taiwan_strait",
            transponder_silence_events=5,
        )
        _, record = process_opensky_gold_message(silver, [], _now=_NOW)
        assert "Transponder_Deactivation_Alert" not in record["metadata_extension"]["anomaly_flags"]

    def test_07_cold_start_impact_level_is_1(self):
        """[07] Empty price_history → impact_level == 1 (no triggers)."""
        silver = _make_silver_metric()
        _, record = process_opensky_gold_message(silver, [], _now=_NOW)
        assert record["metadata_extension"]["impact_level"] == 1

    def test_08_cold_start_trigger_reason_is_none(self):
        """[08] Empty price_history → trigger_reason == None."""
        silver = _make_silver_metric()
        _, record = process_opensky_gold_message(silver, [], _now=_NOW)
        assert record["metadata_extension"]["trigger_reason"] is None

    def test_09_cold_start_change_30d_is_zero(self):
        """[09] Empty price_history → change_30d == 0.0."""
        silver = _make_silver_metric()
        _, record = process_opensky_gold_message(silver, [], _now=_NOW)
        assert record["momentum_block"]["change_30d"] == 0.0


# ==========================================================
# [10–14] Momentum Block With Prior History
# ==========================================================

class TestMomentumBlock:
    """Gate 2 Gold [10]–[14]: momentum deltas computed from price_history."""

    def test_10_change_24h_computed_from_25h_history(self):
        """[10] Prior density 25h ago → change_24h computed correctly (fractional)."""
        # reference=30 aircraft 25h ago, current=47 → change_24h=(47-30)/30=0.566...
        history = _history_at(hours_ago=25, value=30.0)
        silver  = _make_silver_metric(aircraft_count=47)
        _, record = process_opensky_gold_message(silver, history, _now=_NOW)
        expected = round((47.0 - 30.0) / 30.0, 6)
        assert record["momentum_block"]["change_24h"] == pytest.approx(expected, abs=1e-5)

    def test_11_change_24h_zero_if_history_within_24h(self):
        """[11] Prior density 12h ago → change_24h == 0.0 (more recent than now-24h cutoff)."""
        history = _history_at(hours_ago=12, value=30.0)
        silver  = _make_silver_metric(aircraft_count=47)
        _, record = process_opensky_gold_message(silver, history, _now=_NOW)
        assert record["momentum_block"]["change_24h"] == 0.0

    def test_12_change_7d_computed_from_8d_history(self):
        """[12] Prior density 8d ago → change_7d computed correctly."""
        history = _history_at(hours_ago=8 * 24, value=40.0)
        silver  = _make_silver_metric(aircraft_count=60)
        _, record = process_opensky_gold_message(silver, history, _now=_NOW)
        expected = round((60.0 - 40.0) / 40.0, 6)
        assert record["momentum_block"]["change_7d"] == pytest.approx(expected, abs=1e-5)

    def test_13_change_30d_computed_from_31d_history(self):
        """[13] Prior density 31d ago → change_30d computed correctly."""
        history = _history_at(hours_ago=31 * 24, value=35.0)
        silver  = _make_silver_metric(aircraft_count=50)
        _, record = process_opensky_gold_message(silver, history, _now=_NOW)
        expected = round((50.0 - 35.0) / 35.0, 6)
        assert record["momentum_block"]["change_30d"] == pytest.approx(expected, abs=1e-5)

    def test_14_is_new_market_false_with_history(self):
        """[14] is_new_market == False when price_history is non-empty."""
        history = _history_at(hours_ago=25, value=30.0)
        silver  = _make_silver_metric()
        _, record = process_opensky_gold_message(silver, history, _now=_NOW)
        assert record["momentum_block"]["is_new_market"] is False


# ==========================================================
# [15–18] Aerial_Escalation_Risk
# ==========================================================

class TestAerialEscalationRisk:
    """Gate 2 Gold [15]–[18]: Aerial_Escalation_Risk trigger boundaries."""

    def _make_with_change_30d(self, change_30d: float) -> dict:
        """Build a Gold record with a specific change_30d value."""
        # Set reference so that (current - ref) / ref = change_30d
        # current = 50, ref = 50 / (1 + change_30d)
        current = 50.0
        if change_30d == 0.0:
            ref = current  # would give 0.0 change
        else:
            ref = current / (1.0 + change_30d)
        history = _history_at(hours_ago=31 * 24, value=ref)
        silver  = _make_silver_metric(aircraft_count=int(current))
        _, record = process_opensky_gold_message(silver, history, _now=_NOW)
        return record

    def test_15_change_30d_040_fires_aerial_escalation(self):
        """[15] change_30d == 0.40 → Aerial_Escalation_Risk fires, impact_level == 4."""
        record = self._make_with_change_30d(0.40)
        assert "Aerial_Escalation_Risk" in record["metadata_extension"]["anomaly_flags"]
        assert record["metadata_extension"]["impact_level"] == 4

    def test_16_change_30d_030_does_not_fire(self):
        """[16] change_30d == 0.30 → does NOT fire (strictly >0.30 required)."""
        record = self._make_with_change_30d(0.30)
        assert "Aerial_Escalation_Risk" not in record["metadata_extension"]["anomaly_flags"]

    def test_17_change_30d_029_does_not_fire(self):
        """[17] change_30d == 0.29 → does NOT fire."""
        record = self._make_with_change_30d(0.29)
        assert "Aerial_Escalation_Risk" not in record["metadata_extension"]["anomaly_flags"]

    def test_18_large_spike_fires(self):
        """[18] change_30d == 1.50 → Aerial_Escalation_Risk fires (large spike)."""
        record = self._make_with_change_30d(1.50)
        assert "Aerial_Escalation_Risk" in record["metadata_extension"]["anomaly_flags"]


# ==========================================================
# [19–26] Transponder_Deactivation_Alert
# ==========================================================

class TestTransponderDeactivationAlert:
    """Gate 2 Gold [19]–[26]: Transponder_Deactivation_Alert trigger conditions."""

    def _run(self, bounding_box_id: str, silence_events: int) -> dict:
        """Run with non-empty history so cold-start guard does not suppress."""
        history = _history_at(hours_ago=25, value=30.0)
        silver  = _make_silver_metric(
            bounding_box_id=bounding_box_id,
            transponder_silence_events=silence_events,
        )
        _, record = process_opensky_gold_message(silver, history, _now=_NOW)
        return record

    def test_19_silence_in_taiwan_strait_fires(self):
        """[19] silence=1 + taiwan_strait → Transponder_Deactivation_Alert fires."""
        record = self._run("taiwan_strait", 1)
        assert "Transponder_Deactivation_Alert" in record["metadata_extension"]["anomaly_flags"]
        assert record["metadata_extension"]["impact_level"] >= 4

    def test_20_silence_in_dc_does_not_fire(self):
        """[20] silence=1 + washington_dc_airspace → does NOT fire (not in TENSION_ZONES)."""
        record = self._run("washington_dc_airspace", 1)
        assert "Transponder_Deactivation_Alert" not in record["metadata_extension"]["anomaly_flags"]

    def test_21_zero_silence_does_not_fire(self):
        """[21] silence=0 + taiwan_strait → does NOT fire."""
        record = self._run("taiwan_strait", 0)
        assert "Transponder_Deactivation_Alert" not in record["metadata_extension"]["anomaly_flags"]

    def test_22_iranian_borders_fires(self):
        """[22] silence=3 + iranian_borders → fires."""
        record = self._run("iranian_borders", 3)
        assert "Transponder_Deactivation_Alert" in record["metadata_extension"]["anomaly_flags"]

    def test_23_strait_of_hormuz_fires(self):
        """[23] silence=1 + strait_of_hormuz → fires."""
        record = self._run("strait_of_hormuz", 1)
        assert "Transponder_Deactivation_Alert" in record["metadata_extension"]["anomaly_flags"]

    def test_24_bab_al_mandab_fires(self):
        """[24] silence=1 + bab_al_mandab → fires."""
        record = self._run("bab_al_mandab", 1)
        assert "Transponder_Deactivation_Alert" in record["metadata_extension"]["anomaly_flags"]

    def test_25_eastern_mediterranean_fires(self):
        """[25] silence=1 + eastern_mediterranean → fires."""
        record = self._run("eastern_mediterranean", 1)
        assert "Transponder_Deactivation_Alert" in record["metadata_extension"]["anomaly_flags"]

    def test_26_polish_ukrainian_border_fires(self):
        """[26] silence=1 + polish_ukrainian_border → fires."""
        record = self._run("polish_ukrainian_border", 1)
        assert "Transponder_Deactivation_Alert" in record["metadata_extension"]["anomaly_flags"]


# ==========================================================
# [27–30] Both Triggers + Trigger Reason
# ==========================================================

class TestBothTriggersAndReason:
    """Gate 2 Gold [27]–[30]: simultaneous trigger firing and trigger_reason format."""

    def _run_both(self) -> dict:
        """Change_30d=0.40 + silence=1 + taiwan_strait → both triggers."""
        # current=50, ref=50/1.40≈35.71 → change_30d=0.40
        ref     = 50.0 / 1.40
        history = _history_at(hours_ago=31 * 24, value=ref)
        silver  = _make_silver_metric(
            bounding_box_id="taiwan_strait",
            aircraft_count=50,
            transponder_silence_events=1,
        )
        _, record = process_opensky_gold_message(silver, history, _now=_NOW)
        return record

    def test_27_both_triggers_fire(self):
        """[27] change_30d>0.30 + silence>0 + tension_zone → both triggers fire."""
        record = self._run_both()
        flags  = record["metadata_extension"]["anomaly_flags"]
        assert "Aerial_Escalation_Risk"          in flags
        assert "Transponder_Deactivation_Alert"  in flags

    def test_28_impact_level_max_is_4(self):
        """[28] Both triggers fire at level 4 → max impact_level == 4."""
        record = self._run_both()
        assert record["metadata_extension"]["impact_level"] == 4

    def test_29_trigger_reason_is_pipe_separated_when_both_fire(self):
        """[29] trigger_reason is pipe-separated string when both triggers fire."""
        record = self._run_both()
        reason = record["metadata_extension"]["trigger_reason"]
        assert reason is not None
        assert "|" in reason

    def test_30_trigger_reason_none_when_no_triggers(self):
        """[30] trigger_reason is None when no triggers fire."""
        history = _history_at(hours_ago=25, value=45.0)
        silver  = _make_silver_metric(aircraft_count=47, transponder_silence_events=0)
        _, record = process_opensky_gold_message(silver, history, _now=_NOW)
        assert record["metadata_extension"]["trigger_reason"] is None


# ==========================================================
# [31–34] Anomaly Score
# ==========================================================

class TestAnomalyScore:
    """Gate 2 Gold [31]–[34]: anomaly_score normalization."""

    def _run_with_change_30d(self, change_30d: float) -> float:
        current = 50.0
        if abs(1.0 + change_30d) < 1e-9:
            ref = 0.0
        else:
            ref = current / (1.0 + change_30d)
        history = _history_at(hours_ago=31 * 24, value=max(ref, 1.0))
        # Adjust current to yield exact change_30d given ref may be adjusted
        actual_current = max(ref, 1.0) * (1.0 + change_30d)
        silver  = _make_silver_metric(aircraft_count=max(1, int(actual_current)))
        _, record = process_opensky_gold_message(silver, history, _now=_NOW)
        return record["metadata_extension"]["anomaly_score"]

    def test_31_positive_change_gives_score_equal_to_change_30d(self):
        """[31] change_30d=0.40 → anomaly_score≈0.40."""
        score = self._run_with_change_30d(0.40)
        assert score == pytest.approx(0.40, abs=0.02)

    def test_32_negative_change_clamped_to_zero(self):
        """[32] change_30d=-0.20 → anomaly_score=0.0 (density drop is not an anomaly here)."""
        history = _history_at(hours_ago=31 * 24, value=60.0)
        silver  = _make_silver_metric(aircraft_count=47)  # 47/60-1 ≈ -0.217
        _, record = process_opensky_gold_message(silver, history, _now=_NOW)
        assert record["metadata_extension"]["anomaly_score"] == 0.0

    def test_33_extreme_spike_capped_at_one(self):
        """[33] change_30d=1.80 → anomaly_score=1.0 (capped)."""
        score = self._run_with_change_30d(1.80)
        assert score == 1.0

    def test_34_cold_start_anomaly_score_is_zero(self):
        """[34] Cold-start: anomaly_score == 0.0."""
        silver = _make_silver_metric()
        _, record = process_opensky_gold_message(silver, [], _now=_NOW)
        assert record["metadata_extension"]["anomaly_score"] == 0.0


# ==========================================================
# [35–37] Immutability, _now Injection, Metadata Preservation
# ==========================================================

class TestImmutabilityAndPreservation:
    """Gate 2 Gold [35]–[37]: deep copy + _now injection + metadata survival."""

    def test_35_original_silver_not_mutated(self):
        """[35] apply_opensky_automation_triggers() does not mutate the input."""
        silver = _make_silver_metric()
        _, gold_base = process_opensky_gold_message(silver, [], _now=_NOW)
        original_meta = copy.deepcopy(gold_base.get("metadata_extension", {}))
        # Run triggers on the gold record — should not mutate gold_base
        history = _history_at(hours_ago=31 * 24, value=30.0)
        silver2 = _make_silver_metric(aircraft_count=50)
        silver_copy = copy.deepcopy(silver2)
        process_opensky_gold_message(silver2, history, _now=_NOW)
        assert silver2 == silver_copy, "silver_metric was mutated by process_opensky_gold_message"

    def test_36_now_injection_produces_deterministic_results(self):
        """[36] Same _now produces identical momentum values across calls."""
        history = _history_at(hours_ago=25, value=30.0)
        silver  = _make_silver_metric(aircraft_count=47)
        _, record1 = process_opensky_gold_message(silver, history, _now=_NOW)
        _, record2 = process_opensky_gold_message(silver, history, _now=_NOW)
        assert record1["momentum_block"]["change_24h"] == record2["momentum_block"]["change_24h"]

    def test_37_silver_metadata_extension_survives_gold_enrichment(self):
        """[37] Silver metadata_extension fields survive Gold enrichment."""
        history = _history_at(hours_ago=25, value=30.0)
        silver  = _make_silver_metric(
            bounding_box_id="strait_of_hormuz",
            aircraft_count=47,
            transponder_silence_events=2,
        )
        _, record = process_opensky_gold_message(silver, history, _now=_NOW)
        meta = record["metadata_extension"]
        assert meta.get("bounding_box_id")            == "strait_of_hormuz"
        assert meta.get("aircraft_density_count")     == 47
        assert meta.get("transponder_silence_events") == 2
        assert "strategic_tag"        in meta
        assert "bounding_box_coords"  in meta
