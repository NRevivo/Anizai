# Anizai — GCP Setup Scripts (Phase C)

This directory holds every `gcloud` / billing setup script needed to stand up the
Phase C cloud foundation. Pair it with the Kubernetes manifests in
`../k8s/` and the per-sprint task tables in
[`../../docs/cloud_deployment_implementation.md`](../../docs/cloud_deployment_implementation.md).

All scripts are **idempotent** — re-running a script when the resource already
exists is a no-op (or, where applicable, adds a new version).

---

## Reference Card

| Constant | Value |
|---|---|
| GCP project (cluster + pipeline) | `anizai-pipeline` |
| GCP project (Firestore, cross-project) | `anizai-ai` |
| GKE cluster | `anizai-cluster` |
| Cluster zone | `us-central1-a` |
| Cluster pool — main | `main-pool`: `e2-standard-8` × 1 (manually scaled to 0 between collection windows) |
| Cluster pool — polymarket | `polymarket-pool`: `e2-micro` × 1 (always-on for Polymarket WebSocket continuity) |
| Artifact Registry | `us-central1-docker.pkg.dev/anizai-pipeline/anizai-images` |
| Namespace | `anizai` |
| Pipeline GSA (C1) | `pipeline-runtime@anizai-pipeline.iam.gserviceaccount.com` |
| Pipeline KSA (C1) | `pipeline-runtime` (in ns `anizai`) |
| Agent GSA (C5) | `agent-worker@anizai-pipeline.iam.gserviceaccount.com` |
| Agent KSA (C5) | `agent-worker` (in ns `anizai`) |
| Billing alerts (D4) | `₪200` warning (~$54) + `₪400` critical (~$108), 50/90/100% triggers — ILS because billing account currency is ILS |

---

## Pre-Sprint Checklist (one-time, manual)

Before running anything in this directory:

1. **`gcloud auth login`** — authenticate as a project Owner of `anizai-pipeline`.
2. **`gcloud config set project anizai-pipeline`** — activate the project.
3. **Link a billing account** to `anizai-pipeline` with a payment method.
4. **`gcloud components install gke-gcloud-auth-plugin`** — required for `kubectl` against GKE.
5. Have your **billing account ID** ready: `gcloud billing accounts list`.
6. Make sure **Docker is running** locally (image builds run from `data-pipeline/`).

---

## Scripts (run in order)

### `00_enable_apis.sh`
**What:** Enables 7 GCP APIs (`compute`, `container`, `artifactregistry`, `secretmanager`, `logging`, `monitoring`, `iam`) on `anizai-pipeline`.
**When:** Once at the start of Phase C. Re-run safely — idempotent.
**Runtime:** ~2 minutes.

```bash
bash infrastructure/gcp/00_enable_apis.sh
```

---

### `01_billing_alerts.sh`
**What:** Creates two budgets on the linked billing account: `₪200` warning (~$54) and `₪400` critical (~$108), each with 50% / 90% / 100% threshold notifications. Default IAM recipients of the billing account receive emails (Design Decision D4). Currency follows the billing account currency — ILS for `01C603-6D345F-105BC9`. gcloud rejects budgets denominated in any other currency on this account.
**When:** Once at the start of Phase C, after APIs are enabled (including `billingbudgets.googleapis.com`, which `00_enable_apis.sh` enables alongside the other 7).
**Runtime:** ~30 seconds.
**Requires:** `BILLING_ACCOUNT_ID` env var.

```bash
export BILLING_ACCOUNT_ID="XXXXXX-XXXXXX-XXXXXX"  # gcloud billing accounts list
bash infrastructure/gcp/01_billing_alerts.sh
```

#### Custom email channel (optional)
The script routes alerts to *billing-account default IAM recipients*. To also
notify a specific address (e.g. `ron.mintz21@gmail.com`), create a Cloud
Monitoring email channel and pass its resource name to the budget:

```bash
gcloud beta monitoring channels create \
  --display-name="Anizai billing alerts" \
  --type=email \
  --channel-labels=email_address=ron.mintz21@gmail.com
# Copy the channel resource name (projects/.../notificationChannels/NN)
gcloud billing budgets update <BUDGET_ID> \
  --billing-account="${BILLING_ACCOUNT_ID}" \
  --monitoring-notification-channels=<CHANNEL_NAME>
```

---

### `02_artifact_registry.sh`
**What:** Creates the Docker repo `anizai-images` in `us-central1` and runs
`gcloud auth configure-docker us-central1-docker.pkg.dev` so local `docker push`
works (Design Decision D5).
**When:** Once before any image push. Re-run the `configure-docker` portion in
any new shell that needs to push.
**Runtime:** ~30 seconds.

```bash
bash infrastructure/gcp/02_artifact_registry.sh
```

---

### `03_migrate_secrets.sh`
**What:** Reads `data-pipeline/infrastructure/.env` and creates 15 Secret
Manager secrets — one per allowlisted sensitive key (Design Decision D3).
Values are piped via stdin so they never appear in shell history.
**When:** Once at Sprint C1 after `.env` is finalized. To rotate a value, set
`ADD_NEW_VERSION=true` and re-run.
**Runtime:** ~1-2 minutes.

```bash
bash infrastructure/gcp/03_migrate_secrets.sh
# To rotate values from a freshly edited .env:
ADD_NEW_VERSION=true bash infrastructure/gcp/03_migrate_secrets.sh
```

The 15 keys (intentional allowlist — non-secret config like `POSTGRES_USER` or
`OPENAI_MODEL_NAME` is **not** migrated):

