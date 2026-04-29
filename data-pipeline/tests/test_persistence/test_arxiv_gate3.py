"""
Gate 3 — Persistence Gate: ArXiv knowledge_vault + knowledge_vectors (Section 5.1 / 5.2).

Verifies that the full ArXiv pipeline (Silver → knowledge_vault, Gold → knowledge_vectors)
writes correctly to a live PostgreSQL database and that all fetch helpers return
accurate, queryable data.

Requires: PostgreSQL container running with tables initialised.
    docker compose -f infrastructure/docker-compose.yml up -d postgres

What Gate 3 covers (Section 9.3 Gate 3):

knowledge_vault (checks [1]–[11]):
    [1]  archive() inserts an ArXiv Silver doc and returns a doc_id string
    [2]  archive() with duplicate document_hash returns None (dedup guard, Section 4.1C)
    [3]  exists_by_document_hash() returns True after insert
    [4]  exists_by_document_hash() returns False for an unknown hash
    [5]  fetch_by_doc_id() retrieves the inserted row
    [6]  fetch_by_doc_id() returns correct document_hash, source_name="arxiv", original_url
    [7]  fetch_by_doc_id() returns correct full_text_raw (= abstract) and relevance_score
    [8]  fetch_by_canonical_event() returns the inserted row for matching id
    [9]  update_detected_entities() writes entities and fetch_by_doc_id reflects them
    [10] archive() raises ValueError for unknown source_name
    [11] archive() stores source_name="arxiv" without error (validates VALID_SOURCES)

knowledge_vectors (checks [12]–[22]):
    [12] insert() persists an ArXiv Gold record and returns a signal_id
    [13] insert() is idempotent — ON CONFLICT (signal_id) DO NOTHING
    [14] exists_by_signal_id() returns True after insert
    [15] exists_by_signal_id() returns False for an unknown signal_id
    [16] fetch_by_signal_id() returns source_platform="arxiv" and entry_type="arxiv_paper"
    [17] fetch_by_signal_id() returns correct enrichment_ai (impact_level, reliability_score)
    [18] fetch_by_signal_id() returns domain_context with Academic fields (Section C.5)
    [19] domain_context.is_peer_reviewed == False in persisted row
    [20] domain_context.citation_count == 0 in persisted row
    [21] domain_context.domain_tags matches the categories list
    [22] domain_context has no Tactical fields (is_breaking, sniper_keywords)
    [23] fetch_by_canonical_event() returns the inserted row
    [24] similarity_search() returns results with a 'similarity' float field
    [25] similarity_search(source_platform="arxiv") only returns arxiv rows
    [26] insert() raises ValueError for wrong embedding dimension
    [27] insert() raises ValueError for invalid source_platform

Key ArXiv departures from NewsAPI Gate 3:
    - source_name / source_platform = "arxiv" (not "newsapi")
    - entry_type = "arxiv_paper" (not "news_article") — derived by _ENTRY_TYPE_MAP
    - domain_context uses Academic extension (no is_breaking, no sniper_keywords)
    - author field in Silver = ", ".join(authors) — base C.3 string compat
    - full_text_raw / inverted_pyramid_lead both = abstract

Architecture note — test isolation:
    Each test record uses canonical_event_id = "test_{run_id}_{label}" so
    cleanup fixtures (cleanup_knowledge_vault, cleanup_knowledge_vectors)
    can delete all test rows by prefix after each test. No production data touched.

References:
    - Section 5.1:  Knowledge Vault specification
    - Section 5.2:  Vector Intelligence — pgvector with HNSW
    - Section 4.1C: SHA-256 deduplication (document_hash UNIQUE guard)
    - Section B.1:  ArXiv parameters — is_peer_reviewed=False, citation_count=0
    - Section C.3:  Silver Full-Text Document Store schema
    - Section C.5:  Gold Global Signal Schema — ArXiv Academic domain_context
    - Section 9.3:  Triple-Gate Test Matrix — Gate 3
"""

import json
import uuid
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


