"""
Sprint 5 End-to-End Integration Test — Telegram Vertical Slice.

Runs the full Telegram pipeline in standalone mode (without a Flink cluster or
Kafka broker):
  1. Connects to Telegram via Telethon MTProto using the persisted session file
     (data-pipeline/anizai_telegram.session) — no re-authentication on subsequent runs
  2. Fetches the most recent N messages from each of the 7 vetted channels (Source
     Registry, Section A.1) using iter_messages() — deterministic batch pull, not
     event-driven streaming, so the E2E completes cleanly without a timeout
  3. Filters media-only messages (no text) — same rule as the producer's
     _handle_new_message() guard (Section A.1)
  4. Builds Bronze envelopes (http_status_code=0, request_duration_ms=0 — MTProto
     streaming, not HTTP poll) and routes them through the Silver Job
     (process_telegram_message) — sniper scoring, SHA-256 dedup, Silver schema validation
  5. Archives all valid Silver records to knowledge_vault (Section 5.1,
     dedup-aware; source_name="telegram")
  6. Runs high-signal Silver records through the Gold Job (process_telegram_gold_message)
     using real GPT-4o cognitive metadata extraction and text-embedding-3-small (Section 4.2A)
  7. Inserts Gold records into knowledge_vectors (HNSW-indexed, ON CONFLICT DO NOTHING,
     Section 5.2; source_platform="telegram", entry_type="direct_message")
  8. Post-run: fetches 3 sample records from knowledge_vectors and prints Direct Message
     domain_context (channel_username, is_forwarded, views, extracted_links) — distinct
     from NewsAPI Tactical and ArXiv Academic domain_context extensions (Section C.5)
  9. Prints final pipeline counters

FIRST-RUN AUTHENTICATION NOTE:
    If data-pipeline/anizai_telegram.session does not exist, Telethon will prompt:
        - Phone number (your personal account phone, e.g. +1234567890)
        - Verification code (sent to Telegram app or SMS)
    This is a one-time step. The session file is written to:
        data-pipeline/anizai_telegram.session
    Subsequent runs skip authentication entirely.
    *.session is in .gitignore — never committed.

No Kafka broker required: messages are built via build_bronze_message() directly.
No Flink required: Silver/Gold jobs are pure Python functions called directly.

Uses real OpenAI tokens — no mocks. Set OPENAI_API_KEY in infrastructure/.env.
Uses real Telegram credentials — set TELEGRAM_API_ID + TELEGRAM_API_HASH in .env.

Usage (from data-pipeline/ with venv active):
    python -m tests.e2e.run_telegram_e2e

PowerShell run command:
    cd C:\\Users\\ronki\\Desktop\\Anizai\\data-pipeline; `
    $env:PYTHONPATH = "."; `
    python -m tests.e2e.run_telegram_e2e

References:
    - Section 2.1:  Producer Matrix (Telegram row)
    - Section 4.1:  Silver Job specification (sniper, dedup, routing)
    - Section 4.2A: Gold Job — Cognitive Metadata Extraction
    - Section 5.1:  Knowledge Vault — Silver full-text persistence
    - Section 5.2:  Knowledge Vectors — Gold HNSW vector persistence
    - Section 9.3:  Triple-Gate Test Matrix — E2E validation
    - Section A.1:  Telegram technical parameters — MTProto, 7 channels, no Impact Boost
    - Section C.5:  Gold Global Signal — Telegram Direct Message domain_context
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time

from config.kafka_topics import DEAD_LETTER_QUEUE, GOLD_GLOBAL_NEWS, SILVER_GLOBAL_NEWS
from config.settings import OPENAI_API_KEY, TELEGRAM_API_HASH, TELEGRAM_API_ID
from ingestion.telegram_producer import CHANNELS, SESSION_PATH, TelegramProducer
from persistence.knowledge_vault import archive as kv_archive
from persistence.knowledge_vectors import (
    fetch_by_signal_id,
    insert as kvec_insert,
)
from processing.gold_job import process_telegram_gold_message
from processing.silver_job import process_telegram_message
from utils.db import get_cursor
from utils.kafka_utils import build_bronze_message


# ==========================================================
# Runner parameters
# ==========================================================

# Messages to fetch per channel. 3 × 7 channels = 21 attempts max.
# After media-only filter, typically 10-15 text messages reach Silver.
# With ~30s per Gold OpenAI call, worst case ≈ 10-12 min for high-signal messages.
MAX_PER_CHANNEL = 3

# Source endpoint label used in Bronze envelopes.
# Telegram uses MTProto streaming (not HTTP), so there is no real URL.
# This label traces the envelope to the Telethon iter_messages() call (Section C.1).
_BRONZE_SOURCE_ENDPOINT = "mtproto://telegram/iter_messages"


# ==========================================================
# Helpers
# ==========================================================

def _check_prerequisites() -> None:
    """
    Fail fast with clear messages if any required key or service is unavailable.

    Checked before the main loop so any misconfiguration surfaces before
    Telegram connections or DB writes are attempted.
    """
    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        print(
            "\n[e2e] ERROR: TELEGRAM_API_ID or TELEGRAM_API_HASH is not set in .env.\n"
            "Set both values from https://my.telegram.org/apps\n"
        )
        sys.exit(1)

    if not OPENAI_API_KEY:
        print(
            "\n[e2e] ERROR: OPENAI_API_KEY is not set in .env.\n"
            "This E2E test calls GPT-4o + text-embedding-3-small with real tokens.\n"
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


def _make_openai_client():
    """Instantiate the OpenAI client with the live key from settings."""
    try:
        from openai import OpenAI
    except ImportError:
        print("\n[e2e] ERROR: openai package not installed. Run: pip install openai\n")
        sys.exit(1)
    return OpenAI(api_key=OPENAI_API_KEY)


def _wrap(text: str, width: int = 58) -> list[str]:
    """
    Word-wrap a string to the given column width.

    Used to format long enrichment fields (executive_summary) in the
    post-run sample printout without truncating content.
    """
    words = text.split()
    lines: list[str] = []
    line:  list[str] = []
    col = 0
    for word in words:
        need = len(word) + (1 if line else 0)
        if col + need > width:
            lines.append(" ".join(line))
            line = [word]
            col = len(word)
        else:
            line.append(word)
            col += need
    if line:
        lines.append(" ".join(line))
    return lines or [""]


# ==========================================================
# Async pipeline runner
# ==========================================================

async def _run_async(logger: logging.Logger) -> dict:
    """
    Async body: connect to Telegram, fetch messages, run full pipeline.

    Separated from run() so asyncio.run() owns the event loop lifecycle cleanly.
    """
    counts: dict = {
        "channels_processed":      0,
        "messages_fetched":        0,
        "media_only_filtered":     0,
        "bronze_built":            0,
        "silver_ok":               0,
        "silver_dlq":              0,
        "keyword_sniper_passed":   0,
        "keyword_sniper_filtered": 0,
        "knowledge_vault_new":     0,
        "knowledge_vault_deduped": 0,
        "knowledge_vectors_new":   0,
        "openai_calls":            0,
        "errors":                  0,
    }

    inserted_signal_ids: list[str] = []

    # ── Connect Telethon client ───────────────────────────────────────────────
    # SESSION_PATH = data-pipeline/anizai_telegram (no .session suffix)
    # Telethon appends .session automatically.
    client = None
    try:
        from telethon import TelegramClient

        client = TelegramClient(
            SESSION_PATH,
            int(TELEGRAM_API_ID),
            TELEGRAM_API_HASH,
        )
        await client.start()
        me = await client.get_me()
        logger.info(
            "Telegram authenticated — account: %s (id=%s)",
            me.username or me.first_name,
            me.id,
        )
    except Exception as exc:
        logger.error("[e2e] Telethon connect/auth failed: %s", exc)
        counts["errors"] += 1
        return counts

    openai_client = _make_openai_client()

    try:
        # ── Per-channel message fetch ─────────────────────────────────────────
        for channel_info in CHANNELS:
            channel_username = channel_info["username"]
            channel_title    = channel_info["title"]

            logger.info("")
            logger.info(
                "[fetch] @%-20s  (%s)  max=%d messages",
                channel_username, channel_title, MAX_PER_CHANNEL,
            )

            try:
                messages_from_channel = []
                async for msg in client.iter_messages(channel_username, limit=MAX_PER_CHANNEL):
                    messages_from_channel.append(msg)
            except Exception as exc:
                logger.error(
                    "[fetch] Failed to fetch from @%s: %s — skipping",
                    channel_username, exc,
                )
                counts["errors"] += 1
                continue

            counts["channels_processed"] += 1
            counts["messages_fetched"]   += len(messages_from_channel)
            logger.info(
                "[fetch] @%s — retrieved %d messages",
                channel_username, len(messages_from_channel),
            )

            # ── Per-message pipeline ──────────────────────────────────────────
            for msg in messages_from_channel:

                # --- Media-only filter (Section A.1 — same rule as producer) ---
                if not msg.text or not msg.text.strip():
                    counts["media_only_filtered"] += 1
                    logger.debug(
                        "  @%s msg_id=%s — media-only, skipping",
                        channel_username, msg.id,
                    )
                    continue

                msg_short = msg.text[:80].replace("\n", " ")
                logger.info("")
                logger.info(
                    "  @%-18s  msg_id=%-8s | %s",
                    channel_username, msg.id, msg_short,
                )

                # --- Build raw payload (reuse producer's static method) ---
                try:
                    raw_payload = TelegramProducer._build_raw_payload(
                        msg, channel_username
                    )
                except Exception as exc:
                    counts["errors"] += 1
                    logger.error("  _build_raw_payload FAILED: %s", exc)
                    continue

                # --- Build Bronze envelope (http_status_code=0, request_duration_ms=0
                #     because Telethon MTProto streaming has no HTTP status, Section A.1) ---
                envelope = build_bronze_message(
                    source_name="telegram",
                    source_endpoint=_BRONZE_SOURCE_ENDPOINT,
                    raw_payload=raw_payload,
                    http_status_code=0,
                    request_duration_ms=0,
                )
                counts["bronze_built"] += 1

                # --- Silver routing ---
                silver_topic, silver = process_telegram_message(envelope)

                if silver_topic == DEAD_LETTER_QUEUE:
                    counts["silver_dlq"] += 1
                    stage  = silver.get("failed_stage", "?") if silver else "?"
                    errors = silver.get("validation_errors", []) if silver else ["unknown"]
                    logger.warning(
                        "  → Silver DLQ  stage=%s  errors=%s", stage, errors
                    )
                    continue

                if silver_topic != SILVER_GLOBAL_NEWS:
                    counts["errors"] += 1
                    logger.error("  → Unexpected Silver topic: %s", silver_topic)
                    continue

                counts["silver_ok"] += 1

                sniper_score   = silver.get("relevance_score", 0.0)
                is_high_signal = silver.get("is_high_signal", False)
                # Telegram: impact_boost is always False — no Impact Boost (Section A.1)
                impact_boost   = silver.get("impact_boost", False)

                if is_high_signal:
                    counts["keyword_sniper_passed"] += 1
                else:
                    counts["keyword_sniper_filtered"] += 1

                logger.info(
                    "  → Silver OK  sniper=%.3f  high_signal=%s  "
                    "impact_boost=%s  channel=@%s  views=%s",
                    sniper_score,
                    is_high_signal,
                    impact_boost,   # always False for Telegram
                    silver.get("channel_username", "?"),
                    silver.get("views"),
                )

                # --- Knowledge Vault persistence (Silver layer, Section 5.1) ---
                try:
                    doc_id = kv_archive(silver)
                except Exception as exc:
                    counts["errors"] += 1
                    logger.error("  → knowledge_vault.archive FAILED: %s", exc)
                    continue

                if doc_id is None:
                    counts["knowledge_vault_deduped"] += 1
                    logger.info(
                        "  → knowledge_vault DEDUPED (document_hash already exists — "
                        "same message_url seen before, Section 4.1C)"
                    )
                else:
                    counts["knowledge_vault_new"] += 1
                    logger.info("  → knowledge_vault NEW  doc_id=%s", doc_id)
                    # Patch silver with doc_id so build_telegram_gold_global_signal()
                    # can set silver_data_ref to the vault PK (Section C.5).
                    silver["doc_id"] = doc_id

                # --- Gold enrichment (high-signal messages only, Section 4.1A) ---
                if not is_high_signal:
                    logger.info(
                        "  → Gold SKIPPED (low-signal message, sniper_score=%.3f)",
                        sniper_score,
                    )
                    continue

                counts["openai_calls"] += 1
                gold_topic, gold = process_telegram_gold_message(
                    silver, openai_client=openai_client
                )

                if gold_topic == DEAD_LETTER_QUEUE:
                    counts["errors"] += 1
                    stage  = gold.get("failed_stage", "?") if gold else "?"
                    errors = gold.get("validation_errors", []) if gold else ["unknown"]
                    logger.warning(
                        "  → Gold DLQ  stage=%s  errors=%s", stage, errors
                    )
                    continue

                if gold is None:
                    # (None, None) path: is_high_signal guard inside process_telegram_gold_message
                    # Should not reach here (we already checked is_high_signal), but guard anyway.
                    logger.info("  → Gold returned (None, None) — skipping")
                    continue

                if gold_topic != GOLD_GLOBAL_NEWS:
                    counts["errors"] += 1
                    logger.error("  → Unexpected Gold topic: %s", gold_topic)
                    continue

                enrichment = gold.get("enrichment_ai", {})
                domain     = gold.get("domain_context", {})

                logger.info(
                    "  → Gold OK  impact=%s  urgency=%s  reliability=%.2f  "
                    "sentiment=%.2f  topic=%s",
                    enrichment.get("impact_level"),
                    enrichment.get("urgency_level"),
                    enrichment.get("reliability_score", 0.0),
                    enrichment.get("sentiment_score", 0.0),
                    enrichment.get("topic_classification", "?"),
                )
                logger.info(
                    "  → Direct Message context  channel=@%s  is_forwarded=%s  "
                    "views=%s  links=%d",
                    domain.get("channel_username", "?"),
                    domain.get("is_forwarded"),
                    domain.get("views"),
                    len(domain.get("extracted_links") or []),
                )

                # --- Knowledge Vectors persistence (Gold layer, Section 5.2) ---
                try:
                    signal_id = kvec_insert(gold)
                    counts["knowledge_vectors_new"] += 1
                    inserted_signal_ids.append(signal_id)
                    logger.info(
                        "  → knowledge_vectors NEW  signal_id=%s  "
                        "entry_type=direct_message",
                        signal_id,
                    )
                except Exception as exc:
                    counts["errors"] += 1
                    logger.error("  → knowledge_vectors.insert FAILED: %s", exc)

    finally:
        await client.disconnect()
        logger.info("")
        logger.info("Telegram client disconnected.")

    return counts, inserted_signal_ids


# ==========================================================
# Main runner
# ==========================================================

def run() -> dict:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        stream=sys.stdout,
    )
    logger = logging.getLogger("e2e_telegram")

    logger.info("=== Anizai Sprint 5 — Telegram E2E Integration Test ===")
    logger.info(
        "Channels: %d (Source Registry, Section A.1)  |  "
        "Max per channel: %d  |  "
        "Model: GPT-4o + text-embedding-3-small",
        len(CHANNELS), MAX_PER_CHANNEL,
    )
    logger.info(
        "Session file: %s.session  "
        "(first run prompts phone + verification code — one-time only)",
        SESSION_PATH,
    )

    _check_prerequisites()
    logger.info(
        "Prerequisites verified: TELEGRAM credentials present, "
        "OPENAI_API_KEY present, PostgreSQL reachable."
    )

    start_ts = time.time()

    result = asyncio.run(_run_async(logger))
    counts, inserted_signal_ids = result

    elapsed = time.time() - start_ts

    # ── Post-run sample readout ───────────────────────────────────────────────
    logger.info("")
    logger.info("=== Post-Run Sample (up to 3 records from knowledge_vectors) ===")
    logger.info(
        "(Direct Message domain_context: channel_username, is_forwarded, "
        "forwarded_from, views, extracted_links)"
    )

    sample_ids = inserted_signal_ids[:3]
    if not sample_ids:
        logger.warning(
            "  No Gold records inserted this run — "
            "check sniper scores, channel availability, and OpenAI connectivity."
        )
    else:
        for i, signal_id in enumerate(sample_ids, start=1):
            row = fetch_by_signal_id(signal_id)
            if row is None:
                logger.warning(
                    "  [%d] signal_id=%s not found in DB — skipping", i, signal_id
                )
                continue

            vitals     = row.get("content_vitals", {}) or {}
            enrichment = row.get("enrichment_ai",  {}) or {}
            domain     = row.get("domain_context",  {}) or {}

            # ── Example Gold object printout ─────────────────────────────────
            print(f"\n{'─' * 64}")
            print(f"  [{i}] {(vitals.get('title') or 'N/A')[:80]}")
            print(f"  Channel URL  : {(vitals.get('url') or 'N/A')[:78]}")
            print(f"  Source       : {row.get('source_platform', '?')}")
            print(f"  Entry Type   : {row.get('entry_type', '?')}")
            print(f"  Topic        : {enrichment.get('topic_classification', 'N/A')}")
            print()

            print("  Executive Summary:")
            summary = enrichment.get("executive_summary") or ""
            for line in _wrap(summary, 60):
                print(f"    {line}")
            print()

            key_findings = enrichment.get("key_findings") or []
            if key_findings:
                print("  Key Findings:")
                for finding in key_findings[:3]:
                    print(f"    • {str(finding)[:80]}")
                print()

            # Scores
            print(f"  impact_level       : {enrichment.get('impact_level', 'N/A')}")
            print(f"  urgency_level      : {enrichment.get('urgency_level', 'N/A')}")
            print(f"  reliability_score  : {enrichment.get('reliability_score', 'N/A')}")
            print(f"  sentiment_score    : {enrichment.get('sentiment_score', 'N/A')}")
            print(f"  fact_check_flag    : {enrichment.get('fact_check_flag', 'N/A')}")
            entities = enrichment.get("extracted_entities") or []
            print(f"  extracted_entities : {entities[:5]}")
            print()

            # Direct Message domain_context — the Telegram-specific extension (Section C.5)
            # Contrast with NewsAPI Tactical (sniper_keywords, is_breaking, share_count)
            # and ArXiv Academic (authors, is_peer_reviewed, citation_count, domain_tags)
            extracted_links = domain.get("extracted_links") or []
            print("  ── Direct Message Domain Context (Telegram) ─────────")
            print(f"  channel_username   : @{domain.get('channel_username', 'N/A')}")
            print(f"  is_forwarded       : {domain.get('is_forwarded', 'N/A')}")
            print(f"  forwarded_from     : {domain.get('forwarded_from', 'N/A')}")
            print(f"  views              : {domain.get('views', 'N/A')}")
            print(f"  extracted_links    : {extracted_links[:3]}")
            if len(extracted_links) > 3:
                print(f"                       ... (+{len(extracted_links) - 3} more)")

    # ── Final summary ─────────────────────────────────────────────────────────
    print(f"\n{'=' * 64}")
    print(f"  Sprint 5 E2E — Telegram — COMPLETE  ({elapsed:.1f}s)")
    print(f"{'=' * 64}")
    col_w = 30
    for key, val in counts.items():
        print(f"  {key:<{col_w}} {val}")
    print(f"{'=' * 64}")

    if counts["errors"] > 0:
        print(f"\n  {counts['errors']} error(s) above — check logs for details.")
    else:
        print(
            "\n  All messages processed without errors.\n"
            "  Telegram vertical slice end-to-end pipeline validated.\n"
            "  knowledge_vectors entries use entry_type='direct_message' "
            "and Direct Message domain_context (Section C.5)."
        )

    return counts


if __name__ == "__main__":
    run()
