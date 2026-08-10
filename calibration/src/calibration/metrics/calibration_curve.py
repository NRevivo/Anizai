"""
Calibration curve — is 70% actually 70%?

Buckets forecasts by predicted probability and compares each bucket's mean
prediction against the rate at which those questions actually resolved YES.
A perfectly calibrated forecaster sits on the diagonal.

This is the metric the whole system is named for, and it answers a different
question from Brier. Brier says "how wrong were you"; the curve says "when you
said 70%, how often were you right" — which is what tells you whether the
agent is systematically overconfident, underconfident, or fine.

Two decisions that shape how the output reads:

**Bucket boundaries are half-open, `[low, high)`, except the last which is
closed.** So 0.2 falls in `0.2-0.4`, not `0.0-0.2`, and 1.0 falls in
`0.8-1.0`. Without the closed upper end, a forecast of exactly 1.0 would
belong to no bucket and vanish from the curve silently.

**Wilson intervals, not normal-approximation ones.** At the sample sizes this
system will actually have — often 3 or 4 forecasts in a bucket — the normal
approximation produces intervals that extend below 0 or above 1, which is
nonsense on a rate. Wilson stays inside [0,1] and stays honest at small n,
which is the whole point of showing an interval here.

References:
    - calibration_plan.md §3 E2
"""

from __future__ import annotations

import math
from typing import Iterable, Optional

from calibration.metrics import brier

# Five fixed buckets (plan E2). Fixed rather than adaptive so the curve is
# comparable across weeks: a bucket that moves cannot be tracked over time.
BUCKETS: tuple[tuple[float, float], ...] = (
    (0.0, 0.2),
    (0.2, 0.4),
    (0.4, 0.6),
    (0.6, 0.8),
    (0.8, 1.0),
)

# z for a 95% two-sided interval.
_Z_95 = 1.959963985


def bucket_label(low: float, high: float) -> str:
    return f"{low:.1f}-{high:.1f}"


def assign_bucket(probability: float) -> Optional[str]:
    """
    Which bucket a probability belongs to.

    Half-open `[low, high)` except the final bucket, which includes 1.0. A
    probability outside [0,1] returns None — it should never reach here (the
    schema CHECK forbids it), and inventing a bucket for it would hide a real
    data problem.
    """
    p = float(probability)
    if not 0.0 <= p <= 1.0:
        return None

    for low, high in BUCKETS:
        if low <= p < high:
            return bucket_label(low, high)
    if p == 1.0:
        last_low, last_high = BUCKETS[-1]
        return bucket_label(last_low, last_high)
    return None


def wilson_interval(successes: int, total: int, z: float = _Z_95) -> tuple[float, float]:
    """
    Wilson score interval for a binomial proportion.

    Returns `(lower, upper)`, both within [0, 1]. For total == 0 returns
    (0.0, 1.0) — total ignorance, which is the honest rendering of an empty
    bucket rather than a point at zero.
    """
    if total <= 0:
        return 0.0, 1.0

    p = successes / total
    denominator = 1 + (z**2) / total
    centre = p + (z**2) / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + (z**2) / (4 * total)) / total)

    lower = (centre - margin) / denominator
    upper = (centre + margin) / denominator
    return max(0.0, lower), min(1.0, upper)


def compute(pairs: Iterable[tuple[float, float]]) -> dict:
    """
    Build the calibration curve.

    Args:
        pairs: `(probability, outcome_numeric)` for every scorable forecast.

    Returns:
        A payload with one entry per bucket — always all five, including empty
        ones. An empty bucket is information: it says the agent never made a
        forecast in that confidence band, which is itself a finding about its
        behaviour. Dropping empty buckets would make a curve with three points
        look like a complete picture.
    """
    rows = [(float(p), float(y)) for p, y in pairs]

    by_bucket: dict[str, list[tuple[float, float]]] = {
        bucket_label(low, high): [] for low, high in BUCKETS
    }
    unbucketed = 0
    for probability, outcome in rows:
        label = assign_bucket(probability)
        if label is None:
            unbucketed += 1
            continue
        by_bucket[label].append((probability, outcome))

    points = []
    for low, high in BUCKETS:
        label = bucket_label(low, high)
        members = by_bucket[label]
        count = len(members)
        yes_count = sum(1 for _p, y in members if y == 1.0)
        lower, upper = wilson_interval(yes_count, count)

        points.append(
            {
                "bucket": label,
                "count": count,
                "mean_predicted": (
                    sum(p for p, _y in members) / count if count else None
                ),
                "actual_yes_rate": (yes_count / count if count else None),
                "yes_count": yes_count,
                "lower_bound": lower,
                "upper_bound": upper,
            }
        )

    scores = [brier.compute(p, y) for p, y in rows]
    return {
        "points": points,
        "total_forecasts": len(rows),
        "unbucketed": unbucketed,
        "aggregate_brier": brier.mean(scores),
        "skill_vs_coin_flip": brier.skill_score(brier.mean(scores)),
    }


def render_ascii(payload: dict) -> list[str]:
    """
    Human-readable curve for the CLI.

    Shows `n` on every row. A bucket with n=2 and a bucket with n=40 look
    identical as points on a chart, and treating them the same is how a
    calibration report becomes misleading (Risk 6).
    """
    lines = [
        f"{'bucket':<12} {'n':>4} {'predicted':>10} {'actual':>8} {'95% CI':>16}",
        "-" * 56,
    ]
    for point in payload["points"]:
        if point["count"] == 0:
            lines.append(f"{point['bucket']:<12} {0:>4}        —        —                —")
            continue
        ci = f"[{point['lower_bound']:.2f}, {point['upper_bound']:.2f}]"
        flag = "  (n<10)" if point["count"] < 10 else ""
        lines.append(
            f"{point['bucket']:<12} {point['count']:>4} "
            f"{point['mean_predicted']:>10.3f} {point['actual_yes_rate']:>8.3f} "
            f"{ci:>16}{flag}"
        )

    aggregate = payload.get("aggregate_brier")
    skill = payload.get("skill_vs_coin_flip")
    lines.append("-" * 56)
    lines.append(
        f"aggregate Brier: {aggregate:.4f}" if aggregate is not None
        else "aggregate Brier: — (no scorable forecasts)"
    )
    if skill is not None:
        verdict = "better than" if skill > 0 else ("same as" if skill == 0 else "WORSE than")
        lines.append(f"skill vs coin flip: {skill:+.3f} ({verdict} always saying 50%)")
    return lines
