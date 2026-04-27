"""
Gate 3 — Persistence Gate: NewsAPI knowledge_vault + knowledge_vectors (Section 5.1 / 5.2).

Verifies that the full NewsAPI pipeline (Silver → knowledge_vault, Gold → knowledge_vectors)
writes correctly to a live PostgreSQL database and that all fetch helpers return
accurate, queryable data.

Requires: PostgreSQL container running with tables initialised.
    docker compose -f infrastructure/docker-compose.yml up -d postgres

What Gate 3 covers (Section 9.3 Gate 3):

knowledge_vault (checks [1]–[10]):
    [1]  archive() inserts a Silver document and returns a doc_id string
    [2]  archive() with duplicate document_hash returns None (dedup guard, Section 4.1C)
    [3]  exists_by_document_hash() returns True after insert
    [4]  exists_by_document_hash() returns False for an unknown hash
    [5]  fetch_by_doc_id() retrieves the inserted row
    [6]  fetch_by_doc_id returns correct document_hash, source_name, original_url
    [7]  fetch_by_doc_id returns correct full_text_raw and relevance_score
    [8]  fetch_by_canonical_event() returns the inserted row for matching id
    [9]  update_detected_entities() writes entities and fetch_by_doc_id reflects them
    [10] archive() raises ValueError for unknown source_name

knowledge_vectors (checks [11]–[20]):
    [11] insert() persists a Gold Global Signal and returns a signal_id
    [12] insert() is idempotent — ON CONFLICT (signal_id) DO NOTHING
    [13] exists_by_signal_id() returns True after insert
    [14] exists_by_signal_id() returns False for an unknown signal_id
    [15] fetch_by_signal_id() retrieves the correct source_platform and entry_type
    [16] fetch_by_signal_id() retrieves correct enrichment_ai (impact_level, etc.)
    [17] fetch_by_signal_id() returns domain_context with sniper_keywords
    [18] fetch_by_canonical_event() returns the inserted row
    [19] similarity_search() returns results with a 'similarity' float field
    [20] insert() raises ValueError for wrong embedding dimension

Architecture note — test isolation:
    Each test record uses canonical_event_id = "test_{run_id}_{label}" so
    the cleanup fixtures (cleanup_knowledge_vault, cleanup_knowledge_vectors)
    can delete all test rows by prefix after each test. No production data touched.

References:
    - Section 5.1:  Knowledge Vault specification
    - Section 5.2:  Vector Intelligence — pgvector with HNSW
    - Section 4.1C: SHA-256 deduplication (document_hash UNIQUE guard)
    - Section C.3:  Silver Full-Text Document Store schema
    - Section C.5:  Gold Global Signal Schema
    - Section 9.3:  Triple-Gate Test Matrix — Gate 3
"""

import uuid
import json
from pathlib import Path

import pytest

from persistence.knowledge_vault import (
    archive,
    exists_by_document_hash,
    fetch_by_canonical_event,
    fetch_by_doc_id,
    update_detected_entities,
)
from persistence.knowledge_vectors import (
    EMBEDDING_DIM,
    exists_by_signal_id,
    fetch_by_canonical_event as kv_fetch_by_canonical_event,
    fetch_by_signal_id,
    insert,
    similarity_search,
)

# ==========================================================
# Fixtures & Helpers
# ==========================================================

pytestmark = pytest.mark.usefixtures("db_available")

MOCKS_DIR = Path(__file__).parent.parent / "mocks"


def _make_embedding(value: float = 0.1) -> list[float]:
    """Return a valid 1536-dim embedding (all `value` — structurally correct)."""
    return [value] * EMBEDDING_DIM


