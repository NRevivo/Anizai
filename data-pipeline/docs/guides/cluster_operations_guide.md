# Cluster Operations Guide
## Anizai GKE — operational reference for `anizai-cluster` (project `anizai-pipeline`)

This is the long-term reference for operating the running pipeline.
Audience: Ron, and future Claude sessions reaching for "how do I X?".

For one-time deployment history see
`data-pipeline/docs/archive/cloud_deployment_implementation.md` (Phase 9)
and `data-pipeline/docs/phase95_cluster_robustness_implementation.md`
(Phase 9.5).

For day-to-day port-forward commands see
`data-pipeline/docs/guides/CLOUD_CONNECTION_GUIDE.md` (fully swept for accuracy
2026-07-26 — KG-C-6 closed).

**For bringing the cluster up or down — whole system, pipeline-only, or
agents-only — use `data-pipeline/docs/guides/bringup_profiles.md`.** That file
owns the start/stop procedure and its gates; this file owns triage, runbooks,
dashboards and log queries once the cluster is running. Live state (images,
replica counts, what is actually deployed) belongs to
`data-pipeline/docs/C_cloud/cloud_state.md` and, above it, the cluster itself —
which always wins over anything written here.

---

## Section 1 — What's running where

### Cluster topology

- GKE Standard cluster `anizai-cluster`, zone `us-central1-a`, project `anizai-pipeline`.
- One node pool: `main-pool` (e2-standard-8 × 1; manually scaled to 0 between
  collection windows). `polymarket-pool` was deleted in Phase 9.5 Stage A
  (KG-PHASE-9.5 carry: Polymarket now runs on main-pool with everything else).
- Namespace: `anizai`.
- Cloud Scheduler jobs `scale-up-main-pool` / `scale-down-main-pool` exist
  but are currently **PAUSED** (verified live 2026-07-26). Ron resumes them
  manually. The one-shot auto-close job created for the Domain-A day-run fired
  on 2026-07-23 and has since been deleted — only the two recurring jobs remain,
  so nothing will scale the pool on its own.

### Per-pod purpose

| Pod | Image | Purpose | Failure mode if down |
|---|---|---|---|
| `postgres-0` | timescale/timescaledb-ha:pg16 | Vault tables (knowledge_vault, momentum_vault, social_vault, knowledge_vectors, social_vectors, mapping_dict, divergence_alerts). pgvector + TimescaleDB hypertable. | Gold cannot persist. Backed by 20Gi PVC + daily `pg_dump` to GCS. |
| `kafka-0` | apache/kafka:3.7.0 | KRaft single-broker. 19 topics. **Data dir is `/var/lib/kafka/data/kafka-logs` subdir of the 10Gi PVC** — must explicitly set `KAFKA_LOG_DIRS` (Phase 9.5 F1; default would be `/tmp` and ephemeral). | All producers + Flink consumers stuck. |
| `kafka-ui` | provectuslabs/kafka-ui:v0.7.2 | Topic + message inspection UI on port-forward. | Operator UX only. |
| `flink-jobmanager` | anizai-flink:1.19.1-7b5i | Submits + supervises Flink jobs. K8s HA via ConfigMap leader election (Phase 9 follow-up). | Job graph survives via HA ConfigMaps; running tasks stop. |
| `flink-taskmanager` | anizai-flink:1.19.1-7b5i | Runs Silver + Gold PyFlink tasks (4 slots). Mounts checkpoint PVC. | Jobs RESTARTING until TM recovers; messages back up in Kafka. |
| `agent-worker` | anizai-agent:0.5.0-sprint26 | LangGraph forecast agent. Attaches **two** Firestore listeners on startup: `forecastQueries` where `status=='pending'`, and (since Sprint 24) a **collection-group** listener on `messages` where `role=='user'` and `status=='sent'`. Each delivers its full current match set on first attach — see `bringup_profiles.md` §5 trap 2. OpenAI calls via `utils/openai_client.py` factory (max_retries=5). Held at `replicas: 0` declaratively since the 2026-07-23 Stage-1 deploy; brought up with `kubectl scale`, not by editing the manifest. | No forecasts processed; queries pile up in Firestore. |
| `airflow-postgres-0` | postgres:16 | Airflow metadata DB (DAG run state, task instances). 5Gi PVC. Not backed up (KG-PHASE-9.5-* candidate). | Scheduler can't run DAGs; webserver UI shows blank state. |
| `airflow-scheduler` | anizai-airflow:2.9.3-7b5i | Fires the 7 producer DAGs (arxiv, fred, googletrends, hackernews, newsapi, opensky, openweather). Liveness probe on port **8974** (NOT 8793 — Phase 9.5 F2). | Scheduled producer runs miss their windows. |
| `airflow-webserver` | anizai-airflow:2.9.3-7b5i | Airflow UI on port-forward. | Operator UX only. |
| `polymarket` | anizai-polymarket:0.2.0-p95 | Always-on WebSocket producer (CLOB ticks + REST market refresh). Comment-fetch loop disabled by feature flag (Phase 9.5 Item 3). | Polymarket data goes silent during downtime; price gaps not backfillable. |
| `telegram` | anizai-telegram:0.1.0 | MTProto continuous channel listener. Session file from Secret Manager. | Telegram data goes silent during downtime. |
| `trigger-consumer` | anizai-trigger-consumer:0.1.0 | Consumes `ingestion_triggers` topic from the agent for reactive ingestion. **The deployed `0.1.0` image predates Sprint 23 and its SecretProviderClass mounts no NewsAPI key**, so the newsapi reactive dispatch cannot run today even though the repo code supports it — a trigger emitted by the agent is dispatched and fails on the consumer side. Fixing this needs an image rebuild **and** a secret added to the SPC (it is not fixed by rebuilding `anizai-airflow`, a common misreading). | Agent-triggered ingest doesn't fire. |
| `prometheus` | prom/prometheus:v2.51.2 | Scrapes 5 targets (Flink JM/TM, agent /metrics, kafka-exporter, postgres-exporter). 10Gi PVC, 7-day TSDB retention (Phase 9.5 F2). | No metrics scraped; dashboards blank. Alerts stop firing. |
| `kafka-exporter` | danielqsj/kafka-exporter:v1.7.0 | Kafka topic + partition + broker → Prometheus metrics. | DLQ-depth + topic-staleness alerts go silent. |
| `postgres-exporter` | prometheuscommunity/postgres-exporter:v0.15.0 | Postgres internals + Anizai-specific vault freshness queries. | Vault-freshness alerts + dashboard panels go silent. |
| `alertmanager` | prom/alertmanager:v0.27.0 | Receives firing alerts from Prometheus, routes via Gmail SMTP to `ron.mintz21@gmail.com`. | Alerts evaluate but no email. |
| `grafana` | grafana/grafana:10.4.2 | 2 dashboards: `Anizai Pipeline` (Phase 9, detailed Flink) + `Anizai Pipeline Health` (Phase 9.5 C, single-screen). | UI-only; no operational impact. |
| `postgres-backup` (CronJob) | google/cloud-sdk:slim | Daily `pg_dump` → `gs://anizai-pipeline-backups/postgres/YYYY-MM-DD/anizai.sql.gz`, 30-day lifecycle. | Lose ability to restore beyond Postgres PVC contents. |
| `kafka-init` (CronJob) | apache/kafka:3.7.0 | Hourly idempotent topic-reassert (Phase 9.5 F1). Creates the 19 expected topics with `--if-not-exists`. Self-heals after a PVC reset. | New topics would need manual `kafka-init-job.yaml` re-apply. |

