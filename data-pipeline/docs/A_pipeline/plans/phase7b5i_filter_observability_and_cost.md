# phase7b5i_filter_observability_and_cost.md
> Domain: A — Pipeline
> Type: Sprint Plan
> Last updated: 2026-07-02
> TL;DR: Instrumentation prerequisite for Phase 7B.5 and for the pipeline cost day-run.
> Adds (1) reject retention for articles dropped by the two-stage filter, (2) a
> `rescue_cosine` column for rescued survivors, and (3) per-call LLM/embedding cost
> tracking with per-day / per-run summary views — then deploys it to the cloud and runs
> the system for one full day. Self-contained: all decisions are closed and recorded
> inline; every code anchor was verified against `main` on 2026-06-30 → 2026-07-02.

## Navigation
- §0 — Why this sprint exists — the 7B.5 data gap + the cost-measurement goal
- §1 — Closed decisions & verified code facts — the frame Claude Code must not re-litigate
- §2 — Schemas & contracts — DDL, env flags, site tags, cost-module deltas
- §3 — Task table — T1–T9 with `[ ]` checkboxes, in implementation order
- §4 — Gates & test-quality directives — how T1–T6 map to the four-gate model
- §5 — Execution notes for Claude Code — stop point, Docker, sequencing
- §6 — Skills

---

## §0 — Why this sprint exists

Two goals share one one-day cloud run:

1. **Make Phase 7B.5 executable.** 7B.5 (`plans/phase7b5_filter_calibration.md`) must
   empirically calibrate `DEFAULT_THRESHOLD` (0.15) and `GOLD_SEMANTIC_RESCUE_THRESHOLD`
   (0.35) and confirm the 10 A1 keyword removals. Code investigation (2026-06-30) proved
   the data it needs **does not exist**:
   - Articles failing BOTH filter stages are dropped entirely — `return` with an INFO log
     only, no row anywhere (`gold_job.py`, `GlobalNewsGoldFunction.process_element`).
     ⇒ T7B.1's FN/TN classification and T7B.9's reject corpus are unworkable today.
   - The rescue cosine score is logged but never persisted, even for rescued survivors.
     ⇒ T7B.9's threshold sweep has no scores to move against.
   There is no post-7A pre-gate window (7A and the 7B early-drop gate landed the same
   day, 2026-05-09), so backfill is impossible — **forward instrumentation + a fresh
   one-day collection run is the only path** (Option 2, decided 2026-06-30).

2. **Measure what a pipeline-day costs.** The pipeline makes OpenAI calls at 7 runtime
   sites (see §1) and none routes through any cost utility — the spend is silent. The
   day-run's deliverable is a real number: USD per day, decomposable by source and
   usage type.

The two goals are coupled: the rescue stage embeds every sniper-rejected article, so
part of the daily spend is on items ultimately dropped. Reject retention + cost events
together answer "what does a day cost, and is the filter worth it."

**Naming note:** this is *filter*-calibration instrumentation (Domain A / Phase 7B.5).
It is unrelated to Domain D "Calibration" (Phase 10, agent forecast calibration /
Brier). Do not mix the two.

---

## §1 — Closed decisions & verified code facts

All five open decisions were closed with Ron (2026-07-01/02). Claude Code implements
them as written — they are not open questions.

