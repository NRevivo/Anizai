# dayrun20260722_extraction.md

> Domain: A — Pipeline
> Type: Extraction Plan (operational, single-session)
> Last updated: 2026-07-23
> TL;DR: One trip to the cluster to extract everything the day-run analysis needs
> before Kafka retention destroys it. Three payloads: (1) the Kafka funnel scan —
> the perishable half, Silver expires ~2026-07-25 09:25 UTC; (2) the KG-A-13
> empty-`trace_id` count; (3) a Postgres dump of `filter_rejects`,
> `llm_cost_events`, `knowledge_vault` and `social_vault` so all downstream
> analysis runs offline. Every branch is
> pre-decided — Claude Code chooses and Ron confirms, rather than Ron deciding.

## Navigation
- §0 — Why this exists + the retention clock
- §1 — Fixed constants (do not re-derive)
- §2 — Scope: what is extracted and what is deliberately not
- §3 — Task table — E0–E5 with `[ ]` checkboxes
- §4 — Decision tree — every branch, pre-resolved
- §5 — Deliverables
- §6 — Execution notes for Claude Code

---

## §0 — Why this exists

The day-run (`dayrun-20260722`) produced its report and seven CSVs, but the
`funnel.csv` Kafka-side columns — `pulled`, `silver`, `dlq` — were never
populated. Everything measured so far is DB-side: how many objects *passed the
gate*. Nobody measured how many *arrived*.

That missing number is the denominator. Without it there is no true reject rate,
and therefore no evidence-based answer to "is the 20-minute newsapi cadence
right" — the question that gates the `anizai-airflow` rebuild (Stage 2 T2.1).

**The retention clock (verified in `infrastructure/k8s/kafka-init-job.yaml`):**

| Topic group | `retention.ms` | Messages from T0 purge at |
|---|---|---|
| `ingest.bronze.*` | 604800000 (7d) | **2026-07-29 ~09:25 UTC** |
| `process.silver.*` | 259200000 (3d) | **2026-07-25 ~09:25 UTC** |
| `serve.gold.*` | 259200000 (3d) | 2026-07-25 ~09:25 UTC |
| `dead-letter-queue` | 2592000000 (30d) | 2026-08-21 |

Kafka data lives on the `kafka-data` PVC with `KAFKA_LOG_DIRS` explicitly set, so
scaling the node pool to 0 destroys nothing. But retention is **wall-clock based**:
the cleanup thread wakes within `KAFKA_LOG_RETENTION_CHECK_INTERVAL_MS` (300s) of
broker boot and purges anything already past its age. There is no freeze. The
Silver window has roughly two days left as of 2026-07-23.

Two non-perishable payloads ride along in the same session purely to avoid a
second trip: the KG-A-13 trace_id count and the Postgres dump. Once the dump is
on Ron's disk, **all remaining day-run analysis and all of Phase 7B.5 run offline**
and the cluster can stay at 0 indefinitely.

---

## §1 — Fixed constants (do not re-derive)

