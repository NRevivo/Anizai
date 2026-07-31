"""
Metrics repository — `calibration_metrics_snapshots`.

Phase 10A creates the storage boundary; Phase 10C computes what goes into it.
The `payload` shape varies by `metric_type` and is owned by the module that
produces it, not by this one — this repository stores and retrieves opaque
JSONB and deliberately does not validate its interior.

References:
    - calibration_plan.md §4 Table 5
    - calibration_plan.md §8 (Phase 10C, which populates this table)
"""

from __future__ import annotations

import logging
from typing import Optional

from psycopg2.extras import Json

from calibration.db import get_cursor
from calibration.models import MetricsSnapshot

logger = logging.getLogger(__name__)

_COLUMNS = "id::text, snapshot_at, metric_type, cohort, payload"


def insert(snapshot: MetricsSnapshot) -> str:
    """Persist one metric snapshot and return its id."""
    sql = """
        INSERT INTO calibration_metrics_snapshots (metric_type, cohort, payload)
        VALUES (%s, %s, %s)
        RETURNING id::text;
    """
    with get_cursor() as cur:
        cur.execute(sql, (snapshot.metric_type, snapshot.cohort, Json(snapshot.payload)))
        return cur.fetchone()["id"]


def latest(metric_type: str, cohort: Optional[str] = None) -> Optional[MetricsSnapshot]:
    """
    The most recent snapshot of one metric type.

    When `cohort` is None the filter is on `cohort IS NULL` rather than being
    omitted — an aggregate snapshot and a per-cohort snapshot are different
    metrics, and silently returning whichever was written last would mix them.
    """
    sql = f"""
        SELECT {_COLUMNS}
        FROM calibration_metrics_snapshots
        WHERE metric_type = %s
          AND cohort IS NOT DISTINCT FROM %s
        ORDER BY snapshot_at DESC
        LIMIT 1;
    """
    with get_cursor() as cur:
        cur.execute(sql, (metric_type, cohort))
        row = cur.fetchone()
    return MetricsSnapshot.model_validate(dict(row)) if row else None


def list_snapshots(metric_type: Optional[str] = None, limit: int = 100) -> list[MetricsSnapshot]:
    """Snapshots, most recent first, optionally filtered by type."""
    where = "WHERE metric_type = %s" if metric_type else ""
    params: tuple = (metric_type, limit) if metric_type else (limit,)

    with get_cursor() as cur:
        cur.execute(
            f"SELECT {_COLUMNS} FROM calibration_metrics_snapshots {where} "
            "ORDER BY snapshot_at DESC LIMIT %s;",
            params,
        )
        rows = cur.fetchall()
    return [MetricsSnapshot.model_validate(dict(r)) for r in rows]
