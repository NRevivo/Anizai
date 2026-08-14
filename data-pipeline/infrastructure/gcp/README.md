# Anizai — GCP Setup Scripts (Phase C)

This directory holds every `gcloud` / billing setup script needed to stand up the
Phase C cloud foundation. Pair it with the Kubernetes manifests in
`../k8s/` and the per-sprint task tables in
[`../../docs/old_docs/cloud_deployment_implementation.md`](../../docs/old_docs/cloud_deployment_implementation.md).

All scripts are **idempotent** — re-running a script when the resource already
exists is a no-op (or, where applicable, adds a new version).

---

## Reference Card

| Constant | Value |
|---|---|
| GCP project (cluster + pipeline) | `anizai-pipehub` |
| GCP project (Firestore, cross-project) | `anizai-ai` |
| GKE cluster | `anizai-cluster` |
| Cluster zone | `us-central1-a` |
| Cluster pool (the only one) | `main-pool`: `e2-standard-8` × 1 (manually scaled to 0 between collection windows) |
| Artifact Registry | `us-central1-docker.pkg.dev/anizai-pipehub/anizai-images` |
| Namespace | `anizai` |
| Pipeline GSA (C1) | `pipeline-runtime@anizai-pipehub.iam.gserviceaccount.com` |
| Pipeline KSA (C1) | `pipeline-runtime` (in ns `anizai`) |
| Agent GSA (C5) | `agent-worker@anizai-pipehub.iam.gserviceaccount.com` |
| Agent KSA (C5) | **`agent-worker-ksa`** (in ns `anizai`) — note the asymmetry: the KSA has the `-ksa` suffix, the GSA does not. `agent-deployment.yaml` declares `serviceAccountName: agent-worker-ksa`; a KSA named `agent-worker` leaves the agent pod unschedulable |
| Billing alerts (D4) | `₪200` warning (~$54) + `₪400` critical (~$108), 50/90/100% triggers — ILS because billing account currency is ILS |

---

## Pre-Sprint Checklist (one-time, manual)

Before running anything in this directory:

1. **`gcloud auth login`** — authenticate as a project Owner of `anizai-pipehub`.
2. **`gcloud config set project anizai-pipehub`** — activate the project.
3. **Link a billing account** to `anizai-pipehub` with a payment method.
4. **`gcloud components install gke-gcloud-auth-plugin`** — required for `kubectl` against GKE.
5. Have your **billing account ID** ready: `gcloud billing accounts list`.
6. Make sure **Docker is running** locally (image builds run from `data-pipeline/`).

---

## Scripts (run in order)

### `00_enable_apis.sh`
**What:** Enables 7 GCP APIs (`compute`, `container`, `artifactregistry`, `secretmanager`, `logging`, `monitoring`, `iam`) on `anizai-pipehub`.
**When:** Once at the start of Phase C. Re-run safely — idempotent.
**Runtime:** ~2 minutes.

```bash
bash infrastructure/gcp/00_enable_apis.sh
```

---

### `01_billing_alerts.sh`
**What:** Creates two budgets on the linked billing account: `₪200` warning (~$54) and `₪400` critical (~$108), each with 50% / 90% / 100% threshold notifications. Default IAM recipients of the billing account receive emails (Design Decision D4). Currency follows the billing account currency — ILS for `010C82-6CA2C4-183381`. gcloud rejects budgets denominated in any other currency on this account.
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
**What:** Reads `data-pipeline/infrastructure/.env` and creates up to 17 Secret
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

The 17 keys (intentional allowlist — non-secret config like `POSTGRES_USER` or
`OPENAI_MODEL_NAME` is **not** migrated):

```
POSTGRES_PASSWORD, AIRFLOW_POSTGRES_PASSWORD, AIRFLOW_FERNET_KEY,
AIRFLOW_ADMIN_PASSWORD, GRAFANA_ADMIN_PASSWORD, GMAIL_APP_PASSWORD,
OPENAI_API_KEY, NEWSAI_API_KEY, THE_NEWS_API_KEY, FRED_API_KEY,
OPENWEATHER_API_KEY, OPENSKY_CLIENT_ID, OPENSKY_CLIENT_SECRET,
POLYMARKET_API_KEY, POLYMARKET_API_SECRET,
TELEGRAM_API_ID, TELEGRAM_API_HASH
```

**17 allowlisted, 15 actually created, 15 mounted by a SecretProviderClass** — the
three numbers are different and all three are correct:

- Three of the 17 report `absent or empty` and are **expected** to:
  `THE_NEWS_API_KEY` (the KG-C-5 rename target; `.env` carries `NEWSAI_API_KEY`
  today and both are listed so the rename lands in one pass), plus
  `POLYMARKET_API_KEY` / `POLYMARKET_API_SECRET` (Polymarket uses public endpoints
  only — both intentionally empty). Do not chase these warnings.
- `TELEGRAM_SESSION_FILE` is **not** in the allowlist because it is binary and
  cannot live in `.env`. It is uploaded separately with
  `gcloud secrets create TELEGRAM_SESSION_FILE --data-file=<session>`.

