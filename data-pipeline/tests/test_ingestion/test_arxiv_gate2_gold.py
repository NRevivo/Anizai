"""
Gate 2 — Logic Gate: ArXiv Gold Processing Validation (Section 4.2A).

Validates that the Gold Job's ArXiv branch enriches and routes correctly
using mock OpenAI responses — no live OpenAI API, no Flink, no Kafka.

What Gate 2 covers (Section 9.3 Gate 2):
    [1]  High-signal paper routes to GOLD_GLOBAL_NEWS
    [2]  Low-signal (is_high_signal=False) paper → (None, None) — intentional skip
    [3]  Low-signal: OpenAI never called (zero token cost, Section 4.1A)
    [4]  Invalid Silver document → DEAD_LETTER_QUEUE
    [5]  OpenAI cognitive metadata API error → DEAD_LETTER_QUEUE
    [6]  OpenAI embedding API error → DEAD_LETTER_QUEUE
    [7]  Gold output passes validate_gold_signal()
    [8]  signal_id is a non-empty UUID string
    [9]  metadata.canonical_event_id == silver_doc.canonical_event_id
    [10] metadata.source_platform == "arxiv"
    [11] metadata.silver_data_ref == silver_doc.doc_id
    [12] metadata.raw_data_ref == silver_doc.bronze_ref
    [13] enrichment_ai.executive_summary matches the mock response
    [14] enrichment_ai.impact_level is int in [1, 5]
    [15] enrichment_ai.urgency_level is int in [1, 5]
    [16] enrichment_ai.reliability_score is float in [0.0, 1.0]
    [17] enrichment_ai.sentiment_score is float in [-1.0, 1.0]
    [18] enrichment_ai.extracted_entities is a list
    [19] enrichment_ai.topic_classification is a non-empty string
    [20] enrichment_ai.key_findings is a list
    [21] enrichment_ai.fact_check_flag is a bool
    [22] domain_context.authors == silver_doc.authors (list)
    [23] domain_context.is_peer_reviewed == False ALWAYS (structural fact, Section B.1)
    [24] domain_context.citation_count == 0 ALWAYS (not in Atom feed)
    [25] domain_context.domain_tags == silver_doc.categories
    [26] domain_context has NO is_breaking field (NewsAPI Tactical only)
    [27] domain_context has NO sniper_keywords field (NewsAPI Tactical only)
    [28] domain_context has NO share_count field (NewsAPI Tactical only)
    [29] No Impact Boost applied — impact_level == mock AI value (Section B.1)
    [30] Even with energy/geopolitical content, no boost applied
    [31] embedding is a list[float] of length 1536
    [32] DLQ source_topic == SILVER_GLOBAL_NEWS (not SILVER_SOCIAL_PULSE)
    [33] DLQ record has non-empty validation_errors
    [34] signal_id is unique per call

Key ArXiv departures from NewsAPI Gold Gate 2 (Section B.1):
    - build_arxiv_gold_global_signal() used (not build_gold_global_signal())
    - Academic domain_context: authors, is_peer_reviewed=False, citation_count=0, domain_tags
    - No apply_impact_boost() call — ever
    - No is_breaking, no sniper_keywords, no share_count, no geospatial_focus in domain_context
    - geospatial_focus appears only in enrichment_ai (from GPT-4o), not domain_context
    - DLQ source_topic = SILVER_GLOBAL_NEWS (same as NewsAPI — both arrive from global_news)

References:
    - Section 4.2A: Gold Job — Cognitive Metadata Extraction + embeddings
    - Section 4.4:  Mock-Driven Development — tests/mocks/ payloads
    - Section 9.3:  Triple-Gate Test Matrix — Gate 2
    - Section C.5:  Gold Global Signal Schema — ArXiv Academic domain_context
    - Section B.1:  ArXiv parameters — no Impact Boost, is_peer_reviewed=False
    - Section 3.5:  Dead-Letter Queue — never silently drop
"""

