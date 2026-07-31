# calibration_plan.md
> Domain: D — Calibration
> Type: Plan
> Last updated: 2026-07-25
> TL;DR: Phase 10 calibration system plan — not started; read this before
>         beginning any Phase 10 work. A standalone backtesting harness that
>         submits Polymarket-anchored questions to the existing agent, polls
>         Polymarket for resolutions, computes Brier scores + calibration
>         curves to drive a weekly improvement loop, and exposes them in an
>         operator-only dashboard. Zero changes to the agent, the BFF, or the
>         user-facing frontend. **Revised 2026-07-25** — the dispatch contract
>         changed materially (two Firestore docs, not one; `metadata.calibration`
>         namespace, not top-level side fields). See §15 for the full changelog.

## Navigation
- §0 — Prerequisites — dependencies that must be met before Phase 10 begins
- §1 — How to use this document — when to load it and alongside what
- §2 — Phase 10 Overview — goal, non-goals, the 5-sprint structure
- §2.5 — Non-Negotiables — the red lines, and the calibration session marker
- §3 — Settled Design Decisions — A (architecture) → G (Cloud Run & operator surface)
- §4 — Postgres Schema Proposal — the 5 tables, indexes, and integrity rules
- §5 — Phase 10 Gate Model — how the four gates map onto calibration work
- §6 — Phase 10A — Foundation: schema + Polymarket adapter
- §7 — Phase 10B — Forecast Engine Bridge: dispatch + harvest
- §8 — Phase 10C — Scoring & Metrics Layer
- §9 — Phase 10D — Cloud Automation: Cloud Run + Cloud Scheduler
- §10 — Phase 10E — Operator API + UI Contract
- §11 — Cloud Run API Contract (consolidated) — endpoints + Firestore interaction
- §12 — Known Risks
- §13 — Acceptance Criteria — Phase 10 (V1 Done)
- §14 — Sprint Status Ledger — the in-document replacement for the retired `task_plan.md`
- §15 — Changelog — what the 2026-07-25 revision changed and why

---

## §0 — Prerequisites

Dependencies that must be met before Phase 10 work begins. The first row is the
cross-domain gate added during the documentation reorganization; the rest are
restated from the body of this plan.

| # | Prerequisite | Status | Why it gates Phase 10 |
|---|---|---|---|
| P1 | ~~KG-B-5 latency analysis landed before any Phase 10 load testing (100+ forecasts).~~ **Downgraded 2026-07-25 — no longer a phase gate.** Replaced by a dispatch concurrency cap (see G8) and scoped to rollout stages 5–6 only (§9). | **Not blocking 10A–10E build work** | The original wording gated Phase 10 on a "100+ parallel forecasts" load profile that Phase 10 does not actually have: 25–30 open questions with one weekly re-forecast is **~30 dispatches per cycle**, spread over a 5-minute-poll harvest window, not 100+ concurrent. More importantly, the gate was circular — `calibration_forecasts.forecast_dispatched_at → forecast_completed_at` **is** a per-forecast latency baseline, so the harness produces the very measurement it was being blocked on. What replaces it: a conservative dispatch concurrency cap + `CALIBRATION_MAX_FORECASTS_PER_RUN` (G8), started low and raised against observed numbers. Rollout stages 1–4 (§9) generate the latency data; stages 5–6 (the full 25–30 batch and enabling the scheduler) are the only steps that wait on it. Sprint 26 closed 2026-07-18; KG-B-5 remains useful *context* for interpreting a slow cycle — see `B_hub/hub_sprints.md` KG-B-5 + `B_hub/sprint26_latency_report.md`. |
| P2 | **Phase 9 (Cloud Deployment) closed** — cloud agent reachable in `anizai-ai` Firestore via cross-project Workload Identity. | **Satisfied** 2026-05-10 | Hard prerequisite for the cloud-automation sprint (Phase 10D). The §9D Workload Identity pattern is what Phase 10D's cross-project Firestore IAM reuses. 10A–10C do not depend on it (runnable against dev Firestore + local agent). |
| P3 | **§8.7.2 SessionResult contract stable.** | **Satisfied** — stable post-Sprint 20 | The only agent-side contract Phase 10 depends on (Decision A5). Calibration is a read-only consumer of this shape; if it changed, dispatch/harvest would break. |
| P4 | ~~Calibration-specific skills authored.~~ **Dropped 2026-07-25.** | n/a | The `sprint-kickoff` / `code-review` / `infrastructure` skills this row referenced do not exist in the repository, and no calibration skill was ever authored. This plan document is self-contained enough to serve as the working brief. Re-open the row only if calibration-specific tooling actually gets written. |
| P5 | **Not a prerequisite: Phase 7 (intelligent filtering).** | n/a | Phase 7's quality improvements should *show up* in calibration curves, but Phase 10 does not depend on Phase 7 landing first. |
| P6 | **Dedicated Postgres instance for calibration — confirmed 2026-07-25.** | **Decided** | Operator decision: calibration gets its **own database instance**, not a schema on the pipeline Postgres. This was already B1's proposal; it is now settled and not revisitable. See B1 for the instance spec and §6 for the local-dev equivalent. |

> Project-level gate: Domain D measures a hardened, cost-instrumented agent, so it
> follows hub Sprints 24–26 (all closed: 2026-07-04 / 2026-07-16 / 2026-07-18). The
> agent code-stability contract (P3) is the load-bearing dependency. P1 was the
> load-testing gate and was downgraded on 2026-07-25 — see the P1 row.
>
> **Doc-reference note (2026-07-25):** earlier revisions of this file cited
> `project_master.md`, `task_plan.md`, and `CLAUDE.md`. **None of the three exist in
> this repository.** All references to them have been retargeted — sprint status now
> lives in §14 of this document, and the engineering context lives in
> `B_hub/hub_overview.md` + `B_hub/hub_agents.md`.

---

## §1 — How to use this document

This is the **granular implementation plan** for Phase 10 — a standalone
calibration & backtesting harness that measures and improves the Anizai agent's
forecast accuracy over time. It is the calibration system's equivalent of the
Phase 7 filtering plan for the data pipeline and the agentic hub implementation
plan for the agent.

It should be loaded by Claude Code (or by a collaborator picking up this work)
at the start of every Phase 10 sprint, alongside — **all paths verified to exist
as of 2026-07-25**:
- §14 of this document (the sprint status ledger — replaced the retired `task_plan.md`)
- `B_hub/hub_overview.md` §2–§3 — the as-built agent graph and the component table. Read this before touching anything dispatch- or harvest-shaped.
- `B_hub/hub_agents.md` — the authoritative node/edge/routing detail and the Firestore contract the harness consumes.
- `docs/old_docs/agentic_hub_spec.md` §8.7 (SessionResult contract — Phase 10 is a read-only consumer of this shape)
- `docs/old_docs/anizai_handoff_consolidated.md` §3.4 (idempotency key contract)
- `data-pipeline/scripts/submit_query.py` — **the reference implementation of the dispatch write.** It is the closest existing code to what `dispatch_service.py` must do, and its docstring documents the two-document invariant (A6) that this plan previously got wrong.

**Required skills:** none. P4 was dropped on 2026-07-25 — see §0.

**Nomenclature note:** Earlier drafts used "Phase 9" / "Phase 9A–9E" before the
project's Cloud Deployment phase was renumbered. The whole calibration phase is
now **Phase 10**, with sub-phases **10A–10E**. Any reference inside this doc to
"Phase C / C1–C5" or "Phase 9 (Cloud)" refers to the closed Cloud Deployment
phase, now **Phase 9** (9A–9E, formerly C1–C5). The cross-phase mapping is in
`task_plan_implementation.md` §3.

Conventions still apply: Conventional Commits with section refs, **§14 of this
document** updated after every task (this replaced `task_plan.md`, which does not
exist), all work inside `data-pipeline/` (Phase 10 lives at
`data-pipeline/calibration/`), no code without an approved plan, the four-gate
testing model.

---

## §2 — Phase 10 Overview

### Goal

Build a research/development harness that:
1. Picks 25–30 active Polymarket questions across three resolution-time cohorts (~7d, ~14d, ~30–45d).
2. Submits each question to the **existing** Anizai agent through the **existing** `forecastQueries → sessionResults` Firestore flow — **zero changes to the agent or to the user-facing frontend**.
3. Detects Polymarket resolution events automatically (REST polling, no auth) and records ground-truth outcomes.
4. Computes **Brier scores**, a **calibration curve** (5 buckets), **per-cohort Brier scores**, and **resolution-source contribution** per resolved question.
5. Re-forecasts still-open questions weekly on a Cloud Scheduler + Cloud Run cron, using whichever agent version is currently deployed; stores both forecasts so an **improvement delta** (original vs. updated) can be measured against the same ground truth.
6. Exposes the data in an **operator-only standalone React dashboard** — a separate Vite app on a separate Firebase Hosting site, sharing no build, no route, and no component with the user-facing `client/`. **Revised 2026-07-25:** this dashboard is built *in-house as Phase 10E*, not handed to an external collaborator as a contract-only deliverable. See §10.

The system's primary purpose is the **improvement loop** — every week of
resolutions feeds back into agent tuning decisions, and the next week's
re-forecast measures whether those tuning decisions actually moved the needle.

### Non-goals

- No automated agent parameter tuning. Tuning decisions stay human-driven; Phase 10 only measures.
- No integration with the user-facing frontend or the Express BFF's session UI.
- No multi-user RBAC. Single-operator (Firebase Auth email allowlist).
- No question sources other than Polymarket (Kalshi etc. are future work).
- No mutation of any existing pipeline or agent code. Phase 10 is strictly additive: new directory `calibration/`, new Cloud Run service, new Cloud SQL instance, new Firebase Hosting site.
- No manual-add of Tier 2 freeform questions in V1 — calibration is Polymarket-anchored only. Tier 2 calibration is post-V1 work.

### Sprint Structure (5 sprints, sequential)

Five sprints that each produce a working slice. 10A–10C deliver a system runnable
from a CLI; 10D adds cloud automation; 10E adds the operator UI surface. Earlier
sprints don't depend on later ones, but later sprints depend on earlier ones.

| Sprint | Phase | Focus | Definition of Done |
|---|---|---|---|
| Phase 10A | Phase 10 | Foundation — Postgres schema + Polymarket adapter (auto-select + manual add + resolution polling) | New Cloud SQL instance + 5 tables provisioned; CLI seeds 25–30 questions across 3 cohorts; resolution poller correctly flips a known-resolved test market to `resolved`. |
| Phase 10B | Phase 10 | Forecast Engine Bridge — dispatch to `forecastQueries` + harvest from `sessionResults` | CLI dispatches a question through the live agent; `calibration_forecasts` row populated end-to-end with `final_probability`, `agent_version`, `agent_evidence_summary`. |
| Phase 10C | Phase 10 | Scoring & Metrics — Brier + calibration curve + cohort + improvement | Resolution event triggers Brier computation across all forecasts for that question; CLI dumps current calibration curve and per-cohort scores; metrics snapshot row written. |
| Phase 10D | Phase 10 | Cloud Automation — Cloud Run + Cloud Scheduler + IAM + secrets | Single Cloud Run service deployed with `/tasks/dispatch`, `/tasks/harvest`, `/tasks/resolve` endpoints; Scheduler jobs invoke them weekly / every-5-min / hourly; one full unattended weekly cycle completes successfully end-to-end. |
| Phase 10E | Phase 10 | Operator API + Dashboard — read API plus the React app that consumes it | `/api/*` endpoints under the same Cloud Run service; the dashboard builds, tests green, and renders all four metric views plus every empty state against real data. |

**Why this split**: 10A is purely deterministic data work — no agent involvement,
**no Firestore surface at all** — the lowest-risk foundation, and it validates the
schema before anything depends on it. 10B is the only sprint that writes to
Firestore, and it writes exactly three things: two documents per dispatch (A6) and
one Postgres row; everything else on that path is a read. 10C is pure computation
over data already in Postgres — no external dependencies. 10E is API + dashboard,
built against local data so the charts are known-good before any of it is
automated. 10D is the only sprint touching cloud infra (Cloud Run, Cloud
Scheduler, Cloud SQL networking, IAM); it is gated by Phase 9 closeout — CLOSED
2026-05-10 — and it is deliberately **last**, because scheduling a system you
cannot yet see is how unattended runs go wrong quietly.

**Sprint numbering**: Phase 10 sprints carry the **"Phase 10A"…"Phase 10E"
labels only** — no bare numeric sprint identifiers — to avoid collision with Phase 8
hub Sprints 18–27 and Phase 9 (Cloud) Sprints 9A–9E (formerly C1–C5). Tasks inside
each sprint are numbered T10A.1, T10A.2, … T10E.1, T10E.2, …

> **Alias table (added 2026-07-25).** Conversation and planning often refer to these
> as "Sprint 1–5". Those are aliases for the same five sprints, in the same order —
> there is no sixth sprint and no reordering:
>
> | Alias | Canonical label | Focus |
> |---|---|---|
> | Sprint 1 | **Phase 10A** | Foundation — DB + Polymarket |
> | Sprint 2 | **Phase 10B** | Forecast Bridge — dispatch + harvest |
> | Sprint 3 | **Phase 10C** | Scoring + Metrics |
> | Sprint 4 | **Phase 10E** | Dashboard + API |
> | Sprint 5 | **Phase 10D** | Cloud Automation + Production Hardening |
>
> **Note the swap at 4/5.** The alias ordering puts the dashboard *before* cloud
> automation; the canonical lettering has 10D (cloud) before 10E (API+UI). Both
> orderings work — 10E only needs 10C's metrics to exist, and 10D only needs
> 10A–10C's services. **The build order this plan follows is the alias order:
> 10A → 10B → 10C → 10E → 10D**, because a working dashboard over local data makes
> the cloud rollout (§9) far easier to observe than a cloud rollout with no UI.
> The letters are kept as the stable task-ID prefix (T10E.x stays T10E.x even
> though it is built fourth).

