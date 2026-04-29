"""
Gate 1 — Ingestion Gate: Polymarket Bronze Schema Compliance.

Validates that the Polymarket producer's envelope-building logic produces
messages that are fully compliant with the Bronze Schema (Section C.1) and
Message Envelope (Section 3.2) before any message reaches Kafka.

Design decisions:
  - Zero live connections: no Kafka broker, no Polymarket API calls.
    build_bronze_message() is a pure function; it is tested in isolation.
  - Mock payloads loaded from tests/mocks/ (Section 4.4 / Section 9.5).
  - Validators from utils/validators.py are called directly — the same
    validators that the Flink Silver Job uses, so a pass here guarantees
    the Silver Job's first gate will also pass for Polymarket messages.

Gate 1 checklist (Section 9.3):
  [1] envelope fields present and correctly typed
  [2] event_id is a valid UUIDv4
  [3] producer_timestamp is a valid ISO8601 UTC string
  [4] source_name == "polymarket"   (field named source_name in the envelope;
      source_id is the Silver-layer canonical concept — not the Bronze field)
  [5] payload.raw_payload is a non-empty dict
  [6] validate_envelope() passes without errors
  [7] validate_bronze_payload() passes without errors
  [8] whale_alert flag survives round-trip through the envelope unchanged
  [9] mock payload represents a market that passed the $10k volume filter
  [10] each call to build_bronze_message() produces a distinct event_id

References:
    - Section 2.1:  Producer Matrix (Polymarket row)
    - Section 3.2:  Message Envelope
    - Section 3.4:  NDJSON serialization (tested via round-trip)
    - Section 4.4:  Mock-Driven Development
    - Section 9.3:  Triple-Gate Test Matrix — Gate 1
    - Section B.8:  Polymarket parameters (whale threshold, volume filter)
    - Section C.1:  Bronze Schema
"""

import json
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from utils.kafka_utils import build_bronze_message, ndjson_serializer, ndjson_deserializer
from utils.validators import validate_bronze_payload, validate_envelope

# ==========================================================
# Paths
# ==========================================================

MOCKS_DIR = Path(__file__).parent.parent / "mocks"
PRICE_UPDATE_MOCK = MOCKS_DIR / "polymarket_price_update.json"

# Endpoints used by the producer — referenced in envelope source_endpoint
WS_ENDPOINT   = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
REST_ENDPOINT = "https://clob.polymarket.com/markets/0x87ae1d3e2aec7cbf6b7f01781f741c82ae9bd08a"


# ==========================================================
# Fixtures
# ==========================================================

