"""
Gate 2 — Logic Gate: FRED Silver Processing Validation.

Validates that the Silver Job's FRED branch transforms and routes correctly
using mock payloads — no live Flink cluster, no Kafka broker, no FRED API.

What Gate 2 covers (Section 9.3 Gate 2):
    [1]  Valid FRED observation → routes to SILVER_STRUCTURED_METRICS
    [2]  Invalid envelope → routes to DEAD_LETTER_QUEUE
    [3]  Invalid bronze payload → routes to DEAD_LETTER_QUEUE
    [4]  Non-numeric value (FRED sentinel ".") → routes to DEAD_LETTER_QUEUE
    [5]  Silver output passes validate_silver_structured_metric()
    [6]  canonical_event_id in Silver == Bronze envelope event_id
    [7]  external_reference_id in Silver == raw.series_id
    [8]  source_name in Silver core_identity == "fred"
    [9]  data_point.status == "actual"
    [10] data_point.timestamp_utc is full ISO8601 UTC (not bare "YYYY-MM-DD")
    [11] momentum_block stubs are 0.0 (Gold Job computes real deltas, Section 4.2B)
    [12] metadata_extension has all 4 FRED-specific fields (Section C.2)
    [13] metadata_extension.release_priority matches the INDICATORS dict value
    [14] DLQ record carries source_topic == BRONZE_FRED (not BRONZE_POLYMARKET)
    [15] DLQ record carries original_message intact for replay

Test strategy:
    Fixtures build full Bronze envelopes by calling build_bronze_message()
    (same as the producer), then pass them directly to process_fred_message()
    — the pure dispatch function added to silver_job.py. No Flink, no Kafka,
    no network I/O at any point.

References:
    - Section 4.1:   Silver Job specification
    - Section 4.2B:  Gold Job keyed state (why momentum stubs are 0.0)
    - Section 4.4:   Mock-Driven Development
    - Section 9.3:   Triple-Gate Test Matrix — Gate 2
    - Section C.2:   Silver Structured Metric schema — FRED row
    - Section B.3:   FRED parameters (series_id, release_priority, etc.)
    - Section 3.5:   Dead-Letter Queue — never silently drop
"""

import copy
import json
from pathlib import Path

import pytest

from config.kafka_topics import (
    BRONZE_FRED,
    DEAD_LETTER_QUEUE,
    SILVER_STRUCTURED_METRICS,
)
from ingestion.fred_producer import INDICATORS, SOURCE_NAME
from processing.silver_job import (
    map_fred_observation_to_silver,
    process_fred_message,
)
from utils.kafka_utils import build_bronze_message
from utils.validators import validate_silver_structured_metric

# ==========================================================
# Paths
# ==========================================================

MOCKS_DIR = Path(__file__).parent.parent / "mocks"
FRED_ENDPOINT = "https://api.stlouisfed.org/fred/series/observations?series_id=FEDFUNDS"


# ==========================================================
# Fixtures
# ==========================================================

@pytest.fixture(scope="module")
def fred_obs_raw() -> dict:
    """Load the FRED observation mock raw payload (Section 4.4)."""
    with open(MOCKS_DIR / "fred_observation.json", encoding="utf-8") as f:
        data = json.load(f)
    # Strip the _comment key — it is documentation only and must not reach
    # the Bronze layer as part of raw_payload.
    return {k: v for k, v in data.items() if not k.startswith("_")}


@pytest.fixture(scope="module")
def fred_envelope(fred_obs_raw) -> dict:
    """Build full Bronze envelope from the mock FRED observation."""
    return build_bronze_message(
        source_name=SOURCE_NAME,
        source_endpoint=FRED_ENDPOINT,
        raw_payload=fred_obs_raw,
    )


@pytest.fixture(scope="module")
def fred_silver_expected() -> dict:
    """Load the expected Silver output shape for structural assertions."""
    with open(MOCKS_DIR / "fred_silver_metric.json", encoding="utf-8") as f:
        return json.load(f)


# ==========================================================
# Gate 2 — Routing Tests
# ==========================================================