| Constant | Value |
|---|---|
| `RUN_ID` | `dayrun-20260722` |
| `T0` | `2026-07-22T09:25:26Z` |
| `T0 + 24h` | `2026-07-23T09:25:26Z` |
| Namespace | `anizai` |
| In-cluster bootstrap | `kafka:29092` (NOT `127.0.0.1:9092` — that is the port-forward listener) |
| Partitions | 3 per topic for all `ingest.bronze.*`, `process.silver.*`, `dead-letter-queue`; 1 for `ingestion_triggers` |
| Output dir | `docs\A_pipeline\reports\dayrun-20260722\` (alongside the existing seven files) |

**Topics in scope.** Bronze: `polymarket`, `telegram`, `hackernews`, `newsapi`,
`arxiv`, `fred`, `googletrends`, `openweather`, `opensky`. Silver:
`process.silver.global_news`, `process.silver.social_pulse`,
`process.silver.structured_metrics`. Plus `dead-letter-queue`.
`ingest.bronze.predictit` and `ingest.bronze.reddit` exist but have no active
writer (KG-A-6) — probe them, expect zero, do not scan.

**Timestamp field per layer** (the window filter must use the in-payload
timestamp, never the offset):

| Layer | Field |
|---|---|
| Bronze | `producer_timestamp` |
| Silver | the record's own timestamp (`ingested_at` / `data_point.timestamp_utc` for metrics — confirm per topic on the first message) |
| DLQ | `dlq_timestamp` (fall back to `failed_at` if absent — verify against a real message before assuming) |

---

## §2 — Scope

**In scope:** per-source in-window counts of Bronze messages, Silver messages, and
DLQ entries; the offsets probe that proves whether the window was still intact;
the empty-`trace_id` count; a full dump of `filter_rejects`, `llm_cost_events`,
`knowledge_vault` and `social_vault`.

**Why the vault dumps are not optional (added 2026-07-23, after an omission was
caught).** `llm_cost_events.trace_id` is a per-processing-instance identifier, not
a stable article key. The ONLY bridge from a cost row to an article URL lives on
the vault side (`knowledge_vault.canonical_event_id` → `original_url`). Without
`knowledge_vault` on disk, the entire duplicate-enrichment analysis (KG-A-7)
collapses, because cost cannot be attributed to a URL. Worse: `relevance_score`
for the SURVIVORS lives only in `knowledge_vault` — `filter_rejects` carries only
the rejected side of the cut. **Phase 7B.5 T7B.2 is unexecutable without both
sides of the distribution.** `social_vault` is the parallel bridge for hackernews
(the 127 distinct `content_hash` rows behind 732 enrichments) and is cheap to
include in the same session.

**Explicitly out of scope:** any analysis, any conclusion, any threshold decision.
This session extracts and leaves. Interpretation happens afterwards, offline,
against the exported files. Do not tune anything, do not re-run any DAG, do not
rebuild any image.

**Known measurement gaps that will show up in the output — expected, not bugs:**
- `hackernews` will show Bronze and Silver counts but no reject counts anywhere.
  Its rejects are never captured (KG-A-12). `rejects=0` means "not measured".
- `googletrends` and `opensky` will show ~zero Bronze. They are dead (KG-A-3,
  KG-A-5), and this is the accepted state.
- Silver→Gold consumption lag at the window edge means Silver production and gate
  outcomes are counted at slightly different points in the stream. Small
  discrepancies at the boundary are structural.

---

## §3 — Task table

| Task | Description |
|---|---|
| [x] **E0** | **Safe bring-up.** Scale the main node pool up (command per `Claude-anizai-docs\dayrun\dayrun_operator_guide.md` — do not improvise it). **Immediately** scale `flink-jobmanager` and `flink-taskmanager` to 0 before they can consume; confirm the 7 producer DAGs are still paused. Confirm the Kafka pod reaches Ready. Record whether Flink managed to consume anything (see §4 C0). |
| [x] **E1** | **Offsets probe — zero messages read.** For every in-scope topic and partition, record `beginning_offset`, `end_offset`, `offsets_for_times(T0)`, and `offsets_for_times(T0+24h)`. Write `kafka_offsets.csv`. **Hard stop — present to Ron.** This output decides both the scan method and whether the window is still whole (§4 C1). |
| [x] **E2** | **The scan.** Per the method chosen at E1. Parse each message, extract `source_name` + the layer's timestamp field, bucket, count. **Never retain payloads** — counters only. Produce `kafka_funnel.csv`: one row per source with `pulled`, `silver`, `dlq`, plus `malformed` and `unattributed` columns. |
| [x] **E3** | **KG-A-13 trace_id count.** One query (§5). Settles whether the reported S4.6 wasted-spend figure can be trusted at all. Append the result to `kafka_offsets.csv` as a footer row, or write `trace_id_health.csv`. |
| [x] **E4** | **Postgres dump — four tables.** `\copy` the full `filter_rejects`, `llm_cost_events`, `knowledge_vault` and `social_vault` tables to CSV — full tables, not window-filtered; windowing happens offline and the extra rows cost nothing. The two vault dumps are **mandatory, not nice-to-have** (§2 explains why: they are the only trace_id → URL bridge, and they hold the survivor-side `relevance_score`). `filter_rejects` carries `full_text_raw`, so expect a large file — verify each file is complete and opens before proceeding. |
| [x] **E5** | **Close.** Run the reconciliation sanity check (§4 C4). Verify every output file exists, is non-empty, and opens. **Only then** scale the pool back to 0. |

---

## §4 — Decision tree

Each node states the observation, the pre-decided response, and whether Claude Code
proceeds on its own or stops for Ron.

### C0 — Flink consumed during bring-up
**Observe:** rows in `llm_cost_events` with `created_at > 2026-07-23T09:25:26Z`.
**Response:** proceed regardless. Anything created now lands outside the measured
window by definition and cannot contaminate the analysis. Count the rows, note the
figure in the handoff summary, move on.
**Do NOT:** treat this as a failure, or attempt to delete the rows.
→ *Claude Code proceeds without asking.*

### C1 — Offsets probe (the decisive node)

**C1-a — Is the window still intact?**
- `offsets_for_times(T0)` returns a valid offset on **every** partition of every
  in-scope topic → window whole. Proceed.
- Returns `None` on any **Silver** partition → retention has already eaten into the
  window. **Response: proceed anyway.** Record the earliest surviving timestamp per
  partition in `kafka_offsets.csv` and mark every Silver figure in the output as a
  **floor, not a count**. A partial denominator beats no denominator.
  → *Stop and tell Ron, then proceed on his acknowledgement — this changes how the
  numbers may be used, so he must know before he reads them.*
- Returns `None` on a **Bronze** partition → unexpected this early (7-day
  retention). **Stop.** Something is wrong with the assumed retention or the clock.
  → *Hard stop for Ron.*

**C1-b — Which scan method?** Based on total in-window message count derived from
the probe (`offset(T0+24h) − offset(T0)`, summed):
- **≤ 300k** → single Job, straightforward scan of all topics.
- **300k – 1.5M** → single Job, but **Silver topics first** (they are the ones
  expiring), writing partial output per topic as it completes, so a mid-run failure
  still leaves the perishable half captured.
- **> 1.5M** → do not scan from `earliest`. `seek()` directly to the T0 offset per
  partition and stop at the T0+24h offset. The probe already provides both.
→ *Claude Code chooses and states the choice; Ron confirms with one word.*

### C2 — During the scan
| Observation | Response | Ask Ron? |
|---|---|---|
| JSON parse failure on a message | Increment a `malformed` counter, continue. Report the rate; flag only if >1% of a topic | No |
| Timestamp field missing or unparseable | Increment `unattributed` for that topic, continue | No |
| `source_name` missing on a Silver record | Bucket as `unattributed`, continue — do not guess from the topic | No |
| Job OOM-killed | Split into one Job per topic. Sizes are already known from E1, so the split is informed, not blind | No — just do it |
| Elapsed > ~20 minutes | Do not wait it out. Long runtime means the method was wrong, not that it needs patience. Kill, re-derive from E1, switch to the `seek` method | Report after switching |
| Cannot reach `kafka:29092` from the Job | Verify the Job is in namespace `anizai` and using `serviceAccountName: pipeline-runtime`. **Do not fall back to `port-forward`** — the tunnel is a single TCP connection and will be the bottleneck; it turns a 5-minute scan into an hour and may stall mid-run | Stop if unresolved |
| Chosen image lacks a Kafka client | Verify `python -c "import kafka"` in the image **before** writing the scanner. Prefer an image already carrying the pipeline dependencies (`anizai-flink` / `anizai-airflow`) over installing at runtime — the cluster egress may not permit a fresh pip install | Stop if no suitable image |

### C3 — Postgres dump
| Observation | Response | Ask Ron? |
|---|---|---|
| `filter_rejects` dump is very large (`full_text_raw` is untruncated by design) | Expected. Let it complete. If it fails, split by `source_name` — do NOT truncate the text column; the full body is exactly what T7B.1 manual classification needs | No |
| A `\copy` fails midway | Re-run that table only. The dumps are independent | No |

### C4 — Reconciliation before close
For each of `newsapi`, `arxiv`, `telegram`, the identity should hold:

```
pulled ≥ silver ≥ (archived + rejects_rows)
```

- **Holds** → close normally.
- **Violated** (e.g. `silver < archived + rejects`) → **record it, do not fix it.**
  This is a finding about the pipeline, not a defect in the scan. Write it into the
  handoff summary and let the offline analysis explain it.
  → *Report to Ron; do not block the close.*
- `hackernews` is exempt from the reject side of this identity (KG-A-12).
- `polymarket` / `openweather` / `fred` have no gate — only `pulled ≥ silver`
  applies.

### C5 — Close
Scale the pool to 0 **only after** Ron confirms the output files exist and open.
The Kafka scan is not repeatable for Silver after 2026-07-25 09:25 UTC.

---

## §5 — Deliverables

All into `docs\A_pipeline\reports\dayrun-20260722\`:

| File | Contents |
|---|---|
| `kafka_offsets.csv` | topic, partition, beginning, end, offset@T0, offset@T0+24h, earliest surviving timestamp. **This is the evidence that the window was whole** — it is what makes the funnel numbers defensible later |
| `kafka_funnel.csv` | source, pulled, silver, dlq, malformed, unattributed — all in-window |
| `trace_id_health.csv` | the KG-A-13 count |
| `filter_rejects_full.csv` | full table dump — the rejected side of the cut, incl. untruncated `full_text_raw` for T7B.1 |
| `llm_cost_events_full.csv` | full table dump |
| `knowledge_vault_full.csv` | full table dump — **mandatory**: the trace_id → URL bridge and the survivor-side `relevance_score` |
| `social_vault_full.csv` | full table dump — the hackernews-side bridge (`content_hash`) |

**E3 query (KG-A-13):**

```sql
SELECT
    site,
    count(*)                                                   AS rows_total,
    count(*) FILTER (WHERE trace_id IS NULL OR trace_id = '')  AS trace_id_empty
