"""
Gate 2 — Logic Gate: NewsAPI Silver Processing Validation (Section 4.1).

Validates that the Silver Job's NewsAPI branch transforms and routes correctly
using mock payloads — no live Flink cluster, no Kafka broker, no NewsAPI calls.

What Gate 2 covers (Section 9.3 Gate 2):
    [1]  Valid NewsAPI article → routes to SILVER_GLOBAL_NEWS
    [2]  Invalid envelope (missing event_id) → routes to DEAD_LETTER_QUEUE
    [3]  Invalid bronze payload (missing ingestion_id) → routes to DEAD_LETTER_QUEUE
    [4]  Missing url key in raw_payload → routes to DEAD_LETTER_QUEUE
    [5]  Empty url string in raw_payload → routes to DEAD_LETTER_QUEUE
    [6]  Silver output passes validate_silver_document()
    [7]  doc_id is a non-empty UUID string
    [8]  document_hash is a 64-char lowercase SHA-256 hex digest
    [9]  canonical_event_id in Silver == Bronze envelope event_id
    [10] source_name == "newsapi"
    [11] original_url matches raw_payload.url
    [12] full_text_raw uses content when content is non-empty
    [13] full_text_raw falls back to description when content is empty
    [14] inverted_pyramid_lead == raw.description regardless of content presence
    [15] detected_entities == [] (Gold Job populates via OpenAI, Section 4.2A)
    [16] relevance_score is float in [0.0, 1.0]
    [17] High-signal article has is_high_signal == True
    [18] impact_boost == True for OPEC / crude oil article
    [19] sniper_keywords is a sorted list of matched keyword strings
    [20] Low-signal article routes to SILVER_GLOBAL_NEWS (sniper never blocks Silver)
    [21] Low-signal article has is_high_signal == False
    [22] fetch_mode is passed through from raw_payload
    [23] bronze_ref == Bronze envelope event_id (lineage link, Section 3.2)
    [24] DLQ record carries source_topic == BRONZE_NEWSAPI (Section 3.5)
    [25] DLQ record preserves original_message intact for replay (Section 3.5)
    [26] DLQ record has non-empty validation_errors list

Test strategy:
    Fixtures build full Bronze envelopes via build_bronze_message() (same utility
    the producer uses), then pass them directly to process_newsapi_message() —
    the pure dispatch function in silver_job.py. No Flink, no Kafka, no HTTP.
    Keyword Sniper and deduplication run live (pure Python, deterministic).

References:
    - Section 4.1:   Silver Job specification
    - Section 4.1A:  Keyword Sniper — gates Gold OpenAI call (not Silver emission)
    - Section 4.1C:  SHA-256 deduplication
    - Section 4.2A:  Gold Job — detected_entities populated by OpenAI
    - Section 4.4:   Mock-Driven Development
    - Section 9.3:   Triple-Gate Test Matrix — Gate 2
    - Section C.3:   Silver Full-Text Document Store schema
    - Section 3.2:   DRY — lineage tracing via bronze_ref
    - Section 3.5:   Dead-Letter Queue — never silently drop
"""

import json
import uuid
from pathlib import Path

import pytest

from config.kafka_topics import (
    BRONZE_NEWSAPI,
    DEAD_LETTER_QUEUE,
    SILVER_GLOBAL_NEWS,
)
from ingestion.newsapi_producer import SOURCE_NAME, TOP_HEADLINES_ENDPOINT
from processing.silver_job import (
    map_newsapi_article_to_silver,
    process_newsapi_message,
)
from utils.kafka_utils import build_bronze_message
from utils.validators import validate_silver_document

# ==========================================================
# Paths
# ==========================================================

MOCKS_DIR = Path(__file__).parent.parent / "mocks"
_NEWSAPI_ENDPOINT = TOP_HEADLINES_ENDPOINT + "?category=business&language=en"


# ==========================================================
# Fixtures
# ==========================================================

