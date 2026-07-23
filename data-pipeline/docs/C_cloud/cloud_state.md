# cloud_state.md
> Domain: C — Cloud
> Type: State
> Last updated: 2026-07-23 (agent, pipeline image tags, and §6 Scheduler/Airflow refreshed to live; monitoring/storage + non-flink/airflow producer rows as of 2026-06-15, unverified against live)
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

**The one fact that matters most:** the cloud no longer runs a uniformly Phase-9.5-era stack.
Two workloads have rolled since the 9.5 closeout: the **pipeline** (flink + airflow) to
`-7b5i` in **Phase 7B.5-I T7** (the day-run image), and the **agent** to
**`anizai-agent:0.5.0-sprint26` at `replicas:0`** (hard-off) on **2026-07-23** (B-deploy
Stage 1 — the cumulative hub Sprint 22→26 image). The agent is deployed but **idle**: it
cannot pick up a forecast or call OpenAI until Stage 2 scales it to 1. Sprint 27 remains
open hub work (not built). Primary source for the refreshed rows: the live cluster + the
B-deploy Stage-1 evidence; the monitoring/storage rows below are still as of
`deployment_state.md` (2026-06-13) and were not re-verified this session.

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
| flink-jobmanager / -taskmanager | Deployment | `anizai-flink:1.19.1-7b5i` (7B.5-I T7 deploy); K8s HA enabled (Phase 9 follow-up, 2026-05-19) | 7B.5-I T7 (day-run) |
| airflow-scheduler | Deployment | `anizai-airflow:2.9.3-7b5i` (7B.5-I T7 deploy); liveness probe :8974 (9.5-A); hosts 7 producer DAGs (**now manually paused — see §6**) | 7B.5-I T7 (day-run) |
| airflow-webserver | Deployment | `anizai-airflow:2.9.3-7b5i` (7B.5-I T7 deploy) | 7B.5-I T7 (day-run) |
| kafka-ui | Deployment | `provectuslabs/kafka-ui:v0.7.2` | Phase 9 (9B) |
| polymarket | Deployment | `anizai-polymarket:0.2.0-p95`; on main-pool; comments feature-flagged off | 9.5-B |
| telegram | Deployment | `anizai-telegram:0.1.0`; session file via CSI | Phase 9 (9D) |
| trigger-consumer | Deployment | `anizai-trigger-consumer:0.1.0`; on `ingestion_triggers` | Phase 9 (9D) |
| agent-worker | Deployment | `anizai-agent:0.5.0-sprint26` (digest `sha256:7fce4e8b…c316ef4`); `AGENT_VERSION 0.5.0-sprint26+55e8093`; **`replicas:0` — Stage-1 hard-off**; Sprint 22→26 in this image; cross-project Firestore | 2026-07-23 (B-deploy Stage-1 T1.2) |
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
| **Sprint 22 (Revised) — Foundation Fixes** (`marketProbability` / `predictionSeries` / `sentimentTimeSeries` wiring; `pg_trgm` Polymarket fuzzy-match; `canonicalKey` on session doc) | ✅ **LANDED** in `anizai-agent:0.5.0-sprint26` at `replicas:0` (B-deploy Stage-1 T1.2, 2026-07-23). Closed 2026-05-26; Gates 1–3 + E2E passed locally (`e2e-sprint22-c31b5da7`). | Deployed — idle at `replicas:0`; scales to 1 at Stage 2. |
| **Sprint 23 — Producer-trigger Infrastructure** (NewsAPI `run_reactive()`; `ingestion_triggers` registration; `trigger_reactive_ingestion` node, built in isolation) | ⏳ **PARTIAL**: the agent/node side (`trigger_reactive_ingestion`, wired in 23.5) **landed** in `anizai-agent:0.5.0-sprint26` (`replicas:0`, 2026-07-23); the **producer side** (NewsAPI `run_reactive()` in the airflow image) is **NOT yet deployed**. Gate 3 `skipif(win32)` still deferred to Linux/GKE (KG-B-13 / KG-PHASE8-25). | Agent side deployed; producer side needs the `anizai-airflow` rebuild — **Stage 2 T2.1**. |
| **`reactive_triggers_log` table** (Sprint 23 Postgres audit table) | ✅ **VERIFIED PRESENT in cloud Postgres 2026-07-23** (B-deploy T1.3): uuid PK, `session_id`/`keywords`/`source`/`status` NOT NULL, `kafka_offset` bigint, status CHECK `emitted`\|`failed`, PK + `idx_rtl_session_time`. Applied earlier (bundled with 7B.5-I T7); no re-apply needed. | Satisfied — no action. |
| **Hub Sprints 24–26** (follow-up conversations, suggested actions + `agentEvents`, pre-test hardening) | ✅ **LANDED** in `anizai-agent:0.5.0-sprint26` at `replicas:0` (B-deploy Stage-1 T1.2, 2026-07-23). All closed (24: 2026-07-04, 25: 2026-07-16, 26: 2026-07-18). | Deployed — idle at `replicas:0`; scales to 1 at Stage 2. |
| **Hub Sprint 27** (post-test polish + Phase-8 closeout) | **Not built** — open post-test hub work, gated on the initial test. | Closes into a final `anizai-agent` rebuild after the B-centric test. |

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

> **⚠ Updated 2026-07-23 (B-deploy Stage 1 / A day-run closeout).** The A measurement
> window `[2026-07-22T09:25:26Z, 2026-07-23T09:25:26Z]` has **CLOSED**. Two state changes
> below differ from the 9.5-era baseline: a one-shot auto-close job fired at the window
> end, and the 7 producer DAGs are now **deliberately paused** for the upcoming B test.

**Cloud Scheduler.** The recurring scale jobs `scale-up-main-pool` and `scale-down-main-pool`
remain **PAUSED**; the cluster does not auto-scale on a schedule — **Ron resumes manually**
(readiness checklist: `cluster_operations_guide.md` §4). Real recurring schedule is
**05:00 / 15:00 IL, Mon–Fri** (corrected in Phase 9.5-A from the Phase 9 archive's
`08:00 / 18:00`; the archive value is the stale source behind KG-C-6). **New (day-run):** a
**one-shot auto-close job**, added by the A day-run to end the measurement window at T0+24h,
**FIRED at 2026-07-23T09:25:26Z** and closed the window. The `scheduler-scaler` GSA holds
`roles/container.admin` (broader than needed — `container.developer` would suffice; Stage C
cleanup candidate).

**Airflow.** Scheduler + webserver Running; all DAGs `catchup=False` + scheduler
`CATCHUP_BY_DEFAULT=false` — **no backfill flood** when the cluster scales back up. **The 7
scheduled producer DAGs (FRED, NewsAPI, ArXiv, HackerNews, OpenWeather, OpenSky,
GoogleTrends) are now MANUALLY PAUSED** — this is **deliberate**: the upcoming Domain-B test
runs against the vault the A day-run already populated and needs Domain-A producers to stay
**OFF** (a running producer would contaminate the B cost/quality baseline). **Do NOT
reflexively un-pause them on the next scale-up** — leave them paused until the B test is done
and Ron explicitly re-enables them. The 3 daily DAGs can still be triggered manually from the
UI. Scheduler liveness probe runs on **:8974** (9.5-A fix; the prior :8793 was Airflow's
JWT-gated `serve_logs.py` and crash-looped the scheduler).