### Data flow

```
producers/                   Airflow ──▶ DAG fires ──▶ producer subprocess
                                                              │
                                                              ▼
ingest.bronze.<source> ◀───────────────  Bronze envelope (NDJSON)
        │
        ▼
silver_job.py (Flink)        process.silver.{social_pulse,global_news,structured_metrics}
        │
        ▼
gold_job.py (Flink)          serve.gold.{social_pulse,global_news,structured_metrics}
        │                                          │
        │                                          ▼
        │                              postgres / vault tables
        ▼
   dead-letter-queue (any layer failure)
```

Continuous stream (Polymarket WebSocket, Telegram MTProto) bypasses
Airflow — those producers run as always-on Deployments and write to Bronze
directly.

---

## Section 2 — Daily flow

A FRED Bronze message's lifecycle, with typical timing (from Phase 9.5
Stage A F5 verification):

1. **06:00 UTC** — Airflow scheduler fires `fred_daily` DAG.
2. **+0.5s** — DAG task launches `python -m ingestion.fred_producer` as a
   LocalExecutor subprocess inside the airflow-scheduler container.
3. **+1-3s** — Producer fetches FRED series, emits ~88 messages to
   `ingest.bronze.fred` Kafka topic. NDJSON envelope: `event_id`,
   `producer_timestamp`, `payload`, etc.
4. **+5s** — Flink Silver job (`anizai_silver_polymarket` — confusing
   name; it actually handles ALL sources including FRED via a single
   StatefulSet of source operators) reads `ingest.bronze.fred`, parses,
   validates, transforms to Silver schema, emits to
   `process.silver.structured_metrics`.
5. **+8s** — Flink Gold job (`anizai_gold_all_sources`) reads
   `process.silver.structured_metrics`, computes momentum / triggers,
   emits Gold to `serve.gold.structured_metrics` AND writes to
   `momentum_vault` table via psycopg2 (retry-wrapped per Phase 9.5 F1b).
6. **+12s** — Row visible in Postgres `momentum_vault`:
   `SELECT * FROM momentum_vault WHERE source_name='fred' ORDER BY ingested_at DESC LIMIT 1;`

End-to-end latency for FRED ≈ 10-15 seconds.

Polymarket and high-frequency sources (hackernews, openweather) have
similar shapes; their Bronze→Silver→Gold→Postgres latency is dominated by
network calls + Flink checkpoint windows, not the pipeline itself.

---

## Section 3 — Cluster start / stop checklist

The cluster is normally scaled-to-0 between collection windows to control
cost. Currently this is **manual** (Ron runs `gcloud container clusters
resize`); Cloud Scheduler jobs exist but are PAUSED.

> **Use `bringup_profiles.md` instead of this section for anything other than a
> full-system start.** What follows is the FULL profile only — it assumes every
> workload should come up. It has no profile selector and no pre-flight gates, so
> following it for an agent-only or pipeline-only session will start workloads you
> did not want and skip the checks that matter (stale Firestore documents claimed
> on agent startup; Kafka backlog replayed through Gold enrichment the moment Flink
> schedules). Kept here for the full-start command detail and the expected-pod
> reference below.
>
> Two expectations below are also out of date: `agent-worker` no longer comes up
> with everything else (it is held at `replicas: 0` and scaled up deliberately),
> and `polymarket` / `telegram` have been sitting at 0 live while their committed
> manifests still declare `replicas: 1` — read desired replicas from the cluster,
> never from the repo.

### Start (scale 0 → 1)

```powershell
# 1. Scale main-pool back to 1 node.
gcloud container clusters resize anizai-cluster `
  --node-pool=main-pool --num-nodes=1 `
  --zone=us-central1-a --project=anizai-pipeline --quiet

# 2. Wait for Kafka, then for Postgres, to reach 1/1 Ready.
#    Typically ~3-5 min for the node + ~30s per pod after.
kubectl get pods -n anizai --watch

# 3. After all pods are 1/1 Running, the following are expected:
#    - kafka-0          1/1 Running 0 restarts
#    - postgres-0       1/1 Running 0 restarts
#    - flink-jobmanager 1/1 Running 0 restarts
#    - flink-taskmanager 1/1 Running 0-1 restarts (HA recovery)
#    - polymarket / telegram / trigger-consumer 1/1 Running 2-3 restarts
#      (NoBrokersAvailable + retry during Kafka boot — settles in ~60s)
#    - agent-worker     1/1 Running 0 restarts
#    - prometheus + alertmanager + grafana + exporters: 1/1 Running

# 4. Verify Flink jobs auto-recovered to RUNNING:
JM=$(kubectl get pods -n anizai -l app=flink-jobmanager -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n anizai $JM -- curl -s http://localhost:8081/jobs/overview

# Expected: both anizai-silver-polymarket and anizai-gold-all-sources in
# state=RUNNING. If either is FAILED or doesn't appear, see Section 5
# runbook "Flink jobs in RESTARTING loop after a restart".

# 5. Confirm topics exist (kafka-init CronJob may have already run):
kubectl exec -n anizai kafka-0 -- /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server localhost:9092 --list | wc -l
# Expected: 19. If <19, manually re-run kafka-init:
#   kubectl create job kafka-init-manual --from=cronjob/kafka-init -n anizai
```

