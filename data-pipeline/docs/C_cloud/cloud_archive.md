# cloud_archive.md
> Domain: C — Cloud
> Type: Archive
> Last updated: 2026-06-15
> TL;DR: Append-only record of closed cloud work — Phase 9 (9A–9E), the Flink HA follow-up,
> and Phase 9.5 (Stages A/B/C). Per sub-phase: outcome, key decisions, tasks, gaps raised.
> Open this for "why was the cloud built this way?"

> **Naming:** the old `archive/cloud_deployment_implementation.md` uses "Phase C / C1–C5".
> That maps 1:1 to **Phase 9 / 9A–9E** here (9A=C1, 9B=C2, 9C=C3, 9D=C4, 9E=C5).
> **Append-only:** once a sub-phase section is written, it is not rewritten — later
> corrections land as new dated notes.

> **Where closed plans live:** new-style **retired Domain-C deployment plan files** live in
> `archive_plans/` (empty today — populated when an on-demand `plans/` deployment plan
> completes). The Phase 9 / 9.5 records below are **not** migrated there; they stay here.
> **Legacy forensic breadcrumb:** old flat-format work is in
> `data-pipeline/task_plan_archive.md` (and `task_plan_implementation_archive.md`) —
> consult only for rare forensic history, consistent with `project_master.md §6`. The full
> Phase 9 plan + gate records live in `archive/cloud_deployment_implementation.md`, and the
> Phase 9.5 plan in `phase95_cluster_robustness_implementation.md`.

---

## Archive Index

| Phase / Sub-phase | Date | Key decisions summary |
|---|---|---|
| Phase 9 — 9A (GCP Foundation) | 2026-05-07 | Dual-project layout; dual-pool cluster; Secret Manager + WI + CSI, no JSON keys; Artifact Registry |
| Phase 9 — 9B (Postgres + Kafka) | 2026-05-07 | Self-hosted TimescaleDB StatefulSet (Cloud SQL rejected); 19 topics; `publishNotReadyAddresses` for KRaft |
| Phase 9 — 9C (Flink) | 2026-05-09 | JM/TM Deployments; EXACTLY_ONCE; CSI shell-wrapper secret pattern; BlobServer :6124; Windows/PowerShell exec rules |
| Phase 9 — 9D (Airflow + producers + trigger consumer) | 2026-05-10 | Polymarket/Telegram always-on Deployments; 7 producers as DAGs; trigger consumer (closed KG-PHASE8-3) |
| Phase 9 — 9E (Agent + monitoring + backups + E2E) | 2026-05-10 | Cross-project Firestore via WI; Prometheus/Grafana; daily pg_dump → GCS; Phase C E2E passed |
| Phase 9 Follow-up — Flink K8s HA | 2026-05-19 | `high-availability.type: kubernetes`; ConfigMap leader election; `flink-rbac.yaml` |
| Phase 9.5 — Stage A (Infra robustness) | 2026-05-19 | Kafka `log.dirs` root cause; durable PVC subdir; hourly kafka-init CronJob; polymarket-pool deleted; probe/mem fixes |
| Phase 9.5 — Stage B (App robustness) | 2026-05-20 | Gold DB retry; OpenAI client factory; Polymarket comments flag; producer raise-on-0%; 4 images → `*-p95` |
| Phase 9.5 — Stage C (Monitoring + ops docs) | 2026-05-20 | kafka/postgres exporters; Alertmanager + Gmail SMTP; 13 alert rules; health dashboard; ops guide |

---

## Phase 9 — Cloud Deployment (9A–9E)

Parity port of the local docker-compose stack to a single-zone GKE cluster. Strictly
sequential, gate-locked. Pre-phase checkpoint verified 2026-05-07 (Owner on both projects;
billing linked; `kubectl` + auth plugin installed). Full plan + gate records:
`archive/cloud_deployment_implementation.md`.

### 9A — GCP Foundation (C1)

**Outcome (Gate PASSED 2026-05-07).** Empty cluster + all supporting GCP resources stood
up; no application workloads yet. WI smoke-test Job Completed (`OPENAI_API_KEY length=164`).

