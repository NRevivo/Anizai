"""
Improvement curve — did re-forecasting closer to the event actually help?

For every resolved question with more than one forecast, compare the original
(lowest `forecast_run_index`) against the latest (highest) and report the
delta. A positive delta means the later forecast scored better.

    delta = brier(original) - brier(latest)      positive = improved

This is the metric that justifies the weekly re-forecast cycle. If deltas
cluster around zero, the agent is not learning anything from the extra week of
evidence and the cycle is pure cost.

The interpretation trap here is severe enough to be worth stating in the
module rather than the docs (Risk 6): **at small n, this metric is noise.**
Three resolved questions that happened to go badly on the second pass read as
"the agent regressed" with the same visual weight as thirty that did. Every
output therefore carries `n`, and `interpretable` is False below a threshold —
not to hide the number, but so no chart renders it without the caveat
attached.

A question forecast only once contributes nothing. That is not a gap: with one
forecast there is no before-and-after to compare, and imputing one would
manufacture a delta from nothing.

References:
    - calibration_plan.md §3 F2, F3
"""

from __future__ import annotations

from typing import Iterable, Optional

from calibration.metrics import brier

# Below this many paired questions the aggregate delta is dominated by noise.
MIN_INTERPRETABLE_N = 10


def compute(
    rows: Iterable[tuple[str, str, int, float, float, Optional[str], Optional[str]]],
) -> dict:
    """
    Build the improvement series.

    Args:
        rows: one tuple per scorable forecast —
              `(question_id, cohort, run_index, probability, outcome,
                agent_version, resolved_at_iso)`.

    Returns:
        A payload with a per-question series and an aggregate. Questions with
        a single forecast are counted in `single_forecast_questions` and
        excluded from the deltas.
    """
    by_question: dict[str, list[tuple]] = {}
    for row in rows:
        by_question.setdefault(row[0], []).append(row)

    points = []
    single_forecast = 0

    for question_id, forecasts in by_question.items():
        if len(forecasts) < 2:
            single_forecast += 1
            continue

        ordered = sorted(forecasts, key=lambda r: r[2])
        first, last = ordered[0], ordered[-1]

        original_brier = brier.compute(float(first[3]), float(first[4]))
        latest_brier = brier.compute(float(last[3]), float(last[4]))

        points.append(
            {
                "question_id": question_id,
                "cohort": first[1],
                "original_run_index": first[2],
                "latest_run_index": last[2],
                "original_probability": float(first[3]),
                "latest_probability": float(last[3]),
                "original_brier": original_brier,
                "latest_brier": latest_brier,
                "delta": original_brier - latest_brier,
                "improved": latest_brier < original_brier,
                "agent_version_pair": [first[5], last[5]],
                "resolved_at": last[6],
            }
        )

    points.sort(key=lambda p: (p["resolved_at"] or "", p["question_id"]))

    deltas = [p["delta"] for p in points]
    mean_delta = sum(deltas) / len(deltas) if deltas else None

    by_cohort: dict[str, list[float]] = {}
    for point in points:
        by_cohort.setdefault(point["cohort"], []).append(point["delta"])

    return {
        "points": points,
        "n_paired_questions": len(points),
        "single_forecast_questions": single_forecast,
        "mean_delta": mean_delta,
        "improved_count": sum(1 for p in points if p["improved"]),
        "worsened_count": sum(1 for p in points if not p["improved"] and p["delta"] != 0),
        "by_cohort": {
            cohort: {
                "n": len(values),
                "mean_delta": sum(values) / len(values),
            }
            for cohort, values in by_cohort.items()
        },
        # The guard against reading noise as a trend. False does not mean the
        # number is wrong — it means it is not yet evidence.
        "interpretable": len(points) >= MIN_INTERPRETABLE_N,
        "min_interpretable_n": MIN_INTERPRETABLE_N,
    }


def render_ascii(payload: dict) -> list[str]:
    """Human-readable improvement summary for the CLI."""
    n = payload["n_paired_questions"]
    lines = [
        f"paired questions      : {n}",
        f"single-forecast only  : {payload['single_forecast_questions']} (excluded — no before/after)",
    ]

    if n == 0:
        lines.append("")
        lines.append("No question has been forecast twice and resolved yet.")
        return lines

    lines += [
        f"improved              : {payload['improved_count']}",
        f"worsened              : {payload['worsened_count']}",
        f"mean delta            : {payload['mean_delta']:+.4f}  (positive = re-forecast scored better)",
    ]

    if not payload["interpretable"]:
        lines.append("")
        lines.append(
            f"NOT YET INTERPRETABLE — {n} paired question(s) is below the "
            f"{payload['min_interpretable_n']} needed for this delta to mean "
            "anything. At this sample size the sign is noise."
        )

    if payload["by_cohort"]:
        lines.append("")
        lines.append("by cohort:")
        for cohort, stats in sorted(payload["by_cohort"].items()):
            lines.append(
                f"  {cohort:<8} n={stats['n']:<3} mean delta {stats['mean_delta']:+.4f}"
            )
    return lines
