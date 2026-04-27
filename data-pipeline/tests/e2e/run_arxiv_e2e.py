"""
Sprint 4 End-to-End Integration Test — ArXiv Vertical Slice.

Runs the full ArXiv pipeline in standalone mode (without a Flink cluster or
Kafka broker):
  1. Fetches real papers from the arXiv public Atom XML feed across a subset
     of categories (cs.AI, econ.GN, cs.CY) using the SNR keyword pre-filter
     (Section B.1, 2.2)
  2. Builds Bronze envelopes and routes them through the Silver Job
     (process_arxiv_message) — sniper scoring, SHA-256 dedup, Silver schema
     validation
  3. Archives all valid Silver records to knowledge_vault (Section 5.1,
     dedup-aware; source_name="arxiv")
  4. Runs high-signal Silver records through the Gold Job
     (process_arxiv_gold_message) using real GPT-4o cognitive metadata
     extraction and text-embedding-3-small (Section 4.2A)
  5. Inserts Gold records into knowledge_vectors (HNSW-indexed,
     ON CONFLICT DO NOTHING, Section 5.2; source_platform="arxiv",
     entry_type="arxiv_paper")
  6. Post-run: fetches 3 sample records from knowledge_vectors and prints
     Academic domain_context (authors, is_peer_reviewed, citation_count,
     domain_tags) — distinct from the NewsAPI Tactical domain_context
  7. Prints final pipeline counters

No Kafka broker required: ArXivProducer.__new__ bypasses Kafka producer init
(same pattern as run_newsapi_e2e.py). No API key required for arXiv — the
Atom XML feed is public (Section B.1).

Mandatory 3-second inter-category delay enforced throughout (arXiv TOS,
Section B.1). E2E_CATEGORIES is capped at 3 categories to keep total runtime
predictable and OpenAI token spend bounded.

Uses real OpenAI tokens — no mocks. Set OPENAI_API_KEY in
infrastructure/.env before running. No arXiv API key needed.

Usage (from data-pipeline/ with venv active):
    python -m tests.e2e.run_arxiv_e2e

PowerShell run command:
    cd C:\\Users\\ronki\\Desktop\\Anizai\\data-pipeline; `
    $env:PYTHONPATH = "."; `
    python -m tests.e2e.run_arxiv_e2e

References:
    - Section 2.1:  Producer Matrix (ArXiv row)
    - Section 2.2:  SNR Optimization — Domain Filtering Strategy
    - Section 4.1:  Silver Job specification (sniper, dedup, routing)
    - Section 4.2A: Gold Job — Cognitive Metadata Extraction
    - Section 5.1:  Knowledge Vault — Silver full-text persistence
    - Section 5.2:  Knowledge Vectors — Gold HNSW vector persistence
    - Section 9.3:  Triple-Gate Test Matrix — E2E validation
    - Section B.1:  ArXiv technical parameters — no API key, 3s delay TOS
    - Section C.5:  Gold Global Signal — Academic domain_context
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Optional

from config.kafka_topics import DEAD_LETTER_QUEUE, GOLD_GLOBAL_NEWS, SILVER_GLOBAL_NEWS
from config.settings import OPENAI_API_KEY
from ingestion.arxiv_producer import (
    ARXIV_API_URL,
    REQUEST_DELAY_SEC,
    SOURCE_NAME,
    ArXivProducer,
)
from persistence.knowledge_vault import archive as kv_archive
from persistence.knowledge_vectors import (
    fetch_by_signal_id,
    insert as kvec_insert,
)
from processing.gold_job import process_arxiv_gold_message
from processing.silver_job import process_arxiv_message
from utils.db import get_cursor
from utils.kafka_utils import build_bronze_message


# ==========================================================
# Runner parameters
# ==========================================================

# Subset of CATEGORIES for the E2E run — 3 categories balances coverage
# against OpenAI token spend and arXiv TOS 3-second inter-request delays.
# cs.AI  — Artificial Intelligence (core domain)
# econ.GN — General Economics (market/policy relevance)
# cs.CY  — Computers and Society (regulation, policy)
E2E_CATEGORIES = ["cs.AI", "econ.GN", "cs.CY"]

# Cap per category after fetch. Keeps E2E runtime predictable.
# With 3 categories × 5 papers × ~30s/Gold call ≈ ~7-8 min max.
MAX_PER_CATEGORY = 5


# ==========================================================
# Helpers
# ==========================================================

def _check_prerequisites() -> None:
    """
    Fail fast with clear messages if any required key or service is unavailable.

    arXiv requires no API key — only OPENAI_API_KEY and PostgreSQL are checked.
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


