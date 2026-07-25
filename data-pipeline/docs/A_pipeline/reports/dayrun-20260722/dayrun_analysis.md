# dayrun_analysis.md — Day-Run Analysis, `dayrun-20260722`

> Domain: A — Pipeline
> Type: Analysis deliverable (offline; no cluster required)
> Last updated: 2026-07-25
> Window: `[2026-07-22T09:25:26Z, 2026-07-23T09:25:26Z)` — every figure below is
> bounded on the row's own timestamp, never on `run_id`.
> Source plan: `plans/dayrun20260722_analysis.md` · Companion artifact: `instances_flat.csv`

---

## §0 — Read this first

**The pipeline is healthy.** For the three global-news sources (newsapi, arxiv,
telegram) the accounting closes exactly: every message Kafka pulled reached the
filter and got a verdict, every row written to `knowledge_vault` traces back to
the instance that produced it, and nothing was lost in between. That is the bulk
of the pipeline, and it works.

**One real inefficiency, already known.** Duplicate enrichment (KG-A-7): the same
article, re-fetched, is enriched again at full price, and the duplicate is only
caught at the moment of writing — after the money is spent. This day-run measured
it precisely for the first time. It costs ~$19/month. The reason to fix it is not
the money (see §1).

**Everything else found here is a measurement gap, not a broken system.**
HackerNews reject data is not captured; `filter_rejects` has no key back to the
instance; three existing artifacts contain statements written before a denominator
existed. None of these means anything is malfunctioning.

**One blocking decision was settled: do not change the producer cadence** (§3).

### Vocabulary used throughout

| Term | Meaning |
|---|---|
| **instance** | One Kafka message = one `trace_id`. Minted on arrival, regardless of content. **This is the unit of spend.** |
| **item** | One unique article = one URL / `document_hash`. **This is the unit of content.** |

The gap between the two is the whole story. newsapi: 2,766 instances, ~1,160 items.

---

## §1 — Report 1: cost per producer

**Day total: $1.3511 across 8,835 successful calls and 5,493 instances.**

### Where the money goes

| Category | USD | Share |
|---|---|---|
| Enrichment (`gold_enrich` + `gold_consensus`) | **$1.3399** | **99.2%** |
| All embeddings (`gold_embed` + `rescue_embed`) | $0.0112 | 0.8% |

Stated plainly so it is not re-litigated later: **any optimisation aimed at
embeddings — including the entire semantic-rescue stage — is aimed at noise.**

### Per source

| Source | instances | enrich calls | archived | `enriched_then_deduped` | enrich USD | wasted USD |
|---|---|---|---|---|---|---|
| newsapi | 2,766 | 1,360 | 644 | **716** (52.6%) | $0.5738 | **$0.3065** |
| arxiv | 1,400 | 910 | 108 | **802** (88.1%) | $0.3583 | **$0.3159** |
| telegram | 595 | 327 | 327 | **0** | $0.1037 | $0 |
| hackernews | 732 | 732 | — | *unmeasured (§4)* | $0.3041 | *unmeasured* |

`enriched_then_deduped` is derived **row-level**, not by subtraction: each
`gold_enrich` event's `trace_id` was tested for the presence of a
`knowledge_vault` row carrying it as `canonical_event_id`. The row-level result
matches the aggregate subtraction exactly for all three sources.

**telegram is the control.** Zero waste, same code, same path — because Telegram
channels push each message once and maintain no "top list". This is what makes the
newsapi and arxiv figures credible as genuine waste rather than a measurement
artifact.

### Projection — what a day would cost with KG-A-7 fixed

Counting each item's enrichment once:

| | measured | projected | saving |
|---|---|---|---|
| Global-news enrichment | $1.0384 | **$0.4160** | $0.6224/day ≈ **$18.7/month** |

### The argument for fixing KG-A-7 is NOT the money

$19/month does not justify a sprint. Two other arguments do:

1. **Request-per-day headroom.** 6,718 calls on 2026-07-22 against a 10,000/day
   cap — 67%, and that is a **floor** (only successful calls are recorded; SDK
   retries burn quota invisibly). Roughly 46% of that traffic is duplicate work.
   Adding a source, or raising any cadence, walks into the cap — and the failure
   mode is enrichment stopping mid-day.
2. **Retrieval quality (KG-A-8).** Every duplicate enrichment also lands a
   near-identical vector in `knowledge_vectors`. The Domain-B agent retrieves k
   results; the more of them are copies of one article, the less information
   reaches the context window. This never appears on an invoice — it appears in
   forecast quality.

---

## §2 — Report 2: object funnel

### The reconciliation closes exactly

For each global-news source, instances pulled = passed the sniper + failed it:

| Source | pulled | passed sniper | failed sniper | rescued | rejected (rows) | identity |
|---|---|---|---|---|---|---|
| newsapi | 2,766 | 1,358 (49.1%) | 1,408 | 2 | 1,406 | ✓ exact |
| arxiv | 1,400 | 910 (65.0%) | 490 | 0 | 490 | ✓ exact |
| telegram | 595 | 316 (53.1%) | 279 | 11 | 268 | ✓ exact |

No remainder in any source. Three things follow:

1. **No instance is lost.** Everything pulled reached the gate and got a verdict.
2. **No window-edge lag.** Silver did not trail Gold at the boundary; the window is whole.
3. **The gate precedes payment.** A rejected instance never reaches enrichment —
   it pays only its rescue embedding (~$0.000002). The concern that rejected items
   might still be enriched is **closed, and the answer is good.**

> The analysis plan (§5) predicted two structural leaks and warned the identity
> would probably not close. It closed. This is a stronger result than planned, and
> it is what makes every figure derived from it trustworthy.

### True reject rate — now that a denominator exists

| Source | rejected instances | of pulled | distinct items rejected | duplication |
|---|---|---|---|---|
| newsapi | 1,406 | **50.8%** | 518 | 2.71× |
| arxiv | 490 | **35.0%** | 468 | 1.05× |
| telegram | 268 | **45.0%** | 268 | 1.00× |
| hackernews | *unknown (KG-A-12)* | *≈79.7% inferred, §4* | *not captured* | — |

### Semantic rescue barely fires

13 rescues out of 2,177 rescue evaluations (0.6%), split **newsapi 2 / arxiv 0 /
telegram 11**. Maximum observed `rescue_cosine` is ~0.345 across all sources,
below the promote threshold. Telegram carries 11 of the 13 despite being the
smallest source — the threshold behaves differently there. This is a Phase-7B.5
question, recorded here, not answered here.

---

## §3 — Report 3: cadence decision

### Decision: DO NOT change the newsapi or hackernews cadence. Keep 20 minutes.

The change was proposed as a cost saving. The data says the saving is small and
the cost of taking it is large.

**Pulse-appearance distribution (newsapi, reject side, 518 distinct URLs):**

| appeared in N pulses | URLs |
|---|---|
| **1 only** | **289 (55.8%)** |
| 2 | 100 |
| 3 | 37 |
| 4+ | 92 |

**Direct simulation.** Sampling one pulse in three — exactly what an hourly
cadence does — retains 292 of 518 distinct URLs: **a 43.6% loss of unique
articles.** The saving on the other side is ~$14/month.

Two notes that strengthen rather than weaken this:

- The distribution above is measured on the **reject** side, the only side with
  per-instance URLs. The passed side duplicates *less* (≤2.11× vs 2.71×), so its
  single-pulse share is likely **higher** — the direction worsens, not improves.
- The newsapi.ai token budget (≈360/day at 20-min cadence; 5 tokens per pulse) is
  the one genuine argument for slowing down. It does not outweigh 43% coverage.

### The open question this decision does NOT settle: query spread

Slowing the cadence is a blunt instrument. It cuts the 55.8% single-pulse
articles just as hard as the minority that linger — and it is the lingerers that
generate the duplication. The targeted lever is **which queries are sent, not how
often**: the same 5 category URIs every pulse guarantee overlap by construction.
Spreading or rotating the category set across pulses would raise unique-article
yield per token without touching the interval.

**This cannot be measured from the day-run.** No field records which query
returned which article, so query-mix effectiveness is inferable at best. It is
recorded here as an open design question requiring its own investigation — and
possibly a small instrumentation change first.

### KG-C-5 (secret rename `NEWSAI_API_KEY` → `THE_NEWS_API_KEY`)

Independent of this data. It simply needs to ride the next `anizai-airflow`
rebuild. With the cadence decision resolved as "no change", nothing else is
waiting on this analysis.

---

## §4 — What this analysis could not measure

Stated explicitly so silence is not mistaken for a clean bill.

1. **HackerNews duplicate-enrichment waste — UNMEASURED.** `social_vault` is a
   **Silver-layer archive** (written by the Silver job, deduplicated there), not
   the Gold output; the Gold social path writes to `social_vectors`, which was not
   exported. Any comparison of the 732 consensus calls against the 127
   `social_vault` rows compares two different layers and is meaningless. The
   $0.3041 of hackernews enrichment is therefore neither confirmed waste nor
   confirmed sound. **Resolution:** a one-minute query against `social_vectors`
   when the cluster is next up for the Domain-B test.
2. **HackerNews reject rate — inferred, not proven.** 3,600 pulled minus 732 that
   incurred any LLM call leaves 2,868 that were stopped before payment, implying
   ≈79.7%. Under the identity that closes exactly for all three global-news
   sources these are sniper rejects, but the social path has no second side to
   verify against (KG-A-12). Treat as an order of magnitude, never as a fact — and
   **never render the hackernews reject cell as `0`.**