FROM llm_cost_events
WHERE created_at >= TIMESTAMPTZ '2026-07-22T09:25:26Z'
  AND created_at <  TIMESTAMPTZ '2026-07-23T09:25:26Z'
GROUP BY site
ORDER BY site;
```

Read it as follows: any non-zero `trace_id_empty` on `rescue_embed` means the
reported S4.6 wasted-spend number is unreliable and must be recomputed offline
against `original_url` instead. Zero across the board means S4.6 stands and
KG-A-13 is latent rather than realised.

**E4 dump:**

```
\copy (SELECT * FROM filter_rejects)    TO 'filter_rejects_full.csv'    CSV HEADER
\copy (SELECT * FROM llm_cost_events)   TO 'llm_cost_events_full.csv'   CSV HEADER
\copy (SELECT * FROM knowledge_vault)   TO 'knowledge_vault_full.csv'   CSV HEADER
\copy (SELECT * FROM social_vault)      TO 'social_vault_full.csv'      CSV HEADER
```

If `knowledge_vault` proves too large to dump whole, narrow the COLUMNS (drop
`full_text_raw` on the vault side only — the reject-side text is the one T7B.1
needs), never the ROWS. Dropping rows silently biases the score distribution that
T7B.2 depends on.

---

## §6 — Execution notes for Claude Code

1. **E1 is a hard stop.** Present `kafka_offsets.csv` and the chosen scan method
   before reading a single message. Everything downstream depends on it.
2. **Never `port-forward` the scan.** Run it in-cluster against `kafka:29092`.
   `kubectl cp` the small CSVs out afterwards.
3. **Counters, not payloads.** The scanner must never accumulate message bodies in
   memory. This is what keeps it OOM-safe at any volume.
4. **Offsets are not a window filter.** Always filter on the in-payload timestamp.
   Offsets only bound the read range.
5. **Verify the timestamp field name per topic against a real message** before
   trusting it — §1 lists the expected fields, but the DLQ envelope in particular
   should be confirmed, not assumed.
6. **Zero changes to pipeline behaviour.** No DAG unpause, no image rebuild, no
   config edit, no threshold touch. If a task appears to require any of these,
   stop and raise it.
7. Precedent for the consumer pattern: `tests/e2e/run_newsapi_trace.py`
   (`snapshot_offsets`) and the 2026-07-03 Bronze-by-day histogram stint. Re-verify
   both against the working tree before reusing — they may have drifted.

---

---

## §7 — Execution log (session 2026-07-25 ~evening UTC, ~48h post-close)

**Retention reality at execution time (Ron directive 2026-07-25):** the cluster has
been scaled to 0 since 07-23 morning, so the broker is DOWN and nothing has been
purged yet — Silver is frozen intact on the PVC. The first retention sweep fires
within ~300s of broker boot and deletes the early edge (22.07-morning messages, now
>3d old). Directive amendments folded in:
- **Scan Silver FIRST**, immediately after E1 (Bronze can wait — 7d retention, safe
  through 07-29; Silver tail purges ~07-26 09:25 UTC).
- **E1 races the sweep, does NOT wait for it.** Run the offsets probe the instant
  `kafka-0` is `1/1 Ready`. Two samples ~60s apart CATCH a live purge
  (`beginning_offset` advancing between samples); `earliest_surviving_ts` is taken
  from sample 1 (least-purged). This reverses the initial "settle-before-probe" idea.
- Every Silver figure is a **floor, not a count**, unless E1 proves `offset@T0`
  valid on every Silver partition.
- **Measurement only** — retention.ms is NOT touched (a config change is forbidden
  by §6.6; Ron confirmed measure-don't-save).

**Pinned execution artifacts (this session):**
- `scripts/dayrun_kafka_funnel_scan.py` — two-mode (offsets/scan), counters-only,
  offset-committing-free (group_id=None). Field maps confirmed vs `gold_job.py`.
- `infrastructure/k8s/dayrun-scan-job.yaml` — ephemeral in-cluster Job; script
  mounted from ConfigMap `dayrun-scan-script`; deleted at E5.
- **Image PINNED to the live tag `anizai-flink:1.19.1-7b5i`** (read from the live
  flink-taskmanager Deployment + AR-verified 2026-07-25). The
  `cluster_operations_guide §1` `-p95` tag is stale — do not use it.
- Scale-up command: `cluster_operations_guide.md §3 Start` verbatim (Ron-confirmed).

**Results (E1–E4 complete):**
- **E1 — we beat the sweep.** The whole Silver window was found INTACT: `offset@T0`
  valid on all 9 Silver partitions, earliest surviving Silver msg ~07-21 16:1x
  (pre-window), `purged_between_samples=0`. No live in-window data was purged — the
  only `FLOOR` flags are on empty/dead/pre-window partitions (telegram[0],
  googletrends, opensky, DLQ). **Every Silver figure is a true COUNT, not a floor.**
- **E2 funnel** (`kafka_funnel.csv`): `pulled == silver` for every live source
  (polymarket 26,136 · hackernews 3,600 · newsapi 2,766 · arxiv 1,400 · openweather
  1,440 · telegram 595 · fred 174) → **zero Bronze→Silver loss**. **DLQ in-window = 0**
  (30 retained rows all pre-T0). global_news carries no in-payload ingestion ts (only
  `publish_date`); windowed correctly by Kafka append time (== ingestion, verified
  against social_pulse where the two agree exactly). fred Silver via
  `data_point.timestamp_utc` reads 0 (observation-date artifact); 174 = Bronze/
  append-time evidence — deterministic source, no gate.
- **E3 — KG-A-13 latent** (`trace_id_health.csv`): `trace_id_empty=0` on all sites
  (rescue_embed 2,177 · gold_enrich 2,597 · gold_embed 3,329 · gold_consensus 732).
  **S4.6 wasted-spend figure stands.** rescue_embed 2,177 matches run-report rescue
  accounting exactly.
- **C0**: 0 rows written during today's bring-up (Flink held at 0); the 178 post-close
  rows are 07-23 stragglers from the original drain — outside the window, harmless.
- **E4** dumps verified vs DB counts: filter_rejects 6,042 · llm_cost_events 28,873 ·
  knowledge_vault 2,759 · social_vault 626 (all exact). NOTE: the kubectl-exec channel
  reset on this Windows client above ~3 MB; `filter_rejects` (17 MB) was pulled via a
  **port-forward + psycopg2 COPY** (separate transport) — the other three streamed OK.
- **C4 reconciliation holds** (`pulled ≥ silver ≥ archived+rejects`): newsapi
  2766≥2766≥2050 · arxiv 1400≥1400≥598 · telegram 595≥595≥595 (exact). Cross-checks
  reconcile to the unit with the run report: archived 644+108+327 = **1,079**; reject
  rows 1406+490+268 = **2,164**; reject distinct 518+468+268 = **1,254**. The
  newsapi/arxiv gaps are Silver→Gold boundary lag (arxiv enrichment spill past T0+24h),
  not a violation.
- **E5 CLOSED** (Ron confirmed all 7 files open, incl. the 17 MB `filter_rejects_full.csv`
  — 11 cols, untruncated `full_text_raw`+`rescue_cosine`, 6,042 rows). Scan Jobs +
  ConfigMap + imgwarm pod deleted; `flink-jobmanager`/`flink-taskmanager`/`polymarket`/
  `telegram` **left at replicas=0** (Ron's call — aligns with the B-test "Domain-A
  producers OFF" state; `agent-worker` already 0); main-pool scaled to 0 (zero nodes
  confirmed). **All day-run + Phase 7B.5 analysis now runs offline.**
- Follow-up (Ron, offline, non-blocking): fix the stale `ingested_at` assumption for
  global_news in `dayrun20260722_analysis.md` — Silver global_news has no ingestion ts
  (only `publish_date`); the funnel windows it by Kafka append time.

> Companion: `plans/phase7b5i_filter_observability_and_cost.md` §7 (the day-run
> protocol this closes out) and `reports/dayrun-20260722/run_report.md` (the DB-side
> half already delivered). Gaps referenced: KG-A-12, KG-A-13, KG-A-3, KG-A-5, KG-A-6.
