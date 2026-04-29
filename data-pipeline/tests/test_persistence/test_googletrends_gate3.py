"""
Gate 3 — Persistence Gate: Google Trends momentum_vault Live PostgreSQL Validation.

Verifies that the full Google Trends pipeline (Bronze → Silver → Gold → momentum_vault)
writes correctly to a live TimescaleDB hypertable and that all fetch helpers
return accurate, queryable data.

Requires: PostgreSQL container running with momentum_vault table initialised.
    docker compose -f infrastructure/docker-compose.yml up -d postgres

What Gate 3 covers (Section 9.3 Gate 3):
    [1]  momentum_vault.insert() accepts a Google Trends Gold record without error
    [2]  fetch_latest("googletrends", "Inflation") returns the inserted row
    [3]  fetch_latest returns correct current_value (interest_score)
    [4]  fetch_latest returns source_name == "googletrends"
    [5]  metadata_extension.keyword survives the JSONB round-trip
    [6]  metadata_extension.geo survives the JSONB round-trip
    [7]  metadata_extension.mode survives the JSONB round-trip
    [8]  fetch_time_series("googletrends", keyword) returns the inserted row
    [9]  fetch_time_series returns oldest-first ordering (two records)
    [10] insert() is idempotent — ON CONFLICT (metric_id, timestamp_utc) DO NOTHING
         (no exception on duplicate insert)
    [11] Idempotency: only one row exists after two identical inserts
    [12] Public_Hype_Alert == True stored in metadata_extension JSONB correctly
    [13] Public_Hype_Alert == False stored in JSONB correctly
    [14] trigger_reason is a non-empty string when alert fires, stored in JSONB
    [15] impact_level == 4 when alert fires, stored in JSONB
    [16] is_new_market == True stored and retrieved correctly for first observation
    [17] change_24h == 0.0 for first observation (no prior history)
    [18] Non-zero momentum values stored and retrieved correctly

Architecture note — test isolation:
    Each test record uses canonical_event_id = "test_{run_id}_{label}" so the
    cleanup_momentum_vault fixture deletes all test rows by prefix after each test.
    No production data is touched (Section 9.3).

References:
    - Section 5.3:  Momentum Vault — TimescaleDB hypertable
    - Section 4.2B: Momentum Block — pre-computed deltas
    - Section B.5:  Google Trends Public_Hype_Alert trigger
    - Section C.2:  Silver/Gold Structured Metric schema
    - Section 9.3:  Triple-Gate Test Matrix — Gate 3
"""

import uuid
from datetime import datetime, timezone

import pytest

from ingestion.googletrends_producer import SOURCE_NAME
from persistence.momentum_vault import (
    fetch_latest,
    fetch_time_series,
    insert,
)
from processing.gold_job import process_googletrends_gold_message
from processing.silver_job import map_googletrends_to_silver
from utils.kafka_utils import build_bronze_message

# ==========================================================
# Fixtures & Helpers
# ==========================================================

pytestmark = pytest.mark.usefixtures("db_available")

_SOURCE_ENDPOINT = "https://trends.google.com/trends/explore"

# Fixed reference time — prevents timestamp collisions between test records
# from different test functions running in the same second.
_BASE_TS = "2026-04-01T00:00:00+00:00"
_BASE_TS_DT = datetime.fromisoformat(_BASE_TS)


def _build_googletrends_gold(
    keyword: str = "Inflation",
    geo: str = "US",
    interest_score: float = 72.0,
    mode: str = "static",
    rank_in_trending: int = 5,
    obs_date: str = None,
    test_run_id: str = "test",
    label: str = "",
) -> dict:
    """
    Build a complete Google Trends Gold record via the real pipeline:
    raw_payload → Bronze envelope → Silver map → Gold process (empty history).

    Uses the actual production transform functions so Gate 3 validates
    the full stack end-to-end, not just the persistence layer in isolation.
    The _now parameter is fixed to _BASE_TS_DT for deterministic deltas.
    """
    raw = {
        "keyword":         keyword,
        "geo":             geo,
        "geo_name":        "United States" if geo == "US" else geo,
        "interest_score":  interest_score,
        "mode":            mode,
        "fetch_timestamp": _BASE_TS,
        "rank_in_trending": rank_in_trending if mode == "static" else None,
    }
    if obs_date:
        raw["observation_date"] = obs_date

    envelope = build_bronze_message(
        source_name=SOURCE_NAME,
        source_endpoint=_SOURCE_ENDPOINT,
        raw_payload=raw,
    )

    # Override canonical_event_id to the namespaced test ID so the cleanup
    # fixture deletes only this test's rows.
    tag = f"test_{test_run_id}_{label or keyword.lower()}"
    envelope["event_id"] = tag

    silver = map_googletrends_to_silver(raw, envelope)
    _, gold = process_googletrends_gold_message(silver, [], _now=_BASE_TS_DT)

    # Stamp metric_id deterministically so idempotency tests get the same UUID.
    gold["core_identity"]["metric_id"] = str(uuid.uuid5(uuid.NAMESPACE_DNS, tag))
    return gold


