"""
Gate 2 — Logic Gate: NewsAPI Gold Processing Validation (Section 4.2A).

Validates that the Gold Job's NewsAPI branch enriches and routes correctly
using mock OpenAI responses — no live OpenAI API, no Flink, no Kafka.

What Gate 2 covers (Section 9.3 Gate 2):
    [1]  High-signal article routes to GOLD_GLOBAL_NEWS
    [2]  Low-signal (is_high_signal=False) article → (None, None) — intentional skip
    [3]  Silver document validation failure → DEAD_LETTER_QUEUE
    [4]  OpenAI cognitive metadata API error → DEAD_LETTER_QUEUE
    [5]  OpenAI embedding API error → DEAD_LETTER_QUEUE
    [6]  Gold output passes validate_gold_signal()
    [7]  signal_id is a non-empty UUID string
    [8]  metadata.canonical_event_id == silver_doc.canonical_event_id
    [9]  metadata.source_platform == "newsapi"
    [10] metadata.silver_data_ref == silver_doc.doc_id
    [11] metadata.raw_data_ref == silver_doc.bronze_ref
    [12] enrichment_ai.executive_summary matches the mock response
    [13] enrichment_ai.impact_level is int in [1, 5]
    [14] enrichment_ai.urgency_level is int in [1, 5]
    [15] enrichment_ai.reliability_score is float in [0.0, 1.0]
    [16] enrichment_ai.sentiment_score is float in [-1.0, 1.0]
    [17] enrichment_ai.extracted_entities is a list
    [18] enrichment_ai.topic_classification is a non-empty string
    [19] Impact Boost rule: impact_boost=True → impact_level boosted by 1 (max 5)
    [20] Impact Boost does not raise above 5
    [21] No impact boost when impact_boost=False
    [22] domain_context.sniper_keywords matches silver_doc.sniper_keywords
    [23] domain_context.is_breaking == silver_doc.impact_boost
    [24] domain_context.geospatial_focus matches mock response
    [25] embedding is a list[float] of length 1536
    [26] DLQ source_topic == SILVER_GLOBAL_NEWS (not SILVER_SOCIAL_PULSE)
    [27] DLQ record has non-empty validation_errors

Test strategy:
    All OpenAI calls are intercepted by a mock client (unittest.mock.MagicMock).
    The mock is injected via the openai_client parameter of process_newsapi_gold_message()
    and call_openai_cognitive_metadata(). Silver documents are built by calling
    process_newsapi_message() on mock Bronze envelopes — the same path the pipeline
    uses, ensuring structural consistency. No Flink, no network I/O.

References:
    - Section 4.2A: Gold Job — Cognitive Metadata Extraction + embeddings
    - Section 4.4:  Mock-Driven Development — tests/mocks/ payloads
    - Section 9.3:  Triple-Gate Test Matrix — Gate 2
    - Section C.5:  Gold Global Signal Schema
    - Section B.4:  Impact Boost rule (+1 impact_level for energy/geopolitical)
    - Section 3.5:  Dead-Letter Queue — never silently drop
"""

import json
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from config.kafka_topics import (
    DEAD_LETTER_QUEUE,
    GOLD_GLOBAL_NEWS,
    SILVER_GLOBAL_NEWS,
    SILVER_SOCIAL_PULSE,
)
from ingestion.newsapi_producer import SOURCE_NAME, TOP_HEADLINES_ENDPOINT
from processing.gold_job import (
    apply_impact_boost,
    build_cognitive_metadata_prompt,
    build_gold_global_signal,
    process_newsapi_gold_message,
)
from processing.silver_job import process_newsapi_message
from utils.kafka_utils import build_bronze_message
from utils.validators import validate_gold_signal

# ==========================================================
# Paths
# ==========================================================

MOCKS_DIR       = Path(__file__).parent.parent / "mocks"
_NEWSAPI_ENDPOINT = TOP_HEADLINES_ENDPOINT + "?category=business&language=en"
_EMBEDDING_DIM  = 1536


