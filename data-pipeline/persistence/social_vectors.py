"""
Social Vectors — Gold Layer HNSW-indexed vector persistence (Section 5.2, C.6).

Persists AI-enriched Gold Social Pulse records into the `social_vectors`
PostgreSQL table, which carries an independent HNSW index for sub-second
semantic similarity search by The Pulse Analyst agent (Section 6.2).

Sources written here: Polymarket (market_consensus), HackerNews (hackernews_story_summary).
Reddit removed (Sprint 11 T4) — API pre-approval required (Nov 2025 policy).

Why independent from knowledge_vectors: mixing heterogeneous object types
(news/research vs. community discourse) in a shared HNSW index degrades
recall quality — post-hoc entry_type filtering consumes neighbor slots
after the ANN search has already run. Two independent indexes eliminate
this penalty entirely (Section C.5 architectural note, init.sql Section 3b).

Table: social_vectors
Embedding dimension: 1536 (OpenAI text-embedding-3-small)
Index: HNSW, cosine distance, m=16, ef_construction=64

Public interface:
    insert(record)                           → signal_id (str)
    exists_by_canonical_event(id, platform)  → bool
    fetch_by_signal_id(signal_id)            → dict | None
    similarity_search(vector, limit, ...)    → list[dict]

References:
    - Section 5.2:  Vector Intelligence — pgvector with HNSW
    - Section 6.2:  The Pulse Analyst agent (primary consumer)
    - Section C.6:  Gold Social Pulse Schema
    - Section 3.2:  DRY — all DB logic in dedicated persistence modules
    - Section 3.3:  Service Isolation — only this module writes to social_vectors
"""

from __future__ import annotations

import json
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

# Embedding dimension must match the vector(N) declaration in init.sql
# and the OpenAI model used by the Gold Job (text-embedding-3-small = 1536).
EMBEDDING_DIM = 1536

# Valid source_platform values — enforced by CHECK constraint in init.sql
# Reddit removed (Sprint 11 T4) — API pre-approval required (Nov 2025 policy)
VALID_PLATFORMS = frozenset({"polymarket", "hackernews"})


# ==========================================================
# Insert
# ==========================================================

def insert(record: dict) -> str:
    """
    Insert one Gold Social Pulse record into `social_vectors`.

    Idempotent: ON CONFLICT (signal_id) DO NOTHING means pipeline restarts
    and Flink exactly-once re-deliveries never produce duplicate rows.

    Args:
        record: Gold Social Pulse dict conforming to Section C.6.
                Required top-level keys:
                  metadata        — signal_id, canonical_event_id, source_platform,
                                    published_at, silver_data_ref, raw_data_ref
                  content_vitals  — title, url, description_snippet
                  enrichment_ai   — impact_level, urgency_level, reliability_score, …
                  social_context  — community_name, primary_engagement_score, …
                  platform_logic  — platform-specific extension (Section C.6 table)
                  embedding       — list[float], exactly EMBEDDING_DIM elements

    Returns:
        signal_id: UUID string of the inserted (or pre-existing) row.

    Raises:
        ValueError: If the record is structurally invalid before hitting the DB.
        psycopg2.Error: On unexpected database errors (connection, constraint, etc.).
    """
    _validate_record(record)

    meta           = record["metadata"]
    signal_id      = meta.get("signal_id") or str(uuid.uuid4())
    source_platform = meta["source_platform"]

    # Derive entry_type from source_platform when not explicitly set.
    # This matches the Section C.6 Platform Extensions table entries.
    entry_type = record.get("entry_type") or _default_entry_type(source_platform)

    published_at = meta.get("published_at")
    if isinstance(published_at, str):
        published_at = _parse_timestamp(published_at)

    embedding = record["embedding"]
    _validate_embedding(embedding)

    sql = """
        INSERT INTO social_vectors (
            signal_id,
            canonical_event_id,
            silver_data_ref,
            raw_data_ref,
            source_platform,
            entry_type,
            published_at,
            content_vitals,
            enrichment_ai,
            social_context,
            platform_logic,
            embedding
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s
        )
        ON CONFLICT (signal_id) DO NOTHING
        RETURNING signal_id;
    """

    params = (
        signal_id,
        meta["canonical_event_id"],
        meta.get("silver_data_ref"),          # nullable
        meta.get("raw_data_ref"),              # nullable
        source_platform,
        entry_type,
        published_at,                          # nullable TIMESTAMPTZ
        Json(record["content_vitals"]),
        Json(record["enrichment_ai"]),
        Json(record["social_context"]),
        Json(record.get("platform_logic", {})),
        embedding,                             # list[float] → vector(1536) via pgvector adapter
    )

    with get_connection() as conn:
        # Register the pgvector type adapter on this connection so psycopg2
        # knows how to serialise list[float] → PostgreSQL vector(N).
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()

    if row is None:
        # ON CONFLICT DO NOTHING — row already existed; return the provided signal_id
        logger.debug(
            "[social_vectors] Skipped duplicate signal_id=%s (already exists)", signal_id
        )
    else:
        logger.debug("[social_vectors] Inserted signal_id=%s", signal_id)

    return signal_id