**Ordering vs. other phases** (see also §0):
- **Hard prerequisite for any cloud automation (10D)**: Phase 9D closed — satisfied 2026-05-10.
- **Soft prerequisite (10A–10C)**: runnable against the existing dev Firestore + local agent; the cloud agent is also live, so they can run either way.
- **Not a prerequisite**: Phase 7 (intelligent filtering).

---

## §2.5 — Non-Negotiables

*Added 2026-07-25. These are hard constraints, not preferences. A calibration change
that violates one of them is rejected, not negotiated — the whole premise of Phase 10
is that it measures the production system **without perturbing it**.*

### What Phase 10 must never touch

| # | Red line | Why |
|---|---|---|
| N1 | **No changes to `data-pipeline/agent/`** — not the graph, not the nodes, not the worker, not the LangGraph state. | An agent that was modified to accommodate its own measurement is no longer the thing being measured. This is A5, restated as a rule. |
| N2 | **No changes to `server/` (the Express BFF).** Calibration does not route through it and does not extend it. | Dispatching via the BFF would drag in the real user's auth flow, plan-limit charging, and idempotency accounting. Writing straight to Firestore is what keeps calibration off the user's billing surface. |
| N3 | **No changes to `client/` (the user-facing frontend).** The dashboard is a separate app. | A shared component or route is a shared blast radius. |
| N4 | **No writes to any vault** (`knowledge_*`, `social_*`, `momentum_vault`, `mapping_dict`) and no changes to ingestion or processing. | Calibration is downstream of the pipeline, never upstream. |
| N5 | **No writes to `sessionResults` or to `sessions/{id}/evidence`.** Those are agent-owned. Calibration reads them only. | Anything else corrupts the very ground truth being harvested. |
| N6 | **No modification or deletion of any pre-existing session.** Calibration only ever creates its own new sessions. | An existing session belongs to a real user. |
| N7 | **No interference with real users.** No shared quota, no shared user record, no shared collection semantics beyond the two documents calibration itself writes. | Cost and reliability of the product must not depend on research load. |

### What Phase 10 adds — all of it new, none of it shared

- `data-pipeline/calibration/` — new directory tree.
- A **separate Postgres instance** (P6/B1) — not a schema on the pipeline DB.
- A **separate Cloud Run service**, `calibration-runner`.
- A **separate React dashboard app** on a separate Hosting site.
- New Firestore sessions that carry the calibration marker below — and nothing else.

### The calibration session marker

Every session and every queue document calibration creates carries this exact block:

```json
{
  "metadata": {
    "calibration": {
      "enabled": true,
      "questionId": "...",
      "runId": "...",
      "forecastRunIndex": 0
    }
  }
}
```

This marker is the **single mechanism** by which a calibration session is
distinguishable from a real user's session, in Firestore, in logs, and in any
cleanup query. Two consequences follow, and both are load-bearing:

1. **Every calibration query filters on it.** Any operation that could touch a session — harvest, cleanup, audit, backfill — is scoped by `metadata.calibration.enabled == true` (or by the `userId == "calibration-runner"` sentinel, which is equivalent and cheaper to index). An unscoped query over `sessions` is a bug, and a reviewable one.
2. **The agent must ignore it.** The marker lives under `metadata` precisely so that a future strict-validation pass on inbound `forecastQueries` has one namespaced field to allow rather than two loose top-level ones. See A4 for the history of this decision — it used to be two top-level fields, and that was wrong.

### The kill switch

The system must be stoppable by a non-author, in under a minute, without a deploy
and without touching the agent. Three independent levers, any one of which is
sufficient:

1. **Disable the Cloud Scheduler jobs** (`gcloud scheduler jobs pause`). Stops all new work; in-flight forecasts still harvest.
2. **Set `CALIBRATION_ENABLED=false`** on the Cloud Run service. Every `/tasks/*` endpoint short-circuits to a no-op `{"status":"disabled"}` before touching Firestore or Postgres. This is checked **first**, before any I/O.
3. **Remove the `roles/datastore.user` binding** from the calibration GSA. The blunt instrument — revokes the ability to write to Firestore at the IAM layer.

Stopping calibration by any of these routes must leave the agent, the BFF, and the
frontend completely unaffected. That property is what T10D.12 verifies.

---

## §3 — Settled Design Decisions

### A — Architecture

| ID | Decision |
|---|---|
| **A1** | **Standalone cloud footprint.** Phase 10 deploys as one Cloud Run service (`calibration-runner`) plus Cloud Scheduler jobs and one Cloud SQL Postgres 16 instance. Lives in the **same GCP project as the agent (`anizai-pipeline`)** for IAM simplicity but in its own directory tree (`data-pipeline/calibration/`) and behind its own GSA (`calibration-runner@anizai-pipeline`). Cross-project Firestore access (`anizai-ai`) reuses the same Workload Identity pattern Phase 9D established. |
| **A2** | **One Cloud Run service, multiple endpoints.** All scheduled tasks (`dispatch`, `harvest`, `resolve`, `discover`, `snapshot_metrics`) and the operator-facing `/api/*` routes live in one container. Reasons: shared Postgres pool, shared Firestore client, shared config, single image to build/deploy, $0 cold-start cost across endpoints. Auth differs per route (Cloud Scheduler OIDC for `/tasks/*`, Firebase Auth ID token for `/api/*`). |
| **A3** | **Polling, not listeners.** Harvester is a **scheduled poll** (Cloud Scheduler → `/tasks/harvest` every 5 min), not a long-lived Firestore listener. Reason: Cloud Run's billing model penalizes always-on; polling at 5-min granularity adds at most a 5-min delay to result capture, which is irrelevant for a weekly cycle. This is a deliberate departure from the agent's listener pattern. |
| **A4** | **~~Two custom top-level side fields~~ → `metadata.calibration` namespace. REVISED 2026-07-25.** The calibration-runner marks its submissions with a single nested block — `metadata.calibration.{enabled, questionId, runId, forecastRunIndex}` — on **both** documents it writes (see A6). The agent reads `question` only and ignores `metadata` entirely. **Why the change:** the original decision put `calibrationRunId` + `calibrationQuestionId` at the top level and deferred the namespaced form to "if strict validation ever lands". Taking the namespace now costs nothing, survives a future validation pass without a migration, gives cleanup queries one stable path to filter on (§2.5), and carries `forecastRunIndex` — which the top-level form had no place for and which the improvement loop (F1) needs on the document itself for debugging a mid-flight run. |
| **A5** | **No agent code changes.** If a calibration-side need would require touching `agent/`, that need is descoped to Phase 10.5 or rejected. The only contract Phase 10 depends on is the §8.7.2 SessionResult shape, which is already stable post-Sprint 20. See N1. |
| **A6** | **Dispatch writes TWO documents, not one, and both are keyed by `sessionId`. ADDED 2026-07-25 — this corrects a real defect in the pre-revision plan.** A dispatch is `sessions/{sessionId}` **first**, then `forecastQueries/{sessionId}`. Both use the same operator-minted `sessionId` as the document ID — no auto-IDs. **Why this is not optional:** the agent's `update_session_status` path calls Firestore `.update()`, which raises `NotFound` if `sessions/{sessionId}` does not already exist. A dispatch that writes only the queue document produces a session the worker claims and then immediately fails on. `scripts/submit_query.py:68-71` documents exactly this ("the worker's `update_session_status` calls require this doc to exist before they can run") and is the reference implementation. The pre-revision §11 of this plan said "Writes (one type, one collection)" — that was wrong and would have failed on the first live dispatch. Order matters: session doc first, queue doc second, because the queue doc is what the worker's listener is watching. |
| **A7** | **Calibration sessions are owned by a sentinel user, `userId: "calibration-runner"`. ADDED 2026-07-25.** Both documents carry it. It is not a Firebase Auth UID and no such user exists in Auth — deliberately, so that nothing in the product's user-facing surface can ever enumerate, bill, or render these sessions. It is the cheap index-friendly complement to the `metadata.calibration` marker: `where userId == "calibration-runner"` is the query every cleanup and audit path uses. The BFF is never involved (N2), so no auth flow is exercised and no plan quota is charged. |

### B — Storage / Schema

| ID | Decision |
|---|---|
| **B1** | **Dedicated Cloud SQL Postgres 16 instance — CONFIRMED 2026-07-25 (P6).** Instance name `anizai-calibration-db`, smallest tier (`db-f1-micro`, 10 GB SSD, single zone), database `anizai_calibration`, user `calibration_app`. Reasons: (a) full ops isolation from the pipeline GKE Postgres — no TimescaleDB / pgvector dependencies, and no possibility of a calibration migration touching a vault; (b) Cloud Run → Cloud SQL via Cloud SQL Auth Proxy is one-line setup, vs. exposing GKE Postgres via internal LB; (c) cost is ~$8–10/month, well within the budget envelope. **A separate schema on the pipeline instance was considered and rejected** — it would share a connection pool, a backup/restore lifecycle, and a blast radius with the vaults, which is exactly what N4 forbids. Local development mirrors this: a **separate local Postgres database**, reached only via `CALIBRATION_DATABASE_URL`. The calibration code never imports `persistence/` and never reads the pipeline's `POSTGRES_*` constants. |
| **B2** | **5 tables, no views (pre-V1).** `calibration_questions`, `calibration_forecasts`, `calibration_resolutions`, `calibration_runs`, `calibration_metrics_snapshots`. Aggregations are computed in Python (Phase 10C) and either returned live by `/api/metrics/*` or persisted as JSONB rows in `calibration_metrics_snapshots`. Materialized views are a post-V1 optimization. |
| **B3** | **All times in UTC, all timestamps `TIMESTAMPTZ`.** Polymarket resolution dates come in UTC; Cloud Scheduler fires in UTC; cohort window math is in UTC. No tz conversion in the data layer — only at UI render time if the operator wants local time. |
| **B4** | **`final_probability` stored as `NUMERIC(5,4)` with CHECK 0–1.** Matches the §5.1 handoff contract (probability units always 0–1 floats). Brier score stored as `NUMERIC(8,7)` (range 0–1, four-significant-digit headroom). |

### C — Question Management

| ID | Decision |
|---|---|
| **C1** | **Auto-selection criteria (defaults; calibratable via env vars):** time-to-resolution windows **5–9 days / 12–16 days / 28–46 days**; minimum cumulative volume **$50K** for 7d/14d cohorts and **$25K** for 30–45d (fewer high-volume long-horizon markets); category allowlist `geopolitical`, `financial`, `ai`; category blocklist `sports`, `entertainment`, `pure_crypto_price`. Target counts: **8–10 per cohort, 24–30 active total** (`CALIBRATION_TARGET_COUNT_7D=10`, `_14D=10`, `_30_45D=8`). |
| **C2** | **Manual adds use the same internal `Question` record.** UI form posts `{question_text, polymarket_slug, expected_resolution_date, category, cohort, operator_email}` → `/api/questions` → row inserted with `added_by='manual'`. Auto and manual rows are otherwise indistinguishable downstream. |
| **C3** | **Auto-selection runs on a separate hourly Cloud Scheduler job** that calls `/tasks/discover`. Discovers new candidate markets, doesn't kick anyone out, only tops up to the target count. New questions are inserted with `status='open'` and immediately enqueued for an initial forecast (Week 0). |
| **C4** | **Polymarket category mapping** lives in `calibration/polymarket/taxonomy.json`: a JSON-driven mapping from Polymarket tag strings to the four-category internal taxonomy. Updateable without redeploy via Cloud Run env var if needed. Seed allowlist: `Politics`, `Geopolitics`, `Macroeconomy`, `AI`, `Tech`. Seed blocklist: `Sports`, `Entertainment`, `Crypto Prices`. |

### D — Resolution

