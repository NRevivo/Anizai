"""
Gate 2 (Silver) — Logic Gate: Google Trends Silver Processing Validation.

Validates that the Silver Job's Google Trends branch transforms and routes
correctly using mock payloads — no live Flink cluster, no Kafka, no Pytrends.

What Gate 2 Silver covers (Section 9.3 Gate 2):

  ROUTING — HAPPY PATHS
  [01] Valid static mode payload      → SILVER_STRUCTURED_METRICS
  [02] Valid reactive mode payload    → SILVER_STRUCTURED_METRICS
  [03] Valid backfill mode payload    → SILVER_STRUCTURED_METRICS

  ROUTING — DLQ PATHS
  [04] Missing envelope fields        → DEAD_LETTER_QUEUE
  [05] Invalid bronze payload         → DEAD_LETTER_QUEUE
  [06] interest_score missing         → DEAD_LETTER_QUEUE
  [07] interest_score non-numeric     → DEAD_LETTER_QUEUE
  [08] interest_score > 100           → DEAD_LETTER_QUEUE (out of range)
  [09] interest_score < 0             → DEAD_LETTER_QUEUE (out of range)
  [10] keyword empty string           → DEAD_LETTER_QUEUE
  [11] DLQ record carries source_topic == BRONZE_GOOGLETRENDS (not POLYMARKET or FRED)
  [12] DLQ record carries original_message intact for replay

  SILVER SCHEMA COMPLIANCE
  [13] Silver output passes validate_silver_structured_metric()
  [14] schema_version == "2.0", layer == "Silver", entity_type == "Structured_Metric"
  [15] core_identity.source_name == "googletrends"
  [16] core_identity.external_reference_id == keyword (stub _translate_keyword returns unchanged)
  [17] core_identity.metric_id is a valid UUID
  [18] core_identity.canonical_event_id == Bronze envelope event_id
  [19] data_point.current_value == interest_score from raw_payload
  [20] data_point.unit == "interest_score_0_100"
  [21] data_point.status == "trending" for static mode
  [22] data_point.status == "observed" for reactive mode
  [23] data_point.status == "observed" for backfill mode

  TIMESTAMP LOGIC
  [24] timestamp_utc == fetch_timestamp for static mode (no observation_date)
  [25] timestamp_utc == observation_date + "T00:00:00+00:00" for reactive mode
  [26] timestamp_utc == observation_date + "T00:00:00+00:00" for backfill mode

  MOMENTUM BLOCK STUBS
  [27] momentum_block.change_24h == 0.0  (Gold Job computes real deltas, Section 4.2B)
  [28] momentum_block.change_7d  == 0.0
  [29] momentum_block.change_30d == 0.0
  [30] momentum_block.is_new_market == False

  METADATA EXTENSION
  [31] metadata_extension contains: keyword, keyword_en, geo, geo_name, mode
  [32] metadata_extension.rank_in_trending == raw rank for static mode
  [33] metadata_extension.rank_in_trending is None for reactive mode
  [34] metadata_extension.rank_in_trending is None for backfill mode
  [35] metadata_extension.timeframe is None for static mode
  [36] metadata_extension.timeframe == raw timeframe for reactive/backfill

  END-TO-END MOCK
  [37] Mock payload from googletrends_bronze_payload.json round-trips correctly

Test strategy:
    Fixtures build full Bronze envelopes via build_bronze_message() (same as
    the producer), then pass directly to process_googletrends_message() — the
    pure dispatch function in silver_job.py. No Flink, no Kafka, no network I/O.

References:
    - Section 4.1:  Silver Job specification
    - Section 3.5:  Dead-Letter Queue routing — never silently drop
    - Section 4.2B: Gold Job keyed state (why momentum stubs are 0.0)
    - Section 4.4:  Mock-Driven Development
    - Section 9.3:  Triple-Gate Test Matrix — Gate 2
    - Section B.5:  Google Trends parameters
    - Section C.2:  Silver Structured Metric schema
"""

import copy
import json
import uuid
from pathlib import Path

import pytest

from config.kafka_topics import (
    BRONZE_GOOGLETRENDS,
    DEAD_LETTER_QUEUE,
    SILVER_STRUCTURED_METRICS,
)
from ingestion.googletrends_producer import (
    SOURCE_NAME,
    TIMEFRAME_7D,
    TIMEFRAME_5Y,
)
from processing.silver_job import (
    map_googletrends_to_silver,
    process_googletrends_message,
)
from utils.kafka_utils import build_bronze_message
from utils.validators import validate_silver_structured_metric