```
POSTGRES_PASSWORD, AIRFLOW_POSTGRES_PASSWORD, AIRFLOW_FERNET_KEY,
AIRFLOW_ADMIN_PASSWORD, GRAFANA_ADMIN_PASSWORD,
OPENAI_API_KEY, THE_NEWS_API_KEY, FRED_API_KEY, OPENWEATHER_API_KEY,
OPENSKY_CLIENT_ID, OPENSKY_CLIENT_SECRET,
POLYMARKET_API_KEY, POLYMARKET_API_SECRET,
TELEGRAM_API_ID, TELEGRAM_API_HASH
```

`TELEGRAM_SESSION_FILE` is added separately in Sprint C4 (16 total post-C4).

---

### `04_create_cluster.sh`
**What:** Provisions the GKE Standard cluster `anizai-cluster` in `us-central1-a` with **two node pools** (Design Decision D2 revised):

- **`main-pool`** — `e2-standard-8` × 1. All workloads except Polymarket. Manually scale to 0 between data-collection windows to drop compute spend to ~$0 (cluster-management fee aside).
- **`polymarket-pool`** — `e2-micro` × 1. Polymarket producer only — always-on so its WebSocket connection survives `main-pool` going to 0. Polymarket pod pins via `nodeSelector: cloud.google.com/gke-nodepool=polymarket-pool`.

Workload Identity pool enabled; GCE persistent-disk CSI driver enabled. Cluster create uses GKE-default `default-pool` first, then swaps it for `main-pool` (SDK 549's `clusters create` doesn't expose a flag to name the initial pool). Idempotent.

**When:** Once at Sprint C1.
**Runtime:** ~7-10 minutes (cluster + 3 pool ops).
**Cost:** `main-pool` ≈ `$200`/month at on-demand 24/7; `polymarket-pool` ≈ `$7`/month (always-on). Pause `main-pool` between collection windows:

```bash
gcloud container clusters resize anizai-cluster \
  --node-pool=main-pool --zone=us-central1-a --num-nodes=0 --quiet
# Bring back up:
gcloud container clusters resize anizai-cluster \
  --node-pool=main-pool --zone=us-central1-a --num-nodes=1 --quiet
```

Do **NOT** scale `polymarket-pool` to 0 — Polymarket prices are push-only WebSocket and cannot be backfilled.

Automatic schedule-based scaling is deferred to post-Sprint-C5 (manual only for V1).

```bash
bash infrastructure/gcp/04_create_cluster.sh
```

---

### `05_kubectl_config.sh`
**What:** Runs `gcloud container clusters get-credentials` to point local
`kubectl` at the cloud cluster, then prints the active context and node list.
**When:** Once after `04_create_cluster.sh`. Re-run on any new shell or new
machine that needs cluster access.
**Runtime:** ~30 seconds.

```bash
bash infrastructure/gcp/05_kubectl_config.sh
```

---

## After the scripts (manual steps in the same Bundle 6 sequence)

These are runtime command sequences (not committed scripts) — see
[`../../docs/cloud_deployment_implementation.md`](../../docs/cloud_deployment_implementation.md)
§C1.11–C1.14 for the exact commands.

1. **Apply the namespace** — `kubectl apply -f infrastructure/k8s/00_namespace.yaml`
2. **Enable Secret Manager CSI driver** — `gcloud container clusters update ... --update-addons=GcpSecretManagerCsiDriver=ENABLED`
3. **Create `pipeline-runtime` GSA** + `roles/secretmanager.secretAccessor` per-secret
4. **Create `pipeline-runtime` KSA** + Workload Identity binding
5. **Apply `wi-smoke-test.yaml`** + verify `Job/wi-smoke-test` reaches `Completed`

---

## Gate C1 verification

All eight checks must pass before Sprint C2 begins.

```bash
# 1. APIs enabled
gcloud services list --enabled --project anizai-pipeline \
  --filter="config.name:(compute.googleapis.com OR container.googleapis.com OR artifactregistry.googleapis.com OR secretmanager.googleapis.com OR logging.googleapis.com OR monitoring.googleapis.com OR iam.googleapis.com)" \
  --format="value(config.name)"

# 2. Billing budgets armed
gcloud billing budgets list --billing-account="${BILLING_ACCOUNT_ID}"

# 3. Three images pushed
gcloud artifacts docker images list \
  us-central1-docker.pkg.dev/anizai-pipeline/anizai-images

# 4. 15 secrets in SM
gcloud secrets list --project anizai-pipeline

# 5. Cluster reachable
kubectl get nodes

# 6. Namespace exists
kubectl get namespace anizai

# 7. CSI driver pods Running
kubectl get pods -n kube-system -l k8s-app=secrets-store-csi-driver

# 8. WI smoke-test Completed
kubectl get job wi-smoke-test -n anizai
kubectl logs -n anizai job/wi-smoke-test
```

---

## Troubleshooting

See the `gcp-deployment` skill (`.claude/skills/gcp-deployment/SKILL.md`) §10
for the full Workload Identity troubleshooting matrix and §11 for stop-and-ask
conditions.

Common Sprint C1 cold-start failures:

| Symptom | Fix |
|---|---|
| `04_create_cluster.sh` exits with `BILLING_DISABLED` | Billing not linked to project. Re-do Pre-Sprint Checklist step 3, then re-run. |
| `docker push` returns `denied: Permission` | Re-run `gcloud auth configure-docker us-central1-docker.pkg.dev` in the same shell. |
| WI smoke-test pod stuck in `Pending` with `MountVolume.SetUp failed` | Secret Manager CSI driver not Ready yet — wait 30-60s after addon enable, then `kubectl delete job wi-smoke-test` and re-apply. |
| WI smoke-test pod logs `Could not automatically determine credentials` | KSA→GSA binding hasn't propagated. Wait 60s, then `kubectl delete job wi-smoke-test && kubectl apply -f infrastructure/k8s/wi-smoke-test.yaml`. |
