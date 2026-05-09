"""
Knowledge Vault — Full-Text Document Store, Silver Layer (Section 5.1).

Persists Silver Full-Text Document records into the `knowledge_vault` PostgreSQL
table. Stores cleaned article bodies and research papers for RAG agent drill-down
from a Gold Global Signal (via silver_data_ref → doc_id) to the original source.

Sources stored here (Section 5.1 / Part 1.5 Vector Persistence Map):
    NewsAPI articles   → knowledge_vault + knowledge_vectors (C.5)
    ArXiv papers       → knowledge_vault + knowledge_vectors (C.5)
    Telegram messages  → knowledge_vault + knowledge_vectors (C.5, entry_type="direct_message")

Deduplication strategy (Section 4.1C):
    knowledge_vault has a UNIQUE constraint on document_hash (the SHA-256 digest
    of normalised full_text_raw + URL). The archive() function checks
    exists_by_document_hash() before inserting to surface a clean signal rather
    than relying on a silent ON CONFLICT. The UNIQUE constraint is a last-resort
    guard against duplicate Bronze replays.

Public interface:
    archive(silver_doc)                              → doc_id (str) | None
    exists_by_document_hash(document_hash)           → bool
    fetch_by_doc_id(doc_id)                          → dict | None
    fetch_by_canonical_event(canonical_event_id, …)  → list[dict]
References:
    - Section 5.1:  Knowledge Vault specification
    - Section 4.1B: Dual-Store Persistence routing
    - Section 4.1C: SHA-256 deduplication — document_hash UNIQUE guard
    - Section C.3:  Silver Full-Text Document Store schema
    - Section C.5:  Gold Global Signal — silver_data_ref links here
    - Section 3.2:  DRY — all DB logic in dedicated persistence modules
    - Section 3.3:  Service Isolation — only this module writes to knowledge_vault
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from psycopg2.extras import Json, RealDictCursor

from utils.db import get_cursor
from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)
setup_logging()

# Valid source_name values stored in knowledge_vault
# Telegram added in Sprint 5 (Section A.1 — Direct Message path, entry_type="direct_message")
VALID_SOURCES = frozenset({"newsapi", "arxiv", "telegram"})


# ==========================================================
# Archive (insert with dedup guard)
# ==========================================================

def archive(silver_doc: dict) -> Optional[str]:
    """
    Persist one Silver Full-Text Document to `knowledge_vault`.

    Performs a dedup check before inserting: if a row with the same
    document_hash already exists, returns None (duplicate — not an error).
    This surfaces the dedup result cleanly to the caller (the Flink Silver
    ProcessFunction) so it can log and skip without relying on exception handling
    (Section 4.1C: 'prevents duplicate ingestion').

    The full_text_raw, inverted_pyramid_lead, and detected_entities fields
    are stored in dedicated columns rather than inside a JSONB blob to enable
    the trigram index (idx_kv_fulltext_trgm) and GIN entity index
    (idx_kv_entities) required for RAG drill-down queries (Section 5.1).

    Why raw_data_ref from bronze_ref: the Bronze envelope event_id is the
    ingestion_id — it links the Knowledge Vault row back to the exact Kafka
    message offset for audit replay (Section C.5 raw_data_ref field).

    Args:
        silver_doc: Silver Full-Text Document Store dict conforming to Section C.3.
                    Required keys: document_hash, canonical_event_id, source_name,
                    full_text_raw, bronze_ref.

    Returns:
        doc_id: UUID string of the inserted row.
        None:   If document_hash already exists (duplicate — intentional skip).

    Raises:
        ValueError: If source_name is missing or not in VALID_SOURCES, or if
                    document_hash is not a 64-char hex string.
        psycopg2.Error: On unexpected database errors.
    """
    _validate_record(silver_doc)

    doc_hash = silver_doc["document_hash"]

    # --- Dedup guard (Section 4.1C) ---
    if exists_by_document_hash(doc_hash):
        logger.debug(
            "[knowledge_vault] Duplicate document_hash=%s — skipping insert",
            doc_hash[:16],
        )
        return None

    source_name = silver_doc["source_name"]
    bronze_ref  = silver_doc.get("bronze_ref") or silver_doc.get("canonical_event_id")

    # publish_date: store as TIMESTAMPTZ — pass the ISO string and let psycopg2
    # coerce it. If publish_date is empty, store NULL rather than a parse error.
    publish_date_raw = silver_doc.get("publish_date", "")
    publish_date: Optional[str] = publish_date_raw if publish_date_raw else None

    sql = """
        INSERT INTO knowledge_vault (
            document_hash,
            canonical_event_id,
            raw_data_ref,
            source_name,
            author,
            original_url,
            publish_date,
            full_text_raw,
            inverted_pyramid_lead,
            detected_entities,
            relevance_score,
            sniper_keywords
        ) VALUES (
            %s, %s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        RETURNING doc_id::text;
    """
    params = (
        doc_hash,
        silver_doc.get("canonical_event_id", ""),
        bronze_ref,                                     # UUID cast in SQL
        source_name,
        silver_doc.get("author", ""),
        silver_doc.get("original_url", ""),
        publish_date,                                   # TIMESTAMPTZ or NULL
        silver_doc.get("full_text_raw", ""),
        silver_doc.get("inverted_pyramid_lead", ""),
        Json(silver_doc.get("detected_entities", [])),  # JSONB — empty at Silver layer
        float(silver_doc.get("relevance_score", 0.0)),
        Json(silver_doc.get("sniper_keywords", [])),    # JSONB list of matched keywords
    )

    with get_cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()

    doc_id = row["doc_id"]
    logger.debug(
        "[knowledge_vault] Archived doc_id=%s  source=%s  hash=%s…",
        doc_id, source_name, doc_hash[:16],
    )
    return doc_id


# ==========================================================
# Deduplication check
# ==========================================================

def exists_by_document_hash(document_hash: str) -> bool:
    """
    Check whether a document with this SHA-256 hash already exists.

    Called by archive() before every insert to implement the Silver-layer dedup
    guard (Section 4.1C). The query hits the UNIQUE index on document_hash
    (B-tree) which is O(log n) regardless of table size.

    Args:
        document_hash: 64-char lowercase SHA-256 hex digest from hash_document().

    Returns:
        True if at least one row with this hash exists.
    """
    sql = """
        SELECT 1 FROM knowledge_vault
        WHERE document_hash = %s
        LIMIT 1;
    """
    with get_cursor() as cur:
        cur.execute(sql, (document_hash,))
        return cur.fetchone() is not None


# ==========================================================
# Fetch by Primary Key
# ==========================================================

def fetch_by_doc_id(doc_id: str) -> Optional[dict]:
    """
    Retrieve one knowledge_vault row by its primary key.

    Used by:
        - The Researcher RAG agent: drill-down from a Gold Global Signal record
          (via knowledge_vectors.silver_data_ref → knowledge_vault.doc_id)
          to inspect the original article body for context (Section 5.1).
        - Gold Job: reads the Silver document to build the embedding text
          when replaying failed Gold enrichments.
        - Gate 3 tests: verifies the row written by archive() is retrievable.

    Args:
        doc_id: UUID string (primary key of knowledge_vault).

    Returns:
        Row as a RealDictCursor dict, or None if not found.
    """
    sql = """
        SELECT
            doc_id::text,
            document_hash,
            canonical_event_id,
            raw_data_ref::text,
            source_name,
            author,
            original_url,
            publish_date,
            full_text_raw,
            inverted_pyramid_lead,
            detected_entities,
            relevance_score,
            sniper_keywords,
            ingested_at
        FROM knowledge_vault
        WHERE doc_id = %s::uuid;
    """
    with get_cursor() as cur:
        cur.execute(sql, (doc_id,))
        return cur.fetchone()


# ==========================================================
# Fetch by Canonical Event
# ==========================================================

def fetch_by_canonical_event(
    canonical_event_id: str,
    source_name: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    """
    Retrieve knowledge_vault rows linked to a canonical event.

    Used by the Researcher RAG agent when drilling down from a Gold signal
    to all related articles (e.g., multiple newsapi articles covering the
    same geopolitical event keyed by the same canonical_event_id).

    Args:
        canonical_event_id: The shared grouping key (Bronze envelope event_id).
        source_name:        Optional filter ('newsapi', 'arxiv').
        limit:              Maximum rows returned (default 100).

    Returns:
        List of row dicts ordered by publish_date DESC.
    """
    if source_name:
        sql = """
            SELECT
                doc_id::text,
                document_hash,
                canonical_event_id,
                source_name,
                original_url,
                publish_date,
                inverted_pyramid_lead,
                relevance_score,
                ingested_at
            FROM knowledge_vault
            WHERE canonical_event_id = %s
              AND source_name = %s
            ORDER BY publish_date DESC NULLS LAST
            LIMIT %s;
        """
        params: tuple = (canonical_event_id, source_name, limit)
    else:
        sql = """
            SELECT
                doc_id::text,
                document_hash,
                canonical_event_id,
                source_name,
                original_url,
                publish_date,
                inverted_pyramid_lead,
                relevance_score,
                ingested_at
            FROM knowledge_vault
            WHERE canonical_event_id = %s
            ORDER BY publish_date DESC NULLS LAST
            LIMIT %s;
        """
        params = (canonical_event_id, limit)

    with get_cursor() as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


# ==========================================================
# Update detected_entities (Gold Job callback)
# ==========================================================

def update_detected_entities(doc_id: str, detected_entities: list) -> None:
    """
    Backfill detected_entities after Gold Job OpenAI extraction.

    The Silver Job stores detected_entities=[] at archive time because
    entity extraction runs in the Gold Job (Section 4.2A). After the Gold
    Job's Cognitive Metadata call returns extracted_entities, the Flink
    Gold ProcessFunction calls this function to update the Silver row
    in-place.

    Why UPDATE instead of re-insert: the Silver document is already
    deduplicated by document_hash. Re-inserting would conflict on the
    UNIQUE constraint. Updating the entities column is the correct operation.

    Args:
        doc_id:             UUID string (primary key of knowledge_vault).
        detected_entities:  List of entity strings from enrichment_ai.extracted_entities.
    """
    sql = """
        UPDATE knowledge_vault
        SET detected_entities = %s
        WHERE doc_id = %s::uuid;
    """
    with get_cursor() as cur:
        cur.execute(sql, (Json(detected_entities), doc_id))
    logger.debug(
        "[knowledge_vault] Updated detected_entities for doc_id=%s (%d entities)",
        doc_id, len(detected_entities),
    )


# ==========================================================
# Internal helpers
# ==========================================================

def _validate_record(silver_doc: dict) -> None:
    """
    Lightweight pre-insert structural check.

    Full Silver schema validation happens in silver_job.process_newsapi_message()
    before this module is called. This guard catches programming errors (wrong
    dict type passed, missing mandatory keys) with a clearer error message than
    a psycopg2 IntegrityError or NOT NULL constraint violation.
    """
    if not isinstance(silver_doc, dict):
        raise ValueError(
            f"[knowledge_vault.archive] Record must be a dict, "
            f"got {type(silver_doc).__name__}."
        )
    source = silver_doc.get("source_name", "")
    if not source:
        raise ValueError("[knowledge_vault.archive] 'source_name' is required.")
    if source not in VALID_SOURCES:
        raise ValueError(
            f"[knowledge_vault.archive] source_name '{source}' is not valid. "
            f"Expected one of: {sorted(VALID_SOURCES)}"
        )
    doc_hash = silver_doc.get("document_hash", "")
    if not doc_hash or len(doc_hash) != 64:
        raise ValueError(
            f"[knowledge_vault.archive] 'document_hash' must be a 64-char "
            f"SHA-256 hex string. Got: {doc_hash!r}"
        )
    if not silver_doc.get("full_text_raw") and not silver_doc.get("inverted_pyramid_lead"):
        raise ValueError(
            "[knowledge_vault.archive] At least one of 'full_text_raw' or "
            "'inverted_pyramid_lead' must be non-empty."
        )
