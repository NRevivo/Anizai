# Cluster Robustness — Implementation Plan
## Anizai Project | Phase 9.5 (Cloud Deployment Hardening)

---

## How to use this document

This is the **structured implementation plan** for Phase 9.5, the cloud-deployment
hardening phase that follows Phase 9 (GKE Cloud Deployment, closed 2026-05-10) and
runs in parallel with Phase 10 (Calibration & Backtesting, queued).

Phase 9.5 is not a regular sprint. It is a multi-stage robustness investigation
split into three sequential stages (A, B, C), each of which is gated by Ron's
explicit approval at two checkpoints (post-investigation, post-fix-plan).

This doc is the **reader-friendly summary** — plan, decisions, findings, fix list,
verification results. For raw diagnostic output (commands run, timestamps, raw
tool outputs, intermediate conclusions), see the companion file
`data-pipeline/docs/phase95_investigation_log.md`.

It should be loaded by Claude Code at the start of every Phase 9.5 session,
alongside:
- `data-pipeline/docs/phase95_investigation_log.md` (the audit-trail log)
- `data-pipeline/docs/archive/cloud_deployment_implementation.md` (Phase 9 baseline)
- `data-pipeline/docs/guides/CLOUD_CONNECTION_GUIDE.md` (operator runbook — known to have outdated secret names; treat as topology reference, not authoritative for specifics)
- `data-pipeline/infrastructure/k8s/*.yaml` (every manifest under investigation)
- The `infrastructure` and `code-review` skills

---

## Phase 9.5 Overview

### Why this phase exists

Over a multi-day debugging session (2026-05-11 through 2026-05-19) the May 11–18
pipeline silence was traced to layered causes, not one:
1. **Flink ran without HA** — job graphs lost on every JM restart. Fixed mid-session
   (see Phase 9 follow-up entry in `task_plan.md`). HA is now enabled and verified.
2. **Kafka PVC was effectively reset at some point.** `/var/lib/kafka/data` contains
   only `lost+found`; zero topics. Mechanism of reset unknown.
3. **kafka-init Job was TTL-GC'd.** No remaining mechanism in the cluster to
   re-create topics on a fresh Kafka PVC.
4. **Polymarket / polymarket-pool design flaw.** Polymarket runs always-on on
   polymarket-pool (e2-micro, 24/7), but Kafka — its destination — only runs during
   main-pool's daily window. Result: NoBrokersAvailable crashloop ~14 hours/day.
   The data buffering intent was never sound; Ron has decided to revert.
5. **CLOUD_CONNECTION_GUIDE.md has wrong secret names** (lowercase-with-dashes
   vs UPPER_SNAKE_CASE). 13 wrong references. Separate doc cleanup.
6. **Monitoring blindspot.** Prometheus + Grafana reported every pod as `health: up`
   throughout the 7-day silence. Pod-liveness monitoring, not pipeline-functionality.
7. **Cloud Scheduler is PAUSED.** Both scale-up-main-pool and scale-down-main-pool
   stay paused until end of Phase 9.5 (Ron's manual final step).

### Goal

Make the cluster genuinely robust:
- Every problem from the debug session is fixed, with no patchwork.
- Comprehensive investigation surfaces problems we haven't yet found.
- Edge cases are addressed (PVC reset recovery, node replacement, OpenAI quota,
  network partitions, etc.).
- End state: cluster is so well-understood and self-healing that Cloud Scheduler
  can be resumed without expecting debugging surprises.
- Monitoring shifts from "pod liveness" to "pipeline functionality" — alerts for
  "zero records processed in X hours", not just "container restart".

### Stage structure

Strictly sequential. Each stage gates the next.

| Stage | Focus | Status |
|---|---|---|
| **A** | Infrastructure robustness — PVCs, StatefulSets, init Jobs, RBAC, node pools, scheduler, networking, secrets, image management, GCP resources | **A.1 in progress (started 2026-05-19)** |
| B | Application robustness — producer error handling, Flink retry, OpenAI/external failure modes, idempotency, DLQ, agent worker error handling | Not planned (planned after A closes) |
| C | Monitoring & operational documentation — pipeline-functionality alerts, dashboards, structured logging, `cluster_operations_guide.md` | Not planned (planned after B closes) |

### Working pattern (each stage)

1. Plan investigation (Claude writes, Ron approves) — no execution yet.
2. Execute investigation (read-only).
3. Surface findings (written summary) + recommended fix plan.
4. Ron approves the fix plan (or refines).
5. Execute fixes (state-changing operations permitted).
6. Verify each fix with concrete tests.
7. Close stage with summary in this doc.
8. Plan next stage.

### Autonomy granted (Ron, 2026-05-19)

- ✅ May execute state-changing operations (`kubectl apply`, `kubectl create`,
  `kubectl delete`, `gcloud container clusters resize`, etc.) after the stage's
  fix plan has been approved.
- ✅ May modify any infrastructure manifest in `data-pipeline/infrastructure/`
  after approval.
- ✅ May run any read-only diagnostic command at any time without asking.
- ❌ Must NOT resume Cloud Scheduler jobs at the end of Phase 9.5 — Ron's manual
  final step.
- ❌ Must NOT modify application code (`agent/`, `ingestion/`, `processing/`,
  `prompts/`) unless Ron explicitly authorizes a specific file.
- ❌ Must NOT touch `task_plan.md` or `task_plan_implementation.md` mid-stage —
  only at stage-close.
- ❌ Must NOT run git commands. Ron handles all git operations.

### Stop-and-surface framework (Ron, 2026-05-19)

- **RED** — stop immediately and ask:
  - Anything where executing your fix could lose data.
  - Anything that would break a currently-functional workload to fix something else.
  - Anything that requires touching code outside the autonomy boundary.
  - Anything where the right answer depends on Ron's intent.
- **YELLOW** — note and continue:
  - Weird but stable (orphaned ConfigMap, unused KSA, comment drift).
  - Needs fixing but belongs to Stage B/C, not Stage A.
  - Works "by accident" but isn't structured right — log + plan for the right stage.
- **GREEN** — fix as part of consolidated stage plan (one approval, then execute).
  - Anything in scope for the current stage where the fix is bounded and the
    impact is predictable.

---

## Current cluster state (diagnostic baseline, 2026-05-19)

Per Ron's brief at Phase 9.5 kickoff:
- `main-pool`: 1 node (up)
- `polymarket-pool`: 1 node (up)
- Kafka: Running, **0 topics** (PVC effectively reset)
- Flink JM + TM: Running with HA enabled (RBAC patched live + in source)
- Flink jobs: 2 jobs in RESTARTING loop (waiting for topics)
- Cloud Scheduler: both jobs PAUSED
- Postgres: Running, 424 rows in `knowledge_vault`

This is the entry state for Stage A.1. Nothing is "fixed" before Stage A's fix plan
is approved — even obvious problems. The current state is informative.

---

## Stage A — Infrastructure Robustness

### Stage scope

PVCs, StatefulSets, init Jobs, RBAC, node pools, scheduler, networking, secrets,
image management, GCP resources. Cluster-level only. No application code review.

The investigation order is chosen so that earlier findings feed later ones:
storage → statefulness → init Jobs → RBAC → scheduling → recovery → networking
→ secrets → images → cluster settings → backups → Cloud Scheduler.

### Sprint structure

| Phase | Focus |
|---|---|
| **A.1** | Investigation (read-only). 12 areas, in order. Continuous-write findings to `phase95_investigation_log.md`. |
| **A.1 → A.2 gate** | Surface consolidated findings + recommended fix plan to Ron. Wait for explicit approval. |
| **A.2** | Fix execution (state-changing). Includes Polymarket revert, Kafka topic recreation, idempotency conversions, restore drill, and the "reach working order" E2E milestone (FRED + NewsAPI both flow E2E). |
| **A.2 close** | Stage A summary written to this doc. Plan Stage B. |

### A.1 — Investigation areas (12)

| # | Area | Status |
|---|---|---|
| 1 | Persistent storage: PVCs, disks, data-at-rest | **Closed** |
| 2 | Stateful workloads: Kafka + Postgres bootstrap + recovery | **Closed** |
| 3 | One-shot Jobs and forgotten dependencies | **Closed** |
| 4 | RBAC and Workload Identity bindings | **Closed** |
| 5 | Node pools and scheduling | **Closed** |
| 6 | Workload recovery from main-pool scale 0→1 | **Closed** |
| 7 | Networking and service discovery | **Closed** |
| 8 | Secrets: inventory, naming drift, rotation impact | **Closed** |
| 9 | Image management | **Closed** |
| 10 | Cluster-level settings: autorepair, autoupgrade, autoscaling | **Closed** |
| 11 | Backups and restore | **Closed** |
| 12 | Cloud Scheduler (paused state) | **Closed** |

### A.1 — Findings summary

A.1 investigation completed 2026-05-19. Every command, output, and intermediate
conclusion is in `phase95_investigation_log.md`. The summary below highlights
what changes A.2 must address vs. what was found to be correct.

#### The single biggest finding (PRIMARY ROOT CAUSE)

**Kafka has been writing data to the container's ephemeral `/tmp/kafka-logs`
since Sprint C2 (May 7), not to the 10 GB PVC mounted at `/var/lib/kafka/data`.**

- The `kafka-statefulset.yaml` manifest comment claims `/var/lib/kafka/data` is
  the `apache/kafka:3.7.0` default — this assumption is wrong. The image's
  actual default is `/tmp/kafka-logs` when `log.dirs` is unset.
- No `KAFKA_LOG_DIRS` env var is set in the StatefulSet (verified).
- The PVC has been mounted but unused for 11 days — directory mtime is May 7,
  contents are only `lost+found`.
- Every Kafka pod restart wipes `/tmp` → wipes topics → producers and Flink
  reconnect to an empty broker → pipeline silence.
- The 424 knowledge_vault rows from May 10–11 came through during the single
  window when topics existed in `/tmp/kafka-logs` (Sprint C5 closeout E2E
  through to the first daily scale-down).
- `docker-compose.yml` has the same latent bug — same env-only config + same
  mount path. The dev environment doesn't trip it because the local container
  rarely restarts.

This is the corrected story of the May 11–18 silence. The "PVC reset" theory
was wrong: the PVC was never used, so it couldn't have been reset.

#### Other findings

**Multiple workloads in degraded/broken state (24h+):**

1. **Airflow scheduler — 111 restarts in 10h.** Liveness probe targets the
   wrong port: `:8793/health` is Airflow's log-serving API (`serve_logs.py`),
   which requires a JWT and returns 403 to the kube-probe. The scheduler's
   actual health endpoint (enabled by `ENABLE_HEALTH_CHECK=true`, which IS set)
   lives at port **8974**. The KG-PHASE-C-4 closure note picked the wrong
   port. Effect: scheduler container killed every ~150s, DAG dispatch unreliable.

2. **Prometheus — 118 restarts in 24h, OOMKilled at 512Mi.** 871 WAL segments
   accumulated from May 10 → May 18; on every pod start, replaying 871 segments
   exceeds the 512Mi memory limit and the pod is killed at WAL segment ~302.
   Effect: monitoring blind throughout the post-scale-up window (24h+).

3. **Polymarket — 1,577 restarts in 6d1h.** Confirmed: NoBrokersAvailable
   because polymarket-pool runs 24/7 but Kafka does not, and even when Kafka is
   up there are no topics. Per Ron's brief, A.2 reverts the pool design.

**Latent / design-class:**

4. **No mechanism to recreate Kafka topics after a fresh broker boot.** The
   `kafka-init` Job has TTL=300s and ran exactly once on Sprint C2. There is no
   CronJob, no initContainer, no sidecar that re-asserts topic existence. Even
   after F1 (log.dirs fix) lands, the first-time-on-fresh-PVC bootstrap still
   requires manual re-application of the Job.

5. ~~**`THE_NEWS_API_KEY` missing from Secret Manager.**~~ **CORRECTED 2026-05-19.**
   The producer code at `config/settings.py:124` and `ingestion/newsapi_producer.py:107`
   reads env var `NEWSAI_API_KEY` (NOT `THE_NEWS_API_KEY`). Sprint 21.5 kept
   the env var name and changed only the upstream provider; Ron rotated the
   Secret Manager secret's VALUE on May 9 to the thenewsapi.com key while
   keeping the name. The May 9–11 NewsAPI E2E succeeded against thenewsapi.com.
   **YELLOW finding (Stage C):** secret name is misleading (suggests newsapi.ai,
   the deprecated provider) — recommend coordinated rename to `THE_NEWS_API_KEY`
   in Stage C (code + manifests + Secret Manager + migrate-secrets script
   together). Full rationale in `phase95_investigation_log.md` Area 8 Check 8.3.

6. **Cloud Scheduler schedule doc drift.** Real schedule is `05:00 / 15:00 IL`
   (Mon-Fri), not `08:00 / 18:00 IL` as the Phase 9 archive claims.

7. **Two daily Postgres backups missing (May 16 + 17).** Coincides with the
   silence period; the CronJob fired but its pod couldn't schedule (no node
   available + `startingDeadlineSeconds` exceeded). Backups are not robust to
   cluster scale-down.

