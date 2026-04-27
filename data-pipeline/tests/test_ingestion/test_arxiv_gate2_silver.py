"""
Gate 2 — Logic Gate: ArXiv Silver Processing Validation (Section 4.1).

Validates that the Silver Job's ArXiv branch transforms and routes correctly
using mock payloads — no live Flink cluster, no Kafka broker, no arXiv API calls.

What Gate 2 covers (Section 9.3 Gate 2):
    [1]  Valid ArXiv paper → routes to SILVER_GLOBAL_NEWS
    [2]  Invalid envelope (missing event_id) → routes to DEAD_LETTER_QUEUE
    [3]  Invalid bronze payload (missing ingestion_id) → routes to DEAD_LETTER_QUEUE
    [4]  Missing url key in raw_payload → routes to DEAD_LETTER_QUEUE
    [5]  Empty url string in raw_payload → routes to DEAD_LETTER_QUEUE
    [6]  Silver output passes validate_silver_document()
    [7]  doc_id is a non-empty UUID string
    [8]  document_hash is a 64-char lowercase SHA-256 hex digest
    [9]  canonical_event_id in Silver == Bronze envelope event_id
    [10] source_name == "arxiv"
    [11] original_url matches raw_payload.url (canonical abstract URL)
    [12] full_text_raw == raw_payload.abstract (no content/description split for arXiv)
    [13] inverted_pyramid_lead == raw_payload.abstract (abstract IS the lede, Section C.3)
    [14] full_text_raw == inverted_pyramid_lead (always equal for arXiv)
    [15] detected_entities == [] (Gold Job populates via OpenAI, Section 4.2A)
    [16] relevance_score is float in [0.0, 1.0]
    [17] High-signal paper has is_high_signal == True
    [18] impact_boost == False ALWAYS (Section B.1 — no Impact Boost for academic papers)
    [19] impact_boost_reason == "" ALWAYS
    [20] authors is a list in the Silver record (not a joined string)
    [21] categories is a list in the Silver record
    [22] primary_category is a non-empty string
    [23] arxiv_id matches raw_payload.arxiv_id
    [24] sniper_keywords is a sorted list
    [25] fetch_mode passed through from raw_payload
    [26] bronze_ref == Bronze envelope event_id (lineage link, Section 3.2)
    [27] publish_date matches raw_payload.published
    [28] Low-signal paper routes to SILVER_GLOBAL_NEWS (sniper never blocks Silver)
    [29] Low-signal paper has is_high_signal == False
    [30] DLQ record carries source_topic == BRONZE_ARXIV (Section 3.5)
    [31] DLQ record preserves original_message intact for replay
    [32] DLQ record has non-empty validation_errors
    [33] document_hash deterministic across repeated calls
    [34] doc_id unique per call

Key ArXiv departures from NewsAPI Silver Gate 2 (Section B.1):
    - full_text_raw = abstract (not content‖description — no content field on arXiv)
    - inverted_pyramid_lead = abstract (not description — abstract IS the lede)
    - impact_boost = False always (hard-coded in mapper, not from raw_payload)
    - authors stored as list (not joined string) for Gold domain_context (Section C.5)
    - DLQ source_topic = BRONZE_ARXIV (not BRONZE_NEWSAPI)

References:
    - Section 4.1:   Silver Job specification
    - Section 4.1A:  Keyword Sniper — gates Gold OpenAI call (not Silver emission)
    - Section 4.1C:  SHA-256 deduplication — canonical_url as dedup key
    - Section 4.2A:  Gold Job — detected_entities populated by OpenAI
    - Section 4.4:   Mock-Driven Development
    - Section 9.3:   Triple-Gate Test Matrix — Gate 2
    - Section B.1:   ArXiv parameters — no Impact Boost for academic papers
    - Section C.3:   Silver Full-Text Document Store schema
    - Section C.5:   Gold domain_context — ArXiv Academic extension (authors list)
    - Section 3.5:   Dead-Letter Queue — never silently drop
"""

import json
import uuid
from pathlib import Path

import pytest

from config.kafka_topics import BRONZE_ARXIV, DEAD_LETTER_QUEUE, SILVER_GLOBAL_NEWS
from ingestion.arxiv_producer import ARXIV_API_URL, SOURCE_NAME
from processing.silver_job import map_arxiv_paper_to_silver, process_arxiv_message
from utils.kafka_utils import build_bronze_message
from utils.validators import validate_silver_document

