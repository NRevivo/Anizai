"""
Gate 2 — Logic Gate: Telegram Silver Processing Validation (Section 4.1).

Validates that the Silver Job's Telegram branch transforms and routes correctly
using mock payloads — no live Flink cluster, no Kafka broker, no Telethon auth.

What Gate 2 covers (Section 9.3 Gate 2):
  [1]  Valid Telegram message → routes to SILVER_GLOBAL_NEWS (not SILVER_SOCIAL_PULSE)
  [2]  Invalid envelope (missing event_id) → routes to DEAD_LETTER_QUEUE
  [3]  Invalid bronze payload (missing ingestion_id) → routes to DEAD_LETTER_QUEUE
  [4]  Missing message_url in raw_payload → routes to DEAD_LETTER_QUEUE
  [5]  Empty message_url string → routes to DEAD_LETTER_QUEUE
  [6]  Missing message_text in raw_payload → routes to DEAD_LETTER_QUEUE (text_guard)
  [7]  Whitespace-only message_text → routes to DEAD_LETTER_QUEUE (text_guard)
  [8]  Silver output passes validate_silver_document()
  [9]  doc_id is a non-empty UUID string
  [10] document_hash is a 64-char lowercase SHA-256 hex digest
  [11] canonical_event_id in Silver == Bronze envelope event_id
  [12] source_name == "telegram"
  [13] original_url == raw_payload.message_url (canonical dedup key, Section 4.1C)
  [14] full_text_raw == raw_payload.message_text
  [15] inverted_pyramid_lead == raw_payload.message_text (whole message IS the lede)
  [16] full_text_raw == inverted_pyramid_lead (always equal for Telegram micro-articles)
  [17] detected_entities == [] (Gold Job populates via OpenAI, Section 4.2A)
  [18] relevance_score is float in [0.0, 1.0]
  [19] High-signal message (naval + Strait of Hormuz keywords) → is_high_signal == True
  [20] impact_boost == False ALWAYS (Section B.4 — not applicable to Telegram)
  [21] impact_boost_reason == "" ALWAYS
  [22] author == channel_title (human-readable, not @handle)
  [23] channel_username passed through from raw_payload
  [24] extracted_links passed through as a list
  [25] is_forwarded passed through as a bool
  [26] forwarded_from passed through (str or None)
  [27] views passed through (int or None)
  [28] has_media passed through as a bool
  [29] sniper_keywords is a sorted list
  [30] bronze_ref == Bronze envelope event_id (lineage link, Section 3.2)
  [31] publish_date == raw_payload.message_date
  [32] Low-signal message routes to SILVER_GLOBAL_NEWS (sniper never blocks Silver)
  [33] Low-signal message has is_high_signal == False
  [34] DLQ record carries source_topic == BRONZE_TELEGRAM (Section 3.5)
  [35] DLQ record preserves original_message intact for replay
  [36] DLQ record has non-empty validation_errors
  [37] DLQ failed_stage == "url_guard" when message_url is missing
  [38] DLQ failed_stage == "text_guard" when message_text is empty
  [39] document_hash deterministic across repeated calls (same input = same hash)
  [40] document_hash differs for different message_url (different message identity)
  [41] doc_id unique per call (primary key, not a dedup key)

Translation paths (Section 4.1D — added Sprint 13):
  [42] needs_translation() returns True for Hebrew channels (abualiexpress, yediotnews25)
  [43] needs_translation() returns False for English channels (clashreport, intelslava, etc.)
  [44] English channel with mock client → full_text_raw == message_text (no API call)
  [45] Hebrew channel with mock client → full_text_raw == translated text
  [46] Hebrew channel with mock client → original_text == raw message_text
  [47] Hebrew channel with mock client → document_hash computed from ORIGINAL text (not translated)
  [48] Hebrew channel, client=None → full_text_raw == message_text (pass-through)
  [49] Hebrew channel, client raises → full_text_raw == message_text (pass-through, warning only)
  [50] English channel → original_text == "" (no translation performed, no storage waste)

Key Telegram departures from ArXiv Silver Gate 2:
  - Guard on message_url (not url) — canonical dedup key for Telegram (Section 4.1C)
  - Additional text_guard: empty message_text → DLQ (defense in depth, Section A.1)
  - full_text_raw = message_text (entire message, no content/description split)
  - inverted_pyramid_lead = message_text (micro-article = lede)
  - author = channel_title (human-readable, not a joined authors list)
  - title = first 120 chars of message_text (no separate title field)
  - impact_boost = False always (Section B.4 — not a live news feed)
  - DLQ source_topic = BRONZE_TELEGRAM (not BRONZE_ARXIV or BRONZE_NEWSAPI)
  - Routing: SILVER_GLOBAL_NEWS (overrides legacy BRONZE_TO_SILVER_ROUTING which
    mapped BRONZE_TELEGRAM → SILVER_SOCIAL_PULSE; corrected in Task 4B.1 fix)

References:
    - Section 4.1:   Silver Job specification
    - Section 4.1A:  Keyword Sniper — gates Gold OpenAI call (not Silver emission)
    - Section 4.1C:  SHA-256 deduplication — message_url as dedup key
    - Section 4.2A:  Gold Job — detected_entities populated by OpenAI
    - Section 4.4:   Mock-Driven Development
    - Section 9.3:   Triple-Gate Test Matrix — Gate 2
    - Section A.1:   Telegram ingestion method — media-only filter, micro-article model
    - Section B.4:   Impact Boost — not applicable to Telegram channels
    - Section C.3:   Silver Full-Text Document Store schema (Telegram News-Centric)
    - Section C.5:   Gold domain_context — Telegram Direct Signal extension
    - Section 3.5:   Dead-Letter Queue — never silently drop
"""

