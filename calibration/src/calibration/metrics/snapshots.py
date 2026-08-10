"""
Snapshots — compute every metric and persist the results.

The one module here that touches the database. It pulls the scorable forecast
set once, hands the same rows to every metric module, and writes one
`calibration_metrics_snapshots` row per metric type.

Pulling once and sharing matters: if each metric ran its own query, two
snapshots written in the same batch could disagree because a resolution
landed between them. A snapshot is supposed to be a consistent picture of one
moment, and a set of rows that never coexisted is not that.

References:
    - calibration_plan.md §3 E1-E5, F2
    - calibration_plan.md §8 T10C.7
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from calibration.metrics import (
    brier,
    calibration_curve,
    cohort_brier,
    improvement_curve,
    source_contribution,
)
from calibration.models import MetricsSnapshot

logger = logging.getLogger(__name__)

METRIC_TYPES = (
    "aggregate_brier",
    "calibration_curve",
    "cohort_brier",
    "improvement_curve",
    "source_contribution",
)


def load_scorable_rows() -> list[dict[str, Any]]:
    """
    Fetch every forecast eligible for scoring, with the fields all metrics need.

    The inclusion rule lives in the SQL here and in `repos.forecasts.
    list_scorable`, and nowhere else: `completed`, non-null probability, on a
    question with a non-AMBIGUOUS resolution.
    """
    from calibration.db import get_cursor

    sql = """
        SELECT
            f.id::text                AS forecast_id,
            f.question_id::text       AS question_id,
            q.cohort                  AS cohort,
            q.category                AS category,
            f.forecast_run_index      AS run_index,
            f.final_probability       AS probability,
            f.agent_version           AS agent_version,
            f.agent_evidence_summary  AS evidence_summary,
            r.outcome                 AS outcome,
            r.outcome_numeric         AS outcome_numeric,
            r.resolved_at             AS resolved_at
        FROM calibration_forecasts f
        JOIN calibration_questions   q ON q.id = f.question_id
        JOIN calibration_resolutions r ON r.question_id = f.question_id
        WHERE f.status = 'completed'
          AND f.final_probability IS NOT NULL
          AND r.outcome <> 'AMBIGUOUS'
          AND r.outcome_numeric IS NOT NULL
        ORDER BY f.question_id, f.forecast_run_index;
    """
    with get_cursor() as cur:
        cur.execute(sql)
        return [dict(row) for row in cur.fetchall()]


def compute_all(rows: Optional[list[dict[str, Any]]] = None) -> dict[str, dict]:
    """
    Compute every metric from one consistent row set.

    Args:
        rows: pre-loaded scorable rows. When None they are loaded here.
              Injectable so the whole metric suite can be exercised against
              synthetic data with no database.

    Returns:
        `{metric_type: payload}` for all five types. Always all five, even
        with zero scorable forecasts — a metric missing from the output is
        indistinguishable from a metric that failed, and an empty payload with
        `n=0` says clearly that there is nothing to report yet.
    """
    rows = load_scorable_rows() if rows is None else rows

    pairs = [(float(r["probability"]), float(r["outcome_numeric"])) for r in rows]
    scores = [brier.compute(p, y) for p, y in pairs]
    mean_brier = brier.mean(scores)

    return {
        "aggregate_brier": {
            "n": len(scores),
            "mean_brier": mean_brier,
            "std_brier": brier.std(scores),
            "skill_vs_coin_flip": brier.skill_score(mean_brier),
            "uninformed_baseline": brier.UNINFORMED_BASELINE,
        },
        "calibration_curve": calibration_curve.compute(pairs),
        "cohort_brier": cohort_brier.compute(
            [(r["cohort"], float(r["probability"]), float(r["outcome_numeric"])) for r in rows]
        ),
        "improvement_curve": improvement_curve.compute(
            [
                (
                    r["question_id"],
                    r["cohort"],
                    int(r["run_index"]),
                    float(r["probability"]),
                    float(r["outcome_numeric"]),
                    r.get("agent_version"),
                    r["resolved_at"].isoformat() if r.get("resolved_at") else None,
                )
                for r in rows
            ]
        ),
        "source_contribution": source_contribution.compute(
            [
                (r.get("evidence_summary"), float(r["probability"]), float(r["outcome_numeric"]))
                for r in rows
            ]
        ),
    }


def write_snapshots(payloads: Optional[dict[str, dict]] = None) -> dict[str, str]:
    """
    Persist one snapshot row per metric type.

    Returns `{metric_type: snapshot_id}`.
    """
    from calibration.repos import metrics as metrics_repo

    payloads = compute_all() if payloads is None else payloads
    written: dict[str, str] = {}

    for metric_type in METRIC_TYPES:
        payload = payloads.get(metric_type, {})
        snapshot_id = metrics_repo.insert(
            MetricsSnapshot(metric_type=metric_type, cohort=None, payload=payload)
        )
        written[metric_type] = snapshot_id

    logger.info("[snapshots] Wrote %d metric snapshot(s)", len(written))
    return written


def render_summary(payloads: dict[str, dict]) -> list[str]:
    """A single readable block covering every metric, for the CLI."""
    aggregate = payloads.get("aggregate_brier", {})
    n = aggregate.get("n", 0)

    lines = ["", "=== Aggregate ==="]
    if n == 0:
        lines += [
            "  No scorable forecasts yet.",
            "  A forecast counts once it is `completed`, carries a probability,",
            "  and its question has resolved to YES or NO (not AMBIGUOUS).",
        ]
        return lines

    lines += [
        f"  n                  : {n}",
        f"  mean Brier         : {aggregate['mean_brier']:.4f}",
        f"  baseline (coin)    : {aggregate['uninformed_baseline']:.4f}",
        f"  skill              : {aggregate['skill_vs_coin_flip']:+.3f}",
        "",
        "=== Calibration curve ===",
    ]
    lines += ["  " + ln for ln in calibration_curve.render_ascii(payloads["calibration_curve"])]
    lines += ["", "=== Cohort Brier ==="]
    lines += ["  " + ln for ln in cohort_brier.render_ascii(payloads["cohort_brier"])]
    lines += ["", "=== Improvement ==="]
    lines += ["  " + ln for ln in improvement_curve.render_ascii(payloads["improvement_curve"])]
    lines += ["", "=== Source contribution ==="]
    lines += ["  " + ln for ln in source_contribution.render_ascii(payloads["source_contribution"])]
    return lines
