"""
Questions repository — `calibration_questions`.

The insert path is `ON CONFLICT (polymarket_condition_id) DO NOTHING`, which
is what makes discovery idempotent: hourly re-discovery inserts only genuinely
new markets and never raises on the ones it has already seen. Callers detect
the no-op by the returned id being None.

References:
    - calibration_plan.md §4 Table 1
    - calibration_plan.md §6 T10A.5
"""

from __future__ import annotations

import logging
from typing import Optional

from calibration.db import get_cursor
from calibration.models import Question

logger = logging.getLogger(__name__)

_COLUMNS = """
    id::text,
    question_text,
    polymarket_slug,
    polymarket_condition_id,
    category,
    cohort,
    expected_resolution_date,
    liquidity_usd_at_pickup,
    status,
    added_by,
    added_by_operator,
    created_at,
    updated_at
"""


def insert(question: Question) -> Optional[str]:
    """
    Insert one question, ignoring duplicates by condition id.

    Returns:
        The new row's id, or None when a row for that condition id already
        existed. None is a normal outcome during re-discovery, not an error.
    """
    sql = f"""
        INSERT INTO calibration_questions (
            question_text, polymarket_slug, polymarket_condition_id,
            category, cohort, expected_resolution_date,
            liquidity_usd_at_pickup, status, added_by, added_by_operator
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (polymarket_condition_id) DO NOTHING
        RETURNING id::text;
    """
    params = (
        question.question_text,
        question.polymarket_slug,
        question.polymarket_condition_id,
        question.category,
        question.cohort,
        question.expected_resolution_date,
        question.liquidity_usd_at_pickup,
        question.status,
        question.added_by,
        question.added_by_operator,
    )
    with get_cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()

    if row is None:
        logger.debug(
            "[questions] condition_id=%s already present — skipped",
            question.polymarket_condition_id,
        )
        return None
    return row["id"]


def get_by_id(question_id: str) -> Optional[Question]:
    """Fetch one question by primary key, or None."""
    with get_cursor() as cur:
        cur.execute(
            f"SELECT {_COLUMNS} FROM calibration_questions WHERE id = %s;",
            (question_id,),
        )
        row = cur.fetchone()
    return Question.model_validate(dict(row)) if row else None


def get_by_condition_id(condition_id: str) -> Optional[Question]:
    """Fetch one question by its Polymarket condition id, or None."""
    with get_cursor() as cur:
        cur.execute(
            f"SELECT {_COLUMNS} FROM calibration_questions "
            "WHERE polymarket_condition_id = %s;",
            (condition_id,),
        )
        row = cur.fetchone()
    return Question.model_validate(dict(row)) if row else None


def list_questions(
    status: Optional[str] = None,
    cohort: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 200,
) -> list[Question]:
    """
    List questions with optional filters, newest first.

    Filters are composed dynamically but every value is still bound as a
    parameter — the SQL text varies only by which fixed predicate strings are
    appended, never by caller-supplied content.
    """
    clauses: list[str] = []
    params: list[object] = []
    for column, value in (("status", status), ("cohort", cohort), ("category", category)):
        if value:
            clauses.append(f"{column} = %s")
            params.append(value)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)

    with get_cursor() as cur:
        cur.execute(
            f"SELECT {_COLUMNS} FROM calibration_questions {where} "
            "ORDER BY created_at DESC LIMIT %s;",
            tuple(params),
        )
        rows = cur.fetchall()
    return [Question.model_validate(dict(r)) for r in rows]


def list_open_due_for_resolution(days_ahead: int = 2) -> list[Question]:
    """
    Open questions whose expected resolution date is within `days_ahead`.

    The resolver's work list. Polling every open question every hour would be
    wasteful; polling only those near their expected date, plus any already
    past it, covers settlement lag without the volume.
    """
    sql = f"""
        SELECT {_COLUMNS}
        FROM calibration_questions
        WHERE status = 'open'
          AND expected_resolution_date <= (CURRENT_DATE + %s::int)
        ORDER BY expected_resolution_date ASC;
    """
    with get_cursor() as cur:
        cur.execute(sql, (days_ahead,))
        rows = cur.fetchall()
    return [Question.model_validate(dict(r)) for r in rows]


def count_open_by_cohort() -> dict[str, int]:
    """
    Open-question counts keyed by cohort.

    Cohorts with no open questions are returned as 0 rather than omitted, so
    discovery's "how many more do I need" arithmetic never has to guard for a
    missing key.
    """
    with get_cursor() as cur:
        cur.execute(
            "SELECT cohort, COUNT(*) AS n FROM calibration_questions "
            "WHERE status = 'open' GROUP BY cohort;"
        )
        rows = cur.fetchall()

    counts = {"7d": 0, "14d": 0, "30-45d": 0}
    for row in rows:
        counts[row["cohort"]] = int(row["n"])
    return counts


def count_by_status() -> dict[str, int]:
    """Question counts keyed by status ('open', 'resolved', 'archived')."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT status, COUNT(*) AS n FROM calibration_questions GROUP BY status;"
        )
        rows = cur.fetchall()

    counts = {"open": 0, "resolved": 0, "archived": 0}
    for row in rows:
        counts[row["status"]] = int(row["n"])
    return counts


def mark_resolved(question_id: str) -> bool:
    """
    Flip a question to `resolved`.

    Only transitions from `open`, so a concurrent second resolver cannot
    re-resolve an already-resolved question. Returns True when this call was
    the one that made the transition.
    """
    with get_cursor() as cur:
        cur.execute(
            "UPDATE calibration_questions "
            "SET status = 'resolved', updated_at = NOW() "
            "WHERE id = %s AND status = 'open' "
            "RETURNING id::text;",
            (question_id,),
        )
        return cur.fetchone() is not None


def archive(question_id: str) -> bool:
    """Archive a question so discovery stops counting it against the target."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE calibration_questions "
            "SET status = 'archived', updated_at = NOW() "
            "WHERE id = %s AND status <> 'archived' "
            "RETURNING id::text;",
            (question_id,),
        )
        return cur.fetchone() is not None