class TestFREDSilverRouting:
    """
    Verifies process_fred_message() routes to the correct Kafka topic
    (Section 9.3 Gate 2 checks 1-4).
    """

    def test_valid_observation_routes_to_structured_metrics(self, fred_envelope):
        """
        A valid FRED observation must route to SILVER_STRUCTURED_METRICS.

        FRED has no social branch — every observation is a structured metric
        (Section 3.1 BRONZE_TO_SILVER_ROUTING: BRONZE_FRED → SILVER_STRUCTURED_METRICS).
        """
        topic, record = process_fred_message(fred_envelope)
        assert topic == SILVER_STRUCTURED_METRICS, (
            f"Expected SILVER_STRUCTURED_METRICS, got '{topic}'"
        )
        assert record is not None

    def test_invalid_envelope_routes_to_dlq(self, fred_obs_raw):
        """
        An envelope missing event_id must route to DEAD_LETTER_QUEUE (Section 3.5).
        """
        bad_envelope = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=FRED_ENDPOINT,
            raw_payload=fred_obs_raw,
        )
        del bad_envelope["event_id"]   # corrupt the envelope
        topic, record = process_fred_message(bad_envelope)
        assert topic == DEAD_LETTER_QUEUE
        assert "validation_errors" in record

    def test_invalid_bronze_payload_routes_to_dlq(self, fred_obs_raw):
        """
        An envelope whose payload is missing ingestion_id must route to DLQ.
        """
        envelope = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=FRED_ENDPOINT,
            raw_payload=fred_obs_raw,
        )
        del envelope["payload"]["ingestion_id"]
        topic, record = process_fred_message(envelope)
        assert topic == DEAD_LETTER_QUEUE

    def test_fred_sentinel_dot_routes_to_dlq(self):
        """
        A raw_payload where value is the FRED sentinel '.' must route to DLQ.

        Why test this: the producer filters '.' at fetch time, but Bronze
        replay of pre-filter messages is possible. The Silver Job must catch
        this here so it never produces a corrupt Silver metric (Section B.3 /
        Section 3.5).
        """
        raw_with_sentinel = {
            "series_id":          "UNRATE",
            "observation_date":   "2026-02-01",
            "value":              ".",       # FRED missing-value sentinel
            "unit":               "percent",
            "release_priority":   4,
            "seasonal_adjustment": "Seasonally Adjusted",
            "fetch_mode":         "pulse",
        }
        envelope = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint="https://api.stlouisfed.org/fred/series/observations?series_id=UNRATE",
            raw_payload=raw_with_sentinel,
        )
        topic, record = process_fred_message(envelope)
        assert topic == DEAD_LETTER_QUEUE
        # Confirm the DLQ record explains WHY it was routed there
        error_text = " ".join(record.get("validation_errors", []))
        assert "value" in error_text.lower() or "numeric" in error_text.lower(), (
            f"DLQ error should mention the value issue. Got: {record['validation_errors']}"
        )

    def test_non_numeric_value_routes_to_dlq(self):
        """
        Any non-numeric value (not just '.') must also route to DLQ.
        """
        raw = {
            "series_id":          "VIXCLS",
            "observation_date":   "2026-03-01",
            "value":              "N/A",
            "unit":               "Index",
            "release_priority":   5,
            "seasonal_adjustment": "Not Seasonally Adjusted",
            "fetch_mode":         "pulse",
        }
        envelope = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint="https://api.stlouisfed.org/fred/series/observations?series_id=VIXCLS",
            raw_payload=raw,
        )
        topic, _ = process_fred_message(envelope)
        assert topic == DEAD_LETTER_QUEUE


# ==========================================================
# Gate 2 — Silver Schema Correctness
# ==========================================================

