"""
dayrun_kafka_funnel_scan.py — Kafka-side funnel extractor for `dayrun-20260722`.

WHY this exists (plans/dayrun20260722_extraction.md §0): the day-run report is
DB-side only — it measured how many objects *passed the gate*, never how many
*arrived*. This script supplies the missing denominator (Bronze `pulled`, Silver
`silver`, DLQ `dlq`) from Kafka itself, before 3-day Silver retention destroys it.

It runs IN-CLUSTER as an ephemeral k8s Job against `kafka:29092` (never via
port-forward — §6.2), prints CSV to stdout between sentinels (captured with
`kubectl logs`), and sends all diagnostics to stderr.

Two modes:

  --mode offsets   E1. Reads ZERO message bodies. Per in-scope topic+partition,
                   records beginning/end/offset@T0/offset@T0+24h and the earliest
                   surviving message timestamp (via ListOffsets time-index, not a
                   fetch — §E1 "zero messages read" is honoured). Takes TWO samples
                   ~`--sample-gap` apart so a live retention purge is caught in the
                   act (beginning_offset advancing between samples = messages being
                   deleted right now). earliest_surviving_ts is taken from sample 1,
                   the least-purged snapshot (Ron directive 2026-07-25).

  --mode scan      E2. Counters only, never retains a payload (§6.3 OOM-safety).
                   Seeks [offset@T0 .. offset@T0+24h) per partition — falling back
                   to [beginning .. end) with a FLOOR flag when T0 has been purged
                   past — parses each message, buckets by source using the in-PAYLOAD
                   timestamp (§6.4: offsets bound the read range; the window filter is
                   always the in-payload ts), and emits per-source pulled/silver/dlq
                   plus malformed + unattributed counters.

Safety (pipeline-principles P1/P3, extraction §6.6): the consumer uses NO group_id
and `enable_auto_commit=False` — it never commits offsets, so it cannot perturb any
Flink consumer group or change pipeline behaviour. Read-only throughout.

Field maps confirmed against processing/gold_job.py (2026-07-25):
  Bronze  : source = topic suffix; ts = producer_timestamp
  Silver global_news / social_pulse : source = source_name;                 ts = ingested_at
  Silver structured_metrics         : source = core_identity.source_name;   ts = data_point.timestamp_utc
  DLQ     : source = original_message.source_name (fallback source_name);   ts = dlq_timestamp (fallback failed_at)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

CSV_BEGIN = "===CSV-BEGIN==="
CSV_END = "===CSV-END==="

# --- In-scope topics (extraction plan §1). Partitions are discovered live. ------
BRONZE_SOURCES = [
    "polymarket", "telegram", "hackernews", "newsapi", "arxiv",
    "fred", "googletrends", "openweather", "opensky",
]
BRONZE_TOPICS = [f"ingest.bronze.{s}" for s in BRONZE_SOURCES]
SILVER_TOPICS = [
    "process.silver.global_news",
    "process.silver.social_pulse",
    "process.silver.structured_metrics",
]
DLQ_TOPICS = ["dead-letter-queue"]

LAYERS = {
    "silver": SILVER_TOPICS,   # scanned FIRST — the perishable half (Ron directive)
    "bronze": BRONZE_TOPICS,
    "dlq": DLQ_TOPICS,
}
# Offsets probe (E1) covers every in-scope topic, Silver foregrounded in output.
ALL_TOPICS = SILVER_TOPICS + BRONZE_TOPICS + DLQ_TOPICS


def log(msg: str) -> None:
    """Diagnostics to stderr so stdout carries only the CSV block."""
    print(f"[scan] {msg}", file=sys.stderr, flush=True)


def parse_iso(value) -> datetime | None:
    """Parse an ISO-8601 string to an aware UTC datetime; None if unparseable.

    Why lenient: the in-payload timestamp is the window filter (§6.4) and its exact
    formatting varies per source; a parse miss must become an `unattributed` tally,
    never a crash that aborts the perishable scan.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def to_epoch_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def ms_to_iso(ms: int | None) -> str:
    if ms is None:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


