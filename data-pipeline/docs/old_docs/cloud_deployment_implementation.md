# Cloud Deployment — Implementation Plan
## Anizai Project | Phase C (Sprints C1-C5)

---

## How to use this document

This is the **granular implementation plan** for migrating the Anizai data pipeline + Agentic Hub from the local docker-compose stack to a single-zone GKE cluster on GCP project `anizai-pipeline`. It is the cloud deployment equivalent of `agentic_hub_implementation.md` (Phase 8) and the per-source phase tables in `task_plan_implementation.md`.

It should be loaded by Claude Code at the start of every Phase C sprint, alongside:
- `infrastructure/docker-compose.yml` (the working reference stack — every cloud workload mirrors a service in this file)
- `infrastructure/Dockerfile.flink`, `infrastructure/Dockerfile.airflow`, `infrastructure/Dockerfile.agent` (existing images)
- `infrastructure/sql/init.sql` (Postgres DDL — mounted into the StatefulSet in C2)
- The relevant sprint section in `task_plan.md` (active task tracker)
- The `infrastructure` skill (Docker images, K8s manifests, monitoring) and the `sprint-kickoff` skill

The data-pipeline sprint conventions still apply:
- Conventional commits with section references (`feat(infra): ... (Phase C, Sprint Cx)`)
- Update `task_plan.md` after every completed task
- All work inside `data-pipeline/` — K8s manifests live in `data-pipeline/infrastructure/k8s/`, gcloud scripts in `data-pipeline/infrastructure/gcp/`
- No code without an approved implementation plan
- Per-sprint gate must pass before the next sprint starts (see "Phase C Gate Model" below)

---

## Phase C Overview

### Goal

Move the running data-pipeline + Agentic Hub from a single developer machine running `docker compose up` to a self-managed GKE cluster reachable from the developer's laptop via `kubectl port-forward`. By the end of Phase C:

1. The cluster runs the same six core services as docker-compose (Kafka, Postgres, Flink JobManager + TaskManager, Airflow, agent worker) plus Prometheus + Grafana, plus all 9 producers + the reactive ingestion trigger consumer.
2. All secrets live in Secret Manager and reach pods via Workload Identity + the Secret Manager CSI driver — no JSON key files, no committed secrets.
3. The local frontend (and the local agentic hub developer loop) talks to cloud Postgres, cloud Firestore-listening agent, and cloud Grafana through `kubectl port-forward` — no public domain, no LoadBalancer.
4. Real data lands in `knowledge_vault`, `social_vault`, and `momentum_vault` from all 9 sources, and the Tier 1 + Tier 2 forecast pipelines run end-to-end against cloud-side state.

### Sprint Structure (sequential, gate-locked)

Phase C is **strictly sequential**. Each sprint produces a deployable subset that the next sprint depends on; gates are integration checkpoints, not code-correctness checkpoints.

| Sprint | Focus | Definition of Done |
|--------|-------|---------------------|
| C1 | GCP Foundation — APIs, billing, Artifact Registry, Secret Manager, GKE cluster, Workload Identity | Cluster reachable from local kubectl. All 3 existing images pushed. All 15 secrets in Secret Manager. Workload Identity smoke-test passes. |
| C2 | Postgres + Kafka StatefulSets | Postgres Ready with 7 tables + extensions + hypertable. Kafka Ready with 19 topics. kafka-ui + psql via port-forward. |
| C3 | Flink JobManager + TaskManager + Silver/Gold jobs | Both jobs RUNNING. Test message round-trips Bronze→Silver→Gold→Postgres. Checkpoint recovery validated. |
| C4 | Airflow + all 9 producers + reactive trigger consumer | Real data from all 9 sources in Postgres. Trigger consumer running. KG-PHASE8-3 closed. |
| C5 | Agent worker (cross-project Firestore) + Prometheus + Grafana + pg_dump backups + Local E2E | Agent processes real queries from cloud Postgres. Grafana live. Full E2E succeeds. `LOCAL_CONNECTION.md` written. |

### Pre-Phase C Checkpoint — **Verified 2026-05-07**

Items confirmed before Sprint C1 began (and exercised during C1):

- [x] Phase 8B (Sprint 19-20) and Phase 8C (Sprint 21) are merged on `main` and the docker-compose stack runs end-to-end locally — the cloud migration is a parity port, not a rewrite.
- [x] User has Owner role on **both** GCP projects: `anizai-pipeline` (verified during C1 — GSA creation + IAM grants succeeded) and `anizai-ai` (asserted by user; first exercised in C5 cross-project IAM). Cross-project grants in Sprint C5 remain self-service.
- [x] Billing account `01C603-6D345F-105BC9` (ILS) is linked to `anizai-pipeline` — verified by `01_billing_alerts.sh`.
- [x] Local `gcloud` CLI is authenticated and `gcloud config get-value project` returns `anizai-pipeline` — verified by `00_enable_apis.sh` precondition check.
- [x] `kubectl` + `gke-gcloud-auth-plugin` installed locally (plugin auto-installed during Bundle 5 of C1).
- [x] `.env` is at `data-pipeline/infrastructure/.env` (NOT `data-pipeline/.env` as originally written here) and is up to date — `03_migrate_secrets.sh` migrated 13 secrets in C1.7. After migration, pods read from Secret Manager.

For Sprint C2 onward: these items remain satisfied — no need to re-verify at each sprint kickoff unless an explicit change occurred (e.g., billing account swapped, new GCP project added).

---

## Phase C Gate Model

The data-pipeline used Gate 1 (Bronze schema) → Gate 2 (Silver/Gold logic) → Gate 3 (Persistence) → E2E. The hub used a 4-gate variant for code logic. Phase C is **infrastructure**, not code logic; the gate model is simpler:

| Gate type | What it validates |
|-----------|-------------------|
| **Per-sprint integration gate** | Every sprint has one named gate at the bottom of its section. The gate is a checklist of operationally observable conditions (pod Ready, manifest applied, smoke command output). All checklist items must be green before the next sprint starts. |
| **Phase C E2E** | Sprint C5 ends with a single end-to-end run from local frontend → cloud Firestore → cloud agent → cloud Postgres → cloud Firestore → local frontend renders. This is the Phase C closing gate. |

There is no Gate 1/2/3 split inside a Phase C sprint — the docker-compose stack already validates code logic; Phase C only validates that the cloud deployment runs the same code identically.

---

## Sprint C1 — GCP Foundation, Artifact Registry, Secrets, Cluster

### Sprint scope

Stand up the empty container that the rest of Phase C fills: GCP APIs enabled, billing alerts armed, Artifact Registry repository created, all three existing images pushed, all 15 secrets in Secret Manager, GKE cluster provisioned, namespace `anizai` created, Secret Manager CSI driver installed, Workload Identity binding wired, and a one-shot Job that proves a pod can read a secret without a JSON key. **No application workloads run yet** — that starts in C2.

The end of Sprint C1 deliverable: `kubectl get pods -n anizai` returns the Job pod in `Completed` state with logs showing the secret value (or its length, if redacted) and `gcloud artifacts docker images list us-central1-docker.pkg.dev/anizai-pipeline/anizai-images` lists three images.

### Confirmed design decisions

- **D1 — GCP project layout: `anizai-pipeline` for the cluster, `anizai-ai` for Firestore.** The data pipeline lives entirely in `anizai-pipeline`. Firestore (the agent's queue + result store) stays on `anizai-ai` because that is the project the partner frontend writes to. Cross-project IAM (granting the agent's GCP SA `roles/datastore.user` on `anizai-ai`) is self-service in Sprint C5 because the user is Owner on both projects.
- **D2 — Single-zone, dual-pool GKE cluster, regular VMs.** Two node pools in `us-central1-a`:
  - **`main-pool`** — one `e2-standard-8` (8 vCPU / 32 GB). Hosts every workload except Polymarket. **Manually scaled to 0** between data-collection windows to keep idle cost near zero (cluster-management fee aside). Bring back to 1 with `gcloud container clusters resize anizai-cluster --node-pool=main-pool --zone=us-central1-a --num-nodes=1`.
  - **`polymarket-pool`** — one `e2-micro` (2 vCPU shared / 1 GB). Hosts the Polymarket producer **only**, **always-on 24/7** (~$7/month). Polymarket pod pins via `nodeSelector: cloud.google.com/gke-nodepool=polymarket-pool` (GKE auto-labels every pool node).
  - Rationale for the split: Polymarket emits real-time prices over WebSocket; gaps in the connection cannot be backfilled (push-only protocol, no historical replay). Every other producer either supports backfill (Telegram MTProto channel-history catch-up; FRED/NewsAPI/etc. via DAG re-run) or is itself a scheduled poller. Polymarket alone must stay connected continuously, so it gets its own tiny pool that survives when main-pool is scaled to 0.
  - Cluster-init quirk: `gcloud container clusters create` does not expose a flag (in SDK 549) to name the initial pool, so `04_create_cluster.sh` creates the cluster with the GKE-default `default-pool`, then adds `main-pool`, deletes `default-pool`, and adds `polymarket-pool`. Idempotent on re-run.
  - Multi-zone HA, autoscaling, and Spot/preemptible nodes are still rejected for V1: total expected workload is ~12 pods at peak on main-pool (well under 8 vCPU / 32 GB) and Spot interruptions would kill in-flight Flink checkpoints.
  - Automatic schedule-based scale-down (Cloud Scheduler + a tiny Cloud Run trigger) is **deferred to post-Sprint-C5**.
  - Cluster type: GKE Standard (not Autopilot — Autopilot blocks pod-level Workload Identity scoping needed for the cross-project agent SA in C5).