@pytest.fixture(scope="module")
def article_raw() -> dict:
    """
    Load the NewsAPI article mock raw payload (Section 4.4).

    The _comment key is stripped — it is documentation only and must not
    appear in the Bronze raw_payload (no producer emits it).
    """
    with open(MOCKS_DIR / "newsapi_article.json", encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


@pytest.fixture(scope="module")
def article_envelope(article_raw) -> dict:
    """Build a full Bronze envelope from the mock NewsAPI article."""
    return build_bronze_message(
        source_name=SOURCE_NAME,
        source_endpoint=_NEWSAPI_ENDPOINT,
        raw_payload=article_raw,
    )


@pytest.fixture(scope="module")
def silver_record(article_envelope) -> dict:
    """
    Run process_newsapi_message() on the mock envelope and return the Silver record.

    Fails the test immediately if routing is unexpected — downstream schema
    tests depend on this fixture producing the Silver output, not a DLQ record.
    """
    topic, record = process_newsapi_message(article_envelope)
    assert topic == SILVER_GLOBAL_NEWS, (
        f"Expected SILVER_GLOBAL_NEWS but got '{topic}'. "
        f"Silver record: {record}"
    )
    return record


# ==========================================================
# Gate 2 — Routing Tests (checks [1]–[5])
# ==========================================================

class TestNewsAPISilverRouting:
    """
    Verifies process_newsapi_message() routes to the correct Kafka topic.
    """

    def test_valid_article_routes_to_silver_global_news(self, article_envelope):
        """
        A valid NewsAPI article must route to SILVER_GLOBAL_NEWS (check [1]).

        SILVER_GLOBAL_NEWS is the only routing target for NewsAPI — there is
        no social_pulse or structured_metrics branch (Section 3.1).
        """
        topic, record = process_newsapi_message(article_envelope)
        assert topic == SILVER_GLOBAL_NEWS, (
            f"Expected SILVER_GLOBAL_NEWS, got '{topic}'"
        )
        assert record is not None

    def test_invalid_envelope_routes_to_dlq(self, article_raw):
        """
        An envelope missing event_id must route to DEAD_LETTER_QUEUE (check [2]).

        event_id is required by validate_envelope() — every Bronze message
        must carry a UUID for trace lineage (Section 7.2).
        """
        envelope = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=_NEWSAPI_ENDPOINT,
            raw_payload=article_raw,
        )
        del envelope["event_id"]
        topic, record = process_newsapi_message(envelope)
        assert topic == DEAD_LETTER_QUEUE
        assert "validation_errors" in record

    def test_invalid_bronze_payload_routes_to_dlq(self, article_raw):
        """
        An envelope with a corrupt Bronze payload (missing ingestion_id) must
        route to DEAD_LETTER_QUEUE (check [3]).
        """
        envelope = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=_NEWSAPI_ENDPOINT,
            raw_payload=article_raw,
        )
        del envelope["payload"]["ingestion_id"]
        topic, record = process_newsapi_message(envelope)
        assert topic == DEAD_LETTER_QUEUE

    def test_missing_url_routes_to_dlq(self, article_raw):
        """
        A raw_payload without a url key must route to DEAD_LETTER_QUEUE (check [4]).

        URL is the canonical dedup key for hash_document() (Section 4.1C).
        An article without a URL cannot be deduplicated and must not enter
        the Knowledge Vault.
        """
        raw_no_url = {k: v for k, v in article_raw.items() if k != "url"}
        envelope = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=_NEWSAPI_ENDPOINT,
            raw_payload=raw_no_url,
        )
        topic, record = process_newsapi_message(envelope)
        assert topic == DEAD_LETTER_QUEUE
        error_text = " ".join(record.get("validation_errors", []))
        assert "url" in error_text.lower(), (
            f"DLQ error should mention URL. Got: {record['validation_errors']}"
        )

    def test_empty_url_routes_to_dlq(self, article_raw):
        """
        A raw_payload with url="" must route to DEAD_LETTER_QUEUE (check [5]).

        An empty string URL fails the url_guard (url.strip() is falsy) for
        the same reason as a missing URL — it cannot serve as a dedup key.
        """
        raw_empty_url = dict(article_raw)
        raw_empty_url["url"] = ""
        envelope = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=_NEWSAPI_ENDPOINT,
            raw_payload=raw_empty_url,
        )
        topic, record = process_newsapi_message(envelope)
        assert topic == DEAD_LETTER_QUEUE

    def test_whitespace_url_routes_to_dlq(self, article_raw):
        """
        A url consisting only of whitespace must route to DEAD_LETTER_QUEUE.

        url.strip() is falsy for whitespace-only strings — same protection as
        empty string.
        """
        raw_ws_url = dict(article_raw)
        raw_ws_url["url"] = "   "
        envelope = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=_NEWSAPI_ENDPOINT,
            raw_payload=raw_ws_url,
        )
        topic, _ = process_newsapi_message(envelope)
        assert topic == DEAD_LETTER_QUEUE


