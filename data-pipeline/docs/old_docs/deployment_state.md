# deployment_state.md — What Is Deployed Where (Pipeline + Agent)

> **Purpose:** Single source of truth for *runtime state* — what is actually running on
> GKE, what exists only locally, and the state of the schedulers and the local stack.
> This is a **state/status doc**, not a plan. When deployment changes, update this file.
>
> **Last updated:** 2026-06-13 (reflects: Phase 9 closeout, Phase 9.5 closeout, Sprints 22–23 local closeout)
> **Authoritative for plans, not state:** `task_plan.md`, `task_plan_implementation.md`.
> **Cloud topology reference:** `guides/cluster_operations_guide.md`.

---

## 1. Running on GKE (cloud)

**Cluster:** `anizai-cluster` · project `anizai-pipeline` · zone `us-central1-a` · namespace `anizai`
**Last deployment touch:** Phase 9.5 closeout, **2026-05-20**. Nothing newer than this has been deployed.

**Deployed via:**
- **Phase 9 — Cloud Deployment** (Sprints 9A–9E, internal name C1–C5) — **CLOSED 2026-05-10.** Full local docker-compose stack ported to GKE.
- **Phase 9 follow-up — Flink K8s HA** — **2026-05-19.** `high-availability.type: kubernetes`, ConfigMap leader election.
- **Phase 9.5 — Cluster Robustness** (Stages A/B/C) — **CLOSED 2026-05-20.** Robustness + monitoring; 4 images rebuilt to `*-p95` tags.

**Workloads (per `infrastructure/k8s/` + Phase 9/9.5 records):**

| Workload | Kind | Notes |
|---|---|---|
| Kafka (KRaft) | StatefulSet | `KAFKA_LOG_DIRS=/var/lib/kafka/data/kafka-logs` (9.5-A root-cause fix); kafka-init now an hourly idempotent CronJob |
| Postgres (pgvector + TimescaleDB) | StatefulSet | `publishNotReadyAddresses: true` (9.5-B) |
| Flink JobManager / TaskManager | Deployment | K8s HA enabled (2026-05-19) |
| Airflow scheduler / webserver + airflow-postgres | Deployment / StatefulSet | scheduler liveness → `httpGet :8793/health` (KG-PHASE-C-4) |
| 9 producers | Deployment | polymarket, fred, newsapi, arxiv, hackernews, telegram, openweather, opensky, googletrends |
| Trigger consumer | Deployment | `python -m orchestration.ingestion_trigger_consumer` on `ingestion_triggers` — Running (KG-PHASE8-3 CLOSED C4) |
| agent-worker | Deployment | Firestore worker (cross-project Firestore `anizai-ai` via Workload Identity) |
| Prometheus + Grafana | Deployment | 13 alert rules, Alertmanager via Gmail SMTP (9.5-C) |
| kafka_exporter + postgres_exporter | Deployment | added 9.5-C |

**Deployed agent version:** ⟨verify on cluster⟩ — the cloud agent image dates from Phase 9 (C5, ~2026-05-10) with possible Phase 9.5 `*-p95` rebuild. `AGENT_VERSION` string at that point: `0.4.0-sprint21-clarification-tier2`. **Sprint 22/23 code is NOT in any cloud image** (see §2).

**Node pools:** single `main-pool` (e2-standard-8 ×1), manually scaled to 0 between collection windows. `polymarket-pool` **deleted** in Phase 9.5-A (Polymarket now runs on main-pool).

---

## 2. Built locally, NOT yet deployed to GKE

Both sprints below closed **2026-05-26**, six days after the last cloud touch. Neither has a cloud image build or deploy on record.

| Sprint | What was built | Deploy/verify state |
|---|---|---|
| **Sprint 22 (Revised) — Foundation Fixes** | Wiring of `marketProbability`, `predictionSeries`, `sentimentTimeSeries` bucketing; Polymarket fuzzy-match resolver (`pg_trgm` 0.85); `canonicalKey` on session doc. Closed KG-PHASE8-12, KG-PHASE8-22. | **Local only.** Gates 1–3 + E2E passed locally (session `e2e-sprint22-c31b5da7`). `AGENT_VERSION` unchanged (wiring, not behavior). Not built into a cloud image. |
| **Sprint 23 (New) — Producer-trigger Infrastructure** | NewsAPI `run_reactive()`; `ingestion_triggers` registration; `reactive_triggers_log` table; `trigger_reactive_ingestion` node (built in isolation). | **Local only + partially unverified.** Gate 1 (20/20) + Gate 2 (3/3) pass; **Gate 3 `skipif(win32)` — deferred to Linux/GKE** (kafka-python-ng race, KG-PHASE8-25); E2E (T23.10) **deferred to Sprint 26** (node not yet wired into `agent/graph.py` until T26.7). |

**Next cloud deployment:** gated on Sprint 26 closing + initial-test approval. See `task_plan.md` "Active tracks" for the full sequence.

**Schema drift to watch:** Sprint 23's `reactive_triggers_log` table requires **manual DDL apply** — `init.sql` edits don't re-run on existing Postgres volumes (local or cloud). On first cloud use, apply §7 of `infrastructure/sql/init.sql` directly. ⟨verify the table exists in cloud Postgres before relying on Sprint 23 in the initial test⟩

**Net:** the cloud runs the Sprint ~21.5-era agent + pipeline. The Sprint 22 BI-card wiring and Sprint 23 producer-trigger path will first reach cloud at the **initial-test phase** (gated on Sprint 26 closing).

---

## 3. Cloud Scheduler status

**PAUSED.** Both jobs `scale-up-main-pool` and `scale-down-main-pool` remain paused; the cluster does not auto-scale on a schedule. **Ron resumes manually.** Readiness checklist: `guides/cluster_operations_guide.md` §4.

---

## 4. Docker Compose local state

`infrastructure/docker-compose.yml` — active services: `kafka`, `kafka-ui`, `kafka-init`, `flink-jobmanager`, `flink-taskmanager`, `postgres`, `airflow-postgres`, `airflow-init`, `airflow-scheduler`, `airflow-webserver`, `prometheus`, `grafana`, `agent-worker`.

**Producers are NOT Compose services** — all 9 producer service blocks are commented out (lines ~728–784). Locally they run inside `airflow-scheduler` (DAGs) or as one-off `docker exec … python -m ingestion.<source>` execs (see `guides/VALIDATION_GUIDE.md`).

---

## 5. "Running" ≠ "working" — known cloud-runtime gaps

These workloads are deployed but had functional failures at last cloud run (track before the initial test):

| Gap | Effect in cloud |
|---|---|
| **KG-PHASE-C-5** | OpenAI `429 insufficient_quota` → Gold embeddings fail (Silver writes still succeed). Agent needs GPT-4o — check billing. |
| **KG-PHASE-C-6** | OpenSky `ConnectTimeoutError` from GKE → 0 Bronze messages. |
| **KG-PHASE-C-7 / KG-PHASE-9.5-5** | pytrends `404` (upstream API change, no fixed release) → 0 Bronze messages; producer now raises on 0% success. |
| **KG-PHASE-9.5-1** | OpenAI Tier 1 RPD cap (10k/day) can exhaust during backlog processing, halting agent queries until midnight UTC. |
| **KG-PHASE-9.5-4** | Polymarket `/comments` gated off (`POLYMARKET_COMMENTS_ENABLED=false`) — API contract broke. |