# ==========================================================
# Existence Check (deduplication)
# ==========================================================

def exists_by_canonical_event(
    canonical_event_id: str,
    source_platform: Optional[str] = None,
) -> bool:
    """
    Check whether a Gold Social Pulse record already exists for a given
    canonical_event_id, optionally filtered by source_platform.

    Why used: the Gold Job calls this before generating an expensive OpenAI
    embedding to avoid re-processing consensus bundles that have already
    been vectorised and persisted (cost guard — Section 4.3 backpressure).

    Args:
        canonical_event_id: The shared event key across Bronze → Silver → Gold.
        source_platform:    Optional filter — 'polymarket', 'hackernews'.
                            If None, checks across all platforms.

    Returns:
        True if at least one matching row exists, False otherwise.
    """
    if source_platform:
        sql = """
            SELECT 1 FROM social_vectors
            WHERE canonical_event_id = %s
              AND source_platform     = %s
            LIMIT 1;
        """
        params: tuple = (canonical_event_id, source_platform)
    else:
        sql = """
            SELECT 1 FROM social_vectors
            WHERE canonical_event_id = %s
            LIMIT 1;
        """
        params = (canonical_event_id,)

    with get_cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone() is not None


# ==========================================================
# Fetch by Primary Key (Gate 3 / RAG drill-down)
# ==========================================================

def fetch_by_signal_id(signal_id: str) -> Optional[dict]:
    """
    Retrieve one Gold Social Pulse record by its primary key.

    Used by:
        - Gate 3 tests: verifies the record written by insert() is retrievable.
        - The Pulse Analyst agent: drill-down from a search result to the
          full enrichment_ai + social_context payload (Section 6.2).

    Args:
        signal_id: UUID string (primary key of social_vectors).

    Returns:
        Row as a dict (RealDictCursor), or None if not found.
        The `embedding` column is excluded — callers that need it should use
        similarity_search() which returns similarity score alongside vectors.
    """
    sql = """
        SELECT
            signal_id,
            canonical_event_id,
            silver_data_ref,
            raw_data_ref,
            source_platform,
            entry_type,
            published_at,
            content_vitals,
            enrichment_ai,
            social_context,
            platform_logic,
            ingested_at
        FROM social_vectors
        WHERE signal_id = %s;
    """
    with get_cursor() as cur:
        cur.execute(sql, (signal_id,))
        return cur.fetchone()


# ==========================================================
# Semantic Similarity Search (RAG query interface)
# ==========================================================

def similarity_search(
    query_vector: list[float],
    limit: int = 10,
    min_impact: int = 1,
    platforms: Optional[list[str]] = None,
    min_reliability: float = 0.0,
) -> list[dict]:
    """
    Execute a cosine similarity search against the social_vectors HNSW index.

    Returns the top `limit` records ordered by semantic similarity to
    `query_vector`, with optional hard metadata filters applied before
    the ANN search (Section 5.2 "Metadata Filtering" requirement).

    Filter application strategy: WHERE clauses on indexed columns
    (source_platform, enrichment_ai JSONB) are pushed down to PostgreSQL
    before the HNSW scan. This is more efficient than post-processing
    the ANN results because high-selectivity filters significantly shrink
    the candidate set before cosine distance is evaluated.

    Args:
        query_vector:    1536-dim query embedding from The Pulse Analyst.
        limit:           Maximum number of results to return (default 10).
        min_impact:      Minimum impact_level from enrichment_ai (1–5).
                         Set to 1 to disable (returns all impact levels).
        platforms:       Optional list of source_platforms to restrict search.
                         E.g., ['polymarket'] for market-only consensus vectors.
        min_reliability: Minimum reliability_score (0.0–1.0, default 0.0).

    Returns:
        List of row dicts, each containing all non-embedding columns plus
        `similarity_score` (float, 0.0–1.0 cosine similarity).
        Empty list if no results match the filters.

    Raises:
        ValueError: If query_vector dimension does not equal EMBEDDING_DIM.
    """
    _validate_embedding(query_vector)

    filters: list[str] = []
    params: list[Any]  = []

    if min_impact > 1:
        filters.append("(enrichment_ai->>'impact_level')::int >= %s")
        params.append(min_impact)

    if min_reliability > 0.0:
        filters.append("(enrichment_ai->>'reliability_score')::float >= %s")
        params.append(min_reliability)

    if platforms:
        filters.append("source_platform = ANY(%s)")
        params.append(platforms)

    where_clause = ("WHERE " + " AND ".join(filters)) if filters else ""

    sql = f"""
        SELECT
            signal_id,
            canonical_event_id,
            silver_data_ref,
            source_platform,
            entry_type,
            published_at,
            content_vitals,
            enrichment_ai,
            social_context,
            platform_logic,
            1 - (embedding <=> %s::vector) AS similarity_score
        FROM social_vectors
        {where_clause}
        ORDER BY embedding <=> %s::vector
        LIMIT %s;
    """

    # embedding parameter appears twice: once for similarity_score, once for ORDER BY
    all_params = [query_vector] + params + [query_vector, limit]

    with get_connection() as conn:
        register_vector(conn)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, all_params)
            rows = cur.fetchall()

    return [dict(row) for row in rows]