**Key decisions:**
- **D1 — Dual-project layout.** Pipeline + cluster in `anizai-pipeline`; Firestore stays in
  `anizai-ai` (the partner frontend's project). Cross-project IAM granted in 9E.
- **D2 — Single-zone, dual-pool, GKE Standard.** `main-pool` (e2-standard-8 ×1, scaled to 0
  between windows) + `polymarket-pool` (always-on for Polymarket's push-only WebSocket).
  Standard not Autopilot (pod-level WI scoping needed in 9E). Multi-zone/Spot/autoscaling
  rejected for V1. *(Note: polymarket-pool later deleted in Phase 9.5-A.)*
- **D3 — Secret Manager + Workload Identity + CSI driver; no JSON key files, ever.**
- **D4 — Billing alerts ₪200 (~$54) / ₪400 (~$108).** Currency follows the ILS billing
  account. Alerts route to default IAM recipients (custom `ron.mintz21@gmail.com` channel
  is an optional follow-up).
- **D5 — One Artifact Registry repo** `us-central1-docker.pkg.dev/anizai-pipeline/anizai-images`.

**Tasks:** C1.1–C1.15 (APIs, billing, registry, push 3 images, migrate secrets, create
cluster, namespace, enable Secret Manager add-on, GSA + KSA + WI binding, smoke test, README).

**Gaps raised:** KG-PHASE-C-1 (docker-compose `kafka-ui:latest` not pinned → KG-C-8).
**Confirmed:** 13 secrets (POLYMARKET_API_KEY/SECRET intentionally excluded — public-only).

### 9B — Postgres + Kafka (C2)

**Outcome (Gate PASSED 2026-05-07).** Postgres Ready with 7 tables + extensions +
`momentum_vault` hypertable; Kafka Ready with 19 topics; both reachable via port-forward.

**Key decisions:**
- **D1 — Postgres = self-hosted `timescale/timescaledb-ha:pg16` StatefulSet. Cloud SQL
  rejected** (no TimescaleDB; the hypertable + Continuous Aggregates are non-negotiable).
  Same image as docker-compose → zero behavior drift.
- **D2 — PVCs: Postgres 20 GB, Kafka 10 GB**, `pd-balanced`, expandable in place.
- **D3 — `init.sql` mounted as a ConfigMap** (schema source-of-truth stays one file).
- **D4 — `kafka-init` one-shot Job** creating 19 topics (11 Bronze + 3 Silver + 3 Gold +
  `ingestion_triggers` + `dead-letter-queue`). *(Originally written "14 topics"; corrected
  to 19 to match docker-compose verbatim. Later converted to an hourly CronJob in 9.5-A.)*
- **D5 — port-forward only** — no LoadBalancer/Ingress/DNS.
- **D6 — `publishNotReadyAddresses: true` on the kafka headless Service** — required for
  KRaft single-node self-bootstrap (pod must resolve its own DNS before Ready). K8s-specific;
  docker-compose's bridge DNS doesn't need it.

**Tasks:** C2.1–C2.11. **7th table** is `divergence_alerts` (not the spec-only
`reactive_article_cache`). Extensions: pgvector 0.8.2, timescaledb 2.26.4, pg_trgm 1.6.

### 9C — Flink (C3)

**Outcome (Gate PASSED 2026-05-09).** Both jobs RUNNING; FRED test message round-tripped
Bronze→Silver→Gold→`momentum_vault`; checkpoint recovery validated via force-kill.

**Key decisions (selected from D1–D11):**
- **D1 — JM + TM as separate Deployments** (not the Flink Helm operator).
- **D2 — EXACTLY_ONCE + 60s checkpoints** from the same `FLINK_PROPERTIES` as docker-compose.
- **D3 — single shared 5 GB checkpoint PVC** (RWO; would need RWX if a 2nd TM is added).
- **D5 — CSI shell-wrapper secret pattern.** GKE-native CSI (`provider: gke`) supports
  file mounts but **not** `secretObjects` K8s-Secret sync; pods that read env vars via
  `os.getenv()` use a `bash -c` wrapper that `export`s from `/var/secrets/...` files. Applies
  to all later Deployments (Airflow, producers, agent).
- **D6 — BlobServer port 6124 must be in the JM Service** or tasks hang in DEPLOYING.
- **D7 — no `#` comment lines in `FLINK_PROPERTIES`** (Flink 1.19 parses them as keys).
- **D9 — checkpoint recovery needs force-kill** (`--force --grace-period=0`); graceful
  SIGTERM is treated as planned deallocation and skips the restart strategy.
- **D10 — `kubectl exec` with Unix paths must use PowerShell on Windows** (Git Bash path
  translation breaks PyFlink staging).
- **D11 — inject test messages via base64'd Python inside the JM pod** (PowerShell stdin
  adds a UTF-8 BOM the NDJSON deserializer rejects).

**Tasks:** C3.1–C3.9. **Gaps raised:** KG-PHASE-C-2 (no `#` in Flink/Airflow env ConfigMaps
→ later KG context), KG-PHASE-C-3 (no `secretObjects` → shell wrapper everywhere).

### 9D — Airflow + Producers + Trigger Consumer (C4)

**Outcome (Gate — 30-min observation).** Orchestration layer + all 9 producers deployed;
real data from all 9 sources targeted into the vaults.

**Key decisions:**
- **D1 — Polymarket + Telegram are always-on streaming Deployments**, not DAGs (persistent
  WebSocket / MTProto). The other 7 producers run as Airflow DAGs baked into the scheduler
  image.
- **D2 — Telegram session file generated locally once, pushed to Secret Manager**, mounted
  via CSI (MTProto interactive SMS auth must never run on the cluster).
- **D3 — Reactive trigger consumer gets `Dockerfile.trigger_consumer` + its own Deployment**
  — **closes KG-PHASE8-3** (runtime validation of `ingestion_trigger_consumer.py`).
- **D4 — Gate = 30-min observation window** with non-zero rows from all 9 sources.

**Tasks:** C4.1–C4.20 (airflow-postgres, init Job, scheduler + webserver, polymarket +
telegram images + Deployments, trigger-consumer image + Deployment, observation gate,
KG-PHASE8-3 closure).

**Gaps raised:**
- KG-PHASE-C-4 — Airflow scheduler liveness probe (closed at :8793 — **later found wrong**;
  :8793 is the JWT-gated `serve_logs.py`. Corrected to :8974 in Phase 9.5-A).
- KG-PHASE-C-5 — OpenAI `429 insufficient_quota` → Gold embeddings fail (later superseded by
  Stage B + KG-PHASE-9.5-1 / KG-C-1).
- KG-PHASE-C-6 — OpenSky `ConnectTimeoutError` from GKE → 0 Bronze (→ KG-C-7).
- KG-PHASE-C-7 — pytrends 404 → 0 Bronze (→ KG-PHASE-9.5-5, Domain A).

### 9E — Agent + Monitoring + Local E2E (C5)

**Outcome (Gate + Phase C closing gate PASSED 2026-05-10).** Agent worker live on
cross-project Firestore; Prometheus/Grafana live; daily backup CronJob + restore verified;
full E2E ("Will the Fed cut rates by Q2 2026?") rendered all four BI cards.

**Key decisions:**
- **D1 — Agent uses cross-project Firestore via WI** (GSA `agent-worker@anizai-pipeline`,
  `roles/datastore.user` on `anizai-ai`; KSA `agent-worker-ksa`; no `GOOGLE_APPLICATION_CREDENTIALS`).
- **D2 — Daily `pg_dump` CronJob → `gs://anizai-pipeline-backups/postgres/`** (02:00 UTC,
  30-day lifecycle; restore tested once into a scratch DB).
- **D3 — Prometheus + Grafana in-cluster, port-forward only.** Scrape targets:
  flink-jm:9249, flink-tm:9249, agent-worker:8000. *(agent /metrics was a Sprint-18 stub —
  zero `agent_*` metrics; real metrics deferred to hub Sprint 26.)*
- **D4 — `LOCAL_CONNECTION.md`** is the source of truth for the laptop loop.

**Deployed agent version at close:** `0.4.0-sprint21-clarification-tier2`. Cloud Scheduler
enabled at C5 close (later paused). **Tasks:** C5.0–C5.15. Closes Phase 9.

---

## Phase 9 Follow-up — Flink Kubernetes HA (2026-05-19)

**Outcome.** Flink job graphs survive JobManager restarts (previously lost on every
restart — a root cause of the May 11–18 silence).

**Key decisions:** `high-availability.type: kubernetes` + K8s ConfigMap leader election;
new `flink-rbac.yaml` (ConfigMap-CRUD Role + RoleBinding on `pipeline-runtime`).
- **D-FU-1:** HA ConfigMap selector `anizai-flink-<job-id>-config-map`; Flink auto-cleans on
  `cancel`.
- **D-FU-2:** HA preserves job graphs across pod restarts — **but also preserves the OLD
  compiled Python BLOB** (the seed of KG-PHASE-9.5-8 / KG-C-4: code changes need job
  re-submit, not just a pod restart).

**Verification:** confirmed working across two scale 0→1 cycles in Phase 9.5-A (F6).

---

## Phase 9.5 — Cluster Robustness (Stages A/B/C)

Three-stage robustness + monitoring hardening after the May 11–18 silence. Stage A =
infrastructure; Stage B = application; Stage C = monitoring + operational docs. Each stage
gated by Ron's approval at two checkpoints. Full record:
`phase95_cluster_robustness_implementation.md` (+ `phase95_investigation_log.md` audit trail).

### Stage A — Infrastructure Robustness (CLOSED 2026-05-19 14:30 UTC)

**Outcome.** Root-caused the silence and proved the cluster robust to the daily scale cycle.

**Primary root cause:** Kafka wrote to the container's ephemeral `/tmp/kafka-logs` (the
`apache/kafka:3.7.0` default when `log.dirs` is unset), **not** the 10 GB PVC — mounted but
unused for 11 days. Every pod restart wiped topics → producers/Flink reconnected to an empty
broker → silence. The "PVC reset" theory was wrong (the PVC was never used).

