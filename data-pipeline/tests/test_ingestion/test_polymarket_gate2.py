"""
Gate 2 — Logic Gate: Polymarket Silver Processing Validation.

Validates that the Silver Job's transformation and routing logic is correct
using mock payloads — no live Flink cluster, no Kafka, no API calls.

What Gate 2 covers (Section 9.3 Gate 2):
    [1]  price_update envelope → routes to SILVER_STRUCTURED_METRICS
    [2]  comment envelope      → routes to SILVER_SOCIAL_PULSE
    [3]  invalid envelope      → routes to DEAD_LETTER_QUEUE
    [4]  unknown payload_type  → routes to DEAD_LETTER_QUEUE
    [5]  empty comment batch   → (None, None) intentional skip
    [6]  Silver Structured Metric output passes validate_silver_structured_metric()
    [7]  Silver Social output passes validate_silver_social()
    [8]  momentum_block stubs are 0.0 (Gold Job computes deltas via keyed state)
    [9]  canonical_event_id in Silver record matches the Bronze envelope event_id
    [10] whale_alert flag is preserved through the Silver transform
    [11] content_hash is a valid 64-char SHA-256 hex string
    [12] bronze_ref in comment Silver record links back to Bronze event_id
    [13] hash_social_batch() is deterministic regardless of comment list order
    [14] hash_document() produces a 64-char hex string (Phase 3 readiness)
    [15] distinct content produces distinct hashes (collision sanity check)

Test strategy:
    Fixtures build full Bronze envelopes by calling build_bronze_message()
    (the same function the producer uses), then pass them directly to
    process_polymarket_message() — the pure dispatch function in silver_job.py.
    No Flink, no Kafka, no network I/O at any point.

References:
    - Section 4.1:   Silver Job specification
    - Section 4.1C:  SHA-256 deduplication
    - Section 4.2B:  Gold Job keyed state (explains why momentum stubs are 0.0)
    - Section 4.4:   Mock-Driven Development
    - Section 9.3:   Triple-Gate Test Matrix — Gate 2
    - Section C.2:   Silver Structured Metric schema
    - Section C.3:   Silver Social schema — Polymarket pattern
    - Section 3.5:   Dead-Letter Queue — never silently drop
"""

import copy
import json
from pathlib import Path

import pytest

from config.kafka_topics import (
    DEAD_LETTER_QUEUE,
    SILVER_SOCIAL_PULSE,
    SILVER_STRUCTURED_METRICS,
)
from processing.deduplication import hash_document, hash_social_batch, sha256_hash
from processing.silver_job import (
    map_comment_to_silver,
    map_price_update_to_silver,
    process_polymarket_message,
)
from utils.kafka_utils import build_bronze_message
from utils.validators import validate_silver_social, validate_silver_structured_metric

# ==========================================================
# Paths
# ==========================================================

MOCKS_DIR = Path(__file__).parent.parent / "mocks"
WS_ENDPOINT = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


# ==========================================================
# Fixtures
# ==========================================================