# ==========================================================
# Gate 2 — Silver Schema Correctness (checks [6]–[23])
# ==========================================================

class TestNewsAPISilverSchema:
    """
    Verifies the Silver Full-Text Document Store output conforms to Section C.3.
    """

    def test_silver_passes_validator(self, silver_record):
        """
        The Silver output must pass validate_silver_document() — the same
        validator called inside process_newsapi_message() (check [6]).

        Running the validator again here is intentional: it proves the test
        independently verifies the schema, not just that the function didn't crash.
        """
        result = validate_silver_document(silver_record)
        assert result.is_valid, (
            f"Silver document validation failed: {result.errors}"
        )

    def test_doc_id_is_non_empty_uuid(self, silver_record):
        """
        doc_id must be a non-empty UUID string (check [7]).

        Each Silver document gets a fresh UUID at map time, providing a
        unique primary key for the Knowledge Vault write (Section C.3).
        """
        doc_id = silver_record.get("doc_id", "")
        assert isinstance(doc_id, str) and len(doc_id) > 0
        # Validate UUID4 format (must not raise)
        parsed = uuid.UUID(doc_id)
        assert str(parsed) == doc_id

    def test_document_hash_is_64_char_hex(self, silver_record):
        """
        document_hash must be a 64-character lowercase SHA-256 hex digest (check [8]).

        validate_silver_document() enforces length=64. This test additionally
        confirms character set to rule out truncation or Base64 encoding.
        """
        doc_hash = silver_record.get("document_hash", "")
        assert isinstance(doc_hash, str)
        assert len(doc_hash) == 64, (
            f"document_hash must be 64 chars, got {len(doc_hash)}"
        )
        assert all(c in "0123456789abcdef" for c in doc_hash), (
            "document_hash must be lowercase hexadecimal"
        )

    def test_canonical_event_id_matches_envelope(self, article_envelope, silver_record):
        """
        canonical_event_id must equal the Bronze envelope's event_id (check [9]).

        This is the Paper Trail link — every Silver record must trace back to
        the exact Bronze message that produced it (Section 3.2 / 7.2).
        """
        assert silver_record["canonical_event_id"] == article_envelope["event_id"]

    def test_source_name_is_newsapi(self, silver_record):
        """source_name must be 'newsapi' (check [10])."""
        assert silver_record["source_name"] == "newsapi"

    def test_original_url_matches_raw(self, article_raw, silver_record):
        """
        original_url must equal raw_payload.url verbatim (check [11]).

        No normalisation is applied at this stage — the URL is stored as-is
        so the dedup hash can be reproduced from the stored record.
        """
        assert silver_record["original_url"] == article_raw["url"]

    def test_full_text_uses_content_when_present(self, article_raw, silver_record):
        """
        full_text_raw must equal raw_payload.content when content is non-empty (check [12]).

        Why content is preferred over description: content is the full article
        body (though truncated on some tiers). The embedding captures more
        signal from full body text than from the lede sentence alone (Section C.3).
        """
        assert silver_record["full_text_raw"] == article_raw["content"]

    def test_full_text_falls_back_to_description(self, article_raw):
        """
        When content is empty, full_text_raw must equal description (check [13]).

        NewsAPI's content field is empty on free/basic plan tiers. The fallback
        prevents the Knowledge Vault from storing an empty full_text_raw, which
        would produce a useless embedding (Section C.3 content ‖ description).
        """
        raw_no_content = dict(article_raw)
        raw_no_content["content"] = ""
        envelope = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=_NEWSAPI_ENDPOINT,
            raw_payload=raw_no_content,
        )
        _, record = process_newsapi_message(envelope)
        assert record["full_text_raw"] == article_raw["description"]

    def test_inverted_pyramid_lead_always_uses_description(self, article_raw, silver_record):
        """
        inverted_pyramid_lead must equal raw_payload.description regardless of
        whether content is present (check [14]).

        Why description is always the lead: NewsAPI's description is the lede
        sentence/paragraph — the journalistic summary of the article's most
        newsworthy fact. It is structurally distinct from content (Section C.3).
        """
        assert silver_record["inverted_pyramid_lead"] == article_raw["description"]

    def test_inverted_pyramid_lead_is_description_even_when_content_absent(self, article_raw):
        """
        inverted_pyramid_lead must still equal description when content is empty.
        """
        raw_no_content = dict(article_raw)
        raw_no_content["content"] = ""
        envelope = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=_NEWSAPI_ENDPOINT,
            raw_payload=raw_no_content,
        )
        _, record = process_newsapi_message(envelope)
        assert record["inverted_pyramid_lead"] == article_raw["description"]

    def test_detected_entities_is_empty_list(self, silver_record):
        """
        detected_entities must be an empty list at Silver layer (check [15]).

        Entity extraction (actors, locations, weapon systems) is performed by
        the Gold Job's OpenAI Cognitive Metadata call (Section 4.2A).
        An empty list — not None — is the contract for 'extraction pending'.
        """
        assert silver_record["detected_entities"] == []

    def test_relevance_score_is_float_in_range(self, silver_record):
        """
        relevance_score must be a float in [0.0, 1.0] (check [16]).

        Out-of-range scores would fail validate_silver_document() — this test
        independently confirms the Keyword Sniper's capped scoring formula
        (relevance_score = min(1.0, raw_score / SCORE_SATURATION), Section 4.1A).
        """
        score = silver_record["relevance_score"]
        assert isinstance(score, float), f"relevance_score must be float, got {type(score)}"
        assert 0.0 <= score <= 1.0, f"relevance_score must be in [0.0, 1.0], got {score}"

    def test_high_signal_for_opec_article(self, silver_record):
        """
        The OPEC crude-oil article must have is_high_signal == True (check [17]).

        This article contains 'crude oil', 'opec', 'production cut' in its
        title and description — well above the DEFAULT_THRESHOLD of 0.09.
        A relevance_score of 1.0 is expected from title-weighted hits alone.
        """
        assert silver_record["is_high_signal"] is True, (
            f"OPEC article must be high-signal. "
            f"relevance_score={silver_record['relevance_score']}, "
            f"keywords={silver_record['sniper_keywords']}"
        )

    def test_impact_boost_true_for_opec_article(self, silver_record):
        """
        impact_boost must be True for the OPEC article (check [18]).

        The mock payload sets impact_boost=true (crude oil trigger, Section B.4).
        The Silver Job passes this through from the Bronze raw_payload so the
        Gold Job can apply +1 impact_level without re-evaluating the payload text.
        """
        assert silver_record["impact_boost"] is True

    def test_impact_boost_reason_is_present(self, silver_record):
        """impact_boost_reason must be a non-empty string when impact_boost is True."""
        assert isinstance(silver_record.get("impact_boost_reason"), str)
        assert len(silver_record["impact_boost_reason"]) > 0

    def test_sniper_keywords_is_sorted_list(self, silver_record):
        """
        sniper_keywords must be a sorted list of matched keyword strings (check [19]).

        Sorted order is required by snipe() (Section 4.1A) for deterministic
        Silver records — the same article must always produce the same keyword list
        regardless of frozenset iteration order.
        """
        keywords = silver_record["sniper_keywords"]
        assert isinstance(keywords, list)
        assert keywords == sorted(keywords), (
            f"sniper_keywords is not sorted: {keywords}"
        )

    def test_sniper_keywords_non_empty_for_opec_article(self, silver_record):
        """The OPEC article must match at least one keyword from MASTER_KEYWORD_LIST."""
        assert len(silver_record["sniper_keywords"]) > 0

    def test_sniper_keywords_contains_crude_oil(self, silver_record):
        """
        'crude oil' must appear in sniper_keywords for the OPEC mock article.

        Both title and description contain 'crude oil', which is a Master Keyword
        (Section 4.1A energy/commodities category). This confirms title-weight
        scoring is actually running.
        """
        assert "crude oil" in silver_record["sniper_keywords"]

    def test_fetch_mode_passed_through(self, article_raw, silver_record):
        """
        fetch_mode must equal raw_payload.fetch_mode (check [22]).

        The mock article has fetch_mode='pulse'. knowledge_vault.py uses this
        field to partition backfill from pulse inserts for observability.
        """
        assert silver_record["fetch_mode"] == article_raw["fetch_mode"]

    def test_bronze_ref_equals_envelope_event_id(self, article_envelope, silver_record):
        """
        bronze_ref must equal the Bronze envelope's event_id (check [23]).

        This is the lineage link — knowledge_vault.py stores bronze_ref so
        a failed Gold Job run can be replayed from the exact Bronze offset
        (Section 3.2 DRY — trace lineage).
        """
        assert silver_record["bronze_ref"] == article_envelope["event_id"]

    def test_publish_date_matches_raw(self, article_raw, silver_record):
        """publish_date must pass through raw_payload.published_at unchanged."""
        assert silver_record["publish_date"] == article_raw["published_at"]

    def test_title_matches_raw(self, article_raw, silver_record):
        """title must match raw_payload.title (consumed by Knowledge Vault index)."""
        assert silver_record["title"] == article_raw["title"]

    def test_source_display_name_matches_raw_source_name(self, article_raw, silver_record):
        """source_display_name must equal raw_payload.source.name."""
        assert silver_record["source_display_name"] == article_raw["source"]["name"]

    def test_category_matches_raw(self, article_raw, silver_record):
        """category must pass through from raw_payload.category."""
        assert silver_record["category"] == article_raw["category"]


