"""
Gate 2 — Logic Gate: HackerNews Silver Processing Validation (Section 4.1).

Validates that the Silver Job's HackerNews branch transforms and routes correctly
using mock payloads — no live Flink cluster, no Kafka broker, no Algolia calls.

What Gate 2 covers (Section 9.3 Gate 2):
  [1]  Valid story → routes to SILVER_SOCIAL_PULSE (not SILVER_GLOBAL_NEWS)
  [2]  Invalid envelope (missing event_id) → routes to DEAD_LETTER_QUEUE
  [3]  Invalid bronze payload (missing ingestion_id) → routes to DEAD_LETTER_QUEUE
  [4]  Missing story_id in raw_payload → routes to DEAD_LETTER_QUEUE
  [5]  Empty story_id string → routes to DEAD_LETTER_QUEUE
  [6]  Missing title in raw_payload → routes to DEAD_LETTER_QUEUE
  [7]  Empty title string → routes to DEAD_LETTER_QUEUE
  [8]  Silver output passes validate_silver_social()
  [9]  source_name == "hackernews"
  [10] story_id preserved from raw_payload (str)
  [11] title preserved from raw_payload
  [12] points preserved as int
  [13] url preserved (empty string for Ask HN)
  [14] top_comments preserved as list
  [15] story_type preserved ("story" | "ask_hn" | "show_hn")
  [16] published_at == raw_payload.created_at
  [17] content_hash is a valid 64-char SHA-256 hex string
  [18] relevance_score is float in [0.0, 1.0]
  [19] High-signal story ('Polymarket' + 'prediction markets' in title) → is_high_signal == True
  [20] sniper_keywords is a sorted list
  [21] is_high_signal == True means relevance_score > 0.0
  [22] fetch_mode preserved from raw_payload
  [23] bronze_ref == Bronze envelope event_id (lineage, Section 3.2)
  [24] Low-signal story still routes to SILVER_SOCIAL_PULSE (sniper never blocks)
  [25] Low-signal story has is_high_signal == False
  [26] DLQ source_topic == BRONZE_HACKERNEWS (Section 3.5)
  [27] DLQ preserves original_message for replay
  [28] DLQ has non-empty validation_errors
  [29] DLQ failed_stage == "story_id_guard" when story_id is missing
  [30] DLQ failed_stage == "title_guard" when title is empty
  [31] content_hash is deterministic across repeated calls
  [32] Different stories produce different hashes (collision sanity)
  [33] num_comments preserved as int
  [34] author preserved from raw_payload
  [35] DLQ failed_layer == "Silver"

Key HackerNews departures from Telegram/ArXiv Silver Gate 2:
  - Routes to SILVER_SOCIAL_PULSE (social discourse, not SILVER_GLOBAL_NEWS)
  - Guard on story_id (not message_url or url) — Algolia objectID as dedup anchor
  - Guard on title (not message_text) — primary Gold embedding input
  - validate_silver_social() (not validate_silver_document())
  - No impact_boost field — not applicable to HackerNews (Section B.2)
  - content_hash = hash_social_batch(story_id, top_comments) (Section 4.1C)
  - DLQ source_topic = BRONZE_HACKERNEWS

References:
    - Section 4.1:   Silver Job specification
    - Section 4.1A:  Keyword Sniper — gates Gold OpenAI call (not Silver emission)
    - Section 4.1C:  SHA-256 deduplication — hash_social_batch(story_id, top_comments)
    - Section 4.4:   Mock-Driven Development
    - Section 9.3:   Triple-Gate Test Matrix — Gate 2
    - Section B.2:   HackerNews ingestion parameters (points > 50, pulse mode)
    - Section C.3:   Silver Social Discourse Store — HackerNews Story-Centric pattern
    - Section C.6:   Gold Social Pulse — HackerNews vector target
    - Section 3.5:   Dead-Letter Queue — never silently drop
"""

import copy
import json
import uuid
from pathlib import Path

import pytest