| ID | Decision |
|---|---|
| **D1** | **Resolution detection via CLOB REST**, not the WebSocket. Endpoint: `GET https://clob.polymarket.com/markets/{condition_id}` (already used by `ingestion/polymarket_producer.py:181`). Resolution = `closed=true` AND one outcome's `winner=true`, OR `outcomePrices` shows `[1.0, 0.0]` / `[0.0, 1.0]` for >24h (settle-window guard). Polled hourly. |
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
| **G1** | **Single Docker image** at `data-pipeline/infrastructure/Dockerfile.calibration`. Python 3.11, FastAPI, runs `python -m calibration.server`. Image deployed to `us-central1-docker.pkg.dev/anizai-pipeline/anizai-images/anizai-calibration:VERSION`. |
| **G2** | **Cloud Run config:** min instances 0, max 2, concurrency 20, request timeout 120s for `/api/*` and 540s (max) for `/tasks/dispatch` (must wait for ~30 forecastQueries inserts + ack). CPU only-during-request mode. |
| **G3** | **Cloud SQL connectivity via Cloud SQL Auth Proxy sidecar** (built into Cloud Run via the connection string `--add-cloudsql-instances`). No VPC connector needed — keeps networking simple. |
| **G4** | **Secrets via Secret Manager**, mounted as env vars at Cloud Run deploy time: `CALIBRATION_DB_PASSWORD`, `FIREBASE_PROJECT_ID` (=`anizai-ai`), `FIREBASE_AUTH_OPERATOR_EMAILS` (comma-separated allowlist), `POLYMARKET_API_BASE` (constants for resolver). Reuses the Phase 9A Secret Manager pattern. |
| **G5** | **No agent worker on Cloud Run.** The cloud-deployed agent (Phase 9D) keeps owning `forecastQueries` claims. Calibration only writes new forecastQueries docs and reads the resulting sessionResults — it does not subscribe to claim queues, does not run LangGraph code, does not import `agent/`. |
| **G6** | **UI hosted on `anizai-ai` Firebase project, separate Hosting site.** Reuses the existing Firebase Auth tokens; one less project to provision. Hosting target named `calibration` distinct from the user-facing site. Firebase rules + auth allowlist gate access. |
| **G7** | **Operator allowlist initial:** `ronking79@gmail.com`. The allowlist is a Secret Manager secret (`FIREBASE_AUTH_OPERATOR_EMAILS`), comma-separated, hot-swappable without a redeploy. *Revised 2026-07-25:* the "add the UI collaborator's email once known" clause is dropped — the dashboard is built in-house (see §10), so there is no external collaborator to provision. Add operator emails as actual operators appear. |
| **G8** | **Cost guardrails and the kill switch. ADDED 2026-07-25.** Four env-controlled ceilings, all enforced in the dispatch service *before* any Firestore write, plus the master switch: `CALIBRATION_ENABLED` (default `true`; when `false` every `/tasks/*` endpoint returns `{"status":"disabled"}` **before** touching Firestore or Postgres — checked first, always); `CALIBRATION_MAX_OPEN_QUESTIONS=30` (discovery refuses to exceed it); `CALIBRATION_MAX_FORECASTS_PER_RUN=30` (dispatch truncates and **logs the truncation** rather than silently dropping); `CALIBRATION_DISPATCH_CONCURRENCY=3` (how many dispatches are in flight at once — the concrete replacement for the retired P1 latency gate, started deliberately low and raised only against numbers observed in rollout stages 3–4). The three-lever kill switch is specified in §2.5. |

---

## §4 — Postgres Schema Proposal

Single Cloud SQL Postgres 16 instance, single database `anizai_calibration`,
single schema `public` (kept simple — there is no other schema to disambiguate
from). Five tables.

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

**Reasoning:** `polymarket_condition_id` is the stable resolution key (slug can
change). `UNIQUE` on `condition_id` prevents duplicate ingestion across
auto-discovery and manual-add. Partial index on `expected_resolution_date`
accelerates the resolver's "what's about to resolve" query without bloating the
index with archived rows. `added_by_operator` deliberately allows NULL for auto
to avoid a sentinel string.

### Table 2: `calibration_forecasts`

