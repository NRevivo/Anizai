"""
Forecasts repository — `calibration_forecasts`.

Phase 10A creates this module so the schema has a complete typed boundary and
so `count_by_status` can back the CLI summary. It is exercised by tests only —
nothing in Phase 10A dispatches a forecast, because Phase 10A has no Firestore
surface at all. Dispatch and harvest arrive in Phase 10B.

Two invariants are enforced by the table and worth restating here because they
are the ones a caller can violate:

    UNIQUE (question_id, forecast_run_index)
        One forecast per question per run index. This is what makes the
        improvement loop meaningful — index 0 is always the original forecast
        and the highest index is always the latest.

    UNIQUE (idempotency_key)
        Defence in depth against a dispatch retry writing two rows for one
        Firestore session.

References:
    - calibration_plan.md §4 Table 2
    - calibration_plan.md §7 (dispatch and harvest, Phase 10B)
"""

from __future__ import annotations

import logging
from typing import Optional

from psycopg2.extras import Json

from calibration.db import get_cursor
from calibration.models import Forecast

logger = logging.getLogger(__name__)

_COLUMNS = """
    id::text,
    question_id::text,
    run_id::text,
    forecast_run_index,
    session_id,
    query_doc_id,
    idempotency_key,
    agent_version,
    final_probability,
    confidence,
    tier,
    status,
    error_message,
    agent_evidence_summary,
    brier_score,
    forecast_dispatched_at,
    forecast_completed_at
"""


def insert(forecast: Forecast) -> str:
    """
    Insert a forecast row.

    Deliberately has no ON CONFLICT clause: unlike questions and resolutions,
    a duplicate forecast is not a benign re-run, it is a bug in dispatch. The
    unique-violation should surface loudly rather than be silently swallowed.
    """
    sql = """
        INSERT INTO calibration_forecasts (
            question_id, run_id, forecast_run_index, session_id, query_doc_id,
            idempotency_key, agent_version, final_probability, confidence,
            tier, status, error_message, agent_evidence_summary
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id::text;
    """
    params = (
        forecast.question_id,
        forecast.run_id,
        forecast.forecast_run_index,
        forecast.session_id,
        forecast.query_doc_id,
        forecast.idempotency_key,
        forecast.agent_version,
        forecast.final_probability,
        forecast.confidence,
        forecast.tier,
        forecast.status,
        forecast.error_message,
        Json(forecast.agent_evidence_summary) if forecast.agent_evidence_summary else None,
    )
    with get_cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()["id"]


def get_by_id(forecast_id: str) -> Optional[Forecast]:
    """Fetch one forecast, or None."""
    with get_cursor() as cur:
        cur.execute(
            f"SELECT {_COLUMNS} FROM calibration_forecasts WHERE id = %s;",
            (forecast_id,),
        )
        row = cur.fetchone()
    return Forecast.model_validate(dict(row)) if row else None


def list_by_question(question_id: str) -> list[Forecast]:
    """Every forecast for one question, ordered by run index ascending."""
    with get_cursor() as cur:
        cur.execute(
            f"SELECT {_COLUMNS} FROM calibration_forecasts "
            "WHERE question_id = %s ORDER BY forecast_run_index ASC;",
            (question_id,),
        )
        rows = cur.fetchall()
    return [Forecast.model_validate(dict(r)) for r in rows]


def list_pending() -> list[Forecast]:
    """
    Forecasts awaiting a result — the harvester's work list (Phase 10B).

    Uses the partial index on status. `needs_clarification` is terminal and is
    correctly absent from this list.
    """
    with get_cursor() as cur:
        cur.execute(
            f"SELECT {_COLUMNS} FROM calibration_forecasts "
            "WHERE status = 'dispatched' ORDER BY forecast_dispatched_at ASC;"
        )
        rows = cur.fetchall()
    return [Forecast.model_validate(dict(r)) for r in rows]


def next_run_index(question_id: str) -> int:
    """
    The run index a new forecast for this question should take.

    0 for a question that has never been forecast, else max+1. Computed rather
    than counted so that a deleted row does not cause an index collision.
    """
    with get_cursor() as cur:
        cur.execute(
            "SELECT COALESCE(MAX(forecast_run_index) + 1, 0) AS next "
            "FROM calibration_forecasts WHERE question_id = %s;",
            (question_id,),
        )
        return int(cur.fetchone()["next"])


