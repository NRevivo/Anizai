# Anizai Data Pipeline — Cloud Connection Guide
## GKE Cluster: anizai-cluster | Project: anizai-pipeline

---

## QUICK-START CHEAT SHEET

```powershell
# 1. Authenticate and set kubectl context (one-time per machine):
gcloud auth login
gcloud container clusters get-credentials anizai-cluster `
  --zone us-central1-a --project anizai-pipeline

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
gcloud secrets versions access latest --secret=SECRET_NAME --project=anizai-pipeline

# 6. Scale main-pool on/off:
gcloud container clusters resize anizai-cluster `
  --node-pool=main-pool --num-nodes=1 `
  --zone=us-central1-a --project=anizai-pipeline   # ON

gcloud container clusters resize anizai-cluster `
  --node-pool=main-pool --num-nodes=0 `
  --zone=us-central1-a --project=anizai-pipeline   # OFF
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
gcloud config set project anizai-pipeline

# Optionally set application-default credentials (required by some SDK calls):
gcloud auth application-default login
```

### 1.0.3 Configure kubectl Context

This command downloads cluster credentials and writes them to your local kubeconfig:

```powershell
gcloud container clusters get-credentials anizai-cluster `
  --zone us-central1-a --project anizai-pipeline
```

**Verify the context is set correctly:**

```powershell
kubectl config current-context
# Expected: gke_anizai-pipeline_us-central1-a_anizai-cluster

kubectl get nodes
# Expected: one or two nodes in Ready state
```

### 1.0.4 Retrieve Credentials from Secret Manager

All service passwords are stored in GCP Secret Manager under project `anizai-pipeline`.
Never hard-code passwords. Always retrieve them like this:

```powershell
gcloud secrets versions access latest --secret=SECRET_NAME --project=anizai-pipeline
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
# Username:
gcloud secrets versions access latest --secret=airflow-admin-username --project=anizai-pipeline

# Password:
gcloud secrets versions access latest --secret=airflow-admin-password --project=anizai-pipeline
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

- **Browse topics** — Left sidebar → **Topics**. You should see all 15 topics:
  `ingest.bronze.*`, `process.silver.*`, `serve.gold.*`, `ingestion_triggers`, `dead-letter-queue`.
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
  - `anizai-silver-polymarket` (Bronze → Silver transformation)
  - `anizai-gold-polymarket` (Silver → Gold enrichment + persistence)
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
# Username:
gcloud secrets versions access latest --secret=grafana-admin-username --project=anizai-pipeline

# Password:
gcloud secrets versions access latest --secret=grafana-admin-password --project=anizai-pipeline
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
gcloud secrets versions access latest --secret=postgres-anizai-password --project=anizai-pipeline

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
Password: (retrieved from Secret Manager — secret: postgres-anizai-password)
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
# Expected: {"status": "ok", "worker_id": "worker-1", "agent_version": "0.4.0-sprint21-..."}
```

### Metrics (Prometheus exposition format)

```powershell
curl http://localhost:8000/metrics
# Sample output keys:
#   agent_node_duration_ms{node_name=...}
#   agent_session_total{outcome=done|failed|clarification_needed}
#   agent_llm_cost_usd_total{model=...}
#   agent_queue_depth
#   agent_active_sessions
```

These metrics are also scraped automatically by the Prometheus pod (Section 1.5).

### Pod Logs

```powershell
# Stream agent logs (forecast pipeline node sequence):
kubectl logs -f -n anizai deploy/agent-worker --tail=100
```

Expected log sequence per session:
`claim_session → query_understand → build_embedding → vault_query → rate_evidence → synthesize → write_to_firestore`

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
6. Frontend renders the four BI cards when Firestore `sessionResults` is written.

---

## Section 1.9 — Backup & Restore

**Backup runs daily at 02:00 UTC** → `gs://anizai-pipeline-backups/postgres/YYYY-MM-DD/anizai.sql.gz`

### Manual Restore to Local Postgres

```powershell
# Download a specific day's backup
gsutil cp gs://anizai-pipeline-backups/postgres/2026-05-10/anizai.sql.gz .

# Restore to local scratch (requires local Postgres with TimescaleDB)
gunzip anizai.sql.gz
psql -h localhost -U anizai -d anizai_scratch < anizai.sql

# Compare row counts
psql -h localhost -U anizai -d anizai_scratch -c "SELECT relname, n_live_tup FROM pg_stat_user_tables WHERE schemaname='public' ORDER BY relname;"
```

Lifecycle: backups older than 30 days are auto-deleted from the bucket.

---

## Section 1.10 — Scaling the Cluster (Start / Stop Data Collection)

The cluster has two node pools:

| Pool | Purpose | Cost when running |
|------|---------|------------------|
| `main-pool` | Airflow, Kafka, Flink, Grafana, Prometheus, PostgreSQL | ~$0.15/hr |
| `polymarket-pool` | Polymarket WebSocket producer (always-on) | ~$0.05/hr |

Scale `main-pool` down to 0 to pause data collection and stop billing for heavy services.
`polymarket-pool` continues running independently.

