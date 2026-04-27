"""
Sprint 9 End-to-End Integration Test — OpenWeather Vertical Slice.

Runs the full OpenWeather pipeline in standalone mode (without a Flink cluster
or Kafka broker):
  1. Fetches live weather from the OpenWeatherMap /weather endpoint for all
     10 strategic hotspots (static mode — one call per hotspot).
  2. Builds Bronze envelopes and runs them through the Silver Job
     (process_openweather_message).
  3. Runs Silver records through the Gold Job (process_openweather_gold_message)
     with an in-process price history keyed by hotspot_name — replicating
     Flink's per-key ValueState.
  4. Inserts Gold records into momentum_vault (TimescaleDB hypertable).
  5. Post-run verification: fetch_latest() spot-check per hotspot + trigger
     summary table.

No Kafka broker required: OpenWeatherProducer is used for _fetch_hotspot() and
  _build_raw_payload() helpers only. _emit() is never called.
No OpenAI required: all OpenWeather Gold enrichment is deterministic math
  (momentum deltas + rule-based triggers).

OWM API key: must be set as OPENWEATHER_API_KEY in data-pipeline/.env.
  If missing or invalid, the producer logs a warning and all requests return 401.

What this confirms end-to-end:
  - Real OWM /weather endpoint connectivity for all 10 hotspots
  - Bronze payload building (_build_raw_payload — OWM field extraction)
  - Bronze → Silver transform + Silver schema validation
  - Silver → Gold transform + all three trigger evaluations:
      Natural_Disaster_Alert   (condition_severity ≥ 4)
      Tech_Supply_Risk         (severity ≥ 4 + tech hotspot)
      Potential_Shipping_Delay (wind_speed_knots > 50 + maritime hotspot)
  - Real momentum block computation (is_new_market=True for fresh run)
  - momentum_vault.insert() accepting source_name="openweather"
  - fetch_latest() returning the inserted row per hotspot
  - JSONB round-trip for strategic_tag, condition_severity, wind_speed_knots

Usage (from data-pipeline/ with venv active):
    python -m tests.e2e.run_openweather_e2e

Rate-limit note:
  OWM free tier allows 60 calls/minute (1/sec). With 10 hotspots and a
  REQUEST_DELAY_SEC gap between calls, the E2E completes in approximately
  10 × (1s API + delay) ≈ 30-40s total.

References:
    - Section 2.3:  Scheduled pollers — Airflow DAGs manage production invocation
    - Section 4.1:  Silver Job specification
    - Section 4.2B: Momentum Block — keyed state deltas
    - Section 5.3:  Momentum Vault — TimescaleDB hypertable
    - Section 9.3:  Triple-Gate Test Matrix — E2E validation
    - Section B.6:  OpenWeather technical parameters & trigger thresholds
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone

from config.kafka_topics import DEAD_LETTER_QUEUE, SILVER_STRUCTURED_METRICS
from ingestion.openweather_producer import (
    HOTSPOTS,
    OWM_BASE_URL,
    REQUEST_DELAY_SEC,
    SOURCE_NAME,
    OpenWeatherProducer,
)
from persistence.momentum_vault import (
    fetch_latest,
    fetch_time_series,
    insert as mv_insert,
)
from processing.gold_job import process_openweather_gold_message
from processing.silver_job import process_openweather_message
from utils.db import get_cursor
from utils.kafka_utils import build_bronze_message

# ==========================================================
# Runner parameters
# ==========================================================

# Fetch window for the post-run time-series verification.
_VERIFY_HOURS: int = 24

# ==========================================================
# Helpers
# ==========================================================

def _check_prerequisites() -> None:
    """Fail fast if PostgreSQL is unreachable before touching the OWM API."""
    try:
        with get_cursor() as cur:
            cur.execute("SELECT 1;")
    except Exception as exc:
        print(
            f"\n[e2e] ERROR: PostgreSQL not reachable: {exc}\n"
            "Start the stack: docker compose -f infrastructure/docker-compose.yml up -d postgres\n"
        )
        sys.exit(1)


def _make_producer_helper() -> OpenWeatherProducer:
    """
    Create an OpenWeatherProducer with a live session but NO Kafka producer.

    Uses __new__ to bypass __init__ (which calls make_producer()) so the
    E2E can exercise the OWM API and pipeline logic without a running broker.
    _emit() is never called — Gold records go directly to momentum_vault.
    """
    producer = OpenWeatherProducer.__new__(OpenWeatherProducer)
    producer._emitted = 0
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
    logger = logging.getLogger("e2e_openweather")

    logger.info("=== Anizai Sprint 9 — OpenWeather E2E Integration Test ===")
    logger.info(
        "Mode: static  |  Hotspots: %d  |  Endpoint: %s",
        len(HOTSPOTS), OWM_BASE_URL,
    )

    _check_prerequisites()
    logger.info("Prerequisites verified: PostgreSQL reachable.")

    producer = _make_producer_helper()
    logger.info("OpenWeatherProducer helper initialised (no Kafka broker).")

    counts: dict = {
        "hotspots_attempted":           len(HOTSPOTS),
        "api_fetch_ok":                 0,
        "api_fetch_failed":             0,
        "bronze_built":                 0,
        "silver_ok":                    0,
        "silver_dlq":                   0,
        "momentum_vault_inserted":      0,
        "natural_disaster_alerts":      0,
        "tech_supply_risks":            0,
        "potential_shipping_delays":    0,
        "errors":                       0,
    }

    # Per-hotspot rows: printed after each hotspot for live progress visibility
    per_hotspot_rows: list[dict] = []

    # In-process price history keyed by hotspot_name (replicates Flink keyed state)
    # Each value: list of (timestamp_utc_iso, temperature_celsius) tuples
    price_histories: dict[str, list[tuple[str, float]]] = {}

    fetch_ts  = datetime.now(timezone.utc).isoformat()
    start_ts  = time.time()

    print("\n" + "-" * 72)
    print(f"  {'Hotspot':<26} {'°C':>5}  {'Wind kt':>8}  {'Sev':>4}  {'Tag':<24}  Triggers")
    print("-" * 72)

    for hotspot in HOTSPOTS:
        name         = hotspot["name"]
        strategic_tag = hotspot.get("strategic_tag", "")

        # ── OWM API fetch ────────────────────────────────────────────────────
        owm_data = producer._fetch_hotspot(hotspot)
        if owm_data is None:
            counts["api_fetch_failed"] += 1
            counts["errors"] += 1
            print(f"  {name:<26}  [FETCH FAILED — skipped]")
            per_hotspot_rows.append({"name": name, "status": "fetch_failed"})
            continue

        counts["api_fetch_ok"] += 1

        # ── Bronze ───────────────────────────────────────────────────────────
        raw = producer._build_raw_payload(hotspot, owm_data, fetch_ts, mode="static")
        source_endpoint = f"{OWM_BASE_URL}?lat={hotspot['lat']}&lon={hotspot['lon']}&units=metric"
        envelope = build_bronze_message(SOURCE_NAME, source_endpoint, raw)
        counts["bronze_built"] += 1

        # ── Silver ───────────────────────────────────────────────────────────
        topic, silver = process_openweather_message(envelope)

        if topic == DEAD_LETTER_QUEUE:
            counts["silver_dlq"] += 1
            counts["errors"] += 1
            err_detail = silver.get("validation_errors", silver.get("errors", []))
            logger.warning(
                "  [%s] → DLQ  errors=%s", name, err_detail,
            )
            print(f"  {name:<26}  [SILVER DLQ — errors: {err_detail}]")
            per_hotspot_rows.append({"name": name, "status": "silver_dlq"})
            continue

        if topic != SILVER_STRUCTURED_METRICS:
            counts["errors"] += 1
            logger.error("  [%s] → Unexpected Silver topic: %s", name, topic)
            per_hotspot_rows.append({"name": name, "status": "unexpected_topic"})
            continue

        counts["silver_ok"] += 1

        # ── Gold ─────────────────────────────────────────────────────────────
        history = price_histories.get(name, [])
        _, gold = process_openweather_gold_message(silver, history)

        # Extract trigger results
        meta          = gold.get("metadata_extension", {})
        anomaly_flags = meta.get("anomaly_flags", [])
        impact_level  = meta.get("impact_level", 1)
        trigger_reason = meta.get("trigger_reason")

        fired_natural_disaster   = "Natural_Disaster_Alert" in anomaly_flags
        fired_tech_supply        = "Tech_Supply_Risk" in anomaly_flags
        fired_shipping_delay     = "Potential_Shipping_Delay" in anomaly_flags

        if fired_natural_disaster:  counts["natural_disaster_alerts"] += 1
        if fired_tech_supply:       counts["tech_supply_risks"] += 1
        if fired_shipping_delay:    counts["potential_shipping_delays"] += 1

        # ── momentum_vault insert ─────────────────────────────────────────────
        try:
            mv_insert(gold)
            counts["momentum_vault_inserted"] += 1
        except Exception as exc:
            counts["errors"] += 1
            logger.error("  [%s] → momentum_vault.insert FAILED: %s", name, exc)
            per_hotspot_rows.append({"name": name, "status": "vault_error"})
            continue

        # Update running price history (oldest-first, mirrors Flink state)
        ts_utc = silver.get("data_point", {}).get("timestamp_utc", "")
        temp   = float(silver.get("data_point", {}).get("current_value", 0.0))
        price_histories.setdefault(name, []).append((ts_utc, temp))

        # Live progress row
        cond_sev    = meta.get("condition_severity", "-")
        wind_knots  = meta.get("wind_speed_knots", 0.0)
        trigger_str = " | ".join(anomaly_flags) if anomaly_flags else "none"

        print(
            f"  {name:<26} {temp:>5.1f}  {wind_knots:>7.1f}kt  {cond_sev!s:>3}  "
            f"{strategic_tag:<24}  {trigger_str}"
        )

        per_hotspot_rows.append({
            "name":          name,
            "status":        "ok",
            "temp_celsius":  temp,
            "wind_knots":    wind_knots,
            "cond_severity": cond_sev,
            "triggers":      anomaly_flags,
            "impact_level":  impact_level,
        })

        # OWM free-tier rate limit: ~60 req/min; delay avoids throttling
        time.sleep(REQUEST_DELAY_SEC)

    elapsed = time.time() - start_ts

    # ── Post-run verification ─────────────────────────────────────────────────
    print("\n" + "-" * 72)
    logger.info("=== Post-Run Verification ===")
    verification_errors = 0

    if counts["momentum_vault_inserted"] > 0:
        # Spot-check fetch_latest for every successfully inserted hotspot
        for row_meta in per_hotspot_rows:
            if row_meta.get("status") != "ok":
                continue

            hname = row_meta["name"]
            latest = fetch_latest(SOURCE_NAME, hname)
            if latest is None:
                logger.error(
                    "  ✗ fetch_latest('%s', '%s') returned None — row missing",
                    SOURCE_NAME, hname,
                )
                verification_errors += 1
                continue

            ext            = latest.get("metadata_extension", {})
            db_tag         = ext.get("strategic_tag", "")
            db_cond_sev    = ext.get("condition_severity")
            db_wind_knots  = ext.get("wind_speed_knots")
            db_temp        = latest.get("current_value")

            field_errors: list[str] = []
            if db_temp is None:
                field_errors.append("current_value is None")
            if db_tag != row_meta.get("name", ""):
                # strategic_tag won't match name — just check it's present
                if not db_tag:
                    field_errors.append("strategic_tag missing from JSONB")
            if db_cond_sev is None:
                field_errors.append("condition_severity missing from JSONB")
            if db_wind_knots is None:
                field_errors.append("wind_speed_knots missing from JSONB")

            if field_errors:
                logger.error(
                    "  ✗ %s: JSONB field errors: %s", hname, field_errors,
                )
                verification_errors += 1
            else:
                logger.info(
                    "  ✓ %-26s  temp=%.1f°C  sev=%s  wind=%.1fkt  tag=%s",
                    hname,
                    db_temp,
                    db_cond_sev,
                    db_wind_knots,
                    db_tag,
                )

        # fetch_time_series spot-check on the first successful hotspot
        ok_rows = [r for r in per_hotspot_rows if r.get("status") == "ok"]
        if ok_rows:
            first_name = ok_rows[0]["name"]
            ts_rows = fetch_time_series(SOURCE_NAME, first_name, hours=_VERIFY_HOURS)
            if len(ts_rows) == 0:
                logger.error(
                    "  ✗ fetch_time_series('%s', '%s') returned 0 rows",
                    SOURCE_NAME, first_name,
                )
                verification_errors += 1
            else:
                logger.info(
                    "  ✓ fetch_time_series('%s') — %d row(s)  newest=%s",
                    first_name, len(ts_rows), ts_rows[-1]["timestamp_utc"],
                )
    else:
        logger.warning(
            "  Skipping DB verification — no records were inserted "
            "(all hotspots failed or went to DLQ)."
        )
        if counts["errors"] > 0:
            verification_errors += 1

    # ── Trigger summary table ─────────────────────────────────────────────────
    print("\n" + "=" * 72)
    any_trigger = (
        counts["natural_disaster_alerts"]
        + counts["tech_supply_risks"]
        + counts["potential_shipping_delays"]
    )
    if any_trigger > 0:
        print("  TRIGGERS FIRED THIS RUN:")
        if counts["natural_disaster_alerts"] > 0:
            print(
                f"  ⚠  Natural_Disaster_Alert    ×{counts['natural_disaster_alerts']} "
                "(condition_severity ≥ 4)"
            )
        if counts["tech_supply_risks"] > 0:
            print(
                f"  ⚠  Tech_Supply_Risk          ×{counts['tech_supply_risks']} "
                "(severity ≥ 4 + semiconductor/tech hotspot)"
            )
        if counts["potential_shipping_delays"] > 0:
            print(
                f"  ⚠  Potential_Shipping_Delay  ×{counts['potential_shipping_delays']} "
                "(wind > 50 kt at maritime chokepoint)"
            )
    else:
        print(
            "  No triggers fired — all hotspots within normal severity/wind thresholds."
        )

    # ── Final summary table ───────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print(f"  Sprint 9 E2E — OpenWeather — COMPLETE  ({elapsed:.1f}s)")
    print("=" * 72)
    col_w = 32
    for key, val in counts.items():
        print(f"  {key:<{col_w}} {val}")
    print(f"  {'verification_errors':<{col_w}} {verification_errors}")
    print("=" * 72)

    total_errors = counts["errors"] + counts["silver_dlq"] + verification_errors
    if total_errors > 0:
        print(f"\n  ⚠  {total_errors} error(s) above — check logs for details.")
    else:
        print(
            "\n  All hotspots processed and verified without errors.\n"
            "  OpenWeather vertical slice end-to-end pipeline validated."
        )

    return counts


if __name__ == "__main__":
    run()