# ==========================================================
# Paths & Constants
# ==========================================================

MOCKS_DIR = Path(__file__).parent.parent / "mocks"
BRONZE_ENDPOINT = "https://trends.google.com/trends/explore?geo=US&q=Inflation&mode=static"


# ==========================================================
# Helpers
# ==========================================================

def _make_envelope(raw_payload: dict) -> dict:
    """Wrap a raw_payload dict in a full Bronze envelope (same as the producer)."""
    return build_bronze_message(
        source_name=SOURCE_NAME,
        source_endpoint=BRONZE_ENDPOINT,
        raw_payload=raw_payload,
    )


def _static_raw(
    keyword: str = "Inflation",
    geo: str = "US",
    interest_score: float = 92.0,
    rank_in_trending: int = 5,
    fetch_timestamp: str = "2026-04-02T10:00:00+00:00",
) -> dict:
    """Build a static mode raw_payload."""
    return {
        "keyword":         keyword,
        "geo":             geo,
        "geo_name":        "United States",
        "interest_score":  interest_score,
        "mode":            "static",
        "fetch_timestamp": fetch_timestamp,
        "rank_in_trending": rank_in_trending,
    }


def _reactive_raw(
    keyword: str = "Inflation",
    geo: str = "US",
    interest_score: float = 72.0,
    observation_date: str = "2026-04-01",
    timeframe: str = TIMEFRAME_7D,
    fetch_timestamp: str = "2026-04-02T10:00:00+00:00",
) -> dict:
    """Build a reactive mode raw_payload."""
    return {
        "keyword":          keyword,
        "geo":              geo,
        "geo_name":         "United States",
        "interest_score":   interest_score,
        "mode":             "reactive",
        "fetch_timestamp":  fetch_timestamp,
        "observation_date": observation_date,
        "timeframe":        timeframe,
    }


def _backfill_raw(
    keyword: str = "Crude Oil",
    geo: str = "DE",
    interest_score: float = 55.0,
    observation_date: str = "2022-03-07",
    fetch_timestamp: str = "2026-04-02T10:00:00+00:00",
) -> dict:
    """Build a backfill mode raw_payload."""
    return {
        "keyword":          keyword,
        "geo":              geo,
        "geo_name":         "Germany",
        "interest_score":   interest_score,
        "mode":             "backfill",
        "fetch_timestamp":  fetch_timestamp,
        "observation_date": observation_date,
        "timeframe":        TIMEFRAME_5Y,
    }


# ==========================================================
# [01–03] Routing — Happy Paths
# ==========================================================

class TestRoutingHappyPaths:
    """Gate 2 Silver checks [01]–[03]: valid payloads → SILVER_STRUCTURED_METRICS."""

    def test_01_static_routes_to_silver_structured_metrics(self):
        """[01] Valid static mode → SILVER_STRUCTURED_METRICS."""
        envelope = _make_envelope(_static_raw())
        topic, record = process_googletrends_message(envelope)
        assert topic == SILVER_STRUCTURED_METRICS
        assert record is not None

    def test_02_reactive_routes_to_silver_structured_metrics(self):
        """[02] Valid reactive mode → SILVER_STRUCTURED_METRICS."""
        envelope = _make_envelope(_reactive_raw())
        topic, record = process_googletrends_message(envelope)
        assert topic == SILVER_STRUCTURED_METRICS
        assert record is not None

    def test_03_backfill_routes_to_silver_structured_metrics(self):
        """[03] Valid backfill mode → SILVER_STRUCTURED_METRICS."""
        envelope = _make_envelope(_backfill_raw())
        topic, record = process_googletrends_message(envelope)
        assert topic == SILVER_STRUCTURED_METRICS
        assert record is not None


# ==========================================================
# [04–12] Routing — DLQ Paths
# ==========================================================