# --- Per-layer source / timestamp extractors (confirmed vs gold_job.py) ---------
def extract_source(topic: str, rec: dict) -> str | None:
    if topic.startswith("ingest.bronze."):
        return topic.rsplit(".", 1)[-1]
    if topic == "dead-letter-queue":
        om = rec.get("original_message") or {}
        return om.get("source_name") or rec.get("source_name")
    if topic.endswith("structured_metrics"):
        return (rec.get("core_identity") or {}).get("source_name")
    # global_news / social_pulse
    return rec.get("source_name")


# global_news / social_pulse ingestion-time candidates, funnel-correct order:
# ingested_at is the Silver-processing time (what the funnel wants). Fallbacks exist
# because global_news failed on ingested_at in the first scan (2026-07-25) — the
# per-message ts-field logging reveals which field actually carries it.
NEWS_SOCIAL_TS = ["ingested_at", "silver_timestamp", "processed_at", "created_at", "published_at"]


def extract_ts(topic: str, rec: dict) -> datetime | None:
    if topic.startswith("ingest.bronze."):
        return parse_iso(rec.get("producer_timestamp"))
    if topic == "dead-letter-queue":
        return parse_iso(rec.get("dlq_timestamp") or rec.get("failed_at"))
    if topic.endswith("structured_metrics"):
        return parse_iso((rec.get("data_point") or {}).get("timestamp_utc"))
    for field in NEWS_SOCIAL_TS:
        dt = parse_iso(rec.get(field))
        if dt is not None:
            return dt
    return None


def make_consumer(bootstrap: str):
    """Read-only, offset-committing-free consumer (extraction §6.6).

    group_id=None + enable_auto_commit=False guarantees we never write a consumer
    offset, so the scan cannot disturb any Flink consumer group.
    """
    from kafka import KafkaConsumer

    return KafkaConsumer(
        bootstrap_servers=bootstrap.split(","),
        group_id=None,
        enable_auto_commit=False,
        consumer_timeout_ms=15000,
        request_timeout_ms=30000,
        api_version_auto_timeout_ms=15000,
    )


def topic_partitions(consumer, topic: str):
    from kafka import TopicPartition

    parts = consumer.partitions_for_topic(topic)
    if not parts:
        return None
    return [TopicPartition(topic, p) for p in sorted(parts)]


# ------------------------------- E1: offsets ------------------------------------
def sample_offsets(consumer, tps, t0_ms, t0_end_ms):
    """One snapshot: begin, end, offset@T0, offset@T0+24h, earliest-surviving-ts.

    All four are ListOffsets metadata lookups — no message body is fetched, so the
    E1 "zero messages read" contract holds. earliest_surviving_ts uses
    offsets_for_times(1ms): Kafka returns the first message whose timestamp >= 1ms,
    i.e. the earliest still-retained message, with its index timestamp.
    """
    begin = consumer.beginning_offsets(tps)
    end = consumer.end_offsets(tps)
    at_t0 = consumer.offsets_for_times({tp: t0_ms for tp in tps})
    at_end = consumer.offsets_for_times({tp: t0_end_ms for tp in tps})
    earliest = consumer.offsets_for_times({tp: 1 for tp in tps})
    out = {}
    for tp in tps:
        ot0 = at_t0.get(tp)
        oend = at_end.get(tp)
        early = earliest.get(tp)
        out[tp] = {
            "begin": begin.get(tp),
            "end": end.get(tp),
            "off_t0": None if ot0 is None else ot0.offset,
            "off_t0_end": None if oend is None else oend.offset,
            "earliest_ts": None if early is None else early.timestamp,
        }
    return out