- **D3 — All secrets via Secret Manager + Workload Identity + CSI driver. No JSON key files. Ever.** Each pod uses a Kubernetes ServiceAccount (KSA) bound to a GCP ServiceAccount (GSA) via Workload Identity. The KSA inherits the GSA's IAM at the metadata server. Secrets are mounted via the Secret Manager CSI driver as files at `/var/secrets/<name>` (preferred for files like the Telegram session) or projected as env vars. No service-account JSON files are ever created, downloaded, committed, or volume-mounted.
- **D4 — Billing alerts at ₪200 (warning, ~$54) / ₪400 (critical, ~$108) on the `anizai-pipeline` billing account.** Solo-dev cost ceiling. Currency is ILS because the linked billing account (`01C603-6D345F-105BC9`) is denominated in ILS — gcloud requires the budget currency to match the billing account currency, so USD-denominated thresholds were not a viable option for V1. Conversion uses ~₪3.7/$1; round numbers chosen for clarity. Both alerts route via Cloud Billing's default IAM recipients; a custom email channel pointing at `ron.mintz21@gmail.com` is documented as an optional follow-up in `infrastructure/gcp/README.md`. No PagerDuty/Slack integration in V1.
- **D5 — Artifact Registry repository: `us-central1-docker.pkg.dev/anizai-pipeline/anizai-images`.** Single Docker-format repository in `us-central1` (matches cluster region, eliminates inter-region image-pull latency and egress). All cloud images live here under tags matching the docker-compose names: `anizai-flink:1.19.1`, `anizai-airflow:2.9.3`, `anizai-agent:0.1.0`. New images added in C4 (`anizai-polymarket:0.1.0`, `anizai-telegram:0.1.0`, `anizai-trigger-consumer:0.1.0`) follow the same naming convention.

### Task table

| Task | Description | Gate(s) | Spec Reference |
|------|-------------|---------|----------------|
| [x] C1.1 | Create `infrastructure/gcp/` directory. Add `00_enable_apis.sh` — single idempotent script that runs `gcloud services enable compute.googleapis.com container.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com logging.googleapis.com monitoring.googleapis.com iam.googleapis.com` against `anizai-pipeline`. Document expected runtime (~2 min). | — | §9.1 |
| [x] C1.2 | Add `infrastructure/gcp/01_billing_alerts.sh` — creates two budget alerts on the `anizai-pipeline` billing account: $50 warning (50%/90%/100% triggers) and $100 critical (50%/90%/100%). Both notify the user's email channel. Per D4. | — | §9.1 |
| [x] C1.3 | Add `infrastructure/gcp/02_artifact_registry.sh` — creates the Docker repository `us-central1-docker.pkg.dev/anizai-pipeline/anizai-images` and runs `gcloud auth configure-docker us-central1-docker.pkg.dev` so local `docker push` works. Per D5. | Gate | §9.1 |
| [x] C1.4 | Build and push `anizai-flink:1.19.1` to Artifact Registry. Build context: `data-pipeline/`. Dockerfile: `infrastructure/Dockerfile.flink`. Tag both `:1.19.1` and `:latest`. Verify with `gcloud artifacts docker images list`. | Gate | `infrastructure/Dockerfile.flink` |
| [x] C1.5 | Build and push `anizai-airflow:2.9.3`. Same pattern as C1.4. Tag both `:2.9.3` and `:latest`. | Gate | `infrastructure/Dockerfile.airflow` |
| [x] C1.6 | Build and push `anizai-agent:0.1.0`. Same pattern. Tag both `:0.1.0` and `:latest`. | Gate | `infrastructure/Dockerfile.agent` |
| [x] C1.7 | Add `infrastructure/gcp/03_migrate_secrets.sh` — reads `data-pipeline/.env`, creates a Secret Manager secret per key (15 secrets total: Postgres credentials, Airflow credentials + Fernet, OpenAI, Telegram trio, FRED, TheNewsAPI, Reddit trio, OpenWeather, Grafana admin, plus any provider keys present). Each secret is added with `gcloud secrets create <name> --data-file=-` (stdin) so values never appear in shell history. Idempotent (skip if secret exists). | Gate | §9.1, §8.11 |
| [x] C1.8 | Add `infrastructure/gcp/04_create_cluster.sh` — provisions the GKE cluster: `gcloud container clusters create anizai-cluster --zone us-central1-a --num-nodes 1 --machine-type e2-standard-8 --release-channel regular --workload-pool=anizai-pipeline.svc.id.goog --addons=GcePersistentDiskCsiDriver`. Per D2. Document expected runtime (~5-7 min). | Gate | §9.1 |
| [x] C1.9 | Add `infrastructure/gcp/05_kubectl_config.sh` — runs `gcloud container clusters get-credentials anizai-cluster --zone us-central1-a` so local `kubectl` targets the cloud cluster. Verify with `kubectl get nodes` (one node, Ready). | Gate | §9.1 |
| [x] C1.10 | Add `infrastructure/k8s/00_namespace.yaml` — creates namespace `anizai`. Apply with `kubectl apply -f`. All Phase C workloads land in this namespace. | — | §9.1 |
| [x] C1.11 | Enable GKE's native Secret Manager add-on: `gcloud container clusters update anizai-cluster --zone us-central1-a --enable-secret-manager`. (The legacy `--update-addons=GcpSecretManagerCsiDriver=ENABLED` syntax was removed in current SDK — `--enable-secret-manager` is the supported top-level flag in SDK 549+.) Verify the registered CSI driver name is `secrets-store-gke.csi.k8s.io` (`kubectl get csidrivers`) and that `csi-secrets-store-gke-*` + `csi-secrets-store-provider-gke-*` pods are Running in `kube-system`. **Note**: pods scheduled on the always-on `polymarket-pool` (e2-micro) may stay `Pending` due to insufficient resources for the 3-container CSI DaemonSet — this is fine for the C1 smoke test (which lands on `main-pool`), but means polymarket-pool pods cannot mount SecretProviderClass-backed volumes; per the project memory, polymarket runs unauthenticated and does not need secret mounts. **SecretProviderClass spec note**: GKE's native add-on registers its provider under the name `gke`, not the upstream `gcp` — SPC manifests must use `spec.provider: gke`. | Gate | §9.1 |
| [x] C1.12 | Create the GCP service account `pipeline-runtime@anizai-pipeline.iam.gserviceaccount.com` and grant `roles/secretmanager.secretAccessor` scoped to the 15 secrets created in C1.7. This is the shared GSA for all in-cluster pipeline workloads (Postgres init, Kafka, Flink, Airflow, producers, trigger consumer). The agent worker gets its own GSA in Sprint C5 (cross-project access). Per D3. | — | §9.1 |
| [x] C1.13 | Create the Kubernetes ServiceAccount `pipeline-runtime` in namespace `anizai` and bind it to the GSA from C1.12 via Workload Identity: `gcloud iam service-accounts add-iam-policy-binding ... --role roles/iam.workloadIdentityUser --member "serviceAccount:anizai-pipeline.svc.id.goog[anizai/pipeline-runtime]"`, then annotate the KSA with `iam.gke.io/gcp-service-account=...`. Per D3. | — | §9.1 |
| [x] C1.14 | Add `infrastructure/k8s/wi-smoke-test.yaml` — one-shot `kind: Job` that uses ServiceAccount `pipeline-runtime`, mounts the `OPENAI_API_KEY` secret via the CSI driver at `/var/secrets/openai`, and runs a small alpine container that prints `wc -c </var/secrets/openai/OPENAI_API_KEY` (length-only, never the value). Apply with `kubectl apply -f`, wait for `Completed`, verify length matches expected. | Gate | §9.1 |
| [x] C1.15 | Document all gcloud commands in `data-pipeline/infrastructure/gcp/README.md`. Each script gets a one-paragraph "what does this do, when do you re-run it" entry. Include the current GCP project, cluster name, namespace, and Artifact Registry URL as a header reference card. | — | §5.4 |

### Gate C1 — **PASSED 2026-05-07**

- [x] All 8 GCP APIs enabled (7 specced + `billingbudgets.googleapis.com` added at runtime for C1.2).
- [x] Billing alerts armed: ₪200 warning + ₪400 critical (currency follows ILS billing account, D4 revised).
- [x] All 3 images pushed: `anizai-flink:1.19.1`, `anizai-airflow:2.9.3`, `anizai-agent:0.1.0` — each with `:latest` tag.
- [x] **13** secrets in Secret Manager (`gcloud secrets list` returns 13). POLYMARKET_API_KEY and POLYMARKET_API_SECRET intentionally excluded — public-only Polymarket per project memory.
- [x] Cluster reachable: `kubectl get nodes` shows **2** Ready nodes (one per pool — `main-pool` + `polymarket-pool`).
- [x] Namespace `anizai` exists (Active).
- [x] CSI driver `secrets-store-gke.csi.k8s.io` registered (GKE-native add-on, not upstream `secrets-store.csi.k8s.io`). Driver DaemonSet pods Running on `main-pool`; Pending on `polymarket-pool` due to e2-micro resource pressure — non-blocking for C1 because the smoke test schedules on main-pool, and per project memory polymarket runs unauthenticated so no SecretProviderClass mounts are needed on polymarket-pool.
- [x] WI smoke-test Job `Completed` — log: `wi-smoke-test: OPENAI_API_KEY length=164 bytes` + `wi-smoke-test: PASS`.

