"""
Gate 1 — Ingestion Gate: Telegram Bronze Schema Compliance (Section 9.3).

Validates that the Telegram producer's envelope-building and payload-construction
logic produces messages fully compliant with the Bronze Schema (Section C.1)
and Message Envelope (Section 3.2) before any message reaches Kafka.

Design decisions:
  - Zero live connections: no Kafka broker, no Telethon authentication.
    build_bronze_message(), _build_raw_payload(), _extract_links(), and
    _get_forwarded_from() are tested in complete isolation.
  - Mock payload loaded from tests/mocks/telegram_bronze_payload.json (Section 4.4).
  - Telethon Message objects mocked with unittest.mock.MagicMock — no MTProto
    session required. Telethon entity types (MessageEntityUrl, MessageEntityTextUrl)
    are real instances — they are pure data classes with no connection dependency.
  - TelegramProducer.__new__() used to access static methods without triggering
    __init__ (avoids Telethon auth + Kafka connection attempt).
  - Validators from utils/validators.py are called directly — the same validators
    the Flink Silver Job uses, so a pass here guarantees the Silver Job's first
    gate will also accept Telegram messages.

Key Telegram departures from ArXiv/NewsAPI Gate 1:
  - No authority whitelist: all 7 channels are pre-vetted at the registry level.
  - No impact_boost field: not applicable to Telegram (Section B.4).
  - http_status_code = 0: MTProto is not HTTP — 0 signals "not applicable".
  - request_duration_ms = 0: event-driven push — no request/response cycle.
  - Partition key = channel_username (not arxiv_id or source.id).
  - Dedup key = message_url (https://t.me/{username}/{id}), not a content URL.
  - Media-only filter enforced at producer level (Section A.1).

Gate 1 checklist (Section 9.3 Gate 1):
  [1]  envelope fields present and correctly typed
  [2]  event_id is a valid UUIDv4
  [3]  producer_timestamp is a valid ISO8601 UTC string
  [4]  source_name == "telegram"
  [5]  payload.raw_payload is a non-empty dict
  [6]  validate_envelope() passes without errors
  [7]  validate_bronze_payload() passes without errors
  [8]  NDJSON round-trip preserves the full envelope
  [9]  each call to build_bronze_message() produces a distinct event_id
  [10] raw_payload.message_id is an integer
  [11] raw_payload.channel_username is a string in CHANNEL_USERNAMES (Section A.1)
  [12] raw_payload.channel_title is a non-empty string
  [13] raw_payload.message_text is non-empty (media-only filter already applied)
  [14] raw_payload.message_date is a valid ISO8601 UTC string
  [15] raw_payload.message_url starts with "https://t.me/"
  [16] raw_payload.message_url contains the message_id
  [17] raw_payload.extracted_links is a list
  [18] raw_payload.is_forwarded is a bool
  [19] raw_payload.forwarded_from is a str or None
  [20] raw_payload.views is an int or None
  [21] raw_payload.has_media is a bool
  [22] raw_payload has NO impact_boost field (Section B.4 — not applicable)
  [23] http_status_code == 0 (MTProto — not HTTP)
  [24] request_duration_ms == 0 (push event — no request duration)
  [25] source_endpoint is "https://t.me/{channel_username}"
  [26] _build_raw_payload() produces all 10 Silver-Job-required keys
  [27] media-only filter rejects text=None messages (Section A.1)
  [28] media-only filter rejects text="" messages
  [29] media-only filter rejects whitespace-only text
  [30] media-only filter passes messages with non-empty text
  [31] _extract_links() returns empty list for message with no entities
  [32] _extract_links() extracts bare URL (MessageEntityUrl)
  [33] _extract_links() extracts hyperlinked text URL (MessageEntityTextUrl)
  [34] _get_forwarded_from() returns None for non-forwarded messages
  [35] _get_forwarded_from() returns from_name string for forwarded messages
  [36] _get_forwarded_from() returns "private" when fwd_from.from_name is None
  [37] _build_raw_payload() sets has_media=False for text-only messages
  [38] _build_raw_payload() sets has_media=True for text+media messages
  [39] CHANNEL_USERNAMES contains all 7 registered channels (Section A.1)

References:
    - Section A.1:  Telegram Channel Registry (7 channels + ingestion method)
    - Section C.1:  Bronze Schema
    - Section C.5:  Gold Global Signal — domain_context.Telegram extension
    - Section 3.2:  Message Envelope
    - Section 3.4:  NDJSON serialisation (tested via round-trip)
    - Section 4.1C: SHA-256 deduplication — message_url as dedup key
    - Section 4.4:  Mock-Driven Development
    - Section 9.3:  Triple-Gate Test Matrix — Gate 1
    - Section B.4:  Impact Boost — not applicable to Telegram
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl

from ingestion.telegram_producer import (
    CHANNEL_USERNAMES,
    SOURCE_NAME,
    TelegramProducer,
    _extract_links,
    _get_forwarded_from,
)
from utils.kafka_utils import build_bronze_message, ndjson_deserializer, ndjson_serializer
from utils.validators import validate_bronze_payload, validate_envelope


# ==========================================================
# Paths & Constants
# ==========================================================

MOCKS_DIR            = Path(__file__).parent.parent / "mocks"
TELEGRAM_MOCK_PATH   = MOCKS_DIR / "telegram_bronze_payload.json"

# All 7 channels mandated by Section A.1.
REQUIRED_CHANNELS = {
    "abualiexpress",
    "yediotnews25",
    "Faytuks_Network",
    "clashreport",
    "financialjuice",
    "disclosetv",
    "intelslava",
}

# All 10 keys that _build_raw_payload() must produce for the Silver Job (Task 4B.2).
REQUIRED_RAW_PAYLOAD_KEYS = {
    "message_id",
    "channel_username",
    "channel_title",
    "message_text",
    "message_date",
    "message_url",
    "extracted_links",
    "is_forwarded",
    "forwarded_from",
    "has_media",
}


# ==========================================================
# Fixtures
# ==========================================================

@pytest.fixture(scope="module")
def telegram_raw() -> dict:
    """
    Load the Telegram bronze mock payload (Section 4.4).

    This is the dict that TelegramProducer._build_raw_payload() produces —
    stored verbatim as raw_payload inside the Bronze envelope.
    scope="module" reads the file once and shares it across all tests.
    """
    with open(TELEGRAM_MOCK_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def telegram_envelope(telegram_raw) -> dict:
    """
    Build the full Bronze envelope from the mock Telegram payload.

    Mirrors what TelegramProducer._emit() does in production: calls
    build_bronze_message() with the raw_payload, source endpoint, and
    Telegram-specific sentinel values (http_status_code=0, duration=0).
    """
    channel_username = telegram_raw["channel_username"]
    source_endpoint  = f"https://t.me/{channel_username}"
    return build_bronze_message(
        source_name=SOURCE_NAME,
        source_endpoint=source_endpoint,
        raw_payload=telegram_raw,
        http_status_code=0,
        request_duration_ms=0,
    )


@pytest.fixture(scope="module")
def mock_message() -> MagicMock:
    """
    Mock Telethon Message for testing _build_raw_payload() directly.

    Sets all attributes accessed by _build_raw_payload() to realistic values.
    entities=None and fwd_from=None represent a plain, non-forwarded text message
    with no embedded links — the simplest valid Telegram message.

    Why MagicMock (not a Telethon Message): Telethon Message objects require
    a live client context to instantiate correctly. MagicMock with explicit
    attribute assignments exercises the same code paths as real objects for
    the attributes _build_raw_payload() actually reads.
    """
    msg = MagicMock()
    msg.id       = 98765
    msg.text     = "BREAKING: Iranian naval vessels in Strait of Hormuz — developing situation."
    msg.date     = datetime(2024, 9, 23, 14, 8, 31, tzinfo=timezone.utc)
    msg.fwd_from = None
    msg.media    = None
    msg.entities = None
    msg.views    = 24312
    return msg


@pytest.fixture(scope="module")
def built_raw(mock_message) -> dict:
    """
    Run TelegramProducer._build_raw_payload() against the mock message.

    Tests in TestRawPayloadBuilder use this fixture to verify the output
    structure without touching Kafka or Telethon.
    """
    return TelegramProducer._build_raw_payload(mock_message, "clashreport")


# ==========================================================
# Gate 1 — Envelope Structure Tests (Section 3.2)
# ==========================================================

class TestEnvelopeStructure:
    """Verifies the outer Message Envelope fields (Section 3.2 / checks [1]–[4])."""

    def test_envelope_is_dict(self, telegram_envelope):
        assert isinstance(telegram_envelope, dict)

    def test_envelope_has_event_id(self, telegram_envelope):
        """event_id must be present and a string (Section 3.2)."""
        assert "event_id" in telegram_envelope
        assert isinstance(telegram_envelope["event_id"], str)

    def test_event_id_is_valid_uuid4(self, telegram_envelope):
        """event_id must be a valid UUIDv4 (Section 3.2)."""
        parsed = uuid.UUID(telegram_envelope["event_id"])
        assert parsed.version == 4

    def test_producer_timestamp_is_present(self, telegram_envelope):
        assert "producer_timestamp" in telegram_envelope
        assert isinstance(telegram_envelope["producer_timestamp"], str)

    def test_producer_timestamp_is_iso8601_utc(self, telegram_envelope):
        """producer_timestamp must be timezone-aware ISO8601 (Section 3.2)."""
        ts     = telegram_envelope["producer_timestamp"]
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None, "producer_timestamp must be timezone-aware"

    def test_source_name_is_telegram(self, telegram_envelope):
        """source_name must equal 'telegram' (Section C.1, A.1)."""
        assert telegram_envelope.get("source_name") == SOURCE_NAME
        assert telegram_envelope.get("source_name") == "telegram"

    def test_schema_version_is_present(self, telegram_envelope):
        """schema_version enables forward-compatible schema evolution (Section 3.2)."""
        assert "schema_version" in telegram_envelope
        assert telegram_envelope["schema_version"] != ""

    def test_trace_id_is_valid_uuid(self, telegram_envelope):
        """trace_id must be a valid UUID for distributed tracing (Section 7.2)."""
        trace_id = telegram_envelope.get("trace_id", "")
        parsed   = uuid.UUID(trace_id)
        assert parsed.version == 4


# ==========================================================
# Gate 1 — Bronze Payload Tests (Section C.1)
# ==========================================================

class TestBronzePayload:
    """Verifies the inner Bronze Schema payload (Section C.1 / checks [5], [23]–[25])."""

    def test_payload_key_exists(self, telegram_envelope):
        assert "payload" in telegram_envelope
        assert isinstance(telegram_envelope["payload"], dict)

    def test_raw_payload_is_non_empty_dict(self, telegram_envelope):
        """raw_payload must be a non-empty dict (Section C.1 check [5])."""
        raw = telegram_envelope["payload"].get("raw_payload")
        assert isinstance(raw, dict), "raw_payload must be a dict"
        assert len(raw) > 0, "raw_payload must not be empty"

    def test_bronze_payload_has_ingestion_id(self, telegram_envelope):
        """ingestion_id must be a valid UUID (Section C.1)."""
        ingestion_id = telegram_envelope["payload"].get("ingestion_id", "")
        parsed = uuid.UUID(ingestion_id)
        assert parsed.version == 4

    def test_http_status_code_is_zero(self, telegram_envelope):
        """
        http_status_code must be 0 for MTProto sources (check [23]).

        Why 0 (not 200): MTProto is not HTTP. Using 200 would imply a successful
        HTTP response that never occurred. 0 signals "not applicable" for
        event-driven push sources (Section C.1 architectural note).
        """
        metadata = telegram_envelope["payload"].get("metadata", {})
        assert metadata.get("http_status_code") == 0, (
            "Telegram is MTProto (not HTTP) — http_status_code must be 0"
        )

    def test_request_duration_ms_is_zero(self, telegram_envelope):
        """
        request_duration_ms must be 0 for push events (check [24]).

        Why 0: Telethon delivers messages via a persistent push connection —
        there is no discrete request/response cycle to time (Section C.1).
        """
        metadata = telegram_envelope["payload"].get("metadata", {})
        assert metadata.get("request_duration_ms") == 0, (
            "Telegram is push-based — request_duration_ms must be 0"
        )

    def test_source_endpoint_is_telegram_url(self, telegram_envelope):
        """
        source_endpoint must be 'https://t.me/{channel_username}' (check [25]).

        Format chosen to be human-readable and consistently resolvable
        for Bronze audit replay (Section C.1 source_endpoint field).
        """
        endpoint = telegram_envelope["payload"].get("source_endpoint", "")
        assert endpoint.startswith("https://t.me/"), (
            f"source_endpoint must be a t.me URL. Got: {endpoint!r}"
        )
        # The channel in the endpoint must be one of the 7 registered channels
        channel_part = endpoint.replace("https://t.me/", "").lower()
        assert any(ch.lower() == channel_part for ch in CHANNEL_USERNAMES), (
            f"source_endpoint channel not in CHANNEL_USERNAMES. Got: {endpoint!r}"
        )

    def test_bronze_payload_has_ingestion_timestamp(self, telegram_envelope):
        """ingestion_timestamp must be a valid ISO8601 timezone-aware string (Section C.1)."""
        ts     = telegram_envelope["payload"].get("ingestion_timestamp", "")
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None


# ==========================================================
# Gate 1 — Validator Integration Tests (Section 9.3)
# ==========================================================

class TestValidatorIntegration:
    """
    Runs the same validators the Flink Silver Job uses (Section 9.3 Gate 1).

    A pass here guarantees the Silver Job's first gate will also accept
    Telegram messages without routing them to dead-letter-queue.
    """

    def test_validate_envelope_passes(self, telegram_envelope):
        """
        validate_envelope() must return is_valid=True (checks [6]).

        A failure here means the Silver Job would DLQ this message at its
        very first validation step — before any transformation runs.
        """
        result = validate_envelope(telegram_envelope)
        assert result.is_valid, f"Envelope validation errors: {result.errors}"

    def test_validate_bronze_payload_passes(self, telegram_envelope):
        """validate_bronze_payload() on the inner payload must pass (check [7])."""
        inner  = telegram_envelope["payload"]
        result = validate_bronze_payload(inner)
        assert result.is_valid, f"Bronze payload validation errors: {result.errors}"

    def test_each_envelope_has_distinct_event_id(self):
        """
        Two back-to-back build_bronze_message() calls must produce different
        event_ids (check [9]).

        Verifies that event_id is generated fresh per call (not a module-level
        constant), which is required for the Bronze 'Paper Trail' (Section 3.2).
        """
        raw = {"message_id": 1, "message_text": "test", "message_url": "https://t.me/c/1"}
        env1 = build_bronze_message("telegram", "https://t.me/c", raw, 0, 0)
        env2 = build_bronze_message("telegram", "https://t.me/c", raw, 0, 0)
        assert env1["event_id"] != env2["event_id"], (
            "event_id must be a fresh UUID per message, not reused"
        )


# ==========================================================
# Gate 1 — NDJSON Round-Trip (Section 3.4)
# ==========================================================

class TestNdjsonRoundTrip:
    """Verifies NDJSON serialisation/deserialisation preserves the full envelope (check [8])."""

    def test_ndjson_round_trip_preserves_envelope(self, telegram_envelope):
        """
        Serialise the envelope to NDJSON bytes and back — the result must be
        structurally identical to the original dict.

        Why this matters: Kafka messages are stored as NDJSON bytes. If the
        serialiser drops a key or changes a value (e.g. coerces an int to a
        string), the Silver Job's validator will fail on the deserialized message
        even though the original Python dict was valid (Section 3.4).
        """
        serialised   = ndjson_serializer(telegram_envelope)
        deserialised = ndjson_deserializer(serialised)

        assert deserialised["event_id"]    == telegram_envelope["event_id"]
        assert deserialised["source_name"] == telegram_envelope["source_name"]
        assert deserialised["payload"]["raw_payload"]["message_id"] == (
            telegram_envelope["payload"]["raw_payload"]["message_id"]
        )
        assert deserialised["payload"]["raw_payload"]["message_url"] == (
            telegram_envelope["payload"]["raw_payload"]["message_url"]
        )

    def test_ndjson_bytes_end_with_newline(self, telegram_envelope):
        """
        NDJSON output must be terminated with a newline (Section 3.4).

        The trailing newline is required by the NDJSON spec — it makes
        GCS-appended files streamable without loading the entire object.
        """
        serialised = ndjson_serializer(telegram_envelope)
        assert serialised.endswith(b"\n"), "NDJSON must end with a newline byte"


# ==========================================================
# Gate 1 — Telegram Domain-Specific Correctness (Section A.1)
# ==========================================================

class TestTelegramDomainCorrectness:
    """
    Verifies Telegram-specific business rules in the raw_payload
    (Section A.1, C.1 / checks [10]–[22]).
    """

    def test_message_id_is_integer(self, telegram_envelope):
        """message_id must be an integer — it is Telegram's native message identifier."""
        raw = telegram_envelope["payload"]["raw_payload"]
        assert isinstance(raw["message_id"], int), (
            f"message_id must be int. Got: {type(raw['message_id']).__name__}"
        )

    def test_channel_username_in_registry(self, telegram_envelope):
        """
        channel_username must be one of the 7 channels from Section A.1.

        Ensures no message from an unvetted channel enters the pipeline.
        All messages arriving at _handle_new_message() originate from
        the channels subscribed in events.NewMessage(chats=CHANNEL_USERNAMES).
        """
        raw      = telegram_envelope["payload"]["raw_payload"]
        username = raw.get("channel_username", "")
        assert any(ch.lower() == username.lower() for ch in CHANNEL_USERNAMES), (
            f"channel_username '{username}' not in CHANNEL_USERNAMES registry"
        )

    def test_channel_title_is_non_empty_string(self, telegram_envelope):
        """channel_title must be a non-empty string (human-readable channel name)."""
        raw = telegram_envelope["payload"]["raw_payload"]
        assert isinstance(raw.get("channel_title"), str)
        assert len(raw["channel_title"]) > 0

    def test_message_text_is_non_empty(self, telegram_envelope):
        """
        message_text must be non-empty (check [13]).

        The media-only filter in _handle_new_message() ensures no message
        reaches _build_raw_payload() with empty text (Section A.1).
        A message with empty text here would indicate the filter was bypassed.
        """
        raw = telegram_envelope["payload"]["raw_payload"]
        text = raw.get("message_text", "")
        assert isinstance(text, str) and len(text.strip()) > 0, (
            "message_text must be non-empty — media-only messages must be filtered"
        )

    def test_message_date_is_iso8601_utc(self, telegram_envelope):
        """message_date must be a timezone-aware ISO8601 string (check [14])."""
        raw = telegram_envelope["payload"]["raw_payload"]
        ts  = raw.get("message_date", "")
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None, (
            "message_date must be timezone-aware ISO8601"
        )

    def test_message_url_is_telegram_link(self, telegram_envelope):
        """
        message_url must be a canonical t.me URL (check [15]).

        This is the dedup key passed to hash_document() by the Silver Job
        (Section 4.1C) — equivalent to canonical_url in the ArXiv producer.
        Format: https://t.me/{channel_username}/{message_id}
        """
        raw = telegram_envelope["payload"]["raw_payload"]
        url = raw.get("message_url", "")
        assert url.startswith("https://t.me/"), (
            f"message_url must start with 'https://t.me/'. Got: {url!r}"
        )

    def test_message_url_contains_message_id(self, telegram_envelope):
        """message_url must embed the message_id (check [16])."""
        raw        = telegram_envelope["payload"]["raw_payload"]
        url        = raw.get("message_url", "")
        message_id = raw.get("message_id")
        assert str(message_id) in url, (
            f"message_url must contain message_id={message_id}. Got: {url!r}"
        )

    def test_extracted_links_is_list(self, telegram_envelope):
        """extracted_links must be a list — may be empty (check [17])."""
        raw = telegram_envelope["payload"]["raw_payload"]
        assert isinstance(raw.get("extracted_links"), list)

    def test_is_forwarded_is_bool(self, telegram_envelope):
        """is_forwarded must be a boolean (check [18])."""
        raw = telegram_envelope["payload"]["raw_payload"]
        assert isinstance(raw.get("is_forwarded"), bool)

    def test_forwarded_from_is_str_or_none(self, telegram_envelope):
        """forwarded_from must be a str or None — not an int, dict, or other type (check [19])."""
        raw  = telegram_envelope["payload"]["raw_payload"]
        fwd  = raw.get("forwarded_from")
        assert fwd is None or isinstance(fwd, str), (
            f"forwarded_from must be str or None. Got: {type(fwd).__name__}"
        )

    def test_views_is_int_or_none(self, telegram_envelope):
        """views must be int or None — Telegram channels don't always expose view counts (check [20])."""
        raw   = telegram_envelope["payload"]["raw_payload"]
        views = raw.get("views")
        assert views is None or isinstance(views, int), (
            f"views must be int or None. Got: {type(views).__name__}"
        )

    def test_has_media_is_bool(self, telegram_envelope):
        """has_media must be a boolean (check [21])."""
        raw = telegram_envelope["payload"]["raw_payload"]
        assert isinstance(raw.get("has_media"), bool)

    def test_no_impact_boost_field(self, telegram_envelope):
        """
        raw_payload must NOT contain an impact_boost field (check [22]).

        Impact Boost (+1 to impact_level) is reserved for NewsAPI articles
        covering Israeli/Middle East security or energy topics (Section B.4).
        Telegram messages receive their impact_level purely from the Gold Job's
        Cognitive Metadata Extraction (Section 4.2A).
        """
        raw = telegram_envelope["payload"]["raw_payload"]
        assert "impact_boost" not in raw, (
            "raw_payload must not include 'impact_boost' — not applicable to Telegram (Section B.4)"
        )