def run_offsets(consumer, t0_ms, t0_end_ms, sample_gap):
    rows = []
    tp_map = {}
    for topic in ALL_TOPICS:
        tps = topic_partitions(consumer, topic)
        if tps is None:
            log(f"topic ABSENT (no partitions): {topic}")
            continue
        tp_map[topic] = tps

    log("sample 1 …")
    s1 = {t: sample_offsets(consumer, tps, t0_ms, t0_end_ms) for t, tps in tp_map.items()}
    log(f"sleeping {sample_gap}s before sample 2 (to catch a live purge) …")
    time.sleep(sample_gap)
    log("sample 2 …")
    s2 = {}
    for t, tps in tp_map.items():
        begin = consumer.beginning_offsets(tps)
        end = consumer.end_offsets(tps)
        s2[t] = {tp: {"begin": begin.get(tp), "end": end.get(tp)} for tp in tps}

    for topic in ALL_TOPICS:
        if topic not in tp_map:
            continue
        for tp in tp_map[topic]:
            a = s1[topic][tp]
            b = s2[topic][tp]
            purged = None
            if a["begin"] is not None and b["begin"] is not None:
                purged = b["begin"] - a["begin"]
            in_window = None
            lo = a["off_t0"] if a["off_t0"] is not None else a["begin"]
            hi = a["off_t0_end"] if a["off_t0_end"] is not None else a["end"]
            if lo is not None and hi is not None:
                in_window = hi - lo
            rows.append({
                "topic": topic,
                "partition": tp.partition,
                "begin_s1": a["begin"],
                "end_s1": a["end"],
                "begin_s2": b["begin"],
                "end_s2": b["end"],
                "purged_between_samples": purged,
                "offset_at_t0": a["off_t0"],
                "offset_at_t0_end": a["off_t0_end"],
                "t0_purged_past": a["off_t0"] is None,
                "earliest_surviving_ts": ms_to_iso(a["earliest_ts"]),
                "in_window_floor": in_window,
            })
    fields = [
        "topic", "partition", "begin_s1", "end_s1", "begin_s2", "end_s2",
        "purged_between_samples", "offset_at_t0", "offset_at_t0_end",
        "t0_purged_past", "earliest_surviving_ts", "in_window_floor",
    ]
    return fields, rows


