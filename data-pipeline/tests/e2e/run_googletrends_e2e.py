"""
Sprint 8 End-to-End Integration Test — Google Trends Vertical Slice.

Runs the full Google Trends pipeline in standalone mode (without a Flink cluster
or Kafka broker):
  1. Fetches real Interest Over Time data from the Pytrends API for a single
     keyword ("Inflation") for the US geo, using TIMEFRAME_7D — the reactive
     mode fetch path.
  2. Builds Bronze envelopes and runs them through the Silver Job
     (process_googletrends_message)
  3. Runs Silver records through the Gold Job (process_googletrends_gold_message)
     with an in-process price history keyed by keyword — replicating Flink's
     per-key ValueState
  4. Inserts Gold records into momentum_vault (TimescaleDB hypertable)
  5. Post-run verification: fetch_latest() + fetch_time_series() spot-check

Why a single keyword:
  Google's unofficial Trends API rate-limits aggressively — consecutive requests
  risk 429 even with browser headers. One keyword with proper retry backoff
  is sufficient to validate the complete Bronze→Silver→Gold→vault pipeline.
  The producer's multi-keyword looping logic is exercised by Gate 2 tests.

Rate-limit mitigation strategy:
  1. Browser-like headers via requests_args (PYTRENDS_REQUESTS_ARGS) — reduces
     the probability that Google classifies the session as bot traffic.
  2. Exponential backoff on 429: wait 30s → 60s → 120s, up to 3 retries.
     Total worst-case wait: ~210s per keyword before giving up.

No Kafka broker required: GoogleTrendsProducer is used for its Pytrends session
  and _build_raw_payload() helper only. _emit() is never called.
No API key required: Pytrends wraps Google's unofficial web API via cookie-based
  auth (Section B.5).
No OpenAI required: all Google Trends Gold enrichment is deterministic math
  (momentum deltas + rule-based Public_Hype_Alert trigger).

What this confirms end-to-end:
  - Real Pytrends API connectivity + interest_over_time() response parsing
  - Bronze payload building (_build_raw_payload, real 0-100 scores)
  - Bronze → Silver transform + Silver schema validation
  - Silver → Gold transform + Public_Hype_Alert trigger evaluation
  - Real momentum block computation with running price history
  - momentum_vault.insert() accepting source_name="googletrends"
  - fetch_latest() and fetch_time_series() returning the inserted row

Usage (from data-pipeline/ with venv active):
    python -m tests.e2e.run_googletrends_e2e

References:
    - Section 2.3:  Scheduled pollers — Airflow DAGs manage production invocation
    - Section 4.1:  Silver Job specification
    - Section 4.2B: Momentum Block — keyed state deltas
    - Section 5.3:  Momentum Vault — TimescaleDB hypertable
    - Section 9.3:  Triple-Gate Test Matrix — E2E validation
    - Section B.5:  Google Trends technical parameters & Public_Hype_Alert trigger
"""

from __future__ import annotations

import copy
import logging
import sys
import time
from datetime import datetime, timezone

from pytrends.exceptions import TooManyRequestsError
from pytrends.request import TrendReq

from config.kafka_topics import DEAD_LETTER_QUEUE, SILVER_STRUCTURED_METRICS
from ingestion.googletrends_producer import (
    PYTRENDS_BASE_URL,
    PYTRENDS_HL,
    PYTRENDS_REQUESTS_ARGS,
    PYTRENDS_TZ,
    REQUEST_DELAY_SEC,
    SOURCE_NAME,
    TIMEFRAME_7D,
    GoogleTrendsProducer,
)
from persistence.momentum_vault import (
    fetch_latest,
    fetch_time_series,
    insert as mv_insert,
)
from processing.gold_job import process_googletrends_gold_message
from processing.silver_job import process_googletrends_message
from utils.db import get_cursor
from utils.kafka_utils import build_bronze_message

# ==========================================================
# Runner parameters
# ==========================================================

# Single keyword — sufficient to validate the full pipeline end-to-end.
# "Inflation" is reliably indexed by Google Trends at all times.
E2E_KEYWORD: str = "Inflation"
E2E_GEO: str = "US"

# Retry waits in seconds after consecutive 429 responses (exponential backoff).
# Attempt 0: immediate. Attempt 1: wait 30s. Attempt 2: wait 60s. Attempt 3: wait 120s.
_RETRY_WAIT_SECS: list[int] = [30, 60, 120]


# ==========================================================
# Helpers
# ==========================================================

def _check_prerequisites() -> None:
    """Fail fast if PostgreSQL is unreachable."""
    try:
        with get_cursor() as cur:
            cur.execute("SELECT 1;")
    except Exception as exc:
        print(
            f"\n[e2e] ERROR: PostgreSQL not reachable: {exc}\n"
            "Start the stack: docker compose -f infrastructure/docker-compose.yml up -d postgres\n"
        )
        sys.exit(1)