```sql
CREATE TABLE calibration_forecasts (
    id                          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id                 UUID            NOT NULL REFERENCES calibration_questions(id) ON DELETE CASCADE,
    run_id                      UUID            NOT NULL REFERENCES calibration_runs(id),
    forecast_run_index          INTEGER         NOT NULL,    -- 0=initial seed, 1=first re-forecast, ...
    session_id                  TEXT            NOT NULL,    -- Firestore sessionId; minted by US at dispatch (A6), never NULL
    query_doc_id                TEXT            NOT NULL,    -- forecastQueries doc id — equals session_id (A6); kept as a
                                                             -- distinct column so a future decoupling needs no migration
    idempotency_key             TEXT            NOT NULL,    -- the UUID4 written onto sessions/{id}.idempotencyKey
    agent_version               TEXT,                        -- copied from SessionResult.agentVersion
    final_probability           NUMERIC(5,4)    CHECK (final_probability >= 0 AND final_probability <= 1),
    confidence                  NUMERIC(5,4)    CHECK (confidence >= 0 AND confidence <= 1),
    tier                        TEXT            CHECK (tier IN ('tier_1','tier_2')),
    status                      TEXT            NOT NULL CHECK (status IN ('dispatched','completed','failed','timed_out','needs_clarification'))
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

**Reasoning:** Two unique constraints — `(question_id, forecast_run_index)`
enforces the improvement-loop semantics (no duplicate forecasts at the same run
index for one question), and `idempotency_key` prevents harvester double-writes.
Partial index on `status` is what the harvester scans every 5 min — keeps it
fast. `agent_version` is nullable because it's populated at harvest time (a
`dispatched` row hasn't seen a SessionResult yet).

*Revised 2026-07-25 — three changes:*
- **`session_id` is `NOT NULL`.** It was nullable ("NULL until harvest") on the assumption that Firestore would assign the ID. Under A6 we mint the `sessionId` ourselves and use it as the document ID for both writes, so it is known before the row is inserted. Making it `NOT NULL` turns "we lost track of which session this row dispatched" from a silent data state into an insert-time error.
- **`needs_clarification` added to the status CHECK.** The agent has a real terminal state the pre-revision plan had no slot for: `query_understand` can route to `write_clarification`, which ends the graph with `sessions/{id}.status == 'awaiting_clarification'` and no SessionResult. Without this value the harvester's only options were to mislabel it `failed` (polluting the failure rate with a non-failure) or leave it `dispatched` until it aged into `timed_out` (a 120-minute lie). V1 does not answer clarifications (§11 assumption), so this is a terminal state, not a retry state.
- **The partial index on `status` must include the new value** if the harvester is to keep scanning cheaply — but it deliberately does **not**: `needs_clarification` is terminal, so the harvester never rescans it. The index stays `WHERE status IN ('dispatched','timed_out')`.

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

**Reasoning:** `UNIQUE (question_id)` makes the resolver naturally idempotent —
second-time detection is a `INSERT ... ON CONFLICT DO NOTHING`. `outcome_numeric`
is denormalized for join-free Brier computation; CHECK guarantees it's `NULL`
exactly when outcome is AMBIGUOUS.

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

**Reasoning:** Operational/audit table. Lets the operator see "this week's run
dispatched 25 questions, 23 completed, 1 failed, 1 timed out". A run is `finished`
when every dispatched forecast is in a terminal state
(`completed`/`failed`/`timed_out`) — closed by the harvester when it walks the
final pending row.

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

**Reasoning:** Snapshots are written every time a question resolves (snapshot
represents "as-of this resolution event") *and* on a daily Cloud Scheduler trigger
as a low-cost audit point. `payload` shape varies by `metric_type` and is
documented in `calibration/metrics/snapshots.py`. The improvement curve is one row
per resolution event with `payload` carrying `{original_brier, latest_brier, delta,
agent_version_pair, cohort}`. UI plots improvement_curve rows over time.

### Cross-table integrity rules (enforced in Python, not SQL)

- A forecast row's `forecast_run_index = 0` must correspond to a `calibration_runs` row of `run_type IN ('initial_seed', 'manual')`. Re-forecasts (`run_type='weekly_reforecast'`) get `forecast_run_index >= 1`.
- A question with `status='resolved'` must have exactly one row in `calibration_resolutions`.
- Brier scores on forecasts get backfilled (transaction) immediately after a resolution row is inserted.

---

## §5 — Phase 10 Gate Model

Phase 10 is partially data-pipeline-shaped (Postgres-backed) and partially
hub-shaped (Firestore round-trips). The four-gate model maps:

| Gate | Meaning in Phase 10 |
|---|---|
| **Gate 1** | Module imports cleanly, constants/config validated, schemas (Pydantic for API shapes) pass instantiation, SQL DDL runs without error against an empty Postgres. |
| **Gate 2** | Pure-function logic with mocks: dispatch payload construction, harvest projection logic (E5 extraction), Brier math, calibration-curve bucketing, cohort aggregation, source-contribution rollup. Mocked Postgres (sqlite or testcontainer Postgres), mocked Firestore admin client. |
| **Gate 3** | Round-trip: full dispatch → Firestore emulator → harvest → Postgres → metrics computation, asserting end-state matches expectations. Resolution polling against a recorded Polymarket API fixture (no live HTTP). |
| **E2E** | Real environment: real Cloud Run, real Cloud SQL, real `anizai-ai` Firestore, real Polymarket public API, real cloud-deployed agent. One full weekly cycle observed unattended. |

Each task is tagged with which gate(s) it must pass before being marked `[x]`.

---

## §6 — Phase 10A — Foundation: Schema + Polymarket Adapter

### Sprint scope

Land the Postgres schema, the Polymarket adapter (auto-selection + resolution
polling), the manual-add path, and a CLI that exercises the full data flow
without involving the agent or Firestore. End of sprint: `python -m
calibration.cli seed` populates 25–30 questions; `python -m calibration.cli
resolve` correctly flips a known-resolved test market.

### Confirmed design decisions

- All A1, A4, A5, B1–B4, C1–C4, D1–D4 from §3 apply.
- All Phase 10A code lives under `data-pipeline/calibration/`. No imports from `agent/`, `processing/`, `persistence/`, or `ingestion/`. The only cross-package import allowed is `config/settings.py` (for shared infra constants).
- Calibration uses its own dedicated Postgres connection (`CALIBRATION_DATABASE_URL`); does **not** reuse the pipeline `POSTGRES_HOST` constants. Phase 10D wires this to Cloud SQL Auth Proxy; for 10A–10C the operator points it at a local Postgres or Cloud SQL instance via `pg_proxy` for development.
- Polymarket API calls reuse the `GAMMA_API_BASE` and `CLOB_API_BASE` constants (lifted to a shared location in `calibration/polymarket/constants.py`; copy-not-import to avoid pulling in `ingestion/`'s asyncio/kafka deps).

### Task table

| Task | Description | Gate(s) | Files / Refs |
|---|---|---|---|
| T10A.1 | Provision Cloud SQL Postgres 16 instance `anizai-calibration-db` (db-f1-micro, 10 GB, single zone). Create DB `anizai_calibration` and user `calibration_app`. Store password in Secret Manager. **Gates T10A.2 — no schema work until DB is reachable from operator workstation via Cloud SQL Auth Proxy.** | — | gcloud script `infrastructure/gcp/c10_create_calibration_db.sh` |
| T10A.2 | Author `calibration/sql/init.sql` — full DDL for the 5 tables + indexes from the schema proposal above. Apply to dev DB. | Gate 1 | `calibration/sql/init.sql` (new) |
| T10A.3 | Implement `calibration/db.py` — connection pool wrapper (psycopg3 async), reads `CALIBRATION_DATABASE_URL`. Mirrors the pattern of `persistence/db.py` but isolated. | Gate 1 | `calibration/db.py` (new) |
| T10A.4 | Implement `calibration/models.py` — Pydantic models matching the 5 tables (Question, Forecast, Resolution, Run, MetricsSnapshot). Used by all downstream code as the typed boundary. | Gate 1 | `calibration/models.py` (new) |
| T10A.5 | Implement `calibration/repos/questions.py`, `forecasts.py`, `resolutions.py`, `runs.py`, `metrics.py` — one repository module per table with `insert`, `get_by_id`, `list`, plus table-specific queries (e.g., `list_open_by_cohort`, `mark_resolved`). All async, all return Pydantic models. | Gate 1, Gate 2 | `calibration/repos/*.py` (new, 5 files) |
| T10A.6 | Implement `calibration/polymarket/discover.py` — auto-selection logic. Calls Gamma API for active markets; filters by liquidity per C1; maps tags to category per C4; bins by cohort window per C1; returns a list of candidate `Question` records. **No DB writes in this module** — pure function. | Gate 1, Gate 2 | `calibration/polymarket/discover.py` (new) |
| T10A.7 | Implement `calibration/polymarket/taxonomy.py` — JSON-driven Polymarket tag → internal-category mapping (C4). Includes the seed allowlist (`Politics`, `Geopolitics`, `Macroeconomy`, `AI`, `Tech`) and blocklist (`Sports`, `Entertainment`, `Crypto Prices`). | Gate 1 | `calibration/polymarket/taxonomy.py` + `calibration/polymarket/taxonomy.json` (new) |
| T10A.8 | Implement `calibration/polymarket/resolve.py` — resolution detection. Calls `GET /markets/{condition_id}` per D1, maps to `(YES/NO/AMBIGUOUS, outcome_numeric, raw_data)`. Single function, no DB writes. | Gate 1, Gate 2 | `calibration/polymarket/resolve.py` (new) |
| T10A.9 | Implement `calibration/services/discovery_service.py` — orchestrates: discover candidates → diff against existing open questions → top up to target count → insert new rows with `status='open'`. Captures `liquidity_usd_at_pickup`. **Idempotent**: re-running doesn't insert duplicates (UNIQUE constraint on `condition_id`). | Gate 2 | `calibration/services/discovery_service.py` (new) |
| T10A.10 | Implement `calibration/services/resolution_service.py` — orchestrates: list open questions whose `expected_resolution_date <= today + 2d`, poll each, insert into `calibration_resolutions` on hit (`ON CONFLICT DO NOTHING`), flip `calibration_questions.status` to `resolved`. Bulk operation, single transaction per question. | Gate 2, Gate 3 | `calibration/services/resolution_service.py` (new) |
| T10A.11 | Implement `calibration/services/manual_add_service.py` — validates manual question payload, fetches market metadata from Polymarket to populate `polymarket_condition_id` + `expected_resolution_date` from the slug, inserts. | Gate 2 | `calibration/services/manual_add_service.py` (new) |
| T10A.12 | Implement `calibration/cli.py` — Click-based CLI with subcommands: `seed`, `discover`, `resolve`, `add-manual`, `list-questions`, `wipe-dev`. Each subcommand is a thin wrapper around a service. | Gate 1 | `calibration/cli.py` (new) |
| T10A.13 | Phase 10A env vars in `calibration/config.py`: `CALIBRATION_DATABASE_URL`, `CALIBRATION_TARGET_COUNT_7D=10`, `CALIBRATION_TARGET_COUNT_14D=10`, `CALIBRATION_TARGET_COUNT_30_45D=8`, `CALIBRATION_LIQUIDITY_MIN_7_14D_USD=50000`, `CALIBRATION_LIQUIDITY_MIN_30_45D_USD=25000`, `POLYMARKET_GAMMA_API`, `POLYMARKET_CLOB_API`. | — | `calibration/config.py` (new) |
| T10A.14 | Gate 1 unit tests for `models.py`, `db.py` (connection lifecycle), `polymarket/taxonomy.py` (tag mapping). | Gate 1 | `tests/test_calibration/test_models.py`, `test_polymarket_taxonomy.py` (new) |
| T10A.15 | Gate 2 unit tests for `discover.py` (mocked Gamma API), `resolve.py` (mocked CLOB responses for YES/NO/AMBIGUOUS/still-open), `discovery_service.py` (mocked Postgres via testcontainer), `resolution_service.py`. | Gate 2 | `tests/test_calibration/test_discover.py`, `test_resolve.py`, `test_discovery_service.py`, `test_resolution_service.py` (new) |
| T10A.16 | Gate 3 integration test: `tests/test_calibration/test_phase_10a_e2e.py` — spins up testcontainer Postgres, applies init.sql, runs the full `seed` CLI flow against a recorded Gamma API fixture, asserts ~25 questions across 3 cohorts. Then runs `resolve` against a fixture for one already-resolved market, asserts the resolution row + question status flip. | Gate 3 | `tests/test_calibration/test_phase_10a_e2e.py` (new) |
| T10A.17 | E2E (operator-driven): point CLI at the live Cloud SQL instance and live Polymarket API. Run `seed` and `list-questions`. Capture before/after counts in commit message. | E2E | live Cloud SQL + Polymarket |
| T10A.18 | Update **§14 of this document** — mark the Phase 10A row active. *(Retargeted 2026-07-25: `task_plan.md` does not exist in this repository.)* | — | this file, §14 |

### Constants introduced (Phase 10A)

- `POLYMARKET_GAMMA_API = "https://gamma-api.polymarket.com"` (mirror of `ingestion/polymarket_producer.py:53`)
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
- **Nothing written to Firestore and nothing touched in `agent/`** — Phase 10A has no Firestore surface at all, which is what makes it the safe first sprint.
- §14 updated: Phase 10A row marked active.

---

## §7 — Phase 10B — Forecast Engine Bridge: Dispatch + Harvest

### Sprint scope

Wire the calibration system to the existing agent. Build the dispatcher (writes
`forecastQueries` docs), the harvester (reads `sessionResults` + the `evidence`
subcollection, applies the E5 projection, writes `calibration_forecasts` rows).
End of sprint: a `python -m calibration.cli dispatch-one --question-id <id>`
round-trips through the live agent and persists a complete forecast row with
`agent_version`, `final_probability`, `agent_evidence_summary`.

### Confirmed design decisions

- A4 + A5 + **A6 + A7** are the load-bearing constraints. **The dispatcher writes two documents per forecast** — `sessions/{sessionId}` then `forecastQueries/{sessionId}`, both keyed by an operator-minted `sessionId`, both carrying `userId: "calibration-runner"` and the `metadata.calibration` block. The exact payloads are in the subsection below. This is the single most important correction in the 2026-07-25 revision: the pre-revision plan wrote only the queue document, which would have failed on the first live dispatch.
- Harvester scopes every query by `userId == "calibration-runner"` (§2.5), but in practice it does not need to query Firestore by filter at all — it drives from Postgres, reading `calibration_forecasts` rows in `dispatched` state and looking up each `session_id` directly. Direct document reads, no collection scans, no chance of touching a real user's session. **All Firestore access on the harvest path is read-only.**
- Harvester is non-listening — it polls (A3). Polling cadence: every 5 minutes via `/tasks/harvest` in Phase 10D; in Phase 10B it's run on demand from the CLI.
- **Session-state mapping is explicit and total.** The harvester reads `sessions/{sessionId}.status` and maps: `done` → `completed`; `failed` → `failed` (+ `error_message` from `errorMessage`); `awaiting_clarification` → `needs_clarification` (terminal in V1 — calibration never answers a clarification); anything else past `CALIBRATION_DISPATCH_TIMEOUT_MIN` → `timed_out`; anything else within the window → leave `dispatched` and re-poll. Every branch is covered by a test (T10B.8).
- Failed sessions (sessionResults.status='failed' or status='error') are recorded with `calibration_forecasts.status='failed'` and `error_message` populated. They do not block resolution scoring later (the row is just excluded from Brier aggregations).
- Timeout handling: if a forecast has been in `dispatched` status for more than `CALIBRATION_DISPATCH_TIMEOUT_MIN=120` minutes, harvester flips it to `timed_out` and the run summary records the failure.
- `agent_evidence_summary` is computed at harvest time using the projection in E5. The projection lives in `calibration/evidence_projection.py`, has a versioned schema, and is unit-tested against fixtures captured from real SessionResult docs.

### The dispatch contract (authoritative — added 2026-07-25)

Two documents, written in this order, both keyed by the same operator-minted
`sessionId`. Reference implementation: `scripts/submit_query.py`.

**1. `sessions/{sessionId}` — written FIRST.** The agent's `update_session_status`
uses `.update()` and raises `NotFound` if this document is absent (A6). The field
set mirrors what the BFF writes for a real session, so the worker sees nothing
unusual:

```json
{
  "userId": "calibration-runner",
  "question": "...",
  "title": null,
  "idempotencyKey": "<uuid4>",
  "status": "queued",
  "latestProbability": null,
  "latestConfidence": null,
  "followEnabled": false,
  "isFollowing": false,
  "canonicalKey": null,
  "errorCode": null,
  "errorMessage": null,
  "clarificationCandidates": null,
  "createdAt": "serverTimestamp",
  "updatedAt": "serverTimestamp",
  "lastActivityAt": "serverTimestamp",
  "metadata": {
    "calibration": {
      "enabled": true,
      "questionId": "...",
      "runId": "...",
      "forecastRunIndex": 0
    }
  }
}
```

`followEnabled: false` is deliberate — calibration sessions must never enter the
follow-up subgraph (Sprint 24), which would spend tokens on a conversation nobody
is having.

**2. `forecastQueries/{sessionId}` — written SECOND.** This is what the worker's
listener is watching, so it goes last; until it exists, nothing is claimable:

```json
{
  "queryId": "...",
  "sessionId": "...",
  "userId": "calibration-runner",
  "question": "...",
  "status": "pending",
  "createdAt": "serverTimestamp",
  "claimedAt": null,
  "claimedBy": null,
  "metadata": {
    "calibration": {
      "enabled": true,
      "questionId": "...",
      "runId": "...",
      "forecastRunIndex": 0
    }
  }
}
```

**Failure between the two writes** leaves an orphan `sessions/{id}` in `queued`
that no worker will ever claim. This is the benign failure direction and is
deliberately not made transactional: the `calibration_forecasts` row is inserted
only after *both* writes succeed, so an orphan session has no Postgres row, is
invisible to every metric, and is swept by a `userId == "calibration-runner" AND
status == "queued" AND createdAt < now-24h` cleanup. Writing the queue document
first would give the opposite and much worse failure: a claimable query whose
session document does not exist, which is precisely the `NotFound` crash A6 exists
to prevent.

### Task table

| Task | Description | Gate(s) | Files / Refs |
|---|---|---|---|
| T10B.1 | Implement `calibration/firestore_client.py` — Admin SDK wrapper specific to calibration. Cross-project init using the same `_EmulatorCredentials` pattern Phase 8A established (for emulator tests). Functions: `init_app(project_id)`, `get_db()`, **`write_dispatch(session_id, payloads)` (writes the session doc then the queue doc, in that order — A6)**, `read_session(session_id)`, `read_session_result(session_id)`, `read_evidence_subcollection(session_id)`. **No claim/transaction logic** — calibration is read-only on the agent's flow apart from the two dispatch writes. | Gate 1 | `calibration/firestore_client.py` (new) |
| T10B.2 | Implement `calibration/services/dispatch_service.py` — orchestrates: check `CALIBRATION_ENABLED` and the G8 ceilings **first**; pick open questions to dispatch (input list of question_ids); for each, mint `session_id` + `idempotency_key` (uuid4), build the **two** payloads from the dispatch contract above, call `write_dispatch` (session doc first, queue doc second — A6), then insert the `calibration_forecasts` row with `status='dispatched'` **only after both writes succeed**. Bulk-safe (per-question try/except — one failure doesn't block others), and bounded by `CALIBRATION_DISPATCH_CONCURRENCY`. Records the summary on `calibration_runs`, including any G8 truncation. | Gate 1, Gate 2 | `calibration/services/dispatch_service.py` (new) |
| T10B.3 | Implement `calibration/evidence_projection.py` — E5 projection. Pure function `project_evidence(session_result_doc, evidence_docs) -> dict`. Handles missing fields gracefully (an evidence doc may omit `sourceType` for an early-version forecast). Returns the projected JSON for `agent_evidence_summary`. | Gate 1, Gate 2 | `calibration/evidence_projection.py` (new) |
| T10B.4 | Implement `calibration/services/harvest_service.py` — orchestrates: list `calibration_forecasts` rows in `dispatched` state; for each, read `sessions/{session_id}` and branch on its `status` per the **total mapping** above (`done`→`completed`, `failed`→`failed`+`error_message`, `awaiting_clarification`→`needs_clarification`, else timeout-or-wait). On `done`, read `sessionResults/{session_id}` + the `evidence` subcollection, apply the E5 projection, and populate `final_probability`, `confidence`, `tier`, `agent_version`, `agent_evidence_summary`, `forecast_completed_at`. Closes runs whose dispatched count == terminal count (terminal now includes `needs_clarification`). | Gate 1, Gate 2, Gate 3 | `calibration/services/harvest_service.py` (new) |
| T10B.5 | Extend `calibration/cli.py` with `dispatch-one`, `dispatch-all-open`, `harvest-pending`, `show-forecast --question-id <id>`. | Gate 1 | `calibration/cli.py` |
| T10B.6 | Add Phase 10B env vars: `FIREBASE_PROJECT_ID=anizai-ai`, `CALIBRATION_DISPATCH_TIMEOUT_MIN=120`. | — | `calibration/config.py` |
| T10B.7 | Gate 1 unit tests for `firestore_client.py` (mocked Admin SDK), `evidence_projection.py` (fixture-driven from real SessionResult dumps captured during Sprint 20 E2E). | Gate 1 | `tests/test_calibration/test_firestore_client.py`, `test_evidence_projection.py` (new) |
| T10B.8 | Gate 2 unit tests for `dispatch_service.py` (mocked Firestore + testcontainer Postgres — assert **both** documents written, in order, with the `metadata.calibration` block and `userId` sentinel on each; assert no `calibration_forecasts` row when the second write fails) and `harvest_service.py` (**one fixture session per branch of the total state mapping: `done`, `failed`, `awaiting_clarification`, in-progress-within-window, in-progress-past-timeout**). | Gate 2 | `tests/test_calibration/test_dispatch_service.py`, `test_harvest_service.py` (new) |
| T10B.9 | Gate 3 integration test: against Firebase emulator, run `dispatch-one` on a seeded question, **assert `sessions/{id}` exists before `forecastQueries/{id}`**, simulate the agent (test fixture writing SessionResult + evidence subcoll into the emulator), run `harvest-pending`, assert the forecast row is `completed` with all fields populated. Plus a **negative test**: assert that a dispatch touches no document outside `sessions/{our-id}` and `forecastQueries/{our-id}` — the machine-checkable form of N5/N6. | Gate 3 | `tests/test_calibration/test_phase_10b_e2e.py` (new) |
| T10B.10 | E2E (operator-driven): against the live cloud-deployed agent + real `anizai-ai` Firestore + Cloud SQL: dispatch one question, wait, harvest, inspect the row. Capture round-trip latency (expected: 30–60s end-to-end including agent processing). | E2E | live cloud agent + Firestore + Cloud SQL |
| T10B.11 | Update **§14** — close the Phase 10A row, open the Phase 10B row. | — | this file, §14 |

### Constants introduced (Phase 10B)

- `CALIBRATION_DISPATCH_TIMEOUT_MIN = 120` — after this many minutes in `dispatched` state, a forecast is flagged `timed_out`.
- `EVIDENCE_PROJECTION_VERSION = "1.0"` — written into every `agent_evidence_summary` payload so future schema changes can be detected and back-projected if needed.

### Error-handling paths (Phase 10B)

| Failure | Handling |
|---|---|
| Firestore write fails on dispatch | Per-question try/except. Forecast row not inserted. Run summary records the count. Question stays `open` and gets re-attempted next cycle. |
| **Session doc written, queue doc write fails** *(added 2026-07-25)* | No `calibration_forecasts` row is inserted (the row is written only after both succeed), so the orphan session is invisible to every metric and to the run summary. No worker will claim it — `forecastQueries` has no entry. Swept by the 24h orphan cleanup in §2.5. Logged at ERROR; the question is re-attempted next cycle with a **fresh** `sessionId`. |
| sessionResults missing or malformed at harvest time | Forecast row stays `dispatched` until either sessionResults appears or the timeout expires. Logged at INFO. |
| sessionResults reports `status='failed'` | Forecast row → `status='failed'`; `error_message` copied from `errorMessage` field on the session doc. Excluded from Brier aggregations. |
| **Session reaches `awaiting_clarification`** *(added 2026-07-25)* | Forecast row → `status='needs_clarification'`. **Terminal in V1** — calibration does not answer clarifications (§11), so the row is closed rather than left to age into `timed_out`. Excluded from Brier aggregations, but counted and surfaced on the dashboard Overview: a rising clarification rate is a real signal about question phrasing, not noise to hide. |
| Evidence subcollection missing or empty | Projection returns `{evidence_count_total: 0, ...}` — does not fail the harvest. Logged at WARN. |
| Polymarket condition_id changed since pickup | Out of scope V1. Logged as a known gap; manual operator triage. |

### Acceptance criteria — Phase 10B

- All Gate 1, Gate 2 tests pass.
- Gate 3 E2E test passes against Firebase emulator.
- Operator-driven E2E: one question dispatched → completed forecast row in Cloud SQL with `agent_version` reported by the live agent (currently `0.5.0-sprint26+<git-sha>` — the git-sha-suffixed form landed in Sprint 26; do **not** assert a `>=` string comparison against it, record it verbatim), `final_probability` ∈ [0,1], non-empty `agent_evidence_summary`.
- **No agent code changed** (N1) and **no pre-existing session touched** (N6) — verified by the T10B.9 negative test and by a `git diff --stat agent/` showing zero lines.

---

## §8 — Phase 10C — Scoring & Metrics Layer

### Sprint scope

Compute Brier scores on resolution events; compute and snapshot the calibration
curve, per-cohort Brier, and source-contribution rollups. End of sprint: `python
-m calibration.cli compute-metrics` generates a snapshot row for every metric type
and prints a human-readable summary.

### Confirmed design decisions

- All E1–E5, F1–F3 from §3 apply.
- Brier computation is **transactional with resolution insertion** — when `resolution_service` writes a row, in the same transaction it backfills `brier_score` on every existing forecast for that question.
- Aggregations (calibration curve, cohort, source contribution, improvement curve) are **stateless pure functions** that read from Postgres and return Pydantic models. Snapshots are persisted on every resolution event + on a daily Cloud Scheduler trigger.
- Improvement-delta semantics: per question, take the forecast with the lowest `forecast_run_index` (call it `original`) and the one with the highest (`latest`). Delta = `original.brier_score - latest.brier_score`. Aggregated per cohort + per `(original.agent_version, latest.agent_version)` pair.
- Source-contribution rollup: for each `vault_type ∈ {knowledge, social, momentum, mapping, reactive_search}`, compute mean Brier when that vault type appears in `agent_evidence_summary.vault_types_present` vs. when it doesn't. Reported as a single delta plus a count for statistical context.
- Ambiguous resolutions are excluded from every aggregation. They appear in raw lists but do not influence Brier means.
- **The inclusion rule, stated once and positively (added 2026-07-25).** A forecast contributes to Brier and to every aggregation **iff** `calibration_forecasts.status == 'completed'` **AND** the question has a resolution row **AND** that resolution's `outcome != 'AMBIGUOUS'`. Everything else — `failed`, `timed_out`, `needs_clarification`, `dispatched`, and any forecast on an unresolved or ambiguously-resolved question — is excluded. Excluded rows are still **counted and displayed**: the Overview screen reports failed / timed-out / needs-clarification totals, because a metric that silently drops a third of its inputs is worse than no metric. `YES = 1.0`, `NO = 0.0`, `AMBIGUOUS = NULL`.

### Task table

| Task | Description | Gate(s) | Files / Refs |
|---|---|---|---|
| T10C.1 | Implement `calibration/metrics/brier.py` — pure function `compute_brier(probability: float, outcome_numeric: float) -> float`. Plus `backfill_brier_for_question(question_id, conn)` that runs inside a transaction and updates all forecast rows. | Gate 1 | `calibration/metrics/brier.py` (new) |
| T10C.2 | Update `services/resolution_service.py` from Phase 10A — when inserting a resolution, in the same transaction call `backfill_brier_for_question`. | Gate 2 | `calibration/services/resolution_service.py` |
| T10C.3 | Implement `calibration/metrics/calibration_curve.py` — pure function over forecast/resolution rows. Returns 5 bucket points `[{bucket, count, mean_predicted, actual_yes_rate, lower_bound, upper_bound}, ...]`. Wilson-score 95% CIs for actual rates (cosmetic — useful in the UI). | Gate 1, Gate 2 | `calibration/metrics/calibration_curve.py` (new) |
| T10C.4 | Implement `calibration/metrics/cohort_brier.py` — mean Brier per cohort + an `all` aggregate. Returns `{cohort, n, mean_brier, std_brier}`. | Gate 1, Gate 2 | `calibration/metrics/cohort_brier.py` (new) |
| T10C.5 | Implement `calibration/metrics/improvement_curve.py` — for each resolved question, compute (original_brier, latest_brier, delta, cohort, agent_version_pair). Aggregated as a series ordered by resolution date. | Gate 1, Gate 2 | `calibration/metrics/improvement_curve.py` (new) |
| T10C.6 | Implement `calibration/metrics/source_contribution.py` — per vault type, compute mean Brier with vs. without that type, plus counts. | Gate 1, Gate 2 | `calibration/metrics/source_contribution.py` (new) |
| T10C.7 | Implement `calibration/metrics/snapshots.py` — orchestrator. Computes every metric type, persists each as a `calibration_metrics_snapshots` row. Called from resolution_service after every resolution + standalone via CLI / Cloud Scheduler. | Gate 2 | `calibration/metrics/snapshots.py` (new) |
| T10C.8 | Extend CLI: `compute-metrics`, `show-curve`, `show-cohort-brier`, `show-improvement`. | Gate 1 | `calibration/cli.py` |
| T10C.9 | Gate 1 unit tests: Brier math (5 cases including 0.0 and 1.0 edges), bucket assignment edge cases (exactly 0.2, exactly 0.8), Wilson interval correctness on 0/0 and 1/1 cases. | Gate 1 | `tests/test_calibration/test_brier.py`, `test_calibration_curve.py` (new) |
| T10C.10 | Gate 2 integration tests: against testcontainer Postgres pre-seeded with synthetic forecast/resolution data, verify each aggregation produces the expected snapshot payload. | Gate 2 | `tests/test_calibration/test_metrics_integration.py` (new) |
| T10C.11 | Gate 3 end-to-end (using the synthetic fixture from Phase 10B): dispatch fixture → harvest fixture → fake-resolve fixture → compute_metrics → assert snapshot rows of all 5 types exist with valid payloads. | Gate 3 | `tests/test_calibration/test_phase_10c_e2e.py` (new) |
| T10C.12 | Update **§14** — close the Phase 10B row, open the Phase 10C row. | — | this file, §14 |

### Acceptance criteria — Phase 10C

- All Gate 1, Gate 2, Gate 3 tests pass.
- CLI commands print readable summaries (calibration curve as ASCII table, cohort Brier, improvement series).
- One synthetic full cycle (3 questions × 2 forecasts each × resolution) produces all 5 metric snapshot rows with non-trivial payloads.

---

## §9 — Phase 10D — Cloud Automation: Cloud Run + Cloud Scheduler

### Sprint scope

Containerize. Deploy. Wire up Cloud Scheduler. End of sprint: a single Cloud Run
service runs all four scheduled tasks unattended; one full unattended weekly cycle
(discover → dispatch → harvest → resolve → compute_metrics) completes successfully.

### Confirmed design decisions

- All G1–**G8** from §3 apply. G8 (cost guardrails + kill switch) is new in the 2026-07-25 revision and is this sprint's main hardening deliverable.
- **Hard prerequisite**: Phase 9 (Cloud Deployment) closed — cloud agent reachable. Phase 9 closed 2026-05-10, so this prerequisite is satisfied.
- FastAPI is the web framework. One `server.py`, five task endpoints, `/healthz`, and the `/api/*` routes. *Revised 2026-07-25:* because 10E is now built **before** 10D (see the alias table in §2), the `/api/*` routes already exist and are real by the time this sprint runs — there are no stubs to fill in. This sprint containerizes and schedules an application that is already complete and already has a working dashboard pointed at it.
- Cloud Scheduler invokes endpoints via OIDC tokens. The Cloud Run service's invoker IAM is restricted to two principals: the calibration-runner GSA itself (for self-invocation if needed) and the cloud-scheduler GSA.
- Operator can manually trigger any `/tasks/*` endpoint via `gcloud run services proxy` + curl with an OIDC token (documented in `calibration/docs/OPERATOR_RUNBOOK.md`).

### Task table

| Task | Description | Gate(s) | Files / Refs |
|---|---|---|---|
| T10D.1 | Implement `calibration/server.py` — FastAPI app. Mounts `/healthz`, `/tasks/discover`, `/tasks/dispatch`, `/tasks/harvest`, `/tasks/resolve`, `/tasks/snapshot_metrics`. Each task endpoint is a thin wrapper over the corresponding service from 10A-10C. Returns `{status: "ok", summary: {...}}`. | Gate 1, Gate 2 | `calibration/server.py` (new) |
| T10D.2 | Implement OIDC verification middleware on `/tasks/*` — verifies the request bears a valid Cloud Scheduler service-account token. | Gate 1, Gate 2 | `calibration/auth.py` (new) |
| T10D.3 | Author `data-pipeline/infrastructure/Dockerfile.calibration`. Python 3.11 slim, installs from `requirements.lock`, runs `uvicorn calibration.server:app`. | Gate 1 | `infrastructure/Dockerfile.calibration` (new) |
| T10D.4 | Build + push image to Artifact Registry. Tag `:0.1.0` and `:latest`. | — | gcloud script |
| T10D.5 | Provision GSA `calibration-runner@anizai-pipeline.iam.gserviceaccount.com`. Grant: `roles/cloudsql.client` on the calibration DB; `roles/secretmanager.secretAccessor` (project-level, mirroring the Phase 9A pattern); `roles/datastore.user` on `anizai-ai` (cross-project). | — | gcloud script `infrastructure/gcp/c10_create_calibration_gsa.sh` |
| T10D.6 | Deploy Cloud Run service `calibration-runner` (us-central1, min=0, max=2, concurrency=20, --add-cloudsql-instances, --service-account=calibration-runner@..., env vars from Secret Manager per G4). | Gate 3 | gcloud script `infrastructure/gcp/c10_deploy_cloud_run.sh` |
| T10D.7 | Provision Cloud Scheduler GSA `calibration-scheduler@anizai-pipeline...`. Grant `roles/run.invoker` scoped to the calibration-runner service. | — | gcloud script |
| T10D.8 | Create 4 Cloud Scheduler jobs: `calibration-discover-hourly` (`0 * * * *`); `calibration-harvest-5min` (`*/5 * * * *`); `calibration-resolve-hourly` (`15 * * * *`); `calibration-weekly-reforecast` (`0 2 * * 0`). All UTC. All targeting the corresponding `/tasks/*` endpoint with OIDC. | Gate 3 | gcloud script `infrastructure/gcp/c10_create_schedulers.sh` |
| T10D.9 | Author `calibration/docs/OPERATOR_RUNBOOK.md` — how to manually trigger each task, how to read the logs, how to inspect the DB, how to roll back a deploy, **and how to stop the system by each of the three kill-switch levers (§2.5), written so someone who has never seen this code can execute it**. | — | `calibration/docs/OPERATOR_RUNBOOK.md` (new) |
| T10D.9a | **Implement the G8 guardrails and the kill switch.** `CALIBRATION_ENABLED` short-circuits every `/tasks/*` handler before any I/O; `CALIBRATION_MAX_OPEN_QUESTIONS` bounds discovery; `CALIBRATION_MAX_FORECASTS_PER_RUN` bounds dispatch **and logs every truncation** (a silent cap reads as full coverage); `CALIBRATION_DISPATCH_CONCURRENCY` bounds in-flight dispatches. | Gate 1, Gate 2 | `calibration/config.py`, `calibration/server.py`, `calibration/services/dispatch_service.py` |
| T10D.9b | **Structured logging.** Every task handler emits one JSON summary line per invocation (`task`, `run_id`, counts, duration, `truncated`), using the same `python-json-logger` setup the pipeline producers use. This is the only observability surface the operator has during an unattended week. | Gate 1 | `calibration/logging.py` (new) |
| T10D.10 | Gate 1: `pytest` against the FastAPI app via TestClient. Each `/tasks/*` endpoint returns 200 on a happy path with mocked services, 401 without OIDC. | Gate 1 | `tests/test_calibration/test_server.py` (new) |
| T10D.11 | Gate 2: integration test, FastAPI app + testcontainer Postgres + emulator Firestore. Drive the four task endpoints in sequence, assert state changes. | Gate 2 | `tests/test_calibration/test_server_integration.py` (new) |
| T10D.12 | Gate 3: deploy to staging Cloud Run revision, manually invoke each Scheduler job (one-shot), verify logs + DB state. **Then verify the three properties that make this safe to leave running:** (a) each kill-switch lever independently halts new work; (b) after a halt, the agent, BFF, and frontend are demonstrably unaffected; (c) a full cycle writes **zero** documents outside `sessions/{our-ids}` and `forecastQueries/{our-ids}` — audited via a Firestore query scoped to `userId != "calibration-runner"` over the run window. | Gate 3 | live staging |
| T10D.13 | E2E: let the system run unattended for a full week. Capture: count of forecasts dispatched, count harvested, count resolved (if any), count of metric snapshots written. Cost report from Billing. | E2E | live cloud |
| T10D.14 | Update **§14** — close the Phase 10D row and, with it, Phase 10. | — | this file, §14 |

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

### Rollout ladder (added 2026-07-25)

Do not go from local to a scheduled 30-question batch in one step. Six stages, each
gating the next. Stages 3–4 are what produce the latency numbers that the retired P1
gate was asking for (§0), which is why stages 5–6 are the only ones that wait.

| # | Stage | What must be true to advance |
|---|---|---|
| 1 | **Local only** — services driven from the CLI against a local Postgres, no Firestore. | 10A–10C test suites green. |
| 2 | **Firestore emulator** — full dispatch → simulated agent → harvest round trip. | T10B.9 green, including the negative "touched nothing else" assertion. |
| 3 | **Live Firestore, one question**, manually triggered, real cloud agent. | One `completed` forecast row with a real `agent_version`. Record the observed dispatch→complete latency. |
| 4 | **Live Firestore, five questions**, still manually triggered. | All five terminal. Latency distribution recorded. `CALIBRATION_DISPATCH_CONCURRENCY` tuned against it. |
| 5 | **Full batch, 25–30 questions**, still manually triggered. | No timeouts attributable to concurrency. Cost for the batch measured and extrapolated to a weekly figure. |
| 6 | **Scheduler enabled** — the system runs unattended. | Stage 5 clean, runbook written, all three kill-switch levers verified (T10D.12). |

Each stage is reversible by the kill switch, and no stage requires a code change to
back out of — only a config or scheduler change.

### Acceptance criteria — Phase 10D

- All Gate 1, Gate 2, Gate 3 tests pass.
- Cloud Run service deployed and healthy (`/healthz` returns 200).
- All 5 Cloud Scheduler jobs created and successfully invoking endpoints.
- E2E unattended week: at least one weekly reforecast cycle runs end-to-end without operator intervention.
- Total observed cost for the week is within ±25% of the $0.90 forecast estimate (forecasts only, excluding Cloud Run/Scheduler/Cloud SQL fixed costs).
- **All six rollout stages completed in order**, with the stage-3/4 latency numbers recorded in the runbook.
- **The kill switch works, and is documented well enough for a non-author to use it** — this is the acceptance bar for the whole sprint, not a nice-to-have.

---

## §10 — Phase 10E — Operator API + Dashboard

*Alias: **Sprint 4**. Built fourth, before 10D — see the alias table in §2.*

### Sprint scope

Implement the read-mostly `/api/*` endpoints **and build the operator dashboard
that consumes them**. End of sprint: an operator signs in and can see questions,
forecasts, metrics, and runs against real data.

> **Revised 2026-07-25.** The pre-revision version of this sprint delivered a
> *contract document* for an external "UI collaborator" to implement against. That
> framing is dropped: the dashboard is built in-house, in this sprint, by the same
> people building the API. Consequences — `UI_CONTRACT.md` becomes internal
> documentation rather than a handoff artifact (still worth writing: it is what
> keeps the API honest and is CI-checked by T10E.14); the collaborator-email
> provisioning in G7 goes away; and the sprint's acceptance bar moves from "a
> collaborator could build this" to "the charts render real numbers".

### Confirmed design decisions

- API is read-mostly. Only POST endpoints are `/api/questions` (manual add) and `/api/runs/trigger` (manual one-off cycle for dev/debug).
- All `/api/*` endpoints require Firebase Auth ID tokens. Verify token signature + check email against `FIREBASE_AUTH_OPERATOR_EMAILS` allowlist.
- API serves JSON only; no rendering. UI is a separate Vite/React app deployed to Firebase Hosting (separate site from the user-facing frontend, hosted on the **same `anizai-ai` Firebase project** for auth simplicity, distinct hosting target named `calibration` per G6).
- API responses are paginated where it matters (questions list, forecasts list).
- **The dashboard is a separate Vite + React app** at `data-pipeline/calibration/dashboard/`, built and deployed independently of `client/`. It shares **no** build, route, component, or dependency tree with the user-facing frontend (N3) — it merely happens to use the same libraries (React, Vite, Firebase Auth, Recharts, lucide-react), which is convenience, not coupling.
- **It is a tool, not a product.** Dense, readable, plain. No hero section, no marketing surface, no landing page, no animation. Tables that are legible at 30 rows, charts that are legible at n=4, and every aggregate shown next to its `n`. If a number is excluded from a metric (§8 inclusion rule), the dashboard says so rather than hiding it.
- **Six screens** — Overview, Questions, Question Detail, Metrics, Runs, Manual Add. The pre-revision plan had three tabs and no Overview; Overview is where an operator answers "is the system healthy and is anything stuck?" without reading a table.

### Task table

| Task | Description | Gate(s) | Files / Refs |
|---|---|---|---|
| T10E.1 | Implement Firebase Auth verification middleware on `/api/*` — verifies the ID token, checks email against `FIREBASE_AUTH_OPERATOR_EMAILS` allowlist (initial: `ronking79@gmail.com`; further operator emails added as needed — the secret is hot-swappable without a redeploy, per G7), attaches `request.state.operator_email`. | Gate 1, Gate 2 | `calibration/auth.py` (extend) |
| T10E.1a | **Implement `/api/overview` GET** *(added 2026-07-25)* — the single call that backs the Overview screen: `{openQuestions, resolvedQuestions, completedForecasts, failedForecasts, timedOutForecasts, needsClarification, latestAggregateBrier, latestSnapshotAt, latestAgentVersion}`. One query per counter, no pagination. The failure counters are first-class here by design (§8 inclusion rule). | Gate 1, Gate 2 | `calibration/server.py` |
| T10E.2 | Implement `/api/questions` GET (list with filters: status, cohort, category) and POST (manual add). | Gate 1, Gate 2 | `calibration/server.py` |
| T10E.3 | Implement `/api/questions/{id}` GET — full question detail including all forecasts and (if present) resolution. | Gate 1, Gate 2 | `calibration/server.py` |
| T10E.4 | Implement `/api/questions/{id}/forecasts/compare` GET — original vs. latest forecast with deltas. | Gate 1, Gate 2 | `calibration/server.py` |
| T10E.5 | Implement `/api/metrics/calibration_curve` GET — latest snapshot or computed live (query param `live=true`). | Gate 1, Gate 2 | `calibration/server.py` |
| T10E.6 | Implement `/api/metrics/cohort_brier` GET — same pattern. | Gate 1, Gate 2 | `calibration/server.py` |
| T10E.7 | Implement `/api/metrics/improvement_curve` GET — time-series of resolution events with deltas. | Gate 1, Gate 2 | `calibration/server.py` |
| T10E.8 | Implement `/api/metrics/source_contribution` GET. | Gate 1, Gate 2 | `calibration/server.py` |
| T10E.9 | Implement `/api/runs` GET (list of runs with status/counts) and `/api/runs/trigger` POST (operator-triggered manual cycle for debugging). | Gate 1, Gate 2 | `calibration/server.py` |
| T10E.10 | Author `calibration/docs/UI_CONTRACT.md` — full OpenAPI-style contract per endpoint (request/response shapes, auth requirements, error codes). Now internal documentation rather than a handoff artifact, but still written and still CI-checked (T10E.14) — it is what keeps the API and the dashboard from drifting. | — | `calibration/docs/UI_CONTRACT.md` (new) |
| T10E.11 | **Scaffold the dashboard app** — `calibration/dashboard/`, Vite + React + TypeScript, Firebase Auth sign-in, typed `ApiClient` that attaches the ID token, tab shell (Overview / Questions / Metrics / Runs). Independent `package.json`; nothing in `client/` is touched or imported (N3). | — | `calibration/dashboard/` (new) |
| T10E.11a | **Build the screens** — `<Overview>` (metric cards from `/api/overview`), `<QuestionTable>` + filters, `<QuestionDetail>` (forecast history, resolution, original-vs-latest compare), `<ManualAddForm>`, `<RunsTable>`. Empty states are a first-class case: at the start of Phase 10 every one of these screens has zero rows, and each must say so plainly rather than render a broken chart. | — | `calibration/dashboard/src/` |
| T10E.11b | **Build the charts** — `<CalibrationCurveChart>` (Recharts, diagonal reference + 5 bucket points + Wilson bars), `<CohortBrierChart>`, `<ImprovementCurveChart>`, `<SourceContributionTable>`. **Every chart displays its `n`**; at n<10 it renders the sample-size caveat rather than implying precision it does not have (Risk 6). | — | `calibration/dashboard/src/charts/` |
| T10E.11c | Author `calibration/docs/UI_DEPLOY.md` — Firebase Hosting deploy: create hosting target `calibration` on the `anizai-ai` project, `firebase.json` rewrites, deploy command, env vars (API base URL, Firebase config). | — | `calibration/docs/UI_DEPLOY.md` (new) |
| T10E.12 | Gate 1: per-endpoint TestClient tests (200 + 401 no token + 403 non-allowlisted email + 404 unknown question). | Gate 1 | `tests/test_calibration/test_api.py` (new) |
| T10E.13 | Gate 2: full operator workflow integration test — seed fixture data, simulate three operator actions (list questions, manually add one, view metrics), assert response shapes. | Gate 2 | `tests/test_calibration/test_api_workflow.py` (new) |
| T10E.13a | **Dashboard tests** (Vitest + Testing Library): render Overview with fixture data, render the questions table, **render every screen in its empty state**, render each chart with sample data. Plus `npm run build` and `npm run test` green. | — | `calibration/dashboard/src/**/*.test.tsx` (new) |
| T10E.14 | Gate 3: `pytest tests/test_calibration/test_ui_contract.py` validates the live deployed API against the published `UI_CONTRACT.md` shapes using a recorded operator ID token — the drift check between §11 and reality. | Gate 3 | `tests/test_calibration/test_ui_contract.py` (new) |
| T10E.15 | E2E: operator signs in to the deployed dashboard and confirms all four metric visualizations render against real data — and that the user-facing frontend is unaffected (N3). | E2E | live cloud |
| T10E.16 | Update **§14 of this document** — close the Phase 10E row. | — | this file, §14 |

### Dashboard component list

A single-page React (Vite) app on Firebase Hosting (target `calibration` on the
`anizai-ai` project, per G6), living at `calibration/dashboard/`. Screens and
components:

**1. App shell**
- `<Login>` — Firebase Auth email-link or Google sign-in.
- `<AppShell>` — top nav with four tabs: Overview | Questions | Metrics | Runs. Operator email + sign-out in corner.

**1a. Overview tab** *(added 2026-07-25 — the landing screen)*
- `<MetricCard>` ×N backed by `GET /api/overview`: open questions, resolved questions, completed forecasts, failed, timed out, needs-clarification, aggregate Brier, latest snapshot time, latest agent version.
- The failure counters sit alongside the success ones deliberately. The first question an operator asks is "is anything stuck?", and a dashboard that only shows what worked cannot answer it.

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
- API endpoints documented in `UI_CONTRACT.md` matching live behavior (enforced by T10E.14).
- **Operator can sign in** and see questions, forecasts, metrics, and runs against real data.
- **`npm run build` and `npm run test` pass** in `calibration/dashboard/`.
- **Every screen renders correctly with zero rows** — the state the system is actually in on day one.
- **The user-facing frontend is untouched** (N3): `git diff --stat client/` shows zero lines.
- Phase 10E row closed in §14.

---

## §11 — Cloud Run API Contract (consolidated)

**Auth modes:** `OIDC` = Cloud Scheduler / GSA-to-GSA. `FAUTH` = Firebase Auth ID
token + operator-email allowlist. `NONE` = liveness probes only.

| Method | Path | Auth | Body | Response | Phase |
|---|---|---|---|---|---|
| GET  | `/healthz` | NONE | — | `{status, commit, db, firestore}` | 10D |
| POST | `/tasks/discover` | OIDC | `{}` | `{discovered, already_present, target_count}` | 10D |
| POST | `/tasks/dispatch` | OIDC | `{run_type}` | `{run_id, questions_dispatched}` | 10D |
| POST | `/tasks/harvest` | OIDC | `{}` | `{completed, failed, timed_out, still_pending}` | 10D |
| POST | `/tasks/resolve` | OIDC | `{}` | `{resolved, snapshots_written}` | 10D |
| POST | `/tasks/snapshot_metrics` | OIDC | `{}` | `{snapshots_written}` | 10D |
| GET  | `/api/overview` | FAUTH | — | `{openQuestions, resolvedQuestions, completedForecasts, failedForecasts, timedOutForecasts, needsClarification, latestAggregateBrier, latestSnapshotAt, latestAgentVersion}` | 10E |
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

The calibration-runner reads/writes Firestore on `anizai-ai` (cross-project, via
Workload Identity):

**Writes — TWO documents per dispatch, in this order (CORRECTED 2026-07-25):**

> The pre-revision text of this subsection read *"Writes (one type, one
> collection)"* and specified an auto-ID `forecastQueries` document only. **That was
> wrong and would have failed on the first live dispatch** — the agent's
> `update_session_status` calls `.update()` on `sessions/{sessionId}`, which raises
> `NotFound` when the document does not exist. See A6 and
> `scripts/submit_query.py:68-71`. Both documents are keyed by the **same
> operator-minted `sessionId`**; neither uses an auto-ID.

```
1. sessions/{sessionId}          — written FIRST  (see §7 for the full payload)
2. forecastQueries/{sessionId}   — written SECOND (see §7 for the full payload)
```

Both carry `userId: "calibration-runner"` (A7) and the `metadata.calibration`
block (A4). Nothing else in Firestore is ever written.

**Reads:**
- `sessions/{sessionId}` — `status` (the harvest state machine), `errorMessage`, `latestProbability`.
- `sessionResults/{sessionId}` — the forecast result (top-level per Phase 8B Sprint 20 D6).
- `sessions/{sessionId}/evidence/*` — the evidence subcollection, for the E5 projection.
- `forecastQueries/{sessionId}` — only to confirm its own dispatch landed.

Every read is a **direct document lookup by an ID calibration itself minted** —
the harvester drives from Postgres, not from a Firestore collection scan, so it
cannot encounter a document it did not create. The calibration-runner **never**
writes to `sessionResults`, to any session subcollection, or to any document it
does not own (N5, N6).

---

## §12 — Known Risks

1. **Polymarket API drift.** A schema change in Gamma or CLOB silently breaks discovery or resolution. *Mitigation:* `raw_resolution_data JSONB` preserves full audit trail; `evidence_projection.py` and `polymarket/resolve.py` have schema-version guards (`projection_version` field on outputs); fixture-driven Gate 3 tests catch most shape drift in CI.
2. **Slow agent → harvest timeouts.** If forecast latency regresses past the 120-min `CALIBRATION_DISPATCH_TIMEOUT_MIN`, lots of forecasts get marked `timed_out` and the weekly cycle reports degraded. *Mitigation:* timeout is env-overridable; harvester's `still_pending` count surfaces the issue early in Cloud Logging.
3. **Cross-project IAM friction.** Cloud Run on `anizai-pipeline` writing to Firestore on `anizai-ai` requires the same WI pattern Phase 9D established. Phase 9 closed cleanly 2026-05-10 so the pattern is proven; if a regression appears here, see Phase 9D's gcp-deployment runbook. *Mitigation:* 10D inherits the existing Workload Identity bindings.
4. **Cost overrun on bad weeks.** A failure mode where dispatch succeeds but harvest never sees results (Firestore index miss, agent crash loop) keeps re-dispatching the same questions. *Mitigation:* harvester closes runs when all rows are terminal; dispatcher refuses to dispatch a question that already has an active forecast for the current run_id. Budget alerts already armed in Phase 9A (₪200/₪400) catch billing surprises.
5. **Resolution detection lag.** Polymarket may show a market as "settled" hours after the actual outcome event. The hourly resolver may report resolutions a day late on slow markets, distorting "resolved within cohort" reports. *Mitigation:* not user-impacting; the Brier scores eventually become correct.
6. **Improvement-loop interpretation pitfall.** Improvement deltas are noisy at small N. Three resolved questions with bad luck could read as "agent regressed." *Mitigation:* UI surfaces `n` alongside every aggregate; Wilson intervals on calibration curves; per-cohort splits prevent lumping short and long horizons.
7. **Calibration questions could leak into the agent's training data via vault refresh.** If the agent's vault picks up news articles about a Polymarket market that calibration is forecasting, the forecast becomes self-referential. *Mitigation:* this is the actual behavior under test — the calibration system is *measuring whether the agent is well-calibrated using the data it actually has*. Documented as feature, not bug.

---

## §13 — Acceptance Criteria — Phase 10 (V1 Done)

- [ ] Phase 10A merged: schema applied to Cloud SQL, ~25–30 questions seeded, resolution detection validated against a live resolved market.
- [ ] Phase 10B merged: end-to-end dispatch → harvest works against the live cloud agent.
- [ ] Phase 10C merged: scoring + 5 metric snapshot types written; CLI dumps human-readable summaries.
- [ ] Phase 10D merged: Cloud Run service deployed, all 5 Cloud Scheduler jobs active, one full unattended weekly cycle observed.
- [ ] Phase 10E merged: full operator API live + documented; the dashboard renders all four metric views against real data, and every screen renders correctly when empty.
- [ ] §14 of this document: all five Phase 10 rows closed.
- [ ] `OPERATOR_RUNBOOK.md` covers: how to manually trigger a dispatch, how to debug a stuck forecast, how to inspect the DB, how to read the metrics snapshots, how to roll back the Cloud Run revision, **and how to stop the system by each of the three kill-switch levers**.
- [ ] `UI_CONTRACT.md` matches the deployed API exactly (CI-checked via T10E.14).
- [ ] One full improvement loop observed end-to-end: 7-day cohort question resolves → Brier computed → metrics snapshot updates → second forecast for the same question (taken pre-resolution) shows up in the improvement curve.
- [ ] Total cost across one full week: forecasts ≤ $1.20 (forecast-only, ±25% of $0.90 estimate); fixed costs (Cloud Run + Cloud SQL + Cloud Scheduler) < $15/week.

### Safety criteria (added 2026-07-25) — these are pass/fail, not best-effort

The functional criteria above describe a system that works. These describe a system
that is safe to leave running, and any one of them failing blocks V1 regardless of
how well the rest performs:

- [ ] **Zero lines changed in `data-pipeline/agent/`** (N1) — `git diff --stat agent/`.
- [ ] **Zero lines changed in `server/`** (N2) and **in `client/`** (N3).
- [ ] **Zero writes to any vault, ingestion, or processing module** (N4).
- [ ] **No pre-existing session read, modified, or deleted** (N6) — verified by the T10B.9 negative test and the T10D.12 live audit.
- [ ] **Every calibration session carries `metadata.calibration.enabled == true` and `userId == "calibration-runner"`** — no exceptions, verifiable by a single Firestore query returning zero unmarked rows.
- [ ] **The kill switch works**, all three levers, and stopping calibration leaves the agent, BFF, and frontend unaffected.
- [ ] **A runbook exists** that a person who has never seen this code can use to start, inspect, and stop the system.

---

## §14 — Sprint Status Ledger

*Added 2026-07-25. This replaces the `task_plan.md` that earlier revisions of this
document referenced — that file does not exist in this repository. Update this table
after every task, per the convention in §1.*

Build order is the alias order (§2): 10A → 10B → 10C → 10E → 10D.

| # | Sprint | Alias | Status | Started | Closed | Notes |
|---|---|---|---|---|---|---|
| 1 | **Phase 10A** — Foundation: schema + Polymarket adapter | Sprint 1 | **Built and verified — local only** | 2026-07-25 | 2026-07-27 | Schema applied to a real Postgres 16, full write path exercised, discovery run end-to-end against live Polymarket (18 questions inserted, idempotent on re-run). **Open: the 14d cohort cannot be filled — F5 / Q4.** |
| 2 | **Phase 10B** — Forecast Bridge: dispatch + harvest | Sprint 2 | **Built and verified — emulator only** | 2026-07-27 | — | Both dispatch documents written in the A6 order, verified against a live Firestore emulator including the write-ordering invariant. All five harvest branches proven end to end. **Not yet run against live Firestore or the real agent — that is rollout stage 3 and is gated on operator review.** |
| 3 | **Phase 10C** — Scoring + Metrics | Sprint 3 | **Built and verified** | 2026-07-27 | 2026-07-27 | Brier, calibration curve, cohort Brier, improvement curve, source contribution, snapshots. Verified through a 12-question / 22-forecast cycle against real Postgres + emulator. Pure computation — nothing here needs the cloud. |
| 4 | **Phase 10E** — Dashboard + API | Sprint 4 | **Built and verified — local only** | 2026-07-27 | — | FastAPI operator API (auth + allowlist, 5 metric endpoints, questions, runs, manual add) and a separate Vite/React dashboard. 470 Python tests + 45 dashboard tests; `npm run build` clean. Verified live against real data through the API and the CLI. **Not deployed — Firebase Hosting is Phase 10D.** |
| 5 | **Phase 10D** — Cloud Automation + Hardening | Sprint 5 | Not started | — | — | Gated on 10E. Follows the six-stage rollout ladder in §9. |

### Implementation deviations from this plan (recorded, not silent)

| # | Plan said | Built as | Why |
|---|---|---|---|
| D1 | psycopg3, async (T10A.3) | **psycopg2, sync** | The repository pins `psycopg2-binary` and every `persistence/` module is sync. Calibration is a batch workload run a few times an hour; it has no concurrency requirement that justifies a second Postgres driver and an async test harness in a codebase that has neither. |
| D2 | Click-based CLI (T10A.12) | **argparse (stdlib)** | Click is not currently a dependency. A package whose defining property is that it drags nothing new into the project should not add one for a seven-command operator tool. Revisit if the command surface outgrows argparse subparsers. |
| D3 | `config/settings.py` permitted as the one cross-package import (§6) | **No cross-package imports at all** | `config/settings.py` has import-time side effects (creates `DATA_DIR`, loads the pipeline `.env`) and exposes the vault connection constants. A duplicated four-line `load_dotenv` is cheaper than a dependency on that. Enforced by `tests/test_calibration/test_isolation.py`. |
| D4 | Discovery fetches active markets and filters client-side (T10A.6) | **One narrow server-side query per cohort window** | Forced by finding F3: Gamma refuses offsets past ~2000, so a broad scan cannot reach the whole exchange and an unfiltered walk returns an arbitrary slice. The client-side filters remain as a second pass — the server cannot filter on category, and server filtering is not trusted blindly. |

### Live Polymarket findings (measured 2026-07-25 during Phase 10A)

Five behaviours of the Gamma API that the plan assumed wrongly or did not
address. Four were found only because the adapter was pointed at the live API
during the sprint rather than at fixtures alone; all four produced *plausible
but wrong* results rather than errors, which is the failure mode this whole
system exists to detect and so is worth recording carefully.

| # | Finding | Consequence | Status |
|---|---|---|---|
| F1 | **`/markets` omits the `tags` key entirely unless `include_tag=true` is passed.** Not an empty list — the key is absent. | Every market failed categorisation. Discovery reported scanning 2000 markets and finding 0 candidates: a confident, detailed, empty answer. | **Fixed** — the param is now always sent, and a test asserts it. |
| F2 | **Gamma caps `limit` server-side at 100** regardless of what is requested. | The original walk treated "page shorter than requested" as "last page" and stopped after 100 markets out of thousands. | **Fixed** — the walk advances by records received and terminates on an empty page. |
| F3 | **Offset-based pagination is refused past ~2000** (offset=2000 succeeds, offset=3000 returns 422 pointing at `/markets/keyset`). | A run with a higher cap crashed outright, discarding every page already collected. | **Fixed** — the ceiling is caught, the walk keeps what it has and warns. Keyset pagination remains unimplemented and is not needed given F4. |
| F4 | **Gamma supports server-side `end_date_min`, `end_date_max`, `volume_num_min`, `order`, `ascending`.** | Discovery no longer scans broadly. It issues one narrow query per cohort window with that cohort's liquidity floor. Measured effect: 343 markets fetched instead of 2000, and **209 candidates instead of 4**. Given F3 this is not an optimisation — a broad scan can never reach the whole exchange, so anything the server can filter, it must. | **Adopted** — `fetch_markets_in_window` + `discovery_service.fetch_cohort_windows`. |
| F5 | **Polymarket resolution dates cluster hard on month boundaries** (July 31, August 31). The 12–16 day window therefore falls in a structural dead zone: only 5 markets existed in it above the $50k floor, against a target of 10. | **The 14d cohort cannot be filled as specified.** The first live discovery run inserted 10/10 for 7d, 8/8 for 30-45d, and 0/10 for 14d. | **Open — needs a decision, see Q4.** |

### Open questions carried into the build

| # | Question | Resolve by |
|---|---|---|
| Q1 | Does the live agent tolerate `metadata` on inbound `forecastQueries` without complaint? Expected yes — there is no inbound schema validation — but it is asserted, not verified, until a live dispatch runs. | Rollout stage 3 (§9) |
| Q2 | What is the real dispatch→complete latency distribution under concurrency? Determines the final value of `CALIBRATION_DISPATCH_CONCURRENCY` and whether the 120-minute timeout is right. | Rollout stages 3–4 |
| Q3 | What fraction of Polymarket-sourced questions trip the agent's clarification branch? A high rate would mean the question text needs pre-processing before dispatch — but V1 measures it rather than fixing it. | After the first full batch |
| Q4 | **How should the 14d cohort respond to F5?** Four options, none obviously right, and the choice changes what the cohort comparison means: (a) **widen the window** to 10–20 days so it catches month-end clusters — cheapest, but blurs the horizon separation the cohorts exist to measure; (b) **lower the 14d liquidity floor** below $50k — keeps the horizon clean, admits noisier implied probabilities; (c) **accept a smaller 14d cohort** and report its `n` honestly — statistically weakest but most truthful; (d) **redefine cohorts around month boundaries** (this-month-end / next-month-end / two-months-out), which matches how the exchange actually behaves rather than how we assumed it does. **Not decided unilaterally — this is a measurement-design question, not an implementation detail.** | Before Phase 10B dispatch |

---

## §15 — Changelog

### 2026-07-31 — Constraints from the agent's owners; cloud descoped

The team that runs the agent set out how calibration may use the shared
infrastructure. Their constraints changed the design in three places, and one
of them removed a sprint's worth of planned work.

**Granted:** `roles/datastore.user` on `anizai-ai` — the identity works
against Firestore.

**Refused, correctly:** Secret Manager. All 15 secrets there are pipeline
credentials (OpenAI, Postgres, Telegram, Airflow, source API keys). There is
no calibration secret among them and there should not be. **The request is
withdrawn** — with the database moving off their project, calibration needs
nothing from that store.

**Moot:** `roles/cloudsql.client` was granted, but there is no Cloud SQL
instance in `anizai-pipeline` and none will be created there.

#### Phase 10D is descoped, not deferred

The stated requirements were: results as files in a repository folder, no
automatic export mechanism, hard caps on run size, and a demo in a month.
Under those, provisioning Cloud SQL + Cloud Run + Cloud Scheduler in a new GCP
project is a full infrastructure stack built to run one command a day.

**What replaces it:** local Postgres in Docker, manual capped runs, and
`cli export` writing to `results/`.

The Phase 10D *code* stays — the `/tasks/*` endpoints, OIDC verification, the
Dockerfile, and `infrastructure/provision.sh` are written, tested, and ready —
so adopting cloud automation later becomes a provisioning decision rather than
a development one. Nothing is discarded; it is simply not switched on.

The two service accounts created in `anizai-pipeline` on 2026-07-29 hold no
permissions and can be deleted.

#### Two dispatch ceilings, not one

The concern raised was a stuck loop burning the shared OpenAI daily request
quota before a demo. A per-run cap does not address that: a caller that
dispatches 3, fails to harvest, and retries every five minutes respects the
per-run cap perfectly while emitting 864 forecasts a day.

| Ceiling | Value | Protects against |
|---|---|---|
| `CALIBRATION_MAX_FORECASTS_PER_RUN` | **3** (was 30) | One oversized run |
| `CALIBRATION_MAX_FORECASTS_PER_DAY` | **30** | A caller stuck in a loop |

The daily ceiling counts dispatches in a rolling 24-hour window from the
database, so it holds regardless of how the caller behaves. Reaching it
**raises** rather than dispatching zero: arriving there means something is
looping, and a silent no-op would let it keep looping unnoticed.

The per-run default dropped from 30 to 3 because a default that quietly sends
30 forecasts is a default that eventually sends 30 by accident. A sanity check
needs two or three; anything larger should have to be typed out.

#### The demonstration state is now a tested state

The system will most likely be shown with zero to three scored forecasts —
markets take weeks to settle, so that is the normal shape of this data for its
first month, not an edge case. Seven dashboard tests render exactly those
states: a curve from three points, a cohort with no data at all, an
improvement chart from a single pair, and an overview where forecasts exist
but none have resolved.

The invariant they all check: **a missing score is never rendered as zero.**
0.0 is a *perfect* Brier score, so an absent one displayed as 0 would claim a
flawless forecaster. Missing renders as an em dash everywhere — dashboard,
CLI, and exported CSVs, where an empty file still carries its header so
"measured, no data yet" stays distinguishable from "something broke".

#### Added

- `calibration/export.py` + `cli export` — nine CSV/JSON files into
  `results/`, with a `how_to_read_this` block written into `summary.json` for
  whoever opens the folder without having been in the conversation.
- `results/README.md` — what each file is, what Brier means, why 0.25 is the
  bar, and what is deliberately excluded from the scores.

**Verification:** 494 Python tests, 52 dashboard tests, clean production build.

### 2026-07-27 (later) — Phase 10E: operator API + dashboard

Built locally. No Firebase Hosting deploy, no cloud resources — deployment is
Phase 10D and remains gated on operator review.

**Built:** `calibration/auth.py`, `calibration/server.py` (FastAPI), and
`calibration/dashboard/` — a separate Vite + React + TypeScript app sharing no
build, route, or component with `client/` (N3).

**Defects found and fixed:**

| # | Defect | How it would have failed |
|---|---|---|
| H1 | `CohortBrierChart` used `domain={[0, 'auto']}`, so the 0.25 coin-flip reference line fell outside the y-domain and Recharts silently discarded it whenever every cohort scored better than 0.25. | The chart would lose its reference point at exactly the moment that point is good news — leaving a reader with a bar of 0.12 and nothing to compare it to. Caught by a chart-render test, not by any assertion on the data. |
| H2 | `_question_row` took `latestProbability` from the highest run index regardless of status, so a question whose newest run failed rendered a Brier score beside a blank probability. | Two numbers from different forecasts side by side, reading as broken data rather than as a failed re-forecast. Found by inspecting live API output, not by a test. |
| H3 | The Phase 10B isolation guard banned every `firebase_admin` import, which `auth.py` legitimately needs for token verification. | Rather than exempting the file, the guard was **re-aimed**: it now bans Firestore *operations* (`.collection(`, `.document(`, `firestore.client(`) outside the gateway, which is both narrower and closer to the property that matters. A second test still confines SDK imports to two named modules. |
| H4 | Chart tests rendered against a 0×0 container in jsdom, so every chart assertion passed while testing an empty SVG. | The suite would have reported chart coverage it did not have. Fixed with a sized ResizeObserver stub, and a zero-size Recharts warning is now promoted to a hard test failure so the stub cannot rot silently. |

**Design decisions:**

- **The allowlist is the real authorisation gate, and it fails closed.** Firebase Auth on `anizai-ai` issues valid tokens to any product user, so verification proves "a real person", not "an operator". An empty `FIREBASE_AUTH_OPERATOR_EMAILS` denies everyone — the alternative reading (empty = allow all) points a misconfiguration in the worst possible direction.
- **401 and 403 stay distinct** end to end. 401 means sign in again; 403 means signing in again will not help. Collapsing them sends an operator round a loop that cannot succeed.
- **`POST /api/runs/trigger` runs discovery only, never dispatch.** Discovery costs a few HTTP calls; dispatch spends tokens. A button that can spend money should not be one click from a button that cannot, and the UI states this rather than relying on the operator knowing.
- **A missing number renders as an em dash, never as zero.** 0.0 is a *perfect* Brier score, so a null shown as 0 would display a flawless forecast where there is no data.
- **Every aggregate ships with its `n`, and caveats live in the payload.** `improvement_curve.interpretable` and `source_contribution.interpretation` come from the API, so the dashboard cannot render a small-sample delta or a correlational attribution without its warning.
- **Charts follow the data-viz method**: a validated three-slot palette (all-pairs, both modes), one axis per chart, diverging blue↔red only where the value is a polarity, a table twin under every chart so no value is hover-only, and a legend wherever there are two marks.

**Verification:** 470 Python tests (218 pure, 39 Postgres, 14 emulator, 20
full-cycle, 56 API) and 45 dashboard tests; `npm run build` clean. The live
API was exercised against real demo data — `/healthz` ok, `/api/overview`
returning the 12-question cycle, 401 with no token and 403 for a
non-allowlisted account.

**Still not proven:** anything requiring live Firestore or the real agent
(Q1–Q3), and the Firebase Hosting deploy.

### 2026-07-27 — Phase 10B + 10C built and verified locally

Both sprints landed against local backends only: a throwaway Postgres 16 and
the Firebase Firestore emulator. **No live Firestore, no cloud resources, no
real agent.** Rollout stage 3 (live Firestore, one question) remains gated on
operator review, per the boundary set when Phase 10A was authorised.

**Built:**

- `calibration/firestore_client.py` — the single gateway. Document-by-id reads
  and the two dispatch writes; no queries, no collection scans, no claim logic.
- `calibration/evidence_projection.py` — the E5 contract, plus SessionResult
  field extraction with unit normalisation.
- `calibration/services/dispatch_service.py`, `harvest_service.py`.
- `calibration/metrics/` — brier, calibration_curve, cohort_brier,
  improvement_curve, source_contribution, snapshots.
- CLI: `dispatch`, `harvest`, `compute-metrics`, `show-curve`,
  `show-cohort-brier`, `show-improvement`.

**Defects found and fixed during the sprint:**

| # | Defect | How it would have failed |
|---|---|---|
| G1 | `repos.forecasts.list_scorable` reused the unqualified `_COLUMNS` list inside a two-table join. | `AmbiguousColumn` on `id` — every metric read would have raised the moment a resolution existed. Caught by the full-cycle integration test, not by any unit test. |
| G2 | The Phase 10A isolation test forbade *any* Firestore import, which 10B necessarily violates. | Rather than deleting the guard, it was **tightened**: Firestore imports are now confined to `firestore_client.py`, and a second test asserts that module issues no `.where()`, no `.list_documents()`, and no unscoped `.stream()`. The constraint got stronger, not weaker. |

**Decisions taken during implementation:**

- **The two dispatch writes are not transactional.** The orderings fail
  differently and only one fails safely — session-first leaves an orphan
  session with no Postgres row (invisible, sweepable), queue-first leaves a
  claimable query whose session does not exist (the `NotFound` crash A6
  exists to prevent). The benign direction is free; a cross-collection
  transaction would add contention for a race that only matters if the
  process dies inside a two-write window.
- **The Postgres row is written last**, only after both Firestore writes
  return. A row written first would point at a session the agent never
  processes, age into a false `timed_out`, and pollute the failure rate with
  a failure that never happened.
- **`extract_probability` converts a percentage-shaped value rather than
  rejecting it**, and logs. A NULL probability is excluded from Brier
  entirely, so rejecting would quietly shrink the sample instead of
  announcing the problem. Values above 100 are still discarded — those are
  corruption, not units.
- **`dispatch` refuses to run against live Firestore** unless `--allow-live`
  is passed. Without the emulator, a dispatch creates real sessions and
  spends real tokens; that should require typing something.
- **Every metric carries its `n`, and small samples are flagged in the data,
  not only in the rendering.** `improvement_curve.interpretable` is False
  below 10 paired questions and `source_contribution.interpretation` carries
  the observational-not-causal caveat as a field. The numbers will outlive
  anyone's memory of the caveats.

**Verification:** 411 tests pass — 218 pure-function, 39 Postgres integration,
14 Firestore emulator, and 20 full-cycle tests that drive
discover → dispatch → simulated agent → harvest → resolve → metrics across
both backends together. A 12-question / 22-forecast demo cycle produced a
coherent calibration curve (aggregate Brier 0.1712, skill +0.315), per-cohort
scores ordered as the design predicts (7d best), and a correctly-excluded
AMBIGUOUS resolution.

**Still not proven:** that the live agent tolerates the `metadata` block on
inbound `forecastQueries` (Q1), the real latency distribution (Q2), and the
clarification rate on real Polymarket phrasing (Q3). All three need rollout
stage 3.

### 2026-07-25 — Revision against the operator plan

Reconciled this document with the operator-authored calibration plan. The two
agreed on goals, architecture, and sprint shape; this revision folds in the
differences. **Two of them were defects in this document, not preferences.**

**Corrections — the pre-revision plan would not have worked as written:**

1. **Dispatch writes two documents, not one** (A6, §7, §11). The plan specified an auto-ID `forecastQueries` document only. The agent's `update_session_status` uses Firestore `.update()`, which raises `NotFound` when `sessions/{sessionId}` is absent — so every dispatch would have been claimed and then immediately failed. Now: `sessions/{sessionId}` first, `forecastQueries/{sessionId}` second, same operator-minted ID, per `scripts/submit_query.py`.
2. **`needs_clarification` was a missing terminal state** (§4 Table 2, §7, §8). The agent can end a run at `awaiting_clarification` with no SessionResult. The old status enum had nowhere to put it, so it would have been mislabelled `failed` or left to age into a 120-minute `timed_out`. Added to the CHECK constraint, to the harvest state mapping, and to the Overview counters.

**Decisions settled:**

3. **`metadata.calibration` namespace replaces the two top-level side fields** (A4). Taking the namespaced form now costs nothing and carries `forecastRunIndex`, which the old form had no place for.
4. **`userId: "calibration-runner"` sentinel** (A7) — added; the plan had no owner field at all.
5. **P1 (latency gate) downgraded** (§0). It gated on a "100+ parallel forecasts" profile Phase 10 does not have, and was circular — the harness produces the latency baseline it was blocked on. Replaced by G8's concurrency cap and scoped to rollout stages 5–6.
6. **Separate Postgres instance confirmed** (P6, B1) — operator decision; a schema on the pipeline DB was considered and rejected.
7. **The dashboard is built in-house** (§10). The "UI collaborator" handoff framing is dropped throughout; `UI_CONTRACT.md` survives as internal drift-protection.
8. **Build order is 10A → 10B → 10C → 10E → 10D** (§2 alias table) — dashboard before cloud automation, so the rollout is observable.

**Added:**

9. **§2.5 Non-Negotiables** — the seven red lines (N1–N7), the calibration session marker, and the three-lever kill switch, promoted from prose into hard constraints with machine-checkable acceptance criteria (§13 safety criteria).
10. **G8 cost guardrails** — `CALIBRATION_ENABLED`, max open questions, max forecasts per run, dispatch concurrency; plus T10D.9a/9b to implement them and structured logging.
11. **Six-stage rollout ladder** (§9) — local → emulator → 1 question → 5 → 25–30 → scheduler.
12. **`/api/overview` endpoint and the Overview screen** (T10E.1a, §10, §11) — the "is anything stuck?" surface the three-tab design had no room for.
13. **The positive inclusion rule for Brier** (§8), stated once: `completed` AND resolved AND not `AMBIGUOUS`. Everything else is excluded but still counted and displayed.
14. **§14 status ledger** and this changelog.

**Repairs to stale references:**

15. `CLAUDE.md`, `project_master.md`, and `task_plan.md` were cited throughout and **none of the three exist in this repository**. All references retargeted — sprint status to §14, engineering context to `B_hub/hub_overview.md` and `B_hub/hub_agents.md`, and the dispatch reference implementation to `scripts/submit_query.py`.
16. P4 (calibration-specific skills) dropped — the skills it referenced were never authored and the generic ones it named do not exist.
17. Sprint 26 status corrected to closed (2026-07-18); `agent_version` example updated to the git-sha-suffixed form the agent now emits.
