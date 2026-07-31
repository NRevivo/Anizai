"""
Runs repository — `calibration_runs`.

The operational audit trail: what fired, when, how much it dispatched, and how
it ended. Phase 10A uses it for discovery and resolution runs; Phase 10B adds
dispatch runs.

References:
    - calibration_plan.md §4 Table 4
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from psycopg2.extras import Json

from calibration.db import get_cursor
from calibration.models import Run

logger = logging.getLogger(__name__)

_COLUMNS = """
    id::text,
    run_type,
    triggered_at,
    triggered_by,
    questions_dispatched,
    forecasts_completed,
    forecasts_failed,
    finished_at,
    run_metadata
"""


def start(run_type: str, triggered_by: str, metadata: Optional[dict] = None) -> str:
    """
    Open a run row and return its id.

    Opened before the work begins, not after, so a run that crashes mid-way
    still leaves a record with a NULL `finished_at` — visible as "started and
    never finished", which is exactly what an operator needs to see. A row
    written only on success would make a crashed run indistinguishable from a
    run that never fired.
    """
    sql = """
        INSERT INTO calibration_runs (run_type, triggered_by, run_metadata)
        VALUES (%s, %s, %s)
        RETURNING id::text;
    """
    with get_cursor() as cur:
        cur.execute(sql, (run_type, triggered_by, Json(metadata) if metadata else None))
        run_id = cur.fetchone()["id"]
    logger.info("[runs] Started run_type=%s id=%s by=%s", run_type, run_id, triggered_by)
    return run_id


def finish(
    run_id: str,
    questions_dispatched: Optional[int] = None,
    forecasts_completed: Optional[int] = None,
    forecasts_failed: Optional[int] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """
    Close a run, recording its counts.

    `metadata` is merged into whatever the run was opened with rather than
    replacing it, so a caller closing the run does not have to know what the
    caller that opened it recorded.
    """
    sql = """
        UPDATE calibration_runs
        SET finished_at          = NOW(),
            questions_dispatched = COALESCE(%s, questions_dispatched),
            forecasts_completed  = COALESCE(%s, forecasts_completed),
            forecasts_failed     = COALESCE(%s, forecasts_failed),
            run_metadata         = COALESCE(run_metadata, '{}'::jsonb) || COALESCE(%s, '{}'::jsonb)
        WHERE id = %s;
    """
    with get_cursor() as cur:
        cur.execute(
            sql,
            (
                questions_dispatched,
                forecasts_completed,
                forecasts_failed,
                Json(metadata) if metadata else None,
                run_id,
            ),
        )
    logger.info("[runs] Finished run id=%s", run_id)


def get_by_id(run_id: str) -> Optional[Run]:
    """Fetch one run, or None."""
    with get_cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM calibration_runs WHERE id = %s;", (run_id,))
        row = cur.fetchone()
    return Run.model_validate(dict(row)) if row else None


def list_runs(limit: int = 50) -> list[Run]:
    """Runs, most recent first."""
    with get_cursor() as cur:
        cur.execute(
            f"SELECT {_COLUMNS} FROM calibration_runs "
            "ORDER BY triggered_at DESC LIMIT %s;",
            (limit,),
        )
        rows = cur.fetchall()
    return [Run.model_validate(dict(r)) for r in rows]