# ==========================================================
# Paths & Helpers
# ==========================================================

MOCKS_DIR = Path(__file__).parent.parent / "mocks"

_ARXIV_ENDPOINT = (
    f"{ARXIV_API_URL}?search_query=cat:cs.AI&start=0&max_results=200"
    f"&sortBy=submittedDate&sortOrder=descending"
)


def _build_envelope(raw: dict) -> dict:
    return build_bronze_message(
        source_name=SOURCE_NAME,
        source_endpoint=_ARXIV_ENDPOINT,
        raw_payload=raw,
        http_status_code=200,
        request_duration_ms=312,
    )


# ==========================================================
# Fixtures
# ==========================================================

@pytest.fixture(scope="module")
def arxiv_raw() -> dict:
    """
    Load the ArXiv bronze mock payload (Section 4.4), stripping _comment key.

    This is the dict ArXivProducer._build_raw_payload() produces — stored
    verbatim as raw_payload inside the Bronze envelope.
    """
    with open(MOCKS_DIR / "arxiv_bronze_payload.json", encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


@pytest.fixture(scope="module")
def arxiv_envelope(arxiv_raw) -> dict:
    """Build a full Bronze envelope from the mock ArXiv paper."""
    return _build_envelope(arxiv_raw)


@pytest.fixture(scope="module")
def silver_record(arxiv_envelope) -> dict:
    """
    Run process_arxiv_message() on the mock envelope and return the Silver record.

    The mock paper (AI regulation / market volatility) must score high-signal
    via the Keyword Sniper — 'regulation' and 'policy' appear in the title.
    Fails the test immediately if routing is unexpected.
    """
    topic, record = process_arxiv_message(arxiv_envelope)
    assert topic == SILVER_GLOBAL_NEWS, (
        f"Expected SILVER_GLOBAL_NEWS but got '{topic}'. Record: {record}"
    )
    return record


# ==========================================================
# Gate 2 — Routing Tests (checks [1]–[5])
# ==========================================================

class TestArXivSilverRouting:
    """
    Verifies process_arxiv_message() routes to the correct Kafka topic.
    """

    def test_valid_paper_routes_to_silver_global_news(self, arxiv_envelope):
        """
        A valid ArXiv paper must route to SILVER_GLOBAL_NEWS (check [1]).

        SILVER_GLOBAL_NEWS is the only routing target for ArXiv — all papers
        are Full-Text Documents; there is no social_pulse or structured_metrics
        branch (Section 3.1 BRONZE_TO_SILVER_ROUTING).
        """
        topic, record = process_arxiv_message(arxiv_envelope)
        assert topic == SILVER_GLOBAL_NEWS, (
            f"Expected SILVER_GLOBAL_NEWS, got '{topic}'"
        )
        assert record is not None

    def test_invalid_envelope_routes_to_dlq(self, arxiv_raw):
        """
        An envelope missing event_id must route to DEAD_LETTER_QUEUE (check [2]).
        """
        envelope = _build_envelope(arxiv_raw)
        del envelope["event_id"]
        topic, record = process_arxiv_message(envelope)
        assert topic == DEAD_LETTER_QUEUE
        assert "validation_errors" in record

    def test_invalid_bronze_payload_routes_to_dlq(self, arxiv_raw):
        """
        An envelope with a corrupt Bronze payload (missing ingestion_id) must
        route to DEAD_LETTER_QUEUE (check [3]).
        """
        envelope = _build_envelope(arxiv_raw)
        del envelope["payload"]["ingestion_id"]
        topic, record = process_arxiv_message(envelope)
        assert topic == DEAD_LETTER_QUEUE

    def test_missing_url_routes_to_dlq(self, arxiv_raw):
        """
        A raw_payload without a url key must route to DEAD_LETTER_QUEUE (check [4]).

        url is the canonical arXiv abstract URL and the dedup key for hash_document()
        (Section 4.1C). A paper without a URL cannot be deduplicated and must not
        enter the Knowledge Vault.
        """
        raw_no_url = {k: v for k, v in arxiv_raw.items() if k != "url"}
        topic, record = process_arxiv_message(_build_envelope(raw_no_url))
        assert topic == DEAD_LETTER_QUEUE
        error_text = " ".join(record.get("validation_errors", []))
        assert "url" in error_text.lower(), (
            f"DLQ error should mention URL. Got: {record['validation_errors']}"
        )

    def test_empty_url_routes_to_dlq(self, arxiv_raw):
        """
        A raw_payload with url="" must route to DEAD_LETTER_QUEUE (check [5]).

        An empty string URL fails the url_guard (url.strip() is falsy) — same
        failure mode as a missing URL.
        """
        raw = dict(arxiv_raw)
        raw["url"] = ""
        topic, _ = process_arxiv_message(_build_envelope(raw))
        assert topic == DEAD_LETTER_QUEUE

    def test_whitespace_url_routes_to_dlq(self, arxiv_raw):
        """A url of only whitespace must route to DEAD_LETTER_QUEUE."""
        raw = dict(arxiv_raw)
        raw["url"] = "   "
        topic, _ = process_arxiv_message(_build_envelope(raw))
        assert topic == DEAD_LETTER_QUEUE


# ==========================================================
# Gate 2 — Silver Schema Correctness (checks [6]–[27])
# ==========================================================

class TestArXivSilverSchema:
    """
    Verifies the Silver Full-Text Document Store output conforms to Section C.3
    for ArXiv papers.
    """

    def test_silver_passes_validator(self, silver_record):
        """
        The Silver output must pass validate_silver_document() (check [6]).

        Running the validator again here independently confirms the schema
        contract, not just that the function didn't crash.
        """
        result = validate_silver_document(silver_record)
        assert result.is_valid, f"Silver document validation failed: {result.errors}"

    def test_doc_id_is_non_empty_uuid(self, silver_record):
        """doc_id must be a non-empty UUID string (check [7])."""
        doc_id = silver_record.get("doc_id", "")
        assert isinstance(doc_id, str) and len(doc_id) > 0
        parsed = uuid.UUID(doc_id)
        assert str(parsed) == doc_id

    def test_document_hash_is_64_char_hex(self, silver_record):
        """document_hash must be a 64-character lowercase SHA-256 hex digest (check [8])."""
        doc_hash = silver_record.get("document_hash", "")
        assert len(doc_hash) == 64, f"document_hash must be 64 chars, got {len(doc_hash)}"
        assert all(c in "0123456789abcdef" for c in doc_hash), (
            "document_hash must be lowercase hexadecimal"
        )

    def test_canonical_event_id_matches_envelope(self, arxiv_envelope, silver_record):
        """
        canonical_event_id must equal the Bronze envelope's event_id (check [9]).

        This is the Paper Trail link — every Silver record must trace back to
        the exact Bronze message that produced it (Section 3.2 / 7.2).
        """
        assert silver_record["canonical_event_id"] == arxiv_envelope["event_id"]

    def test_source_name_is_arxiv(self, silver_record):
        """source_name must be 'arxiv' (check [10])."""
        assert silver_record["source_name"] == "arxiv"

    def test_original_url_matches_raw(self, arxiv_raw, silver_record):
        """
        original_url must equal raw_payload.url verbatim (check [11]).

        No normalisation is applied at this stage — the canonical abstract URL
        is stored as-is so the dedup hash can be reproduced from the stored record.
        """
        assert silver_record["original_url"] == arxiv_raw["url"]

    def test_full_text_raw_equals_abstract(self, arxiv_raw, silver_record):
        """
        full_text_raw must equal raw_payload.abstract (check [12]).

        ArXiv raw_payload has only the abstract — there is no separate 'content'
        or 'description' field. The abstract is the complete text available at
        Bronze time (Section C.3: full_text_raw is the cleaned document body).
        """
        assert silver_record["full_text_raw"] == arxiv_raw["abstract"]

    def test_inverted_pyramid_lead_equals_abstract(self, arxiv_raw, silver_record):
        """
        inverted_pyramid_lead must equal raw_payload.abstract (check [13]).

        For academic papers the abstract IS the lede — it encapsulates the
        paper's contribution in one block (Section C.3, map_arxiv_paper_to_silver
        docstring rationale).
        """
        assert silver_record["inverted_pyramid_lead"] == arxiv_raw["abstract"]

    def test_full_text_raw_equals_inverted_pyramid_lead(self, silver_record):
        """
        full_text_raw and inverted_pyramid_lead must be identical for ArXiv (check [14]).

        Unlike NewsAPI (where description is the lede and content is the body),
        arXiv has only the abstract, so both fields point to the same value.
        """
        assert silver_record["full_text_raw"] == silver_record["inverted_pyramid_lead"]

    def test_detected_entities_is_empty_list(self, silver_record):
        """
        detected_entities must be an empty list at Silver layer (check [15]).

        Entity extraction is performed by the Gold Job's OpenAI call (Section 4.2A).
        An empty list — not None — is the contract for 'extraction pending'.
        """
        assert silver_record["detected_entities"] == []

    def test_relevance_score_is_float_in_range(self, silver_record):
        """relevance_score must be a float in [0.0, 1.0] (check [16])."""
        score = silver_record["relevance_score"]
        assert isinstance(score, float), f"relevance_score must be float, got {type(score)}"
        assert 0.0 <= score <= 1.0, f"relevance_score must be in [0.0, 1.0], got {score}"

    def test_high_signal_for_regulation_paper(self, silver_record):
        """
        The mock ArXiv paper (AI regulation + market volatility) must score
        is_high_signal == True (check [17]).

        The title contains 'Regulatory', 'AI Governance', 'Market Volatility' and
        the abstract mentions 'regulation', 'policy', 'intervention' — well above
        the DEFAULT_THRESHOLD. A failure indicates a keyword list mismatch.
        """
        assert silver_record["is_high_signal"] is True, (
            f"ArXiv regulation paper must be high-signal. "
            f"relevance_score={silver_record['relevance_score']}, "
            f"keywords={silver_record['sniper_keywords']}"
        )

    def test_impact_boost_is_always_false(self, silver_record):
        """
        impact_boost must be False for ALL ArXiv papers (check [18]).

        Section B.1 explicit: "No Impact Boost rule (academic papers are not
        breaking-news sources)." This is a hard-coded False in
        map_arxiv_paper_to_silver() — not evaluated from the raw_payload content.
        """
        assert silver_record["impact_boost"] is False, (
            "impact_boost must always be False for ArXiv papers (Section B.1). "
            f"Got: {silver_record['impact_boost']!r}"
        )

    def test_impact_boost_reason_is_always_empty(self, silver_record):
        """
        impact_boost_reason must be '' for all ArXiv papers (check [19]).

        Consistent with impact_boost=False — there is no reason to store
        because no boost is ever applied (Section B.1).
        """
        assert silver_record["impact_boost_reason"] == "", (
            f"impact_boost_reason must be '' for ArXiv. "
            f"Got: {silver_record['impact_boost_reason']!r}"
        )

    def test_authors_is_a_list_in_silver(self, arxiv_raw, silver_record):
        """
        authors must be stored as a list (not a joined string) in the Silver record
        (check [20]).

        The Gold Job's build_arxiv_gold_global_signal() maps authors directly to
        domain_context.authors as a list. Converting it to a string here would
        force the Knowledge Vault to re-parse it, breaking Section 3.2 DRY.
        """
        assert isinstance(silver_record["authors"], list), (
            f"authors must be a list in Silver record, got {type(silver_record['authors'])}"
        )
        assert silver_record["authors"] == arxiv_raw["authors"]

    def test_categories_is_a_list_in_silver(self, arxiv_raw, silver_record):
        """
        categories must be a list in the Silver record (check [21]).

        Maps to domain_context.domain_tags in the Gold record (Section C.5
        ArXiv Academic extension).
        """
        assert isinstance(silver_record["categories"], list), (
            f"categories must be a list, got {type(silver_record['categories'])}"
        )
        assert silver_record["categories"] == arxiv_raw["categories"]

    def test_primary_category_is_string(self, arxiv_raw, silver_record):
        """primary_category must be a non-empty string (check [22])."""
        pc = silver_record.get("primary_category", "")
        assert isinstance(pc, str) and len(pc) > 0
        assert pc == arxiv_raw["primary_category"]

    def test_arxiv_id_matches_raw(self, arxiv_raw, silver_record):
        """arxiv_id in Silver must match raw_payload.arxiv_id (check [23])."""
        assert silver_record["arxiv_id"] == arxiv_raw["arxiv_id"]

    def test_sniper_keywords_is_sorted_list(self, silver_record):
        """
        sniper_keywords must be a sorted list of matched keyword strings (check [24]).

        Sorted order is required for deterministic Silver records — the same paper
        must always produce the same keyword list (Section 4.1A).
        """
        keywords = silver_record["sniper_keywords"]
        assert isinstance(keywords, list)
        assert keywords == sorted(keywords), (
            f"sniper_keywords is not sorted: {keywords}"
        )

    def test_fetch_mode_passed_through(self, arxiv_raw, silver_record):
        """fetch_mode must equal raw_payload.fetch_mode (check [25])."""
        assert silver_record["fetch_mode"] == arxiv_raw["fetch_mode"]

    def test_bronze_ref_equals_envelope_event_id(self, arxiv_envelope, silver_record):
        """
        bronze_ref must equal the Bronze envelope's event_id (check [26]).

        knowledge_vault.py stores bronze_ref so a failed Gold Job run can be
        replayed from the exact Bronze offset (Section 3.2 DRY — trace lineage).
        """
        assert silver_record["bronze_ref"] == arxiv_envelope["event_id"]

    def test_publish_date_matches_raw(self, arxiv_raw, silver_record):
        """publish_date must pass through raw_payload.published unchanged (check [27])."""
        assert silver_record["publish_date"] == arxiv_raw["published"]

    def test_title_matches_raw(self, arxiv_raw, silver_record):
        """title must match raw_payload.title (consumed by knowledge_vault)."""
        assert silver_record["title"] == arxiv_raw["title"]

    def test_pdf_url_passed_through(self, arxiv_raw, silver_record):
        """pdf_url must be passed through from raw_payload."""
        assert silver_record.get("pdf_url") == arxiv_raw["pdf_url"]


# ==========================================================
# Gate 2 — Impact Boost Invariant for ArXiv (Section B.1)
# ==========================================================

class TestArXivImpactBoostAbsent:
    """
    Verifies that impact_boost is ALWAYS False for ArXiv papers, even if
    the abstract contains energy/geopolitical keywords that would trigger
    Impact Boost on a NewsAPI article (Section B.1).
    """

    def test_energy_paper_impact_boost_still_false(self):
        """
        Even if an arXiv paper abstract mentions 'crude oil' and 'Israel' (Impact
        Boost triggers for NewsAPI), impact_boost must remain False.

        Impact Boost is an editorial rule for live news feeds — it does not
        apply to academic research regardless of subject matter (Section B.1).
        """
        raw = {
            "arxiv_id":         "2301.00001v1",
            "url":              "https://arxiv.org/abs/2301.00001",
            "pdf_url":          "https://arxiv.org/pdf/2301.00001",
            "title":            "Israel crude oil market analysis",
            "abstract":         "We study Israel energy policy and crude oil sanctions.",
            "authors":          ["Alice"],
            "published":        "2024-01-01T00:00:00Z",
            "updated":          "2024-01-01T00:00:00Z",
            "categories":       ["econ.GN"],
            "primary_category": "econ.GN",
            "query_category":   "econ.GN",
            "fetch_mode":       "pulse",
            "search_query":     "cat:econ.GN AND (ti:energy OR abs:energy)",
        }
        envelope = _build_envelope(raw)
        _, silver = process_arxiv_message(envelope)
        assert silver["impact_boost"] is False, (
            "impact_boost must be False even for energy/Israel arXiv papers (Section B.1)"
        )
        assert silver["impact_boost_reason"] == ""

    def test_impact_boost_absent_regardless_of_raw_payload_content(self, arxiv_raw):
        """
        map_arxiv_paper_to_silver() must hard-code impact_boost=False regardless
        of what keys or values are present in the raw_payload.

        The raw_payload does not carry an impact_boost key (unlike NewsAPI) and
        the mapper must never derive one from the content.
        """
        raw = dict(arxiv_raw)
        raw["title"] = "Missile defense systems and crude oil geopolitics"
        envelope = _build_envelope(raw)
        _, silver = process_arxiv_message(envelope)
        assert silver["impact_boost"] is False


# ==========================================================
# Gate 2 — Low-Signal Paper Routing (checks [28]–[29])
# ==========================================================

class TestLowSignalArXivRouting:
    """
    Verifies that a low-signal arXiv paper (no keyword matches) still routes
    to SILVER_GLOBAL_NEWS — the Keyword Sniper gates OpenAI enrichment in the
    Gold Job, not Silver emission (Section 4.1A).
    """

    @pytest.fixture(scope="class")
    def low_signal_envelope(self):
        """
        Build a Bronze envelope for a purely mathematical paper that matches
        zero keywords from MASTER_KEYWORD_LIST.
        """
        raw = {
            "arxiv_id":         "2301.99999v1",
            "url":              "https://arxiv.org/abs/2301.99999",
            "pdf_url":          "https://arxiv.org/pdf/2301.99999",
            "title":            "Topological Properties of Graph Manifolds",
            "abstract":         (
                "We prove several new results about the homotopy groups of "
                "smooth four-manifolds using Morse theory and surgery techniques."
            ),
            "authors":          ["John Smith"],
            "published":        "2024-06-01T00:00:00Z",
            "updated":          "2024-06-01T00:00:00Z",
            "categories":       ["math.GT"],
            "primary_category": "math.GT",
            "query_category":   "cs.AI",
            "fetch_mode":       "backfill",
            "search_query":     "cat:cs.AI AND (ti:regulation OR abs:regulation)",
        }
        return _build_envelope(raw)

    def test_low_signal_routes_to_silver_global_news(self, low_signal_envelope):
        """
        A low-signal paper must still route to SILVER_GLOBAL_NEWS (check [28]).

        The Keyword Sniper score gates Gold Job OpenAI enrichment — it NEVER
        blocks Silver emission. All valid papers enter the Knowledge Vault
        (Section 4.1A: 'ALL valid articles emitted to SILVER_GLOBAL_NEWS').
        """
        topic, record = process_arxiv_message(low_signal_envelope)
        assert topic == SILVER_GLOBAL_NEWS, (
            f"Low-signal paper should still reach SILVER_GLOBAL_NEWS. Got '{topic}'"
        )

    def test_low_signal_has_is_high_signal_false(self, low_signal_envelope):
        """
        Low-signal paper must have is_high_signal == False (check [29]).

        The Gold Job reads this flag to skip OpenAI enrichment (Section 4.1A).
        """
        _, record = process_arxiv_message(low_signal_envelope)
        assert record["is_high_signal"] is False

    def test_low_signal_passes_silver_validator(self, low_signal_envelope):
        """Low-signal paper must still pass validate_silver_document()."""
        _, record = process_arxiv_message(low_signal_envelope)
        result = validate_silver_document(record)
        assert result.is_valid, f"Low-signal Silver doc must pass validator: {result.errors}"

    def test_low_signal_impact_boost_still_false(self, low_signal_envelope):
        """Impact boost is always False for arXiv — even low-signal papers."""
        _, record = process_arxiv_message(low_signal_envelope)
        assert record["impact_boost"] is False


# ==========================================================
# Gate 2 — DLQ Correctness (checks [30]–[32])
# ==========================================================

class TestArXivDLQCorrectness:
    """
    Verifies DLQ records are correctly formed when the ArXiv Silver branch
    rejects a message (Section 3.5).
    """

    def test_dlq_source_topic_is_bronze_arxiv(self, arxiv_raw):
        """
        DLQ records from the ArXiv branch must carry source_topic=BRONZE_ARXIV
        (check [30]).

        DLQ operators filter by source_topic to route replay to the correct
        producer/offset (Section 3.5). Wrong source_topic routes replay to
        the wrong consumer group.
        """
        envelope = _build_envelope(arxiv_raw)
        del envelope["event_id"]
        topic, dlq_record = process_arxiv_message(envelope)

        assert topic == DEAD_LETTER_QUEUE
        assert dlq_record["source_topic"] == BRONZE_ARXIV, (
            f"DLQ source_topic must be BRONZE_ARXIV ('{BRONZE_ARXIV}'), "
            f"got '{dlq_record['source_topic']}'"
        )

    def test_dlq_source_topic_not_bronze_newsapi(self, arxiv_raw):
        """
        Regression guard: DLQ from ArXiv branch must NOT carry BRONZE_NEWSAPI.

        _arxiv_dlq_record() must use BRONZE_ARXIV (Section 3.5).
        """
        from config.kafka_topics import BRONZE_NEWSAPI
        envelope = _build_envelope(arxiv_raw)
        del envelope["event_id"]
        _, dlq_record = process_arxiv_message(envelope)
        assert dlq_record["source_topic"] != BRONZE_NEWSAPI

    def test_dlq_record_preserves_original_message(self, arxiv_raw):
        """
        DLQ record must include the original_message for replay (check [31]).

        The corrupted envelope is stored as-is — operators must be able to
        inspect and replay once the schema issue is resolved (Section 3.5).
        """
        envelope = _build_envelope(arxiv_raw)
        del envelope["event_id"]
        _, dlq_record = process_arxiv_message(envelope)

        assert "original_message" in dlq_record
        assert isinstance(dlq_record["original_message"], dict)

    def test_dlq_record_has_validation_errors(self, arxiv_raw):
        """
        DLQ record must include a non-empty validation_errors list (check [32]).

        An empty errors list would make it impossible to diagnose why the
        message was rejected (Section 3.5 DLQ envelope format).
        """
        envelope = _build_envelope(arxiv_raw)
        del envelope["trace_id"]
        _, dlq_record = process_arxiv_message(envelope)

        errors = dlq_record.get("validation_errors", [])
        assert isinstance(errors, list)
        assert len(errors) > 0, "DLQ record must contain at least one validation error"

    def test_dlq_failed_layer_is_silver(self, arxiv_raw):
        """DLQ record must identify failed_layer='Silver' (Section 3.5)."""
        envelope = _build_envelope(arxiv_raw)
        del envelope["event_id"]
        _, dlq_record = process_arxiv_message(envelope)
        assert dlq_record["failed_layer"] == "Silver"

    def test_url_guard_dlq_identifies_stage(self, arxiv_raw):
        """
        DLQ record for a missing-URL failure must identify failed_stage='url_guard'.

        Named stages in DLQ records help operators pinpoint which check failed
        without re-reading the Silver Job code (Section 3.5).
        """
        raw_no_url = {k: v for k, v in arxiv_raw.items() if k != "url"}
        _, dlq_record = process_arxiv_message(_build_envelope(raw_no_url))
        assert dlq_record["failed_stage"] == "url_guard"


# ==========================================================
# Gate 2 — Document Hash Determinism (checks [33]–[34])
# ==========================================================

class TestArXivDocumentHashDeterminism:
    """
    Verifies document_hash is stable across repeated calls and correctly
    differentiates papers (Section 4.1C deduplication).
    """

    def test_same_paper_same_hash(self, arxiv_envelope):
        """
        Calling process_arxiv_message() twice on the same envelope must
        produce the same document_hash (check [33]).

        The Knowledge Vault uses this hash for ON CONFLICT dedup — a
        non-deterministic hash would corrupt the dedup gate.
        """
        _, r1 = process_arxiv_message(arxiv_envelope)
        _, r2 = process_arxiv_message(arxiv_envelope)
        assert r1["document_hash"] == r2["document_hash"]

    def test_different_url_different_hash(self, arxiv_raw):
        """
        Same abstract on a different canonical URL must produce a different hash.

        The dedup key is (abstract, url) — different URL = different paper identity
        even if the abstract text is identical.
        """
        raw_alt = dict(arxiv_raw)
        raw_alt["url"] = "https://arxiv.org/abs/9999.99999"

        _, r_original = process_arxiv_message(_build_envelope(arxiv_raw))
        _, r_alt      = process_arxiv_message(_build_envelope(raw_alt))

        assert r_original["document_hash"] != r_alt["document_hash"], (
            "Different URLs must produce different document hashes"
        )

    def test_doc_id_unique_per_call(self, arxiv_envelope):
        """
        doc_id must be a fresh UUID on each call (check [34]).

        doc_id is the primary key for the Knowledge Vault row — not the dedup key.
        document_hash is the dedup guard; doc_id identifies this specific Silver
        record instance.
        """
        _, r1 = process_arxiv_message(arxiv_envelope)
        _, r2 = process_arxiv_message(arxiv_envelope)
        assert r1["doc_id"] != r2["doc_id"], (
            "doc_id must be a fresh UUID each call — it is a primary key"
        )