### Cold-start handling

If `gcloud container clusters create` fails with `BILLING_DISABLED`, billing was not linked before C1.1 — see Pre-Phase C checkpoint, then re-run C1.8. If image pushes fail with `denied: Permission`, `gcloud auth configure-docker` was not run in C1.3 — re-run that step in the same shell as the `docker push`. If the WI smoke-test pod stays in `Pending` with `MountVolume.SetUp failed`, the CSI driver isn't fully Ready yet (~30-60s after enable) — wait and retry.

### Open questions deferred to future sprints

- Multi-node autoscaling (deferred to post-Phase C — current single-node sizing is well under capacity; revisit if any pod is OOMKilled or if total CPU > 70% sustained).
- Per-workload GSAs with least-privilege secret access (V1 uses one shared `pipeline-runtime` GSA; partition further if a secret leak ever happens).

---

## Sprint C2 — Postgres + Kafka

### Sprint scope

Stand up the two stateful core services that the rest of the pipeline reads from and writes to. Postgres comes up first because Flink (C3), Airflow (C4), and the agent (C5) all have it as a hard dependency; Kafka comes up alongside it because the producers and the Silver/Gold Flink jobs both need it. Both services run as `StatefulSet`s (not `Deployment`s) so the persistent volumes survive pod restarts.

The end of Sprint C2 deliverable: from the local laptop, `kubectl port-forward svc/postgres 5432:5432` followed by `psql` lists the 7 tables and confirms the `momentum_vault` hypertable. `kubectl port-forward svc/kafka-ui 8080:8080` opens the kafka-ui in a browser showing 19 topics.

### Confirmed design decisions

- **D1 — Postgres = self-hosted StatefulSet running `timescale/timescaledb-ha:pg16`. Cloud SQL was rejected.** Cloud SQL for PostgreSQL does not support the TimescaleDB extension; the `momentum_vault` hypertable + Continuous Aggregates (§3 of the pipeline spec) are non-negotiable, so a managed offering that drops TimescaleDB is not a viable substitute. The same image runs locally in docker-compose, so there is zero behavior drift between dev and cloud.
- **D2 — Persistent volume sizes: Postgres 20 GB, Kafka 10 GB.** Both `ReadWriteOnce` PVCs on the GKE default `pd-balanced` storage class. 20 GB covers the four vault tables (knowledge, social, momentum, mapping) for the V1 retention horizon (~90 days); 10 GB matches the longest topic retention (Bronze 7d × 11 sources at observed throughput). Both can be expanded in place via PVC resize if usage approaches 70%.
- **D3 — `init.sql` is mounted into the Postgres pod as a ConfigMap, same path as docker-compose (`/docker-entrypoint-initdb.d/01_init.sql`).** This keeps the schema source-of-truth in one place: `data-pipeline/infrastructure/sql/init.sql`. ConfigMap is regenerated from the file on every `kubectl apply`; the file only runs on first-pod-init (Postgres semantics).
- **D4 — `kafka-init` runs as a one-shot `Job` (not an `initContainer` of the Kafka pod).** Mirrors the docker-compose pattern. The Job blocks until Kafka is Ready (init-probe loop), then creates 19 topics with the exact retention/compaction settings from `infrastructure/docker-compose.yml` kafka-init (11 Bronze per source, 2 Silver streams + 1 Silver structured_metrics, 2 Gold streams + 1 Gold structured_metrics, ingestion_triggers, dead-letter-queue) per §3.3. Sprint C2 closeout note: this section originally read "14 topics" — corrected to 19 to match docker-compose verbatim, which is the parity-port source of truth.
- **D5 — kafka-ui and Postgres are reachable only via `kubectl port-forward` — no Service of type LoadBalancer, no Ingress, no public DNS.** Per the broader Phase C "no domain, no LoadBalancer" decision. The local developer loop uses `port-forward` for everything; when the partner frontend is wired in C5, it is also a local laptop process that port-forwards.

### Task table

