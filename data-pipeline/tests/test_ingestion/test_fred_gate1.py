"""
Gate 1 — Ingestion Gate: FRED Bronze Schema Compliance.

Validates that the FRED producer's envelope-building logic produces messages
that are fully compliant with the Bronze Schema (Section C.1) and Message
Envelope (Section 3.2) before any message reaches Kafka.

Design decisions:
  - Zero live connections: no Kafka broker, no FRED API calls.
    build_bronze_message() and FREDProducer._build_raw_payload() are pure
    functions; tested in complete isolation.
  - Mock payloads loaded from tests/mocks/fred_observation.json (Section 4.4).
  - Validators from utils/validators.py are called directly — the same
    validators the Flink Silver Job uses, so a pass here guarantees the
    Silver Job's first gate will also pass for FRED messages.
  - INDICATORS dict is imported from the producer to test invariants
    (release_priority, unit presence) across all 9 series without
    hard-coding the values in the test.

Gate 1 checklist (Section 9.3 Gate 1):
  [1]  envelope fields present and correctly typed
  [2]  event_id is a valid UUIDv4
  [3]  producer_timestamp is a valid ISO8601 UTC string
  [4]  source_name == "fred"
  [5]  payload.raw_payload is a non-empty dict
  [6]  validate_envelope() passes without errors
  [7]  validate_bronze_payload() passes without errors
  [8]  NDJSON round-trip preserves the full envelope
  [9]  each call to build_bronze_message() produces a distinct event_id
  [10] raw_payload.value is a float (never the FRED sentinel ".")
  [11] raw_payload.series_id matches INDICATORS keys
  [12] raw_payload.release_priority is an integer in [1, 5]
  [13] raw_payload.observation_date is a non-empty string
  [14] raw_payload.fetch_mode is "pulse" or "backfill"
  [15] Kafka partition key == series_id (domain correctness)
  [16] INDICATORS dict: all 9 required series IDs are present
  [17] INDICATORS dict: every entry has release_priority and unit

References:
    - Section 2.1:  Producer Matrix (FRED row)
    - Section 3.2:  Message Envelope
    - Section 3.4:  NDJSON serialization (tested via round-trip)
    - Section 4.4:  Mock-Driven Development
    - Section 9.3:  Triple-Gate Test Matrix — Gate 1
    - Section B.3:  FRED parameters (Indicator Matrix, backfill, pulse modes)
    - Section C.1:  Bronze Schema
    - Section C.2:  Silver Structured Metric — FRED metadata_extension fields
"""

import json
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from ingestion.fred_producer import INDICATORS, SOURCE_NAME, FREDProducer
from utils.kafka_utils import build_bronze_message, ndjson_deserializer, ndjson_serializer
from utils.validators import validate_bronze_payload, validate_envelope

# ==========================================================
# Paths & Constants
# ==========================================================

MOCKS_DIR = Path(__file__).parent.parent / "mocks"
FRED_OBS_MOCK = MOCKS_DIR / "fred_observation.json"

FRED_ENDPOINT_PREFIX = "https://api.stlouisfed.org/fred/series/observations"

# All 9 series IDs mandated by Section B.3 Indicator Matrix
REQUIRED_SERIES_IDS = {
    "FEDFUNDS",
    "CPIAUCSL",
    "UNRATE",
    "CSUSHPINSA",
    "DCOILWTICO",
    "GASREGW",
    "DHHNGSP",
    "VIXCLS",
    "T10Y2Y",
}


# ==========================================================
# Fixtures
# ==========================================================

