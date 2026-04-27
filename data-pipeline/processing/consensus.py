"""
Temporal Consensus Bundling — Social Signal Aggregation (Section 4.2A).

Groups Polymarket social comments into 4-hour temporal blocks.
GPT-4o summarizes each group into a single Consensus Vector instead
of individual message embeddings, preventing Vector DB bloat.

Public API:
    bucket_key(ts: str) -> str
        Floor an ISO 8601 timestamp to a 4-hour UTC bucket key.
        Returns "YYYY-MM-DD_HH" (HH = 00, 04, 08, 12, 16, or 20).

    group_by_window(records: list, ts_field: str) -> dict
        Partition a list of dicts by their 4-hour bucket key.
        Returns {bucket_key: [records]} — empty input returns {}.

Why 4-hour windows (Section 4.2A):
    Polymarket / HackerNews social signals arrive continuously. Embedding each
    message individually inflates the Vector DB with highly correlated vectors.
    A 4-hour window captures a coherent conversation cycle (a news event lands,
    commentary accumulates, then the cycle resets). GPT-4o summarises the window
    into one Consensus Vector that represents the collective sentiment — six
    vectors per day instead of hundreds of individual embeddings.

References:
    - Section 4.2A: Temporal Consensus Bundling
    - Section C.6: Gold Social Pulse Schema (market_consensus entry_type)
    - Section B.8: Polymarket Consensus Vector spec
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# 4-hour temporal window — consensus aggregation interval (Section 4.2A).
# Bucket boundaries (UTC): 00:00, 04:00, 08:00, 12:00, 16:00, 20:00.
_BUCKET_HOURS = 4


def bucket_key(ts: str) -> str:
    """
    Floor an ISO 8601 timestamp to a 4-hour UTC bucket key (Section 4.2A).

    The 4-hour window is the consensus aggregation interval: all social signals
    within the same bucket are bundled into one GPT-4o consensus call instead
    of individual embeddings, preventing Vector DB bloat (Section B.8).

    Bucket boundaries (UTC): 00:00, 04:00, 08:00, 12:00, 16:00, 20:00.

    Args:
        ts: ISO 8601 timestamp string. Accepted formats:
            "2024-09-23T09:37:00Z"
            "2024-09-23 09:37:00+00:00"
            "2024-09-23T09:37:00.123456Z"
            "2024-09-23T09:37:00"   (assumed UTC if tz-naive)

    Returns:
        Bucket key string "YYYY-MM-DD_HH" where HH is the floored 4-hour
        boundary in UTC (e.g. "2024-09-23_08" for any time 08:00–11:59 UTC).

    Raises:
        ValueError: If ts cannot be parsed as an ISO 8601 timestamp.
    """
    # datetime.fromisoformat does not handle the trailing 'Z' before Python 3.11.
    # Replace it with the explicit UTC offset so parsing is version-safe.
    normalized = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)

    # Treat tz-naive timestamps as UTC (producer timestamps are always UTC).
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    # Floor to 4-hour boundary: hours 0-3 → 00, 4-7 → 04, 8-11 → 08, ...
    floored_hour = (dt.hour // _BUCKET_HOURS) * _BUCKET_HOURS
    return f"{dt.strftime('%Y-%m-%d')}_{floored_hour:02d}"


def group_by_window(
    records: list[dict[str, Any]],
    ts_field: str,
) -> dict[str, list[dict[str, Any]]]:
    """
    Partition records into 4-hour temporal buckets (Section 4.2A).

    Used by the Gold job consensus path to group Polymarket/HackerNews social
    signals before passing each group to call_openai_consensus(). Each group
    produces one Consensus Vector in social_vectors — six potential groups per
    source per day instead of one embedding per message.

    Records with a missing or unparseable timestamp are logged at WARNING level
    and skipped. This is non-fatal: a single malformed record must not block
    bundling of the remaining records in the same window (Section 4.3).

    Args:
        records:  List of dicts, each containing at minimum ts_field.
        ts_field: Key name in each dict that holds the ISO 8601 timestamp.
                  Common values: "created_at", "producer_timestamp", "event_time".

    Returns:
        Dict mapping bucket_key → list of records in that bucket.
        Keys are "YYYY-MM-DD_HH" strings (4-hour UTC boundaries).
        Returns {} for empty input or if all records have invalid timestamps.
    """
    groups: dict[str, list[dict[str, Any]]] = {}

    for record in records:
        ts = record.get(ts_field)
        if not ts:
            logger.warning(
                "[consensus] record missing ts_field '%s' — skipped. record keys: %s",
                ts_field, list(record.keys()),
            )
            continue

        try:
            key = bucket_key(str(ts))
        except (ValueError, AttributeError) as exc:
            logger.warning(
                "[consensus] unparseable timestamp '%s' for ts_field '%s' — skipped. Error: %s",
                ts, ts_field, exc,
            )
            continue

        groups.setdefault(key, []).append(record)

    return groups
