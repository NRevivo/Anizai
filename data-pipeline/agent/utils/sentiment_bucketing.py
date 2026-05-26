"""
agent/utils/sentiment_bucketing.py — Generic time-bucketing for
sentiment scores (Sprint 22 T22.5).

Closes the wiring portion of KG-PHASE8-22 in tandem with T22.6: T22.5
produces in-state aggregates; T22.6 adapts them into the
sentimentTimeSeries subcollection.

Public interface:
    bucket_sentiment_by_time(items, *, sentiment_field, time_field,
                             window_days=14, bucket_days=1, now=None)
                                                       → list[dict]

Why one generic helper (not one per source):
    Both Expert (`knowledge_vectors.sentiment_score`) and Public
    (`social_vectors.community_sentiment`) lines bucket the same way —
    chronological aggregate, calendar-day boundaries. Per-source
    duplication would diverge in subtle ways over time. The field-name
    parameters (`sentiment_field`, `time_field`) let the same body back
    both lines + any future enrichment-source line without per-call
    customisation.

Bucket semantics (D-decision recorded in revised plan + T22.5 proposal):
    - Calendar boundaries: each bucket starts at UTC 00:00:00.
    - Right-open intervals `[start, start + bucket_days)`.
    - Reproducible across same-day forecasts at different wall-clock
      times: a forecast at 14:00 UTC and one at 16:00 UTC on the same
      day produce identical bucket structures.

Output is fixed-size:
    Always returns exactly `window_days // bucket_days` buckets in
    chronological ascending order. Buckets with zero matching items
    are emitted with `avg_sentiment=None, sample_count=0` — this
    matters for the FE chart's continuous x-axis, and lets
    Future Enhancement 5 sample-floor filtering work post-hoc
    (drop entries where `sample_count < N`).

Forward-compat (signature):
    The five named parameters are the public contract. Future kwargs
    (e.g., `weight_field` for weighted averaging, `aggregator` for
    non-mean reductions, `min_samples` for inline sample-floor
    filtering) can be appended without breaking current callers —
    all current callers use the documented kwarg names with stable
    defaults.

Spec references:
    - data-pipeline/docs/agentic_hub_implementation_phase8_revised.md
      §Sprint 22 T22.5 + Future Enhancement 5
    - task_plan.md Known Gaps KG-PHASE8-22
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Module-level constants — surfaced for greppability.
DEFAULT_WINDOW_DAYS: int = 14
DEFAULT_BUCKET_DAYS: int = 1


def bucket_sentiment_by_time(
    items: list[dict],
    *,
    sentiment_field: str,
    time_field: str,
    window_days: int = DEFAULT_WINDOW_DAYS,
    bucket_days: int = DEFAULT_BUCKET_DAYS,
    now: Optional[datetime] = None,
) -> list[dict]:
    """
    Bucket sentiment scores into a chronological calendar-day time series.

    Args:
        items:           List of dicts with at least `sentiment_field`
                         and `time_field` keys. Items missing either
                         field, or with unparseable values, are silently
                         skipped — the helper is robust to partial data.
        sentiment_field: Name of the per-item numeric sentiment field
                         (e.g., "sentiment_score", "community_sentiment").
                         Values must be `float`-coercible.
        time_field:      Name of the per-item timestamp field (e.g.,
                         "published_at"). Accepts ISO 8601 strings or
                         tz-aware `datetime` objects. Naive datetimes
                         are coerced to UTC.
        window_days:     Lookback window in calendar days, anchored to
                         `now`'s UTC calendar day. Default 14.
        bucket_days:     Bucket width in calendar days. Default 1 (one
                         bucket per day).
        now:             Reference instant. Defaults to `datetime.now(
                         timezone.utc)`. Test-injectable per the
                         `market_bridge.run(now=...)` convention so
                         calendar-based bucketing is reproducible
                         without freezing real time.

    Returns:
        List of dicts, length = `window_days // bucket_days`, ordered
        by `date` ascending:

            {
                "date":          datetime  # tz-aware UTC, bucket START
                                           # (UTC midnight of the first
                                           # day in the bucket)
                "avg_sentiment": Optional[float]  # None if sample_count == 0
                "sample_count":  int >= 0
            }

        Buckets with no matching items are emitted with
        `sample_count=0, avg_sentiment=None` — the FE chart's
        continuous x-axis needs the gap markers, and Future Enhancement
        5 sample-floor filtering can post-process by `sample_count`.

    Edge cases:
        - Empty `items` → all buckets emitted with sample_count=0.
        - Items outside [earliest_bucket_start, latest_bucket_end) → excluded.
        - Future-dated items (after the latest bucket's end) → excluded.
        - Multiple items in the same bucket → arithmetic mean.
        - Item missing sentiment_field or time_field → skipped.
        - Item with non-numeric sentiment value → skipped.
        - Item with naive datetime in time_field → coerced to UTC
          (matches the project convention in processing/gold_job.py
          and persistence/momentum_vault.py).

    Why bucket START (not midpoint/end):
        Half-open interval convention `[start, start + bucket_days)`
        matches the downstream chart's natural rendering: each x-axis
        tick represents the start of a day/period.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    today_start = datetime(
        now.year, now.month, now.day, tzinfo=timezone.utc,
    )
    bucket_count = window_days // bucket_days
    bucket_delta = timedelta(days=bucket_days)
    earliest_bucket_start = today_start - bucket_delta * (bucket_count - 1)
    window_end = today_start + bucket_delta  # exclusive upper bound

    # Initialise empty buckets indexed by their start datetime.
    bucket_samples: dict[datetime, list[float]] = {}
    for i in range(bucket_count):
        bucket_start = earliest_bucket_start + bucket_delta * i
        bucket_samples[bucket_start] = []

    for item in items:
        ts = _coerce_to_utc_datetime(item.get(time_field))
        if ts is None:
            continue
        if ts < earliest_bucket_start or ts >= window_end:
            continue

        sentiment = _coerce_to_float(item.get(sentiment_field))
        if sentiment is None:
            continue

        # Integer-divide timedeltas to find the bucket index.
        bucket_idx = (ts - earliest_bucket_start) // bucket_delta
        bucket_start = earliest_bucket_start + bucket_delta * bucket_idx
        bucket_samples[bucket_start].append(sentiment)

    result: list[dict] = []
    for bucket_start in sorted(bucket_samples.keys()):
        samples = bucket_samples[bucket_start]
        avg: Optional[float] = (
            sum(samples) / len(samples) if samples else None
        )
        result.append({
            "date":          bucket_start,
            "avg_sentiment": avg,
            "sample_count":  len(samples),
        })
    return result


# ==========================================================
# Internal helpers
# ==========================================================

def _coerce_to_utc_datetime(value: Any) -> Optional[datetime]:
    """
    Accept an ISO 8601 string or a `datetime` object; return a tz-aware
    `datetime` in UTC or None when the input can't be coerced.

    Naive datetimes are treated as UTC (project convention — see
    `persistence/momentum_vault._parse_timestamp` and
    `processing/gold_job` callsites).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


def _coerce_to_float(value: Any) -> Optional[float]:
    """
    Convert a sentiment-field value to float. Return None for missing
    or non-numeric inputs.

    bool is a subclass of int — explicitly reject so a stray True/False
    in a sentiment column doesn't silently average as 1.0/0.0.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
