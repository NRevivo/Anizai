# Anizai Data Pipeline — Cloud Connection Guide
## GKE Cluster: anizai-cluster | Project: anizai-pipehub

> Last updated: 2026-08-15 — project-identity pass (KG-C-11). Every `--project=`,
> the kubectl context string, the Artifact Registry path and the GCS backup bucket
> were re-pointed from the retired `anizai-pipeline` to `anizai-pipehub`. Identity
> facts are owned by `docs/C_cloud/cloud_constants.md`. **Scope limit: identity
> strings only — no procedure in this guide was re-verified against a live cluster
> in that pass.** Four places where the rename alone does *not* make the surrounding
> instruction correct are called out inline: the two-identity note in §1.0.2, the
> backup-bucket start date in §1.9, the registry tag inventory in §2.4, and the
> billing history break in §2.6.

> **Scope — read this first.** This guide is about **connecting** to a cluster that is
> already running: port-forwards, credentials, UIs, and where things live in the GCP
> Console. It is **not** the bring-up procedure. To start or stop the cluster — whole
> system, pipeline-only, or agents-only — use
> `data-pipeline/docs/guides/bringup_profiles.md`, which carries the profile table and
> the pre-flight gates. The bare `clusters resize` commands below will start every
> workload whose desired replicas are ≥ 1, with no gate in front of them.
>
> For triage once you are connected, see `guides/cluster_operations_guide.md`.
> For what is actually deployed right now, see `docs/C_cloud/cloud_state.md` — the
> live cluster always wins over any value written into a guide.
>
> **Full accuracy sweep 2026-07-26** (closes KG-C-6). The two defects KG-C-6 named —
> lowercase secret names and an outdated Scheduler schedule — were already gone; the
> sweep instead corrected the node-pool count, the Flink re-submit instruction, the
> Artifact Registry paths, the agent metric names, topic and job names, and the
> Firestore collection layout.

---

## QUICK-START CHEAT SHEET

```powershell
# 1. Authenticate and set kubectl context (one-time per machine):
gcloud auth login
gcloud container clusters get-credentials anizai-cluster `
  --zone us-central1-a --project anizai-pipehub

# 2. Open all port-forwards in separate terminals (or background jobs):
kubectl port-forward -n anizai svc/airflow-webserver 8090:8080
kubectl port-forward -n anizai svc/kafka-ui 8080:8080
kubectl port-forward -n anizai svc/flink-jobmanager 8081:8081
kubectl port-forward -n anizai svc/grafana 3000:3000
kubectl port-forward -n anizai svc/prometheus 9090:9090
kubectl port-forward -n anizai svc/postgres 5432:5432

# 3. Open in browser:
#   Airflow:    http://localhost:8090  (credentials in Secret Manager)
#   Kafka UI:   http://localhost:8080  (no credentials required)
#   Flink:      http://localhost:8081  (no credentials required)
#   Grafana:    http://localhost:3000  (credentials in Secret Manager)
#   Prometheus: http://localhost:9090  (no credentials required)

# 4. Query PostgreSQL directly:
psql -h localhost -U anizai -d anizai
# (password in Secret Manager — see Section 1.6)

# 5. Retrieve any secret:
gcloud secrets versions access latest --secret=SECRET_NAME --project=anizai-pipehub

# 6. Scale main-pool on/off:
gcloud container clusters resize anizai-cluster `
  --node-pool=main-pool --num-nodes=1 `
  --zone=us-central1-a --project=anizai-pipehub   # ON

gcloud container clusters resize anizai-cluster `
  --node-pool=main-pool --num-nodes=0 `
  --zone=us-central1-a --project=anizai-pipehub   # OFF
