"""
Sprint 6 End-to-End Integration Test — HackerNews Vertical Slice.

Runs the full HackerNews pipeline in standalone mode (without a Flink cluster
or Kafka broker):
  1. Fetches real stories from the Algolia HackerNews Search API
     (public endpoint, no API key required — Section B.2)
  2. For each story, fetches the top comments via the Algolia items endpoint
  3. Builds Bronze envelopes and routes them through the Silver Job
     (process_hackernews_message) — SHA-256 dedup, sniper scoring, Silver
     schema validation → SILVER_SOCIAL_PULSE
  4. Archives all valid Silver records to social_vault (Section 5.1,
     dedup-aware via exists_by_content_hash)
  5. Runs high-signal Silver records through the Gold Job
     (process_hackernews_gold_message) using real GPT-4o Consensus Bundling
     and text-embedding-3-small (Section 4.2A)
  6. Inserts Gold records into social_vectors (HNSW-indexed,
     ON CONFLICT DO NOTHING, Section 5.2)
  7. Post-run: fetches 3 sample records from social_vectors and prints
     enrichment detail (title, executive summary, key findings, all scores,
     entities, community sentiment, platform_logic fields)
  8. Prints final pipeline counters

No Kafka broker required: HackerNewsProducer is used for its fetch/filter/
payload-build helpers only. __new__ bypasses the Kafka producer init (same
pattern as run_newsapi_e2e.py).

No API key required for Algolia (Section B.2 / 9.1): the HackerNews Algolia
Search API is fully public. Only OPENAI_API_KEY and a live PostgreSQL
container are required.

Usage (from data-pipeline/ with venv active):
    python -m tests.e2e.run_hackernews_e2e

PowerShell run command:
    cd C:\\Users\\ronki\\Desktop\\Anizai\\data-pipeline; `
    $env:PYTHONPATH = "."; `
    python -m tests.e2e.run_hackernews_e2e

References:
    - Section 2.1:  Producer Matrix (HackerNews row)
    - Section 4.1:  Silver Job specification (dedup, routing, sniper)
    - Section 4.2A: Gold Job — Consensus Bundling (title + top_comments)
    - Section 5.1:  Social Vault — Silver social discourse persistence
    - Section 5.2:  Social Vectors — Gold HNSW vector persistence
    - Section 9.1:  Environment parity — no HN API key required
    - Section 9.3:  Triple-Gate Test Matrix — E2E validation
    - Section B.2:  HackerNews parameters — Algolia endpoints, thresholds
    - Section C.3:  Silver Social Discourse Store — Story-Centric pattern
    - Section C.6:  Gold Social Pulse — entry_type="hackernews_story_summary"
"""

from __future__ import annotations

import logging
import sys
import time

from config.kafka_topics import DEAD_LETTER_QUEUE, GOLD_SOCIAL_PULSE, SILVER_SOCIAL_PULSE
from config.settings import OPENAI_API_KEY
from ingestion.hackernews_producer import (
    ALGOLIA_SEARCH_BY_DATE_URL,
    INTER_FETCH_DELAY_SEC,
    PULSE_MIN_POINTS,
    SOURCE_NAME,
    HackerNewsProducer,
)
from persistence.social_vault import exists_by_content_hash
from persistence.social_vault import insert as sv_insert
from persistence.social_vectors import fetch_by_signal_id
from persistence.social_vectors import insert as svec_insert
from processing.gold_job import process_hackernews_gold_message
from processing.silver_job import process_hackernews_message
from utils.db import get_cursor
from utils.kafka_utils import build_bronze_message


# ==========================================================
# Runner parameters
# ==========================================================

# Cap on stories processed per E2E run.
# Algolia returns up to PULSE_HITS_PER_PAGE=50 per pulse page; capping at 10
# keeps E2E runtime predictable and OpenAI token spend bounded.
# Each high-signal story triggers one GPT-4o + one embedding call.
MAX_STORIES = 10

# Algolia source_endpoint string — used in Bronze envelope metadata for
# full replay auditability (Section C.1).
PULSE_SOURCE_ENDPOINT = (
    f"{ALGOLIA_SEARCH_BY_DATE_URL}"
    f"?tags=story&numericFilters=points%3E{PULSE_MIN_POINTS}"
    f"&hitsPerPage=50"
)


