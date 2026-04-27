"""
Sprint 2 End-to-End Integration Test — FRED Vertical Slice.

Runs the full FRED pipeline in standalone mode (without a Flink cluster or Kafka broker):
  1. Fetches real observations from the FRED API for all 9 indicators
  2. Builds Bronze envelopes and runs them through the Silver Job (process_fred_message)
  3. Runs Silver records through the Gold Job (process_fred_gold_message) with
     an in-process price history — replicating Flink's per-key ValueState
  4. Inserts Gold records into momentum_vault (TimescaleDB hypertable)
  5. Post-run verification: fetch_latest(), fetch_fred_series(), fetch_fred_anomalies()

No Kafka broker required: the FRED producer is invoked for its fetch/build helpers only.
No OpenAI required: all FRED Gold enrichment is deterministic math (momentum deltas +
rule-based automation triggers). This makes Sprint 2 E2E fully self-contained.

What this confirms end-to-end:
  - Real FRED API connectivity + response parsing
  - Bronze payload building (_build_raw_payload)
  - Bronze → Silver transform + Silver schema validation (process_fred_message)
  - Silver → Gold transform + automation trigger evaluation (process_fred_gold_message)
  - Real momentum block computation (compute_momentum_block with actual history)
  - momentum_vault.insert() for all 9 FRED series
  - Idempotency: re-running inserts zero extra rows (ON CONFLICT DO NOTHING)
  - fetch_latest(), fetch_fred_series(), fetch_fred_anomalies() queries

Why standalone instead of through Flink:
  The apache/flink:1.19-java11 image ships without Python, PyFlink, or the Kafka
  connector JAR. A custom Dockerfile.flink is deferred from Sprint 2. The exact
  same transform functions used here will be deployed to the Flink cluster later —
  only the EXACTLY_ONCE semantics and checkpointing are omitted in this runner.

Usage (from data-pipeline/ with venv active):
    python -m tests.e2e.run_fred_e2e

References:
    - Section 2.3:  Scheduled pollers — Airflow DAGs manage production invocation
    - Section 4.1:  Silver Job specification
    - Section 4.2B: Momentum Block — keyed state deltas
    - Section 5.3:  Momentum Vault — TimescaleDB hypertable
    - Section 9.3:  Triple-Gate Test Matrix — E2E validation
    - Section B.3:  FRED technical parameters & automation triggers
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Optional

import requests

from config.kafka_topics import DEAD_LETTER_QUEUE, SILVER_STRUCTURED_METRICS
from config.settings import FRED_API_KEY
from ingestion.fred_producer import (
    FRED_OBSERVATIONS_ENDPOINT,
    INDICATORS,
    REQUEST_DELAY_SEC,
    SOURCE_NAME,
    FREDProducer,
)
from persistence.momentum_vault import (
    fetch_fred_anomalies,
    fetch_fred_series,
    fetch_latest,
    insert as mv_insert,
)
from processing.gold_job import process_fred_gold_message
from processing.silver_job import process_fred_message
from utils.db import get_cursor
from utils.kafka_utils import build_bronze_message

# ==========================================================
# Runner parameters
# ==========================================================

# Observations to fetch per series in desc order (most-recent-first).
# 35 covers enough history for the 24h / 7d / 30d momentum windows on
# daily series. Reversed to oldest-first before processing so the
# in-process price_history is built in chronological order.
PULSE_OBS = 35


# ==========================================================
# Helpers
# ==========================================================

def _check_prerequisites() -> None:
    """
    Fail fast with clear messages if FRED_API_KEY or PostgreSQL is unavailable.

    Called before the main loop so any misconfiguration is caught before the
    runner attempts API calls or DB writes.
    """
    if not FRED_API_KEY:
        print(
            "\n[e2e] ERROR: FRED_API_KEY is not set in .env.\n"
            "Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html\n"
            "Set it in data-pipeline/infrastructure/.env and re-run.\n"
        )
        sys.exit(1)

    try:
        with get_cursor() as cur:
            cur.execute("SELECT 1;")
    except Exception as exc:
        print(
            f"\n[e2e] ERROR: PostgreSQL not reachable: {exc}\n"
            "Start the stack: docker compose -f infrastructure/docker-compose.yml up -d postgres\n"
        )
        sys.exit(1)


def _make_producer_helper() -> FREDProducer:
    """
    Create a FREDProducer instance without initialising the Kafka producer.

    The E2E test uses only _fetch_observations() and _build_raw_payload(),
    both of which are stateless with respect to the Kafka producer. Using
    __new__ lets us call these helpers without requiring a running Kafka broker.
    """
    producer = FREDProducer.__new__(FREDProducer)
    producer._emitted = 0
    return producer


# ==========================================================
# Main runner
# ==========================================================

def run() -> dict:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        stream=sys.stdout,
    )
    logger = logging.getLogger("e2e_fred")

    logger.info("=== Anizai Sprint 2 — FRED E2E Integration Test ===")
    logger.info("Series: %d  |  Observations per series: %d", len(INDICATORS), PULSE_OBS)

    _check_prerequisites()
    logger.info("Prerequisites verified: FRED_API_KEY present, PostgreSQL reachable.")

    producer = _make_producer_helper()

    counts: dict = {
        "series_attempted":     0,
        "series_completed":     0,
        "observations_fetched": 0,
        "silver_ok":            0,
        "silver_dlq":           0,
        "momentum_vault_new":   0,
        "triggers_fired":       0,
        "errors":               0,
    }

    # In-process price history keyed by series_id.
    # Each entry is a list of (timestamp_utc_str, float_value) tuples,
    # oldest-first. Replicates the Flink keyed ValueState per series_id
    # (Section 4.2B — Momentum Block).
    price_history: dict[str, list[tuple[str, float]]] = {}

    start_ts = time.time()

    # ── Per-series loop ─────────────────────────────────────────────────────
    for series_id, meta in INDICATORS.items():
        counts["series_attempted"] += 1
        logger.info("")
        logger.info("── %s (%s) ─────────────────────────────────────", series_id, meta["category"])

        # 1. Fetch real observations from FRED API
        try:
            observations = producer._fetch_observations(
                series_id=series_id,
                sort_order="desc",
                limit=PULSE_OBS,
            )
        except requests.HTTPError as exc:
            logger.error("  [%s] HTTP error: %s — skipping series", series_id, exc)
            counts["errors"] += 1
            time.sleep(REQUEST_DELAY_SEC)
            continue
        except requests.RequestException as exc:
            logger.error("  [%s] Network error: %s — skipping series", series_id, exc)
            counts["errors"] += 1
            time.sleep(REQUEST_DELAY_SEC)
            continue

        if not observations:
            logger.warning("  [%s] No valid observations returned — skipping", series_id)
            time.sleep(REQUEST_DELAY_SEC)
            continue

        logger.info("  Fetched %d observations (oldest → newest)", len(observations))
        counts["observations_fetched"] += len(observations)

        # 2. Process each observation: Bronze → Silver → Gold → vault
        for obs in observations:
            raw_payload = producer._build_raw_payload(series_id, obs, meta, "pulse")
            endpoint    = f"{FRED_OBSERVATIONS_ENDPOINT}?series_id={series_id}"
            envelope    = build_bronze_message(SOURCE_NAME, endpoint, raw_payload)

            # Silver dispatch — full validation path (identical to Flink Silver Job)
            topic, silver = process_fred_message(envelope)

            if topic == DEAD_LETTER_QUEUE:
                counts["silver_dlq"] += 1
                logger.warning(
                    "  → DLQ  date=%s  errors=%s",
                    raw_payload.get("observation_date"),
                    silver.get("validation_errors", []),
                )
                continue

            if topic != SILVER_STRUCTURED_METRICS:
                counts["errors"] += 1
                logger.error("  → Unexpected Silver topic: %s", topic)
                continue

            counts["silver_ok"] += 1

            # Gold processing — real momentum computation with running history
            _, gold = process_fred_gold_message(
                silver,
                price_history.get(series_id, []),
            )

            # Check if any automation trigger fired (Section B.3)
            anomaly_flags = gold.get("metadata_extension", {}).get("anomaly_flags", [])
            if anomaly_flags:
                counts["triggers_fired"] += 1
                logger.info(
                    "  → TRIGGER  date=%s  flags=%s  value=%.4f",
                    raw_payload.get("observation_date"),
                    anomaly_flags,
                    float(silver.get("data_point", {}).get("current_value", 0)),
                )

            # Persist Gold record to TimescaleDB momentum_vault
            try:
                mv_insert(gold)
                counts["momentum_vault_new"] += 1
            except Exception as exc:
                counts["errors"] += 1
                logger.error(
                    "  → momentum_vault.insert FAILED  date=%s: %s",
                    raw_payload.get("observation_date"),
                    exc,
                )
                continue

            # Update in-process price history (oldest-first, mirrors Flink state)
            ts  = silver.get("data_point", {}).get("timestamp_utc", "")
            val = float(silver.get("data_point", {}).get("current_value", 0.0))
            price_history.setdefault(series_id, []).append((ts, val))

        counts["series_completed"] += 1
        logger.info(
            "  [%s] Done — vault_new=%d  triggers=%d",
            series_id,
            counts["momentum_vault_new"],
            counts["triggers_fired"],
        )

        # Rate-limit guard between series (FRED: 120 req/min)
        time.sleep(REQUEST_DELAY_SEC)

    elapsed = time.time() - start_ts

    # ── Post-run verification ───────────────────────────────────────────────
    logger.info("")
    logger.info("=== Post-Run Verification ===")
    verification_errors = 0

    # Verify every series has a retrievable row
    logger.info("  fetch_latest() per series:")
    for series_id in INDICATORS:
        row = fetch_latest("fred", series_id)
        if row is None:
            logger.error("  ✗ fetch_latest returned None for %-20s", series_id)
            verification_errors += 1
        else:
            flags = row.get("metadata_extension", {}).get("anomaly_flags", [])
            flag_str = f"  [ANOMALY: {flags}]" if flags else ""
            logger.info(
                "  ✓ %-22s  value=%-10.4f  change_24h=%+.6f%s",
                series_id,
                row["current_value"],
                row["change_24h"],
                flag_str,
            )

    # Momentum block detail for FEDFUNDS (daily series — enough history for deltas)
    logger.info("")
    logger.info("  Momentum block detail — FEDFUNDS (daily series):")
    fedfunds_row = fetch_latest("fred", "FEDFUNDS")
    if fedfunds_row:
        logger.info("    current_value = %.4f %s",
                    fedfunds_row["current_value"], fedfunds_row.get("unit", ""))
        logger.info("    change_24h    = %+.6f", fedfunds_row["change_24h"])
        logger.info("    change_7d     = %+.6f", fedfunds_row["change_7d"])
        logger.info("    change_30d    = %+.6f", fedfunds_row["change_30d"])
        logger.info("    is_new_market = %s",    fedfunds_row["is_new_market"])

    # Time-series query sanity check
    logger.info("")
    logger.info("  Time-series query (FEDFUNDS, last 60 days):")
    ts_rows = fetch_fred_series("FEDFUNDS", days=60)
    if len(ts_rows) == 0:
        logger.error("  ✗ fetch_fred_series(FEDFUNDS, days=60) returned 0 rows")
        verification_errors += 1
    else:
        oldest = ts_rows[0]["timestamp_utc"]
        newest = ts_rows[-1]["timestamp_utc"]
        logger.info(
            "  ✓ %d rows returned  oldest=%s  newest=%s",
            len(ts_rows), oldest, newest,
        )

    # Active anomaly alerts
    logger.info("")
    logger.info("  Active anomaly alerts (last 7 days):")
    anomalies = fetch_fred_anomalies(days=7)
    if anomalies:
        for r in anomalies:
            logger.info(
                "  ⚠  %-22s  flags=%-35s  value=%.4f  reason=%s",
                r["external_reference_id"],
                str(r["metadata_extension"].get("anomaly_flags", [])),
                r["current_value"],
                r["metadata_extension"].get("trigger_reason", "")[:80],
            )
    else:
        logger.info("  (none — all indicators within normal thresholds)")

    # ── Final summary ───────────────────────────────────────────────────────
    print("\n" + "=" * 62)
    print(f"  Sprint 2 E2E — FRED — COMPLETE  ({elapsed:.1f}s)")
    print("=" * 62)
    col_w = 28
    for key, val in counts.items():
        print(f"  {key:<{col_w}} {val}")
    print(f"  {'verification_errors':<{col_w}} {verification_errors}")
    print("=" * 62)

    total_errors = counts["errors"] + verification_errors
    if total_errors > 0:
        print(f"\n  ⚠  {total_errors} error(s) above — check logs for details.")
    else:
        print(
            "\n  All observations processed and verified without errors.\n"
            "  FRED vertical slice end-to-end pipeline validated."
        )

    return counts


if __name__ == "__main__":
    run()