| # | Decision | Resolution |
|---|---|---|
| D1 | Cost-utility mechanism | **Copy, don't import.** Copy `agent/utils/llm_cost.py` into the pipeline as `utils/llm_cost.py` (pipeline-root utils). No `processing/` → `agent/` import ever — that would invert the A→B dependency direction and force the Flink image to carry `agent/`. Copy-not-import is the established project pattern; number-drift between the copies is guarded by reconciliation gate KG-PHASE-9.5-9. |
| D2 | Cost sink | **Postgres event table** `llm_cost_events` (one row per API call) + two derived `ROLLUP` views (per-day, per-run). The structured `llm_usage …` log line is retained as the per-call audit trail. Rationale: Flink has no per-request shared state to accumulate into (unlike the agent's LangGraph `state`), so per-event rows + SQL aggregation replace an accumulator. Grafana panel via the existing postgres-exporter is optional / non-blocking (T7). |
| D3 | Reject retention scope | **Flag-gated:** `REJECT_CAPTURE_ENABLED` env var, default `false`, read in `config/settings.py` (same pattern as `GOLD_SEMANTIC_RESCUE_THRESHOLD`). On for the day-run, off after. Cost instrumentation, by contrast, is **permanent** (always on). |
| D4 | Day-run target | **The regular cloud environment**, with logical isolation only: survivors are identified by `ingested_at` time-window (no schema change, nothing deleted — they stay in the vault permanently); rejects and cost events carry an explicit `run_id`. `RUN_ID` is a plain env value set at deploy time (e.g. `dayrun-2026-07-XX`). |
| D5 | Reject table schema | As in §2.1. **No `reject_stage` field** — the code has exactly one clean-reject path (failed sniper AND failed rescue; a sniper failure always triggers a rescue attempt first), so the field would hold a single constant value. The three filter scenarios are fully derivable: vault row with `relevance_score ≥ 0.15` = passed sniper; vault row with `score < 0.15` = rescued (carries `rescue_cosine`); `filter_rejects` row = failed both. |

**Verified code facts (re-verified against `main`; do not re-derive, but do re-confirm
line positions before editing — they may drift):**

| Fact | Anchor |
|---|---|
| Sniper runs at Silver; sets `is_high_signal` + `relevance_score` on every record | `processing/silver_job.py`; threshold `DEFAULT_THRESHOLD = 0.15` at `processing/keyword_sniper.py:82` |
| Rescue + drop branch live in `GlobalNewsGoldFunction.process_element` | `processing/gold_job.py` ~2783–2811: `rescued` → `is_high_signal=True` and continue; else `return  # no kv_archive, no Gold` |
| Rescue helper returns `(rescued, similarity)` — the cosine is in hand at both the promote and the drop branch | `compute_semantic_rescue()`, `gold_job.py` ~2459 |
| Rescued docs keep their original `relevance_score` (< 0.15) — tier is derivable, no tier column needed | promote branch only flips `is_high_signal` |
| `knowledge_vault` stores `relevance_score` + `sniper_keywords`; has NO `is_high_signal` and NO cosine column; only survivors reach `archive()` | `persistence/knowledge_vault.py`, INSERT ~line 112 |
| `is_high_signal` on archived rows is always True → a survivor-side flag column would be constant; the ONLY missing survivor datum is the cosine | drop branch precedes `kv_archive` |
| Runtime AI call sites (7 total): 4× chat.completions — `gold_job.py:261`, `:834`, `:1625` (gpt-4o via `OPENAI_MODEL_NAME`) and `translation.py` `_call_translation_api` (**gpt-3.5-turbo**, hardcoded `TRANSLATION_MODEL`); 3× embeddings — `gold_job.py:289–290` (main article vector), `:2492–2493` (rescue embed inside `compute_semantic_rescue`), plus the shared `call_openai_embedding()` used by the social path | verify exact lines at implementation time |
| `build_sniper_reference_vector.py` is offline/one-off — **do not instrument** | report decision 2026-06-30 |
| `agent/utils/llm_cost.py` prices gpt-4o + gpt-4o-mini + text-embedding-3-small, env-overridable; **gpt-3.5-turbo is NOT in `_DEFAULT_PRICING`** — without adding it, all translation calls price at $0.00 + warning | `_DEFAULT_PRICING`, llm_cost.py |
| No dedicated unit-test file for `llm_cost.py` exists anywhere today — agent-side coverage is indirect (Sprint 23.5 gate tests assert `total_cost_usd` accumulation only) | verified 2026-07-01 across `tests/` |
| Filter rejects are NOT DLQ traffic — DLQ (`_dlq_record`) is error-oriented (schema-invalid, API failure); a filter reject is a valid low-signal article. Dedup skips and empty-batch skips are also excluded from `filter_rejects` **by construction** (capture point is the drop branch only) | `gold_job.py:1898` |
| Flink code-bearing image changes require job **cancel + re-submit**, not a pod restart (HA keeps the old compiled job graph). Env-only changes (flags) need only a pod restart | `pipeline_processing.md §9` |
| Known non-participants in the day-run: OpenSky unreachable from GKE (KG-A-5), Google Trends 404 (KG-A-3). Neither affects filter calibration (global_news path only) and neither is an LLM path — but the day's cost number is "cost of the sources that actually ran"; record this caveat in the run report | `pipeline_sprints.md §4` |

---

## §2 — Schemas & contracts

### §2.1 `filter_rejects` (new table)

```sql
CREATE TABLE filter_rejects (
    reject_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                TEXT,
    source_name           TEXT NOT NULL,           -- newsapi / arxiv / telegram
    original_url          TEXT,
    title                 TEXT,
    inverted_pyramid_lead TEXT,
    full_text_raw         TEXT,                    -- FULL text, not truncated: manual FN classification (T7B.1) requires reading the article; volume is bounded because capture is flag-gated
    relevance_score       REAL NOT NULL,           -- sniper score (< 0.15 by construction)
    sniper_keywords       JSONB,
    rescue_cosine         REAL NOT NULL,           -- rescue similarity (< threshold; 0.0 for the empty-text edge)
    rejected_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_filter_rejects_run ON filter_rejects (run_id, rejected_at);
```

### §2.2 `knowledge_vault` — one new column

```sql
ALTER TABLE knowledge_vault ADD COLUMN rescue_cosine REAL;  -- nullable; populated ONLY on the rescue-promote path
```

NULL = passed the sniper directly (rescue never ran). Non-NULL = entered via rescue.
No other survivor-side change (see §1 verified facts for why).

### §2.3 `llm_cost_events` (new table) + summary views

```sql
CREATE TABLE llm_cost_events (
    event_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id            TEXT,
    site              TEXT NOT NULL,        -- see tag registry below
    source_name       TEXT,                 -- newsapi / arxiv / telegram / polymarket / hackernews / googletrends
    model             TEXT NOT NULL,        -- gpt-4o / gpt-3.5-turbo / text-embedding-3-small
    prompt_tokens     INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    total_tokens      INTEGER NOT NULL,
    cost_usd          NUMERIC(12,6) NOT NULL,
    trace_id          TEXT,                 -- canonical_event_id of the processed object (per-article unit economics; joins rescue_embed events to filter_rejects for wasted-spend analysis)
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_llm_cost_run ON llm_cost_events (run_id, created_at);

CREATE VIEW llm_cost_daily_summary AS
SELECT
    date_trunc('day', created_at)::date AS day,
    COALESCE(source_name, 'ALL')        AS source_name,
    COALESCE(site,        'ALL')        AS usage_type,
    count(*)                            AS calls,
    sum(prompt_tokens)                  AS prompt_tokens,
    sum(completion_tokens)              AS completion_tokens,
    sum(total_tokens)                   AS total_tokens,
    round(sum(cost_usd)::numeric, 4)    AS cost_usd
FROM llm_cost_events
GROUP BY date_trunc('day', created_at), ROLLUP (source_name, site);

CREATE VIEW llm_cost_run_summary AS
SELECT
    run_id,
    COALESCE(source_name, 'ALL')        AS source_name,
    COALESCE(site,        'ALL')        AS usage_type,
    count(*)                            AS calls,
    sum(prompt_tokens)                  AS prompt_tokens,
    sum(completion_tokens)              AS completion_tokens,
    sum(total_tokens)                   AS total_tokens,
    round(sum(cost_usd)::numeric, 4)    AS cost_usd
FROM llm_cost_events
GROUP BY run_id, ROLLUP (source_name, site);
```

The views deliver the three macro levels in one table: per usage-type within each
source, per-source totals (the intermediate `ALL` rows), and the grand total row.
Views are derived — never a second write path — so macro and micro cannot disagree.

### §2.4 Pipeline cost module — `utils/llm_cost.py` (copy of `agent/utils/llm_cost.py`)

Two deliberate deltas from the agent original, documented in the module docstring:

1. **Pricing:** add `"gpt-3.5-turbo": (0.0005, 0.0015)` to `_DEFAULT_PRICING`
   (translation model; env-overridable like the rest via the derived
   `LLM_COST_GPT_3_5_TURBO_*` names). *(Deferred follow-up, NOT this sprint: consider
   migrating translation to gpt-4o-mini, which is cheaper still — that is a behavior
   change requiring its own validation.)*
2. **Extended signature + DB write:**
   `record_usage(model, response, *, site, source_name=None, trace_id=None, run_id=None)`
   — emits the identical structured log line AND inserts one `llm_cost_events` row.
   The insert is **fail-open**: a DB error logs a warning and never raises — a cost-
   tracking failure must not fail message processing (mirror the `compute_cost`
   unknown-model philosophy). Reuse `utils/db.get_cursor()`; do not open ad-hoc
   connections.

Everything else stays line-identical to the agent copy (pricing math, env-override
resolution, `extract_usage`, unknown-model → $0.00 + warning).

### §2.5 `site=` tag registry (stable names — the log line and the table share them)

| site | call | model |
|---|---|---|
| `gold_enrich` | cognitive metadata extraction (news/arxiv/telegram) | gpt-4o-mini (production reality: cloud + local manifests override `OPENAI_MODEL_NAME` to gpt-4o-mini — confirmed in `flink-taskmanager-deployment.yaml`; Ron ratified 2026-07-02 that 4o-mini IS the intended enrichment model. `pipeline_processing.md`'s "gpt-4o" is stale — fix in T9) |
| `gold_consensus` | consensus bundling (polymarket/hackernews) | gpt-4o-mini (same override) |
| `translate` | Silver translation | gpt-3.5-turbo |
| `gold_embed` | main article/summary embedding | text-embedding-3-small |
| `rescue_embed` | semantic-rescue embedding | text-embedding-3-small |

`source_name` is passed separately at every call site (the embedding helper is shared
by 5 sources — a function-level tag alone would lose "who is expensive"). `trace_id` =
the record's `canonical_event_id`.

### §2.6 Env flags (read in `config/settings.py`)

| Var | Default | Semantics |
|---|---|---|
| `REJECT_CAPTURE_ENABLED` | `false` | Gates the `filter_rejects` INSERT in the drop branch. Read at job startup (`open()`); toggling requires a pod restart only (env-only change — no rebuild, no cancel/resubmit). |
| `RUN_ID` | `""` (→ stored as NULL) | Stamped into `filter_rejects.run_id` and `llm_cost_events.run_id`. |

---

## §3 — Task table

Implementation order is the listed order. T1–T6 are one contained local code change;
T7–T8 are the cloud/operational tail; T9 is documentation closure.

| Task | Description |
|---|---|
| [x] **T1** | Copy `agent/utils/llm_cost.py` → `utils/llm_cost.py` with exactly the two §2.4 deltas (gpt-3.5-turbo price row; extended `record_usage` with fail-open `llm_cost_events` INSERT). Module docstring documents both deltas, the copy-not-import rationale, and the KG-PHASE-9.5-9 reconciliation gate. The agent's file is NOT touched. |
| [x] **T2** | DDL: `filter_rejects`, `llm_cost_events`, the two views, and the `knowledge_vault.rescue_cosine` column — added to `infrastructure` `init.sql` (fresh installs) AND as an idempotent migration script (`IF NOT EXISTS` / guarded `ALTER`) for existing local + cloud DBs. Add the two §2.6 settings to `config/settings.py`. |
| [x] **T3** | Wire `gold_job.py` `GlobalNewsGoldFunction`: (a) drop branch — when `REJECT_CAPTURE_ENABLED`, INSERT the `filter_rejects` row (source, url, title, lead, full text, relevance_score, sniper_keywords, rescue_cosine, run_id) before the `return`; keep the INFO log; the INSERT is fail-open (warn, never raise — a capture failure must not turn a clean drop into a DLQ event). (b) promote branch — thread the `similarity` value through to `knowledge_vault.archive()` so the row lands with `rescue_cosine` populated (extend the silver_doc dict + the INSERT column list in `persistence/knowledge_vault.py`); sniper-passed docs keep it NULL. |
| [x] **T4** | Wrap all 7 runtime AI call sites in `record_usage(...)` with the §2.5 tags, passing `source_name`, `trace_id` (canonical_event_id), and `RUN_ID`. Covers the 4 chat sites, the main embedding, the rescue embedding, and the shared social-path embedding. `build_sniper_reference_vector.py` is explicitly NOT instrumented. Zero behavior change to any filter/enrichment logic — purely additive. |
| [x] **T5** | Gate 2 unit tests (see §4 for the quality bar): new `tests/test_utils/test_llm_cost.py` + reject-capture and cosine-persistence tests in `tests/test_processing/`. |
| [x] **T6** | Gate 3 + local E2E against the local Docker stack: run the pipeline end-to-end with the flag ON, verify (a) rejects land in `filter_rejects` with all fields populated, (b) rescued survivors carry `rescue_cosine` while sniper-passed rows have NULL, (c) every OpenAI call produced exactly one `llm_cost_events` row and the two views aggregate correctly, (d) re-baseline drop/rescue rates vs the pre-change baseline — they must be unchanged (instrumentation must not move filter behavior), (e) flag OFF run: no reject rows, cost rows still written. **STOP after T6 for Ron's approval before any cloud work.** |
| [x] **T7** | Cloud deployment (Domain C work, planned in — not bolted on): rebuild `anizai-flink` image; **cancel + re-submit** both Flink jobs (code-bearing change — pod restart is NOT sufficient, §1); apply the T2 migration to cloud Postgres; set `RUN_ID` + `REJECT_CAPTURE_ENABLED=true` in the cloud manifests; optional non-blocking Grafana panel reading `llm_cost_daily_summary` via the existing postgres-exporter. **Ron-approved addition (2026-07-02):** run the pending `reactive_triggers_log` DDL in the SAME cloud-Postgres migration window as the T2 migration — it is idempotent, zero-risk, and saves a separate DB session later; the table simply sits empty until the Sprint-23 agent deploy. This is the ONLY adjacent item bundled in: the Sprint 22–23.5 code backlog lives in the `anizai-agent` image (Domain B), is irrelevant to this A-centric day-run, and is explicitly OUT of scope — do not rebuild or deploy the agent image here. |
| [ ] **T8** | Day-run checklist: enable flag + RUN_ID (pod restart), verify capture live early in the run (a handful of reject rows + cost rows within the first hour), monitor via the run-summary view, at day's end set `REJECT_CAPTURE_ENABLED=false` (pod restart) — collected data stays. Produce a short run report: total cost, per-source/per-type breakdown, reject counts, the KG-A-3/KG-A-5 non-participation caveat, and an explicit note that the daily figure is a **gpt-4o-mini** number (the ratified production enrichment model) — not comparable to any gpt-4o projection. |
| [ ] **T9** | Documentation closure: update `plans/phase7b5_filter_calibration.md` to the new data reality (T7B.1/T7B.9 now reference `filter_rejects` + `rescue_cosine`; the day-run dataset replaces the nonexistent corpus) AND record the T6 early observation as a T7B.9 prior (9 real sniper-failed articles all failed rescue at cosines 0.14–0.29 vs the 0.35 threshold — suggestive the threshold is tight; small biased sample, decide only on the day-run data); add this sprint's row + status to `pipeline_sprints.md`; note the new tables in `pipeline_storage.md`; fix the stale "GPT-4o" enrichment-model references in `pipeline_processing.md` (§5/§6) to gpt-4o-mini per the ratified override. |

---

## §4 — Gates & test-quality directives

Standard four-gate model applies. **Ron's explicit directive: build the tests to the
highest standard — test everything that can break, not the happy path only.** Minimum
bar per area (Claude Code should extend, not shrink, this list):

**Gate 2 — `tests/test_utils/test_llm_cost.py` (new; no prior coverage exists anywhere):**
- Pricing math exact-value tests for ALL FOUR models (gpt-4o, gpt-4o-mini,
  gpt-3.5-turbo, text-embedding-3-small), including the 4× input/output asymmetry
  on gpt-4o.
- Env-override: set `LLM_COST_*` vars → price changes without reload; malformed
  value → default kept + warning.
- Unknown model → $0.00 + warning, never raises.
- `extract_usage`: embedding response (no `completion_tokens`) → completion=0;
  missing `usage` → (0,0,0); absent total reconstructed from split.
- `record_usage` DB write: correct row shape (site/source/model/tokens/cost/trace/run);
  **fail-open** — simulated DB error logs a warning and does not raise; log line still
  emitted; empty `RUN_ID` stored as NULL.

**Gate 2 — reject capture & cosine persistence (`tests/test_processing/`):**
- Drop branch with flag ON → exactly one `filter_rejects` row, every field populated,
  full (untruncated) text, cosine < threshold.
- Flag OFF → zero reject rows; drop behavior otherwise identical.
- Empty-text edge → `rescue_cosine = 0.0`, no crash.
- Capture-INSERT failure → warning only; the drop still completes cleanly (no DLQ).
- Promote branch → vault row carries the exact cosine returned by
  `compute_semantic_rescue`; sniper-passed doc → NULL.
- Negative-boundary: dedup skips and DLQ traffic produce NO reject rows.
- Filter behavior invariance: identical drop/promote decisions with instrumentation
  on vs off (same inputs, same outcomes — assert the decision, not just the side effects).

**Gate 3 — persistence round-trips** for `filter_rejects` and `llm_cost_events`
(insert → read back → field equality, JSONB intact), plus a view-correctness test:
seeded events → daily/run views return correct per-source, per-type, ALL-rollup and
grand-total rows.

**E2E (T6)** — as specified in the task; the drop/rescue-rate re-baseline is the gate
that proves "purely additive."

Local stack: Ron will have Docker up — Claude Code may bring up the compose stack
(Kafka, Flink, Postgres) for Gate 3 / E2E as needed.

---

## §5 — Execution notes for Claude Code

1. **Hard stop after T6.** Present local results (test summary + E2E evidence +
   baseline comparison) and wait for Ron's explicit approval before touching anything
   cloud-side (T7+).
2. Re-verify all §1 line anchors against the working tree before editing — they were
   verified 2026-06-30→07-02 and may have drifted.
3. Zero filter-logic change. If any task appears to require touching a threshold or a
   decision branch's semantics, stop and raise it — that belongs to 7B.5, not here.
4. The agent's `agent/utils/llm_cost.py` and all Domain-B code are out of scope. Do
   not modify them.
5. PyFlink 1.19 constraints apply (`pipeline_processing.md §8`): no OutputTag; keep
   new logic in pure functions testable without PyFlink, wiring guarded by
   `PYFLINK_AVAILABLE` — same pattern as the existing code.
6. Naming discipline: this is filter-observability / 7B.5-I. Never label anything here
   "calibration" in code or docs in a way that could collide with Domain D (Phase 10).

## §6 — Skills

- `sprint-kickoff` — open the sprint, establish working context, produce the
  implementation plan for Ron's approval before coding.
- `filter-analysis` — NOT used here; it drives 7B.5 itself, after this sprint's
  day-run has produced the dataset.

---

## §7 — Day-Run Protocol (T8 execution spec — approved 2026-07-02)

Organizing principle: **the "day" is defined by timestamps, not switches.** `RUN_ID`
is fixed at T7 deploy and never changes. Ron declares `T0` (UTC) once Stage-2 gates
pass; the official window is `[T0, T0+24h)` and every analysis query filters on it.
Warm-up rows before T0 and stragglers after close exist harmlessly outside the window.
Window choice: **morning-to-morning Israel time (~09:00 → 09:00 next day)** so the
go/no-go hour falls while Ron is awake and available.

**Freeze rule: during the window, nothing else is deployed, no env is changed, no
parallel experiment runs on the cluster. Clean measurement day.**

Claude Code prepares, for Ron, the evidence at each gate below — Ron approves each
gate explicitly in chat before the next stage proceeds. Ron's companion guide (what
he looks at and how he decides) lives at
`C:\Users\ronki\Desktop\Claude-anizai-docs\dayrun\dayrun_operator_guide.md` — keep
the two consistent if this section changes.

### Stage 0 — Entry conditions (T7 exit evidence)
1. Both Flink jobs RUNNING after cancel+resubmit (not merely pods Ready).
2. Migration 003 + `reactive_triggers_log` DDL applied to cloud Postgres (verify:
   `\d filter_rejects`, `\d llm_cost_events`, both views resolve,
   `knowledge_vault.rescue_cosine` column exists).
3. Live cloud smoke: at least one reject row AND one cost row landed in cloud
   Postgres from a real message.
4. `REJECT_CAPTURE_ENABLED=true` + `RUN_ID` set in manifests (RUN_ID format:
   `dayrun-YYYYMMDD`).

### Stage 1 — Spin-up & stabilization (unmeasured)
**Source schedule reality (verified in `orchestration/dags/`, all times UTC; IL = UTC+3
in July):** newsapi & hackernews every 20 min; openweather every 10 min; opensky
every 3 min (dead, KG-A-5); fred daily 06:00 (09:00 IL, no LLM); arxiv daily 07:00
(**10:00 IL — the single biggest LLM/filter burst of the day**); googletrends daily
08:00 (dead, KG-A-3). Telegram is NOT an Airflow DAG — continuous Telethon listener.
**Exactly-once rule: any daily cron fires exactly once inside any 24h window,
regardless of T0 phase — provided the Airflow scheduler is up the whole window and
the DAGs are unpaused** (note `is_paused_upon_creation=True` on newsapi — "DAGs
enabled" is a real check, not a formality).
**Timing plan: spin-up ~08:15–08:30 IL, stabilization until ~09:15–09:25, T0 declared
~09:30 IL (06:30 UTC).** Rationale: FRED's 09:00 IL burst lands in unmeasured warm-up
(zero-cost anyway; its counted occurrence is next morning inside the window), and the
ArXiv 10:00 IL burst lands INSIDE the first-hour gate — the richest possible material
for validating reject capture fast. Avoid declaring T0 exactly on a round hour when a
daily cron fires.

Scale up from 0 → wait until ALL green: pods Ready with no crash-looping; both jobs
RUNNING with no restart loop; Airflow DAGs enabled and firing; Kafka consumer lag
falling, not climbing; Prometheus/Grafana live. Expect an opening burst as scheduled
sources catch up — let it digest (~1h). **Telegram liveness is a named Stage-1 check**
(Ron's requirement): confirm the Telethon session is alive — evidence = a fresh
`source_name='telegram'` Bronze/Silver message or vault row after spin-up, or an
explicit successful Telegram DAG run. If Telegram auth is dead, fix before declaring
T0 (KG-A-3 googletrends and KG-A-5 opensky remain accepted-dead; telegram is NOT
acceptable-dead). **Drain check is a named Stage-1 gate:** Kafka consumer lag on ALL
Flink consumer groups must reach ≈0 before Ron declares T0 — no pre-window backlog
may leak into the measured window (anything drained during warm-up lands outside the
window by definition, which is fine).

### Stage 2 — Declare T0 + first-hour gate
When Stage 1 is green, record T0 (UTC ISO) in the run report skeleton. During
[T0, T0+1h] run the sanity pack (Claude Code pre-stages these; `:t0`/`:run_id`
parameterized):

```sql
-- S2.1 rejects landing, fields populated, cosine in range
SELECT count(*) AS rejects,
       count(*) FILTER (WHERE full_text_raw IS NULL OR full_text_raw = '') AS missing_text,
       min(rescue_cosine) AS min_cos, max(rescue_cosine) AS max_cos
FROM filter_rejects WHERE run_id = :run_id AND rejected_at >= :t0;
-- expect: rejects > 0, missing_text = 0, 0 <= cosines < 0.35

-- S2.2 cost rows per site
SELECT site, count(*) AS calls, round(sum(cost_usd)::numeric,6) AS usd
FROM llm_cost_events WHERE run_id = :run_id AND created_at >= :t0
GROUP BY site ORDER BY usd DESC;
-- expect: gold_enrich / gold_embed / rescue_embed present; translate only if a
-- Hebrew telegram message arrived (its absence alone is NOT a failure)

-- S2.3 micro/macro reconciliation
SELECT (SELECT count(*) FROM llm_cost_events WHERE run_id = :run_id) AS raw_rows,
       (SELECT calls FROM llm_cost_run_summary
         WHERE run_id = :run_id AND source_name='ALL' AND usage_type='ALL') AS view_total;
-- expect: equal

-- S2.4 DLQ / errors not anomalous (compare against Grafana + alertmanager: no 429 storm)
```

**Go/no-go:** rejects AND cost events landing within the hour ⇒ GO (window runs).
Not landing ⇒ STOP, fix, declare a NEW T0. Never "continue and see."
**Named first-hour check — NewsAPI:** root cause isolated 2026-07-02 by elimination
to request origin: byte-identical code + key returns articles from a local IP and
zero from the GCP datacenter IP — a provider-side (newsapi.ai) response difference
for cloud-origin requests. The producer fix now surfaces this loudly (ERROR on the
HTTP-200-error envelope, WARNING on 0-article categories). **First Stage-1 action
(pending Ron's ~1-credit approval): a single getArticles probe (articlesCount=1)
from the cluster, printing the raw response envelope.** Pre-committed decision
rule: provider-block confirmed → **the day still runs** — the cost goal is fully
valid (measure what actually runs) and the calibration corpus leans on ArXiv and
likely needs the extension rule. NO-GO is reserved for OUR faults, not a
provider-side block. Ron is separately checking newsapi.ai account settings and
filing a support ticket (cloud requests are counted in the dashboard yet return
empty — also a billing question).

### Stage 3 — In-window monitoring
Light checks every ~3–4 waking hours: S2.2 + Grafana glance. Overnight relies on
existing alerting. **Emergency rule (lesson of the 2026-07-02 storm): on ANY
sustained checkpoint-failure alert (GoldCheckpointFailureCluster → Sustained),
pause all Airflow DAGs IMMEDIATELY, whatever the suspected cause — even if seen
hours late.** Mechanism understood post-storm: slow synchronous enrichment under a
dense queue stalls checkpoint barriers → checkpoints EXPIRE (no API errors needed;
the storm showed 0×429, 100% successful calls) → restart → full replay → duplicate
calls (observed: 3,993 calls for 232 unique items, 94% duplicates, ~17 replay
cycles). Pausing DAGs stops queue inflow; the queue drains, checkpoints complete,
the loop dies. A 2-hour data hole beats a night of duplicate burn. **Midday cap
check: read the OpenAI platform usage page directly — NOT llm_cost_events, which
records only successful returned calls and undercounts RPD (SDK retry attempts
burn cap invisibly).** Intervene for: anomalous DLQ rate, zero cost rows for
2h+ during expected traffic, crash-looping pod. Do NOT intervene for a single pod
restart (checkpoint recovery is transparent). Cost writes are fail-open by design: a
transient DB blip = a small counting hole, not a run failure; `llm_usage` log lines
are the reconciliation backup. **Revised RPD expectation (2026-07-03 finding): the
kv_archive dedup does NOT gate enrichment — duplicate articles (same top-10
re-fetched by consecutive pulses) re-run GPT enrichment + embedding and re-insert
Gold vectors (uuid4 signal_id). A full day is therefore ~4,000–8,000 requests,
not 1,500–3,000. Graded cap rule: check the OpenAI usage page at ~13:00, ~17:00,
~21:00 IL; if usage crosses ~7,000 before ~21:00 → pause newsapi + hackernews DAGs
ONLY (the continuous duplicate-generating sources), keep arxiv/telegram/metrics
running; the window continues and the run report notes the cutoff. Expect mass
duplicate rows in filter_rejects (same rejected article re-captured every pulse) —
expected behavior, the DISTINCT-count rule protects the analysis.** Local Docker stays OFF for the entire window — the
local stack shares the same OpenAI account and newsapi credits.**

### Stage 4 — Close at T0+24h
Nothing is time-critical at the boundary — the window closes by definition. Then, in
order:
1. Run the closing pack (below) and **export every result to files** — Ron reviews
   files, not terminal scrollback: `docs\A_pipeline\reports\dayrun-YYYYMMDD\`
   (`cost_summary.csv`, `cost_by_source_site.csv`, `hourly_cost.csv`,
   `rejects_overview.csv`, `survivors_split.csv`, `funnel.csv` (S4.7), plus
   `run_report.md`). Use `psql \copy` for CSVs.
2. Verify completeness (S4-checks below) — only after Ron confirms the files are
   good:
3. `REJECT_CAPTURE_ENABLED=false` + pod restart (collected data stays).
4. Scale down — only after step 2's confirmation; keep the cluster up a few hours
   if Ron wants extra ad-hoc queries first.

```sql
-- S4.1 the day's number + macro table (THE deliverable)
SELECT * FROM llm_cost_run_summary WHERE run_id = :run_id ORDER BY source_name, usage_type;
-- windowed variant (strict 24h): aggregate llm_cost_events with
-- created_at >= :t0 AND created_at < :t0 + interval '24 hours'

-- S4.2 hourly burn curve
SELECT date_trunc('hour', created_at) AS hr, count(*) AS calls,
       round(sum(cost_usd)::numeric,4) AS usd
FROM llm_cost_events
WHERE created_at >= :t0 AND created_at < :t0 + interval '24 hours'
GROUP BY 1 ORDER BY 1;

-- S4.3 rejects overview (the 7B.5 dataset)
SELECT source_name, count(*) AS rejects,
       round(avg(rescue_cosine)::numeric,4) AS avg_cos,
       percentile_cont(ARRAY[0.5,0.9,0.99]) WITHIN GROUP (ORDER BY rescue_cosine) AS p50_90_99
FROM filter_rejects
WHERE rejected_at >= :t0 AND rejected_at < :t0 + interval '24 hours'
GROUP BY source_name;

-- S4.4 survivors split in-window (tier derivation)
SELECT count(*) FILTER (WHERE relevance_score >= 0.15) AS sniper_passed,
       count(*) FILTER (WHERE relevance_score <  0.15) AS rescued,
       count(*) FILTER (WHERE rescue_cosine IS NOT NULL) AS with_cosine
FROM knowledge_vault
WHERE ingested_at >= :t0 AND ingested_at < :t0 + interval '24 hours';
-- expect: rescued = with_cosine

-- S4.5 completeness cross-checks (safe-to-close criteria):
--   (a) S2.3 reconciliation still equal;
--   (b) rescue accounting: rescue_embed cost events in-window ≈ in-window rejects
--       + in-window rescued (small gap = DLQ'd embeds / fail-open holes; investigate if >2-3%);
--   (c) spot-check llm_usage log-line count vs llm_cost_events count for one hour
--       (fail-open hole detection).

-- S4.6 wasted spend on dropped items (dimension #5): rescue_embed events whose
-- trace_id does NOT match any in-window survivor's canonical_event_id
-- (filter_rejects deliberately carries no canonical_event_id — the join runs
-- against the vault side):
SELECT count(*) AS wasted_calls, round(sum(e.cost_usd)::numeric,6) AS wasted_usd
FROM llm_cost_events e
WHERE e.site = 'rescue_embed'
  AND e.created_at >= :t0 AND e.created_at < :t0 + interval '24 hours'
  AND NOT EXISTS (SELECT 1 FROM knowledge_vault kv
                  WHERE kv.canonical_event_id = e.trace_id
                    AND kv.ingested_at >= :t0
                    AND kv.ingested_at <  :t0 + interval '24 hours');
-- sanity: wasted_calls ≈ in-window filter_rejects count
```

**S4.7 — per-source funnel → `funnel.csv` (added 2026-07-04).** One row per source,
every count bounded to [T0, T0+24h). Purpose: "how many objects arrived from each
source, and how many actually landed in the tables." **Duplicates stay visible as-is**
— the raw-rows vs DISTINCT gap IS the duplication measurement (2026-07-02 precedent:
577 reject rows / 32 distinct articles). Columns per source:

| col | meaning | measured from |
|---|---|---|
| (a) `pulled` | Bronze messages on `ingest.bronze.<source>` with `producer_timestamp` in-window | Kafka scan |
| (b) `silver` | Silver messages produced in-window, attributed by the record's `source_name` | Kafka scan of the 3 `process.silver.*` topics |
| (c) `gold_gate` | objects that reached the Gold gate = in-window gate outcomes: `(d raw) + (e)` for the global_news sources; `n/a` for non-gate sources (no drop path) | derived |
| (d) `rejects_rows` / `rejects_distinct` | `count(*)` AND `count(DISTINCT original_url)` from `filter_rejects` | SQL |
| (e) `archived` | in-window rows: `knowledge_vault` (newsapi/arxiv/telegram), `social_vault` (polymarket/hackernews), `momentum_vault` (metrics sources) — all filtered on `ingested_at` | SQL |
| (f) `enriched_distinct` | `count(DISTINCT trace_id)` from `llm_cost_events` per `source_name` | SQL |
| (g) `dlq` | in-window `dead-letter-queue` entries attributed by source in the payload | Kafka scan |

```sql
-- S4.7 SQL side ((d), (e), (f)); run per window, one \copy per block, assemble
-- funnel.csv columns by source key:
SELECT source_name, count(*) AS rejects_rows,
       count(DISTINCT original_url) AS rejects_distinct
FROM filter_rejects
WHERE rejected_at >= :t0 AND rejected_at < :t0 + interval '24 hours'
GROUP BY source_name;

SELECT source_name, count(*) AS archived FROM knowledge_vault
WHERE ingested_at >= :t0 AND ingested_at < :t0 + interval '24 hours'
GROUP BY source_name
UNION ALL
SELECT source_name, count(*) FROM social_vault
WHERE ingested_at >= :t0 AND ingested_at < :t0 + interval '24 hours'
GROUP BY source_name
UNION ALL
SELECT source_name, count(*) FROM momentum_vault
WHERE ingested_at >= :t0 AND ingested_at < :t0 + interval '24 hours'
GROUP BY source_name;

SELECT source_name, count(DISTINCT trace_id) AS enriched_distinct
FROM llm_cost_events
WHERE created_at >= :t0 AND created_at < :t0 + interval '24 hours'
GROUP BY source_name;
```

Kafka side ((a), (b), (g)): scan each topic from `seek_to_beginning` and bucket by
the in-payload timestamp/source — same consumer pattern as
`tests/e2e/run_newsapi_trace.py::snapshot_offsets` extended with a JSON read
(precedent: the 2026-07-03 stint's Bronze-by-day histogram). Offsets alone are NOT
window-bounded — always filter on `producer_timestamp` (Bronze), the Silver record's
own timestamp, and the DLQ record's `dlq_timestamp`. Note two known funnel leaks so
the numbers reconcile: dedup skips at `kv_archive` ((b) > (e) without a reject row)
and the Silver→Gold consumption lag at the window edge ((b) counts production, (c)
counts gate outcomes).

Safe-to-close = S4.5 (a)–(c) pass and the export files (including `funnel.csv`)
exist and open. Then flag off → restart → scale down.

### Stage 5 — Success criteria & extension rule (pre-committed)
- **Cost goal:** met by construction — S4.1 + caveats (gpt-4o-mini number; KG-A-3 /
  KG-A-5 sources absent; translate volume telegram-only).
- **Calibration goal:** target ≈ **100 in-window DISTINCT rejected articles —
  counted as `count(DISTINCT original_url)`, never raw rows** (the 2026-07-02 storm
  wrote 577 reject rows for only 32 distinct articles; replay loops duplicate rows,
  and 7B.5 analysis must dedupe by original_url — a post-Saturday uniqueness guard
  is a candidate hardening item). **Count with the window condition, never by run_id alone**
  (`WHERE rejected_at >= :t0 AND rejected_at < :t0 + interval '24 hours'`) — the
  capture flag was live during T7 smoke and pre-T0 warm-up, so run-tagged junk rows
  exist outside the window. If under target at close: **extend, don't discard** — leave the
  flag ON past T0+24h until the cumulative reject count reaches ~100 (cost analysis
  stays on the first 24h; the calibration corpus uses the full extended window; the
  two goals need not share a window). If the cluster was already scaled down before
  deciding to extend, a fresh capture stint (scale up → flag on → run until target →
  close per Stage 4) is equally valid — reject rows are cumulative across stints
  under the same or a new run_id; 7B.5 consumes them all.


---

> Companion doc: `plans/phase7b5_filter_calibration.md` — the calibration sprint this
> one unblocks (updated by T9).
> Decision provenance: advisor macro plan + cost-scope report (2026-06-30) and the
> Ron↔Advisor decision session (2026-07-01/02) captured in §1.