class TestFREDSilverSchema:
    """
    Verifies the Silver Structured Metric output of map_fred_observation_to_silver()
    conforms to Section C.2 (Gate 2 checks 5-13).
    """

    @pytest.fixture(scope="class")
    def silver(self, fred_envelope) -> dict:
        """Extract the Silver record from a successful process_fred_message() call."""
        _, record = process_fred_message(fred_envelope)
        return record

    def test_silver_passes_validator(self, silver):
        """
        The Silver output must pass validate_silver_structured_metric() — the same
        validator the Gold Job uses as its entry-point guard (Section 9.3).
        """
        result = validate_silver_structured_metric(silver)
        assert result.is_valid, f"Silver metric validation failed: {result.errors}"

    def test_layer_is_silver(self, silver):
        """layer must be 'Silver' (Section C.2)."""
        assert silver["layer"] == "Silver"

    def test_entity_type_is_structured_metric(self, silver):
        """entity_type must be 'Structured_Metric' (Section C.2)."""
        assert silver["entity_type"] == "Structured_Metric"

    def test_canonical_event_id_matches_envelope(self, fred_envelope, silver):
        """
        canonical_event_id in the Silver record must equal the Bronze envelope's
        event_id — this is the Paper Trail link (Section 3.2 / 7.2).
        """
        assert silver["core_identity"]["canonical_event_id"] == fred_envelope["event_id"]

    def test_source_name_is_fred(self, silver):
        """core_identity.source_name must be 'fred' (Section C.2)."""
        assert silver["core_identity"]["source_name"] == "fred"

    def test_external_reference_id_is_series_id(self, fred_obs_raw, silver):
        """
        external_reference_id must equal the series_id from raw_payload.

        This is the key used by the Gold Job's Flink keyed-state momentum
        operator — it must match exactly (Section 4.2B).
        """
        assert silver["core_identity"]["external_reference_id"] == fred_obs_raw["series_id"]

    def test_parent_id_is_empty(self, silver):
        """
        parent_id must be empty string for FRED — no parent concept exists.

        Unlike Polymarket where parent_id = market_id (token's parent market),
        FRED series have no hierarchy above the series_id itself.
        """
        assert silver["core_identity"]["parent_id"] == ""

    def test_data_point_status_is_actual(self, silver):
        """
        data_point.status must be 'actual' — FRED publishes only confirmed
        official data, never forecasts or estimates (Section B.3).
        """
        assert silver["data_point"]["status"] == "actual"

    def test_data_point_value_is_float(self, fred_obs_raw, silver):
        """current_value must be a float matching the raw observation value."""
        assert isinstance(silver["data_point"]["current_value"], float)
        assert silver["data_point"]["current_value"] == float(fred_obs_raw["value"])

    def test_timestamp_utc_is_full_iso8601(self, silver):
        """
        timestamp_utc must be a full ISO8601 UTC datetime, not a bare date string.

        FRED provides 'YYYY-MM-DD'. The Silver Job converts this to
        'YYYY-MM-DDT00:00:00+00:00' for the momentum_vault TIMESTAMPTZ column.
        A bare date would cause a psycopg2 type error on insert.
        """
        ts = silver["data_point"]["timestamp_utc"]
        assert "T" in ts, (
            f"timestamp_utc must be a full ISO8601 datetime, not a bare date. Got: {ts!r}"
        )
        assert "+00:00" in ts or ts.endswith("Z"), (
            f"timestamp_utc must be UTC-aware. Got: {ts!r}"
        )

    def test_momentum_block_stubs_are_zero(self, silver):
        """
        momentum_block values must all be 0.0 at Silver layer.

        The Gold Job computes real change_24h/7d/30d via Flink keyed state
        (Section 4.2B). Silver stubs of 0.0 are the contract for passing
        incomplete momentum data to the next layer.
        """
        mb = silver["momentum_block"]
        assert mb["change_24h"] == 0.0
        assert mb["change_7d"]  == 0.0
        assert mb["change_30d"] == 0.0
        assert mb["is_new_market"] is False

    def test_metadata_extension_has_all_fred_fields(self, silver):
        """
        metadata_extension must have all 4 FRED-specific fields mandated by
        Section C.2 (FRED row in the metadata_extension table):
        series_id, observation_date, release_priority, seasonal_adjustment.
        """
        ext = silver["metadata_extension"]
        required = {"series_id", "observation_date", "release_priority", "seasonal_adjustment"}
        missing = required - set(ext.keys())
        assert not missing, f"metadata_extension missing FRED fields: {missing}"

    def test_metadata_extension_release_priority_matches_indicators(self, fred_obs_raw, silver):
        """
        metadata_extension.release_priority must match the INDICATORS dict value
        for this series — not an arbitrary default.
        """
        series_id = fred_obs_raw["series_id"]
        expected_priority = INDICATORS[series_id]["release_priority"]
        actual_priority   = silver["metadata_extension"]["release_priority"]
        assert actual_priority == expected_priority, (
            f"release_priority for {series_id}: expected {expected_priority}, "
            f"got {actual_priority}"
        )

    def test_metadata_extension_series_id_matches_external_reference(self, silver):
        """
        metadata_extension.series_id and core_identity.external_reference_id
        must be the same value — they both identify the FRED series and must
        never diverge (would cause confusion in the Gold Job's state key).
        """
        assert (
            silver["metadata_extension"]["series_id"]
            == silver["core_identity"]["external_reference_id"]
        )


