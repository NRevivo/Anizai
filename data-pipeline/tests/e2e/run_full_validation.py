"""
Sprint 17 — Full End-to-End Validation Script.

Queries every vault table and prints a structured terminal summary showing
per-source record counts, sample enriched objects, triggers fired, and
scraping progress.

Run AFTER data has been collected (30–60 minutes after triggering DAGs):

    cd data-pipeline
    python -m tests.e2e.run_full_validation

Exit codes:
    0 — all expected sources have at least 1 record
    1 — one or more expected sources are empty (check for failures)

Expected sources (knowledge path):    newsapi, arxiv, telegram
Expected sources (social path):       hackernews, polymarket
Expected sources (momentum path):     fred, googletrends, openweather, opensky

Requires:
    - data-pipeline/.env with POSTGRES_* credentials
    - PostgreSQL container running (anizai-postgres on localhost:5432)
    - venv active with all dependencies installed

References:
    - Section 5: Multi-Vault Strategy
    - Section C.5 / C.6: Gold Signal schemas
    - Section 7.2: Pipeline Observability
"""

from __future__ import annotations

import sys
import textwrap
from typing import Any

# ---------------------------------------------------------------------------
# Import the centralised DB connection (Section 3.2 — DRY, utils/db.py).
# Using the existing connection pool means this script honours the same
# .env credentials as every other pipeline module.
# ---------------------------------------------------------------------------
try:
    from utils.db import get_connection
