"""
Gate 3 — Persistence Gate: Telegram knowledge_vault + knowledge_vectors (Section 5.1 / 5.2).

Verifies that the full Telegram pipeline (Silver → knowledge_vault, Gold → knowledge_vectors)
writes correctly to a live PostgreSQL database and that all fetch helpers return
accurate, queryable data.

Requires: PostgreSQL container running with tables initialised.
    docker compose -f infrastructure/docker-compose.yml up -d postgres

What Gate 3 covers (Section 9.3 Gate 3):

knowledge_vault (checks [1]–[13]):
    [1]  archive() inserts a Telegram Silver doc and returns a doc_id string
    [2]  archive() with duplicate document_hash returns None (dedup guard, Section 4.1C)
    [3]  exists_by_document_hash() returns True after insert
    [4]  exists_by_document_hash() returns False for an unknown hash
    [5]  fetch_by_doc_id() retrieves the inserted row
    [6]  fetch_by_doc_id() returns correct document_hash, source_name="telegram", original_url
    [7]  fetch_by_doc_id() returns correct full_text_raw (= message_text) and relevance_score
    [8]  fetch_by_canonical_event() returns the inserted row for matching id
    [9]  update_detected_entities() writes entities and fetch_by_doc_id reflects them
    [10] archive() raises ValueError for unknown source_name
    [11] archive() accepts source_name="telegram" without error (validates VALID_SOURCES)
    [12] archive() raises ValueError for bad document_hash
    [13] fetch_by_doc_id() returns None for an unknown UUID

knowledge_vectors (checks [14]–[31]):
    [14] insert() persists a Telegram Gold record and returns a signal_id
    [15] insert() is idempotent — ON CONFLICT (signal_id) DO NOTHING
    [16] exists_by_signal_id() returns True after insert
    [17] exists_by_signal_id() returns False for an unknown signal_id
    [18] fetch_by_signal_id() returns source_platform="telegram"
    [19] fetch_by_signal_id() returns entry_type="direct_message"
    [20] fetch_by_signal_id() returns correct enrichment_ai (impact_level, reliability_score)
    [21] fetch_by_signal_id() returns domain_context with Direct Message fields
    [22] domain_context.channel_username persisted correctly
    [23] domain_context.is_forwarded=False persisted correctly
    [24] domain_context.extracted_links list persisted correctly (JSONB round-trip)
    [25] domain_context has NO Academic fields (authors, is_peer_reviewed, citation_count, domain_tags)
    [26] domain_context has NO NewsAPI Tactical fields (is_breaking, sniper_keywords, share_count)
    [27] fetch_by_canonical_event() returns the inserted row
    [28] similarity_search() returns results with a 'similarity' float field
    [29] similarity_search(source_platform="telegram") only returns telegram rows
    [30] insert() raises ValueError for wrong embedding dimension
    [31] insert() raises ValueError for invalid source_platform

Key Telegram departures from ArXiv Gate 3:
    - source_name / source_platform = "telegram" (not "arxiv")
    - entry_type = "direct_message" (not "arxiv_paper") — derived by _ENTRY_TYPE_MAP
    - domain_context uses Direct Message extension (channel_username, is_forwarded,
      forwarded_from, views, extracted_links) — not Academic or Tactical
    - author field in Silver = channel_title (channel display name, not comma-joined authors)
    - full_text_raw / inverted_pyramid_lead both = message_text (no separate article body)
    - Check [11] is the direct confirmation of the VALID_SOURCES fix from Task 4B.4

Architecture note — test isolation:
    Each test record uses canonical_event_id = "test_{run_id}_{label}" so
    cleanup fixtures (cleanup_knowledge_vault, cleanup_knowledge_vectors)
    can delete all test rows by prefix after each test. No production data touched.

References:
    - Section 5.1:  Knowledge Vault specification
    - Section 5.2:  Vector Intelligence — pgvector with HNSW
    - Section 4.1C: SHA-256 deduplication (document_hash UNIQUE guard)
    - Section A.1:  Telegram parameters — 7 vetted channels, no Impact Boost
    - Section C.3:  Silver Full-Text Document Store schema
    - Section C.5:  Gold Global Signal Schema — Telegram Direct Message domain_context
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


def _build_telegram_silver_doc(
    test_run_id: str,
    label: str = "tg_message",
    *,
    doc_hash: str | None = None,
) -> dict:
    """
    Build a minimal Telegram Silver Full-Text Document conforming to Section C.3.

    canonical_event_id is prefixed with test_{run_id} so cleanup fixtures
    can delete all test rows by prefix without touching production data.

    Key Telegram differences from the ArXiv/NewsAPI Silver doc builder:
        - source_name = "telegram"
        - full_text_raw = message_text (Telegram message body is the sole content)
        - inverted_pyramid_lead = message_text (same — no separate lead or abstract)
        - author = channel_title (channel display name, not a byline author)
        - impact_boost = False ALWAYS (Section A.1)
        - Telegram-specific Silver fields: channel_username, is_forwarded,
          forwarded_from, views, extracted_links, has_media
    """
    canonical_event_id = f"test_{test_run_id}_{label}"
    bronze_ref = str(uuid.uuid4())
    hash_val   = doc_hash or ("a" * 64)

    message_text = (
        "BREAKING: Iranian IRGC naval vessels have entered the Strait of Hormuz in "
        "an unusual multi-vessel formation. Three frigates and four fast attack craft "
        "observed moving toward the central shipping lane. Oil tanker traffic is being "
        "rerouted by maritime authorities. Situation is developing."
    )

    return {
        # --- Standard C.3 fields ---
        "doc_id":                str(uuid.uuid4()),
        "document_hash":         hash_val,
        "canonical_event_id":    canonical_event_id,
        "full_text_raw":         message_text,
        "inverted_pyramid_lead": message_text,   # message_text IS the lede
        "source_name":           "telegram",
        "original_url":          f"https://t.me/clashreport/{label[:8]}",
        "author":                "Clash Report",  # channel_title as author
        "publish_date":          "2024-09-23T14:08:31+00:00",
        "detected_entities":     [],
        "relevance_score":       0.82,
        # --- Telegram-specific Silver fields ---
        "title":                 message_text[:120],
        "channel_username":      "clashreport",
        "extracted_links":       ["https://t.me/clashreport/98764"],
        "is_forwarded":          False,
        "forwarded_from":        None,
        "views":                 24312,
        "has_media":             False,
        "impact_boost":          False,   # always False (Section A.1)
        "impact_boost_reason":   "",
        "sniper_keywords":       ["naval", "oil", "strait", "hormuz"],
        "is_high_signal":        True,
        "bronze_ref":            bronze_ref,
    }


def _build_telegram_gold_record(
    silver_doc: dict,
    test_run_id: str,
    label: str = "tg_gold",
    *,
    impact_level: int = 4,
) -> dict:
    """
    Build a minimal Telegram Gold Global Signal conforming to Section C.5,
    using the Direct Message domain_context extension.

    Key Telegram differences from the NewsAPI/ArXiv Gold builder:
        - source_platform = "telegram"
        - domain_context uses Direct Message extension (channel_username, is_forwarded,
          forwarded_from, views, extracted_links) — 5 keys exactly
        - No is_breaking, no sniper_keywords (NewsAPI Tactical)
        - No authors, is_peer_reviewed, citation_count, domain_tags (ArXiv Academic)
    """
    signal_id = str(uuid.uuid4())
    return {
        "metadata": {
            "signal_id":          signal_id,
            "canonical_event_id": silver_doc["canonical_event_id"],
            "source_platform":    "telegram",
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
            "executive_summary": (
                "IRGC multi-vessel formation entered the Strait of Hormuz central "
                "shipping lane, prompting tanker rerouting. Immediate energy supply "
                "disruption risk with high escalation uncertainty."
            ),
            "key_findings": [
                "IRGC naval force (3 frigates + 4 fast attack craft) entered Hormuz",
                "Maritime authorities rerouting oil tanker traffic — active disruption",
            ],
            "impact_level":         impact_level,
            "urgency_level":        5,
            "reliability_score":    0.78,
            "sentiment_score":      -0.7,
            "extracted_entities":   ["IRGC", "Iran", "Strait of Hormuz", "oil tankers"],
            "topic_classification": "Geopolitics",
            "fact_check_flag":      True,
        },
        # Direct Message domain_context (Section C.5 Telegram row)
        "domain_context": {
            "channel_username": silver_doc.get("channel_username", ""),
            "is_forwarded":     bool(silver_doc.get("is_forwarded", False)),
            "forwarded_from":   silver_doc.get("forwarded_from"),
            "views":            silver_doc.get("views"),
            "extracted_links":  silver_doc.get("extracted_links", []),
        },
        "embedding": _make_embedding(0.1),
    }


# ==========================================================
# Gate 3 — knowledge_vault Tests (checks [1]–[13])
# ==========================================================

@pytest.mark.usefixtures("cleanup_knowledge_vault")
class TestTelegramKnowledgeVaultPersistence:
    """
    Verifies archive(), exists_by_document_hash(), fetch_by_doc_id(),
    fetch_by_canonical_event(), and update_detected_entities() for Telegram messages.
    """

    def test_archive_returns_doc_id(self, test_run_id):
        """archive() must return a non-empty UUID string for a Telegram doc (check [1])."""
        silver = _build_telegram_silver_doc(test_run_id, "insert_basic")
        doc_id = archive(silver)
        assert doc_id is not None
        assert isinstance(doc_id, str) and len(doc_id) > 0
        parsed = uuid.UUID(doc_id)
        assert str(parsed) == doc_id

    def test_archive_telegram_source_name_accepted(self, test_run_id):
        """
        archive() must accept source_name='telegram' without raising (check [11]).

        This is the direct confirmation of the VALID_SOURCES fix from Task 4B.4:
        knowledge_vault.VALID_SOURCES was updated to include 'telegram' so that
        Telegram Silver documents can be persisted (Section A.1, Sprint 5).
        """
        silver = _build_telegram_silver_doc(test_run_id, "src_telegram")
        silver["source_name"] = "telegram"
        doc_id = archive(silver)
        assert doc_id is not None, (
            "archive() must accept source_name='telegram'. "
            "VALID_SOURCES in knowledge_vault.py must include 'telegram' (Task 4B.4)."
        )

    def test_archive_duplicate_returns_none(self, test_run_id):
        """
        archive() must return None when document_hash already exists (check [2]).

        The UNIQUE constraint on document_hash is the last-resort dedup guard.
        The exists_by_document_hash() pre-check surfaces a clean None rather than
        raising a psycopg2 IntegrityError (Section 4.1C).
        """
        shared_hash = "b" * 64
        silver_a = _build_telegram_silver_doc(test_run_id, "dedup_a", doc_hash=shared_hash)
        silver_b = _build_telegram_silver_doc(test_run_id, "dedup_b", doc_hash=shared_hash)

        doc_id_a = archive(silver_a)
        doc_id_b = archive(silver_b)

        assert doc_id_a is not None, "First insert must succeed"
        assert doc_id_b is None, (
            "Second insert with same document_hash must return None (Section 4.1C)"
        )

    def test_exists_by_document_hash_true_after_insert(self, test_run_id):
        """exists_by_document_hash() must return True for a stored hash (check [3])."""
        silver = _build_telegram_silver_doc(test_run_id, "exists_true")
        archive(silver)
        assert exists_by_document_hash(silver["document_hash"]) is True

    def test_exists_by_document_hash_false_for_unknown(self):
        """exists_by_document_hash() must return False for an unseen hash (check [4])."""
        assert exists_by_document_hash("f" * 64) is False

    def test_fetch_by_doc_id_returns_row(self, test_run_id):
        """fetch_by_doc_id() must return the inserted row (check [5])."""
        silver = _build_telegram_silver_doc(test_run_id, "fetch_basic")
        doc_id = archive(silver)
        assert doc_id is not None

        row = fetch_by_doc_id(doc_id)
        assert row is not None, f"Row with doc_id={doc_id} must be retrievable"

    def test_fetch_by_doc_id_source_name_is_telegram(self, test_run_id):
        """
        Fetched row must have source_name='telegram' (check [6]).

        This confirms knowledge_vault stores and returns the source_name field
        correctly for Telegram — the key field RAG agents use to distinguish
        channel message vs. news article vs. paper records.
        """
        silver = _build_telegram_silver_doc(test_run_id, "fetch_source")
        doc_id = archive(silver)
        row    = fetch_by_doc_id(doc_id)

        assert row["source_name"] == "telegram", (
            f"source_name must be 'telegram', got {row['source_name']!r}"
        )

    def test_fetch_by_doc_id_correct_fields(self, test_run_id):
        """Fetched row must reflect document_hash and original_url (check [6])."""
        silver = _build_telegram_silver_doc(test_run_id, "fetch_fields")
        doc_id = archive(silver)
        row    = fetch_by_doc_id(doc_id)

        assert row["document_hash"] == silver["document_hash"]
        assert row["original_url"]  == silver["original_url"]

    def test_fetch_by_doc_id_fulltext_is_message_text(self, test_run_id):
        """
        Fetched row must have full_text_raw equal to the message_text (check [7]).

        For Telegram, full_text_raw IS the message body — there is no separate
        article content field. This confirms the Silver mapper's assignment
        (full_text_raw = message_text) survives the DB round-trip.
        """
        silver = _build_telegram_silver_doc(test_run_id, "fetch_body")
        doc_id = archive(silver)
        row    = fetch_by_doc_id(doc_id)

        assert row["full_text_raw"] == silver["full_text_raw"], (
            "full_text_raw must equal the message_text after DB round-trip"
        )
        assert float(row["relevance_score"]) == pytest.approx(silver["relevance_score"])

    def test_fetch_by_canonical_event_returns_row(self, test_run_id):
        """
        fetch_by_canonical_event() must return the row for the matching id (check [8]).
        """
        silver = _build_telegram_silver_doc(test_run_id, "canonical_fetch")
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

        Simulates the Gold Job backfilling extracted_entities for a Telegram message
        after the OpenAI Cognitive Metadata call (Section 4.2A).
        """
        silver = _build_telegram_silver_doc(test_run_id, "update_entities")
        doc_id = archive(silver)
        assert doc_id is not None

        entities = ["IRGC", "Iran", "Strait of Hormuz", "oil tankers", "maritime authorities"]
        update_detected_entities(doc_id, entities)

        row = fetch_by_doc_id(doc_id)
        stored = row["detected_entities"]
        if isinstance(stored, str):
            stored = json.loads(stored)
        assert stored == entities, f"Expected entities {entities}, got {stored}"

    def test_archive_invalid_source_raises_value_error(self, test_run_id):
        """
        archive() must raise ValueError for an unknown source_name (check [10]).

        knowledge_vault only stores 'newsapi', 'arxiv', 'telegram'. Passing
        'opensky' would indicate a misrouted record (Section 3.3 Service Isolation).
        """
        silver = _build_telegram_silver_doc(test_run_id, "bad_source")
        silver["source_name"] = "opensky"

        with pytest.raises(ValueError, match="source_name"):
            archive(silver)

    def test_archive_bad_hash_raises_value_error(self, test_run_id):
        """archive() must raise ValueError for a document_hash shorter than 64 chars (check [12])."""
        silver = _build_telegram_silver_doc(test_run_id, "bad_hash")
        silver["document_hash"] = "tooshort"

        with pytest.raises(ValueError, match="document_hash"):
            archive(silver)

    def test_fetch_nonexistent_doc_returns_none(self):
        """fetch_by_doc_id() must return None for an unknown UUID (check [13])."""
        result = fetch_by_doc_id(str(uuid.uuid4()))
        assert result is None


