# cloud_state.md
> Domain: C — Cloud
> Type: State
> Last updated: 2026-06-15
> TL;DR: Current GKE runtime state — what is deployed, what runs only locally, the known
> cluster gaps (KG-C-*), and the Scheduler/Airflow state. Open this to answer "what is
> actually running in cloud right now, and what isn't?"

## Navigation
- §1 — Overview — pointer + the one fact that matters most
- §2 — GKE Cluster — cluster name, project, zone, namespace, node pool
- §3 — Running Workloads — what is on the cluster and at which image
- §4 — Local-Only — everything that runs locally but is NOT on GKE yet
- §5 — Known Cluster Gaps — pointer to the canonical KG-C-* table in `cloud_sprints.md §4`
- §6 — Cloud Scheduler + Airflow State — what is scheduled, what is paused

---

## §1 — Overview

This is the runtime-state companion to `cloud_overview.md`. Read the overview for
topology and the deployed-workload macro view; read this file for the precise
**deployed-vs-local-only boundary** and the gaps that affect a cloud run.

**The one fact that matters most:** the cloud runs the **Phase ~21.5-era agent + pipeline**.
The last deployment touch was **Phase 9.5 closeout, 2026-05-20**. Sprint 22 (BI-card
wiring) and Sprint 23 (producer-trigger infrastructure) both closed **2026-05-26**, six
days later, and are **not in any cloud image**. They reach cloud via a Track-2
`anizai-agent` rebuild — minimal 22+23.5, or full through-26 — independent of the
near-term Track-1 pipeline cloud run (see `cloud_sprints.md` Rationale). Primary source for
this file: `deployment_state.md` (2026-06-13), validated against `task_plan_archive.md` Phase 9/9.5.

---

## §2 — GKE Cluster

| Attribute | Value |
|---|---|
| Cluster | `anizai-cluster` |
| Project (cluster + pipeline) | `anizai-pipeline` |
| Project (Firestore) | `anizai-ai` (cross-project via Workload Identity, `roles/datastore.user`) |
| Zone | `us-central1-a` (single-zone) |
| Namespace | `anizai` |
| Node pool | `main-pool` — `e2-standard-8` ×1; manually scaled 0↔1; `autoRepair`/`autoUpgrade` on, no maintenance window (KG-C-2) |
| Cluster type | GKE Standard (not Autopilot — pod-level WI scoping needed for cross-project agent SA) |
| Artifact Registry | `us-central1-docker.pkg.dev/anizai-pipeline/anizai-images` |
| Secret Manager | 16 secrets via CSI driver `secrets-store-gke.csi.k8s.io` (`provider: gke`, file mounts only — no `secretObjects` sync) |
| GCS | `gs://anizai-pipeline-backups/` (daily `pg_dump`, 30-day lifecycle) |

> Phase 9 originally ran a second node pool (`polymarket-pool`); it was **deleted** in
> Phase 9.5-A and Polymarket moved to `main-pool`. There is no second pool today.

---

## §3 — Running Workloads

Last touched **Phase 9.5 closeout (2026-05-20)** unless noted. See `cloud_overview.md` §3
for the full image/version table; this lists kind + the most recent change per workload.

| Workload | Kind | Notes / version | Last touched |
|---|---|---|---|
| kafka | StatefulSet | KRaft; `KAFKA_LOG_DIRS=/var/lib/kafka/data/kafka-logs` (durable PVC subdir, 9.5-A); 19 topics | 9.5-A |
| postgres | StatefulSet | `timescale/timescaledb-ha:pg16`; `publishNotReadyAddresses: true` (9.5-B) | 9.5-B |
| airflow-postgres | StatefulSet | `postgres:16`, metadata DB | Phase 9 (9D) |
| flink-jobmanager / -taskmanager | Deployment | `anizai-flink:1.19.1-p95`; K8s HA enabled (Phase 9 follow-up, 2026-05-19) | 9.5-B |
| airflow-scheduler | Deployment | `anizai-airflow:2.9.3-p95`; liveness probe :8974 (9.5-A); hosts 7 producer DAGs | 9.5-B |
| airflow-webserver | Deployment | `anizai-airflow:2.9.3-p95` | 9.5-B |
| kafka-ui | Deployment | `provectuslabs/kafka-ui:v0.7.2` | Phase 9 (9B) |
| polymarket | Deployment | `anizai-polymarket:0.2.0-p95`; on main-pool; comments feature-flagged off | 9.5-B |
| telegram | Deployment | `anizai-telegram:0.1.0`; session file via CSI | Phase 9 (9D) |
| trigger-consumer | Deployment | `anizai-trigger-consumer:0.1.0`; on `ingestion_triggers` | Phase 9 (9D) |
| agent-worker | Deployment | `anizai-agent:0.2.0-p95`; `AGENT_VERSION 0.4.0-sprint21-clarification-tier2`; cross-project Firestore | 9.5-B |
| prometheus | Deployment | `prom/prometheus:v2.51.2`; 2Gi + 7d retention (9.5-A); 5 scrape targets (9.5-C added kafka/postgres exporters) | 9.5-C |
| grafana | Deployment | `grafana/grafana:10.4.2`; 2 dashboards (9.5-C added `Anizai Pipeline Health`) | 9.5-C |
| alertmanager | Deployment | `prom/alertmanager:v0.27.0`; Gmail SMTP → `ron.mintz21@gmail.com` (9.5-C) | 9.5-C |
| kafka-exporter | Deployment | `danielqsj/kafka-exporter:v1.7.0` (9.5-C) | 9.5-C |
| postgres-exporter | Deployment | `prometheuscommunity/postgres-exporter:v0.15.0`; `pg_anizai_*` freshness queries (9.5-C) | 9.5-C |
| postgres-backup | CronJob | daily 02:00 UTC → GCS; not robust to scale-down (KG-C-9) | Phase 9 (9E) |
| kafka-init | CronJob | hourly idempotent topic reassert (9.5-A) | 9.5-A |
| airflow-init | Job | one-shot, retained Complete | Phase 9 (9D) |