# ==========================================================
# Helpers
# ==========================================================

def _make_mock_embedding() -> list[float]:
    """Return a plausible 1536-dim embedding (all 0.1 — structurally valid)."""
    return [0.1] * _EMBEDDING_DIM


def _make_openai_client(ai_meta: dict) -> MagicMock:
    """
    Build a MagicMock OpenAI client that returns the given ai_meta dict as
    the GPT-4o response and a 1536-dim zero vector for embeddings.

    Why MagicMock instead of monkeypatching: injecting the client via the
    openai_client parameter keeps the test environment entirely self-contained
    and avoids patching import paths that could break on package refactors
    (Section 4.4 Mock-Driven Development).
    """
    mock_client = MagicMock()

    # chat.completions.create → message.content = JSON string
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps(ai_meta)
    mock_client.chat.completions.create.return_value = mock_response

    # embeddings.create → data[0].embedding = 1536-dim float list
    mock_embed_response = MagicMock()
    mock_embed_response.data[0].embedding = _make_mock_embedding()
    mock_client.embeddings.create.return_value = mock_embed_response

    return mock_client


# ==========================================================
# Fixtures
# ==========================================================

@pytest.fixture(scope="module")
def article_raw() -> dict:
    """Load the NewsAPI mock raw_payload (strip _comment key)."""
    with open(MOCKS_DIR / "newsapi_article.json", encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


@pytest.fixture(scope="module")
def mock_ai_meta() -> dict:
    """
    Load the mock GPT-4o Cognitive Metadata response (Section 4.4).

    Used to populate the MagicMock OpenAI client so tests assert against
    realistic AI output rather than arbitrary values.
    """
    with open(MOCKS_DIR / "openai_newsapi_enrichment.json", encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


@pytest.fixture(scope="module")
def silver_doc_high_signal(article_raw) -> dict:
    """Silver document confirmed to be high-signal (OPEC article)."""
    envelope = build_bronze_message(
        source_name=SOURCE_NAME,
        source_endpoint=_NEWSAPI_ENDPOINT,
        raw_payload=article_raw,
    )
    _, record = process_newsapi_message(envelope)
    assert record["is_high_signal"] is True, (
        "Test setup error: OPEC mock article must be high-signal. "
        f"Score={record.get('relevance_score')}"
    )
    return record


@pytest.fixture(scope="module")
def mock_client(mock_ai_meta) -> MagicMock:
    """MagicMock OpenAI client pre-loaded with the mock AI metadata."""
    return _make_openai_client(mock_ai_meta)


@pytest.fixture(scope="module")
def gold_record(silver_doc_high_signal, mock_client) -> dict:
    """
    Run process_newsapi_gold_message() with the mock OpenAI client.

    Returns the Gold Global Signal record for structural assertion tests.
    Fails immediately if routing fails — downstream tests depend on a valid
    Gold record.
    """
    topic, record = process_newsapi_gold_message(silver_doc_high_signal, mock_client)
    assert topic == GOLD_GLOBAL_NEWS, (
        f"Expected GOLD_GLOBAL_NEWS but got '{topic}'. Record: {record}"
    )
    return record


# ==========================================================
# Gate 2 — Routing Tests (checks [1]–[5])
# ==========================================================

class TestNewsAPIGoldRouting:
    """
    Verifies process_newsapi_gold_message() routes to the correct target.
    """

    def test_high_signal_routes_to_gold_global_news(
        self, silver_doc_high_signal, mock_client
    ):
        """
        A high-signal article must route to GOLD_GLOBAL_NEWS (check [1]).

        GOLD_GLOBAL_NEWS is the only target for NewsAPI Gold enrichment.
        """
        topic, record = process_newsapi_gold_message(silver_doc_high_signal, mock_client)
        assert topic == GOLD_GLOBAL_NEWS
        assert record is not None

    def test_low_signal_returns_none_none(self, article_raw):
        """
        An article with is_high_signal=False must return (None, None) (check [2]).

        Low-signal articles skip Gold enrichment entirely — zero OpenAI spend.
        The Keyword Sniper score gates this (Section 4.1A). The article is still
        stored in the Knowledge Vault but without embeddings.
        """
        raw_sports = dict(article_raw)
        # Overwrite with a sports article that scores 0.0 on the Keyword Sniper
        raw_sports["title"]       = "Arsenal beat Chelsea 3-1 in Premier League"
        raw_sports["description"] = "A stunning first-half display at Emirates Stadium."
        raw_sports["content"]     = "Arsenal scored three goals in the first half."
        raw_sports["url"]         = "https://bbc.com/sport/arsenal-chelsea-2026"

        envelope = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=_NEWSAPI_ENDPOINT,
            raw_payload=raw_sports,
        )
        _, silver = process_newsapi_message(envelope)
        assert silver["is_high_signal"] is False  # confirms setup

        mock_client = _make_openai_client({})  # should never be called
        topic, record = process_newsapi_gold_message(silver, mock_client)
        assert topic is None
        assert record is None

    def test_low_signal_openai_never_called(self, article_raw):
        """
        The OpenAI client must never be called when is_high_signal=False.

        Confirms that the Keyword Sniper guard short-circuits before any API
        call — critical for the 'reducing downstream token costs' guarantee
        (Section 4.1A).
        """
        raw_sports = dict(article_raw)
        raw_sports["title"]       = "Local marathon raises charity funds"
        raw_sports["description"] = "Hundreds of runners completed the route."
        raw_sports["content"]     = "The annual marathon took place on Sunday."
        raw_sports["url"]         = "https://bbc.com/sport/marathon-2026"

        envelope = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=_NEWSAPI_ENDPOINT,
            raw_payload=raw_sports,
        )
        _, silver = process_newsapi_message(envelope)
        assert silver["is_high_signal"] is False

        spy_client = _make_openai_client({})
        process_newsapi_gold_message(silver, spy_client)
        spy_client.chat.completions.create.assert_not_called()

    def test_invalid_silver_routes_to_dlq(self, silver_doc_high_signal, mock_client):
        """
        A Silver document with a corrupt document_hash must route to DLQ (check [3]).

        validate_silver_document() checks len(document_hash) == 64.
        A truncated hash fails this check and should be caught before OpenAI.
        """
        corrupted = dict(silver_doc_high_signal)
        corrupted["document_hash"] = "tooshort"   # not 64 chars

        topic, record = process_newsapi_gold_message(corrupted, mock_client)
        assert topic == DEAD_LETTER_QUEUE
        assert "validation_errors" in record

    def test_openai_cognitive_metadata_error_routes_to_dlq(self, silver_doc_high_signal):
        """
        An OpenAI API error during cognitive metadata extraction must route to DLQ (check [4]).

        Why test this: network failures, rate limit errors, and model timeouts all
        raise exceptions. The Gold Job must route failures to DLQ rather than
        crashing the Flink task slot (Section 3.5).
        """
        bad_client = MagicMock()
        bad_client.chat.completions.create.side_effect = RuntimeError("rate limit exceeded")
        # embeddings must not be called when chat fails
        bad_client.embeddings.create.side_effect = AssertionError("should not reach embedding")

        topic, record = process_newsapi_gold_message(silver_doc_high_signal, bad_client)
        assert topic == DEAD_LETTER_QUEUE
        error_text = " ".join(record.get("validation_errors", []))
        assert "rate limit" in error_text.lower() or "cognitive" in error_text.lower()

    def test_openai_embedding_error_routes_to_dlq(self, silver_doc_high_signal, mock_ai_meta):
        """
        An OpenAI API error during embedding generation must route to DLQ (check [5]).

        The embedding step follows the cognitive metadata step — an error here
        means the Gold record cannot be built (missing the vector) and must not
        be emitted to knowledge_vectors without an embedding.
        """
        bad_embed_client = MagicMock()
        # Chat succeeds → embedding fails
        mock_response = MagicMock()
        mock_response.choices[0].message.content = json.dumps(mock_ai_meta)
        bad_embed_client.chat.completions.create.return_value = mock_response
        bad_embed_client.embeddings.create.side_effect = RuntimeError("embedding service down")

        topic, record = process_newsapi_gold_message(silver_doc_high_signal, bad_embed_client)
        assert topic == DEAD_LETTER_QUEUE
        error_text = " ".join(record.get("validation_errors", []))
        assert "embedding" in error_text.lower()


