"""
Gate 3 — Persistence Gate: OpenWeather momentum_vault Live PostgreSQL Validation.

Verifies that the full OpenWeather pipeline (Bronze → Silver → Gold → momentum_vault)
writes correctly to a live TimescaleDB hypertable and that all fetch helpers
return accurate, queryable data.

Requires: PostgreSQL container running with momentum_vault table initialised.
    docker compose -f infrastructure/docker-compose.yml up -d postgres

What Gate 3 covers (Section 9.3 Gate 3):
    [1]  momentum_vault.insert() accepts an OpenWeather Gold record without error
    [2]  fetch_latest("openweather", "Taipei") returns the inserted row
    [3]  fetch_latest returns correct current_value (temperature_celsius)
    [4]  fetch_latest returns source_name == "openweather"
    [5]  metadata_extension.strategic_tag survives the JSONB round-trip
    [6]  metadata_extension.condition_severity survives the JSONB round-trip
    [7]  metadata_extension.wind_speed_knots survives the JSONB round-trip
    [8]  fetch_time_series("openweather", "Taipei") returns the inserted row
    [9]  fetch_time_series returns oldest-first ordering (two records)
    [10] insert() is idempotent — ON CONFLICT (metric_id, timestamp_utc) DO NOTHING
         (no exception on duplicate insert)
    [11] Idempotency: only one row exists after two identical inserts
    [12] Natural_Disaster_Alert in anomaly_flags stored correctly in JSONB
    [13] Tech_Supply_Risk in anomaly_flags stored correctly in JSONB
    [14] trigger_reason string stored and retrieved correctly
    [15] impact_level == 5 stored correctly when Natural_Disaster_Alert fires
    [16] is_new_market == True stored correctly for first observation
    [17] change_24h == 0.0 stored for first observation (empty price_history)
    [18] Non-zero momentum values stored and retrieved with full precision

Architecture note — test isolation:
    Each test record uses canonical_event_id = "test_{run_id}_{label}" so the
    cleanup_momentum_vault fixture deletes all test rows by prefix after each test.
    No production data is touched (Section 9.3).

References:
    - Section 5.3:  Momentum Vault — TimescaleDB hypertable
    - Section 4.2B: Momentum Block — pre-computed deltas
    - Section B.6:  OpenWeather automation triggers
    - Section C.2:  Silver/Gold Structured Metric schema
    - Section 9.3:  Triple-Gate Test Matrix — Gate 3
"""

import uuid
from datetime import datetime, timezone

import pytest

from ingestion.openweather_producer import SOURCE_NAME, OWM_BASE_URL
from persistence.momentum_vault import (
    fetch_latest,
    fetch_time_series,
    insert,
)
from processing.gold_job import process_openweather_gold_message
from processing.silver_job import map_openweather_to_silver, _MS_TO_KNOTS
from utils.kafka_utils import build_bronze_message

# ==========================================================
# Fixtures & Helpers
# ==========================================================

pytestmark = pytest.mark.usefixtures("db_available")

_LAT  = 25.033
_LON  = 121.5654
_ENDPOINT = f"{OWM_BASE_URL}?lat={_LAT}&lon={_LON}&units=metric"

# Fixed reference time — prevents timestamp collisions between test records
# from different test functions running in the same second.
_BASE_TS    = "2026-04-01T00:00:00+00:00"
_BASE_TS_DT = datetime.fromisoformat(_BASE_TS)