# ==========================================================
# Gate 2 — Sniper Routing: Low-Signal Article (checks [20]–[21])
# ==========================================================

class TestLowSignalArticleRouting:
    """
    Verifies that a low-signal article (no keyword matches) is still emitted
    to SILVER_GLOBAL_NEWS — the Keyword Sniper gates OpenAI enrichment in the
    Gold Job, not Silver emission (Section 4.1A, spec comment in process_newsapi_message).
    """

    @pytest.fixture(scope="class")
    def low_signal_envelope(self):
        """
        Build a Bronze envelope for a generic sports article that matches
        zero keywords from MASTER_KEYWORD_LIST.
        """
        raw = {
            "article_id": "https://www.bbc.com/sport/football/premier-league-2026-03-31",
            "title": "Arsenal beat Chelsea 3-1 in Premier League clash",
            "description": "Arsenal's stunning performance at Emirates Stadium delights fans.",
            "url": "https://www.bbc.com/sport/football/premier-league-2026-03-31",
            "url_to_image": None,
            "published_at": "2026-03-31T18:00:00Z",
            "content": "Arsenal scored three goals in the first half against Chelsea.",
            "author": "Sports Desk",
            "source": {"id": "bbc-news", "name": "BBC News"},
            "category": "general",
            "fetch_mode": "pulse",
            "impact_boost": False,
            "impact_boost_reason": "",
        }
        return build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=_NEWSAPI_ENDPOINT,
            raw_payload=raw,
        )

    def test_low_signal_still_routes_to_silver_global_news(self, low_signal_envelope):
        """
        A low-signal (sports) article must route to SILVER_GLOBAL_NEWS (check [20]).

        The Keyword Sniper score gates Gold Job OpenAI enrichment — it NEVER
        blocks Silver emission. All valid articles enter the Knowledge Vault
        (Section 4.1A: 'ALL valid articles emitted to SILVER_GLOBAL_NEWS').
        """
        topic, record = process_newsapi_message(low_signal_envelope)
        assert topic == SILVER_GLOBAL_NEWS, (
            f"Low-signal article should still reach SILVER_GLOBAL_NEWS. "
            f"Got '{topic}'"
        )

    def test_low_signal_has_is_high_signal_false(self, low_signal_envelope):
        """
        Low-signal article must have is_high_signal == False in the Silver record
        (check [21]).

        The Gold Job reads this flag to decide whether to spend OpenAI tokens
        on enrichment (Section 4.1A). is_high_signal=False means the article
        will be stored in the Knowledge Vault but will not receive embeddings.
        """
        _, record = process_newsapi_message(low_signal_envelope)
        assert record["is_high_signal"] is False

    def test_low_signal_relevance_score_below_threshold(self, low_signal_envelope):
        """Low-signal article must have relevance_score < DEFAULT_THRESHOLD (0.09)."""
        _, record = process_newsapi_message(low_signal_envelope)
        assert record["relevance_score"] < 0.09, (
            f"Sports article relevance_score should be below threshold. "
            f"Got {record['relevance_score']}, keywords={record.get('sniper_keywords')}"
        )

    def test_low_signal_passes_silver_validator(self, low_signal_envelope):
        """Low-signal article must still pass validate_silver_document()."""
        _, record = process_newsapi_message(low_signal_envelope)
        result = validate_silver_document(record)
        assert result.is_valid, (
            f"Low-signal Silver document must pass validator: {result.errors}"
        )


