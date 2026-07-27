"""
Filter Rejects — reject retention for filter calibration (Phase 7B.5-I §2.1).

Persists articles dropped by the two-stage filter (failed keyword sniper AND
failed semantic rescue) into the `filter_rejects` PostgreSQL table, so Phase
7B.5 can classify false negatives (T7B.1) and sweep thresholds against real
rejected content (T7B.9). Before this module existed the drop branch was a
bare `return` — the reject corpus 7B.5 needs was never persisted (§0).

Capture is flag-gated by the CALLER (REJECT_CAPTURE_ENABLED, read in
GlobalNewsGoldFunction.open()) — this module does not read the flag, it only
writes. Cost instrumentation is permanent; reject retention is not (D3).

Error contract — FAIL-OPEN (plan T3a): `insert_reject()` catches ALL
exceptions, logs a warning, and returns None. A capture failure must not
turn a clean filter drop into a DLQ event — the reject is a VALID low-signal
article, not an error, and the drop decision already happened. This differs
from `llm_cost_events.insert_event()` (which raises, with fail-open at
record_usage) because insert_reject is called directly from the Flink drop
branch with no intermediate policy layer.

Public interface:
    insert_reject(silver_doc, rescue_cosine, run_id=None, canonical_event_id=None)
                                                          → reject_id (str) | None
    fetch_rejects(run_id=None, limit=100)                 → list[dict]

References:
    - docs/A_pipeline/plans/phase7b5i_filter_observability_and_cost.md §2.1, §1 D3/D5
    - Section 3.3 (Service Isolation — only this module writes filter_rejects)
    - processing/gold_job.py GlobalNewsGoldFunction (the single call site)
"""

from __future__ import annotations

import logging
from typing import Optional

from psycopg2.extras import Json

from utils.db import get_cursor
from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)
setup_logging()


def insert_reject(
    silver_doc: dict,
    rescue_cosine: float,
    run_id: Optional[str] = None,
    canonical_event_id: Optional[str] = None,
) -> Optional[str]:
    """
    Persist one filter-rejected article. FAIL-OPEN — never raises.

    Fields are pulled from the Silver document that just failed both filter
    stages. full_text_raw is stored FULL (untruncated): T7B.1's manual FN
    classification requires reading the article; volume is bounded because
    capture is flag-gated (§2.1).

    No reject_stage argument (decision D5): there is exactly one clean-reject
    path — a sniper failure always triggers a rescue attempt first, so every
    row here failed both stages by construction.

    Args:
        silver_doc:    The Silver document that failed both stages. For the social
                       path (HackerNews) the caller normalises the record into this
                       news-doc shape first (Phase 7D, social_reject_doc / D5-O2).
        rescue_cosine: Similarity returned by compute_semantic_rescue() / the social
                       reject cosine (< threshold; 0.0 for the empty-text edge).
        run_id:        Collection-run tag; empty/None → stored as SQL NULL.
        canonical_event_id: Instance key (Phase 7D, T7) — _cost_trace_id() of the
                       rejected object, resolved at the caller (canonical_event_id
                       or bronze_ref). Joins the reject back to its llm_cost_events
                       / vault rows. Empty/None → stored as SQL NULL.

    Returns:
        reject_id (UUID string) on success, None on any failure (after a
        warning log — the caller's drop proceeds unchanged either way).
    """
    sql = """
        INSERT INTO filter_rejects (
            run_id,
            canonical_event_id,
            source_name,
            original_url,
            title,
            inverted_pyramid_lead,
            full_text_raw,
            relevance_score,
            sniper_keywords,
            rescue_cosine
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        RETURNING reject_id::text;
    """
    try:
        params = (
            run_id or None,              # "" → NULL
            canonical_event_id or None,  # "" → NULL (Phase 7D, T7 — instance key)
            silver_doc.get("source_name", "unknown"),
            silver_doc.get("original_url", ""),
            silver_doc.get("title", ""),
            silver_doc.get("inverted_pyramid_lead", ""),
            silver_doc.get("full_text_raw", ""),
            float(silver_doc.get("relevance_score", 0.0)),
            Json(silver_doc.get("sniper_keywords", [])),
            float(rescue_cosine),
        )
        with get_cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        reject_id = row["reject_id"]
        logger.debug(
            "[filter_rejects] captured reject_id=%s source=%s url=%s cosine=%.4f",
            reject_id,
            params[2],   # source_name (index shifted by canonical_event_id at [1])
            (params[3] or "")[:80],  # original_url
            rescue_cosine,
        )
        return reject_id
    except Exception as exc:
        logger.warning(
            "[filter_rejects] capture INSERT failed (fail-open — drop proceeds) "
            "url=%s: %s",
            (silver_doc.get("original_url", "") or "")[:80], exc,
        )
        return None


def fetch_rejects(run_id: Optional[str] = None, limit: int = 100) -> list[dict]:
    """
    Fetch recent reject rows, optionally filtered by run_id.

    Used by Gate 3 round-trip tests and the T8 day-run live-verification
    step ("a handful of reject rows within the first hour"). Ordered
    newest-first.
    """
    if run_id is not None:
        sql = """
            SELECT
                reject_id::text,
                run_id,
                canonical_event_id,
                source_name,
                original_url,
                title,
                inverted_pyramid_lead,
                full_text_raw,
                relevance_score,
                sniper_keywords,
                rescue_cosine,
                rejected_at
            FROM filter_rejects
            WHERE run_id = %s
            ORDER BY rejected_at DESC
            LIMIT %s;
        """
        params: tuple = (run_id, limit)
    else:
        sql = """
            SELECT
                reject_id::text,
                run_id,
                canonical_event_id,
                source_name,
                original_url,
                title,
                inverted_pyramid_lead,
                full_text_raw,
                relevance_score,
                sniper_keywords,
                rescue_cosine,
                rejected_at
            FROM filter_rejects
            ORDER BY rejected_at DESC
            LIMIT %s;
        """
        params = (limit,)

    with get_cursor() as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
