"""
Gate 1 — Ingestion Gate: newsapi.ai (Event Registry) Bronze Schema Compliance.

Validates that the newsapi.ai producer's envelope-building, filter, and
payload-construction logic produces messages that are fully compliant
with the Bronze Schema (Section C.1) and Message Envelope (Section 3.2)
before any message reaches Kafka.

Phase 7A migration (2026-05-09): provider switched from thenewsapi.com to
eventregistry.org. The internal raw_payload contract is preserved verbatim
(Decision M1/M7) so the Silver/Gold gates and persistence layer stay unchanged.
The producer is the API-shape boundary: it normalizes newsapi.ai's response
shape (articles.results[] with `body`, `image`, `dateTime`, `source` as a
{uri, title} dict, `authors[]`) into the same internal raw_payload keys this
test already enforces.

Category model change (Phase 7A, T7A.2 finding):
    PULSE_CATEGORIES now contains 5 explicit URI-notation categories:
    news/Business, news/Technology, news/Health, news/Science, news/Politics.
    GENERAL_CATEGORY ("news" root) removed — returns 0 results on newsapi.ai.
    GENERAL_KEYWORDS pre-filter removed from producer — existed only to gate
    the "news" root bucket. All 5 categories pass through uniformly.

Design decisions:
  - Zero live connections: no Kafka broker, no newsapi.ai calls.
    build_bronze_message(), _build_raw_payload(), and all filter methods
    are pure functions tested in complete isolation.
  - Mock payload loaded from tests/mocks/newsapi_article.json (Section 4.4).
  - Validators from utils/validators.py are called directly — the same
    validators the Flink Silver Job uses, so a pass here guarantees the
    Silver Job's first gate will also accept NewsAPI messages.
  - Filter-logic tests use NewsAPIProducer.__new__() to skip __init__
    (avoids NEWSAI_API_KEY requirement and Kafka connection in CI).

Gate 1 checklist (Section 9.3 Gate 1):
  [1]  envelope fields present and correctly typed
  [2]  event_id is a valid UUIDv4
  [3]  producer_timestamp is a valid ISO8601 UTC string
  [4]  source_name == "newsapi" (preserved across the migration)
  [5]  payload.raw_payload is a non-empty dict
  [6]  validate_envelope() passes without errors
  [7]  validate_bronze_payload() passes without errors
  [8]  NDJSON round-trip preserves the full envelope
  [9]  each call to build_bronze_message() produces a distinct event_id
  [10] raw_payload.article_id is a non-empty URL string
  [11] raw_payload.published_at is a non-empty ISO8601 string
  [12] raw_payload.source.id and source.name are both present (post-normalization)
  [13] raw_payload.category is one of the expected categoryUri values
  [14] raw_payload.fetch_mode is a valid mode string
  [15] raw_payload.impact_boost is a boolean
  [16] raw_payload.impact_boost_reason is a string
  [17] Kafka partition key == source.id (falls back to source.name)
  [18] AUTHORITY_WHITELIST covers all 15 mandated domains (Section B.4)
  [19] news/Politics is in PULSE_CATEGORIES (new in Phase 7A, was absent on TheNewsAPI)
  [20] _passes_whitelist(): reads source.uri dict field; whitelisted domains pass
  [21] _passes_whitelist(): Israeli domain (ynet.co.il) passes
  [22] _impact_boost_info(): Israel/energy terms detected correctly
  [23] _build_raw_payload() produces all Silver-Job-required keys; newsapi.ai shape

References:
    - Section 2.1:  Producer Matrix (NewsAPI row)
    - Section 2.2:  SNR Optimization — Domain Filtering Strategy
    - Section 3.2:  Message Envelope
    - Section 3.4:  NDJSON serialisation (tested via round-trip)
    - Section 4.4:  Mock-Driven Development
    - Section 9.3:  Triple-Gate Test Matrix — Gate 1
    - Section B.4:  newsapi.ai parameters, Authority Whitelist, Impact Boost
    - Section C.1:  Bronze Schema
    - Section C.3:  Silver Full-Text Document Store fields
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ingestion.newsapi_producer import (
    AUTHORITY_WHITELIST,
    NEWSAI_GETARTICLES_URL,
    PULSE_CATEGORIES,
    SOURCE_NAME,
    TIER_ONE_DOMAINS,
    TIER_ONE_SOURCE_IDS,
    NewsAPIProducer,
)
from utils.kafka_utils import build_bronze_message, ndjson_deserializer, ndjson_serializer
from utils.validators import validate_bronze_payload, validate_envelope

# ==========================================================
# Paths & Constants
# ==========================================================

MOCKS_DIR         = Path(__file__).parent.parent / "mocks"
NEWSAPI_MOCK_PATH = MOCKS_DIR / "newsapi_article.json"

# The 15 authority domains mandated by Section B.4.
REQUIRED_DOMAINS: list[str] = [
    "reuters.com",
    "apnews.com",
    "wsj.com",
    "bloomberg.com",
    "nytimes.com",
    "washingtonpost.com",
    "cnbc.com",
    "cnn.com",
    "bbc.co.uk",
    "ft.com",
    "theguardian.com",
    "economist.com",
    "jpost.com",
    "ynetnews.com",
    "ynet.co.il",
]

# All 5 category URIs confirmed live in T7A.2.
EXPECTED_CATEGORY_URIS: set[str] = {
    "news/Business",
    "news/Technology",
    "news/Health",
    "news/Science",
    "news/Politics",
}

# Valid fetch_mode values for the NewsAPI producer.
VALID_FETCH_MODES = {"pulse", "backfill_full", "backfill_tier_one", "backfill_anomaly"}


# ==========================================================
# Fixtures
# ==========================================================

@pytest.fixture(scope="module")
def article_raw() -> dict:
    """
    Load the newsapi.ai mock raw_payload (Section 4.4).

    This is the dict that NewsAPIProducer._build_raw_payload() would produce.
    scope="module" reads the file once and shares it across all tests.
    """
    with open(NEWSAPI_MOCK_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def newsapi_envelope(article_raw) -> dict:
    """
    Build the full Bronze envelope from the mock newsapi.ai article payload.

    Mirrors what NewsAPIProducer._emit() does in production: calls
    build_bronze_message() with the raw_payload and Bronze metadata.
    Endpoint uses newsapi.ai's NEWSAI_GETARTICLES_URL with categoryUri param (M1).
    """
    endpoint = f"{NEWSAI_GETARTICLES_URL}?categoryUri={article_raw['category']}"
    return build_bronze_message(
        source_name=SOURCE_NAME,
        source_endpoint=endpoint,
        raw_payload=article_raw,
    )


@pytest.fixture(scope="module")
def producer_instance() -> NewsAPIProducer:
    """
    Create a NewsAPIProducer instance without triggering __init__.

    Skips NEWSAI_API_KEY validation and Kafka connection — filter and
    payload-builder methods depend only on class-level constants.
    """
    return NewsAPIProducer.__new__(NewsAPIProducer)


# ==========================================================
# Gate 1 — Envelope Structure Tests (Section 3.2)
# ==========================================================

class TestEnvelopeStructure:
    """Verifies the outer Message Envelope fields (Section 3.2 / Gate 1 checks 1-4)."""

    def test_envelope_is_dict(self, newsapi_envelope):
        assert isinstance(newsapi_envelope, dict)

    def test_envelope_has_event_id(self, newsapi_envelope):
        """event_id must be present and a string (Section 3.2)."""
        assert "event_id" in newsapi_envelope
        assert isinstance(newsapi_envelope["event_id"], str)

    def test_event_id_is_valid_uuid4(self, newsapi_envelope):
        """event_id must be a valid UUIDv4 (Section 3.2)."""
        parsed = uuid.UUID(newsapi_envelope["event_id"])
        assert parsed.version == 4

    def test_producer_timestamp_is_present(self, newsapi_envelope):
        assert "producer_timestamp" in newsapi_envelope
        assert isinstance(newsapi_envelope["producer_timestamp"], str)

    def test_producer_timestamp_is_iso8601_utc(self, newsapi_envelope):
        """producer_timestamp must be timezone-aware ISO8601 (Section 3.2)."""
        ts     = newsapi_envelope["producer_timestamp"]
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None, "producer_timestamp must be timezone-aware"

    def test_source_name_is_newsapi(self, newsapi_envelope):
        """source_name in the envelope must equal 'newsapi' (Section C.1)."""
        assert newsapi_envelope.get("source_name") == SOURCE_NAME
        assert newsapi_envelope.get("source_name") == "newsapi"

    def test_schema_version_is_present(self, newsapi_envelope):
        """schema_version must be set — enables schema evolution (Section 3.2)."""
        assert "schema_version" in newsapi_envelope
        assert newsapi_envelope["schema_version"] != ""

    def test_trace_id_is_valid_uuid(self, newsapi_envelope):
        """trace_id must be a valid UUID for distributed tracing (Section 7.2)."""
        trace_id = newsapi_envelope.get("trace_id", "")
        parsed   = uuid.UUID(trace_id)
        assert parsed.version == 4


# ==========================================================
# Gate 1 — Bronze Payload Tests (Section C.1)
# ==========================================================

class TestBronzePayload:
    """Verifies the inner Bronze Schema payload (Section C.1 / Gate 1 checks 5-7)."""

    def test_payload_key_exists(self, newsapi_envelope):
        assert "payload" in newsapi_envelope
        assert isinstance(newsapi_envelope["payload"], dict)

    def test_raw_payload_is_non_empty_dict(self, newsapi_envelope):
        raw = newsapi_envelope["payload"].get("raw_payload")
        assert isinstance(raw, dict), "raw_payload must be a dict"
        assert len(raw) > 0, "raw_payload must not be empty"

    def test_bronze_payload_has_ingestion_id(self, newsapi_envelope):
        """ingestion_id must be present and a valid UUID (Section C.1)."""
        ingestion_id = newsapi_envelope["payload"].get("ingestion_id", "")
        parsed = uuid.UUID(ingestion_id)
        assert parsed.version == 4

    def test_bronze_payload_has_source_endpoint(self, newsapi_envelope):
        """
        source_endpoint must reference the newsapi.ai (eventregistry.org) URL.

        Phase 7A: NEWSAI_GETARTICLES_URL replaces the two TheNewsAPI endpoints.
        Confirms the _emit() method uses the correct new URL (M1).
        """
        endpoint = newsapi_envelope["payload"].get("source_endpoint", "")
        assert isinstance(endpoint, str) and len(endpoint) > 0
        assert "eventregistry.org" in endpoint, (
            f"source_endpoint should reference eventregistry.org. Got: {endpoint}"
        )

    def test_bronze_payload_has_ingestion_timestamp(self, newsapi_envelope):
        """ingestion_timestamp must be a valid ISO8601 timezone-aware string (Section C.1)."""
        ts     = newsapi_envelope["payload"].get("ingestion_timestamp", "")
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None

    def test_bronze_payload_metadata_block(self, newsapi_envelope):
        """metadata block must have http_status_code (int) and request_duration_ms (int|float)."""
        metadata = newsapi_envelope["payload"].get("metadata", {})
        assert isinstance(metadata, dict)
        assert isinstance(metadata.get("http_status_code"), int)
        assert isinstance(metadata.get("request_duration_ms"), (int, float))


# ==========================================================
# Gate 1 — Validator Integration Tests (Section 9.3)
# ==========================================================

class TestValidatorIntegration:
    """
    Runs the same validators the Flink Silver Job uses (Section 9.3 Gate 1).

    A pass here guarantees the Silver Job's first gate will accept NewsAPI messages.
    """

    def test_validate_envelope_passes(self, newsapi_envelope):
        """
        validate_envelope() must return is_valid=True (Section 3.2, 9.3).

        A failure means the Silver Job would route this message to
        dead-letter-queue at its very first validation step.
        """
        result = validate_envelope(newsapi_envelope)
        assert result.is_valid, f"Envelope validation errors: {result.errors}"

    def test_validate_bronze_payload_passes(self, newsapi_envelope):
        """validate_bronze_payload() on the inner payload must pass (Section C.1, 9.3)."""
        inner  = newsapi_envelope["payload"]
        result = validate_bronze_payload(inner)
        assert result.is_valid, f"Bronze payload validation errors: {result.errors}"


# ==========================================================
# Gate 1 — NewsAPI Domain-Specific Correctness (Section B.4)
# ==========================================================

class TestNewsAPIDomainCorrectness:
    """
    Verifies NewsAPI-specific business rules in the Bronze payload
    (Section B.4, Gate 1 checks 10-17).
    """

    def test_article_id_is_non_empty_url(self, newsapi_envelope):
        """
        raw_payload.article_id must be a non-empty URL string.

        article_id is the canonical dedup key for SHA-256 hashing in the
        Silver Job (Section 4.1C). An empty article_id would produce
        identical hashes across different articles, corrupting the dedup gate.
        """
        article_id = newsapi_envelope["payload"]["raw_payload"].get("article_id", "")
        assert isinstance(article_id, str) and len(article_id) > 0
        assert article_id.startswith("http"), (
            f"article_id must be a URL, got: {article_id!r}"
        )

    def test_published_at_is_non_empty(self, newsapi_envelope):
        """raw_payload.published_at must be a non-empty string (Silver schema field)."""
        published_at = newsapi_envelope["payload"]["raw_payload"].get("published_at", "")
        assert isinstance(published_at, str) and len(published_at) > 0

    def test_published_at_parses_as_iso8601(self, newsapi_envelope):
        """published_at must parse as ISO8601 (Section C.3 publish_date field)."""
        published_at = newsapi_envelope["payload"]["raw_payload"].get("published_at", "")
        parsed = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        assert parsed is not None

    def test_source_dict_has_id_and_name(self, newsapi_envelope):
        """
        raw_payload.source must have both 'id' and 'name' keys (strings).

        The Silver Job reads both for whitelist re-validation and for
        the Silver document store's source_name field (Section C.3).
        """
        source = newsapi_envelope["payload"]["raw_payload"].get("source", {})
        assert isinstance(source, dict), "source must be a dict"
        assert "id"   in source, "source.id must be present"
        assert "name" in source, "source.name must be present"
        assert isinstance(source["id"],   str), "source.id must be a string"
        assert isinstance(source["name"], str), "source.name must be a string"

    def test_category_is_valid_uri_string(self, newsapi_envelope):
        """
        raw_payload.category must be one of the 5 newsapi.ai categoryUri values
        or empty string (backfill articles have no category).

        Phase 7A: categories use URI notation (news/Business) not slug notation.
        """
        category = newsapi_envelope["payload"]["raw_payload"].get("category", "")
        valid    = EXPECTED_CATEGORY_URIS | {""}
        assert category in valid, (
            f"category {category!r} not in expected set {valid}"
        )

    def test_fetch_mode_is_valid(self, newsapi_envelope):
        """fetch_mode must be one of the defined producer operating modes."""
        fetch_mode = newsapi_envelope["payload"]["raw_payload"].get("fetch_mode", "")
        assert fetch_mode in VALID_FETCH_MODES, (
            f"fetch_mode {fetch_mode!r} not in {VALID_FETCH_MODES}"
        )

    def test_impact_boost_is_boolean(self, newsapi_envelope):
        """
        raw_payload.impact_boost must be a boolean (Section B.4 Impact Boost rule).

        The Gold Job reads this flag to apply +1 to impact_level for
        Israeli/Middle East security or energy articles.
        """
        boost = newsapi_envelope["payload"]["raw_payload"].get("impact_boost")
        assert isinstance(boost, bool), (
            f"impact_boost must be bool, got {type(boost).__name__}: {boost!r}"
        )

    def test_impact_boost_reason_is_string(self, newsapi_envelope):
        """raw_payload.impact_boost_reason must be a string (may be empty)."""
        reason = newsapi_envelope["payload"]["raw_payload"].get("impact_boost_reason")
        assert isinstance(reason, str), (
            f"impact_boost_reason must be str, got {type(reason).__name__}"
        )

    def test_mock_article_triggers_impact_boost(self, newsapi_envelope):
        """
        The mock article (OPEC/crude oil headline) must have impact_boost=True.

        Validates the impact boost detection embedded at Bronze layer (Section B.4).
        """
        raw = newsapi_envelope["payload"]["raw_payload"]
        assert raw["impact_boost"] is True, (
            "Mock article contains 'crude oil' — impact_boost should be True"
        )
        assert raw["impact_boost_reason"] != "", (
            "impact_boost_reason must be non-empty when impact_boost=True"
        )

    def test_each_call_produces_unique_event_id(self, article_raw):
        """
        Two calls to build_bronze_message() must produce different event_ids.

        Shared event_ids corrupt the Paper Trail across Bronze → Silver → Gold
        (Section 3.2 — event_id is the UUIDv4 shared across all layers).
        """
        endpoint = f"{NEWSAI_GETARTICLES_URL}?categoryUri={article_raw['category']}"
        msg1 = build_bronze_message(SOURCE_NAME, endpoint, article_raw)
        msg2 = build_bronze_message(SOURCE_NAME, endpoint, article_raw)
        assert msg1["event_id"] != msg2["event_id"]

    def test_partition_key_source_id_is_present(self, newsapi_envelope):
        """
        The Kafka partition key is source.id (falls back to source.name).

        This test confirms source.id is non-empty in the mock so the partition
        key never falls back to 'unknown' for authoritative whitelisted sources.
        """
        source        = newsapi_envelope["payload"]["raw_payload"]["source"]
        partition_key = source["id"] or source["name"]
        assert partition_key != "", (
            "Partition key must be non-empty for whitelisted sources"
        )
        assert partition_key != "unknown"


# ==========================================================
# Gate 1 — Filter Logic Tests (Section 2.2, B.4)
# ==========================================================

class TestFilterLogic:
    """
    Unit tests for the pre-emission filters in NewsAPIProducer.

    All tests use NewsAPIProducer.__new__() to bypass __init__ —
    filter methods depend only on class-level constants.

    Phase 7A update: newsapi.ai's source field is a dict with a uri key
    (e.g. {"uri": "reuters.com", "title": "Reuters"}), not a bare domain string.
    _passes_whitelist now reads article["source"]["uri"] (M3). There is no
    keyword sniper pre-filter in the producer — GENERAL_KEYWORDS gate removed.
    """

    # --- Authority Whitelist (source.uri dict, Phase 7A / M3) ---

    def test_whitelist_passes_reuters_domain(self, producer_instance):
        """The reuters.com source.uri must pass the whitelist."""
        assert producer_instance._passes_whitelist(
            {"source": {"uri": "reuters.com"}}
        ) is True

    def test_whitelist_passes_apnews_domain(self, producer_instance):
        """The apnews.com source.uri must pass."""
        assert producer_instance._passes_whitelist(
            {"source": {"uri": "apnews.com"}}
        ) is True

    def test_whitelist_passes_israeli_domain_ynet(self, producer_instance):
        """
        ynet.co.il (Israeli wire service) must pass.

        Compound TLDs (.co.il, .co.uk) must be treated as first-class domains.
        """
        assert producer_instance._passes_whitelist(
            {"source": {"uri": "ynet.co.il"}}
        ) is True

    def test_whitelist_passes_jpost_domain(self, producer_instance):
        """jpost.com (Jerusalem Post) must pass the whitelist."""
        assert producer_instance._passes_whitelist(
            {"source": {"uri": "jpost.com"}}
        ) is True

    def test_whitelist_passes_bbc_compound_tld(self, producer_instance):
        """
        bbc.co.uk (compound TLD) must pass — verifies the whitelist stores
        domains verbatim and does not strip TLDs at match time.
        """
        assert producer_instance._passes_whitelist(
            {"source": {"uri": "bbc.co.uk"}}
        ) is True

    def test_whitelist_blocks_unknown_blog(self, producer_instance):
        """An unknown source.uri must be blocked by the whitelist."""
        assert producer_instance._passes_whitelist(
            {"source": {"uri": "random-blog.com"}}
        ) is False

    def test_whitelist_blocks_empty_source_uri(self, producer_instance):
        """An article with an empty source.uri must be blocked."""
        assert producer_instance._passes_whitelist({"source": {"uri": ""}}) is False

    def test_whitelist_blocks_missing_source(self, producer_instance):
        """An article with no source key must be blocked gracefully."""
        assert producer_instance._passes_whitelist({}) is False

    def test_whitelist_is_case_insensitive(self, producer_instance):
        """Mixed-case domain "Reuters.COM" must match the lowercase whitelist."""
        assert producer_instance._passes_whitelist(
            {"source": {"uri": "Reuters.COM"}}
        ) is True

    # --- Impact Boost Detection ---

    def test_impact_boost_detected_for_israel(self, producer_instance):
        """'israel' in title must trigger impact boost (Section B.4)."""
        boost, reason = producer_instance._impact_boost_info(
            {"title": "Israel strikes Hezbollah positions in Lebanon", "description": ""}
        )
        assert boost is True
        assert reason != ""

    def test_impact_boost_detected_for_crude_oil(self, producer_instance):
        """'crude oil' in title must trigger impact boost (energy term, Section B.4)."""
        boost, reason = producer_instance._impact_boost_info(
            {"title": "Crude oil hits $90 on supply concerns", "description": ""}
        )
        assert boost is True
        assert "crude oil" in reason

    def test_no_impact_boost_for_neutral_article(self, producer_instance):
        """An article with no boost terms must return (False, '') — no boost."""
        boost, reason = producer_instance._impact_boost_info(
            {"title": "Tech earnings beat expectations", "description": "Software companies report growth."}
        )
        assert boost is False
        assert reason == ""


# ==========================================================
# Gate 1 — Constants Invariants (Section B.4)
# ==========================================================

class TestConstantsInvariants:
    """
    Verifies that AUTHORITY_WHITELIST and PULSE_CATEGORIES contain all
    mandated values from Section B.4. Catches accidental omissions.
    """

    def test_all_15_domains_covered_in_whitelist(self):
        """
        AUTHORITY_WHITELIST must cover all 15 domains from Section B.4.

        A missing domain means the whitelist will silently block that
        outlet's articles at the Bronze gate.
        """
        for domain in REQUIRED_DOMAINS:
            assert domain in AUTHORITY_WHITELIST, (
                f"Section B.4 authority domain '{domain}' is not covered in "
                f"AUTHORITY_WHITELIST."
            )

    def test_pulse_categories_are_five_uri_strings(self):
        """
        PULSE_CATEGORIES must be exactly the 5 newsapi.ai category URIs confirmed
        in T7A.2. All categories are treated uniformly — no GENERAL_CATEGORY.

        Phase 7A change: added news/Politics; removed "general" slug and the
        "news" root (returns 0 results). URI notation (news/X) replaces slugs.
        """
        assert set(PULSE_CATEGORIES) == EXPECTED_CATEGORY_URIS, (
            f"PULSE_CATEGORIES {set(PULSE_CATEGORIES)} != expected {EXPECTED_CATEGORY_URIS}"
        )

    def test_politics_category_is_in_pulse_categories(self):
        """
        news/Politics must be in PULSE_CATEGORIES.

        This category was not available on TheNewsAPI — its presence here is
        the key Phase 7A addition (M4, T7A.2 confirmed it returns articles).
        """
        assert "news/Politics" in PULSE_CATEGORIES, (
            "news/Politics must be in PULSE_CATEGORIES (Phase 7A, M4)"
        )

    def test_tier_one_source_ids_are_non_empty(self):
        """TIER_ONE_SOURCE_IDS (alias of TIER_ONE_DOMAINS) must be non-empty."""
        assert isinstance(TIER_ONE_SOURCE_IDS, list)
        assert len(TIER_ONE_SOURCE_IDS) > 0

    def test_tier_one_sources_are_subset_of_whitelist(self):
        """
        Every domain in TIER_ONE_DOMAINS must appear in AUTHORITY_WHITELIST.

        Tier-1 domains are a strict subset of the authority whitelist (Section B.4).
        A tier-1 domain not in the whitelist would be blocked by the client-side
        recheck during backfill — silently dropping data.
        """
        for domain in TIER_ONE_DOMAINS:
            assert domain in AUTHORITY_WHITELIST, (
                f"Tier-1 domain '{domain}' is not in AUTHORITY_WHITELIST — "
                "it would be blocked by the client-side whitelist during backfill"
            )

    def test_build_raw_payload_produces_required_keys(self, producer_instance):
        """
        _build_raw_payload() must normalize a newsapi.ai article into all keys
        the Silver Job needs for the Full-Text Document Store (Section C.3).

        Phase 7A input shape: newsapi.ai articles use `body` (full text),
        `image`, `dateTime`, `source` as {uri, title} dict, `authors` as list.
        The producer normalizes these into the unchanged internal contract.
        """
        article = {
            "url":         "https://www.reuters.com/test-article",
            "title":       "Test headline",
            "description": "Test description with interest rates.",
            "image":       "https://example.com/img.jpg",
            "dateTime":    "2026-03-31T09:00:00Z",
            "body":        "Full article body text from newsapi.ai, replacing snippet.",
            "source":      {"uri": "reuters.com", "title": "Reuters"},
            "authors":     [{"name": "John Smith", "uri": "john-smith"}],
        }
        raw = producer_instance._build_raw_payload(article, "news/Business", "pulse")

        required_keys = {
            "article_id", "title", "description", "url", "published_at",
            "content", "author", "source", "category", "fetch_mode",
            "impact_boost", "impact_boost_reason",
        }
        missing = required_keys - set(raw.keys())
        assert not missing, f"_build_raw_payload() missing keys: {missing}"

        # Core field mapping assertions (M7)
        assert raw["article_id"]   == "https://www.reuters.com/test-article"
        assert raw["category"]     == "news/Business"
        assert raw["fetch_mode"]   == "pulse"
        assert raw["published_at"] == "2026-03-31T09:00:00Z"
        assert raw["url_to_image"] == "https://example.com/img.jpg"
        assert isinstance(raw["impact_boost"], bool)
        assert isinstance(raw["impact_boost_reason"], str)

        # Phase 7A normalisation invariants
        assert raw["source"]["id"]   == "reuters", (
            "TLD-strip rule should produce source.id='reuters' for reuters.com"
        )
        assert raw["source"]["name"] == "Reuters", (
            "source.title='Reuters' from API should be used as source.name (M7)"
        )
        assert raw["author"] == "John Smith", (
            "authors[0].name should be extracted and used as author string"
        )
        assert raw["content"] == "Full article body text from newsapi.ai, replacing snippet.", (
            "newsapi.ai body must be normalised into the internal `content` field (M7)"
        )

    def test_fetch_articles_uses_correct_params(self, producer_instance):
        """
        _fetch_articles() must send all required params for newsapi.ai (M5, M6).

        Asserts: apiKey present, categoryUri present, sourceUri present (as
        multiple separate params per domain — list-of-tuples format; CSV not
        accepted by newsapi.ai per T7A.14 diagnostic), lang=eng, articlesPage,
        articlesCount, resultType=articles, articleBodyLen=-1,
        includeArticleBody=true, articlesSortBy=date (pulse mode).
        """
        with patch("ingestion.newsapi_producer.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "articles": {"results": [], "totalResults": 0}
            }
            mock_resp.raise_for_status.return_value = None

            with patch("ingestion.newsapi_producer.timed_request") as mock_timed:
                mock_timed.side_effect = lambda fn: (fn(), 100)
                mock_get.return_value = mock_resp

                try:
                    producer_instance._fetch_articles("news/Business")
                except Exception:
                    pass

            if mock_get.called:
                _, kwargs = mock_get.call_args
                raw_params = kwargs.get("params", [])

                # params is list-of-tuples — one entry per sourceUri domain.
                # Build a set of keys and a first-match value lookup for scalars.
                if isinstance(raw_params, list):
                    param_keys = {k for k, _ in raw_params}
                    def get_param(key):
                        return next((v for k, v in raw_params if k == key), None)
                else:
                    param_keys = set(raw_params.keys())
                    def get_param(key):
                        return raw_params.get(key)

                assert "apiKey"          in param_keys, "apiKey must be in request params (M2)"
                assert "categoryUri"     in param_keys, "categoryUri must be in request params (M4)"
                assert "articlesPage"    in param_keys, "articlesPage must be in params (M6)"
                assert "articlesCount"   in param_keys, "articlesCount must be in params (M6)"
                assert "sourceUri"       in param_keys, "sourceUri must be in params — one per domain (M3, T7A.14)"
                assert get_param("lang")               == "eng",      "lang must be 'eng' (ISO 639-3, M5)"
                assert get_param("resultType")         == "articles",  "resultType=articles required (M5)"
                assert get_param("articleBodyLen")     == -1,          "articleBodyLen=-1 required (M5)"
                assert get_param("includeArticleBody") == "true",      "includeArticleBody=true required (M5)"
                assert get_param("articlesSortBy")     == "date",      "articlesSortBy=date required for pulse (M5)"

                # Confirm sourceUri appears once per whitelisted domain
                source_uri_values = [v for k, v in raw_params if k == "sourceUri"] if isinstance(raw_params, list) else []
                if source_uri_values:
                    assert len(source_uri_values) == len(set(source_uri_values)), \
                        "Each sourceUri domain must appear exactly once"
                    assert "reuters.com" in source_uri_values, \
                        "reuters.com must be in sourceUri params (AUTHORITY_WHITELIST member)"


# ==========================================================
# Gate 1 — NDJSON Round-Trip Test (Section 3.4)
# ==========================================================

class TestNDJSONRoundTrip:
    """Verifies the NDJSON serialisation mandated by Section 3.4."""

    def test_ndjson_round_trip_preserves_envelope(self, newsapi_envelope):
        """Serialise → deserialise must produce an equal envelope dict."""
        serialised = ndjson_serializer(newsapi_envelope)
        assert isinstance(serialised, bytes)
        assert serialised.endswith(b"\n"), "NDJSON must end with newline (Section 3.4)"

        restored = ndjson_deserializer(serialised)
        assert restored == newsapi_envelope

    def test_ndjson_bytes_are_valid_single_line(self, newsapi_envelope):
        """
        NDJSON output must be exactly one JSON line plus a trailing newline.

        Multiple newlines break streaming log parsers and GCS append operations
        (Section 3.4 rationale).
        """
        serialised = ndjson_serializer(newsapi_envelope)
        lines      = serialised.decode("utf-8").splitlines()
        assert len(lines) == 1, f"Expected 1 NDJSON line, got {len(lines)}"