# ==========================================================
# Gate 3 — Basic Insert & Fetch [1–9]
# ==========================================================

@pytest.mark.usefixtures("cleanup_momentum_vault")
class TestGoogleTrendsBasicPersistence:
    """
    Verifies insert(), fetch_latest(), and fetch_time_series() for a
    normal Google Trends observation (Gate 3 checks 1-9).
    """

    @pytest.fixture(scope="function")
    def gold_inflation(self, test_run_id) -> dict:
        """Build and insert an Inflation Gold record; return the record."""
        gold = _build_googletrends_gold(
            keyword="Inflation", interest_score=72.0,
            test_run_id=test_run_id, label="inflation_basic",
        )
        insert(gold)
        return gold

    def test_insert_returns_metric_id(self, test_run_id):
        """[1] insert() must return a non-empty metric_id string without raising."""
        gold = _build_googletrends_gold(
            keyword="Bitcoin", interest_score=55.0,
            test_run_id=test_run_id, label="btc_insert_check",
        )
        metric_id = insert(gold)
        assert isinstance(metric_id, str)
        assert len(metric_id) > 0

    def test_fetch_latest_returns_row(self, gold_inflation):
        """[2] fetch_latest() must return a non-None dict for an inserted keyword."""
        row = fetch_latest("googletrends", "Inflation")
        assert row is not None, "fetch_latest returned None — row was not inserted"

    def test_fetch_latest_correct_value(self, gold_inflation):
        """[3] fetch_latest() must return the exact interest_score that was inserted."""
        row = fetch_latest("googletrends", "Inflation")
        assert row is not None
        assert row["current_value"] == pytest.approx(72.0, rel=1e-5)

    def test_fetch_latest_correct_source_name(self, gold_inflation):
        """[4] fetch_latest() must return source_name == 'googletrends'."""
        row = fetch_latest("googletrends", "Inflation")
        assert row is not None
        assert row["source_name"] == "googletrends"

    def test_fetch_latest_metadata_keyword(self, gold_inflation):
        """[5] metadata_extension.keyword must survive the JSONB round-trip."""
        row = fetch_latest("googletrends", "Inflation")
        assert row is not None
        assert row["metadata_extension"].get("keyword") == "Inflation"

    def test_fetch_latest_metadata_geo(self, gold_inflation):
        """[6] metadata_extension.geo must survive the JSONB round-trip."""
        row = fetch_latest("googletrends", "Inflation")
        assert row is not None
        assert row["metadata_extension"].get("geo") == "US"

    def test_fetch_latest_metadata_mode(self, gold_inflation):
        """[7] metadata_extension.mode must survive the JSONB round-trip."""
        row = fetch_latest("googletrends", "Inflation")
        assert row is not None
        assert row["metadata_extension"].get("mode") == "static"

    def test_fetch_time_series_returns_row(self, gold_inflation):
        """[8] fetch_time_series() must return at least the inserted row."""
        rows = fetch_time_series("googletrends", "Inflation", hours=365 * 24)
        assert len(rows) >= 1

    def test_fetch_time_series_oldest_first(self, test_run_id):
        """
        [9] fetch_time_series() must return rows ordered oldest-first (ASC).

        Insert two observations for the same keyword with different timestamps,
        then verify the older one comes first.
        """
        gold_old = _build_googletrends_gold(
            keyword="Recession", interest_score=40.0, obs_date="2025-12-01",
            mode="backfill", rank_in_trending=None,
            test_run_id=test_run_id, label="recession_old",
        )
        gold_new = _build_googletrends_gold(
            keyword="Recession", interest_score=55.0, obs_date="2026-01-15",
            mode="backfill", rank_in_trending=None,
            test_run_id=test_run_id, label="recession_new",
        )
        insert(gold_old)
        insert(gold_new)

        rows = fetch_time_series("googletrends", "Recession", hours=400 * 24)
        test_rows = [
            r for r in rows
            if r["canonical_event_id"].startswith(f"test_{test_run_id}_recession")
        ]
        assert len(test_rows) == 2
        assert test_rows[0]["timestamp_utc"] <= test_rows[1]["timestamp_utc"], (
            "fetch_time_series must return oldest-first (ASC timestamp)"
        )