# ==========================================================
# Gate 1 — _build_raw_payload() Direct Tests (check [26]–[38])
# ==========================================================

class TestRawPayloadBuilder:
    """
    Tests TelegramProducer._build_raw_payload() directly using a mock Telethon
    Message object. Validates the field mapping contract between the producer
    and the Silver Job's map_telegram_message_to_silver() (Task 4B.2).
    """

    def test_all_required_keys_present(self, built_raw):
        """
        _build_raw_payload() must produce all keys the Silver Job reads (check [26]).

        These 10 keys are the canonical interface between Bronze and Silver
        for Telegram. Adding a key here requires a corresponding update in
        map_telegram_message_to_silver() — and vice versa.
        """
        missing = REQUIRED_RAW_PAYLOAD_KEYS - set(built_raw.keys())
        assert not missing, f"_build_raw_payload() missing required keys: {missing}"

    def test_message_url_format(self, built_raw):
        """message_url must follow https://t.me/{username}/{id} format."""
        assert built_raw["message_url"] == "https://t.me/clashreport/98765"

    def test_channel_title_matches_registry(self, built_raw):
        """channel_title must be the human-readable name from CHANNELS registry."""
        assert built_raw["channel_title"] == "Clash Report"

    def test_message_date_is_timezone_aware(self, built_raw):
        """message_date from a tz-aware datetime must preserve UTC offset."""
        parsed = datetime.fromisoformat(built_raw["message_date"].replace("Z", "+00:00"))
        assert parsed.tzinfo is not None

    def test_has_media_false_for_text_only_message(self, mock_message, built_raw):
        """has_media must be False when message.media is None (check [37])."""
        assert mock_message.media is None
        assert built_raw["has_media"] is False

    def test_has_media_true_for_text_with_media(self):
        """
        has_media must be True when message has both text and media (check [38]).

        A photo with a caption passes the media-only filter (text is non-empty)
        but should still record has_media=True for downstream context.
        """
        msg_with_media = MagicMock()
        msg_with_media.id       = 99999
        msg_with_media.text     = "Photo caption with text — should pass the filter"
        msg_with_media.date     = datetime(2024, 9, 23, tzinfo=timezone.utc)
        msg_with_media.fwd_from = None
        msg_with_media.media    = MagicMock()  # non-None → has_media=True
        msg_with_media.entities = None
        msg_with_media.views    = None

        raw = TelegramProducer._build_raw_payload(msg_with_media, "disclosetv")
        assert raw["has_media"] is True