except ImportError as exc:
    print(
        "[ERROR] Could not import utils.db — make sure you are running from the "
        "data-pipeline/ directory with the venv active.\n"
        f"  ImportError: {exc}"
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Sources that must have ≥1 record for exit code 0.
REQUIRED_KNOWLEDGE_SOURCES = {"newsapi", "arxiv", "telegram"}
REQUIRED_SOCIAL_SOURCES = {"hackernews", "polymarket"}
REQUIRED_MOMENTUM_SOURCES = {"fred", "googletrends", "openweather", "opensky"}

LINE_WIDTH = 60


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _divider(char: str = "=") -> str:
    return char * LINE_WIDTH


def _header(title: str) -> str:
    pad = LINE_WIDTH - len(title) - 2
    left = pad // 2
    right = pad - left
    return f"\n{'=' * left} {title} {'=' * right}"


def _truncate(text: str | None, width: int = 80) -> str:
    if not text:
        return "(empty)"
    text = str(text).replace("\n", " ").strip()
    return textwrap.shorten(text, width=width, placeholder="…")


def _run_query(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Execute a read-only query and return all rows as dicts."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Per-table query functions
# ---------------------------------------------------------------------------

def _knowledge_vault_summary() -> dict:
    counts_rows = _run_query(
        "SELECT source_name, COUNT(*) AS n "
        "FROM knowledge_vault "
        "GROUP BY source_name ORDER BY source_name"
    )
    counts = {r["source_name"]: int(r["n"]) for r in counts_rows}

    scrape_rows = _run_query(
        "SELECT scrape_attempted, COUNT(*) AS n "
        "FROM knowledge_vault "
        "GROUP BY scrape_attempted"
    )
    scrape = {r["scrape_attempted"]: int(r["n"]) for r in scrape_rows}

    sample_rows = _run_query(
        "SELECT source_name, "
        "       LEFT(full_text_raw, 100) AS text_preview, "
        "       relevance_score "
        "FROM knowledge_vault "
        "ORDER BY ingested_at DESC LIMIT 1"
    )
    return {
        "counts": counts,
        "scrape_attempted": scrape.get(True, 0),
        "scrape_pending": scrape.get(False, 0),
        "sample": sample_rows[0] if sample_rows else None,
    }


def _knowledge_vectors_summary() -> dict:
    counts_rows = _run_query(
        "SELECT source_platform, COUNT(*) AS n "
        "FROM knowledge_vectors "
        "GROUP BY source_platform ORDER BY source_platform"
    )
    counts = {r["source_platform"]: int(r["n"]) for r in counts_rows}

    sample_rows = _run_query(
        "SELECT source_platform, "
        "       content_vitals->>'title'                 AS title, "
        "       (enrichment_ai->>'impact_level')::int    AS impact, "
        "       (enrichment_ai->>'urgency_level')::int   AS urgency, "
        "       (enrichment_ai->>'sentiment_score')::float AS sentiment "
        "FROM knowledge_vectors "
        "ORDER BY ingested_at DESC LIMIT 1"
    )
    return {
        "counts": counts,
        "sample": sample_rows[0] if sample_rows else None,
    }


def _social_vault_summary() -> dict:
    counts_rows = _run_query(
        "SELECT source_name, COUNT(*) AS n "
        "FROM social_vault "
        "GROUP BY source_name ORDER BY source_name"
    )
    return {"counts": {r["source_name"]: int(r["n"]) for r in counts_rows}}


def _social_vectors_summary() -> dict:
    counts_rows = _run_query(
        "SELECT source_platform, entry_type, COUNT(*) AS n "
        "FROM social_vectors "
        "GROUP BY source_platform, entry_type ORDER BY source_platform"
    )
    counts: dict[str, int] = {}
    for r in counts_rows:
        counts[r["source_platform"]] = counts.get(r["source_platform"], 0) + int(r["n"])

    sample_rows = _run_query(
        "SELECT source_platform, "
        "       entry_type, "
        "       content_vitals->>'title'                AS title, "
        "       social_context->>'community_name'       AS community, "
        "       (enrichment_ai->>'impact_level')::int   AS impact, "
        "       (enrichment_ai->>'sentiment_score')::float AS sentiment "
        "FROM social_vectors "
        "ORDER BY ingested_at DESC LIMIT 1"
    )
    return {
        "counts": counts,
        "sample": sample_rows[0] if sample_rows else None,
    }


def _momentum_vault_summary() -> dict:
    counts_rows = _run_query(
        "SELECT source_name, COUNT(*) AS n "
        "FROM momentum_vault "
        "GROUP BY source_name ORDER BY source_name"
    )
    counts = {r["source_name"]: int(r["n"]) for r in counts_rows}

    trigger_rows = _run_query(
        "SELECT source_name, "
        "       external_reference_id, "
        "       metadata_extension->>'trigger_type'  AS trigger_type, "
        "       metadata_extension->>'trigger_label' AS trigger_label, "
        "       current_value, "
        "       timestamp_utc "
        "FROM momentum_vault "
        "WHERE metadata_extension->>'trigger_type' IS NOT NULL "
        "ORDER BY timestamp_utc DESC "
        "LIMIT 5"
    )

    trigger_count_rows = _run_query(
        "SELECT COUNT(*) AS n "
        "FROM momentum_vault "
        "WHERE metadata_extension->>'trigger_type' IS NOT NULL"
    )
    trigger_total = int(trigger_count_rows[0]["n"]) if trigger_count_rows else 0

    return {
        "counts": counts,
        "trigger_total": trigger_total,
        "trigger_samples": trigger_rows,
    }


def _mapping_dict_summary() -> dict:
    total_rows = _run_query("SELECT COUNT(*) AS n FROM mapping_dict")
    total = int(total_rows[0]["n"]) if total_rows else 0

    platform_rows = _run_query(
        "SELECT platform, COUNT(*) AS n "
        "FROM mapping_dict "
        "GROUP BY platform ORDER BY platform"
    )
    return {
        "total": total,
        "by_platform": {r["platform"]: int(r["n"]) for r in platform_rows},
    }


def _trace_id_sample() -> str | None:
    """Return one raw_data_ref from knowledge_vectors as a trace_id hint."""
    rows = _run_query(
        "SELECT raw_data_ref::text AS trace_id "
        "FROM knowledge_vectors "
        "WHERE raw_data_ref IS NOT NULL "
        "ORDER BY ingested_at DESC LIMIT 1"
    )
    return rows[0]["trace_id"] if rows else None


# ---------------------------------------------------------------------------
# Validation logic — determine pass/fail
# ---------------------------------------------------------------------------

def _collect_empty_sources(
    kv_counts: dict,
    sv_counts: dict,
    mv_counts: dict,
) -> list[str]:
    """Return list of expected sources that have 0 records."""
    empty = []
    for src in sorted(REQUIRED_KNOWLEDGE_SOURCES):
        if kv_counts.get(src, 0) == 0:
            empty.append(f"knowledge_vault[{src}]")
    for src in sorted(REQUIRED_SOCIAL_SOURCES):
        if sv_counts.get(src, 0) == 0:
            empty.append(f"social_vault[{src}]")
    for src in sorted(REQUIRED_MOMENTUM_SOURCES):
        if mv_counts.get(src, 0) == 0:
            empty.append(f"momentum_vault[{src}]")
    return empty


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------

def _print_report(
    kv: dict,
    kvec: dict,
    sv: dict,
    svec: dict,
    mv: dict,
    md: dict,
    trace_id: str | None,
    empty_sources: list[str],
) -> None:
    print()
    print(_divider())
    print("  ANIZAI PIPELINE — FULL VALIDATION REPORT")
    print(_divider())

    # ------------------------------------------------------------------
    # KNOWLEDGE PATH
    # ------------------------------------------------------------------
    print(_header("KNOWLEDGE PATH"))
    print("  Sources: NewsAPI, ArXiv, Telegram\n")

    # knowledge_vault counts
    kv_counts = kv["counts"]
    total_kv = sum(kv_counts.values())
    print(f"  knowledge_vault   : {total_kv:>5} records total")
    for src in sorted(REQUIRED_KNOWLEDGE_SOURCES | set(kv_counts)):
        n = kv_counts.get(src, 0)
        flag = "" if n > 0 else "  ← EMPTY"
        print(f"    {src:<14}: {n:>5}{flag}")

    # knowledge_vectors counts
    kvec_counts = kvec["counts"]
    total_kvec = sum(kvec_counts.values())
    print(f"\n  knowledge_vectors : {total_kvec:>5} vectors")
    for src in sorted(REQUIRED_KNOWLEDGE_SOURCES | set(kvec_counts)):
        n = kvec_counts.get(src, 0)
        print(f"    {src:<14}: {n:>5}")

    # scraping
    scraped = kv["scrape_attempted"]
    pending = kv["scrape_pending"]
    total_scrape = scraped + pending
    pct = f"{(scraped / total_scrape * 100):.0f}%" if total_scrape else "n/a"
    print(f"\n  Scraped articles  : {scraped}/{total_scrape} attempted  ({pct} complete)")

    # sample
    kv_sample = kv.get("sample")
    kvec_sample = kvec.get("sample")
    if kvec_sample:
        title = _truncate(kvec_sample.get("title"), 60)
        impact = kvec_sample.get("impact") or "?"
        urgency = kvec_sample.get("urgency") or "?"
        sentiment = kvec_sample.get("sentiment")
        sentiment_str = f"{sentiment:+.2f}" if sentiment is not None else "?"
        print(f"\n  Sample Gold record:")
        print(f"    Title    : {title}")
        print(f"    impact={impact}  urgency={urgency}  sentiment={sentiment_str}")
    elif kv_sample:
        src = kv_sample.get("source_name", "?")
        preview = _truncate(kv_sample.get("text_preview"), 60)
        score = kv_sample.get("relevance_score", 0)
        print(f"\n  Sample Silver record ({src}):")
        print(f"    Text preview: {preview}")
        print(f"    relevance_score: {score:.2f}")
    else:
        print("\n  Sample: (no records yet)")

    # ------------------------------------------------------------------
    # SOCIAL PATH
    # ------------------------------------------------------------------
    print(_header("SOCIAL PATH"))
    print("  Sources: Polymarket, HackerNews\n")

    sv_counts = sv["counts"]
    total_sv = sum(sv_counts.values())
    print(f"  social_vault      : {total_sv:>5} records total")
    for src in sorted(REQUIRED_SOCIAL_SOURCES | set(sv_counts)):
        n = sv_counts.get(src, 0)
        flag = "" if n > 0 else "  ← EMPTY"
        print(f"    {src:<14}: {n:>5}{flag}")

    svec_counts = svec["counts"]
    total_svec = sum(svec_counts.values())
    print(f"\n  social_vectors    : {total_svec:>5} consensus summaries")
    for src in sorted(REQUIRED_SOCIAL_SOURCES | set(svec_counts)):
        n = svec_counts.get(src, 0)
        print(f"    {src:<14}: {n:>5}")

    svec_sample = svec.get("sample")
    if svec_sample:
        title = _truncate(svec_sample.get("title"), 60)
        community = svec_sample.get("community") or svec_sample.get("source_platform", "?")
        impact = svec_sample.get("impact") or "?"
        sentiment = svec_sample.get("sentiment")
        sentiment_str = f"{sentiment:+.2f}" if sentiment is not None else "?"
        print(f"\n  Sample Gold record:")
        print(f"    Title     : {title}")
        print(f"    community={community}  impact={impact}  sentiment={sentiment_str}")
    else:
        print("\n  Sample: (no records yet)")

    # ------------------------------------------------------------------
    # MOMENTUM PATH
    # ------------------------------------------------------------------
    print(_header("MOMENTUM PATH"))
    print("  Sources: FRED, GoogleTrends, OpenWeather, OpenSky, Polymarket prices\n")

    mv_counts = mv["counts"]
    total_mv = sum(mv_counts.values())
    print(f"  momentum_vault    : {total_mv:>5} records total")
    all_mv_sources = REQUIRED_MOMENTUM_SOURCES | set(mv_counts)
    for src in sorted(all_mv_sources):
        n = mv_counts.get(src, 0)
        flag = "" if n > 0 else "  ← EMPTY"
        print(f"    {src:<14}: {n:>5}{flag}")

    # polymarket prices may appear as 'polymarket' in momentum_vault
    poly_mv = mv_counts.get("polymarket", 0)
    if poly_mv:
        print(f"    {'polymarket':<14}: {poly_mv:>5}  (price updates via WebSocket)")

    trigger_total = mv["trigger_total"]
    trigger_samples = mv["trigger_samples"]
    print(f"\n  Triggers fired    : {trigger_total:>5} total")
    if trigger_samples:
        print("  Recent triggers:")
        for t in trigger_samples:
            label = t.get("trigger_label") or t.get("trigger_type") or "?"
            src = t.get("source_name", "?")
            ref = t.get("external_reference_id", "?")
            val = t.get("current_value")
            val_str = f"{val:.4g}" if val is not None else "?"
            ts = str(t.get("timestamp_utc", ""))[:19]
            print(f"    {label:<35} | {src}/{ref} | val={val_str} | {ts}")
    else:
        print("  (no triggers fired yet — normal on first run if FRED/OpenWeather values"
              " are within normal ranges)")

    # ------------------------------------------------------------------
    # MAPPING DICT
    # ------------------------------------------------------------------
    print(_header("MAPPING DICT"))
    print(f"  Total linkages    : {md['total']:>5}")
    if md["by_platform"]:
        for platform, n in sorted(md["by_platform"].items()):
            print(f"    {platform:<16}: {n:>5}")
    else:
        print("  (empty — grows as data volume increases across sources)")

    # ------------------------------------------------------------------
    # LOGGING / TRACING
    # ------------------------------------------------------------------
    print(_header("DISTRIBUTED TRACING"))
    if trace_id:
        print(f"  Sample trace_id: {trace_id}")
        print()
        print("  To trace this message end-to-end:")
        print(f'    docker logs anizai-airflow-scheduler 2>&1 | Select-String "{trace_id}"')
        print(f'    docker logs anizai-flink-taskmanager 2>&1 | Select-String "{trace_id}"')
    else:
        print("  No records in knowledge_vectors yet — no trace_id available.")
        print("  Try again after the Gold Job has processed at least one message.")

    # ------------------------------------------------------------------
    # FINAL VERDICT
    # ------------------------------------------------------------------
    print()
    print(_divider())
    if empty_sources:
        print("  RESULT: PARTIAL  (some sources are empty)")
        print()
        print("  Empty sources:")
        for src in empty_sources:
            print(f"    - {src}")
        print()
        print("  Check:")
        print("    1. Flink jobs are RUNNING: http://localhost:8081/jobs/running")
        print("    2. DAGs were triggered in Airflow: http://localhost:8090")
        print("    3. API keys are set in .env")
        print("    4. Dead-letter queue for failures:")
        print("       docker exec anizai-kafka /opt/kafka/bin/kafka-console-consumer.sh \\")
        print("         --bootstrap-server localhost:9092 --topic dead-letter-queue \\")
        print("         --from-beginning --max-messages 5")
    else:
        print("  RESULT: PASS  (all expected sources have data)")
        print()
        print("  All vault tables are populated. The pipeline is working end-to-end.")
    print(_divider())
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("\nRunning Anizai full validation — connecting to PostgreSQL...")

    try:
        kv = _knowledge_vault_summary()
        kvec = _knowledge_vectors_summary()
        sv = _social_vault_summary()
        svec = _social_vectors_summary()
        mv = _momentum_vault_summary()
        md = _mapping_dict_summary()
        trace_id = _trace_id_sample()
    except Exception as exc:
        print(f"\n[ERROR] Database query failed: {exc}")
        print("Make sure:")
        print("  - anizai-postgres container is running")
        print("  - POSTGRES_* credentials in .env are correct")
        print("  - You are running from data-pipeline/ with venv active")
        return 1

    empty_sources = _collect_empty_sources(
        kv_counts=kv["counts"],
        sv_counts=sv["counts"],
        mv_counts=mv["counts"],
    )

    _print_report(kv, kvec, sv, svec, mv, md, trace_id, empty_sources)

    return 1 if empty_sources else 0


if __name__ == "__main__":
    sys.exit(main())