# ==========================================================
# Helpers
# ==========================================================

def _check_prerequisites() -> None:
    """
    Fail fast with clear messages if any required key or service is unavailable.

    HackerNews Algolia API requires no key (Section B.2). Only OPENAI_API_KEY
    and a reachable PostgreSQL container are checked.

    Checked before the main loop so any misconfiguration is surfaced before
    API calls or DB writes are attempted (same pattern as run_newsapi_e2e.py).
    """
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


def _make_producer_helper() -> HackerNewsProducer:
    """
    Create a HackerNewsProducer without initialising the Kafka producer.

    The E2E test uses only the producer's fetch/payload-build helpers:
        _fetch_stories_page(), _fetch_story_item(),
        _extract_top_comments(), _build_raw_payload().
    All are stateless with respect to the Kafka producer. Using __new__
    avoids requiring a live Kafka broker (same pattern as run_newsapi_e2e.py).
    """
    p = HackerNewsProducer.__new__(HackerNewsProducer)
    p._emitted = 0
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
    """Word-wrap a string to the given column width for post-run printout."""
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
    logger = logging.getLogger("e2e_hackernews")

    logger.info("=== Anizai Sprint 6 — HackerNews E2E Integration Test ===")
    logger.info(
        "Source: Algolia HackerNews API (no key required)  |  "
        "points>%d  |  cap=%d stories  |  "
        "Model: GPT-4o + text-embedding-3-small",
        PULSE_MIN_POINTS, MAX_STORIES,
    )

    _check_prerequisites()
    logger.info(
        "Prerequisites verified: OPENAI_API_KEY present, PostgreSQL reachable. "
        "(No HN API key required — Algolia endpoint is public.)"
    )

    producer      = _make_producer_helper()
    openai_client = _make_openai_client()

    counts: dict = {
        "stories_fetched":         0,
        "bronze_built":            0,
        "silver_ok":               0,
        "silver_dlq":              0,
        "keyword_sniper_passed":   0,
        "keyword_sniper_filtered": 0,
        "social_vault_new":        0,
        "social_vault_deduped":    0,
        "social_vectors_new":      0,
        "openai_calls":            0,
        "errors":                  0,
    }

    # signal_ids of Gold records inserted this run — used for post-run sampling.
    inserted_signal_ids: list[str] = []

    start_ts = time.time()

    # ── Fetch pulse stories from Algolia ─────────────────────────────────────
    logger.info("")
    logger.info("[fetch] Fetching latest stories from Algolia (points>%d) ...", PULSE_MIN_POINTS)

    params = {
        "tags":           "story",
        "numericFilters": f"points>{PULSE_MIN_POINTS}",
        "hitsPerPage":    50,
    }

    try:
        hits, _, search_duration_ms = producer._fetch_stories_page(
            ALGOLIA_SEARCH_BY_DATE_URL, params
        )
    except Exception as exc:
        logger.error("[fetch] Algolia search failed: %s — aborting", exc)
        counts["errors"] += 1
        _print_summary(counts, inserted_signal_ids, time.time() - start_ts, logger)
        return counts

    hits = hits[:MAX_STORIES]
    counts["stories_fetched"] = len(hits)
    logger.info(
        "[fetch] Algolia returned %d stories (%dms) — capping at %d",
        len(hits), search_duration_ms, MAX_STORIES,
    )

    # ── Per-story pipeline ───────────────────────────────────────────────────
    for idx, hit in enumerate(hits, start=1):
        story_id    = hit.get("objectID") or "unknown"
        title_short = (hit.get("title") or "")[:80]

        logger.info("")
        logger.info(
            "[%d/%d]  story_id=%-10s | points=%-4s | %s",
            idx, counts["stories_fetched"],
            story_id,
            hit.get("points", "?"),
            title_short,
        )

        # --- Fetch comments from Algolia items endpoint ---
        top_comments: list[dict] = []
        try:
            item_data, _ = producer._fetch_story_item(story_id)
            top_comments = producer._extract_top_comments(item_data)
            logger.info("  → items endpoint: %d comments fetched", len(top_comments))
        except Exception as exc:
            logger.warning(
                "  → items endpoint FAILED (story_id=%s): %s — proceeding without comments",
                story_id, exc,
            )
        finally:
            time.sleep(INTER_FETCH_DELAY_SEC)

        # --- Build Bronze envelope ---
        raw_payload = producer._build_raw_payload(hit, top_comments, "pulse")
        envelope    = build_bronze_message(SOURCE_NAME, PULSE_SOURCE_ENDPOINT, raw_payload)
        counts["bronze_built"] += 1

        # --- Silver routing ---
        silver_topic, silver = process_hackernews_message(envelope)

        if silver_topic == DEAD_LETTER_QUEUE:
            counts["silver_dlq"] += 1
            stage  = silver.get("failed_stage", "?") if silver else "?"
            errors = silver.get("validation_errors", []) if silver else ["unknown"]
            logger.warning("  → Silver DLQ  stage=%s  errors=%s", stage, errors)
            continue

        if silver_topic != SILVER_SOCIAL_PULSE:
            counts["errors"] += 1
            logger.error("  → Unexpected Silver topic: %s", silver_topic)
            continue

        counts["silver_ok"] += 1

        sniper_score   = silver.get("relevance_score", 0.0)
        is_high_signal = silver.get("is_high_signal", False)

        if is_high_signal:
            counts["keyword_sniper_passed"] += 1
        else:
            counts["keyword_sniper_filtered"] += 1

        logger.info(
            "  → Silver OK  sniper=%.3f  high_signal=%s  keywords=%s",
            sniper_score,
            is_high_signal,
            silver.get("sniper_keywords", []),
        )

        # --- Social Vault persistence (Silver layer, Section 5.1) ---
        content_hash = silver.get("content_hash", "")
        try:
            if content_hash and exists_by_content_hash(content_hash):
                counts["social_vault_deduped"] += 1
                logger.info("  → social_vault DEDUPED (content_hash already exists)")
            else:
                social_id = sv_insert(silver)
                counts["social_vault_new"] += 1
                # Patch so Gold can reference the Silver vault row (Section C.6 silver_data_ref).
                # Known gap: Gold job currently uses bronze_ref as silver_data_ref fallback
                # (task_plan.md Known Gaps — deferred until Flink deployment).
                silver["social_id"] = social_id
                logger.info("  → social_vault NEW  social_id=%s", social_id)
        except Exception as exc:
            counts["errors"] += 1
            logger.error("  → social_vault FAILED: %s", exc)
            continue

        # --- Gold enrichment (high-signal stories only, Section 4.1A) ---
        if not is_high_signal:
            logger.info(
                "  → Gold SKIPPED (low-signal story, sniper_score=%.3f)", sniper_score
            )
            continue

        counts["openai_calls"] += 1
        gold_topic, gold = process_hackernews_gold_message(
            silver, openai_client=openai_client
        )

        if gold_topic == DEAD_LETTER_QUEUE:
            counts["errors"] += 1
            stage  = gold.get("failed_stage", "?") if gold else "?"
            errors = gold.get("validation_errors", []) if gold else ["unknown"]
            logger.warning("  → Gold DLQ  stage=%s  errors=%s", stage, errors)
            continue

        if gold_topic != GOLD_SOCIAL_PULSE:
            counts["errors"] += 1
            logger.error("  → Unexpected Gold topic: %s", gold_topic)
            continue

        enrichment = gold.get("enrichment_ai", {})
        platform   = gold.get("platform_logic", {})
        logger.info(
            "  → Gold OK  impact=%s  urgency=%s  reliability=%.2f  "
            "sentiment=%.2f  story_type=%s  points=%s",
            enrichment.get("impact_level"),
            enrichment.get("urgency_level"),
            enrichment.get("reliability_score", 0.0),
            platform.get("community_sentiment", 0.0),
            platform.get("story_type", "?"),
            platform.get("points", "?"),
        )

        # --- Social Vectors persistence (Gold layer, Section 5.2) ---
        try:
            signal_id = svec_insert(gold)
            counts["social_vectors_new"] += 1
            inserted_signal_ids.append(signal_id)
            logger.info("  → social_vectors NEW  signal_id=%s", signal_id)
        except Exception as exc:
            counts["errors"] += 1
            logger.error("  → social_vectors.insert FAILED: %s", exc)

    elapsed = time.time() - start_ts
    _print_sample(inserted_signal_ids, logger)
    _print_summary(counts, inserted_signal_ids, elapsed, logger)
    return counts


