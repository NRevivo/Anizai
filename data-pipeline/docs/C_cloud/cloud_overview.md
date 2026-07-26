# cloud_overview.md
> Domain: C — Cloud
> Type: Overview
> Last updated: 2026-07-26 (§1 deployment-work paragraph + §5 navigation paths corrected; agent + pipeline image-tag rows refreshed 2026-07-23; monitoring/storage rows as of 2026-06-15, unverified against live)
> TL;DR: The macro view of the Anizai GKE deployment — cluster topology, what is
> deployed vs. local-only, and which phases closed when. Open this first to orient
> before the detailed Domain C files.

## Navigation
- §1 — Purpose & Scope — what Domain C covers and what it does not
- §2 — Architecture — the GKE cluster: project, node pool, namespace, workloads at a glance
- §3 — Deployed Workloads — every workload on the cluster, its kind, image, and status
- §4 — Phase Status — pointer to the canonical status table in `cloud_sprints.md §1`
- §5 — Navigation Map — which Domain C file/section holds what (the routing table)

---

## §1 — Purpose & Scope

Domain C is the **GCP/GKE cloud deployment** of the Anizai stack built in Domains A
(pipeline) and B (hub). The full local Docker Compose stack — Kafka, Postgres, Flink,
the 9 producers, Airflow, the agent worker, and monitoring — was ported to a single-zone
GKE cluster in **Phase 9** (Sprints 9A–9E, internal legacy name "Phase C / C1–C5"),
then hardened in **Phase 9.5** (Stages A/B/C).

**In scope:** the GKE cluster (`anizai-cluster`), its single node pool and namespace,
every Kubernetes workload (StatefulSets, Deployments, CronJobs, Jobs), the GCP plumbing
that supports them (Artifact Registry, Secret Manager + Workload Identity, cross-project
Firestore IAM, Cloud Scheduler, GCS backups), the monitoring stack (Prometheus, Grafana,
Alertmanager, exporters), and the **deployed-vs-local-only boundary**.

**Not in scope (other domains):**
- **Pipeline internals** — Kafka topic design, the Flink Silver/Gold jobs, producer
  logic, the vault schemas. Domain A (`pipeline_overview.md`). Cloud refers to these
  workloads by name only.
- **Hub internals** — the LangGraph graph, nodes, retrieval agents. Domain B
  (`hub_overview.md`). Cloud refers to the `agent-worker` Deployment only.
- **Calibration / backtesting** — Domain D (Phase 10).

**What Domain C is NOT:** it is not a redesign of the pipeline or hub — it is a
**parity port** of the same code onto Kubernetes. Behavior differences between local
and cloud are documented as deployment decisions (e.g. `publishNotReadyAddresses` for
KRaft self-bootstrap, the Secret Manager CSI shell-wrapper pattern), not feature changes.

Phase 9 and Phase 9.5 are both **fully closed**; the cluster runs (Cloud Scheduler PAUSED,
scaled to 0 between windows). Domain C has **no open implementation sprints**, but it does
have open *deployment work*. As of **2026-07-23**, hub Sprint 22→26 has **landed** on the
deployed image (`anizai-agent:0.5.0-sprint26` at `replicas:0`, B-deploy Stage 1). **Stage 2
T2.2 was executed 2026-07-25/26** — the agent was scaled to 1, passed its health gate, served
7 real forecasts, and was returned to 0 (`B_hub/agent_cloud_run_20260726.md`). What remains
is the `anizai-airflow` rebuild (T2.1) — note it does **not** enable the reactive producer
path, which needs a `trigger-consumer` rebuild plus a NewsAPI secret on its
SecretProviderClass — plus the known cluster gaps. Four workloads are currently held at
`replicas: 0` live against manifests that say 1 (KG-C-10). See `cloud_state.md`,
`cloud_sprints.md`, and `../guides/bringup_profiles.md` for bring-up.

---

## §2 — Architecture

