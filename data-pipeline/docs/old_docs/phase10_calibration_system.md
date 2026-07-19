# Phase 10 — Calibration & Backtesting System
## Anizai Project | Phase 10 (Phases 10A–10E)

---

## How to use this document

This is the **granular implementation plan** for Phase 10 — a standalone calibration & backtesting harness that measures and improves the Anizai agent's forecast accuracy over time. It is the calibration system's equivalent of [phase7_intelligent_filtering.md](data-pipeline/docs/archive/phase7_intelligent_filtering.md) for the data pipeline and [agentic_hub_implementation.md](data-pipeline/docs/agentic_hub_implementation.md) for the agent.

It should be loaded by Claude Code (or by a collaborator picking up this work) at the start of every Phase 10 sprint, alongside:
- [CLAUDE.md](CLAUDE.md) (engineering guardrails, skill reference)
- The relevant Phase 10 section in [data-pipeline/task_plan.md](data-pipeline/task_plan.md)
- [data-pipeline/docs/agentic_hub_spec.md](data-pipeline/docs/agentic_hub_spec.md) §8.7 (SessionResult contract — read-only consumer of this contract)
- [data-pipeline/docs/anizai_handoff_consolidated.md](data-pipeline/docs/anizai_handoff_consolidated.md) §3.4 (idempotency key contract on `POST /sessions`)

**Required skills — placeholder (skills not yet built):** Calibration-specific skills will be authored before Phase 10 work begins. Until they exist, the generic data-pipeline + hub skills cover most of the surface: `sprint-kickoff`, `code-review`, plus `infrastructure` for sprints touching Docker/Cloud Run/Cloud SQL.

*When calibration skills are built, this section should be updated:* replace this placeholder with the actual skill names as required pre-reading (likely candidates: `calibration-design`, `polymarket-integration`, `metrics-and-scoring` — exact names TBD when the skills are authored).

**Nomenclature note:** Earlier drafts of this plan used "Phase 9" / "Phase 9A–9E" before the project's Cloud Deployment phase was renumbered. The whole calibration phase is now **Phase 10**, with sub-phases **10A–10E**. Likewise, any reference inside this doc to "Phase C / C1–C5" or "Phase 9 (Cloud)" refers to the closed Cloud Deployment phase, now **Phase 9** in current nomenclature; sub-phases there are **9A–9E** (formerly C1–C5). The cross-phase mapping is in [data-pipeline/task_plan_implementation.md](data-pipeline/task_plan_implementation.md) §3.

Conventions still apply: Conventional Commits with section refs, `task_plan.md` updated after every task, all work inside `data-pipeline/` (Phase 10 lives at `data-pipeline/calibration/`), no code without an approved plan, the four-gate testing model.

---

## Phase 10 Overview

### Goal

Build a research/development harness that:
1. Picks 25–30 active Polymarket questions across three resolution-time cohorts (~7d, ~14d, ~30–45d).
2. Submits each question to the **existing** Anizai agent through the **existing** `forecastQueries → sessionResults` Firestore flow — **zero changes to the agent or to the user-facing frontend**.
3. Detects Polymarket resolution events automatically (REST polling, no auth) and records ground-truth outcomes.
4. Computes **Brier scores**, a **calibration curve** (5 buckets), **per-cohort Brier scores**, and **resolution-source contribution** per resolved question.
5. Re-forecasts still-open questions weekly on a Cloud Scheduler + Cloud Run cron, using whichever agent version is currently deployed; stores both forecasts so an **improvement delta** (original vs. updated) can be measured against the same ground truth.
6. Exposes the data to an **operator-only** standalone React UI (separate Firebase Hosting site) for analysis.

The system's primary purpose is the **improvement loop** — every week of resolutions feeds back into agent tuning decisions, and the next week's re-forecast measures whether those tuning decisions actually moved the needle.

### Non-goals

- No automated agent parameter tuning. Tuning decisions stay human-driven; Phase 10 only measures.
- No integration with the user-facing frontend or the Express BFF's session UI.
- No multi-user RBAC. Single-operator (Firebase Auth email allowlist).
- No question sources other than Polymarket (Kalshi etc. are future work).
- No mutation of any existing pipeline or agent code. Phase 10 is strictly additive: new directory `calibration/`, new Cloud Run service, new Cloud SQL instance, new Firebase Hosting site.
- No manual-add of Tier 2 freeform questions in V1 — calibration is Polymarket-anchored only. Tier 2 calibration is post-V1 work.

### Sprint Structure (5 sprints, sequential)

The phase is split into five sprints that each produce a working slice. Phase 10A through 10C deliver a system runnable from a CLI; 10D adds cloud automation; 10E adds the operator UI surface. Earlier sprints don't depend on later ones, but later sprints depend on earlier ones.

| Sprint | Phase | Focus | Definition of Done |
|---|---|---|---|
| Phase 10A | Phase 10 | Foundation — Postgres schema + Polymarket adapter (auto-select + manual add + resolution polling) | New Cloud SQL instance + 5 tables provisioned; CLI seeds 25–30 questions across 3 cohorts; resolution poller correctly flips a known-resolved test market to `resolved`. |
| Phase 10B | Phase 10 | Forecast Engine Bridge — dispatch to `forecastQueries` + harvest from `sessionResults` | CLI dispatches a question through the live agent; `calibration_forecasts` row populated end-to-end with `final_probability`, `agent_version`, `agent_evidence_summary`. |
| Phase 10C | Phase 10 | Scoring & Metrics — Brier + calibration curve + cohort + improvement | Resolution event triggers Brier computation across all forecasts for that question; CLI dumps current calibration curve and per-cohort scores; metrics snapshot row written. |
| Phase 10D | Phase 10 | Cloud Automation — Cloud Run + Cloud Scheduler + IAM + secrets | Single Cloud Run service deployed with `/tasks/dispatch`, `/tasks/harvest`, `/tasks/resolve` endpoints; Scheduler jobs invoke them weekly / every-5-min / hourly; one full unattended weekly cycle completes successfully end-to-end. |
| Phase 10E | Phase 10 | Operator API + UI Contract — read API for the React app | `/api/*` endpoints under the same Cloud Run service; React component contract doc handed off to the UI collaborator; integration test covers the full operator workflow against the live API. |

**Why this split**: 10A is purely deterministic data work (no agent involvement, no Firestore writes) — it's the lowest-risk foundation and validates the schema before anything depends on it. 10B touches Firestore but is read-mostly with two writes (forecastQueries insert + Postgres write); easy to validate. 10C is pure computation over data already in Postgres — no external dependencies. 10D is the first sprint that involves cloud infra (Cloud Run, Cloud Scheduler, Cloud SQL networking, IAM) and is gated by Phase 9 (Cloud Deployment) closeout — already CLOSED 2026-05-10, so 10D is unblocked. 10E is API + collaborator handoff — runs in parallel with the React UI implementation.

**Sprint numbering**: Phase 10 sprints carry the **"Phase 10A" / "Phase 10B" / "Phase 10C" / "Phase 10D" / "Phase 10E" labels only** — no numeric sprint identifiers — to avoid collision with Phase 8 hub Sprints 18-26 and Phase 9 (Cloud) Sprints 9A-9E (formerly C1-C5). Tasks inside each sprint are numbered T10A.1, T10A.2, … T10E.1, T10E.2, …

**Ordering vs. other phases**:
- **Hard prerequisite for any cloud automation (10D)**: Phase 9D closed (agent reachable in cloud Firestore via cross-project Workload Identity). Phase 9 closed 2026-05-10, so this prerequisite is satisfied.
- **Soft prerequisite (10A–10C)**: 10A–10C are runnable against the existing dev Firestore + local agent, but the cloud agent is now also live, so they can run either way.
- **Not a prerequisite**: Phase 7 (intelligent filtering). 7's quality improvements should *show up* in calibration curves, but Phase 10 doesn't depend on 7 landing first.

---

## Settled Design Decisions

### A — Architecture

| ID | Decision |
|---|---|
| **A1** | **Standalone cloud footprint.** Phase 10 deploys as one Cloud Run service (`calibration-runner`) plus Cloud Scheduler jobs and one Cloud SQL Postgres 16 instance. Lives in the **same GCP project as the agent (`anizai-pipeline`)** for IAM simplicity but in its own directory tree (`data-pipeline/calibration/`) and behind its own GSA (`calibration-runner@anizai-pipeline`). Cross-project Firestore access (`anizai-ai`) reuses the same Workload Identity pattern Phase 9D established. |
| **A2** | **One Cloud Run service, multiple endpoints.** All scheduled tasks (`dispatch`, `harvest`, `resolve`, `discover`, `snapshot_metrics`) and the operator-facing `/api/*` routes live in one container. Reasons: shared Postgres pool, shared Firestore client, shared config, single image to build/deploy, $0 cold-start cost across endpoints. Auth differs per route (Cloud Scheduler OIDC for `/tasks/*`, Firebase Auth ID token for `/api/*`). |
| **A3** | **Polling, not listeners.** Harvester is a **scheduled poll** (Cloud Scheduler → `/tasks/harvest` every 5 min), not a long-lived Firestore listener. Reason: Cloud Run's billing model penalizes always-on; polling at 5-min granularity adds at most a 5-min delay to result capture, which is irrelevant for a weekly cycle. This is a deliberate departure from the agent's listener pattern. |
| **A4** | **Calibration writes its own forecastQueries; agent does not know it's a calibration submission.** The calibration-runner inserts forecastQueries docs with `idempotencyKey` (UUID4 per dispatch attempt, per the §3.4 contract), `question`, and **two custom side fields** `calibrationRunId` + `calibrationQuestionId`. The agent reads `question` only and ignores the side fields (verified against KG-PHASE8-15: no schema validation on inbound forecastQueries). The harvester filters back via these side fields. If Sprint 26 closes KG-PHASE8-15 with strict validation, calibration moves the side fields under a `metadata: {}` namespace. |
| **A5** | **No agent code changes.** If a calibration-side need would require touching `agent/`, that need is descoped to Phase 10.5 or rejected. The only contract Phase 10 depends on is the §8.7.2 SessionResult shape, which is already stable post-Sprint 20. |