```

**Port-forward reference:**

| Service | Command | URL | Purpose |
|---------|---------|-----|---------|
| Airflow | `kubectl port-forward -n anizai svc/airflow-webserver 8090:8080` | http://localhost:8090 | DAG monitoring, trigger producers |
| Kafka UI | `kubectl port-forward -n anizai svc/kafka-ui 8080:8080` | http://localhost:8080 | Topic inspection, message counts |
| Flink UI | `kubectl port-forward -n anizai svc/flink-jobmanager 8081:8081` | http://localhost:8081 | Job status, checkpoint info |
| Grafana | `kubectl port-forward -n anizai svc/grafana 3000:3000` | http://localhost:3000 | Pipeline metrics dashboards |
| Prometheus | `kubectl port-forward -n anizai svc/prometheus 9090:9090` | http://localhost:9090 | Raw metrics |
| PostgreSQL | `kubectl port-forward -n anizai svc/postgres 5432:5432` | `psql -h localhost -U anizai -d anizai` | Query vault tables directly |
| Agent worker | `kubectl port-forward -n anizai svc/agent-worker 8000:8000` | http://localhost:8000/health | Agent /health + /metrics endpoint |

**Start all port-forwards in one PowerShell window (background jobs):**

```powershell
$services = @(
  "svc/postgres 5432:5432",
  "svc/kafka 9092:9092",
  "svc/kafka-ui 8080:8080",
  "svc/flink-jobmanager 8081:8081",
  "svc/airflow-webserver 8090:8080",
  "svc/prometheus 9090:9090",
  "svc/grafana 3000:3000",
  "svc/agent-worker 8000:8000"
)
foreach ($svc in $services) {
    Start-Job -ScriptBlock { param($s) kubectl port-forward -n anizai $s.Split(" ") } -ArgumentList $svc
}
Get-Job   # verify all Running
```

**Stop all port-forwards:**

```powershell
Get-Job | Stop-Job; Get-Job | Remove-Job
```

---

## Part 1 — Local Connection (kubectl port-forward)

---

## Section 1.0 — Prerequisites

### 1.0.1 Install Required Tools

All three tools must be installed and on your PATH before proceeding:

- **gcloud CLI** — [cloud.google.com/sdk/docs/install](https://cloud.google.com/sdk/docs/install)
- **kubectl** — install via gcloud: `gcloud components install kubectl`
- **psql client** — ships with PostgreSQL; on Windows install [PostgreSQL](https://www.postgresql.org/download/windows/) or use `winget install PostgreSQL.PostgreSQL`

### 1.0.2 Authenticate with GCP

```powershell
# Authenticate your user account:
gcloud auth login

# Set the default project:
gcloud config set project anizai-pipehub

# NOTE — there are TWO Google identities, one per project. kingron79@gmail.com
# owns anizai-pipehub and has NO access to anizai-ai; ron.mintz21@gmail.com owns
# anizai-ai. For any anizai-ai work pass --account=ron.mintz21@gmail.com as a
# PER-INVOCATION flag; never `gcloud config set account`, or every subsequent
# anizai-pipehub command silently runs as the wrong identity. Under kingron79@,
# an anizai-ai read returns a PERMISSION ERROR, not an empty result — the two
# are not the same. Full detail: docs/C_cloud/cloud_constants.md §2.

# Optionally set application-default credentials (required by some SDK calls):
gcloud auth application-default login
```

### 1.0.3 Configure kubectl Context

This command downloads cluster credentials and writes them to your local kubeconfig:

```powershell
gcloud container clusters get-credentials anizai-cluster `
  --zone us-central1-a --project anizai-pipehub
```

**Verify the context is set correctly:**

```powershell
kubectl config current-context
# Expected: gke_anizai-pipehub_us-central1-a_anizai-cluster

kubectl get nodes
# Expected: one or two nodes in Ready state
```

### 1.0.4 Retrieve Credentials from Secret Manager

All service passwords are stored in GCP Secret Manager under project `anizai-pipehub`.
Never hard-code passwords. Always retrieve them like this:

```powershell
gcloud secrets versions access latest --secret=SECRET_NAME --project=anizai-pipehub
```

Secret names are listed in each service section below.

---

## Section 1.1 — Airflow UI

### Port-Forward

```powershell
kubectl port-forward -n anizai svc/airflow-webserver 8090:8080
```

Leave this terminal open. The forward stays active until you press `Ctrl+C`.

### Access

Open http://localhost:8090 in a browser.

**Credentials:**

```powershell
# Username: hardcoded "admin" (set in airflow-init-job.yaml — not stored as a secret).

# Password:
gcloud secrets versions access latest --secret=AIRFLOW_ADMIN_PASSWORD --project=anizai-pipehub
```

### What You Can Do Here

- **Monitor DAGs** — See all 7 scheduled DAGs (FRED, ArXiv, GoogleTrends, NewsAPI, HackerNews, OpenWeather, OpenSky) and their last-run status.
- **Trigger a DAG manually** — Click the DAG name → click the **▶ Run** button (top right of DAG row).
- **Inspect task logs** — Click a DAG run row → click a task box → **Log** tab.
- **Check for errors** — Search task logs for `ERROR` or `Traceback`.
- **Pause/unpause a DAG** — Toggle the switch to the left of the DAG name.

### Pod Logs

```powershell
# Scheduler logs (all DAG runs, task errors):
kubectl logs -n anizai deploy/airflow-scheduler --tail=100 -f

# Webserver logs:
kubectl logs -n anizai deploy/airflow-webserver --tail=50
```

### Pod Status

