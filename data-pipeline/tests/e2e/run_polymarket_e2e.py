"""
Sprint 1 End-to-End Integration Test — Polymarket Vertical Slice.

Runs the full pipeline in standalone mode (without a Flink cluster) to validate:
  1. Bronze envelope parsing and Silver dispatch (process_polymarket_message)
  2. Silver structured-metric → Gold momentum block → momentum_vault
  3. Silver social → Gold consensus (real OpenAI GPT-4o + embedding) → social_vault + social_vectors

Why standalone instead of deploying to Flink:
  The apache/flink:1.19-java11 image ships without Python, pip, PyFlink,
  or the Kafka connector JAR. Deploying Python jobs to it requires a custom
  Dockerfile that will be built in Sprint 2 alongside the FRED vertical slice.
  For Sprint 1 validation we use the exact same transform functions — the only
  thing omitted is the Flink runtime itself (EXACTLY_ONCE semantics,
  checkpointing, keyed state for cross-message price history).

What this confirms end-to-end:
  - Kafka connectivity (Bronze topic readable at localhost:9092)
  - Bronze → Silver transforms + validators
  - Gold momentum-block computation
  - Real OpenAI API call (GPT-4o consensus summary + text-embedding-3-small)
  - social_vault  write (Silver raw archive)
  - social_vectors write (Gold HNSW-indexed vector)
  - momentum_vault write (Gold TimescaleDB hypertable)

Usage (from data-pipeline/ with venv active):
    python -m tests.e2e.run_polymarket_e2e

The runner consumes all messages currently available on ingest.bronze.polymarket
(auto_offset_reset='earliest') and exits after MAX_MESSAGES or IDLE_TIMEOUT_MS
with no new messages, whichever comes first.

References:
    - Section 4.1:  Silver Job specification
    - Section 4.2A: Consensus Bundling (GPT-4o)
    - Section 4.2B: Momentum Block (keyed state deltas)
    - Section 5.1:  Social Vault
    - Section 5.2:  Social Vectors
    - Section 5.3:  Momentum Vault
    - Section 9.3:  Triple-Gate Test Matrix
"""

import json
import logging
import sys
import time
from typing import Optional

from openai import OpenAI

from kafka import KafkaConsumer, TopicPartition

from config.kafka_topics import (
    BRONZE_POLYMARKET,
    DEAD_LETTER_QUEUE,
    SILVER_SOCIAL_PULSE,
    SILVER_STRUCTURED_METRICS,
)
from config.settings import KAFKA_BOOTSTRAP_SERVERS, OPENAI_API_KEY
from persistence.momentum_vault import insert as mv_insert
from persistence.social_vault import (
    exists_by_content_hash,
    fetch_social_id_by_content_hash,
    insert as sv_archive,
)
from persistence.social_vectors import insert as svec_insert
from processing.gold_job import (
    process_social_pulse_message,
    process_structured_metrics_message,
)
from processing.silver_job import process_polymarket_message

# ------------------------------------------------------------------ #
# Runner parameters
# ------------------------------------------------------------------ #

# Stop after this many Bronze messages (regardless of type).
# Set high to drain the full topic — consumer_timeout_ms handles the exit
# once all messages are consumed and the topic goes idle.
MAX_MESSAGES = 10_000

# Stop if Kafka delivers no new messages for this long (ms).
# Producer's WebSocket emits price_change events continuously, so
# 10 seconds of silence means the topic is drained.
IDLE_TIMEOUT_MS = 10_000


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _make_openai_client() -> OpenAI:
    if not OPENAI_API_KEY:
        print(
            "\n[e2e] ERROR: OPENAI_API_KEY is not set in .env.\n"
            "The Gold social-pulse path requires a real OpenAI key.\n"
            "Set it in data-pipeline/.env and re-run.\n"
        )
        sys.exit(1)
    return OpenAI(api_key=OPENAI_API_KEY)


# ------------------------------------------------------------------ #
# Main runner
# ------------------------------------------------------------------ #