### B — Storage / Schema

| ID | Decision |
|---|---|
| **B1** | **Dedicated Cloud SQL Postgres 16 instance.** Shared name `anizai-calibration-db`, smallest tier (`db-f1-micro`, 10 GB SSD, single zone). Reasons: (a) full ops isolation from the pipeline GKE Postgres (no TimescaleDB / pgvector dependencies); (b) Cloud Run → Cloud SQL via Cloud SQL Auth Proxy is one-line setup, vs. exposing GKE Postgres via internal LB; (c) cost is ~$8–10/month, well within the cited budget envelope. |
| **B2** | **5 tables, no views (pre-V1).** `calibration_questions`, `calibration_forecasts`, `calibration_resolutions`, `calibration_runs`, `calibration_metrics_snapshots`. Aggregations are computed in Python (Phase 10C) and either returned live by `/api/metrics/*` or persisted as JSONB rows in `calibration_metrics_snapshots`. Materialized views are a post-V1 optimization. |
| **B3** | **All times in UTC, all timestamps `TIMESTAMPTZ`.** Polymarket resolution dates come in UTC; Cloud Scheduler fires in UTC; cohort window math is in UTC. No tz conversion in the data layer — only at UI render time if the operator wants local time. |
| **B4** | **`final_probability` stored as `NUMERIC(5,4)` with CHECK 0–1.** Matches the §5.1 handoff contract (probability units always 0–1 floats). Brier score stored as `NUMERIC(8,7)` (range 0–1, four-significant-digit headroom). |

### C — Question Management

| ID | Decision |
|---|---|
| **C1** | **Auto-selection criteria (defaults; calibratable via env vars):** time-to-resolution windows **5–9 days / 12–16 days / 28–46 days**; minimum cumulative volume **$50K** for 7d/14d cohorts and **$25K** for 30–45d (fewer high-volume long-horizon markets); category allowlist `geopolitical`, `financial`, `ai`; category blocklist `sports`, `entertainment`, `pure_crypto_price`. Target counts: **8–10 per cohort, 24–30 active total** (`CALIBRATION_TARGET_COUNT_7D=10`, `_14D=10`, `_30_45D=8`). |
| **C2** | **Manual adds use the same internal `Question` record.** UI form posts `{question_text, polymarket_slug, expected_resolution_date, category, cohort, operator_email}` → `/api/questions` → row inserted with `added_by='manual'`. Auto and manual rows are otherwise indistinguishable downstream. |
| **C3** | **Auto-selection runs on a separate hourly Cloud Scheduler job** that calls `/tasks/discover`. Discovers new candidate markets, doesn't kick anyone out, only tops up to the target count. New questions are inserted with `status='open'` and immediately enqueued for an initial forecast (Week 0). |
| **C4** | **Polymarket category mapping** lives in [calibration/polymarket/taxonomy.json](data-pipeline/calibration/polymarket/taxonomy.json): a JSON-driven mapping from Polymarket tag strings to the four-category internal taxonomy. Updateable without redeploy via Cloud Run env var if needed. Seed allowlist: `Politics`, `Geopolitics`, `Macroeconomy`, `AI`, `Tech`. Seed blocklist: `Sports`, `Entertainment`, `Crypto Prices`. |

### D — Resolution

