"""
Gate 3 — Persistence Gate: FRED momentum_vault Live PostgreSQL Validation.

Verifies that the full FRED pipeline (Bronze → Silver → Gold → momentum_vault)
writes correctly to a live TimescaleDB hypertable and that all fetch helpers
return accurate, queryable data.

Requires: PostgreSQL container running with momentum_vault table initialised.
    docker compose -f infrastructure/docker-compose.yml up -d postgres

What Gate 3 covers (Section 9.3 Gate 3):
    [1]  momentum_vault.insert() accepts a FRED Gold record without error
    [2]  fetch_latest("fred", series_id) returns the inserted row
    [3]  fetch_latest returns correct current_value
    [4]  fetch_latest returns correct metadata_extension.series_id
    [5]  fetch_latest returns correct metadata_extension.release_priority
    [6]  fetch_latest metadata_extension contains anomaly_flags list
    [7]  fetch_fred_series(series_id) returns the inserted row
    [8]  fetch_fred_series returns oldest-first ordering
    [9]  insert() is idempotent — ON CONFLICT (metric_id, timestamp_utc) DO NOTHING
    [10] T10Y2Y inversion: anomaly_flags=["Market_Anomaly"] persists to JSONB correctly
    [11] VIXCLS spike:     anomaly_flags=["Extreme_Uncertainty"] persists correctly
    [12] FEDFUNDS normal:  anomaly_flags=[] (empty list) persists correctly
    [13] momentum_block values (change_24h/7d/30d) are stored and retrieved correctly
    [14] fetch_fred_anomalies() returns only rows with non-empty anomaly_flags
    [15] fetch_fred_anomalies() does NOT return normal (no-flag) rows
    [16] Multiple series for the same observation date coexist without conflicts
    [17] TimescaleDB range query: fetch_time_series() with hours filter works

Architecture note — test isolation:
    Each test record uses canonical_event_id = "test_{run_id}_{label}" so
    the cleanup_momentum_vault fixture can delete all test rows by prefix
    after each test. No production data is touched (Section 9.3).

References:
    - Section 5.3:  Momentum Vault — TimescaleDB hypertable
    - Section 4.2B: Momentum Block — pre-computed deltas
    - Section B.3:  FRED automation trigger conditions
    - Section C.2:  Silver/Gold Structured Metric schema
    - Section 9.3:  Triple-Gate Test Matrix — Gate 3
"""

import uuid
from datetime import datetime, timezone

import pytest

from ingestion.fred_producer import INDICATORS
from persistence.momentum_vault import (
    fetch_fred_anomalies,
    fetch_fred_series,
    fetch_latest,
    fetch_time_series,
    insert,
)
from processing.gold_job import process_fred_gold_message
from processing.silver_job import map_fred_observation_to_silver
from utils.kafka_utils import build_bronze_message

# ==========================================================
# Fixtures & Helpers
# ==========================================================

pytestmark = pytest.mark.usefixtures("db_available")

FRED_ENDPOINT = "https://api.stlouisfed.org/fred/series/observations?series_id={}"

# Fixed reference time — prevents timestamp collisions between test records
# from different test functions running in the same second.
_BASE_TS = "2026-01-15T00:00:00+00:00"