```powershell
kubectl get pods -n anizai -l component=scheduler
kubectl get pods -n anizai -l component=webserver
```

---

## Section 1.2 — Kafka UI

### Port-Forward

```powershell
kubectl port-forward -n anizai svc/kafka-ui 8080:8080
```

### Access

Open http://localhost:8080 in a browser. No credentials required.

### What You Can Do Here

- **Browse topics** — Left sidebar → **Topics**. You should see all 19 topics:
  `ingest.bronze.*`, `process.silver.*`, `serve.gold.*`, `ingestion_triggers`, `dead-letter-queue`.
  (The `kafka-init` CronJob re-asserts all 19 hourly with `--if-not-exists`.)
- **Inspect messages** — Click a topic → **Messages** tab → browse by offset or time.
- **Check message counts** — The topic list shows total message count and partition lag.
- **Monitor the dead-letter queue** — Click `dead-letter-queue` → **Messages** to see any schema-validation failures.

**Note:** Kafka topics are only accessible via port-forward — they are not visible in
the GCP Console. Kafka runs self-hosted inside the GKE cluster, not as a managed GCP service.

### Pod Logs

```powershell
# Kafka broker logs:
kubectl logs -n anizai statefulset/kafka --tail=100

# Kafka UI logs:
kubectl logs -n anizai deploy/kafka-ui --tail=50
```

### Pod Status

```powershell
kubectl get pods -n anizai -l app=kafka
kubectl get pods -n anizai -l app=kafka-ui
```

---

## Section 1.3 — Flink UI

### Port-Forward

```powershell
kubectl port-forward -n anizai svc/flink-jobmanager 8081:8081
```

### Access

Open http://localhost:8081 in a browser. No credentials required.

### What You Can Do Here

- **Check running jobs** — Left sidebar → **Jobs** → **Running Jobs**. You should see:
  - `anizai-silver-polymarket` (Bronze → Silver — the name is historical; this job
    handles **all** sources, not just Polymarket)
  - `anizai-gold-all-sources` (Silver → Gold enrichment + persistence)
- **Inspect job details** — Click a job name → **Subtasks** tab → watch `numRecordsIn` incrementing.
- **View checkpoint history** — Click a job → **Checkpoints** tab → confirm checkpoints complete in < 10 seconds.
- **Check TaskManager resources** — Left sidebar → **Task Managers** → confirm 1 TaskManager with 4 task slots.
- **Read job exceptions** — Click a failed job → **Exceptions** tab for the root cause.

### Pod Logs

```powershell
# JobManager logs (job submission, checkpoint coordination):
kubectl logs -n anizai deploy/flink-jobmanager --tail=100

# TaskManager logs (actual processing, enrichment errors):
kubectl logs -n anizai deploy/flink-taskmanager --tail=100 -f
```

### Pod Status

```powershell
kubectl get pods -n anizai -l app=flink-jobmanager
kubectl get pods -n anizai -l app=flink-taskmanager
```

---

## Section 1.4 — Grafana

### Port-Forward

```powershell
kubectl port-forward -n anizai svc/grafana 3000:3000
```

### Access

Open http://localhost:3000 in a browser.

**Credentials:**

```powershell
# Username: hardcoded "admin" (set in grafana-deployment.yaml as GF_SECURITY_ADMIN_USER env var — not stored as a secret).

# Password:
gcloud secrets versions access latest --secret=GRAFANA_ADMIN_PASSWORD --project=anizai-pipehub
```

### What You Can Do Here

- **View pipeline dashboards** — Left sidebar → **Dashboards** → click **"Anizai Pipeline"**.
- **Throughput row** — `Records/sec (Silver)` and `Records/sec (Gold)` — should show non-zero spikes when DAGs fire.
- **Checkpoint row** — Checkpoint duration (target < 10s) and checkpoint lag (target < 60s).
- **JVM / Resources row** — Heap usage (healthy at 30–50% of 2GB TaskManager memory).

**If panels show "No data":** Flink jobs may not be running. Flink metrics are only
exposed while a job is in `RUNNING` state. Verify jobs are running in the Flink UI
(Section 1.3), then click the Grafana refresh button (top right of dashboard).

### Pod Logs

```powershell
kubectl logs -n anizai deploy/grafana --tail=50
```

### Pod Status

```powershell
kubectl get pods -n anizai -l app=grafana
```

---

## Section 1.5 — Prometheus

### Port-Forward

```powershell
kubectl port-forward -n anizai svc/prometheus 9090:9090
```

### Access

Open http://localhost:9090 in a browser. No credentials required.

### What You Can Do Here