def _make_producer_helper() -> GoogleTrendsProducer:
    """
    Create a GoogleTrendsProducer with a live Pytrends session but NO Kafka producer.

    Browser-like headers are passed via requests_args (PYTRENDS_REQUESTS_ARGS) to
    reduce the likelihood of Google classifying the session as bot traffic.
    deep-copy is required because pytrends.__init__ pops 'headers' from the dict
    in-place — without copying, subsequent instantiations would have no headers.
    """
    producer = GoogleTrendsProducer.__new__(GoogleTrendsProducer)
    producer._pytrends = TrendReq(
        hl=PYTRENDS_HL,
        tz=PYTRENDS_TZ,
        timeout=(10, 30),
        requests_args=copy.deepcopy(PYTRENDS_REQUESTS_ARGS),
    )
    producer._emitted = 0
    return producer


def _fetch_interest_over_time(producer, keyword, geo, timeframe, logger):
    """
    Fetch interest_over_time() with exponential backoff on 429.

    Returns the DataFrame on success, or None if all retries are exhausted.
    Attempts: immediate, then +30s, +60s, +120s waits between each retry.
    """
    for attempt, wait_s in enumerate([0] + _RETRY_WAIT_SECS):
        if wait_s > 0:
            logger.warning(
                "  [%s] 429 rate-limit — waiting %ds before retry %d/%d...",
                keyword, wait_s, attempt, len(_RETRY_WAIT_SECS),
            )
            time.sleep(wait_s)

        try:
            producer._pytrends.build_payload(
                kw_list=[keyword],
                timeframe=timeframe,
                geo=geo,
            )
            df = producer._pytrends.interest_over_time()
            logger.info("  [%s] interest_over_time() succeeded on attempt %d", keyword, attempt + 1)
            return df
        except TooManyRequestsError:
            if attempt == len(_RETRY_WAIT_SECS):
                logger.error(
                    "  [%s] 429 persists after %d retries — "
                    "Google rate-limit not resolved in this session.",
                    keyword, len(_RETRY_WAIT_SECS),
                )
                return None
            # else: loop continues with next wait
        except Exception as exc:
            logger.error(
                "  [%s] Unexpected API error (%s: %s) — aborting.",
                keyword, type(exc).__name__, exc,
            )
            return None

    return None


# ==========================================================
# Main runner
# ==========================================================