def _build_silver_doc(
    test_run_id: str,
    label: str = "opec",
    *,
    doc_hash: str | None = None,
) -> dict:
    """
    Build a minimal Silver Full-Text Document conforming to Section C.3.

    canonical_event_id is prefixed with test_{run_id} so cleanup fixtures
    can delete all test rows by prefix without touching production data.
    """
    canonical_event_id = f"test_{test_run_id}_{label}"
    bronze_ref = str(uuid.uuid4())
    hash_val = doc_hash or ("a" * 64)   # valid 64-char hex digest

    return {
        "doc_id":                str(uuid.uuid4()),
        "document_hash":         hash_val,
        "canonical_event_id":    canonical_event_id,
        "full_text_raw":         (
            "OPEC+ ministers agreed to maintain crude oil production cuts "
            "through Q3 2026, keeping the collective reduction at 3.66 mb/d."
        ),
        "inverted_pyramid_lead": (
            "OPEC+ maintains crude oil cuts through Q3 2026 amid demand uncertainty."
        ),
        "source_name":           "newsapi",
        "original_url":          f"https://reuters.com/opec-q3-2026-{label}",
        "author":                "Ahmad Ghaddar",
        "publish_date":          "2026-03-31T10:15:00Z",
        "detected_entities":     [],
        "relevance_score":       0.9,
        "title":                 "OPEC agrees to extend crude oil output cuts",
        "source_display_name":   "Reuters",
        "category":              "business",
        "impact_boost":          True,
        "impact_boost_reason":   "crude oil",
        "sniper_keywords":       ["crude oil", "opec", "production cut"],
        "is_high_signal":        True,
        "fetch_mode":            "pulse",
        "bronze_ref":            bronze_ref,
    }


def _build_gold_record(
    silver_doc: dict,
    test_run_id: str,
    label: str = "opec",
    *,
    impact_level: int = 5,
) -> dict:
    """
    Build a minimal Gold Global Signal conforming to Section C.5.

    Uses the canonical_event_id from the Silver document so the lineage chain
    Silver → Gold is intact for fetch_by_canonical_event tests.
    """
    signal_id = str(uuid.uuid4())
    return {
        "metadata": {
            "signal_id":          signal_id,
            "canonical_event_id": silver_doc["canonical_event_id"],
            "source_platform":    "newsapi",
            "published_at":       silver_doc["publish_date"],
            "silver_data_ref":    silver_doc.get("doc_id", str(uuid.uuid4())),
            "raw_data_ref":       silver_doc.get("bronze_ref", str(uuid.uuid4())),
        },
        "content_vitals": {
            "title":               silver_doc["title"],
            "url":                 silver_doc["original_url"],
            "description_snippet": silver_doc["inverted_pyramid_lead"][:300],
        },
        "enrichment_ai": {
            "executive_summary":    "OPEC+ extends crude oil cuts through Q3 2026.",
            "key_findings":         ["Production cut of 3.66 mb/d extended", "Supports Brent"],
            "impact_level":         impact_level,
            "urgency_level":        3,
            "reliability_score":    0.95,
            "sentiment_score":      0.4,
            "extracted_entities":   ["OPEC+", "Brent crude", "Vienna"],
            "topic_classification": "Energy",
            "fact_check_flag":      False,
        },
        "domain_context": {
            "sniper_keywords":  silver_doc["sniper_keywords"],
            "is_breaking":      True,
            "share_count":      0,
            "geospatial_focus": "Middle East / Global energy markets",
        },
        "embedding": _make_embedding(0.1),
    }


# ==========================================================
# Gate 3 — knowledge_vault Tests (checks [1]–[10])
# ==========================================================

