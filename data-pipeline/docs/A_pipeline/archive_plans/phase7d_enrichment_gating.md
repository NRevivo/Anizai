# phase7d_enrichment_gating.md
> Domain: A — Pipeline
> Type: Sprint Plan
> Last updated: 2026-07-27 (§0 note, §4.2, §5 T11–T12 and §8.1–§8.5 revised in the
> pre-T11 Advisor session — see §8.0 for the decision log)
> TL;DR: Close the duplicate-enrichment loop measured by the `dayrun-20260722` day-run.
> Gates Gold enrichment on the dedup check that already runs one step earlier (KG-A-7),
> makes global_news `signal_id` deterministic so re-deliveries stop accumulating
> near-identical vectors (KG-A-8), extends reject capture to the social path so
> HackerNews stops burning ~2,868 uncaptured rejects a day (KG-A-12), and adds the
> instance key that lets a reject be traced back to its payload. Purpose is **RPD
> headroom and retrieval quality**, not cost. Prerequisite for the multi-day cloud run.

## Navigation
- §0 — Why this sprint exists — the RPD ceiling, not the invoice
- §1 — Verified code facts — re-verified against the working tree 2026-07-26
- §2 — The HackerNews dedup contradiction — RESOLVED by T1 (see F9, F17)
- §3 — Closed decisions — D1 and D2 resolved by Ron 2026-07-26
- §4 — Schemas & contracts — migration 004, settings, invariants
- §5 — Task table — T1–T12 in implementation order
- §6 — Gates & test-quality directives
- §7 — Execution notes for Claude Code
- §8 — Deployment protocol — **REWRITTEN 2026-07-27, read before T11**: §8.0 decision log (P1–P8) · §8.2 queue gate (closed: discard + truncate) · §8.3 sequence · §8.4.2 close pack incl. the new C0 baseline · §8.4.4 partial teardown
- §9 — Acceptance criteria
- §10 — Skills
- §11 — Explicitly out of scope

---

## §0 — Why this sprint exists

The `dayrun-20260722` analysis
(`reports/dayrun-20260722/dayrun_analysis.md`) measured duplicate enrichment for
the first time. The headline conclusion is stated there verbatim and is repeated
here so it is not re-litigated: **the argument for fixing KG-A-7 is not the money.**

Projected saving is $0.62/day ≈ $18.7/month. That does not justify a sprint. Three
other things do.

**1. RPD headroom — the binding constraint on the next milestone.**
6,718 successful OpenAI calls on 2026-07-22, against a Tier-1 cap of 10,000/day.
67%, and that figure is a **floor**: `llm_cost_events` records successful calls
only, so SDK retries burn quota invisibly. Roughly **46% of the day's global-news
enrichment spend was paid on objects that were then discarded**. The failure mode
at the cap is enrichment stopping mid-day, and — because the cap is shared with
the Domain-B agent (KG-C-1) — forecasts failing alongside it. Ron's stated next
milestone is a continuous cloud run of several days to a week. That run does not
fit under the cap at today's duplication rate.

**2. Retrieval quality (KG-A-8) — invisible on any invoice.**
Every duplicate enrichment also lands a near-identical vector in
`knowledge_vectors`. The agent retrieves k results and narrows to 5; the more of
them are copies of one article, the less unique information reaches the forecast
context. This is the concrete mechanism by which a Domain-A inefficiency degrades
Domain-B output.

**3. The reject corpus burns daily and is not recoverable.**
The social path captures nothing: **~2,868 rejected HackerNews items per day are
discarded unrecorded** (inferred rate ≈79.7%, KG-A-12). Phase 7B.5 cannot
calibrate a source with no reject rows. Separately, `filter_rejects` carries no
key back to the instance that produced it, so a reject whose URL repeats 2.71×
in-window cannot be resolved. **Neither is retroactively recoverable** — every day
that passes without them is a day that cannot be calibrated later.

> **Narrow correction (2026-07-27), scoped to the day-run only.** "Not retroactively
> recoverable" is true in general but was *temporarily* false for one specific day: the
> `dayrun-20260722` HackerNews Bronze messages were still inside Kafka's 7-day retention
> as of 2026-07-27, so that day's rejects could have been recovered by replaying them
> through the post-7D code. **Ron decided on 2026-07-27 not to** — see §8.2. The reasons:
> the replay would also have re-paid full `gold_consensus` on every high-signal HN story
> (D1a changed their key), it would have written thousands of duplicate `filter_rejects`
> rows into the very corpus 7B.5 must calibrate on, and D4 makes that day non-special
> anyway (HN cosines are not comparable to news cosines, so there is no cross-source
> value in it being the *same* news cycle). From the second the gate is deployed the
> corpus accrues at ~2,868 HN rejects/day, so the multi-day run supplies far more than
> Kafka held. **One day of HN rejects was deliberately sacrificed; record it in T12.**

### One thing this sprint does NOT need to fix

The day-run closed a standing worry with a good answer: **the sniper gate already
precedes payment.** For all three global-news sources the identity
`pulled = passed + failed` closes exactly, with no remainder. A rejected instance
never reaches enrichment — it pays only its rescue embedding (~$0.000002). The
enrichment waste is duplicates, and *only* duplicates. Do not widen the scope in
this direction.

---

## §1 — Verified code facts

Re-verified against the working tree on **2026-07-26**. `gold_job.py` and
`silver_job.py` were both last modified 2026-07-02, i.e. before the 2026-07-23
gap analysis — so the KG-A-* anchors were written against the code as it stands
today. Line numbers may still drift; **re-confirm positions before editing.**

| # | Fact | Anchor |
|---|---|---|
| F1 | `knowledge_vault.archive()` performs the dedup check itself and returns `None` when `document_hash` already exists. The return value is the discarded signal. | `persistence/knowledge_vault.py::archive` |
| F2 | `exists_by_document_hash(document_hash) -> bool` is **public, standalone, and index-backed** (UNIQUE B-tree on `document_hash`, O(log n)). The gate this sprint needs already exists as a callable — nothing new must be built for the global_news path. | `persistence/knowledge_vault.py::exists_by_document_hash` |
| F3 | `document_hash` is a **required** field on the Silver Document schema (64-char SHA-256, validator-enforced). It is therefore present on every record arriving at the Gold global_news path. | `utils/validators.py::validate_silver_document` |
| F4 | `_cost_trace_id(record)` exists at module scope in `gold_job.py` with the `canonical_event_id or bronze_ref` fallback chain. | `processing/gold_job.py`, top of file |
| F5 | `_deterministic_signal_id(content_hash)` exists (Sprint 11 Gap 2): UUID5 over `NAMESPACE_X500`, falling back to `uuid4()` when `content_hash` is empty. Used by `build_gold_social_pulse`. **The global_news builders do not use it.** | `processing/gold_job.py`, top of file |
| F6 | **VERIFIED 2026-07-26 (T1):** `knowledge_vectors` declares `signal_id UUID PRIMARY KEY` and `knowledge_vectors.insert()` already carries `ON CONFLICT (signal_id) DO NOTHING`. `social_vectors.insert()` has the same guard. **No DDL is needed for KG-A-8** — T5 reduces to the `uuid4` → UUID5 switch. | `infrastructure` `init.sql`; `persistence/knowledge_vectors.py` |
| F7 | `social_vault` has **no UNIQUE constraint** — it is an append-only archive and idempotency is entirely caller-managed via `exists_by_content_hash()`. There is no DB-level backstop on the social path. | `persistence/social_vault.py`, module docstring |
| F8 | `process_social_pulse_message` (Polymarket comments) calls consensus + embedding **unconditionally** — no `is_high_signal` guard, no dedup gate. It is feature-flagged off (KG-A-4) and is **out of scope** here, but the missing guard must not be reintroduced by accident. | `processing/gold_job.py::process_social_pulse_message` |
| F9 | `deduplication.py` provides two hashers: `hash_document(full_text, url)` for newsapi/arxiv, and `hash_social_batch(market_id, comments)`. **T1 correction:** despite its Polymarket-shaped docstring, `hash_social_batch` **is** called by the HackerNews Silver branch as `hash_social_batch(story_id, top_comments)` — so the HN key is comment-inclusive and drifts as comments accrue. D1a replaces this derivation with a `story_id`-only hasher. | `processing/deduplication.py`; `processing/silver_job.py` (HN branch) |
| F10 | `validate_silver_social` requires, for `hackernews`: `story_id, title, url, points, author, published_at, top_comments`. **`content_hash` is not required.** | `utils/validators.py::validate_silver_social` |
| F11 | Embeddings are **0.8% of day spend** ($0.0112 of $1.3511). Any optimisation aimed at the embedding or semantic-rescue stage is aimed at noise. | `dayrun_analysis.md §1` |
| F12 | Semantic rescue fires **13 times out of 2,177 evaluations (0.6%)**; max observed `rescue_cosine` ≈0.345 against a 0.35 promote threshold; telegram carries 11 of the 13 despite being the smallest source. The threshold behaves differently per source. | `dayrun_analysis.md §2` |
| F13 | KG-A-13 measured **0 empty `trace_id` rows** across all four sites in the run window. The S4.6 figure stands. What remains is a code-level inconsistency — latent risk, not an active fault. | `dayrun_analysis.md §6` |
| F14 | Cadence decision is **closed: no change** to newsapi/hackernews (20 min). Sampling one pulse in three loses 43.6% of distinct URLs. Do not reopen. | `dayrun_analysis.md §3` |
| F15 | Flink code-bearing image changes require job **cancel + re-submit**. A pod restart restores the old compiled job graph from HA state and silently ignores the new image. | `pipeline_processing.md §9`, KG-C-4 |
| F16 | Known non-participants: OpenSky unreachable from GKE (KG-A-5), Google Trends 404 (KG-A-3). Both return 0 throughout. Neither is an LLM path. | `pipeline_sprints.md §4` |
| F17 | **VERIFIED 2026-07-26 (Advisor, direct read).** The Silver job does **NOT** dedup HackerNews on `content_hash`. `process_hackernews_message` computes the hash, stores it on the Silver record, and publishes to `SILVER_SOCIAL_PULSE` unconditionally — its only guards are envelope / Bronze-payload / `story_id` / `title` / Silver-schema. **Consequence: the T4 Gold gate is the only dedup point on the social path, so it does fire, and §8.4.2 C1 will show HackerNews skips in the cloud window.** The complete consumer set for social-path `content_hash` is exactly three, all Gold-side: `exists_by_content_hash`, `fetch_social_id_by_content_hash`, and `_deterministic_signal_id`. Closed — do not re-investigate. | `processing/silver_job.py::process_hackernews_message`; `persistence/social_vault.py` |

---

## §2 — The HackerNews dedup contradiction (RESOLVED — kept as decision provenance)

> **This section is settled.** T1 read the code and F9/F17 record the answer:
> the HackerNews Silver branch **did** set a `content_hash`, via
> `hash_social_batch(story_id, top_comments)` — a *comment-inclusive* key that
> drifted every pulse as comments accrued. That is the social-side mechanism of
> both KG-A-7 and KG-A-8, and D1a replaces it with a `story_id`-only key.
> The four-way disagreement below is retained only so a future reader
> understands why the investigation was necessary. **Do not re-open it.**

**Four authoritative sources in this project disagree about whether HackerNews
Silver records carry a `content_hash`.** This is not a detail: it decides whether
the social half of KG-A-7 is a one-line gate move or a new dedup key with its own
design decision.