| ID | Decision |
|---|---|
| **D1** | **Resolution detection via CLOB REST**, not the WebSocket. Endpoint: `GET https://clob.polymarket.com/markets/{condition_id}` (already used by [polymarket_producer.py:181](data-pipeline/ingestion/polymarket_producer.py#L181)). Resolution = `closed=true` AND one outcome's `winner=true`, OR `outcomePrices` shows `[1.0, 0.0]` / `[0.0, 1.0]` for >24h (settle-window guard). Polled hourly. |
| **D2** | **Outcome encoding:** `YES` if YES outcome `winner=true`, `NO` if NO outcome `winner=true`, `AMBIGUOUS` if Polymarket reports `disputed=true` or the market is `void`/`invalid`. Ambiguous resolutions are **excluded from Brier aggregations** but still recorded for audit. |
| **D3** | **Resolution detection is idempotent.** Resolver re-runs every hour; if `calibration_resolutions` already has a row for the question, skip. The Brier computation re-runs on every fresh resolution insert (Phase 10C), populating `brier_score` on every existing forecast for that question. |
| **D4** | **`raw_resolution_data JSONB`** stores the full CLOB API response at the moment of detection — full audit trail, supports later forensic re-scoring if Polymarket's API shape changes or a resolution is contested. |

### E — Measurement Framework

| ID | Decision |
|---|---|
| **E1** | **Brier Score is the primary metric.** Defined as `(forecast.final_probability - outcome_numeric)^2` where `outcome_numeric ∈ {0.0, 1.0}` (YES=1, NO=0). Stored per forecast row. Aggregated as a simple mean across forecasts (no weighting in V1). |
| **E2** | **Calibration curve buckets:** five fixed buckets `[0.0, 0.2)`, `[0.2, 0.4)`, `[0.4, 0.6)`, `[0.6, 0.8)`, `[0.8, 1.0]`. For each bucket: count of forecasts, mean predicted probability, **actual YES rate** (resolved YES count / total resolved in bucket), Wilson 95% interval bounds. Bucket points plotted against the diagonal in the UI. |
| **E3** | **Cohort metrics:** Brier mean computed separately for each `cohort` value plus an `all` aggregate. Stored in `calibration_metrics_snapshots` keyed by `(metric_type='cohort_brier', cohort)`. |
| **E4** | **Source contribution rollup.** From each forecast's `agent_evidence_summary` JSONB, compute per-vault-type contribution counts. Aggregated as: for resolved questions only, what was the mean Brier when evidence type X was present in `agent_evidence_summary` vs. absent. This is the "which vault is most predictive" signal. |
| **E5** | **`agent_evidence_summary` extraction contract.** At harvest time, the harvester reads the SessionResult doc and the `evidence` subcollection (per §5.2 of handoff), then projects to: `{evidence_count_total, evidence_count_by_source_type, reactive_search_used: bool, reactive_search_count, top_3_key_factor_titles, vault_types_present: ["knowledge","social","momentum","mapping"], projection_version: "1.0"}`. **Counts and metadata only — no raw text snippets** (the evidence subcollection in Firestore stays the source of truth for raw text). The projection is **stable across agent versions** and survives §8.7.2 schema additions. |

### F — Improvement Loop

| ID | Decision |
|---|---|
| **F1** | **Re-forecast cycle: weekly, Sundays 02:00 UTC.** Configurable via Cloud Scheduler. Re-forecasts every question with `status='open'` regardless of how many prior forecasts it has. Each new forecast row gets `forecast_run_index = (max existing for this question) + 1`. |
| **F2** | **Counter-factual / improvement delta — per resolution event.** For any question that resolves, the UI shows: (a) the original Week 0 forecast and its Brier, (b) the most recent re-forecast and its Brier, (c) the delta. Aggregated improvement = mean(Brier_original) − mean(Brier_latest) across resolved questions, split by cohort (so short-horizon vs. long-horizon improvement is visible separately). The improvement curve is one row per resolution event in `calibration_metrics_snapshots`; the UI rolls into weekly bins for display if desired. |
| **F3** | **`agent_version` is the attribution key.** Each forecast row stores the `agentVersion` string the agent reported in SessionResult.agentVersion. Improvement-delta charts can be filtered by agent version pair (e.g., "0.4.0 → 0.5.0"). |

### G — Cloud Run Runner & Operator Surface

| ID | Decision |
|---|---|
| **G1** | **Single Docker image** at [data-pipeline/infrastructure/Dockerfile.calibration](data-pipeline/infrastructure/Dockerfile.calibration). Python 3.11, FastAPI, runs `python -m calibration.server`. Image deployed to `us-central1-docker.pkg.dev/anizai-pipeline/anizai-images/anizai-calibration:VERSION`. |
| **G2** | **Cloud Run config:** min instances 0, max 2, concurrency 20, request timeout 120s for `/api/*` and 540s (max) for `/tasks/dispatch` (must wait for ~30 forecastQueries inserts + ack). CPU only-during-request mode. |
| **G3** | **Cloud SQL connectivity via Cloud SQL Auth Proxy sidecar** (built into Cloud Run via the connection string `--add-cloudsql-instances`). No VPC connector needed — keeps networking simple. |
| **G4** | **Secrets via Secret Manager**, mounted as env vars at Cloud Run deploy time: `CALIBRATION_DB_PASSWORD`, `FIREBASE_PROJECT_ID` (=`anizai-ai`), `FIREBASE_AUTH_OPERATOR_EMAILS` (comma-separated allowlist), `POLYMARKET_API_BASE` (constants for resolver). Reuses the Phase 9A Secret Manager pattern. |
| **G5** | **No agent worker on Cloud Run.** The cloud-deployed agent (Phase 9D) keeps owning `forecastQueries` claims. Calibration only writes new forecastQueries docs and reads the resulting sessionResults — it does not subscribe to claim queues, does not run LangGraph code, does not import `agent/`. |
| **G6** | **UI hosted on `anizai-ai` Firebase project, separate Hosting site.** Reuses the existing Firebase Auth tokens; one less project to provision. Hosting target named `calibration` distinct from the user-facing site. Firebase rules + auth allowlist gate access. |
| **G7** | **Operator allowlist initial:** `ronking79@gmail.com`. Adding the UI collaborator's email is part of T10E.1 once the email is known. The allowlist is a Secret Manager secret (`FIREBASE_AUTH_OPERATOR_EMAILS`), comma-separated, hot-swappable without redeploy. |

---

## Postgres Schema Proposal

Single Cloud SQL Postgres 16 instance, single database `anizai_calibration`, single schema `public` (kept simple — there is no other schema to disambiguate from). Five tables.

### Table 1: `calibration_questions`

```sql
CREATE TABLE calibration_questions (
    id                          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    question_text               TEXT            NOT NULL,
    polymarket_slug             TEXT            NOT NULL,
    polymarket_condition_id     TEXT            NOT NULL,    -- CLOB condition_id, used for resolution polling
    category                    TEXT            NOT NULL CHECK (category IN ('geopolitical','financial','ai','other')),
    cohort                      TEXT            NOT NULL CHECK (cohort IN ('7d','14d','30-45d')),
    expected_resolution_date    DATE            NOT NULL,
    liquidity_usd_at_pickup     NUMERIC(14,2),
    status                      TEXT            NOT NULL CHECK (status IN ('open','resolved','archived'))
                                                DEFAULT 'open',
    added_by                    TEXT            NOT NULL CHECK (added_by IN ('auto','manual')),
    added_by_operator           TEXT,                        -- email if manual; NULL if auto
    created_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (polymarket_condition_id)
);
CREATE INDEX idx_questions_status_cohort ON calibration_questions(status, cohort);
CREATE INDEX idx_questions_expected_resolution ON calibration_questions(expected_resolution_date)
    WHERE status = 'open';
```

**Reasoning:** `polymarket_condition_id` is the stable resolution key (slug can change). `UNIQUE` on `condition_id` prevents duplicate ingestion across auto-discovery and manual-add. Partial index on `expected_resolution_date` accelerates the resolver's "what's about to resolve" query without bloating the index with archived rows. `added_by_operator` deliberately allows NULL for auto to avoid a sentinel string.

### Table 2: `calibration_forecasts`

```sql
CREATE TABLE calibration_forecasts (
    id                          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id                 UUID            NOT NULL REFERENCES calibration_questions(id) ON DELETE CASCADE,
    run_id                      UUID            NOT NULL REFERENCES calibration_runs(id),
    forecast_run_index          INTEGER         NOT NULL,    -- 0=initial seed, 1=first re-forecast, ...
    query_doc_id                TEXT            NOT NULL,    -- forecastQueries doc id (Firestore)
    session_id                  TEXT,                        -- Firestore sessionId; NULL until harvest
    idempotency_key             TEXT            NOT NULL,    -- the UUID4 we sent on POST
    agent_version               TEXT,                        -- copied from SessionResult.agentVersion
    final_probability           NUMERIC(5,4)    CHECK (final_probability >= 0 AND final_probability <= 1),
    confidence                  NUMERIC(5,4)    CHECK (confidence >= 0 AND confidence <= 1),
    tier                        TEXT            CHECK (tier IN ('tier_1','tier_2')),
    status                      TEXT            NOT NULL CHECK (status IN ('dispatched','completed','failed','timed_out'))
                                                DEFAULT 'dispatched',
    error_message               TEXT,                        -- populated when status='failed'
    agent_evidence_summary      JSONB,                       -- E5 projection of SessionResult + evidence subcoll
    brier_score                 NUMERIC(8,7),                -- NULL until question resolves
    forecast_dispatched_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    forecast_completed_at       TIMESTAMPTZ,                 -- when harvester wrote the result
    UNIQUE (question_id, forecast_run_index),                -- one forecast per (question, run_index)
    UNIQUE (idempotency_key)                                 -- defense-in-depth on dispatch retries
);
CREATE INDEX idx_forecasts_question ON calibration_forecasts(question_id);
CREATE INDEX idx_forecasts_status ON calibration_forecasts(status) WHERE status IN ('dispatched','timed_out');
CREATE INDEX idx_forecasts_query_doc ON calibration_forecasts(query_doc_id);
```

**Reasoning:** Two unique constraints — `(question_id, forecast_run_index)` enforces the improvement-loop semantics (no duplicate forecasts at the same run index for one question), and `idempotency_key` prevents harvester double-writes. Partial index on `status` is what the harvester scans every 5 min — keeps it fast. `agent_version` is nullable because it's populated at harvest time (a `dispatched` row hasn't seen a SessionResult yet).

### Table 3: `calibration_resolutions`

```sql
CREATE TABLE calibration_resolutions (
    id                          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id                 UUID            NOT NULL UNIQUE REFERENCES calibration_questions(id) ON DELETE CASCADE,
    resolved_at                 TIMESTAMPTZ     NOT NULL,    -- timestamp Polymarket reports
    detected_at                 TIMESTAMPTZ     NOT NULL DEFAULT NOW(),  -- when our resolver saw it
    outcome                     TEXT            NOT NULL CHECK (outcome IN ('YES','NO','AMBIGUOUS')),
    outcome_numeric             NUMERIC(2,1)    CHECK (outcome_numeric IN (0.0, 1.0)),  -- NULL for AMBIGUOUS
    resolution_source           TEXT            NOT NULL DEFAULT 'polymarket_clob',
    raw_resolution_data         JSONB           NOT NULL
);
```

**Reasoning:** `UNIQUE (question_id)` makes the resolver naturally idempotent — second-time detection is a `INSERT ... ON CONFLICT DO NOTHING`. `outcome_numeric` is denormalized for join-free Brier computation; CHECK guarantees it's `NULL` exactly when outcome is AMBIGUOUS.

### Table 4: `calibration_runs`

```sql
CREATE TABLE calibration_runs (
    id                          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    run_type                    TEXT            NOT NULL CHECK (run_type IN ('initial_seed','weekly_reforecast','manual','single_question')),
    triggered_at                TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    triggered_by                TEXT            NOT NULL,    -- 'cloud_scheduler' or operator email
    questions_dispatched        INTEGER,
    forecasts_completed         INTEGER,
    forecasts_failed            INTEGER,
    finished_at                 TIMESTAMPTZ,                 -- NULL until harvester closes the run
    run_metadata                JSONB
);
CREATE INDEX idx_runs_triggered_at ON calibration_runs(triggered_at DESC);
```

**Reasoning:** Operational/audit table. Lets the operator see "this week's run dispatched 25 questions, 23 completed, 1 failed, 1 timed out". A run is `finished` when every dispatched forecast is in a terminal state (`completed`/`failed`/`timed_out`) — closed by the harvester when it walks the final pending row.

### Table 5: `calibration_metrics_snapshots`

```sql
CREATE TABLE calibration_metrics_snapshots (
    id                          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_at                 TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    metric_type                 TEXT            NOT NULL CHECK (metric_type IN
                                                ('aggregate_brier','calibration_curve','cohort_brier','improvement_curve','source_contribution')),
    cohort                      TEXT            CHECK (cohort IS NULL OR cohort IN ('7d','14d','30-45d','all')),
    payload                     JSONB           NOT NULL
);
CREATE INDEX idx_metrics_type_at ON calibration_metrics_snapshots(metric_type, snapshot_at DESC);
```

**Reasoning:** Snapshots are written every time a question resolves (snapshot represents "as-of this resolution event") *and* on a daily Cloud Scheduler trigger as a low-cost audit point. `payload` shape varies by `metric_type` and is documented in [calibration/metrics/snapshots.py](data-pipeline/calibration/metrics/snapshots.py). The improvement curve is one row per resolution event with `payload` carrying `{original_brier, latest_brier, delta, agent_version_pair, cohort}`. UI plots improvement_curve rows over time.

### Cross-table integrity rules (enforced in Python, not SQL)

- A forecast row's `forecast_run_index = 0` must correspond to a `calibration_runs` row of `run_type IN ('initial_seed', 'manual')`. Re-forecasts (`run_type='weekly_reforecast'`) get `forecast_run_index >= 1`.
- A question with `status='resolved'` must have exactly one row in `calibration_resolutions`.
- Brier scores on forecasts get backfilled (transaction) immediately after a resolution row is inserted.

---

## Phase 10 Gate Model

Phase 10 is partially data-pipeline-shaped (Postgres-backed) and partially hub-shaped (Firestore round-trips). The four-gate model maps:

| Gate | Meaning in Phase 10 |
|---|---|
| **Gate 1** | Module imports cleanly, constants/config validated, schemas (Pydantic for API shapes) pass instantiation, SQL DDL runs without error against an empty Postgres. |
| **Gate 2** | Pure-function logic with mocks: dispatch payload construction, harvest projection logic (E5 extraction), Brier math, calibration-curve bucketing, cohort aggregation, source-contribution rollup. Mocked Postgres (sqlite or testcontainer Postgres), mocked Firestore admin client. |
| **Gate 3** | Round-trip: full dispatch → Firestore emulator → harvest → Postgres → metrics computation, asserting end-state matches expectations. Resolution polling against a recorded Polymarket API fixture (no live HTTP). |
| **E2E** | Real environment: real Cloud Run, real Cloud SQL, real `anizai-ai` Firestore, real Polymarket public API, real cloud-deployed agent. One full weekly cycle observed unattended. |

Each task is tagged with which gate(s) it must pass before being marked `[x]`.

---

## Phase 10A — Foundation: Schema + Polymarket Adapter

### Sprint scope

Land the Postgres schema, the Polymarket adapter (auto-selection + resolution polling), the manual-add path, and a CLI that exercises the full data flow without involving the agent or Firestore. End of sprint: `python -m calibration.cli seed` populates 25–30 questions; `python -m calibration.cli resolve` correctly flips a known-resolved test market.

### Confirmed design decisions

- All A1, A4, A5, B1–B4, C1–C4, D1–D4 from the Settled Design Decisions table apply.
- All Phase 10A code lives under `data-pipeline/calibration/`. No imports from `agent/`, `processing/`, `persistence/`, or `ingestion/`. The only cross-package import allowed is `config/settings.py` (for shared infra constants).
- Calibration uses its own dedicated Postgres connection (`CALIBRATION_DATABASE_URL`); does **not** reuse the pipeline `POSTGRES_HOST` constants. Phase 10D wires this to Cloud SQL Auth Proxy; for 10A–10C the operator points it at a local Postgres or Cloud SQL instance via `pg_proxy` for development.
- Polymarket API calls reuse the `GAMMA_API_BASE` and `CLOB_API_BASE` constants (lifted to a shared location in [calibration/polymarket/constants.py](data-pipeline/calibration/polymarket/constants.py); copy-not-import to avoid pulling in `ingestion/`'s asyncio/kafka deps).

### Task table

| Task | Description | Gate(s) | Files / Refs |
|---|---|---|---|
| T10A.1 | Provision Cloud SQL Postgres 16 instance `anizai-calibration-db` (db-f1-micro, 10 GB, single zone). Create DB `anizai_calibration` and user `calibration_app`. Store password in Secret Manager. **Gates T10A.2 — no schema work until DB is reachable from operator workstation via Cloud SQL Auth Proxy.** | — | gcloud script `infrastructure/gcp/c10_create_calibration_db.sh` |
| T10A.2 | Author `calibration/sql/init.sql` — full DDL for the 5 tables + indexes from the schema proposal above. Apply to dev DB. | Gate 1 | [calibration/sql/init.sql](data-pipeline/calibration/sql/init.sql) (new) |
| T10A.3 | Implement `calibration/db.py` — connection pool wrapper (psycopg3 async), reads `CALIBRATION_DATABASE_URL`. Mirrors the pattern of [persistence/db.py](data-pipeline/persistence/db.py) but isolated. | Gate 1 | [calibration/db.py](data-pipeline/calibration/db.py) (new) |
| T10A.4 | Implement `calibration/models.py` — Pydantic models matching the 5 tables (Question, Forecast, Resolution, Run, MetricsSnapshot). Used by all downstream code as the typed boundary. | Gate 1 | [calibration/models.py](data-pipeline/calibration/models.py) (new) |
| T10A.5 | Implement `calibration/repos/questions.py`, `forecasts.py`, `resolutions.py`, `runs.py`, `metrics.py` — one repository module per table with `insert`, `get_by_id`, `list`, plus table-specific queries (e.g., `list_open_by_cohort`, `mark_resolved`). All async, all return Pydantic models. | Gate 1, Gate 2 | `calibration/repos/*.py` (new, 5 files) |
| T10A.6 | Implement `calibration/polymarket/discover.py` — auto-selection logic. Calls Gamma API for active markets; filters by liquidity per C1; maps tags to category per C4; bins by cohort window per C1; returns a list of candidate `Question` records. **No DB writes in this module** — pure function. | Gate 1, Gate 2 | [calibration/polymarket/discover.py](data-pipeline/calibration/polymarket/discover.py) (new) |
| T10A.7 | Implement `calibration/polymarket/taxonomy.py` — JSON-driven Polymarket tag → internal-category mapping (C4). Includes the seed allowlist (`Politics`, `Geopolitics`, `Macroeconomy`, `AI`, `Tech`) and blocklist (`Sports`, `Entertainment`, `Crypto Prices`). | Gate 1 | [calibration/polymarket/taxonomy.py](data-pipeline/calibration/polymarket/taxonomy.py) + [calibration/polymarket/taxonomy.json](data-pipeline/calibration/polymarket/taxonomy.json) (new) |
| T10A.8 | Implement `calibration/polymarket/resolve.py` — resolution detection. Calls `GET /markets/{condition_id}` per D1, maps to `(YES/NO/AMBIGUOUS, outcome_numeric, raw_data)`. Single function, no DB writes. | Gate 1, Gate 2 | [calibration/polymarket/resolve.py](data-pipeline/calibration/polymarket/resolve.py) (new) |
| T10A.9 | Implement `calibration/services/discovery_service.py` — orchestrates: discover candidates → diff against existing open questions → top up to target count → insert new rows with `status='open'`. Captures `liquidity_usd_at_pickup`. **Idempotent**: re-running doesn't insert duplicates (UNIQUE constraint on `condition_id`). | Gate 2 | [calibration/services/discovery_service.py](data-pipeline/calibration/services/discovery_service.py) (new) |
| T10A.10 | Implement `calibration/services/resolution_service.py` — orchestrates: list open questions whose `expected_resolution_date <= today + 2d`, poll each, insert into `calibration_resolutions` on hit (`ON CONFLICT DO NOTHING`), flip `calibration_questions.status` to `resolved`. Bulk operation, single transaction per question. | Gate 2, Gate 3 | [calibration/services/resolution_service.py](data-pipeline/calibration/services/resolution_service.py) (new) |
| T10A.11 | Implement `calibration/services/manual_add_service.py` — validates manual question payload, fetches market metadata from Polymarket to populate `polymarket_condition_id` + `expected_resolution_date` from the slug, inserts. | Gate 2 | [calibration/services/manual_add_service.py](data-pipeline/calibration/services/manual_add_service.py) (new) |
| T10A.12 | Implement `calibration/cli.py` — Click-based CLI with subcommands: `seed`, `discover`, `resolve`, `add-manual`, `list-questions`, `wipe-dev`. Each subcommand is a thin wrapper around a service. | Gate 1 | [calibration/cli.py](data-pipeline/calibration/cli.py) (new) |
| T10A.13 | Phase 10A env vars in `calibration/config.py`: `CALIBRATION_DATABASE_URL`, `CALIBRATION_TARGET_COUNT_7D=10`, `CALIBRATION_TARGET_COUNT_14D=10`, `CALIBRATION_TARGET_COUNT_30_45D=8`, `CALIBRATION_LIQUIDITY_MIN_7_14D_USD=50000`, `CALIBRATION_LIQUIDITY_MIN_30_45D_USD=25000`, `POLYMARKET_GAMMA_API`, `POLYMARKET_CLOB_API`. | — | [calibration/config.py](data-pipeline/calibration/config.py) (new) |
| T10A.14 | Gate 1 unit tests for `models.py`, `db.py` (connection lifecycle), `polymarket/taxonomy.py` (tag mapping). | Gate 1 | `tests/test_calibration/test_models.py`, `test_polymarket_taxonomy.py` (new) |
| T10A.15 | Gate 2 unit tests for `discover.py` (mocked Gamma API), `resolve.py` (mocked CLOB responses for YES/NO/AMBIGUOUS/still-open), `discovery_service.py` (mocked Postgres via testcontainer), `resolution_service.py`. | Gate 2 | `tests/test_calibration/test_discover.py`, `test_resolve.py`, `test_discovery_service.py`, `test_resolution_service.py` (new) |
| T10A.16 | Gate 3 integration test: `tests/test_calibration/test_phase_10a_e2e.py` — spins up testcontainer Postgres, applies init.sql, runs the full `seed` CLI flow against a recorded Gamma API fixture, asserts ~25 questions across 3 cohorts. Then runs `resolve` against a fixture for one already-resolved market, asserts the resolution row + question status flip. | Gate 3 | `tests/test_calibration/test_phase_10a_e2e.py` (new) |
| T10A.17 | E2E (operator-driven): point CLI at the live Cloud SQL instance and live Polymarket API. Run `seed` and `list-questions`. Capture before/after counts in commit message. | E2E | live Cloud SQL + Polymarket |
| T10A.18 | Update [data-pipeline/task_plan.md](data-pipeline/task_plan.md) — open Phase 10 section with sprint table, mark Phase 10A row active. | — | `task_plan.md` |

### Constants introduced (Phase 10A)

- `POLYMARKET_GAMMA_API = "https://gamma-api.polymarket.com"` (mirror of [ingestion/polymarket_producer.py:53](data-pipeline/ingestion/polymarket_producer.py#L53))
- `POLYMARKET_CLOB_API = "https://clob.polymarket.com"`
- `CALIBRATION_TARGET_COUNT_7D = 10` (env-overridable)
- `CALIBRATION_TARGET_COUNT_14D = 10`
- `CALIBRATION_TARGET_COUNT_30_45D = 8`
- `CALIBRATION_LIQUIDITY_MIN_7_14D_USD = 50000`
- `CALIBRATION_LIQUIDITY_MIN_30_45D_USD = 25000`
- Cohort window definitions in `polymarket/discover.py`: `(5, 9)`, `(12, 16)`, `(28, 46)` days

### Acceptance criteria — Phase 10A

- All Gate 1 + Gate 2 unit tests pass.
- Gate 3 E2E test passes against testcontainer Postgres + recorded Polymarket fixture.
- Operator-driven E2E: CLI seeds 25–30 questions against live Cloud SQL + live Polymarket API.
- One known-resolved Polymarket market correctly flipped to `resolved` via `resolve` command.
- `task_plan.md` opened with Phase 10 section; Phase 10A row marked active.

---

## Phase 10B — Forecast Engine Bridge: Dispatch + Harvest

### Sprint scope

Wire the calibration system to the existing agent. Build the dispatcher (writes `forecastQueries` docs), the harvester (reads `sessionResults` + the `evidence` subcollection, applies the E5 projection, writes `calibration_forecasts` rows). End of sprint: a `python -m calibration.cli dispatch-one --question-id <id>` round-trips through the live agent and persists a complete forecast row with `agent_version`, `final_probability`, `agent_evidence_summary`.

### Confirmed design decisions

- A4 + A5 are the load-bearing constraints. The dispatcher writes a forecastQueries doc with the contract from §3.4 (idempotency key) of the consolidated handoff doc, plus the two custom side fields (`calibrationRunId`, `calibrationQuestionId`). The agent ignores these side fields.
- Harvester reads forecastQueries via `where calibrationQuestionId IS NOT NULL` filter, follows to sessionResults via the sessionId, then to the evidence subcollection. **All reads are read-only on Firestore.**
- Harvester is non-listening — it polls (A3). Polling cadence: every 5 minutes via `/tasks/harvest` in Phase 10D; in Phase 10B it's run on demand from the CLI.
- Failed sessions (sessionResults.status='failed' or status='error') are recorded with `calibration_forecasts.status='failed'` and `error_message` populated. They do not block resolution scoring later (the row is just excluded from Brier aggregations).
- Timeout handling: if a forecast has been in `dispatched` status for more than `CALIBRATION_DISPATCH_TIMEOUT_MIN=120` minutes, harvester flips it to `timed_out` and the run summary records the failure.
- `agent_evidence_summary` is computed at harvest time using the projection in E5. The projection lives in `calibration/evidence_projection.py`, has a versioned schema, and is unit-tested against fixtures captured from real SessionResult docs.

### Task table

| Task | Description | Gate(s) | Files / Refs |
|---|---|---|---|
| T10B.1 | Implement `calibration/firestore_client.py` — Admin SDK wrapper specific to calibration. Cross-project init using the same `_EmulatorCredentials` pattern Phase 8A established (for emulator tests). Functions: `init_app(project_id)`, `get_db()`, `write_forecast_query(payload)`, `read_session_result(session_id)`, `read_evidence_subcollection(session_id)`. **No claim/transaction logic** — calibration is read-only on the agent's flow except for the initial forecastQueries write. | Gate 1 | [calibration/firestore_client.py](data-pipeline/calibration/firestore_client.py) (new) |
| T10B.2 | Implement `calibration/services/dispatch_service.py` — orchestrates: pick open questions to dispatch (input list of question_ids); for each, generate idempotency_key (uuid4), construct forecastQueries payload `{question, idempotencyKey, calibrationRunId, calibrationQuestionId}`, write to Firestore, insert `calibration_forecasts` row with status='dispatched'. Bulk-safe (per-question try/except — one failure doesn't block others). Records summary on `calibration_runs`. | Gate 1, Gate 2 | [calibration/services/dispatch_service.py](data-pipeline/calibration/services/dispatch_service.py) (new) |
| T10B.3 | Implement `calibration/evidence_projection.py` — E5 projection. Pure function `project_evidence(session_result_doc, evidence_docs) -> dict`. Handles missing fields gracefully (an evidence doc may omit `sourceType` for an early-version forecast). Returns the projected JSON for `agent_evidence_summary`. | Gate 1, Gate 2 | [calibration/evidence_projection.py](data-pipeline/calibration/evidence_projection.py) (new) |
| T10B.4 | Implement `calibration/services/harvest_service.py` — orchestrates: list `calibration_forecasts` rows in `dispatched` state; for each, read its forecastQueries doc + (if present) sessionResults + evidence subcollection; if sessionResults reports `status='done'`, project evidence, populate the forecast row, set `status='completed'`; if `status='failed'`, set `status='failed'` + `error_message`; if older than the timeout, set `status='timed_out'`. Closes runs whose dispatched count == terminal count. | Gate 1, Gate 2, Gate 3 | [calibration/services/harvest_service.py](data-pipeline/calibration/services/harvest_service.py) (new) |
| T10B.5 | Extend `calibration/cli.py` with `dispatch-one`, `dispatch-all-open`, `harvest-pending`, `show-forecast --question-id <id>`. | Gate 1 | [calibration/cli.py](data-pipeline/calibration/cli.py) |
| T10B.6 | Add Phase 10B env vars: `FIREBASE_PROJECT_ID=anizai-ai`, `CALIBRATION_DISPATCH_TIMEOUT_MIN=120`. | — | [calibration/config.py](data-pipeline/calibration/config.py) |
| T10B.7 | Gate 1 unit tests for `firestore_client.py` (mocked Admin SDK), `evidence_projection.py` (fixture-driven from real SessionResult dumps captured during Sprint 20 E2E). | Gate 1 | `tests/test_calibration/test_firestore_client.py`, `test_evidence_projection.py` (new) |
| T10B.8 | Gate 2 unit tests for `dispatch_service.py` (mocked Firestore + testcontainer Postgres) and `harvest_service.py` (mocked Firestore reads against three fixture sessions: completed, failed, in-progress). | Gate 2 | `tests/test_calibration/test_dispatch_service.py`, `test_harvest_service.py` (new) |
| T10B.9 | Gate 3 integration test: against Firebase emulator, run `dispatch-one` on a seeded question, simulate the agent (test fixture writing the SessionResult + evidence subcoll into the emulator), run `harvest-pending`, assert the forecast row is `completed` with all fields populated. | Gate 3 | `tests/test_calibration/test_phase_10b_e2e.py` (new) |
| T10B.10 | E2E (operator-driven): against the live cloud-deployed agent + real `anizai-ai` Firestore + Cloud SQL: dispatch one question, wait, harvest, inspect the row. Capture round-trip latency (expected: 30–60s end-to-end including agent processing). | E2E | live cloud agent + Firestore + Cloud SQL |
| T10B.11 | Update `task_plan.md` — close Phase 10A row, open Phase 10B row. | — | `task_plan.md` |

### Constants introduced (Phase 10B)

- `CALIBRATION_DISPATCH_TIMEOUT_MIN = 120` — after this many minutes in `dispatched` state, a forecast is flagged `timed_out`.
- `EVIDENCE_PROJECTION_VERSION = "1.0"` — written into every `agent_evidence_summary` payload so future schema changes can be detected and back-projected if needed.

### Error-handling paths (Phase 10B)

| Failure | Handling |
|---|---|
| Firestore write fails on dispatch | Per-question try/except. Forecast row not inserted. Run summary records the count. Question stays `open` and gets re-attempted next cycle. |
| sessionResults missing or malformed at harvest time | Forecast row stays `dispatched` until either sessionResults appears or the timeout expires. Logged at INFO. |
| sessionResults reports `status='failed'` | Forecast row → `status='failed'`; `error_message` copied from `errorMessage` field on the session doc. Excluded from Brier aggregations. |
| Evidence subcollection missing or empty | Projection returns `{evidence_count_total: 0, ...}` — does not fail the harvest. Logged at WARN. |
| Polymarket condition_id changed since pickup | Out of scope V1. Logged as a known gap; manual operator triage. |

### Acceptance criteria — Phase 10B

- All Gate 1, Gate 2 tests pass.
- Gate 3 E2E test passes against Firebase emulator.
- Operator-driven E2E: one question dispatched → completed forecast row in Cloud SQL with `agent_version` ≥ 0.4.0 (current cloud agent version), `final_probability` ∈ [0,1], non-empty `agent_evidence_summary`.

---

## Phase 10C — Scoring & Metrics Layer

### Sprint scope

Compute Brier scores on resolution events; compute and snapshot the calibration curve, per-cohort Brier, and source-contribution rollups. End of sprint: `python -m calibration.cli compute-metrics` generates a snapshot row for every metric type and prints a human-readable summary.

### Confirmed design decisions

- All E1–E5, F1–F3 from the Settled Design Decisions table apply.
- Brier computation is **transactional with resolution insertion** — when `resolution_service` writes a row, in the same transaction it backfills `brier_score` on every existing forecast for that question.
- Aggregations (calibration curve, cohort, source contribution, improvement curve) are **stateless pure functions** that read from Postgres and return Pydantic models. Snapshots are persisted on every resolution event + on a daily Cloud Scheduler trigger.
- Improvement-delta semantics: per question, take the forecast with the lowest `forecast_run_index` (call it `original`) and the one with the highest (`latest`). Delta = `original.brier_score - latest.brier_score`. Aggregated per cohort + per `(original.agent_version, latest.agent_version)` pair.
- Source-contribution rollup: for each `vault_type ∈ {knowledge, social, momentum, mapping, reactive_search}`, compute mean Brier when that vault type appears in `agent_evidence_summary.vault_types_present` vs. when it doesn't. Reported as a single delta plus a count for statistical context.
- Ambiguous resolutions are excluded from every aggregation. They appear in raw lists but do not influence Brier means.

### Task table

| Task | Description | Gate(s) | Files / Refs |
|---|---|---|---|
| T10C.1 | Implement `calibration/metrics/brier.py` — pure function `compute_brier(probability: float, outcome_numeric: float) -> float`. Plus `backfill_brier_for_question(question_id, conn)` that runs inside a transaction and updates all forecast rows. | Gate 1 | [calibration/metrics/brier.py](data-pipeline/calibration/metrics/brier.py) (new) |
| T10C.2 | Update `services/resolution_service.py` from Phase 10A — when inserting a resolution, in the same transaction call `backfill_brier_for_question`. | Gate 2 | [calibration/services/resolution_service.py](data-pipeline/calibration/services/resolution_service.py) |
| T10C.3 | Implement `calibration/metrics/calibration_curve.py` — pure function over forecast/resolution rows. Returns 5 bucket points `[{bucket, count, mean_predicted, actual_yes_rate, lower_bound, upper_bound}, ...]`. Wilson-score 95% CIs for actual rates (cosmetic — useful in the UI). | Gate 1, Gate 2 | [calibration/metrics/calibration_curve.py](data-pipeline/calibration/metrics/calibration_curve.py) (new) |
| T10C.4 | Implement `calibration/metrics/cohort_brier.py` — mean Brier per cohort + an `all` aggregate. Returns `{cohort, n, mean_brier, std_brier}`. | Gate 1, Gate 2 | [calibration/metrics/cohort_brier.py](data-pipeline/calibration/metrics/cohort_brier.py) (new) |
| T10C.5 | Implement `calibration/metrics/improvement_curve.py` — for each resolved question, compute (original_brier, latest_brier, delta, cohort, agent_version_pair). Aggregated as a series ordered by resolution date. | Gate 1, Gate 2 | [calibration/metrics/improvement_curve.py](data-pipeline/calibration/metrics/improvement_curve.py) (new) |
| T10C.6 | Implement `calibration/metrics/source_contribution.py` — per vault type, compute mean Brier with vs. without that type, plus counts. | Gate 1, Gate 2 | [calibration/metrics/source_contribution.py](data-pipeline/calibration/metrics/source_contribution.py) (new) |
| T10C.7 | Implement `calibration/metrics/snapshots.py` — orchestrator. Computes every metric type, persists each as a `calibration_metrics_snapshots` row. Called from resolution_service after every resolution + standalone via CLI / Cloud Scheduler. | Gate 2 | [calibration/metrics/snapshots.py](data-pipeline/calibration/metrics/snapshots.py) (new) |
| T10C.8 | Extend CLI: `compute-metrics`, `show-curve`, `show-cohort-brier`, `show-improvement`. | Gate 1 | [calibration/cli.py](data-pipeline/calibration/cli.py) |
| T10C.9 | Gate 1 unit tests: Brier math (5 cases including 0.0 and 1.0 edges), bucket assignment edge cases (exactly 0.2, exactly 0.8), Wilson interval correctness on 0/0 and 1/1 cases. | Gate 1 | `tests/test_calibration/test_brier.py`, `test_calibration_curve.py` (new) |
| T10C.10 | Gate 2 integration tests: against testcontainer Postgres pre-seeded with synthetic forecast/resolution data, verify each aggregation produces the expected snapshot payload. | Gate 2 | `tests/test_calibration/test_metrics_integration.py` (new) |
| T10C.11 | Gate 3 end-to-end (using the synthetic fixture from Phase 10B): dispatch fixture → harvest fixture → fake-resolve fixture → compute_metrics → assert snapshot rows of all 5 types exist with valid payloads. | Gate 3 | `tests/test_calibration/test_phase_10c_e2e.py` (new) |
| T10C.12 | Update `task_plan.md` — close Phase 10B row, open Phase 10C row. | — | `task_plan.md` |

### Acceptance criteria — Phase 10C

- All Gate 1, Gate 2, Gate 3 tests pass.
- CLI commands print readable summaries (calibration curve as ASCII table, cohort Brier, improvement series).
- One synthetic full cycle (3 questions × 2 forecasts each × resolution) produces all 5 metric snapshot rows with non-trivial payloads.

---

## Phase 10D — Cloud Automation: Cloud Run + Cloud Scheduler

### Sprint scope

Containerize. Deploy. Wire up Cloud Scheduler. End of sprint: a single Cloud Run service runs all four scheduled tasks unattended; one full unattended weekly cycle (discover → dispatch → harvest → resolve → compute_metrics) completes successfully.

### Confirmed design decisions

- All G1–G7 from the Settled Design Decisions table apply.
- **Hard prerequisite**: Phase 9 (Cloud Deployment) closed — cloud agent reachable. Phase 9 closed 2026-05-10, so this prerequisite is satisfied.
- FastAPI is the web framework. One `main.py`, four task endpoints, `/api/*` stubs (real implementations land in 10E), `/healthz`.
- Cloud Scheduler invokes endpoints via OIDC tokens. The Cloud Run service's invoker IAM is restricted to two principals: the calibration-runner GSA itself (for self-invocation if needed) and the cloud-scheduler GSA.
- Operator can manually trigger any `/tasks/*` endpoint via `gcloud run services proxy` + curl with an OIDC token (documented in [calibration/docs/OPERATOR_RUNBOOK.md](data-pipeline/calibration/docs/OPERATOR_RUNBOOK.md)).

### Task table

| Task | Description | Gate(s) | Files / Refs |
|---|---|---|---|
| T10D.1 | Implement `calibration/server.py` — FastAPI app. Mounts `/healthz`, `/tasks/discover`, `/tasks/dispatch`, `/tasks/harvest`, `/tasks/resolve`, `/tasks/snapshot_metrics`. Each task endpoint is a thin wrapper over the corresponding service from 10A-10C. Returns `{status: "ok", summary: {...}}`. | Gate 1, Gate 2 | [calibration/server.py](data-pipeline/calibration/server.py) (new) |
| T10D.2 | Implement OIDC verification middleware on `/tasks/*` — verifies the request bears a valid Cloud Scheduler service-account token. | Gate 1, Gate 2 | [calibration/auth.py](data-pipeline/calibration/auth.py) (new) |
| T10D.3 | Author [data-pipeline/infrastructure/Dockerfile.calibration](data-pipeline/infrastructure/Dockerfile.calibration). Python 3.11 slim, installs from `requirements.lock`, runs `uvicorn calibration.server:app`. | Gate 1 | `infrastructure/Dockerfile.calibration` (new) |
| T10D.4 | Build + push image to Artifact Registry. Tag `:0.1.0` and `:latest`. | — | gcloud script |
| T10D.5 | Provision GSA `calibration-runner@anizai-pipeline.iam.gserviceaccount.com`. Grant: `roles/cloudsql.client` on the calibration DB; `roles/secretmanager.secretAccessor` (project-level, mirroring the Phase 9A pattern); `roles/datastore.user` on `anizai-ai` (cross-project). | — | gcloud script `infrastructure/gcp/c10_create_calibration_gsa.sh` |
| T10D.6 | Deploy Cloud Run service `calibration-runner` (us-central1, min=0, max=2, concurrency=20, --add-cloudsql-instances, --service-account=calibration-runner@..., env vars from Secret Manager per G4). | Gate 3 | gcloud script `infrastructure/gcp/c10_deploy_cloud_run.sh` |
| T10D.7 | Provision Cloud Scheduler GSA `calibration-scheduler@anizai-pipeline...`. Grant `roles/run.invoker` scoped to the calibration-runner service. | — | gcloud script |
| T10D.8 | Create 4 Cloud Scheduler jobs: `calibration-discover-hourly` (`0 * * * *`); `calibration-harvest-5min` (`*/5 * * * *`); `calibration-resolve-hourly` (`15 * * * *`); `calibration-weekly-reforecast` (`0 2 * * 0`). All UTC. All targeting the corresponding `/tasks/*` endpoint with OIDC. | Gate 3 | gcloud script `infrastructure/gcp/c10_create_schedulers.sh` |
| T10D.9 | Author [calibration/docs/OPERATOR_RUNBOOK.md](data-pipeline/calibration/docs/OPERATOR_RUNBOOK.md) — how to manually trigger each task, how to read the logs, how to inspect the DB, how to roll back a deploy. | — | `calibration/docs/OPERATOR_RUNBOOK.md` (new) |
| T10D.10 | Gate 1: `pytest` against the FastAPI app via TestClient. Each `/tasks/*` endpoint returns 200 on a happy path with mocked services, 401 without OIDC. | Gate 1 | `tests/test_calibration/test_server.py` (new) |
| T10D.11 | Gate 2: integration test, FastAPI app + testcontainer Postgres + emulator Firestore. Drive the four task endpoints in sequence, assert state changes. | Gate 2 | `tests/test_calibration/test_server_integration.py` (new) |
| T10D.12 | Gate 3: deploy to staging Cloud Run revision, manually invoke each Scheduler job (one-shot), verify logs + DB state. | Gate 3 | live staging |
| T10D.13 | E2E: let the system run unattended for a full week. Capture: count of forecasts dispatched, count harvested, count resolved (if any), count of metric snapshots written. Cost report from Billing. | E2E | live cloud |
| T10D.14 | Update `task_plan.md` — close Phase 10C row, open Phase 10D row. | — | `task_plan.md` |

### Cloud Run / Cloud Scheduler endpoint contract (Phase 10D)

| Endpoint | Method | Caller | Auth | Purpose | Inputs | Outputs |
|---|---|---|---|---|---|---|
| `/healthz` | GET | Cloud Run probes | None | Liveness | — | `{"status":"ok","commit":"...","db":"ok","firestore":"ok"}` |
| `/tasks/discover` | POST | Cloud Scheduler `calibration-discover-hourly` | OIDC (calibration-scheduler GSA) | Run discovery_service | `{}` | `{"discovered":N,"already_present":M,"target_count":T}` |
| `/tasks/dispatch` | POST | Cloud Scheduler `calibration-weekly-reforecast` | OIDC | Run dispatch_service for all `status='open'` questions | `{"run_type":"weekly_reforecast"}` | `{"run_id":"<uuid>","questions_dispatched":N}` |
| `/tasks/harvest` | POST | Cloud Scheduler `calibration-harvest-5min` | OIDC | Run harvest_service over all `dispatched` rows | `{}` | `{"completed":N,"failed":F,"timed_out":T,"still_pending":P}` |
| `/tasks/resolve` | POST | Cloud Scheduler `calibration-resolve-hourly` | OIDC | Run resolution_service + cascade Brier backfill + snapshot | `{}` | `{"resolved":N,"snapshots_written":S}` |
| `/tasks/snapshot_metrics` | POST | Cloud Scheduler daily (added 10D) | OIDC | Compute & snapshot all metric types regardless of resolution event | `{}` | `{"snapshots_written":5}` |
| `/api/*` | various | UI (browser) | Firebase Auth ID token | Stubbed in 10D, real in 10E | — | — |

### Acceptance criteria — Phase 10D

- All Gate 1, Gate 2, Gate 3 tests pass.
- Cloud Run service deployed and healthy (`/healthz` returns 200).
- All 5 Cloud Scheduler jobs created and successfully invoking endpoints.
- E2E unattended week: at least one weekly reforecast cycle runs end-to-end without operator intervention.
- Total observed cost for the week is within ±25% of the $0.90 forecast estimate (forecasts only, excluding Cloud Run/Scheduler/Cloud SQL fixed costs).

---

## Phase 10E — Operator API + UI Contract

### Sprint scope

Implement the read-mostly `/api/*` endpoints the React UI needs. Hand off the React component contract doc to the UI collaborator. End of sprint: UI collaborator can build the dashboard against a live, documented API.

### Confirmed design decisions

- API is read-mostly. Only POST endpoints are `/api/questions` (manual add) and `/api/runs/trigger` (manual one-off cycle for dev/debug).
- All `/api/*` endpoints require Firebase Auth ID tokens. Verify token signature + check email against `FIREBASE_AUTH_OPERATOR_EMAILS` allowlist.
- API serves JSON only; no rendering. UI is a separate Vite/React app deployed to Firebase Hosting (separate site from the user-facing frontend, hosted on the **same `anizai-ai` Firebase project** for auth simplicity, distinct hosting target named `calibration` per G6).
- API responses are paginated where it matters (questions list, forecasts list).
- The UI collaborator implements the React app; this sprint only delivers the contract.

### Task table

| Task | Description | Gate(s) | Files / Refs |
|---|---|---|---|
| T10E.1 | Implement Firebase Auth verification middleware on `/api/*` — verifies the ID token, checks email against `FIREBASE_AUTH_OPERATOR_EMAILS` allowlist (initial: `ronking79@gmail.com`; UI collaborator email added when known), attaches `request.state.operator_email`. | Gate 1, Gate 2 | [calibration/auth.py](data-pipeline/calibration/auth.py) (extend) |
| T10E.2 | Implement `/api/questions` GET (list with filters: status, cohort, category) and POST (manual add). | Gate 1, Gate 2 | `calibration/server.py` |
| T10E.3 | Implement `/api/questions/{id}` GET — full question detail including all forecasts and (if present) resolution. | Gate 1, Gate 2 | `calibration/server.py` |
| T10E.4 | Implement `/api/questions/{id}/forecasts/compare` GET — original vs. latest forecast with deltas. | Gate 1, Gate 2 | `calibration/server.py` |
| T10E.5 | Implement `/api/metrics/calibration_curve` GET — latest snapshot or computed live (query param `live=true`). | Gate 1, Gate 2 | `calibration/server.py` |
| T10E.6 | Implement `/api/metrics/cohort_brier` GET — same pattern. | Gate 1, Gate 2 | `calibration/server.py` |
| T10E.7 | Implement `/api/metrics/improvement_curve` GET — time-series of resolution events with deltas. | Gate 1, Gate 2 | `calibration/server.py` |
| T10E.8 | Implement `/api/metrics/source_contribution` GET. | Gate 1, Gate 2 | `calibration/server.py` |
| T10E.9 | Implement `/api/runs` GET (list of runs with status/counts) and `/api/runs/trigger` POST (operator-triggered manual cycle for debugging). | Gate 1, Gate 2 | `calibration/server.py` |
| T10E.10 | Author [calibration/docs/UI_CONTRACT.md](data-pipeline/calibration/docs/UI_CONTRACT.md) — full OpenAPI-style contract per endpoint (request/response shapes, auth requirements, error codes), plus the React component list (see below). | — | `calibration/docs/UI_CONTRACT.md` (new) |
| T10E.11 | Author [calibration/docs/UI_DEPLOY.md](data-pipeline/calibration/docs/UI_DEPLOY.md) — Firebase Hosting deploy steps for the UI collaborator: create hosting target `calibration` on the `anizai-ai` project, configure `firebase.json` with rewrites, deploy command, env vars (API base URL, Firebase config). | — | `calibration/docs/UI_DEPLOY.md` (new) |
| T10E.12 | Gate 1: per-endpoint TestClient tests (200 + 401 + 403 + 404 cases). | Gate 1 | `tests/test_calibration/test_api.py` (new) |
| T10E.13 | Gate 2: full operator workflow integration test — seed fixture data, simulate three operator actions (list questions, manually add one, view metrics), assert response shapes. | Gate 2 | `tests/test_calibration/test_api_workflow.py` (new) |
| T10E.14 | Gate 3: hand-off integration test the UI collaborator can run — `pytest tests/test_calibration/test_ui_contract.py` validates the live deployed Cloud Run against the published OpenAPI contract using a recorded operator ID token. | Gate 3 | `tests/test_calibration/test_ui_contract.py` (new) |
| T10E.15 | E2E: UI collaborator builds skeleton React app pointing at the deployed API, verifies all three primary visualizations render against real data. (Joint with collaborator.) | E2E | live cloud + collaborator |
| T10E.16 | Update `task_plan.md` — close Phase 10D, close Phase 10E, archive Phase 10. | — | `task_plan.md` |

### UI Component List (for the React collaborator)

The UI collaborator builds a single-page React (Vite) app on Firebase Hosting (target `calibration` on the `anizai-ai` project, per G6). The app has these screens / components:

**1. App shell**
- `<Login>` — Firebase Auth email-link or Google sign-in.
- `<AppShell>` — top nav with three tabs: Questions | Metrics | Runs. Operator email + sign-out in corner.

**2. Questions tab**
- `<QuestionList>` — paginated table backed by `GET /api/questions?status=&cohort=&category=`. Columns: question_text, cohort, category, status, expected_resolution_date, latest forecast probability, latest Brier, added_by. Click row → `<QuestionDetail>`.
- `<ManualAddForm>` — POST `/api/questions`. Fields: question_text, polymarket_slug, category, cohort. Other fields auto-populated by API.
- `<QuestionDetail>` — header with question_text + Polymarket link; section showing all forecasts (table sorted by run_index); section for resolution (if present); if resolved, side-by-side "original vs latest forecast" cards with Brier deltas via `GET /api/questions/{id}/forecasts/compare`.

**3. Metrics tab (the core research surface)**
- `<CalibrationCurveChart>` — primary visualization. Diagonal reference line + 5 plotted bucket points with Wilson 95% bars. Backed by `GET /api/metrics/calibration_curve`. Optional cohort filter pill.
- `<CohortBrierBars>` — bar chart, one bar per cohort + an `all` bar. `GET /api/metrics/cohort_brier`.
- `<ImprovementCurveChart>` — time series, X = resolution date, Y = Brier delta (original − latest), color-coded by cohort. `GET /api/metrics/improvement_curve`. Tooltip shows agent_version_pair.
- `<SourceContributionTable>` — table of vault types × `(mean_brier_with, mean_brier_without, delta, count)`. `GET /api/metrics/source_contribution`.

**4. Runs tab (operations + audit)**
- `<RunsList>` — chronological list. Columns: triggered_at, run_type, dispatched, completed, failed, finished_at. Backed by `GET /api/runs`.
- `<TriggerRunButton>` — POST `/api/runs/trigger` for manual ad-hoc cycles (dev/debug only).

**5. Shared**
- `<ProbabilityBar>` — visual rendering of a 0–1 probability with optional comparison line.
- `<BrierBadge>` — colored badge (green ≤0.10, amber ≤0.25, red >0.25).
- `<ApiClient>` — typed fetch wrapper with Firebase ID token attachment.

### Acceptance criteria — Phase 10E

- All Gate 1, 2, 3 tests pass.
- API endpoints documented in `UI_CONTRACT.md` matching live behavior.
- UI collaborator can build the skeleton and the core metrics views work against real data.
- Phase 10 closed in `task_plan.md`; Phase 10 archived.

---

## Cloud Run API Contract (consolidated)

**Auth modes:** `OIDC` = Cloud Scheduler / GSA-to-GSA. `FAUTH` = Firebase Auth ID token + operator-email allowlist. `NONE` = liveness probes only.

| Method | Path | Auth | Body | Response | Phase |
|---|---|---|---|---|---|
| GET  | `/healthz` | NONE | — | `{status, commit, db, firestore}` | 10D |
| POST | `/tasks/discover` | OIDC | `{}` | `{discovered, already_present, target_count}` | 10D |
| POST | `/tasks/dispatch` | OIDC | `{run_type}` | `{run_id, questions_dispatched}` | 10D |
| POST | `/tasks/harvest` | OIDC | `{}` | `{completed, failed, timed_out, still_pending}` | 10D |
| POST | `/tasks/resolve` | OIDC | `{}` | `{resolved, snapshots_written}` | 10D |
| POST | `/tasks/snapshot_metrics` | OIDC | `{}` | `{snapshots_written}` | 10D |
| GET  | `/api/questions` | FAUTH | — | `{items: Question[], page, total}` | 10E |
| POST | `/api/questions` | FAUTH | `{question_text, polymarket_slug, category, cohort}` | `Question` | 10E |
| GET  | `/api/questions/{id}` | FAUTH | — | `{question, forecasts: Forecast[], resolution: Resolution|null}` | 10E |
| GET  | `/api/questions/{id}/forecasts/compare` | FAUTH | — | `{original, latest, delta, agent_version_pair}` | 10E |
| GET  | `/api/metrics/calibration_curve` | FAUTH | — | `{snapshot_at, cohort, points: [...]}` | 10E |
| GET  | `/api/metrics/cohort_brier` | FAUTH | — | `{snapshot_at, items: [...]}` | 10E |
| GET  | `/api/metrics/improvement_curve` | FAUTH | — | `{points: [...]}` | 10E |
| GET  | `/api/metrics/source_contribution` | FAUTH | — | `{items: [...]}` | 10E |
| GET  | `/api/runs` | FAUTH | — | `{items: Run[]}` | 10E |
| POST | `/api/runs/trigger` | FAUTH | `{run_type: "manual"}` | `{run_id}` | 10E |

### Firestore interaction contract

The calibration-runner reads/writes Firestore on `anizai-ai` (cross-project, via Workload Identity):

**Writes (one type, one collection):**
```
forecastQueries/{queryId} (auto-id)
{
  question: string,                  // verbatim from calibration_questions.question_text
  idempotencyKey: string,            // calibration-side UUID4, also stored in calibration_forecasts
  status: "pending",                 // hub will claim
  createdAt: serverTimestamp,
  // calibration-side side fields (ignored by agent):
  calibrationRunId: string,
  calibrationQuestionId: string
}
```

**Reads:**
- `forecastQueries/{queryId}` — to detect if a dispatch was successfully written.
- `sessionResults/{sessionId}` — when the agent has produced a result (top-level per Phase 8B Sprint 20 D6).
- `sessions/{sessionId}/evidence/*` — the evidence subcollection per §5.2 of handoff doc.
- `sessions/{sessionId}` — to read `status` and `errorMessage` for failed sessions.

The calibration-runner **does not** read `forecastQueries` other than its own writes (it tracks its own `query_doc_id` in calibration_forecasts), and **does not** ever write to `sessionResults` or any session subcollection.

---

## Known Risks

1. **Polymarket API drift.** A schema change in Gamma or CLOB silently breaks discovery or resolution. *Mitigation:* `raw_resolution_data JSONB` preserves full audit trail; `evidence_projection.py` and `polymarket/resolve.py` have schema-version guards (`projection_version` field on outputs); fixture-driven Gate 3 tests catch most shape drift in CI.
2. **Slow agent → harvest timeouts.** If forecast latency regresses past the 120-min `CALIBRATION_DISPATCH_TIMEOUT_MIN`, lots of forecasts get marked `timed_out` and the weekly cycle reports degraded. *Mitigation:* timeout is env-overridable; harvester's `still_pending` count surfaces the issue early in Cloud Logging.
3. **Cross-project IAM friction.** Cloud Run on `anizai-pipeline` writing to Firestore on `anizai-ai` requires the same WI pattern Phase 9D established. Phase 9 closed cleanly 2026-05-10 so the pattern is proven; if a regression appears here, see Phase 9D's gcp-deployment runbook. *Mitigation:* 10D inherits the existing Workload Identity bindings.
4. **Cost overrun on bad weeks.** A failure mode where dispatch succeeds but harvest never sees results (Firestore index miss, agent crash loop) keeps re-dispatching the same questions. *Mitigation:* harvester closes runs when all rows are terminal; dispatcher refuses to dispatch a question that already has an active forecast for the current run_id. Budget alerts already armed in Phase 9A (₪200/₪400) catch billing surprises.
5. **Resolution detection lag.** Polymarket may show a market as "settled" hours after the actual outcome event. The hourly resolver may report resolutions a day late on slow markets, distorting "resolved within cohort" reports. *Mitigation:* not user-impacting; the Brier scores eventually become correct.
6. **Improvement-loop interpretation pitfall.** Improvement deltas are noisy at small N. Three resolved questions with bad luck could read as "agent regressed." *Mitigation:* UI surfaces `n` alongside every aggregate; Wilson intervals on calibration curves; per-cohort splits prevent lumping short and long horizons.
7. **Calibration questions could leak into the agent's training data via vault refresh.** If the agent's vault picks up news articles about a Polymarket market that calibration is forecasting, the forecast becomes self-referential. *Mitigation:* this is the actual behavior under test — the calibration system is *measuring whether the agent is well-calibrated using the data it actually has*. Documented as feature, not bug.

---

## Acceptance criteria — Phase 10 (V1 Done)

- [ ] Phase 10A merged: schema applied to Cloud SQL, ~25–30 questions seeded, resolution detection validated against a live resolved market.
- [ ] Phase 10B merged: end-to-end dispatch → harvest works against the live cloud agent.
- [ ] Phase 10C merged: scoring + 5 metric snapshot types written; CLI dumps human-readable summaries.
- [ ] Phase 10D merged: Cloud Run service deployed, all 5 Cloud Scheduler jobs active, one full unattended weekly cycle observed.
- [ ] Phase 10E merged: full operator API live + documented; UI collaborator successfully renders the three primary metric views against real data.
- [ ] `task_plan.md`: Phase 10 rows closed, Phase 10 archived to `task_plan_archive.md`.
- [ ] `OPERATOR_RUNBOOK.md` covers: how to manually trigger a dispatch, how to debug a stuck forecast, how to inspect the DB, how to read the metrics snapshots, how to roll back the Cloud Run revision.
- [ ] `UI_CONTRACT.md` matches the deployed API exactly (CI-checked via T10E.14).
- [ ] One full improvement loop observed end-to-end: 7-day cohort question resolves → Brier computed → metrics snapshot updates → second forecast for the same question (taken pre-resolution) shows up in the improvement curve.
- [ ] Total cost across one full week: forecasts ≤ $1.20 (forecast-only, ±25% of $0.90 estimate); fixed costs (Cloud Run + Cloud SQL + Cloud Scheduler) < $15/week.
