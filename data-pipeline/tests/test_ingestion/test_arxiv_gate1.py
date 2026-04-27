"""
Gate 1 — Ingestion Gate: ArXiv Bronze Schema Compliance.

Validates that the ArXiv producer's envelope-building and payload-construction
logic produces messages fully compliant with the Bronze Schema (Section C.1)
and Message Envelope (Section 3.2) before any message reaches Kafka.

Design decisions:
  - Zero live connections: no Kafka broker, no arXiv API calls.
    build_bronze_message(), _build_raw_payload(), _build_search_query(),
    and all helper functions are pure functions tested in complete isolation.
  - Mock payload loaded from tests/mocks/arxiv_bronze_payload.json (Section 4.4).
  - Validators from utils/validators.py are called directly — the same
    validators the Flink Silver Job uses, so a pass here guarantees the
    Silver Job's first gate will also accept ArXiv messages.
  - ArXivProducer.__new__() used to skip __init__ (avoids Kafka connection).

Key ArXiv departures from NewsAPI Gate 1 (Section B.1):
  - No authority whitelist: all arXiv papers originate from arxiv.org.
  - No impact_boost field: Impact Boost is not applicable to academic papers.
  - Dedup key is canonical URL (versionless abstract page URL, Section 4.1C).
  - Partition key is base arxiv_id (versionless) — not source.id.
  - SNR filtering applied at API query level via search_query parameter.

Gate 1 checklist (Section 9.3 Gate 1):
  [1]  envelope fields present and correctly typed
  [2]  event_id is a valid UUIDv4
  [3]  producer_timestamp is a valid ISO8601 UTC string
  [4]  source_name == "arxiv"
  [5]  payload.raw_payload is a non-empty dict
  [6]  validate_envelope() passes without errors
  [7]  validate_bronze_payload() passes without errors
  [8]  NDJSON round-trip preserves the full envelope
  [9]  each call to build_bronze_message() produces a distinct event_id
  [10] raw_payload.url is a canonical arxiv.org/abs/ URL (dedup key)
  [11] raw_payload.pdf_url is present and non-empty
  [12] raw_payload.arxiv_id is a non-empty string
  [13] raw_payload.published is a non-empty ISO8601 string
  [14] raw_payload.authors is a list
  [15] raw_payload.categories is a non-empty list
  [16] raw_payload.primary_category is a string
  [17] raw_payload.query_category is in CATEGORIES
  [18] raw_payload.fetch_mode is "pulse" or "backfill"
  [19] raw_payload.search_query is a non-empty string
  [20] raw_payload has NO impact_boost field (Section B.1)
  [21] Kafka partition key == base arxiv_id (versionless)
  [22] _build_search_query() includes category and all SNR keyword terms
  [23] _build_search_query() uses ti:/abs: field prefixes correctly
  [24] _extract_base_arxiv_id() strips version suffix from Atom URL
  [25] _extract_versioned_arxiv_id() keeps full versioned ID
  [26] _clean_text() collapses whitespace for NDJSON compliance
  [27] _build_raw_payload() produces all Silver-Job-required keys
  [28] CATEGORIES contains all 7 mandated category strings (Section B.1)
  [29] SNR_KEYWORDS contains all 6 mandated terms (Section B.1)

References:
    - Section 2.1:  Producer Matrix (ArXiv row)
    - Section 2.2:  SNR Optimization — API-level query pre-filter
    - Section 3.2:  Message Envelope
    - Section 3.4:  NDJSON serialisation (tested via round-trip)
    - Section 4.1C: SHA-256 deduplication — canonical_url as dedup key
    - Section 4.4:  Mock-Driven Development
    - Section 9.3:  Triple-Gate Test Matrix — Gate 1
    - Section B.1:  ArXiv parameters, Categories, SNR Keywords, no Impact Boost
    - Section C.1:  Bronze Schema
    - Section C.3:  Silver Full-Text Document Store fields
"""

import json
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pytest

from ingestion.arxiv_producer import (
    ARXIV_API_URL,
    CATEGORIES,
    REQUEST_DELAY_SEC,
    SNR_KEYWORDS,
    SOURCE_NAME,
    ArXivProducer,
    _clean_text,
    _extract_base_arxiv_id,
    _extract_versioned_arxiv_id,
)
from utils.kafka_utils import build_bronze_message, ndjson_deserializer, ndjson_serializer
from utils.validators import validate_bronze_payload, validate_envelope


# ==========================================================
# Paths & Constants
# ==========================================================

MOCKS_DIR        = Path(__file__).parent.parent / "mocks"
ARXIV_MOCK_PATH  = MOCKS_DIR / "arxiv_bronze_payload.json"

# All 7 arXiv categories mandated by Section B.1.
REQUIRED_CATEGORIES = {
    "cs.AI",    # Artificial Intelligence
    "cs.LG",    # Machine Learning
    "econ.GN",  # General Economics
    "q-fin.ST", # Statistical Finance
    "q-bio.PE", # Populations and Evolution
    "stat.AP",  # Applications
    "cs.CY",    # Computers and Society
}

