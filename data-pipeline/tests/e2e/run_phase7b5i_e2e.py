"""
Phase 7B.5-I End-to-End Validation — Filter Observability & Cost Instrumentation (T6).

Emulates the CURRENT GlobalNewsGoldFunction.process_element flow standalone
(no Kafka/Flink — same pattern as run_newsapi_e2e.py) against the live local
Postgres and real OpenAI, and verifies every T6 claim:

  (a) Rejects land in filter_rejects with all fields populated (flag ON).
  (b) Rescued survivors carry rescue_cosine; sniper-passed rows read NULL.
  (c) Every OpenAI call produced exactly one llm_cost_events row; the
      run-summary view aggregates correctly.
  (d) Drop/rescue decisions are IDENTICAL with instrumentation flag ON vs
      OFF (same silver docs through the gate twice) — the "purely additive"
      baseline gate.
  (e) Flag OFF: zero reject rows; cost rows still written.

Inputs: two real newsapi.ai category fetches (bounded) PLUS three synthetic
clearly-off-domain articles (celebrity / recipes / local sports) injected as
Bronze envelopes — they deterministically fail the sniper and (very likely)
the rescue, guaranteeing the drop/capture path is exercised with real
embedding calls even on a news day when everything genuine passes.

Two gate passes over the same Silver docs:
  PASS A — flag OFF, RUN_ID=<tag>-off:  decisions only (no archive, no Gold)
           → proves (d) invariance + (e) flag-OFF semantics.
  PASS B — flag ON,  RUN_ID=<tag>-on:   full flow (archive, Gold, vectors)
           → proves (a), (b), (c).

Usage (from data-pipeline/ with venv active; Postgres up, migration 003 applied):
    venv\\Scripts\\python.exe -m tests.e2e.run_phase7b5i_e2e

References:
    - docs/A_pipeline/plans/phase7b5i_filter_observability_and_cost.md §3 T6, §4
    - tests/e2e/run_newsapi_e2e.py (fetch/standalone conventions reused)
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone

from config.kafka_topics import DEAD_LETTER_QUEUE, GOLD_GLOBAL_NEWS, SILVER_GLOBAL_NEWS
from config.settings import (
    GOLD_SEMANTIC_RESCUE_THRESHOLD,
    NEWSAI_API_KEY,
    OPENAI_API_KEY,
)
from ingestion.newsapi_producer import (
    NEWSAI_GETARTICLES_URL,
    REQUEST_DELAY_SEC,
    SOURCE_NAME,
    NewsAPIProducer,
)
from persistence.filter_rejects import fetch_rejects
from persistence.knowledge_vault import archive as kv_archive, fetch_by_doc_id
from persistence.knowledge_vectors import insert as kvec_insert
from persistence.llm_cost_events import fetch_events
from processing.gold_job import (
    apply_rescue_outcome,
    compute_semantic_rescue,
    process_newsapi_gold_message,
)
from processing.keyword_sniper import SNIPER_REFERENCE_VECTOR_PATH
from processing.silver_job import process_newsapi_message
from utils.db import get_cursor
from utils.kafka_utils import build_bronze_message

import config.settings as settings


# ==========================================================
# Runner parameters
# ==========================================================

E2E_CATEGORIES   = ["news/Business", "news/Health"]   # Health skews low-signal
MAX_PER_CATEGORY = 5

# Off-domain synthetics — deterministic sniper failures; bodies are long
# enough to be realistic and verify FULL-text retention in filter_rejects.
_SYNTHETIC_ARTICLES = [
    {
        "url":         "https://example.com/e2e-7b5i/celebrity-gala",
        "title":       "Stars dazzle on the red carpet at annual charity gala",
        "description": "Celebrities gathered for a night of fashion and fundraising.",
        "body": (
            "The annual charity gala drew a crowd of film and television stars, "
            "with designers debuting bespoke gowns and tuxedos on the red carpet. "
            "Attendees enjoyed a five-course dinner and a silent auction of "
            "memorabilia, with proceeds supporting local arts programs. "
        ) * 12,
        "dateTime":    datetime.now(timezone.utc).isoformat(),
        "source":      {"uri": "reuters.com", "title": "Reuters"},
        "authors":     [],
    },
    {
        "url":         "https://example.com/e2e-7b5i/sourdough-recipes",
        "title":       "Five sourdough recipes to master this weekend",
        "description": "From classic boules to inventive discard crackers.",
        "body": (
            "Home bakers continue to embrace sourdough, and this weekend is the "
            "perfect time to refine your starter routine. Begin with a classic "
            "country boule, then experiment with olive and rosemary folds. "
            "Discard need not go to waste: crackers and pancakes await. "
        ) * 12,
        "dateTime":    datetime.now(timezone.utc).isoformat(),
        "source":      {"uri": "bbc.com", "title": "BBC News"},
        "authors":     [],
    },
    {
        "url":         "https://example.com/e2e-7b5i/local-sports-day",
        "title":       "Village sports day ends in friendly tie after rain delay",
        "description": "The annual egg-and-spoon race was the highlight once again.",
        "body": (
            "The village green hosted its beloved annual sports day, briefly "
            "interrupted by an afternoon shower. Families picnicked along the "
            "boundary while children competed in sack races and tug of war. "
            "Organisers praised volunteers for a smooth, cheerful event. "
        ) * 12,
        "dateTime":    datetime.now(timezone.utc).isoformat(),
        "source":      {"uri": "apnews.com", "title": "Associated Press"},
        "authors":     [],
    },
]


logger = logging.getLogger("e2e_7b5i")


# ==========================================================
# Helpers (conventions from run_newsapi_e2e.py)
# ==========================================================

def _check_prerequisites() -> None:
    if not NEWSAI_API_KEY:
        print("\n[e2e] ERROR: NEWSAI_API_KEY not set in .env.\n")
        sys.exit(1)
    if not OPENAI_API_KEY:
        print("\n[e2e] ERROR: OPENAI_API_KEY not set — this run uses real tokens.\n")
        sys.exit(1)
    if not SNIPER_REFERENCE_VECTOR_PATH.exists():
        print(f"\n[e2e] ERROR: sniper reference vector missing: {SNIPER_REFERENCE_VECTOR_PATH}\n")
        sys.exit(1)
    try:
        with get_cursor() as cur:
            for obj in ("filter_rejects", "llm_cost_events"):
                cur.execute("SELECT to_regclass(%s) AS t;", (f"public.{obj}",))
                if cur.fetchone()["t"] is None:
                    print(f"\n[e2e] ERROR: table {obj} missing — apply migration 003 first.\n")
                    sys.exit(1)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"\n[e2e] ERROR: PostgreSQL not reachable: {exc}\n")
        sys.exit(1)


def _make_producer_helper() -> NewsAPIProducer:
    p = NewsAPIProducer.__new__(NewsAPIProducer)
    p._emitted            = 0
    p._filtered_whitelist = 0
    return p


def _make_openai_client():
    from openai import OpenAI
    return OpenAI(api_key=OPENAI_API_KEY)


def _site_counts(run_id: str) -> dict[str, int]:
    with get_cursor() as cur:
        cur.execute(
            "SELECT site, count(*) AS n FROM llm_cost_events "
            "WHERE run_id = %s GROUP BY site;",
            (run_id,),
        )
        return {r["site"]: r["n"] for r in cur.fetchall()}


def _print_run_summary(run_id: str) -> None:
    with get_cursor() as cur:
        cur.execute(
            "SELECT source_name, usage_type, calls, prompt_tokens, "
            "completion_tokens, total_tokens, cost_usd "
            "FROM llm_cost_run_summary WHERE run_id = %s "
            "ORDER BY source_name, usage_type;",
            (run_id,),
        )
        rows = cur.fetchall()
    print(f"\n  llm_cost_run_summary  run_id={run_id}")
    print(f"  {'source':<10} {'usage_type':<14} {'calls':>5} {'prompt':>8} "
          f"{'compl':>7} {'total':>8} {'cost_usd':>10}")
    for r in rows:
        print(f"  {r['source_name']:<10} {r['usage_type']:<14} {r['calls']:>5} "
              f"{r['prompt_tokens']:>8} {r['completion_tokens']:>7} "
              f"{r['total_tokens']:>8} {float(r['cost_usd']):>10.4f}")


# ==========================================================
# Main runner
# ==========================================================

def run() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        stream=sys.stdout,
    )

    stamp   = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    tag_off = f"e2e-7b5i-{stamp}-off"
    tag_on  = f"e2e-7b5i-{stamp}-on"

    logger.info("=== Phase 7B.5-I E2E — Filter Observability & Cost Instrumentation ===")
    logger.info("Run tags: OFF=%s  ON=%s  threshold=%.2f",
                tag_off, tag_on, GOLD_SEMANTIC_RESCUE_THRESHOLD)

    _check_prerequisites()
    producer = _make_producer_helper()
    client   = _make_openai_client()

    import numpy as np
    ref_vec = np.load(str(SNIPER_REFERENCE_VECTOR_PATH)).astype(np.float32)

    failures: list[str] = []

    def check(cond: bool, label: str) -> None:
        status = "PASS" if cond else "FAIL"
        logger.info("  [%s] %s", status, label)
        if not cond:
            failures.append(label)

    # ── Fetch real articles + inject synthetics ─────────────────────────
    article_queue: list[tuple[dict, str]] = []
    for category in E2E_CATEGORIES:
        try:
            articles, _, _ = producer._fetch_articles(category)
        except Exception as exc:
            logger.error("[fetch] category=%s failed: %s — skipping", category, exc)
            continue
        whitelisted = [a for a in articles if producer._passes_whitelist(a)]
        article_queue.extend((a, category) for a in whitelisted[:MAX_PER_CATEGORY])
        logger.info("[fetch] %-14s raw=%3d whitelisted=%3d taking=%d",
                    category, len(articles), len(whitelisted),
                    len(whitelisted[:MAX_PER_CATEGORY]))
        time.sleep(REQUEST_DELAY_SEC)

    article_queue.extend((a, "") for a in _SYNTHETIC_ARTICLES)
    logger.info("[fetch] queued %d articles (%d real + %d synthetic off-domain)",
                len(article_queue), len(article_queue) - len(_SYNTHETIC_ARTICLES),
                len(_SYNTHETIC_ARTICLES))

    # ── Silver pass ──────────────────────────────────────────────────────
    silver_docs: list[dict] = []
    silver_dlq = 0
    for article, category in article_queue:
        raw_payload = producer._build_raw_payload(article, category, "pulse")
        endpoint = (
            f"{NEWSAI_GETARTICLES_URL}?categoryUri={category}"
            if category else NEWSAI_GETARTICLES_URL
        )
        envelope = build_bronze_message(SOURCE_NAME, endpoint, raw_payload)
        topic, silver = process_newsapi_message(envelope)
        if topic == DEAD_LETTER_QUEUE:
            silver_dlq += 1
            continue
        if topic == SILVER_GLOBAL_NEWS:
            silver_docs.append(silver)

    high  = [d for d in silver_docs if d.get("is_high_signal")]
    low   = [d for d in silver_docs if not d.get("is_high_signal")]
    logger.info("[silver] ok=%d dlq=%d | sniper passed=%d failed=%d",
                len(silver_docs), silver_dlq, len(high), len(low))
    if not low:
        logger.error("[e2e] No sniper-failed docs — cannot exercise the gate. Aborting.")
        return 1

    # ── PASS A — flag OFF (decisions only; proves d + e) ─────────────────
    logger.info("")
    logger.info("=== PASS A — flag OFF  (run_id=%s) ===", tag_off)
    settings.RUN_ID = tag_off

    decisions_a: dict[str, tuple[bool, float]] = {}
    rescue_calls_a = 0
    for doc in low:
        probe = dict(doc)                       # copy — Pass B gets the original
        try:
            rescued, sim = compute_semantic_rescue(
                probe, client, ref_vec, GOLD_SEMANTIC_RESCUE_THRESHOLD)
            rescue_calls_a += 1
        except Exception as exc:
            logger.error("  rescue embed failed (→ DLQ in prod): %s", exc)
            continue
        survived = apply_rescue_outcome(probe, rescued, sim, capture_enabled=False)
        decisions_a[doc["original_url"]] = (survived, sim)
        logger.info("  A: %-55s cosine=%.4f → %s",
                    doc["original_url"][:55], sim,
                    "PROMOTE" if survived else "DROP")

    # ── PASS B — flag ON (full flow; proves a + b + c) ───────────────────
    logger.info("")
    logger.info("=== PASS B — flag ON  (run_id=%s) ===", tag_on)
    settings.RUN_ID = tag_on

    decisions_b: dict[str, tuple[bool, float]] = {}
    rescue_calls_b = 0
    gold_attempts  = 0
    gold_dlq       = 0
    archived_direct:  list[tuple[str, dict]] = []   # (doc_id, doc) sniper-passed
    archived_rescued: list[tuple[str, dict]] = []   # (doc_id, doc) rescue-promoted
    dropped = 0

    for doc in silver_docs:
        if not doc.get("is_high_signal", False):
            try:
                rescued, sim = compute_semantic_rescue(
                    doc, client, ref_vec, GOLD_SEMANTIC_RESCUE_THRESHOLD)
                rescue_calls_b += 1
            except Exception as exc:
                logger.error("  rescue embed failed (→ DLQ in prod): %s", exc)
                continue
            survived = apply_rescue_outcome(
                doc, rescued, sim, capture_enabled=True, run_id=tag_on)
            decisions_b[doc["original_url"]] = (survived, sim)
            logger.info("  B: %-55s cosine=%.4f → %s",
                        doc["original_url"][:55], sim,
                        "PROMOTE" if survived else "DROP")
            if not survived:
                dropped += 1
                continue                        # no kv_archive, no Gold

        # Archive (survivors only — mirrors process_element order)
        try:
            doc_id = kv_archive(doc)
        except Exception as exc:
            logger.error("  kv_archive failed: %s", exc)
            continue
        if doc_id:
            doc["doc_id"] = doc_id
            if "rescue_cosine" in doc:
                archived_rescued.append((doc_id, doc))
            else:
                archived_direct.append((doc_id, doc))

        # Gold enrichment (real GPT + embedding spend)
        gold_attempts += 1
        gold_topic, gold = process_newsapi_gold_message(doc, openai_client=client)
        if gold_topic == DEAD_LETTER_QUEUE:
            gold_dlq += 1
            logger.warning("  gold DLQ: %s", (gold or {}).get("failed_stage"))
            continue
        if gold_topic == GOLD_GLOBAL_NEWS:
            try:
                kvec_insert(gold)
            except Exception as exc:
                logger.error("  knowledge_vectors.insert failed: %s", exc)

    # ── Promote-leg probe ────────────────────────────────────────────────
    # If no article landed in the rescue window this run (a real
    # distribution fact worth reporting for 7B.5), exercise the LIVE
    # promote → archive-with-cosine → Gold path anyway: re-run the
    # highest-cosine dropped doc against an EXPLICIT probe threshold set
    # just below its score. The threshold is a parameter of
    # compute_semantic_rescue — production settings are not touched.
    if not archived_rescued and decisions_b:
        probe_url = max(
            (u for u, (s, _) in decisions_b.items() if not s),
            key=lambda u: decisions_b[u][1],
            default=None,
        )
        probe_src = next(
            (d for d in silver_docs if d.get("original_url") == probe_url), None,
        )
        if probe_src is not None:
            probe_doc = dict(probe_src)
            probe_doc.pop("rescue_cosine", None)
            probe_threshold = max(0.05, decisions_b[probe_url][1] - 0.05)
            logger.info("")
            logger.info("=== Promote-leg probe (threshold=%.2f, explicit) ===",
                        probe_threshold)
            try:
                rescued, sim = compute_semantic_rescue(
                    probe_doc, client, ref_vec, probe_threshold)
                rescue_calls_b += 1
                survived = apply_rescue_outcome(
                    probe_doc, rescued, sim, capture_enabled=True, run_id=tag_on)
                logger.info("  probe: %-50s cosine=%.4f → %s",
                            probe_url[:50], sim,
                            "PROMOTE" if survived else "DROP")
                if survived:
                    doc_id = kv_archive(probe_doc)
                    if doc_id:
                        probe_doc["doc_id"] = doc_id
                        archived_rescued.append((doc_id, probe_doc))
                    gold_attempts += 1
                    gold_topic, gold = process_newsapi_gold_message(
                        probe_doc, openai_client=client)
                    if gold_topic == DEAD_LETTER_QUEUE:
                        gold_dlq += 1
                    elif gold_topic == GOLD_GLOBAL_NEWS:
                        kvec_insert(gold)
            except Exception as exc:
                logger.error("  probe failed: %s", exc)

    settings.RUN_ID = ""   # restore

    # ── Verification ─────────────────────────────────────────────────────
    logger.info("")
    logger.info("=== Verification ===")

    # (d) invariance — identical decisions A vs B on the same docs
    common = set(decisions_a) & set(decisions_b)
    flips = {
        u: (decisions_a[u], decisions_b[u])
        for u in common if decisions_a[u][0] != decisions_b[u][0]
    }
    max_delta = max(
        (abs(decisions_a[u][1] - decisions_b[u][1]) for u in common),
        default=0.0,
    )
    check(len(common) == len(decisions_a) == len(decisions_b),
          f"(d) both passes decided every low-signal doc ({len(common)} docs)")
    check(not flips, f"(d) drop/promote decisions identical ON vs OFF "
                     f"(max cosine delta={max_delta:.6f}; flips={flips or 'none'})")
    rate_a = sum(1 for s, _ in decisions_a.values() if not s)
    rate_b = sum(1 for s, _ in decisions_b.values() if not s)
    check(rate_a == rate_b, f"(d) drop count identical: OFF={rate_a} ON={rate_b}")

    # (e) flag OFF wrote NO rejects but DID write cost rows
    rejects_off = fetch_rejects(run_id=tag_off, limit=1000)
    events_off  = _site_counts(tag_off)
    check(len(rejects_off) == 0, f"(e) flag OFF: zero filter_rejects rows")
    check(events_off.get("rescue_embed", 0) == rescue_calls_a,
          f"(e) flag OFF: cost rows still written "
          f"(rescue_embed={events_off.get('rescue_embed', 0)} == calls={rescue_calls_a})")

    # (a) flag ON captured every drop, fields populated, full text intact
    rejects_on = fetch_rejects(run_id=tag_on, limit=1000)
    check(len(rejects_on) == dropped,
          f"(a) filter_rejects rows == drops ({len(rejects_on)} == {dropped})")
    by_url = {r["original_url"]: r for r in rejects_on}
    fields_ok = all(
        r["source_name"] and r["title"] is not None
        and r["relevance_score"] is not None and r["rescue_cosine"] is not None
        and r["rescue_cosine"] < GOLD_SEMANTIC_RESCUE_THRESHOLD
        for r in rejects_on
    )
    check(fields_ok or not rejects_on, "(a) every reject row fully populated, cosine < threshold")
    text_ok = True
    for doc in silver_docs:
        r = by_url.get(doc.get("original_url"))
        if r is not None and r["full_text_raw"] != doc.get("full_text_raw", ""):
            text_ok = False
    check(text_ok, "(a) reject full_text_raw is byte-identical (untruncated)")

    # (b) cosine on rescued rows; NULL on sniper-passed rows
    cosine_ok = True
    for doc_id, doc in archived_rescued:
        row = fetch_by_doc_id(doc_id)
        if row["rescue_cosine"] is None or \
           abs(row["rescue_cosine"] - doc["rescue_cosine"]) > 1e-5:
            cosine_ok = False
    check(cosine_ok, f"(b) {len(archived_rescued)} rescued vault row(s) carry the exact cosine")
    null_ok = all(
        fetch_by_doc_id(doc_id)["rescue_cosine"] is None
        for doc_id, _ in archived_direct
    )
    check(null_ok, f"(b) {len(archived_direct)} sniper-passed vault row(s) read back NULL")

    # (c) exactly one cost row per OpenAI call, per site
    events_on = _site_counts(tag_on)
    check(events_on.get("rescue_embed", 0) == rescue_calls_b,
          f"(c) rescue_embed events == rescue calls "
          f"({events_on.get('rescue_embed', 0)} == {rescue_calls_b})")
    check(events_on.get("gold_enrich", 0) == gold_attempts,
          f"(c) gold_enrich events == gold attempts "
          f"({events_on.get('gold_enrich', 0)} == {gold_attempts})")
    embed_events = events_on.get("gold_embed", 0)
    check(gold_attempts - gold_dlq <= embed_events <= gold_attempts,
          f"(c) gold_embed events consistent ({embed_events} for "
          f"{gold_attempts} attempts, {gold_dlq} DLQ)")

    _print_run_summary(tag_on)

    # ── Summary ──────────────────────────────────────────────────────────
    print(f"\n{'=' * 64}")
    print("  PHASE 7B.5-I E2E SUMMARY")
    print(f"{'=' * 64}")
    print(f"  articles queued          : {len(article_queue)} "
          f"({len(article_queue) - len(_SYNTHETIC_ARTICLES)} real + "
          f"{len(_SYNTHETIC_ARTICLES)} synthetic)")
    print(f"  silver ok / dlq          : {len(silver_docs)} / {silver_dlq}")
    print(f"  sniper passed / failed   : {len(high)} / {len(low)}")
    print(f"  PASS A (off) rescue calls: {rescue_calls_a}  drops={rate_a}")
    print(f"  PASS B (on)  rescue calls: {rescue_calls_b}  drops={rate_b} "
          f"rescued={len(archived_rescued)}")
    print(f"  vault archived           : {len(archived_direct)} direct + "
          f"{len(archived_rescued)} rescued")
    print(f"  gold attempts / dlq      : {gold_attempts} / {gold_dlq}")
    print(f"  reject rows (ON tag)     : {len(rejects_on)}")
    print(f"  cost events ON  by site  : {events_on}")
    print(f"  cost events OFF by site  : {events_off}")
    print(f"  checks failed            : {len(failures)}")
    for f in failures:
        print(f"    FAIL: {f}")
    print(f"{'=' * 64}\n")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(run())