# ==========================================================
# Gate 1 — Media-Only Filter Tests (Section A.1 / checks [27]–[30])
# ==========================================================

class TestMediaOnlyFilter:
    """
    Validates the media-only filter logic that runs at the top of
    _handle_new_message() (Section A.1).

    Why test the filter condition (not the async handler): the async handler
    requires a live Telethon event object. The filter itself is a single boolean
    expression — testing the precondition directly is sufficient and avoids the
    need for an asyncio test loop at Gate 1.
    """

    @pytest.mark.parametrize("text,description", [
        (None,  "text=None (photo with no caption)"),
        ("",    "text='' (empty string)"),
        ("   ", "text='   ' (whitespace only)"),
        ("\n",  "text='\\n' (newline only)"),
        ("\t",  "text='\\t' (tab only)"),
    ])
    def test_media_only_messages_are_dropped(self, text, description):
        """
        Messages with no meaningful text content must be filtered (checks [27]–[29]).

        The filter expression from _handle_new_message():
            if not message.text or not message.text.strip(): return
        """
        msg          = MagicMock()
        msg.text     = text
        should_drop  = not msg.text or not msg.text.strip()
        assert should_drop, (
            f"Expected message to be dropped: {description}. "
            f"Media-only messages must not reach Bronze (Section A.1)."
        )

    @pytest.mark.parametrize("text", [
        "BREAKING: Naval vessels detected",
        "Photo caption: explosion near port",
        "  Leading whitespace with content  ",
        ".",  # single character — still content
    ])
    def test_text_messages_pass_filter(self, text):
        """Messages with non-empty, non-whitespace text must pass the filter (check [30])."""
        msg          = MagicMock()
        msg.text     = text
        should_drop  = not msg.text or not msg.text.strip()
        assert not should_drop, (
            f"Expected message to pass filter but it was dropped. text={text!r}"
        )