# ==========================================================
# Gate 3 — Idempotency [10–11]
# ==========================================================

@pytest.mark.usefixtures("cleanup_momentum_vault")
class TestGoogleTrendsIdempotency:
    """Verifies ON CONFLICT (metric_id, timestamp_utc) DO NOTHING [10, 11]."""

    def test_duplicate_insert_does_not_raise(self, test_run_id):
        """
        [10] Inserting the same Gold record twice must not raise an exception.

        The momentum_vault has ON CONFLICT (metric_id, timestamp_utc) DO NOTHING,
        ensuring Flink exactly-once re-deliveries never fail with a duplicate key
        error (Section 4.3 — idempotent insert pattern).
        """
        gold = _build_googletrends_gold(
            keyword="FederalReserve", interest_score=65.0,
            test_run_id=test_run_id, label="fed_idem",
        )
        insert(gold)
        insert(gold)   # second insert — must not raise

    def test_duplicate_insert_does_not_create_extra_row(self, test_run_id):
        """[11] After two identical inserts, only one row must exist in the DB."""
        gold = _build_googletrends_gold(
            keyword="Unemployment", interest_score=48.0,
            test_run_id=test_run_id, label="unemp_idem",
        )
        insert(gold)
        insert(gold)

        canon_id = gold["core_identity"]["canonical_event_id"]
        rows = fetch_time_series("googletrends", "Unemployment", hours=365 * 24)
        matching = [r for r in rows if r["canonical_event_id"] == canon_id]
        assert len(matching) == 1, (
            f"Expected 1 row after duplicate insert, found {len(matching)}"
        )


# ==========================================================
# Gate 3 — Automation Trigger Fields in JSONB [12–15]
# ==========================================================

@pytest.mark.usefixtures("cleanup_momentum_vault")
class TestGoogleTrendsTriggerPersistence:
    """
    Verifies that Public_Hype_Alert fields are stored and retrievable
    correctly via the JSONB metadata_extension column [12-15].
    """

    def test_public_hype_alert_true_persists(self, test_run_id):
        """
        [12] A Gold record with public_hype_alert=True must store that flag in JSONB.

        We manually set a large abs_change_24h_pts and public_hype_alert=True
        to simulate a 60-pt jump — no need to reconstruct the full history chain
        for a persistence-layer test.
        """
        gold = _build_googletrends_gold(
            keyword="StockMarket", interest_score=80.0,
            test_run_id=test_run_id, label="sm_hype_alert",
        )
        # Simulate a 60-pt spike for persistence testing
        gold["metadata_extension"]["public_hype_alert"]  = True
        gold["metadata_extension"]["abs_change_24h_pts"] = 60.0
        gold["metadata_extension"]["impact_level"]       = 4
        gold["metadata_extension"]["trigger_reason"] = (
            "Public_Hype_Alert: 'StockMarket' (US) interest score spiked "
            "60.0 pts in 24h (threshold=50 pts)."
        )
        insert(gold)

        row = fetch_latest("googletrends", "StockMarket")
        assert row is not None
        ext = row["metadata_extension"]
        assert ext.get("public_hype_alert") is True, (
            "public_hype_alert=True must survive JSONB round-trip"
        )

    def test_public_hype_alert_false_persists(self, test_run_id):
        """
        [13] A normal observation with public_hype_alert=False must store False (not NULL).
        """
        gold = _build_googletrends_gold(
            keyword="CryptoNews", interest_score=30.0,
            test_run_id=test_run_id, label="crypto_no_alert",
        )
        insert(gold)

        row = fetch_latest("googletrends", "CryptoNews")
        assert row is not None
        ext = row["metadata_extension"]
        assert "public_hype_alert" in ext, (
            "public_hype_alert must be present in metadata_extension even when False"
        )
        assert ext["public_hype_alert"] is False

    def test_trigger_reason_stored_when_alert_fires(self, test_run_id):
        """[14] trigger_reason string must survive JSONB storage for alert rows."""
        gold = _build_googletrends_gold(
            keyword="BankRun", interest_score=90.0,
            test_run_id=test_run_id, label="bankrun_reason",
        )
        gold["metadata_extension"]["public_hype_alert"]  = True
        gold["metadata_extension"]["abs_change_24h_pts"] = 75.0
        gold["metadata_extension"]["impact_level"]       = 4
        gold["metadata_extension"]["trigger_reason"] = (
            "Public_Hype_Alert: 'BankRun' (US) interest score spiked 75.0 pts in 24h."
        )
        insert(gold)

        row = fetch_latest("googletrends", "BankRun")
        assert row is not None
        reason = row["metadata_extension"].get("trigger_reason")
        assert isinstance(reason, str) and len(reason) > 0, (
            "trigger_reason must be a non-empty string in DB when alert fires"
        )

    def test_impact_level_4_stored_when_alert_fires(self, test_run_id):
        """[15] impact_level == 4 must be stored correctly in JSONB when alert fires."""
        gold = _build_googletrends_gold(
            keyword="TariffWar", interest_score=85.0,
            test_run_id=test_run_id, label="tariff_impact",
        )
        gold["metadata_extension"]["public_hype_alert"]  = True
        gold["metadata_extension"]["abs_change_24h_pts"] = 55.0
        gold["metadata_extension"]["impact_level"]       = 4
        gold["metadata_extension"]["trigger_reason"] = "Public_Hype_Alert: 'TariffWar' spiked."
        insert(gold)

        row = fetch_latest("googletrends", "TariffWar")
        assert row is not None
        assert row["metadata_extension"].get("impact_level") == 4, (
            "impact_level=4 must survive JSONB round-trip when alert fires"
        )


