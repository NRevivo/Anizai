"""
Sprint 3 End-to-End Integration Test — NewsAPI Vertical Slice.

Runs the full NewsAPI pipeline in standalone mode (without a Flink cluster or
Kafka broker):
  1. Fetches real articles from NewsAPI (business + technology categories, plus a
     targeted Israel/Middle East fetch to exercise the Impact Boost rule — Section B.4)
  2. Applies the authority whitelist at the producer layer
  3. Builds Bronze envelopes and routes them through the Silver Job
     (process_newsapi_message) — sniper scoring, SHA-256 dedup, Silver schema validation
  4. Archives all valid Silver records to knowledge_vault (Section 5.1, dedup-aware)
  5. Runs high-signal Silver records through the Gold Job
     (process_newsapi_gold_message) using real GPT-4o cognitive metadata extraction
     and text-embedding-3-small (Section 4.2A)
  6. Inserts Gold records into knowledge_vectors (HNSW-indexed, ON CONFLICT DO NOTHING,
     Section 5.2)
  7. Post-run: fetches 3 sample records from knowledge_vectors and prints enrichment
     detail (title, executive summary, key findings, all scores, entities, topic)
  8. Prints final pipeline counters

No Kafka broker required: the NewsAPI producer is used for its fetch/filter/payload-build
helpers only. __new__ bypasses the Kafka producer init (same pattern as run_fred_e2e.py).

Uses real OpenAI tokens — no mocks. Set OPENAI_API_KEY and NEWS_API_KEY in
infrastructure/.env before running.

Usage (from data-pipeline/ with venv active):
    python -m tests.e2e.run_newsapi_e2e

PowerShell run command:
    cd C:\\Users\\ronki\\Desktop\\Anizai\\data-pipeline; `
    $env:PYTHONPATH = "."; `
    python -m tests.e2e.run_newsapi_e2e

References:
    - Section 2.1:  Producer Matrix (NewsAPI row)
    - Section 4.1:  Silver Job specification (sniper, dedup, routing)
    - Section 4.2A: Gold Job — Cognitive Metadata Extraction
    - Section 5.1:  Knowledge Vault — Silver full-text persistence
    - Section 5.2:  Knowledge Vectors — Gold HNSW vector persistence
    - Section 9.3:  Triple-Gate Test Matrix — E2E validation
    - Section B.4:  NewsAPI parameters — Authority Whitelist, Impact Boost rule
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import date, timedelta
from typing import Optional

from config.kafka_topics import DEAD_LETTER_QUEUE, GOLD_GLOBAL_NEWS, SILVER_GLOBAL_NEWS
from config.settings import NEWS_API_KEY, OPENAI_API_KEY
from ingestion.newsapi_producer import (
    EVERYTHING_ENDPOINT,
    REQUEST_DELAY_SEC,
    SOURCE_NAME,
    TOP_HEADLINES_ENDPOINT,
    NewsAPIProducer,
)
from persistence.knowledge_vault import archive as kv_archive
from persistence.knowledge_vectors import (
    fetch_by_signal_id,
    insert as kvec_insert,
)
from processing.gold_job import process_newsapi_gold_message
from processing.silver_job import process_newsapi_message
from utils.db import get_cursor
from utils.kafka_utils import build_bronze_message


# ==========================================================
# Runner parameters
# ==========================================================

# Standard pulse categories to exercise for this E2E.
# "general" is intentionally excluded — Israel/ME articles are fetched
# directly via /everything to guarantee whitelist-passing Impact Boost hits.
E2E_CATEGORIES = ["business", "technology"]

# Cap per category after the whitelist filter. The top-headlines endpoint
# can return up to PAGE_SIZE=100; capping keeps E2E runtime predictable
# and OpenAI token spend bounded.
MAX_PER_CATEGORY = 8

# Cap on Israel/Middle East articles from the /everything targeted fetch.
MAX_ISRAEL_ARTICLES = 5

# Day window for the Israel/ME targeted fetch — keeps articles fresh.
ISRAEL_FETCH_DAYS = 3


# ==========================================================
# Helpers
# ==========================================================

def _check_prerequisites() -> None:
    """
    Fail fast with clear messages if any required key or service is unavailable.

    Checked before the main loop so any misconfiguration is surfaced before
    API calls or DB writes are attempted (same pattern as run_fred_e2e.py).
    """
    if not NEWS_API_KEY:
        print(
            "\n[e2e] ERROR: NEWS_API_KEY is not set in .env.\n"
            "Set it to your Massive.com Stocks Starter Plan key (Section B.4).\n"
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


def _make_producer_helper() -> NewsAPIProducer:
    """
    Create a NewsAPIProducer without initialising the Kafka producer.

    The E2E test uses only the producer's fetch/filter/payload-build helpers,
    all of which are stateless with respect to the Kafka producer. Using __new__
    avoids requiring a live Kafka broker (same pattern as run_fred_e2e.py).
    """
    p = NewsAPIProducer.__new__(NewsAPIProducer)
    p._emitted            = 0
    p._filtered_whitelist = 0
    p._filtered_keyword   = 0
    return p


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
    words  = text.split()
    lines: list[str] = []
    line:  list[str] = []
    col    = 0
    for word in words:
        need = len(word) + (1 if line else 0)
        if col + need > width:
            lines.append(" ".join(line))
            line = [word]
            col  = len(word)
        else:
            line.append(word)
            col += need
    if line:
        lines.append(" ".join(line))
    return lines or [""]


# ==========================================================
# Main runner
# ==========================================================

def run() -> dict:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        stream=sys.stdout,
    )
    logger = logging.getLogger("e2e_newsapi")

    logger.info("=== Anizai Sprint 3 — NewsAPI E2E Integration Test ===")
    logger.info(
        "Categories: %s  |  Israel/ME fetch: last %d days  |  "
        "Model: GPT-4o + text-embedding-3-small",
        E2E_CATEGORIES, ISRAEL_FETCH_DAYS,
    )

    _check_prerequisites()
    logger.info(
        "Prerequisites verified: NEWS_API_KEY present, OPENAI_API_KEY present, "
        "PostgreSQL reachable."
    )

    producer      = _make_producer_helper()
    openai_client = _make_openai_client()

    counts: dict = {
        "articles_fetched":        0,
        "bronze_sent":             0,
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

    # signal_ids of Gold records inserted this run — used for post-run sampling.
    inserted_signal_ids: list[str] = []

    start_ts = time.time()

    # ── Collect articles from all fetch rounds ────────────────────────────────
    # Each entry: (article_dict, category_str)
    # category="" for /everything articles (backfill convention from newsapi_producer.py)
    article_queue: list[tuple[dict, str]] = []

    # --- Round 1: Standard pulse categories (business + technology) ---
    for category in E2E_CATEGORIES:
        logger.info("")
        logger.info("[fetch] Fetching top-headlines  category=%s ...", category)
        try:
            articles, _, _ = producer._fetch_top_headlines(category)
        except Exception as exc:
            logger.error("[fetch] HTTP/network error  category=%s: %s — skipping", category, exc)
            counts["errors"] += 1
            time.sleep(REQUEST_DELAY_SEC)
            continue

        whitelisted = [a for a in articles if producer._passes_whitelist(a)]
        capped      = whitelisted[:MAX_PER_CATEGORY]
        article_queue.extend((a, category) for a in capped)
        logger.info(
            "[fetch] category=%-12s  raw=%3d  whitelisted=%3d  taking=%d",
            category, len(articles), len(whitelisted), len(capped),
        )
        time.sleep(REQUEST_DELAY_SEC)

    # --- Round 2: Israel / Middle East targeted fetch (/everything) ---
    # Explicitly targets Impact Boost articles (Section B.4): articles mentioning
    # "israel", "hamas", "gaza", or "iran" with any whitelisted source (Reuters,
    # AP, BBC, etc.) will have raw_payload.impact_boost=True, which the Gold Job
    # converts to impact_level = min(5, gpt_score + 1).
    logger.info("")
    logger.info("[fetch] Fetching Israel/ME articles via /everything (Impact Boost exercise) ...")
    today     = date.today()
    from_date = (today - timedelta(days=ISRAEL_FETCH_DAYS)).isoformat()
    to_date   = today.isoformat()
    try:
        israel_articles, _, _ = producer._fetch_everything(
            from_date=from_date,
            to_date=to_date,
            sources=None,
            keywords="israel OR hamas OR gaza OR iran OR hezbollah",
        )
        whitelisted_il = [a for a in israel_articles if producer._passes_whitelist(a)]
        capped_il      = whitelisted_il[:MAX_ISRAEL_ARTICLES]
        article_queue.extend((a, "") for a in capped_il)
        logger.info(
            "[fetch] Israel/ME  raw=%3d  whitelisted=%3d  taking=%d",
            len(israel_articles), len(whitelisted_il), len(capped_il),
        )
    except Exception as exc:
        logger.warning(
            "[fetch] Israel/ME fetch failed: %s — proceeding without Impact Boost articles",
            exc,
        )
    time.sleep(REQUEST_DELAY_SEC)

    counts["articles_fetched"] = len(article_queue)
    logger.info("")
    logger.info("=== Pipeline Run: %d articles queued ===", counts["articles_fetched"])

    # ── Per-article pipeline ─────────────────────────────────────────────────
    for idx, (article, category) in enumerate(article_queue, start=1):
        source_name = (article.get("source") or {}).get("name", "unknown")
        title_short = (article.get("title") or "")[:80]

        logger.info("")
        logger.info(
            "[%d/%d]  %-20s | %s",
            idx, counts["articles_fetched"],
            source_name, title_short,
        )

        # Determine the endpoint label for the Bronze envelope metadata.
        # /top-headlines for standard categories; /everything for Israel fetch.
        endpoint = (
            f"{TOP_HEADLINES_ENDPOINT}?category={category}"
            if category in E2E_CATEGORIES
            else EVERYTHING_ENDPOINT
        )

        # --- Build Bronze envelope ---
        raw_payload = producer._build_raw_payload(article, category, "pulse")
        envelope    = build_bronze_message(SOURCE_NAME, endpoint, raw_payload)
        counts["bronze_sent"] += 1

        # --- Silver routing ---
        silver_topic, silver = process_newsapi_message(envelope)

        if silver_topic == DEAD_LETTER_QUEUE:
            counts["silver_dlq"] += 1
            stage  = silver.get("failed_stage", "?") if silver else "?"
            errors = silver.get("validation_errors", []) if silver else ["unknown"]
            logger.warning("  → Silver DLQ  stage=%s  errors=%s", stage, errors)
            continue

        if silver_topic != SILVER_GLOBAL_NEWS:
            counts["errors"] += 1
            logger.error("  → Unexpected Silver topic: %s", silver_topic)
            continue

        counts["silver_ok"] += 1

        sniper_score   = silver.get("relevance_score", 0.0)
        is_high_signal = silver.get("is_high_signal", False)
        impact_boost   = raw_payload.get("impact_boost", False)

        if is_high_signal:
            counts["keyword_sniper_passed"] += 1
        else:
            counts["keyword_sniper_filtered"] += 1

        logger.info(
            "  → Silver OK  sniper=%.3f  high_signal=%s  impact_boost=%s  keywords=%s",
            sniper_score,
            is_high_signal,
            impact_boost,
            silver.get("sniper_keywords", []),
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
            logger.info("  → knowledge_vault DEDUPED (document_hash already exists)")
        else:
            counts["knowledge_vault_new"] += 1
            logger.info("  → knowledge_vault NEW  doc_id=%s", doc_id)
            # Patch silver_data_ref so the Gold record links back to this vault row
            # (build_gold_global_signal reads silver_doc.get("doc_id"), Section C.5).
            silver["doc_id"] = doc_id

        # --- Gold enrichment (high-signal articles only, Section 4.1A) ---
        if not is_high_signal:
            logger.info(
                "  → Gold SKIPPED (low-signal article, sniper_score=%.3f)", sniper_score
            )
            continue

        counts["openai_calls"] += 1
        gold_topic, gold = process_newsapi_gold_message(silver, openai_client=openai_client)

        if gold_topic == DEAD_LETTER_QUEUE:
            counts["errors"] += 1
            stage  = gold.get("failed_stage", "?") if gold else "?"
            errors = gold.get("validation_errors", []) if gold else ["unknown"]
            logger.warning("  → Gold DLQ  stage=%s  errors=%s", stage, errors)
            continue

        if gold_topic != GOLD_GLOBAL_NEWS:
            counts["errors"] += 1
            logger.error("  → Unexpected Gold topic: %s", gold_topic)
            continue

        enrichment = gold.get("enrichment_ai", {})
        logger.info(
            "  → Gold OK  impact=%s  urgency=%s  reliability=%.2f  "
            "sentiment=%.2f  topic=%s%s",
            enrichment.get("impact_level"),
            enrichment.get("urgency_level"),
            enrichment.get("reliability_score", 0.0),
            enrichment.get("sentiment_score", 0.0),
            enrichment.get("topic_classification", "?"),
            "  [IMPACT BOOSTED +1]" if impact_boost else "",
        )

        # --- Knowledge Vectors persistence (Gold layer, Section 5.2) ---
        try:
            signal_id = kvec_insert(gold)
            counts["knowledge_vectors_new"] += 1
            inserted_signal_ids.append(signal_id)
            logger.info("  → knowledge_vectors NEW  signal_id=%s", signal_id)
        except Exception as exc:
            counts["errors"] += 1
            logger.error("  → knowledge_vectors.insert FAILED: %s", exc)

    elapsed = time.time() - start_ts

    # ── Post-run sample readout ───────────────────────────────────────────────
    logger.info("")
    logger.info("=== Post-Run Sample (3 records from knowledge_vectors) ===")

    sample_ids = inserted_signal_ids[:3]
    if not sample_ids:
        logger.warning(
            "  No Gold records inserted this run — "
            "check sniper scores and OpenAI availability."
        )
    else:
        for i, signal_id in enumerate(sample_ids, start=1):
            row = fetch_by_signal_id(signal_id)
            if row is None:
                logger.warning("  [%d] signal_id=%s not found in DB — skipping", i, signal_id)
                continue

            vitals     = row.get("content_vitals", {}) or {}
            enrichment = row.get("enrichment_ai",  {}) or {}
            domain     = row.get("domain_context",  {}) or {}

            print(f"\n{'─' * 64}")
            print(f"  [{i}] {(vitals.get('title') or 'N/A')[:80]}")
            print(f"  Source    : {row.get('source_platform', '?')}")
            print(f"  URL       : {(vitals.get('url') or 'N/A')[:78]}")
            print(f"  Topic     : {enrichment.get('topic_classification', 'N/A')}")
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

            print(f"  impact_level       : {enrichment.get('impact_level', 'N/A')}")
            print(f"  urgency_level      : {enrichment.get('urgency_level', 'N/A')}")
            print(f"  reliability_score  : {enrichment.get('reliability_score', 'N/A')}")
            print(f"  sentiment_score    : {enrichment.get('sentiment_score', 'N/A')}")
            print(f"  fact_check_flag    : {enrichment.get('fact_check_flag', 'N/A')}")
            entities = enrichment.get("extracted_entities") or []
            print(f"  extracted_entities : {entities[:5]}")
            print(f"  geospatial_focus   : {domain.get('geospatial_focus', 'N/A')}")
            print(f"  sniper_keywords    : {domain.get('sniper_keywords', [])}")
            print(f"  is_breaking        : {domain.get('is_breaking', False)}")

    # ── Final summary ─────────────────────────────────────────────────────────
    print(f"\n{'=' * 64}")
    print(f"  Sprint 3 E2E — NewsAPI — COMPLETE  ({elapsed:.1f}s)")
    print(f"{'=' * 64}")
    col_w = 30
    for key, val in counts.items():
        print(f"  {key:<{col_w}} {val}")
    print(f"{'=' * 64}")

    if counts["errors"] > 0:
        print(f"\n  {counts['errors']} error(s) above — check logs for details.")
    else:
        print(
            "\n  All articles processed without errors.\n"
            "  NewsAPI vertical slice end-to-end pipeline validated."
        )

    return counts


if __name__ == "__main__":
    run()
