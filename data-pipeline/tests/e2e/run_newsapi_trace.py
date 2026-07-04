"""
Focused NewsAPI end-to-end trace — ONE call, N articles followed to their fate
(Phase 7B.5-I diagnostic, Ron directive 2026-07-03).

Fires ONE direct getArticles call (no Airflow, single category, small
articlesCount), captures the RAW provider envelope BEFORE any parsing, then
emits the first N whitelisted articles to Bronze and traces each through
every station: pre-emit dedup registry state -> Bronze -> Silver -> vault /
gold drop, with topic-offset deltas as evidence. Designed for a QUIET stack
(all DAGs paused, Telegram down) so any message movement is ours by
construction.

Environment-driven, so the same script runs locally (localhost:9092 /
localhost:5432 via .env) and inside the cluster (kafka:29092; pass
--skip-db there and run the vault queries via psql in postgres-0).

Usage:
    python -m tests.e2e.run_newsapi_trace [--limit 3] [--page-size 5]
        [--category news/Business] [--envelope-out PATH] [--skip-db]
        [--watch-seconds 180]

Spends exactly ONE newsapi.ai request (Ron-approved) and, for articles that
survive the two-stage gate, the corresponding OpenAI enrichment calls.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone

from config.settings import KAFKA_BOOTSTRAP_SERVERS

TOPICS = [
    "ingest.bronze.newsapi",
    "process.silver.global_news",
    "serve.gold.global_news",
    "dead-letter-queue",
]


def snapshot_offsets() -> dict[str, int]:
    """Sum of end offsets per topic via a throwaway consumer."""
    from kafka import KafkaConsumer, TopicPartition

    consumer = KafkaConsumer(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS)
    result: dict[str, int] = {}
    for topic in TOPICS:
        parts = consumer.partitions_for_topic(topic) or set()
        tps = [TopicPartition(topic, p) for p in sorted(parts)]
        ends = consumer.end_offsets(tps) if tps else {}
        result[topic] = sum(ends.values())
    consumer.close()
    return result


def vault_lookup(doc_hash: str, url: str) -> dict:
    """Dedup registry forensics: is this hash/URL already in knowledge_vault?"""
    from utils.db import get_cursor

    out: dict = {"hash_hit": None, "url_hits": []}
    with get_cursor() as cur:
        cur.execute(
            "SELECT doc_id::text, ingested_at, source_name "
            "FROM knowledge_vault WHERE document_hash = %s;",
            (doc_hash,),
        )
        row = cur.fetchone()
        if row:
            out["hash_hit"] = dict(row)
        cur.execute(
            "SELECT doc_id::text, document_hash, ingested_at "
            "FROM knowledge_vault WHERE original_url = %s "
            "ORDER BY ingested_at DESC LIMIT 3;",
            (url,),
        )
        out["url_hits"] = [dict(r) for r in cur.fetchall()]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--page-size", type=int, default=5)
    ap.add_argument("--category", default="news/Business")
    ap.add_argument("--envelope-out", default="newsapi_trace_envelope.json")
    ap.add_argument("--skip-db", action="store_true")
    ap.add_argument("--watch-seconds", type=int, default=180)
    args = ap.parse_args()

    import ingestion.newsapi_producer as np_mod
    from ingestion.newsapi_producer import NewsAPIProducer
    from processing.deduplication import hash_document

    print(f"[trace] bootstrap={KAFKA_BOOTSTRAP_SERVERS}  "
          f"category={args.category}  page_size={args.page_size}  "
          f"limit={args.limit}  utc={datetime.now(timezone.utc).isoformat()}")

    # ── Quiet check: two snapshots 20s apart must be identical ───────────
    snap1 = snapshot_offsets()
    time.sleep(20)
    snap2 = snapshot_offsets()
    print(f"[trace] offsets t0    : {snap1}")
    print(f"[trace] offsets t0+20s: {snap2}")
    if snap1 != snap2:
        print("[trace] ABORT: topics are NOT quiet — trace would be ambiguous.")
        return 1
    print("[trace] topics quiet — proceeding.")

    # ── Raw-envelope interception (in-process tee, zero code change) ─────
    captured: dict = {}
    real_get = np_mod.requests.get

    def tee_get(*a, **kw):
        resp = real_get(*a, **kw)
        try:
            captured["status"] = resp.status_code
            captured["json"] = resp.json()
            captured["request_url"] = resp.url.split("apiKey=")[0] + "apiKey=<redacted>" \
                if "apiKey=" in resp.url else resp.url
        except Exception as exc:   # noqa: BLE001 — capture must never break the call
            captured["capture_error"] = str(exc)
        return resp

    np_mod.requests.get = tee_get
    np_mod.PAGE_SIZE = args.page_size          # runtime override, not a code change

    # ── ONE producer call (real Kafka emit) ──────────────────────────────
    producer = NewsAPIProducer()               # real Kafka producer (quiet stack)
    try:
        articles, total, duration_ms = producer._fetch_articles(args.category)
    finally:
        np_mod.requests.get = real_get

    env_head = json.dumps(captured.get("json", {}))[:600]
    with open(args.envelope_out, "w", encoding="utf-8") as fh:
        json.dump(captured, fh, ensure_ascii=False, indent=2)
    print(f"\n[trace] RAW ENVELOPE — status={captured.get('status')}  "
          f"returned={len(articles)}  totalResults={total}  ({duration_ms}ms)")
    print(f"[trace] request (key redacted): {captured.get('request_url', '?')[:220]}")
    print(f"[trace] envelope head: {env_head}")
    print(f"[trace] full envelope saved -> {args.envelope_out}")

    if not articles:
        print("[trace] Provider returned ZERO articles — envelope above is the evidence. "
              "Nothing to emit; fate table not applicable.")
        return 0

    whitelisted = [a for a in articles if producer._passes_whitelist(a)]
    chosen = whitelisted[: args.limit]
    print(f"[trace] whitelisted {len(whitelisted)}/{len(articles)}; tracing {len(chosen)}")

    # ── Pre-emit dedup forensics ─────────────────────────────────────────
    fates: list[dict] = []
    for art in chosen:
        raw = producer._build_raw_payload(art, args.category, "pulse")
        doc_hash = hash_document(raw.get("content", ""), raw.get("url", ""))
        entry = {
            "url": raw.get("url", ""),
            "title": (raw.get("title") or "")[:70],
            "doc_hash": doc_hash,
            "pre_registered": None,
        }
        if not args.skip_db:
            entry["pre_registered"] = vault_lookup(doc_hash, entry["url"])
        fates.append(entry)
        print(f"\n[trace] CANDIDATE {entry['title']}")
        print(f"        url ......... {entry['url'][:100]}")
        print(f"        doc_hash .... {doc_hash}")
        if entry["pre_registered"] is not None:
            hit = entry["pre_registered"]["hash_hit"]
            print(f"        dedup-state . "
                  + (f"PRE-REGISTERED doc_id={hit['doc_id']} ingested_at={hit['ingested_at']}"
                     if hit else "not in vault (hash)"))
            for u in entry["pre_registered"]["url_hits"]:
                print(f"        url-match ... doc_id={u['doc_id']} hash={u['document_hash'][:12]}... "
                      f"ingested_at={u['ingested_at']}")

    # ── Emit ─────────────────────────────────────────────────────────────
    pre_emit = snapshot_offsets()
    emitted = producer._process_and_emit(chosen, args.category, "pulse", duration_ms)
    producer._producer.flush()
    print(f"\n[trace] emitted={emitted} Bronze message(s); watching for "
          f"{args.watch_seconds}s ...")

    # ── Watch the stations ───────────────────────────────────────────────
    deadline = time.time() + args.watch_seconds
    last = pre_emit
    while time.time() < deadline:
        time.sleep(15)
        now = snapshot_offsets()
        if now != last:
            delta = {t: now[t] - pre_emit[t] for t in TOPICS if now[t] != pre_emit[t]}
            print(f"[trace] {datetime.now(timezone.utc).strftime('%H:%M:%S')} "
                  f"deltas vs pre-emit: {delta}")
        last = now
    final = snapshot_offsets()
    deltas = {t: final[t] - pre_emit[t] for t in TOPICS}
    print(f"\n[trace] FINAL topic deltas: {deltas}")

    # ── Post-run fate resolution ─────────────────────────────────────────
    if not args.skip_db:
        for entry in fates:
            after = vault_lookup(entry["doc_hash"], entry["url"])
            pre_hit = (entry["pre_registered"] or {}).get("hash_hit")
            post_hit = after["hash_hit"]
            if pre_hit:
                fate = (f"DEDUPED — hash registered since {pre_hit['ingested_at']} "
                        f"(doc_id={pre_hit['doc_id']}); kv_archive returns None")
            elif post_hit:
                fate = (f"VAULT-INSERTED this trace — doc_id={post_hit['doc_id']} "
                        f"ingested_at={post_hit['ingested_at']}")
            else:
                fate = ("NOT in vault after trace — dropped at the two-stage gate "
                        "(check gold logs / rescue cosine) or still in flight")
            entry["fate"] = fate

        print("\n===== PER-ARTICLE FATE TABLE =====")
        for e in fates:
            print(f"- {e['title']}\n    hash={e['doc_hash'][:16]}...  fate: {e['fate']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