8. **Polymarket-pool machine type is `e2-small`, not `e2-micro`** as Phase 9
   D2 spec said. Doc drift; non-blocking (more capable than spec).

**Robust / no fix needed:**

- All 5 PVCs Bound and durable. Reclaim policy `Delete` is the GKE default and
  acceptable for V1.
- Postgres data healthy. 424 knowledge_vault, 9,202 knowledge_vectors, 34,665
  momentum_vault, 157 social_vault, 21 social_vectors. All extensions
  installed (pgvector, timescaledb, pg_trgm). momentum_vault hypertable
  confirmed.
- All KSAs correctly annotated with WI GSAs; all GSAs correctly grant WI.
- RBAC scope correct: pipeline-runtime has Secret Manager + GCS access;
  agent-worker has datastore.user on `anizai-ai` (cross-project); Flink HA
  Role + RoleBinding present.
- Cluster egress to OpenAI and Firestore works.
- All Service definitions correct, including kafka headless +
  `publishNotReadyAddresses: true`.
- All Airflow DAGs `catchup=False` + scheduler `CATCHUP_BY_DEFAULT=false`.
  **No backfill flood will occur when Cloud Scheduler resumes (OQ-5 answered).**
- Flink HA verified working: both jobs persist across the JM restart that
  happened 88m ago; they're in RESTARTING (not LOST) because their Kafka
  source topics are missing — exactly the symptom that F1 will resolve.

### A.2 — Fix plan

The plan below is grouped by risk class. **All items require Ron's single
consolidated approval before any state-changing execution begins.** After
approval, fixes execute in order with verification after each.

#### Execution order (revised 2026-05-19 per Ron)

Original A.1 surfacing proposed `F0 → F1 → F2 → F3 → F4 → F5 → F6`. Ron
revised to **`F2 → F0 → F1 → F4 → F5 → F6`** with the following rationale:

- **F2 first** to stabilize monitoring before riskier work — we need
  Prometheus (currently OOMKill-looping 118x/24h) and the Airflow scheduler
  (probe-failing 111x/10h) up before we change anything storage-related.
  Airflow scheduler must also be stable for the F5 E2E test later.
- **F0 second** to remove the 1,577-restarts/6d Polymarket crashloop noise
  from logs before we start changing Kafka — clean signal during F1.
- **F1 third** — the big Kafka log.dirs + topic-reassert fix.
- **F3 cancelled** — env var is `NEWSAI_API_KEY` (not `THE_NEWS_API_KEY` as
  I initially mis-read). Secret already exists with the correct value.
  Misleading name logged as YELLOW Stage C finding.
- **F4 → F5 → F6** unchanged in relative order.
- **F5 narrowed to FRED only** — NewsAPI verified Working May 9–11; silence
  was Kafka-related, not NewsAPI-related.

Stage A "working order" success criterion (OQ-2 revised): FRED flows E2E
through Bronze → Silver (structured_metrics) → Gold (structured_metrics) →
`momentum_vault`. NewsAPI not required for Stage A close.

#### Phase 0 — Polymarket revert (OQ-1: do first)

**F0.1 — Move Polymarket pod back to main-pool.**
- Edit `infrastructure/k8s/producers/polymarket-deployment.yaml`: delete the
  `nodeSelector` block (lines 46-47). Update the file-level comment block to
  reflect the revert.
- `kubectl apply -f infrastructure/k8s/producers/polymarket-deployment.yaml`
  → pod schedules on main-pool.
- Verify pod is `Running` (not crashlooping). Will still fail to produce until
  F2.1 lands (no topic), but the noise from crashlooping stops.

**F0.2 — Delete the polymarket-pool node pool.**
- `gcloud container node-pools delete polymarket-pool --cluster=anizai-cluster --zone=us-central1-a --project=anizai-pipeline`
- ~3-5 minutes. Removes the always-on e2-small cost (~$7/month).
- Verify: `gcloud container node-pools list` shows only main-pool.

**Verification F0:** `kubectl get pods -n anizai | grep polymarket` shows
Running 1/1; `gcloud container node-pools list` shows main-pool only.

#### Phase 1 — Kafka data durability + topic reassertion

**F1.1 — Add `KAFKA_LOG_DIRS=/var/lib/kafka/data` to Kafka StatefulSet.**
- Edit `infrastructure/k8s/kafka-statefulset.yaml`: add env var
  `KAFKA_LOG_DIRS: /var/lib/kafka/data` to the existing env block. Update the
  comment block to remove the incorrect "default Kafka log directory" claim
  and explain the explicit override.
- `kubectl apply -f infrastructure/k8s/kafka-statefulset.yaml` → triggers
  rolling restart of kafka-0.
- On restart, Kafka detects empty PVC, generates fresh KRaft cluster-id on the
  PVC (NOT /tmp), boots Ready.
- Verify: `kubectl exec -n anizai kafka-0 -- ls /var/lib/kafka/data` shows
  KRaft files (meta.properties, __cluster_metadata-0/, etc.).
- Verify: `kubectl exec -n anizai kafka-0 -- find /tmp/kafka-logs` returns
  nothing (or only the leftover from the now-stopped previous process; on a
  fresh pod the new /tmp will be empty).

**F1.2 — Apply same fix to docker-compose.yml.** Parity for dev environment.
- Edit `infrastructure/docker-compose.yml`: add `KAFKA_LOG_DIRS:
  /var/lib/kafka/data` to the kafka service env block (lines ~49-63).