### Start Data Collection (main-pool on)

```powershell
gcloud container clusters resize anizai-cluster `
  --node-pool=main-pool `
  --num-nodes=1 `
  --zone=us-central1-a `
  --project=anizai-pipeline
```

After scaling up, wait 3–5 minutes for all pods to reach `Running` state:

```powershell
kubectl get pods -n anizai --watch
```

Flink jobs must be re-submitted after a scale-up (they do not auto-restart):

```powershell
# Find the JobManager pod name:
$JM_POD = kubectl get pods -n anizai -l app=flink-jobmanager -o jsonpath='{.items[0].metadata.name}'

# Submit Silver and Gold jobs:
kubectl exec -n anizai $JM_POD -- `
  flink run -py /opt/flink/usrlib/processing/silver_job.py
kubectl exec -n anizai $JM_POD -- `
  flink run -py /opt/flink/usrlib/processing/gold_job.py
```

### Stop Data Collection (main-pool off)

```powershell
gcloud container clusters resize anizai-cluster `
  --node-pool=main-pool `
  --num-nodes=0 `
  --zone=us-central1-a `
  --project=anizai-pipeline
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
# polymarket-producer-xxx               1/1     Running   <- polymarket-pool, always on

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
| `anizai-pipeline` | GKE cluster, Artifact Registry, Secret Manager, Billing, Storage |
| `anizai-ai` | Firestore (Agentic Hub — forecasting query sessions) |

**Switch projects:** Click the project name in the top bar → select from the list,
or type the project ID in the search box.

---

## Section 2.2 — GKE Workloads (Pods)

**Project:** `anizai-pipeline`

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

**Project:** `anizai-pipeline`

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

**Project:** `anizai-pipeline`

**Path:** Hamburger menu → **Artifact Registry** → **Repositories**

What you can find here:
- The `anizai` repository holds all custom Docker images built by CI.
- Key images: `anizai-flink`, `airflow-anizai`, `polymarket-producer`, `kafka-init`.
- Click an image name to see all tags and their push timestamps.
- Digest hashes here match the image digests in `kubectl describe pod`.

**To pull an image locally (for debugging):**

```powershell
gcloud auth configure-docker us-central1-docker.pkg.dev
docker pull us-central1-docker.pkg.dev/anizai-pipeline/anizai/<IMAGE>:<TAG>
```

---

## Section 2.5 — Secret Manager (Credentials)

**Project:** `anizai-pipeline`

**Path:** Hamburger menu → **Security** → **Secret Manager**

What you can find here:
- All service credentials: PostgreSQL passwords, Airflow admin credentials, Grafana admin credentials, API keys for external data sources.
- Click a secret name → **Versions** tab → click a version → **Access Secret Value** to reveal the value in-browser.
- Secret rotation history: each new version appears as a new row.

**To retrieve a secret from the command line:**

```powershell
gcloud secrets versions access latest --secret=SECRET_NAME --project=anizai-pipeline
```

**Key secrets reference:**

| Secret Name | Used By |
|-------------|---------|
| `postgres-anizai-password` | PostgreSQL `anizai` user |
| `airflow-admin-username` | Airflow web login |
| `airflow-admin-password` | Airflow web login |
| `grafana-admin-username` | Grafana web login |
| `grafana-admin-password` | Grafana web login |
| `airflow-fernet-key` | Airflow connection encryption |
| `openai-api-key` | Gold enrichment (GPT-4o-mini) |
| `newsai-api-key` | newsapi.ai / Event Registry |
| `fred-api-key` | FRED economic data |
| `openweather-api-key` | OpenWeather source |
| `opensky-client-id` | OpenSky OAuth2 |
| `opensky-client-secret` | OpenSky OAuth2 |
| `telegram-api-id` | Telegram MTProto |
| `telegram-api-hash` | Telegram MTProto |

---

## Section 2.6 — Billing and Budget Alerts

**Project:** `anizai-pipeline`

**Path:** Hamburger menu → **Billing** → select the billing account → **Budgets & alerts**

What you can find here:
- Current spend vs. budget threshold for the month.
- Alert history — emails sent to `ron.mintz21@gmail.com` when 50%, 90%, or 100% of budget is reached.
- Click a budget name → **Edit** to adjust the threshold or alert recipients.

**Path to current month cost breakdown:** Hamburger menu → **Billing** → **Reports**
— filter by project `anizai-pipeline`, group by **Service** or **SKU** to identify
which services are driving cost (typically GKE node pool compute and PD storage).

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
- `forecastQueries` collection — active and completed forecast sessions from the Agentic Hub frontend.
- `agentEvents` subcollection — streaming events written by the agent worker for each session.
- Click a document ID to inspect all fields and their current values.

**Note:** This data is written by the Agentic Hub (`data-pipeline/` is not involved).
If you are debugging a pipeline issue, you do not need to access Firestore.

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
  --zone us-central1-a --project anizai-pipeline
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
stored in Secret Manager (`openai-api-key`). The agent uses GPT-4o for synthesis and
GPT-4o-mini for other nodes — verify both have quota.