@pytest.mark.usefixtures("cleanup_knowledge_vault")
class TestKnowledgeVaultPersistence:
    """
    Verifies archive(), exists_by_document_hash(), fetch_by_doc_id(),
    fetch_by_canonical_event(), and update_detected_entities().
    """

    def test_archive_returns_doc_id(self, test_run_id):
        """archive() must return a non-empty UUID string (check [1])."""
        silver = _build_silver_doc(test_run_id, "insert_basic")
        doc_id = archive(silver)
        assert doc_id is not None
        assert isinstance(doc_id, str) and len(doc_id) > 0
        # Must be a valid UUID
        parsed = uuid.UUID(doc_id)
        assert str(parsed) == doc_id

    def test_archive_duplicate_returns_none(self, test_run_id):
        """
        archive() must return None when document_hash already exists (check [2]).

        The UNIQUE constraint on document_hash is the last-resort dedup guard
        (Section 4.1C). The exists_by_document_hash() pre-check makes archive()
        return None cleanly rather than raising a psycopg2 IntegrityError.
        """
        shared_hash = "b" * 64
        silver_a = _build_silver_doc(test_run_id, "dedup_a", doc_hash=shared_hash)
        silver_b = _build_silver_doc(test_run_id, "dedup_b", doc_hash=shared_hash)

        doc_id_a = archive(silver_a)
        doc_id_b = archive(silver_b)   # same hash — must be skipped

        assert doc_id_a is not None, "First insert must succeed"
        assert doc_id_b is None, (
            "Second insert with same document_hash must return None "
            "(Section 4.1C dedup guard)"
        )

    def test_exists_by_document_hash_true_after_insert(self, test_run_id):
        """exists_by_document_hash() must return True for a stored hash (check [3])."""
        silver = _build_silver_doc(test_run_id, "exists_true")
        archive(silver)
        assert exists_by_document_hash(silver["document_hash"]) is True

    def test_exists_by_document_hash_false_for_unknown(self):
        """exists_by_document_hash() must return False for an unseen hash (check [4])."""
        unknown_hash = "f" * 64
        assert exists_by_document_hash(unknown_hash) is False

    def test_fetch_by_doc_id_returns_row(self, test_run_id):
        """fetch_by_doc_id() must return the inserted row (check [5])."""
        silver = _build_silver_doc(test_run_id, "fetch_basic")
        doc_id = archive(silver)
        assert doc_id is not None

        row = fetch_by_doc_id(doc_id)
        assert row is not None, f"Row with doc_id={doc_id} must be retrievable"

    def test_fetch_by_doc_id_correct_fields(self, test_run_id):
        """
        Fetched row must reflect document_hash, source_name, and original_url
        stored at archive time (check [6]).
        """
        silver = _build_silver_doc(test_run_id, "fetch_fields")
        doc_id = archive(silver)
        row = fetch_by_doc_id(doc_id)

        assert row["document_hash"] == silver["document_hash"]
        assert row["source_name"]   == silver["source_name"]
        assert row["original_url"]  == silver["original_url"]

    def test_fetch_by_doc_id_fulltext_and_score(self, test_run_id):
        """
        Fetched row must preserve full_text_raw and relevance_score (check [7]).

        full_text_raw is the column that feeds the trigram index used for
        RAG drill-down (idx_kv_fulltext_trgm, Section 5.1).
        """
        silver = _build_silver_doc(test_run_id, "fetch_body")
        doc_id = archive(silver)
        row = fetch_by_doc_id(doc_id)

        assert row["full_text_raw"]    == silver["full_text_raw"]
        assert float(row["relevance_score"]) == pytest.approx(silver["relevance_score"])

    def test_fetch_by_canonical_event_returns_row(self, test_run_id):
        """
        fetch_by_canonical_event() must return the row for the matching id (check [8]).
        """
        silver = _build_silver_doc(test_run_id, "canonical_fetch")
        archive(silver)

        rows = fetch_by_canonical_event(silver["canonical_event_id"])
        assert len(rows) >= 1
        matching = [r for r in rows if r["document_hash"] == silver["document_hash"]]
        assert len(matching) == 1, (
            f"Expected 1 matching row for canonical_event_id="
            f"{silver['canonical_event_id']}, got {len(matching)}"
        )

    def test_update_detected_entities(self, test_run_id):
        """
        update_detected_entities() must write entities to the row and
        fetch_by_doc_id must reflect the update (check [9]).

        This simulates the Gold Job backfilling extracted_entities after
        the OpenAI Cognitive Metadata call (Section 4.2A).
        """
        silver = _build_silver_doc(test_run_id, "update_entities")
        doc_id = archive(silver)
        assert doc_id is not None

        entities = ["OPEC+", "Saudi Arabia", "Vienna", "Brent crude"]
        update_detected_entities(doc_id, entities)

        row = fetch_by_doc_id(doc_id)
        # detected_entities is returned as a list (psycopg2 JSONB deserialises it)
        stored = row["detected_entities"]
        if isinstance(stored, str):
            stored = json.loads(stored)
        assert stored == entities, (
            f"Expected entities {entities}, got {stored}"
        )

    def test_archive_invalid_source_raises_value_error(self, test_run_id):
        """
        archive() must raise ValueError for an unknown source_name (check [10]).

        knowledge_vault only stores 'newsapi' and 'arxiv' documents. Passing
        'polymarket' would indicate a misrouted record (Section 3.3 Service Isolation).
        """
        silver = _build_silver_doc(test_run_id, "bad_source")
        silver["source_name"] = "polymarket"

        with pytest.raises(ValueError, match="source_name"):
            archive(silver)

    def test_archive_bad_hash_raises_value_error(self, test_run_id):
        """archive() must raise ValueError for a document_hash shorter than 64 chars."""
        silver = _build_silver_doc(test_run_id, "bad_hash")
        silver["document_hash"] = "tooshort"

        with pytest.raises(ValueError, match="document_hash"):
            archive(silver)

    def test_fetch_nonexistent_doc_returns_none(self):
        """fetch_by_doc_id() must return None for an unknown UUID."""
        result = fetch_by_doc_id(str(uuid.uuid4()))
        assert result is None