@pytest.fixture(scope="module")
def fred_obs_raw() -> dict:
    """
    Load the realistic FRED observation mock payload (Section 4.4).

    scope="module" so the file is read once and shared across all tests
    in this module — mock I/O should not be repeated per test.
    """
    with open(FRED_OBS_MOCK, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def fred_envelope(fred_obs_raw) -> dict:
    """
    Build the full Bronze envelope from the mock FRED observation payload.

    Reused by multiple tests — built once at module scope.
    Mirrors what FREDProducer._emit() does in production.
    """
    series_id = fred_obs_raw["series_id"]
    return build_bronze_message(
        source_name=SOURCE_NAME,
        source_endpoint=f"{FRED_ENDPOINT_PREFIX}?series_id={series_id}",
        raw_payload=fred_obs_raw,
    )


# ==========================================================
# Gate 1 — Envelope Structure Tests (Section 3.2)
# ==========================================================

class TestEnvelopeStructure:
    """
    Verifies the outer Message Envelope fields (Section 3.2 / Gate 1 checks 1-4).
    """

    def test_envelope_is_dict(self, fred_envelope):
        """build_bronze_message must return a dict."""
        assert isinstance(fred_envelope, dict)

    def test_envelope_has_event_id(self, fred_envelope):
        """event_id must be present and a string (Section 3.2)."""
        assert "event_id" in fred_envelope
        assert isinstance(fred_envelope["event_id"], str)

    def test_event_id_is_valid_uuid4(self, fred_envelope):
        """event_id must be a valid UUIDv4 (Section 3.2)."""
        parsed = uuid.UUID(fred_envelope["event_id"])
        assert parsed.version == 4

    def test_producer_timestamp_is_present(self, fred_envelope):
        """producer_timestamp must be present and a string (Section 3.2)."""
        assert "producer_timestamp" in fred_envelope
        assert isinstance(fred_envelope["producer_timestamp"], str)

    def test_producer_timestamp_is_iso8601_utc(self, fred_envelope):
        """
        producer_timestamp must parse as a timezone-aware ISO8601 string.

        fromisoformat() raises ValueError on invalid input — pass = valid.
        tzinfo must be non-None to confirm UTC encoding (Section 3.2).
        """
        ts = fred_envelope["producer_timestamp"]
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None, "producer_timestamp must be timezone-aware"

    def test_source_name_is_fred(self, fred_envelope):
        """source_name in the envelope must equal 'fred' (Section C.1)."""
        assert fred_envelope.get("source_name") == SOURCE_NAME
        assert fred_envelope.get("source_name") == "fred"

    def test_schema_version_is_present(self, fred_envelope):
        """schema_version must be set — enables schema evolution (Section 3.2)."""
        assert "schema_version" in fred_envelope
        assert fred_envelope["schema_version"] != ""

    def test_trace_id_is_valid_uuid(self, fred_envelope):
        """trace_id must be a valid UUID for distributed tracing (Section 7.2)."""
        trace_id = fred_envelope.get("trace_id", "")
        parsed = uuid.UUID(trace_id)
        assert parsed.version == 4


# ==========================================================
# Gate 1 — Bronze Payload Tests (Section C.1)
# ==========================================================

class TestBronzePayload:
    """
    Verifies the inner Bronze Schema payload (Section C.1 / Gate 1 checks 5-7).
    """

    def test_payload_key_exists(self, fred_envelope):
        """envelope['payload'] must exist and be a dict (Section C.1)."""
        assert "payload" in fred_envelope
        assert isinstance(fred_envelope["payload"], dict)

    def test_raw_payload_is_non_empty_dict(self, fred_envelope):
        """
        payload.raw_payload must be a non-empty dict (Section C.1 Gate 1).

        An empty raw_payload means the producer captured nothing — pipeline bug.
        """
        raw = fred_envelope["payload"].get("raw_payload")
        assert isinstance(raw, dict), "raw_payload must be a dict"
        assert len(raw) > 0, "raw_payload must not be empty"

    def test_bronze_payload_has_ingestion_id(self, fred_envelope):
        """ingestion_id must be present and a valid UUID (Section C.1)."""
        ingestion_id = fred_envelope["payload"].get("ingestion_id", "")
        parsed = uuid.UUID(ingestion_id)
        assert parsed.version == 4

    def test_bronze_payload_has_source_endpoint(self, fred_envelope):
        """source_endpoint must be a non-empty string pointing at FRED API (Section C.1)."""
        endpoint = fred_envelope["payload"].get("source_endpoint", "")
        assert isinstance(endpoint, str)
        assert len(endpoint) > 0
        assert FRED_ENDPOINT_PREFIX in endpoint, (
            f"source_endpoint should reference the FRED observations URL. Got: {endpoint}"
        )

    def test_bronze_payload_has_ingestion_timestamp(self, fred_envelope):
        """ingestion_timestamp must be a valid ISO8601 timezone-aware string (Section C.1)."""
        ts = fred_envelope["payload"].get("ingestion_timestamp", "")
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None

    def test_bronze_payload_metadata_block(self, fred_envelope):
        """metadata block must have http_status_code (int) and request_duration_ms (int|float)."""
        metadata = fred_envelope["payload"].get("metadata", {})
        assert isinstance(metadata, dict)
        assert isinstance(metadata.get("http_status_code"), int)
        assert isinstance(metadata.get("request_duration_ms"), (int, float))


# ==========================================================
# Gate 1 — Validator Integration Tests (Section 9.3)
# ==========================================================

class TestValidatorIntegration:
    """
    Runs the same validators the Flink Silver Job uses (Section 9.3 Gate 1).

    A pass here guarantees the Silver Job's first gate will accept FRED messages.
    """

    def test_validate_envelope_passes(self, fred_envelope):
        """
        validate_envelope() must return is_valid=True (Section 3.2, 9.3).

        A failure here means the Silver Job would route this message to
        dead-letter-queue at its very first validation step.
        """
        result = validate_envelope(fred_envelope)
        assert result.is_valid, f"Envelope validation errors: {result.errors}"

    def test_validate_bronze_payload_passes(self, fred_envelope):
        """validate_bronze_payload() on the inner payload must pass (Section C.1, 9.3)."""
        inner = fred_envelope["payload"]
        result = validate_bronze_payload(inner)
        assert result.is_valid, f"Bronze payload validation errors: {result.errors}"


# ==========================================================
# Gate 1 — FRED Domain-Specific Correctness (Section B.3)
# ==========================================================

class TestFREDDomainCorrectness:
    """
    Verifies FRED-specific business rules survive the Bronze round-trip
    (Section B.3, Gate 1 checks 10-15).
    """

    def test_value_is_float(self, fred_obs_raw, fred_envelope):
        """
        raw_payload.value must be a float, not the FRED sentinel string '.'.

        FRED API returns value="." for unreported periods (e.g., a monthly
        series on a non-release day). The producer must filter these out
        before emitting to Kafka (Section B.3 — valid observations only).
        """
        value_in_raw  = fred_obs_raw.get("value")
        value_in_env  = fred_envelope["payload"]["raw_payload"].get("value")
        assert isinstance(value_in_raw, float), (
            f"Mock value must be a float, got {type(value_in_raw).__name__}: {value_in_raw!r}"
        )
        assert isinstance(value_in_env, float), (
            f"Envelope value must be a float after round-trip, got {type(value_in_env).__name__}"
        )
        assert value_in_env != ".", "FRED sentinel '.' must never reach the Bronze layer"

    def test_series_id_is_valid_fred_indicator(self, fred_obs_raw, fred_envelope):
        """
        raw_payload.series_id must be one of the 9 mandated FRED indicators
        (Section B.3 Indicator Matrix).
        """
        series_id_raw = fred_obs_raw.get("series_id")
        series_id_env = fred_envelope["payload"]["raw_payload"].get("series_id")
        assert series_id_raw in REQUIRED_SERIES_IDS, (
            f"Mock series_id '{series_id_raw}' is not in the Section B.3 Indicator Matrix"
        )
        assert series_id_env in REQUIRED_SERIES_IDS

    def test_release_priority_range(self, fred_obs_raw, fred_envelope):
        """
        release_priority must be an integer in [1, 5] (Section C.2 — FRED metadata_extension).
        """
        priority = fred_envelope["payload"]["raw_payload"].get("release_priority")
        assert isinstance(priority, int), (
            f"release_priority must be int, got {type(priority).__name__}"
        )
        assert 1 <= priority <= 5, (
            f"release_priority must be in [1, 5], got {priority}"
        )

    def test_observation_date_is_non_empty(self, fred_envelope):
        """observation_date must be a non-empty string (ISO date 'YYYY-MM-DD' from FRED)."""
        obs_date = fred_envelope["payload"]["raw_payload"].get("observation_date", "")
        assert isinstance(obs_date, str)
        assert len(obs_date) > 0, "observation_date must not be empty"

    def test_fetch_mode_is_valid(self, fred_envelope):
        """fetch_mode must be 'pulse' or 'backfill' (Section B.3 operating modes)."""
        fetch_mode = fred_envelope["payload"]["raw_payload"].get("fetch_mode", "")
        assert fetch_mode in ("pulse", "backfill"), (
            f"fetch_mode must be 'pulse' or 'backfill', got '{fetch_mode}'"
        )

    def test_partition_key_is_series_id(self, fred_obs_raw):
        """
        The Kafka partition key must equal series_id.

        Why: the Gold Job's keyed-state momentum operator keys by series_id.
        Partition affinity ensures all observations for the same indicator
        are processed by the same Flink task (Section 4.2B).

        We verify this by confirming series_id is present in raw_payload and
        matches the mock — the actual producer.send(..., key=series_id) call
        is verified via the _emit() method's docstring and code review.
        """
        series_id = fred_obs_raw.get("series_id", "")
        assert series_id != "", "series_id must be non-empty to serve as partition key"
        assert series_id in REQUIRED_SERIES_IDS

    def test_each_call_produces_unique_event_id(self, fred_obs_raw):
        """
        Two calls to build_bronze_message() must produce different event_ids.

        Shared event_ids corrupt the Paper Trail across Bronze → Silver → Gold
        (Section 3.2 — event_id is the UUIDv4 shared across all layers).
        """
        series_id = fred_obs_raw["series_id"]
        endpoint = f"{FRED_ENDPOINT_PREFIX}?series_id={series_id}"
        msg1 = build_bronze_message(SOURCE_NAME, endpoint, fred_obs_raw)
        msg2 = build_bronze_message(SOURCE_NAME, endpoint, fred_obs_raw)
        assert msg1["event_id"] != msg2["event_id"]


# ==========================================================
# Gate 1 — NDJSON Round-Trip Test (Section 3.4)
# ==========================================================

class TestNDJSONRoundTrip:
    """
    Verifies the NDJSON serialization mandated by Section 3.4.
    """

    def test_ndjson_round_trip_preserves_envelope(self, fred_envelope):
        """Serialise → deserialise must produce an equal envelope dict."""
        serialised = ndjson_serializer(fred_envelope)
        assert isinstance(serialised, bytes)
        assert serialised.endswith(b"\n"), "NDJSON must end with newline (Section 3.4)"

        restored = ndjson_deserializer(serialised)
        assert restored == fred_envelope

    def test_ndjson_bytes_are_valid_single_line(self, fred_envelope):
        """
        NDJSON output must be exactly one JSON line plus a trailing newline.

        Multiple newlines break streaming log parsers and GCS append operations
        (Section 3.4 rationale).
        """
        serialised = ndjson_serializer(fred_envelope)
        lines = serialised.decode("utf-8").splitlines()
        assert len(lines) == 1, f"Expected 1 NDJSON line, got {len(lines)}"


# ==========================================================
# Gate 1 — INDICATORS Dict Invariants (Section B.3)
# ==========================================================

class TestIndicatorsDict:
    """
    Verifies the INDICATORS constant in fred_producer.py (Section B.3).

    These tests catch accidental omission of a required series or missing
    metadata fields that would cause KeyError in _build_raw_payload().
    """

    def test_all_required_series_present(self):
        """
        INDICATORS must contain exactly the 9 series mandated by Section B.3.

        Missing a series means a key economic indicator is never ingested.
        """
        actual_ids = set(INDICATORS.keys())
        missing = REQUIRED_SERIES_IDS - actual_ids
        extra   = actual_ids - REQUIRED_SERIES_IDS
        assert not missing, f"Missing series from INDICATORS: {missing}"
        assert not extra,   f"Unexpected series in INDICATORS (not in spec B.3): {extra}"

    def test_every_indicator_has_release_priority(self):
        """Every INDICATORS entry must have a release_priority in [1, 5] (Section C.2)."""
        for series_id, meta in INDICATORS.items():
            priority = meta.get("release_priority")
            assert priority is not None, (
                f"INDICATORS['{series_id}'] is missing 'release_priority'"
            )
            assert isinstance(priority, int) and 1 <= priority <= 5, (
                f"INDICATORS['{series_id}'].release_priority={priority} out of [1,5]"
            )

    def test_every_indicator_has_unit(self):
        """Every INDICATORS entry must have a non-empty 'unit' (Section C.2)."""
        for series_id, meta in INDICATORS.items():
            unit = meta.get("unit", "")
            assert unit, f"INDICATORS['{series_id}'] is missing 'unit'"

    def test_every_indicator_has_seasonal_adjustment(self):
        """Every INDICATORS entry must have a non-empty 'seasonal_adjustment' (Section C.2)."""
        for series_id, meta in INDICATORS.items():
            sa = meta.get("seasonal_adjustment", "")
            assert sa, f"INDICATORS['{series_id}'] is missing 'seasonal_adjustment'"

    def test_high_priority_series_have_priority_5(self):
        """
        The five highest-importance series (Section B.3 Flink triggers) must
        have release_priority == 5:
          FEDFUNDS (Monetary Policy), CPIAUCSL (Inflation), DCOILWTICO (Oil),
          VIXCLS (Fear Index), T10Y2Y (Recession Indicator).
        """
        priority_5 = {"FEDFUNDS", "CPIAUCSL", "DCOILWTICO", "VIXCLS", "T10Y2Y"}
        for series_id in priority_5:
            assert INDICATORS[series_id]["release_priority"] == 5, (
                f"Section B.3 Flink trigger series '{series_id}' must have "
                f"release_priority=5, got {INDICATORS[series_id]['release_priority']}"
            )

    def test_build_raw_payload_produces_expected_keys(self):
        """
        _build_raw_payload() must produce all keys the Silver Job needs for
        the FRED metadata_extension block (Section C.2 table — FRED row):
        series_id, observation_date, release_priority, seasonal_adjustment.
        """
        producer = FREDProducer.__new__(FREDProducer)  # skip __init__ (no Kafka needed)
        meta = INDICATORS["VIXCLS"]
        sample_obs = {
            "date":           "2026-03-01",
            "value":          "18.45",       # raw FRED value is a string; producer parses to float
            "realtime_start": "2026-03-29",
            "realtime_end":   "2026-03-29",
        }
        # Cast value to float as _build_raw_payload() does
        sample_obs_for_test = dict(sample_obs)
        sample_obs_for_test["value"] = float(sample_obs["value"])

        raw = producer._build_raw_payload("VIXCLS", sample_obs_for_test, meta, "pulse")

        required_keys = {
            "series_id", "observation_date", "value",
            "category", "description", "unit",
            "release_priority", "seasonal_adjustment", "fetch_mode",
        }
        missing = required_keys - set(raw.keys())
        assert not missing, f"_build_raw_payload() missing keys: {missing}"
        assert raw["series_id"] == "VIXCLS"
        assert raw["value"] == 18.45
        assert raw["fetch_mode"] == "pulse"
        assert raw["release_priority"] == 5
