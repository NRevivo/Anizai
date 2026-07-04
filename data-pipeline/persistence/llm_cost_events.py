"""
LLM Cost Events — per-call OpenAI cost event store (Phase 7B.5-I, §2.3).

Persists one row per runtime OpenAI API call into the `llm_cost_events`
PostgreSQL table. The two derived ROLLUP views (`llm_cost_daily_summary`,
`llm_cost_run_summary`) aggregate these rows into per-day / per-run,
per-source, per-usage-type cost breakdowns — the views are derived, never a
second write path, so macro and micro totals cannot disagree (§2.3).

Why a Postgres event table and not an in-memory accumulator (decision D2):
Flink has no per-request shared state to accumulate into (unlike the agent's
LangGraph `state`), so per-event rows + SQL aggregation replace the agent's
accumulator pattern.

Error contract: `insert_event()` RAISES on DB failure. The fail-open policy
(a cost-tracking failure must never fail message processing, §2.4) lives in
the single caller, `utils/llm_cost.record_usage()` — keeping this module an
honest persistence layer while the policy stays in one place.

Public interface:
    insert_event(...)                 → event_id (str)
    fetch_events(run_id=None, limit)  → list[dict]   (Gate 3 round-trips + run monitoring)

References:
    - docs/A_pipeline/plans/phase7b5i_filter_observability_and_cost.md §2.3, §2.4 (D2)
    - Section 3.2 (DRY — connections via utils/db only)
    - Section 3.3 (Service Isolation — only this module writes llm_cost_events)
"""

from __future__ import annotations

import logging
from typing import Optional

from utils.db import get_cursor
from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)
setup_logging()


def insert_event(
    *,
    site: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    cost_usd: float,
    source_name: Optional[str] = None,
    trace_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> str:
    """
    Insert one per-call cost event and return its event_id.

    `site` is a stable tag from the §2.5 registry (gold_enrich /
    gold_consensus / translate / gold_embed / rescue_embed) — the log line
    and this table share the same names so the per-call audit trail and the
    SQL rollups are joinable on vocabulary.

    Why `trace_id` (= canonical_event_id): per-article unit economics —
    joining `rescue_embed` events to `filter_rejects` rows quantifies the
    spend on articles that were ultimately dropped (§0 goal 2).

    Empty-string `run_id`/`source_name`/`trace_id` are stored as NULL so the
    ROLLUP views' COALESCE(…, 'ALL') stays unambiguous.

    Raises:
        psycopg2.Error: On DB failure — the caller (record_usage) owns the
        fail-open policy (§2.4).
    """
    sql = """
        INSERT INTO llm_cost_events (
            run_id,
            site,
            source_name,
            model,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            cost_usd,
            trace_id
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        RETURNING event_id::text;
    """
    params = (
        run_id or None,        # "" → NULL
        site,
        source_name or None,
        model,
        int(prompt_tokens),
        int(completion_tokens),
        int(total_tokens),
        float(cost_usd),
        trace_id or None,
    )
    with get_cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return row["event_id"]


def fetch_events(run_id: Optional[str] = None, limit: int = 100) -> list[dict]:
    """
    Fetch recent cost events, optionally filtered by run_id.

    Used by Gate 3 round-trip tests (insert → read back → field equality)
    and by the T8 day-run live-verification step ("cost rows within the
    first hour"). Ordered newest-first.
    """
    if run_id is not None:
        sql = """
            SELECT
                event_id::text,
                run_id,
                site,
                source_name,
                model,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                cost_usd,
                trace_id,
                created_at
            FROM llm_cost_events
            WHERE run_id = %s
            ORDER BY created_at DESC
            LIMIT %s;
        """
        params: tuple = (run_id, limit)
    else:
        sql = """
            SELECT
                event_id::text,
                run_id,
                site,
                source_name,
                model,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                cost_usd,
                trace_id,
                created_at
            FROM llm_cost_events
            ORDER BY created_at DESC
            LIMIT %s;
        """
        params = (limit,)

    with get_cursor() as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