- **Query raw metrics** — Use the Expression Browser on the home page to run PromQL queries.
- **Check scrape targets** — Click **Status** → **Targets** to confirm Flink, Kafka, and PostgreSQL exporters are `UP`.
- **Explore available metrics** — Click **Graph** and start typing `flink_` or `kafka_` to autocomplete metric names.

**Sample queries:**

```
# Flink records processed per second (Silver job):
rate(flink_taskmanager_job_task_numRecordsIn_total[1m])

# Kafka consumer lag:
kafka_consumer_group_lag

# PostgreSQL active connections:
pg_stat_activity_count
```

### Pod Logs

```powershell
kubectl logs -n anizai deploy/prometheus --tail=50
```

### Pod Status

```powershell
kubectl get pods -n anizai -l app=prometheus
```

---

## Section 1.6 — PostgreSQL (psql)

### Port-Forward

```powershell
kubectl port-forward -n anizai svc/postgres 5432:5432
```

### Access

In a separate terminal (leave the port-forward terminal open):

```powershell
# Retrieve password first:
gcloud secrets versions access latest --secret=POSTGRES_PASSWORD --project=anizai-pipehub

# Connect:
psql -h localhost -U anizai -d anizai
# Enter the password retrieved above when prompted.
```

**Connection string format (for tools like DBeaver, TablePlus):**

```
Host:     localhost
Port:     5432
Database: anizai
Username: anizai
Password: (retrieved from Secret Manager — secret: POSTGRES_PASSWORD)
```

### What You Can Do Here

Query the five vault tables directly:

```sql
-- Record counts across all vault tables:
SELECT 'knowledge_vault'  AS tbl, COUNT(*) FROM knowledge_vault
UNION ALL
SELECT 'knowledge_vectors', COUNT(*) FROM knowledge_vectors
UNION ALL
SELECT 'social_vault',      COUNT(*) FROM social_vault
UNION ALL
SELECT 'social_vectors',    COUNT(*) FROM social_vectors
UNION ALL
SELECT 'momentum_vault',    COUNT(*) FROM momentum_vault
UNION ALL
SELECT 'mapping_dict',      COUNT(*) FROM mapping_dict;

-- Most recent records per source:
SELECT source_name, MAX(ingested_at) AS last_seen, COUNT(*) AS total
FROM knowledge_vault
GROUP BY source_name ORDER BY last_seen DESC;

-- Check for recent Gold enrichment:
SELECT source_platform, entry_type, COUNT(*) AS records,
       MAX(ingested_at) AS last_seen
FROM knowledge_vectors
GROUP BY source_platform, entry_type ORDER BY last_seen DESC;
```

See `data-pipeline/docs/VALIDATION_GUIDE.md` Section C for full query examples per table.

**Note:** PostgreSQL is self-hosted inside the GKE cluster — it is **not** Cloud SQL.
Its data is not visible anywhere in the GCP Console. Port-forward is the only way
to access it from a local machine.

### Pod Logs

```powershell
kubectl logs -n anizai statefulset/postgres --tail=100
```

### Pod Status

```powershell
kubectl get pods -n anizai -l app=postgres
```

---

## Section 1.7 — Agent Worker (Phase 8 Hub)

The agent worker exposes a health endpoint and Prometheus metrics endpoint on port 8000.

### Port-Forward

```powershell
kubectl port-forward -n anizai svc/agent-worker 8000:8000
```

### Health Check

```powershell
curl http://localhost:8000/health
# Expected: {"status": "ok", "worker_id": "worker-1", "agent_version": "0.5.0-sprint26+<git-sha>"}
```

### Metrics (Prometheus exposition format)

```powershell
curl http://localhost:8000/metrics
# Three real metric families since Sprint 26 (verified against agent/metrics.py):
#   agent_node_duration_seconds{node_name=...}   Histogram — per-node wall clock
#   agent_llm_cost_usd_total{model=...}          Counter   — cumulative USD by model
#   agent_session_total{tier=...,status=...}     Counter   — terminal outcomes
```

**These are the authoritative source for agent cost and latency** — the agent's INFO
logs (including `llm_usage`) are 1 %-sampled in cloud, so do not expect to reconstruct
cost from `kubectl logs`. See `cluster_operations_guide.md` §5.6 / §11 and KG-B-4.

`agent_queue_depth` and `agent_active_sessions` appear in older drafts of this guide
and do **not** exist — the queue-depth gauge is Sprint 27 task 27.13, unbuilt.

These metrics are also scraped automatically by the Prometheus pod (Section 1.5).

### Pod Logs

```powershell
# Stream agent logs (forecast pipeline node sequence):
kubectl logs -f -n anizai deploy/agent-worker --tail=100
```

