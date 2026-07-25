# dayrun20260722_analysis.md

> Domain: A — Pipeline
> Type: Analysis Plan (offline, no cluster required)
> Last updated: 2026-07-23
> TL;DR: Turns the extracted day-run data into three reports — cost per producer,
> the object funnel with duplication, and the producer-cadence decision — plus a
> projection of what a day WOULD cost once KG-A-7 is fixed. Runs entirely offline
> against the E4 dumps and the E2 Kafka counts. Filter *quality* (TP/FP/FN) is NOT
> here — that is Phase 7B.5, in its own plan file.

## Navigation
- §0 — Why this exists + the one idea that organises it
- §1 — Prerequisites — what must be on disk before starting
- §2 — The three paths and their dedup keys
- §3 — Task table — A1–A6 with `[ ]` checkboxes
- §4 — Report 1: cost per producer
- §5 — Report 2: object funnel + duplication
- §6 — Report 3: cadence decision
- §7 — What this analysis deliberately cannot answer
- §8 — Deliverable

> **Assumptions here were refinable after E1 — and E1 has now run (2026-07-25).**
> Outcome: the Silver window was **fully intact** (true counts, not floors), so the
> denominators and funnel identities below stand as written. `pulled == silver` for
> every source; DLQ in-window = 0; KG-A-13 latent (`trace_id_empty = 0`), so
> trace_id joins are usable and the S4.6 spend figure stands. The one field-level
> correction E1 surfaced (global_news has no `ingested_at`; window by Kafka append
> time) is folded into §1 below. No other revision was needed.

---

## §0 — Why this exists

The day-run report answered *hygiene* questions (no restarts, no DLQ, no cost
gaps) and produced one headline number ($1.3511/day). It did not answer the
questions Ron actually needs answered before the `anizai-airflow` rebuild:

1. What does each producer cost, and how many objects does each produce?
2. How many objects arrived versus how many survived — and how much of the work
   was duplicated?
3. Should any producer's cadence change, and by how much, and why?

**The organising idea: there is one dataset, not three reports.** Every dollar
spent is an object that was enriched. Cost and volume are the same measurement in
two units. So the work is: build one flat table where a row is one *processing
instance* of one object, then the three reports are three `GROUP BY`s over it.

Columns of that table: **source · dedup key · first-or-repeat · outcome · cost**.

Two consequences follow, and both matter:
- Duplicates are **rows**, not noise to be cleaned. The same article pulled three
  times is three rows. That gap *is* the duplication measurement.
- The table is built **per path**, then unioned — because the three paths do not
  share a dedup key (§2).

---

## §1 — Prerequisites

Nothing here runs until all of the following are on local disk from the extraction
session (`plans/dayrun20260722_extraction.md`):

| Input | From | Used for |
|---|---|---|
| `knowledge_vault_full.csv` | E4 | survivor side: `original_url`, `relevance_score`, `rescue_cosine`, `canonical_event_id`, `ingested_at` (funnel-time; global_news has no separate ingestion ts — see note below) |
| `filter_rejects_full.csv` | E4 | reject side: `original_url`, `relevance_score`, `rescue_cosine`, `rejected_at` |
| `llm_cost_events_full.csv` | E4 | every call: `site`, `source_name`, `model`, tokens, `cost_usd`, `trace_id`, `created_at` |
| `social_vault_full.csv` | E4 | hackernews bridge: `content_hash`, `ingested_at` |
| `kafka_funnel.csv` | E2 | **the denominator**: `pulled` / `silver` / `dlq` per source |
| `kafka_offsets.csv` | E1 | whether the window was whole — governs whether Silver figures are counts or floors |
| `trace_id_health.csv` | E3 | whether trace_id-based joins are usable at all |

**Gate before A1: read `trace_id_health.csv` first.** If `rescue_embed` shows any
empty `trace_id` (KG-A-13), every trace_id join in this plan must be replaced by a
`original_url` join, and the previously reported S4.6 wasted-spend figure must be
discarded rather than cited. Settle this before building anything.