3. **Which items were duplicate-enriched.** The 1,518 `enriched_then_deduped`
   instances have no resolvable URL: `llm_cost_events` carries only an id, and
   that id never reached any table because the row was never written. Their count
   and cost are exact; their identity is unrecoverable.
4. **Whether the filter is correct.** Nothing here says a rejected article
   *should* have been rejected. That is human judgement on a sample — Phase 7B.5.
5. **True RPD.** `llm_cost_events` records successful calls only. Every RPD figure
   is a floor.
6. **Cost under gpt-4o.** Every figure is a gpt-4o-mini number.

---

## §5 — Corrections to existing artifacts

Three documents contain statements written before a denominator existed. They
should be corrected rather than left to mislead a future reader.

| Artifact | Statement | Correction |
|---|---|---|
| `run_report.md` | hackernews `732 ≫ 127` explained as "social-consensus aggregation/dedup (known funnel leak, not an error)" | Two errors. Consensus does **not** aggregate — 732 calls carry 732 distinct `trace_id`s, strictly 1:1. And the two numbers belong to different layers (Gold spend vs Silver archive), so the comparison is invalid in either direction. |
| `funnel.csv` | column `enriched_distinct` | Counts instances that produced **any** LLM call, including `rescue_embed` for rejected items — which is why it equals Kafka `pulled` exactly for global news (2,766 / 1,400 / 595). It is not an enrichment count. newsapi enriched 1,360, not 2,766. Rename or annotate. |
| `funnel.csv` | polymarket `archived` 26,166 vs `pulled` 26,136 | Archived exceeds pulled by 30. Likely pre-window rows; needs an explanation, not silence. |

---

## §6 — Known-Gap updates

**KG-A-7 — correct the day-run evidence line.** The global-news evidence is
sound and now row-level verified (newsapi 1,360→644, arxiv 910→108,
telegram 327→327). The `hackernews 732 → 127` figure must be **removed**: it
compares Gold calls against a Silver archive (§4.1). Overall waste share should
read **~46% of day spend, global-news only**, not ~65% of all enrichment.

**KG-A-12 — split "unmeasurable" into rate vs items.** The Kafka denominator now
yields an inferred reject rate (≈79.7%); the **items** remain uncaptured. Add the
practical consequence: ~2,868 rejected items per day are discarded unrecorded —
a calibration corpus burned daily. This is an independent reason to close
KG-A-12 *before* Phase 7B.5, not after.

**KG-A-13 — ready to close.** Its stated condition was to count empty-`trace_id`
rows in the run window. Done: **0 empty across all four sites**
(`gold_consensus` 732, `gold_embed` 3,329, `gold_enrich` 2,597, `rescue_embed`
2,177). The S4.6 figure stands. What remains is the code-level inconsistency —
the rescue call site bypasses the `_cost_trace_id()` helper — a latent risk, not
an active fault. Downgrade rather than delete.

**New gap (proposed) — `filter_rejects` has no instance key.** Migration 003
annotates `llm_cost_events.trace_id` as joining `rescue_embed` → `filter_rejects`,
but `filter_rejects` carries no `canonical_event_id` and no `trace_id`; the join
described does not exist. Impact on this analysis is negligible ($0.0079, already
reconciled by count). Impact later: a rejected item cannot be traced back to its
Bronze/Silver payload — which matters when 7B.5 reviews rejects whose URL repeats
2.71× in-window. Fix is one nullable column plus passing a value already in scope
at the write site. Not retroactively recoverable. Priority: Low. Should ride the
next Flink rebuild.

---

## §7 — Recommended next actions

| # | Action | Blocking? |
|---|---|---|
| 1 | Cadence: **no change**. Record and move on. | Unblocks the `anizai-airflow` rebuild |
| 2 | Fold KG-C-5 (secret rename) into that rebuild | No |
| 3 | Apply the §5 corrections and §6 KG updates | No |
| 4 | Query-spread investigation (newsapi category rotation) — separate scope | No |
| 5 | `social_vectors` query while the cluster is up for the Domain-B test | No |
| 6 | KG-A-7 fix — move the exists-check ahead of Gold dispatch, both paths | No; argued on RPD headroom + retrieval quality, not cost |

---

## §8 — Companion artifact

`instances_flat.csv` — 5,493 rows, one per processing instance:
`source · path · dedup_key · ts · outcome · relevance_score · rescue_cosine ·
cost_usd · trace_id · url_resolvable · first_or_repeat`.

`dedup_key` is null for the 1,518 `enriched_then_deduped` instances and for all
hackernews rows (§4.1, §4.3); `url_resolvable` marks this explicitly so no future
reader mistakes a null for a zero. Among rows with a resolvable key: 2,333 first
appearances, 910 repeats.