Expected log sequence per main-graph session:
`claim_session → query_understand → build_embedding → vault_query → sufficiency_check
→ rate_evidence → synthesize → generate_suggested_actions → write_to_firestore`

Branches: an ambiguous question routes to clarification and the session pauses at
`awaiting_clarification`; an insufficient vault routes through
`trigger_reactive_ingestion` (fire-and-forget) and rejoins at `rate_evidence`.
Follow-up messages run a **separate, much shorter** subgraph and emit no agentEvents.

**Remember the 1 % INFO sampling** — a healthy session will usually show only a
fraction of this sequence in the logs, or none of it. Absence is not failure.

### Pod Status

```powershell
kubectl get pods -n anizai -l app=agent-worker
```

---

## Section 1.8 — Local Frontend → Cloud Firestore (E2E Testing)

The frontend connects to **cloud Firestore** (`anizai-ai`) directly — no port-forward needed
for Firestore (Firebase SDK uses HTTPS). Port-forward Postgres and the agent health endpoint
for any local dev that needs them.

### Frontend `.env` Configuration

**Frontend `.env` (or `client/.env`) variables for cloud mode:**

```env
VITE_FIREBASE_PROJECT_ID=anizai-ai
VITE_FIREBASE_API_KEY=<same as local dev>
VITE_FIREBASE_AUTH_DOMAIN=anizai-ai.firebaseapp.com
# No POSTGRES_HOST needed by the frontend — Postgres is accessed by the agent
```

### E2E Forecast Submission Walkthrough

1. Start all port-forwards (see Quick-Start Cheat Sheet above).
2. Open the local frontend with `VITE_FIREBASE_PROJECT_ID=anizai-ai`.
3. Submit a question (e.g., "Will the Fed cut rates by Q2 2026?").
4. Watch agent logs in real time:
   ```powershell
   kubectl logs -f -n anizai deploy/agent-worker
   ```
5. Expect the log sequence shown in Section 1.7 above.
6. Frontend renders the BI cards when Firestore `sessionResults` is written (five since
   Sprint 22: prediction series, sentiment time series, evidence, market probability on a
   Tier-1 match, and suggested actions from Sprint 25).

---

## Section 1.9 — Backup & Restore

**Backup runs daily at 02:00 UTC** → `gs://anizai-pipehub-backups/postgres/YYYY-MM-DD/anizai.sql.gz`

> **⚠ This bucket starts at 2026-08-15.** It is a new, separately-namespaced
> bucket created with the `anizai-pipehub` project — not a rename of
> `gs://anizai-pipeline-backups`, and **nothing was copied across**. Its earliest
> object is `postgres/2026-08-15/anizai.sql.gz`. Every backup taken before that
> date lives in the retired project's bucket and is lost when that project is
> deleted (`migration_plan.md` §9 item 3). Restore dates earlier than 2026-08-15
> are not available here — list the prefix before assuming a date exists.
> Separately, the CronJob does not fire while `main-pool` rests at 0 nodes, so
> days are missing even after that date (KG-C-9).

### Manual Restore to Local Postgres

```powershell
# Download a specific day's backup. Substitute a date that EXISTS — see the
# warning below; the old hardcoded 2026-05-10 example was from the retired
# project's bucket and does not exist here.
gsutil ls gs://anizai-pipehub-backups/postgres/          # pick a date first
gsutil cp gs://anizai-pipehub-backups/postgres/YYYY-MM-DD/anizai.sql.gz .

# Restore to local scratch (requires local Postgres with TimescaleDB)
gunzip anizai.sql.gz
psql -h localhost -U anizai -d anizai_scratch < anizai.sql

# Compare row counts
psql -h localhost -U anizai -d anizai_scratch -c "SELECT relname, n_live_tup FROM pg_stat_user_tables WHERE schemaname='public' ORDER BY relname;"
```

Lifecycle: backups older than 30 days are auto-deleted from the bucket.

---

## Section 1.10 — Scaling the Cluster (Start / Stop Data Collection)

> **Prefer `guides/bringup_profiles.md`.** It wraps the commands below in a profile
> selector and two gates. Use this section only for the raw command syntax.

The cluster has **one** node pool:

| Pool | Purpose | Cost when running |
|------|---------|------------------|
| `main-pool` | Everything — Airflow, Kafka, Flink, PostgreSQL, the producers, the agent, and monitoring | ~$0.15/hr |

`polymarket-pool` was **deleted in Phase 9.5 Stage A**; Polymarket now runs on
`main-pool` with everything else. There is no second pool, and nothing keeps running
when `main-pool` is at 0.