**Postgres data (last measured, Phase 9.5-A baseline):** `knowledge_vault` 424,
`knowledge_vectors` 9,202, `momentum_vault` 34,665, `social_vault` 157,
`social_vectors` 21, `mapping_dict` 0, `divergence_alerts` 0. (FRED E2E runs in 9.5-A
added rows; the agent reads vault state read-only.)

---

## §4 — Local-Only (built, NOT on GKE)

This is the critical Domain-C distinction. Everything below runs locally but is absent
from every cloud image. **The next cloud touch is the Track-1 pipeline cloud run — which
is independent of these items and of hub Sprint 26.** The components below reach cloud only
via a **Track-2** `anizai-agent` update, whose scope Ron chooses (minimal **22+23.5**, or
full **through-26**); see `cloud_sprints.md` Rationale. The **facts** below (what is
local-only) are unchanged — only the *when/why it deploys* framing is.

| Component | Why not deployed | Condition to deploy |
|---|---|---|
| **Sprint 22 (Revised) — Foundation Fixes** (`marketProbability` / `predictionSeries` / `sentimentTimeSeries` wiring; `pg_trgm` Polymarket fuzzy-match; `canonicalKey` on session doc) | Closed 2026-05-26, six days after the last cloud touch; no cloud image build on record. Gates 1–3 + E2E passed locally (`e2e-sprint22-c31b5da7`). | Carried by the Track-2 `anizai-agent` rebuild — present in **both** options (the minimal 22+23.5 image and the full through-26 bundle). Not gated on the Track-1 pipeline run. |
| **Sprint 23 — Producer-trigger Infrastructure** (NewsAPI `run_reactive()`; `ingestion_triggers` registration; `trigger_reactive_ingestion` node, built in isolation) | Local only + partially unverified. Gate 1 (20/20) + Gate 2 (3/3) pass; Gate 3 `skipif(win32)` deferred to Linux/GKE (KG-B-13 / KG-PHASE8-25); E2E deferred to hub Sprint 26 (node not wired into `agent/graph.py` until T26.7). | Needs `anizai-agent` + `anizai-airflow` rebuilds (the reactive trigger touches both the node and the NewsAPI DAG path). |
| **`reactive_triggers_log` table** (Sprint 23 Postgres audit table) | Requires **manual DDL apply** — `init.sql` edits do not re-run on an existing Postgres PVC (local or cloud). Not yet applied to cloud Postgres. | Apply §7 of `infrastructure/sql/init.sql` directly to cloud Postgres before any run that exercises Sprint 23's trigger path. ⟨verify table exists in cloud before that run⟩ |
| **Hub Sprints 24–27** (follow-up conversations, suggested actions + `agentEvents`, pre-test hardening, post-test polish) | Not started; open hub work. | Each closes into the cumulative `anizai-agent` rebuild — see `cloud_sprints.md` §2. |

**Net:** the cloud runs the Sprint ~21.5-era agent + pipeline. Sprint 22 BI-card wiring and
the Sprint 23 producer-trigger path reach cloud only via a **Track-2** `anizai-agent` rebuild
(minimal 22+23.5 or full through-26) — not via, and not gating, the near-term **Track-1**
pipeline cloud run. See `cloud_sprints.md` Rationale.

---

## §5 — Known Cluster Gaps

The cloud Known-Gaps table (**KG-C-\***) is now maintained **canonically** in
`cloud_sprints.md §4` — full Origin / Description / Impact / Workaround / Priority /
Raised-in / Condition-to-address, in one complete table. It is **not** duplicated here.

> Most cost-critical gap: **KG-C-1** — the OpenAI Tier 1 RPD cap (10k/day) can halt the
> agent + Gold enrichment during backlog catch-up; it gates the initial-test cost work.
> See `cloud_sprints.md §4` for KG-C-1 through KG-C-9.

Producer-**code** gaps (OpenSky producer logic, GoogleTrends/pytrends 404, Polymarket
`/comments`) are **Domain A**, not cloud-infra — see `A_pipeline/pipeline_sprints.md`.

---

## §6 — Cloud Scheduler + Airflow State

**Cloud Scheduler — PAUSED.** Both jobs `scale-up-main-pool` and `scale-down-main-pool`
remain paused; the cluster does not auto-scale on a schedule. **Ron resumes manually**
(readiness checklist: `cluster_operations_guide.md` §4). Real schedule is
**05:00 / 15:00 IL, Mon–Fri** (corrected in Phase 9.5-A from the Phase 9 archive's
`08:00 / 18:00`; the archive value is the stale source behind KG-C-6). The
`scheduler-scaler` GSA holds `roles/container.admin` (broader than needed — `container.developer`
would suffice; Stage C cleanup candidate).

**Airflow.** Scheduler + webserver Running; all DAGs `catchup=False` + scheduler
`CATCHUP_BY_DEFAULT=false` — **no backfill flood** when the cluster scales back up. The 7
scheduled producer DAGs (FRED, NewsAPI, ArXiv, HackerNews, OpenWeather, OpenSky,
GoogleTrends) fire only on their next scheduled window; the 3 daily ones can be triggered
manually from the UI. Scheduler liveness probe runs on **:8974** (9.5-A fix; the prior
:8793 was Airflow's JWT-gated `serve_logs.py` and crash-looped the scheduler).