# ==========================================================
# Gate 2 — DLQ Correctness (checks [24]–[26])
# ==========================================================

class TestNewsAPIDLQCorrectness:
    """
    Verifies DLQ records are correctly formed when the NewsAPI Silver branch
    rejects a message (Section 3.5 Gate 2 checks 24-26).
    """

    def test_dlq_source_topic_is_bronze_newsapi(self, article_raw):
        """
        DLQ records from the NewsAPI branch must carry source_topic=BRONZE_NEWSAPI
        (check [24]).

        DLQ operators filter by source_topic to route replay to the correct
        producer/offset (Section 3.5). A wrong source_topic (e.g., BRONZE_FRED)
        would route the replay to the wrong consumer group.
        """
        envelope = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=_NEWSAPI_ENDPOINT,
            raw_payload=article_raw,
        )
        del envelope["event_id"]   # force DLQ via envelope validation
        topic, dlq_record = process_newsapi_message(envelope)

        assert topic == DEAD_LETTER_QUEUE
        assert dlq_record["source_topic"] == BRONZE_NEWSAPI, (
            f"DLQ source_topic must be BRONZE_NEWSAPI ('{BRONZE_NEWSAPI}'), "
            f"got '{dlq_record['source_topic']}'"
        )

    def test_dlq_source_topic_not_bronze_fred(self, article_raw):
        """
        DLQ from NewsAPI branch must NOT carry BRONZE_FRED as source_topic.

        Regression guard — _newsapi_dlq_record() must use BRONZE_NEWSAPI,
        not the _fred_dlq_record() constant (Section 3.5).
        """
        from config.kafka_topics import BRONZE_FRED
        envelope = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=_NEWSAPI_ENDPOINT,
            raw_payload=article_raw,
        )
        del envelope["event_id"]
        _, dlq_record = process_newsapi_message(envelope)
        assert dlq_record["source_topic"] != BRONZE_FRED

    def test_dlq_record_preserves_original_message(self, article_raw):
        """
        DLQ record must include the original_message for replay (check [25]).

        The corrupted envelope is stored as-is — operators must be able to
        inspect and replay once the schema issue is resolved (Section 3.5).
        """
        envelope = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=_NEWSAPI_ENDPOINT,
            raw_payload=article_raw,
        )
        del envelope["event_id"]
        _, dlq_record = process_newsapi_message(envelope)

        assert "original_message" in dlq_record
        assert isinstance(dlq_record["original_message"], dict)

    def test_dlq_record_has_validation_errors(self, article_raw):
        """
        DLQ record must include a non-empty validation_errors list (check [26]).

        An empty errors list would make it impossible to diagnose why the
        message was rejected (Section 3.5 DLQ envelope format).
        """
        envelope = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=_NEWSAPI_ENDPOINT,
            raw_payload=article_raw,
        )
        del envelope["trace_id"]   # corrupt via missing trace_id (distinct from event_id)
        _, dlq_record = process_newsapi_message(envelope)

        errors = dlq_record.get("validation_errors", [])
        assert isinstance(errors, list)
        assert len(errors) > 0, "DLQ record must contain at least one validation error"

    def test_dlq_failed_layer_is_silver(self, article_raw):
        """DLQ record must identify failed_layer='Silver' (Section 3.5)."""
        envelope = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=_NEWSAPI_ENDPOINT,
            raw_payload=article_raw,
        )
        del envelope["event_id"]
        _, dlq_record = process_newsapi_message(envelope)
        assert dlq_record["failed_layer"] == "Silver"

    def test_url_guard_dlq_identifies_stage(self, article_raw):
        """
        DLQ record for a missing-URL failure must identify failed_stage='url_guard'.

        Named stages in DLQ records help operators pinpoint which check failed
        without re-reading the Silver Job code (Section 3.5).
        """
        raw_no_url = {k: v for k, v in article_raw.items() if k != "url"}
        envelope = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=_NEWSAPI_ENDPOINT,
            raw_payload=raw_no_url,
        )
        _, dlq_record = process_newsapi_message(envelope)
        assert dlq_record["failed_stage"] == "url_guard"