from config.kafka_topics import (
    BRONZE_HACKERNEWS,
    DEAD_LETTER_QUEUE,
    SILVER_SOCIAL_PULSE,
)
from processing.deduplication import hash_social_batch
from processing.silver_job import map_hackernews_story_to_silver, process_hackernews_message
from utils.kafka_utils import build_bronze_message
from utils.validators import validate_silver_social

# ==========================================================
# Paths & Helpers
# ==========================================================

MOCKS_DIR = Path(__file__).parent.parent / "mocks"

_HN_ENDPOINT = "https://hn.algolia.com/api/v1/search_by_date"


def _build_envelope(raw: dict) -> dict:
    return build_bronze_message(
        source_name="hackernews",
        source_endpoint=_HN_ENDPOINT,
        raw_payload=raw,
        http_status_code=200,
        request_duration_ms=112,
    )


# ==========================================================
# Fixtures
# ==========================================================

@pytest.fixture(scope="module")
def hn_raw() -> dict:
    """
    Load the HackerNews Bronze mock payload (Section 4.4), stripping _comment.

    The mock story title contains "Polymarket" and "prediction markets" —
    both now in MASTER_KEYWORD_LIST — guaranteeing is_high_signal=True
    through the Keyword Sniper (Section 4.1A).
    """
    with open(MOCKS_DIR / "hackernews_bronze_payload.json", encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


@pytest.fixture(scope="module")
def hn_envelope(hn_raw) -> dict:
    """Build a full Bronze envelope from the mock HackerNews story."""
    return _build_envelope(hn_raw)


@pytest.fixture(scope="module")
def silver_record(hn_envelope) -> dict:
    """
    Run process_hackernews_message() on the mock envelope and return the Silver record.

    The mock story must route to SILVER_SOCIAL_PULSE (not SILVER_GLOBAL_NEWS).
    Fails immediately if routing is wrong.
    """
    topic, record = process_hackernews_message(hn_envelope)
    assert topic == SILVER_SOCIAL_PULSE, (
        f"Expected SILVER_SOCIAL_PULSE, got '{topic}'. "
        f"HackerNews is Story-Centric social discourse — routes to social_pulse, "
        f"not global_news (Part 1.5 Vector Persistence Map). Record: {record}"
    )
    return record


# ==========================================================
# Gate 2 — Routing Tests (checks [1]–[7])
# ==========================================================

class TestHackerNewsSilverRouting:
    """
    Verifies process_hackernews_message() routes to the correct Kafka topic.
    """

    def test_valid_story_routes_to_silver_social_pulse(self, hn_envelope):
        """Valid story must route to SILVER_SOCIAL_PULSE (check [1])."""
        topic, record = process_hackernews_message(hn_envelope)
        assert topic == SILVER_SOCIAL_PULSE
        assert record is not None

    def test_valid_story_does_not_route_to_global_news(self, hn_envelope):
        """HackerNews must never route to SILVER_GLOBAL_NEWS (regression guard)."""
        from config.kafka_topics import SILVER_GLOBAL_NEWS
        topic, _ = process_hackernews_message(hn_envelope)
        assert topic != SILVER_GLOBAL_NEWS, (
            "HackerNews routes to SILVER_SOCIAL_PULSE. "
            "SILVER_GLOBAL_NEWS is reserved for NewsAPI/ArXiv/Telegram."
        )

    def test_invalid_envelope_missing_event_id_routes_to_dlq(self, hn_raw):
        """Missing event_id in envelope → DLQ (check [2])."""
        bad_env = _build_envelope(hn_raw)
        del bad_env["event_id"]
        topic, record = process_hackernews_message(bad_env)
        assert topic == DEAD_LETTER_QUEUE
        assert record is not None

    def test_invalid_bronze_payload_missing_ingestion_id_routes_to_dlq(self, hn_raw):
        """Missing ingestion_id inside payload → DLQ (check [3])."""
        bad_env = _build_envelope(hn_raw)
        del bad_env["payload"]["ingestion_id"]
        topic, record = process_hackernews_message(bad_env)
        assert topic == DEAD_LETTER_QUEUE

    def test_missing_story_id_routes_to_dlq(self, hn_raw):
        """Missing story_id in raw_payload → DLQ (check [4])."""
        raw = copy.deepcopy(hn_raw)
        del raw["story_id"]
        topic, record = process_hackernews_message(_build_envelope(raw))
        assert topic == DEAD_LETTER_QUEUE

    def test_empty_story_id_routes_to_dlq(self, hn_raw):
        """Empty story_id string → DLQ (check [5])."""
        raw = copy.deepcopy(hn_raw)
        raw["story_id"] = "   "
        topic, record = process_hackernews_message(_build_envelope(raw))
        assert topic == DEAD_LETTER_QUEUE

    def test_missing_title_routes_to_dlq(self, hn_raw):
        """Missing title in raw_payload → DLQ (check [6])."""
        raw = copy.deepcopy(hn_raw)
        del raw["title"]
        topic, record = process_hackernews_message(_build_envelope(raw))
        assert topic == DEAD_LETTER_QUEUE

    def test_empty_title_routes_to_dlq(self, hn_raw):
        """Empty title string → DLQ (check [7])."""
        raw = copy.deepcopy(hn_raw)
        raw["title"] = "   "
        topic, record = process_hackernews_message(_build_envelope(raw))
        assert topic == DEAD_LETTER_QUEUE


# ==========================================================
# Gate 2 — Silver Schema Tests (checks [8]–[23])
# ==========================================================

class TestHackerNewsSilverSchema:
    """
    Verifies the Silver record produced by map_hackernews_story_to_silver()
    passes the validator and carries all required fields.
    """

    def test_silver_passes_validate_silver_social(self, silver_record):
        """Silver output passes validate_silver_social() (check [8])."""
        result = validate_silver_social(silver_record)
        assert result.is_valid, f"validate_silver_social errors: {result.errors}"

    def test_source_name_is_hackernews(self, silver_record):
        """source_name must be 'hackernews' (check [9])."""
        assert silver_record["source_name"] == "hackernews"

    def test_story_id_preserved_as_string(self, silver_record, hn_raw):
        """story_id preserved from raw_payload and converted to str (check [10])."""
        assert silver_record["story_id"] == str(hn_raw["story_id"])

    def test_title_preserved(self, silver_record, hn_raw):
        """title preserved from raw_payload (check [11])."""
        assert silver_record["title"] == hn_raw["title"]

    def test_points_preserved_as_int(self, silver_record, hn_raw):
        """points preserved as int (check [12])."""
        assert isinstance(silver_record["points"], int)
        assert silver_record["points"] == int(hn_raw["points"])

    def test_url_preserved(self, silver_record, hn_raw):
        """url preserved from raw_payload (check [13])."""
        assert silver_record["url"] == hn_raw.get("url", "")

    def test_top_comments_preserved_as_list(self, silver_record, hn_raw):
        """top_comments preserved as list (check [14])."""
        assert isinstance(silver_record["top_comments"], list)
        assert len(silver_record["top_comments"]) == len(hn_raw["top_comments"])

    def test_story_type_preserved(self, silver_record, hn_raw):
        """story_type preserved from raw_payload (check [15])."""
        assert silver_record["story_type"] == hn_raw.get("story_type", "story")

    def test_published_at_equals_created_at(self, silver_record, hn_raw):
        """published_at == raw_payload.created_at (Algolia submission timestamp) (check [16])."""
        assert silver_record["published_at"] == hn_raw["created_at"]

    def test_content_hash_is_64_char_hex(self, silver_record):
        """content_hash is a 64-char lowercase SHA-256 hex digest (check [17])."""
        ch = silver_record["content_hash"]
        assert isinstance(ch, str)
        assert len(ch) == 64
        assert ch == ch.lower()
        assert all(c in "0123456789abcdef" for c in ch)

    def test_relevance_score_is_float_in_range(self, silver_record):
        """relevance_score is a float in [0.0, 1.0] (check [18])."""
        score = silver_record["relevance_score"]
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_high_signal_story_has_is_high_signal_true(self, silver_record):
        """'Polymarket' + 'prediction markets' in title → is_high_signal == True (check [19])."""
        assert silver_record["is_high_signal"] is True, (
            "Mock story title contains 'Polymarket' and 'prediction markets' — "
            "both are in MASTER_KEYWORD_LIST (core project domain). "
            f"Expected is_high_signal=True, got {silver_record['is_high_signal']}. "
            f"relevance_score={silver_record['relevance_score']}"
        )

    def test_sniper_keywords_is_sorted_list(self, silver_record):
        """sniper_keywords is a sorted list (check [20])."""
        kws = silver_record["sniper_keywords"]
        assert isinstance(kws, list)
        assert kws == sorted(kws)

    def test_high_signal_implies_positive_relevance_score(self, silver_record):
        """is_high_signal=True implies relevance_score > 0.0 (check [21])."""
        if silver_record["is_high_signal"]:
            assert silver_record["relevance_score"] > 0.0

    def test_fetch_mode_preserved(self, silver_record, hn_raw):
        """fetch_mode preserved from raw_payload (check [22])."""
        assert silver_record["fetch_mode"] == hn_raw.get("fetch_mode", "pulse")

    def test_bronze_ref_equals_envelope_event_id(self, silver_record, hn_envelope):
        """bronze_ref == Bronze envelope event_id (lineage link, Section 3.2) (check [23])."""
        assert silver_record["bronze_ref"] == hn_envelope["event_id"]

    def test_num_comments_preserved_as_int(self, silver_record, hn_raw):
        """num_comments preserved as int (check [33])."""
        assert isinstance(silver_record["num_comments"], int)
        assert silver_record["num_comments"] == int(hn_raw.get("num_comments", 0))

    def test_author_preserved(self, silver_record, hn_raw):
        """author preserved from raw_payload (check [34])."""
        assert silver_record["author"] == hn_raw.get("author", "")


# ==========================================================
# Gate 2 — Low-Signal Routing Tests (checks [24]–[25])
# ==========================================================

class TestHackerNewsSilverLowSignal:
    """
    Verifies that low-relevance stories still reach Silver (sniper never blocks).
    The Gold Job's is_high_signal guard controls OpenAI spend — Section 4.1A.
    """

    def test_low_signal_story_routes_to_silver_social_pulse(self):
        """Low-signal story (no keywords) still routes to SILVER_SOCIAL_PULSE (check [24])."""
        raw = {
            "story_id":    "99999999",
            "title":       "My hobby project uses a sqlite database",
            "url":         "https://example.com/hobby",
            "author":      "hobbyist",
            "points":      55,
            "num_comments": 3,
            "created_at":  "2024-04-01T10:00:00.000Z",
            "story_text":  "",
            "tags":        ["story"],
            "story_type":  "story",
            "top_comments": [
                {"comment_id": 1, "author": "user1", "text": "Cool project!",
                 "created_at": "2024-04-01T10:01:00.000Z"}
            ],
            "fetch_mode": "pulse",
        }
        topic, record = process_hackernews_message(_build_envelope(raw))
        assert topic == SILVER_SOCIAL_PULSE, (
            f"Low-signal stories must still reach Silver. Got topic='{topic}'"
        )

    def test_low_signal_story_has_is_high_signal_false(self):
        """Low-signal story has is_high_signal == False (check [25])."""
        raw = {
            "story_id":    "99999998",
            "title":       "Weekend woodworking: building a bookshelf",
            "url":         "https://example.com/woodworking",
            "author":      "woodworker99",
            "points":      52,
            "num_comments": 2,
            "created_at":  "2024-04-01T09:00:00.000Z",
            "story_text":  "",
            "tags":        ["story"],
            "story_type":  "story",
            "top_comments": [],
            "fetch_mode":  "pulse",
        }
        topic, record = process_hackernews_message(_build_envelope(raw))
        assert topic == SILVER_SOCIAL_PULSE
        assert record["is_high_signal"] is False


# ==========================================================
# Gate 2 — DLQ Tests (checks [26]–[30], [35])
# ==========================================================

class TestHackerNewsSilverDLQ:
    """
    Verifies DLQ records have the correct structure and source tags.
    """

    def _get_dlq_record(self, hn_raw: dict) -> dict:
        bad = copy.deepcopy(hn_raw)
        del bad["story_id"]
        _, record = process_hackernews_message(_build_envelope(bad))
        return record

    def test_dlq_source_topic_is_bronze_hackernews(self, hn_raw):
        """DLQ source_topic must be BRONZE_HACKERNEWS (check [26])."""
        record = self._get_dlq_record(hn_raw)
        assert record["source_topic"] == BRONZE_HACKERNEWS

    def test_dlq_preserves_original_message(self, hn_raw):
        """DLQ original_message must be a non-empty dict (check [27])."""
        record = self._get_dlq_record(hn_raw)
        assert isinstance(record.get("original_message"), dict)
        assert len(record["original_message"]) > 0

    def test_dlq_has_non_empty_validation_errors(self, hn_raw):
        """DLQ validation_errors must be a non-empty list (check [28])."""
        record = self._get_dlq_record(hn_raw)
        errors = record.get("validation_errors", [])
        assert isinstance(errors, list)
        assert len(errors) > 0

    def test_dlq_failed_stage_story_id_guard(self, hn_raw):
        """failed_stage == 'story_id_guard' when story_id is missing (check [29])."""
        bad = copy.deepcopy(hn_raw)
        del bad["story_id"]
        _, record = process_hackernews_message(_build_envelope(bad))
        assert record["failed_stage"] == "story_id_guard"

    def test_dlq_failed_stage_title_guard(self, hn_raw):
        """failed_stage == 'title_guard' when title is empty (check [30])."""
        bad = copy.deepcopy(hn_raw)
        bad["title"] = ""
        _, record = process_hackernews_message(_build_envelope(bad))
        assert record["failed_stage"] == "title_guard"

    def test_dlq_failed_layer_is_silver(self, hn_raw):
        """DLQ failed_layer == 'Silver' (check [35])."""
        record = self._get_dlq_record(hn_raw)
        assert record["failed_layer"] == "Silver"


# ==========================================================
# Gate 2 — Deduplication Tests (checks [31]–[32])
# ==========================================================

class TestHackerNewsSilverDedup:
    """
    Verifies SHA-256 deduplication correctness for HackerNews stories.
    """

    def test_content_hash_is_deterministic(self, hn_raw, hn_envelope):
        """
        Same input always produces the same content_hash (check [31]).

        Determinism is critical: multiple Silver Job instances processing the
        same story must produce identical hashes so the Social Vault dedup
        guard (exists_by_content_hash) fires correctly (Section 4.1C).
        """
        _, r1 = process_hackernews_message(hn_envelope)
        _, r2 = process_hackernews_message(hn_envelope)
        assert r1["content_hash"] == r2["content_hash"]

    def test_different_stories_produce_different_hashes(self, hn_raw):
        """Different story_id → different content_hash (check [32])."""
        raw_a = copy.deepcopy(hn_raw)
        raw_b = copy.deepcopy(hn_raw)
        raw_b["story_id"] = "99999001"

        _, r_a = process_hackernews_message(_build_envelope(raw_a))
        _, r_b = process_hackernews_message(_build_envelope(raw_b))
        assert r_a["content_hash"] != r_b["content_hash"]

    def test_hash_social_batch_matches_silver_content_hash(self, silver_record, hn_raw):
        """
        content_hash == hash_social_batch(story_id, top_comments).

        Confirms the Silver Job uses the correct dedup function (not hash_document)
        and that the implementation matches the canonical formula (Section 4.1C).
        """
        expected = hash_social_batch(
            str(hn_raw["story_id"]),
            hn_raw["top_comments"],
        )
        assert silver_record["content_hash"] == expected