# ==========================================================
# Gate 3 — knowledge_vectors Tests (checks [14]–[31])
# ==========================================================

@pytest.mark.usefixtures("cleanup_knowledge_vectors")
class TestTelegramKnowledgeVectorsPersistence:
    """
    Verifies insert(), exists_by_signal_id(), fetch_by_signal_id(),
    fetch_by_canonical_event(), and similarity_search() for Telegram Gold records.

    The critical Telegram-specific check is entry_type='direct_message' — derived
    automatically from source_platform='telegram' by knowledge_vectors._ENTRY_TYPE_MAP.
    This distinguishes Telegram records from 'news_article' (NewsAPI) and
    'arxiv_paper' (ArXiv) in the HNSW index for RAG recall quality (Section 5.2).
    """

    @pytest.fixture(scope="class")
    def gold_and_silver(self, test_run_id):
        """
        Build a Telegram Silver document and its corresponding Gold record.

        The canonical_event_id links the two so lineage tests can verify
        the Silver → Gold Paper Trail (Section 3.2).
        """
        silver = _build_telegram_silver_doc(test_run_id, "vec_base")
        gold   = _build_telegram_gold_record(silver, test_run_id, "vec_base")
        return silver, gold

    def test_insert_returns_signal_id(self, gold_and_silver):
        """
        insert() must return a non-empty UUID string for a Telegram Gold record (check [14]).
        """
        _, gold = gold_and_silver
        signal_id = insert(gold)
        assert isinstance(signal_id, str) and len(signal_id) > 0
        parsed = uuid.UUID(signal_id)
        assert str(parsed) == signal_id

    def test_insert_idempotent_on_conflict(self, gold_and_silver):
        """
        Inserting the same Gold record twice must return the same signal_id (check [15]).

        ON CONFLICT (signal_id) DO NOTHING — Flink exactly-once re-deliveries
        must not produce duplicate knowledge_vectors rows (Section 5.2).
        """
        _, gold = gold_and_silver
        id_1 = insert(gold)
        id_2 = insert(gold)
        assert id_1 == id_2

    def test_exists_by_signal_id_true_after_insert(self, gold_and_silver):
        """exists_by_signal_id() must return True after insert (check [16])."""
        _, gold = gold_and_silver
        signal_id = insert(gold)
        assert exists_by_signal_id(signal_id) is True

    def test_exists_by_signal_id_false_for_unknown(self):
        """exists_by_signal_id() must return False for an unseen UUID (check [17])."""
        assert exists_by_signal_id(str(uuid.uuid4())) is False

    def test_fetch_by_signal_id_source_platform_is_telegram(self, gold_and_silver):
        """
        Fetched row must have source_platform='telegram' (check [18]).
        """
        _, gold = gold_and_silver
        signal_id = insert(gold)
        row = fetch_by_signal_id(signal_id)

        assert row is not None
        assert row["source_platform"] == "telegram", (
            f"source_platform must be 'telegram', got {row['source_platform']!r}"
        )

    def test_fetch_by_signal_id_entry_type_is_direct_message(self, gold_and_silver):
        """
        Fetched row must have entry_type='direct_message' (check [19]).

        entry_type is derived from source_platform by knowledge_vectors._ENTRY_TYPE_MAP
        inside insert() — the caller (Gold Job) never passes it explicitly.
        'direct_message' separates Telegram records from 'news_article' and
        'arxiv_paper' in the HNSW index for RAG recall quality (Section 5.2).
        """
        _, gold = gold_and_silver
        signal_id = insert(gold)
        row = fetch_by_signal_id(signal_id)

        assert row["entry_type"] == "direct_message", (
            f"entry_type must be 'direct_message' for Telegram Gold records. "
            f"Got: {row['entry_type']!r}. "
            f"Check knowledge_vectors._ENTRY_TYPE_MAP['telegram'] == 'direct_message'."
        )

    def test_fetch_by_signal_id_enrichment_ai(self, gold_and_silver):
        """
        Fetched row must reflect enrichment_ai impact_level and reliability_score
        (check [20]).
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

    def test_fetch_by_signal_id_domain_context_direct_message_fields(
        self, gold_and_silver
    ):
        """
        Fetched domain_context must contain all Direct Message extension fields (check [21]).

        Tests that JSONB round-trip correctly preserves: channel_username (str),
        is_forwarded (bool), forwarded_from (None), views (int), extracted_links (list).
        """
        _, gold = gold_and_silver
        signal_id = insert(gold)
        row = fetch_by_signal_id(signal_id)

        dc = row["domain_context"]
        if isinstance(dc, str):
            dc = json.loads(dc)

        assert "channel_username" in dc, "domain_context must have 'channel_username'"
        assert "is_forwarded"     in dc, "domain_context must have 'is_forwarded'"
        assert "forwarded_from"   in dc, "domain_context must have 'forwarded_from'"
        assert "views"            in dc, "domain_context must have 'views'"
        assert "extracted_links"  in dc, "domain_context must have 'extracted_links'"

    def test_domain_context_channel_username_persisted(self, gold_and_silver):
        """
        domain_context.channel_username must match the input after DB round-trip (check [22]).

        channel_username is the RAG agent's primary filter for source-channel-specific
        queries (e.g., retrieve only @clashreport signals).
        """
        silver, gold = gold_and_silver
        signal_id = insert(gold)
        row = fetch_by_signal_id(signal_id)

        dc = row["domain_context"]
        if isinstance(dc, str):
            dc = json.loads(dc)
        assert dc["channel_username"] == silver["channel_username"], (
            f"channel_username must survive JSONB round-trip. "
            f"Expected {silver['channel_username']!r}, got {dc['channel_username']!r}"
        )

    def test_domain_context_is_forwarded_false_persisted(self, gold_and_silver):
        """
        domain_context.is_forwarded=False must survive JSONB serialisation (check [23]).

        JSONB booleans must come back as Python booleans, not integers.
        """
        _, gold = gold_and_silver
        signal_id = insert(gold)
        row = fetch_by_signal_id(signal_id)

        dc = row["domain_context"]
        if isinstance(dc, str):
            dc = json.loads(dc)
        assert dc["is_forwarded"] is False, (
            f"is_forwarded must be False after JSONB round-trip, got {dc['is_forwarded']!r}"
        )

    def test_domain_context_extracted_links_list_persisted(self, gold_and_silver):
        """
        domain_context.extracted_links must be stored and retrieved as a list (check [24]).

        JSONB round-trip must preserve list type — not coerce to a string or tuple.
        """
        silver, gold = gold_and_silver
        signal_id = insert(gold)
        row = fetch_by_signal_id(signal_id)

        dc = row["domain_context"]
        if isinstance(dc, str):
            dc = json.loads(dc)
        assert isinstance(dc["extracted_links"], list), (
            f"extracted_links must be a list after JSONB round-trip, "
            f"got {type(dc['extracted_links'])}"
        )
        assert dc["extracted_links"] == silver["extracted_links"]

    def test_domain_context_has_no_academic_fields(self, gold_and_silver):
        """
        Persisted domain_context must NOT contain ArXiv Academic fields (check [25]).

        authors, is_peer_reviewed, citation_count, domain_tags are ArXiv Academic
        fields. Their presence would indicate build_telegram_gold_global_signal()
        accidentally mixed in ArXiv schema (Section C.5).
        """
        _, gold = gold_and_silver
        signal_id = insert(gold)
        row = fetch_by_signal_id(signal_id)

        dc = row["domain_context"]
        if isinstance(dc, str):
            dc = json.loads(dc)

        academic_fields = {"authors", "is_peer_reviewed", "citation_count", "domain_tags"}
        for field in academic_fields:
            assert field not in dc, (
                f"domain_context must not contain ArXiv Academic field '{field}' "
                f"(Section C.5 — Telegram uses Direct Message extension only)"
            )

    def test_domain_context_has_no_tactical_fields(self, gold_and_silver):
        """
        Persisted domain_context must NOT contain NewsAPI Tactical fields (check [26]).

        is_breaking, sniper_keywords, share_count are NewsAPI Tactical fields.
        Their presence would indicate build_telegram_gold_global_signal()
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
                f"(Section C.5 — Telegram uses Direct Message extension only)"
            )

    def test_fetch_by_canonical_event_returns_row(self, gold_and_silver):
        """
        kv_fetch_by_canonical_event() must return the row for the matching id (check [27]).
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
        kv_fetch_by_canonical_event(source_platform='telegram') must return the row.

        The platform filter is used by the RAG agent when grouping all Telegram
        messages about the same canonical event (Section 5.2 query pattern).
        """
        _, gold = gold_and_silver
        insert(gold)

        ceid = gold["metadata"]["canonical_event_id"]
        rows = kv_fetch_by_canonical_event(ceid, source_platform="telegram")
        assert len(rows) >= 1

    def test_similarity_search_returns_results(self, gold_and_silver):
        """
        similarity_search() must return rows with a 'similarity' float field (check [28]).

        Uses the same embedding as the inserted record so the result
        must have similarity ≈ 1.0 (cosine distance of identical vectors ≈ 0).
        """
        _, gold = gold_and_silver
        insert(gold)

        results = similarity_search(
            query_vector=gold["embedding"],
            limit=5,
            source_platform="telegram",
        )
        assert len(results) >= 1, "similarity_search must return at least one result"
        for row in results:
            assert "similarity" in row, "Each result must have a 'similarity' field"
            assert isinstance(row["similarity"], float)

    def test_similarity_search_telegram_platform_filter(self, test_run_id):
        """
        similarity_search(source_platform='telegram') must only return telegram rows
        (check [29]).

        The HNSW index is shared across all sources in knowledge_vectors —
        the platform filter ensures the RAG agent's Telegram-specific queries
        do not receive NewsAPI or ArXiv records (Section 5.2).
        """
        silver = _build_telegram_silver_doc(test_run_id, "sim_filter")
        gold   = _build_telegram_gold_record(silver, test_run_id, "sim_filter")
        insert(gold)

        results = similarity_search(
            query_vector=gold["embedding"],
            limit=10,
            source_platform="telegram",
        )
        for row in results:
            assert row["source_platform"] == "telegram", (
                f"similarity_search with source_platform='telegram' must only return "
                f"telegram rows. Got: {row['source_platform']!r}"
            )

    def test_similarity_search_identical_vector_high_score(self, gold_and_silver):
        """Querying with the exact same embedding must return similarity ≈ 1.0."""
        _, gold = gold_and_silver
        insert(gold)

        results = similarity_search(
            query_vector=gold["embedding"],
            limit=1,
            source_platform="telegram",
        )
        assert len(results) >= 1
        assert results[0]["similarity"] == pytest.approx(1.0, abs=0.01), (
            f"Identical vector must yield similarity ≈ 1.0, "
            f"got {results[0]['similarity']}"
        )

    def test_insert_wrong_dimension_raises_value_error(self, test_run_id):
        """
        insert() must raise ValueError for wrong embedding dimension (check [30]).

        A 512-dim embedding would fail the PostgreSQL vector(1536) cast with a
        cryptic error. The Python-level check in knowledge_vectors surfaces it early.
        """
        silver = _build_telegram_silver_doc(test_run_id, "bad_dim")
        gold   = _build_telegram_gold_record(silver, test_run_id, "bad_dim")
        gold["embedding"] = [0.1] * 512

        with pytest.raises(ValueError, match="1536"):
            insert(gold)

    def test_insert_invalid_platform_raises_value_error(self, test_run_id):
        """
        insert() must raise ValueError for an unknown source_platform (check [31]).
        """
        silver = _build_telegram_silver_doc(test_run_id, "bad_plat")
        gold   = _build_telegram_gold_record(silver, test_run_id, "bad_plat")
        gold["metadata"]["source_platform"] = "opensky"  # not in VALID_PLATFORMS

        with pytest.raises(ValueError, match="source_platform"):
            insert(gold)

    def test_fetch_nonexistent_signal_returns_none(self):
        """fetch_by_signal_id() must return None for an unknown UUID."""
        result = fetch_by_signal_id(str(uuid.uuid4()))
        assert result is None