### Stop (scale 1 → 0)

```powershell
# 1. Optional: cleanly cancel running Flink jobs first to ensure their
#    final checkpoints land + their HA state is preserved cleanly:
JM=$(kubectl get pods -n anizai -l app=flink-jobmanager -o jsonpath='{.items[0].metadata.name}')
for jid in $(kubectl exec -n anizai $JM -- curl -s http://localhost:8081/jobs/overview | jq -r '.jobs[].jid'); do
  kubectl exec -n anizai $JM -- curl -s -X PATCH "http://localhost:8081/jobs/$jid"
done
# Cancel is NOT strictly necessary — HA preserves graphs across restarts
# regardless — but it produces a cleaner state for diagnostics.

# 2. Scale main-pool to 0.
gcloud container clusters resize anizai-cluster `
  --node-pool=main-pool --num-nodes=0 `
  --zone=us-central1-a --project=anizai-pipeline --quiet

# Pods will Terminate; PVCs detach. Data persists (postgres-data,
# kafka-data, flink-checkpoints, prometheus-data, airflow-postgres-data).

# 3. Verify zero nodes:
kubectl get nodes
# Expected: "No resources found"
```

---

## Section 4 — Cloud Scheduler resume procedure

Cloud Scheduler currently has two PAUSED jobs that, when resumed, will
auto-scale main-pool Mon-Fri 05:00 IL up / 15:00 IL down. **Do not resume
without first confirming the cluster is in good operational shape** —
the start cycle exercises every Phase 9.5 finding.

### Pre-resume checklist

- [ ] Stage A, B, C all closed (Phase 9.5 done).
- [ ] Cluster currently scaled to 1, all pods 1/1 Running.
- [ ] Both Flink jobs RUNNING, recent checkpoints completing (check Grafana
  Pipeline Health "Gold failed checkpoints (1h)" stat panel — should be 0).
- [ ] No firing alerts in Prometheus `/api/v1/alerts` other than known
  intentional-silent KGs (KG-PHASE-C-6 OpenSky network, KG-PHASE-9.5-5
  pytrends, KG-PHASE-9.5-4 Polymarket comments).
- [ ] DLQ depth < 1000 messages.
- [ ] OpenAI credit balance ≥ $5 (provides ~150-200 queries headroom).

### Resume

```powershell
gcloud scheduler jobs resume scale-up-main-pool `
  --location=us-central1 --project=anizai-pipeline
gcloud scheduler jobs resume scale-down-main-pool `
  --location=us-central1 --project=anizai-pipeline

# Confirm:
gcloud scheduler jobs list --location=us-central1 --project=anizai-pipeline
# Both should show STATE=ENABLED.
```

After the next 05:00 IL scale-up, monitor the first cycle:
- [ ] Confirm `kafka-init` CronJob's hourly run shows all 19 topics
  `--if-not-exists` no-op.
- [ ] Confirm Gold first checkpoint succeeds within ~5 min (Phase 9.5 F1
  invariant — if checkpoints start failing in clusters, see Section 5
  "Gold checkpoint failures clustering").
- [ ] Confirm at least one Bronze message lands per active producer in
  the first 30 min.

### Re-pause if anything is off

```powershell
gcloud scheduler jobs pause scale-up-main-pool   --location=us-central1
gcloud scheduler jobs pause scale-down-main-pool --location=us-central1
```

---

## Section 5 — Common-symptom runbooks

Each entry is a specific symptom you'd see (alert email, dashboard panel,
or manual `kubectl` observation) and how to triage.

### 5.1 — "Pipeline silently idle"

**Symptom**: vault row counts stop growing despite active producers.
Likely alert: `MomentumVaultIdle` or `KnowledgeVaultIdle` (warning).

**First-look commands**:
```powershell
# Check producer pod state.
kubectl get pods -n anizai

# Check Bronze topic rates:
kubectl exec -n anizai kafka-0 -- /opt/kafka/bin/kafka-get-offsets.sh `
  --bootstrap-server localhost:9092 --topic ingest.bronze.polymarket --time -1