# ==========================================================
# Gate 3 — Momentum Block Persistence [16–18]
# ==========================================================

@pytest.mark.usefixtures("cleanup_momentum_vault")
class TestGoogleTrendsMomentumPersistence:
    """
    Verifies momentum_block values are stored and retrieved correctly [16-18].
    """

    def test_is_new_market_true_for_first_observation(self, test_run_id):
        """[16] First observation (empty history) → is_new_market=True stored correctly."""
        gold = _build_googletrends_gold(
            keyword="AIRegulation", interest_score=60.0,
            test_run_id=test_run_id, label="ai_reg_first",
        )
        assert gold["momentum_block"]["is_new_market"] is True
        insert(gold)

        row = fetch_latest("googletrends", "AIRegulation")
        assert row is not None
        assert row["is_new_market"] is True

    def test_change_24h_zero_for_first_observation(self, test_run_id):
        """[17] First observation → change_24h == 0.0 (no prior history)."""
        gold = _build_googletrends_gold(
            keyword="QuantumComputing", interest_score=35.0,
            test_run_id=test_run_id, label="quantum_first",
        )
        insert(gold)

        row = fetch_latest("googletrends", "QuantumComputing")
        assert row is not None
        assert row["change_24h"] == pytest.approx(0.0)

    def test_nonzero_momentum_stored_correctly(self, test_run_id):
        """
        [18] Non-zero momentum values must be stored and retrieved with full precision.

        Manually override the momentum_block to simulate a record produced
        with a non-empty history, then verify the DB round-trip preserves values.
        """
        gold = _build_googletrends_gold(
            keyword="ClimateChange", interest_score=78.0,
            test_run_id=test_run_id, label="climate_momentum",
        )
        gold["momentum_block"]["change_24h"]   = 0.125000
        gold["momentum_block"]["change_7d"]    = 0.333333
        gold["momentum_block"]["change_30d"]   = 0.560000
        gold["momentum_block"]["is_new_market"] = False
        insert(gold)

        row = fetch_latest("googletrends", "ClimateChange")
        assert row is not None
        assert row["change_24h"]  == pytest.approx(0.125000, rel=1e-5)
        assert row["change_7d"]   == pytest.approx(0.333333, rel=1e-5)
        assert row["change_30d"]  == pytest.approx(0.560000, rel=1e-5)
        assert row["is_new_market"] is False
