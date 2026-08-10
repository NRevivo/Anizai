"""
Resolutions repository — `calibration_resolutions`.

One row per question, ever, enforced by UNIQUE (question_id). The insert is
`ON CONFLICT DO NOTHING`, which makes the hourly resolver idempotent: seeing
the same settled market every hour for a week inserts once and no-ops six
times, with no branching in the caller.

References:
    - calibration_plan.md §4 Table 3
    - calibration_plan.md §3 D3 (idempotent detection)
"""

from __future__ import annotations

import logging
from typing import Optional

from psycopg2.extras import Json

from calibration.db import get_cursor
from calibration.models import Resolution

logger = logging.getLogger(__name__)

_COLUMNS = """
    id::text,
    question_id::text,
    resolved_at,
    detected_at,
    outcome,
    outcome_numeric,
    resolution_source,
    raw_resolution_data
"""


def insert(resolution: Resolution) -> Optional[str]:
    """
    Record ground truth for one question.

    Returns:
        The new row's id, or None when a resolution for that question already
        existed. None is the expected outcome on every poll after the first.
    """
    sql = """
        INSERT INTO calibration_resolutions (
            question_id, resolved_at, outcome, outcome_numeric,
            resolution_source, raw_resolution_data
        ) VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (question_id) DO NOTHING
        RETURNING id::text;
    """
    params = (
        resolution.question_id,
        resolution.resolved_at,
        resolution.outcome,
        resolution.outcome_numeric,
        resolution.resolution_source,
        Json(resolution.raw_resolution_data),
    )
    with get_cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()

    if row is None:
        logger.debug(
            "[resolutions] question_id=%s already resolved — skipped",
            resolution.question_id,
        )
        return None
    return row["id"]


def get_by_question(question_id: str) -> Optional[Resolution]:
    """Fetch the resolution for one question, or None if unresolved."""
    with get_cursor() as cur:
        cur.execute(
            f"SELECT {_COLUMNS} FROM calibration_resolutions WHERE question_id = %s;",
            (question_id,),
        )
        row = cur.fetchone()
    return Resolution.model_validate(dict(row)) if row else None


def list_resolutions(limit: int = 200) -> list[Resolution]:
    """All resolutions, most recently detected first."""
    with get_cursor() as cur:
        cur.execute(
            f"SELECT {_COLUMNS} FROM calibration_resolutions "
            "ORDER BY detected_at DESC LIMIT %s;",
            (limit,),
        )
        rows = cur.fetchall()
    return [Resolution.model_validate(dict(r)) for r in rows]


def count_by_outcome() -> dict[str, int]:
    """
    Resolution counts keyed by outcome.

    AMBIGUOUS is reported alongside YES and NO rather than filtered out. It is
    excluded from scoring, but a rising ambiguous count is a real signal about
    question selection quality and must stay visible.
    """
    with get_cursor() as cur:
        cur.execute(
            "SELECT outcome, COUNT(*) AS n FROM calibration_resolutions GROUP BY outcome;"
        )
        rows = cur.fetchall()

    counts = {"YES": 0, "NO": 0, "AMBIGUOUS": 0}
    for row in rows:
        counts[row["outcome"]] = int(row["n"])
    return counts