Single-zone GKE Standard cluster. One node pool, one namespace. Firestore lives in a
**separate** GCP project (`anizai-ai`, the partner frontend's project), reached
cross-project via Workload Identity — the only multi-project hop in the system.

```
 GCP project: anizai-pipeline                         GCP project: anizai-ai
 ────────────────────────────                         ──────────────────────
 GKE cluster: anizai-cluster (us-central1-a)          Firestore
   node pool: main-pool  (e2-standard-8 ×1,             - forecastQueries (work queue)
              scaled 0↔1 manually; Scheduler PAUSED)     - sessions/{id}/... (results)
   namespace: anizai                                          ▲
                                                              │ cross-project
   ┌───────────── STATEFULSETS ─────────────┐                │ Workload Identity
   │ kafka (KRaft)   postgres (TimescaleDB)  │                │ (roles/datastore.user)
   │ airflow-postgres                        │                │
   └─────────────────────────────────────────┘               │
   ┌───────────── DEPLOYMENTS ───────────────┐                │
   │ flink-jobmanager / flink-taskmanager     │   agent-worker ┘
   │ airflow-scheduler / airflow-webserver    │   (7 scheduled producers run as
   │ kafka-ui                                 │    Airflow DAGs inside the scheduler)
   │ polymarket   telegram   trigger-consumer │
   │ agent-worker                             │
   │ prometheus  grafana  alertmanager        │
   │ kafka-exporter  postgres-exporter        │
   └─────────────────────────────────────────┘
   ┌──────────── CRONJOBS / JOBS ────────────┐
   │ postgres-backup (daily 02:00 UTC → GCS)  │   GCS: gs://anizai-pipeline-backups/
   │ kafka-init (hourly, idempotent reassert) │   Artifact Registry: anizai-images
   │ airflow-init (one-shot, complete)        │   Secret Manager: 16 secrets (CSI, WI)
   └─────────────────────────────────────────┘

 Supporting GCP: Artifact Registry (us-central1-docker.pkg.dev/anizai-pipeline/anizai-images),
 Secret Manager (CSI driver secrets-store-gke.csi.k8s.io, provider: gke — file mounts only),
 Cloud Scheduler (scale-up / scale-down main-pool, 05:00 / 15:00 IL Mon–Fri — PAUSED),
 Cloud Logging + 2 Cloud Monitoring policies (OpenAI-429 proxy alerts).
```

**Key topology facts:**
- **One node pool.** Phase 9's second pool (`polymarket-pool`, always-on for Polymarket's
  WebSocket) was **deleted** in Phase 9.5-A; Polymarket now schedules on `main-pool` and
  stops with everything else when the pool scales to 0 (acceptable data gap; the crash-loop
  it replaced was not).
- **Scaled to 0 between windows.** `main-pool` is manually resized 0↔1. Cloud Scheduler
  would automate this on a Mon–Fri 05:00/15:00 IL cycle but is **PAUSED** — Ron resumes
  manually. The cluster was proven robust to the full scale 0→1 cycle in Phase 9.5-A (F6).
- **No public ingress.** No LoadBalancer, no DNS, no TLS. Every service is reached from
  the developer laptop via `kubectl port-forward` (see `guides/LOCAL_CONNECTION.md`).
- **Storage is self-hosted.** Postgres is a `timescale/timescaledb-ha:pg16` StatefulSet,
  not Cloud SQL (which lacks TimescaleDB). GCS holds only daily `pg_dump` backups — there
  is no GCS Bronze data lake (Bronze lives in Kafka topics).

---

## §3 — Deployed Workloads

Most rows are as of the Phase 9.5 closeout (2026-05-20), but the stack is **no longer
uniformly 9.5-era**: the **pipeline** (flink + airflow) was rolled to `-7b5i` in **Phase
7B.5-I T7** (the day-run image), and **`agent-worker`** to `anizai-agent:0.5.0-sprint26` at
`replicas:0` on **2026-07-23** (B-deploy Stage 1). Each row below carries its own image tag;
the monitoring/storage rows remain at the 9.5 baseline (not re-verified this session).

