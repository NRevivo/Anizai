"""
Social Vault — Silver Social Discourse Store (Section 5.1, C.3).

Persists raw Silver social records into the `social_vault` PostgreSQL table.
This is the Silver-layer raw archive — all original comments are stored here
so the Gold Layer Pulse Analyst can drill down to source material when needed
(Section B.8 has_raw_source=True / social_id reference).

Four platform patterns stored in `platform_data` JSONB (Section C.3):
    Reddit:     {post_id, subreddit, post_body, upvote_ratio, full_comment_archive}
    Polymarket: {market_id, question, raw_comments, content_hash, bronze_ref}
    Telegram:   {message_id, message_text, channel_source, extracted_links}
    HackerNews: {story_id, title, url, points, author, published_at, top_comments}

Deduplication strategy: the Silver Job calls exists_by_content_hash() before
calling insert(). The social_vault table has no UNIQUE constraint (it is an
append-only archive), so idempotency is caller-managed via that check.

Public interface:
    insert(silver_social)                           → social_id (str)
    exists_by_content_hash(content_hash)            → bool
    fetch_by_social_id(social_id)                   → dict | None
    fetch_by_canonical_event(id, source, limit)     → list[dict]

References:
    - Section 5.1:  Silver stores — Social Vault architecture
    - Section C.3:  Silver Social Discourse Store — four platform patterns
    - Section 3.2:  DRY — all DB logic in dedicated persistence modules
    - Section 3.3:  Service Isolation — only this module writes to social_vault
    - Section B.8:  Polymarket drill-down — has_raw_source + social_id reference
    - Section 4.1B: Dual-Store Persistence routing
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from psycopg2.extras import Json, RealDictCursor

from utils.db import get_cursor

logger = logging.getLogger(__name__)

# Valid source_name values — enforced by CHECK constraint in init.sql
VALID_SOURCES = frozenset({"reddit", "polymarket", "telegram", "hackernews"})


# ==========================================================
# Insert
# ==========================================================

def insert(silver_social: dict) -> str:
    """
    Insert one Silver social record into `social_vault`.

    The full Silver record is stored as `platform_data` JSONB so all
    platform-specific fields (market_id, raw_comments, content_hash, etc.)
    are preserved and queryable via the GIN index (idx_sv_platform_data).

    Callers should call exists_by_content_hash() before this function to
    avoid re-archiving duplicate comment batches (Section 4.1C).

    Args:
        silver_social: Silver social dict conforming to Section C.3.
                       Required keys: source_name, ingested_at.
                       Platform-specific keys depend on source_name
                       (see module docstring patterns above).

    Returns:
        social_id: UUID string of the inserted row.

    Raises:
        ValueError: If source_name is missing or not in VALID_SOURCES.
        psycopg2.Error: On unexpected database errors.
    """
    _validate_record(silver_social)

    source_name = silver_social["source_name"]

    # canonical_event_id and raw_data_ref both come from bronze_ref, which
    # is the Bronze envelope's event_id. This creates the Bronze→Silver traceability
    # chain required by Section 3.2 (message envelope paper trail).
    # An explicit "canonical_event_id" key in the record overrides the bronze_ref
    # fallback — used when the canonical grouping key differs from the raw ingestion UUID.
    bronze_ref = silver_social.get("bronze_ref") or silver_social.get("event_id")
    canonical_event_id = silver_social.get("canonical_event_id") or bronze_ref

    # Derive raw_data_ref UUID — must be a valid UUID string
    raw_data_ref: Optional[str] = None
    if bronze_ref:
        try:
            uuid.UUID(bronze_ref)  # validate it's a UUID
            raw_data_ref = bronze_ref
        except ValueError:
            logger.warning(
                "[social_vault] bronze_ref is not a valid UUID: %r — raw_data_ref=NULL",
                bronze_ref,
            )

    sql = """
        INSERT INTO social_vault (
            canonical_event_id,
            raw_data_ref,
            source_name,
            platform_data,
            ingested_at
        ) VALUES (%s, %s, %s, %s, %s)
        RETURNING social_id::text;
    """
    params = (
        canonical_event_id,
        raw_data_ref,                               # UUID or NULL
        source_name,
        Json(silver_social),                        # full Silver record as platform_data
        datetime.now(timezone.utc),
    )

    with get_cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()

    social_id = row["social_id"]
    logger.debug("[social_vault] Inserted social_id=%s source=%s", social_id, source_name)
    return social_id


# ==========================================================
# Deduplication check
# ==========================================================

def exists_by_content_hash(content_hash: str) -> bool:
    """
    Check whether a Silver social record with this content_hash already exists.

    Called by the Silver Job (or Flink Silver ProcessFunction) before archiving
    a Polymarket comment batch to skip re-ingestion of batches already stored
    (Section 4.1C SHA-256 deduplication).

    Queries via the GIN index on platform_data using the @> (contains) operator,
    which is supported by jsonb_ops GIN indexes (idx_sv_platform_data):
        WHERE platform_data @> '{"content_hash": "..."}'

    Note: Telegram, Reddit, and HackerNews use document_hash in knowledge_vault
    (via persistence/knowledge_vault.py), not this function.

    Args:
        content_hash: 64-char SHA-256 hex digest from hash_social_batch().

    Returns:
        True if at least one row with this content_hash exists.
    """
    sql = """
        SELECT 1 FROM social_vault
        WHERE platform_data @> jsonb_build_object('content_hash', %s::text)
        LIMIT 1;
    """
    with get_cursor() as cur:
        cur.execute(sql, (content_hash,))
        return cur.fetchone() is not None


# ==========================================================
# Fetch by Primary Key
# ==========================================================

def fetch_by_social_id(social_id: str) -> Optional[dict]:
    """
    Retrieve one social_vault row by its primary key.

    Used by:
        - The Pulse Analyst agent: drill-down from a Gold Social Pulse record
          (via social_vectors.platform_logic.has_raw_source=True + social_id
          stored as silver_data_ref) to inspect the original comment texts.
        - Gate 3 tests: verifies the row written by insert() is retrievable.

    Args:
        social_id: UUID string (primary key of social_vault).

    Returns:
        Row as a RealDictCursor dict, or None if not found.
    """
    sql = """
        SELECT
            social_id::text,
            canonical_event_id,
            raw_data_ref::text,
            source_name,
            platform_data,
            ingested_at
        FROM social_vault
        WHERE social_id = %s::uuid;
    """
    with get_cursor() as cur:
        cur.execute(sql, (social_id,))
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
    Retrieve social_vault rows for a canonical event.

    Used by the Gold Job when building Consensus Bundles: the Job needs
    all comment batches for a market_id grouped before passing to GPT-4o
    (Section 4.2A temporal grouping).

    Args:
        canonical_event_id: The shared event key (Bronze envelope event_id).
        source_name:        Optional platform filter ('polymarket', etc.).
        limit:              Maximum rows returned (default 100).

    Returns:
        List of row dicts, ordered by ingested_at DESC.
    """
    if source_name:
        sql = """
            SELECT
                social_id::text,
                canonical_event_id,
                source_name,
                platform_data,
                ingested_at
            FROM social_vault
            WHERE canonical_event_id = %s
              AND source_name = %s
            ORDER BY ingested_at DESC
            LIMIT %s;
        """
        params: tuple = (canonical_event_id, source_name, limit)
    else:
        sql = """
            SELECT
                social_id::text,
                canonical_event_id,
                source_name,
                platform_data,
                ingested_at
            FROM social_vault
            WHERE canonical_event_id = %s
            ORDER BY ingested_at DESC
            LIMIT %s;
        """
        params = (canonical_event_id, limit)

    with get_cursor() as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


# ==========================================================
# Internal helpers
# ==========================================================

def _validate_record(silver_social: dict) -> None:
    """
    Lightweight pre-insert structural check (last-resort guard, not full validation).

    Full Silver schema validation happens in silver_job.py before this module
    is called. This guard catches programming errors (wrong dict passed) with
    a clearer message than a psycopg2 constraint error.
    """
    if not isinstance(silver_social, dict):
        raise ValueError(
            "[social_vault.insert] Record must be a dict, "
            f"got {type(silver_social).__name__}."
        )
    source = silver_social.get("source_name", "")
    if not source:
        raise ValueError("[social_vault.insert] 'source_name' is required.")
    if source not in VALID_SOURCES:
        raise ValueError(
            f"[social_vault.insert] source_name '{source}' is not valid. "
            f"Expected one of: {sorted(VALID_SOURCES)}"
        )