# ==========================================================
# Gate 3 — knowledge_vectors Tests (checks [11]–[20])
# ==========================================================

@pytest.mark.usefixtures("cleanup_knowledge_vectors")
class TestKnowledgeVectorsPersistence:
    """
    Verifies insert(), exists_by_signal_id(), fetch_by_signal_id(),
    fetch_by_canonical_event(), and similarity_search().
    """

    @pytest.fixture(scope="class")
    def gold_and_silver(self, test_run_id):
        """
        Build a Silver document and its corresponding Gold record.

        The canonical_event_id links the two so lineage tests can verify
        the Silver → Gold Paper Trail (Section 3.2).
        """
        silver = _build_silver_doc(test_run_id, "vec_base")
        gold   = _build_gold_record(silver, test_run_id, "vec_base")
        return silver, gold

    def test_insert_returns_signal_id(self, gold_and_silver):
        """insert() must return a non-empty UUID string (check [11])."""
        _, gold = gold_and_silver
        signal_id = insert(gold)
        assert isinstance(signal_id, str) and len(signal_id) > 0
        parsed = uuid.UUID(signal_id)
        assert str(parsed) == signal_id

    def test_insert_idempotent_on_conflict(self, gold_and_silver):
        """
        Inserting the same Gold record twice must not raise and must return
        the same signal_id both times (check [12]).

        ON CONFLICT (signal_id) DO NOTHING — Flink exactly-once re-deliveries
        must not produce duplicate knowledge_vectors rows.
        """
        _, gold = gold_and_silver
        id_1 = insert(gold)
        id_2 = insert(gold)   # same signal_id → ON CONFLICT DO NOTHING
        assert id_1 == id_2

    def test_exists_by_signal_id_true_after_insert(self, gold_and_silver):
        """exists_by_signal_id() must return True after insert (check [13])."""
        _, gold = gold_and_silver
        signal_id = insert(gold)
        assert exists_by_signal_id(signal_id) is True

    def test_exists_by_signal_id_false_for_unknown(self):
        """exists_by_signal_id() must return False for an unseen UUID (check [14])."""
        assert exists_by_signal_id(str(uuid.uuid4())) is False

    def test_fetch_by_signal_id_source_and_entry_type(self, gold_and_silver, test_run_id):
        """
        Fetched row must reflect source_platform='newsapi' and
        entry_type='news_article' (check [15]).

        entry_type is derived from source_platform by knowledge_vectors.insert()
        using _ENTRY_TYPE_MAP — the caller never passes it explicitly.
        """
        _, gold = gold_and_silver
        signal_id = insert(gold)
        row = fetch_by_signal_id(signal_id)

        assert row is not None
        assert row["source_platform"] == "newsapi"
        assert row["entry_type"]       == "news_article"

    def test_fetch_by_signal_id_enrichment_ai(self, gold_and_silver):
        """
        Fetched row must reflect enrichment_ai impact_level and reliability_score
        (check [16]).
        """
        _, gold = gold_and_silver
        signal_id = insert(gold)
        row = fetch_by_signal_id(signal_id)

        ea = row["enrichment_ai"]
        if isinstance(ea, str):
            ea = json.loads(ea)
        assert int(ea["impact_level"])        == gold["enrichment_ai"]["impact_level"]
        assert float(ea["reliability_score"]) == pytest.approx(
            gold["enrichment_ai"]["reliability_score"]
        )

    def test_fetch_by_signal_id_domain_context(self, gold_and_silver):
        """
        Fetched row must reflect domain_context.sniper_keywords (check [17]).

        domain_context is a JSONB column — retrieved as a dict/list by psycopg2.
        """
        _, gold = gold_and_signal = gold_and_silver
        signal_id = insert(gold)
        row = fetch_by_signal_id(signal_id)

        dc = row["domain_context"]
        if isinstance(dc, str):
            dc = json.loads(dc)
        assert dc["sniper_keywords"] == gold["domain_context"]["sniper_keywords"]
        assert dc["is_breaking"]     == gold["domain_context"]["is_breaking"]

    def test_fetch_by_canonical_event_returns_row(self, gold_and_silver):
        """
        kv_fetch_by_canonical_event() must return the row for the matching id
        (check [18]).
        """
        _, gold = gold_and_silver
        insert(gold)

        ceid = gold["metadata"]["canonical_event_id"]
        rows = kv_fetch_by_canonical_event(ceid)
        assert len(rows) >= 1

        sids = [r["signal_id"] for r in rows]
        assert gold["metadata"]["signal_id"] in sids

    def test_similarity_search_returns_results(self, gold_and_silver, test_run_id):
        """
        similarity_search() must return rows with a 'similarity' float field
        (check [19]).

        Uses the same embedding vector as the inserted record so the result
        must have similarity ≈ 1.0 (cosine distance of identical vectors ≈ 0).
        """
        _, gold = gold_and_silver
        insert(gold)

        # Query with the same vector — should find the inserted record
        results = similarity_search(
            query_vector=gold["embedding"],
            limit=5,
            source_platform="newsapi",
        )
        assert len(results) >= 1, "similarity_search must return at least one result"
        for row in results:
            assert "similarity" in row, "Each result must have a 'similarity' field"
            assert isinstance(row["similarity"], float)

    def test_similarity_search_identical_vector_high_score(self, gold_and_silver):
        """
        Querying with the exact same embedding must return similarity ≈ 1.0.

        cosine similarity of two identical unit vectors = 1.0. The test
        accepts a tolerance of 0.01 for floating-point rounding in pgvector.
        """
        _, gold = gold_and_silver
        insert(gold)

        results = similarity_search(
            query_vector=gold["embedding"],
            limit=1,
            source_platform="newsapi",
        )
        assert len(results) >= 1
        assert results[0]["similarity"] == pytest.approx(1.0, abs=0.01), (
            f"Identical vector query must yield similarity ≈ 1.0, "
            f"got {results[0]['similarity']}"
        )

    def test_insert_wrong_dimension_raises_value_error(self, test_run_id):
        """
        insert() must raise ValueError when embedding has the wrong dimension
        (check [20]).

        A wrong-dimension embedding would fail the vector(1536) cast in PostgreSQL
        with a cryptic error. The Python-level check surfaces it early.
        """
        silver = _build_silver_doc(test_run_id, "bad_dim")
        gold   = _build_gold_record(silver, test_run_id, "bad_dim")
        gold["embedding"] = [0.1] * 512   # wrong dimension

        with pytest.raises(ValueError, match="1536"):
            insert(gold)

    def test_insert_invalid_platform_raises_value_error(self, test_run_id):
        """insert() must raise ValueError for an unknown source_platform."""
        silver = _build_silver_doc(test_run_id, "bad_platform")
        gold   = _build_gold_record(silver, test_run_id, "bad_platform")
        gold["metadata"]["source_platform"] = "polymarket"  # not in VALID_PLATFORMS

        with pytest.raises(ValueError, match="source_platform"):
            insert(gold)

    def test_fetch_nonexistent_signal_returns_none(self):
        """fetch_by_signal_id() must return None for an unknown UUID."""
        result = fetch_by_signal_id(str(uuid.uuid4()))
        assert result is None