# All 6 SNR keyword terms mandated by Section B.1.
REQUIRED_SNR_KEYWORDS = {
    "regulation",
    "policy",
    "market impact",
    "outbreak",
    "intervention",
    "trend",
}

# Valid fetch_mode values for the ArXiv producer.
VALID_FETCH_MODES = {"pulse", "backfill"}


# ==========================================================
# Fixtures
# ==========================================================

@pytest.fixture(scope="module")
def arxiv_raw() -> dict:
    """
    Load the ArXiv bronze mock payload (Section 4.4).

    This is the dict that ArXivProducer._build_raw_payload() produces —
    stored verbatim as raw_payload inside the Bronze envelope.
    scope="module" reads the file once and shares it across all tests.
    """
    with open(ARXIV_MOCK_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def arxiv_envelope(arxiv_raw) -> dict:
    """
    Build the full Bronze envelope from the mock ArXiv paper payload.

    Mirrors what ArXivProducer._emit() does in production: calls
    build_bronze_message() with the raw_payload and source endpoint.
    """
    search_query = ArXivProducer._build_search_query(arxiv_raw["query_category"])
    source_endpoint = (
        f"{ARXIV_API_URL}?search_query={search_query}"
        f"&start=0&max_results=200&sortBy=submittedDate&sortOrder=descending"
    )
    return build_bronze_message(
        source_name=SOURCE_NAME,
        source_endpoint=source_endpoint,
        raw_payload=arxiv_raw,
        http_status_code=200,
        request_duration_ms=312,
    )


@pytest.fixture(scope="module")
def producer_instance() -> ArXivProducer:
    """
    Create an ArXivProducer instance without triggering __init__.

    Skips Kafka connection — _build_search_query(), _build_raw_payload(),
    and helper methods depend only on class-level constants or pure logic.
    """
    return ArXivProducer.__new__(ArXivProducer)


# ==========================================================
# Gate 1 — Envelope Structure Tests (Section 3.2)
# ==========================================================

class TestEnvelopeStructure:
    """Verifies the outer Message Envelope fields (Section 3.2 / Gate 1 checks 1-4)."""

    def test_envelope_is_dict(self, arxiv_envelope):
        assert isinstance(arxiv_envelope, dict)

    def test_envelope_has_event_id(self, arxiv_envelope):
        """event_id must be present and a string (Section 3.2)."""
        assert "event_id" in arxiv_envelope
        assert isinstance(arxiv_envelope["event_id"], str)

    def test_event_id_is_valid_uuid4(self, arxiv_envelope):
        """event_id must be a valid UUIDv4 (Section 3.2)."""
        parsed = uuid.UUID(arxiv_envelope["event_id"])
        assert parsed.version == 4

    def test_producer_timestamp_is_present(self, arxiv_envelope):
        assert "producer_timestamp" in arxiv_envelope
        assert isinstance(arxiv_envelope["producer_timestamp"], str)

    def test_producer_timestamp_is_iso8601_utc(self, arxiv_envelope):
        """producer_timestamp must be timezone-aware ISO8601 (Section 3.2)."""
        ts     = arxiv_envelope["producer_timestamp"]
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None, "producer_timestamp must be timezone-aware"

    def test_source_name_is_arxiv(self, arxiv_envelope):
        """source_name in the envelope must equal 'arxiv' (Section C.1, B.1)."""
        assert arxiv_envelope.get("source_name") == SOURCE_NAME
        assert arxiv_envelope.get("source_name") == "arxiv"

    def test_schema_version_is_present(self, arxiv_envelope):
        """schema_version must be set — enables schema evolution (Section 3.2)."""
        assert "schema_version" in arxiv_envelope
        assert arxiv_envelope["schema_version"] != ""

    def test_trace_id_is_valid_uuid(self, arxiv_envelope):
        """trace_id must be a valid UUID for distributed tracing (Section 7.2)."""
        trace_id = arxiv_envelope.get("trace_id", "")
        parsed   = uuid.UUID(trace_id)
        assert parsed.version == 4


# ==========================================================
# Gate 1 — Bronze Payload Tests (Section C.1)
# ==========================================================

class TestBronzePayload:
    """Verifies the inner Bronze Schema payload (Section C.1 / Gate 1 checks 5-7)."""

    def test_payload_key_exists(self, arxiv_envelope):
        assert "payload" in arxiv_envelope
        assert isinstance(arxiv_envelope["payload"], dict)

    def test_raw_payload_is_non_empty_dict(self, arxiv_envelope):
        raw = arxiv_envelope["payload"].get("raw_payload")
        assert isinstance(raw, dict), "raw_payload must be a dict"
        assert len(raw) > 0, "raw_payload must not be empty"

    def test_bronze_payload_has_ingestion_id(self, arxiv_envelope):
        """ingestion_id must be present and a valid UUID (Section C.1)."""
        ingestion_id = arxiv_envelope["payload"].get("ingestion_id", "")
        parsed = uuid.UUID(ingestion_id)
        assert parsed.version == 4

    def test_bronze_payload_has_source_endpoint(self, arxiv_envelope):
        """source_endpoint must reference an arxiv API URL (Section C.1)."""
        endpoint = arxiv_envelope["payload"].get("source_endpoint", "")
        assert isinstance(endpoint, str) and len(endpoint) > 0
        assert "arxiv.org" in endpoint, (
            f"source_endpoint should reference an arxiv.org URL. Got: {endpoint}"
        )

    def test_bronze_payload_has_ingestion_timestamp(self, arxiv_envelope):
        """ingestion_timestamp must be a valid ISO8601 timezone-aware string (Section C.1)."""
        ts     = arxiv_envelope["payload"].get("ingestion_timestamp", "")
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None

    def test_bronze_payload_metadata_block(self, arxiv_envelope):
        """metadata block must have http_status_code (int) and request_duration_ms (int|float)."""
        metadata = arxiv_envelope["payload"].get("metadata", {})
        assert isinstance(metadata, dict)
        assert isinstance(metadata.get("http_status_code"), int)
        assert isinstance(metadata.get("request_duration_ms"), (int, float))


# ==========================================================
# Gate 1 — Validator Integration Tests (Section 9.3)
# ==========================================================

class TestValidatorIntegration:
    """
    Runs the same validators the Flink Silver Job uses (Section 9.3 Gate 1).

    A pass here guarantees the Silver Job's first gate will accept ArXiv messages.
    """

    def test_validate_envelope_passes(self, arxiv_envelope):
        """
        validate_envelope() must return is_valid=True (Section 3.2, 9.3).

        A failure means the Silver Job would route this message to
        dead-letter-queue at its very first validation step.
        """
        result = validate_envelope(arxiv_envelope)
        assert result.is_valid, f"Envelope validation errors: {result.errors}"

    def test_validate_bronze_payload_passes(self, arxiv_envelope):
        """validate_bronze_payload() on the inner payload must pass (Section C.1, 9.3)."""
        inner  = arxiv_envelope["payload"]
        result = validate_bronze_payload(inner)
        assert result.is_valid, f"Bronze payload validation errors: {result.errors}"


# ==========================================================
# Gate 1 — ArXiv Domain-Specific Correctness (Section B.1)
# ==========================================================

class TestArXivDomainCorrectness:
    """
    Verifies ArXiv-specific business rules in the Bronze payload
    (Section B.1, Gate 1 checks 10-21).
    """

    def test_url_is_canonical_arxiv_abs_url(self, arxiv_envelope):
        """
        raw_payload.url must be the canonical abstract URL (no version suffix).

        This is the SHA-256 dedup key in the Silver Job (Section 4.1C).
        The canonical URL (https://arxiv.org/abs/{base_id}) is stable across
        paper versions — v1 and v2 share the same hash and are not double-ingested.
        """
        url = arxiv_envelope["payload"]["raw_payload"].get("url", "")
        assert isinstance(url, str) and len(url) > 0
        assert url.startswith("https://arxiv.org/abs/"), (
            f"url must be canonical arxiv.org/abs/ URL. Got: {url!r}"
        )
        # Must not contain a version suffix (e.g. "v1", "v2")
        base_part = url.replace("https://arxiv.org/abs/", "")
        assert "v" not in base_part.lower() or not base_part[-2:][0] == "v", (
            f"Canonical URL must not include version suffix. Got: {url!r}"
        )

    def test_pdf_url_is_present_and_non_empty(self, arxiv_envelope):
        """raw_payload.pdf_url must be a non-empty string."""
        pdf_url = arxiv_envelope["payload"]["raw_payload"].get("pdf_url", "")
        assert isinstance(pdf_url, str) and len(pdf_url) > 0, (
            "pdf_url must be non-empty — required for Knowledge Vault archival"
        )

    def test_arxiv_id_is_present_and_non_empty(self, arxiv_envelope):
        """
        raw_payload.arxiv_id must be the versioned ID string (e.g. '2409.14891v1').

        Stored for audit trail — the Silver Job identifies the exact paper version
        that was ingested. Different from url, which is the versionless dedup key.
        """
        arxiv_id = arxiv_envelope["payload"]["raw_payload"].get("arxiv_id", "")
        assert isinstance(arxiv_id, str) and len(arxiv_id) > 0

    def test_published_is_non_empty(self, arxiv_envelope):
        """raw_payload.published must be a non-empty string (Silver schema field)."""
        published = arxiv_envelope["payload"]["raw_payload"].get("published", "")
        assert isinstance(published, str) and len(published) > 0

    def test_published_parses_as_iso8601(self, arxiv_envelope):
        """published must parse as ISO8601 (Section C.3 publish_date field)."""
        published = arxiv_envelope["payload"]["raw_payload"].get("published", "")
        parsed = datetime.fromisoformat(published.replace("Z", "+00:00"))
        assert parsed is not None

    def test_authors_is_a_list(self, arxiv_envelope):
        """
        raw_payload.authors must be a list.

        The Silver Job's map_arxiv_paper_to_silver() calls ', '.join(authors)
        for the Silver document's author field. A non-list would raise TypeError.
        """
        authors = arxiv_envelope["payload"]["raw_payload"].get("authors")
        assert isinstance(authors, list), (
            f"authors must be a list, got {type(authors).__name__}"
        )

    def test_categories_is_non_empty_list(self, arxiv_envelope):
        """
        raw_payload.categories must be a non-empty list.

        The Gold Job maps categories → domain_context.domain_tags (Section C.5
        ArXiv Academic extension). An empty list would silently discard
        discipline-level metadata from the Gold record.
        """
        categories = arxiv_envelope["payload"]["raw_payload"].get("categories")
        assert isinstance(categories, list), (
            f"categories must be a list, got {type(categories).__name__}"
        )
        assert len(categories) > 0, "categories must not be empty"

    def test_primary_category_is_string(self, arxiv_envelope):
        """raw_payload.primary_category must be a non-empty string."""
        primary = arxiv_envelope["payload"]["raw_payload"].get("primary_category", "")
        assert isinstance(primary, str) and len(primary) > 0

    def test_query_category_is_in_categories_constant(self, arxiv_envelope):
        """
        raw_payload.query_category must be one of the CATEGORIES values.

        The Silver Job uses query_category for routing context. An unexpected
        value would be a producer configuration bug — this test catches it at
        the Bronze gate (Section 9.3 Gate 1).
        """
        query_cat = arxiv_envelope["payload"]["raw_payload"].get("query_category", "")
        assert query_cat in CATEGORIES, (
            f"query_category '{query_cat}' is not in CATEGORIES: {CATEGORIES}"
        )

    def test_fetch_mode_is_valid(self, arxiv_envelope):
        """fetch_mode must be 'pulse' or 'backfill' (the two ArXiv operating modes)."""
        fetch_mode = arxiv_envelope["payload"]["raw_payload"].get("fetch_mode", "")
        assert fetch_mode in VALID_FETCH_MODES, (
            f"fetch_mode '{fetch_mode}' not in {VALID_FETCH_MODES}"
        )

    def test_search_query_is_non_empty_string(self, arxiv_envelope):
        """
        raw_payload.search_query must be a non-empty string.

        The full arXiv API search_query is stored in Bronze for replay auditing —
        allows reconstructing exactly which query produced each batch (Section C.1).
        """
        sq = arxiv_envelope["payload"]["raw_payload"].get("search_query", "")
        assert isinstance(sq, str) and len(sq) > 0

    def test_no_impact_boost_field_in_raw_payload(self, arxiv_envelope):
        """
        raw_payload must NOT contain an impact_boost field (Section B.1).

        Impact Boost is reserved for Israeli/Middle East security and energy
        articles from live news feeds. ArXiv academic papers receive impact_level
        purely from the Gold Job's Cognitive Metadata Extraction prompt (Section 4.2A).
        Presence of impact_boost would mislead the Gold Job into applying a +1
        bonus that is not applicable to research papers.
        """
        raw = arxiv_envelope["payload"]["raw_payload"]
        assert "impact_boost" not in raw, (
            "impact_boost must NOT appear in ArXiv raw_payload — "
            "Impact Boost does not apply to academic papers (Section B.1)"
        )

    def test_each_call_produces_unique_event_id(self, arxiv_raw):
        """
        Two calls to build_bronze_message() must produce different event_ids.

        Shared event_ids would corrupt the Paper Trail across Bronze → Silver → Gold
        (Section 3.2 — event_id is the UUIDv4 shared across all layers).
        """
        endpoint = f"{ARXIV_API_URL}?search_query=cat:cs.AI&start=0&max_results=200"
        msg1 = build_bronze_message(SOURCE_NAME, endpoint, arxiv_raw)
        msg2 = build_bronze_message(SOURCE_NAME, endpoint, arxiv_raw)
        assert msg1["event_id"] != msg2["event_id"]

    def test_partition_key_is_base_arxiv_id(self, arxiv_envelope):
        """
        The Kafka partition key must be the versionless base arxiv_id.

        Why: groups all versions of the same paper into the same Kafka partition,
        ensuring the Silver Job's SHA-256 dedup sees v1 and v2 together (Section 4.1C).
        This test confirms _extract_base_arxiv_id() strips version suffixes correctly
        and returns a non-empty key.
        """
        url = arxiv_envelope["payload"]["raw_payload"].get("url", "")
        partition_key = _extract_base_arxiv_id(url)
        assert partition_key != "", "Partition key must be non-empty"
        assert "v" not in partition_key.split(".")[-1], (
            f"Partition key must be versionless (no 'v1', 'v2' suffix). Got: {partition_key!r}"
        )


# ==========================================================
# Gate 1 — Search Query Builder Tests (Section 2.2, B.1)
# ==========================================================

class TestSearchQueryBuilder:
    """
    Unit tests for _build_search_query() — the API-level SNR pre-filter.

    These tests verify that the query string correctly combines the category
    filter with the SNR keyword terms, including proper phrase quoting for
    multi-word keywords (Section 2.2, B.1).
    """

    def test_query_includes_category_filter(self):
        """search_query must start with 'cat:<category>'."""
        q = ArXivProducer._build_search_query("cs.AI")
        assert q.startswith("cat:cs.AI"), (
            f"Query must start with category filter. Got: {q[:50]}"
        )

    def test_query_includes_all_snr_keywords(self):
        """
        All SNR_KEYWORDS must appear in the search_query for each category.

        Missing a keyword means arXiv papers on that topic will never enter
        Bronze (Section 2.2 SNR pre-filter is not client-side for ArXiv).
        """
        q = ArXivProducer._build_search_query("cs.AI")
        for keyword in SNR_KEYWORDS:
            assert keyword in q, (
                f"SNR keyword '{keyword}' not found in search_query: {q}"
            )

    def test_multi_word_keywords_are_quoted(self):
        """
        Multi-word SNR keywords (e.g. 'market impact') must use arXiv double-quote
        phrase syntax: ti:"market impact" OR abs:"market impact".

        Without quotes, arXiv interprets 'market impact' as two separate terms
        (boolean AND), degrading search precision (Section B.1 note).
        """
        q = ArXivProducer._build_search_query("econ.GN")
        assert '"market impact"' in q, (
            f"Multi-word keyword 'market impact' must be quoted in query. Got: {q}"
        )

    def test_single_word_keywords_use_field_prefix(self):
        """
        Single-word keywords must use 'ti:' and 'abs:' field prefixes
        (not quoted phrase syntax).
        """
        q = ArXivProducer._build_search_query("cs.AI")
        assert "ti:regulation" in q, (
            f"Single-word keyword must use ti: prefix. Got: {q}"
        )
        assert "abs:regulation" in q, (
            f"Single-word keyword must use abs: prefix. Got: {q}"
        )

    def test_query_is_different_per_category(self):
        """Different categories must produce different query strings."""
        q_ai    = ArXivProducer._build_search_query("cs.AI")
        q_econ  = ArXivProducer._build_search_query("econ.GN")
        assert q_ai != q_econ
        assert "cat:cs.AI"   in q_ai
        assert "cat:econ.GN" in q_econ


# ==========================================================
# Gate 1 — ID Helper Function Tests (Section 4.1C)
# ==========================================================

class TestIDHelpers:
    """
    Unit tests for _extract_base_arxiv_id(), _extract_versioned_arxiv_id(),
    and _clean_text() — all pure functions used in _parse_entry().
    """

    # --- _extract_base_arxiv_id ---

    def test_strips_version_suffix_v1(self):
        """URL ending in 'v1' must return the versionless base ID."""
        url = "http://arxiv.org/abs/2301.12345v1"
        assert _extract_base_arxiv_id(url) == "2301.12345"

    def test_strips_version_suffix_v2(self):
        """URL ending in 'v2' must also return the versionless base ID."""
        url = "http://arxiv.org/abs/2301.12345v2"
        assert _extract_base_arxiv_id(url) == "2301.12345"

    def test_handles_new_format_arxiv_id(self):
        """New-format arXiv IDs (YYMM.NNNNN) must be extracted correctly."""
        url = "http://arxiv.org/abs/2409.14891v1"
        assert _extract_base_arxiv_id(url) == "2409.14891"

    def test_fallback_on_unexpected_url(self):
        """
        An unexpected URL format (no '/abs/' segment) must fall back to
        returning the full URL rather than raising an exception.
        """
        url = "https://example.com/paper/12345"
        result = _extract_base_arxiv_id(url)
        assert result == url  # fallback: return full URL unchanged

    # --- _extract_versioned_arxiv_id ---

    def test_keeps_version_suffix_v1(self):
        """Versioned extraction must return '2301.12345v1' from the URL."""
        url = "http://arxiv.org/abs/2301.12345v1"
        assert _extract_versioned_arxiv_id(url) == "2301.12345v1"

    def test_keeps_version_suffix_v3(self):
        """Later versions (v3, v4...) must be preserved correctly."""
        url = "http://arxiv.org/abs/2301.12345v3"
        assert _extract_versioned_arxiv_id(url) == "2301.12345v3"

    def test_versioned_and_base_differ_for_versioned_url(self):
        """
        For a versioned URL, base ID and versioned ID must be different strings.

        This confirms that both functions serve different purposes:
        base ID for dedup (Section 4.1C), versioned ID for audit trail.
        """
        url = "http://arxiv.org/abs/2409.14891v1"
        base     = _extract_base_arxiv_id(url)
        versioned = _extract_versioned_arxiv_id(url)
        assert base != versioned
        assert versioned.endswith("v1")
        assert not base.endswith("v1")

    # --- _clean_text ---

    def test_collapses_internal_newlines(self):
        """
        Newlines inside abstract text must be collapsed to a single space
        so Bronze NDJSON lines remain valid (Section 3.4).
        """
        raw = "First sentence.\nSecond sentence.\nThird."
        result = _clean_text(raw)
        assert "\n" not in result
        assert "First sentence. Second sentence. Third." == result

    def test_collapses_multiple_spaces(self):
        """Multiple consecutive spaces must be collapsed to one."""
        raw = "Too   many   spaces   here."
        result = _clean_text(raw)
        assert result == "Too many spaces here."

    def test_strips_leading_trailing_whitespace(self):
        """Leading and trailing whitespace must be stripped."""
        raw = "  \n  Abstract text here.  \n  "
        result = _clean_text(raw)
        assert result == "Abstract text here."

    def test_empty_string_returns_empty(self):
        """Empty or None input must return an empty string without raising."""
        assert _clean_text("") == ""
        assert _clean_text(None) == ""

    def test_normal_text_unchanged(self):
        """Clean single-line text must pass through unchanged."""
        text = "A normal abstract with no extra whitespace."
        assert _clean_text(text) == text


# ==========================================================
# Gate 1 — _parse_entry XML Tests (Section B.1)
# ==========================================================

class TestParseEntry:
    """
    Tests for _parse_entry() using a minimal in-memory Atom XML entry.

    Avoids any live HTTP requests by constructing the XML directly.
    Validates that all field extractions produce the keys expected by
    _build_raw_payload() and ultimately the Silver Job.
    """

    ATOM_NS   = "http://www.w3.org/2005/Atom"
    ARXIV_NS  = "http://arxiv.org/schemas/atom"

    def _make_entry_xml(
        self,
        entry_url:    str = "http://arxiv.org/abs/2409.14891v1",
        title:        str = "Test Paper Title",
        abstract:     str = "Test abstract text.",
        published:    str = "2024-09-23T14:08:31Z",
        updated:      str = "2024-09-25T09:22:14Z",
        authors:      list = None,
        categories:   list = None,
        primary_cat:  str = "cs.AI",
        pdf_href:     str = "https://arxiv.org/pdf/2409.14891",
    ) -> ET.Element:
        """Build a minimal valid Atom <entry> element for testing."""
        if authors is None:
            authors = ["Author One", "Author Two"]
        if categories is None:
            categories = ["cs.AI", "econ.GN"]

        AN  = self.ATOM_NS
        ARX = self.ARXIV_NS

        entry = ET.Element(f"{{{AN}}}entry")

        id_elem = ET.SubElement(entry, f"{{{AN}}}id")
        id_elem.text = entry_url

        title_elem = ET.SubElement(entry, f"{{{AN}}}title")
        title_elem.text = title

        summary_elem = ET.SubElement(entry, f"{{{AN}}}summary")
        summary_elem.text = abstract

        pub_elem = ET.SubElement(entry, f"{{{AN}}}published")
        pub_elem.text = published

        upd_elem = ET.SubElement(entry, f"{{{AN}}}updated")
        upd_elem.text = updated

        # PDF link
        pdf_link = ET.SubElement(entry, f"{{{AN}}}link")
        pdf_link.set("title", "pdf")
        pdf_link.set("href", pdf_href)

        # Authors
        for name in authors:
            author_el = ET.SubElement(entry, f"{{{AN}}}author")
            name_el   = ET.SubElement(author_el, f"{{{AN}}}name")
            name_el.text = name

        # Categories
        for cat in categories:
            cat_el = ET.SubElement(entry, f"{{{AN}}}category")
            cat_el.set("term", cat)

        # Primary category (arxiv namespace)
        primary_el = ET.SubElement(entry, f"{{{ARX}}}primary_category")
        primary_el.set("term", primary_cat)

        return entry

    def test_parse_entry_returns_dict(self):
        """_parse_entry() must return a dict."""
        entry = self._make_entry_xml()
        result = ArXivProducer._parse_entry(entry)
        assert isinstance(result, dict)

    def test_parse_entry_extracts_versioned_arxiv_id(self):
        """arxiv_id must be the versioned ID (e.g. '2409.14891v1')."""
        entry = self._make_entry_xml(entry_url="http://arxiv.org/abs/2409.14891v1")
        result = ArXivProducer._parse_entry(entry)
        assert result["arxiv_id"] == "2409.14891v1"

    def test_parse_entry_canonical_url_strips_version(self):
        """canonical_url must be the versionless abstract URL (Section 4.1C)."""
        entry = self._make_entry_xml(entry_url="http://arxiv.org/abs/2409.14891v1")
        result = ArXivProducer._parse_entry(entry)
        assert result["canonical_url"] == "https://arxiv.org/abs/2409.14891"
        assert "v1" not in result["canonical_url"]

    def test_parse_entry_extracts_title(self):
        entry = self._make_entry_xml(title="Regulatory Uncertainty and AI")
        result = ArXivProducer._parse_entry(entry)
        assert result["title"] == "Regulatory Uncertainty and AI"

    def test_parse_entry_cleans_abstract_whitespace(self):
        """Abstract with internal newlines must be cleaned by _clean_text()."""
        entry = self._make_entry_xml(abstract="Line one.\nLine two.\nLine three.")
        result = ArXivProducer._parse_entry(entry)
        assert "\n" not in result["abstract"]
        assert result["abstract"] == "Line one. Line two. Line three."

    def test_parse_entry_extracts_all_authors(self):
        """All <author><name> elements must be extracted into authors list."""
        entry = self._make_entry_xml(authors=["Alice Smith", "Bob Jones", "Carol Lee"])
        result = ArXivProducer._parse_entry(entry)
        assert result["authors"] == ["Alice Smith", "Bob Jones", "Carol Lee"]

    def test_parse_entry_extracts_all_categories(self):
        """All <category term=...> elements must appear in categories list."""
        entry = self._make_entry_xml(categories=["cs.AI", "econ.GN", "q-fin.ST"])
        result = ArXivProducer._parse_entry(entry)
        assert set(result["categories"]) == {"cs.AI", "econ.GN", "q-fin.ST"}

    def test_parse_entry_extracts_primary_category(self):
        """primary_category must come from <arxiv:primary_category term=...>."""
        entry = self._make_entry_xml(primary_cat="econ.GN")
        result = ArXivProducer._parse_entry(entry)
        assert result["primary_category"] == "econ.GN"

    def test_parse_entry_extracts_pdf_url(self):
        """pdf_url must be extracted from the <link title='pdf' href=...> element."""
        entry = self._make_entry_xml(pdf_href="https://arxiv.org/pdf/2409.14891v1")
        result = ArXivProducer._parse_entry(entry)
        assert result["pdf_url"] == "https://arxiv.org/pdf/2409.14891v1"

    def test_parse_entry_all_required_keys_present(self):
        """_parse_entry() result must contain all keys expected by _build_raw_payload()."""
        entry = self._make_entry_xml()
        result = ArXivProducer._parse_entry(entry)
        required = {
            "arxiv_id", "canonical_url", "pdf_url", "title", "abstract",
            "authors", "published", "updated", "categories", "primary_category",
        }
        missing = required - set(result.keys())
        assert not missing, f"_parse_entry() missing keys: {missing}"


# ==========================================================
# Gate 1 — _build_raw_payload Tests (Section C.3, B.1)
# ==========================================================

class TestBuildRawPayload:
    """
    Unit tests for _build_raw_payload() — verifies all Silver-Job-required
    keys are present and correctly mapped (Gate 1 check 27).
    """

    def test_build_raw_payload_produces_required_keys(self):
        """
        _build_raw_payload() must produce all keys the Silver Job needs
        to populate the Full-Text Document Store (Section C.3):
        url, pdf_url, arxiv_id, title, abstract, authors, published, updated,
        categories, primary_category, query_category, fetch_mode, search_query.

        The url field is the canonical dedup key (Section 4.1C); abstract maps to
        full_text_raw and inverted_pyramid_lead in map_arxiv_paper_to_silver().
        """
        entry_data = {
            "arxiv_id":        "2409.14891v1",
            "canonical_url":   "https://arxiv.org/abs/2409.14891",
            "pdf_url":         "https://arxiv.org/pdf/2409.14891",
            "title":           "Regulatory Uncertainty and Market Volatility",
            "abstract":        "We examine how evolving AI regulation affects markets.",
            "authors":         ["Alice", "Bob"],
            "published":       "2024-09-23T14:08:31Z",
            "updated":         "2024-09-25T09:22:14Z",
            "categories":      ["cs.AI", "econ.GN"],
            "primary_category": "cs.AI",
        }
        raw = ArXivProducer._build_raw_payload(
            entry_data,
            query_category="cs.AI",
            fetch_mode="pulse",
            search_query='cat:cs.AI AND (ti:regulation OR abs:regulation)',
        )

        required_keys = {
            "arxiv_id", "url", "pdf_url", "title", "abstract",
            "authors", "published", "updated", "categories",
            "primary_category", "query_category", "fetch_mode", "search_query",
        }
        missing = required_keys - set(raw.keys())
        assert not missing, f"_build_raw_payload() missing keys: {missing}"

    def test_build_raw_payload_url_is_canonical(self):
        """url in raw_payload must equal canonical_url (versionless)."""
        entry_data = {
            "arxiv_id":        "2409.14891v1",
            "canonical_url":   "https://arxiv.org/abs/2409.14891",
            "pdf_url":         "https://arxiv.org/pdf/2409.14891",
            "title":           "Test",
            "abstract":        "Test abstract.",
            "authors":         [],
            "published":       "2024-09-23T14:08:31Z",
            "updated":         "2024-09-23T14:08:31Z",
            "categories":      ["cs.AI"],
            "primary_category": "cs.AI",
        }
        raw = ArXivProducer._build_raw_payload(entry_data, "cs.AI", "pulse", "cat:cs.AI")
        assert raw["url"] == "https://arxiv.org/abs/2409.14891"

    def test_build_raw_payload_has_no_impact_boost(self):
        """
        _build_raw_payload() must not produce an impact_boost key.

        Confirms at the builder level that no Impact Boost logic was accidentally
        added (Section B.1: Impact Boost not applicable to academic papers).
        """
        entry_data = {
            "arxiv_id":        "2409.14891v1",
            "canonical_url":   "https://arxiv.org/abs/2409.14891",
            "pdf_url":         "https://arxiv.org/pdf/2409.14891",
            "title":           "AI regulation and market volatility",
            "abstract":        "An abstract about crude oil prices and Israel sanctions.",
            "authors":         [],
            "published":       "2024-09-23T14:08:31Z",
            "updated":         "2024-09-23T14:08:31Z",
            "categories":      ["cs.AI"],
            "primary_category": "cs.AI",
        }
        raw = ArXivProducer._build_raw_payload(entry_data, "cs.AI", "pulse", "cat:cs.AI")
        assert "impact_boost" not in raw, (
            "impact_boost must NOT be produced by _build_raw_payload() (Section B.1)"
        )


# ==========================================================
# Gate 1 — Constants Invariants (Section B.1)
# ==========================================================

class TestConstantsInvariants:
    """
    Verifies that CATEGORIES and SNR_KEYWORDS contain all mandated values
    from Section B.1. Catches accidental omissions that would silently
    drop entire domains or keyword groups from the Bronze intake.
    """

    def test_all_7_categories_present(self):
        """
        CATEGORIES must contain all 7 mandated arXiv categories (Section B.1).

        A missing category means an entire academic domain (e.g. q-bio.PE for
        outbreak signals) would be silently excluded from Bronze ingestion.
        """
        missing = REQUIRED_CATEGORIES - set(CATEGORIES)
        assert not missing, (
            f"CATEGORIES is missing Section B.1 entries: {missing}"
        )

    def test_categories_is_a_list(self):
        """CATEGORIES must be a list (iterable for both pulse and backfill loops)."""
        assert isinstance(CATEGORIES, list)
        assert len(CATEGORIES) > 0

    def test_all_6_snr_keywords_present(self):
        """
        SNR_KEYWORDS must contain all 6 terms mandated by Section B.1.

        Missing a keyword means papers on that topic will not pass the API-level
        pre-filter and will never enter Bronze — a silent data loss.
        """
        missing = REQUIRED_SNR_KEYWORDS - set(SNR_KEYWORDS)
        assert not missing, (
            f"SNR_KEYWORDS is missing Section B.1 terms: {missing}"
        )

    def test_snr_keywords_is_a_tuple(self):
        """SNR_KEYWORDS should be a tuple (immutable constant, not accidentally mutable)."""
        assert isinstance(SNR_KEYWORDS, tuple)

    def test_request_delay_is_at_least_3_seconds(self):
        """
        REQUEST_DELAY_SEC must be >= 3.0 (arXiv Terms of Service, Section B.1).

        This constant is used in both run_pulse() and _backfill_category().
        A lower value would violate TOS and risk IP bans for the production system.
        """
        assert REQUEST_DELAY_SEC >= 3, (
            f"REQUEST_DELAY_SEC must be >= 3 per arXiv TOS. Got: {REQUEST_DELAY_SEC}"
        )


# ==========================================================
# Gate 1 — NDJSON Round-Trip Test (Section 3.4)
# ==========================================================

class TestNDJSONRoundTrip:
    """Verifies the NDJSON serialisation mandated by Section 3.4."""

    def test_ndjson_round_trip_preserves_envelope(self, arxiv_envelope):
        """Serialise then deserialise must produce an equal envelope dict."""
        serialised = ndjson_serializer(arxiv_envelope)
        assert isinstance(serialised, bytes)
        assert serialised.endswith(b"\n"), "NDJSON must end with newline (Section 3.4)"

        restored = ndjson_deserializer(serialised)
        assert restored == arxiv_envelope

    def test_ndjson_bytes_are_valid_single_line(self, arxiv_envelope):
        """
        NDJSON output must be exactly one JSON line plus a trailing newline.

        Multiple newlines would break streaming log parsers and GCS append
        operations (Section 3.4 rationale). arXiv abstracts are cleaned by
        _clean_text() before Bronze emission precisely to prevent this.
        """
        serialised = ndjson_serializer(arxiv_envelope)
        lines      = serialised.decode("utf-8").splitlines()
        assert len(lines) == 1, f"Expected 1 NDJSON line, got {len(lines)}"