def run() -> dict:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        stream=sys.stdout,
    )
    logger = logging.getLogger("e2e_polymarket")

    logger.info("=== Anizai Sprint 1 — Polymarket E2E Integration Test ===")
    logger.info("Kafka: %s  |  topic: %s", KAFKA_BOOTSTRAP_SERVERS, BRONZE_POLYMARKET)

    openai_client = _make_openai_client()
    logger.info("OpenAI client initialised.")

    # No group_id — use manual partition assignment + seek_to_beginning so the
    # runner always reads from offset 0, regardless of any previously committed
    # group offsets. This makes reruns idempotent (safe — vault inserts are
    # deduped via ON CONFLICT / exists_by_content_hash).
    consumer = KafkaConsumer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        auto_offset_reset="earliest",
        consumer_timeout_ms=IDLE_TIMEOUT_MS,
        value_deserializer=lambda m: m.decode("utf-8"),
    )
    partitions = [TopicPartition(BRONZE_POLYMARKET, p) for p in range(3)]
    consumer.assign(partitions)
    consumer.seek_to_beginning(*partitions)
    logger.info("Assigned partitions 0-2, seeked to beginning.")

    counts: dict = {
        "bronze_received":      0,
        "price_updates":        0,
        "comments":             0,
        "skipped_empty":        0,
        "dlq_silver":           0,
        "dlq_gold":             0,
        "social_vault_new":     0,
        "social_vault_deduped": 0,
        "social_vectors_new":   0,
        "momentum_vault_new":   0,
        "openai_calls":         0,
        "errors":               0,
    }

    # In-process price history keyed by canonical_event_id.
    # Replicates the per-key Flink ValueState without the Flink runtime.
    price_history: dict[str, list[tuple[str, float]]] = {}

    start_ts = time.time()

    for msg in consumer:
        if counts["bronze_received"] >= MAX_MESSAGES:
            logger.info("MAX_MESSAGES (%d) reached — stopping.", MAX_MESSAGES)
            break

        # ── 1. Parse Bronze envelope ──────────────────────────────────
        try:
            envelope = json.loads(msg.value)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Bronze JSON parse error (offset=%d): %s", msg.offset, exc)
            counts["errors"] += 1
            continue

        counts["bronze_received"] += 1
        logger.info(
            "[bronze #%d]  offset=%-6d  payload_type=%s",
            counts["bronze_received"],
            msg.offset,
            envelope.get("payload", {}).get("raw_payload", {}).get("payload_type", "?"),
        )

        # ── 2. Silver dispatch ────────────────────────────────────────
        topic, silver = process_polymarket_message(envelope)

        if topic is None:
            # Empty comment batch — intentional skip, not an error
            counts["skipped_empty"] += 1
            logger.debug("  → skipped (empty comment batch)")
            continue

        if topic == DEAD_LETTER_QUEUE:
            counts["dlq_silver"] += 1
            errs = silver.get("validation_errors", [])
            logger.warning("  → Silver DLQ: %s", errs)
            continue

        # ── 3a. Price update → momentum_vault ────────────────────────
        if topic == SILVER_STRUCTURED_METRICS:
            counts["price_updates"] += 1
            ceid    = silver.get("core_identity", {}).get("canonical_event_id", "")
            history = price_history.get(ceid, [])

            _, gold = process_structured_metrics_message(silver, history)

            try:
                mv_insert(gold)
                counts["momentum_vault_new"] += 1
                logger.info(
                    "  → momentum_vault  canonical_event_id=%s  value=%.4f",
                    ceid,
                    float(silver.get("data_point", {}).get("current_value", 0)),
                )
            except Exception as exc:
                counts["errors"] += 1
                logger.error("  → momentum_vault.insert FAILED: %s", exc)

            # Update in-process price history (mirrors Flink keyed state)
            ts  = silver.get("data_point", {}).get("timestamp_utc", "")
            val = float(silver.get("data_point", {}).get("current_value", 0.0))
            history.append((ts, val))
            price_history[ceid] = history[-10_000:]

        # ── 3b. Comment → social_vault + social_vectors ───────────────
        elif topic == SILVER_SOCIAL_PULSE:
            counts["comments"] += 1
            content_hash = silver.get("content_hash", "")
            market_id    = silver.get("market_id", "?")

            # Silver archive — dedup-guarded (Section 4.1C).
            # Capture social_id for silver_data_ref wiring (Gap 1 fix, Sprint 11).
            social_id = None
            try:
                if content_hash and exists_by_content_hash(content_hash):
                    counts["social_vault_deduped"] += 1
                    social_id = fetch_social_id_by_content_hash(content_hash)
                    logger.info(
                        "  → social_vault  DEDUPED  market_id=%s", market_id
                    )
                else:
                    social_id = sv_archive(silver)   # capture return value
                    counts["social_vault_new"] += 1
                    logger.info(
                        "  → social_vault  INSERTED  market_id=%s social_id=%s",
                        market_id, social_id,
                    )
            except Exception as exc:
                counts["errors"] += 1
                logger.error("  → social_vault.insert FAILED: %s", exc)

            # Gold enrichment — real OpenAI API call
            logger.info(
                "  → calling OpenAI (GPT-4o consensus + embedding)  market_id=%s …",
                market_id,
            )
            counts["openai_calls"] += 1
            try:
                gold_topic, gold = process_social_pulse_message(
                    silver, openai_client=openai_client
                )
            except Exception as exc:
                counts["errors"] += 1
                logger.error("  → OpenAI call FAILED: %s", exc)
                continue

            if gold_topic == DEAD_LETTER_QUEUE:
                counts["dlq_gold"] += 1
                logger.warning(
                    "  → Gold DLQ: %s", gold.get("validation_errors", [])
                )
                continue

            # Wire correct social_vault.social_id into silver_data_ref (Gap 1).
            if social_id:
                gold["metadata"]["silver_data_ref"] = social_id

            try:
                svec_insert(gold)
                counts["social_vectors_new"] += 1
                logger.info(
                    "  → social_vectors  INSERTED  market_id=%s", market_id
                )
            except Exception as exc:
                counts["errors"] += 1
                logger.error("  → social_vectors.insert FAILED: %s", exc)

    consumer.close()
    elapsed = time.time() - start_ts

    # ── Final summary ─────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"  Sprint 1 E2E — Polymarket — COMPLETE  ({elapsed:.1f}s)")
    print("=" * 60)
    col_w = 28
    for key, val in counts.items():
        print(f"  {key:<{col_w}} {val}")
    print("=" * 60)

    if counts["errors"] > 0:
        print(f"\n  ⚠  {counts['errors']} error(s) above — check logs for details.")
    else:
        print("\n  All messages processed without errors.")

    return counts


if __name__ == "__main__":
    run()
