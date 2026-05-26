"""
Reactive Triggers Log — Agent Reactive-Ingestion Audit (Sprint 23).

Records every reactive-ingestion trigger the Agentic Hub emits to the
`ingestion_triggers` Kafka topic when the vault is insufficient for a
forecast (Phase 8, Sprint 23). One row per emit attempt.

Two-row purpose (per `agentic_hub_implementation_phase8_revised.md` §Sprint 23):
    1. Debugging during initial test — confirm a trigger fired and with what
       payload when a session looks under-evidenced.
    2. Cost attribution — basis for "how many reactive triggers emitted this
       week" measurements feeding KG-PHASE-9.5-9 cost analysis.

Trigger-and-forget semantics: there is no polling-state column. If the
agent ever needs to wait for results (Future Enhancement 1 Option B in the
revised plan), polling state belongs in a new column added in that future
sprint, not retrofitted here.

Public interface:
    insert(session_id, source, keywords, kafka_offset, status) → trigger_id
    list_by_session(session_id)                                → list[dict] (newest first)

Service Isolation (CLAUDE.md §3.3):
    Only this module writes to or reads from `reactive_triggers_log`.
    Agent nodes and tests import from here; they never craft raw SQL.

References:
    - data-pipeline/docs/agentic_hub_implementation_phase8_revised.md §Sprint 23
    - data-pipeline/infrastructure/sql/init.sql §7 (table definition)
    - CLAUDE.md §3.2 (DRY — DB logic centralized) / §3.3 (Service Isolation)
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from psycopg2.extras import Json

from utils.db import get_cursor
from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)
setup_logging()


# ==========================================================
# Client-side validation constants
# ==========================================================
# Mirrors the DB CHECK on status. Validating client-side raises a clean
# ValueError before the SQL roundtrip — better than letting psycopg2 surface
# a CheckViolation. The DB is still the source of truth; this is a guard.
_VALID_STATUSES: frozenset[str] = frozenset({"emitted", "failed"})


# ==========================================================
# Insert
# ==========================================================

def insert(
    session_id: str,
    source: str,
    keywords: list[str],
    kafka_offset: Optional[int],
    status: str,
) -> str:
    """
    Insert one reactive-trigger audit row into `reactive_triggers_log`.

    Generates a fresh UUID4 trigger_id Python-side (same pattern as
    momentum_vault.insert) and uses RETURNING to confirm the row was written.

    Args:
        session_id:   Owning session UUID (the Firestore forecastQueries doc id).
                      Required.
        source:       Producer source name (V1: 'newsapi'; future: 'telegram' /
                      'arxiv' / 'polymarket'). Required; not validated against an
                      enum here because the consumer's VALID_SOURCES is the
                      gate — this module stays agnostic to source membership.
        keywords:     List of keyword strings sent in the Kafka trigger payload.
                      Stored as JSONB; allowed to be empty (the consumer
                      validates non-emptiness for live triggers — failed-emit
                      audits may want to record an empty keywords list).
        kafka_offset: Offset returned by KafkaProducer.send().get().offset on
                      success; None on emit failure or when the mock producer
                      doesn't expose offset (Gate 1 tests).
        status:       'emitted' | 'failed' — must be one of `_VALID_STATUSES`.

    Returns:
        trigger_id: UUID4 string of the inserted row.

    Raises:
        ValueError: If `session_id` or `source` is empty, `keywords` is not a
                    list, or `status` is not in `_VALID_STATUSES`.
        psycopg2.Error: On unexpected database errors.
    """
    if not session_id:
        raise ValueError("session_id must be a non-empty string")
    if not source:
        raise ValueError("source must be a non-empty string")
    if not isinstance(keywords, list):
        raise ValueError(
            f"keywords must be a list, got {type(keywords).__name__}"
        )
    if status not in _VALID_STATUSES:
        raise ValueError(
            f"status must be one of {sorted(_VALID_STATUSES)}, got {status!r}"
        )

    trigger_id = str(uuid.uuid4())

    sql = """
        INSERT INTO reactive_triggers_log (
            trigger_id,
            session_id,
            keywords,
            source,
            kafka_offset,
            status
        ) VALUES (
            %s, %s, %s, %s, %s, %s
        )
        RETURNING trigger_id::text;
    """
    params = (
        trigger_id,
        session_id,
        Json(keywords),
        source,
        kafka_offset,
        status,
    )

    with get_cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()

    if row is None:
        # Should not happen — there's no ON CONFLICT clause and the PK is a
        # freshly generated UUID. Defensive log so a silent insert failure
        # never goes unnoticed during initial test debugging.
        logger.error(
            "[reactive_triggers_log] INSERT returned no row — "
            "session_id=%s source=%s status=%s",
            session_id, source, status,
        )
    else:
        logger.debug(
            "[reactive_triggers_log] Inserted trigger_id=%s session_id=%s "
            "source=%s status=%s",
            trigger_id, session_id, source, status,
        )

    return trigger_id


# ==========================================================
# Fetch by session
# ==========================================================

def list_by_session(session_id: str) -> list[dict]:
    """
    Return every reactive-trigger row for one session, newest first.

    Uses `idx_rtl_session_time (session_id, trigger_time DESC)` for an
    index-only seek. Empty list when no triggers were emitted for the
    session — does not return None.

    Args:
        session_id: Owning session UUID. Required.

    Returns:
        List of row dicts (RealDictCursor format), ordered trigger_time DESC.
        Each row has: trigger_id, session_id, trigger_time, keywords (list),
        source, kafka_offset, status.

    Raises:
        ValueError: If `session_id` is empty.
    """
    if not session_id:
        raise ValueError("session_id must be a non-empty string")

    sql = """
        SELECT
            trigger_id::text,
            session_id,
            trigger_time,
            keywords,
            source,
            kafka_offset,
            status
        FROM reactive_triggers_log
        WHERE session_id = %s
        ORDER BY trigger_time DESC;
    """
    with get_cursor() as cur:
        cur.execute(sql, (session_id,))
        rows = cur.fetchall()
    return [dict(r) for r in rows]