class TestRoutingDLQPaths:
    """Gate 2 Silver checks [04]–[12]: invalid payloads → DEAD_LETTER_QUEUE."""

    def test_04_missing_envelope_fields_routes_to_dlq(self):
        """[04] Envelope missing required fields → DEAD_LETTER_QUEUE."""
        bad_envelope = {"event_id": str(uuid.uuid4())}  # stripped-down, invalid
        topic, record = process_googletrends_message(bad_envelope)
        assert topic == DEAD_LETTER_QUEUE

    def test_05_invalid_bronze_payload_routes_to_dlq(self):
        """[05] Payload dict missing Bronze schema fields → DEAD_LETTER_QUEUE."""
        envelope = _make_envelope(_static_raw())
        envelope["payload"] = {"not_a_valid_payload": True}
        topic, record = process_googletrends_message(envelope)
        assert topic == DEAD_LETTER_QUEUE

    def test_06_missing_interest_score_routes_to_dlq(self):
        """[06] raw_payload missing interest_score → DEAD_LETTER_QUEUE."""
        raw = _static_raw()
        del raw["interest_score"]
        envelope = _make_envelope(raw)
        topic, record = process_googletrends_message(envelope)
        assert topic == DEAD_LETTER_QUEUE

    def test_07_non_numeric_interest_score_routes_to_dlq(self):
        """[07] interest_score is a non-numeric string → DEAD_LETTER_QUEUE."""
        raw = _static_raw()
        raw["interest_score"] = "not-a-number"
        envelope = _make_envelope(raw)
        topic, record = process_googletrends_message(envelope)
        assert topic == DEAD_LETTER_QUEUE

    def test_08_interest_score_above_100_routes_to_dlq(self):
        """[08] interest_score = 100.1 (above valid range) → DEAD_LETTER_QUEUE."""
        raw = _static_raw(interest_score=100.1)
        envelope = _make_envelope(raw)
        topic, record = process_googletrends_message(envelope)
        assert topic == DEAD_LETTER_QUEUE

    def test_09_interest_score_below_0_routes_to_dlq(self):
        """[09] interest_score = -0.1 (below valid range) → DEAD_LETTER_QUEUE."""
        raw = _static_raw(interest_score=-0.1)
        envelope = _make_envelope(raw)
        topic, record = process_googletrends_message(envelope)
        assert topic == DEAD_LETTER_QUEUE

    def test_10_empty_keyword_routes_to_dlq(self):
        """[10] keyword is an empty string → DEAD_LETTER_QUEUE."""
        raw = _static_raw(keyword="   ")  # whitespace-only
        envelope = _make_envelope(raw)
        topic, record = process_googletrends_message(envelope)
        assert topic == DEAD_LETTER_QUEUE

    def test_11_dlq_record_carries_bronze_googletrends_source_topic(self):
        """[11] DLQ record source_topic == BRONZE_GOOGLETRENDS, not FRED or POLYMARKET."""
        raw = _static_raw(interest_score=200.0)  # invalid → DLQ
        envelope = _make_envelope(raw)
        topic, dlq_record = process_googletrends_message(envelope)
        assert topic == DEAD_LETTER_QUEUE
        assert dlq_record["source_topic"] == BRONZE_GOOGLETRENDS

    def test_12_dlq_record_carries_original_message_intact(self):
        """[12] DLQ record original_message == the original envelope for replay."""
        raw = _static_raw(interest_score=-5.0)  # invalid → DLQ
        envelope = _make_envelope(raw)
        _, dlq_record = process_googletrends_message(envelope)
        assert dlq_record["original_message"] == envelope


# ==========================================================
# [13–20] Silver Schema Compliance
# ==========================================================