**Key decisions:**
- **D-A-1/2:** set `KAFKA_LOG_DIRS=/var/lib/kafka/data/kafka-logs` — a **subdir** of the
  mount (Kafka 3.7 rejects the `lost+found` at the mount root). Same fix applied to
  `docker-compose.yml` for parity.
- **D-A-3:** Polymarket moved to `main-pool`; **`polymarket-pool` deleted** (the always-on
  design was incoherent — Kafka, its destination, only runs in main-pool's window).
- **D-A-4:** `kafka-init` converted to an **hourly idempotent CronJob** (`--if-not-exists`);
  topics self-heal within an hour of a fresh broker. CronJob over initContainer (topic
  creation needs a *Ready* broker). The one-shot Job is kept for manual operator use.
- **D-A-5:** Airflow scheduler liveness probe → **:8974** (the KG-PHASE-C-4 closure used
  :8793, the JWT-gated `serve_logs.py`, which 403'd the kube-probe → 111 restarts/10h).
- **D-A-6:** Prometheus **2Gi + 7d TSDB retention** (512Mi OOMKill-looped replaying 871 WAL
  segments → 118 restarts/24h).

**Fixes executed (Ron-revised order F2→F0→F1→F4→F5→F6):** probe/mem stabilization,
Polymarket revert + pool delete, Kafka log.dirs + topic recreate + CronJob, restore drill
(7 tables exact row-count match), FRED E2E (88 Bronze → 88 `momentum_vault` rows in ~12s),
scale 0→1 robustness cycle (14 pods recovered, Flink auto-resumed in ~90s).

**Gaps raised:** KG-PHASE-9.5-1 (OpenAI RPD ceiling → KG-C-1), -2 (NEWSAI_API_KEY name →
KG-C-5), -3 (CLOUD_CONNECTION_GUIDE drift → KG-C-6), -6 (no maintenance window → KG-C-2),
-7 (no digest pinning → KG-C-3); plus the missed-backup finding (→ KG-C-9).

### Stage B — Application Robustness (CLOSED 2026-05-20 ~00:15 UTC)

**Outcome.** Closed the transient-failure and silent-failure classes the silence exposed.

**Investigation (B.1):** all 70 DLQ messages were a single class — Gold
`momentum_vault_insert` Postgres-DNS failures from a 28s window during the F6 scale-cycle;
Gold had no transient retry. OpenAI 429 and Polymarket 422 were **zero** in the DLQ.
OpenSky + pytrends confirmed broken, sharing a silent-success app pattern.

**Key decisions / fixes (single package):**
- **D-B-1/2:** custom `utils/retry.py` (over adding tenacity) — 5 retries, exp-backoff, on
  Gold DB inserts; paired with `publishNotReadyAddresses: true` on the postgres Service.
  Re-ran scale-cycle → +2 DLQ vs +4000 before.
- **D-B-3:** centralized `utils/openai_client.py` factory (`max_retries=5`, `timeout=60`);
  replaced 12 in-place instantiations across 7 files (semantics-preserving; 102/102 existing
  tests still pass).
- **D-B-4:** Polymarket comments **feature-flagged off** (`POLYMARKET_COMMENTS_ENABLED=false`)
  — Gamma `/comments` breaking change (now needs `parent_entity_id` + `entity_entity_type`;
  21 enum values tried, none worked). Stops ~100 warnings/cycle; preserves the code path.
- **D-B-5:** producer **raise-on-0% success** (OpenSky + GoogleTrends) so Airflow reports
  `failed` instead of silent `success` with 0 Bronze.
- **D-B-6/7:** per-image **`*-p95` tag bump** (clean pull, no stale-cache risk) and the
  documented rule that **Flink jobs must be cancel + re-submitted after a code-bearing
  image rebuild** (HA recovers the old BLOB).

**Images rebuilt + pushed:** `anizai-flink:1.19.1-p95`, `anizai-agent:0.2.0-p95`,
`anizai-airflow:2.9.3-p95`, `anizai-polymarket:0.2.0-p95`. 20/20 new unit tests pass.

**Gaps raised:** KG-PHASE-9.5-4 (Polymarket comments retire-vs-repair, Domain A),
-5 (pytrends no upstream fix, Domain A), -8 (Flink re-submit → KG-C-4),
-9 (OpenAI cost analysis — parallel session).

### Stage C — Monitoring + Operational Documentation (CLOSED 2026-05-20)

**Outcome.** Monitoring shifted from "pod liveness" to "pipeline functionality"; the
long-term operations runbook written.

**Pre-action:** dropped a 5,939-message Silver→Gold backlog (cancel Gold → truncate via
`kafka-delete-records.sh` → resubmit; first checkpoint in 14.7s) — saved ~$88 / ~14,600
RPD-calls.

**Key decisions / deliverables:**
- **D-C-1:** two notification paths — Prometheus + **Alertmanager → Gmail SMTP** for
  metric alerts; **Cloud Logging metric + Cloud Monitoring policies** for the OpenAI-429
  log-derived alerts (agent `/metrics` is still a Sprint-18 stub, so 429 uses log-line
  counts). Both land in `ron.mintz21@gmail.com`.
- **D-C-2:** conservative V1 thresholds (revisit after 2 weeks); the one exception is the
  OpenAI-429 first-occurrence warning (KG-C-1 makes early signal high-value).
- **D-C-3:** custom `pg_anizai_*` metrics via postgres_exporter `--extend.query-path`
  (built-ins don't give per-source freshness).
- **D-C-4/5:** `sed` substitution for the Gmail app-password in the Alertmanager config
  (no `envsubst` in the alpine base); backlog-drop documented as a standard operation.

**Deployed:** `kafka-exporter` + `postgres-exporter` Deployments; Alertmanager Deployment
+ ConfigMap (`GMAIL_APP_PASSWORD` secret); `prometheus-rules-configmap.yaml` (11 metric
rules) + 2 Cloud Monitoring policies (13 alert rules total); `Anizai Pipeline Health`
Grafana dashboard; `infrastructure/gcp/06_monitoring_setup.sh`;
`guides/cluster_operations_guide.md` (~15-section operational reference).

**Verification:** `alertmanager_notifications_total{integration="email"} 6`,
`_failed_total 0`; alert rules fired true-positive `DailyBronzeStale` (expected given the
cluster was scaled down most of 5/19); a Flink `job_name` hyphen→underscore label
false-positive caught and fixed.

**End state:** **Phase 9.5 closed 2026-05-20.** Cloud Scheduler left **PAUSED** (Ron's
manual final step). Last cloud deployment touch on record.