# ==========================================================
# Post-run helpers
# ==========================================================

def _print_sample(inserted_signal_ids: list[str], logger) -> None:
    """
    Fetch and print 3 sample Gold records from social_vectors.

    Prints the full C.6 Gold Social Pulse fields including HackerNews
    platform_logic extension: story_type, points, top_technical_insights,
    external_link_ref, community_sentiment, has_raw_source.
    """
    logger.info("")
    logger.info("=== Post-Run Sample (3 records from social_vectors) ===")

    sample_ids = inserted_signal_ids[:3]
    if not sample_ids:
        logger.warning(
            "  No Gold records inserted this run — "
            "check sniper scores and OpenAI availability."
        )
        return

    for i, signal_id in enumerate(sample_ids, start=1):
        row = fetch_by_signal_id(signal_id)
        if row is None:
            logger.warning("  [%d] signal_id=%s not found in DB — skipping", i, signal_id)
            continue

        vitals     = row.get("content_vitals",  {}) or {}
        enrichment = row.get("enrichment_ai",   {}) or {}
        social_ctx = row.get("social_context",  {}) or {}
        platform   = row.get("platform_logic",  {}) or {}

        print(f"\n{'─' * 64}")
        print(f"  [{i}] {(vitals.get('title') or 'N/A')[:78]}")
        print(f"  Platform   : {row.get('source_platform', '?')}"
              f"  |  entry_type: {platform.get('entry_type', '?')}")
        print(f"  URL        : {(vitals.get('url') or 'N/A')[:78]}")
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

        print(f"  impact_level          : {enrichment.get('impact_level', 'N/A')}")
        print(f"  urgency_level         : {enrichment.get('urgency_level', 'N/A')}")
        print(f"  reliability_score     : {enrichment.get('reliability_score', 'N/A')}")
        print(f"  fact_check_flag       : {enrichment.get('fact_check_flag', 'N/A')}")
        print(f"  consensus_rating      : {enrichment.get('consensus_rating', 'N/A')}")
        print(f"  uncertainty_index     : {enrichment.get('uncertainty_index', 'N/A')}")
        entities = enrichment.get("extracted_entities") or []
        print(f"  extracted_entities    : {entities[:5]}")
        print()
        print(f"  community_name        : {social_ctx.get('community_name', 'N/A')}")
        print(f"  engagement_score      : {social_ctx.get('primary_engagement_score', 'N/A')}")
        print()
        print(f"  story_type            : {platform.get('story_type', 'N/A')}")
        print(f"  points                : {platform.get('points', 'N/A')}")
        print(f"  community_sentiment   : {platform.get('community_sentiment', 'N/A')}")
        print(f"  has_raw_source        : {platform.get('has_raw_source', 'N/A')}")
        insights = platform.get("top_technical_insights") or []
        if insights:
            print("  top_technical_insights:")
            for ins in insights[:3]:
                print(f"    • {str(ins)[:80]}")
        ext_link = platform.get("external_link_ref")
        if ext_link:
            print(f"  external_link_ref     : {str(ext_link)[:78]}")


def _print_summary(
    counts: dict,
    inserted_signal_ids: list[str],
    elapsed: float,
    logger,
) -> None:
    """Print the final pipeline counter summary."""
    print(f"\n{'=' * 64}")
    print(f"  Sprint 6 E2E — HackerNews — COMPLETE  ({elapsed:.1f}s)")
    print(f"{'=' * 64}")
    col_w = 30
    for key, val in counts.items():
        print(f"  {key:<{col_w}} {val}")
    print(f"{'=' * 64}")

    if counts["errors"] > 0:
        print(f"\n  {counts['errors']} error(s) above — check logs for details.")
    else:
        print(
            "\n  All stories processed without errors.\n"
            "  HackerNews vertical slice end-to-end pipeline validated."
        )


if __name__ == "__main__":
    run()
