"""
Gate 3 — Persistence Gate: OpenSky momentum_vault Live PostgreSQL Validation.

Verifies that the full OpenSky pipeline (Bronze → Silver → Gold → momentum_vault)
writes correctly to a live TimescaleDB hypertable and that all fetch helpers
return accurate, queryable data.

Requires: PostgreSQL container running with momentum_vault table initialised.
    docker compose -f infrastructure/docker-compose.yml up -d postgres

What Gate 3 covers (Section 9.3 Gate 3):
    [1]  momentum_vault.insert() accepts an OpenSky Gold record without error
    [2]  fetch_latest("opensky", "taiwan_strait") returns the inserted row
    [3]  fetch_latest returns correct current_value (aircraft_density_count)
    [4]  fetch_latest returns source_name == "opensky"
    [5]  metadata_extension.bounding_box_id survives the JSONB round-trip
    [6]  metadata_extension.transponder_silence_events survives the JSONB round-trip
    [7]  metadata_extension.anomaly_score survives the JSONB round-trip
    [8]  fetch_time_series("opensky", "taiwan_strait") returns the inserted row
    [9]  fetch_time_series returns oldest-first ordering (two records)
    [10] insert() is idempotent — ON CONFLICT (metric_id, timestamp_utc) DO NOTHING
         (no exception on duplicate insert)
    [11] Idempotency: only one row exists after two identical inserts
    [12] Aerial_Escalation_Risk in anomaly_flags stored correctly in JSONB
    [13] Transponder_Deactivation_Alert in anomaly_flags stored correctly
    [14] trigger_reason string stored and retrieved correctly
    [15] impact_level == 4 stored correctly when Aerial_Escalation_Risk fires
    [16] is_new_market == True stored correctly for first observation (cold start)
    [17] change_30d == 0.0 stored for first observation (empty price_history)
    [18] Non-zero momentum values stored and retrieved with full precision

Architecture note — test isolation:
    Each test record uses canonical_event_id = "test_{run_id}_{label}" so the
    cleanup_momentum_vault fixture deletes all test rows by prefix after each test.
    No production data is touched (Section 9.3).

References:
    - Section 5.3:  Momentum Vault — TimescaleDB hypertable
    - Section 4.2B: Momentum Block — pre-computed deltas
    - Section B.7:  OpenSky automation triggers
    - Section C.2:  Silver/Gold Structured Metric schema
    - Section 9.3:  Triple-Gate Test Matrix — Gate 3
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from ingestion.opensky_producer import BOUNDING_BOXES, OPENSKY_BASE_URL, SOURCE_NAME
from persistence.momentum_vault import (
    fetch_latest,
    fetch_time_series,
    insert,
)
from processing.gold_job import process_opensky_gold_message
from processing.silver_job import map_opensky_to_silver
from utils.kafka_utils import build_bronze_message

# ==========================================================
# Fixtures & Helpers
# ==========================================================

pytestmark = pytest.mark.usefixtures("db_available")

_BOX = BOUNDING_BOXES[1]  # taiwan_strait
_BASE_TS    = "2026-04-04T00:00:00+00:00"
_BASE_TS_DT = datetime.fromisoformat(_BASE_TS)


def _build_opensky_gold(
    bounding_box_id: str  = "taiwan_strait",
    strategic_tag: str    = "taiwan_strait",
    aircraft_count: int   = 47,
    transponder_silence_events: int = 0,
    price_history: list   = None,
    test_run_id: str      = "test",
    label: str            = "",
) -> dict:
    """
    Build a complete OpenSky Gold record via the real pipeline:
    raw_payload → Bronze envelope → Silver map → Gold process.

    Uses the actual production transform functions so Gate 3 validates
    the full stack end-to-end, not just the persistence layer in isolation.
    _now is fixed to _BASE_TS_DT for deterministic deltas.
    """
    # Build states_compact with the requested silence events
    states_compact = []
    for i in range(transponder_silence_events):
        states_compact.append({
            "icao24": f"silence{i:04d}",
            "callsign": f"SIL{i:04d}",
            "lat": None, "lon": None,
            "on_ground": False,
            "last_contact": 1743760800,
        })

    raw = {
        "bounding_box_id":  bounding_box_id,
        "strategic_tag":    strategic_tag,
        "lat_min": _BOX["lat_min"], "lat_max": _BOX["lat_max"],
        "lon_min": _BOX["lon_min"], "lon_max": _BOX["lon_max"],
        "aircraft_count":   aircraft_count,
        "states_compact":   states_compact,
        "observation_time": 1743760800,
        "fetch_timestamp":  _BASE_TS,
        "mode":             "static",
    }

    endpoint = (
        f"{OPENSKY_BASE_URL}"
        f"?lamin={_BOX['lat_min']}&lamax={_BOX['lat_max']}"
        f"&lomin={_BOX['lon_min']}&lomax={_BOX['lon_max']}"
    )
    envelope = build_bronze_message(
        source_name=SOURCE_NAME,
        source_endpoint=endpoint,
        raw_payload=raw,
    )

    # Override canonical_event_id to the namespaced test ID so the cleanup
    # fixture deletes only this test's rows.
    tag = f"test_{test_run_id}_{label or bounding_box_id}"
    envelope["event_id"] = tag

    silver = map_opensky_to_silver(raw, envelope)
    _, gold = process_opensky_gold_message(
        silver, price_history or [], _now=_BASE_TS_DT
    )

    # Stamp metric_id deterministically so idempotency tests get the same UUID.
    gold["core_identity"]["metric_id"] = str(uuid.uuid5(uuid.NAMESPACE_DNS, tag))
    return gold


# ==========================================================
# Gate 3 — Basic Insert & Fetch [1–9]
# ==========================================================

@pytest.mark.usefixtures("cleanup_momentum_vault")
class TestOpenSkyBasicPersistence:
    """
    Verifies insert(), fetch_latest(), and fetch_time_series() for a
    normal OpenSky taiwan_strait observation (Gate 3 checks 1–9).
    """

    @pytest.fixture(scope="function")
    def gold_taiwan(self, test_run_id) -> dict:
        """Build and insert a Taiwan Strait Gold record; return the record."""
        gold = _build_opensky_gold(
            bounding_box_id="taiwan_strait",
            aircraft_count=47,
            transponder_silence_events=1,
            test_run_id=test_run_id,
            label="taiwan_basic",
        )
        insert(gold)
        return gold

    def test_1_insert_returns_metric_id(self, test_run_id):
        """[1] insert() must return a non-empty metric_id string without raising."""
        gold = _build_opensky_gold(
            bounding_box_id="strait_of_hormuz",
            aircraft_count=22,
            test_run_id=test_run_id,
            label="hormuz_insert_check",
        )
        metric_id = insert(gold)
        assert isinstance(metric_id, str)
        assert len(metric_id) > 0

    def test_2_fetch_latest_returns_row(self, gold_taiwan):
        """[2] fetch_latest("opensky", "taiwan_strait") must return a non-None dict."""
        row = fetch_latest("opensky", "taiwan_strait")
        assert row is not None, "fetch_latest returned None — row was not inserted"

    def test_3_fetch_latest_correct_value(self, gold_taiwan):
        """[3] fetch_latest() must return the exact aircraft_density_count that was inserted."""
        row = fetch_latest("opensky", "taiwan_strait")
        assert row is not None
        assert row["current_value"] == pytest.approx(47.0, rel=1e-5)

    def test_4_fetch_latest_correct_source_name(self, gold_taiwan):
        """[4] fetch_latest() must return source_name == 'opensky'."""
        row = fetch_latest("opensky", "taiwan_strait")
        assert row is not None
        assert row["source_name"] == "opensky"

    def test_5_metadata_bounding_box_id_survives_jsonb(self, gold_taiwan):
        """[5] metadata_extension.bounding_box_id must survive the JSONB round-trip."""
        row = fetch_latest("opensky", "taiwan_strait")
        assert row is not None
        assert row["metadata_extension"].get("bounding_box_id") == "taiwan_strait"

    def test_6_metadata_transponder_silence_survives_jsonb(self, gold_taiwan):
        """[6] metadata_extension.transponder_silence_events survives JSONB round-trip."""
        row = fetch_latest("opensky", "taiwan_strait")
        assert row is not None
        # transponder_silence_events=1 was set in the fixture
        assert row["metadata_extension"].get("transponder_silence_events") == 1

    def test_7_metadata_anomaly_score_survives_jsonb(self, gold_taiwan):
        """[7] metadata_extension.anomaly_score survives the JSONB round-trip."""
        row = fetch_latest("opensky", "taiwan_strait")
        assert row is not None
        # Cold-start (empty price_history) → anomaly_score=0.0
        assert row["metadata_extension"].get("anomaly_score") == pytest.approx(0.0, abs=1e-6)

    def test_8_fetch_time_series_returns_row(self, gold_taiwan):
        """[8] fetch_time_series() must return at least the inserted row."""
        rows = fetch_time_series("opensky", "taiwan_strait", hours=365 * 24)
        assert len(rows) >= 1

    def test_9_fetch_time_series_oldest_first(self, test_run_id):
        """
        [9] fetch_time_series() must return rows ordered oldest-first (ASC).

        Insert two Taiwan Strait observations with different timestamps, then
        verify the older one (lower count) comes first.
        """
        # Older record: 30 aircraft at a timestamp 2 days before _BASE_TS
        _older_ts = (_BASE_TS_DT - timedelta(days=2)).isoformat()

        # Build the older record
        raw_old = {
            "bounding_box_id": "taiwan_strait",
            "strategic_tag":   "taiwan_strait",
            "lat_min": _BOX["lat_min"], "lat_max": _BOX["lat_max"],
            "lon_min": _BOX["lon_min"], "lon_max": _BOX["lon_max"],
            "aircraft_count":  30,
            "states_compact":  [],
            "observation_time": 1743587400,
            "fetch_timestamp": _older_ts,
            "mode": "static",
        }
        endpoint = (
            f"{OPENSKY_BASE_URL}"
            f"?lamin={_BOX['lat_min']}&lamax={_BOX['lat_max']}"
            f"&lomin={_BOX['lon_min']}&lomax={_BOX['lon_max']}"
        )
        env_old = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=endpoint,
            raw_payload=raw_old,
        )
        tag_old = f"test_{test_run_id}_taiwan_older"
        env_old["event_id"] = tag_old
        silver_old = map_opensky_to_silver(raw_old, env_old)
        older_ts_dt = _BASE_TS_DT - timedelta(days=2)
        _, gold_old = process_opensky_gold_message(silver_old, [], _now=older_ts_dt)
        gold_old["core_identity"]["metric_id"] = str(uuid.uuid5(uuid.NAMESPACE_DNS, tag_old))

        # Build the newer record
        gold_new = _build_opensky_gold(
            aircraft_count=47,
            test_run_id=test_run_id,
            label="taiwan_newer",
        )

        insert(gold_old)
        insert(gold_new)

        rows = fetch_time_series("opensky", "taiwan_strait", hours=365 * 24)
        counts = [r["current_value"] for r in rows]
        assert counts[0] <= counts[-1], (
            f"Expected oldest-first ordering (count 30 before 47), got: {counts}"
        )


# ==========================================================
# Gate 3 — Idempotency [10–11]
# ==========================================================

@pytest.mark.usefixtures("cleanup_momentum_vault")
class TestOpenSkyIdempotency:
    """Verifies that duplicate inserts are silently ignored (Gate 3 checks 10–11)."""

    def test_10_duplicate_insert_no_exception(self, test_run_id):
        """[10] insert() twice with the same metric_id → no exception raised."""
        gold = _build_opensky_gold(
            aircraft_count=35,
            test_run_id=test_run_id,
            label="dedup_no_exc",
        )
        insert(gold)
        insert(gold)  # must not raise

    def test_11_duplicate_insert_single_row(self, test_run_id):
        """[11] Only one row exists in momentum_vault after two identical inserts."""
        tag  = f"test_{test_run_id}_dedup_count"
        gold = _build_opensky_gold(
            aircraft_count=35,
            test_run_id=test_run_id,
            label="dedup_count",
        )
        insert(gold)
        insert(gold)

        rows = fetch_time_series("opensky", "taiwan_strait", hours=365 * 24)
        matching = [r for r in rows if r["canonical_event_id"] == gold["core_identity"]["canonical_event_id"]]
        assert len(matching) == 1, (
            f"Expected 1 row after duplicate insert, found {len(matching)}"
        )


# ==========================================================
# Gate 3 — Trigger Flags & Momentum Storage [12–18]
# ==========================================================

@pytest.mark.usefixtures("cleanup_momentum_vault")
class TestOpenSkyTriggerAndMomentumStorage:
    """Verifies trigger flags and momentum values survive the DB round-trip (Gate 3 checks 12–18)."""

    def test_12_aerial_escalation_stored_in_jsonb(self, test_run_id):
        """[12] Aerial_Escalation_Risk in anomaly_flags stored correctly in JSONB."""
        # 30-day history: reference=35 aircraft 31 days ago.
        # current=50 → change_30d=(50-35)/35≈0.429 > 0.30 → fires.
        history_ts = (_BASE_TS_DT - timedelta(days=31)).isoformat()
        price_history = [(history_ts, 35.0)]
        gold = _build_opensky_gold(
            bounding_box_id="taiwan_strait",
            aircraft_count=50,
            price_history=price_history,
            test_run_id=test_run_id,
            label="aerial_flag",
        )
        insert(gold)
        row = fetch_latest("opensky", "taiwan_strait")
        assert row is not None
        flags = row["metadata_extension"].get("anomaly_flags", [])
        assert "Aerial_Escalation_Risk" in flags

    def test_13_transponder_alert_stored_in_jsonb(self, test_run_id):
        """[13] Transponder_Deactivation_Alert in anomaly_flags stored correctly."""
        # Non-empty history so cold-start guard doesn't suppress.
        history_ts    = (_BASE_TS_DT - timedelta(hours=25)).isoformat()
        price_history = [(history_ts, 40.0)]
        gold = _build_opensky_gold(
            bounding_box_id="taiwan_strait",
            aircraft_count=42,
            transponder_silence_events=2,
            price_history=price_history,
            test_run_id=test_run_id,
            label="transponder_flag",
        )
        insert(gold)
        row = fetch_latest("opensky", "taiwan_strait")
        assert row is not None
        flags = row["metadata_extension"].get("anomaly_flags", [])
        assert "Transponder_Deactivation_Alert" in flags

    def test_14_trigger_reason_stored_and_retrieved(self, test_run_id):
        """[14] trigger_reason string stored and retrieved correctly."""
        history_ts    = (_BASE_TS_DT - timedelta(hours=25)).isoformat()
        price_history = [(history_ts, 40.0)]
        gold = _build_opensky_gold(
            bounding_box_id="taiwan_strait",
            aircraft_count=42,
            transponder_silence_events=1,
            price_history=price_history,
            test_run_id=test_run_id,
            label="trigger_reason",
        )
        insert(gold)
        row = fetch_latest("opensky", "taiwan_strait")
        assert row is not None
        reason = row["metadata_extension"].get("trigger_reason")
        assert reason is not None
        assert isinstance(reason, str)
        assert len(reason) > 0

    def test_15_impact_level_4_stored_correctly(self, test_run_id):
        """[15] impact_level == 4 stored correctly when Aerial_Escalation_Risk fires."""
        history_ts    = (_BASE_TS_DT - timedelta(days=31)).isoformat()
        price_history = [(history_ts, 35.0)]
        gold = _build_opensky_gold(
            aircraft_count=50,
            price_history=price_history,
            test_run_id=test_run_id,
            label="impact_level_4",
        )
        insert(gold)
        row = fetch_latest("opensky", "taiwan_strait")
        assert row is not None
        assert row["metadata_extension"].get("impact_level") == 4

    def test_16_is_new_market_true_stored_for_cold_start(self, test_run_id):
        """[16] is_new_market == True stored correctly for first observation."""
        gold = _build_opensky_gold(
            aircraft_count=47,
            price_history=[],       # empty → cold start
            test_run_id=test_run_id,
            label="cold_start",
        )
        insert(gold)
        row = fetch_latest("opensky", "taiwan_strait")
        assert row is not None
        assert row["is_new_market"] is True

    def test_17_change_30d_zero_for_cold_start(self, test_run_id):
        """[17] change_30d == 0.0 stored for first observation (empty price_history)."""
        gold = _build_opensky_gold(
            aircraft_count=47,
            price_history=[],
            test_run_id=test_run_id,
            label="change_30d_zero",
        )
        insert(gold)
        row = fetch_latest("opensky", "taiwan_strait")
        assert row is not None
        assert row["change_30d"] == pytest.approx(0.0, abs=1e-6)

    def test_18_non_zero_momentum_stored_with_precision(self, test_run_id):
        """[18] Non-zero momentum values stored and retrieved with full precision."""
        # reference=35 at 25h ago → change_24h=(47-35)/35=0.342857...
        history_ts    = (_BASE_TS_DT - timedelta(hours=25)).isoformat()
        price_history = [(history_ts, 35.0)]
        gold = _build_opensky_gold(
            aircraft_count=47,
            price_history=price_history,
            test_run_id=test_run_id,
            label="precision_check",
        )
        expected_24h = round((47.0 - 35.0) / 35.0, 6)
        insert(gold)
        row = fetch_latest("opensky", "taiwan_strait")
        assert row is not None
        assert row["change_24h"] == pytest.approx(expected_24h, rel=1e-4)