def _build_arxiv_silver_doc(
    test_run_id: str,
    label: str = "arxiv_paper",
    *,
    doc_hash: str | None = None,
) -> dict:
    """
    Build a minimal ArXiv Silver Full-Text Document conforming to Section C.3.

    canonical_event_id is prefixed with test_{run_id} so cleanup fixtures
    can delete all test rows by prefix without touching production data.

    Key ArXiv differences from the NewsAPI Silver doc builder:
        - source_name = "arxiv"
        - full_text_raw = abstract (not content / description)
        - inverted_pyramid_lead = abstract (abstract IS the lede, Section C.3)
        - author = ", ".join(authors) — string for base C.3 compat
        - authors = list — for Gold domain_context (Section C.5)
        - impact_boost = False ALWAYS (Section B.1)
        - categories = list — maps to domain_context.domain_tags
    """
    canonical_event_id = f"test_{test_run_id}_{label}"
    bronze_ref = str(uuid.uuid4())
    hash_val   = doc_hash or ("c" * 64)

    abstract = (
        "We examine how evolving AI regulation and policy uncertainty affect "
        "market volatility and the behavior of algorithmic trading systems. "
        "Using a panel dataset spanning 14 jurisdictions over 2019-2024, we find "
        "that periods of high regulatory uncertainty are associated with an 18-23% "
        "increase in intraday volatility across AI-exposed sectors."
    )
    authors    = ["Noa Ben-David", "Kwame Asante", "Ingrid Solberg"]
    categories = ["cs.AI", "econ.GN", "q-fin.ST"]

    return {
        # --- Standard C.3 fields ---
        "doc_id":                str(uuid.uuid4()),
        "document_hash":         hash_val,
        "canonical_event_id":    canonical_event_id,
        "full_text_raw":         abstract,
        "inverted_pyramid_lead": abstract,   # abstract IS the lede (Section C.3)
        "source_name":           "arxiv",
        "original_url":          f"https://arxiv.org/abs/2409.{label[:5]}",
        "author":                ", ".join(authors),  # string for base C.3 compat
        "publish_date":          "2024-09-23T14:08:31Z",
        "detected_entities":     [],
        "relevance_score":       0.78,
        # --- ArXiv-specific Silver fields ---
        "title":                 "Regulatory Uncertainty and Market Volatility: AI Governance",
        "arxiv_id":              f"2409.{label[:5]}v1",
        "authors":               authors,    # list for Gold domain_context
        "primary_category":      "cs.AI",
        "categories":            categories,
        "pdf_url":               f"https://arxiv.org/pdf/2409.{label[:5]}",
        "impact_boost":          False,      # always False (Section B.1)
        "impact_boost_reason":   "",
        "sniper_keywords":       ["intervention", "policy", "regulation"],
        "is_high_signal":        True,
        "fetch_mode":            "pulse",
        "bronze_ref":            bronze_ref,
    }


def _build_arxiv_gold_record(
    silver_doc: dict,
    test_run_id: str,
    label: str = "arxiv_gold",
    *,
    impact_level: int = 3,
) -> dict:
    """
    Build a minimal ArXiv Gold Global Signal conforming to Section C.5,
    using the Academic domain_context extension.

    Key ArXiv differences from the NewsAPI Gold builder:
        - source_platform = "arxiv"
        - domain_context uses Academic extension (authors, is_peer_reviewed,
          citation_count, domain_tags) — NOT Tactical extension
        - No is_breaking, no sniper_keywords, no share_count, no geospatial_focus
          in domain_context
    """
    signal_id = str(uuid.uuid4())
    return {
        "metadata": {
            "signal_id":          signal_id,
            "canonical_event_id": silver_doc["canonical_event_id"],
            "source_platform":    "arxiv",
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
            "executive_summary":    (
                "Cross-jurisdictional study links AI regulatory uncertainty to "
                "18-23% intraday volatility increases in AI-exposed sectors."
            ),
            "key_findings": [
                "High AI regulatory uncertainty correlated with 18-23% volatility rise",
                "Institutional algorithms reduce AI-intensive stock exposure during uncertainty",
            ],
            "impact_level":         impact_level,
            "urgency_level":        1,
            "reliability_score":    0.87,
            "sentiment_score":      -0.2,
            "extracted_entities":   ["AI governance", "algorithmic trading", "equity markets"],
            "topic_classification": "AI Regulation / Financial Markets",
            "fact_check_flag":      False,
        },
        # Academic domain_context (Section C.5 ArXiv row)
        "domain_context": {
            "authors":          silver_doc.get("authors", []),
            "is_peer_reviewed": False,   # arXiv pre-print (structural fact, Section B.1)
            "citation_count":   0,       # not available in Atom feed
            "domain_tags":      silver_doc.get("categories", []),
        },
        "embedding": _make_embedding(0.1),
    }