**Window:** `[2026-07-22T09:25:26Z, 2026-07-23T09:25:26Z)`. Every figure in every
report is window-bounded on the row's own timestamp — never on `run_id` alone
(pre-T0 warm-up shares the run_id; post-window rows inherit it because `RUN_ID` is
a static env var in both Flink manifests). **The dumps are the FULL tables, not
window-filtered** (E4 by design) — so the first step on every table is to drop rows
outside the window (e.g. `filter_rejects_full.csv` carries `t7-smoke-2026-07-02`
rows; window on `rejected_at`, and they fall out on their own). Do not count them.

**Timestamp fields, corrected after E1 (was `ingested_at` throughout — wrong for
global_news).** The extraction found that `global_news` records carry no ingestion
timestamp, only `publish_date` (publication time, not funnel time). CC therefore
windowed global_news by **Kafka append time**, verified equal to ingestion against
`social_pulse` where both fields exist. Consequences for this analysis:
- **Window filter** for global-news rows: use the row's `rejected_at`
  (`filter_rejects`) / `ingested_at` (`knowledge_vault`) where present; these DB
  timestamps are funnel-time and are correct. `publish_date` is NOT a funnel
  timestamp — never window on it.
- **first-vs-repeat ordering** (§5): order each dedup key's instances by their
  funnel timestamp (`created_at` on `llm_cost_events`, `rejected_at`/`ingested_at`
  on the vault/reject side) — never by `publish_date`. Two articles published days
  apart can enter the funnel seconds apart, and it is funnel order that defines
  which instance is "first".

Tooling: pandas or duckdb over the CSVs. No cluster, no live DB.

---

## §2 — The three paths and their dedup keys