# ==========================================================
# Gate 2 — Gold Schema Correctness (checks [6]–[25])
# ==========================================================

class TestNewsAPIGoldSchema:
    """
    Verifies the Gold Global Signal output conforms to Section C.5.
    """

    def test_gold_passes_validator(self, gold_record):
        """
        The Gold output must pass validate_gold_signal() — the same validator
        called inside process_newsapi_gold_message() (check [6]).

        Running it again here independently confirms the schema contract, not
        just that the function didn't crash.
        """
        result = validate_gold_signal(gold_record)
        assert result.is_valid, f"Gold signal validation failed: {result.errors}"

    def test_signal_id_is_uuid(self, gold_record):
        """signal_id must be a non-empty UUID string (check [7])."""
        signal_id = gold_record.get("metadata", {}).get("signal_id", "")
        assert isinstance(signal_id, str) and len(signal_id) > 0
        parsed = uuid.UUID(signal_id)
        assert str(parsed) == signal_id

    def test_canonical_event_id_matches_silver(self, silver_doc_high_signal, gold_record):
        """
        metadata.canonical_event_id must equal silver_doc.canonical_event_id (check [8]).

        This is the Paper Trail link — every Gold record traces back to its
        originating Bronze envelope via Silver (Section 3.2 / 7.2).
        """
        assert (
            gold_record["metadata"]["canonical_event_id"]
            == silver_doc_high_signal["canonical_event_id"]
        )

    def test_source_platform_is_newsapi(self, gold_record):
        """metadata.source_platform must be 'newsapi' (check [9])."""
        assert gold_record["metadata"]["source_platform"] == "newsapi"

    def test_silver_data_ref_is_doc_id(self, silver_doc_high_signal, gold_record):
        """
        metadata.silver_data_ref must equal silver_doc.doc_id (check [10]).

        This is the link from the Gold record to the Silver document in
        the knowledge_vault table. knowledge_vectors.py uses it to join
        the embedding back to the article body for RAG retrieval.
        """
        assert (
            gold_record["metadata"]["silver_data_ref"]
            == silver_doc_high_signal["doc_id"]
        )

    def test_raw_data_ref_is_bronze_ref(self, silver_doc_high_signal, gold_record):
        """metadata.raw_data_ref must equal silver_doc.bronze_ref (check [11])."""
        assert (
            gold_record["metadata"]["raw_data_ref"]
            == silver_doc_high_signal["bronze_ref"]
        )

    def test_executive_summary_from_mock(self, gold_record, mock_ai_meta):
        """
        enrichment_ai.executive_summary must equal the mock AI response (check [12]).

        Confirms that build_gold_global_signal() takes the summary from ai_meta,
        not from the raw article text.
        """
        assert (
            gold_record["enrichment_ai"]["executive_summary"]
            == mock_ai_meta["executive_summary"]
        )

    def test_impact_level_is_int_in_range(self, gold_record):
        """enrichment_ai.impact_level must be int in [1, 5] (check [13])."""
        il = gold_record["enrichment_ai"]["impact_level"]
        assert isinstance(il, int), f"impact_level must be int, got {type(il)}"
        assert 1 <= il <= 5, f"impact_level must be in [1, 5], got {il}"

    def test_urgency_level_is_int_in_range(self, gold_record):
        """enrichment_ai.urgency_level must be int in [1, 5] (check [14])."""
        ul = gold_record["enrichment_ai"]["urgency_level"]
        assert isinstance(ul, int)
        assert 1 <= ul <= 5

    def test_reliability_score_is_float_in_range(self, gold_record):
        """enrichment_ai.reliability_score must be float in [0.0, 1.0] (check [15])."""
        rs = gold_record["enrichment_ai"]["reliability_score"]
        assert isinstance(rs, float)
        assert 0.0 <= rs <= 1.0

    def test_sentiment_score_is_float_in_range(self, gold_record):
        """enrichment_ai.sentiment_score must be float in [-1.0, 1.0] (check [16])."""
        ss = gold_record["enrichment_ai"]["sentiment_score"]
        assert isinstance(ss, float)
        assert -1.0 <= ss <= 1.0

    def test_extracted_entities_is_list(self, gold_record):
        """enrichment_ai.extracted_entities must be a list (check [17])."""
        assert isinstance(gold_record["enrichment_ai"]["extracted_entities"], list)

    def test_topic_classification_is_non_empty_string(self, gold_record):
        """enrichment_ai.topic_classification must be a non-empty string (check [18])."""
        tc = gold_record["enrichment_ai"]["topic_classification"]
        assert isinstance(tc, str) and len(tc) > 0

    def test_domain_context_sniper_keywords(self, silver_doc_high_signal, gold_record):
        """
        domain_context.sniper_keywords must equal silver_doc.sniper_keywords (check [22]).

        The Gold record carries forward the sniper keywords so the RAG agent
        can filter by keyword domain without re-running the Keyword Sniper.
        """
        assert (
            gold_record["domain_context"]["sniper_keywords"]
            == silver_doc_high_signal["sniper_keywords"]
        )

    def test_domain_context_is_breaking_matches_impact_boost(
        self, silver_doc_high_signal, gold_record
    ):
        """
        domain_context.is_breaking must equal silver_doc.impact_boost (check [23]).

        is_breaking is a RAG-layer convenience flag — the LangGraph Hub can
        prioritise breaking news signals without parsing the full enrichment_ai block.
        """
        assert (
            gold_record["domain_context"]["is_breaking"]
            == silver_doc_high_signal["impact_boost"]
        )

    def test_domain_context_geospatial_focus_from_ai(self, gold_record, mock_ai_meta):
        """
        domain_context.geospatial_focus must equal the geospatial_focus in the
        mock AI response (check [24]).
        """
        assert (
            gold_record["domain_context"]["geospatial_focus"]
            == mock_ai_meta["geospatial_focus"]
        )

    def test_embedding_is_1536_dim_float_list(self, gold_record):
        """
        embedding must be a list[float] of length 1536 (check [25]).

        HNSW index in the knowledge_vectors table is built for dim=1536
        (text-embedding-3-small). A wrong dimension fails the pgvector insert.
        """
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

    def test_signal_id_unique_per_call(self, silver_doc_high_signal, mock_ai_meta):
        """
        signal_id must be a fresh UUID on each call — each Gold record is
        a distinct row in knowledge_vectors with its own primary key.
        """
        client = _make_openai_client(mock_ai_meta)
        _, r1 = process_newsapi_gold_message(silver_doc_high_signal, client)
        client2 = _make_openai_client(mock_ai_meta)
        _, r2 = process_newsapi_gold_message(silver_doc_high_signal, client2)
        assert r1["metadata"]["signal_id"] != r2["metadata"]["signal_id"]