| Evidence | Says |
|---|---|
| `_deterministic_signal_id` docstring (F5) | "should not happen for Polymarket/**HackerNews** Silver records, which always set content_hash via `hash_social_batch()`" — asserts HN **has** a content_hash |
| `hash_social_batch` signature + docstring (F9) | `(market_id, comments)`, "Usage (Silver Job — **Polymarket** comment branch)" — shaped for Polymarket only |
| `validate_silver_social` (F10) | Does **not** require `content_hash` for the hackernews branch |
| `instances_flat.csv` / `dayrun_analysis.md §8` | `dedup_key` is **null for all hackernews rows** |

If the HN Silver branch does call `hash_social_batch(story_id, top_comments)`,
then a content_hash exists, `_deterministic_signal_id` is already deterministic
for HN, and the gate is a straightforward `exists_by_content_hash()` check moved
ahead of the LLM call. If it does not, then HN `signal_id`s have been `uuid4()`
all along, there is no dedup key at all, and one must be introduced.

**T1 resolves this by reading the code, before anything else is touched.** D1 is
already decided (§3) — what T1 determines is the *shape* of T4: whether a
`content_hash` field already exists on the HackerNews Silver record and is merely
re-derived, or whether it must be introduced. It also determines whether the
one-time re-enrichment note in §3/D1 applies on first deploy.

> Note on why the day-run's null `dedup_key` is not by itself conclusive: the
> analysis could not export `social_vectors`, so a null there may reflect the
> analysis's inability to resolve the key rather than its absence in the record.
> That ambiguity is exactly why this is a read-the-code task and not an
> assumption.

---

## §3 — Closed decisions

Both decisions were closed by Ron on **2026-07-26**. Claude Code implements them as
written — they are not open questions and are not to be re-litigated during
implementation.

### D1 — HackerNews dedup semantics

Only relevant in the shape T1 reveals. The underlying product question is the same
either way: **when the same HN story is re-fetched with more comments, should it
be enriched again?**

| Option | Behaviour | Trade-off |
|---|---|---|
| **(a) story_id** | One enrichment per story, ever | Maximum RPD saving. An evolving discussion is analysed once, from its earliest state — which for HN is usually its thinnest state |
| **(b) content hash over story_id + comment ids** | Re-enrich only when the comment set actually changes | Faithful to the source's nature (HN's value *is* the discussion). Saves much less: with 20-min polling, comments accrue continuously, so many pulses will legitimately produce a new hash |
| **(c) story_id + refresh window** | One enrichment per story per N hours | Bounded cost, captures discussion maturity. Adds a tunable nobody has data to tune yet |

**DECISION (Ron, 2026-07-26) — option (a): one enrichment per HackerNews story,
ever. Option (c) is recorded as a future revisit, conditional on evidence (below).**

Reasoning: the sprint's purpose is RPD headroom before a multi-day run, and (b)
delivers the least of it precisely where the waste is. HackerNews is a
*social-pulse* source feeding community sentiment, not a primary evidence source —
re-analysing the same story as its comment count drifts is low-value relative to
its RPD cost. If the multi-day run shows HN evidence is actually load-bearing in
forecasts, (c) becomes a data-backed follow-up rather than a guess made now.

**Note the honest caveat:** HN duplicate waste is *unmeasured* (`dayrun_analysis.md
§4.1` — the 732→127 comparison compared Gold spend against a Silver archive and is
invalid). The `social_vectors` count query, already on Ron's list for the next
cluster bring-up, is what turns this decision from reasoned to evidenced. It does
not block implementation — it can only widen or narrow the expected saving, not
change the chosen semantics.

**Implementation shape (D1a).** `story_id` is a required field on the HackerNews
Silver record and is validator-enforced (F10), so it is guaranteed present. The
cheapest correct implementation reuses both existing mechanisms rather than adding
a query path:

- Derive the social dedup key as a SHA-256 over a namespaced `story_id`
  (e.g. `sha256("hackernews|<story_id>")`), stored on the Silver record in the same
  `content_hash` field the Polymarket branch already uses.
- `exists_by_content_hash()` (existing) then becomes the pre-dispatch gate unchanged.
- `_deterministic_signal_id()` (existing, F5) then produces a stable `signal_id` for
  HackerNews with no modification — closing the social half of KG-A-8 as a side
  effect, at no extra cost.

**One-time re-enrichment on first deploy — expected, not a fault.** If T1 finds that
HackerNews already sets a `content_hash` derived from the comment set, this change
alters the key's *semantics*. Every story currently in flight will present a key that
has never been seen before and will be enriched once more. That is a bounded,
one-off cost on the first post-deploy cycle. Note it in the deployment log so the
first hour's call count is not misread as the gate failing.

**Archival consequence of D1a (approved, not to be changed — Ron 2026-07-26).**
The `story_id`-only key drives BOTH dedup **and** archival, because `social_vault`
archival is gated by the same `exists_by_content_hash(content_hash)` check. So
`social_vault` now retains only the **first** comment-set captured for each HN story
— the raw discussion archive stops updating on re-fetch, not just enrichment.
"One enrichment per story" and "one archive per story" come together because they
share the key. This follows correctly from D1a; Ron approved it. Recorded in the
T12 docs (`pipeline_storage.md` / `pipeline_processing.md`).

**Revisit condition for (c).** If the multi-day cloud run shows HackerNews evidence
carrying real weight in forecasts — appearing in `evidence` with `used_in_answer`
true and non-trivial `impact_on_forecast` — revisit with a refresh window. Note that
option (c) restores **both** archival and enrichment (a refresh window re-opens the
shared key), not just enrichment. Until then, the cost of re-analysis is not
justified by the source's role.

### D2 — Does the social path get semantic-rescue *promote*, or cosine capture only?

KG-A-12 as written asks for both semantic rescue and reject capture on the social
path. They are split.

**DECISION (Ron, 2026-07-26): compute and store `rescue_cosine` for social-path
rejects; do NOT wire the promote branch.**

Reasoning: rescue fires 0.6% of the time; the maximum observed cosine across the
entire day (~0.345) sits *below* the 0.35 promote threshold; and telegram carries
11 of 13 rescues despite being the smallest source — strong evidence the threshold
is source-dependent. Phase 7B.5 exists specifically to set that threshold
empirically, and will likely set it per-source. Wiring a promote branch now means
choosing a news-calibrated threshold for HackerNews discussions by guesswork, and
then changing it two weeks later.

Computing the cosine costs ~$0.000002 per reject (F11 — noise) and is exactly the
datum 7B.5 needs to sweep the threshold for this source. So: **capture everything
7B.5 needs, decide nothing 7B.5 owns.**

**What this means in practice.** Today a rejected HackerNews story vanishes with a
debug log. After this sprint it is recorded, with its cosine — so the source's true
reject rate becomes a measured number for the first time, and Phase 7B.5 can sweep a
threshold for it. It simply does not yet get a second chance. Given that the highest
cosine observed across the entire day-run (~0.345) sits below the current promote
threshold (F12), almost nothing is being given up.

**Revisit condition.** Phase 7B.5 owns the promote decision. If its sweep over the
captured social-path cosines shows a source-appropriate threshold at which real
stories would have been rescued, wire the promote branch then — flag-gated, with the
threshold 7B.5 sets, not the one inherited from the news path.

### §3.3 — Implementation decisions locked at kickoff approval (Ron, 2026-07-26)

T1 investigation complete (findings below). Three refinements to the §5 approach,
plus one new gap and one named test assertion. **T1 result:** the HackerNews Silver
branch already sets `content_hash = hash_social_batch(story_id, top_comments)`
(`silver_job.py::map_hackernews_story_to_silver`) — a *comment-inclusive* hash, so
`_deterministic_signal_id` is deterministic only per comment-set (the social-side
mechanism of KG-A-8). D1a changes the derivation to `story_id`-only, so the §3/D1
**one-time re-enrichment on first deploy DOES apply**.

- **D3 — T3 gates on `knowledge_vault.archive()`'s return, not a new query.**
  `archive()` already returns `None` when `document_hash` exists (F1); that return is
  currently discarded in `GlobalNewsGoldFunction.process_element` — which is precisely
  KG-A-7. Gate on it: **no new `exists_by_document_hash` call** (zero extra queries on
  the enrichment operator already implicated in KG-A-9 checkpoint stalls). **`doc_id is
  None` arises in two cases** — a duplicate, and `archive()` having raised (caught,
  warned, `doc_id` left None). Track the raise separately; gate only on *"None without
  raising"*. An archive **failure** still proceeds to enrichment, unchanged. Dedicated
  Gate 2 test for the distinction (§6).
- **D4 — O1(b): shared `_embed_and_score` core; HN reject cosine over title + body.**
  The extraction touches working news-path code, so a **pre/post-refactor cosine-equality
  test on the news path ships with it** (§6). **The sniper reference vector is
  news-built** — HN cosines are therefore NOT directly comparable to news cosines;
  Phase 7B.5 must not pool them into a single threshold (recorded here and carried into
  the T12 docs).
- **D5 — O2: the caller normalises, one writer.** The T6 social branch maps the HN record
  into the news-doc shape and passes it to `insert_reject`; `insert_reject`'s field
  contract stays explicit (no `original_url or url` fallback inside persistence).
- **New gap — KG-A-14 (proposed; record in T12): archived-but-unvectorised under the
  gate.** An article archived to `knowledge_vault` whose enrichment *then* failed (→ DLQ,
  no vector) is, on re-delivery, skipped by the D3 gate and never receives a
  `knowledge_vectors` row — searchable full-text present, semantic vector absent. Rare
  (requires a post-archive enrichment failure); recoverable via DLQ reprocessing or by
  disabling the gate. Recorded, not fixed.
- **Named invariance assertion (T9/§6):** low-signal HN reject capture is ordered *before*
  the T4 dedup gate, so a low-signal HN **duplicate** captures a reject identically with
  the gate ON and OFF. This is an explicit, named test — not incidental.

---

## §4 — Schemas & contracts

### §4.1 Migration 004

```sql
-- filter_rejects: instance key (new gap, dayrun_analysis §6)
ALTER TABLE filter_rejects ADD COLUMN IF NOT EXISTS canonical_event_id TEXT;
CREATE INDEX IF NOT EXISTS idx_filter_rejects_cei
    ON filter_rejects (canonical_event_id);
```

**VERIFIED 2026-07-26 (T1):** `filter_rejects.source_name` is plain `TEXT NOT NULL`
with **no CHECK constraint** — `'hackernews'` is already admissible, nothing to widen.
Note also that `filter_rejects.rescue_cosine` is `REAL NOT NULL`: T6 must always pass
a float, `0.0` on the empty-text / embed-failure edge, never `NULL`.

**No further DDL is required.** D1a reuses the existing `content_hash` field, and F6
confirms the `knowledge_vectors` conflict guard already exists. **All statements
idempotent**, applied to both
`infrastructure` `init.sql` (fresh installs) and as a numbered migration script
for the existing local + cloud databases — same pattern as migration 003.

### §4.2 Settings

| Var | Default | Semantics |
|---|---|---|
| `ENRICHMENT_DEDUP_GATE_ENABLED` | `true` | Gates the pre-dispatch dedup check on both paths. Exists as a kill switch, not as an experiment — default **on**. Env-only change: pod restart, no rebuild, no cancel/resubmit. |
| `REJECT_CAPTURE_ENABLED` | *(existing)* | Now also gates the social-path capture added in T6. Same flag, extended scope — do not introduce a second one. |
| `RUN_ID` | *(existing)* | Stamps `filter_rejects` / `llm_cost_events` rows. Static env var — it does not expire with a window. |
| `LOG_INFO_SAMPLE_RATE` | `0.01` (pipeline default) | Fraction of INFO lines that reach Cloud Logging. **Must be `1.0` for this window** — see §8.3. |