| Path | Sources | Gate | Rejects visible | AI cost | Dedup key |
|---|---|---|---|---|---|
| **Global news** | newsapi, arxiv, telegram | sniper + semantic rescue | Yes (`filter_rejects`) | `gold_enrich` + `gold_embed` + `rescue_embed` | `original_url` (analysis) / `document_hash` (the code's own check) |
| **Social** | hackernews | sniper only | **No** (KG-A-12) | `gold_consensus` + `gold_embed` | `content_hash` |
| **Metrics** | polymarket, openweather, fred | none | n/a | zero | `(metric_id, timestamp_utc)` |

`googletrends` and `opensky` are dead (KG-A-3, KG-A-5) — expect zeros throughout
and record them as a caveat, not as a finding.

**"polymarket" in this analysis means the metrics path only.** Its comment path is
feature-flagged off (KG-A-4), so its ~26k in-window objects are price updates, not
discourse, and they carry no LLM cost by design.

**The keys do not unify.** The code checks duplication on `document_hash`
(knowledge_vault) and `content_hash` (social_vault), while `filter_rejects` carries
only `original_url`. Analysis therefore keys global-news on `original_url` — the
one field present on both sides of the cut — and accepts that cross-path
comparison of duplication rates is directional, not exact. State this in the
deliverable rather than papering over it.

---

## §3 — Task table

| Task | Description |
|---|---|
| [ ] **A1** | **Trace-id health gate + flat table, global-news path.** Read `trace_id_health.csv` and fix the join strategy accordingly (§1). Then build one row per processing instance for newsapi/arxiv/telegram: source, `original_url`, outcome (`passed` / `rescued` / `rejected` / `enriched_then_deduped`), first-or-repeat, and summed `cost_usd`. Derivation rules in §5. |
| [ ] **A2** | **Flat table, social path.** Same shape for hackernews, keyed on `content_hash`. Outcome collapses to `enriched` / `enriched_then_deduped` only — the rejected outcome is unobservable (KG-A-12) and must be rendered as `unknown`, never as zero. |
| [ ] **A3** | **Metrics path — counts only.** Per-source in-window object counts for polymarket/openweather/fred from `momentum_vault` plus the Kafka `pulled` figure. No cost columns; assert zero LLM cost and move on. |
| [ ] **A4** | **Report 1 — cost per producer** (§4), including the post-fix projection. |
| [ ] **A5** | **Report 2 — object funnel + duplication** (§5), including the true reject rate now that the denominator exists. |
| [ ] **A6** | **Report 3 — cadence decision** (§6) and the two Stage-2 blocking decisions; assemble the single deliverable (§8). |

---

## §4 — Report 1: cost per producer

**The table Ron asked for.** One row per producer:

| Column | Definition |
|---|---|
| `objects_pulled` | from `kafka_funnel.csv` — the denominator |
| `objects_enriched_instances` | processing instances that generated ≥1 chat call |
| `objects_enriched_unique` | distinct dedup keys among those |
| `cost_usd` | sum of all `llm_cost_events.cost_usd` for that source, in-window |
| `cost_per_unique_object` | `cost_usd / objects_enriched_unique` |
| `cost_wasted_usd` | cost attributable to instances whose object was already in the vault |

Then the totals row, and the split by `site` (enrich vs consensus vs embed vs
rescue) so the shape of the spend is visible, not just its size.

**The finding this must quantify precisely.** From the report CSVs, enrichment
(`gold_enrich` + `gold_consensus`) is $1.3399 of $1.3511 — **99.2% of all spend**.
Embeddings, including the entire semantic-rescue stage, are 0.8%. Any optimisation
that targets embeddings is targeting noise. State this explicitly so nobody
optimises the wrong thing later.

**The projection — "what would a day cost with KG-A-7 fixed".** Recompute the
total counting each dedup key's enrichment **once**:

```
projected_cost = Σ over distinct dedup keys of (cost of that key's FIRST instance)
```

Report measured and projected side by side, per source. Preliminary ratios from
`funnel.csv` (enrich calls → rows archived) suggest the gap is large: newsapi
1,360→644, arxiv 910→108, hackernews 732→127, telegram 327→327. Telegram is the
control — 1:1, no waste — which is what makes the others credible as waste rather
than as a measurement artifact.

**State the conclusion in the right currency.** $1.35/day is ~$41/month; the
projected saving is ~$26/month. That does not justify an engineering sprint on its
own. The argument for fixing KG-A-7 is **capacity and retrieval quality**: the RPD
floor was 6,718 on a single calendar day against a 10,000 cap (and the floor
undercounts, since it records only successful calls), and every duplicate
enrichment also lands a near-identical vector in `knowledge_vectors` (KG-A-8),
diluting the recall the Domain-B agent depends on. Make this argument explicitly
in the deliverable — a reader who sees only the dollar figure will draw the wrong
conclusion.

---

## §5 — Report 2: object funnel + duplication

**The funnel, per source, all in-window:**

```
pulled (Kafka)  →  silver (Kafka)  →  gate outcome  →  archived / rejected
```

Columns (a)–(c) come from `kafka_funnel.csv`; the rest from the E4 dumps.

**Outcome derivation for the global-news path** — the four states are mutually
exclusive and jointly exhaustive:

| Outcome | How to derive |
|---|---|
| `passed` | vault row with `relevance_score ≥ 0.15` and `rescue_cosine IS NULL` |
| `rescued` | vault row with `rescue_cosine IS NOT NULL` (score < 0.15) |
| `rejected` | row in `filter_rejects` |
| `enriched_then_deduped` | a `gold_enrich` cost event whose instance produced no new vault row — i.e. enrichment ran and the archive was a no-op |

The fourth state is the one nobody has counted before and the one that carries the
money. Deriving it is the analytical core of this report.

**First-vs-repeat:** order each dedup key's instances by timestamp; the earliest is
`first`, all others `repeat`. This is what separates "we pulled a lot" from "we
pulled the same thing a lot".

**The true reject rate — the thing the denominator unlocks:**

```
reject_rate = rejects_distinct / distinct objects that reached the gate
```

Until now only `rejects_distinct` (1,254) was known, with no denominator, so the
filter's strictness was unmeasurable. Compute it per source. Also report the
duplication ratio per source (`raw rows / distinct keys`) — newsapi 1,406/518 =
2.7× on the reject side is the reference point.

**Reconciliation identity**, per global-news source:

```
pulled ≥ silver ≥ (passed + rescued + rejected + enriched_then_deduped)
```

If it fails, **record the discrepancy as a finding and do not force it to close.**
Two structural leaks are already known and expected: dedup skips at `kv_archive`,
and Silver→Gold consumption lag at the window edge. A gap that these do not
explain is new information.

**hackernews:** its funnel stops at `enriched` / `enriched_then_deduped`. Render
the reject cell as `unknown (KG-A-12)`. Do not render it as `0` in any table, chart
or summary line — a zero there will be misread as a fact by every future reader.

---

## §6 — Report 3: cadence decision

The question is whether newsapi and hackernews should move from a 20-minute pulse
to hourly. It has been treated as a cost question; the data says it is not.

**Three inputs, weighed together:**

1. **OpenAI cost.** newsapi + hackernews are ~65% of the day's spend, so a 3×
   cadence cut sounds large — but it is a cut to $41/month. Cost alone does not
   decide this.
2. **newsapi.ai token budget.** Each `getArticles` call is 1 token; one pulse
   iterates 5 category URIs = 5 tokens; at 20-minute intervals ≈ **360 tokens/day**.
   Hourly ≈ 120/day. This is a hard external quota, unlike the OpenAI dollars, and
   it is the stronger argument.
3. **Coverage loss — the side nobody has quantified.** Compute, from the flat
   table, the distribution of *how many distinct pulses each URL appeared in*.
   URLs appearing in exactly one pulse are the ones an hourly cadence would risk
   missing entirely: they entered and left the top-10 inside a single interval.
   **If that share is large, the cadence cut buys savings by losing coverage** —
   and coverage, not cost, is the pipeline's reason to exist.

**Also evaluate query diversification, not only interval.** Repeating the same 5
category URIs every pulse guarantees overlap by construction — the same top-10
comes back. Rotating or widening the query set could raise distinct-article yield
**per token** without touching the interval at all. Quantify the headroom: what
fraction of each pulse's returned articles were already seen in the previous pulse?
A high overlap fraction argues for diversification over slowdown; a low one argues
the opposite. Note that the day-run data cannot attribute an article to the query
that fetched it (that field is not recorded anywhere), so this can only be
inferred from URL-repeat structure — state the limitation rather than overclaiming.

**Then settle the two decisions that block Stage 2 T2.1** (both want the same
`anizai-airflow` rebuild; deciding late means rebuilding twice):
- **The cadence change** — decided on the above, with the coverage cost stated
  alongside the saving, not buried.
- **KG-C-5 secret rename** (`NEWSAI_API_KEY` → `THE_NEWS_API_KEY`) — independent of
  this data; it simply needs to ride the same rebuild.

---

## §7 — What this analysis deliberately cannot answer

State these in the deliverable so nobody mistakes silence for a clean bill:

1. **Whether the filter is correct.** Nothing in this data says a rejected article
   *should* have been rejected. That is human judgement on a sample — Phase 7B.5,
   separate plan.
2. **Anything about hackernews rejects.** Unobservable (KG-A-12).
3. **Which query fetched which article.** Not recorded anywhere, so query-mix
   effectiveness is inferable but not measurable.
4. **True RPD.** `llm_cost_events` records successful calls only; SDK retries burn
   quota invisibly. Every RPD figure here is a floor.
5. **Cost under gpt-4o.** Everything is a gpt-4o-mini number — the ratified
   production enrichment model. Not comparable to any gpt-4o projection.

---

## §8 — Deliverable

One report: `docs\A_pipeline\reports\dayrun-20260722\dayrun_analysis.md`, with the
three report tables inline and the derived flat table exported alongside as
`instances_flat.csv` (so 7B.5 and any later question can be answered without
rebuilding it).

Structure: the cost table first (it is what Ron asked for), the funnel second, the
cadence recommendation third with both sides argued, then the §7 limitations, then
the recommended next actions with their KG references.

Update `pipeline_sprints.md` §1 to reflect 7B.5-I's true state on close, and mark
T8/T9 in the 7B.5-I plan.

---

> Companions: `plans/dayrun20260722_extraction.md` (produces every input here),
> `plans/phase7b5_filter_calibration.md` (the quality half, which consumes the same
> dumps), `plans/phase7b5i_filter_observability_and_cost.md` §7 (the protocol this
> closes out). Gaps referenced: KG-A-7, KG-A-8, KG-A-12, KG-A-13, KG-C-5.