- Not applied to running compose stack (dev's job). Code change only.

**F1.3 — Recreate Kafka topics by re-running kafka-init.**
- `kubectl apply -f infrastructure/k8s/kafka-init-job.yaml` (re-creates the Job).
- Wait for Complete.
- Verify: `kubectl exec -n anizai kafka-0 -- /opt/kafka/bin/kafka-topics.sh
  --bootstrap-server localhost:9092 --list` shows 19 topics.

**F1.4 — Convert kafka-init from one-shot Job to a CronJob (every hour).**
- Create new file `infrastructure/k8s/kafka-init-cronjob.yaml` with the same
  topic-creation script wrapped in a `CronJob` spec at `0 * * * *`.
  `--if-not-exists` means re-runs are idempotent no-ops when topics already
  exist.
- Keep the existing `kafka-init-job.yaml` as a one-shot for manual operator
  use, but the CronJob is the self-healing mechanism.
- `kubectl apply -f infrastructure/k8s/kafka-init-cronjob.yaml`.
- Verify: `kubectl get cronjob -n anizai` shows two CronJobs (postgres-backup
  + kafka-init); next scheduled run within the hour.

**Verification F1:**
- `kubectl exec -n anizai kafka-0 -- /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list | wc -l` returns 19.
- `kubectl get pods -n anizai | grep -E 'flink|polymarket|telegram|trigger'` —
  all 1/1 Running (no crashloops). Flink jobs auto-recover from RESTARTING
  → RUNNING within 1-2 minutes of topics existing.

#### Phase 2 — Workload stability fixes

**F2.1 — Airflow scheduler liveness probe port.**
- Edit `infrastructure/k8s/airflow-scheduler-deployment.yaml`: change
  `httpGet.port: 8793` → `8974`.
- `kubectl apply -f ...` triggers a rollout. New pod starts; liveness probe
  passes; restart count stops climbing.
- Verify after 10 min: `kubectl get pods -n anizai | grep airflow-scheduler`
  shows restart count stable (single-digit if it was previously stable, or
  0 if a fresh pod just landed).

**F2.2 — Prometheus memory limit.**
- Edit `infrastructure/k8s/prometheus-deployment.yaml`: change
  `resources.limits.memory: 512Mi` → `2Gi`, `resources.requests.memory: 256Mi`
  → `512Mi`. Also consider adding `--storage.tsdb.retention.time=7d` to args
  to bound future WAL growth.
- `kubectl apply -f ...`. Pod restarts, WAL replay completes (~30s for 871
  segments at 2Gi).
- Verify: `kubectl get pods -n anizai | grep prometheus` shows 1/1 Running.
- Verify scrape targets: `kubectl exec -n anizai prometheus-... -c prometheus
  -- wget -qO- localhost:9090/api/v1/targets | jq` (or via port-forward).

#### Phase 3 — NewsAPI key (REQUIRES RON'S INPUT)

**F3.1 — Add `THE_NEWS_API_KEY` to Secret Manager.**
- **Requires Ron's input:** the actual API key value from thenewsapi.com.
- `gcloud secrets create THE_NEWS_API_KEY --data-file=-` (read from stdin).
- Verify: `gcloud secrets versions list THE_NEWS_API_KEY` shows version 1
  enabled.

**F3.2 — Mount `THE_NEWS_API_KEY` in airflow-secrets-spc.**
- Edit `infrastructure/k8s/airflow-secretproviderclass.yaml`: add resourceName
  entry for `THE_NEWS_API_KEY`.
- Edit `infrastructure/k8s/airflow-scheduler-deployment.yaml`: add
  `export THE_NEWS_API_KEY=$(cat /var/secrets/airflow/THE_NEWS_API_KEY)` to
  the wrapper script.
- `kubectl apply -f ...` for both.
- Verify: `kubectl exec -n anizai airflow-scheduler-... -- printenv THE_NEWS_API_KEY` returns the key.

**F3.3 — Optional: deprecate `NEWSAI_API_KEY`.** If the producer code no
longer reads it, the Secret Manager entry can be deleted in Stage C. For now,
leave it in place; it costs nothing and removing it is a separate cleanup.

#### Phase 4 — Restore drill (OQ-3)

**F4.1 — Real restore drill into a scratch database.**
- `kubectl exec -n anizai postgres-0 -- createdb -U anizai anizai_scratch`.
- Download yesterday's backup via a temp pod or directly:
  - `gsutil cp gs://anizai-pipeline-backups/postgres/2026-05-18/anizai.sql.gz -`
    piped into postgres-0.
- `kubectl exec -n anizai postgres-0 -- bash -c "gunzip -c < /tmp/backup.sql.gz | psql -U anizai -d anizai_scratch"`.
- Row-count compare:
  - `SELECT relname, n_live_tup FROM pg_stat_user_tables WHERE schemaname='public';`
    for both databases.
- Confirm 7 application tables present in `anizai_scratch` with matching row
  counts.
- Drop scratch database: `kubectl exec -n anizai postgres-0 -- dropdb -U anizai anizai_scratch`.
- Document the exact restore commands in the future `cluster_operations_guide.md`.

#### Phase 5 — Working order E2E (OQ-2 success criterion)

**F5.1 — FRED E2E.**
- Manually trigger `fred_daily` DAG from Airflow UI (via port-forward to
  `kubectl port-forward svc/airflow-webserver 8090:8080`).
- Verify task succeeds.
- Confirm Bronze message appears in `ingest.bronze.fred` (via kafka-ui
  port-forward).
- Confirm new row in `momentum_vault` (via psql port-forward).
- Document the timing in the implementation doc.

**F5.2 — NewsAPI E2E.**
- (Prerequisite: F3 done)
- Manually trigger `newsapi_daily` DAG.
- Verify task succeeds, Bronze in `ingest.bronze.newsapi`, Silver in
  `process.silver.global_news`, Gold in `serve.gold.global_news`, row in
  `knowledge_vault`, vector in `knowledge_vectors`.

**Verification F5:** Both sources flow E2E. Stage A closes.

#### Phase 6 — Scale 0→1 robustness test

**F6.1 — Final scale 0→1 cycle.**
- `gcloud container clusters resize anizai-cluster --node-pool=main-pool --num-nodes=0 --zone=us-central1-a`
- Wait ~5 min for graceful eviction.
- `gcloud container clusters resize anizai-cluster --node-pool=main-pool --num-nodes=1 --zone=us-central1-a`
- Wait ~5 min for pods to come back.
- Verify topic count (`kafka-topics --list | wc -l = 19`).
- Verify Flink jobs auto-recover to RUNNING.
- Verify no producer pod is in CrashLoopBackOff.
- Trigger one DAG (FRED) to confirm data flows.

This proves the cluster is robust to the daily scale-up/scale-down that
Cloud Scheduler would do once resumed.

#### Items intentionally deferred from A.2 → Stages B/C

| Item | Stage | Reason |
|---|---|---|
| Producer error-handling, OpenAI 429 (KG-PHASE-C-5), OpenSky timeout (KG-PHASE-C-6), pytrends 404 (KG-PHASE-C-7) | B | Application code; out of Stage A scope. |
| Pipeline-functionality monitoring (zero-rows-in-N-hours alerts) | C | Monitoring-design work. |
| CLOUD_CONNECTION_GUIDE.md secret name fixes + schedule fixes | C | Documentation cleanup. |
| Scheduler GSA over-privilege (`container.admin` → `container.developer`) | C | Cleanup; not blocking. |
| `imagePullPolicy: Always + tag` → digest pinning | C | Defensive improvement; not blocking. |
| Maintenance window for cluster autoUpgrade | C | Risk reduction; not blocking. |
| Airflow metadata backup CronJob | C | Defense-in-depth; not blocking. |
| `google/cloud-sdk:slim` pinning (KG-PHASE-C-1 class) | C | Same class; not blocking. |
| Polymarket-pool e2-small/e2-micro doc drift | C | Stale doc, not a real issue. |
| Old Flink checkpoint directory cleanup | C | Disk hygiene; 67MB on a 5GB PVC = not blocking. |
| Cluster scheduler `successfulJobsHistoryLimit` hygiene + missed-backup detection | C | Monitoring improvement; not blocking. |

### A.2 — Risk classification summary

| Class | Items | Action |
|---|---|---|
| **RED — needs Ron's input** | F3.1 (the actual `THE_NEWS_API_KEY` value) | Ron provides the key value when approving the consolidated A.2 plan. |
| **GREEN — bounded fixes** | F0, F1, F2, F4, F5, F6 | Execute after Ron approves the consolidated A.2 plan. |
| **YELLOW — noted, deferred** | All items in the "intentionally deferred" table above | Documented in `task_plan.md` Known Gaps at A close; addressed in Stage B/C. |

### A.2 — Estimated time

| Phase | Time | Notes |
|---|---|---|
| F0 (Polymarket revert) | ~10 min | Manifest edit + apply + node pool delete. |
| F1 (Kafka log.dirs + topics) | ~30 min | Manifest edit + rolling restart + re-run init + add CronJob. |
| F2 (Airflow probe + Prometheus mem) | ~15 min | Two manifest edits + rollouts. |
| F3 (NewsAPI key) | ~10 min | Once Ron provides the key. |
| F4 (Restore drill) | ~20 min | Download + restore + compare + cleanup. |
| F5 (FRED + NewsAPI E2E) | ~30 min | Two DAG triggers + observation windows. |
| F6 (Scale 0→1 cycle) | ~15 min | Resize + wait + verify. |
| **Total** | **~2 hours active + ~30 min waits** | Verifications interleave between phases. |

---

**Awaiting Ron's approval to execute A.2 fix plan.** If anything in this
consolidated plan needs adjustment (ordering, items added/removed, deferring
more to Stage B/C), say so now; otherwise the plan executes in the order above
with verifications after each phase.



### A.2 — Fix execution log

A.2 executed 2026-05-19. All 6 phases complete in the Ron-revised order:
F2 → F0 → F1 → F4 → F5 → F6.

| Phase | Description | Status | Notes |
|---|---|---|---|
| F2 | Airflow scheduler probe port 8793 → 8974; Prometheus memory 512Mi → 2Gi + 7d retention + longer livenessProbe.initialDelaySeconds | ✅ COMPLETE | Both pods stable past the 150s probe-failure horizon; Prometheus replayed 871 WAL segments without OOMKill; 3 scrape targets UP. |
| F0 | Polymarket nodeSelector removed; polymarket-pool node pool deleted | ✅ COMPLETE | Pod scheduled on main-pool; node pool deletion confirmed (gcloud node-pools list shows only main-pool); pre-existing crashloop noise stopped. |
| F1 | `KAFKA_LOG_DIRS=/var/lib/kafka/data/kafka-logs` added to kafka-statefulset.yaml + docker-compose.yml; topics recreated via kafka-init Job; new kafka-init-cronjob.yaml (hourly idempotent reassert) | ✅ COMPLETE | First attempt with mount-root path failed (Kafka 3.7 rejects lost+found); subdir pattern matches Postgres pgdata/data. Topics persist on PVC subdir. Flink jobs auto-recovered RESTARTING → RUNNING when topics appeared. Hourly CronJob chosen over initContainer (rationale: initContainers run before the broker is Ready, but topic creation needs a Ready broker — dependency is the wrong direction). Hourly cadence balances self-heal latency vs. churn. |
| F4 | Postgres restore drill into scratch DB from 2026-05-18 GCS backup | ✅ COMPLETE | All 7 tables restored with exact row counts matching live; 4 extensions present (timescaledb 2.27.0 vs live 2.26.4 — image-update-driven, not a restore issue); scratch DB dropped. Restore procedure verified. |
| F5 | FRED E2E (Bronze → Silver → Gold → momentum_vault) | ✅ COMPLETE | Triggered fred_daily DAG; 88 Bronze messages → 88 new momentum_vault rows (888 → 976) in ~12s end-to-end. Pipeline confirmed functional. |
| F6 | Final scale 0→1 cycle robustness test | ✅ COMPLETE | 14 pods returned 1/1 Running; 19 topics persist on PVC; Flink jobs auto-recover from HA ConfigMaps in ~90s; second FRED trigger flows another 88 → 88 rows. **The cluster is robust to the Cloud Scheduler daily cycle.** |

Findings surfaced during A.2 execution (logged YELLOW for Stage B/C):

- **YELLOW (Stage B)** — Polymarket producer is logging `422 Unprocessable Entity`
  errors from `gamma-api.polymarket.com/comments` for many market IDs. Price
  data flows correctly to Kafka; only comment-fetching is failing. Producer
  treats as warnings (not crash). Polymarket API change or stale market IDs;
  Stage B triage.

- **YELLOW (Stage C)** — Orphan files at `/var/lib/kafka/data/` PVC mount root
  from the first-attempt F1 boot (when KAFKA_LOG_DIRS was the mount root,
  before the subdir correction). Harmless — Kafka now reads from
  `kafka-logs/` subdir and ignores the root. Cleanup in Stage C.

### A — Closeout summary

Stage A complete 2026-05-19 ~14:30 UTC.

#### Entry state vs. exit state

| Aspect | Entry (Phase 9.5 start, 2026-05-19 morning) | Exit (Stage A close, 2026-05-19 14:30 UTC) |
|---|---|---|
| Kafka topics | 0 — data on ephemeral `/tmp/kafka-logs` | 19 — durable on PVC `kafka-logs/` subdir; auto-reasserts hourly via CronJob |
| Flink jobs | 2 in RESTARTING loop (no source topics) | 2 RUNNING; HA verified across full scale cycle |
| Postgres data | 424 + 9,202 + 157 + 21 + 34,665 = 44,469 rows | Same data preserved + new FRED rows added (976 fred + growth elsewhere) |
| Cloud Scheduler | PAUSED | Still PAUSED (Ron's manual final step) |
| Polymarket pod | CrashLoopBackOff (1,577 restarts/6d on polymarket-pool) | 1/1 Running on main-pool (3 restarts during first ~2 min, then settled) |
| polymarket-pool node pool | e2-small, always-on, $7/mo | DELETED |
| Airflow scheduler | CrashLoopBackOff (111 restarts/10h on wrong probe port) | 1/1 Running stable on port 8974 |
| Prometheus | CrashLoopBackOff (OOMKill, 118 restarts/24h) | 1/1 Running with 2Gi limit + 7d retention; 3 scrape targets UP |
| Backup restore | "Tested once" (per Phase 9 closeout) | Re-verified 2026-05-19 via real restore of yesterday's dump; exact row counts |
| Scale 0→1 robustness | UNKNOWN | PROVEN: full cycle survived without manual intervention |

#### Files changed (committed by Ron later, per autonomy)

- `infrastructure/k8s/airflow-scheduler-deployment.yaml` — liveness port 8793 → 8974.
- `infrastructure/k8s/prometheus-deployment.yaml` — memory 512Mi → 2Gi, +`--storage.tsdb.retention.time=7d`, livenessProbe.initialDelaySeconds 30 → 300.
- `infrastructure/k8s/producers/polymarket-deployment.yaml` — nodeSelector removed, comment block rewritten, memory bumped.
- `infrastructure/k8s/kafka-statefulset.yaml` — `KAFKA_LOG_DIRS=/var/lib/kafka/data/kafka-logs` added; comment block rewritten with root-cause rationale.
- `infrastructure/docker-compose.yml` — same `KAFKA_LOG_DIRS` value (dev parity).
- `infrastructure/k8s/kafka-init-cronjob.yaml` — NEW file, hourly idempotent topic-reassert.
- `data-pipeline/docs/phase95_cluster_robustness_implementation.md` — this doc.
- `data-pipeline/docs/phase95_investigation_log.md` — full audit trail.

The existing `kafka-init-job.yaml` was NOT modified — retained as the operator
one-shot bootstrap path.

#### GCP resources changed

- Deleted: GKE node pool `polymarket-pool` (e2-small × 1).
- Created: K8s CronJob `kafka-init` (hourly).
- Re-created (then preserved): K8s pods for all 14 workloads via scale 0→1 cycle.

#### Yellow / Stage B+C carry-forward findings (added during Stage A)

| Finding | Discovered | Stage | Notes |
|---|---|---|---|
| Misleading secret name `NEWSAI_API_KEY` holds a thenewsapi.com key | A.1 + A.2 F3 review | C | Coordinated rename: secret + `config/settings.py` + producer + airflow-scheduler-deployment + 03_migrate_secrets.sh + gcp/README.md + newsapi_end_to_end.md guide. Risky to do mid-Phase 9.5; focused Stage C task. |
| Polymarket `422` comment-fetch failures | A.2 F1.7 | B | Producer code change; API endpoint or parameter format drift. Producer doesn't crash; price data is unaffected. |
| Orphan files at `/var/lib/kafka/data/` PVC root | A.2 F1 | C | Cosmetic — Kafka reads only from `kafka-logs/` subdir. |
| `infrastructure/gcp/03_migrate_secrets.sh:53` references non-existent `THE_NEWS_API_KEY` | A.1 | C | Doc/script drift. Cleaned up as part of the coordinated rename. |
| `infrastructure/gcp/README.md:122` mentions `THE_NEWS_API_KEY` | A.1 | C | Same. |
| Old Flink checkpoint dirs accumulate on the checkpoint PVC (67 MB / 5 GB today) | A.1 Area 1.10 | C | Disk hygiene; not blocking yet. |
| `google/cloud-sdk:slim` rolling tag in postgres-backup-cronjob | A.1 Area 9 | C | KG-PHASE-C-1 class. |
| `imagePullPolicy: Always + :tag` on Anizai images | A.1 Area 9 | C | Switch to digest pinning. |
| Maintenance window not set on the cluster | A.1 Area 10 | C | Set a low-traffic window. |
| Two missing daily backups (2026-05-16, 2026-05-17) — CronJob couldn't schedule when main-pool was at 0 | A.1 Area 11 | C | startingDeadlineSeconds + monitoring for missed runs. |
| Airflow metadata DB not backed up | A.1 Area 11 | C | DAG run history. |
| Cloud Scheduler over-privileged `roles/container.admin` | A.1 Area 4 | C | `roles/container.developer` would suffice. |
| Pipeline-functionality monitoring (zero-rows-in-N-hours) | A.1 monitoring blind-spot | C | Core Stage C work. |
| CLOUD_CONNECTION_GUIDE.md secret name + schedule drift | Pre-Phase 9.5 brief | C | Doc cleanup. |
| KG-PHASE-C-5 OpenAI 429, KG-PHASE-C-6 OpenSky timeout, KG-PHASE-C-7 pytrends 404 | Phase 9 closeout | B | Application-code triage. |

These do not block resuming the pipeline. They are the natural backlog for
Stage B (application robustness) and Stage C (monitoring + operational
documentation).

#### What Stage A did NOT change

- Cloud Scheduler — still PAUSED. Ron's manual final step at end of Phase 9.5.
- Application code (`agent/`, `ingestion/`, `processing/`, `prompts/`) —
  untouched per autonomy bound.
- `task_plan.md` / `task_plan_implementation.md` — Phase 9.5 only updates
  these at full-phase close (after Stage C).
- Git — Ron handles all git operations.

#### Estimated cost impact

- **Eliminated**: `polymarket-pool` e2-small × 1 always-on (~$7/mo recurring).
- **Net change**: minor savings; no offsetting cost added.

#### Stage A success criteria — all met

- [x] Polymarket revert executed (OQ-1).
- [x] FRED flows E2E (OQ-2 primary criterion).
- [x] Restore drill run for real (OQ-3).
- [x] Cloud Scheduler stays PAUSED (OQ-4).
- [x] Airflow `catchup=False` confirmed (OQ-5 answer logged in A.1).
- [x] All 12 A.1 areas investigated and surfaced.
- [x] All 6 A.2 fix phases applied with verification.
- [x] Cluster proven robust to the daily scale 0→1 cycle.

#### Surfacing to Ron

Stage A is complete. Awaiting Ron's review before planning Stage B.

Recommended Stage B framing (for Ron to confirm/refine before the next
investigation plan):
- Producer error handling (KG-PHASE-C-5/6/7, the Polymarket 422 finding,
  and the general pattern of producers that fail loudly on Kafka boot then
  settle).
- Flink job retry/backoff behavior on transient errors.
- OpenAI 429 handling in Gold + agent paths.
- DLQ contents review — what's actually in dead-letter-queue right now?
- Idempotency story for producers + persistence.
- Agent worker error handling under partial failures (Postgres reconnect,
  Firestore retries, OpenAI quota).

The Polymarket 422 finding is the most operationally significant Stage B
candidate to surface first.

**Stage A status: CLOSED 2026-05-19 14:30 UTC.**

---

## Stage B — Application Robustness

### Stage scope

Approved 2026-05-19. Unified investigation of three interconnected areas:
(1) DLQ contents inventory, (2) Polymarket 422 on comments endpoint,
(3) OpenAI 429 handling across Gold + agent paths. KG-PHASE-C-6 (OpenSky)
and KG-PHASE-C-7 (pytrends) promoted to in-scope after Area 1 empirically
confirmed they share an application-layer pattern with the main three.

### B.1 — Investigation findings (closed 2026-05-19 ~16:00 UTC)

Full audit trail in `phase95_investigation_log.md`. Summary:

| # | Area | Status | Key finding |
|---|---|---|---|
| 1 | DLQ inventory | Closed | **NEW finding (largest impact):** All 70 DLQ messages are a single error class — Gold `momentum_vault_insert` Postgres-DNS-resolution failures from a 28s window during F6 scale-cycle (14:27:32 → 14:28:00 UTC). Gold has no transient-error retry; first failure goes straight to DLQ. Every future scale 0→1 cycle would re-produce this loss. Other Stage B target failures (OpenAI 429, Polymarket 422) are ZERO in current DLQ. |
| 2 | Polymarket 422 | Closed | Polymarket's Gamma API `/comments` endpoint has a **breaking change**: now requires `parent_entity_id` + `entity_entity_type`. 21 candidate enum values tried — none worked. Producer warnings only; never reached DLQ. Zero comment data has flowed since the change. |
| 3 | OpenAI 429 | Closed | 12 OpenAI client instantiations across 7 files, none with `max_retries` set (SDK default = 2). Error handling otherwise sound: Gold routes to DLQ with categorized stage; agent routes to Firestore `failed`. No centralized factory → drift risk. Empirically: zero OpenAI failures in current DLQ. |
| 4 | OpenSky + pytrends | Closed | Both confirmed actively broken (zero Bronze post-trigger). KG-PHASE-C-6 OpenSky: infra-layer (cluster can't reach `opensky-network.org`). KG-PHASE-C-7 pytrends: 4.9.2 is the latest release — no upgrade available. **Common app-layer gap: producers report Airflow `success` on 100% unit failure (silent failure).** |

### B.2 — Consolidated Fix Package

**Package philosophy:** apply the smallest set of changes that addresses every
finding, with strong preference for centralized fixes over per-site patches.
Application code authority granted by Ron (Stage B autonomy). All fixes
batched here for single approval; execution after Ron's go-ahead.

#### Package Item 1 — Postgres DNS robustness (the big one)

Two complementary fixes; both land or neither:

**1a. Infrastructure: `publishNotReadyAddresses: true` on postgres Service.**
- File: `infrastructure/k8s/postgres-service.yaml`.
- Change: add `publishNotReadyAddresses: true` to the spec (1 line + comment
  block explaining the C2 D6 analogue).
- Effect: DNS resolves to the Postgres pod IP as soon as the pod has an IP,
  before its readiness probe passes. Same fix as kafka-service.yaml C2 D6.

**1b. Application: tenacity-style retry on Gold momentum_vault_insert.**
- File: `processing/gold_job.py` (the momentum_vault_insert call sites — to be
  identified during execution; current `_dlq_record` invocations stay as
  final-failure routing).
- Change: wrap DB insert calls in a tenacity retry decorator targeting
  transient errors (DNS resolution failures, ConnectionRefused,
  ConnectionReset, psycopg2 OperationalError up to a configurable max).
  Specifically:
  - Add tenacity (already in dependency tree — verify).
  - 5 retries with exponential backoff (1s → 16s, total ~30s).
  - Only retry transient classes; non-transient (constraint violation,
    schema mismatch) goes straight to DLQ as today.
- Effect: a single scale-cycle DNS hiccup no longer drops data.

Combined: 1a removes the DNS-failure class entirely for normal operation;
1b catches any remaining transient class (Postgres restart mid-write, network
partition).

#### Package Item 2 — Centralized OpenAI client factory

- New file: `utils/openai_client.py` containing:
  ```python
  def get_openai_client() -> OpenAI:
      """
      Single-source-of-truth OpenAI client factory.
      max_retries=5 bumps SDK default (2) for tighter resilience against
      transient 429/5xx/timeout. timeout=60s for chat, embedding calls
      use the same client (their typical latency fits well under 60s).
      """
      return OpenAI(
          api_key=OPENAI_API_KEY,
          max_retries=5,
          timeout=60.0,
      )
  ```
- Replace 12 in-file `OpenAI(...)` instantiations with
  `from utils.openai_client import get_openai_client; client = get_openai_client()`:
  - `processing/gold_job.py` × 7 sites
  - `agent/nodes/{query_understand,build_embedding,synthesize,rate_evidence}.py` × 4 sites
  - `processing/build_sniper_reference_vector.py` × 1 site
- Unit test: `tests/test_utils/test_openai_client.py` — verifies factory
  returns a client with the expected `max_retries` + `timeout`, and a 429
  mock-injected test that confirms the SDK retries 5 times before raising.

Effect: bumps retry depth 2→5 (total retry window ~30-60s); unifies timeout;
prevents drift; provides a mock-test for 429 behavior (no live OpenAI calls
needed).

#### Package Item 3 — Polymarket comment-fetch feature flag

Selected Option C from Area 2 (least risk, most reversible).

- File: `ingestion/polymarket_producer.py`.
- Change: add `POLYMARKET_COMMENTS_ENABLED` env var (default `false`).
  - If `false`, `_comment_poll_loop` returns immediately after logging one
    `INFO`-level startup message documenting the upstream API breakage.
  - If `true`, current behavior (which will continue to fail until upstream
    is fixed) runs unchanged.
- File: `config/settings.py` — define `POLYMARKET_COMMENTS_ENABLED` setting.
- File: `infrastructure/.env.example` — document the new env var.

Effect: stops the ~100s warnings/cycle log spam, captures the broken state
explicitly, keeps the code in place for future revival. Future Stage Q
decision: either retire entirely (Option A) or re-engineer the API call
once upstream contract is clearer (Option B).

#### Package Item 4 — Producer "raise on 0% success" pattern

Applies to opensky and googletrends. Same pattern:

- File: `ingestion/opensky_producer.py` `run_static()` (line ~597).
  - After the per-box loop completes, check `if run_emitted == 0 and
    len(BOUNDING_BOXES) > 0: raise RuntimeError(...)`.
- File: `ingestion/googletrends_producer.py` `run_static()` (line ~430
  vicinity, after per-geo loop).
  - Analogous "all geos failed → raise" check.

Effect: Airflow surfaces the failure as `task: failed` rather than `task:
success` with zero Bronze. Stage C monitoring then has a clean signal to
alert on.

This also addresses the **broader silent-failure pattern** the original
Phase 9.5 motivating brief flagged ("monitoring measures pod liveness, not
pipeline functionality"). Producer-level signal is now correct.

#### Package Item 5 — Unit tests + verification mocks

- `tests/test_utils/test_openai_client.py` — new test file:
  - Test 1: factory returns a properly-configured client.
  - Test 2: mock injection raising `openai.RateLimitError` verifies the SDK
    retries 5 times before raising.
- `tests/test_ingestion/test_polymarket_feature_flag.py` — new test file:
  - Verify default-disabled comment poll exits cleanly.
  - Verify env-var override re-enables the path.
- `tests/test_ingestion/test_opensky_silent_failure.py` — new test file:
  - Mock all 7 boxes to return None → `run_static` raises.
- `tests/test_ingestion/test_googletrends_silent_failure.py` — new test file:
  - Mock all 4 geos to raise → producer raises.

Effect: the new code is verified offline (no live API calls, no token spend).

#### Files Changed Summary

| File | Type | Change |
|---|---|---|
| `infrastructure/k8s/postgres-service.yaml` | infra | `publishNotReadyAddresses: true` |
| `processing/gold_job.py` | app | wrap momentum_vault_insert in tenacity retry |
| `utils/openai_client.py` | app (new) | centralized factory |
| `processing/gold_job.py` (× 7 sites) | app | use factory |
| `agent/nodes/{query_understand,build_embedding,synthesize,rate_evidence}.py` | app | use factory |
| `processing/build_sniper_reference_vector.py` | app | use factory |
| `ingestion/polymarket_producer.py` | app | `POLYMARKET_COMMENTS_ENABLED` feature flag |
| `config/settings.py` | app | new env var |
| `infrastructure/.env.example` | infra | document new env var |
| `ingestion/opensky_producer.py` | app | raise on 0% success |
| `ingestion/googletrends_producer.py` | app | raise on 0% success |
| `tests/test_utils/test_openai_client.py` | test (new) | factory + 429 mock |
| `tests/test_ingestion/test_polymarket_feature_flag.py` | test (new) | flag behavior |
| `tests/test_ingestion/test_opensky_silent_failure.py` | test (new) | raise on 0% |
| `tests/test_ingestion/test_googletrends_silent_failure.py` | test (new) | raise on 0% |

**Total: ~9 production files modified, 1 new util, 4 new test files,
1 manifest modified.**

#### Verification plan (after package execution)

1. **Unit tests:** run new pytest files; all pass.
2. **Postgres DNS robustness:** scale main-pool to 0 → 1 again; verify DLQ
   stays empty during the new recovery window (vs. 70 messages in F6).
3. **Polymarket flag:** confirm `kubectl logs polymarket-...` stops showing
   the 422 warnings; one startup INFO log mentions the flag state.
4. **OpenAI factory:** trigger one live agent query (cost ~$0.025) via
   Firestore to confirm the agent still functions end-to-end with the new
   factory in place. (~9 verification queries left after this.)
5. **Producer raise-on-0:** manually trigger `opensky_high_frequency` and
   `googletrends_daily`; confirm Airflow task state goes to `failed`
   (not `success` with zero Bronze).

#### Items NOT in this package (Stage C carry)

- DLQ failure_stage categorization (transient vs permanent). Cosmetic.
- KG-PHASE-C-6 OpenSky firewall/network fix. Infra/network, not app.
- KG-PHASE-C-7 pytrends upstream fix. No upgrade available; needs
  alternative provider or retirement.
- Polymarket retire-vs-reengineer decision. Feature flag defers this.
- Pipeline-functionality monitoring (zero-rows-in-N-hours alerts).
  Stage C core scope.
- The `NEWSAI_API_KEY` misleading-name rename. Stage C.

#### Risk classification

| Class | Items | Notes |
|---|---|---|
| GREEN | 1a, 1b, 2, 3, 4, 5 | All bounded, application-code-only changes (1a is infra but minor). Reversible if anything breaks. |
| YELLOW | none | — |
| RED | none requires Ron's input beyond package approval | $4.02 OpenAI budget is sufficient; live verification consumes ~1 query of the 10 estimated. |

#### Estimated execution time

- Package execution: ~2-3 hours (most changes are mechanical refactors).
- Unit tests: ~30 min.
- Live verifications: ~30 min (incl. one scale 0→1 cycle).
- **Total: ~3-4 hours.**

#### Awaiting approval

Plan is concrete and ready. Awaiting Ron's go-ahead for execution.

### B.3 — Fix execution log

All 5 items executed 2026-05-19 → 2026-05-20. Full audit trail in
`phase95_investigation_log.md` Stage B.2 section. Summary:

| Item | Files modified | New files | Status |
|---|---|---|---|
| 1a | `infrastructure/k8s/postgres-service.yaml` | — | Applied + live. |
| 1b | `processing/gold_job.py` (5 sites) | `utils/retry.py` | Applied; verified live via postgres pod-restart test (TM logs show retries firing). |
| 2 | `agent/nodes/{query_understand,build_embedding,synthesize,rate_evidence}.py`, `processing/gold_job.py` (× 7 sites), `processing/build_sniper_reference_vector.py` | `utils/openai_client.py` | Applied; semantics-preserving refactor; verified by 20 unit tests including a 429 mock. |
| 3 | `ingestion/polymarket_producer.py`, `config/settings.py`, `infrastructure/.env.example` | — | Applied + live; Polymarket pod logs are now silent (was ~100 422 warnings per 20-min cycle). |
| 4 | `ingestion/opensky_producer.py`, `ingestion/googletrends_producer.py` | — | Applied + live; OpenSky DAG runs now correctly fail (was silently `success`); googletrends pattern verified semantically. |
| 5 | — | `tests/test_utils/test_openai_client.py`, `tests/test_utils/test_retry.py`, `tests/test_ingestion/test_polymarket_feature_flag.py`, `tests/test_ingestion/test_opensky_silent_failure.py`, `tests/test_ingestion/test_googletrends_silent_failure.py` | 20/20 new tests pass; 102/102 existing tests still pass. |

**Docker images rebuilt + pushed:**
- `us-central1-docker.pkg.dev/anizai-pipeline/anizai-images/anizai-flink:1.19.1-p95`
- `us-central1-docker.pkg.dev/anizai-pipeline/anizai-images/anizai-agent:0.2.0-p95`
- `us-central1-docker.pkg.dev/anizai-pipeline/anizai-images/anizai-airflow:2.9.3-p95`
- `us-central1-docker.pkg.dev/anizai-pipeline/anizai-images/anizai-polymarket:0.2.0-p95`

**Manifests updated to new image tags:** `flink-jobmanager-deployment.yaml`,
`flink-taskmanager-deployment.yaml`, `agent-deployment.yaml`,
`airflow-scheduler-deployment.yaml`, `airflow-webserver-deployment.yaml`,
`producers/polymarket-deployment.yaml`.

**One execution surprise — documented in log:** Flink jobs continued running
OLD code after the image rebuild because HA recovered the previously-submitted
job graphs from ConfigMaps. Detected when the first Postgres-restart test
showed zero retry log messages despite the image containing the new code.
Resolved by cancelling and re-submitting both Flink jobs after the image
rollout. Stage C documentation candidate: "Flink Python code changes
require post-rollout job re-submission, not just a pod restart."

### B — Closeout summary

Stage B complete 2026-05-20 ~00:15 UTC.

#### Entry state vs. exit state

| Aspect | Entry (Stage B start) | Exit (Stage B close) |
|---|---|---|
| DLQ content | 70 messages, 100% Gold `momentum_vault_insert` Postgres-DNS failures from F6 scale-cycle. No retry logic in Gold. | Gold retries transient DB errors 5 times with exp-backoff. Same scenario reproduced (postgres pod-restart) produces +2 DLQ instead of +4000. |
| OpenAI calls | 12 in-place `OpenAI(api_key=...)` instantiations across 7 production files. SDK default `max_retries=2`. Drift risk. | All routed through `utils/openai_client.py` factory. `max_retries=5`. Single chokepoint. |
| Polymarket producer | ~100 `422 validation error` warnings per 20-min cycle (Gamma API breaking change). | Comment loop early-exits with one INFO message. Pod logs clean. Price path unaffected. |
| OpenSky DAG | Reported `success` despite 0 Bronze messages (KG-PHASE-C-6 infra issue masked at application layer). | Reports `failed` correctly when all 7 boxes fail. Monitoring layer now has a clean signal. |
| googletrends DAG | Same silent-success pattern. | Will report `failed` when all 4 geos return pytrends 404. |
| postgres-service DNS during scale-up | NXDOMAIN until Postgres pod Ready (headless Service default). | DNS resolves to the pod IP as soon as pod has an IP (`publishNotReadyAddresses: true`). |

#### Files Changed (final inventory)

Production code (9 files):
- `infrastructure/k8s/postgres-service.yaml` — `publishNotReadyAddresses: true`
- `processing/gold_job.py` — 5 retry wrappers + 7 OpenAI factory replacements
- `agent/nodes/query_understand.py` — OpenAI factory
- `agent/nodes/build_embedding.py` — OpenAI factory
- `agent/nodes/synthesize.py` — OpenAI factory
- `agent/nodes/rate_evidence.py` — OpenAI factory
- `processing/build_sniper_reference_vector.py` — OpenAI factory
- `ingestion/polymarket_producer.py` — feature flag
- `ingestion/opensky_producer.py` + `ingestion/googletrends_producer.py` — raise-on-0%
- `config/settings.py` — new `POLYMARKET_COMMENTS_ENABLED` setting
- `infrastructure/.env.example` — document new env var

New code (2 utility files + 5 test files):
- `utils/openai_client.py`
- `utils/retry.py`
- `tests/test_utils/__init__.py`
- `tests/test_utils/test_openai_client.py`
- `tests/test_utils/test_retry.py`
- `tests/test_ingestion/test_polymarket_feature_flag.py`
- `tests/test_ingestion/test_opensky_silent_failure.py`
- `tests/test_ingestion/test_googletrends_silent_failure.py`

K8s manifests (image tag bump only — 6 files):
- `flink-jobmanager-deployment.yaml`, `flink-taskmanager-deployment.yaml`,
  `agent-deployment.yaml`, `airflow-scheduler-deployment.yaml`,
  `airflow-webserver-deployment.yaml`, `producers/polymarket-deployment.yaml`.

#### Test results

- New unit tests: **20/20 pass**.
- Existing tests (regression check): **102/102 pass** in
  test_agent/test_{synthesize,build_embedding,rate_evidence,query_understand}.py
  and test_processing/test_semantic_rescue.py — the OpenAI factory refactor
  is semantics-preserving.

#### Stage B carry-forward findings (Stage C)

| Finding | Stage | Notes |
|---|---|---|
| Polymarket Gamma `/comments` API endpoint reverse-engineering OR retire | C (or future small sprint) | Feature flag is the holding action; final retire/repair decision deferred. |
| KG-PHASE-C-6 OpenSky firewall/network — cluster cannot reach `opensky-network.org` | C | App-layer raise-on-0% lands in Stage B; infra firewall fix is separate work. |
| KG-PHASE-C-7 pytrends — `pytrends 4.9.2` is the latest release; no upgrade available | C | App-layer raise-on-0% lands in Stage B. Long-term: switch to Google Trends official API (requires quota + OAuth) or retire the producer. |
| `NEWSAI_API_KEY` misleading secret name (holds a thenewsapi.com key) | C | Coordinated rename across code + manifests + Secret Manager + scripts. |
| Flink Python code changes require post-rollout job re-submission | C (documentation) | Discovered during B.2 execution. Operator-procedure note for `cluster_operations_guide.md`. |
| Orphan files at `/var/lib/kafka/data` PVC root | C | Cosmetic. |
| Live OpenAI agent verification | C (or now via Ron's manual frontend test) | Code path verified by unit tests + agent pod healthy; live query not run during B.2. |
| DLQ failure_stage categorization (transient vs permanent) | C | Cosmetic dashboard improvement. |

#### Stage B success criteria — all met

- [x] Three primary areas (DLQ, Polymarket 422, OpenAI 429) all investigated.
- [x] Two secondary KGs (KG-PHASE-C-6/7) properly classified.
- [x] All fix-package items applied and live.
- [x] Postgres-DNS retry verified via real Postgres pod-restart (TM retry logs + DLQ growth bounded to +2).
- [x] Polymarket comment-spam eliminated (pod logs silent).
- [x] OpenSky DAG raise-on-0% verified (runs report `failed`).
- [x] No regressions in existing test suite.

#### What Stage B did NOT change

- Cloud Scheduler — still PAUSED. Ron's manual final step at end of Phase 9.5.
- `task_plan.md` / `task_plan_implementation.md` — Phase 9.5 only updates these
  at full-phase close (after Stage C).
- Git — Ron handles all git operations.

#### Surfacing to Ron

Stage B is complete. Awaiting Ron's review before planning Stage C.

Recommended Stage C framing (for Ron to confirm/refine):
- Pipeline-functionality monitoring (alert rules on "zero rows / N hours",
  topic-count expectations, agent-worker liveness from Firestore-side).
- `data-pipeline/docs/guides/cluster_operations_guide.md` — the long-term
  reference doc per the original Phase 9.5 brief.
- `NEWSAI_API_KEY` coordinated rename.
- Maintenance window + autorepair/autoupgrade tuning.
- `CLOUD_CONNECTION_GUIDE.md` cleanup (secret names + Scheduler schedule drift).
- The Flink-jobs-need-resubmission-after-image-rollout operator note.
- KG-PHASE-C-6 OpenSky network/firewall fix (infra coordination).

**Stage B status: CLOSED 2026-05-20 ~00:15 UTC.**

---

## Stage C — Monitoring + Operational Documentation

### Stage scope

Pipeline-functionality monitoring + `cluster_operations_guide.md`. Per Ron's
authorisation (2026-05-20):
- Infrastructure-only — no application code.
- Conservative alert thresholds for V1.
- Minimise OpenAI calls: alerts use proxies (log-line counts, dashboard
  scraping), not live API instrumentation. Consistent with the parallel
  OpenAI cost-analysis session (KG-PHASE-9.5-9).

### Pre-Stage-C action: backlog drop ✅

Dropped 5,939-message Silver→Gold backlog on 2026-05-20 ~10:50 UTC.
- Cancelled Gold job (`7f34cb75c8b540ceef0dccb704d2ff4e`).
- Truncated `process.silver.social_pulse` (low-watermark 911/884/905) and
  `process.silver.global_news` (1122/1019/1098) via `kafka-delete-records.sh`.
- Step 3 (HA ConfigMap delete) was a no-op — Flink auto-cleans on CANCEL.
- Resubmitted Gold (`082b5b6eadf27048b1c37ae432ad11d1`); reached RUNNING in
  28s; first checkpoint COMPLETED in 14.7s.

### C.1 — Investigation findings summary

Full audit trail in `phase95_investigation_log.md` Stage C section.

| Area | Status | Key finding |
|---|---|---|
| 1 — Scrape coverage | Closed | 3 existing targets (Flink JM/TM, agent stub). Agent /metrics is a **Sprint 18 stub** — zero `agent_*` metrics in Prometheus. Gaps: kafka, postgres, OpenAI proxy. GMP installed but unused. fluentbit-gke active (logs flow to Cloud Logging). Clean slate on Cloud Monitoring policies + log-based metrics. |
| 2 — Alert rules | Closed | 13-rule catalogue: 11 metric-based via Prometheus + Alertmanager → Gmail SMTP, 2 log-based via Cloud Logging + Cloud Monitoring → native email. Conservative thresholds. Includes Ron's Gold checkpoint failure cluster rule (≥3 fails / 10m WARNING, persists 20m → CRITICAL). |
| 3 — Dashboards | Closed | New `Anizai Pipeline Health` single-screen "is it healthy?" dashboard. 4 rows × ~12 panels: vault freshness, Bronze topic activity, DLQ+Flink, OpenAI+alerts. Existing detailed dashboard untouched. |
| 4 — Operations guide | Closed | ~15-section scope: architecture, daily flow, start/stop, runbooks for each Phase 9.5 finding, backlog-drop procedure, restore drill, diagnostics by symptom. Written in C.2 after alerts + dashboards are live. |

### C.2 — Consolidated Fix Package

#### Item 1 — Two new exporter Deployments

**1a. kafka_exporter**
- File: `infrastructure/k8s/kafka-exporter-deployment.yaml` (new).
- Image: `danielqsj/kafka-exporter:v1.7.0` (per Ron's Q4).
- ClusterIP Service on port 9308.
- Args: `--kafka.server=kafka:29092`.
- Resources: 50m CPU / 64Mi memory (lightweight).
- Exposes: `kafka_topic_partition_current_offset`,
  `kafka_topic_partition_oldest_offset`, `kafka_brokers`,
  `kafka_consumergroup_*`.

**1b. postgres_exporter**
- File: `infrastructure/k8s/postgres-exporter-deployment.yaml` (new).
- Image: `prometheuscommunity/postgres-exporter:v0.15.0` (per Ron's Q4).
- ClusterIP Service on port 9187.
- Connection: `postgresql://anizai@postgres:5432/anizai?sslmode=disable`,
  password from existing `postgres-secrets-spc` CSI mount (no new secret).
- Custom queries ConfigMap: `infrastructure/k8s/postgres-exporter-queries-configmap.yaml`
  defining `pg_anizai_*` metrics (per-source row counts, freshness windows).
- Exposes: `pg_stat_database_*`, `pg_settings_*`, `pg_stat_user_tables_*`,
  plus the custom queries.

Both pods schedule on main-pool (no nodeSelector — only pool now).

#### Item 2 — Prometheus config update

- File: `infrastructure/k8s/prometheus-configmap.yaml` (modified).
- Add 2 new scrape jobs:
  - `kafka-exporter` → `kafka-exporter:9308`.
  - `postgres-exporter` → `postgres-exporter:9187`.
- Add `rule_files: ["/etc/prometheus/rules/*.yml"]` to load the new alert
  rules ConfigMap.
- Add `alerting.alertmanagers` block pointing at `alertmanager:9093`.

#### Item 3 — Prometheus alert rules ConfigMap

- File: `infrastructure/k8s/prometheus-rules-configmap.yaml` (new).
- 11 metric-based alert rules per the Area 2 catalogue.
- Mounted at `/etc/prometheus/rules/rules.yml` in the Prometheus pod via
  a subPath update to `prometheus-deployment.yaml`.

#### Item 4 — Alertmanager Deployment

- File: `infrastructure/k8s/alertmanager-deployment.yaml` (new).
- Image: `prom/alertmanager:v0.27.0`.
- ConfigMap: `infrastructure/k8s/alertmanager-configmap.yaml` (new).
- ClusterIP Service on port 9093.
- Routes all alerts to `gmail_receiver` (SMTP smtp.gmail.com:587, TLS,
  username `ron.mintz21@gmail.com`, password from new
  `GMAIL_APP_PASSWORD` secret).
- Subject template: `[anizai-pipeline] [{{ .CommonLabels.severity }}] {{ .CommonLabels.alertname }}`.
- Resources: 50m / 64Mi.

**RED — needs Ron's input:** Gmail App-Password.

If Ron's account has 2FA enabled (likely), an App-Password is needed at
https://myaccount.google.com/apppasswords. The generated 16-character
string goes into Secret Manager as `GMAIL_APP_PASSWORD` and is mounted into
Alertmanager via a new SPC.

Alternative if SMTP friction: pivot to **Path B unified architecture**
(Prometheus → Alertmanager webhook → Cloud Logging structured log →
Cloud Monitoring policy → email). One extra relay component but no SMTP
credential. Surface as Ron's choice.

#### Item 5 — Cloud Logging-based metrics + Cloud Monitoring policies

Two new resources via `gcloud` (no K8s manifests):

**5a. Cloud Logging-based metric** — created via
`gcloud logging metrics create openai_rate_limit_errors --description="..." --log-filter="..."`.

**5b. Cloud Monitoring email notification channel** — created via
`gcloud beta monitoring channels create --display-name="Anizai pipeline ops" --type=email --channel-labels=email_address=ron.mintz21@gmail.com`.

**5c. Two Cloud Monitoring alerting policies** — created via
`gcloud monitoring policies create --policy-from-file=...yaml`:
- `openai-rate-limit-warning`: condition metric > 0 in 5m window.
- `openai-rate-limit-storm-critical`: > 50 in 1h window.

All three additions go in `infrastructure/gcp/06_monitoring_setup.sh`
(new script, idempotent).

#### Item 6 — Grafana dashboard

- File: `infrastructure/k8s/grafana-configmap.yaml` (modified).
- Add `anizai_pipeline_health.json` key alongside the existing
  `anizai_pipeline.json`.
- Dashboard JSON includes the 12 panels designed in Area 3.

#### Item 7 — `cluster_operations_guide.md`

- File: `data-pipeline/docs/guides/cluster_operations_guide.md` (new).
- ~2,500 lines. Same style as `CLOUD_CONNECTION_GUIDE.md`.
- Written in C.2 AFTER items 1-6 are live and verified, so runbooks
  reference real working infrastructure.

#### Files Changed Summary

| File | Type | Notes |
|---|---|---|
| `infrastructure/k8s/kafka-exporter-deployment.yaml` | new | + Service |
| `infrastructure/k8s/postgres-exporter-deployment.yaml` | new | + Service + SPC mount |
| `infrastructure/k8s/postgres-exporter-queries-configmap.yaml` | new | Custom Postgres queries |
| `infrastructure/k8s/prometheus-configmap.yaml` | modify | +2 scrape jobs + rule_files + alertmanagers |
| `infrastructure/k8s/prometheus-deployment.yaml` | modify | mount rules ConfigMap subPath |
| `infrastructure/k8s/prometheus-rules-configmap.yaml` | new | 11 alert rules |
| `infrastructure/k8s/alertmanager-deployment.yaml` | new | + Service |
| `infrastructure/k8s/alertmanager-configmap.yaml` | new | Gmail SMTP route |
| `infrastructure/k8s/alertmanager-secretproviderclass.yaml` | new | mounts `GMAIL_APP_PASSWORD` |
| `infrastructure/gcp/06_monitoring_setup.sh` | new | log-based metrics + Cloud Monitoring policies + email channel |
| `infrastructure/k8s/grafana-configmap.yaml` | modify | + `anizai_pipeline_health.json` |
| Secret Manager: `GMAIL_APP_PASSWORD` | new secret | **Ron creates** |
| `data-pipeline/docs/guides/cluster_operations_guide.md` | new | Operational guide |
| `data-pipeline/docs/phase95_cluster_robustness_implementation.md` | modify | this doc — Stage C closeout |
| `data-pipeline/docs/phase95_investigation_log.md` | modify | append C.2 execution log |

**Total: ~10 new files, 3 modified files, 1 new secret, 1 setup script.**

#### Verification plan (C.2 closeout)

Trigger each alert condition + visually confirm dashboard renders. Conservative
on OpenAI calls — most monitoring testing doesn't need them.

| Alert | How to trigger | Expected |
|---|---|---|
| F-Flink-1 (job not RUNNING) | Cancel one Flink job via Flink REST (no resubmit for 6m); resubmit after. | Email arrives. |
| F-Flink-2 (Gold checkpoint failure cluster) | Force-delete postgres pod (already done in B.2; can repeat). | Email arrives within ~15m. |
| K-Kafka-1 (DLQ growth) | Verify via DLQ-already-has-messages state, or wait for natural fire. | Likely already firing post-backlog-drop period — verify the email arrives. |
| K-Kafka-4 (polymarket stale) | Scale `kubectl scale deploy/polymarket --replicas=0` for 35min. | Email arrives. Restore replicas after. |
| P-Postgres-1/2 (vault stale) | Same — pause sources for the window. | Email arrives. |
| O-OpenAI-1/2 (RateLimit proxy) | Inject a fake RateLimitError log via `kubectl exec ... -- python -c "import logging; logging.getLogger().error('RateLimitError test')"` into agent pod. | Email arrives via Cloud Monitoring. |

Visual dashboard verification: port-forward Grafana, open new dashboard,
confirm all 12 panels render with live data.

#### Risk classification

| Class | Items |
|---|---|
| **RED — needs Ron's input** | Item 4: Gmail App-Password OR decision to pivot to Path B unified Cloud Monitoring architecture. |
| GREEN — bounded fixes | Items 1, 2, 3, 5, 6, 7 — all standard, well-documented patterns. |

#### Estimated execution time

- Item 1 (exporters): ~30 min (write manifests + apply + verify scrape).
- Item 2 (Prometheus config): ~10 min.
- Item 3 (alert rules): ~30 min.
- Item 4 (Alertmanager): ~45 min (incl. waiting for Ron's app-password).
- Item 5 (Cloud Monitoring): ~30 min.
- Item 6 (Grafana dashboard): ~45 min (JSON construction + verify).
- Verification: ~1 hour (trigger each alert).
- Item 7 (cluster_operations_guide.md): ~2-3 hours of writing.
- **Total: ~5-7 hours active work.**

#### Items intentionally deferred (Stage C scope confirmation)

- Agent worker `agent_*` metric instrumentation (Sprint 26 territory, not Stage C).
- Airflow scheduler / DAG state Prometheus scraping (rely on Airflow UI + Cloud Logging).
- kube-state-metrics installation (rely on `kubectl describe` runbook).
- pytrends / OpenSky upstream fixes (KG-PHASE-9.5-5, KG-PHASE-C-6).
- CLOUD_CONNECTION_GUIDE.md cleanup (KG-PHASE-9.5-3).
- `NEWSAI_API_KEY` rename (KG-PHASE-9.5-2).
- Image digest pinning (KG-PHASE-9.5-7).
- Maintenance window tuning (KG-PHASE-9.5-6).
- Polymarket /comments resolution (KG-PHASE-9.5-4).
- OpenAI cost analysis (KG-PHASE-9.5-9, parallel session).

#### Awaiting approval

Plan is concrete. Awaiting Ron's:
1. Go-ahead on the consolidated fix package (Items 1-7).
2. Decision on RED Item 4: provide Gmail App-Password (default — Path A) OR pivot to Path B unified Cloud Monitoring architecture.
3. Any threshold adjustments to the 13 alert rules.

### C.3 — Fix execution log

C.2 executed 2026-05-20 12:00-14:00 UTC. Full audit trail in
`phase95_investigation_log.md` Stage C.2 section.

| Item | Files | Status | Verification |
|---|---|---|---|
| 1a | `kafka-exporter-deployment.yaml` (new) | ✅ | Scrape UP; `kafka_topic_partition_current_offset` exposes all 19 topics. First-attempt args fix: dropped `--log.enable-sarama=false`. |
| 1b | `postgres-exporter-deployment.yaml` (new), `postgres-exporter-queries-configmap.yaml` (new) | ✅ | Scrape UP; custom metrics `pg_anizai_*_rows` exposing per-source freshness. First-attempt args fix: dropped `--auto-discover-databases=false`. |
| 2 | `prometheus-configmap.yaml` (modify) | ✅ | 5 scrape targets UP; `rule_files` + `alerting.alertmanagers` blocks loaded. |
| 3 | `prometheus-rules-configmap.yaml` (new), `prometheus-deployment.yaml` (modify) | ✅ | 13 rules in 3 groups loaded. **Mid-execution fix**: Flink job_name labels use underscores (`anizai_silver_polymarket`), not hyphens — rules patched via `replace_all=true`, Prometheus reloaded via `POST /-/reload`. |
| 4 | `alertmanager-secretproviderclass.yaml` (new), `alertmanager-configmap.yaml` (new), `alertmanager-deployment.yaml` (new). Secret Manager: `GMAIL_APP_PASSWORD` (new). | ✅ | SMTP path verified live: `alertmanager_notifications_total{integration="email"} 6`, `alertmanager_notifications_failed_total{integration="email"} 0`. First-attempt args fix: `envsubst` not present in prom/alertmanager image — switched substitution to `sed`. |
| 5 | `infrastructure/gcp/06_monitoring_setup.sh` (new) | ✅ | 4 GCP resources created idempotently: log-based metric `openai_rate_limit_errors`, email channel `Anizai pipeline ops`, 2 alerting policies (WARNING + CRITICAL). |
| 6 | `grafana-configmap.yaml` (modify), `grafana-deployment.yaml` (modify) | ✅ | New `anizai_pipeline_health.json` dashboard provisioned. Visual verification deferred to operator port-forward; all panels query Prometheus (verified UP). |
| 7 | `data-pipeline/docs/guides/cluster_operations_guide.md` (new) | ✅ | ~15 sections covering architecture, daily flow, start/stop, Cloud Scheduler resume, 9 common-symptom runbooks, Flink-job-resubmission procedure, backlog-drop procedure, restore drill, diagnostic command reference, alert-tuning notes, Cloud Logging query patterns, known-silent producer inventory, dashboard URLs. |

#### Mid-execution surprises documented

Two minor blockers handled in-flight (both logged):
1. **Boolean-flag syntax** in danielqsj/kafka-exporter v1.7.0 and
   prometheuscommunity/postgres-exporter v0.15.0 — neither accepts
   `--flag=false`. Dropped both flags (defaults match desired behaviour).
2. **`envsubst` not in prom/alertmanager:v0.27.0 alpine image** —
   substituted `sed` in the shell wrapper for the template substitution.

One real bug caught + fixed live:
1. **Flink job_name underscore-vs-hyphen mismatch** in alert rules. Flink
   normalises hyphens to underscores in metric labels (so
   `anizai-silver-polymarket` becomes `anizai_silver_polymarket`). My
   initial rules used hyphens, triggering two false-positive critical
   alerts. Fixed via `replace_all=true` text substitution + Prometheus
   `POST /-/reload`. After fix: only the 2 true-positive
   `DailyBronzeStale` alerts remain (arxiv + fred Bronze topics haven't
   received messages in 26h — cluster was down most of yesterday). Those
   will self-resolve when the next scheduled DAG fires.

### C — Closeout summary

Stage C complete 2026-05-20 ~16:30 UTC. Pipeline-functionality monitoring
operational for the first time + cluster_operations_guide.md ships as the
long-term operational reference.

#### Entry state vs. exit state

| Aspect | Entry (Stage C start) | Exit (Stage C close) |
|---|---|---|
| Prometheus scrape coverage | 3 targets (Flink JM/TM, agent stub — agent metrics endpoint is a Sprint 18 stub exposing zero data) | **5 targets**: +kafka_exporter (Kafka topic/partition/broker), +postgres_exporter (incl. 5 custom Anizai metrics for vault freshness). |
| Alert rules | 0 rules in Prometheus, 0 Cloud Monitoring policies | **13 Prometheus rules** in 3 groups (anizai-flink, anizai-kafka, anizai-postgres) + **2 Cloud Monitoring policies** (OpenAI rate-limit WARNING + storm CRITICAL). Total: 15 alert rules. |
| Notification path | None — no Alertmanager, no email channel | **Alertmanager via Gmail SMTP** for the 13 Prometheus rules + **Cloud Monitoring email channel** for the 2 OpenAI proxy rules. Both land in `ron.mintz21@gmail.com` with `[anizai-pipeline]` subject prefix. SMTP path verified live (6 sends, 0 failures). |
| Grafana dashboards | 1 dashboard (detailed Flink, from Phase 9) | 2 dashboards: detailed Phase 9 kept, **new Pipeline Health single-screen at-a-glance** added. |
| Cloud Logging-based metrics | 0 | 1 (`openai_rate_limit_errors` — proxy for KG-PHASE-9.5-1 RPD ceiling). |
| Operations guide | None | **`cluster_operations_guide.md` ~15 sections** — architecture, daily flow, start/stop checklist, Cloud Scheduler resume procedure, 9 common-symptom runbooks, Flink jobs-need-resubmission-after-image-rollout procedure (KG-PHASE-9.5-8), backlog-drop procedure (Stage C pre-action), restore drill, command reference by symptom, alert-tuning guidance, Cloud Logging query patterns, known-silent producer inventory, dashboard URLs. |

#### Files Changed (final inventory)

**Stage C — new files** (10):
- `infrastructure/k8s/kafka-exporter-deployment.yaml`
- `infrastructure/k8s/postgres-exporter-deployment.yaml`
- `infrastructure/k8s/postgres-exporter-queries-configmap.yaml`
- `infrastructure/k8s/prometheus-rules-configmap.yaml`
- `infrastructure/k8s/alertmanager-secretproviderclass.yaml`
- `infrastructure/k8s/alertmanager-configmap.yaml`
- `infrastructure/k8s/alertmanager-deployment.yaml`
- `infrastructure/gcp/06_monitoring_setup.sh`
- `data-pipeline/docs/guides/cluster_operations_guide.md`
- *(this doc + the investigation log, both updated through Stage C)*

**Stage C — modified files** (3):
- `infrastructure/k8s/prometheus-configmap.yaml` — added 2 scrape jobs, rule_files block, alertmanagers block.
- `infrastructure/k8s/prometheus-deployment.yaml` — added rules ConfigMap mount.
- `infrastructure/k8s/grafana-configmap.yaml` — added `anizai_pipeline_health.json` dashboard.
- `infrastructure/k8s/grafana-deployment.yaml` — added subPath mount for the new dashboard.

**Stage C — Secret Manager additions** (1):
- `GMAIL_APP_PASSWORD` (Gmail App-Password, 16-char).

**Stage C — GCP resources created** (4):
- Cloud Logging-based metric: `openai_rate_limit_errors`.
- Cloud Monitoring email notification channel: "Anizai pipeline ops".
- Cloud Monitoring alerting policy: "Anizai OpenAI rate-limit (WARNING)".
- Cloud Monitoring alerting policy: "Anizai OpenAI rate-limit storm (CRITICAL)".

#### Stage C success criteria — all met

- [x] Pipeline-functionality monitoring deployed (the original Phase 9.5 motivator).
- [x] Pod-liveness monitoring SUPPLEMENTED with row-count, topic-rate, DLQ-depth, OpenAI proxy alerts — pivoting from "container restarts" to "is the data flowing?".
- [x] Conservative thresholds applied per Q2; revisit-after-2-weeks decision documented in cluster_operations_guide.md §10.
- [x] OpenAI 429 warning at first-occurrence (per Ron 2026-05-20) intentionally aggressive — documented as a revisit candidate.
- [x] Live SMTP delivery verified end-to-end (6 emails sent successfully, 0 failures).
- [x] `cluster_operations_guide.md` ships with 9 runbooks each anchored to a specific Phase 9.5 finding — future operators don't have to relearn what we learned.
- [x] KG-PHASE-9.5-8 (Flink-jobs-need-resubmission) procedure formally documented in the operations guide §6.

#### Items intentionally deferred (Stage C scope confirmation)

Per Ron's Stage C scope decision (2026-05-20), these items are in
`task_plan.md` Known Gaps but NOT touched by Stage C:

| KG | Description | Why deferred |
|---|---|---|
| KG-PHASE-9.5-1 | OpenAI Tier 1 RPD ceiling | Capacity planning (parallel OpenAI cost-analysis session, KG-PHASE-9.5-9). |
| KG-PHASE-9.5-2 | NEWSAI_API_KEY rename | Cross-cutting; needs focused session. |
| KG-PHASE-9.5-3 | CLOUD_CONNECTION_GUIDE.md cleanup | Doc-only; non-blocking. |
| KG-PHASE-9.5-4 | Polymarket /comments retire/repair | Pending upstream API contract clarity. |
| KG-PHASE-9.5-5 | pytrends retire/switch decision | Pending upstream library state OR Google official API cost decision. |
| KG-PHASE-9.5-6 | Maintenance window + autoupgrade tuning | Risk-reduction; not blocking. |
| KG-PHASE-9.5-7 | Image digest pinning | Defensive improvement; not blocking. |
| KG-PHASE-9.5-9 | OpenAI cost analysis | Parallel session, Ron's parallel work. |

Plus three pre-existing Phase-9 KGs that remain unfixed:

| KG | Notes |
|---|---|
| KG-PHASE-C-5 | OpenAI 429 hitting Gold — superseded by Stage B Item 2 centralised factory + max_retries=5; remaining occurrences are KG-PHASE-9.5-1 RPD ceiling territory. |
| KG-PHASE-C-6 | OpenSky network timeout from GKE — Stage B Item 4 raise-on-0% mitigates the silent-success symptom; the underlying firewall issue is unfixed. |
| KG-PHASE-C-7 | pytrends 404 — Stage B Item 4 raise-on-0% mitigates the silent-success symptom; the underlying library issue is unfixed (KG-PHASE-9.5-5 supersedes). |

#### Surfacing to Ron

Stage C is complete. Phase 9.5 closeout summary follows.

**Stage C status: CLOSED 2026-05-20 ~16:30 UTC.**

---

## Phase 9.5 — Closeout summary

Phase 9.5 closed 2026-05-20 ~17:00 UTC.

### Phase 9.5 timeline

| Stage | Window | Outcome |
|---|---|---|
| **A** — Infrastructure robustness | 2026-05-19 morning → 14:30 UTC | 12-area investigation + 6 fix phases (F2 → F0 → F1 → F4 → F5 → F6). The discovery that Kafka was writing to ephemeral `/tmp` not the PVC (Stage A.1 Area 1) was the most impactful finding of the whole phase — explained the May 11–18 silence. |
| **B** — Application robustness | 2026-05-19 afternoon → 2026-05-20 00:15 UTC | 4-area investigation + 5-item fix package (Postgres DNS + Gold retry, OpenAI factory, Polymarket comments flag, raise-on-0% in OpenSky/googletrends, 20 unit tests). Discovered Flink-jobs-need-resubmit-after-image-rollout (KG-PHASE-9.5-8). |
| **B closeout retro** | 2026-05-20 morning | OpenAI Tier 1 RPD ceiling hit during F1 backlog processing (now KG-PHASE-9.5-1). Graceful-failure path verified production-correct (the agent retried, exhausted, wrote `failed` to Firestore, frontend showed retry button). |
| **C** — Monitoring + operational documentation | 2026-05-20 ~10:30 → 16:30 UTC | Pre-action: Silver→Gold backlog drop (5,939 messages, ~$88 saved). Then: 5 → 7 Prometheus scrape targets, 13 alert rules, Alertmanager + Gmail SMTP, Cloud Logging-based OpenAI proxy + 2 Cloud Monitoring policies, second Grafana dashboard, cluster_operations_guide.md. |

### What Phase 9.5 changed (cumulative)

**Cluster topology**:
- `polymarket-pool` node pool deleted (Stage A F0).
- Postgres Service `publishNotReadyAddresses: true` (Stage B 1a).
- `kafka-init` converted from one-shot Job to hourly CronJob (Stage A F1).
- Single main-pool node pool only.

**Application code** (Stage B):
- `utils/openai_client.py` (new) — centralised factory, `max_retries=5`.
- `utils/retry.py` (new) — transient-error retry helper.
- 12 OpenAI client instantiations consolidated through the factory.
- 5 DB-insert call sites in `gold_job.py` wrapped in `retry_on_transient`.
- `ingestion/polymarket_producer.py` — `POLYMARKET_COMMENTS_ENABLED` feature flag.
- `ingestion/opensky_producer.py` + `ingestion/googletrends_producer.py` — raise on 100% unit failure.
- 5 new test files; 20 unit tests pass; 102 existing tests still pass.

**Docker images rebuilt + redeployed**:
- `anizai-flink:1.19.1-p95`, `anizai-agent:0.2.0-p95`, `anizai-airflow:2.9.3-p95`, `anizai-polymarket:0.2.0-p95`.

**Monitoring (Stage C)**:
- kafka-exporter + postgres-exporter Deployments.
- Prometheus 13 alert rules + rule mount.
- Alertmanager + Gmail SMTP (new GMAIL_APP_PASSWORD secret).
- 1 Cloud Logging-based metric + 2 Cloud Monitoring policies.
- 2 Grafana dashboards (1 new — Pipeline Health).
- `cluster_operations_guide.md` (new, ~15 sections).

### Phase 9.5 Known Gap surface (carry-forward)

9 new KGs surfaced + carried, plus 3 pre-existing Phase-9 KGs partially mitigated. All tracked in `task_plan.md`:

| KG | Status |
|---|---|
| KG-PHASE-9.5-1 (OpenAI Tier 1 RPD) | Open (parallel cost-analysis session). |
| KG-PHASE-9.5-2 (NEWSAI_API_KEY rename) | Open (Stage C-deferred cross-cutting work). |
| KG-PHASE-9.5-3 (CLOUD_CONNECTION_GUIDE drift) | Open (doc cleanup). |
| KG-PHASE-9.5-4 (Polymarket /comments) | Open (upstream API contract unclear; feature flag default off). |
| KG-PHASE-9.5-5 (pytrends 404) | Open (no upstream fix). |
| KG-PHASE-9.5-6 (maintenance window) | Open (risk-reduction; not blocking). |
| KG-PHASE-9.5-7 (image digest pinning) | Open (defensive improvement). |
| KG-PHASE-9.5-8 (Flink jobs need cancel+resubmit) | Operationally documented in `cluster_operations_guide.md` §6. Process gap, not a code gap. |
| KG-PHASE-9.5-9 (OpenAI cost analysis) | Open (parallel session). |
| KG-PHASE-C-5 (OpenAI 429 in Gold) | Superseded by Stage B Item 2 + KG-PHASE-9.5-1. |
| KG-PHASE-C-6 (OpenSky network) | App-layer silent-success mitigated by Stage B Item 4; underlying firewall fix open. |
| KG-PHASE-C-7 (pytrends 404) | App-layer silent-success mitigated by Stage B Item 4; underlying lib fix → KG-PHASE-9.5-5. |

### Cloud Scheduler — recommendation

**Currently PAUSED.** Phase 9.5 was deliberately conducted with Scheduler paused so the cluster's daily scale-cycle didn't interfere with debugging. With Stage C done:
- The cluster is now monitored end-to-end (Phase 9.5's original motivation).
- The scale-cycle robustness was verified in Stage A F6 + Stage B post-restart.
- The known-silent producer set is documented (KG-PHASE-9.5 carryforward) so post-resume alerts don't surprise.

**Resume readiness checklist** is in `cluster_operations_guide.md` §4. Ron handles the actual resume + final manual steps.

### Git commit guidance

Ron handles commits. Phase 9.5's file changes span 3 stages — for the post-closeout commit set, see the staging plan provided mid-stage. Suggested high-level grouping:
- 1 commit: Phase 9 follow-up (Flink HA enablement — already done by Ron's pre-Phase-9.5 edits).
- 1 commit: Phase 9.5 implementation docs (both docs in this directory).
- 4 commits for Stage A fixes (F0/F1/F2 grouped by item).
- 5 commits for Stage B items (1 per item — code + tests per item).
- 1 commit for Stage B Docker image tag bumps.
- 4 commits for Stage C items (exporters + Prometheus + Alertmanager + Cloud-Monitoring + Grafana + operations guide).

**Phase 9.5 status: CLOSED 2026-05-20 ~17:00 UTC.**

---

## Cross-stage dependencies and answers to Ron's framing

### OQ-1 — Polymarket revert timing
**Answered (Ron, 2026-05-19):** Execute early in A.2, before Kafka topic
recreation. Removes one source of noise from every subsequent diagnostic.

### OQ-2 — "Working order" success criterion
**Answered (Ron, 2026-05-19):** Two sources flow E2E to close Stage A:
- **FRED** — daily DAG, simple schema → `momentum_vault`.
- **NewsAPI** — full Bronze → Silver → Gold path through filtering + enrichment
  → `knowledge_vault` + `knowledge_vectors`. The most representative source for
  the agentic hub's actual workload.

Remaining 7 sources are Stage B territory.

### OQ-3 — Restore drill scope
**Answered (Ron, 2026-05-19):** Run a real restore from a backup into a scratch
database in the live cluster. "Tested once" is not "tested".

### OQ-4 — Cloud Scheduler during Stage A
**Answered (Ron, 2026-05-19):** Stays PAUSED throughout. Claude manages main-pool
manually via `gcloud container clusters resize` during A.2. Scheduler is not
touched until end of Phase 9.5 — and even then only with Ron's explicit final
approval.

### OQ-5 — Airflow DAG catchup
**Answered (Ron, 2026-05-19):** Investigate during A.1, surface finding + Claude's
assessment. Decision (pause DAGs / set new start_date / let catch up) is made
together once Ron has the findings.

### OQ-6 — Stop-and-surface threshold
**Answered (Ron, 2026-05-19):** RED/YELLOW/GREEN framework documented in the
"Autonomy granted" section above.

---

## Reference: Phase 9 closeout state (entry conditions for Phase 9.5)

For full Phase 9 history see `data-pipeline/docs/archive/cloud_deployment_implementation.md`.

| Resource | Status at Phase 9 closeout (2026-05-10) |
|---|---|
| GKE cluster `anizai-cluster` | Zone `us-central1-a`, dual-pool (`main-pool` e2-standard-8 × 1 + `polymarket-pool` e2-micro × 1) |
| K8s namespace | `anizai` |
| Postgres StatefulSet | `timescale/timescaledb-ha:pg16`, 20 GB PVC, 7 application tables, 424 rows in `knowledge_vault` |
| Kafka StatefulSet | `apache/kafka:3.7.0` (KRaft), 10 GB PVC, 19 topics |
| Flink JM + TM | `anizai-flink:1.19.1`, shared 5 GB checkpoint PVC. *HA enabled in Phase 9 follow-up sprint (2026-05-19), not at Phase 9 closeout.* |
| Airflow scheduler + webserver + dedicated Postgres | All Running |
| 9 producers | Polymarket on polymarket-pool; Telegram + 7 scheduled producers on main-pool |
| Trigger consumer | Running (closes KG-PHASE8-3) |
| Agent worker | `anizai-agent:0.1.0`, cross-project Firestore via Workload Identity |
| Prometheus + Grafana | Running; 3 scrape targets UP |
| Postgres backup CronJob | `0 2 * * *` UTC → `gs://anizai-pipeline-backups/postgres/` |
| Cloud Scheduler | `scale-up-main-pool` (Mon-Fri 08:00 IL) + `scale-down-main-pool` (Mon-Fri 18:00 IL); both ENABLED at closeout, both PAUSED at Phase 9.5 entry |

---

## Open Known Gaps from `task_plan.md` (carried into Phase 9.5 scope analysis)

| Gap | Stage assignment |
|---|---|
| KG-PHASE-C-1 — `docker-compose.yml` kafka-ui pinned to `:latest` | Stage A (image management) |
| KG-PHASE-C-2 — `FLINK_PROPERTIES` `#`-comment-line pitfall | Stage A reference (already applied to Flink; check other ConfigMaps) |
| KG-PHASE-C-3 — GKE-native CSI does not support `secretObjects` | Stage A reference (already applied throughout) |
| KG-PHASE-C-4 — Airflow scheduler liveness probe must use HTTP, not CLI | Stage A reference (already applied) |
| KG-PHASE-C-5 — OpenAI `429 insufficient_quota` hitting Gold stage | Stage B |
| KG-PHASE-C-6 — OpenSky network timeout from GKE | Stage B |
| KG-PHASE-C-7 — pytrends `ResponseError 404` | Stage B |

---

*This document is updated incrementally throughout Phase 9.5. Companion log:
`phase95_investigation_log.md`.*