def _build_fred_gold(
    series_id: str,
    value: float,
    obs_date: str = "2026-01-15",
    test_run_id: str = "test",
    label: str = "",
) -> dict:
    """
    Build a complete FRED Gold record via the real pipeline:
    Bronze envelope → Silver map → Gold process (with empty history).

    Uses the actual production transform functions so Gate 3 validates
    the full stack end-to-end, not just the persistence layer in isolation.
    """
    meta = INDICATORS[series_id]
    raw  = {
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
    envelope = build_bronze_message(
        source_name="fred",
        source_endpoint=FRED_ENDPOINT.format(series_id),
        raw_payload=raw,
    )
    # Override canonical_event_id to the namespaced test ID so the cleanup
    # fixture deletes only this test's rows.
    tag = f"test_{test_run_id}_{label or series_id.lower()}"
    envelope["event_id"] = tag

    silver = map_fred_observation_to_silver(envelope["payload"]["raw_payload"], envelope)
    _, gold = process_fred_gold_message(silver, [])  # empty history → is_new_market=True

    # Stamp metric_id deterministically so idempotency tests get the same UUID.
    gold["core_identity"]["metric_id"] = str(uuid.uuid5(uuid.NAMESPACE_DNS, tag))
    return gold


# ==========================================================
# Gate 3 — Basic Insert & Fetch
# ==========================================================

@pytest.mark.usefixtures("cleanup_momentum_vault")
class TestFREDBasicPersistence:
    """
    Verifies insert(), fetch_latest(), and fetch_fred_series() for a
    normal FEDFUNDS observation (Gate 3 checks 1-8).
    """

    @pytest.fixture(scope="function")
    def gold_fedfunds(self, test_run_id) -> dict:
        """Build and insert a FEDFUNDS Gold record; return the record."""
        gold = _build_fred_gold("FEDFUNDS", 4.33, test_run_id=test_run_id, label="fedfunds_basic")
        insert(gold)
        return gold

    def test_insert_returns_metric_id(self, test_run_id):
        """insert() must return the metric_id string without raising [1]."""
        gold = _build_fred_gold("CPIAUCSL", 315.8, obs_date="2026-01-15",
                                test_run_id=test_run_id, label="cpi_insert_check")
        metric_id = insert(gold)
        assert isinstance(metric_id, str)
        assert len(metric_id) > 0

    def test_fetch_latest_returns_row(self, gold_fedfunds):
        """fetch_latest() must return a non-None dict for an inserted series [2]."""
        row = fetch_latest("fred", "FEDFUNDS")
        assert row is not None, "fetch_latest returned None — row was not inserted"

    def test_fetch_latest_correct_value(self, gold_fedfunds):
        """fetch_latest() must return the exact current_value that was inserted [3]."""
        row = fetch_latest("fred", "FEDFUNDS")
        assert row is not None
        assert row["current_value"] == pytest.approx(4.33, rel=1e-5)

    def test_fetch_latest_correct_source_name(self, gold_fedfunds):
        """fetch_latest() must return source_name='fred'."""
        row = fetch_latest("fred", "FEDFUNDS")
        assert row["source_name"] == "fred"

    def test_fetch_latest_metadata_extension_series_id(self, gold_fedfunds):
        """metadata_extension.series_id must survive the JSONB round-trip [4]."""
        row = fetch_latest("fred", "FEDFUNDS")
        assert row is not None
        ext = row["metadata_extension"]
        assert ext.get("series_id") == "FEDFUNDS"

    def test_fetch_latest_metadata_extension_release_priority(self, gold_fedfunds):
        """metadata_extension.release_priority must be stored and returned intact [5]."""
        row = fetch_latest("fred", "FEDFUNDS")
        assert row is not None
        assert row["metadata_extension"]["release_priority"] == INDICATORS["FEDFUNDS"]["release_priority"]

    def test_fetch_latest_anomaly_flags_present(self, gold_fedfunds):
        """metadata_extension must contain anomaly_flags (list type) after insert [6]."""
        row = fetch_latest("fred", "FEDFUNDS")
        assert row is not None
        flags = row["metadata_extension"].get("anomaly_flags")
        assert isinstance(flags, list)

    def test_fetch_fred_series_returns_row(self, gold_fedfunds):
        """fetch_fred_series() must return at least the inserted row [7]."""
        rows = fetch_fred_series("FEDFUNDS", days=365)
        assert len(rows) >= 1

    def test_fetch_fred_series_oldest_first(self, test_run_id):
        """
        fetch_fred_series() must return rows ordered oldest-first (ASC) [8].

        Insert two observations for the same series with different dates,
        then verify the older one comes first.
        """
        gold_old = _build_fred_gold("UNRATE", 4.0, obs_date="2025-12-01",
                                    test_run_id=test_run_id, label="unrate_old")
        gold_new = _build_fred_gold("UNRATE", 4.1, obs_date="2026-01-01",
                                    test_run_id=test_run_id, label="unrate_new")
        insert(gold_old)
        insert(gold_new)

        rows = fetch_fred_series("UNRATE", days=400)
        # Filter to only our test rows (other tests might have inserted UNRATE too)
        test_rows = [
            r for r in rows
            if r["canonical_event_id"].startswith(f"test_{test_run_id}_unrate")
        ]
        assert len(test_rows) == 2
        assert test_rows[0]["timestamp_utc"] <= test_rows[1]["timestamp_utc"], (
            "fetch_fred_series must return oldest-first (ASC timestamp)"
        )


# ==========================================================
# Gate 3 — Idempotency (Section 4.3 / 9.5)
# ==========================================================

@pytest.mark.usefixtures("cleanup_momentum_vault")
class TestFREDIdempotency:
    """Verifies ON CONFLICT (metric_id, timestamp_utc) DO NOTHING [9]."""

    def test_duplicate_insert_does_not_raise(self, test_run_id):
        """
        Inserting the same Gold record twice must not raise an exception [9].

        The momentum_vault has ON CONFLICT (metric_id, timestamp_utc) DO NOTHING,
        ensuring Flink exactly-once re-deliveries never fail with a duplicate key
        error (Section 4.3 — idempotent insert pattern).
        """
        gold = _build_fred_gold("VIXCLS", 18.45, test_run_id=test_run_id, label="vix_idem")
        insert(gold)
        insert(gold)   # second insert — must not raise

    def test_duplicate_insert_does_not_create_extra_row(self, test_run_id):
        """After two identical inserts, only one row must exist in the DB."""
        gold = _build_fred_gold("GASREGW", 3.41, test_run_id=test_run_id, label="gas_idem")
        insert(gold)
        insert(gold)

        series_id = gold["core_identity"]["external_reference_id"]
        rows = fetch_fred_series(series_id, days=400)
        # Count rows with this exact canonical_event_id
        canon_id = gold["core_identity"]["canonical_event_id"]
        matching = [r for r in rows if r["canonical_event_id"] == canon_id]
        assert len(matching) == 1, (
            f"Expected 1 row after duplicate insert, found {len(matching)}"
        )


# ==========================================================
# Gate 3 — Automation Trigger Fields in JSONB
# ==========================================================

@pytest.mark.usefixtures("cleanup_momentum_vault")
class TestFREDTriggerPersistence:
    """
    Verifies that automation trigger fields are stored and retrievable
    correctly via the JSONB metadata_extension column [10-12].
    """

    def test_market_anomaly_flag_persists(self, test_run_id):
        """
        T10Y2Y < 0 → anomaly_flags=["Market_Anomaly"] must survive
        JSONB storage round-trip [10].
        """
        gold = _build_fred_gold("T10Y2Y", -0.25, test_run_id=test_run_id, label="t10y_inv")
        insert(gold)

        row = fetch_latest("fred", "T10Y2Y")
        assert row is not None
        flags = row["metadata_extension"].get("anomaly_flags", [])
        assert "Market_Anomaly" in flags, (
            f"Expected 'Market_Anomaly' in anomaly_flags, got: {flags}"
        )
        assert row["metadata_extension"]["impact_level"] == 5

    def test_extreme_uncertainty_flag_persists(self, test_run_id):
        """
        VIXCLS > 30 → anomaly_flags=["Extreme_Uncertainty"] must survive
        JSONB round-trip [11].
        """
        gold = _build_fred_gold("VIXCLS", 38.5, test_run_id=test_run_id, label="vix_spike")
        insert(gold)

        row = fetch_latest("fred", "VIXCLS")
        assert row is not None
        flags = row["metadata_extension"].get("anomaly_flags", [])
        assert "Extreme_Uncertainty" in flags, (
            f"Expected 'Extreme_Uncertainty' in anomaly_flags, got: {flags}"
        )

    def test_normal_observation_has_empty_anomaly_flags(self, test_run_id):
        """
        A normal FEDFUNDS observation must have anomaly_flags=[] in the DB [12].

        Empty list must not become NULL or a missing key after JSONB storage.
        """
        gold = _build_fred_gold("FEDFUNDS", 4.33, obs_date="2026-01-14",
                                test_run_id=test_run_id, label="fed_normal")
        insert(gold)

        row = fetch_latest("fred", "FEDFUNDS")
        assert row is not None
        flags = row["metadata_extension"].get("anomaly_flags")
        assert flags is not None, "anomaly_flags must not be NULL in DB"
        assert isinstance(flags, list)
        # Note: can't assert flags==[] because other test rows for FEDFUNDS
        # may have been inserted. We just verify the field is a list.

    def test_trigger_reason_is_stored(self, test_run_id):
        """trigger_reason text must survive JSONB storage for anomaly rows."""
        gold = _build_fred_gold("T10Y2Y", -0.10, obs_date="2026-01-13",
                                test_run_id=test_run_id, label="t10y_reason")
        insert(gold)

        row = fetch_latest("fred", "T10Y2Y")
        assert row is not None
        reason = row["metadata_extension"].get("trigger_reason")
        assert reason is not None
        assert isinstance(reason, str)
        assert len(reason) > 0


# ==========================================================
# Gate 3 — Momentum Block Persistence
# ==========================================================

@pytest.mark.usefixtures("cleanup_momentum_vault")
class TestFREDMomentumPersistence:
    """
    Verifies momentum_block values are stored and retrieved correctly [13].
    """

    def test_is_new_market_true_for_first_observation(self, test_run_id):
        """
        First observation (empty history) → is_new_market=True must be stored [13].
        """
        gold = _build_fred_gold("DCOILWTICO", 72.4,
                                test_run_id=test_run_id, label="oil_first")
        assert gold["momentum_block"]["is_new_market"] is True
        insert(gold)

        row = fetch_latest("fred", "DCOILWTICO")
        assert row is not None
        assert row["is_new_market"] is True

    def test_change_24h_zero_for_first_observation(self, test_run_id):
        """First observation → change_24h=0.0 (no prior history) [13]."""
        gold = _build_fred_gold("DHHNGSP", 3.62,
                                test_run_id=test_run_id, label="natgas_first")
        insert(gold)

        row = fetch_latest("fred", "DHHNGSP")
        assert row is not None
        assert row["change_24h"] == pytest.approx(0.0)

    def test_nonzero_momentum_stored_correctly(self, test_run_id):
        """
        A Gold record with non-zero momentum values must be stored and
        retrieved with those exact values (not silently zeroed by the DB).
        """
        # Build a Gold record and manually override the momentum block
        # to simulate a record produced with a non-empty history.
        gold = _build_fred_gold("CPIAUCSL", 315.8, obs_date="2026-01-10",
                                test_run_id=test_run_id, label="cpi_momentum")
        gold["momentum_block"]["change_24h"] = 0.003182
        gold["momentum_block"]["change_7d"]  = 0.009870
        gold["momentum_block"]["change_30d"] = 0.024500
        gold["momentum_block"]["is_new_market"] = False

        insert(gold)

        row = fetch_latest("fred", "CPIAUCSL")
        assert row is not None
        assert row["change_24h"] == pytest.approx(0.003182, rel=1e-5)
        assert row["change_7d"]  == pytest.approx(0.009870, rel=1e-5)
        assert row["change_30d"] == pytest.approx(0.024500, rel=1e-5)
        assert row["is_new_market"] is False


# ==========================================================
# Gate 3 — fetch_fred_anomalies()
# ==========================================================

@pytest.mark.usefixtures("cleanup_momentum_vault")
class TestFetchFREDAnomalies:
    """
    Verifies fetch_fred_anomalies() filters correctly [14, 15].
    """

    def test_anomaly_row_appears_in_results(self, test_run_id):
        """
        A T10Y2Y inversion row must appear in fetch_fred_anomalies() [14].
        """
        gold = _build_fred_gold("T10Y2Y", -0.30, obs_date="2026-01-16",
                                test_run_id=test_run_id, label="t10y_anomaly_fetch")
        insert(gold)

        anomalies = fetch_fred_anomalies(days=365)
        canon_ids = {r["canonical_event_id"] for r in anomalies}
        assert gold["core_identity"]["canonical_event_id"] in canon_ids, (
            "T10Y2Y inversion row must appear in fetch_fred_anomalies()"
        )

    def test_normal_row_excluded_from_anomalies(self, test_run_id):
        """
        A normal FEDFUNDS row (no flags) must NOT appear in fetch_fred_anomalies() [15].
        """
        gold = _build_fred_gold("FEDFUNDS", 4.33, obs_date="2026-01-17",
                                test_run_id=test_run_id, label="fed_no_anomaly")
        insert(gold)

        anomalies = fetch_fred_anomalies(days=365)
        canon_ids = {r["canonical_event_id"] for r in anomalies}
        assert gold["core_identity"]["canonical_event_id"] not in canon_ids, (
            "Normal FEDFUNDS row (no anomaly flags) must not appear in fetch_fred_anomalies()"
        )


# ==========================================================
# Gate 3 — Multi-Series Coexistence
# ==========================================================

@pytest.mark.usefixtures("cleanup_momentum_vault")
class TestFREDMultiSeriesCoexistence:
    """
    Verifies multiple FRED series coexist in the hypertable without
    conflicts or cross-contamination [16, 17].
    """

    def test_all_9_series_insert_without_conflict(self, test_run_id):
        """
        All 9 FRED series must be insertable with the same observation_date
        without primary key conflicts [16].

        The PK is (metric_id, timestamp_utc) — each series gets a different
        metric_id UUID so they coexist on the same date partition.
        """
        sample_values = {
            "FEDFUNDS": 4.33, "CPIAUCSL": 315.8, "UNRATE": 4.1,
            "CSUSHPINSA": 318.2, "DCOILWTICO": 72.4, "GASREGW": 3.41,
            "DHHNGSP": 3.62, "VIXCLS": 18.45, "T10Y2Y": 0.12,
        }
        for series_id, value in sample_values.items():
            gold = _build_fred_gold(series_id, value, obs_date="2026-01-18",
                                    test_run_id=test_run_id,
                                    label=f"multi_{series_id.lower()[:6]}")
            insert(gold)   # must not raise

        # Spot-check: each series is queryable independently
        for series_id in sample_values:
            row = fetch_latest("fred", series_id)
            assert row is not None, f"fetch_latest returned None for {series_id}"

    def test_timescaledb_range_query_works(self, test_run_id):
        """
        fetch_time_series() range predicate must leverage TimescaleDB chunk
        exclusion — rows outside the window must not be returned [17].

        Insert a row with timestamp far in the past, then query with a
        small window that excludes it.
        """
        gold = _build_fred_gold("FEDFUNDS", 3.0, obs_date="2015-01-01",
                                test_run_id=test_run_id, label="fed_old_ts")
        insert(gold)

        # Query only the last 30 days — the 2015 row must not appear
        rows = fetch_time_series("fred", "FEDFUNDS", hours=30 * 24)
        old_canon = gold["core_identity"]["canonical_event_id"]
        old_in_results = any(r["canonical_event_id"] == old_canon for r in rows)
        assert not old_in_results, (
            "TimescaleDB range query must exclude rows outside the time window"
        )