# Check Flink job state:
JM=$(kubectl get pods -n anizai -l app=flink-jobmanager -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n anizai $JM -- curl -s http://localhost:8081/jobs/overview
```

**Decision tree**:
- Bronze topics growing + Silver topics growing + vault tables NOT growing
  → Gold processing or Postgres insert problem. See 5.4 (Gold checkpoint
  failures) or 5.7 (Postgres DNS errors).
- Bronze topics growing + Silver topics NOT growing → Silver job problem.
  Check Flink UI for Silver job state.
- Bronze topics NOT growing → producer-side problem. See 5.2.

### 5.2 — Bronze topic flatlined (producer-side)

**Symptom**: `PolymarketBronzeStale`, `HighFrequencyBronzeStale`, or
`DailyBronzeStale` alert firing. Bronze topic offset hasn't moved.

**First-look**:
```powershell
# Producer pod state:
kubectl get pods -n anizai -l app=polymarket   # or telegram, trigger-consumer
# Airflow DAG state (for scheduled producers):
kubectl port-forward -n anizai svc/airflow-webserver 8090:8080
# → http://localhost:8090
```

**Common causes**:
- **Producer pod CrashLoopBackOff with NoBrokersAvailable**: Kafka pod is
  down or unreachable. Check Kafka pod (`kubectl get pods -n anizai -l app=kafka`).
- **Airflow DAG runs showing `failed`**: producer raised an exception.
  Look at the Airflow task log. If it's KG-PHASE-C-6 (OpenSky firewall)
  or KG-PHASE-9.5-5 (pytrends 404), that's an **intentional-silent**
  producer per Phase 9.5 Stage B Item 4 (producer raises on 100% unit
  failure → Airflow shows `failed`).
- **Newsapi paused by design**: `airflow dags list` shows
  `is_paused=True` for `newsapi_high_frequency`. Sprint C4 deliberately
  paused this. Manual trigger doesn't fire while paused.
- **Polymarket comments disabled by feature flag**: `POLYMARKET_COMMENTS_ENABLED=false`
  default. Only the comment-fetch loop is gated; price WebSocket still
  runs. If polymarket Bronze is silent, it's the WebSocket, not the flag.

### 5.3 — Polymarket spammy 422 warnings

**Symptom**: `kubectl logs -l app=polymarket` shows hundreds of:
```
[polymarket] Comment fetch failed for market <id>: 422 Client Error: Unprocessable Entity
```

**Cause**: KG-PHASE-9.5-4. Polymarket's Gamma `/comments` endpoint made
a breaking change (now requires `parent_entity_id` + `entity_entity_type`;
correct enum value not yet known).

**Resolution status**: Phase 9.5 Stage B Item 3 added a feature flag
defaulting to OFF. **If you're seeing this spam, the env var was somehow
flipped to true.** Check:
```powershell
kubectl exec -n anizai $(kubectl get pods -n anizai -l app=polymarket -o jsonpath='{.items[0].metadata.name}') -- `
  printenv POLYMARKET_COMMENTS_ENABLED
# Empty or "false" → feature flag is off → no spam expected.
# "true" → flag is on → spam expected until upstream API is repaired.
```

To re-disable, ensure `POLYMARKET_COMMENTS_ENABLED` is unset in the
Polymarket Deployment env block (default false). Apply + rollout.

### 5.4 — Gold checkpoint failures clustering

**Symptom**: alert `GoldCheckpointFailureCluster` (warning) or
`GoldCheckpointFailureClusterSustained` (critical) firing. Grafana
"Gold failed checkpoints (1h)" panel ≥ 3.

**Cause**: backpressure from a downstream choke. Phase 9.5 history says
this fires under three common conditions:
1. OpenAI RPD ceiling hit (KG-PHASE-9.5-1) — Gold's consensus calls slow
   to a crawl, checkpoints expire.
2. Postgres unreachable mid-write (rare with Phase 9.5 publishNotReadyAddresses
   + retry; would happen if Postgres pod is actually down).
3. Producer surge — Bronze rate spikes faster than Gold can process; Flink
   backs up, checkpoint state grows, eventually expires.

**Triage steps** (run in this order, stop when you have the answer):

1. Check Cloud Monitoring for `OpenAI rate-limit (WARNING)` alert firing.
   If yes → KG-PHASE-9.5-1 active. Options:
   - Wait for next midnight UTC (RPD ceiling resets).
   - If urgent, cancel the Gold job to stop OpenAI consumption + free
     RPD budget for the agent. See Section 7 backlog-drop procedure.
2. Check Postgres pod state: `kubectl get pods -n anizai -l app=postgres`.
   If not 1/1 → restart the Postgres pod (`kubectl delete pod postgres-0 -n anizai`)
   and wait for it to come back. Gold retry will catch the transient.
3. Check Bronze topic rates in Grafana "Bronze topic activity" panel.
   If a topic is spiking 10× normal → producer is overrun; consider
   scaling that producer down or pausing the DAG.

### 5.5 — OpenAI 429s appearing in DLQ

**Symptom**: DLQ inspection shows `failed_stage="openai_consensus"` or
`failed_stage="openai_embedding"` entries.

**Cause**: Gold's OpenAI calls exhausted retries (5 retries × exp backoff
per Phase 9.5 Stage B Item 2 — `utils/openai_client.py` factory). Could
be RPD ceiling (KG-PHASE-9.5-1) or credit exhaustion.

**Inspect**:
```powershell
# Dump recent DLQ messages:
kubectl exec -n anizai kafka-0 -- /opt/kafka/bin/kafka-console-consumer.sh `
  --bootstrap-server localhost:9092 --topic dead-letter-queue `
  --partition 0 --offset latest --max-messages 10 --timeout-ms 30000
```

**Check OpenAI status**: https://platform.openai.com/usage. Look for
- Daily request count approaching 10,000 (Tier 1 ceiling).
- Credit balance > $0.50.

**If RPD-ceiling**:
- Either wait for midnight UTC.
- Or cancel Gold job temporarily (frees budget for the agent).

**If credit-exhaustion**:
- Add credit at https://platform.openai.com/billing.
- Optionally re-run from DLQ via a one-shot consumer (replay):
  ```powershell
  # Replay DLQ messages to their original source_topic via a small Python
  # script — see scripts/replay_dlq.py if present, or write ad-hoc.
  # Not a standard runbook step; only useful for high-value lost data.
  ```

### 5.6 — Agent query hangs or returns failed

**Symptom**: A forecastQuery sits in `processing` state for >5 min OR
returns to the frontend as `failed`.

**Cause**: typically OpenAI 429 / network error / Postgres unreachable in
the vault_query node. Phase 9.5 Stage B Item 2 added the
`utils/openai_client.py` factory with `max_retries=5`; on exhaustion the
node raises `AgentProcessingError`, the runner catches and writes
`status='failed'` to Firestore.

**Inspect agent worker logs**:
```powershell
kubectl logs -n anizai -l app=agent-worker --tail=200 | grep -E "ERROR|Traceback"
```

> **The agent's INFO logs are 1 %-sampled — do not read their absence as evidence.**
> ERROR and WARNING are emitted at 100 %, so the grep above is reliable for
> failures. Everything at INFO (including the per-forecast `llm_usage` cost lines)
> passes at `LOG_INFO_SAMPLE_RATE`, default `0.01`. A forecast that ran perfectly
> will usually leave no INFO trace at all. Cost and per-node latency come from
> Prometheus (`agent_llm_cost_usd_total`, `agent_node_duration_seconds`), not from
> logs. See `bringup_profiles.md` §5 trap 3 and KG-B-4.

Common log patterns:
- `RateLimitError` from `openai` → OpenAI RPD hit. See 5.5.
- `OperationalError: could not translate host name "postgres"` → Postgres
  unreachable. Check Postgres pod state.