# ==========================================================
# Batch Insert
# ==========================================================

def insert_batch(records: list[dict]) -> list[str]:
    """
    Insert multiple Gold Social Pulse records in a single transaction.

    Why single transaction: the Gold Job emits Consensus Bundle vectors
    in temporal blocks (Section 4.2A). Committing them atomically means
    either all vectors for a time window land in the DB or none do —
    preventing partial bundles from being served to the RAG agent.

    Args:
        records: List of Gold Social Pulse dicts (same schema as insert()).

    Returns:
        List of signal_id strings in insertion order.
    """
    return [insert(record) for record in records]


# ==========================================================
# Internal Helpers
# ==========================================================

def _validate_record(record: dict) -> None:
    """
    Lightweight pre-insert structural check.

    Why validate here in addition to validate_gold_social_pulse() in
    validators.py: the Gold Job runs the full schema validator before
    calling insert(). This check is a last-resort guard catching
    programming errors (e.g., missing 'embedding' key) that would
    otherwise surface as cryptic psycopg2 errors.
    """
    required_top = ("metadata", "content_vitals", "enrichment_ai",
                    "social_context", "embedding")
    for key in required_top:
        if key not in record:
            raise ValueError(
                f"[social_vectors.insert] Missing required key: '{key}'. "
                "Record must conform to Gold Social Pulse schema (Section C.6)."
            )

    meta = record["metadata"]
    required_meta = ("canonical_event_id", "source_platform")
    for key in required_meta:
        if not meta.get(key):
            raise ValueError(
                f"[social_vectors.insert] metadata['{key}'] is missing or empty."
            )

    platform = meta["source_platform"]
    if platform not in VALID_PLATFORMS:
        raise ValueError(
            f"[social_vectors.insert] source_platform '{platform}' is not valid. "
            f"Expected one of: {sorted(VALID_PLATFORMS)}"
        )


def _validate_embedding(embedding: Any) -> None:
    """
    Confirm the embedding is a list of EMBEDDING_DIM floats.

    Why strict dimension check: inserting a wrong-dimension vector into
    a vector(1536) column raises a DB error. Catching it here produces
    a clearer message and avoids a wasted round-trip to PostgreSQL.
    """
    if not isinstance(embedding, (list, tuple)):
        raise ValueError(
            f"[social_vectors] embedding must be a list, got {type(embedding).__name__}"
        )
    if len(embedding) != EMBEDDING_DIM:
        raise ValueError(
            f"[social_vectors] embedding dimension mismatch: "
            f"expected {EMBEDDING_DIM}, got {len(embedding)}"
        )


def _default_entry_type(source_platform: str) -> str:
    """
    Return the canonical entry_type for a given source_platform.

    Matches the Section C.6 Platform Extensions table:
        polymarket  → 'market_consensus'
        hackernews  → 'hackernews_story_summary'
    Reddit removed (Sprint 11 T4) — API pre-approval required (Nov 2025 policy).
    """
    return {
        "polymarket":  "market_consensus",
        "hackernews":  "hackernews_story_summary",
    }.get(source_platform, source_platform)


def _parse_timestamp(ts: str) -> Optional[datetime]:
    """Parse an ISO8601 string to a timezone-aware datetime, or return None on failure."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        logger.warning(
            "[social_vectors] Could not parse published_at timestamp: %r — storing NULL", ts
        )
        return None