| Workload | Kind | Image / version | Status |
|---|---|---|---|
| kafka | StatefulSet | `apache/kafka:3.7.0` (KRaft) | Running — 19 topics, durable on PVC subdir `kafka-logs/` (9.5-A fix) |
| postgres | StatefulSet | `timescale/timescaledb-ha:pg16` | Running — vault + audit tables incl. `reactive_triggers_log` (verified present 2026-07-23); pgvector + timescaledb + pg_trgm |
| airflow-postgres | StatefulSet | `postgres:16` | Running — Airflow metadata DB |
| flink-jobmanager | Deployment | `anizai-flink:1.19.1-7b5i` (7B.5-I T7) | Running — K8s HA enabled (Phase 9 follow-up) |
| flink-taskmanager | Deployment | `anizai-flink:1.19.1-7b5i` (7B.5-I T7) | Running — Silver + Gold jobs RUNNING |
| airflow-scheduler | Deployment | `anizai-airflow:2.9.3-7b5i` (7B.5-I T7) | Running — liveness probe on :8974 (9.5-A fix); hosts the 7 producer DAGs (**now manually paused — see `cloud_state.md` §6**) |
| airflow-webserver | Deployment | `anizai-airflow:2.9.3-7b5i` (7B.5-I T7) | Running |
| kafka-ui | Deployment | `provectuslabs/kafka-ui:v0.7.2` | Running |
| polymarket | Deployment | `anizai-polymarket:0.2.0-p95` | Running on main-pool — comments path feature-flagged off (KG-A / KG-PHASE-9.5-4) |
| telegram | Deployment | `anizai-telegram:0.1.0` | Running — session file via Secret Manager CSI |
| trigger-consumer | Deployment | `anizai-trigger-consumer:0.1.0` | Running — consumes `ingestion_triggers` (closed KG-PHASE8-3, 9D) |
| agent-worker | Deployment | `anizai-agent:0.5.0-sprint26` (digest `sha256:7fce4e8b…c316ef4`; `AGENT_VERSION 0.5.0-sprint26+55e8093`) | **Deployed at `replicas:0` — Stage-1 hard-off (2026-07-23)**; cross-project Firestore on `anizai-ai`. **Sprint 22→26 in this image; idle until Stage 2 scales it to 1** |
| prometheus | Deployment | `prom/prometheus:v2.51.2` | Running — 2Gi + 7d retention (9.5-A); 5 scrape targets |
| grafana | Deployment | `grafana/grafana:10.4.2` | Running — 2 dashboards (detailed + `Anizai Pipeline Health`, 9.5-C) |
| alertmanager | Deployment | `prom/alertmanager:v0.27.0` | Running — Gmail SMTP → `ron.mintz21@gmail.com` (9.5-C) |
| kafka-exporter | Deployment | `danielqsj/kafka-exporter:v1.7.0` | Running — added 9.5-C |
| postgres-exporter | Deployment | `prometheuscommunity/postgres-exporter:v0.15.0` | Running — custom `pg_anizai_*` freshness metrics (9.5-C) |
| postgres-backup | CronJob | `google/cloud-sdk:slim` | Active — daily 02:00 UTC `pg_dump → gzip → GCS` |
| kafka-init | CronJob | `apache/kafka:3.7.0` | Active — hourly idempotent topic reassert (9.5-A) |
| kafka-init / airflow-init | Job | `apache/kafka:3.7.0` / `apache/airflow:2.9.3-python3.12` | One-shot operator bootstrap (airflow-init retained Complete) |

> The **7 scheduled producers** (FRED, NewsAPI, ArXiv, HackerNews, OpenWeather, OpenSky,
> GoogleTrends) are **not** separate Deployments — they run as Airflow DAGs baked into the
> `airflow-scheduler` image. Only the two always-on streaming producers (Polymarket,
> Telegram) and the trigger-consumer have standalone Deployments. (`deployment_state.md`
> §1 lists "9 producers / Deployment" loosely — corrected here.)