- `AgentProcessingError: synthesize: OpenAI call failed` → 5 retries
  exhausted in synthesize node. See 5.5.

**Recovery**: failed forecasts can be re-submitted by the user (frontend
shows "Forecast could not be completed" with a retry button). No
operator action required unless the failure cause is unfixed.

### 5.7 — Postgres-DNS errors in Gold

**Symptom**: `kubectl logs -l app=flink-taskmanager | grep "could not translate host name"`.

**Cause**: rare since Phase 9.5 Stage A Item 1a added
`publishNotReadyAddresses: true` to postgres-service.yaml + Stage B Item 1b
added retry. The remaining failure mode is a long Postgres outage that
exhausts the 5-attempt × exp-backoff (~15s) retry window.

**Resolution**: usually self-heals if Postgres becomes Ready within ~15s.
If not, check Postgres pod for the actual outage cause.

### 5.8 — Kafka has zero topics after a restart

**Symptom**: `kubectl exec kafka-0 -- /opt/kafka/bin/kafka-topics.sh --list`
returns empty.

**Cause (extremely rare after Phase 9.5 F1)**: Kafka's `log.dirs` was
overridden to something ephemeral. Phase 9.5 Stage A discovered the
original Phase 9 config wrote data to `/tmp/kafka-logs` (ephemeral). F1
fixed this by setting `KAFKA_LOG_DIRS=/var/lib/kafka/data/kafka-logs`
explicitly. If you ever see this symptom again, the fix may have been
reverted.

**Recovery**:
```powershell
# 1. Verify KAFKA_LOG_DIRS env var is set:
kubectl exec -n anizai kafka-0 -- printenv KAFKA_LOG_DIRS
# Expected: /var/lib/kafka/data/kafka-logs

# 2. Re-run the kafka-init topic creation (the hourly CronJob will do it
#    eventually, but to act immediately):
kubectl create job kafka-init-manual --from=cronjob/kafka-init -n anizai

# 3. Wait for the Job pod to Complete, then verify:
kubectl exec -n anizai kafka-0 -- /opt/kafka/bin/kafka-topics.sh `
  --bootstrap-server localhost:9092 --list | wc -l
# Expected: 19
```

### 5.9 — Flink jobs in RESTARTING loop after a restart

**Symptom**: `curl /jobs/overview` shows jobs in state `RESTARTING` with
`tasks.canceled` > 0 and `tasks.running` == 0.

**Cause**: typically the Kafka source operator cannot find its expected
topic (zero topics; see 5.8) OR the job's checkpoint state is incompatible
with the current code (rare).

**Recovery**:
1. First confirm topics exist (5.8). If they don't, fix that — Flink will
   auto-recover.
2. If topics exist + job still RESTARTING for >10 min, check Flink UI
   exceptions tab. If `IllegalStateException` or similar deserialization
   error, the previous checkpoint may be incompatible. Cancel the job
   and resubmit cleanly — see Section 6 for the procedure.

---

## Section 6 — Flink jobs need cancel + re-submit after image rollout (KG-PHASE-9.5-8)

**The trap**: if you rebuild + push a new `anizai-flink:1.19.x` image and
roll out the Flink JM/TM pods, the running jobs will continue executing
the **OLD code** that was compiled at submit time and shipped via Flink's
BlobServer. The Flink HA mechanism preserves the job graph across pod
restarts — but it does NOT pick up new code from the image.

This was discovered during Phase 9.5 Stage B.2: the Postgres-retry fix
appeared dead on first test because the running Gold job was still the
pre-rebuild compilation.

### Procedure: after rebuilding anizai-flink

```powershell
# 1. Confirm the new image is in Artifact Registry:
gcloud artifacts docker images list `
  us-central1-docker.pkg.dev/anizai-pipeline/anizai-images --include-tags `
  | grep anizai-flink

# 2. Update the image tag in:
#      infrastructure/k8s/flink-jobmanager-deployment.yaml
#      infrastructure/k8s/flink-taskmanager-deployment.yaml
#    Apply both.

# 3. Roll out JM + TM. JM HA leader-election sometimes needs a hard scale 1→0→1:
kubectl scale deployment/flink-jobmanager -n anizai --replicas=0
Start-Sleep -Seconds 8
kubectl scale deployment/flink-jobmanager -n anizai --replicas=1
kubectl rollout status deployment/flink-jobmanager -n anizai --timeout=180s
kubectl rollout status deployment/flink-taskmanager -n anizai --timeout=180s