def mark_completed(
    forecast_id: str,
    final_probability: Optional[float],
    confidence: Optional[float],
    tier: Optional[str],
    agent_version: Optional[str],
    agent_evidence_summary: Optional[dict],
) -> bool:
    """
    Populate a forecast from a finished session and mark it `completed`.

    Guarded on `status = 'dispatched'` so a concurrent second harvester cannot
    overwrite a row that has already reached a terminal state. Returns True
    when this call made the transition.
    """
    sql = """
        UPDATE calibration_forecasts
        SET status                 = 'completed',
            final_probability      = %s,
            confidence             = %s,
            tier                   = %s,
            agent_version          = %s,
            agent_evidence_summary = %s,
            forecast_completed_at  = NOW()
        WHERE id = %s AND status = 'dispatched'
        RETURNING id::text;
    """
    with get_cursor() as cur:
        cur.execute(
            sql,
            (
                final_probability,
                confidence,
                tier,
                agent_version,
                Json(agent_evidence_summary) if agent_evidence_summary else None,
                forecast_id,
            ),
        )
        return cur.fetchone() is not None


def mark_terminal(
    forecast_id: str, status: str, error_message: Optional[str] = None
) -> bool:
    """
    Move a forecast to a non-completed terminal state.

    Used for `failed`, `timed_out`, and `needs_clarification`. The last of
    these is not a failure and carries no error message — see the harvest
    service for why it is terminal rather than a retry.

    Guarded on `status = 'dispatched'` for the same reason as `mark_completed`.
    """
    if status not in {"failed", "timed_out", "needs_clarification"}:
        raise ValueError(
            f"mark_terminal handles failed/timed_out/needs_clarification, got {status!r}. "
            "Use mark_completed for a successful harvest."
        )

    sql = """
        UPDATE calibration_forecasts
        SET status                = %s,
            error_message         = %s,
            forecast_completed_at = NOW()
        WHERE id = %s AND status = 'dispatched'
        RETURNING id::text;
    """
    with get_cursor() as cur:
        cur.execute(sql, (status, error_message, forecast_id))
        return cur.fetchone() is not None


def list_scorable() -> list[Forecast]:
    """
    Every forecast eligible to contribute to a metric (plan §8 inclusion rule).

    `completed`, with a probability, on a question that resolved
    non-ambiguously. Stated once here as a join so that no metric module has
    to restate it — a second copy of this predicate would eventually disagree
    with the first and the two would produce different Brier scores from the
    same data.
    """
    # Columns must be table-qualified here: the join puts `id` and
    # `question_id` in scope twice, and the unqualified `_COLUMNS` list used
    # by the single-table queries raises AmbiguousColumn.
    sql = """
        SELECT
            f.id::text,
            f.question_id::text,
            f.run_id::text,
            f.forecast_run_index,
            f.session_id,
            f.query_doc_id,
            f.idempotency_key,
            f.agent_version,
            f.final_probability,
            f.confidence,
            f.tier,
            f.status,
            f.error_message,
            f.agent_evidence_summary,
            f.brier_score,
            f.forecast_dispatched_at,
            f.forecast_completed_at
        FROM calibration_forecasts f
        JOIN calibration_resolutions r ON r.question_id = f.question_id
        WHERE f.status = 'completed'
          AND f.final_probability IS NOT NULL
          AND r.outcome <> 'AMBIGUOUS'
        ORDER BY f.question_id, f.forecast_run_index;
    """
    with get_cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    return [Forecast.model_validate(dict(r)) for r in rows]


def count_dispatched_since(hours: int = 24) -> int:
    """
    How many forecasts were dispatched in the last `hours`.

    Backs the rolling daily ceiling. Counts by `forecast_dispatched_at`
    regardless of what happened afterwards — a forecast that later failed
    still consumed a request against the shared OpenAI quota, and the quota is
    what the ceiling exists to protect.
    """
    with get_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM calibration_forecasts "
            "WHERE forecast_dispatched_at > NOW() - make_interval(hours => %s);",
            (hours,),
        )
        return int(cur.fetchone()["n"])


def count_by_status() -> dict[str, int]:
    """
    Forecast counts keyed by status.

    Every status is present in the result, including the zero ones. The
    failure statuses are as load-bearing as `completed` here: the Overview
    screen exists to answer "is anything stuck", and a counter that omits
    empty categories cannot answer it.
    """
    with get_cursor() as cur:
        cur.execute(
            "SELECT status, COUNT(*) AS n FROM calibration_forecasts GROUP BY status;"
        )
        rows = cur.fetchall()

    counts = {
        "dispatched": 0,
        "completed": 0,
        "failed": 0,
        "timed_out": 0,
        "needs_clarification": 0,
    }
    for row in rows:
        counts[row["status"]] = int(row["n"])
    return counts
