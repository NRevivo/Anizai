"""
Brier score — the primary calibration metric.

    brier = (forecast_probability - actual_outcome)^2      YES=1, NO=0

Lower is better. 0.0 is a perfect confident call; 1.0 is a confidently wrong
one; 0.25 is what you get by always saying 50%.

Why this metric and not accuracy: accuracy throws away the probability. An
agent that says 51% and one that says 99% are identical under accuracy, and
completely different under Brier. Since the whole product is a probability,
the metric has to be one that punishes overconfidence.

The one thing this module refuses to do is score a forecast it should not.
`compute` takes an outcome that is already 0.0 or 1.0; an AMBIGUOUS
resolution has no numeric outcome and never reaches here. That boundary is
enforced by `repos.forecasts.list_scorable` and re-asserted by the type
signature rather than by a runtime check, because the failure mode — scoring
an ambiguous market as though it resolved NO — produces a plausible number
rather than an error.

References:
    - calibration_plan.md §3 E1
    - calibration_plan.md §8 (inclusion rule)
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Iterable, Optional, Sequence

logger = logging.getLogger(__name__)

# Brier score of always predicting 50%. The bar any forecaster must clear to
# be worth running at all; surfaced alongside every aggregate so a score is
# never reported without its reference point.
UNINFORMED_BASELINE = 0.25


def compute(probability: float, outcome_numeric: float) -> float:
    """
    Brier score for one forecast.

    Args:
        probability:     the agent's forecast, in [0, 1].
        outcome_numeric: ground truth, exactly 0.0 or 1.0.

    Raises:
        ValueError: if either argument is outside its domain. This is a hard
            error rather than a clamp: a probability outside [0,1] means the
            extraction upstream is wrong, and silently clamping it would
            produce a real-looking score from broken input.
    """
    p = float(probability)
    y = float(outcome_numeric)

    if not 0.0 <= p <= 1.0:
        raise ValueError(f"probability must be within [0, 1], got {p}")
    if y not in (0.0, 1.0):
        raise ValueError(
            f"outcome_numeric must be exactly 0.0 or 1.0, got {y}. An AMBIGUOUS "
            "resolution has no numeric outcome and must never be scored."
        )

    return (p - y) ** 2


def mean(scores: Sequence[float]) -> Optional[float]:
    """
    Mean Brier over a set of scores. None for an empty set.

    None rather than 0.0 deliberately: 0.0 is a perfect score, and an empty
    sample rendered as a perfect score is the single most misleading number
    this system could produce.
    """
    values = [float(s) for s in scores if s is not None]
    return sum(values) / len(values) if values else None


def std(scores: Sequence[float]) -> Optional[float]:
    """Population standard deviation. None for fewer than two scores."""
    values = [float(s) for s in scores if s is not None]
    if len(values) < 2:
        return None
    m = sum(values) / len(values)
    return (sum((v - m) ** 2 for v in values) / len(values)) ** 0.5


def skill_score(mean_brier: Optional[float]) -> Optional[float]:
    """
    Brier skill relative to always saying 50%.

        skill = 1 - (brier / 0.25)

    Positive means the agent beats a coin flip; zero means it matches one;
    negative means it would be better to not run the agent. Reported because
    a raw Brier of 0.18 means nothing to a reader without its baseline, and
    the baseline is the first thing anyone should ask for.
    """
    if mean_brier is None:
        return None
    return 1.0 - (mean_brier / UNINFORMED_BASELINE)


def score_rows(rows: Iterable[tuple[float, float]]) -> list[float]:
    """Brier score for each `(probability, outcome)` pair."""
    return [compute(p, y) for p, y in rows]


def backfill_for_question(question_id: str) -> int:
    """
    Compute and store Brier for every scorable forecast on one question.

    Called immediately after a resolution is recorded. Runs as a single
    statement so a question's forecasts are either all scored or none are —
    a half-scored question would make the aggregate depend on when it was
    read.

    Ambiguous resolutions are excluded by the join predicate, so calling this
    for an ambiguous question is a safe no-op rather than a special case the
    caller has to remember.

    Returns:
        The number of forecast rows updated.
    """
    from calibration.db import get_cursor

    sql = """
        UPDATE calibration_forecasts f
        SET brier_score = POWER(f.final_probability - r.outcome_numeric, 2)
        FROM calibration_resolutions r
        WHERE r.question_id = f.question_id
          AND f.question_id = %s
          AND f.status = 'completed'
          AND f.final_probability IS NOT NULL
          AND r.outcome <> 'AMBIGUOUS'
          AND r.outcome_numeric IS NOT NULL
        RETURNING f.id::text;
    """
    with get_cursor() as cur:
        cur.execute(sql, (question_id,))
        updated = cur.fetchall()

    if updated:
        logger.info(
            "[brier] Scored %d forecast(s) for question_id=%s", len(updated), question_id
        )
    return len(updated)


def to_float(value: Optional[Decimal]) -> Optional[float]:
    """Decimal -> float for arithmetic. None passes through."""
    return float(value) if value is not None else None