Scaling `main-pool` to 0 stops all billing for compute. PVCs (Postgres, Kafka, Flink
checkpoints, Prometheus, Airflow metadata) persist and are billed separately.

### Start Data Collection (main-pool on)

```powershell
gcloud container clusters resize anizai-cluster `
  --node-pool=main-pool `
  --num-nodes=1 `
  --zone=us-central1-a `
  --project=anizai-pipehub
```

After scaling up, wait 3–5 minutes for all pods to reach `Running` state:

```powershell
kubectl get pods -n anizai --watch
```

**Flink jobs recover on their own — do NOT re-submit them.** K8s HA (ConfigMap leader
election) preserves the compiled job graph across pod restarts, so the Silver and Gold
jobs come back by themselves. Submitting again produces duplicate jobs consuming the
same topics. Verify recovery instead:

```powershell
$JM = kubectl get pods -n anizai -l app=flink-jobmanager -o jsonpath='{.items[0].metadata.name}'
kubectl exec -n anizai $JM -- curl -s http://localhost:8081/jobs/overview
# Expected: anizai-silver-polymarket + anizai-gold-all-sources, both state=RUNNING
```

The one case that **does** require cancel + re-submit is a **new `anizai-flink` image**:
HA restores the previously compiled code, not the new image's code. Full procedure in
`cluster_operations_guide.md` §6 (KG-C-4).

### Stop Data Collection (main-pool off)

```powershell
gcloud container clusters resize anizai-cluster `
  --node-pool=main-pool `
  --num-nodes=0 `
  --zone=us-central1-a `
  --project=anizai-pipehub
```

**Note:** Scaling down evicts all pods on `main-pool`. Kafka messages that have not
yet been processed by Flink will be lost unless Kafka topic retention is set
(configured to 7 days by default). PostgreSQL data persists on its PVC.

---

## Section 1.11 — Check All Pod Status at Once

```powershell
# All pods in the anizai namespace with status:
kubectl get pods -n anizai

# Expected when main-pool is running (all should be Running or Completed):
# NAME                                  READY   STATUS
# airflow-scheduler-xxx                 1/1     Running
# airflow-webserver-xxx                 1/1     Running
# flink-jobmanager-xxx                  1/1     Running
# flink-taskmanager-xxx                 1/1     Running
# grafana-xxx                           1/1     Running
# kafka-0                               1/1     Running
# kafka-ui-xxx                          1/1     Running
# postgres-0                            1/1     Running
# prometheus-xxx                        1/1     Running
# polymarket-xxx                        1/1     Running
# telegram-xxx                          1/1     Running
# trigger-consumer-xxx                  1/1     Running
#
# Expectations that depend on desired replicas, NOT on the pool being up:
#   agent-worker      — declared replicas: 0. No pod unless deliberately scaled up.
#   flink-jm/tm, polymarket, telegram
#                     — currently held at 0 live while their manifests say 1 (KG-C-10).
#                       No pod will appear for them until someone scales them.
# Check desired replicas against the cluster, never against the repo:
#   kubectl get deploy -n anizai -o custom-columns=NAME:.metadata.name,DESIRED:.spec.replicas

# Describe a pod for detailed status / recent events:
kubectl describe pod -n anizai <pod-name>

# Stream logs from any pod:
kubectl logs -n anizai <pod-name> -f --tail=100
```

---

## Part 2 — GCP Console (Web UI)

