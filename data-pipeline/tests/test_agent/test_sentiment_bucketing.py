"""
Sprint 22 T22.5 — Gate 1 tests for agent/utils/sentiment_bucketing.py.

Pure unit tests — the function is deterministic and side-effect-free.
Every test injects a fixed `now` for reproducibility (the calendar-day
bucketing semantics would otherwise drift each day the suite runs).

Coverage map (10 tests):
    1. Empty input → fixed-size empty output
    2. Single item → one populated bucket + empties
    3. Multiple items in the same bucket → arithmetic mean
    4. Multiple items across buckets → independent per-bucket averages
    5. Items older than window_days → excluded
    6. Future-dated items → excluded
    7. Custom window_days / bucket_days → output size + alignment
    8. Bucket dates ordered ascending + aligned to UTC midnight
    9. Items with missing sentiment_field or time_field → skipped
   10. Items with naive datetime in time_field → coerced to UTC

References:
    - data-pipeline/agent/utils/sentiment_bucketing.py
    - data-pipeline/docs/agentic_hub_implementation_phase8_revised.md
      §Sprint 22 T22.5
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent.utils.sentiment_bucketing import bucket_sentiment_by_time


# ==========================================================
# Fixtures / helpers
# ==========================================================

# A fixed reference instant for all tests — picks an arbitrary
# afternoon UTC so the boundary between "today's bucket" and "tomorrow's
# bucket" is clearly inside today's calendar day.
NOW = datetime(2026, 5, 23, 14, 0, 0, tzinfo=timezone.utc)

# today_start under NOW: 2026-05-23T00:00:00Z. The 14-day window at
# default bucket_days=1 spans bucket starts from 2026-05-10T00:00:00Z
# (the 14th bucket counting backwards including today) through
# 2026-05-23T00:00:00Z (today).
TODAY_START = datetime(2026, 5, 23, 0, 0, 0, tzinfo=timezone.utc)
EARLIEST_BUCKET_START_DEFAULT = TODAY_START - timedelta(days=13)


def _item(ts: datetime | str, sentiment: float) -> dict:
    """Minimal item shape — only the two fields the helper reads."""
    return {"published_at": ts, "sentiment_score": sentiment}


def _all_dates(result: list[dict]) -> list[datetime]:
    return [b["date"] for b in result]


# ==========================================================
# Tests
# ==========================================================

class TestSentimentBucketing:
    """Gate 1 unit tests for the generic time-bucketing helper."""

    # 1
    def test_empty_input_yields_fixed_size_all_empty_buckets(self):
        """
        Empty `items` still returns exactly 14 buckets (default
        window_days // bucket_days) — sample_count=0, avg_sentiment=None.
        Predictable output size lets T22.6 and the FE rely on it.
        """
        result = bucket_sentiment_by_time(
            [],
            sentiment_field="sentiment_score",
            time_field="published_at",
            now=NOW,
        )
        assert len(result) == 14
        for bucket in result:
            assert bucket["sample_count"] == 0
            assert bucket["avg_sentiment"] is None

    # 2
    def test_single_item_lands_in_one_populated_bucket(self):
        """
        A single item produces 14 buckets total — the one containing
        the item has sample_count=1 and avg_sentiment equal to that
        item's score; the other 13 are empty.
        """
        items = [
            _item(
                ts=datetime(2026, 5, 20, 10, 0, 0, tzinfo=timezone.utc),
                sentiment=0.42,
            ),
        ]
        result = bucket_sentiment_by_time(
            items,
            sentiment_field="sentiment_score",
            time_field="published_at",
            now=NOW,
        )
        assert len(result) == 14
        populated = [b for b in result if b["sample_count"] > 0]
        empty = [b for b in result if b["sample_count"] == 0]
        assert len(populated) == 1
        assert len(empty) == 13
        assert populated[0]["date"] == datetime(2026, 5, 20, tzinfo=timezone.utc)
        assert populated[0]["sample_count"] == 1
        assert populated[0]["avg_sentiment"] == pytest.approx(0.42)

    # 3
    def test_multiple_items_same_bucket_are_averaged(self):
        """
        Three items in the same calendar day → arithmetic mean.
        sample_count reflects the actual item count.
        """
        items = [
            _item(datetime(2026, 5, 21,  3, 0, 0, tzinfo=timezone.utc),  0.2),
            _item(datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc), -0.3),
            _item(datetime(2026, 5, 21, 18, 0, 0, tzinfo=timezone.utc),  0.4),
        ]
        result = bucket_sentiment_by_time(
            items,
            sentiment_field="sentiment_score",
            time_field="published_at",
            now=NOW,
        )
        bucket = next(
            b for b in result
            if b["date"] == datetime(2026, 5, 21, tzinfo=timezone.utc)
        )
        assert bucket["sample_count"] == 3
        # (0.2 + -0.3 + 0.4) / 3 = 0.1
        assert bucket["avg_sentiment"] == pytest.approx(0.1)

    # 4
    def test_multiple_items_across_buckets_distributed_independently(self):
        """
        Items in different calendar days populate different buckets
        with independent averages. Each bucket's mean uses only its
        own items.
        """
        items = [
            _item(datetime(2026, 5, 20, 10, 0, 0, tzinfo=timezone.utc), 0.5),
            _item(datetime(2026, 5, 20, 18, 0, 0, tzinfo=timezone.utc), 0.7),  # avg 0.6 on the 20th
            _item(datetime(2026, 5, 22,  9, 0, 0, tzinfo=timezone.utc), -0.4), # avg -0.4 on the 22nd
        ]
        result = bucket_sentiment_by_time(
            items,
            sentiment_field="sentiment_score",
            time_field="published_at",
            now=NOW,
        )
        by_date = {b["date"]: b for b in result}
        twentieth = by_date[datetime(2026, 5, 20, tzinfo=timezone.utc)]
        twenty_first = by_date[datetime(2026, 5, 21, tzinfo=timezone.utc)]
        twenty_second = by_date[datetime(2026, 5, 22, tzinfo=timezone.utc)]
        assert twentieth["sample_count"] == 2
        assert twentieth["avg_sentiment"] == pytest.approx(0.6)
        assert twenty_first["sample_count"] == 0
        assert twenty_first["avg_sentiment"] is None
        assert twenty_second["sample_count"] == 1
        assert twenty_second["avg_sentiment"] == pytest.approx(-0.4)

    # 5
    def test_items_older_than_window_are_excluded(self):
        """
        An item dated 15 days ago (just outside the 14-day window) must
        not appear in any bucket. The whole result remains empty.
        """
        too_old = NOW - timedelta(days=15)
        items = [_item(ts=too_old, sentiment=0.9)]
        result = bucket_sentiment_by_time(
            items,
            sentiment_field="sentiment_score",
            time_field="published_at",
            now=NOW,
        )
        assert all(b["sample_count"] == 0 for b in result)
        assert all(b["avg_sentiment"] is None for b in result)

    # 6
    def test_future_dated_items_are_excluded(self):
        """
        Defensive: an item dated tomorrow must not appear. The latest
        bucket's right boundary is today_start + bucket_days (exclusive).
        Items at NOW (today, before midnight) should still land in
        today's bucket.
        """
        items = [
            _item(
                ts=datetime(2026, 5, 24, 10, 0, 0, tzinfo=timezone.utc),  # tomorrow
                sentiment=0.9,
            ),
            _item(NOW, sentiment=0.5),  # today — should land
        ]
        result = bucket_sentiment_by_time(
            items,
            sentiment_field="sentiment_score",
            time_field="published_at",
            now=NOW,
        )
        today_bucket = next(b for b in result if b["date"] == TODAY_START)
        assert today_bucket["sample_count"] == 1
        assert today_bucket["avg_sentiment"] == pytest.approx(0.5)
        # No other bucket should contain the future-dated item.
        assert sum(b["sample_count"] for b in result) == 1

    # 7
    def test_custom_window_and_bucket_days_emit_correct_count_and_alignment(self):
        """
        window_days=30 + bucket_days=7 → exactly 4 buckets (30 // 7 = 4),
        each spanning a week, aligned to UTC midnight. Buckets are
        right-open `[start, start + 7d)`. Note that 4 * 7 = 28 days, so
        the effective span is 28 days from today's start, not 30 — the
        last 2 days of the requested window are dropped by floor
        division on bucket_count. Items must fall within the 28-day
        effective window to be counted.
        """
        # NOW = 2026-05-23T14:00:00Z; today_start = 2026-05-23T00:00:00Z.
        # Earliest bucket start = today_start - 21d = 2026-05-02T00:00:00Z.
        # Effective window: [2026-05-02, 2026-05-30).
        items = [
            _item(NOW - timedelta(days=2),  sentiment=0.1),   # bucket 3 (2026-05-23)
            _item(NOW - timedelta(days=10), sentiment=0.2),   # bucket 1 (2026-05-09)
            _item(NOW - timedelta(days=18), sentiment=0.3),   # bucket 0 (2026-05-02)
        ]
        result = bucket_sentiment_by_time(
            items,
            sentiment_field="sentiment_score",
            time_field="published_at",
            window_days=30,
            bucket_days=7,
            now=NOW,
        )
        assert len(result) == 4
        # Each bucket is 7 days apart, ascending
        diffs = [
            result[i + 1]["date"] - result[i]["date"]
            for i in range(len(result) - 1)
        ]
        assert all(d == timedelta(days=7) for d in diffs)
        # All 3 items lie inside the 28-day effective window
        assert sum(b["sample_count"] for b in result) == 3
        # Three distinct buckets each carry one item
        populated = [b for b in result if b["sample_count"] > 0]
        assert len(populated) == 3

    # 8
    def test_bucket_dates_are_ordered_ascending_and_aligned_to_utc_midnight(self):
        """
        Every bucket date is tz-aware UTC at 00:00:00, and the list is
        in ascending order. Both properties matter for the FE chart's
        x-axis and for downstream Firestore Timestamp conversion.
        """
        result = bucket_sentiment_by_time(
            [],
            sentiment_field="sentiment_score",
            time_field="published_at",
            now=NOW,
        )
        dates = _all_dates(result)
        # Ascending
        assert dates == sorted(dates)
        # All at UTC midnight, tz-aware
        for d in dates:
            assert d.hour == 0
            assert d.minute == 0
            assert d.second == 0
            assert d.microsecond == 0
            assert d.tzinfo == timezone.utc
        # First bucket is the expected window start
        assert dates[0] == EARLIEST_BUCKET_START_DEFAULT
        # Last bucket is today
        assert dates[-1] == TODAY_START

    # 9
    def test_items_with_missing_fields_are_silently_skipped(self):
        """
        Items missing either `sentiment_field` or `time_field`, or
        carrying non-numeric / non-parseable values, are skipped
        silently. Other well-formed items in the same input are
        unaffected.
        """
        items = [
            # Valid baseline item.
            _item(datetime(2026, 5, 20, 10, 0, 0, tzinfo=timezone.utc), 0.5),
            # Missing sentiment field.
            {"published_at": datetime(2026, 5, 21, 10, 0, 0, tzinfo=timezone.utc)},
            # Missing time field.
            {"sentiment_score": 0.7},
            # Non-numeric sentiment value.
            {"published_at": datetime(2026, 5, 22, 10, 0, 0, tzinfo=timezone.utc),
             "sentiment_score": "not-a-number"},
            # Unparseable timestamp string.
            {"published_at": "not-an-iso-string",
             "sentiment_score": 0.3},
            # None values.
            {"published_at": None, "sentiment_score": None},
        ]
        result = bucket_sentiment_by_time(
            items,
            sentiment_field="sentiment_score",
            time_field="published_at",
            now=NOW,
        )
        # Only the first item is well-formed and inside the window.
        total = sum(b["sample_count"] for b in result)
        assert total == 1
        bucket = next(
            b for b in result
            if b["date"] == datetime(2026, 5, 20, tzinfo=timezone.utc)
        )
        assert bucket["sample_count"] == 1
        assert bucket["avg_sentiment"] == pytest.approx(0.5)

    # 10
    def test_naive_datetime_in_time_field_is_coerced_to_utc(self):
        """
        An item whose time_field is a naive `datetime` is treated as
        UTC (project convention). The item must end up in the same
        bucket as the tz-aware equivalent.
        """
        naive_ts = datetime(2026, 5, 21, 10, 0, 0)  # no tzinfo
        aware_ts = datetime(2026, 5, 21, 10, 0, 0, tzinfo=timezone.utc)
        items = [
            _item(ts=naive_ts, sentiment=0.2),
            _item(ts=aware_ts, sentiment=0.4),
        ]
        result = bucket_sentiment_by_time(
            items,
            sentiment_field="sentiment_score",
            time_field="published_at",
            now=NOW,
        )
        bucket = next(
            b for b in result
            if b["date"] == datetime(2026, 5, 21, tzinfo=timezone.utc)
        )
        # Both items land in the same bucket; mean is (0.2 + 0.4) / 2.
        assert bucket["sample_count"] == 2
        assert bucket["avg_sentiment"] == pytest.approx(0.3)

    # Bonus: ISO string input for time_field should also work
    # (researcher/pulse evidence sometimes carries strings).
    def test_iso_string_in_time_field_is_parsed(self):
        """
        Items whose time_field is an ISO-8601 string parse correctly
        and land in the right bucket. Robustness check — Researcher
        and Pulse evidence items use ISO strings for published_at.
        """
        items = [
            _item(ts="2026-05-21T10:00:00+00:00", sentiment=0.4),
            _item(ts="2026-05-22T10:00:00Z",      sentiment=0.6),
        ]
        result = bucket_sentiment_by_time(
            items,
            sentiment_field="sentiment_score",
            time_field="published_at",
            now=NOW,
        )
        by_date = {b["date"]: b for b in result}
        b21 = by_date[datetime(2026, 5, 21, tzinfo=timezone.utc)]
        b22 = by_date[datetime(2026, 5, 22, tzinfo=timezone.utc)]
        assert b21["sample_count"] == 1 and b21["avg_sentiment"] == pytest.approx(0.4)
        assert b22["sample_count"] == 1 and b22["avg_sentiment"] == pytest.approx(0.6)