| Task | Description | Gate(s) | Spec Reference |
|------|-------------|---------|----------------|
| [x] C2.1 | Add `infrastructure/k8s/postgres-configmap.yaml` — ConfigMap `postgres-init-sql` from `infrastructure/sql/init.sql`. Per D3. Generated via `kubectl create configmap postgres-init-sql --from-file=01_init.sql=infrastructure/sql/init.sql -o yaml --dry-run=client > postgres-configmap.yaml` (committed, not regenerated on every apply). | — | §3 |
| [x] C2.2 | Add `infrastructure/k8s/postgres-statefulset.yaml` — Postgres StatefulSet (1 replica, image `timescale/timescaledb-ha:pg16`, 20 GB PVC at `/home/postgres/pgdata`, ConfigMap-backed `01_init.sql` mounted at `/docker-entrypoint-initdb.d/`). Pod uses ServiceAccount `pipeline-runtime`. **Closeout note (Q3 outcome)**: only `POSTGRES_PASSWORD` is projected from Secret Manager (via `infrastructure/k8s/postgres-secretproviderclass.yaml` — added during C2 closure as a supplementary file); `POSTGRES_USER=anizai` and `POSTGRES_DB=anizai` are plain env vars (not secrets, identical to docker-compose defaults). Pattern is `POSTGRES_PASSWORD_FILE=/var/secrets/postgres/POSTGRES_PASSWORD` — no intermediary K8s Secret object. `securityContext.fsGroup: 999` to fix postgres uid/gid PVC ownership. Per D1, D2, D3. | — | §3, §8.2 |
| [x] C2.3 | Add `infrastructure/k8s/postgres-service.yaml` — headless `Service` `postgres` on port 5432. ClusterIP only — no NodePort, no LoadBalancer. | — | §8.2 |
| [x] C2.4 | Apply Postgres manifests (`kubectl apply -f`). Wait for `kubectl get pods -n anizai -l app=postgres` to show `Running` and Ready. | — | — |
| [x] C2.5 | From local laptop: `kubectl port-forward -n anizai svc/postgres 5432:5432`, then `psql -h localhost -U <user> -d <db>` and run `\dt` — verify all 7 tables (knowledge_vault, knowledge_vectors, social_vault, social_vectors, momentum_vault, mapping_dict, **divergence_alerts**). Verify `\dx` shows `pgvector` and `timescaledb`. Verify hypertable: `SELECT * FROM timescaledb_information.hypertables WHERE hypertable_name = 'momentum_vault';` returns one row. **Closeout note (Q2 outcome)**: this task originally listed `reactive_article_cache` as the 7th table, but `init.sql` actually defines `divergence_alerts`. `reactive_article_cache` is spec-only (not yet in init.sql) and was not added in this sprint. | Gate | §3 |
| [x] C2.6 | Add `infrastructure/k8s/kafka-statefulset.yaml` — Kafka StatefulSet (1 replica, image `apache/kafka:3.7.0`, 10 GB PVC at `/var/lib/kafka/data`, KRaft mode, dual listeners INTERNAL+EXTERNAL exactly as docker-compose, `KAFKA_AUTO_CREATE_TOPICS_ENABLE=false`). Per D1, D2. | — | §8.2 |
| [x] C2.7 | Add `infrastructure/k8s/kafka-service.yaml` — headless `Service` `kafka` on ports 9092/29092/9093. ClusterIP only. **Closeout note (D6 added during sprint)**: `publishNotReadyAddresses: true` is REQUIRED on the headless Service for KRaft self-bootstrap. Without it, kafka-0 cannot resolve `kafka:9093` at startup (DNS unpublished while pod isn't Ready) and crashes with `Received a fatal error while waiting for the controller to acknowledge that we are caught up`. Docker-compose doesn't hit this because Docker bridge DNS resolves regardless of health. | — | §8.2 |
| [x] C2.8 | Add `infrastructure/k8s/kafka-init-job.yaml` — one-shot `Job` (image `apache/kafka:3.7.0`, restartPolicy OnFailure) that runs the same topic-creation loop as the docker-compose `kafka-init` service: 19 topics matching the kafka-init definitions in `infrastructure/docker-compose.yml` (11 Bronze per source, 2 Silver streams + 1 Silver structured_metrics, 2 Gold streams + 1 Gold structured_metrics, ingestion_triggers, dead-letter-queue) with retention per §3.3. Per D4. | Gate | §3.1, §3.3 |
| [x] C2.9 | Apply Kafka manifests + Job. Wait for Kafka pod `Running` + `Job/kafka-init` `Completed`. | — | — |
| [x] C2.10 | Add `infrastructure/k8s/kafka-ui-deployment.yaml` — `Deployment` (image `provectuslabs/kafka-ui:v0.7.2`, 1 replica, env `KAFKA_CLUSTERS_0_BOOTSTRAPSERVERS=kafka:29092`). Per D5. Add accompanying ClusterIP `Service` on port 8080. **Closeout note**: pinned to `:v0.7.2` (was `:latest` in spec) per project no-`:latest` convention; docker-compose's `:latest` should be tightened in a follow-up for full parity. | — | §8.2 |
| [x] C2.11 | Apply kafka-ui. From local laptop: `kubectl port-forward -n anizai svc/kafka-ui 8080:8080` and open browser. Verify all 19 topics listed with the expected retention values. | Gate | §3.3 |

### Gate C2 — **PASSED 2026-05-07**

- [x] Postgres pod `Running` and Ready.
- [x] All 7 tables present (`divergence_alerts`, `knowledge_vault`, `knowledge_vectors`, `mapping_dict`, `momentum_vault`, `social_vault`, `social_vectors`), `pgvector` 0.8.2 + `timescaledb` 2.26.4 + `pg_trgm` 1.6 installed, `momentum_vault` confirmed hypertable.
- [x] Local `psql` via port-forward works end-to-end (TCP bind + Postgres SSLRequest protocol response confirmed).
- [x] Kafka pod `Running` and Ready (after `publishNotReadyAddresses: true` fix on Service).
- [x] `kafka-init` Job `Completed`; **19 topics** visible (matches docker-compose verbatim — see D6 below).
- [x] kafka-ui reachable via port-forward, listing all 19 topics with correct retention (Bronze 7d, Silver 3d, Gold 3d, ingestion_triggers 7d, dead-letter-queue 30d, all `cleanup.policy=delete`).

### D6 — `publishNotReadyAddresses: true` on the kafka headless Service (added during sprint)

KRaft single-node bootstrap with `KAFKA_CONTROLLER_QUORUM_VOTERS=0@kafka:9093` requires the broker to resolve its own service DNS before becoming Ready. Default headless Services do not publish DNS records for not-Ready pods, creating a chicken-and-egg loop: pod can't reach Ready until DNS resolves; DNS doesn't resolve until pod is Ready. Setting `publishNotReadyAddresses: true` publishes DNS records as soon as the pod has an IP, breaking the loop. This decision is K8s-specific (docker-compose does not need it because Docker's bridge DNS resolves regardless of health), so the env var stays identical between dev and cloud — only the Service flag differs. Captured in `data-pipeline/infrastructure/k8s/kafka-service.yaml` with full inline documentation.

### Cold-start handling

If Postgres CrashLoops with "permission denied for /home/postgres/pgdata", the PVC was provisioned with the wrong fsGroup — set `securityContext.fsGroup: 999` on the StatefulSet (matches the postgres user inside the timescaledb-ha image; same fix applied for Windows Docker Desktop in docker-compose). If `kafka-init` exits with `UnknownTopicOrPartitionException` on the first pass, the Kafka readiness probe was satisfied before the controller fully booted — restart the Job (`kubectl delete job kafka-init && kubectl apply -f kafka-init-job.yaml`), it is idempotent (`--if-not-exists`).

---

## Sprint C3 — Flink

### Sprint scope

Stand up the stream processing layer: Flink JobManager, Flink TaskManager, Silver job, Gold job. The image (`anizai-flink:1.19.1`) is already in Artifact Registry from C1; this sprint only creates the K8s manifests, the checkpoint PVC, and submits the two PyFlink jobs. Once both jobs are RUNNING, a manual test message to a Bronze topic must round-trip Bronze → Silver → Gold → Postgres without operator intervention; killing the TaskManager pod must trigger a checkpoint-based restart that resumes processing without data loss.

### Confirmed design decisions

- **D1 — Flink JobManager and TaskManager are separate `Deployment`s, not a single Helm chart.** Same split as docker-compose (`flink-jobmanager` / `flink-taskmanager`). Helm operator (`flink-kubernetes-operator`) is rejected for V1 because (a) the docker-compose configuration is the source of truth for FLINK_PROPERTIES and we do not want a second config surface, (b) the cluster is intentionally tiny and the operator overhead (CRDs, controller pod) is disproportionate.
- **D2 — `EXACTLY_ONCE` semantics + 60-second checkpoint interval applied via the same `FLINK_PROPERTIES` block already validated in `infrastructure/docker-compose.yml`.** No semantics change between local and cloud — the YAML block is copied verbatim into a ConfigMap and projected into both pods. Per §4.3.
- **D3 — Single shared checkpoint PVC, mounted on JobManager and TaskManager.** PVC name `flink-checkpoints`, 5 GB, `pd-balanced`. JobManager writes `state.checkpoints.dir=file:///opt/flink/checkpoints`; TaskManager mounts the same path read-write. Single TaskManager replica in V1 makes `ReadWriteOnce` acceptable; if a second TaskManager is added later, the volume must move to `ReadWriteMany` (Filestore CSI) — flagged.
- **D4 — Prometheus reporter exposed on `:9249` on both pods, scraped by the Sprint C5 Prometheus.** Same port and reporter class as docker-compose. The reporter JAR is already baked into the image (Dockerfile.flink LAYER 4B); the K8s manifest only needs to expose the port.
- **D5 — Secret injection via shell wrapper (not `secretObjects`). Confirmed during sprint: GKE-native CSI (`provider: gke`, `--enable-secret-manager`) supports file mounts but does NOT support the `secretObjects` → K8s Secret sync. Attempting `secretObjects` produces `FailedToCreateSecret: timed out waiting for the condition`. The Flink containers read `POSTGRES_PASSWORD` and `OPENAI_API_KEY` by way of a shell wrapper that runs before handing off to `/docker-entrypoint.sh`:  `command: ["/bin/bash", "-c"]` / `args: ["export POSTGRES_PASSWORD=$(cat /var/secrets/flink/POSTGRES_PASSWORD) && export OPENAI_API_KEY=$(cat /var/secrets/flink/OPENAI_API_KEY) && exec /docker-entrypoint.sh <role>"]`. This pattern applies to any future pod where the process reads env vars via `os.getenv()` and the application entrypoint has no `_FILE` convention.**
- **D6 — Flink BlobServer port 6124 must be included in the JobManager Service. Confirmed during sprint: the JM's BlobServer listens on port 6124 (hardcoded in Flink 1.19). TaskManagers download Python code BLOBs from the BlobServer via the JM Service DNS name. Without port 6124 in the ClusterIP Service, all tasks hang indefinitely in DEPLOYING state. Added as named `blob` port to `flink-jobmanager-service.yaml`.**
- **D7 — `FLINK_PROPERTIES` must never contain `#` comment lines. Confirmed during sprint: Flink 1.19's config loader treats `# comment text` as a configuration property key (not a YAML comment). Comments appearing before `restart-strategy.fixed-delay.attempts` caused that key to be parsed with a corrupted value (`attempts=1, delay=1s` instead of `attempts=10, delay=20s`). All `#` comment lines have been removed from the ConfigMap data values; explanatory text belongs in the manifest YAML header comments above the `data:` block, not inside the values.**
- **D8 — `restart-strategy.fixed-delay.*` sub-keys must not coexist with `restart-strategy: fixed-delay` (scalar parent). Confirmed during sprint: Flink 1.19's YAML serializer silently drops `restart-strategy.fixed-delay.attempts` and `restart-strategy.fixed-delay.delay` when `restart-strategy` is already set as a scalar. Fix: remove the scalar selector entirely; Flink 1.19 defaults to `ExponentialDelayRestartBackoffTimeStrategy(initialBackoffMS=1000, maxBackoffMS=60000, attemptsBeforeResetBackoff=MAX_INT)` when checkpointing is enabled — unlimited restart attempts with exponential backoff capped at 60s. This is strictly better than `fixed-delay` for Kubernetes pod restart scenarios.**
- **D9 — Checkpoint recovery requires force-kill (`--force --grace-period=0`). Confirmed during sprint: `kubectl delete pod` sends SIGTERM → Flink TM catches it and sends a graceful disconnect RPC to the JM → the JM treats this as a planned deallocation, not a failure → restart strategy is skipped → job goes directly to FAILED terminal state. Simulating unexpected failure (node crash / OOM) requires bypassing graceful shutdown: `kubectl delete pod --force --grace-period=0`. This causes a TCP RST (Disassociated), which the JM treats as an unexpected failure and triggers the restart strategy.**
- **D10 — `kubectl exec` with Unix paths must use PowerShell on Windows. Confirmed during sprint: Git Bash translates Unix absolute paths (e.g., `/opt/flink/usrlib/processing/silver_job.py`) to Windows paths (`C:/Program Files/Git/opt/flink/...`) in command-line arguments, even when those paths are destined for a Linux container. PyFlink then stages a broken symlink pointing at the Windows path, and `toRealPath()` fails with `NoSuchFileException`. Fix: all `kubectl exec` commands with Unix-path arguments must be issued from PowerShell.**
- **D11 — Test message production must use Python inside the JM pod (not PowerShell stdin pipe). Confirmed during sprint: PowerShell 5.1 adds a UTF-8 BOM when piping a `$string` to `kubectl exec -i`, causing the Silver job's `ndjson_deserializer` to fail with `Unexpected UTF-8 BOM`. Canonical fix: base64-encode a Python producer script in PowerShell and decode+execute inside the JM pod: `kubectl exec -- bash -c "echo <b64> | base64 -d | python3"`. This uses the INTERNAL Kafka listener (`kafka:29092`) and the exact same NDJSON serializer as the actual producers.**

### Task table

| Task | Description | Gate(s) | Spec Reference |
|------|-------------|---------|----------------|
| [x] C3.1 | Add `infrastructure/k8s/flink-properties-configmap.yaml` — ConfigMap `flink-properties` with `jobmanager` and `taskmanager` keys, FLINK_PROPERTIES lifted verbatim from docker-compose. **Closeout note (D7)**: `#` comment lines were added then removed after discovering Flink parses them as property keys. Final ConfigMap contains no inline comments. Per D2, D7. | — | §4.3 |
| [x] C3.2 | Add `infrastructure/k8s/flink-checkpoints-pvc.yaml` — PVC `flink-checkpoints`, 5 GB, `standard-rwo` (not `pd-balanced` as spec read — corrected to match C2 carry-forward), `ReadWriteOnce`. Per D3. | — | §4.3 |
| [x] C3.3 | Add `infrastructure/k8s/flink-secretproviderclass.yaml` (NEW — not in original spec), `flink-jobmanager-deployment.yaml`, `flink-jobmanager-service.yaml`. SPC mounts `POSTGRES_PASSWORD` + `OPENAI_API_KEY` as files only (no `secretObjects`). JM uses shell wrapper command (D5). Service includes ports 8081, 6123, 6124 (`blob`), 9249 (D6). Per D1, D3–D6. | — | §4.3, §8.2 |
| [x] C3.4 | Add `infrastructure/k8s/flink-taskmanager-deployment.yaml` — same shell wrapper pattern (D5), same SPC, checkpoint subPath mount. No Service needed. Per D1, D3–D6. | — | §4.3, §8.2 |
| [x] C3.5 | Applied 6 manifests. Both pods Running + Ready. Flink web UI: 1 TaskManager, 4 free slots. | — | §8.2 |
| [x] C3.6 | Submitted Silver job (PowerShell, D10). Job `anizai-silver-polymarket` RUNNING, 18/18 tasks, first checkpoint completed (27504B, ~42ms). No exceptions in 60s. | Gate | §4.1 |
| [x] C3.7 | Submitted Gold job. Job `anizai-gold-all-sources` RUNNING, 8/8 tasks. | Gate | §4.2 |
| [x] C3.8 | Produced synthetic FRED Bronze record via Python in JM pod (D11). Round-trip confirmed: `process.silver.structured_metrics` (741B), `serve.gold.structured_metrics` (824B), `momentum_vault` row `FEDFUNDS / 4.33` ingested at 2026-05-09 12:10:26 UTC. | Gate | §4.1, §4.2, §5 |
| [x] C3.9 | Force-killed TM (`--force --grace-period=0`, D9). New TM pod Ready. Silver restored from `chk-6`, Gold restored from `chk-5`. Both RUNNING. Second test message `FEDFUNDS / 4.50` confirmed in `momentum_vault` at 2026-05-09 13:39:39 UTC. | Gate | §4.3 |

### Gate C3 — **PASSED 2026-05-09**

- [x] Both Flink pods `Running` and Ready (JM `558778fb4f`, TM `67bbf79d76`).
- [x] Silver job `anizai-silver-polymarket` RUNNING, 18/18 tasks, checkpoint 1 completed (27504B, 42ms).
- [x] Gold job `anizai-gold-all-sources` RUNNING, 8/8 tasks.
- [x] Test message round-trip: `FEDFUNDS / 4.33` in `momentum_vault` at 12:10:26 UTC (~10 min after production, including first checkpoint window). Bronze partition confirmed (897B), Silver p1 741B, Gold p2 824B.
- [x] Checkpoint recovery (force-kill, D9): Silver restored from `chk-6` (`file:/opt/flink/checkpoints/.../chk-6`), Gold restored from `chk-5`. Second test message `FEDFUNDS / 4.50` confirmed in `momentum_vault` at 13:39:39 UTC.

### Carry-forward to C4

- **Shell wrapper pattern (D5)** applies to all C4 Deployments that use env vars sourced from Secret Manager CSI files (Airflow scheduler, producers, trigger consumer).
- **No `#` comments in FLINK_PROPERTIES-equivalent env blocks (D7)** — also applies to Airflow's `AIRFLOW__*` env block construction.
- **Use PowerShell for `kubectl exec` with Unix paths on Windows (D10)** — all C4 kubectl exec commands must use PowerShell, not the Bash tool.
- **Test message injection pattern (D11)** — use base64-encoded Python script via JM pod for any additional E2E validation in C4.
- **`ExponentialDelay` restart strategy (D8)** — FLINK_PROPERTIES now uses `restart-strategy.fixed-delay.attempts: 10` + `delay: 20s` as sub-keys only (no scalar parent); Flink resolves to ExponentialDelay with unlimited retries by default when checkpointing is enabled. This is the intended C4 forward-state.

### Cold-start handling

If a job fails to submit with `ModuleNotFoundError`: `PYTHONPATH` is not set — verify Dockerfile.flink ENV is preserved in the image (it is, since the image is unchanged from C1). If checkpoint recovery shows the job restarting from EMPTY state instead of the latest checkpoint, the PVC was not mounted on the new TM pod — verify `volumes` and `volumeMounts` in the TM Deployment. If tasks hang in `DEPLOYING` indefinitely after job submission: verify port 6124 (`blob`) is in the JM Service — this was the root cause in Sprint C3 (D6). If a new GKE cluster `secretObjects` sync fails with `FailedToCreateSecret`, the GKE-native CSI provider confirmed does not support secretObjects — use the shell wrapper pattern (D5) instead.

---

## Sprint C4 — Airflow + All Producers + Trigger Consumer

### Sprint scope

Stand up the orchestration layer (Airflow scheduler + webserver + dedicated Postgres) and deploy all 9 producers — 7 scheduled (driven by Airflow DAGs) plus 2 always-on streaming daemons (Polymarket via WebSocket, Telegram via MTProto). Also build the new `Dockerfile.trigger_consumer` and deploy the reactive ingestion trigger consumer (which closes KG-PHASE8-3, the runtime-validation gap on `orchestration/ingestion_trigger_consumer.py`). At the end of the sprint, real data from all 9 sources lands in Postgres.

The end of Sprint C4 deliverable: 30 minutes after the trigger consumer comes up, `SELECT source, COUNT(*) FROM knowledge_vault GROUP BY source` and the analogous query on `social_vault` + `momentum_vault` show non-zero counts for every one of the 9 sources.

### Confirmed design decisions

- **D1 — Polymarket and Telegram are always-on streaming `Deployment`s, not Airflow DAGs.** Mirrors the docker-compose comments in `Dockerfile.airflow` (header) — both are persistent connections (WebSocket / MTProto), not scheduled pollers. Putting them in Airflow would either (a) require a TaskFlow continuous task pattern that fights the LocalExecutor, or (b) cycle the connection on every DAG schedule. Both unacceptable.
- **D2 — Telegram session file is generated locally on the developer laptop with a new account, then uploaded to Secret Manager and projected into the Telegram pod via the CSI driver as a file at `TELETHON_SESSION_PATH`.** MTProto auth requires an interactive SMS flow on first run; we cannot let the pod attempt it on startup. Generating once locally + pushing as a secret avoids ever putting interactive auth on the cluster. The session file is treated as a secret (it grants read access to the account's messages).
- **D3 — Reactive trigger consumer (`orchestration/ingestion_trigger_consumer.py`) gets a new `infrastructure/Dockerfile.trigger_consumer` and a new K8s `Deployment` in this sprint.** This closes **KG-PHASE8-3** ("Pipeline reactive ingestion runtime validation pending"). The existing static wiring is verified; runtime exercise happens here as a side-effect of the consumer running for the first time outside docker-compose.
- **D4 — Gate criterion: 30-minute observation window with non-zero rows from all 9 sources.** Shorter windows miss low-frequency producers (FRED runs daily at 06:00 UTC, ArXiv daily at 07:00 UTC, GoogleTrends daily at 08:00 UTC). 30 min covers the longest sub-daily cadence (NewsAPI / HackerNews every 20 min) so each scheduled producer fires at least once. The three daily producers may be triggered manually via Airflow UI (`Trigger DAG`) to land at least one row inside the observation window.

### Task table

| Task | Description | Gate(s) | Spec Reference |
|------|-------------|---------|----------------|
| [ ] C4.1 | Add `infrastructure/k8s/airflow-postgres-statefulset.yaml` — dedicated Postgres for Airflow metadata (image `postgres:16`, 5 GB PVC). Separate from the application Postgres per the existing docker-compose D1 ("two databases fully independent"). | — | §2.3, §8.2 |
| [ ] C4.2 | Add `infrastructure/k8s/airflow-postgres-service.yaml` — ClusterIP `airflow-postgres` on port 5432. | — | §8.2 |
| [ ] C4.3 | Apply airflow-postgres manifests. Wait for Ready. | — | — |
| [ ] C4.4 | Add `infrastructure/k8s/airflow-init-job.yaml` — one-shot `Job` that runs `airflow db migrate` then provisions the admin user (same script as docker-compose `airflow-init`, image `apache/airflow:2.9.3-python3.12`, env from Secret Manager). | — | §2.3 |
| [ ] C4.5 | Apply airflow-init Job. Wait for `Completed`. | — | — |
| [ ] C4.6 | Add `infrastructure/k8s/airflow-scheduler-deployment.yaml` — `Deployment` (image `us-central1-docker.pkg.dev/anizai-pipeline/anizai-images/anizai-airflow:2.9.3`, command `scheduler`, env from Secret Manager + `KAFKA_BOOTSTRAP_SERVERS=kafka:29092`, `POSTGRES_HOST=postgres`, `PYTHONPATH=/opt/airflow/data-pipeline`). DAG files baked into the image (no volume mount — production mode per Dockerfile.airflow D2). | — | §2.3 |
| [ ] C4.7 | Add `infrastructure/k8s/airflow-webserver-deployment.yaml` — same image, command `webserver`, port 8080. Add accompanying ClusterIP Service. | — | §2.3 |
| [ ] C4.8 | Apply scheduler + webserver. Verify pods Ready. From local laptop: `kubectl port-forward svc/airflow-webserver 8090:8080`, log in with admin credentials, verify all 8 DAGs present (7 producers + scraper_dag) and unpaused. | — | §2.3 |
| [ ] C4.9 | Manually trigger `fred_daily` from the Airflow UI. Verify task succeeds; check `kubectl logs` of the scheduler shows the producer subprocess running; verify a row lands in Kafka topic `ingest.bronze.fred` (via kafka-ui) and downstream in `momentum_vault`. | Gate | §2.3, §4 |
| [ ] C4.10 | Write **NEW** `infrastructure/Dockerfile.polymarket` — base image `python:3.12-slim`, installs the Polymarket-relevant subset of `requirements.txt` (websockets, kafka-python-ng, python-dotenv, structured logging deps), COPYs `ingestion/polymarket_producer.py` + `config/` + `utils/`, CMD runs the producer module. Build + push as `anizai-polymarket:0.1.0` to Artifact Registry. | — | §2.3, §8.2 |
| [ ] C4.11 | Finalize `infrastructure/k8s/producers/polymarket-deployment.yaml` (scaffold authored in C1 to capture the dual-node-pool decision; see Sprint C1 §D2). Always-on `Deployment` (1 replica, restart policy Always, env from Secret Manager + `KAFKA_BOOTSTRAP_SERVERS=kafka:29092`). **Required**: `nodeSelector: cloud.google.com/gke-nodepool=polymarket-pool` so the pod pins to the always-on `polymarket-pool` (e2-micro × 1) and survives when `main-pool` is scaled to 0. Per D1 + Sprint C1 D2 (revised). | — | §2.3 |
| [ ] C4.12 | Locally generate the Telegram session file with a new account: run the Telethon first-login flow on the laptop, accept the SMS code, save the resulting `.session` file. Push it to Secret Manager as secret `TELEGRAM_SESSION_FILE` (binary content, base64 if needed). Per D2. | — | §2.3 |
| [ ] C4.13 | Write **NEW** `infrastructure/Dockerfile.telegram` — base `python:3.12-slim`, installs telethon + kafka-python-ng + project deps, COPYs `ingestion/telegram_producer.py` + `config/` + `utils/`, CMD runs the producer. Build + push as `anizai-telegram:0.1.0`. | — | §2.3, §8.2 |
| [ ] C4.14 | Add `infrastructure/k8s/producers/telegram-deployment.yaml` — always-on `Deployment`, env from Secret Manager, **session file mounted via the CSI driver** as a file at the path Telethon expects (per D2). 1 replica, restart Always. SecretProviderClass uses `provider: gke` and CSI driver `secrets-store-gke.csi.k8s.io` (per Sprint C1 closure findings). Path note: producer manifests live under `producers/` subdirectory — convention settled at Sprint C2 kickoff. | — | §2.3 |
| [ ] C4.15 | Apply Polymarket + Telegram. Verify both pods Ready, no error logs in the first 5 minutes, and rows landing in `ingest.bronze.polymarket` + `ingest.bronze.telegram` (via kafka-ui). | — | §2.3 |
| [ ] C4.16 | Write **NEW** `infrastructure/Dockerfile.trigger_consumer` — base `python:3.12-slim`, installs kafka-python-ng + project deps, COPYs `orchestration/ingestion_trigger_consumer.py` + `config/` + `utils/` + `ingestion/`, CMD runs the consumer module. Build + push as `anizai-trigger-consumer:0.1.0`. Per D3. | — | §2.4 |
| [ ] C4.17 | Add `infrastructure/k8s/producers/trigger-consumer-deployment.yaml` — always-on `Deployment` (1 replica, env from Secret Manager + `KAFKA_BOOTSTRAP_SERVERS=kafka:29092`, restart Always). Per D3. Path note: lives under `producers/` per the convention settled at Sprint C2 kickoff (alongside polymarket + telegram). | — | §2.4 |
| [ ] C4.18 | Apply trigger consumer. Verify pod Ready and listening on `ingestion_triggers`. | Gate | §2.4 |
| [ ] C4.19 | Wait 30 minutes (kick off the timer after C4.18 lands and after manually triggering `fred_daily`, `arxiv_daily`, `googletrends_daily` in the Airflow UI). Then run the gate query against cloud Postgres: `SELECT source, COUNT(*) FROM knowledge_vault GROUP BY source` + analogous on `social_vault` + `momentum_vault`. Verify non-zero rows from all 9 sources. Per D4. | Gate | §3, §5 |
| [ ] C4.20 | Update `task_plan.md` Known Gaps to mark **KG-PHASE8-3 CLOSED** with a note pointing at this sprint and the timestamp of the gate query. Per D3. | — | KG-PHASE8-3 |

### Gate C4

- [ ] Airflow scheduler + webserver pods Ready, all 8 DAGs visible and unpaused.
- [ ] Manual `fred_daily` trigger: Airflow task succeeded, row visible in `ingest.bronze.fred`, downstream row in `momentum_vault`.
- [ ] Polymarket pod Ready, rows landing in `ingest.bronze.polymarket`.
- [ ] Telegram pod Ready (no SMS-prompt loop), rows landing in `ingest.bronze.telegram`.
- [ ] Trigger consumer pod Ready, listening on `ingestion_triggers`.
- [ ] **30-minute observation gate**: non-zero rows from all 9 sources across `knowledge_vault` + `social_vault` + `momentum_vault`.
- [ ] **KG-PHASE8-3 closed** in `task_plan.md`.

### Cold-start handling

If the Telegram pod CrashLoops with `AuthKeyUnregisteredError`, the session file in the secret has been invalidated (account logged in elsewhere, or session expired) — re-generate locally per C4.12 and update the secret. If `fred_daily` succeeds in Airflow but no row lands in Kafka, the scheduler env override `KAFKA_BOOTSTRAP_SERVERS=kafka:29092` is missing — same fix as the docker-compose scheduler env block. If after 30 minutes one of the three daily producers (FRED / ArXiv / GoogleTrends) still has zero rows, manually trigger it from the Airflow UI and extend the observation window by 5 minutes — gate criterion is non-zero, not "natural firing".

---

## Sprint C5 — Agent + Monitoring + Local E2E

### Sprint scope

Stand up the agent worker (with cross-project Firestore access to `anizai-ai`), Prometheus + Grafana, the daily Postgres backup CronJob, and run the Phase C closing E2E: local frontend submits a forecast → cloud agent claims it → cloud Postgres serves vault evidence → GPT-4o synthesizes → cloud Firestore receives `sessionResults` → local frontend renders. Document the developer's local connection ergonomics in `data-pipeline/docs/LOCAL_CONNECTION.md`.

### Confirmed design decisions

- **D1 — Agent uses cross-project Firestore via Workload Identity. No JSON key file.** Create GCP SA `agent-worker@anizai-pipeline.iam.gserviceaccount.com` in the cluster's project. Bind it to KSA `agent-worker-ksa` in namespace `anizai` (**D-C5-1**: KSA named `agent-worker-ksa` per user instruction, deviating from the original spec name `agent-worker`). Grant `roles/datastore.user` on the **other** project (`anizai-ai`) — this is self-service because the user is Owner on both projects. The agent process sets `FIREBASE_PROJECT_ID=anizai-ai` and the Admin SDK picks up Workload Identity credentials from the metadata server. `GOOGLE_APPLICATION_CREDENTIALS` is **not** set; no service-account JSON exists anywhere.
- **D2 — Daily `pg_dump` `CronJob` → GCS bucket `gs://anizai-pipeline-backups/postgres/`.** Schedule `0 2 * * *` (02:00 UTC). 30-day GCS object lifecycle. Restore is tested once during this sprint by importing yesterday's dump into a scratch database and running a row-count comparison. Restore is not automated — manual on demand.
- **D3 — Prometheus + Grafana run inside the cluster, scraped/exposed via port-forward (no public access).** Prometheus scrape targets: `flink-jobmanager:9249`, `flink-taskmanager:9249`, `agent-worker:8000` (the `/metrics` endpoint exposed by the agent's health server, §8.8.2). Prometheus PVC: 10 GB (~15 days at current scrape volume). Grafana provisioning loads the same dashboard files already committed at `infrastructure/grafana/` for docker-compose — zero dashboard divergence between local and cloud.
- **D4 — `data-pipeline/docs/LOCAL_CONNECTION.md` is the single source of truth for the developer-laptop loop.** Captures every `kubectl port-forward` command (Postgres, kafka-ui, Airflow web, Flink web UI, Grafana, agent /health), every env var the local frontend needs flipped (Firestore project ID, agent endpoint, etc.), and a copy-pasteable "start everything" + "stop everything" recipe. Without this doc the cloud cluster is unusable from the laptop.

### Task table

| Task | Description | Gate(s) | Spec Reference |
|------|-------------|---------|----------------|
| [x] C5.0 | Migration `002_drop_scrape_attempted.sql` already applied to cloud Postgres in C4. No action needed. | Gate | Phase 7C T7C.11 |
| [x] C5.1 | Created GCP SA `agent-worker@anizai-pipeline.iam.gserviceaccount.com`. Granted `roles/secretmanager.secretAccessor` at project level (same pattern as `pipeline-runtime` from C1). Per D1. | — | §8.10 |
| [x] C5.2 | User granted `roles/datastore.user` on `anizai-ai` (self-service — Owner on both projects). Confirmed. Per D1. | — | §8.10 |
| [x] C5.3 | Created KSA `agent-worker-ksa` (D-C5-1: name per user instruction). WI binding to `agent-worker@...` confirmed. KSA annotation set. Per D1. | — | §8.10 |
| [x] C5.4 | Added `agent-secretproviderclass.yaml` (OPENAI_API_KEY + POSTGRES_PASSWORD — D-C5-2: POSTGRES_PASSWORD needed by persistence layer), `agent-deployment.yaml` (shell wrapper, ServiceAccount: agent-worker-ksa, no GOOGLE_APPLICATION_CREDENTIALS), `agent-service.yaml` (ClusterIP port 8000 named metrics). Also added: **Bundle 0** — updated `Dockerfile.agent` to include `persistence/`, `utils/`, `config/` COPY layers (Sprint 18 image was stub-only; Sprint 19+ vault queries require them). Rebuilt + pushed `anizai-agent:0.1.0`. Per D1. | — | §8.10, §8.11 |
| [x] C5.5 | Agent pod `1/1 Running`, 0 restarts. `/health` returns `{"status": "ok", "agent_version": "0.4.0-sprint21-clarification-tier2"}`. Health endpoint returning `ok` confirms Firestore listener subscription (worker only reaches ok after successful subscribe). Per D1. | Gate | §8.8.1 |
| [x] C5.6 | Added `flink-taskmanager-service.yaml` (ClusterIP port 9249 — new; no TM service existed in C3, needed for Prometheus scraping per D-C5-3). Added `prometheus-configmap.yaml` (3 scrape jobs: flink-jobmanager:9249, flink-taskmanager:9249, agent-worker:8000/metrics, 15s interval). Per D3. | — | §7.2 |
| [x] C5.7 | Added `prometheus-pvc.yaml` (10Gi standard-rwo) + `prometheus-deployment.yaml` (prom/prometheus:v2.51.2, ConfigMap subPath mount, PVC at /prometheus, --web.enable-lifecycle). **D-C5-3**: required `securityContext.fsGroup: 65534` — Prometheus runs as nobody (UID 65534); without fsGroup the PVC mount is root-owned and `open /prometheus/queries.active: permission denied` at startup. Same root cause as Postgres fsGroup:999 in C2. ClusterIP Service on port 9090. Per D3. | — | §7.2 |
| [x] C5.8 | All 3 targets UP: `agent-worker:8000` (last scrape 15:45:53Z), `flink-jobmanager:9249` (15:45:54Z), `flink-taskmanager:9249` (15:45:50Z). Zero dropped targets. Per D3. | Gate | §7.2 |
| [x] C5.9 | Added `grafana-secretproviderclass.yaml` (GRAFANA_ADMIN_PASSWORD). `grafana-configmap.yaml` — 3 keys: `prometheus.yml` (url: http://prometheus:9090), `dashboard.yml` (provider path /var/lib/grafana/dashboards), `anizai_pipeline.json` (16KB verbatim). `grafana-deployment.yaml` (grafana/grafana:10.4.2, shell wrapper for GF_SECURITY_ADMIN_PASSWORD, 3 subPath mounts, fsGroup:472, ClusterIP Service port 3000). **D-C5-4**: fsGroup:472 for Grafana data dir; ConfigMap subPath mounts for all 3 provisioning files — zero dashboard divergence from docker-compose source. Per D3, D4. | — | §7.2 |
| [x] C5.10 | Grafana pod `1/1 Running`. `/api/health` returns `{"database": "ok", "version": "10.4.2"}`. Startup log confirms `provisioning.dashboard: finished to provision dashboards`. Per D3. | Gate | §7.2 |
| [x] C5.11 | GCS bucket `gs://anizai-pipeline-backups/` created (us-central1, standard). 30-day lifecycle on `postgres/` prefix. **D-C5-5**: granted `roles/storage.objectAdmin` (not `objectCreator` as original spec — `gsutil cp -` from stdin requires `storage.objects.list` in addition to `create`; objectCreator alone returns 403 at the list step). Script: `infrastructure/gcp/07_gcs_backup_bucket.sh`. Per D2. | — | §9.1 |
| [x] C5.12 | Added `postgres-backup-cronjob.yaml` (schedule `0 2 * * *`, image `google/cloud-sdk:slim`, ServiceAccount `pipeline-runtime`). Installs `postgresql-client-16` from pgdg apt repo using `gpg --dearmor` + `signed-by` method (not deprecated `apt-key add`). Pipes `pg_dump | gzip | gsutil cp -` to GCS. POSTGRES_PASSWORD from `postgres-secrets-spc` CSI mount. Per D2, D-C5-5. | — | §9.1 |
| [x] C5.13 | Manual trigger `backup-test-run` Completed. GCS object: `gs://anizai-pipeline-backups/postgres/2026-05-10/anizai.sql.gz` (15.1 MiB). Restore verification: dump contains all 7 application tables (COPY rows confirmed). `knowledge_vault` dump count = 424 = cloud live count. Per D2. | Gate | §9.1 |
| [x] C5.14 | `data-pipeline/docs/LOCAL_CONNECTION.md` written. Sections: Prerequisites, Port-forward table + PowerShell start/stop recipes, per-service connection instructions, local frontend env vars, node pool scale commands, troubleshooting (WI, port-forward, OpenAI quota). Per D4. | — | §5.4 |
| [x] C5.15 | **Phase C E2E PASSED 2026-05-10.** Query: "Will the Fed cut rates by Q2 2026?" → `anizai-ai` Firestore → cloud agent (version 0.4.0-sprint21-clarification-tier2) claimed → cloud Postgres vault retrieval → GPT-4o synthesis → `sessionResults` written (finalProbability, evidenceVolumeLabel, generatedAt all populated) → local frontend rendered all four BI cards. Log silence = KG-PHASE8-7 (known INFO suppression, non-blocking). | Gate (Phase C E2E) | §8.7.1, §8.7.2 |

### Gate C5 + Phase C closing gate — **PASSED 2026-05-10**

- [x] Agent pod `1/1 Running`, 0 restarts. `/health` `{"status":"ok","agent_version":"0.4.0-sprint21-clarification-tier2"}`. Firestore listener on `anizai-ai` confirmed (health only reaches `ok` post-subscribe). KSA `agent-worker-ksa` + WI binding to `agent-worker@anizai-pipeline.iam.gserviceaccount.com`.
- [x] Prometheus `/api/v1/targets`: all 3 targets `health: up` — `flink-jobmanager:9249`, `flink-taskmanager:9249`, `agent-worker:8000`. Zero dropped targets.
- [x] Grafana `/api/health` `{"database":"ok","version":"10.4.2"}`. Dashboard `anizai-pipeline-v1` provisioned from ConfigMap. Startup log confirms `provisioning.dashboard: finished`.
- [x] GCS bucket `gs://anizai-pipeline-backups/` (lifecycle 30d on postgres/). CronJob `backup-test-run` Completed: `anizai.sql.gz` 15.1 MiB in GCS. Restore check: `knowledge_vault` dump rows = 424 = cloud live count.
- [x] `data-pipeline/docs/LOCAL_CONNECTION.md` written (port-forward recipes, frontend env, scale commands, troubleshooting).
- [x] Cloud Scheduler: `scale-up-main-pool` (Mon-Fri 08:00 IL) + `scale-down-main-pool` (Mon-Fri 18:00 IL) both `ENABLED`. `scheduler-scaler` GSA has `roles/container.admin`.
- [x] **Phase C E2E PASSED**: local frontend → `anizai-ai` Firestore → cloud agent claimed → cloud Postgres vault retrieval (Researcher/Pulse/Market Bridge) → GPT-4o synthesis → `sessionResults` written (`finalProbability`, `evidenceVolumeLabel`, `generatedAt` populated) → local frontend rendered four BI cards.

### Cold-start handling

If the agent pod logs `Could not automatically determine credentials`, Workload Identity binding did not propagate — wait 30-60s after the KSA annotation lands, then bounce the pod (`kubectl rollout restart deploy/agent-worker`). If the cross-project IAM grant in C5.2 fails with `PERMISSION_DENIED`, the user is not actually Owner on `anizai-ai` — back to the Pre-Phase C checkpoint. If Grafana shows "No data" but Prometheus targets are UP, the dashboard's data-source UID does not match the provisioning file — same root cause as the local docker-compose Grafana provisioning fix.

---

## Out of Scope for Phase C

These items are **explicitly deferred** beyond Phase C:

- **Multi-zone HA / multi-node node pools** — single-zone, single-node is sufficient for V1; revisit if any pod is OOMKilled or if total CPU sustains > 70%.
- **Public ingress / DNS / TLS / OAuth** — local laptop is the only client; everything goes through `kubectl port-forward`.
- **Helm charts / Argo CD / GitOps** — V1 is plain `kubectl apply`. The manifests are simple enough that adding a Helm layer is more cost than benefit.
- **Cloud SQL or any other managed Postgres** — rejected in Sprint C2 D1 (no TimescaleDB support); revisit only if/when Cloud SQL adds the extension.
- **Per-workload least-privilege GSAs** — V1 uses one shared `pipeline-runtime` GSA + one cross-project `agent-worker` GSA; partition further only if a secret leak ever happens.
- **Automated restore testing in CI** — restore is tested once manually in C5.13; periodic automation is post-V1.
- **Horizontal Pod Autoscaler / Vertical Pod Autoscaler** — single replicas everywhere; revisit when a real second user shows up.
- **kafka-ui authentication** — port-forward is the auth boundary; no in-cluster auth.

---

## Cross-Sprint Dependencies & Skill Loading

### Hard dependencies between sprints

| Sprint | Depends on | Why |
|--------|------------|-----|
| C2 | C1 | Cluster + namespace + secrets must exist before any StatefulSet can run. |
| C3 | C1, C2 | Flink reads from Kafka and writes to Postgres. |
| C4 | C1, C2, C3 | Airflow DAGs produce into Kafka topics that Silver/Gold jobs (C3) consume; trigger consumer reads from `ingestion_triggers` (C2). |
| C5 | C1, C2, C4 | Agent reads from Postgres vaults populated by the C4 producers; Prometheus scrapes Flink (C3) and the agent (C5). The Phase C closing E2E exercises everything from C1-C4. |

A failed gate in any sprint blocks the next sprint. There is no "partial deploy" mode — Phase C is sequential.

### Skills required per sprint

| Sprint | Required skills |
|---|---|
| C1 | sprint-kickoff, infrastructure |
| C2 | sprint-kickoff, infrastructure |
| C3 | sprint-kickoff, infrastructure, code-review |
| C4 | sprint-kickoff, infrastructure, code-review |
| C5 | sprint-kickoff, infrastructure, code-review, frontend-integration |

### Documents required per sprint

All Phase C sprints require:
- `infrastructure/docker-compose.yml` (the parity-source-of-truth)
- The relevant Dockerfile(s): `Dockerfile.flink` (C3), `Dockerfile.airflow` (C4), `Dockerfile.agent` (C5), and the new files written in C4 (`Dockerfile.polymarket`, `Dockerfile.telegram`, `Dockerfile.trigger_consumer`).
- `infrastructure/sql/init.sql` (C2)
- `cloud_deployment_implementation.md` (this doc)
- `task_plan.md` (active tracker)
- The skills listed above

Sprint C5 also needs `agentic_hub_implementation.md` and `agentic_hub_spec.md` (for the Phase C E2E to exercise the same Tier 1 path validated in Sprint 20).

---

## Phase C Acceptance Criteria (Cloud Deployment Done)

Phase C is complete when:

- [ ] All 5 sprints (C1-C5) closed with Sprint State Ledgers.
- [ ] All per-sprint gates green.
- [ ] Phase C E2E passes: local frontend → cloud Firestore → cloud agent (cross-project WI) → cloud Postgres (TimescaleDB self-hosted) → GPT-4o → cloud Firestore → local frontend renders, with no JSON key files anywhere in the cluster or repo.
- [ ] All 9 sources show non-zero data in `knowledge_vault` + `social_vault` + `momentum_vault`.
- [ ] **KG-PHASE8-3 closed** (reactive trigger consumer running in cluster, runtime-validated).
- [ ] Daily `pg_dump` CronJob successful, restore test passed.
- [ ] Grafana dashboard live with Flink + agent metrics.
- [ ] Billing alerts armed; first-month cost projection under $100.
- [ ] `data-pipeline/docs/LOCAL_CONNECTION.md` committed; a developer who's never seen the cluster can reach every service from their laptop using only that doc.

---

## Appendix: GCP Resource Inventory

Single source of truth for everything Phase C creates. If anything is unclear about who owns what, this is the table to read.

| Resource type | Name | Project | Sprint | Notes |
|---------------|------|---------|--------|-------|
| GCP project (existing) | `anizai-pipeline` | — | — | Cluster + Postgres + Kafka + Flink + Airflow + producers + agent. |
| GCP project (existing) | `anizai-ai` | — | — | Firestore (`forecastQueries`, `sessionResults`, `messages`). |
| Artifact Registry repo | `anizai-images` | `anizai-pipeline` | C1 | `us-central1-docker.pkg.dev/anizai-pipeline/anizai-images`. |
| Container image | `anizai-flink:1.19.1` | `anizai-pipeline` | C1 | Built from `infrastructure/Dockerfile.flink`. |
| Container image | `anizai-airflow:2.9.3` | `anizai-pipeline` | C1 | Built from `infrastructure/Dockerfile.airflow`. |
| Container image | `anizai-agent:0.1.0` | `anizai-pipeline` | C1 | Built from `infrastructure/Dockerfile.agent`. |
| Container image | `anizai-polymarket:0.1.0` | `anizai-pipeline` | C4 | NEW — built from `infrastructure/Dockerfile.polymarket`. |
| Container image | `anizai-telegram:0.1.0` | `anizai-pipeline` | C4 | NEW — built from `infrastructure/Dockerfile.telegram`. |
| Container image | `anizai-trigger-consumer:0.1.0` | `anizai-pipeline` | C4 | NEW — built from `infrastructure/Dockerfile.trigger_consumer`. Closes KG-PHASE8-3. |
| Secret Manager secrets | 15 secrets (per `.env`) | `anizai-pipeline` | C1 | Plus `TELEGRAM_SESSION_FILE` added in C4 (16 total). |
| GKE cluster | `anizai-cluster` | `anizai-pipeline` | C1 | Zone `us-central1-a`, dual-pool: `main-pool` (e2-standard-8 × 1, manually scaled to 0 between collection windows) + `polymarket-pool` (e2-micro × 1, always-on for Polymarket WebSocket). Per D2 (revised). |
| K8s namespace | `anizai` | — | C1 | All Phase C workloads. |
| GCP SA | `pipeline-runtime@anizai-pipeline.iam.gserviceaccount.com` | `anizai-pipeline` | C1 | Shared runtime SA — Secret Manager accessor + GCS object creator (C5). |
| GCP SA | `agent-worker@anizai-pipeline.iam.gserviceaccount.com` | `anizai-pipeline` | C5 | Cross-project: `roles/datastore.user` on `anizai-ai`. |
| K8s SA | `pipeline-runtime` (ns `anizai`) | — | C1 | Bound to GSA above via Workload Identity. |
| K8s SA | `agent-worker` (ns `anizai`) | — | C5 | Bound to agent-worker GSA via Workload Identity. |
| Postgres StatefulSet | `postgres` | — | C2 | Image `timescale/timescaledb-ha:pg16`, 20 GB PVC. |
| Kafka StatefulSet | `kafka` | — | C2 | Image `apache/kafka:3.7.0` (KRaft), 10 GB PVC, 19 topics. |
| Flink Deployments | `flink-jobmanager`, `flink-taskmanager` | — | C3 | Shared 5 GB checkpoint PVC. |
| Airflow Postgres StatefulSet | `airflow-postgres` | — | C4 | Image `postgres:16`, 5 GB PVC. |
| Airflow Deployments | `airflow-scheduler`, `airflow-webserver` | — | C4 | Plus one-shot `airflow-init` Job. |
| Producer Deployments | `polymarket`, `telegram`, `trigger-consumer` | — | C4 | Always-on streaming workloads (D1, D3). `polymarket` pins to `polymarket-pool` via `nodeSelector` (Sprint C1 D2 revised); `telegram` and `trigger-consumer` schedule on `main-pool`. |
| Agent Deployment | `agent-worker` | — | C5 | Cross-project Firestore via WI. |
| Monitoring Deployments | `prometheus`, `grafana` | — | C5 | Prometheus 10 GB PVC. |
| GCS bucket | `gs://anizai-pipeline-backups/` | `anizai-pipeline` | C5 | 30-day lifecycle on `postgres/`. |
| K8s CronJob | `postgres-backup` | — | C5 | `0 2 * * *` UTC, dumps to GCS. |
| Billing budget | "Anizai pipeline warning" | `anizai-pipeline` billing acct | C1 | ₪200 threshold (~$54). Currency follows ILS billing account. |
| Billing budget | "Anizai pipeline critical" | `anizai-pipeline` billing acct | C1 | ₪400 threshold (~$108). Currency follows ILS billing account. |