---

## §4 — Phase Status

The full phase-status table — every Phase 9 / 9.5 sub-phase, its status, closed date,
and key outcome — is the **canonical view in `cloud_sprints.md §1`**. Both phases are
**Closed** (Phase 9 on 2026-05-10, Phase 9.5 on 2026-05-20); per-sub-phase outcomes,
decisions, and gaps raised are in `cloud_archive.md`.

> Naming: "Phase C / C1–C5" in the old `archive/cloud_deployment_implementation.md` ==
> "Phase 9 / 9A–9E" used here and in `cloud_sprints.md §1`.

---

## §5 — Navigation Map

Open this file first to orient, then route to the precise file/section below.

| To find… | Go to |
|---|---|
| **What's pending in cloud** — the deployment backlog | `cloud_sprints.md §2` |
| **Phase 9 / 9.5 status + rationale** (why the cloud work is sequenced as it is) | `cloud_sprints.md` (§1 Status, §Rationale) |
| **What's actually deployed now + the local-only boundary** | `cloud_state.md` (§3 Running Workloads, §4 Local-Only) |
| **Cluster topology / architecture** | this file — `cloud_overview.md §2` / §3 |
| **How to bring the cluster up or down** (agents-only / pipeline-only / full) | `data-pipeline/docs/guides/bringup_profiles.md` |
| **Known Gaps (KG-C-\*)** — the canonical table | `cloud_sprints.md §4` |
| **Future deployment plans** (written on demand) / **closed work** | `plans/` (when activated) / `cloud_archive.md` (+ `archive_plans/`) |

**Domain C files:**
- `cloud_state.md` — current GKE runtime state: running workloads, the local-only boundary, Scheduler + Airflow state (KG-C-\* table now lives in `cloud_sprints.md §4`).
- `cloud_sprints.md` — the Domain-C tracker: Phase 9/9.5 status, deployment model / rationale, the deployment backlog, deferred items, and the canonical cloud Known Gaps.
- `cloud_archive.md` — append-only record of Phase 9 (9A–9E) + Phase 9.5 (A/B/C) closed work.
- `plans/` + `archive_plans/` — on-demand / retired cloud-deployment plan files (empty today).

**Source / operational docs (Domain C consumes or supersedes these).** Paths below are
full repo-relative paths — the `guides/` folder sits at `data-pipeline/docs/guides/`,
**not** inside `C_cloud/`:
- `data-pipeline/docs/old_docs/deployment_state.md` — prior runtime-state doc (2026-06-13); primary source for current state, superseded by `cloud_state.md`.
- `data-pipeline/docs/guides/bringup_profiles.md` — **bring-up / teardown by profile** (AGENTS / PIPELINE / FULL): the procedure, its gates, and the traps. Start here for any scale-up.
- `data-pipeline/docs/guides/cluster_operations_guide.md` — operational runbook (triage, backlog-drop, restore drill, per-finding diagnostics, dashboards, Cloud Logging; Phase 9.5-C).
- `data-pipeline/docs/guides/CLOUD_CONNECTION_GUIDE.md` / `LOCAL_CONNECTION.md` — operator connection ergonomics (port-forward recipes, credentials, GCP Console navigation). Fully swept for accuracy 2026-07-26; KG-C-6 closed.
- `data-pipeline/docs/old_docs/cloud_deployment_implementation.md` — Phase 9 (C1–C5) implementation plan + gate records.
- `data-pipeline/docs/old_docs/phase95_cluster_robustness_implementation.md` — Phase 9.5 plan, fix packages, and stage closeouts.

**Cross-domain:**
- `A_pipeline/pipeline_overview.md` — the pipeline whose workloads this cluster runs; owner of producer-code KGs (OpenSky, GoogleTrends, Polymarket comments).
- `B_hub/hub_overview.md` + `B_hub/hub_sprints.md` — the hub whose `agent-worker` runs here; the source of the Sprint 24–27 deployment backlog.