class TestSilverSchemaCompliance:
    """Gate 2 Silver checks [13]–[20]: schema structure and key field values."""

    def _process_static(self) -> dict:
        envelope = _make_envelope(_static_raw())
        _, silver = process_googletrends_message(envelope)
        return silver

    def test_13_validate_silver_structured_metric_passes(self):
        """[13] Silver output passes validate_silver_structured_metric()."""
        silver = self._process_static()
        result = validate_silver_structured_metric(silver)
        assert result.is_valid, f"validate_silver_structured_metric() failed: {result.errors}"

    def test_14_top_level_schema_fields(self):
        """[14] schema_version == '2.0', layer == 'Silver', entity_type == 'Structured_Metric'."""
        silver = self._process_static()
        assert silver["schema_version"] == "2.0"
        assert silver["layer"] == "Silver"
        assert silver["entity_type"] == "Structured_Metric"

    def test_15_source_name_is_googletrends(self):
        """[15] core_identity.source_name == 'googletrends'."""
        silver = self._process_static()
        assert silver["core_identity"]["source_name"] == "googletrends"

    def test_16_external_reference_id_equals_keyword(self):
        """[16] core_identity.external_reference_id == keyword (stub translate returns unchanged)."""
        keyword = "Inflation"
        envelope = _make_envelope(_static_raw(keyword=keyword))
        _, silver = process_googletrends_message(envelope)
        assert silver["core_identity"]["external_reference_id"] == keyword

    def test_17_metric_id_is_valid_uuid(self):
        """[17] core_identity.metric_id is a valid UUID string."""
        silver = self._process_static()
        metric_id = silver["core_identity"]["metric_id"]
        try:
            uuid.UUID(metric_id)
        except ValueError:
            pytest.fail(f"metric_id='{metric_id}' is not a valid UUID")

    def test_18_canonical_event_id_matches_envelope_event_id(self):
        """[18] core_identity.canonical_event_id == Bronze envelope event_id."""
        envelope = _make_envelope(_static_raw())
        _, silver = process_googletrends_message(envelope)
        assert silver["core_identity"]["canonical_event_id"] == envelope["event_id"]

    def test_19_current_value_equals_interest_score(self):
        """[19] data_point.current_value == interest_score from raw_payload."""
        score = 88.0
        envelope = _make_envelope(_static_raw(interest_score=score))
        _, silver = process_googletrends_message(envelope)
        assert silver["data_point"]["current_value"] == pytest.approx(score)

    def test_20_unit_is_interest_score_0_100(self):
        """[20] data_point.unit == 'interest_score_0_100'."""
        silver = self._process_static()
        assert silver["data_point"]["unit"] == "interest_score_0_100"


# ==========================================================
# [21–23] Status Field by Mode
# ==========================================================

class TestStatusByMode:
    """Gate 2 Silver checks [21]–[23]: data_point.status per mode."""

    def test_21_status_trending_for_static_mode(self):
        """[21] data_point.status == 'trending' for static mode."""
        envelope = _make_envelope(_static_raw())
        _, silver = process_googletrends_message(envelope)
        assert silver["data_point"]["status"] == "trending"

    def test_22_status_observed_for_reactive_mode(self):
        """[22] data_point.status == 'observed' for reactive mode."""
        envelope = _make_envelope(_reactive_raw())
        _, silver = process_googletrends_message(envelope)
        assert silver["data_point"]["status"] == "observed"

    def test_23_status_observed_for_backfill_mode(self):
        """[23] data_point.status == 'observed' for backfill mode."""
        envelope = _make_envelope(_backfill_raw())
        _, silver = process_googletrends_message(envelope)
        assert silver["data_point"]["status"] == "observed"


# ==========================================================
# [24–26] Timestamp Logic by Mode
# ==========================================================

class TestTimestampLogic:
    """Gate 2 Silver checks [24]–[26]: timestamp_utc derivation."""

    def test_24_static_timestamp_utc_uses_fetch_timestamp(self):
        """[24] static mode: timestamp_utc == fetch_timestamp (no observation_date)."""
        fetch_ts = "2026-04-02T10:00:00+00:00"
        envelope = _make_envelope(_static_raw(fetch_timestamp=fetch_ts))
        _, silver = process_googletrends_message(envelope)
        assert silver["data_point"]["timestamp_utc"] == fetch_ts

    def test_25_reactive_timestamp_utc_uses_observation_date(self):
        """[25] reactive mode: timestamp_utc == observation_date + 'T00:00:00+00:00'."""
        obs_date = "2026-04-01"
        envelope = _make_envelope(_reactive_raw(observation_date=obs_date))
        _, silver = process_googletrends_message(envelope)
        assert silver["data_point"]["timestamp_utc"] == f"{obs_date}T00:00:00+00:00"

    def test_26_backfill_timestamp_utc_uses_observation_date(self):
        """[26] backfill mode: timestamp_utc == observation_date + 'T00:00:00+00:00'."""
        obs_date = "2022-03-07"
        envelope = _make_envelope(_backfill_raw(observation_date=obs_date))
        _, silver = process_googletrends_message(envelope)
        assert silver["data_point"]["timestamp_utc"] == f"{obs_date}T00:00:00+00:00"


# ==========================================================
# [27–30] Momentum Block Stubs
# ==========================================================