# ==========================================================
# Gate 2 — Impact Boost Rule (checks [19]–[21])
# ==========================================================

class TestImpactBoostRule:
    """
    Verifies the apply_impact_boost() rule and its integration in the Gold path
    (Section B.4 — Impact Boost: +1 impact_level, capped at 5).
    """

    def test_impact_boost_increments_by_one(self, mock_ai_meta):
        """
        When impact_boost=True, apply_impact_boost() must increment
        impact_level by exactly 1 (check [19]).
        """
        ai_meta = dict(mock_ai_meta)
        ai_meta["impact_level"] = 3   # GPT-4o scored 3
        silver = {"impact_boost": True}
        result = apply_impact_boost(ai_meta, silver)
        assert result["impact_level"] == 4, (
            f"impact_boost=True with base 3 must yield 4, got {result['impact_level']}"
        )

    def test_impact_boost_capped_at_5(self, mock_ai_meta):
        """
        When impact_boost=True and impact_level is already 5, the boosted
        value must remain 5 — not 6 (check [20]).

        Impact Boost must never push impact_level out of the [1, 5] range
        that validate_gold_signal() enforces (Section C.5).
        """
        ai_meta = dict(mock_ai_meta)
        ai_meta["impact_level"] = 5   # GPT-4o already at maximum
        silver = {"impact_boost": True}
        result = apply_impact_boost(ai_meta, silver)
        assert result["impact_level"] == 5, (
            f"impact_boost with base=5 must stay at 5, got {result['impact_level']}"
        )

    def test_no_boost_when_impact_boost_false(self, mock_ai_meta):
        """
        When impact_boost=False, impact_level must remain unchanged (check [21]).

        The OPEC article carries impact_boost=True, but other sources (e.g.,
        technology regulatory articles) should not receive the boost.
        """
        ai_meta = dict(mock_ai_meta)
        ai_meta["impact_level"] = 3
        silver = {"impact_boost": False}
        result = apply_impact_boost(ai_meta, silver)
        assert result["impact_level"] == 3

    def test_no_boost_when_impact_boost_missing(self, mock_ai_meta):
        """
        When impact_boost key is absent, impact_level must remain unchanged.

        Defensive guard — not all Silver documents are guaranteed to carry
        the impact_boost key (ArXiv papers, Phase 4 sources).
        """
        ai_meta = dict(mock_ai_meta)
        ai_meta["impact_level"] = 2
        silver = {}   # no impact_boost key
        result = apply_impact_boost(ai_meta, silver)
        assert result["impact_level"] == 2

    def test_impact_boost_applied_in_gold_record(
        self, silver_doc_high_signal, mock_ai_meta
    ):
        """
        The full process_newsapi_gold_message() path must apply the boost.

        The mock AI response has impact_level=4. The OPEC article has
        impact_boost=True → expected final impact_level = 5.
        """
        # Confirm the fixture setup (mock gives 4, silver has impact_boost=True)
        assert mock_ai_meta["impact_level"] == 4
        assert silver_doc_high_signal["impact_boost"] is True

        client = _make_openai_client(mock_ai_meta)
        _, record = process_newsapi_gold_message(silver_doc_high_signal, client)

        assert record["enrichment_ai"]["impact_level"] == 5, (
            f"Expected impact_level=5 after boost (4+1), "
            f"got {record['enrichment_ai']['impact_level']}"
        )


