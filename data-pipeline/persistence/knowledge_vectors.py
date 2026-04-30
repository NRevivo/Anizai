"""
Knowledge Vectors — Gold Layer HNSW-indexed vector persistence (Section 5.2, C.5).

Persists AI-enriched Gold Global Signal records into the `knowledge_vectors`
PostgreSQL table, which carries an independent HNSW index for sub-second
semantic similarity search by The Researcher agent (Section 6.2).

Sources written here (Section 5.1 / Part 1.5 Vector Persistence Map):
    NewsAPI   → entry_type 'news_article'
    ArXiv     → entry_type 'arxiv_paper'
    Telegram  → entry_type 'direct_message'

Why independent from social_vectors (Section 5.2 / C.5 architectural note):
    Mixing heterogeneous object types (news/research vs. community discourse)
    in a shared HNSW index degrades recall quality: post-hoc entry_type
    filtering consumes neighbor slots after the ANN search has already run.
    Two independent indexes eliminate this penalty entirely. The Researcher
    queries stay within knowledge_vectors; The Pulse Analyst stays within
    social_vectors — no cross-contamination of neighbor slots.

Deduplication: ON CONFLICT (signal_id) DO NOTHING — Flink exactly-once
re-deliveries and pipeline restarts never produce duplicate rows. The
signal_id comes from the Gold record built by build_gold_global_signal().

Table: knowledge_vectors
Embedding dimension: 1536 (OpenAI text-embedding-3-small)
Index: HNSW, cosine distance, m=16, ef_construction=64

Public interface:
    insert(record)                            → signal_id (str)
    exists_by_signal_id(signal_id)            → bool
    fetch_by_signal_id(signal_id)             → dict | None
    fetch_by_canonical_event(id, platform)    → list[dict]
    similarity_search(vector, limit, filters) → list[dict]

References:
    - Section 5.2:  Vector Intelligence — pgvector with HNSW
    - Section 6.2:  The Researcher agent (primary consumer of this table)
    - Section C.5:  Gold Global Signal Schema
    - Section 3.2:  DRY — all DB logic in dedicated persistence modules
    - Section 3.3:  Service Isolation — only this module writes to knowledge_vectors
    - Section 4.4:  Task 3.7 — formerly vector_vault.py; renamed to match table name
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pgvector.psycopg2 import register_vector
from psycopg2.extras import Json, RealDictCursor

from utils.db import get_connection, get_cursor
from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)
setup_logging()

# Embedding dimension must match vector(N) in init.sql and the OpenAI model used
# by the Gold Job (text-embedding-3-small = 1536 dimensions, Section 5.2).
EMBEDDING_DIM = 1536

# Valid source_platform values — enforced by CHECK constraint in init.sql
VALID_PLATFORMS = frozenset({"newsapi", "arxiv", "telegram"})

# entry_type derivation from source_platform (Section C.5 Platform Extensions)
_ENTRY_TYPE_MAP: dict[str, str] = {
    "newsapi":  "news_article",
    "arxiv":    "arxiv_paper",
    "telegram": "direct_message",
}


# ==========================================================
# Insert
# ==========================================================

def insert(record: dict) -> str:
    """
    Insert one Gold Global Signal record into `knowledge_vectors`.

    Idempotent: ON CONFLICT (signal_id) DO NOTHING means Flink exactly-once
    re-deliveries and pipeline restarts never produce duplicate rows.

    Field mapping from the Gold record (Section C.5):
        metadata.signal_id          → signal_id (PRIMARY KEY)
        metadata.canonical_event_id → canonical_event_id
        metadata.silver_data_ref    → silver_data_ref (bridge to knowledge_vault)
        metadata.raw_data_ref       → raw_data_ref (bridge to Bronze)
        metadata.source_platform    → source_platform
        metadata.published_at       → published_at
        content_vitals              → content_vitals JSONB
        enrichment_ai               → enrichment_ai JSONB
        domain_context              → domain_context JSONB
        embedding                   → embedding vector(1536)

    Why entry_type is derived, not passed: callers (the Gold Job Flink function)
    should not need to know the entry_type string — it is deterministically
    derived from source_platform so the caller never risks passing a wrong value.

    Args:
        record: Gold Global Signal dict conforming to Section C.5.
                Required top-level keys:
                  metadata        — signal_id, canonical_event_id, source_platform,
                                    published_at, silver_data_ref, raw_data_ref
                  content_vitals  — title, url, description_snippet
                  enrichment_ai   — impact_level, urgency_level, reliability_score, …
                  domain_context  — source-specific extension block (Section C.5):
                                    NewsAPI → sniper_keywords, is_breaking, share_count, geospatial_focus
                                    ArXiv   → authors, is_peer_reviewed, citation_count, domain_tags
                  embedding       — list[float], exactly EMBEDDING_DIM elements

    Returns:
        signal_id: UUID string of the inserted (or pre-existing) row.

    Raises:
        ValueError: If the record is structurally invalid before hitting the DB.
        psycopg2.Error: On unexpected database errors.
    """
    _validate_record(record)

    meta            = record["metadata"]
    signal_id       = meta.get("signal_id") or str(uuid.uuid4())
    source_platform = meta["source_platform"]
    entry_type      = _ENTRY_TYPE_MAP.get(source_platform, "news_article")

    published_at = meta.get("published_at")
    if isinstance(published_at, str):
        published_at = _parse_timestamp(published_at)

    # silver_data_ref and raw_data_ref are UUID columns — pass None if absent
    # rather than an empty string which would fail the UUID cast.
    silver_data_ref = meta.get("silver_data_ref") or None
    raw_data_ref    = meta.get("raw_data_ref") or None

    embedding = record["embedding"]
    _validate_embedding(embedding)

    sql = """
        INSERT INTO knowledge_vectors (
            signal_id,
            canonical_event_id,
            silver_data_ref,
            raw_data_ref,
            source_platform,
            entry_type,
            published_at,
            content_vitals,
            enrichment_ai,
            domain_context,
            embedding
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s
        )
        ON CONFLICT (signal_id) DO NOTHING
        RETURNING signal_id;
    """
    params = (
        signal_id,
        meta["canonical_event_id"],
        silver_data_ref,                        # UUID or NULL
        raw_data_ref,                           # UUID or NULL
        source_platform,
        entry_type,
        published_at,                           # TIMESTAMPTZ or NULL
        Json(record["content_vitals"]),
        Json(record["enrichment_ai"]),
        Json(record.get("domain_context", {})), # NewsAPI-specific extensions
        embedding,                              # list[float] → vector(1536) via pgvector adapter
    )

    with get_connection() as conn:
        # Register the pgvector type adapter on this connection so psycopg2
        # knows how to serialise list[float] → PostgreSQL vector(N).
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()

    if row is None:
        logger.debug(
            "[knowledge_vectors] Skipped duplicate signal_id=%s (already exists)", signal_id
        )
    else:
        logger.debug(
            "[knowledge_vectors] Inserted signal_id=%s  platform=%s  entry_type=%s",
            signal_id, source_platform, entry_type,
        )

    return signal_id


# ==========================================================
# Existence Check (deduplication guard)
# ==========================================================

def exists_by_signal_id(signal_id: str) -> bool:
    """
    Check whether a Gold Global Signal row already exists by signal_id.

    Called by the Gold Job Flink function as a cost guard: if the record
    was already vectorised and persisted (e.g., from a previous pipeline run),
    skip the OpenAI embedding call entirely. This is secondary to the
    ON CONFLICT guard in insert() — it avoids the embedding API cost before
    a DB round-trip can even be made (Section 4.3 backpressure).

    Args:
        signal_id: UUID string (primary key of knowledge_vectors).

    Returns:
        True if a row with this signal_id exists.
    """
    sql = """
        SELECT 1 FROM knowledge_vectors
        WHERE signal_id = %s::uuid
        LIMIT 1;
    """
    with get_cursor() as cur:
        cur.execute(sql, (signal_id,))
        return cur.fetchone() is not None


# ==========================================================
# Fetch by Primary Key
# ==========================================================

def fetch_by_signal_id(signal_id: str) -> Optional[dict]:
    """
    Retrieve one knowledge_vectors row by its primary key.

    Used by:
        - The Researcher RAG agent: loads the Gold record (including the
          embedding and enrichment_ai metadata) for a specific signal.
        - Gate 3 tests: verifies the row written by insert() is retrievable.

    Args:
        signal_id: UUID string (primary key of knowledge_vectors).

    Returns:
        Row as a dict (psycopg2 RealDictCursor), or None if not found.
        Note: embedding is returned as a list[float] (pgvector adapter handles
        this automatically when register_vector() is called on the connection).
    """
    sql = """
        SELECT
            signal_id::text,
            canonical_event_id,
            silver_data_ref::text,
            raw_data_ref::text,
            source_platform,
            entry_type,
            published_at,
            content_vitals,
            enrichment_ai,
            domain_context,
            embedding,
            ingested_at
        FROM knowledge_vectors
        WHERE signal_id = %s::uuid;
    """
    with get_connection() as conn:
        register_vector(conn)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (signal_id,))
            row = cur.fetchone()
    return dict(row) if row else None


# ==========================================================
# Fetch by Canonical Event
# ==========================================================

def fetch_by_canonical_event(
    canonical_event_id: str,
    source_platform: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    """
    Retrieve knowledge_vectors rows for a canonical event.

    Used by the Researcher RAG agent when grouping all Gold signals
    linked to the same geopolitical or market event for comparative analysis
    (e.g., all NewsAPI + ArXiv articles on the same OPEC decision).

    Args:
        canonical_event_id: Shared grouping key (Bronze envelope event_id).
        source_platform:    Optional filter ('newsapi', 'arxiv', 'telegram').
        limit:              Maximum rows returned (default 100).

    Returns:
        List of row dicts ordered by published_at DESC.
    """
    if source_platform:
        sql = """
            SELECT
                signal_id::text,
                canonical_event_id,
                source_platform,
                entry_type,
                published_at,
                content_vitals,
                enrichment_ai,
                domain_context,
                ingested_at
            FROM knowledge_vectors
            WHERE canonical_event_id = %s
              AND source_platform     = %s
            ORDER BY published_at DESC NULLS LAST
            LIMIT %s;
        """
        params: tuple = (canonical_event_id, source_platform, limit)
    else:
        sql = """
            SELECT
                signal_id::text,
                canonical_event_id,
                source_platform,
                entry_type,
                published_at,
                content_vitals,
                enrichment_ai,
                domain_context,
                ingested_at
            FROM knowledge_vectors
            WHERE canonical_event_id = %s
            ORDER BY published_at DESC NULLS LAST
            LIMIT %s;
        """
        params = (canonical_event_id, limit)

    with get_cursor() as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


# ==========================================================
# Similarity Search (HNSW ANN query)
# ==========================================================

def similarity_search(
    query_vector: list[float],
    limit:             int             = 10,
    source_platform:   Optional[str]  = None,
    min_impact_level:  Optional[int]  = None,
    min_reliability:   Optional[float] = None,
) -> list[dict]:
    """
    Run a cosine-similarity ANN search against the knowledge_vectors HNSW index.

    Used by The Researcher agent (Section 6.2) to find the top-k most
    semantically similar Gold Global Signal records to a query embedding.
    Optional filters narrow the search BEFORE the HNSW traversal using pgvector's
    post-filter API (WHERE clauses evaluated during the index scan).

    Cosine distance is used (vector_cosine_ops) — pgvector's <=> operator.
    Distance 0.0 = identical vectors; 2.0 = maximally dissimilar.
    The similarity score returned is (1 - cosine_distance) for interpretability.

    Why post-filter (not pre-filter): pgvector HNSW does not support pre-filtered
    ANN search. Filters are applied after the index returns candidates. For the
    typical RAG query (top-10 similar articles with impact_level >= 4), the HNSW
    index produces a superset of high-quality candidates from which filters
    quickly select the relevant subset.

    Args:
        query_vector:     1536-dim float list (embedding of the user's query).
        limit:            Number of top-k results to return (default 10).
        source_platform:  Optional platform filter ('newsapi', 'arxiv', 'telegram').
        min_impact_level: Optional minimum impact_level from enrichment_ai.
        min_reliability:  Optional minimum reliability_score from enrichment_ai.

    Returns:
        List of row dicts with an added 'similarity' float field (0.0–1.0),
        ordered by similarity DESC.
    """
    _validate_embedding(query_vector)

    # Build WHERE clauses dynamically based on optional filters.
    where_clauses: list[str] = []
    filter_params: list[Any] = []

    if source_platform:
        where_clauses.append("kv.source_platform = %s")
        filter_params.append(source_platform)
    if min_impact_level is not None:
        where_clauses.append(
            "(kv.enrichment_ai->>'impact_level')::int >= %s"
        )
        filter_params.append(min_impact_level)
    if min_reliability is not None:
        where_clauses.append(
            "(kv.enrichment_ai->>'reliability_score')::float >= %s"
        )
        filter_params.append(min_reliability)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # silver_data_ref is the knowledge_vault.doc_id linkage — required by
    # the Researcher agent's drill-down step (spec §8.4.1 step 3).
    # Parity with social_vectors.similarity_search, which has always
    # exposed it. Restored in T19.2 (correction to Sprint 19 audit §3.1).
    sql = f"""
        SELECT
            kv.signal_id::text,
            kv.canonical_event_id,
            kv.silver_data_ref::text,
            kv.source_platform,
            kv.entry_type,
            kv.published_at,
            kv.content_vitals,
            kv.enrichment_ai,
            kv.domain_context,
            1 - (kv.embedding <=> %s::vector) AS similarity
        FROM knowledge_vectors kv
        {where_sql}
        ORDER BY kv.embedding <=> %s::vector
        LIMIT %s;
    """
    # query_vector appears twice: once for the similarity score, once for ORDER BY
    params = [query_vector] + filter_params + [query_vector, limit]

    with get_connection() as conn:
        register_vector(conn)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]


# ==========================================================
# Internal helpers
# ==========================================================

def _validate_record(record: dict) -> None:
    """
    Lightweight pre-insert structural check.

    Full Gold schema validation runs in process_newsapi_gold_message() (gold_job.py)
    before this module is called. This guard catches programmer errors
    (wrong dict type, missing mandatory keys) with a clearer error than a
    psycopg2 NOT NULL constraint violation.
    """
    if not isinstance(record, dict):
        raise ValueError(
            f"[knowledge_vectors.insert] Record must be a dict, "
            f"got {type(record).__name__}."
        )
    meta = record.get("metadata", {})
    if not isinstance(meta, dict):
        raise ValueError("[knowledge_vectors.insert] 'metadata' must be a dict.")

    platform = meta.get("source_platform", "")
    if not platform:
        raise ValueError("[knowledge_vectors.insert] 'metadata.source_platform' is required.")
    if platform not in VALID_PLATFORMS:
        raise ValueError(
            f"[knowledge_vectors.insert] source_platform '{platform}' is not valid. "
            f"Expected one of: {sorted(VALID_PLATFORMS)}"
        )
    if "embedding" not in record:
        raise ValueError("[knowledge_vectors.insert] 'embedding' key is required.")


def _validate_embedding(embedding: Any) -> None:
    """
    Check that embedding is a list of floats with the correct dimension.

    A wrong-dimension embedding would fail the PostgreSQL vector(1536) cast
    with a cryptic error. This guard surfaces the problem at the Python level
    with a dimension-specific message.
    """
    if not isinstance(embedding, list):
        raise ValueError(
            f"[knowledge_vectors] embedding must be list[float], "
            f"got {type(embedding).__name__}."
        )
    if len(embedding) != EMBEDDING_DIM:
        raise ValueError(
            f"[knowledge_vectors] embedding must have {EMBEDDING_DIM} dimensions, "
            f"got {len(embedding)}."
        )


def _parse_timestamp(ts_str: str) -> Optional[datetime]:
    """
    Parse an ISO8601 timestamp string to a timezone-aware datetime.

    Handles the 'Z' suffix (replaced with '+00:00') and falls back to None
    on parse failure rather than crashing the insert.
    """
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        logger.warning(
            "[knowledge_vectors] Could not parse timestamp %r — storing NULL", ts_str
        )
        return None