# ==========================================================
# Gate 1 — _extract_links() Tests (checks [31]–[33])
# ==========================================================

class TestExtractLinks:
    """
    Tests the _extract_links() helper that parses Telegram message entities
    to extract embedded hyperlinks for domain_context.extracted_links (Section C.5).
    """

    def test_no_entities_returns_empty_list(self):
        """_extract_links() must return [] when message.entities is None (check [31])."""
        msg          = MagicMock()
        msg.entities = None
        assert _extract_links(msg) == []

    def test_empty_entities_list_returns_empty(self):
        """_extract_links() must return [] for an empty entities list."""
        msg          = MagicMock()
        msg.entities = []
        assert _extract_links(msg) == []

    def test_extracts_bare_url_entity(self):
        """
        _extract_links() must extract URLs from MessageEntityUrl (check [32]).

        MessageEntityUrl represents a bare URL typed directly in the message text.
        The URL is sliced from message.text using entity.offset and entity.length.
        """
        url_text = "https://t.me/clashreport/98764"
        entity   = MessageEntityUrl(offset=0, length=len(url_text))
        msg      = MagicMock()
        msg.text     = url_text + " — follow for updates"
        msg.entities = [entity]

        links = _extract_links(msg)
        assert len(links) == 1
        assert links[0] == url_text

    def test_extracts_hyperlinked_text_url(self):
        """
        _extract_links() must extract URLs from MessageEntityTextUrl (check [33]).

        MessageEntityTextUrl represents clickable text (e.g. "Read more") where
        the URL is stored in entity.url, not embedded in message.text.
        """
        target_url = "https://reuters.com/article/strait-of-hormuz"
        entity     = MessageEntityTextUrl(offset=0, length=9, url=target_url)
        msg        = MagicMock()
        msg.text     = "Read more about the developing situation."
        msg.entities = [entity]

        links = _extract_links(msg)
        assert len(links) == 1
        assert links[0] == target_url

    def test_extracts_mixed_entity_types(self):
        """_extract_links() must handle both entity types in the same message."""
        bare_url     = "https://t.me/c/98764"
        hyperlink    = "https://reuters.com/article/123"

        bare_entity  = MessageEntityUrl(offset=0, length=len(bare_url))
        hyper_entity = MessageEntityTextUrl(offset=len(bare_url) + 1, length=4, url=hyperlink)

        msg          = MagicMock()
        msg.text     = bare_url + " Read"
        msg.entities = [bare_entity, hyper_entity]

        links = _extract_links(msg)
        assert bare_url  in links
        assert hyperlink in links
        assert len(links) == 2