# -------------------------------- E2: scan --------------------------------------
def run_scan(consumer, topics, t0, t0_end, t0_ms, t0_end_ms):
    counts: dict[str, int] = {}
    malformed: dict[str, int] = {}
    unattributed = {"missing_source": 0, "bad_timestamp": 0, "malformed_json": 0}
    floors: list[str] = []
    fallback_ts: dict[str, int] = {}   # msgs windowed by Kafka append time (no in-payload ingestion ts)
    schema_seen: set[str] = set()

    from kafka import TopicPartition  # noqa: F401  (TopicPartition used via topic_partitions)

    for topic in topics:
        tps = topic_partitions(consumer, topic)
        if tps is None:
            log(f"topic ABSENT, skipping: {topic}")
            continue
        at_t0 = consumer.offsets_for_times({tp: t0_ms for tp in tps})
        at_end = consumer.offsets_for_times({tp: t0_end_ms for tp in tps})
        begin = consumer.beginning_offsets(tps)
        end = consumer.end_offsets(tps)

        for tp in tps:
            ot0 = at_t0.get(tp)
            start = ot0.offset if ot0 is not None else begin.get(tp)
            if ot0 is None:
                floors.append(f"{topic}[{tp.partition}]")
                log(f"FLOOR: {topic}[{tp.partition}] — T0 purged past; start={start} (beginning)")
            oend = at_end.get(tp)
            stop = oend.offset if oend is not None else end.get(tp)
            if start is None or stop is None or start >= stop:
                continue

            consumer.assign([tp])
            consumer.seek(tp, start)
            while True:
                pos = consumer.position(tp)
                if pos >= stop:
                    break
                batch = consumer.poll(timeout_ms=8000, max_records=2000)
                if not batch:
                    break
                for _tp, msgs in batch.items():
                    for m in msgs:
                        if m.offset >= stop:
                            continue
                        try:
                            rec = json.loads(m.value)
                        except (json.JSONDecodeError, TypeError):
                            malformed[topic] = malformed.get(topic, 0) + 1
                            unattributed["malformed_json"] += 1
                            continue
                        if topic not in schema_seen:
                            schema_seen.add(topic)
                            tslike = {
                                k: rec.get(k) for k in rec
                                if any(s in k.lower() for s in ("time", "date", "_at"))
                            }
                            log(f"first msg {topic}: {len(rec)} keys; ts-like fields={tslike}")
                        ts = extract_ts(topic, rec)
                        used_fallback = False
                        if ts is None:
                            # No in-payload ingestion ts (global_news carries only
                            # publish_date, which is publication — not funnel — time).
                            # Fall back to the Kafka append timestamp, which IS the
                            # Silver-write/ingestion time (verified: social_pulse's
                            # ingested_at count == its offset-range count exactly).
                            if m.timestamp is not None and m.timestamp > 0:
                                ts = datetime.fromtimestamp(m.timestamp / 1000, tz=timezone.utc)
                                used_fallback = True
                            else:
                                unattributed["bad_timestamp"] += 1
                                continue
                        if not (t0 <= ts < t0_end):
                            continue  # outside the measured window — offsets are only a bound
                        src = extract_source(topic, rec)
                        if not src:
                            unattributed["missing_source"] += 1
                            continue
                        counts[src] = counts.get(src, 0) + 1
                        if used_fallback:
                            fallback_ts[topic] = fallback_ts.get(topic, 0) + 1
        consumer.assign([])  # release before next topic

    rows = []
    for src in sorted(counts):
        rows.append({"source": src, "in_window_count": counts[src]})
    for topic in sorted(malformed):
        rows.append({"source": f"__malformed__{topic}", "in_window_count": malformed[topic]})
    for k, v in unattributed.items():
        rows.append({"source": f"__unattributed__{k}", "in_window_count": v})
    for topic in sorted(fallback_ts):
        rows.append({"source": f"__kafka_ts_fallback__{topic}", "in_window_count": fallback_ts[topic]})
    if floors:
        log(f"FLOOR partitions (Silver figures are a floor, not a count): {floors}")
    if fallback_ts:
        log(f"Kafka-append-ts fallback used (no in-payload ingestion ts): {fallback_ts}")
    return ["source", "in_window_count"], rows


def emit_csv(fields, rows):
    print(CSV_BEGIN)
    w = csv.DictWriter(sys.stdout, fieldnames=fields)
    w.writeheader()
    for r in rows:
        w.writerow(r)
    print(CSV_END)
    sys.stdout.flush()


def main() -> int:
    ap = argparse.ArgumentParser(description="dayrun-20260722 Kafka funnel extractor")
    ap.add_argument("--mode", required=True, choices=["offsets", "scan"])
    ap.add_argument("--layer", default="all", choices=["silver", "bronze", "dlq", "all"])
    ap.add_argument("--t0", default="2026-07-22T09:25:26Z")
    ap.add_argument("--t0-end", default="2026-07-23T09:25:26Z")
    ap.add_argument("--sample-gap", type=int, default=60)
    ap.add_argument("--bootstrap", default=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092"))
    args = ap.parse_args()

    t0 = parse_iso(args.t0)
    t0_end = parse_iso(args.t0_end)
    if t0 is None or t0_end is None:
        log("FATAL: bad --t0/--t0-end")
        return 2
    t0_ms, t0_end_ms = to_epoch_ms(t0), to_epoch_ms(t0_end)
    log(f"mode={args.mode} layer={args.layer} bootstrap={args.bootstrap} "
        f"T0={t0.isoformat()} T0+24h={t0_end.isoformat()}")

    consumer = make_consumer(args.bootstrap)
    try:
        if args.mode == "offsets":
            fields, rows = run_offsets(consumer, t0_ms, t0_end_ms, args.sample_gap)
        else:
            topics = ALL_TOPICS if args.layer == "all" else LAYERS[args.layer]
            fields, rows = run_scan(consumer, topics, t0, t0_end, t0_ms, t0_end_ms)
    finally:
        consumer.close()

    emit_csv(fields, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