So a clean run creates 14 from `.env`, plus the session file uploaded by hand = the
15 secrets the cluster mounts.

---

### `04_create_cluster.sh`
**What:** Provisions the GKE Standard cluster `anizai-cluster` in `us-central1-a` with **one node pool** (Design Decision D2, revised twice):

- **`main-pool`** — `e2-standard-8` × 1. Every workload. Manually scale to 0 between data-collection windows to drop compute spend to ~$0 (cluster-management fee aside).

**The second pool is gone — do not re-add it.** Phase C originally created `polymarket-pool` (`e2-micro` × 1, always-on) so Polymarket's WebSocket would survive `main-pool` scaling to 0. It was deleted on purpose in Phase 9.5-A/F0: Kafka only exists while `main-pool` is up, so at zero nodes the pod had a live WebSocket and nowhere to write, and it crash-looped on `NoBrokersAvailable` ~14 h/day undetected. Polymarket now schedules on `main-pool`; `producers/polymarket-deployment.yaml` carries **no** `nodeSelector`.

Workload Identity pool enabled; GCE persistent-disk CSI driver enabled. Cluster create uses GKE-default `default-pool` first, then swaps it for `main-pool` (SDK 549's `clusters create` doesn't expose a flag to name the initial pool). Idempotent.

**When:** Once at Sprint C1.
**Runtime:** ~7-10 minutes (cluster + 2 pool ops).
**Cost:** `main-pool` ≈ `$200`/month at on-demand 24/7, `$0` at zero nodes. Pause `main-pool` between collection windows:

```bash
gcloud container clusters resize anizai-cluster \
  --node-pool=main-pool --zone=us-central1-a --num-nodes=0 --quiet
# Bring back up:
gcloud container clusters resize anizai-cluster \
  --node-pool=main-pool --zone=us-central1-a --num-nodes=1 --quiet
```

Automatic schedule-based scaling exists (`08_cloud_scheduler.sh`) but both jobs are **PAUSED** — Ron resizes manually.

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
[`../../docs/old_docs/cloud_deployment_implementation.md`](../../docs/old_docs/cloud_deployment_implementation.md)
§C1.11–C1.14 for the exact commands.

1. **Apply the namespace** — `kubectl apply -f infrastructure/k8s/00_namespace.yaml`
2. **Enable the Secret Manager add-on** — `gcloud container clusters update anizai-cluster --zone=us-central1-a --project=anizai-pipehub --enable-secret-manager`

   > The old text here read `--update-addons=GcpSecretManagerCsiDriver=ENABLED`. **That is not
   > valid gcloud** — `--update-addons` has no Secret Manager value; the add-on has its own
   > flag. This is the **GKE-native** add-on (`provider: gke`, driver
   > `secrets-store-gke.csi.k8s.io`), not the upstream community CSI driver. Verify on the
   > cluster object, not by guessing a pod label:
   > `gcloud container clusters describe anizai-cluster --zone=us-central1-a --project=anizai-pipehub | grep secretManagerConfig -A 4`
   > then `kubectl get csidriver secrets-store-gke.csi.k8s.io`.
3. **Create `pipeline-runtime` GSA** + `roles/secretmanager.secretAccessor` per-secret
4. **Create `pipeline-runtime` KSA** + Workload Identity binding
5. **Apply `wi-smoke-test.yaml`** + verify `Job/wi-smoke-test` reaches `Completed`

---

## Gate C1 verification

All eight checks must pass before Sprint C2 begins.

```bash
# 1. APIs enabled
gcloud services list --enabled --project anizai-pipehub \
  --filter="config.name:(compute.googleapis.com OR container.googleapis.com OR artifactregistry.googleapis.com OR secretmanager.googleapis.com OR logging.googleapis.com OR monitoring.googleapis.com OR iam.googleapis.com)" \
  --format="value(config.name)"

# 2. Billing budgets armed
gcloud billing budgets list --billing-account="${BILLING_ACCOUNT_ID}"

# 3. Images pushed — nine tags (six current + three rollback), not the
#    Sprint-C1-era three. See the migration plan's S2 push table.
gcloud artifacts docker images list \
  us-central1-docker.pkg.dev/anizai-pipehub/anizai-images --include-tags

# 4. 15 secrets in SM
gcloud secrets list --project anizai-pipehub

# 5. Cluster reachable
kubectl get nodes

# 6. Namespace exists
kubectl get namespace anizai

# 7. Secret Manager add-on registered
#    NOT `-l k8s-app=secrets-store-csi-driver` — that label belongs to the
#    UPSTREAM community driver, which is not installed on this cluster and
#    never should be. Its emptiness is not a failure. Check the add-on:
gcloud container clusters describe anizai-cluster \
  --zone=us-central1-a --project=anizai-pipehub | grep secretManagerConfig -A 4
kubectl get csidriver secrets-store-gke.csi.k8s.io

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