**VERIFIED 2026-07-27 against `infrastructure/k8s/flink-jobmanager-deployment.yaml` and
`flink-taskmanager-deployment.yaml` — both carry day-run values that will silently break
this window if applied unchanged. All four rows below must be set in BOTH manifests
(JM and TM), identically:**

| Var | Value in the manifests today | Required for the window | If left as-is |
|---|---|---|---|
| `REJECT_CAPTURE_ENABLED` | `"false"` | `"true"` | T6 runs, reaches the write, and writes nothing. **C5/C6 return zero rows and it looks like a code bug.** The single most dangerous of the four. |
| `RUN_ID` | `"dayrun-20260722"` | `"phase7d-verify-20260727"` | This window's rows are stamped with the day-run's tag and mix into the corpus 7B.5 calibrates on. |
| `ENRICHMENT_DEDUP_GATE_ENABLED` | *absent* | `"true"` (explicit) | Works (code default is on), but the kill switch is not at hand — needing it mid-window means editing YAML under pressure. |
| `LOG_INFO_SAMPLE_RATE` | *absent* | **REVERTED 2026-07-27 — leave absent (default 0.01).** | **This row is withdrawn.** Setting it to `1.0` was an Advisor addition for this window and is now judged not worth its risk: it multiplies Python-worker log traffic ~100× and that traffic crosses the Beam worker→JVM channel, making it a live suspect in the T0 OOM. It is also the single largest delta between this window and the `dayrun-20260722` configuration, which provably ran nine sources for 24 hours. **What it costs:** C1's skip-line count becomes a ~1% sample — acceptable, because C1 was never the magnitude (C2/C3 are SQL and unaffected), and the six startup lines become unreliable — also acceptable, because the four flags were verified far more directly by importing `config.settings` in-container (§8.3 step 8b). ERROR and WARNING pass at 100% regardless, so crash and failure detection is untouched. |

**Resting state after the window (decided by Ron 2026-07-27):**

- `REJECT_CAPTURE_ENABLED` **stays `true`**. It was born as a collection-run flag in
  7B.5-I; that framing no longer holds. Turning it off loses corpus **permanently**;
  leaving it on costs disk, which is reversible. **Return condition:** Phase 7B.5 closes
  (thresholds calibrated — capture is then cost without return), OR the §8.5 PVC headroom
  check fails before a multi-day run.
- `ENRICHMENT_DEDUP_GATE_ENABLED` stays `true`. Documented use of `false`: (a) the gate
  misbehaves in cloud — above all if the D3 two-`None` distinction is broken in the
  wiring, in which case a transient Postgres blip reads as a duplicate and enrichment
  stops silently while every dashboard stays green; (b) KG-A-14 recovery.
- `LOG_INFO_SAMPLE_RATE` **reverts to the default** — see §8.5. Unsampled INFO at
  day-scale volume is exactly the flood the sampling exists to prevent.

All four are env-only: pod restart, no rebuild, no cancel/resubmit (F15). Note that
toggling one mid-window restarts the TaskManager, so the job recovers from its last
checkpoint — a few seconds of interruption, not free but cheap.

### §4.3 The invariant this sprint must not break

**Gating changes how many times an item is enriched. It must not change which
items are archived, or which pass the filter.**

Concretely, before and after, for the same input stream:
- the set of `document_hash` values present in `knowledge_vault` is identical;
- the set of `filter_rejects` rows (by content) is identical;
- sniper pass/fail decisions are identical;
- only `llm_cost_events` row counts fall.

This is the single most important test assertion in the sprint (§6).

---

## §5 — Task table

Implementation order is the listed order. T1 runs first and reports before T4.
T10 is a hard stop.