@pytest.fixture(scope="module")
def price_update_raw() -> dict:
    """
    Load the realistic Polymarket price_update mock payload (Section 4.4).

    scope="module" so the file is read once and shared across all tests
    in this module — mock I/O should not be repeated per test.
    """
    with open(PRICE_UPDATE_MOCK, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def price_update_envelope(price_update_raw) -> dict:
    """
    Build the full Bronze envelope from the mock price_update payload.

    Reused by multiple tests — builds once at module scope.
    """
    return build_bronze_message(
        source_name="polymarket",
        source_endpoint=WS_ENDPOINT,
        raw_payload=price_update_raw,
    )


# ==========================================================
# Gate 1 — Envelope Structure Tests
# ==========================================================

class TestEnvelopeStructure:
    """
    Verifies the outer Message Envelope fields (Section 3.2 / Gate 1 checks 1-4).
    """

    def test_envelope_is_dict(self, price_update_envelope):
        """build_bronze_message must return a dict."""
        assert isinstance(price_update_envelope, dict)

    def test_envelope_has_event_id(self, price_update_envelope):
        """event_id must be present and a string (Section 3.2)."""
        assert "event_id" in price_update_envelope
        assert isinstance(price_update_envelope["event_id"], str)

    def test_event_id_is_valid_uuid4(self, price_update_envelope):
        """
        event_id must be a valid UUIDv4 (Section 3.2).

        uuid.UUID() raises ValueError on an invalid string — no need
        for a regex; the stdlib parser is authoritative.
        """
        parsed = uuid.UUID(price_update_envelope["event_id"])
        assert parsed.version == 4

    def test_producer_timestamp_is_present(self, price_update_envelope):
        """producer_timestamp must be present and a string (Section 3.2)."""
        assert "producer_timestamp" in price_update_envelope
        assert isinstance(price_update_envelope["producer_timestamp"], str)

    def test_producer_timestamp_is_iso8601_utc(self, price_update_envelope):
        """
        producer_timestamp must parse as a timezone-aware ISO8601 string (Section 3.2).

        fromisoformat() raises ValueError on invalid input — if it parses,
        we additionally assert tzinfo is present (UTC requirement).
        """
        ts = price_update_envelope["producer_timestamp"]
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None, "producer_timestamp must be timezone-aware"

    def test_source_name_is_polymarket(self, price_update_envelope):
        """
        source_name in the envelope must equal 'polymarket' (Section C.1).

        Note: the Bronze envelope field is source_name. The canonical
        source_id concept lives at the Silver layer — not Bronze.
        """
        assert price_update_envelope.get("source_name") == "polymarket"

    def test_schema_version_is_present(self, price_update_envelope):
        """schema_version must be set — enables forward-compatible schema evolution (Section 3.2)."""
        assert "schema_version" in price_update_envelope
        assert price_update_envelope["schema_version"] != ""

    def test_trace_id_is_valid_uuid(self, price_update_envelope):
        """trace_id must be a valid UUID for distributed tracing (Section 7.2)."""
        trace_id = price_update_envelope.get("trace_id", "")
        parsed = uuid.UUID(trace_id)  # raises ValueError if invalid
        assert parsed.version == 4


# ==========================================================
# Gate 1 — Bronze Payload Tests (Section C.1)
# ==========================================================

class TestBronzePayload:
    """
    Verifies the inner Bronze Schema payload (Section C.1 / Gate 1 checks 5-7).
    """

    def test_payload_key_exists(self, price_update_envelope):
        """envelope['payload'] must exist and be a dict (Section C.1)."""
        assert "payload" in price_update_envelope
        assert isinstance(price_update_envelope["payload"], dict)

    def test_raw_payload_is_non_empty_dict(self, price_update_envelope):
        """
        payload.raw_payload must be a non-empty dict (Section C.1 Gate 1).

        The Bronze layer preserves raw API responses — an empty raw_payload
        would mean the producer captured nothing, which is a pipeline bug.
        """
        raw = price_update_envelope["payload"].get("raw_payload")
        assert isinstance(raw, dict), "raw_payload must be a dict"
        assert len(raw) > 0, "raw_payload must not be empty"

    def test_bronze_payload_has_ingestion_id(self, price_update_envelope):
        """ingestion_id must be present and a valid UUID (Section C.1)."""
        ingestion_id = price_update_envelope["payload"].get("ingestion_id", "")
        parsed = uuid.UUID(ingestion_id)
        assert parsed.version == 4

    def test_bronze_payload_has_source_endpoint(self, price_update_envelope):
        """source_endpoint must be a non-empty string (Section C.1)."""
        endpoint = price_update_envelope["payload"].get("source_endpoint", "")
        assert isinstance(endpoint, str)
        assert len(endpoint) > 0

    def test_bronze_payload_has_ingestion_timestamp(self, price_update_envelope):
        """ingestion_timestamp must be a valid ISO8601 string (Section C.1)."""
        ts = price_update_envelope["payload"].get("ingestion_timestamp", "")
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None

    def test_bronze_payload_metadata_block(self, price_update_envelope):
        """metadata block must have http_status_code (int) and request_duration_ms (int|float)."""
        metadata = price_update_envelope["payload"].get("metadata", {})
        assert isinstance(metadata, dict), "metadata must be a dict"
        assert isinstance(metadata.get("http_status_code"), int)
        assert isinstance(metadata.get("request_duration_ms"), (int, float))


# ==========================================================
# Gate 1 — Validator Integration Tests
# ==========================================================

class TestValidatorIntegration:
    """
    Runs the same validators the Flink Silver Job uses (Section 9.3 Gate 1).

    A pass here means the Silver Job's first gate will accept these messages.
    """

    def test_validate_envelope_passes(self, price_update_envelope):
        """
        validate_envelope() must return is_valid=True (Section 3.2, 9.3).

        Uses the same validator called at the Silver Job entry point —
        failure here would route the message to dead-letter-queue.
        """
        result = validate_envelope(price_update_envelope)
        assert result.is_valid, f"Envelope validation errors: {result.errors}"

    def test_validate_bronze_payload_passes(self, price_update_envelope):
        """
        validate_bronze_payload() on the inner payload must pass (Section C.1, 9.3).
        """
        inner = price_update_envelope["payload"]
        result = validate_bronze_payload(inner)
        assert result.is_valid, f"Bronze payload validation errors: {result.errors}"


# ==========================================================
# Gate 1 — Domain-Specific Correctness Tests
# ==========================================================

class TestDomainCorrectness:
    """
    Verifies Polymarket-specific business rules survive the Bronze round-trip
    (Section B.8, Gate 1 checks 8-10).
    """

    def test_whale_alert_true_preserved(self):
        """
        whale_alert=True in a $100k+ trade must be preserved in raw_payload (Section B.8).

        Why a fresh payload instead of the module fixture: this test needs
        whale_alert=True specifically — the standard mock has whale_alert=False.
        """
        whale_raw = {
            "payload_type":    "price_update",
            "ingestion_mode":  "websocket",
            "event_type":      "last_trade",
            "asset_id":        "71321045679652173895153786947501143655108564919899054916890429347085006392",
            "market_id":       "0x87ae1d3e2aec7cbf6b7f01781f741c82ae9bd08a",
            "price":           0.72,
            "side":            "BUY",
            "size":            180_000.0,
            "trade_value_usd": 129_600.0,  # > $100k threshold (Section B.8)
            "whale_alert":     True,
            "volume_24h_usd":  450_000.0,
            "timestamp":       "2026-03-28T14:30:00+00:00",
        }
        msg = build_bronze_message(
            source_name="polymarket",
            source_endpoint=WS_ENDPOINT,
            raw_payload=whale_raw,
        )
        assert msg["payload"]["raw_payload"]["whale_alert"] is True

    def test_whale_alert_false_preserved(self, price_update_raw, price_update_envelope):
        """whale_alert=False in a normal trade must also be preserved unchanged."""
        assert price_update_raw.get("whale_alert") is False
        assert price_update_envelope["payload"]["raw_payload"]["whale_alert"] is False

    def test_mock_represents_above_min_volume(self, price_update_raw):
        """
        Mock payload must represent a market that passed the $10k volume filter.

        Why: if a test fixture represents filtered-out data, tests pass for
        the wrong inputs. The mock must be realistic. (Section 2.2, B.8)
        """
        volume = float(
            price_update_raw.get("volume_24h_usd")
            or price_update_raw.get("size", 0)
        )
        assert volume > 10_000, (
            f"Mock should represent a >$10k volume market; got {volume}. "
            "Update the mock to use realistic market data."
        )

    def test_payload_type_is_price_update(self, price_update_raw, price_update_envelope):
        """payload_type 'price_update' must be preserved verbatim in raw_payload."""
        assert price_update_raw.get("payload_type") == "price_update"
        assert price_update_envelope["payload"]["raw_payload"]["payload_type"] == "price_update"

    def test_market_id_preserved(self, price_update_raw, price_update_envelope):
        """market_id must pass through the Bronze layer without modification."""
        assert (
            price_update_envelope["payload"]["raw_payload"]["market_id"]
            == price_update_raw["market_id"]
        )

    def test_each_call_produces_unique_event_id(self, price_update_raw):
        """
        Two calls to build_bronze_message() must produce different event_ids.

        Ensures UUIDv4 uniqueness — shared event_ids would corrupt the
        Paper Trail across Bronze → Silver → Gold (Section 3.2).
        """
        msg1 = build_bronze_message("polymarket", WS_ENDPOINT, price_update_raw)
        msg2 = build_bronze_message("polymarket", WS_ENDPOINT, price_update_raw)
        assert msg1["event_id"] != msg2["event_id"]


# ==========================================================
# Gate 1 — NDJSON Round-Trip Test (Section 3.4)
# ==========================================================

class TestNDJSONRoundTrip:
    """
    Verifies the NDJSON serialization mandated by Section 3.4.

    The Kafka wire format is NDJSON bytes. Serialising and deserialising
    the envelope must produce an identical dict — no data loss on the wire.
    """

    def test_ndjson_round_trip_preserves_envelope(self, price_update_envelope):
        """Serialise → deserialise must produce an equal envelope dict."""
        serialised = ndjson_serializer(price_update_envelope)
        assert isinstance(serialised, bytes)
        assert serialised.endswith(b"\n"), "NDJSON must end with newline (Section 3.4)"

        restored = ndjson_deserializer(serialised)
        assert restored == price_update_envelope

    def test_ndjson_bytes_are_valid_single_line(self, price_update_envelope):
        """
        NDJSON output must be exactly one JSON line plus a trailing newline.

        Multiple newlines would break streaming log parsers and GCS append
        operations (Section 3.4 rationale).
        """
        serialised = ndjson_serializer(price_update_envelope)
        lines = serialised.decode("utf-8").splitlines()
        assert len(lines) == 1, f"Expected 1 NDJSON line, got {len(lines)}"