# 4. Get the JIDs of the running (recovered-from-HA) jobs:
JM=$(kubectl get pods -n anizai -l app=flink-jobmanager -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n anizai $JM -- curl -s http://localhost:8081/jobs/overview

# 5. CANCEL both jobs. (This is the step easy to forget.)
kubectl exec -n anizai $JM -- curl -s -X PATCH "http://localhost:8081/jobs/<silver-jid>"
kubectl exec -n anizai $JM -- curl -s -X PATCH "http://localhost:8081/jobs/<gold-jid>"

# 6. Re-submit Silver + Gold so they compile from the NEW code in the
#    image:
kubectl exec -n anizai $JM -- flink run -d -py /opt/flink/usrlib/processing/silver_job.py
kubectl exec -n anizai $JM -- flink run -d -py /opt/flink/usrlib/processing/gold_job.py

# 7. Verify new JIDs are RUNNING and reaching their first checkpoint
#    within ~60s:
kubectl exec -n anizai $JM -- curl -s "http://localhost:8081/jobs/<new-jid>/checkpoints"
```

**If you forget Step 5-6**: the symptom is "my code change isn't taking
effect even though the pods restarted." Always pair an image rollout with
job cancel + resubmit.

---

## Section 7 — Backlog-drop procedure

When Silver→Gold backlog grows large enough to threaten OpenAI cost or
RPD ceiling, drop it. Executed Phase 9.5 Stage C (5,939-message backlog)
to contain ~$88 / 14,600 RPD-call exposure.

**When to use it**:
- DLQ growth alert firing with mostly `openai_consensus` failures.
- `kafka_topic_partition_current_offset{topic="process.silver.global_news"}` is
  >2,000 ahead of where Gold has consumed (visible via Grafana Bronze topic
  activity panel — look for Silver outputs without matching Gold input).
- Operator deems the queued data not worth the catch-up OpenAI cost.

**When NOT to use it**:
- For normal cluster start-up — the regular catchup is part of the design.
- When the queued data IS important (e.g., recent global news with high
  relevance) and the OpenAI budget can absorb the cost.

### Procedure

```powershell
# 0. Snapshot current state (for verification):
kubectl exec -n anizai kafka-0 -- /opt/kafka/bin/kafka-get-offsets.sh `
  --bootstrap-server localhost:9092 --topic dead-letter-queue --time -1
kubectl exec -n anizai kafka-0 -- /opt/kafka/bin/kafka-get-offsets.sh `
  --bootstrap-server localhost:9092 --topic process.silver.social_pulse --time -1
kubectl exec -n anizai kafka-0 -- /opt/kafka/bin/kafka-get-offsets.sh `
  --bootstrap-server localhost:9092 --topic process.silver.global_news --time -1

# 1. CANCEL the running Gold job. Flink will auto-clean its HA ConfigMap
#    on CANCEL (confirmed Phase 9.5 Stage C — no manual ConfigMap deletion
#    needed).
JM=$(kubectl get pods -n anizai -l app=flink-jobmanager -o jsonpath='{.items[0].metadata.name}')
GOLD_JID=$(kubectl exec -n anizai $JM -- curl -s http://localhost:8081/jobs/overview | jq -r '.jobs[] | select(.name=="anizai-gold-all-sources") | .jid')
kubectl exec -n anizai $JM -- curl -s -X PATCH "http://localhost:8081/jobs/$GOLD_JID"

# Wait for state=CANCELED before continuing.
until kubectl exec -n anizai $JM -- curl -s "http://localhost:8081/jobs/$GOLD_JID" | grep -q '"state":"CANCELED"'; do
  sleep 3
done

# 2. Build the delete-records JSON file with current end offsets per partition.
#    Save as ./delete-records.json (example for two topics × 3 partitions):
#    {
#      "partitions": [
#        {"topic": "process.silver.social_pulse", "partition": 0, "offset": <end>},
#        ... 6 entries total ...
#      ],
#      "version": 1
#    }

# 3. Copy + execute kafka-delete-records inside kafka-0:
cmd /c "kubectl exec -i -n anizai kafka-0 -- bash -c `"cat > /tmp/delete-records.json`" < delete-records.json"
kubectl exec -n anizai kafka-0 -- /opt/kafka/bin/kafka-delete-records.sh `
  --bootstrap-server localhost:9092 --offset-json-file /tmp/delete-records.json

# 4. Verify low-watermarks (--time -2) == end offsets (--time -1):
kubectl exec -n anizai kafka-0 -- /opt/kafka/bin/kafka-get-offsets.sh `
  --bootstrap-server localhost:9092 --topic process.silver.global_news --time -2

# 5. Resubmit Gold. The Kafka source initializer is
#    KafkaOffsetsInitializer.earliest() — with topics truncated, "earliest"
#    is the new low-watermark, so Gold starts at the right place.
kubectl exec -n anizai $JM -- flink run -d -py /opt/flink/usrlib/processing/gold_job.py

# 6. Verify within ~5 min:
#    - new Gold job state=RUNNING
#    - first checkpoint counts.completed >= 1
#    - DLQ growth rate not climbing
```

**Data lost**: the truncated messages. For Phase 9.5 Stage C this was
5,939 messages of social_pulse + global_news Silver data — recoverable
only if needed by re-running the producers (but they only fetch live data,
not history, so the actual loss is permanent).

---

## Section 8 — Restore drill procedure

Tested Phase 9.5 Stage A F4. Backups land at
`gs://anizai-pipeline-backups/postgres/YYYY-MM-DD/anizai.sql.gz` daily at
02:00 UTC. 30-day GCS lifecycle.

```powershell
# 1. Pick a backup to restore. Today (-1 day for guaranteed completeness):
YDAY=(Get-Date).AddDays(-1).ToString("yyyy-MM-dd")
gsutil cp gs://anizai-pipeline-backups/postgres/$YDAY/anizai.sql.gz `
  "C:\Temp\anizai-restore.sql.gz"

# 2. Copy into postgres-0 (via stdin pipe — kubectl cp has Windows colon-
#    path bugs, see CLOUD_CONNECTION_GUIDE.md or Phase 9.5 Stage A F4):
cmd /c "kubectl exec -i -n anizai postgres-0 -- bash -c `"cat > /tmp/anizai-restore.sql.gz`" < C:\Temp\anizai-restore.sql.gz"

# 3. Create scratch database:
kubectl exec -n anizai postgres-0 -- psql -U anizai -d postgres -c "CREATE DATABASE anizai_scratch;"

# 4. Restore:
kubectl exec -n anizai postgres-0 -- bash -c `
  "gunzip -c /tmp/anizai-restore.sql.gz | psql -U anizai -d anizai_scratch"

# 5. Compare row counts against the live anizai database:
kubectl exec -n anizai postgres-0 -- psql -U anizai -d anizai_scratch -c `
  "SELECT 'knowledge_vault' AS tbl, COUNT(*) FROM knowledge_vault UNION ALL `
   SELECT 'momentum_vault',    COUNT(*) FROM momentum_vault    UNION ALL `
   SELECT 'social_vault',      COUNT(*) FROM social_vault;"

kubectl exec -n anizai postgres-0 -- psql -U anizai -d anizai -c `
  "SELECT 'knowledge_vault' AS tbl, COUNT(*) FROM knowledge_vault UNION ALL `
   SELECT 'momentum_vault',    COUNT(*) FROM momentum_vault    UNION ALL `
   SELECT 'social_vault',      COUNT(*) FROM social_vault;"

# Expected: scratch ≤ live (live has data added since the backup).

# 6. Cleanup:
kubectl exec -n anizai postgres-0 -- psql -U anizai -d postgres -c `
  "DROP DATABASE anizai_scratch;"
kubectl exec -n anizai postgres-0 -- rm -f /tmp/anizai-restore.sql.gz
Remove-Item "C:\Temp\anizai-restore.sql.gz"
```

**Production restore** (if the live anizai DB is corrupted/lost — would
need to drop + recreate `anizai`, then run the restore against the new
empty database). Don't do this unless absolutely necessary.

---

## Section 9 — Diagnostic command reference by symptom

| Symptom | First command |
|---|---|
| Email alert arrived; what's firing? | `kubectl port-forward -n anizai svc/prometheus 9090:9090` → http://localhost:9090/alerts |
| Pod CrashLoopBackOff | `kubectl logs -n anizai <pod> --previous --tail=50` |
| Pod Pending | `kubectl describe pod -n anizai <pod>` (look at Events) |
| Bronze topic offset count | `kubectl exec -n anizai kafka-0 -- /opt/kafka/bin/kafka-get-offsets.sh --bootstrap-server localhost:9092 --topic <topic> --time -1` |
| DLQ message inspection | `kubectl exec -n anizai kafka-0 -- /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic dead-letter-queue --partition 0 --offset latest --max-messages 5 --timeout-ms 30000` |
| Flink job state | `JM=$(kubectl get pods -n anizai -l app=flink-jobmanager -o jsonpath='{.items[0].metadata.name}'); kubectl exec -n anizai $JM -- curl -s http://localhost:8081/jobs/overview` |
| Flink job checkpoint history | `kubectl exec -n anizai $JM -- curl -s http://localhost:8081/jobs/<jid>/checkpoints` |
| Postgres row count by source | `kubectl exec -n anizai postgres-0 -- psql -U anizai -d anizai -c "SELECT source_name, COUNT(*) FROM <table> GROUP BY source_name;"` |
| Recent Postgres connections | `kubectl exec -n anizai postgres-0 -- psql -U anizai -d anizai -c "SELECT pid, application_name, state, query_start FROM pg_stat_activity ORDER BY query_start;"` |
| Agent recent log activity | `kubectl logs -n anizai -l app=agent-worker --tail=100` |
| Airflow DAG run status | `SCHED=$(kubectl get pods -n anizai -l app=airflow-scheduler -o jsonpath='{.items[0].metadata.name}'); kubectl exec -n anizai $SCHED -- bash -c 'export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN="postgresql+psycopg2://airflow:$(cat /var/secrets/airflow/AIRFLOW_POSTGRES_PASSWORD)@airflow-postgres/airflow" && airflow dags list-runs -d <DAG_ID> -o plain | head -5'` |
| Trigger Airflow DAG manually | Same shell as above, then `airflow dags trigger <DAG_ID>` |

---

## Section 10 — How to add a new Prometheus alert

The alert rules are defined in
`infrastructure/k8s/prometheus-rules-configmap.yaml`. Procedure:

```powershell
# 1. Edit the YAML — add a new rule to the appropriate group.
# Example template:
#
#   - alert: MyNewAlert
#     expr: <promql_expression>
#     for: <duration>            # how long the condition must hold
#     labels:
#       severity: warning|critical
#       component: <area>        # flink/kafka/postgres/etc.
#     annotations:
#       summary: "Short one-line"
#       description: |
#         Multi-line. Reference runbook section.

# 2. Apply:
kubectl apply -f infrastructure/k8s/prometheus-rules-configmap.yaml

# 3. ConfigMap mount sync: wait ~60s for kubelet to propagate the new
#    file into the Prometheus pod.

# 4. Trigger Prometheus to reload rules:
PROM=$(kubectl get pods -n anizai -l app=prometheus -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n anizai $PROM -c prometheus -- wget --post-data="" -qO- `
  http://localhost:9090/-/reload

# 5. Verify the new rule is loaded:
kubectl exec -n anizai $PROM -c prometheus -- wget -qO- `
  http://localhost:9090/api/v1/rules | grep MyNewAlert
```

**Threshold tuning**: per Phase 9.5 Stage C decisions, the V1 thresholds
are conservative. Tighten after ~2 weeks of production data shows the
real noise floor. The OpenAI 429 warning specifically (at first-occurrence)
was Ron's call — given the 2026-05-19 and 2026-05-20 incidents, early
signal was deemed more valuable than alert-fatigue avoidance for the first
few weeks. If after ~2 weeks the warning proves noisy, raise the threshold
to ≥2/h.

---

## Section 11 — How to query Cloud Logging for pipeline events

All container stdout/stderr flows to Cloud Logging via `fluentbit-gke`
(kube-system DaemonSet, GKE-managed). Common useful filters:

> **Two things that will make you think logging is broken when it is not.**
> (1) **INFO is sampled at 1 %** for any process that ran `setup_logging()` — which
> includes the agent and the Flink jobs. WARNING and above are at 100 %. A ~20-hour
> agent session on 2026-07-25/26 produced **7 entries in total**. Absence of INFO is
> not absence of activity; raise `LOG_INFO_SAMPLE_RATE` to `1.0` on the workload if
> a session needs full INFO (read at module import — requires a fresh pod).
> (2) **Structured logs land in `jsonPayload.message`, not `textPayload`.** The
> JSON formatter is the default for anything using `setup_logging()`, so a
> `textPayload=~"..."` filter silently returns nothing. Several filters below are
> written against `textPayload` and will miss JSON-formatted lines — query both.

```
# All ERROR-level logs from the anizai namespace, last 1h:
resource.type="k8s_container"
AND resource.labels.namespace_name="anizai"
AND severity=ERROR
AND timestamp >= "now() - 1h"

# OpenAI rate-limit errors specifically (the proxy filter used by the
# openai_rate_limit_errors metric):
resource.type="k8s_container"
AND resource.labels.namespace_name="anizai"
AND (resource.labels.container_name="agent-worker"
     OR resource.labels.container_name="flink-taskmanager")
AND (textPayload=~"RateLimitError"
     OR jsonPayload.message=~"rate limit"
     OR textPayload=~"rate limit")

# Gold job DLQ-routed messages (the actual logger.error lines):
resource.type="k8s_container"
AND resource.labels.container_name="flink-taskmanager"
AND textPayload=~"\\[gold/flink\\].*failed"

# Polymarket 422 spam (should be quiet under default config):
resource.type="k8s_container"
AND resource.labels.container_name="polymarket"
AND textPayload=~"Comment fetch failed.*422"
```

Access via Cloud Console → Logging → Logs Explorer with the above filters.

---

## Section 12 — Inventory of known-broken-by-design components

Some producers are intentionally silent under current configuration. The
"Anizai Pipeline Health" Grafana dashboard has a panel listing these so an
operator doesn't alarm. Known Gaps now live per domain in each
`docs/<domain>/<x>_sprints.md` (KG-A-*, KG-B-*, KG-C-*), not in `task_plan.md`.

| Component | State | What re-evaluates this |
|---|---|---|
| **All 7 producer DAGs** | `is_paused=True` since the Domain-A day-run closed (2026-07-23) — deliberate, so Domain-A producers stay off during Domain-B work. Supersedes the newsapi-only row this table used to carry. **Pausing the DAGs does not stop ingestion on its own:** `telegram` and `polymarket` are always-on Deployments, not DAGs. | Unpause per profile when a pipeline or full run is intended — see `bringup_profiles.md` §2. |
| `opensky_high_frequency` | Cluster cannot reach `opensky-network.org` (KG-PHASE-C-6). Producer raises on 100% box-failure → Airflow shows `failed`. | When GCP firewall is configured to allow opensky-network.org (separate infra coordination work). |
| `googletrends_daily` | pytrends 4.9.2 returns 404 on Google's unofficial Trends endpoint (KG-PHASE-9.5-5; no pytrends version fixes this). Producer raises on 100% geo-failure. | When (a) pytrends upstream ships a fix, OR (b) we switch to Google's official Trends API (cost + OAuth required), OR (c) we retire googletrends from the producer set. |
| Polymarket comments | `POLYMARKET_COMMENTS_ENABLED=false` (KG-PHASE-9.5-4). Gamma `/comments` endpoint had a breaking change (now requires `parent_entity_id` + `entity_entity_type`; correct enum value unknown). | When upstream API contract is reverse-engineered OR a Polymarket developer support contact resolves it. |
| ~~`agent-worker:8000/metrics`~~ | **No longer broken — resolved by Sprint 26 (26.4), verified live 2026-07-26.** Three real metric families are exposed and scraped: `agent_node_duration_seconds{node_name}` (Histogram), `agent_llm_cost_usd_total{model}` (Counter), and `agent_session_total{tier,status}` (Counter). Verified against `agent/metrics.py`. These are the **authoritative** source for agent cost and per-node latency, since INFO logs are 1 %-sampled (§5.6, §11). Retention is 7 days on `prometheus-pvc`, and the retention clock is evaluated at Prometheus startup — so numbers that survive a teardown can still be purged minutes into the next bring-up. Copy anything worth keeping into a doc during the session. | Row retained one cycle, then delete. |

---

## Section 13 — Where the dashboards live

| Dashboard | Purpose | URL after port-forward |
|---|---|---|
| Anizai Pipeline | Detailed: Flink throughput, checkpoint sizes, JVM heap, Kafka producer client metrics. The "is each subsystem behaving?" view. | http://localhost:3000/d/anizai-pipeline-v1 |
| Anizai Pipeline Health | At-a-glance: vault row counts + freshness, Bronze topic rates, DLQ depth, Flink uptime, recent restarts. The "is the pipeline healthy right now?" view. | http://localhost:3000/d/anizai-pipeline-health-v1 |

Port-forward: `kubectl port-forward -n anizai svc/grafana 3000:3000`.
Credentials in Secret Manager: `gcloud secrets versions access latest --secret=GRAFANA_ADMIN_PASSWORD --project=anizai-pipeline`.

The "Known silent producers" text panel in Pipeline Health is kept up to
date manually as the state in Section 12 changes — update both the panel
markdown (in `grafana-configmap.yaml`) and Section 12 here together.

---

## Section 14 — What's NOT covered here

> **Paths corrected 2026-07-26.** The docs were reorganised into per-domain folders
> (`docs/A_pipeline`, `docs/B_hub`, `docs/C_cloud`, `docs/D_calibration`) with
> `data-pipeline/project_master.md` as the entry point; the old flat files moved to
> `docs/old_docs/`. Start at `project_master.md` §2 if a link below ever drifts again.

- **Bring-up / teardown procedure**: `data-pipeline/docs/guides/bringup_profiles.md`
  (AGENTS / PIPELINE / FULL profiles, gates, traps).
- **Live cluster state**: `data-pipeline/docs/C_cloud/cloud_state.md` — images,
  replica counts, scheduler state. This guide deliberately does not duplicate it.
- **One-time deployment history**: see
  `data-pipeline/docs/old_docs/cloud_deployment_implementation.md` (Phase 9)
  and `data-pipeline/docs/old_docs/phase95_cluster_robustness_implementation.md`
  (Phase 9.5).
- **Agent internals**: `data-pipeline/docs/B_hub/hub_agents.md` is the current
  description of how the hub actually works (graph, nodes, the Firestore
  contract). The historical §8 architecture spec is
  `data-pipeline/docs/old_docs/agentic_hub_spec.md`, and Sprints 18–21 are in
  `data-pipeline/docs/old_docs/agentic_hub_implementation.md`.
- **Data contracts**: see `data-pipeline/docs/old_docs/data_contracts_and_sources.md`.
- **Sprint history**: `data-pipeline/task_plan_archive.md` has the full
  closed-sprint records (Sprints 1-21 + Phase 9 C1-C5). Current per-domain sprint
  status and the Known Gaps tables live in each domain's `<x>_sprints.md`.

---

## Section 15 — Scope reminder

This guide is **operational**, not architectural. It tells you how to
recognise, triage, and recover from common pipeline states. It does not
explain why specific design decisions were made — for that, follow the
links in Section 14.

If you find a runbook entry here that doesn't match reality (the cluster
has evolved, a command no longer applies, etc.) — update this file. The
goal is to keep it the canonical operational reference. If something
changes systemically (new producer, new exporter, new dashboard panel),
update Section 1, Section 12, and Section 13 together.