| Task | Description |
|---|---|
| [x] **T1** | **Investigation — read-only, no code changes. Runs first.** Resolve §2: does the HackerNews Silver branch set a `content_hash`, and via what? Read the HN branch of `silver_job.py` and the HN dispatch in `gold_job.py` (`process_hackernews_gold_message`). Report to Ron: (a) does HN carry a dedup key today; (b) if yes, what is it derived from, and is `_deterministic_signal_id` therefore already deterministic for HN; (c) whether the §3/D1 one-time re-enrichment applies on first deploy. **Report before implementing T4.** The decision itself is closed — this establishes the implementation shape, not the semantics. |
| [x] **T2** | Migration 004 (§4.1) + the two settings (§4.2). Idempotent; `init.sql` and migration script both. |
| [x] **T3** | **KG-A-7, global_news path. Gate on the return value that already exists — do NOT add a new `exists_by_document_hash` pre-check.** In `GlobalNewsGoldFunction.process_element`, `kv_archive(silver_doc)` already runs before the enrichment dispatch and already returns `None` when `document_hash` is present; that return is currently discarded. Gate on it. Zero extra queries on an operator already implicated in KG-A-9 checkpoint stalls. **CRITICAL — `doc_id` is `None` in two different cases:** (i) duplicate (archive returned `None`), and (ii) archive **raised** (the existing `try/except` logs a warning and leaves `doc_id` at its `None` initial value). The existing in-code comment states that archive failure must **not** block Gold enrichment. Track the exception in a separate flag and gate **only** on "archive returned `None` **without** raising". On that path: log INFO (hash prefix + source) and return before dispatch, embedding, Gold build, and vector write. On archive failure: proceed to enrichment exactly as today. `ENRICHMENT_DEDUP_GATE_ENABLED=false` → today's behaviour exactly. The dedup check inside `archive()` stays where it is — it remains the last-resort guard (F1). **Do not remove it.** |
| [x] **T4** | **KG-A-7, social path.** Implement D1a (§3): the HackerNews dedup key is derived from `story_id` alone, namespaced and SHA-256'd into the existing `content_hash` field, so `exists_by_content_hash()` becomes the pre-dispatch gate and `_deterministic_signal_id()` becomes stable for HN with no changes to either. Same flag and same skip semantics as T3: the dedup decision moves ahead of the consensus call. Polymarket comments (`process_social_pulse_message`) are **out of scope** — do not touch that function (F8, KG-A-4). |
| [x] **T5** | **KG-A-8.** (a) **Verification only — no DDL.** Confirmed 2026-07-26 (T1 report): `knowledge_vectors` already declares `signal_id UUID PRIMARY KEY` and `knowledge_vectors.insert()` already carries `ON CONFLICT (signal_id) DO NOTHING`. Record the confirmation and move on; the original "add them if absent" branch does not apply. (b) Switch the three global_news Gold builders (newsapi / arxiv / telegram) from `uuid4()` to `_deterministic_signal_id(silver_doc["document_hash"])`, reusing the existing Sprint-11 helper unchanged. Social builders untouched — the social side is closed by T4's key change. |
| [x] **T6** | **KG-A-12.** Social-path reject capture: when the `is_high_signal` guard rejects a story, compute the rescue cosine and write a `filter_rejects` row (source, url, title, text, relevance_score, sniper_keywords, rescue_cosine, canonical_event_id, run_id) instead of the current debug log. Gated on `REJECT_CAPTURE_ENABLED`, fail-open (warn, never raise — a capture failure must not turn a clean drop into a DLQ event). **Per D2 (§3): compute and store the cosine; do NOT wire the promote branch.** A social-path story never gets promoted in this sprint — Phase 7B.5 owns that decision. |
| [x] **T7** | **`filter_rejects` instance key.** Populate the new `canonical_event_id` column at both write sites (global_news drop branch and the T6 social drop branch) from `_cost_trace_id(record)` — the value is already in scope at both. |
| [x] **T8** | **KG-A-13.** Route the `compute_semantic_rescue()` cost call site through `_cost_trace_id()` instead of `silver_doc.get("canonical_event_id", "")`. One line. Latent-risk closure (F13). |
| [x] **T9** | Gate 2 + Gate 3 tests per §6. Tests ship with the sprint — no deferral. |
| [x] **T10** | Local E2E against the Docker stack, including the §4.3 invariance proof and a before/after calls-per-item measurement. **HARD STOP — present results and wait for Ron's explicit approval before any cloud work.** |
| [ ] **T11** | Cloud deployment per §8 — **REVISED 2026-07-27; §8.1–§8.5 were rewritten before execution, so read §8 in full and do not work from memory of the old sequence.** What changed: the image is built and pushed BEFORE the cluster comes up and gets a NEW tag (`-7d`); migration 004 now FOLLOWS bring-up rather than preceding it (it needs live Postgres); the §8.2 queue decision is closed (discard + truncate, and truncation is not optional — resubmit starts from `earliest`); four env vars must be set in BOTH Flink manifests (§4.2); a pre-window baseline C0 runs before the jobs are submitted (§8.4.2); six operator startup log lines are an explicit gate before T0; only newsapi + hackernews are unpaused; there is an abort checkpoint at T0+5 with authority to void T0; and the window is 90 minutes, not 60. Includes the §8.4.2 close pack, which Claude Code runs and exports to files before teardown. |
| [ ] **T12** | Documentation closure: KG-A-7 / A-8 / A-12 / A-13 status in `pipeline_sprints.md §4`; the new `filter_rejects.canonical_event_id` column **and the ~2× storage growth from HN reject capture** (§8.5 PVC note) in `pipeline_storage.md`; the gating behaviour in `pipeline_processing.md`; **the D1a archival consequence** — the `story_id`-only key means `social_vault` retains only the FIRST comment-set per HN story (raw discussion archive stops updating on re-fetch, not just enrichment; §3/D1) — in `pipeline_storage.md` / `pipeline_processing.md`; add this phase's row to `pipeline_sprints.md §1`; record D1/D2 and the §3.3 ratified decisions with their rationale. **Qualify the KG-A-8 guard (T5 correction, Ron 2026-07-26):** the deterministic `signal_id` prevents **new** duplicate vectors **going forward only** — a pre-existing article carries a `uuid4` `signal_id`, so on its first re-delivery post-deploy the new UUID5 does not collide with the old row and one more vector is written (subsequent re-deliveries then dedup against the UUID5 row). Duplicates accumulated before this change are cleared only by a DB reset (§8.4.3), not retroactively by T5 — document it as "prevents new duplicate vectors going forward," not an unconditional guard. Apply the three `dayrun_analysis.md §5` corrections to `run_report.md` and `funnel.csv` if not already applied. **Four new Known Gaps to raise:** (1) **stranded-article gap** — an article already in `knowledge_vault` whose enrichment previously failed will now be permanently skipped by the T3 gate and will never receive a vector: searchable text present, vector absent. Rare and likely acceptable; record it rather than discovering it later. Priority: Low. (2) **HN cosine incomparability** — the sniper reference vector was built on news articles, so social-path cosines are not directly comparable to news-path cosines (see §3.3/D4). (3) **malformed-`document_hash` never-archived** (record next to (1) — same "never gets a vector" family, opposite cause; **pre-existing, NOT a D3 regression**) — a record whose `document_hash` is malformed makes `knowledge_vault.archive()` raise on every delivery (`_validate_record` rejects non-64-char hashes), so the T3 gate treats it as an archive failure (`archive_raised`) and enriches it every time while it is never archived. Fail-open is correct here; the record simply never lands in the vault. Priority: Low. (4) **low-signal duplicate rescue-embedding waste** (Ron 2026-07-26 — **record, do NOT fix in 7D**) — the T3 gate sits *after* the semantic-rescue block, so a low-signal article's rescue embedding runs and the branch returns BEFORE `kv_archive`: the dedup gate never sees it, and a low-signal **duplicate** pays a rescue embedding on every re-delivery. Day-run: reject rows vs distinct URLs — newsapi 1,406/518, arxiv 490/468, telegram 268/268 ≈ **910 repeat rescue evaluations/day** (≈42% of rescue calls, ≈10% of the window's total API calls). Cost is noise (embeddings 0.8% of spend) but each counts against the RPD cap 7D exists to relieve. **Deliberately deferred:** (a) ~10% against the ~46% already addressed — not worth widening scope right before the test task; (b) it collides with the §4.3 invariant — skipping before rescue means no cosine → no `filter_rejects` row → reject content would differ gate-ON vs gate-OFF, breaking the sprint's central assertion; (c) it is Phase 7B.5's call — the effect is one reject row per DISTINCT article rather than per instance, which may be *better* for calibration (7B.5 already must dedupe by `original_url`; newsapi duplication measured at 2.71×), but deciding before 7B.5 runs is a guess. Closing it would also substantially cut `filter_rejects` growth, bearing on the §8.5 PVC headroom check. Priority: Low. **AMENDED 2026-07-27 — gap (4) is materially larger on the HN path than the numbers above suggest, and the numbers above cover only newsapi/arxiv/telegram.** The T6 capture branch is ordered *before* the T4 gate (deliberately — it is the named invariance assertion, §6), so a low-signal HN story that persists on the front page across pulses is re-captured **every pulse**: one `filter_rejects` row and one rescue embedding per 20-minute pulse, for as long as it ranks. The day-run's "~2,868 HN rejects/day" is therefore an **instance** count, not a story count. Restate gap (4) with both paths and both denominators; the decision not to fix it in 7D is unchanged. **Six additional items to record (all Ron, 2026-07-27):** (5) **one day of HN rejects deliberately sacrificed** — the `dayrun-20260722` HN Bronze was still inside Kafka's 7-day retention and could have been replayed to recover that day's rejects; it was discarded instead (§0 note, §8.2). Record the reasoning so a later reader does not read it as an oversight, and correct §0's unqualified "not retroactively recoverable" to the scoped form. (6) **DAG-pause override** — `cloud_state.md` §6 and `cluster_operations_guide.md` §12 both instruct *not* to unpause the 7 producer DAGs (written for the Domain-B test). This window overrode that for **newsapi and hackernews only**, and re-paused them at teardown. The standing instruction is correct and stays; record that the override happened, when, and that it was reverted — do not edit those two files. (7) **`RUN_ID` for the window** — `phase7d-verify-20260727`. Note alongside it that `RUN_ID` is a static env var and does not expire with the window, so any export must be bounded by **timestamp**, never by `run_id` alone. (8) **C0 baseline numbers** (§8.4.2) — record the `social_vault` HN rows-vs-distinct-`story_id` ratio for the day-run window, which is the measurement §3/D1 was decided *without*; state plainly whether it retro-validates option (a) or weakens it, and update D1's revisit condition to cite the measured figure instead of "a query still pending." (9) **`knowledge_vectors` duplicate debt** (§8.4.2 C0) — record total rows vs `count(DISTINCT signal_id)`. This is the evidence base for the §8.4.3 reset decision, which until now rested on an assumption nobody had measured; the last recorded figure (9,202) predates the day-run entirely. (10) **7B.5 scope note** — `phase7b5_filter_calibration.md` §1 states hackernews "cannot be calibrated from this or any existing dataset." True at time of writing; false from this deploy forward. Do **not** edit that plan during 7D; flag it so the HN follow-up is scoped deliberately once a real corpus has accrued. |

---

## §6 — Gates & test-quality directives

Standard four-gate model. **Ron's standing directive applies: build tests to the
highest standard — test what can break, not the happy path.** Tests for anything
built here ship in this sprint.

**The invariance suite (§4.3) — the sprint's central assertion:**
- Same input stream, gate ON vs OFF → identical set of archived `document_hash`
  values, identical `filter_rejects` content, identical sniper decisions.
- Same stream, gate ON → strictly fewer `llm_cost_events` rows.
- A *first* occurrence of an item is enriched normally with the gate ON — the gate
  must not suppress genuine new content. This is the test that catches an
  over-aggressive key.
- **NAMED ASSERTION — low-signal HN duplicate (per §3.3).** A HackerNews story that is
  both low-signal **and** a dedup duplicate must produce an identical `filter_rejects`
  row with the gate ON and with the gate OFF. This holds only because the T6 capture
  branch is ordered *before* the T4 dedup gate. Give this test its own name and its own
  docstring stating why the ordering matters — it is the one invariance break that would
  otherwise pass silently and quietly starve the 7B.5 corpus.

**Gate 2 — dedup gating (T3, per D3):**
- Duplicate (`archive()` returns `None`, no exception) → zero LLM calls, zero vector
  write, no DLQ, INFO log emitted.
- **`archive()` RAISES — the critical distinction.** `doc_id` is `None` here too, but
  this is a failure, not a duplicate. Assert enrichment **proceeds normally**, the
  warning is emitted, and no skip occurs. This test is not optional: without it, a
  transient DB blip would silently stop ingestion while every dashboard stayed green.
  Assert both branches explicitly, in the same test class, so the two `None` cases can
  never be collapsed by a later refactor.
- New article (`archive()` returns a `doc_id`) → full path runs, unchanged.
- `ENRICHMENT_DEDUP_GATE_ENABLED=false` → today's behaviour exactly, including on the
  duplicate path.
- `document_hash` missing/malformed → does not crash; falls through to enrichment.

**Gate 2 — shared `_embed_and_score` extraction (T6, per D4):**
- **Pre/post-refactor cosine equality on the news path.** The extraction touches
  working code on the critical path; for a fixed set of news inputs the cosine values
  produced after the refactor must equal those produced before it, exactly. This is a
  behaviour-preservation gate, not a smoke test.
- Social text assembly: HN record with `story_text` present; with only `top_comments`;
  with neither (title only) → no crash, cosine still produced.
- Embed failure → cosine `0.0` (never `NULL` — the column is `REAL NOT NULL`), drop
  still completes cleanly, no DLQ.

**Gate 2 — deterministic signal_id (T5):**
- Same `document_hash` → same `signal_id`, across process restarts.
- Different hash → different id.
- Empty hash → `uuid4()` fallback, no crash.
- Re-delivery of an identical record → exactly one `knowledge_vectors` row.

**Gate 2 — HackerNews dedup key (T4 / D1a):**
- `hash_hackernews_story("12345")` is stable across calls/process restarts and
  differs from a different `story_id`.
- **Whitespace normalisation (T7 fold-in):** `hash_hackernews_story(" 12345 ") ==
  hash_hackernews_story("12345")` — a stray whitespace difference must not fork a
  story's dedup identity.
- `map_hackernews_story_to_silver` stores the `story_id`-derived `content_hash`, so
  `_deterministic_signal_id(content_hash)` is stable per story (closes the social
  half of KG-A-8) and a re-fetch with new comments yields the SAME key.

**Gate 2 — filter_rejects instance key (T7):**
- `insert_reject(..., canonical_event_id=cei)` → the column round-trips (insert →
  `fetch_rejects` → equality); pre-existing rows read back `NULL`.
- Both write sites pass `_cost_trace_id(record)`: a global_news reject carries the
  `canonical_event_id`; an HN reject (no canonical id yet) carries the `bronze_ref`
  fallback, never empty when `bronze_ref` is present.

**Gate 2 — social reject capture (T6):**
- Rejected story, flag ON → exactly one `filter_rejects` row, all fields populated,
  `source_name='hackernews'`, cosine in range.
- Flag OFF → zero rows, drop behaviour otherwise identical.
- Capture INSERT fails → warning only, drop completes cleanly, no DLQ.
- Negative boundary: dedup skips and DLQ traffic produce no reject rows.

**Gate 3 — persistence round-trips** for the new `filter_rejects` column (insert →
read back → equality, including NULL for pre-existing rows), and a round-trip proving
the **existing** `knowledge_vectors` `ON CONFLICT (signal_id) DO NOTHING` guard fires
as expected under the new deterministic id (T5(a) is verification-only — no DDL was
added, so the test exercises the guard that was already there).

**E2E (T10):** run the local stack with a deliberately duplicated input set. Report
calls-per-item before and after. Expected direction: newsapi from ~2.11 toward
~1.0; arxiv from ~8.4 toward ~1.0. Report actuals, do not assert exact targets.

---

## §7 — Execution notes for Claude Code

1. **T1 runs first and is read-only.** Report its findings before implementing T4. D1 and D2 are closed (§3) — T1 establishes implementation shape, not semantics.
2. **Hard stop after T10.** Present the test summary, the invariance proof, and the
   before/after calls-per-item numbers. Wait for Ron's explicit approval before T11.
3. **Zero filter-logic change.** No threshold moves, no change to any pass/fail
   decision. If a task appears to require one, stop and raise it — thresholds belong
   to Phase 7B.5.
4. **Do not touch `process_social_pulse_message`** (Polymarket comments, F8/KG-A-4).
   It is flagged off and carries a known missing guard; leave it exactly as found.
5. **Do not touch `agent/`** or any Domain-B code. Out of scope entirely.
6. **Do not run `pip freeze`** or otherwise regenerate `requirements.lock`
   (KG-A-11) — it would re-resolve range-pinned dependencies days before a
   measurement run.
7. PyFlink 1.19 constraints apply (`pipeline_processing.md §8`): no OutputTag; keep
   new logic in pure functions testable without PyFlink, wiring guarded by
   `PYFLINK_AVAILABLE`.
8. Re-verify every §1 anchor against the working tree before editing.
9. Do not reopen the cadence decision (F14).

### §7.1 — Local environment lifecycle (Claude Code owns this)

Ron will have Docker running. **Claude Code brings the local stack up and takes it
down itself** — this is not something to ask Ron to do at each step.

1. **Bring up** the compose stack (Kafka, Flink, Postgres, and any other service the
   task needs) when Gate 3 / E2E work requires it. If the work needs an emulator,
   bring that up too.
2. **If bringing a service up proves difficult, stop and ask Ron** rather than
   burning turns on environment debugging — he will start it manually. Say plainly
   what is failing and what you need running.
3. **Tear the stack down when work on it is finished** — not between individual
   tasks, but once local verification is complete and there is no further need for
   it. Do not leave containers running after the T10 hand-back.
4. **Local Docker must be OFF during any cloud window.** The local stack shares the
   same OpenAI account and the same newsapi credits as the cloud deployment — a
   local run during a cloud measurement window contaminates both the cost figures
   and the RPD count. This was already a standing rule during the day-run; it
   applies here for the same reason.
5. Note KG-A-10: the local compose Flink leg has a known Beam Python-worker crash on
   first message. In-process replay is the established local verification path. Do
   **not** open a side-quest to fix the local Flink environment — it is a Low-priority
   gap and out of scope (§11).

---

## §8 — Deployment & bring-up protocol

Planned in from the start. **This section is not optional and not "later".**

> **§8.1–§8.5 were rewritten on 2026-07-27, before T11 ran.** Everything below supersedes
> the version that existed when T1–T10 were executed. §8.0 is the decision log; §8.3 is the
> sequence to follow literally.

### §8.0 Decisions taken in the pre-T11 session (2026-07-27)

Eight decisions, all Ron's, all closed. They are recorded here so §8.3 can be read as a
procedure rather than an argument. **Do not re-open them during execution.**

| # | Decision | Why it exists |
|---|---|---|
| **P1** | **The image is built and pushed BEFORE the cluster comes up, under a NEW tag `anizai-flink:1.19.1-7d`.** Never overwrite `-7b5i`. | The build needs only local Docker, not the cluster. And both Flink manifests set `imagePullPolicy: IfNotPresent` — overwriting an existing tag is exactly the case where a node can serve a cached layer and the new code silently never runs. A new tag makes that impossible. |
| **P2** | **The queue is discarded, not processed. All Bronze topics are truncated to their end offsets before the jobs are submitted.** | See §8.2. Truncation is not a preference here — both jobs start from `earliest`, so without it the replay happens whether or not anyone chose it. |
| **P3** | **Four env vars are set in BOTH Flink manifests** (§4.2 table). | The manifests still carry day-run values. `REJECT_CAPTURE_ENABLED="false"` alone would make C5/C6 return zero rows and look like a code bug. |
| **P4** | **Only `newsapi` and `hackernews` are unpaused at T0; `arxiv` is unpaused shortly after, solely so it can be manually triggered (a paused DAG cannot be triggered — it is daily at 07:00 UTC and will not re-fire inside the window). The other four DAGs stay paused throughout.** | Only global_news and HackerNews are in 7D's blast radius. `fred`/`openweather` feed structured_metrics, which has no LLM call and which 7D does not touch. `opensky`/`googletrends` are known-broken (KG-A-5 / KG-A-3), return 0, and mark `failed` in Airflow — i.e. Alertmanager mail during the one hour you want a clean signal. |
| **P5** | **`telegram` and `polymarket` both come up, per the PIPELINE profile.** | telegram is the sprint's control source (§9 criterion 1 — it should stay at 1.00 while newsapi and arxiv fall). polymarket is not needed by 7D but is harmless (structured_metrics only, no LLM path; comments feature-flagged off so `process_social_pulse_message` is never reached — F8/KG-A-4) and Ron wants fresh Polymarket data in `mapping_dict` for the agent session that follows. Its only cost is added checkpoint load in an area that already carries KG-A-9 — if C9 looks odd, this is a variable in the picture. |
| **P6** | **The window is 90 minutes, not 60.** | HN pulses every 20 min and the first pulse is excluded from C2/C3 by design (§8.3 step 12). 60 minutes leaves two usable pulses at best, and only if T0 happens to align with the DAG schedule — in practice, possibly one. 90 minutes yields 4–5 pulses, 3–4 usable. Half an hour of one `e2-standard-8` node is cents; the difference is between an HN dedup figure you can stand behind and one resting on a single cycle. newsapi/arxiv/telegram have no first-cycle exclusion (`document_hash` is unchanged), so all their pulses count. |
| **P7** | **`agent-worker` comes up only AFTER the window closes and the close pack is exported.** | The agent attaches two Firestore listeners on startup and each delivers its full current match set as ADDED immediately (`bringup_profiles.md` §5 trap 2 — on 2026-07-26 that would have been nine unsolicited follow-ups). Running that inside the window burns the shared RPD ceiling (KG-C-1) at exactly the moment 7D is measuring RPD. `agent-worker` is declared `replicas: 0` deliberately, so this is one `kubectl scale` at any moment of Ron's choosing. |
| **P8** | **After the window: DAGs re-paused, `telegram`/`polymarket` back to 0 — but the node pool stays up for the agent session.** No full scale-to-0 until Ron says so. | The Domain-B test that follows needs Domain-A producers off; leaving them running contaminates it. See §8.4.4. |

### §8.1 Pre-deployment reconciliation (before anything is applied)

1. **KG-C-10 — read desired replicas from the cluster, never from the repo.** Four
   workloads (`flink-jobmanager`, `flink-taskmanager`, `telegram`, `polymarket`) sit at
   0 live while their committed manifests declare 1. Record what you find before
   changing anything (`bringup_profiles.md` §3 step 1).
2. **For this deployment the drift resolves itself, and that is fine.** §8.3 applies both
   Flink manifests anyway (image tag + the four env vars), which restores
   `replicas: 1` — which is what the PIPELINE profile wants. `telegram` and `polymarket`
   come up by `kubectl scale` per P5. **Do not treat this as closing KG-C-10:** at
   teardown (§8.4.4) they return to 0 live while the manifests still say 1, so the trap
   is exactly where it was. The gap closes on its own schedule, not here.
3. Confirm the intended `agent-worker` state — `replicas: 0` in both live and manifest,
   deliberate. Do not "fix" it, and do not bring it up yet (P7).
4. **Local Docker must be OFF for the whole window** (§7.1 rule 4) — it shares the same
   OpenAI account and newsapi credits. Building the image (P1) is not a violation:
   building is not running the stack, and it happens before the cluster is up. Tear the
   local stack down first if T10 left anything running.

### §8.2 Queue gate — KG-A-9 (operational, no code) — REWRITTEN 2026-07-27

**The mechanic that makes this section different from what it used to say.**

VERIFIED 2026-07-27 by direct read of `processing/silver_job.py` and
`processing/gold_job.py`: every `KafkaSource` in both jobs is built with
`.set_starting_offsets(KafkaOffsetsInitializer.earliest())`. A `flink run` without a
savepoint starts with no state, so the starting-offsets initializer applies and the job
begins at the topic's **low watermark** — *not* at the consumer group's committed offset.
The project already knew this: `cluster_operations_guide.md` §7 relies on it ("with topics
truncated, 'earliest' is the new low-watermark"). It had simply never been carried into
this plan.

**Consequence: the KG-C-4 / F15 cancel-and-resubmit that §8.3 requires also replays
everything Kafka still retains.** Retention (from `kafka-init-cronjob.yaml`): Bronze **7
days**, Silver/Gold **3 days**, DLQ 30 days. The `dayrun-20260722` Bronze was still inside
the 7-day window on 2026-07-27; the Silver topics from that run had already aged out.

**Left unmanaged, the replay would have cost roughly** (day-run-derived estimates):
~2,164 news rejects re-paying a rescue embedding each (they return *before* `kv_archive`,
so the gate never sees them — gap (4) in T12); ~2,868 low-signal HN stories writing
**duplicate** `filter_rejects` rows into the corpus 7B.5 must calibrate on; and ~730
high-signal HN stories re-paying **full `gold_consensus`**, because D1a changed their
dedup key. Order of 5,000–6,000 calls against a 10,000/day Tier-1 cap (KG-C-1), for a
90-minute verification window. Note what is *not* in that list: already-archived
global_news articles, which the T3 gate skips at zero LLM cost — the replay's expensive
half is HackerNews, not news.

**DECISION (Ron, 2026-07-27) — P2: discard the whole queue. Truncate every Bronze topic
to its end offset before submitting the jobs.**

Reasoning, recorded so it is not re-litigated:

- The global_news half has no value — it is already archived in `knowledge_vault` and
  already exported to `reports/dayrun-20260722/*.csv`. Replaying it produces skips.
- The HN half *did* have value (it is the one day of HN rejects Kafka still held) but it
  cannot be taken without also re-paying consensus on every high-signal story, and it
  would write thousands of duplicate reject rows. D4 also removes the one thing that made
  that day special — HN cosines are not comparable to news cosines, so there is no
  cross-source benefit in it being the same news cycle. See the §0 note.
- **The evidence the window needs does not depend on the replay.** The manual `arxiv`
  trigger (§8.3 step 11) re-fetches papers the vault already holds — 8.4 calls/item in the
  day-run — which is the cleanest gate demonstration available, on live traffic, within
  minutes. And a live replay would only have exercised Silver and Gold; live traffic
  exercises the DAG and the producer too.

**Procedure.**

1. **Measure before truncating.** Per topic, low-watermark (`--time -2`) and end offset
   (`--time -1`) for every `ingest.bronze.*` and `process.silver.*` topic. **The relevant
   number is total retained messages, not consumer-group lag** — under an `earliest`
   restart, lag is the wrong metric and will understate the exposure. Record the figures;
   they go into the C0 baseline file (§8.4.2).
2. **STOP — approval gate. `kafka-delete-records` is irreversible and it is the only
   irreversible action in T11.** Present the step-1 figures to Ron (per topic: retained
   messages, and the total) and **wait for his explicit go before truncating anything.**
   This is not a formality: the numbers may themselves change the decision. Materially
   less than expected — the whole exposure may be small enough that the question is moot.
   Materially more — that is worth knowing before the window, not after. Do not proceed on
   an assumption that P2 pre-authorises the execution; P2 settles the *policy*, this gate
   authorises the *action*, against real numbers.
3. **Truncate.** `kafka-delete-records` per `cluster_operations_guide.md` §7 (build the
   offset JSON from the end offsets measured in step 1; all partitions). After this,
   `earliest` == now.
4. **Verify.** Re-read `--time -2` and confirm it equals `--time -1` per partition.
5. Record the decision and the numbers in the deployment log.

**Never truncate `dead-letter-queue`, under any circumstance.** It is the C9 canary and the
abort checkpoint's signal, its 30-day retention is deliberate, and it is the one topic
whose history is evidence rather than backlog. C0 snapshots its end offset instead
(§8.4.2). If any instruction, here or elsewhere, appears to call for deleting from it,
stop and ask Ron.

Note: T3/T4 materially reduce the underlying KG-A-9 checkpoint-storm risk — fewer blocking
enrichment calls means fewer stalled checkpoint barriers — but the gate stays regardless.

### §8.3 Deployment sequence — REWRITTEN 2026-07-27

**Follow this order literally.** The two changes most likely to be "corrected" back by
someone working from the old version: the image is built **before** the cluster comes up,
and migration 004 comes **after** it (it needs a live Postgres, so it cannot be step 1).

The shape of the sequence is: *bring everything up with the taps closed, prove the
operators started with the right flags on a completely silent system, and only then open
the taps.* That is what makes step 8 possible — and step 8 is the cheapest verification in
the whole window.

**— Cluster still at 0 nodes —**

1. **Build and push `anizai-flink:1.19.1-7d`** (P1). New tag; never overwrite `-7b5i`.
   Confirm it is in Artifact Registry before continuing. Local Docker is used for the
   build only; the local stack stays down (§8.1 step 4).

**— Bring-up, taps closed —**

2. **Apply the PIPELINE profile with everything that produces or processes held at 0.**
   Per `bringup_profiles.md` §3 steps 1–2, using `kubectl scale` against live objects
   while the pool is still at 0: `flink-jobmanager` 0, `flink-taskmanager` 0, `telegram`
   0, `polymarket` 0, `agent-worker` 0. Record the pre-session desired replicas you find.
   Gate: re-read all five and confirm before resizing. Then resize `main-pool` to 1 node
   (§3 step 3). Expect `postgres`, `kafka`, `airflow-*`, `trigger-consumer` and monitoring
   up; expect **no pod at all** for the five held at 0.
   *(This is deliberately stricter than the published PIPELINE profile, which brings
   Flink/telegram/polymarket straight up. Holding them lets steps 3–8 run on a silent
   system, and it puts 100% of telegram's output — the control source — inside the
   measured window.)*
3. **Apply migration 004** to cloud Postgres. Verify `\d filter_rejects` shows
   `canonical_event_id` and that `idx_filter_rejects_cei` exists. Idempotent (T10 proved
   the real `ALTER` path against a pre-migration DB), so a re-apply is safe.
4. **Run the C0 pre-window baseline** (§8.4.2). Read-only, one file, ~2 minutes. It must
   happen here: before the jobs are submitted, while the DB is quiet, and while the
   cluster is up at all — C7 and C9 are meaningless without it, and the `social_vault` /
   `knowledge_vectors` queries in it are the ones §8.4.3 and §8.5 have been carrying
   forward unanswered.
5. **Queue gate — measure, then truncate** (§8.2 procedure). Truncation must precede
   Flink coming up. Convenient side effect: when the JobManager recovers its old job graph
   from HA state in step 7 and briefly runs pre-7D code, there is nothing left for it to
   process.

**— Deploy and submit, still no traffic —**

6. **Update BOTH Flink manifests and apply.** Image tag → `1.19.1-7d`; and the three
   env vars from the §4.2 verified table, in **both** JM and TM, identically:
   `REJECT_CAPTURE_ENABLED="true"`, `RUN_ID="phase7d-verify-20260727"`,
   `ENRICHMENT_DEDUP_GATE_ENABLED="true"`.
   **`LOG_INFO_SAMPLE_RATE` is NOT set — reverted 2026-07-27, see §4.2.** Leave it absent,
   at the pipeline default. Every other value in these manifests, including
   `resources.limits.memory`, must match the `dayrun-20260722` configuration exactly:
   that configuration provably ran nine sources for 24 hours, and the fewer deltas this
   window carries against it, the fewer candidate explanations there are when something
   goes wrong. Applying restores `replicas: 1` on both, which is the intended state
   (§8.1).
7. **Cancel and re-submit both jobs (KG-C-4 / F15).** A pod restart is **not** sufficient:
   HA restores the old compiled job graph and the new image is silently ignored. Cancel
   whatever recovered, then `flink run -d -py .../silver_job.py` and
   `.../gold_job.py`. Verify both reach `RUNNING` — not merely that pods are `Ready` — and
   that neither is in a RESTARTING loop.
8. **GATE — pre-T0 flag verification. CORRECTED 2026-07-27 during execution; the original
   version of this step was factually wrong.**

   > **What was wrong.** This step used to say that both Gold operators log their flag
   > state "at job start, before any message is processed", and made the six log lines the
   > pre-T0 gate. The *code reading* was right — the lines are emitted from `open()`. The
   > *assumption about when `open()` runs* was not. Under PyFlink/Beam the Python user
   > function's `open()` is invoked lazily, when the first Beam bundle is processed — not
   > at job submission. On a deliberately silent system (§8.3 steps 2–5 truncate Bronze and
   > leave Silver empty) **no bundle ever starts, so `open()` never runs and the six lines
   > cannot exist pre-T0.** Confirmed empirically 2026-07-27: jobs RUNNING, restarts 0,
   > zero bundles processed, zero `[gold/flink]` lines. Record as a T12 discrepancy.

   **Verify the same facts directly instead — all of it is available with zero data flow
   and zero spend.** The log lines were only ever a proxy for these:

   a. **Raw env, in the running TaskManager container** — all four §4.2 vars present with
      the intended values, and present in the Python worker's Beam environment payload.
   b. **Parsed settings, in-container** — the gap (a) leaves open is that the log line
      echoes `config.settings`, not the raw var, so a parsing or naming mismatch would
      still slip through. Close it: exec into the TaskManager and import the settings
      module directly (`PYTHONPATH=/opt/flink/usrlib` is set at image level), printing
      `ENRICHMENT_DEDUP_GATE_ENABLED`, `REJECT_CAPTURE_ENABLED`, `RUN_ID` and
      `GOLD_SEMANTIC_RESCUE_THRESHOLD`. This verifies exactly the values the six lines
      would have printed.
   c. **Sniper vector loads, not merely exists** — `numpy.load` it in-container and print
      the shape. `_load_sniper_reference_vector()` is, as of 7D, called by **both** Gold
      operators and hard-fails at `open()`, so a bad `.npy` presents as "the Gold job dies
      on first data", not as degraded filtering. A 1536-dim float32 vector is 6,272 bytes
      on disk (6,144 + a 128-byte npy header); a size that matches is a good sign, a
      successful `load` with `shape=(1536,)` is proof.
   d. Jobs `RUNNING`, restarts 0, no RESTARTING loop, no ERROR/traceback, DLQ flat.

   **The six lines still get verified — at the T0+5 abort checkpoint (step 10), which is
   where the first bundles run.** They are not skipped; they move. Reference table for
   what they must read when they do appear:

   | Emitted by | Line | Must read |
   |---|---|---|
   | `GlobalNewsGoldFunction` | `[gold/flink] reject capture … (run_id=…)` | `ENABLED`, `run_id='phase7d-verify-20260727'` |
   | `GlobalNewsGoldFunction` | `[gold/flink] enrichment dedup gate … (KG-A-7)` | `ENABLED` |
   | `GlobalNewsGoldFunction` | `[gold/flink] semantic-rescue threshold=…` | `0.35` |
   | `PolymarketGoldSocialFunction` | `[gold/flink] social enrichment dedup gate … (KG-A-7)` | `ENABLED` |
   | `PolymarketGoldSocialFunction` | `[gold/flink] social reject capture … (run_id=…)` | `ENABLED`, same `run_id` |
   | both | `[gold/flink] Sniper reference vector loaded — shape=…` | present, no `FileNotFoundError` |

   If any line is missing entirely once bundles have run, suspect `LOG_INFO_SAMPLE_RATE`
   — these are INFO. If a line reads `disabled`, the manifest did not take: fix it and
   restart the pod. **Note the asymmetry the lazy-`open()` correction introduces:** the
   two `GlobalNewsGoldFunction` operators wake on the first *global_news* bundle and the
   two `PolymarketGoldSocialFunction` ones on the first *social* bundle, so the social
   lines will not appear until HackerNews data actually flows — their absence at T0+5 is
   not a failure if no HN pulse has landed yet. Expect each line **more than once**:
   `env.set_parallelism(2)` means two subtasks per operator, each running its own
   `open()`. Duplicates are normal; a *short* count is the signal that an operator did not
   initialise.

**— Open the taps: this is T0 —**

9. **T0 — start live traffic, all at once.** Unpause `newsapi` and `hackernews` only
   (P4); scale `telegram` and `polymarket` to 1 (P5); then **manually trigger both
   `newsapi` and `hackernews`**. The manual trigger matters: with `catchup=False` an
   unpaused DAG simply waits for its next schedule boundary, so without it the window
   opens with up to 20 dead minutes. Bonus — a manual run followed by the scheduled run
   ~20 minutes later gives newsapi a **guaranteed duplicate pair**, which is exactly what
   C2/C3 need to see.
   **`:t_start` = this moment.** Record it in UTC; every §8.4.2 query is bounded on it.
10. **ABORT CHECKPOINT at T0+5 minutes. REVISED 2026-07-27 — now evidence-based, not
    log-based.** With `LOG_INFO_SAMPLE_RATE` reverted to the default (§4.2), INFO passes
    at ~1% and the six startup lines are no longer reliable evidence. That is acceptable:
    the flags were verified far more directly at step 8b by importing `config.settings`
    in-container. So check the **outputs**, which are SQL and unaffected by sampling:
    - both jobs still `RUNNING`, restart count unchanged, TaskManager not restarting;
    - no `ERROR` / traceback in the TaskManager log — **ERROR and WARNING pass at 100%
      regardless of sampling**, so this check is untouched by the revert;
    - DLQ end offset has not moved from the C0 baseline;
    - **rows are landing:** at least one new `knowledge_vault` row, and at least one new
      `llm_cost_events` row, since `:t_start`. This is the proof the wiring executes at
      all — the thing T10 could not verify anywhere.
    - **the gate is firing:** a `[gold/dedup]` line if sampling happens to surface one,
      OR — better — `gold_enrich` call count below the count of items delivered for a
      source with known duplicates. Do not treat the absence of a sampled log line as
      evidence of anything.
    - once HN data has landed: at least one `filter_rejects` row with
      `source_name='hackernews'` and a populated `canonical_event_id`. If no HN pulse has
      arrived yet, hold this sub-check open rather than passing or failing it.

    If anything is wrong: set `ENRICHMENT_DEDUP_GATE_ENABLED=false` (or cancel the jobs),
    and **T0 is void** — re-declare it after the fix. A void T0 costs nothing; discovering
    the same fault at minute 85 costs the window and leaves rows to clean up.
11. **Trigger `arxiv` manually, immediately after step 10 passes — not at the end.**
    Unpause it first (a paused DAG cannot be triggered); it is daily at 07:00 UTC and will
    not re-fire inside the window, so it can stay unpaused until teardown. This is the
    single highest-yield item in the window: in the day-run arxiv ran **once** and
    produced 910 enrichment calls over 108 distinct papers — 8.4 calls/item, duplication
    internal to a single run. Re-fetching against a vault that already holds those papers
    should show near-total skip within minutes. Deferring it to "before closing the
    window" (as the old §8.4 step 2 invited) risks the papers not clearing Silver→Gold
    before teardown.
12. **Expect an elevated first HN pulse — this is not the gate failing.** Per §3/D1 and
    the T1 finding, HackerNews previously keyed on a comment-derived hash, so on the first
    pulse every in-flight story presents a never-before-seen `story_id`-derived key and is
    enriched one final time. With the queue truncated this is bounded to roughly one
    front page (~50 stories), not the ~730 a replay would have produced. Record it in the
    deployment log, and **exclude the first HN pulse from C2/C3 analytically** — do not
    move `:t_start` to compensate. newsapi/arxiv/telegram have no such exclusion.
13. Fold **KG-C-5** (secret rename `NEWSAI_API_KEY` → `THE_NEWS_API_KEY`) into the
    `anizai-airflow` rebuild **if and only if** that rebuild happens in the same window.
    It is independent of 7D and simply needs to ride a rebuild. Do not initiate an
    `anizai-airflow` rebuild for its sake during this window.

### §8.4 Verification window, then tear down — REVISED 2026-07-27

This deployment is a **verification window, not the start of the multi-day run.**
Ron's intent: bring the cluster up, confirm the gating works on real traffic, take
the pipeline back down. The multi-day run is a separate later event, likely preceded by a
database reset (§8.4.3).

1. **Run the window for 90 minutes from `:t_start`** (P6). The old "roughly an hour"
   figure came from "let a source pulse at least twice", which does not survive the
   first-HN-pulse exclusion in §8.3 step 12: at 20-minute pulses, 60 minutes leaves two
   usable HN cycles at best and, if T0 lands badly against the schedule, possibly one.
   90 minutes yields 4–5 pulses and 3–4 usable ones. newsapi and telegram have no
   exclusion, so every one of their pulses counts.
2. **arxiv is handled at §8.3 step 11, early — not "before closing the window".** Moving
   it to the front is deliberate: it is the strongest single piece of evidence available
   (8.4 calls/item in the day-run) and it needs time to clear Silver→Gold before teardown.
3. **Export the §8.4.2 close pack before touching anything.** Pod logs die with the pods
   and cloud Postgres is unreachable once the pool is at 0 — nothing in the close pack is
   recoverable afterwards. Ron confirms the files open before anything is scaled.
4. **Then §8.4.4 — partial teardown, not a full scale-to-0.**

### §8.4.2 Window close pack — Claude Code runs this, Ron reads files

**Ron does not read terminal scrollback and does not grep logs by hand.** When Ron
says the hour is up, Claude Code runs the commands below, writes every result to a
file under `docs/A_pipeline/reports/phase7d-verify-<YYYYMMDD>/`, and presents a
short summary table in chat with a verdict per check. Same principle as the
day-run's Stage 4: **the deliverable is files plus a verdict, not output on screen.**

`:t_start` = the UTC timestamp at which the verification window was declared open
(recorded at **§8.3 step 9** — the moment the DAGs were unpaused and the producers scaled
up). Every query below is bounded on it. **Bound on the timestamp, never on `RUN_ID`
alone:** `RUN_ID` is a static env var and keeps stamping rows for as long as the pod
lives, so it identifies the *deployment*, not the *window*.

**Order is fixed. Logs first — they are the only artifact that cannot be recovered
after teardown.**

#### C0 — pre-window baseline (NEW 2026-07-27). Runs at §8.3 step 4, BEFORE the jobs are submitted.

Everything else in this pack measures a *change*, and a change with no starting point is
just a number. C0 is read-only, takes about two minutes, and produces one file:
`baseline_pre_window.csv`.

| Capture | Why |
|---|---|
| `dead-letter-queue` end offset | C9's baseline. The DLQ retains 30 days, so an absolute in-window count is meaningless without it. This is also the abort checkpoint's canary (§8.3 step 10). |
| Per-topic low-watermark + end offset for every `ingest.bronze.*` and `process.silver.*` | The §8.2 measurement, kept as evidence of what was discarded and how much. |
| `social_vault`: rows vs `count(DISTINCT platform_data->>'story_id')` where `source_name='hackernews'`, bounded to the day-run window `[2026-07-22T09:25:26Z, 2026-07-23T09:25:26Z]`, **AND `gold_consensus` calls in `llm_cost_events` for hackernews over the same window, against the same distinct-story denominator** | **The measurement §3/D1 was decided without — but it takes TWO ratios, not one (corrected 2026-07-27).** `social_vault` rows ÷ distinct stories measures **archival** duplication, i.e. distinct comment-sets per story. It is a **lower bound**, not the answer: pre-7D there was **no dedup gate on the social path at all**, so enrichment ran on **every delivery** whether or not archival was skipped as a same-hash duplicate. The figure D1 turns on is **enrichment** duplication = `gold_consensus` calls ÷ distinct `story_id`. Report both and label them. High enrichment ratio ⇒ option (a) retro-validated. Near 1.0 ⇒ D1's saving was smaller than assumed while D1a's archival cost (only the first comment-set per story is retained) was still paid — a real argument to revisit option (c) sooner. **Note this is NOT the invalid `dayrun_analysis.md` §4.1 comparison:** that one put Gold spend over an *archive row count*; this one puts it over *distinct stories*, which is the correct denominator. Feeds T12 item (8). |
| `social_vectors`: row count in the same day-run window | Closes `dayrun_analysis.md` §4.1, which could not export this table. |
| `knowledge_vectors` total rows vs `knowledge_vault` total rows | **CORRECTED 2026-07-27 (the original spec here said "total vs `count(DISTINCT signal_id)`", which is tautological — `signal_id` is the PRIMARY KEY per F6, so the two are equal by construction and measure nothing).** The authoritative denominator for *distinct articles* is `knowledge_vault`, which carries a UNIQUE B-tree on `document_hash` (F2) and therefore holds exactly one row per distinct article. `knowledge_vectors` receives only the global_news Gold path (`kv_insert`), so in a world without KG-A-8 the two counts would be ~1:1; the excess is the accumulated duplicate-vector debt. **This is the evidence base for the §8.4.3 reset decision, which until now rested on an assumption nobody had measured** — the last recorded figure (9,202) predates the day-run entirely. If `canonical_event_id` is also reported as a denominator, state explicitly whether it is per-delivery-instance or per-article, otherwise the ratio cannot be interpreted. Feeds T12 item (9). |

**Nothing in C0 writes, alters, or deletes anything.** §8.4.3's prohibition applies in full.

| # | Check | Evidence to capture | Passes when |
|---|---|---|---|
| **C1** | Gate fires at all | Flink TaskManager logs, filtered for the T3/T4 skip-duplicate INFO line — **grep token `[gold/dedup]`** (T3 emits `[gold/dedup] skip enrichment …`; T4 emits `[gold/dedup] skip HN enrichment …`). Capture the total count and ~10 sample lines. → `gate_skips.log`. **(b)-verified 2026-07-26:** both gates are Gold-layer operators in the TaskManager, and the Silver job publishes every HN story to Kafka unconditionally (no Silver-layer dedup — `process_hackernews_message` has no `exists_by_content_hash`), so ALL skips, HN included, appear **here** in the TaskManager logs, never at Silver | Count > 0, with samples from more than one source. **The count is evidence the mechanism fires — it is NOT the magnitude.** Magnitude comes from C2/C3, which are SQL and unaffected by log sampling. If `LOG_INFO_SAMPLE_RATE=1.0` was not set (§8.3 step 6), the count is ~1% of reality — say so explicitly in the summary rather than reporting it as a figure. **AMENDED 2026-07-27 — C1 no longer carries the burden of proving the flags were on.** That is settled at §8.3 step 8 by the six operator startup lines, on a silent system, before T0. Capture those six lines into `gate_skips.log` as its header. C1 then proves only that the mechanism *fires on real traffic*. |
| **C2** | Gate effectiveness (**the headline**) | Per source, in-window: `gold_enrich` / `gold_consensus` call count vs rows archived to `knowledge_vault` / `social_vectors`. → `gate_effectiveness.csv` | Ratio approaches 1:1. Compare against the day-run baseline: newsapi 1,360→644, arxiv 910→108, telegram 327→327 |
| **C3** | Wasted enrichment is gone | Row-level: `gold_enrich` events in-window whose `trace_id` has no matching `knowledge_vault.canonical_event_id`. → `wasted_enrichment.csv` | Approaches 0. This is the day-run's `enriched_then_deduped` metric, re-run |
| **C4** | arxiv verified | Same as C2/C3, filtered to arxiv, after the manual DAG trigger (**§8.3 step 11** — moved early) | arxiv appears in the data at all — if absent, the trigger did not fire |
| **C5** | HackerNews rejects captured | `filter_rejects` rows in-window with `source_name='hackernews'`: count, **`count(DISTINCT original_url)` alongside it (NEW 2026-07-27)**, min/max `rescue_cosine`, count of NULL `canonical_event_id`. → `hn_rejects.csv` | Count > 0, cosines in range, **zero NULL instance keys**. **Report BOTH counts and state the ratio.** The T6 capture branch sits *before* the T4 gate (deliberately — the named invariance assertion, §6), so a low-signal HN story that stays on the front page is re-captured **every 20-minute pulse**. Over 90 minutes expect roughly 4–5 rows per distinct story. Reading the raw count as a story count overstates the daily corpus rate by that factor — a real risk, since §8.5's PVC estimate and §0's "~2,868/day" are both **instance** figures. |
| **C6** | Instance key populated everywhere | `filter_rejects` in-window: count of NULL `canonical_event_id`, all sources. → folded into `hn_rejects.csv` | 0 |
| **C7** | No duplicate vectors | **CORRECTED 2026-07-27 — the original "rows in-window vs `count(DISTINCT signal_id)`" is tautological (`signal_id` is the PK, F6) and proves nothing.** Measure instead: `knowledge_vectors` rows added in-window vs `knowledge_vault` rows added in-window (the latter is one row per distinct `document_hash`, F2). Report against the C0 whole-table baseline so the in-window result is separable from the pre-existing debt | ~1:1 in-window — one vector per newly archived article. The C0 whole-table ratio is expected to be far worse: that is historical debt T5 does not clear retroactively (T12), not a C7 failure. Note one legitimate in-window excess: a **pre-existing** article re-delivered for the first time post-deploy carries a `uuid4` `signal_id` that the new UUID5 does not collide with, so it writes one extra vector once (T12, KG-A-8 qualification) |
| **C8** | Cost instrumentation still healthy | `llm_cost_events` in-window: rows per `site`; count of empty `trace_id`. → `cost_health.csv` | Rows present at every expected site; 0 empty trace_ids (KG-A-13 regression guard) |
| **C9** | No regression | **DLQ end offset now vs the C0 baseline (NEW 2026-07-27 — a delta, not an absolute; the topic retains 30 days, so a raw count is meaningless)**; both Flink jobs still `RUNNING` with restart count unchanged since submission; Kafka consumer lag not climbing. → `health.txt` | DLQ delta at or near zero, no restart loop. Note two legitimate DLQ sources if the delta is non-zero: an embedding API error on the global_news low-signal path routes to DLQ by design (B7), and so does an unknown `source_name`. Neither is a 7D regression — identify which before reporting a failure |

**Interpretation guards — state these in the summary so the numbers are not misread:**

- **Exclude the first HN pulse from C2/C3** (§8.3 step 12). On the social path the first
  pulse legitimately re-enriches everything once. Exclude it **analytically** — do not
  move `:t_start`. newsapi / arxiv / telegram have **no** such exclusion.
- **C5's two counts are not interchangeable.** Rows are instances; distinct URLs are
  stories. State the ratio explicitly.
- **C9 is a delta against C0, not an absolute.**
- **C7's in-window equality and C0's whole-table inequality are both expected.**
- A window-edge lag of a few articles between enrichment and archival is normal and
  is not gate failure.
- If C2 lands short of 1:1 but well below the day-run baseline, that is a **result to
  report, not a failure to hide.** Report the actual figure and say what it implies.
- `arxiv` absent from C4 means the manual trigger did not fire — that is a process
  miss, not a code result. Say so plainly.
- **Small-n honesty.** This is a 90-minute window, not a day-run. Report counts alongside
  every ratio and do not present a ratio drawn from a handful of items as a rate.

**Only after the pack is written to files and Ron has confirmed the files open:**
proceed to §8.4.4.

### §8.4.4 Partial teardown — NEW 2026-07-27 (P8)

**This is not a scale-to-0.** Ron continues into an agent session on the same node after
the window closes. Order matters.

1. **Close the pipeline taps first.** Re-pause all three unpaused DAGs (`newsapi`,
   `hackernews`, `arxiv`); scale `telegram` and `polymarket` back to 0. This restores the
   state `cloud_state.md` §6 and `cluster_operations_guide.md` §12 describe — those files
   stay as written; the override was temporary and is now reverted (T12 item 6).
   **The Domain-B work that follows requires Domain-A producers off**; leaving any of them
   running contaminates it.
2. **Optionally cancel the Flink jobs** for a clean checkpoint and clean HA state. Not
   required — HA preserves the graphs regardless — but it produces tidier diagnostics.
   With `telegram`/`polymarket` at 0 and the DAGs paused, nothing is flowing anyway.
3. **`LOG_INFO_SAMPLE_RATE` — two separate decisions, do not conflate them.** On the Flink
   manifests it reverts to the default before any multi-day run (§8.5). On the
   **agent** Deployment it must be set to `1.0` *for the agent session*, which is a
   different manifest and a different gap (KG-B-4); it is read at module import, so it
   needs a fresh pod.
4. **Only now scale `agent-worker` to 1** (P7), and run the AGENTS pre-flight gate from
   `bringup_profiles.md` §3 step 4 **before** treating the session as open: count the
   pending `forecastQueries` and the collection-group `messages` matches, and decide
   deliberately whether to clear them (flip status — do not delete) or accept that the
   session's first N runs are not yours. Also glance at `forecastQueries` stuck at
   `claimed` — nothing scans that status (KG-B-21).
5. **There is no automatic scale-down.** The two recurring Cloud Scheduler jobs are PAUSED
   and the day-run's one-shot auto-close job fired on 2026-07-23 and was deleted. The node
   stays up, and billed, until someone resizes the pool. If the agent session is
   open-ended, consider a one-shot auto-close job as the day-run used — otherwise put the
   teardown on Ron's own list explicitly.
6. **State the carry-over in the deployment log** (`bringup_profiles.md` §4 step 5): which
   workloads are held at 0, which DAGs are paused, and that both Flink manifests now
   declare the `-7d` image with the four env vars — so whoever brings this up next is not
   surprised.

> *Numbering note: §8.4.4 appears above §8.4.3 in this file. That is intentional — §8.4.3
> is a future contingency that is never executed in this sprint, so it is kept out of the
> execution path. Execution order is §8.4 → §8.4.2 → §8.4.4.*

### §8.4.3 FUTURE CONTINGENCY — not part of this sprint, not to be executed

> **STOP. This subsection is a forward-looking note for Ron, not a task.**
> Claude Code must **never** truncate, drop, delete from, or reset any table, in
> any environment, local or cloud, at any point in this sprint. No `TRUNCATE`, no
> `DROP TABLE`, no `DELETE FROM`, no re-running `init.sql` against a populated
> database. The only permitted schema operations are the idempotent additive
> statements in §4.1 (T2). If any task appears to require removing data, **stop
> and ask Ron.**
>
> The verification window in §8.4 is: bring up, verify, tear down. Nothing else.

Ron is considering wiping the databases at some **later** point, before the
multi-day run, on the grounds that they carry accumulated duplicates and noise.
That is a separate decision on a separate day. It is recorded here only so the
sequencing constraints below are not lost when the time comes.

**The reasoning is sound** — the
duplicate vectors this sprint stops creating are still sitting in
`knowledge_vectors` from before, and no code change removes them retroactively.
But two things must happen first **whenever that day arrives**, and one is
irreversible if missed.

- ~~**Run the `social_vectors` count query BEFORE any reset.**~~ **SATISFIED 2026-07-27 —
  moved forward into §8.4.2 C0 and executed as part of this deployment.** It was the only
  outstanding measurement that could still answer whether HackerNews suffered duplicate
  enrichment during `dayrun-20260722` (`dayrun_analysis.md §4.1`), and a reset would have
  destroyed the ability to answer it **permanently**. Taking it at C0 rather than leaving
  it as a reset precondition removes the single irreversible risk from this list.
- **The reset decision now has evidence.** C0 also captures `knowledge_vectors` total rows
  vs `count(DISTINCT signal_id)` — the accumulated duplicate-vector debt this subsection
  assumes exists but which had never been measured (the last recorded figure, 9,202,
  predates the day-run). Read that number before deciding: if the debt is small, a reset
  may not be worth its costs at all.
- **The day-run corpus is already safe outside the database.** Verified 2026-07-26:
  `reports/dayrun-20260722/` holds `filter_rejects_full.csv` (16.2 MB, includes
  full article text), `knowledge_vault_full.csv` (7.5 MB),
  `llm_cost_events_full.csv` (4.9 MB), and `social_vault_full.csv` (2.6 MB). Phase
  7B.5's calibration corpus therefore survives a reset — but 7B.5's plan is written
  against SQL queries, so it will need to read from CSV instead. **Flag this in the
  7B.5 plan before resetting, not after.**
- Neither `knowledge_vectors` nor `social_vectors` was ever exported. Those are
  exactly the rows a reset is meant to clear, and they regenerate from ingestion —
  no loss.
- **Expect a full re-enrichment cycle after a reset.** An empty vault means every
  incoming article is genuinely new, so the dedup gate correctly lets everything
  through once. Budget RPD for it, and do not read the first day's high call count
  as the gate failing.
- A reset is also the natural moment to drop the dormant `reddit` / `predictit`
  enum values from the CHECK constraints (KG-A-6) — cosmetic, but free at that
  point.

### §8.5 Before any multi-day run (separate from this deployment)

Not this sprint's tasks, but the sprint exists to enable this run — listed so the
sequencing is not lost:

- GKE maintenance window configured (KG-C-2) — an unattended multi-day run with
  auto-upgrade unwindowed will be interrupted.
- Postgres backup: verify the CronJob schedule falls inside the up-window; run a
  manual backup immediately before teardown rather than trusting the schedule (KG-C-9).
- **Postgres PVC headroom for reject capture (Phase 7D T6/T7).** With
  `REJECT_CAPTURE_ENABLED=true` the HN reject body stores the joined top-comment
  texts (up to 10). Day-run reference: 2,164 news rejects = **16.24 MB** of
  `filter_rejects`; HN adds ~2,868 rejects/day at comparable-or-larger per-row size,
  so enabling capture roughly **doubles** `filter_rejects` growth. Trivial for the
  90-minute verification window (§8.4); material over a multi-day run — **confirm the
  Postgres PVC has headroom before starting one with capture on.** *(2026-07-27: the
  ~2,868 figure is an **instance** count, not a story count — a low-signal HN story is
  re-captured every 20-minute pulse for as long as it ranks, because the T6 capture is
  ordered before the T4 gate. The estimate is therefore already on the right basis; do not
  "correct" it upward a second time. C5's distinct count gives the true story rate.)*
- **Revert `LOG_INFO_SAMPLE_RATE` on BOTH Flink manifests to the default before a
  multi-day run** (2026-07-27). It is set to `1.0` for the verification window (§8.3
  step 6); at day-scale volume unsampled INFO is exactly the flood the sampling exists to
  prevent. This is a *different* manifest from the agent's `LOG_INFO_SAMPLE_RATE=1.0`
  below — do not conflate the two.
- **Unpause the producer DAGs deliberately** — they are re-paused at §8.4.4 for the
  Domain-B work, so a multi-day run starts from "all paused" and must unpause what it
  actually wants.
- **NEVER set `LOG_INFO_SAMPLE_RATE=1.0` on the Flink workloads. RESOLVED 2026-07-27 by
  a clean natural experiment — this bullet replaces an earlier one that wrongly blamed
  container memory.** Sequence: attempt 1 OOMKilled the TaskManager (exit 137) 38 s after
  data arrived at the committed `2560Mi`; the limit was raised to `6Gi` and attempt 2
  OOMKilled at 34 s — 2.4× the memory moved time-to-kill by 4 seconds, which is the
  signature of consumption growing with **throughput**, not of a fixed footprint meeting a
  wall. Attempt 3 reverted the limit to `2560Mi` **and** removed `LOG_INFO_SAMPLE_RATE`,
  processed the *same* accumulated queue, and ran clean with restarts 0. Same burst, same
  memory, one variable different, opposite outcome. JVM heap during attempt 3 was a flat
  bounded sawtooth (~170–536 MB) while the container had died in the earlier attempts —
  so the growth was entirely **Python-side**, never the JVM.
  **Mechanism: NOT established — do not repeat the first explanation.** The initial write-up
  here said "~100× more log records over the buffered Beam logging channel." That
  explanation is now in doubt: during the successful attempt, with the variable absent and
  the code default at `0.01`, INFO was observed to pass **unsampled** on the Flink UDF path
  — all 14 operator startup lines appeared, and the 56 `[gold/dedup]` skips reconcile
  exactly against DB-derived counts (62 enriched + 46 news skips = 108 high-signal newsapi
  articles, alongside 119 low-signal rejects, summing to the ~227-message backlog). At a
  true 1% sample those lines would have been ~0. **If INFO is unsampled either way, then
  setting the variable to `1.0` cannot have multiplied record volume, and the mechanism is
  something else** — plausibly that setting it changes which handlers `setup_logging()`
  installs, producing duplicate propagation rather than a higher sample rate. Unresolved.
  Record as an open question; do not restate the 100× story as fact.
  **Related documentation defect:** `guides/bringup_profiles.md` §5 trap 3 and
  `guides/cluster_operations_guide.md` §11 both assert that INFO is 1%-sampled for
  "anything using `setup_logging()`, which includes the agent and the Flink jobs." The
  agent half is evidenced (a ~20-hour session produced 7 entries). The **Flink** half
  appears false, and sessions have been planned around it — "do not plan a measurement
  session around grepping INFO" may be wrong for the pipeline. Verify before relying on
  either statement again.
- **Consequences that DO stand, independent of the mechanism:** (i) container memory is not the lever — raising it chases an unbounded
  quantity, as `6Gi` proved; (ii) the `2560Mi` / 4-slot / `process.size=2048m`
  configuration is **sufficient** — verified independently by Prometheus over the whole
  `dayrun-20260722` window: TaskManager `up` 100%, zero job restarts, 2 failed checkpoints
  in 24 hours across all nine sources; (iii) this was **not** KG-A-9 and must not be
  recorded as such — an earlier session hypothesis to that effect was disproved by the
  day-run metrics and is withdrawn. If a future session needs full INFO from Flink, bound
  the Python-side memory first (`python.fn-execution.*` / managed memory) — that is real
  PyFlink tuning work, not a manifest edit.
- Decide Cloud Scheduler daily up/down vs continuous. Continuous avoids the daily
  backup-miss and daily Flink restart; it costs more.
- `LOG_INFO_SAMPLE_RATE=1.0` on the agent Deployment before it comes up (KG-B-4) —
  read at process start, so it needs a fresh pod.
- Agent alert rules (failure count, cost delta, node p95, **and a silence alert** —
  no sessions processed for N hours while the pod is Ready).
- ~~`social_vectors` count query while the cluster is up~~ — **DISCHARGED 2026-07-27: this
  is now §8.4.2 C0**, run at §8.3 step 4 of this deployment rather than deferred again. It
  closes `dayrun_analysis.md` §4.1 and feeds T12 item (8). Carried here twice without
  being done; it is no longer on this list.
- Expect KG-A-3 and KG-A-5 to return 0 throughout (F16). Not a regression.

---

## §9 — Acceptance criteria

1. **RPD.** Over a bounded post-deploy window — **excluding the first HN pulse**
   (§8.3 step 12; the exclusion applies to the social path only) —
   `gold_enrich` calls per **distinct** item approach 1.0 for newsapi
   and arxiv, and `gold_consensus` calls per distinct `story_id` approach 1.0 for
   hackernews. telegram stays at 1.0 (it already is — it is the control).
2. **Invariance.** The §4.3 invariant holds in the local E2E: same archived set,
   same reject set, same filter decisions, fewer LLM calls.
3. **Vectors.** A deliberate re-delivery of an identical global_news record
   produces exactly one `knowledge_vectors` row.
4. **Reject coverage.** `filter_rejects` contains `hackernews` rows with populated
   `rescue_cosine` and `canonical_event_id`. The hackernews reject cell in any
   future funnel is a real number, never `0`-as-"not captured".
5. **Traceability.** A reject row can be joined back to its instance.
6. **No regression.** Test suite green; no new DLQ traffic; drop/rescue rates for
   global_news unchanged.

---

## §10 — Skills

- `sprint-kickoff` — open the sprint, establish context, produce the implementation
  plan for Ron's approval before coding.
- `filter-analysis` — **not used here.** It drives Phase 7B.5, which consumes the
  corpus this sprint makes capturable.

---

## §11 — Explicitly out of scope

| Item | Why |
|---|---|
| KG-A-9 real fix (async enrichment / checkpoint tuning) | Genuine architectural change. Handled operationally here (§8.2); the real fix should sit on data the multi-day run produces. |
| KG-A-1 (`find_similar_and_link` wiring) | Its stated entry condition is live performance data — which the multi-day run provides. After, not before. |
| KG-A-3 / KG-A-5 (Google Trends, OpenSky) | Product/network decisions, not pipeline efficiency. |
| KG-A-4 (Polymarket comments) | Flagged off. Do not touch; if ever re-enabled, the `is_high_signal` guard must be added first (F8). |
| KG-A-2 / A-6 / A-10 / A-11 (hygiene) | No behavioural impact. A-11 specifically must not be touched before a measurement run. |
| newsapi query-spread investigation | Real open question from `dayrun_analysis.md §3`, needs its own instrumentation and scope. |
| Phase 7B.5 threshold work | This sprint makes the corpus capturable. It decides nothing about thresholds. |
| Any database reset, truncation, or data deletion | **Explicitly forbidden this sprint** (§8.4.3). The only schema work is the additive migration in §4.1. A reset is a separate future decision of Ron's, with its own prerequisites. |
| Anything in `agent/` | Domain B. |

---

> Source analysis: `reports/dayrun-20260722/dayrun_analysis.md` (2026-07-25).
> Gap references: `pipeline_sprints.md §4` — KG-A-7, KG-A-8, KG-A-12, KG-A-13,
> plus the proposed `filter_rejects` instance-key gap.
> Downstream: Phase 7B.5 (filter calibration) consumes the corpus this sprint
> makes capturable — including HackerNews for the first time.