Navigate to [console.cloud.google.com](https://console.cloud.google.com).
Use the project switcher at the top to select the correct project for each section.

---

## Section 2.1 — Projects

| Project ID | What lives here |
|------------|----------------|
| `anizai-pipehub` | GKE cluster, Artifact Registry, Secret Manager, Billing, Storage |
| `anizai-ai` | Firestore (Agentic Hub — forecasting query sessions) |

**Switch projects:** Click the project name in the top bar → select from the list,
or type the project ID in the search box.

---

## Section 2.2 — GKE Workloads (Pods)

**Project:** `anizai-pipehub`

**Path:** Hamburger menu → **Kubernetes Engine** → **Workloads**

What you can do here:
- See all Deployments and StatefulSets in the `anizai` namespace.
- Click a workload name → **Pods** tab → click a pod name → **Logs** tab to stream logs in-browser.
- Click a workload → **Details** tab to see the Docker image, environment variables, resource requests.
- Check pod restart counts (high restarts = OOMKill or crash loop — investigate logs).

**To filter to the anizai namespace:** After clicking **Workloads**, use the
**Namespace** dropdown at the top of the list and select `anizai`.

**GKE Cluster overview:** Hamburger menu → **Kubernetes Engine** → **Clusters** →
click `anizai-cluster` to see node pool status, node count, and cluster version.

---

## Section 2.3 — Storage (Persistent Volumes / Disks)

**Project:** `anizai-pipehub`

**Path:** Hamburger menu → **Compute Engine** → **Disks**

What you can find here:
- PVCs for PostgreSQL and Kafka are backed by GCE persistent disks.
- Disk names follow the pattern `pvc-*` — match to the PVC name in `kubectl get pvc -n anizai`.
- Check disk size, type (SSD vs. standard), and zone.

**To see PVC names and bound disk names:**

```powershell
kubectl get pvc -n anizai
```

**Note:** The PostgreSQL data volume is the source of truth for vault tables.
If a disk is accidentally deleted, vault data is lost. Do not delete disks named
`pvc-*` without confirming which PVC they back.

---

## Section 2.4 — Artifact Registry (Docker Images)

**Project:** `anizai-pipehub`

**Path:** Hamburger menu → **Artifact Registry** → **Repositories**

What you can find here:
- The `anizai-images` repository holds all custom Docker images.
- Key images: `anizai-flink`, `anizai-airflow`, `anizai-agent`, `anizai-polymarket`,
  `anizai-telegram`, `anizai-trigger-consumer`.
- Click an image name to see all tags and their push timestamps.
- Digest hashes here match the image digests in `kubectl describe pod`.

**To pull an image locally (for debugging):**

```powershell
gcloud auth configure-docker us-central1-docker.pkg.dev
docker pull us-central1-docker.pkg.dev/anizai-pipehub/anizai-images/<IMAGE>:<TAG>
```

> **⚠ This registry holds only the 10 tags pushed at the 2026-08-15 migration**
> — it is not a copy of the retired project's registry. A tag you remember from
> before that date may simply not exist here; `gcloud artifacts docker images
> list` before pulling. One tag is gone for good: `anizai-polymarket:0.3.0-price`
> was never pushed and ceases to exist when the old project is deleted
> (`carryover-20260815-migration.md` §10, an accepted loss).

**Tags are mutable and pods use `imagePullPolicy: Always` (KG-C-3)** — re-pushing a tag
silently changes what runs. When identity matters, compare the `@sha256:` digest from
`kubectl describe pod` against `cloud_state.md` §3, not the tag.

---

## Section 2.5 — Secret Manager (Credentials)

**Project:** `anizai-pipehub`

**Path:** Hamburger menu → **Security** → **Secret Manager**

What you can find here:
- All service credentials: PostgreSQL passwords, Airflow admin credentials, Grafana admin credentials, API keys for external data sources.
- Click a secret name → **Versions** tab → click a version → **Access Secret Value** to reveal the value in-browser.
- Secret rotation history: each new version appears as a new row.

**To retrieve a secret from the command line:**

```powershell
gcloud secrets versions access latest --secret=SECRET_NAME --project=anizai-pipehub
```

**Key secrets reference:**

| Secret Name | Used By |
|-------------|---------|
| `POSTGRES_PASSWORD` | PostgreSQL `anizai` user |
| `AIRFLOW_ADMIN_PASSWORD` | Airflow web login (username is plain env var `admin`) |
| `AIRFLOW_FERNET_KEY` | Airflow connection encryption |
| `GRAFANA_ADMIN_PASSWORD` | Grafana web login (username is plain env var `admin`) |
| `OPENAI_API_KEY` | Gold enrichment + agent synthesis (GPT-4o / GPT-4o-mini) |
| `NEWSAI_API_KEY` | NewsAPI ingestion (TheNewsAPI provider since Sprint 21.5). **The name is misleading — it holds a thenewsapi.com key, not a newsapi.ai one. Rename to `THE_NEWS_API_KEY` is pending (KG-C-5); do not rotate a newsapi.ai key into it.** |
| `FRED_API_KEY` | FRED economic data |
| `OPENWEATHER_API_KEY` | OpenWeather source |
| `OPENSKY_CLIENT_ID` | OpenSky OAuth2 |
| `OPENSKY_CLIENT_SECRET` | OpenSky OAuth2 |
| `TELEGRAM_API_ID` | Telegram MTProto |
| `TELEGRAM_API_HASH` | Telegram MTProto |

---

## Section 2.6 — Billing and Budget Alerts

**Project:** `anizai-pipehub`

**Path:** Hamburger menu → **Billing** → select the billing account → **Budgets & alerts**

What you can find here:
- Current spend vs. budget threshold for the month.
- Alert history — emails sent to `ron.mintz21@gmail.com` when 50%, 90%, or 100% of budget is reached.
- Click a budget name → **Edit** to adjust the threshold or alert recipients.

**Path to current month cost breakdown:** Hamburger menu → **Billing** → **Reports**
— filter by project `anizai-pipehub`, group by **Service** or **SKU** to identify
which services are driving cost (typically GKE node pool compute and PD storage).

> **Cost history does not span the migration.** The billing account
> (`010C82-6CA2C4-183381`, ILS) is unchanged, but spend before 2026-08-14 is
> attributed to the retired `anizai-pipeline` project. A report filtered to
> `anizai-pipehub` shows nothing before that date — that is the filter, not a
> billing fault — and the old project's history disappears when it is deleted.
> To compare against a pre-migration baseline, filter by billing account rather
> than by project, and do it before deletion.

---

## Section 2.7 — What Is NOT in the GCP Console

**Kafka topics and messages** — Kafka runs self-hosted in GKE. There is no GCP-managed
Kafka service. Use Kafka UI via port-forward (Section 1.2) to inspect topics and messages.

**Pipeline vault data (knowledge_vault, social_vault, etc.)** — PostgreSQL is
self-hosted in GKE as a StatefulSet. It is **not** Cloud SQL. Vault data is not
accessible through the GCP Console. Use `psql` via port-forward (Section 1.6)
or the Airflow UI to query results.

**Flink job state** — Flink checkpoints are stored on the PostgreSQL PVC, not in GCS.
Use the Flink UI via port-forward (Section 1.3) to inspect running jobs and checkpoints.

**Airflow DAG runs and task logs** — Airflow runs inside GKE. DAG run history and
task logs are only visible via the Airflow UI (Section 1.1) or via `kubectl logs`.

---

## Section 2.8 — Firestore (Agentic Hub)

**Project:** `anizai-ai` *(different project — switch before navigating)*

**Path:** Hamburger menu → **Firestore**

What you can find here:
- `forecastQueries` — the worker's **queue** collection. The agent's main listener watches
  `status=='pending'`; documents also rest at `claimed`, `awaiting_clarification`, `done`,
  and `failed`. Nothing scans `claimed`, so orphans there sit forever (KG-B-21).
- `sessions/{sessionId}` — the session document, with subcollections:
  - `agentEvents` — reasoning events streamed by the main graph (follow-ups emit none)
  - `messages` — follow-up conversation turns; the agent's **second** listener is a
    collection-group query over these (`role=='user'` and `status=='sent'`)
  - plus the result subcollections written at the end of a forecast
- `sessionResults` — written top-level (see KG-B-2 for the spec/implementation drift).
- Click a document ID to inspect all fields and their current values.

**Note:** This data is written by the Agentic Hub. If you are debugging a pipeline
(Domain A) issue, you do not need to access Firestore.

**Before scaling the agent up**, check `forecastQueries` for `pending` documents and the
`messages` collection group for unanswered `sent` documents — both listeners claim their
full match set the instant they attach. See `bringup_profiles.md` §3 Step 4.

---

## Troubleshooting

### Port-forward disconnects unexpectedly

`kubectl port-forward` will silently disconnect if the backing pod restarts.
If a browser URL stops loading, re-run the port-forward command.

For persistent access, consider running port-forwards as background PowerShell jobs:

```powershell
Start-Job -ScriptBlock { kubectl port-forward -n anizai svc/airflow-webserver 8090:8080 }
Start-Job -ScriptBlock { kubectl port-forward -n anizai svc/kafka-ui 8080:8080 }
# List running jobs: Get-Job
# Stop all: Get-Job | Stop-Job | Remove-Job
```

### kubectl: error — context not set

```powershell
gcloud container clusters get-credentials anizai-cluster `
  --zone us-central1-a --project anizai-pipehub
```

### Pod is in CrashLoopBackOff

```powershell
# See recent events and error reason:
kubectl describe pod -n anizai <pod-name>

# Stream the crash logs:
kubectl logs -n anizai <pod-name> --previous
```

Common causes: missing secret reference, OOMKill (increase memory request in manifest),
or a dependent service (Kafka, Postgres) not yet ready.

### main-pool scaled to 0 but pods still show Running

GKE drains nodes gracefully. Wait 2–3 minutes and re-check:

```powershell
kubectl get pods -n anizai
kubectl get nodes
```

If pods persist, check for PodDisruptionBudgets blocking eviction:

```powershell
kubectl get pdb -n anizai
```

### OpenAI quota (KG-PHASE-C-5)

Gold job and agent synthesis both call OpenAI. If Gold embeddings fail (429) or the
agent synthesis fails with a quota error, check the OpenAI dashboard for the key
stored in Secret Manager (`OPENAI_API_KEY`). The agent uses GPT-4o for synthesis and
GPT-4o-mini for other nodes — verify both have quota.