# ==========================================================
# Gate 2 — DLQ Correctness (checks [26]–[27])
# ==========================================================

class TestNewsAPIGoldDLQCorrectness:
    """
    Verifies DLQ records from the NewsAPI Gold branch are correctly formed.
    """

    def test_dlq_source_topic_is_silver_global_news(self, silver_doc_high_signal):
        """
        DLQ records from the NewsAPI Gold branch must carry
        source_topic=SILVER_GLOBAL_NEWS (check [26]).

        SILVER_GLOBAL_NEWS is the source topic for replay — the DLQ operator
        re-reads from this topic to replay failed Gold enrichments. Using
        SILVER_SOCIAL_PULSE would replay to the wrong consumer group.
        """
        bad_client = MagicMock()
        bad_client.chat.completions.create.side_effect = RuntimeError("forced failure")

        topic, dlq_record = process_newsapi_gold_message(silver_doc_high_signal, bad_client)

        assert topic == DEAD_LETTER_QUEUE
        assert dlq_record["source_topic"] == SILVER_GLOBAL_NEWS, (
            f"DLQ source_topic must be SILVER_GLOBAL_NEWS ('{SILVER_GLOBAL_NEWS}'), "
            f"got '{dlq_record['source_topic']}'"
        )

    def test_dlq_source_topic_not_silver_social_pulse(self, silver_doc_high_signal):
        """
        Regression guard: DLQ from NewsAPI Gold must NOT use SILVER_SOCIAL_PULSE.

        _newsapi_gold_dlq_record() must use SILVER_GLOBAL_NEWS, not the
        Polymarket _dlq_record() constant (Section 3.5).
        """
        bad_client = MagicMock()
        bad_client.chat.completions.create.side_effect = RuntimeError("forced failure")

        _, dlq_record = process_newsapi_gold_message(silver_doc_high_signal, bad_client)
        assert dlq_record["source_topic"] != SILVER_SOCIAL_PULSE

    def test_dlq_has_non_empty_validation_errors(self, silver_doc_high_signal):
        """
        DLQ record must include a non-empty validation_errors list (check [27]).

        Operators need to know WHY the message was rejected to decide whether
        to replay or discard (Section 3.5 DLQ envelope format).
        """
        bad_client = MagicMock()
        bad_client.chat.completions.create.side_effect = RuntimeError("test error")

        _, dlq_record = process_newsapi_gold_message(silver_doc_high_signal, bad_client)

        errors = dlq_record.get("validation_errors", [])
        assert isinstance(errors, list)
        assert len(errors) > 0

    def test_dlq_failed_layer_is_gold(self, silver_doc_high_signal):
        """DLQ record must identify failed_layer='Gold'."""
        bad_client = MagicMock()
        bad_client.chat.completions.create.side_effect = RuntimeError("test")

        _, dlq_record = process_newsapi_gold_message(silver_doc_high_signal, bad_client)
        assert dlq_record["failed_layer"] == "Gold"

    def test_dlq_preserves_original_message(self, silver_doc_high_signal):
        """DLQ record must include the original Silver document for replay."""
        bad_client = MagicMock()
        bad_client.chat.completions.create.side_effect = RuntimeError("test")

        _, dlq_record = process_newsapi_gold_message(silver_doc_high_signal, bad_client)
        assert "original_message" in dlq_record
        assert isinstance(dlq_record["original_message"], dict)