# ==========================================================
# Gate 2 — Document Hash Determinism
# ==========================================================

class TestDocumentHashDeterminism:
    """
    Verifies that document_hash is stable across repeated calls and correctly
    differentiates articles (Section 4.1C deduplication).
    """

    def test_same_article_same_hash(self, article_envelope):
        """
        Calling process_newsapi_message() twice on the same envelope must
        produce the same document_hash.

        The Knowledge Vault uses this hash for ON CONFLICT dedup — a
        non-deterministic hash would suppress legitimate updates while
        allowing true duplicates through (Section 4.1C).
        """
        _, r1 = process_newsapi_message(article_envelope)
        _, r2 = process_newsapi_message(article_envelope)
        assert r1["document_hash"] == r2["document_hash"]

    def test_different_url_different_hash(self, article_raw):
        """
        Same article text on a different URL must produce a different hash.

        NewsAPI can return the same Reuters story at different canonical URLs
        (e.g., via different category endpoints). Both entries must enter the
        Knowledge Vault independently — URL differentiation enables this.
        """
        raw_alt_url = dict(article_raw)
        raw_alt_url["url"] = "https://apnews.com/article/opec-crude-oil-q3-2026"

        env_original = build_bronze_message(
            source_name=SOURCE_NAME, source_endpoint=_NEWSAPI_ENDPOINT,
            raw_payload=article_raw,
        )
        env_alt = build_bronze_message(
            source_name=SOURCE_NAME, source_endpoint=_NEWSAPI_ENDPOINT,
            raw_payload=raw_alt_url,
        )
        _, r_original = process_newsapi_message(env_original)
        _, r_alt      = process_newsapi_message(env_alt)

        assert r_original["document_hash"] != r_alt["document_hash"], (
            "Different URLs must produce different document hashes"
        )

    def test_doc_id_unique_per_call(self, article_envelope):
        """
        doc_id must be a fresh UUID on each call — the Knowledge Vault write
        uses doc_id as the primary key, not document_hash.

        document_hash is the dedup guard (skip if already present); doc_id
        uniquely identifies this specific Silver record instance.
        """
        _, r1 = process_newsapi_message(article_envelope)
        _, r2 = process_newsapi_message(article_envelope)
        assert r1["doc_id"] != r2["doc_id"], (
            "doc_id must be a fresh UUID each call — it is a primary key, "
            "not a dedup key"
        )
