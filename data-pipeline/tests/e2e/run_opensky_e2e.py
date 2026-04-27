"""
Sprint 10 End-to-End Integration Test — OpenSky Vertical Slice.

Runs the full OpenSky pipeline in standalone mode (without a Flink cluster
or Kafka broker):
  1. Fetches live aircraft states from the OpenSky /states/all endpoint for all
     7 strategic bounding boxes (static mode — one call per box).
  2. Builds Bronze envelopes and runs them through the Silver Job
     (process_opensky_message).
  3. Runs Silver records through the Gold Job (process_opensky_gold_message)
     with an in-process price history keyed by bounding_box_id — replicating
     Flink's per-key ValueState.
  4. Inserts Gold records into momentum_vault (TimescaleDB hypertable).
  5. Post-run verification: fetch_latest() spot-check per box + trigger
     summary table.

No Kafka broker required: OpenSkyProducer is used for _fetch_box() and
  _build_raw_payload() helpers only. _emit() is never called.
No OpenAI required: all OpenSky Gold enrichment is deterministic math
  (momentum deltas + rule-based triggers).

Auth mode (March 2026 — OAuth2 client credentials):
  Authenticated (both OPENSKY_CLIENT_ID and OPENSKY_CLIENT_SECRET set in .env):
    4,000 calls/day → AUTH_REQUEST_DELAY_SEC (1.0s) between boxes.
  Anonymous (either credential missing):
    ~100 calls/day → ANON_REQUEST_DELAY_SEC (1.0s) between boxes.
  The runner uses OpenSkyProducer.__init__() to auto-detect mode.

What this confirms end-to-end:
  - Real OpenSky /states/all endpoint connectivity for all 7 boxes
  - Bronze payload building (_build_raw_payload — states_compact extraction)
  - Bronze → Silver transform + Silver schema validation
  - Transponder silence domain filter (lat=None AND lon=None AND on_ground=False)
  - Silver → Gold transform + both trigger evaluations:
      Aerial_Escalation_Risk        (change_30d > 0.30)
      Transponder_Deactivation_Alert (silence_events > 0 + tension_zone)
  - Real momentum block computation (is_new_market=True for fresh run)
  - momentum_vault.insert() accepting source_name="opensky"
  - fetch_latest() returning the inserted row per box
  - JSONB round-trip for bounding_box_id, transponder_silence_events, anomaly_score

Usage (from data-pipeline/ with venv active):
    python -m tests.e2e.run_opensky_e2e

Rate-limit note:
  Anonymous: 7 boxes × 6.5s ≈ 45.5s total.
  Authenticated: 7 boxes × 1.5s ≈ 10.5s total.

References:
    - Section 2.3:  Scheduled pollers — Airflow DAGs manage production invocation
    - Section 4.1:  Silver Job specification
    - Section 4.2B: Momentum Block — keyed state deltas
    - Section 5.3:  Momentum Vault — TimescaleDB hypertable
    - Section 9.3:  Triple-Gate Test Matrix — E2E validation
    - Section B.7:  OpenSky technical parameters & trigger thresholds
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone

from config.kafka_topics import DEAD_LETTER_QUEUE, SILVER_STRUCTURED_METRICS
from ingestion.opensky_producer import (
    ANON_REQUEST_DELAY_SEC,
    AUTH_REQUEST_DELAY_SEC,
    BOUNDING_BOXES,
    OPENSKY_BASE_URL,
    SOURCE_NAME,
    OpenSkyProducer,
)
from persistence.momentum_vault import (
    fetch_latest,
    fetch_time_series,
    insert as mv_insert,
)
from processing.gold_job import process_opensky_gold_message
from processing.silver_job import process_opensky_message
from utils.db import get_cursor
from utils.kafka_utils import build_bronze_message

# ==========================================================
# Runner parameters
# ==========================================================

_VERIFY_HOURS: int = 24


# ==========================================================
# Helpers
# ==========================================================

def _check_prerequisites() -> None:
    """Fail fast if PostgreSQL is unreachable before touching the OpenSky API."""
    try:
        with get_cursor() as cur:
            cur.execute("SELECT 1;")
    except Exception as exc:
        print(
            f"\n[e2e] ERROR: PostgreSQL not reachable: {exc}\n"
            "Start the stack: docker compose -f infrastructure/docker-compose.yml up -d postgres\n"
        )
        sys.exit(1)


def _make_producer_helper() -> OpenSkyProducer:
    """
    Create an OpenSkyProducer with live API access but NO Kafka producer.

    OpenSkyProducer.__init__ auto-detects auth mode (reads OPENSKY_CLIENT_ID /
    OPENSKY_CLIENT_SECRET from .env) and sets _request_delay accordingly. We call
    __init__ normally so auth mode detection works, then replace the Kafka
    producer with None since _emit() is never called in the E2E runner.
    """
    producer = OpenSkyProducer()
    producer._producer = None  # Kafka not needed — _emit() is never called
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
    logger = logging.getLogger("e2e_opensky")

    logger.info("=== Anizai Sprint 10 — OpenSky E2E Integration Test ===")
    logger.info(
        "Mode: static  |  Boxes: %d  |  Endpoint: %s",
        len(BOUNDING_BOXES), OPENSKY_BASE_URL,
    )

    _check_prerequisites()
    logger.info("Prerequisites verified: PostgreSQL reachable.")

    producer = _make_producer_helper()
    auth_label = "authenticated" if producer._authenticated else "anonymous"
    logger.info(
        "OpenSkyProducer helper initialised (no Kafka broker) — mode=%s, "
        "request_delay=%.1fs",
        auth_label, producer._request_delay,
    )

    counts: dict = {
        "boxes_attempted":              len(BOUNDING_BOXES),
        "api_fetch_ok":                 0,
        "api_fetch_failed":             0,
        "bronze_built":                 0,
        "silver_ok":                    0,
        "silver_dlq":                   0,
        "momentum_vault_inserted":      0,
        "aerial_escalation_risks":      0,
        "transponder_deactivation_alerts": 0,
        "total_silence_events":         0,
        "errors":                       0,
    }

    per_box_rows: list[dict] = []

    # In-process price history keyed by bounding_box_id (replicates Flink keyed state).
    # Each value: list of (timestamp_utc_iso, aircraft_density_count) tuples.
    price_histories: dict[str, list[tuple[str, float]]] = {}

    fetch_ts  = datetime.now(timezone.utc).isoformat()
    start_ts  = time.time()

    print("\n" + "-" * 80)
    print(f"  {'Box':<30} {'Count':>6}  {'Silence':>7}  {'Tag':<26}  Triggers")
    print("-" * 80)

    for box in BOUNDING_BOXES:
        box_id       = box["bounding_box_id"]
        strategic_tag = box["strategic_tag"]

        # ── OpenSky API fetch ────────────────────────────────────────────────
        api_data = producer._fetch_box(box)
        if api_data is None:
            counts["api_fetch_failed"] += 1
            counts["errors"] += 1
            print(f"  {box_id:<30}  [FETCH FAILED — skipped]")
            per_box_rows.append({"box_id": box_id, "status": "fetch_failed"})
            time.sleep(producer._request_delay)
            continue

        counts["api_fetch_ok"] += 1

        # ── Bronze ────────────────────────────────────────────────────────────
        raw = producer._build_raw_payload(box, api_data, fetch_ts, mode="static")
        lat_min, lat_max = box["lat_min"], box["lat_max"]
        lon_min, lon_max = box["lon_min"], box["lon_max"]
        source_endpoint = (
            f"{OPENSKY_BASE_URL}"
            f"?lamin={lat_min}&lamax={lat_max}&lomin={lon_min}&lomax={lon_max}"
        )
        envelope = build_bronze_message(SOURCE_NAME, source_endpoint, raw)
        counts["bronze_built"] += 1

        # ── Silver ────────────────────────────────────────────────────────────
        topic, silver = process_opensky_message(envelope)

        if topic == DEAD_LETTER_QUEUE:
            counts["silver_dlq"] += 1
            counts["errors"] += 1
            err_detail = silver.get("validation_errors", silver.get("errors", []))
            logger.warning("  [%s] → DLQ  errors=%s", box_id, err_detail)
            print(f"  {box_id:<30}  [SILVER DLQ — errors: {err_detail}]")
            per_box_rows.append({"box_id": box_id, "status": "silver_dlq"})
            time.sleep(producer._request_delay)
            continue

        if topic != SILVER_STRUCTURED_METRICS:
            counts["errors"] += 1
            logger.error("  [%s] → Unexpected Silver topic: %s", box_id, topic)
            per_box_rows.append({"box_id": box_id, "status": "unexpected_topic"})
            time.sleep(producer._request_delay)
            continue

        counts["silver_ok"] += 1

        # ── Gold ──────────────────────────────────────────────────────────────
        history = price_histories.get(box_id, [])
        _, gold = process_opensky_gold_message(silver, history)

        meta          = gold.get("metadata_extension", {})
        anomaly_flags = meta.get("anomaly_flags", [])
        impact_level  = meta.get("impact_level", 1)
        silence_cnt   = meta.get("transponder_silence_events", 0)
        aircraft_cnt  = meta.get("aircraft_density_count", 0)

        fired_aerial      = "Aerial_Escalation_Risk"         in anomaly_flags
        fired_transponder = "Transponder_Deactivation_Alert" in anomaly_flags

        if fired_aerial:      counts["aerial_escalation_risks"] += 1
        if fired_transponder: counts["transponder_deactivation_alerts"] += 1
        counts["total_silence_events"] += silence_cnt

        # ── momentum_vault insert ──────────────────────────────────────────────
        try:
            mv_insert(gold)
            counts["momentum_vault_inserted"] += 1
        except Exception as exc:
            counts["errors"] += 1
            logger.error("  [%s] → momentum_vault.insert FAILED: %s", box_id, exc)
            per_box_rows.append({"box_id": box_id, "status": "vault_error"})
            time.sleep(producer._request_delay)
            continue

        # Update running price history (oldest-first, mirrors Flink state)
        ts_utc       = silver.get("data_point", {}).get("timestamp_utc", "")
        density      = float(silver.get("data_point", {}).get("current_value", 0.0))
        price_histories.setdefault(box_id, []).append((ts_utc, density))

        trigger_str  = " | ".join(anomaly_flags) if anomaly_flags else "none"

        print(
            f"  {box_id:<30} {aircraft_cnt:>6d}  {silence_cnt:>7d}  "
            f"{strategic_tag:<26}  {trigger_str}"
        )

        per_box_rows.append({
            "box_id":         box_id,
            "status":         "ok",
            "aircraft_count": aircraft_cnt,
            "silence_events": silence_cnt,
            "triggers":       anomaly_flags,
            "impact_level":   impact_level,
        })

        time.sleep(producer._request_delay)

    elapsed = time.time() - start_ts

    # ── Post-run verification ─────────────────────────────────────────────────
    print("\n" + "-" * 80)
    logger.info("=== Post-Run Verification ===")
    verification_errors = 0

    if counts["momentum_vault_inserted"] > 0:
        for row_meta in per_box_rows:
            if row_meta.get("status") != "ok":
                continue

            bid    = row_meta["box_id"]
            latest = fetch_latest(SOURCE_NAME, bid)

            if latest is None:
                logger.error(
                    "  ✗ fetch_latest('%s', '%s') returned None — row missing",
                    SOURCE_NAME, bid,
                )
                verification_errors += 1
                continue

            ext        = latest.get("metadata_extension", {})
            db_box_id  = ext.get("bounding_box_id")
            db_silence = ext.get("transponder_silence_events")
            db_score   = ext.get("anomaly_score")
            db_count   = latest.get("current_value")

            field_errors: list[str] = []
            if db_count is None:
                field_errors.append("current_value is None")
            if db_box_id is None:
                field_errors.append("bounding_box_id missing from JSONB")
            if db_silence is None:
                field_errors.append("transponder_silence_events missing from JSONB")
            if db_score is None:
                field_errors.append("anomaly_score missing from JSONB")

            if field_errors:
                logger.error("  ✗ %s: JSONB field errors: %s", bid, field_errors)
                verification_errors += 1
            else:
                logger.info(
                    "  ✓ %-30s  count=%d  silence=%d  anomaly_score=%.4f",
                    bid,
                    int(db_count),
                    int(db_silence),
                    float(db_score),
                )

        # fetch_time_series spot-check on the first successful box
        ok_rows = [r for r in per_box_rows if r.get("status") == "ok"]
        if ok_rows:
            first_box = ok_rows[0]["box_id"]
            ts_rows   = fetch_time_series(SOURCE_NAME, first_box, hours=_VERIFY_HOURS)
            if len(ts_rows) == 0:
                logger.error(
                    "  ✗ fetch_time_series('%s', '%s') returned 0 rows",
                    SOURCE_NAME, first_box,
                )
                verification_errors += 1
            else:
                logger.info(
                    "  ✓ fetch_time_series('%s') — %d row(s)  newest=%s",
                    first_box, len(ts_rows), ts_rows[-1]["timestamp_utc"],
                )
    else:
        logger.warning(
            "  Skipping DB verification — no records were inserted "
            "(all boxes failed or went to DLQ)."
        )
        if counts["errors"] > 0:
            verification_errors += 1

    # ── Trigger summary table ─────────────────────────────────────────────────
    print("\n" + "=" * 80)
    any_trigger = (
        counts["aerial_escalation_risks"]
        + counts["transponder_deactivation_alerts"]
    )
    if any_trigger > 0:
        print("  TRIGGERS FIRED THIS RUN:")
        if counts["aerial_escalation_risks"] > 0:
            print(
                f"  ⚠  Aerial_Escalation_Risk          "
                f"×{counts['aerial_escalation_risks']} "
                "(change_30d > 30% — note: first run has no baseline)"
            )
        if counts["transponder_deactivation_alerts"] > 0:
            print(
                f"  ⚠  Transponder_Deactivation_Alert  "
                f"×{counts['transponder_deactivation_alerts']} "
                f"({counts['total_silence_events']} total null-position aircraft detected)"
            )
    else:
        print(
            "  No triggers fired. On the first run this is expected — is_new_market=True "
            "suppresses all triggers until a 30-day baseline exists."
        )

    # ── Final summary table ───────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print(f"  Sprint 10 E2E — OpenSky [{auth_label}] — COMPLETE  ({elapsed:.1f}s)")
    print("=" * 80)
    col_w = 36
    for key, val in counts.items():
        print(f"  {key:<{col_w}} {val}")
    print(f"  {'verification_errors':<{col_w}} {verification_errors}")
    print("=" * 80)

    total_errors = counts["errors"] + counts["silver_dlq"] + verification_errors
    if total_errors > 0:
        print(f"\n  ⚠  {total_errors} error(s) above — check logs for details.")
    else:
        print(
            "\n  All boxes processed and verified without errors.\n"
            "  OpenSky vertical slice end-to-end pipeline validated."
        )

    return counts


if __name__ == "__main__":
    run()