def _make_producer_helper() -> ArXivProducer:
    """
    Create an ArXivProducer without initialising the Kafka producer.

    The E2E test uses only the producer's fetch/parse/payload-build helpers
    (_fetch_page, _parse_entry, _build_raw_payload, _build_search_query),
    all of which are stateless with respect to the Kafka producer. Using
    __new__ avoids requiring a live Kafka broker (same pattern as
    run_newsapi_e2e.py).
    """
    p = ArXivProducer.__new__(ArXivProducer)
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
# Main runner
# ==========================================================

def run() -> dict:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        stream=sys.stdout,
    )
    logger = logging.getLogger("e2e_arxiv")

    logger.info("=== Anizai Sprint 4 — ArXiv E2E Integration Test ===")
    logger.info(
        "Categories: %s  |  Max per category: %d  |  "
        "Model: GPT-4o + text-embedding-3-small  |  No API key required",
        E2E_CATEGORIES, MAX_PER_CATEGORY,
    )
    logger.info(
        "Note: Mandatory 3-second inter-request delay (arXiv TOS, Section B.1) "
        "will be applied between each category fetch."
    )

    _check_prerequisites()
    logger.info(
        "Prerequisites verified: OPENAI_API_KEY present, PostgreSQL reachable. "
        "(arXiv requires no API key — public Atom XML feed.)"
    )

    producer      = _make_producer_helper()
    openai_client = _make_openai_client()

    counts: dict = {
        "papers_fetched":          0,
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

    # ── Collect papers from all categories ───────────────────────────────────
    # Each entry: (raw_payload_dict, category_str)
    paper_queue: list[tuple[dict, str]] = []

    for category in E2E_CATEGORIES:
        logger.info("")
        logger.info("[fetch] Fetching papers  category=%s ...", category)
        search_query = producer._build_search_query(category)
        source_endpoint = (
            f"{ARXIV_API_URL}?search_query={search_query}"
            f"&start=0&max_results={MAX_PER_CATEGORY}"
            f"&sortBy=submittedDate&sortOrder=descending"
        )
        try:
            entries, total_results, duration_ms = producer._fetch_page(
                search_query=search_query,
                start=0,
                max_results=MAX_PER_CATEGORY,
                sort_by="submittedDate",
                sort_order="descending",
            )
        except Exception as exc:
            logger.error(
                "[fetch] Error for category=%s: %s — skipping", category, exc
            )
            counts["errors"] += 1
            time.sleep(REQUEST_DELAY_SEC)
            continue

        for entry_data in entries:
            raw_payload = producer._build_raw_payload(
                entry_data, category, "pulse", search_query
            )
            paper_queue.append((raw_payload, category))

        logger.info(
            "[fetch] category=%-10s  fetched=%d  api_total=%d  %dms",
            category, len(entries), total_results, duration_ms,
        )
        # Mandatory TOS delay between category requests (Section B.1)
        time.sleep(REQUEST_DELAY_SEC)

    counts["papers_fetched"] = len(paper_queue)
    logger.info("")
    logger.info("=== Pipeline Run: %d papers queued ===", counts["papers_fetched"])

    # ── Per-paper pipeline ───────────────────────────────────────────────────
    for idx, (raw_payload, category) in enumerate(paper_queue, start=1):
        arxiv_id    = raw_payload.get("arxiv_id", "unknown")
        title_short = raw_payload.get("title", "")[:80]

        logger.info("")
        logger.info(
            "[%d/%d]  %-18s | %s",
            idx, counts["papers_fetched"],
            arxiv_id, title_short,
        )

        # --- Build Bronze envelope ---
        # source_endpoint encodes the query URL used to fetch this batch
        # for full Bronze-layer replayability (Section C.1).
        source_endpoint = (
            f"{ARXIV_API_URL}?search_query={producer._build_search_query(category)}"
            f"&start=0&max_results={MAX_PER_CATEGORY}"
            f"&sortBy=submittedDate&sortOrder=descending"
        )
        envelope = build_bronze_message(SOURCE_NAME, source_endpoint, raw_payload)
        counts["bronze_sent"] += 1

        # --- Silver routing ---
        silver_topic, silver = process_arxiv_message(envelope)

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
        # ArXiv: impact_boost is always False — no Impact Boost rule (Section B.1)
        impact_boost   = silver.get("impact_boost", False)

        if is_high_signal:
            counts["keyword_sniper_passed"] += 1
        else:
            counts["keyword_sniper_filtered"] += 1

        logger.info(
            "  → Silver OK  sniper=%.3f  high_signal=%s  impact_boost=%s  "
            "authors=%s",
            sniper_score,
            is_high_signal,
            impact_boost,   # always False for ArXiv
            str(silver.get("authors", []))[:60],
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
                "same canonical_url seen before, Section 4.1C)"
            )
        else:
            counts["knowledge_vault_new"] += 1
            logger.info("  → knowledge_vault NEW  doc_id=%s", doc_id)
            # Patch silver with doc_id so build_arxiv_gold_global_signal()
            # can set silver_data_ref to the vault PK (Section C.5).
            silver["doc_id"] = doc_id

        # --- Gold enrichment (high-signal papers only, Section 4.1A) ---
        if not is_high_signal:
            logger.info(
                "  → Gold SKIPPED (low-signal paper, sniper_score=%.3f)", sniper_score
            )
            continue

        counts["openai_calls"] += 1
        gold_topic, gold = process_arxiv_gold_message(silver, openai_client=openai_client)

        if gold_topic == DEAD_LETTER_QUEUE:
            counts["errors"] += 1
            stage  = gold.get("failed_stage", "?") if gold else "?"
            errors = gold.get("validation_errors", []) if gold else ["unknown"]
            logger.warning("  → Gold DLQ  stage=%s  errors=%s", stage, errors)
            continue

        if gold is None:
            # (None, None) path: is_high_signal=False guard inside process_arxiv_gold_message
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
            "  → Academic context  is_peer_reviewed=%s  citation_count=%s  "
            "domain_tags=%s",
            domain.get("is_peer_reviewed"),
            domain.get("citation_count"),
            domain.get("domain_tags", []),
        )

        # --- Knowledge Vectors persistence (Gold layer, Section 5.2) ---
        try:
            signal_id = kvec_insert(gold)
            counts["knowledge_vectors_new"] += 1
            inserted_signal_ids.append(signal_id)
            logger.info(
                "  → knowledge_vectors NEW  signal_id=%s  entry_type=arxiv_paper",
                signal_id,
            )
        except Exception as exc:
            counts["errors"] += 1
            logger.error("  → knowledge_vectors.insert FAILED: %s", exc)

    elapsed = time.time() - start_ts

    # ── Post-run sample readout ───────────────────────────────────────────────
    logger.info("")
    logger.info("=== Post-Run Sample (up to 3 records from knowledge_vectors) ===")
    logger.info(
        "(Academic domain_context: authors, is_peer_reviewed, "
        "citation_count, domain_tags)"
    )

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
            print(f"  arXiv URL    : {(vitals.get('url') or 'N/A')[:78]}")
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

            # Academic domain_context — the ArXiv-specific extension (Section C.5)
            # Contrast with NewsAPI Tactical fields (sniper_keywords, is_breaking, share_count)
            authors = domain.get("authors") or []
            print("  ── Academic Domain Context (ArXiv) ──────────────────")
            print(f"  is_peer_reviewed   : {domain.get('is_peer_reviewed', 'N/A')}")
            print(f"  citation_count     : {domain.get('citation_count', 'N/A')}")
            print(f"  domain_tags        : {domain.get('domain_tags', [])}")
            print(f"  authors ({len(authors)})       : {authors[:3]}")
            if len(authors) > 3:
                print(f"                       ... (+{len(authors) - 3} more)")

    # ── Final summary ─────────────────────────────────────────────────────────
    print(f"\n{'=' * 64}")
    print(f"  Sprint 4 E2E — ArXiv — COMPLETE  ({elapsed:.1f}s)")
    print(f"{'=' * 64}")
    col_w = 30
    for key, val in counts.items():
        print(f"  {key:<{col_w}} {val}")
    print(f"{'=' * 64}")

    if counts["errors"] > 0:
        print(f"\n  {counts['errors']} error(s) above — check logs for details.")
    else:
        print(
            "\n  All papers processed without errors.\n"
            "  ArXiv vertical slice end-to-end pipeline validated.\n"
            "  knowledge_vectors entries use entry_type='arxiv_paper' "
            "and Academic domain_context (Section C.5)."
        )

    return counts


if __name__ == "__main__":
    run()