def _build_openweather_gold(
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
    test_run_id: str = "test",
    label: str = "",
) -> dict:
    """
    Build a complete OpenWeather Gold record via the real pipeline:
    raw_payload → Bronze envelope → Silver map → Gold process (empty history).

    Uses the actual production transform functions so Gate 3 validates
    the full stack end-to-end, not just the persistence layer in isolation.
    The _now parameter is fixed to _BASE_TS_DT for deterministic deltas.
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
        "fetch_timestamp":     _BASE_TS,
        "mode":                mode,
    }

    endpoint = f"{OWM_BASE_URL}?lat={lat}&lon={lon}&units=metric"
    envelope = build_bronze_message(
        source_name=SOURCE_NAME,
        source_endpoint=endpoint,
        raw_payload=raw,
    )

    # Override canonical_event_id to the namespaced test ID so the cleanup
    # fixture deletes only this test's rows.
    tag = f"test_{test_run_id}_{label or hotspot_name.lower()}"
    envelope["event_id"] = tag

    silver = map_openweather_to_silver(raw, envelope)
    _, gold = process_openweather_gold_message(silver, [], _now=_BASE_TS_DT)

    # Stamp metric_id deterministically so idempotency tests get the same UUID.
    gold["core_identity"]["metric_id"] = str(uuid.uuid5(uuid.NAMESPACE_DNS, tag))
    return gold


# ==========================================================
# Gate 3 — Basic Insert & Fetch [1–9]
# ==========================================================

@pytest.mark.usefixtures("cleanup_momentum_vault")
class TestOpenWeatherBasicPersistence:
    """
    Verifies insert(), fetch_latest(), and fetch_time_series() for a
    normal OpenWeather Taipei observation (Gate 3 checks 1-9).
    """

    @pytest.fixture(scope="function")
    def gold_taipei(self, test_run_id) -> dict:
        """Build and insert a Taipei Gold record; return the record."""
        gold = _build_openweather_gold(
            hotspot_name="Taipei", temperature_celsius=22.4,
            weather_id=502, test_run_id=test_run_id, label="taipei_basic",
        )
        insert(gold)
        return gold

    def test_insert_returns_metric_id(self, test_run_id):
        """[1] insert() must return a non-empty metric_id string without raising."""
        gold = _build_openweather_gold(
            hotspot_name="Kyiv", strategic_tag="ukraine_conflict",
            weather_id=500, temperature_celsius=5.0,
            test_run_id=test_run_id, label="kyiv_insert_check",
        )
        metric_id = insert(gold)
        assert isinstance(metric_id, str)
        assert len(metric_id) > 0

    def test_fetch_latest_returns_row(self, gold_taipei):
        """[2] fetch_latest() must return a non-None dict for an inserted hotspot."""
        row = fetch_latest("openweather", "Taipei")
        assert row is not None, "fetch_latest returned None — row was not inserted"

    def test_fetch_latest_correct_value(self, gold_taipei):
        """[3] fetch_latest() must return the exact temperature_celsius that was inserted."""
        row = fetch_latest("openweather", "Taipei")
        assert row is not None
        assert row["current_value"] == pytest.approx(22.4, rel=1e-5)

    def test_fetch_latest_correct_source_name(self, gold_taipei):
        """[4] fetch_latest() must return source_name == 'openweather'."""
        row = fetch_latest("openweather", "Taipei")
        assert row is not None
        assert row["source_name"] == "openweather"

    def test_fetch_latest_metadata_strategic_tag(self, gold_taipei):
        """[5] metadata_extension.strategic_tag must survive the JSONB round-trip."""
        row = fetch_latest("openweather", "Taipei")
        assert row is not None
        assert row["metadata_extension"].get("strategic_tag") == "taiwan_semiconductor"

    def test_fetch_latest_metadata_condition_severity(self, gold_taipei):
        """[6] metadata_extension.condition_severity must survive the JSONB round-trip."""
        row = fetch_latest("openweather", "Taipei")
        assert row is not None
        # weather_id=502 → severity 4
        assert row["metadata_extension"].get("condition_severity") == 4

    def test_fetch_latest_metadata_wind_speed_knots(self, gold_taipei):
        """[7] metadata_extension.wind_speed_knots must survive the JSONB round-trip."""
        row = fetch_latest("openweather", "Taipei")
        assert row is not None
        expected_knots = round(8.7 * _MS_TO_KNOTS, 4)
        stored_knots   = row["metadata_extension"].get("wind_speed_knots")
        assert stored_knots is not None, "wind_speed_knots missing from stored metadata"
        assert stored_knots == pytest.approx(expected_knots, rel=1e-4)

    def test_fetch_time_series_returns_row(self, gold_taipei):
        """[8] fetch_time_series() must return at least the inserted row."""
        rows = fetch_time_series("openweather", "Taipei", hours=365 * 24)
        assert len(rows) >= 1

    def test_fetch_time_series_oldest_first(self, test_run_id):
        """
        [9] fetch_time_series() must return rows ordered oldest-first (ASC).

        Insert two Taipei observations with different timestamps, then verify
        the older one (lower temperature) comes first.
        """
        # Older record: temperature 18.0 at 2025-12-01
        raw_old = {
            "hotspot_name":        "Taipei",
            "lat":                 _LAT, "lon": _LON,
            "strategic_tag":       "taiwan_semiconductor",
            "temperature_celsius": 18.0,
            "wind_speed_ms":       5.0,
            "pressure_hpa":        1010.0, "humidity_pct": 80.0,
            "weather_id":          800,   # clear sky → severity 1
            "weather_main":        "Clear",
            "weather_description": "clear sky",
            "official_alerts":     [],
            "fetch_timestamp":     "2025-12-01T00:00:00+00:00",
            "mode":                "static",
        }
        env_old = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=_ENDPOINT,
            raw_payload=raw_old,
        )
        env_old["event_id"] = f"test_{test_run_id}_taipei_old"
        silver_old = map_openweather_to_silver(raw_old, env_old)
        _, gold_old = process_openweather_gold_message(
            silver_old, [], _now=datetime(2025, 12, 1, tzinfo=__import__("datetime").timezone.utc)
        )
        gold_old["core_identity"]["metric_id"] = str(
            uuid.uuid5(uuid.NAMESPACE_DNS, env_old["event_id"])
        )

        # Newer record: temperature 22.4 at 2026-04-01
        raw_new = dict(raw_old)
        raw_new["temperature_celsius"] = 22.4
        raw_new["fetch_timestamp"]     = "2026-04-01T00:00:00+00:00"
        env_new = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=_ENDPOINT,
            raw_payload=raw_new,
        )
        env_new["event_id"] = f"test_{test_run_id}_taipei_new"
        silver_new = map_openweather_to_silver(raw_new, env_new)
        _, gold_new = process_openweather_gold_message(silver_new, [], _now=_BASE_TS_DT)
        gold_new["core_identity"]["metric_id"] = str(
            uuid.uuid5(uuid.NAMESPACE_DNS, env_new["event_id"])
        )

        insert(gold_old)
        insert(gold_new)

        rows = fetch_time_series("openweather", "Taipei", hours=400 * 24)
        test_rows = [
            r for r in rows
            if r["canonical_event_id"].startswith(f"test_{test_run_id}_taipei")
        ]
        assert len(test_rows) == 2
        assert test_rows[0]["timestamp_utc"] <= test_rows[1]["timestamp_utc"], (
            "fetch_time_series must return oldest-first (ASC timestamp)"
        )


# ==========================================================
# Gate 3 — Idempotency [10–11]
# ==========================================================

@pytest.mark.usefixtures("cleanup_momentum_vault")
class TestOpenWeatherIdempotency:
    """Verifies ON CONFLICT (metric_id, timestamp_utc) DO NOTHING [10, 11]."""

    def test_duplicate_insert_does_not_raise(self, test_run_id):
        """
        [10] Inserting the same Gold record twice must not raise an exception.

        The momentum_vault has ON CONFLICT (metric_id, timestamp_utc) DO NOTHING,
        ensuring Flink exactly-once re-deliveries never fail with a duplicate key
        error (Section 4.3 — idempotent insert pattern).
        """
        gold = _build_openweather_gold(
            hotspot_name="Tel_Aviv", strategic_tag="israel_tech",
            weather_id=800, temperature_celsius=28.0,
            test_run_id=test_run_id, label="telaviv_idem",
        )
        insert(gold)
        insert(gold)   # second insert — must not raise

    def test_duplicate_insert_does_not_create_extra_row(self, test_run_id):
        """[11] After two identical inserts, only one row must exist in the DB."""
        gold = _build_openweather_gold(
            hotspot_name="Washington_DC", strategic_tag="us_federal_policy",
            weather_id=600, temperature_celsius=2.0,
            test_run_id=test_run_id, label="dc_idem",
        )
        insert(gold)
        insert(gold)

        canon_id = gold["core_identity"]["canonical_event_id"]
        rows = fetch_time_series("openweather", "Washington_DC", hours=365 * 24)
        matching = [r for r in rows if r["canonical_event_id"] == canon_id]
        assert len(matching) == 1, (
            f"Expected 1 row after duplicate insert, found {len(matching)}"
        )


# ==========================================================
# Gate 3 — Automation Trigger Fields in JSONB [12–15]
# ==========================================================

@pytest.mark.usefixtures("cleanup_momentum_vault")
class TestOpenWeatherTriggerPersistence:
    """
    Verifies that automation trigger fields (anomaly_flags, trigger_reason,
    impact_level) are stored and retrievable correctly via JSONB [12-15].
    """

    def test_natural_disaster_alert_true_persists(self, test_run_id):
        """
        [12] A Gold record with Natural_Disaster_Alert in anomaly_flags must
        store that flag correctly in the JSONB metadata_extension column.

        We manually set anomaly_flags and impact_level to simulate a triggered
        record — Gate 2 Gold verified the trigger fires; Gate 3 verifies storage.
        """
        gold = _build_openweather_gold(
            hotspot_name="Taipei", weather_id=502,
            strategic_tag="taiwan_semiconductor",
            test_run_id=test_run_id, label="taipei_disaster",
        )
        # Simulate triggered state (Gate 2 verified trigger fires at severity 4)
        gold["metadata_extension"]["anomaly_flags"]  = ["Natural_Disaster_Alert", "Tech_Supply_Risk"]
        gold["metadata_extension"]["impact_level"]   = 5
        gold["metadata_extension"]["trigger_reason"] = (
            "Natural_Disaster_Alert: 'Taipei' condition_severity=4 ≥ 4. "
            "Significant weather disruption detected. | "
            "Tech_Supply_Risk: extreme weather (severity=4) at 'Taipei' "
            "(tag='taiwan_semiconductor')."
        )
        insert(gold)

        row = fetch_latest("openweather", "Taipei")
        assert row is not None
        ext = row["metadata_extension"]
        flags = ext.get("anomaly_flags", [])
        assert "Natural_Disaster_Alert" in flags, (
            "Natural_Disaster_Alert must be present in anomaly_flags after JSONB round-trip"
        )

    def test_tech_supply_risk_persists(self, test_run_id):
        """[13] Tech_Supply_Risk in anomaly_flags must survive JSONB storage."""
        gold = _build_openweather_gold(
            hotspot_name="Hsinchu_Science_Park", weather_id=502,
            strategic_tag="taiwan_semiconductor",
            test_run_id=test_run_id, label="hsinchu_tech",
        )
        gold["metadata_extension"]["anomaly_flags"]  = ["Natural_Disaster_Alert", "Tech_Supply_Risk"]
        gold["metadata_extension"]["impact_level"]   = 5
        gold["metadata_extension"]["trigger_reason"] = (
            "Natural_Disaster_Alert: 'Hsinchu_Science_Park' condition_severity=4. | "
            "Tech_Supply_Risk: extreme weather at 'Hsinchu_Science_Park'."
        )
        insert(gold)

        row = fetch_latest("openweather", "Hsinchu_Science_Park")
        assert row is not None
        flags = row["metadata_extension"].get("anomaly_flags", [])
        assert "Tech_Supply_Risk" in flags, (
            "Tech_Supply_Risk must be present in anomaly_flags after JSONB round-trip"
        )

    def test_trigger_reason_stored_correctly(self, test_run_id):
        """[14] trigger_reason string must survive JSONB storage and remain non-empty."""
        reason_text = (
            "Natural_Disaster_Alert: 'Suez_Canal' condition_severity=5 ≥ 4. "
            "Significant weather disruption detected."
        )
        gold = _build_openweather_gold(
            hotspot_name="Suez_Canal", strategic_tag="suez_canal",
            weather_id=781,  # tornado → severity 5
            test_run_id=test_run_id, label="suez_reason",
        )
        gold["metadata_extension"]["anomaly_flags"]  = ["Natural_Disaster_Alert"]
        gold["metadata_extension"]["impact_level"]   = 5
        gold["metadata_extension"]["trigger_reason"] = reason_text
        insert(gold)

        row = fetch_latest("openweather", "Suez_Canal")
        assert row is not None
        stored_reason = row["metadata_extension"].get("trigger_reason")
        assert isinstance(stored_reason, str) and len(stored_reason) > 0, (
            "trigger_reason must be a non-empty string in DB when trigger fires"
        )

    def test_impact_level_5_stored_when_disaster_fires(self, test_run_id):
        """[15] impact_level == 5 must be stored correctly in JSONB for Natural_Disaster_Alert."""
        gold = _build_openweather_gold(
            hotspot_name="Port_of_Houston", strategic_tag="port_of_houston",
            weather_id=504,  # extreme rain → severity 5
            test_run_id=test_run_id, label="houston_impact",
        )
        gold["metadata_extension"]["anomaly_flags"]  = ["Natural_Disaster_Alert"]
        gold["metadata_extension"]["impact_level"]   = 5
        gold["metadata_extension"]["trigger_reason"] = (
            "Natural_Disaster_Alert: 'Port_of_Houston' condition_severity=5."
        )
        insert(gold)

        row = fetch_latest("openweather", "Port_of_Houston")
        assert row is not None
        assert row["metadata_extension"].get("impact_level") == 5, (
            "impact_level=5 must survive JSONB round-trip when Natural_Disaster_Alert fires"
        )


# ==========================================================
# Gate 3 — Momentum Block Persistence [16–18]
# ==========================================================

@pytest.mark.usefixtures("cleanup_momentum_vault")
class TestOpenWeatherMomentumPersistence:
    """
    Verifies momentum_block values are stored and retrieved correctly [16-18].
    """

    def test_is_new_market_true_for_first_observation(self, test_run_id):
        """[16] First observation (empty history) → is_new_market=True stored correctly."""
        gold = _build_openweather_gold(
            hotspot_name="Ukraine_Wheat_Belt", strategic_tag="ukraine_agriculture",
            weather_id=800, temperature_celsius=15.0,
            test_run_id=test_run_id, label="ukraine_first",
        )
        assert gold["momentum_block"]["is_new_market"] is True
        insert(gold)

        row = fetch_latest("openweather", "Ukraine_Wheat_Belt")
        assert row is not None
        assert row["is_new_market"] is True

    def test_change_24h_zero_for_first_observation(self, test_run_id):
        """[17] First observation → change_24h == 0.0 (no prior history, empty price_history)."""
        gold = _build_openweather_gold(
            hotspot_name="Strait_of_Hormuz", strategic_tag="strait_of_hormuz",
            weather_id=800, temperature_celsius=32.0,
            test_run_id=test_run_id, label="hormuz_first",
        )
        insert(gold)

        row = fetch_latest("openweather", "Strait_of_Hormuz")
        assert row is not None
        assert row["change_24h"] == pytest.approx(0.0)

    def test_nonzero_momentum_stored_correctly(self, test_run_id):
        """
        [18] Non-zero momentum values must be stored and retrieved with full precision.

        Manually override the momentum_block to simulate a record produced
        with a non-empty history, then verify the DB round-trip preserves values.
        """
        gold = _build_openweather_gold(
            hotspot_name="Tokyo", strategic_tag="japan_tech",
            weather_id=800, temperature_celsius=18.0,
            test_run_id=test_run_id, label="tokyo_momentum",
        )
        gold["momentum_block"]["change_24h"]    = 0.087654
        gold["momentum_block"]["change_7d"]     = -0.123456
        gold["momentum_block"]["change_30d"]    = 0.456789
        gold["momentum_block"]["is_new_market"] = False
        insert(gold)

        row = fetch_latest("openweather", "Tokyo")
        assert row is not None
        assert row["change_24h"]  == pytest.approx(0.087654,  rel=1e-5)
        assert row["change_7d"]   == pytest.approx(-0.123456, rel=1e-5)
        assert row["change_30d"]  == pytest.approx(0.456789,  rel=1e-5)
        assert row["is_new_market"] is False
