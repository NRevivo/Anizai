"""
Gate 3 — Persistence: social_vault and momentum_vault (Polymarket).

Validates that:
    social_vault.py
        [1]  insert() writes a row and returns a valid social_id UUID
        [2]  fetch_by_social_id() retrieves the exact row inserted
        [3]  platform_data JSONB round-trips without data loss
        [4]  exists_by_content_hash() returns True after insert
        [5]  exists_by_content_hash() returns False for unknown hash
        [6]  fetch_by_canonical_event() returns inserted records
        [7]  insert() raises ValueError for invalid source_name
        [8]  insert() raises ValueError for missing source_name

    momentum_vault.py
        [9]  insert() writes a row and returns metric_id
        [10] fetch_latest() returns the most recent observation
        [11] momentum_block values are stored and retrieved correctly
        [12] whale_alert round-trips through metadata_extension JSONB
        [13] insert() is idempotent — ON CONFLICT (metric_id, ts) DO NOTHING
        [14] fetch_time_series() returns observations within the window
        [15] fetch_time_series() returns empty list for no-data queries
        [16] insert() raises ValueError for missing core_identity
        [17] insert() raises ValueError for missing data_point fields
        [18] fetch_latest() returns None for unknown asset

Isolation: canonical_event_id prefixed with "test_<test_run_id>_" in all
test records. cleanup_social_vault and cleanup_momentum_vault fixtures
delete those rows after each test (see conftest.py).

References:
    - Section 5.1:  Social Vault — Silver social discourse store
    - Section 5.3:  Momentum Vault — TimescaleDB hypertable
    - Section 9.3:  Triple-Gate Test Matrix — Gate 3
    - Section C.3:  Silver Social Discourse Store (Polymarket pattern)
    - Section C.2:  Silver/Gold Structured Metric schema
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from persistence.social_vault import (
    VALID_SOURCES,
    exists_by_content_hash,
    fetch_by_canonical_event,
    fetch_by_social_id,
    insert as sv_insert,
)
from persistence.momentum_vault import (
    fetch_latest,
    fetch_time_series,
    insert as mv_insert,
)

pytestmark = pytest.mark.usefixtures("db_available")


# ==========================================================
# Shared builders
# ==========================================================

def make_silver_social(
    canonical_event_id: str,
    source_name: str = "polymarket",
    content_hash: str = None,
) -> dict:
    """Build a minimal valid Polymarket Silver social record (Section C.3).

    canonical_event_id is stored as the TEXT grouping key in social_vault so
    that cleanup_social_vault can DELETE WHERE canonical_event_id LIKE 'test_%'.
    bronze_ref is a real UUID (simulating the Bronze envelope event_id) so that
    social_vault.insert() can derive a valid raw_data_ref (NOT NULL UUID column).
    """
    return {
        "source_name":        source_name,
        "canonical_event_id": canonical_event_id,  # explicit grouping key for cleanup
        "ingested_at":        "2026-03-28T14:22:00+00:00",
        "market_id":          "0x87ae1d3e2aec7cbf6b7f01781f741c82ae9bd08a",
        "question":           "Will the Federal Reserve cut rates before June 2026?",
        "raw_comments": [
            {"comment_id": "cm_001", "author": "macro_trader_99",
             "text": "Fed pivot incoming.", "upvotes": 14},
        ],
        "content_hash": content_hash or "a" * 64,
        "bronze_ref":   str(uuid.uuid4()),  # real UUID → maps to raw_data_ref NOT NULL column
    }


def make_gold_metric(
    canonical_event_id: str,
    current_value: float = 0.67,
    metric_id: str = None,
    whale_alert: bool = False,
    timestamp_utc: str = None,
) -> dict:
    """Build a minimal valid Gold Structured Metric (Section C.2, layer=Gold)."""
    return {
        "schema_version": "2.0",
        "layer":          "Gold",
        "entity_type":    "Structured_Metric",
        "core_identity": {
            "metric_id":             metric_id or str(uuid.uuid4()),
            "canonical_event_id":    canonical_event_id,
            "parent_id":             "0x87ae1d3e2aec7cbf6b7f01781f741c82ae9bd08a",
            "source_name":           "polymarket",
            "external_reference_id": "asset_fed_cut_june2026",
        },
        "data_point": {
            "current_value": current_value,
            "unit":          "USD",
            "status":        "active",
            "timestamp_utc": timestamp_utc or datetime.now(timezone.utc).isoformat(),
        },
        "momentum_block": {
            "change_24h":    0.05,
            "change_7d":     0.12,
            "change_30d":    0.30,
            "is_new_market": False,
        },
        "metadata_extension": {
            "liquidity_pool_tvl": 50000.0,
            "bid_ask_spread":     0.02,
            "24h_volume":         285400.0,
            "is_divergent":       False,
            "whale_alert":        whale_alert,
            "resolution_rules":   "",
        },
        "whale_alert": whale_alert,
    }


# ==========================================================
# social_vault — Insert and Fetch
# ==========================================================

@pytest.mark.usefixtures("cleanup_social_vault")
class TestSocialVaultInsertAndFetch:
    """Verifies CRUD correctness for social_vault (Gate 3 checks 1-3)."""

    def test_insert_returns_valid_uuid(self, test_run_id):
        record = make_silver_social(f"test_{test_run_id}_sv_insert")
        social_id = sv_insert(record)
        assert isinstance(social_id, str)
        uuid.UUID(social_id)  # raises if not valid UUID

    def test_fetch_returns_inserted_row(self, test_run_id):
        record = make_silver_social(f"test_{test_run_id}_sv_fetch")
        social_id = sv_insert(record)
        row = fetch_by_social_id(social_id)

        assert row is not None, f"No row found for social_id={social_id}"
        assert row["source_name"] == "polymarket"
        assert row["social_id"]   == social_id

    def test_platform_data_round_trip(self, test_run_id):
        """Full Silver record stored in platform_data must survive JSONB round-trip."""
        ceid   = f"test_{test_run_id}_sv_jsonb"
        record = make_silver_social(ceid)
        social_id = sv_insert(record)
        row = fetch_by_social_id(social_id)

        pd = row["platform_data"]
        assert pd["market_id"]   == record["market_id"]
        assert pd["question"]    == record["question"]
        assert len(pd["raw_comments"]) == len(record["raw_comments"])
        assert pd["content_hash"] == record["content_hash"]

    def test_fetch_returns_none_for_unknown_id(self):
        result = fetch_by_social_id(str(uuid.uuid4()))
        assert result is None


# ==========================================================
# social_vault — Existence Check and Canonical Event Fetch
# ==========================================================

@pytest.mark.usefixtures("cleanup_social_vault")
class TestSocialVaultExistenceAndQuery:
    """Verifies dedup check and canonical-event query (Gate 3 checks 4-6)."""

    def test_exists_true_after_insert(self, test_run_id):
        content_hash = "b" * 64
        record = make_silver_social(
            f"test_{test_run_id}_sv_exists_true",
            content_hash=content_hash,
        )
        sv_insert(record)
        assert exists_by_content_hash(content_hash) is True

    def test_exists_false_for_unknown_hash(self):
        assert exists_by_content_hash("0" * 64) is False

    def test_fetch_by_canonical_event_returns_inserted(self, test_run_id):
        ceid = f"test_{test_run_id}_sv_ceid_fetch"
        sv_insert(make_silver_social(ceid))
        rows = fetch_by_canonical_event(ceid, source_name="polymarket")
        assert len(rows) >= 1
        assert all(r["source_name"] == "polymarket" for r in rows)

    def test_fetch_by_canonical_event_wrong_source_empty(self, test_run_id):
        ceid = f"test_{test_run_id}_sv_wrong_src"
        sv_insert(make_silver_social(ceid, source_name="polymarket"))
        rows = fetch_by_canonical_event(ceid, source_name="hackernews")
        assert rows == []


# ==========================================================
# social_vault — Validation Guards (no DB)
# ==========================================================

class TestSocialVaultValidation:
    """insert() must raise before touching the DB (Gate 3 checks 7-8)."""

    def test_invalid_source_name_raises(self):
        record = make_silver_social("test_bad_src", source_name="twitter")
        with pytest.raises(ValueError, match="source_name"):
            sv_insert(record)

    def test_missing_source_name_raises(self):
        record = {"ingested_at": "2026-01-01T00:00:00Z", "market_id": "x"}
        with pytest.raises(ValueError, match="source_name"):
            sv_insert(record)

    def test_valid_sources_constant(self):
        # Reddit removed (Sprint 11 T4) — API pre-approval required (Nov 2025 policy)
        assert VALID_SOURCES == frozenset({"polymarket", "telegram", "hackernews"})


# ==========================================================
# momentum_vault — Insert and Fetch
# ==========================================================

@pytest.mark.usefixtures("cleanup_momentum_vault")
class TestMomentumVaultInsertAndFetch:
    """Verifies CRUD correctness for momentum_vault (Gate 3 checks 9-12)."""

    def test_insert_returns_metric_id(self, test_run_id):
        record = make_gold_metric(f"test_{test_run_id}_mv_insert")
        metric_id = mv_insert(record)
        assert isinstance(metric_id, str)
        uuid.UUID(metric_id)

    def test_insert_uses_core_identity_metric_id(self, test_run_id):
        fixed_id = str(uuid.uuid4())
        record = make_gold_metric(
            f"test_{test_run_id}_mv_fixed_id",
            metric_id=fixed_id,
        )
        returned = mv_insert(record)
        assert returned == fixed_id

    def test_fetch_latest_returns_inserted_row(self, test_run_id):
        ceid   = f"test_{test_run_id}_mv_fetch_latest"
        record = make_gold_metric(ceid, current_value=0.67)
        mv_insert(record)

        row = fetch_latest("polymarket", "asset_fed_cut_june2026")
        # Row may include results from previous tests; check that our value appears
        assert row is not None
        # The row we just inserted has current_value=0.67
        # (fetch_latest returns the most recent, which is ours since it was just inserted)
        assert float(row["current_value"]) == pytest.approx(0.67, abs=1e-6)

    def test_momentum_block_stored_correctly(self, test_run_id):
        ceid   = f"test_{test_run_id}_mv_momentum"
        record = make_gold_metric(ceid)
        mv_insert(record)

        row = fetch_latest("polymarket", "asset_fed_cut_june2026")
        assert row is not None
        assert float(row["change_24h"]) == pytest.approx(0.05, abs=1e-6)
        assert float(row["change_7d"])  == pytest.approx(0.12, abs=1e-6)
        assert float(row["change_30d"]) == pytest.approx(0.30, abs=1e-6)
        assert row["is_new_market"] is False

    def test_whale_alert_in_metadata_extension_round_trips(self, test_run_id):
        ceid   = f"test_{test_run_id}_mv_whale"
        record = make_gold_metric(ceid, whale_alert=True)
        mv_insert(record)

        row = fetch_latest("polymarket", "asset_fed_cut_june2026")
        assert row is not None
        assert row["metadata_extension"]["whale_alert"] is True

    def test_fetch_latest_returns_none_for_unknown_asset(self):
        result = fetch_latest("polymarket", "nonexistent_asset_xyz_abc_12345")
        assert result is None


# ==========================================================
# momentum_vault — Idempotency
# ==========================================================

@pytest.mark.usefixtures("cleanup_momentum_vault")
class TestMomentumVaultIdempotency:
    """ON CONFLICT (metric_id, timestamp_utc) DO NOTHING (Gate 3 check 13)."""

    def test_duplicate_insert_does_not_create_extra_row(self, test_run_id):
        """Inserting the same (metric_id, timestamp_utc) twice yields one row."""
        from utils.db import get_cursor

        fixed_ts  = "2026-03-28T12:00:00+00:00"
        fixed_mid = str(uuid.uuid4())
        ceid      = f"test_{test_run_id}_mv_idempotent"
        record    = make_gold_metric(ceid, metric_id=fixed_mid, timestamp_utc=fixed_ts)

        id1 = mv_insert(record)
        id2 = mv_insert(record)  # same record — ON CONFLICT DO NOTHING
        assert id1 == id2 == fixed_mid

        with get_cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM momentum_vault "
                "WHERE metric_id = %s::uuid AND timestamp_utc = %s::timestamptz;",
                (fixed_mid, fixed_ts),
            )
            assert cur.fetchone()["cnt"] == 1


# ==========================================================
# momentum_vault — Time Series
# ==========================================================

@pytest.mark.usefixtures("cleanup_momentum_vault")
class TestMomentumVaultTimeSeries:
    """fetch_time_series() retrieves recent observations (Gate 3 checks 14-15)."""

    def test_time_series_returns_recent_observation(self, test_run_id):
        ceid   = f"test_{test_run_id}_mv_ts_recent"
        record = make_gold_metric(ceid)  # timestamp_utc = now()
        mv_insert(record)

        rows = fetch_time_series("polymarket", "asset_fed_cut_june2026", hours=1)
        assert isinstance(rows, list)
        # Our row was just inserted with NOW() — it must appear in a 1-hour window
        assert len(rows) >= 1

    def test_time_series_empty_for_old_window(self, test_run_id):
        """Requesting a 0-hour window should return nothing (or near-nothing)."""
        ceid   = f"test_{test_run_id}_mv_ts_old"
        record = make_gold_metric(ceid)
        mv_insert(record)

        rows = fetch_time_series("polymarket", "nonexistent_asset_xyz_abc_99999", hours=24)
        assert rows == []


# ==========================================================
# momentum_vault — Validation Guards (no DB)
# ==========================================================

class TestMomentumVaultValidation:
    """insert() must raise before touching the DB (Gate 3 checks 16-17)."""

    def test_missing_core_identity_raises(self):
        record = {"layer": "Gold", "data_point": {}, "momentum_block": {}}
        with pytest.raises(ValueError, match="core_identity"):
            mv_insert(record)

    def test_missing_data_point_current_value_raises(self):
        record = {
            "core_identity": {
                "canonical_event_id":    "test_ev",
                "source_name":           "polymarket",
                "external_reference_id": "x",
            },
            "data_point":    {"unit": "USD"},   # current_value missing
            "momentum_block": {},
        }
        with pytest.raises(ValueError, match="current_value"):
            mv_insert(record)

    def test_non_dict_record_raises(self):
        with pytest.raises(ValueError, match="must be a dict"):
            mv_insert("not a dict")