# ==========================================================
# Gate 2 — Prompt Builder
# ==========================================================

class TestCognitiveMetadataPrompt:
    """
    Validates build_cognitive_metadata_prompt() produces the expected structure.
    """

    def test_prompt_contains_title(self, silver_doc_high_signal):
        """Prompt must include the article headline."""
        prompt = build_cognitive_metadata_prompt(silver_doc_high_signal)
        assert silver_doc_high_signal["title"] in prompt

    def test_prompt_contains_lead(self, silver_doc_high_signal):
        """Prompt must include the inverted_pyramid_lead (lede sentence)."""
        prompt = build_cognitive_metadata_prompt(silver_doc_high_signal)
        assert silver_doc_high_signal["inverted_pyramid_lead"] in prompt

    def test_prompt_contains_body(self, silver_doc_high_signal):
        """Prompt must include full_text_raw (article body)."""
        prompt = build_cognitive_metadata_prompt(silver_doc_high_signal)
        assert silver_doc_high_signal["full_text_raw"] in prompt

    def test_prompt_is_string(self, silver_doc_high_signal):
        """build_cognitive_metadata_prompt() must return a str, not bytes or None."""
        result = build_cognitive_metadata_prompt(silver_doc_high_signal)
        assert isinstance(result, str) and len(result) > 0