# ==========================================================
# Gate 3 — knowledge_vault Tests (checks [1]–[11])
# ==========================================================

@pytest.mark.usefixtures("cleanup_knowledge_vault")
class TestArXivKnowledgeVaultPersistence:
    """
    Verifies archive(), exists_by_document_hash(), fetch_by_doc_id(),
    fetch_by_canonical_event(), and update_detected_entities() for ArXiv papers.
    """

    def test_archive_returns_doc_id(self, test_run_id):
        """archive() must return a non-empty UUID string for an ArXiv doc (check [1])."""
        silver = _build_arxiv_silver_doc(test_run_id, "insert_basic")
        doc_id = archive(silver)
        assert doc_id is not None
        assert isinstance(doc_id, str) and len(doc_id) > 0
        parsed = uuid.UUID(doc_id)
        assert str(parsed) == doc_id

    def test_archive_arxiv_source_name_accepted(self, test_run_id):
        """
        archive() must accept source_name='arxiv' without raising (check [11]).

        knowledge_vault.VALID_SOURCES = frozenset({'newsapi', 'arxiv'}).
        This test directly confirms the VALID_SOURCES guard passes for ArXiv —
        the Gate 4 integration would fail silently if it weren't in the set.
        """
        silver = _build_arxiv_silver_doc(test_run_id, "src_arxiv")
        silver["source_name"] = "arxiv"
        doc_id = archive(silver)
        assert doc_id is not None, (
            "archive() must accept source_name='arxiv' (VALID_SOURCES check)"
        )

    def test_archive_duplicate_returns_none(self, test_run_id):
        """
        archive() must return None when document_hash already exists (check [2]).

        The UNIQUE constraint on document_hash is the last-resort dedup guard.
        The exists_by_document_hash() pre-check makes archive() return None
        cleanly rather than raising a psycopg2 IntegrityError (Section 4.1C).
        """
        shared_hash = "d" * 64
        silver_a = _build_arxiv_silver_doc(test_run_id, "dedup_a", doc_hash=shared_hash)
        silver_b = _build_arxiv_silver_doc(test_run_id, "dedup_b", doc_hash=shared_hash)

        doc_id_a = archive(silver_a)
        doc_id_b = archive(silver_b)

        assert doc_id_a is not None, "First insert must succeed"
        assert doc_id_b is None, (
            "Second insert with same document_hash must return None (Section 4.1C)"
        )

    def test_exists_by_document_hash_true_after_insert(self, test_run_id):
        """exists_by_document_hash() must return True for a stored hash (check [3])."""
        silver = _build_arxiv_silver_doc(test_run_id, "exists_true")
        archive(silver)
        assert exists_by_document_hash(silver["document_hash"]) is True

    def test_exists_by_document_hash_false_for_unknown(self):
        """exists_by_document_hash() must return False for an unseen hash (check [4])."""
        assert exists_by_document_hash("e" * 64) is False

    def test_fetch_by_doc_id_returns_row(self, test_run_id):
        """fetch_by_doc_id() must return the inserted row (check [5])."""
        silver = _build_arxiv_silver_doc(test_run_id, "fetch_basic")
        doc_id = archive(silver)
        assert doc_id is not None

        row = fetch_by_doc_id(doc_id)
        assert row is not None, f"Row with doc_id={doc_id} must be retrievable"

    def test_fetch_by_doc_id_source_name_is_arxiv(self, test_run_id):
        """
        Fetched row must have source_name='arxiv' (check [6]).

        This confirms knowledge_vault stores and returns the source_name field
        correctly for ArXiv — the key field RAG agents use to distinguish paper
        vs. news article records.
        """
        silver = _build_arxiv_silver_doc(test_run_id, "fetch_source")
        doc_id = archive(silver)
        row    = fetch_by_doc_id(doc_id)

        assert row["source_name"] == "arxiv", (
            f"source_name must be 'arxiv', got {row['source_name']!r}"
        )

    def test_fetch_by_doc_id_correct_fields(self, test_run_id):
        """Fetched row must reflect document_hash and original_url (check [6])."""
        silver = _build_arxiv_silver_doc(test_run_id, "fetch_fields")
        doc_id = archive(silver)
        row    = fetch_by_doc_id(doc_id)

        assert row["document_hash"] == silver["document_hash"]
        assert row["original_url"]  == silver["original_url"]

    def test_fetch_by_doc_id_fulltext_is_abstract(self, test_run_id):
        """
        Fetched row must have full_text_raw equal to the abstract (check [7]).

        For ArXiv, full_text_raw IS the abstract — there is no separate article
        body. This confirms the Silver mapper's single-source text assignment
        survives the DB round-trip.
        """
        silver = _build_arxiv_silver_doc(test_run_id, "fetch_body")
        doc_id = archive(silver)
        row    = fetch_by_doc_id(doc_id)

        assert row["full_text_raw"] == silver["full_text_raw"], (
            "full_text_raw must equal the paper abstract after DB round-trip"
        )
        assert float(row["relevance_score"]) == pytest.approx(silver["relevance_score"])

    def test_fetch_by_canonical_event_returns_row(self, test_run_id):
        """
        fetch_by_canonical_event() must return the row for the matching id (check [8]).
        """
        silver = _build_arxiv_silver_doc(test_run_id, "canonical_fetch")
        archive(silver)

        rows = fetch_by_canonical_event(silver["canonical_event_id"])
        assert len(rows) >= 1
        matching = [r for r in rows if r["document_hash"] == silver["document_hash"]]
        assert len(matching) == 1, (
            f"Expected 1 row for canonical_event_id={silver['canonical_event_id']}, "
            f"got {len(matching)}"
        )

    def test_update_detected_entities(self, test_run_id):
        """
        update_detected_entities() must write entities and fetch_by_doc_id reflects
        them (check [9]).

        Simulates the Gold Job backfilling extracted_entities for an ArXiv paper
        after the OpenAI Cognitive Metadata call (Section 4.2A).
        """
        silver = _build_arxiv_silver_doc(test_run_id, "update_entities")
        doc_id = archive(silver)
        assert doc_id is not None

        entities = ["AI governance", "equity markets", "algorithmic trading", "OPEC"]
        update_detected_entities(doc_id, entities)

        row = fetch_by_doc_id(doc_id)
        stored = row["detected_entities"]
        if isinstance(stored, str):
            stored = json.loads(stored)
        assert stored == entities, f"Expected entities {entities}, got {stored}"

    def test_archive_invalid_source_raises_value_error(self, test_run_id):
        """
        archive() must raise ValueError for an unknown source_name (check [10]).

        knowledge_vault only stores 'newsapi', 'arxiv', 'telegram'. Passing 'opensky'
        would indicate a misrouted record (Section 3.3 Service Isolation).
        """
        silver = _build_arxiv_silver_doc(test_run_id, "bad_source")
        silver["source_name"] = "opensky"

        with pytest.raises(ValueError, match="source_name"):
            archive(silver)

    def test_archive_bad_hash_raises_value_error(self, test_run_id):
        """archive() must raise ValueError for a document_hash shorter than 64 chars."""
        silver = _build_arxiv_silver_doc(test_run_id, "bad_hash")
        silver["document_hash"] = "tooshort"

        with pytest.raises(ValueError, match="document_hash"):
            archive(silver)

    def test_fetch_nonexistent_doc_returns_none(self):
        """fetch_by_doc_id() must return None for an unknown UUID."""
        result = fetch_by_doc_id(str(uuid.uuid4()))
        assert result is None