import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from config.kafka_topics import BRONZE_TELEGRAM, DEAD_LETTER_QUEUE, SILVER_GLOBAL_NEWS
from ingestion.telegram_producer import SOURCE_NAME
from processing.silver_job import map_telegram_message_to_silver, process_telegram_message
from processing.translation import needs_translation
from utils.kafka_utils import build_bronze_message
from utils.validators import validate_silver_document

# ==========================================================
# Paths & Helpers
# ==========================================================

MOCKS_DIR = Path(__file__).parent.parent / "mocks"

_TELEGRAM_ENDPOINT = "https://t.me/clashreport"


def _build_envelope(raw: dict) -> dict:
    return build_bronze_message(
        source_name=SOURCE_NAME,
        source_endpoint=_TELEGRAM_ENDPOINT,
        raw_payload=raw,
        http_status_code=0,
        request_duration_ms=0,
    )


# ==========================================================
# Fixtures
# ==========================================================

@pytest.fixture(scope="module")
def telegram_raw() -> dict:
    """
    Load the Telegram bronze mock payload (Section 4.4), stripping _comment key.

    This is the dict TelegramProducer._build_raw_payload() produces — stored
    verbatim as raw_payload inside the Bronze envelope. The mock message contains
    'naval' and 'strait of hormuz' — both in MASTER_KEYWORD_LIST — guaranteeing
    is_high_signal=True through the Keyword Sniper.
    """
    with open(MOCKS_DIR / "telegram_bronze_payload.json", encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


@pytest.fixture(scope="module")
def telegram_envelope(telegram_raw) -> dict:
    """Build a full Bronze envelope from the mock Telegram message."""
    return _build_envelope(telegram_raw)


@pytest.fixture(scope="module")
def silver_record(telegram_envelope) -> dict:
    """
    Run process_telegram_message() on the mock envelope and return the Silver record.

    The mock (Strait of Hormuz naval alert) must route to SILVER_GLOBAL_NEWS —
    not SILVER_SOCIAL_PULSE. Fails immediately if routing is wrong.
    """
    topic, record = process_telegram_message(telegram_envelope)
    assert topic == SILVER_GLOBAL_NEWS, (
        f"Expected SILVER_GLOBAL_NEWS, got '{topic}'. "
        f"Telegram routes to global_news (not social_pulse) — see Part 1.5 "
        f"Vector Persistence Map. Record: {record}"
    )
    return record


# ==========================================================
# Gate 2 — Routing Tests (checks [1]–[7])
# ==========================================================

class TestTelegramSilverRouting:
    """
    Verifies process_telegram_message() routes to the correct Kafka topic.
    """

    def test_valid_message_routes_to_silver_global_news(self, telegram_envelope):
        """
        A valid Telegram message must route to SILVER_GLOBAL_NEWS (check [1]).

        Telegram is treated as a micro-article (direct signal), not community
        discourse. Its Silver store is knowledge_vault; vector target is
        knowledge_vectors (Part 1.5 Vector Persistence Map, Section C.5).
        It must NOT route to SILVER_SOCIAL_PULSE.
        """
        topic, record = process_telegram_message(telegram_envelope)
        assert topic == SILVER_GLOBAL_NEWS, (
            f"Expected SILVER_GLOBAL_NEWS, got '{topic}'"
        )
        assert record is not None

    def test_valid_message_does_not_route_to_social_pulse(self, telegram_envelope):
        """
        Telegram must never route to SILVER_SOCIAL_PULSE (regression guard).

        The legacy BRONZE_TO_SILVER_ROUTING dict mapped BRONZE_TELEGRAM →
        SILVER_SOCIAL_PULSE — this was incorrect and fixed in config/kafka_topics.py
        (Task 4B.1 routing fix). process_telegram_message() routes explicitly to
        SILVER_GLOBAL_NEWS regardless of that dict.
        """
        from config.kafka_topics import SILVER_SOCIAL_PULSE
        topic, _ = process_telegram_message(telegram_envelope)
        assert topic != SILVER_SOCIAL_PULSE, (
            "Telegram must route to SILVER_GLOBAL_NEWS, not SILVER_SOCIAL_PULSE"
        )

    def test_invalid_envelope_routes_to_dlq(self, telegram_raw):
        """An envelope missing event_id must route to DEAD_LETTER_QUEUE (check [2])."""
        envelope = _build_envelope(telegram_raw)
        del envelope["event_id"]
        topic, record = process_telegram_message(envelope)
        assert topic == DEAD_LETTER_QUEUE
        assert "validation_errors" in record

    def test_invalid_bronze_payload_routes_to_dlq(self, telegram_raw):
        """
        An envelope with a corrupt Bronze payload (missing ingestion_id) must
        route to DEAD_LETTER_QUEUE (check [3]).
        """
        envelope = _build_envelope(telegram_raw)
        del envelope["payload"]["ingestion_id"]
        topic, record = process_telegram_message(envelope)
        assert topic == DEAD_LETTER_QUEUE

    def test_missing_message_url_routes_to_dlq(self, telegram_raw):
        """
        A raw_payload without message_url must route to DEAD_LETTER_QUEUE (check [4]).

        message_url is the canonical dedup key for hash_document() (Section 4.1C).
        A message without a URL cannot be deduplicated and must not enter knowledge_vault.
        """
        raw_no_url = {k: v for k, v in telegram_raw.items() if k != "message_url"}
        topic, record = process_telegram_message(_build_envelope(raw_no_url))
        assert topic == DEAD_LETTER_QUEUE
        error_text = " ".join(record.get("validation_errors", []))
        assert "message_url" in error_text.lower(), (
            f"DLQ error should mention message_url. Got: {record['validation_errors']}"
        )

    def test_empty_message_url_routes_to_dlq(self, telegram_raw):
        """A raw_payload with message_url='' must route to DEAD_LETTER_QUEUE (check [5])."""
        raw = dict(telegram_raw)
        raw["message_url"] = ""
        topic, _ = process_telegram_message(_build_envelope(raw))
        assert topic == DEAD_LETTER_QUEUE

    def test_whitespace_message_url_routes_to_dlq(self, telegram_raw):
        """A message_url of only whitespace must route to DEAD_LETTER_QUEUE."""
        raw = dict(telegram_raw)
        raw["message_url"] = "   "
        topic, _ = process_telegram_message(_build_envelope(raw))
        assert topic == DEAD_LETTER_QUEUE

    def test_missing_message_text_routes_to_dlq(self, telegram_raw):
        """
        A raw_payload without message_text must route to DEAD_LETTER_QUEUE (check [6]).

        Defense in depth: the producer's media-only filter (Section A.1) should
        prevent this, but a message bypassing the filter must not corrupt knowledge_vault
        (full_text_raw has a NOT NULL constraint).
        """
        raw_no_text = {k: v for k, v in telegram_raw.items() if k != "message_text"}
        topic, record = process_telegram_message(_build_envelope(raw_no_text))
        assert topic == DEAD_LETTER_QUEUE
        error_text = " ".join(record.get("validation_errors", []))
        assert "message_text" in error_text.lower() or "empty" in error_text.lower(), (
            f"DLQ error should mention message_text or empty. Got: {record['validation_errors']}"
        )

    def test_empty_message_text_routes_to_dlq(self, telegram_raw):
        """A raw_payload with message_text='' must route to DEAD_LETTER_QUEUE (check [7])."""
        raw = dict(telegram_raw)
        raw["message_text"] = ""
        topic, _ = process_telegram_message(_build_envelope(raw))
        assert topic == DEAD_LETTER_QUEUE

    def test_whitespace_message_text_routes_to_dlq(self, telegram_raw):
        """A message_text of only whitespace must route to DEAD_LETTER_QUEUE."""
        raw = dict(telegram_raw)
        raw["message_text"] = "   \n\t  "
        topic, _ = process_telegram_message(_build_envelope(raw))
        assert topic == DEAD_LETTER_QUEUE


# ==========================================================
# Gate 2 — Silver Schema Correctness (checks [8]–[31])
# ==========================================================

class TestTelegramSilverSchema:
    """
    Verifies the Silver Full-Text Document Store output conforms to Section C.3
    for Telegram micro-articles.
    """

    def test_silver_passes_validator(self, silver_record):
        """The Silver output must pass validate_silver_document() (check [8])."""
        result = validate_silver_document(silver_record)
        assert result.is_valid, f"Silver document validation failed: {result.errors}"

    def test_doc_id_is_non_empty_uuid(self, silver_record):
        """doc_id must be a non-empty UUID string (check [9])."""
        doc_id = silver_record.get("doc_id", "")
        assert isinstance(doc_id, str) and len(doc_id) > 0
        parsed = uuid.UUID(doc_id)
        assert str(parsed) == doc_id

    def test_document_hash_is_64_char_hex(self, silver_record):
        """document_hash must be a 64-character lowercase SHA-256 hex digest (check [10])."""
        doc_hash = silver_record.get("document_hash", "")
        assert len(doc_hash) == 64, f"document_hash must be 64 chars, got {len(doc_hash)}"
        assert all(c in "0123456789abcdef" for c in doc_hash), (
            "document_hash must be lowercase hexadecimal"
        )

    def test_canonical_event_id_matches_envelope(self, telegram_envelope, silver_record):
        """
        canonical_event_id must equal the Bronze envelope's event_id (check [11]).

        This is the Paper Trail link from Silver back to Bronze (Section 3.2 / 7.2).
        """
        assert silver_record["canonical_event_id"] == telegram_envelope["event_id"]

    def test_source_name_is_telegram(self, silver_record):
        """source_name must be 'telegram' (check [12])."""
        assert silver_record["source_name"] == "telegram"

    def test_original_url_equals_message_url(self, telegram_raw, silver_record):
        """
        original_url must equal raw_payload.message_url verbatim (check [13]).

        message_url (https://t.me/{channel}/{id}) is the canonical dedup anchor
        for hash_document() — equivalent to canonical_url in ArXiv (Section 4.1C).
        """
        assert silver_record["original_url"] == telegram_raw["message_url"]

    def test_full_text_raw_equals_message_text(self, telegram_raw, silver_record):
        """
        full_text_raw must equal raw_payload.message_text (check [14]).

        Telegram messages are micro-articles — the entire message text IS
        the document body (Section C.3 Telegram News-Centric pattern).
        No content/description split exists unlike NewsAPI.
        """
        assert silver_record["full_text_raw"] == telegram_raw["message_text"]

    def test_inverted_pyramid_lead_equals_message_text(self, telegram_raw, silver_record):
        """
        inverted_pyramid_lead must equal raw_payload.message_text (check [15]).

        For micro-articles the whole message IS the lede — there is no separate
        'description' field as in NewsAPI (Section C.3 mapping rationale).
        """
        assert silver_record["inverted_pyramid_lead"] == telegram_raw["message_text"]

    def test_full_text_raw_equals_inverted_pyramid_lead(self, silver_record):
        """
        full_text_raw and inverted_pyramid_lead must be identical for Telegram (check [16]).

        Unlike NewsAPI (description = lede, content = body), Telegram has only
        the message text — both fields point to the same value.
        """
        assert silver_record["full_text_raw"] == silver_record["inverted_pyramid_lead"]

    def test_detected_entities_is_empty_list(self, silver_record):
        """
        detected_entities must be [] at Silver layer (check [17]).

        Entity extraction is performed by the Gold Job's OpenAI call (Section 4.2A).
        Empty list (not None) is the contract for 'extraction pending'.
        """
        assert silver_record["detected_entities"] == []

    def test_relevance_score_is_float_in_range(self, silver_record):
        """relevance_score must be a float in [0.0, 1.0] (check [18])."""
        score = silver_record["relevance_score"]
        assert isinstance(score, float), f"relevance_score must be float, got {type(score)}"
        assert 0.0 <= score <= 1.0, f"relevance_score must be in [0.0, 1.0], got {score}"

    def test_high_signal_for_naval_message(self, silver_record):
        """
        The mock message (naval + Strait of Hormuz) must score is_high_signal=True (check [19]).

        'naval' and 'strait of hormuz' are both in MASTER_KEYWORD_LIST (Geopolitical
        Conflict category). Combined description-slot weight (1.5 each) → raw_score=3.0
        → relevance_score=0.3 >> DEFAULT_THRESHOLD=0.09. A failure indicates a
        keyword list mismatch or snipe() call error.
        """
        assert silver_record["is_high_signal"] is True, (
            f"Naval/Hormuz mock must be high-signal. "
            f"relevance_score={silver_record['relevance_score']}, "
            f"keywords={silver_record['sniper_keywords']}"
        )
        # Must have matched at least one keyword
        assert len(silver_record["sniper_keywords"]) > 0, (
            "Naval/Hormuz message must match at least one keyword from MASTER_KEYWORD_LIST"
        )

    def test_impact_boost_is_always_false(self, silver_record):
        """
        impact_boost must be False for ALL Telegram messages (check [20]).

        Section B.4: Impact Boost is reserved for NewsAPI articles covering
        Israeli/Middle East security or energy topics. Telegram channels are
        already pre-curated for geopolitical signal — applying an additional
        boost would skew Gold Job scoring.
        """
        assert silver_record["impact_boost"] is False, (
            f"impact_boost must always be False for Telegram (Section B.4). "
            f"Got: {silver_record['impact_boost']!r}"
        )

    def test_impact_boost_reason_is_always_empty(self, silver_record):
        """impact_boost_reason must be '' for all Telegram messages (check [21])."""
        assert silver_record["impact_boost_reason"] == "", (
            f"impact_boost_reason must be '' for Telegram. "
            f"Got: {silver_record['impact_boost_reason']!r}"
        )

    def test_author_equals_channel_title(self, telegram_raw, silver_record):
        """
        author must equal channel_title (human-readable name, not @handle) (check [22]).

        knowledge_vault.author stores a human-readable attribution string
        (e.g. 'Clash Report'), consistent with how newsapi stores source.name.
        The @handle is preserved separately in the channel_username field.
        """
        assert silver_record["author"] == telegram_raw["channel_title"]

    def test_channel_username_passed_through(self, telegram_raw, silver_record):
        """channel_username must pass through from raw_payload (check [23])."""
        assert silver_record["channel_username"] == telegram_raw["channel_username"]

    def test_extracted_links_passed_through(self, telegram_raw, silver_record):
        """extracted_links must pass through as a list (check [24])."""
        assert isinstance(silver_record["extracted_links"], list)
        assert silver_record["extracted_links"] == telegram_raw["extracted_links"]

    def test_is_forwarded_passed_through(self, telegram_raw, silver_record):
        """is_forwarded must pass through as a bool (check [25])."""
        assert isinstance(silver_record["is_forwarded"], bool)
        assert silver_record["is_forwarded"] == telegram_raw["is_forwarded"]

    def test_forwarded_from_passed_through(self, telegram_raw, silver_record):
        """forwarded_from must pass through (str or None) (check [26])."""
        assert silver_record["forwarded_from"] == telegram_raw["forwarded_from"]

    def test_views_passed_through(self, telegram_raw, silver_record):
        """views must pass through (int or None) (check [27])."""
        assert silver_record["views"] == telegram_raw["views"]

    def test_has_media_passed_through(self, telegram_raw, silver_record):
        """has_media must pass through as a bool (check [28])."""
        assert isinstance(silver_record["has_media"], bool)
        assert silver_record["has_media"] == telegram_raw["has_media"]

    def test_sniper_keywords_is_sorted_list(self, silver_record):
        """
        sniper_keywords must be a sorted list of matched keyword strings (check [29]).

        Sorted order ensures deterministic Silver records — the same message
        always produces the same keyword list (Section 4.1A).
        """
        keywords = silver_record["sniper_keywords"]
        assert isinstance(keywords, list)
        assert keywords == sorted(keywords), (
            f"sniper_keywords is not sorted: {keywords}"
        )

    def test_bronze_ref_equals_envelope_event_id(self, telegram_envelope, silver_record):
        """
        bronze_ref must equal the Bronze envelope's event_id (check [30]).

        knowledge_vault.py stores bronze_ref (as raw_data_ref) so a failed Gold
        run can replay from the exact Bronze Kafka offset (Section 3.2 lineage).
        """
        assert silver_record["bronze_ref"] == telegram_envelope["event_id"]

    def test_publish_date_equals_message_date(self, telegram_raw, silver_record):
        """publish_date must equal raw_payload.message_date (check [31])."""
        assert silver_record["publish_date"] == telegram_raw["message_date"]


# ==========================================================
# Gate 2 — Impact Boost Invariant (Section B.4)
# ==========================================================

class TestTelegramImpactBoostAbsent:
    """
    Verifies impact_boost is ALWAYS False for Telegram messages, even when
    content includes Middle East / energy keywords that trigger Impact Boost
    on NewsAPI articles (Section B.4).
    """

    def test_middle_east_message_impact_boost_still_false(self):
        """
        Even a message explicitly mentioning 'Israel' and 'crude oil' must
        have impact_boost=False for Telegram (Section B.4).

        Impact Boost is an editorial rule for live news feeds (NewsAPI) only.
        Telegram channels are pre-curated and never receive the boost.
        """
        raw = {
            "message_id":       12345,
            "channel_username": "abualiexpress",
            "channel_title":    "Abu Ali Express",
            "message_text":     "Israel crude oil sanctions impact on Middle East markets.",
            "message_date":     "2024-09-23T14:00:00+00:00",
            "message_url":      "https://t.me/abualiexpress/12345",
            "extracted_links":  [],
            "is_forwarded":     False,
            "forwarded_from":   None,
            "views":            8000,
            "has_media":        False,
        }
        _, silver = process_telegram_message(_build_envelope(raw))
        assert silver["impact_boost"] is False
        assert silver["impact_boost_reason"] == ""

    def test_impact_boost_absent_for_high_urgency_breaking_message(self):
        """
        A BREAKING NEWS message must not receive impact_boost regardless of its
        urgency level — that is the Gold Job's Cognitive Metadata responsibility.
        """
        raw = {
            "message_id":       99999,
            "channel_username": "financialjuice",
            "channel_title":    "FinancialJuice",
            "message_text":     "BREAKING: Fed emergency rate cut — sanctions on Iran oil.",
            "message_date":     "2024-09-23T18:00:00+00:00",
            "message_url":      "https://t.me/financialjuice/99999",
            "extracted_links":  [],
            "is_forwarded":     False,
            "forwarded_from":   None,
            "views":            45000,
            "has_media":        False,
        }
        _, silver = process_telegram_message(_build_envelope(raw))
        assert silver["impact_boost"] is False


# ==========================================================
# Gate 2 — Low-Signal Message Routing (checks [32]–[33])
# ==========================================================

class TestLowSignalTelegramRouting:
    """
    Verifies that a low-signal Telegram message (zero keyword matches) still
    routes to SILVER_GLOBAL_NEWS — the Keyword Sniper gates OpenAI enrichment
    in the Gold Job, never Silver emission (Section 4.1A).
    """

    @pytest.fixture(scope="class")
    def low_signal_envelope(self):
        """
        Build a Bronze envelope for a neutral channel announcement that matches
        zero keywords from MASTER_KEYWORD_LIST.
        """
        raw = {
            "message_id":       55555,
            "channel_username": "disclosetv",
            "channel_title":    "Disclose.tv",
            "message_text":     "New weekly digest schedule: posts at 08:00 UTC Monday-Friday.",
            "message_date":     "2024-09-23T08:00:00+00:00",
            "message_url":      "https://t.me/disclosetv/55555",
            "extracted_links":  [],
            "is_forwarded":     False,
            "forwarded_from":   None,
            "views":            3200,
            "has_media":        False,
        }
        return _build_envelope(raw)

    def test_low_signal_routes_to_silver_global_news(self, low_signal_envelope):
        """
        A low-signal message must still route to SILVER_GLOBAL_NEWS (check [32]).

        The Keyword Sniper score gates Gold Job OpenAI enrichment — it NEVER
        blocks Silver emission (Section 4.1A).
        """
        topic, record = process_telegram_message(low_signal_envelope)
        assert topic == SILVER_GLOBAL_NEWS, (
            f"Low-signal message should still reach SILVER_GLOBAL_NEWS. Got '{topic}'"
        )

    def test_low_signal_has_is_high_signal_false(self, low_signal_envelope):
        """Low-signal message must have is_high_signal=False (check [33])."""
        _, record = process_telegram_message(low_signal_envelope)
        assert record["is_high_signal"] is False

    def test_low_signal_passes_silver_validator(self, low_signal_envelope):
        """Low-signal message must still pass validate_silver_document()."""
        _, record = process_telegram_message(low_signal_envelope)
        result = validate_silver_document(record)
        assert result.is_valid, f"Low-signal Silver doc must pass validator: {result.errors}"

    def test_low_signal_impact_boost_still_false(self, low_signal_envelope):
        """impact_boost is always False for Telegram — even low-signal messages."""
        _, record = process_telegram_message(low_signal_envelope)
        assert record["impact_boost"] is False


# ==========================================================
# Gate 2 — DLQ Correctness (checks [34]–[38])
# ==========================================================

class TestTelegramDLQCorrectness:
    """
    Verifies DLQ records are correctly formed when the Telegram Silver branch
    rejects a message (Section 3.5).
    """

    def test_dlq_source_topic_is_bronze_telegram(self, telegram_raw):
        """
        DLQ records from the Telegram branch must carry source_topic=BRONZE_TELEGRAM
        (check [34]).

        DLQ operators filter by source_topic to replay to the correct producer
        (Section 3.5). Wrong source_topic routes replay to the wrong consumer group.
        """
        envelope = _build_envelope(telegram_raw)
        del envelope["event_id"]
        topic, dlq_record = process_telegram_message(envelope)

        assert topic == DEAD_LETTER_QUEUE
        assert dlq_record["source_topic"] == BRONZE_TELEGRAM, (
            f"DLQ source_topic must be BRONZE_TELEGRAM ('{BRONZE_TELEGRAM}'), "
            f"got '{dlq_record['source_topic']}'"
        )

    def test_dlq_source_topic_not_bronze_arxiv_or_newsapi(self, telegram_raw):
        """Regression guard: Telegram DLQ must NOT carry BRONZE_ARXIV or BRONZE_NEWSAPI."""
        from config.kafka_topics import BRONZE_ARXIV, BRONZE_NEWSAPI
        envelope = _build_envelope(telegram_raw)
        del envelope["event_id"]
        _, dlq_record = process_telegram_message(envelope)
        assert dlq_record["source_topic"] != BRONZE_ARXIV
        assert dlq_record["source_topic"] != BRONZE_NEWSAPI

    def test_dlq_record_preserves_original_message(self, telegram_raw):
        """
        DLQ record must include original_message for replay (check [35]).

        Operators must inspect and replay the rejected message once the schema
        issue is resolved (Section 3.5).
        """
        envelope = _build_envelope(telegram_raw)
        del envelope["event_id"]
        _, dlq_record = process_telegram_message(envelope)

        assert "original_message" in dlq_record
        assert isinstance(dlq_record["original_message"], dict)

    def test_dlq_record_has_validation_errors(self, telegram_raw):
        """DLQ record must have a non-empty validation_errors list (check [36])."""
        envelope = _build_envelope(telegram_raw)
        del envelope["trace_id"]
        _, dlq_record = process_telegram_message(envelope)

        errors = dlq_record.get("validation_errors", [])
        assert isinstance(errors, list)
        assert len(errors) > 0, "DLQ record must contain at least one validation error"

    def test_dlq_failed_layer_is_silver(self, telegram_raw):
        """DLQ record must identify failed_layer='Silver' (Section 3.5)."""
        envelope = _build_envelope(telegram_raw)
        del envelope["event_id"]
        _, dlq_record = process_telegram_message(envelope)
        assert dlq_record["failed_layer"] == "Silver"

    def test_url_guard_dlq_stage(self, telegram_raw):
        """
        DLQ record for a missing message_url must identify failed_stage='url_guard'
        (check [37]).
        """
        raw_no_url = {k: v for k, v in telegram_raw.items() if k != "message_url"}
        _, dlq_record = process_telegram_message(_build_envelope(raw_no_url))
        assert dlq_record["failed_stage"] == "url_guard"

    def test_text_guard_dlq_stage(self, telegram_raw):
        """
        DLQ record for empty message_text must identify failed_stage='text_guard'
        (check [38]).

        Distinct stage name ('text_guard' vs 'url_guard') helps operators quickly
        identify whether the rejection was from the URL guard or the media-only
        defense-in-depth guard (Section A.1).
        """
        raw = dict(telegram_raw)
        raw["message_text"] = ""
        _, dlq_record = process_telegram_message(_build_envelope(raw))
        assert dlq_record["failed_stage"] == "text_guard"


# ==========================================================
# Gate 2 — Document Hash Determinism (checks [39]–[41])
# ==========================================================

class TestTelegramDocumentHashDeterminism:
    """
    Verifies document_hash is stable across repeated calls and correctly
    differentiates messages (Section 4.1C deduplication).
    """

    def test_same_message_same_hash(self, telegram_envelope):
        """
        Calling process_telegram_message() twice on the same envelope must
        produce the same document_hash (check [39]).

        The Knowledge Vault uses this hash for ON CONFLICT dedup — a
        non-deterministic hash would corrupt the dedup gate.
        """
        _, r1 = process_telegram_message(telegram_envelope)
        _, r2 = process_telegram_message(telegram_envelope)
        assert r1["document_hash"] == r2["document_hash"]

    def test_different_message_url_different_hash(self, telegram_raw):
        """
        Same message_text on a different message_url must produce a different hash
        (check [40]).

        The dedup key is (message_text, message_url) — a re-posted identical
        message on a different channel has a different identity (Section 4.1C).
        """
        raw_alt = dict(telegram_raw)
        raw_alt["message_url"] = "https://t.me/disclosetv/77777"

        _, r_original = process_telegram_message(_build_envelope(telegram_raw))
        _, r_alt      = process_telegram_message(_build_envelope(raw_alt))

        assert r_original["document_hash"] != r_alt["document_hash"], (
            "Different message_urls must produce different document hashes"
        )

    def test_doc_id_unique_per_call(self, telegram_envelope):
        """
        doc_id must be a fresh UUID on each call (check [41]).

        doc_id is the knowledge_vault PRIMARY KEY — not the dedup key.
        document_hash is the dedup guard; doc_id identifies this specific
        Silver record instance.
        """
        _, r1 = process_telegram_message(telegram_envelope)
        _, r2 = process_telegram_message(telegram_envelope)
        assert r1["doc_id"] != r2["doc_id"], (
            "doc_id must be a fresh UUID each call — it is a primary key"
        )


# ==========================================================
# Gate 2 — Translation Paths (Section 4.1D, Sprint 13)
# checks [42]–[50]
# ==========================================================

# Raw payload template for a Hebrew-channel message.
# channel_username = "abualiexpress" → needs_translation() returns True.
_HEBREW_RAW = {
    "message_id":       77001,
    "channel_username": "abualiexpress",
    "channel_title":    "Abu Ali Express",
    "message_text":     "כוחות הצי האיראני עצרו מכלית נפט בסמוך למצר הורמוז.",  # Hebrew
    "message_date":     "2024-09-23T10:00:00+00:00",
    "message_url":      "https://t.me/abualiexpress/77001",
    "extracted_links":  [],
    "is_forwarded":     False,
    "forwarded_from":   None,
    "views":            12000,
    "has_media":        False,
}

_HEBREW_TRANSLATED = "Iranian naval forces seized an oil tanker near the Strait of Hormuz."


def _make_mock_client(translated_text: str = _HEBREW_TRANSLATED) -> MagicMock:
    """
    Build a mock openai.OpenAI client whose chat.completions.create() returns
    a pre-configured translation string.

    The mock mirrors the real OpenAI response structure so translation.py's
    `response.choices[0].message.content` call succeeds without a live API.
    """
    client = MagicMock()
    client.chat.completions.create.return_value.choices[0].message.content = translated_text
    return client


class TestTranslationPaths:
    """
    Verifies translation.py is correctly wired into map_telegram_message_to_silver()
    and process_telegram_message() (Section 4.1D).

    Tests cover four scenarios:
      A. needs_translation() allowlist correctness
      B. English channel — translation never called
      C. Hebrew channel — successful translation applied
      D. Hebrew channel — failure/no-client pass-through
    """

    # ----------------------------------------------------------
    # A. needs_translation() allowlist correctness (checks [42]–[43])
    # ----------------------------------------------------------

    def test_needs_translation_true_for_abualiexpress(self):
        """needs_translation() must return True for abualiexpress (check [42])."""
        assert needs_translation("abualiexpress") is True

    def test_needs_translation_true_for_yediotnews25(self):
        """needs_translation() must return True for yediotnews25 (check [42])."""
        assert needs_translation("yediotnews25") is True

    def test_needs_translation_case_insensitive(self):
        """needs_translation() must be case-insensitive for Hebrew channel lookup."""
        assert needs_translation("AbuAliExpress") is True

    def test_needs_translation_false_for_clashreport(self):
        """needs_translation() must return False for English channels (check [43])."""
        assert needs_translation("clashreport") is False

    def test_needs_translation_false_for_intelslava(self):
        """intelslava posts in English — needs_translation() must return False (check [43])."""
        assert needs_translation("intelslava") is False

    def test_needs_translation_false_for_financialjuice(self):
        """financialjuice is English-only — needs_translation() must return False."""
        assert needs_translation("financialjuice") is False

    def test_needs_translation_false_for_disclosetv(self):
        """disclosetv is English-only — needs_translation() must return False."""
        assert needs_translation("disclosetv") is False

    def test_needs_translation_false_for_faytuks_network(self):
        """Faytuks_Network is English-only — needs_translation() must return False."""
        assert needs_translation("Faytuks_Network") is False

    # ----------------------------------------------------------
    # B. English channel — no translation API call (check [44])
    # ----------------------------------------------------------

    def test_english_channel_no_translation_applied(self, telegram_raw):
        """
        An English channel (clashreport) must not change full_text_raw even when
        a mock client is provided (check [44]).

        needs_translation("clashreport") returns False → translate_to_english()
        returns text unchanged without calling the client.
        """
        mock_client = _make_mock_client("THIS SHOULD NOT APPEAR")
        raw = dict(telegram_raw)   # channel_username == "clashreport"
        silver = map_telegram_message_to_silver(raw, _build_envelope(raw), mock_client)

        assert silver["full_text_raw"] == raw["message_text"], (
            "English channel must not alter full_text_raw"
        )
        # Client must NOT have been called — English channel exits early
        mock_client.chat.completions.create.assert_not_called()

    def test_english_channel_original_text_is_empty(self, telegram_raw):
        """
        original_text must be '' for English channels — translation was not performed,
        so storing the original text is unnecessary (check [50]).
        """
        silver = map_telegram_message_to_silver(telegram_raw, _build_envelope(telegram_raw))
        assert silver["original_text"] == "", (
            "original_text must be '' for English-only channels"
        )

    # ----------------------------------------------------------
    # C. Hebrew channel — successful translation applied (checks [45]–[47])
    # ----------------------------------------------------------

    def test_hebrew_channel_full_text_raw_is_translated(self):
        """
        full_text_raw must equal the translated text for a Hebrew channel (check [45]).

        The Gold Job reads full_text_raw to generate the executive_summary. An
        English Gold Layer requires English full_text_raw — not Hebrew.
        """
        mock_client = _make_mock_client(_HEBREW_TRANSLATED)
        silver = map_telegram_message_to_silver(
            _HEBREW_RAW, _build_envelope(_HEBREW_RAW), mock_client
        )
        assert silver["full_text_raw"] == _HEBREW_TRANSLATED, (
            f"Expected translated text. Got: {silver['full_text_raw']!r}"
        )
        assert silver["inverted_pyramid_lead"] == _HEBREW_TRANSLATED

    def test_hebrew_channel_original_text_preserved(self):
        """
        original_text must hold the pre-translation Hebrew text (check [46]).

        Stored so the RAG agent and human operators can verify the translation
        and trace back to the original OSINT signal (Section 4.1D, D5 decision).
        """
        mock_client = _make_mock_client(_HEBREW_TRANSLATED)
        silver = map_telegram_message_to_silver(
            _HEBREW_RAW, _build_envelope(_HEBREW_RAW), mock_client
        )
        assert silver["original_text"] == _HEBREW_RAW["message_text"], (
            "original_text must hold the pre-translation (Hebrew) message"
        )

    def test_document_hash_computed_from_original_text(self):
        """
        document_hash must be identical whether or not translation is applied (check [47]).

        The hash is the dedup key — it must be stable across translations so that
        a re-ingested message with a different translation model output is correctly
        detected as a duplicate (Section 4.1C).
        """
        from processing.deduplication import hash_document

        expected_hash = hash_document(
            _HEBREW_RAW["message_text"], _HEBREW_RAW["message_url"]
        )
        mock_client = _make_mock_client(_HEBREW_TRANSLATED)
        silver = map_telegram_message_to_silver(
            _HEBREW_RAW, _build_envelope(_HEBREW_RAW), mock_client
        )
        assert silver["document_hash"] == expected_hash, (
            "document_hash must be computed from ORIGINAL (pre-translation) text"
        )

    def test_hebrew_channel_title_uses_translated_text(self):
        """
        title (pseudo-title, first 120 chars) must use the translated text so the
        knowledge_vault stores an English title for the Gold agent's search index.
        """
        mock_client = _make_mock_client(_HEBREW_TRANSLATED)
        silver = map_telegram_message_to_silver(
            _HEBREW_RAW, _build_envelope(_HEBREW_RAW), mock_client
        )
        assert silver["title"] == _HEBREW_TRANSLATED[:120]

    # ----------------------------------------------------------
    # D. Hebrew channel — no-client / failure pass-through (checks [48]–[49])
    # ----------------------------------------------------------

    def test_hebrew_channel_no_client_passthrough(self):
        """
        Hebrew channel with client=None must return the original text unchanged (check [48]).

        Gate 2 tests call process functions without a client — the default client=None
        must be a silent no-op so existing test infrastructure requires no changes.
        """
        silver = map_telegram_message_to_silver(
            _HEBREW_RAW, _build_envelope(_HEBREW_RAW), client=None
        )
        assert silver["full_text_raw"] == _HEBREW_RAW["message_text"], (
            "client=None must pass through original text unchanged"
        )

    def test_hebrew_channel_translation_failure_passthrough(self):
        """
        When the OpenAI call raises an exception, full_text_raw must fall back to
        the original text (check [49]).

        Translation failure is non-fatal: the Bronze signal is valid; only the
        enrichment failed (Section 4.3 — DLQ is for schema failures, not API errors).
        """
        failing_client = MagicMock()
        failing_client.chat.completions.create.side_effect = RuntimeError("API timeout")

        silver = map_telegram_message_to_silver(
            _HEBREW_RAW, _build_envelope(_HEBREW_RAW), failing_client
        )
        assert silver["full_text_raw"] == _HEBREW_RAW["message_text"], (
            "Translation failure must pass through original text — not DLQ, not exception"
        )
        # The record must still be a valid Silver document
        result = validate_silver_document(silver)
        assert result.is_valid, f"Passed-through record must still pass validation: {result.errors}"