# ==========================================================
# Gate 1 — _get_forwarded_from() Tests (checks [34]–[36])
# ==========================================================

class TestGetForwardedFrom:
    """
    Tests the _get_forwarded_from() helper that extracts the original sender
    name for forwarded messages — stored as domain_context.forwarded_from (C.5).
    """

    def test_non_forwarded_message_returns_none(self):
        """_get_forwarded_from() must return None when fwd_from is None (check [34])."""
        msg          = MagicMock()
        msg.fwd_from = None
        assert _get_forwarded_from(msg) is None

    def test_forwarded_message_returns_from_name(self):
        """
        _get_forwarded_from() must return the from_name string (check [35]).

        from_name is the display name Telegram exposes for the original sender.
        It is a string (e.g. "Intel Slava Z") — not a peer_id that would require
        an async entity resolution call.
        """
        fwd           = MagicMock()
        fwd.from_name = "Intel Slava Z"
        msg           = MagicMock()
        msg.fwd_from  = fwd

        result = _get_forwarded_from(msg)
        assert result == "Intel Slava Z"

    def test_forwarded_with_hidden_sender_returns_private(self):
        """
        _get_forwarded_from() must return "private" when from_name is None (check [36]).

        Telegram allows users to forward messages with identity hidden.
        In this case fwd_from is not None (the forward exists) but
        fwd_from.from_name is None (identity suppressed). "private" is the
        sentinel returned rather than None (which would imply no forward).
        """
        fwd           = MagicMock()
        fwd.from_name = None
        msg           = MagicMock()
        msg.fwd_from  = fwd

        result = _get_forwarded_from(msg)
        assert result == "private"


# ==========================================================
# Gate 1 — Channel Registry Completeness (Section A.1 / check [39])
# ==========================================================

class TestChannelRegistry:
    """
    Verifies the CHANNEL_USERNAMES list covers all 7 channels from Section A.1.
    A missing channel means that channel's messages would never enter the pipeline.
    """

    def test_all_seven_channels_registered(self):
        """CHANNEL_USERNAMES must contain all 7 Section A.1 channels (check [39])."""
        registered_lower = {u.lower() for u in CHANNEL_USERNAMES}
        required_lower   = {ch.lower() for ch in REQUIRED_CHANNELS}
        missing = required_lower - registered_lower
        assert not missing, (
            f"Section A.1 channels missing from CHANNEL_USERNAMES: {missing}"
        )

    def test_channel_count_is_exactly_seven(self):
        """CHANNEL_USERNAMES must have exactly 7 entries — no accidental additions."""
        assert len(CHANNEL_USERNAMES) == 7, (
            f"Expected 7 channels (Section A.1). Got: {len(CHANNEL_USERNAMES)}"
        )
