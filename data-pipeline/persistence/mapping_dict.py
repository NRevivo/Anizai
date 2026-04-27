"""
Mapping Dictionary — Cross-Platform ID Linkage (Section 5.4).

Maps platform-specific IDs to canonical_event_id, enabling the system
to bridge disparate sources (e.g., linking a Polymarket contract to a
Reuters article covering the same event).

The `mapping_dict` table is the linkage backbone: any Gold record whose
embedding similarity to an existing knowledge_vectors record exceeds the
0.85 threshold (Section B.9) gets a row here that ties the two together
under a shared canonical_event_id. The RAG agent uses these rows to return
correlated signals from different platforms when answering a query.

Table schema (init.sql Section 6):
    mapping_id           UUID PK (auto-generated)
    canonical_event_id   TEXT NOT NULL
    platform             TEXT NOT NULL
    platform_specific_id TEXT NOT NULL
    similarity_score     FLOAT (nullable — NULL for manual/rule-based links)
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
    notes                TEXT (nullable)
    UNIQUE (platform, platform_specific_id)

Public interface:
    link(canonical_event_id, platform, platform_specific_id,
         similarity_score, notes)                          → mapping_id | None
    lookup_by_canonical(canonical_event_id, platform)      → list[dict]
    lookup_by_platform_id(platform, platform_specific_id)  → dict | None
    find_similar_and_link(embedding, platform,
                          platform_specific_id, notes)     → canonical_event_id | None

Why standalone delivery (Section 5.4 / Phase 7):
    Gold job integration is deferred. The two candidate approaches —
    (A) on-insert linkage inside gold_job.py (one extra DB query per Gold record)
    vs. (B) scheduled batch linkage scan — require live pipeline performance data
    to choose correctly. This module is delivered first so the interface is
    stable and testable before integration is decided.

References:
    - Section 5.4:  Infrastructure Tables — The Mapping Dictionary
    - Section B.9:  Canonical Linkage — vector similarity threshold >0.85
    - Section 3.2:  DRY — all DB logic in dedicated persistence modules
    - Section 3.3:  Service Isolation — only this module writes to mapping_dict
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from pgvector.psycopg2 import register_vector
from psycopg2.extras import RealDictCursor

from utils.db import get_connection, get_cursor
from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)
setup_logging()

# Vector similarity threshold for canonical linkage (Section B.9).
# A cosine similarity above this value is considered "same event".
SIMILARITY_THRESHOLD = 0.85

# Number of HNSW candidates to retrieve when searching for a similar embedding.
# We only need the top-1, but fetching top-3 guards against borderline cases
# where the nearest neighbor is a duplicate of the query itself.
_SEARCH_LIMIT = 3

# Embedding dimension must match knowledge_vectors (text-embedding-3-small = 1536).
_EMBEDDING_DIM = 1536


# ==========================================================
# Link (insert with dedup guard)
# ==========================================================

def link(
    canonical_event_id:  str,
    platform:            str,
    platform_specific_id: str,
    similarity_score:    Optional[float] = None,
    notes:               Optional[str]   = None,
) -> Optional[str]:
    """
    Insert a new canonical linkage row into mapping_dict (Section 5.4).

    Idempotent: ON CONFLICT (platform, platform_specific_id) DO NOTHING.
    If the (platform, platform_specific_id) pair is already linked, the
    existing row is preserved and None is returned.

    Why ON CONFLICT DO NOTHING (not DO UPDATE): once a platform ID is linked
    to a canonical_event_id, changing the linkage would corrupt downstream
    RAG queries that already indexed the original assignment. Manual corrections
    require a direct DB update.

    Args:
        canonical_event_id:   The shared event key shared by linked records.
        platform:             Platform name ('polymarket', 'newsapi', 'arxiv',
                              'telegram', 'hackernews', 'fred', …).
        platform_specific_id: Platform's own ID or slug for this record.
        similarity_score:     Cosine similarity at link time (Section B.9).
                              NULL for manual or rule-based links.
        notes:                Optional free-text annotation on the linkage.

    Returns:
        mapping_id: UUID string of the inserted row.
        None:       If (platform, platform_specific_id) already exists (idempotent skip).

    Raises:
        ValueError: If canonical_event_id, platform, or platform_specific_id is empty.
        psycopg2.Error: On unexpected database errors.
    """
    _validate_link_args(canonical_event_id, platform, platform_specific_id)

    sql = """
        INSERT INTO mapping_dict (
            canonical_event_id,
            platform,
            platform_specific_id,
            similarity_score,
            notes
        ) VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (platform, platform_specific_id) DO NOTHING
        RETURNING mapping_id::text;
    """
    params = (
        canonical_event_id,
        platform,
        platform_specific_id,
        similarity_score,
        notes,
    )

    with get_cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()

    if row is None:
        logger.debug(
            "[mapping_dict] Duplicate link: platform=%s id=%s — skipped",
            platform, platform_specific_id,
        )
        return None

    mapping_id = row["mapping_id"]
    logger.debug(
        "[mapping_dict] Linked mapping_id=%s  platform=%s  id=%s  "
        "canonical=%s  similarity=%.4f",
        mapping_id, platform, platform_specific_id, canonical_event_id,
        similarity_score if similarity_score is not None else -1.0,
    )
    return mapping_id


# ==========================================================
# Lookup by Canonical Event
# ==========================================================

def lookup_by_canonical(
    canonical_event_id: str,
    platform: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    """
    Retrieve all platform IDs linked to a canonical_event_id (Section 5.4).

    Used by the RAG agent when a Gold signal is returned: it queries
    mapping_dict to find all other platforms that have been linked to
    the same canonical event, enabling cross-platform enrichment.

    Args:
        canonical_event_id: The shared event key.
        platform:           Optional platform filter (e.g. 'polymarket').
        limit:              Maximum rows returned (default 100).

    Returns:
        List of row dicts ordered by created_at DESC.
        Each dict contains: mapping_id, canonical_event_id, platform,
        platform_specific_id, similarity_score, created_at, notes.
        Empty list if no matching rows.
    """
    if platform:
        sql = """
            SELECT
                mapping_id::text,
                canonical_event_id,
                platform,
                platform_specific_id,
                similarity_score,
                created_at,
                notes
            FROM mapping_dict
            WHERE canonical_event_id = %s
              AND platform = %s
            ORDER BY created_at DESC
            LIMIT %s;
        """
        params: tuple = (canonical_event_id, platform, limit)
    else:
        sql = """
            SELECT
                mapping_id::text,
                canonical_event_id,
                platform,
                platform_specific_id,
                similarity_score,
                created_at,
                notes
            FROM mapping_dict
            WHERE canonical_event_id = %s
            ORDER BY created_at DESC
            LIMIT %s;
        """
        params = (canonical_event_id, limit)

    with get_cursor() as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


# ==========================================================
# Lookup by Platform ID
# ==========================================================

def lookup_by_platform_id(
    platform: str,
    platform_specific_id: str,
) -> Optional[dict]:
    """
    Retrieve the canonical linkage for a specific platform record (Section 5.4).

    Used by the Gold Job (deferred — Phase 7) to check whether a newly
    ingested record has already been linked to a canonical event, avoiding
    a redundant similarity search.

    Args:
        platform:             Platform name ('polymarket', 'newsapi', …).
        platform_specific_id: Platform's own ID or slug.

    Returns:
        Row dict with mapping_id, canonical_event_id, platform,
        platform_specific_id, similarity_score, created_at, notes.
        None if the (platform, platform_specific_id) pair is not yet linked.
    """
    sql = """
        SELECT
            mapping_id::text,
            canonical_event_id,
            platform,
            platform_specific_id,
            similarity_score,
            created_at,
            notes
        FROM mapping_dict
        WHERE platform = %s
          AND platform_specific_id = %s
        LIMIT 1;
    """
    with get_cursor() as cur:
        cur.execute(sql, (platform, platform_specific_id))
        row = cur.fetchone()
    return dict(row) if row else None


# ==========================================================
# Find Similar and Link (automated canonical linkage)
# ==========================================================

def find_similar_and_link(
    embedding:            list[float],
    platform:             str,
    platform_specific_id: str,
    notes:                Optional[str] = None,
) -> Optional[str]:
    """
    Search knowledge_vectors for a similar embedding and create a linkage
    if cosine similarity exceeds the 0.85 threshold (Section B.9).

    This is the automated linkage path: the Gold Job (deferred — Phase 7)
    calls this function after computing an embedding for a new record.
    If a sufficiently similar existing record is found in knowledge_vectors,
    its canonical_event_id is assigned to the new record via link().

    Decision tree:
        1. Search knowledge_vectors for top-3 nearest neighbours.
        2. If the top result has similarity > SIMILARITY_THRESHOLD (0.85):
               call link() with that canonical_event_id and similarity score.
               return the canonical_event_id (the new record is now linked).
        3. Otherwise: no link established — return None.
           The caller may assign a new canonical_event_id independently.

    Why search knowledge_vectors (not social_vectors): knowledge_vectors
    contains news and research content. Cross-platform linkage (e.g.,
    Polymarket market → Reuters article) bridges prediction markets to their
    underlying news events. Social-to-social linkage (Polymarket to HackerNews)
    is handled separately by the consensus bundling path (Section 4.2A).

    Args:
        embedding:            1536-dim float list for the new record.
        platform:             Platform name for the new record.
        platform_specific_id: Platform's own ID for the new record.
        notes:                Optional annotation stored with the link row.

    Returns:
        canonical_event_id: The linked canonical event (str) if similarity > 0.85.
        None:               If no match above the threshold was found.

    Raises:
        ValueError: If embedding dimension does not equal _EMBEDDING_DIM.
    """
    _validate_embedding(embedding)

    sql = """
        SELECT
            canonical_event_id,
            1 - (embedding <=> %s::vector) AS similarity
        FROM knowledge_vectors
        ORDER BY embedding <=> %s::vector
        LIMIT %s;
    """

    with get_connection() as conn:
        register_vector(conn)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, [embedding, embedding, _SEARCH_LIMIT])
            rows = cur.fetchall()

    if not rows:
        logger.debug(
            "[mapping_dict] knowledge_vectors is empty — no linkage possible "
            "for platform=%s id=%s", platform, platform_specific_id,
        )
        return None

    top = dict(rows[0])
    similarity = float(top["similarity"])

    if similarity <= SIMILARITY_THRESHOLD:
        logger.debug(
            "[mapping_dict] No match above threshold (%.4f <= %.2f) for "
            "platform=%s id=%s",
            similarity, SIMILARITY_THRESHOLD, platform, platform_specific_id,
        )
        return None

    canonical_event_id: str = top["canonical_event_id"]
    link(
        canonical_event_id=canonical_event_id,
        platform=platform,
        platform_specific_id=platform_specific_id,
        similarity_score=similarity,
        notes=notes,
    )
    logger.info(
        "[mapping_dict] Linked platform=%s id=%s → canonical=%s (similarity=%.4f)",
        platform, platform_specific_id, canonical_event_id, similarity,
    )
    return canonical_event_id


# ==========================================================
# Internal Helpers
# ==========================================================

def _validate_link_args(
    canonical_event_id:  str,
    platform:            str,
    platform_specific_id: str,
) -> None:
    """
    Guard against empty required fields before hitting the DB (Section 5.4).

    Why validate here: a missing canonical_event_id would create a row that
    groups records under an empty key — silently corrupting every query that
    uses it as a join condition. Explicit validation produces a clear error
    at the call site.
    """
    for field, value in (
        ("canonical_event_id",   canonical_event_id),
        ("platform",             platform),
        ("platform_specific_id", platform_specific_id),
    ):
        if not value or not isinstance(value, str):
            raise ValueError(
                f"[mapping_dict.link] '{field}' must be a non-empty string. "
                f"Got: {value!r}"
            )


def _validate_embedding(embedding: Any) -> None:
    """
    Confirm the embedding is a list of _EMBEDDING_DIM floats (Section 5.2).

    Why validate here: inserting a wrong-dimension vector raises a cryptic
    psycopg2 DB error. A clear ValueError here pinpoints the problem.
    """
    if not isinstance(embedding, (list, tuple)):
        raise ValueError(
            f"[mapping_dict] embedding must be a list, got {type(embedding).__name__}"
        )
    if len(embedding) != _EMBEDDING_DIM:
        raise ValueError(
            f"[mapping_dict] embedding dimension mismatch: "
            f"expected {_EMBEDDING_DIM}, got {len(embedding)}"
        )
