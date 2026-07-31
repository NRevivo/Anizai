"""
Cohort Brier — does the agent get worse as the horizon lengthens?

Mean Brier computed separately per resolution-time cohort, plus an `all`
aggregate. The expected shape is that short-horizon questions score better
than long-horizon ones; a flat curve would suggest the agent is not actually
using recency, and an inverted one would suggest something is wrong with how
short-horizon questions are being selected.

Every row carries its `n`. A cohort with three resolved questions and a
cohort with forty produce means that render identically and mean entirely
different things — and this system's whole purpose is to avoid producing
confident numbers that are not backed by data.

References:
    - calibration_plan.md §3 E3
"""

from __future__ import annotations

from typing import Iterable, Optional

from calibration.metrics import brier
from calibration.models import COHORTS

# Below this, a cohort mean is reported but flagged. Not a hard cutoff — the
# number is still the best estimate available, and hiding it would leave the
# operator with nothing. Flagging it is the honest middle.
SMALL_SAMPLE_THRESHOLD = 10


def compute(rows: Iterable[tuple[str, float, float]]) -> dict:
    """
    Mean Brier per cohort.

    Args:
        rows: `(cohort, probability, outcome_numeric)` per scorable forecast.

    Returns:
        A payload with one item per cohort — always all three, plus `all`.
        Cohorts with no resolved forecasts appear with `n=0` and a null mean.
        Omitting them would make a report covering one cohort look like a
        report covering the system.
    """
    by_cohort: dict[str, list[float]] = {cohort: [] for cohort in COHORTS}
    everything: list[float] = []

    for cohort, probability, outcome in rows:
        score = brier.compute(float(probability), float(outcome))
        everything.append(score)
        by_cohort.setdefault(cohort, []).append(score)

    items = []
    for cohort in list(COHORTS) + ["all"]:
        scores = everything if cohort == "all" else by_cohort.get(cohort, [])
        mean = brier.mean(scores)
        items.append(
            {
                "cohort": cohort,
                "n": len(scores),
                "mean_brier": mean,
                "std_brier": brier.std(scores),
                "skill_vs_coin_flip": brier.skill_score(mean),
                "small_sample": 0 < len(scores) < SMALL_SAMPLE_THRESHOLD,
            }
        )

    return {"items": items}


def render_ascii(payload: dict) -> list[str]:
    """Human-readable cohort table for the CLI."""
    lines = [
        f"{'cohort':<10} {'n':>4} {'mean Brier':>12} {'std':>8} {'skill':>8}",
        "-" * 48,
    ]
    for item in payload["items"]:
        if item["n"] == 0:
            lines.append(f"{item['cohort']:<10} {0:>4}            —        —        —")
            continue
        std = f"{item['std_brier']:.4f}" if item["std_brier"] is not None else "—"
        flag = "  (small n)" if item["small_sample"] else ""
        lines.append(
            f"{item['cohort']:<10} {item['n']:>4} {item['mean_brier']:>12.4f} "
            f"{std:>8} {item['skill_vs_coin_flip']:>+8.3f}{flag}"
        )
    return lines


def cohort_of(items: list[dict], cohort: str) -> Optional[dict]:
    """Look one cohort's row out of a computed payload."""
    return next((i for i in items if i["cohort"] == cohort), None)