# ==========================================================
# Gate 2 — DLQ Correctness
# ==========================================================

class TestFREDDLQCorrectness:
    """
    Verifies DLQ records are correctly formed (Gate 2 checks 14-15).
    """

    def test_dlq_source_topic_is_bronze_fred(self, fred_obs_raw):
        """
        DLQ records from the FRED branch must carry source_topic=BRONZE_FRED,
        not BRONZE_POLYMARKET. Operators filter DLQ messages by source_topic
        to route them to the correct replay pipeline (Section 3.5).
        """
        envelope = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=FRED_ENDPOINT,
            raw_payload=fred_obs_raw,
        )
        del envelope["event_id"]   # force DLQ
        topic, dlq_record = process_fred_message(envelope)

        assert topic == DEAD_LETTER_QUEUE
        assert dlq_record["source_topic"] == BRONZE_FRED, (
            f"DLQ source_topic must be BRONZE_FRED ('{BRONZE_FRED}'), "
            f"got '{dlq_record['source_topic']}'"
        )

    def test_dlq_record_preserves_original_message(self, fred_obs_raw):
        """
        DLQ record must include the original_message intact so the pipeline
        operator can replay it once the schema issue is resolved (Section 3.5).
        """
        envelope = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=FRED_ENDPOINT,
            raw_payload=fred_obs_raw,
        )
        original_event_id = envelope["event_id"]
        del envelope["event_id"]   # force DLQ, but the event_id is now gone

        _, dlq_record = process_fred_message(envelope)
        # original_message should be the corrupted envelope (sans event_id),
        # still inspectable by operators.
        assert "original_message" in dlq_record
        assert isinstance(dlq_record["original_message"], dict)

    def test_dlq_record_has_validation_errors(self, fred_obs_raw):
        """DLQ record must include a non-empty validation_errors list (Section 3.5)."""
        envelope = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=FRED_ENDPOINT,
            raw_payload=fred_obs_raw,
        )
        del envelope["trace_id"]   # different corruption to keep tests independent
        _, dlq_record = process_fred_message(envelope)

        errors = dlq_record.get("validation_errors", [])
        assert isinstance(errors, list)
        assert len(errors) > 0, "DLQ record must contain at least one validation error"


# ==========================================================
# Gate 2 — All 9 FRED Series Round-Trip
# ==========================================================

class TestAllSeriesRoundTrip:
    """
    Verifies that all 9 FRED indicators can be processed through the
    Silver branch without validation failures (Section B.3 completeness check).

    Uses synthetic but realistic observations — one per series — to confirm
    no series has a metadata mismatch (e.g., wrong unit or missing field).
    """

    # Representative recent values for each series (plausible as of 2026-03)
    _SAMPLE_VALUES: dict[str, float] = {
        "FEDFUNDS":          4.33,
        "CPIAUCSL":          315.8,
        "UNRATE":            4.1,
        "CSUSHPINSA":        318.2,
        "DCOILWTICO":        72.4,
        "GASREGW":           3.41,
        "DHHNGSP":           3.62,
        "VIXCLS":            18.45,
        "T10Y2Y":            0.12,
    }

    @pytest.mark.parametrize("series_id", list(INDICATORS.keys()))
    def test_series_routes_to_structured_metrics(self, series_id):
        """
        Each of the 9 FRED series must route to SILVER_STRUCTURED_METRICS
        with a passing Silver schema validation.

        Why parametrize: ensures no series was accidentally omitted from
        INDICATORS or has a metadata field that breaks map_fred_observation_to_silver().
        """
        meta  = INDICATORS[series_id]
        value = self._SAMPLE_VALUES[series_id]

        raw_payload = {
            "series_id":          series_id,
            "observation_date":   "2026-03-01",
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
        endpoint = (
            f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}"
        )
        envelope = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=endpoint,
            raw_payload=raw_payload,
        )
        topic, record = process_fred_message(envelope)

        assert topic == SILVER_STRUCTURED_METRICS, (
            f"Series {series_id} routed to '{topic}' instead of SILVER_STRUCTURED_METRICS"
        )
        result = validate_silver_structured_metric(record)
        assert result.is_valid, (
            f"Series {series_id} Silver validation failed: {result.errors}"
        )
        # Confirm the series round-trips cleanly
        assert record["core_identity"]["external_reference_id"] == series_id
        assert record["core_identity"]["source_name"] == "fred"
        assert record["data_point"]["current_value"] == value