def run() -> dict:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        stream=sys.stdout,
    )
    logger = logging.getLogger("e2e_googletrends")

    logger.info("=== Anizai Sprint 8 — Google Trends E2E Integration Test ===")
    logger.info(
        "Keyword: %s  |  Geo: %s  |  Timeframe: %s  |  Max retries on 429: %d",
        E2E_KEYWORD, E2E_GEO, TIMEFRAME_7D, len(_RETRY_WAIT_SECS),
    )

    _check_prerequisites()
    logger.info("Prerequisites verified: PostgreSQL reachable.")

    producer = _make_producer_helper()
    logger.info("Pytrends session initialised with browser-like headers.")

    counts: dict = {
        "keywords_fetched":         0,
        "bronze_sent":              0,
        "silver_ok":                0,
        "silver_dlq":               0,
        "momentum_vault_new":       0,
        "public_hype_alerts_fired": 0,
        "errors":                   0,
    }

    # In-process price history for momentum computation (replicates Flink keyed state)
    price_history: list[tuple[str, float]] = []

    fetch_ts = datetime.now(timezone.utc).isoformat()
    start_ts = time.time()

    # ── Fetch ────────────────────────────────────────────────────────────────
    logger.info("")
    logger.info("── Fetching: %s (%s, %s) ──────────────────────────────────",
                E2E_KEYWORD, E2E_GEO, TIMEFRAME_7D)

    df = _fetch_interest_over_time(
        producer, E2E_KEYWORD, E2E_GEO, TIMEFRAME_7D, logger,
    )

    if df is None or df.empty:
        logger.error(
            "  interest_over_time() returned no data — "
            "keyword may have no activity or all retries failed."
        )
        counts["errors"] += 1
    elif E2E_KEYWORD not in df.columns:
        logger.error(
            "  Keyword column '%s' not found in result — columns: %s",
            E2E_KEYWORD, list(df.columns),
        )
        counts["errors"] += 1
    else:
        kw_col = df[E2E_KEYWORD]
        counts["keywords_fetched"] += 1
        logger.info("  Fetched %d data points for '%s'", len(kw_col), E2E_KEYWORD)

        # ── Per-observation loop: Bronze → Silver → Gold → vault ─────────────
        for date_idx, score_val in kw_col.items():
            obs_date = date_idx.strftime("%Y-%m-%d")
            score    = float(score_val)

            raw = producer._build_raw_payload(
                keyword=E2E_KEYWORD,
                geo=E2E_GEO,
                interest_score=score,
                fetch_timestamp=fetch_ts,
                mode="reactive",
                observation_date=obs_date,
                timeframe=TIMEFRAME_7D,
            )
            source_endpoint = (
                f"{PYTRENDS_BASE_URL}?geo={E2E_GEO}&q={E2E_KEYWORD}&mode=reactive"
            )
            envelope = build_bronze_message(SOURCE_NAME, source_endpoint, raw)
            counts["bronze_sent"] += 1

            # Silver
            topic, silver = process_googletrends_message(envelope)

            if topic == DEAD_LETTER_QUEUE:
                counts["silver_dlq"] += 1
                logger.warning(
                    "  → DLQ  date=%s  errors=%s",
                    obs_date, silver.get("validation_errors", []),
                )
                continue

            if topic != SILVER_STRUCTURED_METRICS:
                counts["errors"] += 1
                logger.error("  → Unexpected Silver topic: %s  date=%s", topic, obs_date)
                continue

            counts["silver_ok"] += 1

            # Gold
            _, gold = process_googletrends_gold_message(silver, price_history)

            hype_alert = gold.get("metadata_extension", {}).get("public_hype_alert", False)
            abs_pts    = gold.get("metadata_extension", {}).get("abs_change_24h_pts", 0.0)
            if hype_alert:
                counts["public_hype_alerts_fired"] += 1

            alert_tag = f"  ⚠ PUBLIC_HYPE_ALERT (+{abs_pts:.1f}pts)" if hype_alert else ""
            print(
                f"  {E2E_KEYWORD:<22} date={obs_date}  score={score:>5.1f}  "
                f"silver=OK  hype={str(hype_alert):<5}{alert_tag}"
            )

            # Vault
            try:
                mv_insert(gold)
                counts["momentum_vault_new"] += 1
            except Exception as exc:
                counts["errors"] += 1
                logger.error("  → momentum_vault.insert FAILED  date=%s: %s", obs_date, exc)
                continue

            # Update running price history (oldest-first, mirrors Flink state)
            ts  = silver.get("data_point", {}).get("timestamp_utc", "")
            val = float(silver.get("data_point", {}).get("current_value", 0.0))
            price_history.append((ts, val))

    elapsed = time.time() - start_ts

    # ── Post-run verification ─────────────────────────────────────────────────
    logger.info("")
    logger.info("=== Post-Run Verification ===")
    verification_errors = 0

    if counts["momentum_vault_new"] > 0:
        # fetch_latest
        logger.info("  fetch_latest('%s', '%s'):", SOURCE_NAME, E2E_KEYWORD)
        row = fetch_latest(SOURCE_NAME, E2E_KEYWORD)
        if row is None:
            logger.error("  ✗ fetch_latest returned None — row was not inserted")
            verification_errors += 1
        else:
            ext = row.get("metadata_extension", {})
            logger.info(
                "  ✓ value=%.1f  change_24h=%+.4f  is_new_market=%s  "
                "keyword=%s  geo=%s  mode=%s  hype=%s",
                row["current_value"],
                row["change_24h"],
                row["is_new_market"],
                ext.get("keyword"),
                ext.get("geo"),
                ext.get("mode"),
                ext.get("public_hype_alert", False),
            )

        # fetch_time_series
        logger.info("")
        logger.info("  fetch_time_series('%s', '%s', hours=168):", SOURCE_NAME, E2E_KEYWORD)
        ts_rows = fetch_time_series(SOURCE_NAME, E2E_KEYWORD, hours=7 * 24)
        if len(ts_rows) == 0:
            logger.error("  ✗ fetch_time_series returned 0 rows")
            verification_errors += 1
        else:
            logger.info(
                "  ✓ %d rows  oldest=%s  newest=%s",
                len(ts_rows),
                ts_rows[0]["timestamp_utc"],
                ts_rows[-1]["timestamp_utc"],
            )
    else:
        logger.warning(
            "  Skipping DB verification — no records were inserted "
            "(API fetch failed or all observations went to DLQ)."
        )
        if counts["errors"] > 0:
            verification_errors += 1

    # Public_Hype_Alert summary
    logger.info("")
    if counts["public_hype_alerts_fired"] > 0:
        logger.info(
            "  Public_Hype_Alert fired %d time(s) — "
            "cross-reference with Social Pulse to validate event scale (Section B.5).",
            counts["public_hype_alerts_fired"],
        )
    else:
        logger.info(
            "  No Public_Hype_Alert fired "
            "(all 24h score changes <= 50 pts — within normal range)."
        )

    # ── Final summary table ───────────────────────────────────────────────────
    print("\n" + "=" * 64)
    print(f"  Sprint 8 E2E — Google Trends — COMPLETE  ({elapsed:.1f}s)")
    print("=" * 64)
    col_w = 30
    for key, val in counts.items():
        print(f"  {key:<{col_w}} {val}")
    print(f"  {'verification_errors':<{col_w}} {verification_errors}")
    print("=" * 64)

    total_errors = counts["errors"] + counts["silver_dlq"] + verification_errors
    if total_errors > 0:
        print(f"\n  ⚠  {total_errors} error(s) above — check logs for details.")
    else:
        print(
            "\n  All observations processed and verified without errors.\n"
            "  Google Trends vertical slice end-to-end pipeline validated."
        )

    return counts


if __name__ == "__main__":
    run()