import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from config.kafka_topics import (
    BRONZE_ARXIV,
    DEAD_LETTER_QUEUE,
    GOLD_GLOBAL_NEWS,
    SILVER_GLOBAL_NEWS,
    SILVER_SOCIAL_PULSE,
)
from ingestion.arxiv_producer import ARXIV_API_URL, SOURCE_NAME
from processing.gold_job import (
    build_arxiv_gold_global_signal,
    build_cognitive_metadata_prompt,
    process_arxiv_gold_message,
)
from processing.silver_job import process_arxiv_message
from utils.kafka_utils import build_bronze_message
from utils.validators import validate_gold_signal

# ==========================================================
# Paths & Constants
# ==========================================================

MOCKS_DIR      = Path(__file__).parent.parent / "mocks"
_EMBEDDING_DIM = 1536

_ARXIV_ENDPOINT = (
    f"{ARXIV_API_URL}?search_query=cat:cs.AI&start=0&max_results=200"
    f"&sortBy=submittedDate&sortOrder=descending"
)


# ==========================================================
# Helpers
# ==========================================================

def _make_mock_embedding() -> list[float]:
    """Return a plausible 1536-dim embedding (all 0.1 — structurally valid)."""
    return [0.1] * _EMBEDDING_DIM


def _make_openai_client(ai_meta: dict) -> MagicMock:
    """
    Build a MagicMock OpenAI client that returns ai_meta as GPT-4o response
    and a 1536-dim float list for embeddings.

    Injected via the openai_client parameter of process_arxiv_gold_message()
    to keep tests fully self-contained with no network I/O (Section 4.4).
    """
    mock_client = MagicMock()

    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps(ai_meta)
    mock_client.chat.completions.create.return_value = mock_response

    mock_embed_response = MagicMock()
    mock_embed_response.data[0].embedding = _make_mock_embedding()
    mock_client.embeddings.create.return_value = mock_embed_response

    return mock_client


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
    """Load ArXiv mock raw_payload (strip _comment key)."""
    with open(MOCKS_DIR / "arxiv_bronze_payload.json", encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


@pytest.fixture(scope="module")
def mock_ai_meta() -> dict:
    """
    Load the mock GPT-4o Cognitive Metadata response for the ArXiv paper
    (Section 4.4 — openai_arxiv_enrichment.json).

    Used to populate the MagicMock OpenAI client so tests assert against
    realistic AI output: impact_level=3, urgency_level=1, reliability_score=0.87.
    """
    with open(MOCKS_DIR / "openai_arxiv_enrichment.json", encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


@pytest.fixture(scope="module")
def silver_doc_high_signal(arxiv_raw) -> dict:
    """
    Silver document for the mock ArXiv paper, confirmed high-signal.

    The paper (AI regulation + market volatility) must score above DEFAULT_THRESHOLD
    via 'regulation', 'policy', 'intervention' in the title/abstract.
    """
    envelope = _build_envelope(arxiv_raw)
    _, record = process_arxiv_message(envelope)
    assert record["is_high_signal"] is True, (
        "Test setup error: mock ArXiv paper must be high-signal. "
        f"Score={record.get('relevance_score')}, keywords={record.get('sniper_keywords')}"
    )
    return record


@pytest.fixture(scope="module")
def mock_client(mock_ai_meta) -> MagicMock:
    """MagicMock OpenAI client pre-loaded with the mock ArXiv AI metadata."""
    return _make_openai_client(mock_ai_meta)


@pytest.fixture(scope="module")
def gold_record(silver_doc_high_signal, mock_client) -> dict:
    """
    Run process_arxiv_gold_message() with the mock OpenAI client.

    Returns the Gold Global Signal record for structural assertion tests.
    Fails immediately if routing fails — downstream tests depend on a valid Gold record.
    """
    topic, record = process_arxiv_gold_message(silver_doc_high_signal, mock_client)
    assert topic == GOLD_GLOBAL_NEWS, (
        f"Expected GOLD_GLOBAL_NEWS but got '{topic}'. Record: {record}"
    )
    return record


# ==========================================================
# Gate 2 — Routing Tests (checks [1]–[6])
# ==========================================================

class TestArXivGoldRouting:
    """
    Verifies process_arxiv_gold_message() routes to the correct target.
    """

    def test_high_signal_routes_to_gold_global_news(
        self, silver_doc_high_signal, mock_client
    ):
        """High-signal paper must route to GOLD_GLOBAL_NEWS (check [1])."""
        topic, record = process_arxiv_gold_message(silver_doc_high_signal, mock_client)
        assert topic == GOLD_GLOBAL_NEWS
        assert record is not None

    def test_low_signal_returns_none_none(self, arxiv_raw):
        """
        A paper with is_high_signal=False must return (None, None) (check [2]).

        Low-signal papers skip Gold enrichment entirely — zero OpenAI spend.
        They are stored in the Knowledge Vault but without embeddings (Section 4.1A).
        """
        raw = dict(arxiv_raw)
        raw["title"]    = "Topological Properties of Graph Manifolds"
        raw["abstract"] = (
            "We prove new results about homotopy groups using Morse theory."
        )
        raw["url"] = "https://arxiv.org/abs/2301.88888"
        envelope = _build_envelope(raw)
        _, silver = process_arxiv_message(envelope)
        assert silver["is_high_signal"] is False  # confirms setup

        topic, record = process_arxiv_gold_message(silver, _make_openai_client({}))
        assert topic is None
        assert record is None

    def test_low_signal_openai_never_called(self, arxiv_raw):
        """
        OpenAI must never be called when is_high_signal=False (check [3]).

        Confirms the Keyword Sniper guard short-circuits before any API call —
        critical for the 'reducing downstream token costs' guarantee (Section 4.1A).
        """
        raw = dict(arxiv_raw)
        raw["title"]    = "Pure mathematics: convex geometry proofs"
        raw["abstract"] = "Abstract with no market, policy, or signal keywords."
        raw["url"]      = "https://arxiv.org/abs/2301.77777"
        envelope = _build_envelope(raw)
        _, silver = process_arxiv_message(envelope)
        assert silver["is_high_signal"] is False

        spy_client = _make_openai_client({})
        process_arxiv_gold_message(silver, spy_client)
        spy_client.chat.completions.create.assert_not_called()

    def test_invalid_silver_routes_to_dlq(self, silver_doc_high_signal, mock_client):
        """
        A Silver document with a corrupt document_hash must route to DLQ (check [4]).
        """
        corrupted = dict(silver_doc_high_signal)
        corrupted["document_hash"] = "tooshort"  # not 64 chars

        topic, record = process_arxiv_gold_message(corrupted, mock_client)
        assert topic == DEAD_LETTER_QUEUE
        assert "validation_errors" in record

    def test_openai_cognitive_metadata_error_routes_to_dlq(
        self, silver_doc_high_signal
    ):
        """
        An OpenAI API error during cognitive metadata extraction must route to
        DLQ (check [5]).

        Network failures, rate limit errors, and model timeouts raise exceptions.
        The Gold Job must route failures to DLQ rather than crashing the Flink
        task slot (Section 3.5).
        """
        bad_client = MagicMock()
        bad_client.chat.completions.create.side_effect = RuntimeError("rate limit exceeded")
        bad_client.embeddings.create.side_effect = AssertionError("should not reach embedding")

        topic, record = process_arxiv_gold_message(silver_doc_high_signal, bad_client)
        assert topic == DEAD_LETTER_QUEUE
        error_text = " ".join(record.get("validation_errors", []))
        assert "rate limit" in error_text.lower() or "cognitive" in error_text.lower()

    def test_openai_embedding_error_routes_to_dlq(
        self, silver_doc_high_signal, mock_ai_meta
    ):
        """
        An OpenAI API error during embedding generation must route to DLQ (check [6]).

        A Gold record without an embedding cannot be inserted into knowledge_vectors
        (which requires a 1536-dim vector column).
        """
        bad_embed_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices[0].message.content = json.dumps(mock_ai_meta)
        bad_embed_client.chat.completions.create.return_value = mock_response
        bad_embed_client.embeddings.create.side_effect = RuntimeError("embedding service down")

        topic, record = process_arxiv_gold_message(silver_doc_high_signal, bad_embed_client)
        assert topic == DEAD_LETTER_QUEUE
        error_text = " ".join(record.get("validation_errors", []))
        assert "embedding" in error_text.lower()


# ==========================================================
# Gate 2 — Gold Schema Correctness (checks [7]–[21])
# ==========================================================

class TestArXivGoldSchema:
    """
    Verifies the Gold Global Signal output conforms to Section C.5 for ArXiv.
    """

    def test_gold_passes_validator(self, gold_record):
        """
        The Gold output must pass validate_gold_signal() (check [7]).

        Running it again here independently confirms the schema contract,
        not just that the function didn't crash.
        """
        result = validate_gold_signal(gold_record)
        assert result.is_valid, f"Gold signal validation failed: {result.errors}"

    def test_signal_id_is_uuid(self, gold_record):
        """signal_id must be a non-empty UUID string (check [8])."""
        signal_id = gold_record.get("metadata", {}).get("signal_id", "")
        assert isinstance(signal_id, str) and len(signal_id) > 0
        parsed = uuid.UUID(signal_id)
        assert str(parsed) == signal_id

    def test_canonical_event_id_matches_silver(self, silver_doc_high_signal, gold_record):
        """
        metadata.canonical_event_id must equal silver_doc.canonical_event_id (check [9]).

        This is the Paper Trail link — every Gold record traces back to its
        originating Bronze envelope via Silver (Section 3.2 / 7.2).
        """
        assert (
            gold_record["metadata"]["canonical_event_id"]
            == silver_doc_high_signal["canonical_event_id"]
        )

    def test_source_platform_is_arxiv(self, gold_record):
        """metadata.source_platform must be 'arxiv' (check [10])."""
        assert gold_record["metadata"]["source_platform"] == "arxiv"

    def test_silver_data_ref_is_doc_id(self, silver_doc_high_signal, gold_record):
        """
        metadata.silver_data_ref must equal silver_doc.doc_id (check [11]).

        This is the link from the Gold record to the Silver document in
        the knowledge_vault table. knowledge_vectors.py uses it to join
        the embedding back to the article body for RAG retrieval.
        """
        assert (
            gold_record["metadata"]["silver_data_ref"]
            == silver_doc_high_signal["doc_id"]
        )

    def test_raw_data_ref_is_bronze_ref(self, silver_doc_high_signal, gold_record):
        """metadata.raw_data_ref must equal silver_doc.bronze_ref (check [12])."""
        assert (
            gold_record["metadata"]["raw_data_ref"]
            == silver_doc_high_signal["bronze_ref"]
        )

    def test_executive_summary_from_mock(self, gold_record, mock_ai_meta):
        """
        enrichment_ai.executive_summary must equal the mock AI response (check [13]).

        Confirms build_arxiv_gold_global_signal() takes the summary from ai_meta,
        not from the raw abstract text.
        """
        assert (
            gold_record["enrichment_ai"]["executive_summary"]
            == mock_ai_meta["executive_summary"]
        )

    def test_impact_level_is_int_in_range(self, gold_record):
        """enrichment_ai.impact_level must be int in [1, 5] (check [14])."""
        il = gold_record["enrichment_ai"]["impact_level"]
        assert isinstance(il, int), f"impact_level must be int, got {type(il)}"
        assert 1 <= il <= 5, f"impact_level must be in [1, 5], got {il}"

    def test_urgency_level_is_int_in_range(self, gold_record):
        """enrichment_ai.urgency_level must be int in [1, 5] (check [15])."""
        ul = gold_record["enrichment_ai"]["urgency_level"]
        assert isinstance(ul, int)
        assert 1 <= ul <= 5

    def test_reliability_score_is_float_in_range(self, gold_record):
        """enrichment_ai.reliability_score must be float in [0.0, 1.0] (check [16])."""
        rs = gold_record["enrichment_ai"]["reliability_score"]
        assert isinstance(rs, float)
        assert 0.0 <= rs <= 1.0

    def test_sentiment_score_is_float_in_range(self, gold_record):
        """enrichment_ai.sentiment_score must be float in [-1.0, 1.0] (check [17])."""
        ss = gold_record["enrichment_ai"]["sentiment_score"]
        assert isinstance(ss, float)
        assert -1.0 <= ss <= 1.0

    def test_extracted_entities_is_list(self, gold_record):
        """enrichment_ai.extracted_entities must be a list (check [18])."""
        assert isinstance(gold_record["enrichment_ai"]["extracted_entities"], list)

    def test_topic_classification_is_non_empty_string(self, gold_record):
        """enrichment_ai.topic_classification must be a non-empty string (check [19])."""
        tc = gold_record["enrichment_ai"]["topic_classification"]
        assert isinstance(tc, str) and len(tc) > 0

    def test_key_findings_is_a_list(self, gold_record):
        """enrichment_ai.key_findings must be a list (check [20])."""
        assert isinstance(gold_record["enrichment_ai"]["key_findings"], list)

    def test_fact_check_flag_is_bool(self, gold_record):
        """enrichment_ai.fact_check_flag must be a bool (check [21])."""
        assert isinstance(gold_record["enrichment_ai"]["fact_check_flag"], bool)

    def test_embedding_is_1536_dim_float_list(self, gold_record):
        """embedding must be a list[float] of exactly 1536 elements (check [31])."""
        embedding = gold_record.get("embedding", [])
        assert isinstance(embedding, list), "embedding must be a list"
        assert len(embedding) == _EMBEDDING_DIM, (
            f"embedding must be 1536-dim, got {len(embedding)}"
        )
        assert all(isinstance(v, float) for v in embedding[:10]), (
            "embedding elements must be floats"
        )

    def test_content_vitals_url_matches_silver(self, silver_doc_high_signal, gold_record):
        """content_vitals.url must match silver_doc.original_url."""
        assert (
            gold_record["content_vitals"]["url"]
            == silver_doc_high_signal["original_url"]
        )

    def test_content_vitals_title_matches_silver(self, silver_doc_high_signal, gold_record):
        """content_vitals.title must match silver_doc.title."""
        assert gold_record["content_vitals"]["title"] == silver_doc_high_signal["title"]

    def test_description_snippet_is_truncated_abstract(
        self, silver_doc_high_signal, gold_record
    ):
        """
        content_vitals.description_snippet must be the first 300 chars of abstract.

        For ArXiv, inverted_pyramid_lead == abstract. The snippet is capped at 300
        characters to limit Gold record size (Section C.5 rationale).
        """
        snippet = gold_record["content_vitals"]["description_snippet"]
        expected = silver_doc_high_signal["inverted_pyramid_lead"][:300]
        assert snippet == expected

    def test_signal_id_unique_per_call(self, silver_doc_high_signal, mock_ai_meta):
        """
        signal_id must be a fresh UUID on each call (check [34]).

        Each Gold record is a distinct row in knowledge_vectors with its own PK.
        """
        c1 = _make_openai_client(mock_ai_meta)
        _, r1 = process_arxiv_gold_message(silver_doc_high_signal, c1)
        c2 = _make_openai_client(mock_ai_meta)
        _, r2 = process_arxiv_gold_message(silver_doc_high_signal, c2)
        assert r1["metadata"]["signal_id"] != r2["metadata"]["signal_id"]


# ==========================================================
# Gate 2 — ArXiv Academic domain_context (checks [22]–[28])
# ==========================================================

class TestArXivAcademicDomainContext:
    """
    Verifies the Academic domain_context extension (Section C.5 ArXiv row).

    This is the key structural difference between the ArXiv and NewsAPI Gold
    paths: no Tactical fields (is_breaking, sniper_keywords, share_count,
    geospatial_focus in domain_context). Only Academic fields: authors,
    is_peer_reviewed, citation_count, domain_tags.
    """

    def test_domain_context_authors_matches_silver(
        self, silver_doc_high_signal, gold_record
    ):
        """
        domain_context.authors must equal silver_doc.authors (list) (check [22]).

        Preserved as a list for author-based RAG filtering without string splitting.
        """
        assert (
            gold_record["domain_context"]["authors"]
            == silver_doc_high_signal["authors"]
        )

    def test_domain_context_authors_is_a_list(self, gold_record):
        """domain_context.authors must be a list (not a joined string)."""
        assert isinstance(gold_record["domain_context"]["authors"], list), (
            "domain_context.authors must be list — "
            "build_arxiv_gold_global_signal() must NOT join to string"
        )

    def test_domain_context_is_peer_reviewed_false(self, gold_record):
        """
        domain_context.is_peer_reviewed must be False ALWAYS (check [23]).

        arXiv is a pre-print server — papers are not peer-reviewed before
        appearing. This is a structural fact about the source, not an
        OpenAI-assessed quality score (Section C.5, build_arxiv_gold_global_signal
        docstring).
        """
        assert gold_record["domain_context"]["is_peer_reviewed"] is False, (
            "is_peer_reviewed must always be False for arXiv papers — "
            "arXiv is a pre-print server (Section B.1)"
        )

    def test_domain_context_citation_count_zero(self, gold_record):
        """
        domain_context.citation_count must be 0 ALWAYS (check [24]).

        Citation data is not available in the arXiv Atom feed. Zero correctly
        signals 'unknown' to RAG agents (a future sprint may enrich this from
        Semantic Scholar, Section C.5 note).
        """
        assert gold_record["domain_context"]["citation_count"] == 0, (
            "citation_count must be 0 — not available in arXiv Atom feed (Section C.5)"
        )

    def test_domain_context_domain_tags_matches_silver(
        self, silver_doc_high_signal, gold_record
    ):
        """
        domain_context.domain_tags must equal silver_doc.categories (check [25]).

        arXiv categories (e.g., ['cs.AI', 'econ.GN']) ARE the academic domain
        classification — more precise than free-text topic_classification for
        research paper retrieval (Section C.5).
        """
        assert (
            gold_record["domain_context"]["domain_tags"]
            == silver_doc_high_signal["categories"]
        )

    def test_domain_context_has_no_is_breaking_field(self, gold_record):
        """
        domain_context must NOT have an 'is_breaking' field (check [26]).

        is_breaking is part of the NewsAPI Tactical extension only — it maps
        from silver_doc.impact_boost, which is always False for ArXiv.
        Including it here would mislead the RAG agent into treating all ArXiv
        papers as non-breaking news (which is vacuously correct but wrong schema).
        """
        assert "is_breaking" not in gold_record["domain_context"], (
            "domain_context must not have 'is_breaking' (NewsAPI Tactical field)"
        )

    def test_domain_context_has_no_sniper_keywords_field(self, gold_record):
        """
        domain_context must NOT have a 'sniper_keywords' field (check [27]).

        sniper_keywords is part of the NewsAPI Tactical extension. ArXiv Gold
        records expose the category taxonomy via domain_tags instead (Section C.5).
        """
        assert "sniper_keywords" not in gold_record["domain_context"], (
            "domain_context must not have 'sniper_keywords' (NewsAPI Tactical field)"
        )

    def test_domain_context_has_no_share_count_field(self, gold_record):
        """
        domain_context must NOT have a 'share_count' field (check [28]).

        share_count is a social virality metric from NewsAPI. Academic papers
        do not have article-level share counts (Section C.5).
        """
        assert "share_count" not in gold_record["domain_context"], (
            "domain_context must not have 'share_count' (NewsAPI Tactical field)"
        )

    def test_domain_context_has_exactly_four_keys(self, gold_record):
        """
        domain_context must have exactly four keys: authors, is_peer_reviewed,
        citation_count, domain_tags (Section C.5 ArXiv Academic extension).

        Extra keys indicate builder scope creep; missing keys break the schema.
        """
        expected_keys = {"authors", "is_peer_reviewed", "citation_count", "domain_tags"}
        actual_keys   = set(gold_record["domain_context"].keys())
        assert actual_keys == expected_keys, (
            f"domain_context keys mismatch.\n"
            f"  Expected: {sorted(expected_keys)}\n"
            f"  Actual:   {sorted(actual_keys)}"
        )


# ==========================================================
# Gate 2 — No Impact Boost for ArXiv (Section B.1, checks [29]–[30])
# ==========================================================

class TestNoImpactBoostForArXiv:
    """
    Verifies that impact_level is NEVER boosted for ArXiv papers, regardless
    of content. The Gold Job must use the raw GPT-4o value unchanged (Section B.1).
    """

    def test_impact_level_equals_mock_value_no_boost(
        self, silver_doc_high_signal, mock_ai_meta
    ):
        """
        impact_level in the Gold record must equal the mock AI value (check [29]).

        mock_ai_meta has impact_level=3. The silver_doc has impact_boost=False
        (hard-coded for all ArXiv papers). Expected final impact_level = 3 (no +1).

        If Impact Boost were incorrectly applied, impact_level would be 4.
        """
        assert mock_ai_meta["impact_level"] == 3, "Test setup: mock impact_level must be 3"
        assert silver_doc_high_signal["impact_boost"] is False, (
            "Test setup: ArXiv silver_doc must have impact_boost=False"
        )

        client = _make_openai_client(mock_ai_meta)
        _, record = process_arxiv_gold_message(silver_doc_high_signal, client)

        assert record["enrichment_ai"]["impact_level"] == 3, (
            f"Expected impact_level=3 (no boost for ArXiv), "
            f"got {record['enrichment_ai']['impact_level']}"
        )

    def test_energy_paper_impact_level_not_boosted(self, arxiv_raw, mock_ai_meta):
        """
        An arXiv paper about crude oil/energy must NOT get impact_level boosted
        (check [30]).

        Even if a paper would trigger Impact Boost as a NewsAPI article (energy
        subject), ArXiv papers are categorically excluded (Section B.1).
        """
        raw = dict(arxiv_raw)
        raw["title"]    = "Crude oil supply chain disruption and market volatility"
        raw["abstract"] = (
            "We model crude oil supply chain disruptions caused by sanctions "
            "on Israel and their effect on global energy market volatility."
        )
        raw["url"] = "https://arxiv.org/abs/2301.55555"

        envelope = _build_envelope(raw)
        _, silver = process_arxiv_message(envelope)

        # Despite energy/Israel content, impact_boost must still be False
        assert silver["impact_boost"] is False, "ArXiv impact_boost must always be False"

        ai_meta_base_3 = dict(mock_ai_meta)
        ai_meta_base_3["impact_level"] = 3

        client = _make_openai_client(ai_meta_base_3)
        topic, record = process_arxiv_gold_message(silver, client)

        if topic == GOLD_GLOBAL_NEWS:
            # If the paper is high-signal (regulation/policy matches), confirm no boost
            assert record["enrichment_ai"]["impact_level"] == 3, (
                "Energy arXiv paper must NOT receive +1 Impact Boost (Section B.1)"
            )

    def test_build_arxiv_gold_global_signal_does_not_have_impact_boost_logic(
        self, silver_doc_high_signal, mock_ai_meta
    ):
        """
        Calling build_arxiv_gold_global_signal() directly must never apply a boost.

        This confirms the builder itself is free of any apply_impact_boost() call,
        not just that the test fixture has the right content (Section B.1).
        """
        ai_meta = dict(mock_ai_meta)
        ai_meta["impact_level"] = 2  # low score from GPT

        gold = build_arxiv_gold_global_signal(
            silver_doc_high_signal,
            ai_meta,
            _make_mock_embedding(),
        )
        assert gold["enrichment_ai"]["impact_level"] == 2, (
            "build_arxiv_gold_global_signal() must not increment impact_level"
        )


# ==========================================================
# Gate 2 — DLQ Correctness (checks [32]–[33])
# ==========================================================

class TestArXivGoldDLQCorrectness:
    """
    Verifies DLQ records from the ArXiv Gold branch are correctly formed.
    """

    def test_dlq_source_topic_is_silver_global_news(self, silver_doc_high_signal):
        """
        DLQ records from the ArXiv Gold branch must carry
        source_topic=SILVER_GLOBAL_NEWS (check [32]).

        Both ArXiv and NewsAPI papers arrive from SILVER_GLOBAL_NEWS. DLQ
        operators can distinguish by inspecting original_message.source_name.
        """
        bad_client = MagicMock()
        bad_client.chat.completions.create.side_effect = RuntimeError("forced failure")

        topic, dlq_record = process_arxiv_gold_message(silver_doc_high_signal, bad_client)

        assert topic == DEAD_LETTER_QUEUE
        assert dlq_record["source_topic"] == SILVER_GLOBAL_NEWS, (
            f"DLQ source_topic must be SILVER_GLOBAL_NEWS ('{SILVER_GLOBAL_NEWS}'), "
            f"got '{dlq_record['source_topic']}'"
        )

    def test_dlq_source_topic_not_silver_social_pulse(self, silver_doc_high_signal):
        """
        Regression guard: DLQ from ArXiv Gold must NOT use SILVER_SOCIAL_PULSE.

        _arxiv_gold_dlq_record() must use SILVER_GLOBAL_NEWS (Section 3.5).
        """
        bad_client = MagicMock()
        bad_client.chat.completions.create.side_effect = RuntimeError("forced failure")

        _, dlq_record = process_arxiv_gold_message(silver_doc_high_signal, bad_client)
        assert dlq_record["source_topic"] != SILVER_SOCIAL_PULSE

    def test_dlq_source_topic_not_bronze_arxiv(self, silver_doc_high_signal):
        """
        Gold DLQ source_topic must NOT be BRONZE_ARXIV.

        BRONZE_ARXIV is the Silver DLQ source_topic. The Gold DLQ operator
        replays from SILVER_GLOBAL_NEWS (not from Bronze), since the Silver
        record was already built successfully (Section 3.5).
        """
        bad_client = MagicMock()
        bad_client.chat.completions.create.side_effect = RuntimeError("forced failure")

        _, dlq_record = process_arxiv_gold_message(silver_doc_high_signal, bad_client)
        assert dlq_record["source_topic"] != BRONZE_ARXIV

    def test_dlq_has_non_empty_validation_errors(self, silver_doc_high_signal):
        """DLQ record must include a non-empty validation_errors list (check [33])."""
        bad_client = MagicMock()
        bad_client.chat.completions.create.side_effect = RuntimeError("test error")

        _, dlq_record = process_arxiv_gold_message(silver_doc_high_signal, bad_client)

        errors = dlq_record.get("validation_errors", [])
        assert isinstance(errors, list)
        assert len(errors) > 0

    def test_dlq_failed_layer_is_gold(self, silver_doc_high_signal):
        """DLQ record must identify failed_layer='Gold'."""
        bad_client = MagicMock()
        bad_client.chat.completions.create.side_effect = RuntimeError("test")

        _, dlq_record = process_arxiv_gold_message(silver_doc_high_signal, bad_client)
        assert dlq_record["failed_layer"] == "Gold"

    def test_dlq_preserves_original_message(self, silver_doc_high_signal):
        """DLQ record must include the original Silver document for replay."""
        bad_client = MagicMock()
        bad_client.chat.completions.create.side_effect = RuntimeError("test")

        _, dlq_record = process_arxiv_gold_message(silver_doc_high_signal, bad_client)
        assert "original_message" in dlq_record
        assert isinstance(dlq_record["original_message"], dict)


# ==========================================================
# Gate 2 — Cognitive Metadata Prompt
# ==========================================================

class TestCognitiveMetadataPromptForArXiv:
    """
    Validates build_cognitive_metadata_prompt() works correctly for ArXiv Silver
    documents (shared utility used by both NewsAPI and ArXiv Gold paths).
    """

    def test_prompt_contains_title(self, silver_doc_high_signal):
        """Prompt must include the paper title."""
        prompt = build_cognitive_metadata_prompt(silver_doc_high_signal)
        assert silver_doc_high_signal["title"] in prompt

    def test_prompt_contains_abstract_as_lead(self, silver_doc_high_signal):
        """Prompt must include inverted_pyramid_lead (= abstract for ArXiv)."""
        prompt = build_cognitive_metadata_prompt(silver_doc_high_signal)
        assert silver_doc_high_signal["inverted_pyramid_lead"] in prompt

    def test_prompt_contains_full_text_raw(self, silver_doc_high_signal):
        """Prompt must include full_text_raw (= abstract for ArXiv)."""
        prompt = build_cognitive_metadata_prompt(silver_doc_high_signal)
        assert silver_doc_high_signal["full_text_raw"] in prompt

    def test_prompt_is_string(self, silver_doc_high_signal):
        """build_cognitive_metadata_prompt() must return a non-empty str."""
        result = build_cognitive_metadata_prompt(silver_doc_high_signal)
        assert isinstance(result, str) and len(result) > 0
