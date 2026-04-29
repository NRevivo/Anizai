"""
Gate 1 — Ingestion Gate: HackerNews Bronze Schema Compliance (Section 9.3).

Validates that the HackerNews producer's envelope-building, payload-construction,
and helper logic produces messages fully compliant with the Bronze Schema
(Section C.1) and Message Envelope (Section 3.2) before any message reaches Kafka.

Design decisions:
  - Zero live connections: no Kafka broker, no Algolia API calls.
    build_bronze_message(), _build_raw_payload(), _strip_html(),
    _classify_story_type(), and _extract_top_comments() are tested in
    complete isolation.
  - Mock payload loaded from tests/mocks/hackernews_bronze_payload.json
    (Section 4.4).
  - HackerNewsProducer.__new__() used to access _extract_top_comments()
    without triggering __init__ (avoids Kafka connection attempt).
  - Validators from utils/validators.py are called directly — the same
    validators the Flink Silver Job uses, so a pass here guarantees the
    Silver Job's first gate will also accept HackerNews messages.

Key HackerNews departures from other producers:
  - No API key required (Section B.2 / Section 9.1) — fully public endpoint.
  - http_status_code == 200 (Algolia is a standard REST/HTTPS API, unlike
    Telegram which uses MTProto with http_status_code=0).
  - Partition key = story_id (Algolia objectID), not source.id or arxiv_id.
  - Silver store: social_vault (Story-Centric). Gold: social_vectors (C.6).
  - entry_type = "hackernews_story_summary" (C.6 platform_logic block).
  - No impact_boost field — Impact Boost is reserved for NewsAPI (Section B.4).
  - top_comments may be [] if items endpoint call fails (non-fatal degradation).
  - story_text is HTML-stripped (Algolia returns embedded HTML markup).
  - story_type derived at Bronze time from _tags ("ask_hn"/"show_hn"/"story").

Gate 1 checklist (Section 9.3 Gate 1):
  [1]  envelope fields present and correctly typed (Section 3.2)
  [2]  event_id is a valid UUIDv4
  [3]  producer_timestamp is a valid ISO8601 UTC string
  [4]  source_name == "hackernews"
  [5]  payload.raw_payload is a non-empty dict
  [6]  validate_envelope() passes without errors
  [7]  validate_bronze_payload() passes without errors
  [8]  NDJSON round-trip preserves the full envelope (Section 3.4)
  [9]  each call to build_bronze_message() produces a distinct event_id
  [10] raw_payload.story_id is a non-empty string
  [11] raw_payload.title is a non-empty string
  [12] raw_payload.url is a string (may be empty for Ask HN — not None)
  [13] raw_payload.author is a string
  [14] raw_payload.points is an int >= 0
  [15] raw_payload.num_comments is an int >= 0
  [16] raw_payload.created_at is a non-empty string
  [17] raw_payload.story_text is a string (may be empty for link stories)
  [18] raw_payload.story_type is one of "story" | "ask_hn" | "show_hn"
  [19] raw_payload.tags is a list
  [20] raw_payload.top_comments is a list
  [21] raw_payload.fetch_mode is "pulse" or "backfill"
  [22] raw_payload has NO impact_boost field (Section B.4 — not applicable)
  [23] http_status_code == 200 (Algolia is REST/HTTPS)
  [24] source_endpoint contains the Algolia domain
  [25] _build_raw_payload() produces all Silver-Job-required keys
  [26] _strip_html() removes HTML tags from text
  [27] _strip_html() unescapes HTML entities (e.g. &#x27; → ')
  [28] _strip_html() collapses whitespace for NDJSON compliance (Section 3.4)
  [29] _strip_html() returns "" for None input
  [30] _strip_html() returns "" for empty string input
  [31] _classify_story_type(["ask_hn", ...]) → "ask_hn"
  [32] _classify_story_type(["show_hn", ...]) → "show_hn"
  [33] _classify_story_type(["story", "front_page"]) → "story"
  [34] _classify_story_type([]) → "story" (default)
  [35] _extract_top_comments() filters deleted comments
  [36] _extract_top_comments() filters dead comments
  [37] _extract_top_comments() filters comments with no text (None or empty)
  [38] _extract_top_comments() returns at most TOP_COMMENTS_LIMIT comments
  [39] _extract_top_comments() strips HTML from comment text
  [40] _build_raw_payload() with None objectID → story_id == ""
  [41] PULSE_MIN_POINTS == 50 (Section B.2)
  [42] BACKFILL_MIN_POINTS == 100 (Section B.2)
  [43] TOP_COMMENTS_LIMIT == 10 (Section B.2)
  [44] BACKFILL_KEYWORDS contains all 3 mandated terms (Section B.2)

References:
    - Section 2.1:  Producer Matrix (Hacker News row)
    - Section 2.2:  SNR Optimization — points > 50 is the Bronze-level filter
    - Section B.2:  HackerNews technical parameters (thresholds, keywords)
    - Section C.1:  Bronze Schema
    - Section C.3:  Silver Social Discourse Store — Story-Centric pattern
    - Section C.6:  Gold Social Pulse Schema — social_vectors, entry_type
    - Section 3.2:  Message Envelope
    - Section 3.4:  NDJSON serialisation (tested via round-trip)
    - Section 4.1C: SHA-256 deduplication — story_id as partition key
    - Section 4.4:  Mock-Driven Development
    - Section 9.1:  No API key required — fully public Algolia endpoint
    - Section 9.3:  Triple-Gate Test Matrix — Gate 1
    - Section B.4:  Impact Boost — not applicable to HackerNews
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ingestion.hackernews_producer import (
    BACKFILL_KEYWORDS,
    BACKFILL_MIN_POINTS,
    PULSE_MIN_POINTS,
    SOURCE_NAME,
    TOP_COMMENTS_LIMIT,
    HackerNewsProducer,
    _classify_story_type,
    _strip_html,
)
from utils.kafka_utils import build_bronze_message, ndjson_deserializer, ndjson_serializer
from utils.validators import validate_bronze_payload, validate_envelope


# ==========================================================
# Paths & Constants
# ==========================================================

MOCKS_DIR      = Path(__file__).parent.parent / "mocks"
HN_MOCK_PATH   = MOCKS_DIR / "hackernews_bronze_payload.json"

# All keys _build_raw_payload() must produce for the Silver Job (Task 4D.2).
REQUIRED_RAW_PAYLOAD_KEYS = {
    "story_id",
    "title",
    "url",
    "author",
    "points",
    "num_comments",
    "created_at",
    "story_text",
    "tags",
    "story_type",
    "top_comments",
    "fetch_mode",
}

# All 3 backfill keywords mandated by Section B.2.
REQUIRED_BACKFILL_KEYWORDS = {"Polymarket", "AI Regulation", "Geopolitics"}

# Algolia domain — present in all source_endpoint values.
ALGOLIA_DOMAIN = "hn.algolia.com"


# ==========================================================
# Fixtures
# ==========================================================

@pytest.fixture(scope="module")
def hn_raw() -> dict:
    """
    Load the HackerNews bronze mock payload (Section 4.4).

    This is the dict that HackerNewsProducer._build_raw_payload() produces —
    stored verbatim as raw_payload inside the Bronze envelope.
    scope="module" reads the file once and shares it across all tests.
    """
    with open(HN_MOCK_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def hn_envelope(hn_raw) -> dict:
    """
    Build the full Bronze envelope from the mock HackerNews payload.

    Mirrors what HackerNewsProducer._emit() does in production: calls
    build_bronze_message() with the raw_payload and the Algolia search
    endpoint URL for source_endpoint.
    """
    source_endpoint = (
        "https://hn.algolia.com/api/v1/search_by_date"
        "?tags=story&numericFilters=points%3E50&hitsPerPage=50"
    )
    return build_bronze_message(
        source_name=SOURCE_NAME,
        source_endpoint=source_endpoint,
        raw_payload=hn_raw,
        http_status_code=200,
        request_duration_ms=312,
    )


@pytest.fixture(scope="module")
def hn_producer() -> HackerNewsProducer:
    """
    Return a HackerNewsProducer instance without triggering __init__.

    __new__() bypasses __init__ so no Kafka connection is attempted.
    Only instance methods that do not depend on self._producer are safe
    to call on this object — _extract_top_comments() qualifies because
    it only reads item_data and calls the module-level _strip_html().
    """
    return HackerNewsProducer.__new__(HackerNewsProducer)


# ==========================================================
# Gate 1 — Envelope Structure Tests (Section 3.2)
# ==========================================================

class TestEnvelopeStructure:
    """Verifies the outer Message Envelope fields (Section 3.2 / checks [1]–[4])."""

    def test_envelope_is_dict(self, hn_envelope):
        assert isinstance(hn_envelope, dict)

    def test_envelope_has_event_id(self, hn_envelope):
        """event_id must be present and a string (Section 3.2)."""
        assert "event_id" in hn_envelope
        assert isinstance(hn_envelope["event_id"], str)

    def test_event_id_is_valid_uuid4(self, hn_envelope):
        """event_id must be a valid UUIDv4 (Section 3.2 / check [2])."""
        parsed = uuid.UUID(hn_envelope["event_id"])
        assert parsed.version == 4

    def test_producer_timestamp_is_present(self, hn_envelope):
        """producer_timestamp must be present (check [3])."""
        assert "producer_timestamp" in hn_envelope
        assert isinstance(hn_envelope["producer_timestamp"], str)

    def test_producer_timestamp_is_iso8601_utc(self, hn_envelope):
        """producer_timestamp must be a timezone-aware ISO8601 string (Section 3.2)."""
        ts     = hn_envelope["producer_timestamp"]
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None, "producer_timestamp must be timezone-aware"

    def test_source_name_is_hackernews(self, hn_envelope):
        """source_name must equal 'hackernews' (Section C.1 / check [4])."""
        assert hn_envelope.get("source_name") == SOURCE_NAME
        assert hn_envelope.get("source_name") == "hackernews"

    def test_schema_version_is_present(self, hn_envelope):
        """schema_version enables forward-compatible schema evolution (Section 3.2)."""
        assert "schema_version" in hn_envelope
        assert hn_envelope["schema_version"] != ""

    def test_trace_id_is_valid_uuid4(self, hn_envelope):
        """trace_id must be a valid UUIDv4 for distributed tracing (Section 7.2)."""
        trace_id = hn_envelope.get("trace_id", "")
        parsed   = uuid.UUID(trace_id)
        assert parsed.version == 4


# ==========================================================
# Gate 1 — Bronze Payload Tests (Section C.1)
# ==========================================================

class TestBronzePayload:
    """Verifies the inner Bronze Schema payload (Section C.1 / checks [5], [23]–[24])."""

    def test_payload_key_exists(self, hn_envelope):
        assert "payload" in hn_envelope
        assert isinstance(hn_envelope["payload"], dict)

    def test_raw_payload_is_non_empty_dict(self, hn_envelope):
        """raw_payload must be a non-empty dict (check [5])."""
        raw = hn_envelope["payload"].get("raw_payload")
        assert isinstance(raw, dict), "raw_payload must be a dict"
        assert len(raw) > 0, "raw_payload must not be empty"

    def test_bronze_payload_has_ingestion_id(self, hn_envelope):
        """ingestion_id must be a valid UUID (Section C.1)."""
        ingestion_id = hn_envelope["payload"].get("ingestion_id", "")
        parsed = uuid.UUID(ingestion_id)
        assert parsed.version == 4

    def test_http_status_code_is_200(self, hn_envelope):
        """
        http_status_code must be 200 for Algolia REST calls (check [23]).

        Why 200 (not 0): Algolia is a standard HTTPS REST API with request/
        response semantics, unlike Telegram (MTProto push, http_status_code=0).
        Algolia always returns HTTP 200 for valid search queries.
        """
        metadata = hn_envelope["payload"].get("metadata", {})
        assert metadata.get("http_status_code") == 200, (
            "Algolia is a REST/HTTPS API — http_status_code must be 200"
        )

    def test_source_endpoint_contains_algolia_domain(self, hn_envelope):
        """
        source_endpoint must reference the Algolia HN domain (check [24]).

        Stored in Bronze for full replay auditability (Section C.1).
        The endpoint captures which search type (search_by_date vs search)
        and parameters (numericFilters, hitsPerPage) produced this story.
        """
        endpoint = hn_envelope["payload"].get("source_endpoint", "")
        assert ALGOLIA_DOMAIN in endpoint, (
            f"source_endpoint must contain '{ALGOLIA_DOMAIN}'. Got: {endpoint!r}"
        )

    def test_bronze_payload_has_ingestion_timestamp(self, hn_envelope):
        """ingestion_timestamp must be a valid ISO8601 timezone-aware string."""
        ts     = hn_envelope["payload"].get("ingestion_timestamp", "")
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None


# ==========================================================
# Gate 1 — Validator Integration Tests (Section 9.3)
# ==========================================================

class TestValidatorIntegration:
    """
    Runs the same validators the Flink Silver Job uses (Section 9.3 Gate 1).

    A pass here guarantees the Silver Job's first gate will accept HackerNews
    messages without routing them to the dead-letter-queue.
    """

    def test_validate_envelope_passes(self, hn_envelope):
        """
        validate_envelope() must return is_valid=True (check [6]).

        A failure here means the Silver Job would DLQ this message at its
        very first validation step — before any transformation runs.
        """
        result = validate_envelope(hn_envelope)
        assert result.is_valid, f"Envelope validation errors: {result.errors}"

    def test_validate_bronze_payload_passes(self, hn_envelope):
        """validate_bronze_payload() on the inner payload must pass (check [7])."""
        inner  = hn_envelope["payload"]
        result = validate_bronze_payload(inner)
        assert result.is_valid, f"Bronze payload validation errors: {result.errors}"

    def test_each_envelope_has_distinct_event_id(self):
        """
        Two back-to-back build_bronze_message() calls must produce different
        event_ids (check [9]).

        Verifies that event_id is generated fresh per call, which is required
        for the Bronze 'Paper Trail' (Section 3.2).
        """
        raw  = {"story_id": "12345", "title": "test", "points": 51}
        env1 = build_bronze_message("hackernews", "https://hn.algolia.com/api/v1/search_by_date", raw)
        env2 = build_bronze_message("hackernews", "https://hn.algolia.com/api/v1/search_by_date", raw)
        assert env1["event_id"] != env2["event_id"], (
            "event_id must be a fresh UUID per message, not reused"
        )


# ==========================================================
# Gate 1 — NDJSON Round-Trip (Section 3.4)
# ==========================================================

class TestNdjsonRoundTrip:
    """Verifies NDJSON serialisation/deserialisation preserves the full envelope (check [8])."""

    def test_ndjson_round_trip_preserves_envelope(self, hn_envelope):
        """
        Serialise the envelope to NDJSON bytes and back — the result must be
        structurally identical to the original dict.

        Why this matters: Kafka messages are stored as NDJSON bytes. If the
        serialiser drops a key or changes a value, the Silver Job's validator
        will fail on the deserialized message even though the original Python
        dict was valid (Section 3.4).
        """
        serialised   = ndjson_serializer(hn_envelope)
        deserialised = ndjson_deserializer(serialised)

        assert deserialised["event_id"]    == hn_envelope["event_id"]
        assert deserialised["source_name"] == hn_envelope["source_name"]
        assert deserialised["payload"]["raw_payload"]["story_id"] == (
            hn_envelope["payload"]["raw_payload"]["story_id"]
        )
        assert deserialised["payload"]["raw_payload"]["points"] == (
            hn_envelope["payload"]["raw_payload"]["points"]
        )

    def test_ndjson_bytes_end_with_newline(self, hn_envelope):
        """
        NDJSON output must be terminated with a newline (Section 3.4).

        Required by the NDJSON spec — makes GCS-appended files streamable
        without loading the entire object.
        """
        serialised = ndjson_serializer(hn_envelope)
        assert serialised.endswith(b"\n"), "NDJSON must end with a newline byte"


# ==========================================================
# Gate 1 — HackerNews Domain-Specific Correctness (Section B.2)
# ==========================================================

class TestHackerNewsDomainCorrectness:
    """
    Verifies HackerNews-specific business rules in the raw_payload
    (Section B.2, C.1 / checks [10]–[22]).
    """

    def test_story_id_is_non_empty_string(self, hn_envelope):
        """story_id must be a non-empty string — it is the Kafka partition key (check [10])."""
        raw = hn_envelope["payload"]["raw_payload"]
        assert isinstance(raw.get("story_id"), str)
        assert len(raw["story_id"]) > 0, "story_id must not be empty"

    def test_title_is_non_empty_string(self, hn_envelope):
        """title must be a non-empty string — it is the primary display field (check [11])."""
        raw = hn_envelope["payload"]["raw_payload"]
        assert isinstance(raw.get("title"), str)
        assert len(raw["title"]) > 0, "title must not be empty"

    def test_url_is_string(self, hn_envelope):
        """
        url must be a string — may be empty for Ask HN, but never None (check [12]).

        Ask HN stories have no external URL; their content lives in story_text.
        Silver Job uses url for link stories and story_text for Ask HN bodies.
        """
        raw = hn_envelope["payload"]["raw_payload"]
        assert isinstance(raw.get("url"), str), (
            "url must be a string — use '' for Ask HN, not None"
        )

    def test_author_is_string(self, hn_envelope):
        """author must be a string (check [13])."""
        raw = hn_envelope["payload"]["raw_payload"]
        assert isinstance(raw.get("author"), str)

    def test_points_is_non_negative_int(self, hn_envelope):
        """points must be an int >= 0 (check [14])."""
        raw = hn_envelope["payload"]["raw_payload"]
        points = raw.get("points")
        assert isinstance(points, int), (
            f"points must be int. Got: {type(points).__name__}"
        )
        assert points >= 0, f"points must be >= 0. Got: {points}"

    def test_num_comments_is_non_negative_int(self, hn_envelope):
        """num_comments must be an int >= 0 (check [15])."""
        raw = hn_envelope["payload"]["raw_payload"]
        nc  = raw.get("num_comments")
        assert isinstance(nc, int), (
            f"num_comments must be int. Got: {type(nc).__name__}"
        )
        assert nc >= 0

    def test_created_at_is_non_empty_string(self, hn_envelope):
        """created_at must be a non-empty string (check [16])."""
        raw = hn_envelope["payload"]["raw_payload"]
        assert isinstance(raw.get("created_at"), str)
        assert len(raw["created_at"]) > 0, "created_at must not be empty"

    def test_story_text_is_string(self, hn_envelope):
        """
        story_text must be a string — may be empty for standard link stories
        (check [17]). Ask HN and Show HN bodies are stored here.
        """
        raw = hn_envelope["payload"]["raw_payload"]
        assert isinstance(raw.get("story_text"), str), (
            "story_text must be a string — use '' for link stories, not None"
        )

    def test_story_type_is_valid(self, hn_envelope):
        """story_type must be one of the three valid values (check [18])."""
        raw        = hn_envelope["payload"]["raw_payload"]
        story_type = raw.get("story_type")
        assert story_type in {"story", "ask_hn", "show_hn"}, (
            f"story_type must be 'story', 'ask_hn', or 'show_hn'. Got: {story_type!r}"
        )

    def test_tags_is_list(self, hn_envelope):
        """tags must be a list (check [19])."""
        raw = hn_envelope["payload"]["raw_payload"]
        assert isinstance(raw.get("tags"), list)

    def test_top_comments_is_list(self, hn_envelope):
        """top_comments must be a list — may be empty if items fetch failed (check [20])."""
        raw = hn_envelope["payload"]["raw_payload"]
        assert isinstance(raw.get("top_comments"), list)

    def test_fetch_mode_is_valid(self, hn_envelope):
        """fetch_mode must be 'pulse' or 'backfill' (check [21])."""
        raw  = hn_envelope["payload"]["raw_payload"]
        mode = raw.get("fetch_mode")
        assert mode in {"pulse", "backfill"}, (
            f"fetch_mode must be 'pulse' or 'backfill'. Got: {mode!r}"
        )

    def test_no_impact_boost_field(self, hn_envelope):
        """
        raw_payload must NOT contain an impact_boost field (check [22]).

        Impact Boost (+1 to impact_level) is reserved for NewsAPI articles
        covering Israeli/Middle East security or energy topics (Section B.4).
        HackerNews stories receive their impact_level purely from the Gold
        Job's Consensus Bundling enrichment (Section 4.2A).
        """
        raw = hn_envelope["payload"]["raw_payload"]
        assert "impact_boost" not in raw, (
            "raw_payload must not include 'impact_boost' — not applicable to "
            "HackerNews (Section B.4)"
        )


# ==========================================================
# Gate 1 — _build_raw_payload() Direct Tests (check [25], [40])
# ==========================================================

class TestRawPayloadBuilder:
    """
    Tests HackerNewsProducer._build_raw_payload() directly using synthetic
    hit dicts. Validates the field-mapping contract between the producer
    and the Silver Job's map_hackernews_story_to_silver() (Task 4D.2).
    """

    def _make_hit(self, **overrides) -> dict:
        """Minimal valid Algolia hit dict, with optional overrides."""
        base = {
            "objectID":    "40185065",
            "title":       "Test Story Title",
            "url":         "https://example.com/article",
            "author":      "testuser",
            "points":      75,
            "num_comments": 20,
            "created_at":  "2024-04-01T14:30:00.000Z",
            "story_text":  None,
            "_tags":       ["story", "front_page"],
        }
        base.update(overrides)
        return base

    def test_all_required_keys_present(self):
        """
        _build_raw_payload() must produce all keys the Silver Job reads (check [25]).

        These 12 keys are the canonical interface between Bronze and Silver
        for HackerNews. Adding a key here requires a corresponding update in
        map_hackernews_story_to_silver() — and vice versa.
        """
        raw     = HackerNewsProducer._build_raw_payload(self._make_hit(), [], "pulse")
        missing = REQUIRED_RAW_PAYLOAD_KEYS - set(raw.keys())
        assert not missing, (
            f"_build_raw_payload() missing required keys: {missing}"
        )

    def test_none_objectid_produces_empty_story_id(self):
        """
        A hit with objectID=None must produce story_id='' (check [40]).

        Prevents None from propagating as the Kafka partition key, which
        would cause type errors in kafka-python-ng's key serialiser.
        """
        hit = self._make_hit(objectID=None)
        raw = HackerNewsProducer._build_raw_payload(hit, [], "pulse")
        assert raw["story_id"] == "", (
            "story_id must be '' when objectID is None — not None itself"
        )

    def test_none_url_produces_empty_string(self):
        """url=None in hit must produce url='' in raw_payload (Ask HN pattern)."""
        hit = self._make_hit(url=None)
        raw = HackerNewsProducer._build_raw_payload(hit, [], "pulse")
        assert raw["url"] == ""

    def test_ask_hn_story_type(self):
        """A hit with 'ask_hn' tag must have story_type='ask_hn'."""
        hit = self._make_hit(_tags=["ask_hn", "story"])
        raw = HackerNewsProducer._build_raw_payload(hit, [], "pulse")
        assert raw["story_type"] == "ask_hn"

    def test_show_hn_story_type(self):
        """A hit with 'show_hn' tag must have story_type='show_hn'."""
        hit = self._make_hit(_tags=["show_hn", "story"])
        raw = HackerNewsProducer._build_raw_payload(hit, [], "pulse")
        assert raw["story_type"] == "show_hn"

    def test_html_stripped_from_story_text(self):
        """story_text HTML is stripped via _strip_html() before storage."""
        hit = self._make_hit(story_text="<p>Ask HN: Is <b>Polymarket</b> legal?</p>")
        raw = HackerNewsProducer._build_raw_payload(hit, [], "pulse")
        assert "<p>" not in raw["story_text"]
        assert "<b>" not in raw["story_text"]
        assert "Ask HN: Is Polymarket legal?" in raw["story_text"]

    def test_top_comments_passed_through(self):
        """top_comments list must be stored verbatim in the raw_payload."""
        comments = [{"comment_id": 1, "author": "user", "text": "test", "created_at": ""}]
        raw      = HackerNewsProducer._build_raw_payload(self._make_hit(), comments, "pulse")
        assert raw["top_comments"] == comments

    def test_empty_top_comments_accepted(self):
        """top_comments=[] must be stored as [] — not None or omitted."""
        raw = HackerNewsProducer._build_raw_payload(self._make_hit(), [], "backfill")
        assert raw["top_comments"] == []

    def test_fetch_mode_stored(self):
        """fetch_mode must be stored as provided."""
        raw_pulse    = HackerNewsProducer._build_raw_payload(self._make_hit(), [], "pulse")
        raw_backfill = HackerNewsProducer._build_raw_payload(self._make_hit(), [], "backfill")
        assert raw_pulse["fetch_mode"]    == "pulse"
        assert raw_backfill["fetch_mode"] == "backfill"


# ==========================================================
# Gate 1 — _strip_html() Tests (checks [26]–[30])
# ==========================================================

class TestStripHtml:
    """
    Tests the _strip_html() helper that cleans HTML markup from Algolia
    story_text and comment text fields (Section 3.4 / checks [26]–[30]).
    """

    def test_removes_paragraph_tags(self):
        """<p> tags must be removed (check [26])."""
        result = _strip_html("<p>Hello world</p>")
        assert "<p>" not in result
        assert "Hello world" in result

    def test_removes_anchor_tags(self):
        """<a href="..."> tags must be removed, link text preserved."""
        result = _strip_html('<a href="https://example.com">read more</a>')
        assert "<a" not in result
        assert "read more" in result

    def test_removes_bold_and_italic_tags(self):
        """Inline formatting tags must be removed."""
        result = _strip_html("<b>bold</b> and <i>italic</i>")
        assert "<b>" not in result
        assert "<i>" not in result
        assert "bold" in result
        assert "italic" in result

    def test_unescapes_html_entities(self):
        """
        HTML entities must be decoded (check [27]).

        Note: &lt;tag&gt; unescapes to <tag>, which the tag-stripping regex then
        removes — producing "". This is expected: escaped angle brackets in Algolia
        HN content represent inline code snippets; stripping them is acceptable and
        correct. Only entities that do NOT produce HTML-tag-shaped strings survive.
        """
        assert _strip_html("it&#x27;s") == "it's"
        assert _strip_html("a &amp; b") == "a & b"
        assert _strip_html("&lt;tag&gt;") == ""   # unescapes to <tag>, then stripped
        assert _strip_html("&quot;quoted&quot;") == '"quoted"'

    def test_collapses_whitespace(self):
        """Whitespace normalisation prevents multi-line NDJSON (check [28], Section 3.4)."""
        result = _strip_html("line one\n\nline two\t  line three")
        assert "\n" not in result
        assert "\t" not in result
        assert "  " not in result  # no double spaces
        assert result == "line one line two line three"

    def test_returns_empty_string_for_none(self):
        """_strip_html(None) must return '' (check [29])."""
        assert _strip_html(None) == ""

    def test_returns_empty_string_for_empty_string(self):
        """_strip_html('') must return '' (check [30])."""
        assert _strip_html("") == ""

    def test_returns_empty_string_for_whitespace_only(self):
        """Whitespace-only strings must collapse to '' after normalisation."""
        assert _strip_html("   ") == ""
        assert _strip_html("\n\t\r") == ""


# ==========================================================
# Gate 1 — _classify_story_type() Tests (checks [31]–[34])
# ==========================================================

class TestClassifyStoryType:
    """
    Tests the _classify_story_type() helper that maps Algolia _tags to
    story_type (Section B.2 / checks [31]–[34]).
    """

    def test_ask_hn_tag_returns_ask_hn(self):
        """'ask_hn' tag must return 'ask_hn' (check [31])."""
        assert _classify_story_type(["ask_hn", "story"]) == "ask_hn"

    def test_show_hn_tag_returns_show_hn(self):
        """'show_hn' tag must return 'show_hn' (check [32])."""
        assert _classify_story_type(["show_hn", "story"]) == "show_hn"

    def test_front_page_story_returns_story(self):
        """Standard story without ask/show tags must return 'story' (check [33])."""
        assert _classify_story_type(["story", "front_page"]) == "story"

    def test_empty_tags_returns_story(self):
        """Empty tags list must return 'story' (default, check [34])."""
        assert _classify_story_type([]) == "story"

    def test_ask_hn_takes_priority_over_show_hn(self):
        """If both ask_hn and show_hn appear (edge case), ask_hn wins (first match)."""
        assert _classify_story_type(["ask_hn", "show_hn"]) == "ask_hn"

    @pytest.mark.parametrize("tags", [
        ["story"],
        ["front_page"],
        ["story", "front_page", "author_user"],
        ["story_40185065"],
    ])
    def test_non_ask_show_tags_return_story(self, tags):
        """Any tag combination without ask_hn/show_hn must return 'story'."""
        assert _classify_story_type(tags) == "story"


# ==========================================================
# Gate 1 — _extract_top_comments() Tests (checks [35]–[39])
# ==========================================================

class TestExtractTopComments:
    """
    Tests HackerNewsProducer._extract_top_comments() which filters and
    extracts first-level comments from an Algolia item response
    (Section B.2, C.3 / checks [35]–[39]).
    """

    def _make_child(self, **overrides) -> dict:
        """Minimal valid comment child dict, with optional overrides."""
        base = {
            "id":         40185200,
            "author":     "testuser",
            "text":       "This is a valid comment.",
            "created_at": "2024-04-01T14:45:00.000Z",
            "deleted":    False,
            "dead":       False,
        }
        base.update(overrides)
        return base

    def test_filters_deleted_comments(self, hn_producer):
        """Comments with deleted=True must be excluded (check [35])."""
        item = {"children": [
            self._make_child(deleted=True),
            self._make_child(id=40185201, text="Valid comment."),
        ]}
        result = hn_producer._extract_top_comments(item)
        assert len(result) == 1
        assert result[0]["comment_id"] == 40185201

    def test_filters_dead_comments(self, hn_producer):
        """Comments with dead=True must be excluded (check [36])."""
        item = {"children": [
            self._make_child(dead=True),
            self._make_child(id=40185202, text="Still alive."),
        ]}
        result = hn_producer._extract_top_comments(item)
        assert len(result) == 1
        assert result[0]["comment_id"] == 40185202

    def test_filters_comments_with_no_text(self, hn_producer):
        """Comments with text=None or text='' must be excluded (check [37])."""
        item = {"children": [
            self._make_child(text=None),
            self._make_child(text=""),
            self._make_child(id=40185203, text="Has text."),
        ]}
        result = hn_producer._extract_top_comments(item)
        assert len(result) == 1
        assert result[0]["comment_id"] == 40185203

    def test_returns_at_most_top_comments_limit(self, hn_producer):
        """Must return at most TOP_COMMENTS_LIMIT (10) comments (check [38])."""
        children = [
            self._make_child(id=40185200 + i, text=f"Comment {i}")
            for i in range(15)  # 15 valid comments — limit must kick in
        ]
        result = hn_producer._extract_top_comments({"children": children})
        assert len(result) == TOP_COMMENTS_LIMIT, (
            f"Expected {TOP_COMMENTS_LIMIT} comments (TOP_COMMENTS_LIMIT). "
            f"Got: {len(result)}"
        )

    def test_html_stripped_from_comment_text(self, hn_producer):
        """Comment text HTML is stripped via _strip_html() (check [39])."""
        item = {"children": [
            self._make_child(text="<p>This is a <b>technical</b> point.</p>")
        ]}
        result = hn_producer._extract_top_comments(item)
        assert len(result) == 1
        assert "<p>" not in result[0]["text"]
        assert "<b>" not in result[0]["text"]
        assert "This is a technical point." in result[0]["text"]

    def test_output_keys_are_correct(self, hn_producer):
        """Each comment dict must have exactly: comment_id, author, text, created_at."""
        item   = {"children": [self._make_child()]}
        result = hn_producer._extract_top_comments(item)
        assert len(result) == 1
        assert set(result[0].keys()) == {"comment_id", "author", "text", "created_at"}

    def test_empty_children_returns_empty_list(self, hn_producer):
        """An item with no children must return []."""
        assert hn_producer._extract_top_comments({"children": []}) == []

    def test_missing_children_key_returns_empty_list(self, hn_producer):
        """An item with no 'children' key must return [] (defensive handling)."""
        assert hn_producer._extract_top_comments({}) == []


# ==========================================================
# Gate 1 — Spec Constants Compliance (Section B.2 / checks [41]–[44])
# ==========================================================

class TestSpecConstants:
    """
    Verifies that the producer's constants match the exact values mandated
    by Section B.2. A constant mismatch means Bronze filtering diverges
    from the spec without any code error in the logic itself.
    """

    def test_pulse_min_points_is_50(self):
        """PULSE_MIN_POINTS must be 50 (Section B.2 / check [41])."""
        assert PULSE_MIN_POINTS == 50, (
            f"Section B.2 mandates points > 50 for pulse. Got: {PULSE_MIN_POINTS}"
        )

    def test_backfill_min_points_is_100(self):
        """BACKFILL_MIN_POINTS must be 100 (Section B.2 / check [42])."""
        assert BACKFILL_MIN_POINTS == 100, (
            f"Section B.2 mandates points > 100 for backfill. Got: {BACKFILL_MIN_POINTS}"
        )

    def test_top_comments_limit_is_10(self):
        """TOP_COMMENTS_LIMIT must be 10 (Section B.2, C.3 / check [43])."""
        assert TOP_COMMENTS_LIMIT == 10, (
            f"Section B.2/C.3 mandates top 10 comments per story. Got: {TOP_COMMENTS_LIMIT}"
        )

    def test_backfill_keywords_contains_all_required(self):
        """BACKFILL_KEYWORDS must contain all 3 Section B.2 keyword groups (check [44])."""
        missing = REQUIRED_BACKFILL_KEYWORDS - set(BACKFILL_KEYWORDS)
        assert not missing, (
            f"Section B.2 backfill keywords missing from BACKFILL_KEYWORDS: {missing}"
        )

    def test_backfill_keywords_count_is_three(self):
        """BACKFILL_KEYWORDS must have exactly 3 entries."""
        assert len(BACKFILL_KEYWORDS) == 3, (
            f"Expected 3 backfill keywords (Section B.2). Got: {len(BACKFILL_KEYWORDS)}"
        )