class TestMomentumBlockStubs:
    """Gate 2 Silver checks [27]–[30]: all momentum stubs == 0.0 at Silver layer."""

    def test_27_to_30_momentum_stubs_are_zero(self):
        """[27-30] change_24h/7d/30d == 0.0, is_new_market == False at Silver."""
        envelope = _make_envelope(_static_raw())
        _, silver = process_googletrends_message(envelope)
        mb = silver["momentum_block"]
        assert mb["change_24h"]    == 0.0,  "change_24h stub must be 0.0"
        assert mb["change_7d"]     == 0.0,  "change_7d stub must be 0.0"
        assert mb["change_30d"]    == 0.0,  "change_30d stub must be 0.0"
        assert mb["is_new_market"] is False, "is_new_market stub must be False"


# ==========================================================
# [31–36] Metadata Extension
# ==========================================================

class TestMetadataExtension:
    """Gate 2 Silver checks [31]–[36]: metadata_extension field presence and values."""

    def test_31_common_metadata_fields_present(self):
        """[31] keyword, keyword_en, geo, geo_name, mode are all present."""
        envelope = _make_envelope(_static_raw(keyword="NATO", geo="GB"))
        _, silver = process_googletrends_message(envelope)
        meta = silver["metadata_extension"]
        for field in ("keyword", "keyword_en", "geo", "geo_name", "mode"):
            assert field in meta, f"metadata_extension missing '{field}'"

    def test_32_rank_in_trending_present_for_static(self):
        """[32] metadata_extension.rank_in_trending == raw rank for static mode."""
        rank = 7
        envelope = _make_envelope(_static_raw(rank_in_trending=rank))
        _, silver = process_googletrends_message(envelope)
        assert silver["metadata_extension"]["rank_in_trending"] == rank

    def test_33_rank_in_trending_is_none_for_reactive(self):
        """[33] metadata_extension.rank_in_trending is None for reactive mode."""
        envelope = _make_envelope(_reactive_raw())
        _, silver = process_googletrends_message(envelope)
        assert silver["metadata_extension"]["rank_in_trending"] is None

    def test_34_rank_in_trending_is_none_for_backfill(self):
        """[34] metadata_extension.rank_in_trending is None for backfill mode."""
        envelope = _make_envelope(_backfill_raw())
        _, silver = process_googletrends_message(envelope)
        assert silver["metadata_extension"]["rank_in_trending"] is None

    def test_35_timeframe_is_none_for_static(self):
        """[35] metadata_extension.timeframe is None for static mode."""
        envelope = _make_envelope(_static_raw())
        _, silver = process_googletrends_message(envelope)
        assert silver["metadata_extension"]["timeframe"] is None

    def test_36_timeframe_present_for_reactive_and_backfill(self):
        """[36] metadata_extension.timeframe == raw timeframe for reactive/backfill."""
        for raw, expected in [
            (_reactive_raw(), TIMEFRAME_7D),
            (_backfill_raw(), TIMEFRAME_5Y),
        ]:
            envelope = _make_envelope(raw)
            _, silver = process_googletrends_message(envelope)
            assert silver["metadata_extension"]["timeframe"] == expected, (
                f"mode={raw['mode']}: expected timeframe='{expected}', "
                f"got '{silver['metadata_extension']['timeframe']}'"
            )


# ==========================================================
# [37] End-to-End Mock Round-Trip
# ==========================================================

class TestMockRoundTrip:
    """Gate 2 Silver check [37]: mock payload from file processes end-to-end."""

    def test_37_mock_payload_processes_correctly(self):
        """[37] googletrends_bronze_payload.json can be processed without DLQ routing."""
        mock_path = MOCKS_DIR / "googletrends_bronze_payload.json"
        raw = json.loads(mock_path.read_text())
        # Remove the comment field — not part of the actual payload
        raw.pop("_comment", None)

        envelope = _make_envelope(raw)
        topic, silver = process_googletrends_message(envelope)

        assert topic == SILVER_STRUCTURED_METRICS, (
            f"Mock payload routed to '{topic}' instead of SILVER_STRUCTURED_METRICS"
        )
        result = validate_silver_structured_metric(silver)
        assert result.is_valid, f"Mock Silver output invalid: {result.errors}"
        assert silver["core_identity"]["source_name"] == "googletrends"
        assert silver["data_point"]["current_value"] == pytest.approx(92.0)