@pytest.fixture(scope="module")
def price_update_raw() -> dict:
    """Load the Polymarket price_update mock raw payload (Section 4.4)."""
    with open(MOCKS_DIR / "polymarket_price_update.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def comment_raw() -> dict:
    """Load the Polymarket comment batch mock raw payload (Section 4.4)."""
    with open(MOCKS_DIR / "polymarket_comment.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def price_update_envelope(price_update_raw) -> dict:
    """Full Bronze envelope wrapping the price_update mock — as the Silver Job receives it."""
    return build_bronze_message(
        source_name="polymarket",
        source_endpoint=WS_ENDPOINT,
        raw_payload=price_update_raw,
    )


@pytest.fixture(scope="module")
def comment_envelope(comment_raw) -> dict:
    """Full Bronze envelope wrapping the comment batch mock."""
    return build_bronze_message(
        source_name="polymarket",
        source_endpoint="https://gamma-api.polymarket.com/comments?market=0x87ae1d...",
        raw_payload=comment_raw,
    )


# ==========================================================
# Gate 2.1 — Routing Tests
# ==========================================================

class TestRouting:
    """
    Verifies process_polymarket_message() sends each payload type to the
    correct Silver topic (Section 4.1 / Gate 2 checks 1-5).
    """

    def test_price_update_routes_to_structured_metrics(self, price_update_envelope):
        """price_update payload must route to SILVER_STRUCTURED_METRICS."""
        topic, record = process_polymarket_message(price_update_envelope)
        assert topic == SILVER_STRUCTURED_METRICS, (
            f"Expected {SILVER_STRUCTURED_METRICS!r}, got {topic!r}"
        )
        assert record is not None

    def test_comment_routes_to_social_pulse(self, comment_envelope):
        """comment payload must route to SILVER_SOCIAL_PULSE."""
        topic, record = process_polymarket_message(comment_envelope)
        assert topic == SILVER_SOCIAL_PULSE, (
            f"Expected {SILVER_SOCIAL_PULSE!r}, got {topic!r}"
        )
        assert record is not None

    def test_invalid_envelope_routes_to_dlq(self):
        """Envelope missing required fields must route to DEAD_LETTER_QUEUE."""
        bad_envelope = {"event_id": "not-a-uuid", "source_name": "polymarket"}
        topic, record = process_polymarket_message(bad_envelope)
        assert topic == DEAD_LETTER_QUEUE
        assert "validation_errors" in record

    def test_unknown_payload_type_routes_to_dlq(self):
        """Unknown payload_type must route to DLQ — never silently dropped (Section 3.5)."""
        raw = {"payload_type": "unknown_type", "market_id": "0xabc"}
        envelope = build_bronze_message("polymarket", WS_ENDPOINT, raw)
        topic, record = process_polymarket_message(envelope)
        assert topic == DEAD_LETTER_QUEUE
        assert any("Unknown payload_type" in e for e in record["validation_errors"])

    def test_empty_comment_batch_returns_none(self):
        """An empty raw_comments list must return (None, None) — intentional skip."""
        raw = {
            "payload_type": "comment",
            "market_id":    "0x87ae1d3e2aec7cbf6b7f01781f741c82ae9bd08a",
            "question":     "Will X happen?",
            "raw_comments": [],
            "fetched_at":   "2026-03-28T14:22:00+00:00",
        }
        envelope = build_bronze_message("polymarket", WS_ENDPOINT, raw)
        topic, record = process_polymarket_message(envelope)
        assert topic is None
        assert record is None

    def test_missing_bronze_payload_routes_to_dlq(self):
        """Bronze envelope whose payload fails validate_bronze_payload must hit DLQ."""
        # Build a valid-looking envelope but corrupt the inner payload
        envelope = build_bronze_message("polymarket", WS_ENDPOINT, {"payload_type": "price_update"})
        # Remove the ingestion_id from the inner payload to force Bronze validation failure
        del envelope["payload"]["ingestion_id"]
        topic, record = process_polymarket_message(envelope)
        assert topic == DEAD_LETTER_QUEUE


# ==========================================================
# Gate 2.2 — Silver Schema Compliance
# ==========================================================

class TestSilverSchemaCompliance:
    """
    Runs the Silver validators on the outputs of both transform functions.
    A pass here is identical to what the Flink Silver Job verifies before
    emitting to the Silver Kafka topic (Section 9.3 Gate 2 checks 6-7).
    """

    def test_price_update_output_passes_silver_validator(self, price_update_envelope):
        """validate_silver_structured_metric() must pass on the price_update output."""
        _, record = process_polymarket_message(price_update_envelope)
        result = validate_silver_structured_metric(record)
        assert result.is_valid, f"Silver structured metric validation errors: {result.errors}"

    def test_comment_output_passes_silver_validator(self, comment_envelope):
        """validate_silver_social() must pass on the comment output."""
        _, record = process_polymarket_message(comment_envelope)
        result = validate_silver_social(record)
        assert result.is_valid, f"Silver social validation errors: {result.errors}"

    def test_price_update_layer_is_silver(self, price_update_envelope):
        """Silver Structured Metric must have layer='Silver' (Section C.2)."""
        _, record = process_polymarket_message(price_update_envelope)
        assert record["layer"] == "Silver"

    def test_price_update_entity_type(self, price_update_envelope):
        """Silver Structured Metric must have entity_type='Structured_Metric' (Section C.2)."""
        _, record = process_polymarket_message(price_update_envelope)
        assert record["entity_type"] == "Structured_Metric"

    def test_comment_source_name_is_polymarket(self, comment_envelope):
        """Silver Social record source_name must be 'polymarket' (Section C.3)."""
        _, record = process_polymarket_message(comment_envelope)
        assert record["source_name"] == "polymarket"

    def test_comment_raw_comments_is_list(self, comment_envelope):
        """Silver Social record raw_comments must be a list (Section C.3)."""
        _, record = process_polymarket_message(comment_envelope)
        assert isinstance(record["raw_comments"], list)
        assert len(record["raw_comments"]) > 0


# ==========================================================
# Gate 2.3 — Transform Correctness
# ==========================================================

class TestTransformCorrectness:
    """
    Verifies that field values are mapped correctly by the transform functions.
    These tests pin the exact field-level contract between Silver Job and the
    Gold Job / persistence layer (Section 4.1 / Gate 2 checks 8-12).
    """

    def test_momentum_block_stubs_are_zero(self, price_update_envelope):
        """
        momentum_block deltas must all be 0.0 at Silver layer.

        Why: change_24h/7d/30d require keyed state across multiple price
        observations. The Silver Job stubs them; the Gold Job computes real
        values via Flink keyed state (Section 4.2B).
        """
        _, record = process_polymarket_message(price_update_envelope)
        mb = record["momentum_block"]
        assert mb["change_24h"]  == 0.0
        assert mb["change_7d"]   == 0.0
        assert mb["change_30d"]  == 0.0
        assert mb["is_new_market"] is False

    def test_canonical_event_id_matches_envelope(self, price_update_envelope):
        """
        core_identity.canonical_event_id must equal the Bronze envelope's event_id.

        This is the Paper Trail field that links Bronze → Silver → Gold for
        the same logical event (Section 3.2).
        """
        _, record = process_polymarket_message(price_update_envelope)
        assert (
            record["core_identity"]["canonical_event_id"]
            == price_update_envelope["event_id"]
        )

    def test_whale_alert_preserved_in_silver(self):
        """whale_alert=True from a large trade must survive into Silver metadata_extension."""
        whale_raw = {
            "payload_type":    "price_update",
            "ingestion_mode":  "websocket",
            "event_type":      "last_trade",
            "asset_id":        "71321045679652173895153786947501143655108564919899054916890429347085006392",
            "market_id":       "0x87ae1d3e2aec7cbf6b7f01781f741c82ae9bd08a",
            "price":           0.72,
            "side":            "BUY",
            "size":            180_000.0,
            "trade_value_usd": 129_600.0,
            "whale_alert":     True,
            "volume_24h_usd":  450_000.0,
            "timestamp":       "2026-03-28T14:30:00+00:00",
        }
        envelope = build_bronze_message("polymarket", WS_ENDPOINT, whale_raw)
        _, record = process_polymarket_message(envelope)
        assert record["metadata_extension"]["whale_alert"] is True

    def test_whale_alert_false_preserved(self, price_update_envelope):
        """whale_alert=False must also be preserved correctly."""
        _, record = process_polymarket_message(price_update_envelope)
        assert record["metadata_extension"]["whale_alert"] is False

    def test_price_value_is_float(self, price_update_envelope):
        """current_value in data_point must be a float (Section C.2)."""
        _, record = process_polymarket_message(price_update_envelope)
        assert isinstance(record["data_point"]["current_value"], float)

    def test_price_unit_is_usd(self, price_update_envelope):
        """data_point.unit must be 'USD' for Polymarket market prices."""
        _, record = process_polymarket_message(price_update_envelope)
        assert record["data_point"]["unit"] == "USD"

    def test_comment_bronze_ref_matches_envelope(self, comment_envelope):
        """
        bronze_ref in the Silver Social record must equal the Bronze event_id.

        Enables the persistence layer to bridge back to the raw Bronze record
        for replay or audit without storing the full payload twice (Section 3.2).
        """
        _, record = process_polymarket_message(comment_envelope)
        assert record["bronze_ref"] == comment_envelope["event_id"]

    def test_comment_market_id_preserved(self, comment_raw, comment_envelope):
        """market_id must be propagated unchanged from raw_payload to Silver record."""
        _, record = process_polymarket_message(comment_envelope)
        assert record["market_id"] == comment_raw["market_id"]

    def test_source_name_is_polymarket_in_structured_metric(self, price_update_envelope):
        """core_identity.source_name must be 'polymarket' (Section C.2)."""
        _, record = process_polymarket_message(price_update_envelope)
        assert record["core_identity"]["source_name"] == "polymarket"


# ==========================================================
# Gate 2.3.5 — Sprint 22 T22.1 D1: question propagation
# ==========================================================

class TestQuestionPropagationSprint22:
    """
    Sprint 22 T22.1 (D1 prerequisite): map_price_update_to_silver must
    propagate the `question` field from raw payload to metadata_extension.

    Why this matters:
        Without propagation, momentum_vault rows have no question text
        attached, and persistence.momentum_vault.find_polymarket_market_by_question
        (the resolver added in T22.1) has nothing to fuzzy-match against.
        Verified against live dev DB on 2026-05-23 — pre-patch rows had
        no `question` key in metadata_extension (six keys total). Patch
        makes it seven, REST-snapshot rows carry the question.

    Why two distinct fixtures (REST + WebSocket):
        REST snapshots include `question` in their raw payload
        (polymarket_producer.py:208); WebSocket `last_trade` events do
        NOT — they only carry asset_id + price + size. The patch must
        not crash on the WebSocket path and must store "" there so the
        resolver's `metadata_extension ? 'question'` JSONB key-exists
        filter correctly distinguishes "no question text" from "missing
        key".
    """

    def test_rest_snapshot_question_propagates_to_metadata_extension(self):
        """
        REST-snapshot raw payload with `question` must surface that
        text at metadata_extension.question in the Silver record.
        """
        rest_snapshot_raw = {
            "payload_type":    "price_update",
            "ingestion_mode":  "rest_snapshot",
            "market_id":       "0x87ae1d3e2aec7cbf6b7f01781f741c82ae9bd08a",
            "condition_id":    "0x87ae1d3e2aec7cbf6b7f01781f741c82ae9bd08a",
            "question":        "Will the Federal Reserve cut rates before June 2026?",
            "tokens":          [],
            "volume_24h_usd":  285_400.0,
            "liquidity_usd":   128_900.0,
            "whale_alert":     False,
            "fetched_at":      "2026-05-23T10:00:00+00:00",
        }
        envelope = build_bronze_message("polymarket", WS_ENDPOINT, rest_snapshot_raw)
        topic, record = process_polymarket_message(envelope)
        assert topic == SILVER_STRUCTURED_METRICS
        assert record["metadata_extension"]["question"] == (
            "Will the Federal Reserve cut rates before June 2026?"
        )

    def test_websocket_event_question_defaults_to_empty_string(self, price_update_envelope):
        """
        WebSocket `last_trade` raw payloads don't carry `question` — the
        Silver record must still contain the key (empty string) so the
        downstream record shape is uniform across REST and WebSocket
        paths. The resolver's JSONB key-exists filter treats "" as
        present-but-empty (which similarity() returns 0.0 for and gets
        excluded by the threshold).
        """
        _, record = process_polymarket_message(price_update_envelope)
        assert "question" in record["metadata_extension"]
        assert record["metadata_extension"]["question"] == ""

    def test_question_propagation_does_not_break_validator(self):
        """
        The patch must not break Silver schema validation — the new
        `question` key is permitted in metadata_extension by the existing
        validator (no schema change required).
        """
        rest_snapshot_raw = {
            "payload_type":    "price_update",
            "ingestion_mode":  "rest_snapshot",
            "market_id":       "0x87ae1d3e2aec7cbf6b7f01781f741c82ae9bd08a",
            "condition_id":    "0x87ae1d3e2aec7cbf6b7f01781f741c82ae9bd08a",
            "question":        "Will BTC reach $150K by year-end 2026?",
            "tokens":          [],
            "volume_24h_usd":  50_000.0,
            "liquidity_usd":   25_000.0,
            "whale_alert":     False,
            "fetched_at":      "2026-05-23T10:00:00+00:00",
        }
        envelope = build_bronze_message("polymarket", WS_ENDPOINT, rest_snapshot_raw)
        _, record = process_polymarket_message(envelope)
        result = validate_silver_structured_metric(record)
        assert result.is_valid, (
            f"Silver validation must pass with the new question field: {result.errors}"
        )


# ==========================================================
# Gate 2.4 — DLQ Record Structure
# ==========================================================

class TestDLQRecordStructure:
    """
    Verifies that DLQ records contain the required fields for operator
    inspection and replay (Section 3.5).
    """

    def test_dlq_record_has_required_fields(self):
        """DLQ record must contain dlq_id, failed_layer, validation_errors, original_message."""
        bad_envelope = {"event_id": "bad"}
        topic, record = process_polymarket_message(bad_envelope)
        assert topic == DEAD_LETTER_QUEUE
        for field in ("dlq_id", "failed_at", "failed_layer", "failed_stage",
                      "source_topic", "validation_errors", "original_message"):
            assert field in record, f"DLQ record missing field: '{field}'"

    def test_dlq_original_message_preserved(self):
        """DLQ record must preserve the original envelope intact for replay."""
        bad_envelope = {"event_id": "bad", "trace_id": "x"}
        _, record = process_polymarket_message(bad_envelope)
        assert record["original_message"] == bad_envelope

    def test_dlq_failed_layer_is_silver(self):
        """DLQ records from the Silver Job must have failed_layer='Silver' (Section 3.5)."""
        bad_envelope = {"event_id": "bad"}
        _, record = process_polymarket_message(bad_envelope)
        assert record["failed_layer"] == "Silver"


# ==========================================================
# Gate 2.5 — Deduplication Tests (Section 4.1C)
# ==========================================================

class TestDeduplication:
    """
    Validates the SHA-256 deduplication logic in processing/deduplication.py.
    """

    def test_hash_social_batch_is_64_char_hex(self, comment_raw):
        """hash_social_batch must return a 64-character hex string."""
        h = hash_social_batch(
            comment_raw["market_id"],
            comment_raw["raw_comments"],
        )
        assert isinstance(h, str)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_social_batch_is_deterministic(self, comment_raw):
        """Same input must always produce the same hash."""
        comments = comment_raw["raw_comments"]
        market_id = comment_raw["market_id"]
        h1 = hash_social_batch(market_id, comments)
        h2 = hash_social_batch(market_id, comments)
        assert h1 == h2

    def test_hash_social_batch_order_independent(self, comment_raw):
        """
        Comment list order must not affect the hash.

        Why: Polymarket REST API does not guarantee comment ordering.
        A reversed list is the same logical batch — must hash identically.
        """
        comments = comment_raw["raw_comments"]
        market_id = comment_raw["market_id"]
        h_forward  = hash_social_batch(market_id, comments)
        h_reversed = hash_social_batch(market_id, list(reversed(comments)))
        assert h_forward == h_reversed

    def test_hash_social_batch_differs_on_new_comment(self, comment_raw):
        """Adding a new comment to the batch must produce a different hash."""
        comments  = comment_raw["raw_comments"]
        market_id = comment_raw["market_id"]
        h_original = hash_social_batch(market_id, comments)

        new_comment = {
            "comment_id": "cm_new",
            "author":     "newcomer",
            "text":       "Just joined the market.",
            "timestamp":  "2026-03-28T15:00:00+00:00",
            "upvotes":    1,
        }
        h_extended = hash_social_batch(market_id, comments + [new_comment])
        assert h_original != h_extended

    def test_hash_social_batch_differs_across_markets(self, comment_raw):
        """Same comments under a different market_id must produce a different hash."""
        comments = comment_raw["raw_comments"]
        h1 = hash_social_batch("0xmarket_a", comments)
        h2 = hash_social_batch("0xmarket_b", comments)
        assert h1 != h2

    def test_content_hash_present_in_silver_comment(self, comment_envelope):
        """Silver Social record must contain a content_hash field (Section 4.1C)."""
        _, record = process_polymarket_message(comment_envelope)
        assert "content_hash" in record
        assert len(record["content_hash"]) == 64

    def test_content_hash_matches_direct_call(self, comment_raw, comment_envelope):
        """
        content_hash in the Silver record must equal the direct hash_social_batch()
        result — confirms silver_job.py is calling deduplication.py correctly.
        """
        _, record = process_polymarket_message(comment_envelope)
        expected = hash_social_batch(
            comment_raw["market_id"],
            comment_raw["raw_comments"],
        )
        assert record["content_hash"] == expected

    def test_hash_document_returns_64_char_hex(self):
        """hash_document must return a 64-char SHA-256 hex string (Phase 3 readiness)."""
        h = hash_document("Some article text.", "https://reuters.com/article/123")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_document_normalises_whitespace(self):
        """
        hash_document must treat extra whitespace as insignificant.

        Why: APIs and scrapers sometimes return the same article with
        different internal spacing. Normalised hashing prevents these
        variants from entering the Knowledge Vault twice (Section 4.1C).
        """
        h1 = hash_document("Hello   world.", "https://example.com")
        h2 = hash_document("Hello world.",   "https://example.com")
        assert h1 == h2

    def test_distinct_content_produces_distinct_hashes(self):
        """Two different texts must produce different SHA-256 hashes."""
        h1 = sha256_hash("content_a")
        h2 = sha256_hash("content_b")
        assert h1 != h2

    def test_sha256_hash_dict_is_key_order_independent(self):
        """
        sha256_hash on a dict must be identical regardless of key insertion order.

        Why: Python dicts preserve insertion order since 3.7 but JSON serialisation
        with sort_keys=True makes the hash independent of how the dict was built.
        """
        d1 = {"b": 2, "a": 1}
        d2 = {"a": 1, "b": 2}
        assert sha256_hash(d1) == sha256_hash(d2)


# ==========================================================
# Gate 2.6 — Non-trade WebSocket Event Filtering (Gap 3, Sprint 11)
# ==========================================================

class TestNonTradeEventFiltering:
    """
    Validates that non-last_trade WebSocket price_update events are intentionally
    skipped by process_polymarket_message() rather than producing 0.0 current_value
    records that would corrupt Flink keyed state and momentum_vault (Section B.8).

    Gate 2 check [16]: price_change WebSocket event → (None, None) skip
    Gate 2 check [17]: book WebSocket event → (None, None) skip
    Gate 2 check [18]: last_trade WebSocket event → SILVER_STRUCTURED_METRICS (passes)
    Gate 2 check [19]: REST snapshot (no event_type) → SILVER_STRUCTURED_METRICS (passes)
    """

    def _make_ws_envelope(self, event_type: str) -> dict:
        """Build a minimal WebSocket price_update envelope for the given event_type."""
        raw = {
            "payload_type":   "price_update",
            "ingestion_mode": "websocket",
            "event_type":     event_type,
            "asset_id":       "71321045679652173895153786947501143655108564919899054916890429347085006392",
            "market_id":      "0x87ae1d3e2aec7cbf6b7f01781f741c82ae9bd08a",
            "price":          0.0,   # 0.0 is the symptom for non-trade events
            "side":           "",
            "size":           0.0,
            "trade_value_usd": None,
            "whale_alert":    False,
            "timestamp":      "2026-03-28T14:22:31.412000+00:00",
        }
        return build_bronze_message("polymarket", WS_ENDPOINT, raw)

    def test_price_change_websocket_event_is_skipped(self):
        """
        price_change WebSocket events must return (None, None) — intentional skip,
        not a DLQ error. These events carry bid/ask order-book changes, not executed
        trade prices, so routing them to Silver would produce current_value=0.0 and
        corrupt Flink momentum calculations (Gap 3 fix, Sprint 11, Section B.8).
        """
        envelope = self._make_ws_envelope("price_change")
        topic, record = process_polymarket_message(envelope)
        assert topic is None, (
            f"price_change event must be skipped (None), got topic={topic!r}"
        )
        assert record is None

    def test_book_websocket_event_is_skipped(self):
        """
        book WebSocket events must return (None, None) — intentional skip.
        Full order-book snapshots have no price field at all; allowing them
        through would always produce current_value=0.0 (Gap 3 fix, Sprint 11).
        """
        envelope = self._make_ws_envelope("book")
        topic, record = process_polymarket_message(envelope)
        assert topic is None, (
            f"book event must be skipped (None), got topic={topic!r}"
        )
        assert record is None

    def test_last_trade_websocket_event_routes_to_structured_metrics(self):
        """
        last_trade WebSocket events must route normally to SILVER_STRUCTURED_METRICS.
        These are the only WebSocket events carrying meaningful execution prices.
        """
        raw = {
            "payload_type":   "price_update",
            "ingestion_mode": "websocket",
            "event_type":     "last_trade",
            "asset_id":       "71321045679652173895153786947501143655108564919899054916890429347085006392",
            "market_id":      "0x87ae1d3e2aec7cbf6b7f01781f741c82ae9bd08a",
            "price":          0.67,
            "side":           "BUY",
            "size":           5000.0,
            "trade_value_usd": 3350.0,
            "whale_alert":    False,
            "timestamp":      "2026-03-28T14:22:31.412000+00:00",
        }
        envelope = build_bronze_message("polymarket", WS_ENDPOINT, raw)
        topic, record = process_polymarket_message(envelope)
        assert topic == SILVER_STRUCTURED_METRICS, (
            f"last_trade must route to SILVER_STRUCTURED_METRICS, got {topic!r}"
        )
        assert record is not None
        assert record["data_point"]["current_value"] == 0.67

    def test_rest_snapshot_routes_to_structured_metrics(self):
        """
        REST snapshots (ingestion_mode='rest_snapshot', no event_type) must pass
        through to SILVER_STRUCTURED_METRICS unchanged — the non-trade filter
        applies to WebSocket events only.
        """
        raw = {
            "payload_type":   "price_update",
            "ingestion_mode": "rest_snapshot",
            "market_id":      "0x87ae1d3e2aec7cbf6b7f01781f741c82ae9bd08a",
            "condition_id":   "0x1234",
            "question":       "Will X happen?",
            "tokens":         [],
            "volume_24h_usd": 50000.0,
            "liquidity_usd":  120000.0,
            "end_date":       "2026-06-30T23:59:00+00:00",
            "whale_alert":    False,
            "fetched_at":     "2026-03-28T14:22:00+00:00",
        }
        envelope = build_bronze_message("polymarket", WS_ENDPOINT, raw)
        topic, record = process_polymarket_message(envelope)
        assert topic == SILVER_STRUCTURED_METRICS, (
            f"REST snapshot must route to SILVER_STRUCTURED_METRICS, got {topic!r}"
        )