# ==========================================================
# Gate 3 — knowledge_vectors Tests (checks [12]–[27])
# ==========================================================

@pytest.mark.usefixtures("cleanup_knowledge_vectors")
class TestArXivKnowledgeVectorsPersistence:
    """
    Verifies insert(), exists_by_signal_id(), fetch_by_signal_id(),
    fetch_by_canonical_event(), and similarity_search() for ArXiv Gold records.

    The critical ArXiv-specific check is entry_type='arxiv_paper' — derived
    automatically from source_platform='arxiv' by knowledge_vectors._ENTRY_TYPE_MAP.
    """

    @pytest.fixture(scope="class")
    def gold_and_silver(self, test_run_id):
        """
        Build an ArXiv Silver document and its corresponding Gold record.

        The canonical_event_id links the two so lineage tests can verify
        the Silver → Gold Paper Trail (Section 3.2).
        """
        silver = _build_arxiv_silver_doc(test_run_id, "vec_base")
        gold   = _build_arxiv_gold_record(silver, test_run_id, "vec_base")
        return silver, gold

    def test_insert_returns_signal_id(self, gold_and_silver):
        """insert() must return a non-empty UUID string for an ArXiv Gold record (check [12])."""
        _, gold = gold_and_silver
        signal_id = insert(gold)
        assert isinstance(signal_id, str) and len(signal_id) > 0
        parsed = uuid.UUID(signal_id)
        assert str(parsed) == signal_id

    def test_insert_idempotent_on_conflict(self, gold_and_silver):
        """
        Inserting the same Gold record twice must return the same signal_id (check [13]).

        ON CONFLICT (signal_id) DO NOTHING — Flink exactly-once re-deliveries
        must not produce duplicate knowledge_vectors rows (Section 5.2).
        """
        _, gold = gold_and_silver
        id_1 = insert(gold)
        id_2 = insert(gold)
        assert id_1 == id_2

    def test_exists_by_signal_id_true_after_insert(self, gold_and_silver):
        """exists_by_signal_id() must return True after insert (check [14])."""
        _, gold = gold_and_silver
        signal_id = insert(gold)
        assert exists_by_signal_id(signal_id) is True

    def test_exists_by_signal_id_false_for_unknown(self):
        """exists_by_signal_id() must return False for an unseen UUID (check [15])."""
        assert exists_by_signal_id(str(uuid.uuid4())) is False

    def test_fetch_by_signal_id_source_platform_is_arxiv(self, gold_and_silver):
        """
        Fetched row must have source_platform='arxiv' (check [16]).
        """
        _, gold = gold_and_silver
        signal_id = insert(gold)
        row = fetch_by_signal_id(signal_id)

        assert row is not None
        assert row["source_platform"] == "arxiv", (
            f"source_platform must be 'arxiv', got {row['source_platform']!r}"
        )

    def test_fetch_by_signal_id_entry_type_is_arxiv_paper(self, gold_and_silver):
        """
        Fetched row must have entry_type='arxiv_paper' (check [16]).

        entry_type is derived from source_platform by knowledge_vectors._ENTRY_TYPE_MAP
        inside insert() — the caller (Gold Job) never passes it explicitly.
        'arxiv_paper' is the entry_type that separates ArXiv records from
        'news_article' (NewsAPI) and 'direct_message' (Telegram) in the
        HNSW index for RAG recall quality (Section 5.2).
        """
        _, gold = gold_and_silver
        signal_id = insert(gold)
        row = fetch_by_signal_id(signal_id)

        assert row["entry_type"] == "arxiv_paper", (
            f"entry_type must be 'arxiv_paper' for ArXiv Gold records. "
            f"Got: {row['entry_type']!r}. "
            f"Check knowledge_vectors._ENTRY_TYPE_MAP['arxiv'] == 'arxiv_paper'."
        )

    def test_fetch_by_signal_id_enrichment_ai(self, gold_and_silver):
        """
        Fetched row must reflect enrichment_ai impact_level and reliability_score
        (check [17]).
        """
        _, gold = gold_and_silver
        signal_id = insert(gold)
        row = fetch_by_signal_id(signal_id)

        ea = row["enrichment_ai"]
        if isinstance(ea, str):
            ea = json.loads(ea)
        assert int(ea["impact_level"]) == gold["enrichment_ai"]["impact_level"]
        assert float(ea["reliability_score"]) == pytest.approx(
            gold["enrichment_ai"]["reliability_score"]
        )

    def test_fetch_by_signal_id_domain_context_academic_fields(self, gold_and_silver):
        """
        Fetched domain_context must contain the Academic extension fields (check [18]).

        Tests that JSONB round-trip correctly preserves: authors (list),
        is_peer_reviewed (bool), citation_count (int), domain_tags (list).
        """
        _, gold = gold_and_silver
        signal_id = insert(gold)
        row = fetch_by_signal_id(signal_id)

        dc = row["domain_context"]
        if isinstance(dc, str):
            dc = json.loads(dc)

        assert "authors"          in dc, "domain_context must have 'authors'"
        assert "is_peer_reviewed" in dc, "domain_context must have 'is_peer_reviewed'"
        assert "citation_count"   in dc, "domain_context must have 'citation_count'"
        assert "domain_tags"      in dc, "domain_context must have 'domain_tags'"

    def test_domain_context_is_peer_reviewed_false_persisted(self, gold_and_silver):
        """
        domain_context.is_peer_reviewed must be False after DB round-trip (check [19]).

        arXiv is a pre-print server — this structural fact must survive JSONB
        serialisation and deserialisation unchanged (Section B.1).
        """
        _, gold = gold_and_silver
        signal_id = insert(gold)
        row = fetch_by_signal_id(signal_id)

        dc = row["domain_context"]
        if isinstance(dc, str):
            dc = json.loads(dc)
        assert dc["is_peer_reviewed"] is False, (
            "is_peer_reviewed must be False after JSONB round-trip"
        )

    def test_domain_context_citation_count_zero_persisted(self, gold_and_silver):
        """
        domain_context.citation_count must be 0 after DB round-trip (check [20]).
        """
        _, gold = gold_and_silver
        signal_id = insert(gold)
        row = fetch_by_signal_id(signal_id)

        dc = row["domain_context"]
        if isinstance(dc, str):
            dc = json.loads(dc)
        assert int(dc["citation_count"]) == 0, (
            f"citation_count must be 0, got {dc['citation_count']!r}"
        )

    def test_domain_context_domain_tags_match_categories(self, gold_and_silver):
        """
        domain_context.domain_tags must equal the arXiv categories list (check [21]).
        """
        silver, gold = gold_and_silver
        signal_id = insert(gold)
        row = fetch_by_signal_id(signal_id)

        dc = row["domain_context"]
        if isinstance(dc, str):
            dc = json.loads(dc)
        assert dc["domain_tags"] == silver["categories"], (
            f"domain_tags must match categories. "
            f"Expected {silver['categories']}, got {dc['domain_tags']}"
        )

    def test_domain_context_authors_list_persisted(self, gold_and_silver):
        """
        domain_context.authors must be stored and retrieved as a list (check [18]).
        """
        silver, gold = gold_and_silver
        signal_id = insert(gold)
        row = fetch_by_signal_id(signal_id)

        dc = row["domain_context"]
        if isinstance(dc, str):
            dc = json.loads(dc)
        assert isinstance(dc["authors"], list), (
            f"domain_context.authors must be a list, got {type(dc['authors'])}"
        )
        assert dc["authors"] == silver["authors"]

    def test_domain_context_has_no_tactical_fields(self, gold_and_silver):
        """
        Persisted domain_context must NOT contain NewsAPI Tactical fields (check [22]).

        is_breaking, sniper_keywords, share_count, geospatial_focus are NewsAPI
        Tactical fields. Their presence would indicate build_arxiv_gold_global_signal()
        accidentally copied from build_gold_global_signal() (Section C.5).
        """
        _, gold = gold_and_silver
        signal_id = insert(gold)
        row = fetch_by_signal_id(signal_id)

        dc = row["domain_context"]
        if isinstance(dc, str):
            dc = json.loads(dc)

        newsapi_tactical_fields = {"is_breaking", "sniper_keywords", "share_count"}
        for field in newsapi_tactical_fields:
            assert field not in dc, (
                f"domain_context must not contain NewsAPI Tactical field '{field}' "
                f"(Section C.5 — ArXiv uses Academic extension only)"
            )

    def test_fetch_by_canonical_event_returns_row(self, gold_and_silver):
        """
        kv_fetch_by_canonical_event() must return the row for the matching id (check [23]).
        """
        _, gold = gold_and_silver
        insert(gold)

        ceid = gold["metadata"]["canonical_event_id"]
        rows = kv_fetch_by_canonical_event(ceid)
        assert len(rows) >= 1

        sids = [r["signal_id"] for r in rows]
        assert gold["metadata"]["signal_id"] in sids

    def test_fetch_by_canonical_event_with_platform_filter(self, gold_and_silver):
        """
        kv_fetch_by_canonical_event(source_platform='arxiv') must return the row.

        The platform filter is used by the RAG agent when grouping all ArXiv
        papers about the same canonical event (Section 5.2 query pattern).
        """
        _, gold = gold_and_silver
        insert(gold)

        ceid = gold["metadata"]["canonical_event_id"]
        rows = kv_fetch_by_canonical_event(ceid, source_platform="arxiv")
        assert len(rows) >= 1

    def test_similarity_search_returns_results(self, gold_and_silver):
        """
        similarity_search() must return rows with a 'similarity' float field (check [24]).

        Uses the same embedding as the inserted record so the result
        must have similarity ≈ 1.0 (cosine distance of identical vectors ≈ 0).
        """
        _, gold = gold_and_silver
        insert(gold)

        results = similarity_search(
            query_vector=gold["embedding"],
            limit=5,
            source_platform="arxiv",
        )
        assert len(results) >= 1, "similarity_search must return at least one result"
        for row in results:
            assert "similarity" in row, "Each result must have a 'similarity' field"
            assert isinstance(row["similarity"], float)

    def test_similarity_search_arxiv_platform_filter(self, test_run_id):
        """
        similarity_search(source_platform='arxiv') must only return arxiv rows (check [25]).

        The HNSW index is shared across all sources in knowledge_vectors —
        the platform filter ensures the RAG agent's ArXiv-specific queries
        do not receive NewsAPI or Telegram records (Section 5.2).
        """
        silver = _build_arxiv_silver_doc(test_run_id, "sim_filter")
        gold   = _build_arxiv_gold_record(silver, test_run_id, "sim_filter")
        insert(gold)

        results = similarity_search(
            query_vector=gold["embedding"],
            limit=10,
            source_platform="arxiv",
        )
        for row in results:
            assert row["source_platform"] == "arxiv", (
                f"similarity_search with source_platform='arxiv' must only return "
                f"arxiv rows. Got: {row['source_platform']!r}"
            )

    def test_similarity_search_identical_vector_high_score(self, gold_and_silver):
        """Querying with the exact same embedding must return similarity ≈ 1.0."""
        _, gold = gold_and_silver
        insert(gold)

        results = similarity_search(
            query_vector=gold["embedding"],
            limit=1,
            source_platform="arxiv",
        )
        assert len(results) >= 1
        assert results[0]["similarity"] == pytest.approx(1.0, abs=0.01), (
            f"Identical vector must yield similarity ≈ 1.0, "
            f"got {results[0]['similarity']}"
        )

    def test_insert_wrong_dimension_raises_value_error(self, test_run_id):
        """
        insert() must raise ValueError for wrong embedding dimension (check [26]).

        A 512-dim embedding would fail the PostgreSQL vector(1536) cast with a
        cryptic error. The Python-level check in knowledge_vectors surfaces it early.
        """
        silver = _build_arxiv_silver_doc(test_run_id, "bad_dim")
        gold   = _build_arxiv_gold_record(silver, test_run_id, "bad_dim")
        gold["embedding"] = [0.1] * 512

        with pytest.raises(ValueError, match="1536"):
            insert(gold)

    def test_insert_invalid_platform_raises_value_error(self, test_run_id):
        """insert() must raise ValueError for an unknown source_platform (check [27])."""
        silver = _build_arxiv_silver_doc(test_run_id, "bad_plat")
        gold   = _build_arxiv_gold_record(silver, test_run_id, "bad_plat")
        gold["metadata"]["source_platform"] = "opensky"  # not in VALID_PLATFORMS

        with pytest.raises(ValueError, match="source_platform"):
            insert(gold)

    def test_fetch_nonexistent_signal_returns_none(self):
        """fetch_by_signal_id() must return None for an unknown UUID."""
        result = fetch_by_signal_id(str(uuid.uuid4()))
        assert result is None
