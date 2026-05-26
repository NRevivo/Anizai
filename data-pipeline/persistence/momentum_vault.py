"""
Momentum Vault — Gold Time-Series Hypertable (Section 5.3).

Persists Gold Structured Metric records into the `momentum_vault`
TimescaleDB hypertable, partitioned by timestamp_utc with 1-day chunks.

Sources written here: Polymarket prices, FRED indicators, PredictIt,
OpenWeather readings, OpenSky density counts, Google Trends scores.

The `momentum_block` (change_24h/7d/30d) is pre-computed by the Flink
Gold Job before insert, so the RAG agent can answer "What is the trend?"
without runtime math (Section 4.2B, 5.3).

Idempotency: ON CONFLICT (metric_id, timestamp_utc) DO NOTHING ensures
Flink exactly-once re-deliveries never produce duplicate rows.

Public interface:
    insert(gold_metric)                             → metric_id (str)
    fetch_latest(source_name, external_ref)         → dict | None
    fetch_time_series(source_name, ext_ref, hours)  → list[dict]
    find_polymarket_market_by_question(q, thr=0.85) → dict | None

References:
    - Section 5.3:  Momentum Vault — TimescaleDB hypertable
    - Section 4.2B: Momentum Block — Flink keyed state deltas
    - Section C.2:  Silver/Gold Structured Metric schema (input format)
    - Section 3.2:  DRY — all DB logic in dedicated persistence modules
    - Section 3.3:  Service Isolation — only this module writes to momentum_vault
    - Section B.3:  FRED automation triggers (T10Y2Y, VIXCLS thresholds)
    - Section B.8:  Polymarket whale_alert in metadata_extension
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from psycopg2.extras import Json, RealDictCursor

from utils.db import get_cursor
from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)
setup_logging()


# ==========================================================
# Insert
# ==========================================================

def insert(gold_metric: dict) -> str:
    """
    Insert one Gold Structured Metric record into `momentum_vault`.

    Idempotent: ON CONFLICT (metric_id, timestamp_utc) DO NOTHING ensures
    Flink restart re-deliveries and pipeline replays never create duplicates.

    Maps the nested Gold Structured Metric schema (Section C.2) to flat
    table columns. The full metadata_extension JSONB is stored verbatim for
    source-specific fields (FRED series_id, Polymarket whale_alert, etc.).

    Args:
        gold_metric: Gold Structured Metric dict from the Gold Job.
                     Must conform to Section C.2 with layer="Gold" and
                     a populated momentum_block.
                     Required paths:
                       core_identity.{metric_id, canonical_event_id,
                                      source_name, external_reference_id}
                       data_point.{current_value, unit, timestamp_utc}
                       momentum_block.{change_24h, change_7d, change_30d,
                                       is_new_market}

    Returns:
        metric_id: UUID string of the inserted (or pre-existing) row.

    Raises:
        ValueError: If the record is structurally invalid before hitting the DB.
        psycopg2.Error: On unexpected database errors.
    """
    _validate_record(gold_metric)

    core      = gold_metric["core_identity"]
    dp        = gold_metric["data_point"]
    mb        = gold_metric["momentum_block"]
    meta_ext  = gold_metric.get("metadata_extension", {})

    # Use the Silver-generated metric_id for idempotency. If absent (shouldn't
    # happen at Gold), generate a new UUID so the insert never hard-fails.
    metric_id = core.get("metric_id") or str(uuid.uuid4())

    # Parse timestamp_utc — stored as TIMESTAMPTZ in the hypertable
    timestamp_utc = _parse_timestamp(dp.get("timestamp_utc", ""))
    if timestamp_utc is None:
        # Fall back to now() — never store a NULL for the partitioning key
        timestamp_utc = datetime.now(timezone.utc)
        logger.warning(
            "[momentum_vault] timestamp_utc missing or unparseable for metric_id=%s — using NOW()",
            metric_id,
        )

    sql = """
        INSERT INTO momentum_vault (
            metric_id,
            canonical_event_id,
            source_name,
            external_reference_id,
            current_value,
            unit,
            status,
            timestamp_utc,
            change_24h,
            change_7d,
            change_30d,
            is_new_market,
            metadata_extension
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s
        )
        ON CONFLICT (metric_id, timestamp_utc) DO NOTHING
        RETURNING metric_id::text;
    """
    params = (
        metric_id,
        core["canonical_event_id"],
        core["source_name"],
        core["external_reference_id"],
        float(dp["current_value"]),
        dp["unit"],
        dp.get("status"),                   # nullable
        timestamp_utc,
        float(mb.get("change_24h", 0.0)),
        float(mb.get("change_7d",  0.0)),
        float(mb.get("change_30d", 0.0)),
        bool(mb.get("is_new_market", False)),
        Json(meta_ext),
    )

    with get_cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()

    if row is None:
        logger.debug(
            "[momentum_vault] Skipped duplicate (metric_id=%s, timestamp_utc=%s)",
            metric_id, timestamp_utc,
        )
    else:
        logger.debug(
            "[momentum_vault] Inserted metric_id=%s source=%s",
            metric_id, core["source_name"],
        )

    return metric_id


# ==========================================================
# Fetch Latest
# ==========================================================

def fetch_latest(
    source_name: str,
    external_reference_id: str,
) -> Optional[dict]:
    """
    Retrieve the most recent observation for a specific asset/indicator.

    Used by the RAG agent to answer "What is the current value?" and
    "What is the trend?" questions without a full time-series scan.
    The idx_mv_source_ref index (source_name, external_reference_id,
    timestamp_utc DESC) makes this a fast index seek.

    Args:
        source_name:           Source identifier ('polymarket', 'fred', etc.).
        external_reference_id: Asset/indicator ID (e.g., 'FEDFUNDS', market slug).

    Returns:
        The most recent row as a dict, or None if no rows exist.
    """
    sql = """
        SELECT
            metric_id::text,
            canonical_event_id,
            source_name,
            external_reference_id,
            current_value,
            unit,
            status,
            timestamp_utc,
            change_24h,
            change_7d,
            change_30d,
            is_new_market,
            metadata_extension,
            ingested_at
        FROM momentum_vault
        WHERE source_name            = %s
          AND external_reference_id  = %s
        ORDER BY timestamp_utc DESC
        LIMIT 1;
    """
    with get_cursor() as cur:
        cur.execute(sql, (source_name, external_reference_id))
        row = cur.fetchone()
    return dict(row) if row else None


# ==========================================================
# Fetch Time Series
# ==========================================================

def fetch_time_series(
    source_name: str,
    external_reference_id: str,
    hours: int = 24,
) -> list[dict]:
    """
    Retrieve a time-series window of observations for an asset.

    Returns observations within the last `hours` hours, ordered
    chronologically (oldest first). Used by the Pulse Analyst to
    display trend charts and compute contextual delta explanations.

    TimescaleDB's chunk-exclusion mechanism (automatically enabled for
    TIMESTAMPTZ range predicates) makes this query efficient even over
    large hypertables — only the relevant day-partitions are scanned.

    Args:
        source_name:           Source identifier.
        external_reference_id: Asset/indicator ID.
        hours:                 Lookback window in hours (default 24).

    Returns:
        List of row dicts ordered by timestamp_utc ASC.
        Empty list if no data found in the window.
    """
    sql = """
        SELECT
            metric_id::text,
            canonical_event_id,
            source_name,
            external_reference_id,
            current_value,
            unit,
            timestamp_utc,
            change_24h,
            change_7d,
            change_30d,
            is_new_market,
            metadata_extension
        FROM momentum_vault
        WHERE source_name            = %s
          AND external_reference_id  = %s
          AND timestamp_utc         >= NOW() - (%s * INTERVAL '1 hour')
        ORDER BY timestamp_utc ASC;
    """
    with get_cursor() as cur:
        cur.execute(sql, (source_name, external_reference_id, hours))
        return [dict(row) for row in cur.fetchall()]


# ==========================================================
# FRED-Specific Fetch Helpers
# ==========================================================

def fetch_fred_series(
    series_id: str,
    days: int = 365,
) -> list[dict]:
    """
    Retrieve a date-range window of FRED observations for one indicator.

    Convenience wrapper over fetch_time_series() using days instead of hours
    — appropriate for FRED's daily/monthly cadence. Returns observations in
    chronological order (oldest first) so callers can plot trend charts or
    feed the list into compute_momentum_block() directly.

    Why days not hours: FRED's fastest-updating series (FEDFUNDS, VIXCLS,
    DCOILWTICO) publish once per trading day. Expressing the window in hours
    adds no precision and makes call sites harder to read.

    Args:
        series_id: FRED series identifier (e.g., "FEDFUNDS", "T10Y2Y").
                   Maps to external_reference_id in the momentum_vault table.
        days:      Lookback window in calendar days (default 365 = 1 year).

    Returns:
        List of row dicts ordered by timestamp_utc ASC.
        Empty list if no data found for this series in the window.
    """
    return fetch_time_series(
        source_name="fred",
        external_reference_id=series_id,
        hours=days * 24,
    )


def fetch_fred_anomalies(days: int = 7) -> list[dict]:
    """
    Retrieve recent FRED observations where an automation trigger fired.

    Queries for rows where metadata_extension.anomaly_flags is non-empty.
    Used by the Market Bridge agent (Section 6.2) to surface active
    anomaly alerts (yield curve inversions, VIX spikes, commodity swings)
    without scanning the full time-series.

    Args:
        days: Lookback window in calendar days (default 7).

    Returns:
        List of row dicts ordered by timestamp_utc DESC (most recent first).
        Empty list if no anomalies were detected in the window.
    """
    sql = """
        SELECT
            metric_id::text,
            canonical_event_id,
            source_name,
            external_reference_id,
            current_value,
            unit,
            timestamp_utc,
            change_24h,
            change_7d,
            change_30d,
            is_new_market,
            metadata_extension,
            ingested_at
        FROM momentum_vault
        WHERE source_name        = 'fred'
          AND timestamp_utc     >= NOW() - (%s * INTERVAL '1 day')
          AND metadata_extension->>'anomaly_flags' IS NOT NULL
          AND metadata_extension->>'anomaly_flags' != '[]'
        ORDER BY timestamp_utc DESC;
    """
    with get_cursor() as cur:
        cur.execute(sql, (days,))
        return [dict(row) for row in cur.fetchall()]


# ==========================================================
# Polymarket Question Resolver (Sprint 22 T22.1)
# ==========================================================

POLYMARKET_FUZZY_MATCH_DEFAULT_THRESHOLD = 0.85


def find_polymarket_market_by_question(
    question_text: str,
    threshold: float = POLYMARKET_FUZZY_MATCH_DEFAULT_THRESHOLD,
) -> Optional[dict]:
    """
    Resolve a user's free-text question to the most-recent matching
    Polymarket market row via pg_trgm trigram similarity.

    Closes the resolver portion of KG-PHASE8-12: before Sprint 22 the
    agent had no path from `raw_question` → polymarket market, so every
    Tier 1 forecast ran as effective Tier 2 (empty MarketComparison BI
    card on the frontend). This function gives the Market Bridge agent
    that path.

    Why pg_trgm (not vector similarity) for V1:
        Revised Phase 8 plan §Future Enhancement 4 — V1 question patterns
        are users asking verbatim or near-verbatim from Polymarket; the
        90% case is reachable with trigram similarity at 20% of the
        complexity. Vector index + clarification on multi-match is a
        clean upgrade later.

    Why 0.85 default threshold:
        The threshold is **paraphrase-defense**, not punctuation-defense.
        pg_trgm normalizes aggressively before computing similarity —
        lowercase + strip non-alphanumeric + unordered trigram set — so
        case, punctuation, whitespace, and word order all yield
        similarity = 1.0 automatically. Empirical measurement on the
        dev DB (Sprint 22 T22.1):
          - Trivial variations (case/punctuation/word order): ~1.00
          - Moderate paraphrase (synonyms, slight rewording): ~0.50–0.55
          - Heavy paraphrase (full restructure): <0.30
        0.85 sits well above the moderate-paraphrase band and well below
        the trivial-variation ceiling — accepts user phrasings of the
        same question, rejects paraphrases that may shift semantics.
        Caller-overridable for future tuning per the revised plan
        §Sprint 22 Confirmed design decisions.

    Why ORDER BY match_score DESC, timestamp_utc DESC:
        The same Polymarket market produces many momentum_vault rows
        (one per price observation). All rows from the same market
        carry the same `question` text in metadata_extension (added by
        Sprint 22 T22.1 silver_job patch), so match_score is tied across
        them; timestamp_utc DESC then picks the most-recent observation
        — what the Market Bridge agent wants when reading `current_odds`.

    Why filter on `metadata_extension ? 'question'`:
        Not every momentum_vault row carries the question key — FRED,
        googletrends, openweather rows don't, and pre-Sprint-22
        Polymarket rows (before the silver_job patch landed) also don't.
        The JSONB key-exists operator drops those without computing
        similarity against NULL/missing values.

    Why empty/whitespace guard at the top:
        `similarity('', '...')` returns 0.0 which would never cross the
        0.85 threshold anyway, but the guard avoids the round-trip and
        makes the intent explicit at the call site.

    Note on indexing:
        No GIN/GiST trigram index on `metadata_extension->>'question'`
        today. At current Polymarket vault volume (hundreds to low
        thousands of rows) the sequential scan is acceptable. Adding the
        index belongs with Future Enhancement 4 (vector index work).

    Args:
        question_text: User's raw question. Empty/whitespace → returns
                       None without touching the DB.
        threshold:     Minimum similarity (0.0-1.0). Default 0.85.
                       Rows scoring below this are excluded.

    Returns:
        Most-recent matching row as a dict (same shape as fetch_latest)
        with one added field `match_score: float` in [threshold, 1.0].
        None when no row passes the threshold.

    Spec references:
        - data-pipeline/docs/agentic_hub_implementation_phase8_revised.md
          §Sprint 22 T22.1 + §Future Enhancement 4
        - data-pipeline/docs/agentic_hub_spec.md §8.4.3 (revised)
        - task_plan.md Known Gaps KG-PHASE8-12
    """
    if not question_text or not question_text.strip():
        return None

    sql = """
        SELECT
            metric_id::text,
            canonical_event_id,
            source_name,
            external_reference_id,
            current_value,
            unit,
            status,
            timestamp_utc,
            change_24h,
            change_7d,
            change_30d,
            is_new_market,
            metadata_extension,
            ingested_at,
            similarity(metadata_extension->>'question', %s) AS match_score
        FROM momentum_vault
        WHERE source_name = 'polymarket'
          AND metadata_extension ? 'question'
          AND similarity(metadata_extension->>'question', %s) >= %s
        ORDER BY match_score DESC, timestamp_utc DESC
        LIMIT 1;
    """
    with get_cursor() as cur:
        cur.execute(sql, (question_text, question_text, threshold))
        row = cur.fetchone()
    return dict(row) if row else None


# ==========================================================
# Internal helpers
# ==========================================================

def _validate_record(gold_metric: dict) -> None:
    """
    Lightweight pre-insert structural check (last-resort guard).

    The Gold Job runs validate_silver_structured_metric() before calling
    insert(). This guard catches programming errors such as passing the
    wrong dict, producing a clearer error than a psycopg2 NOT NULL violation.
    """
    if not isinstance(gold_metric, dict):
        raise ValueError(
            f"[momentum_vault.insert] Record must be a dict, "
            f"got {type(gold_metric).__name__}."
        )
    for path in (
        ("core_identity",),
        ("data_point",),
        ("momentum_block",),
        ("core_identity", "canonical_event_id"),
        ("core_identity", "source_name"),
        ("core_identity", "external_reference_id"),
        ("data_point", "current_value"),
        ("data_point", "unit"),
    ):
        obj = gold_metric
        for key in path:
            if not isinstance(obj, dict) or key not in obj:
                raise ValueError(
                    f"[momentum_vault.insert] Missing required field: "
                    f"'{'.'.join(path)}'"
                )
            obj = obj[key]


def _parse_timestamp(ts: str) -> Optional[datetime]:
    """Parse an ISO8601 string to a timezone-aware datetime, or return None."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        logger.warning(
            "[momentum_vault] Could not parse timestamp: %r — falling back to NOW()", ts
        )
        return None
