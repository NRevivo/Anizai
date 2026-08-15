# Cluster Operations Guide
## Anizai GKE — operational reference for `anizai-cluster` (project `anizai-pipehub`)

This is the long-term reference for operating the running pipeline.
Audience: Ron, and future Claude sessions reaching for "how do I X?".

> Last updated: 2026-08-15 — project-identity pass (KG-C-11). Every `--project=`,
> the Artifact Registry path and the GCS backup bucket were re-pointed from the
> retired `anizai-pipeline` to `anizai-pipehub`; identity facts are owned by
> `docs/C_cloud/cloud_constants.md`. **Scope limit: identity strings only — no
> procedure in this guide was re-verified against a live cluster in that pass.**
> The two `anizai-pipeline-*` Grafana dashboard UIDs in §12 are class-D keys and
> were deliberately left alone; see the warning there.
>
> Prior stamp — 2026-07-27 — boundary-hygiene pass with `bringup_profiles.md`. Live
> state (image tags, replica counts, DAG pause state, Scheduler state) was removed from
> §1, §3, §4 and §12 and now lives only in `cloud_state.md`; §3 was rescoped from a
> start/stop checklist to a raw-command reference under `bringup_profiles.md`; §8 gained
> a manual-backup procedure; the INFO-sampling claim in §5.6 and §11 was corrected — it
> is proven for the agent and **withdrawn for Flink**. See §15 for the standing rule.

For one-time deployment history see
`data-pipeline/docs/old_docs/cloud_deployment_implementation.md` (Phase 9)
and `data-pipeline/docs/old_docs/phase95_cluster_robustness_implementation.md`
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

- GKE Standard cluster `anizai-cluster`, zone `us-central1-a`, project `anizai-pipehub`.
- One node pool: `main-pool` (e2-standard-8 × 1; manually scaled to 0 between
  collection windows). `polymarket-pool` was deleted in Phase 9.5 Stage A
  (KG-PHASE-9.5 carry: Polymarket now runs on main-pool with everything else).
- Namespace: `anizai`.
- Cloud Scheduler jobs `scale-up-main-pool` / `scale-down-main-pool` exist. Whether
  they are paused or enabled at any moment is **live state and is not recorded here**
  — see `cloud_state.md` §6, and above it `gcloud scheduler jobs list`. The resume
  procedure and its readiness checklist are §4 below.

### Per-pod purpose

> **Image tags and replica counts are deliberately not stated here.** The Image column
> below names the image, never its tag — which tag is deployed is live state and belongs
> to `cloud_state.md` §3, and above it to the cluster. This guide advertised tags three
> rebuilds out of date once; the column is kept narrow so it cannot happen again.

| Pod | Image | Purpose | Failure mode if down |
|---|---|---|---|
| `postgres-0` | timescale/timescaledb-ha:pg16 | Vault tables (knowledge_vault, momentum_vault, social_vault, knowledge_vectors, social_vectors, mapping_dict, divergence_alerts). pgvector + TimescaleDB hypertable. | Gold cannot persist. Backed by 20Gi PVC + daily `pg_dump` to GCS. |
| `kafka-0` | apache/kafka:3.7.0 | KRaft single-broker. 19 topics. **Data dir is `/var/lib/kafka/data/kafka-logs` subdir of the 10Gi PVC** — must explicitly set `KAFKA_LOG_DIRS` (Phase 9.5 F1; default would be `/tmp` and ephemeral). | All producers + Flink consumers stuck. |
| `kafka-ui` | provectuslabs/kafka-ui:v0.7.2 | Topic + message inspection UI on port-forward. | Operator UX only. |
| `flink-jobmanager` | anizai-flink | Submits + supervises Flink jobs. K8s HA via ConfigMap leader election (Phase 9 follow-up). | Job graph survives via HA ConfigMaps; running tasks stop. |
| `flink-taskmanager` | anizai-flink | Runs Silver + Gold PyFlink tasks (4 slots). Mounts checkpoint PVC. | Jobs RESTARTING until TM recovers; messages back up in Kafka. |
| `agent-worker` | anizai-agent | LangGraph forecast agent. Attaches **two** Firestore listeners on startup: `forecastQueries` where `status=='pending'`, and (since Sprint 24) a **collection-group** listener on `messages` where `role=='user'` and `status=='sent'`. Each delivers its full current match set on first attach — see `bringup_profiles.md` §5 trap 2. OpenAI calls via `utils/openai_client.py` factory (max_retries=5). Normally held off and brought up deliberately with `kubectl scale` rather than by editing the manifest — current declared and live replicas are in `cloud_state.md` §3; bring-up is `bringup_profiles.md` §3 step 5. | No forecasts processed; queries pile up in Firestore. |
| `airflow-postgres-0` | postgres:16 | Airflow metadata DB (DAG run state, task instances). 5Gi PVC. Not backed up (KG-PHASE-9.5-* candidate). | Scheduler can't run DAGs; webserver UI shows blank state. |
| `airflow-scheduler` | anizai-airflow | Fires the 7 producer DAGs (arxiv, fred, googletrends, hackernews, newsapi, opensky, openweather). Liveness probe on port **8974** (NOT 8793 — Phase 9.5 F2). | Scheduled producer runs miss their windows. |
| `airflow-webserver` | anizai-airflow | Airflow UI on port-forward. | Operator UX only. |
| `polymarket` | anizai-polymarket | Always-on WebSocket producer (CLOB ticks + REST market refresh). Comment-fetch loop disabled by feature flag (Phase 9.5 Item 3). | Polymarket data goes silent during downtime; price gaps not backfillable. |
| `telegram` | anizai-telegram | MTProto continuous channel listener. Session file from Secret Manager. | Telegram data goes silent during downtime. |
| `trigger-consumer` | anizai-trigger-consumer | Consumes `ingestion_triggers` topic from the agent for reactive ingestion. A trigger the consumer cannot dispatch fails **on the consumer side** and is logged, not fatal to the agent. Whether the deployed image actually supports the newsapi reactive path — and whether its SecretProviderClass mounts the NewsAPI key — is deployment state: see `cloud_state.md` §3/§4. Note that closing that gap needs a `anizai-trigger-consumer` rebuild **plus** an SPC secret; rebuilding `anizai-airflow` does not fix it (a common misreading). | Agent-triggered ingest doesn't fire. |
| `prometheus` | prom/prometheus:v2.51.2 | Scrapes 5 targets (Flink JM/TM, agent /metrics, kafka-exporter, postgres-exporter). 10Gi PVC, 7-day TSDB retention (Phase 9.5 F2). | No metrics scraped; dashboards blank. Alerts stop firing. |
| `kafka-exporter` | danielqsj/kafka-exporter:v1.7.0 | Kafka topic + partition + broker → Prometheus metrics. | DLQ-depth + topic-staleness alerts go silent. |
| `postgres-exporter` | prometheuscommunity/postgres-exporter:v0.15.0 | Postgres internals + Anizai-specific vault freshness queries. | Vault-freshness alerts + dashboard panels go silent. |
| `alertmanager` | prom/alertmanager:v0.27.0 | Receives firing alerts from Prometheus, routes via Gmail SMTP to `ron.mintz21@gmail.com`. | Alerts evaluate but no email. |
| `grafana` | grafana/grafana:10.4.2 | 2 dashboards: `Anizai Pipeline` (Phase 9, detailed Flink) + `Anizai Pipeline Health` (Phase 9.5 C, single-screen). | UI-only; no operational impact. |
| `postgres-backup` (CronJob) | google/cloud-sdk:slim | Daily `pg_dump` → `gs://anizai-pipehub-backups/postgres/YYYY-MM-DD/anizai.sql.gz`, 30-day lifecycle. | Lose ability to restore beyond Postgres PVC contents. |
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

## Section 3 — Raw commands: pool resize and post-resize verification

> **This section is a command reference, not a procedure.** `bringup_profiles.md`
> owns the order and the gates — which workloads to hold at 0, when to check what,
> when to stop. It deliberately carries no `gcloud` invocations, so the commands it
> tells you to run live here. Run its §3 / §4 and reach into this section for the
> exact syntax; do not follow this section top-to-bottom as a start-up sequence.
>
> **Shell warning:** the blocks below are tagged `powershell` but are mixed. Steps 1–2
> use PowerShell line continuation (backtick); steps 4–5 use bash (`JM=$(...)`,
> `| wc -l`). Pasting a whole block into one shell will fail partway. Run them
> individually, in a shell that matches.

### Pool resize, 0 → 1

```powershell
# 1. Scale main-pool back to 1 node.
gcloud container clusters resize anizai-cluster `
  --node-pool=main-pool --num-nodes=1 `
  --zone=us-central1-a --project=anizai-pipehub --quiet

# 2. Wait for Kafka, then for Postgres, to reach 1/1 Ready.
#    Typically ~3-5 min for the node + ~30s per pod after.
kubectl get pods -n anizai --watch

# 3. WHICH pods should be Running depends entirely on the profile you applied.
#    bringup_profiles.md §2 (the profile table) and §3 step 3 own that expectation —
#    do not read a pod list out of this file. Two things are worth knowing either
#    way: flink-taskmanager may show 0-1 restarts (HA recovery), and
#    polymarket / telegram / trigger-consumer typically show 2-3 restarts from
#    NoBrokersAvailable while Kafka boots — that settles in ~60s and is not a fault.

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

### Pool resize, 1 → 0

> Teardown order and its gates — closing the producer taps, the Postgres backup
> decision, and the carry-over statement — are `bringup_profiles.md` §4. What follows
> is only the commands that step calls for.

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
  --zone=us-central1-a --project=anizai-pipehub --quiet

# Pods will Terminate; PVCs detach. Data persists (postgres-data,
# kafka-data, flink-checkpoints, prometheus-data, airflow-postgres-data).

# 3. Verify zero nodes:
kubectl get nodes
# Expected: "No resources found"
```

---

## Section 4 — Cloud Scheduler resume procedure

The two recurring Scheduler jobs, when enabled, auto-scale main-pool Mon-Fri
05:00 IL up / 15:00 IL down. Their current paused/enabled state is live — read it
from `cloud_state.md` §6 or `gcloud scheduler jobs list`, not from here.
**Do not resume without first confirming the cluster is in good operational shape** —
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
  --location=us-central1 --project=anizai-pipehub
gcloud scheduler jobs resume scale-down-main-pool `
  --location=us-central1 --project=anizai-pipehub

# Confirm:
gcloud scheduler jobs list --location=us-central1 --project=anizai-pipehub
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
- **The DAG is paused**: `airflow dags list` shows `is_paused=True`. A paused DAG
  does not fire on schedule and cannot be manually triggered either — unpause first.
  Producer DAGs are routinely left paused between sessions on purpose, so this is a
  likely explanation before it is a fault; see Section 12 and `cloud_state.md` §6.
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
> ERROR and WARNING are emitted at 100 %, so the grep above **is** reliable for
> failures. Everything at INFO (including the per-forecast `llm_usage` cost lines)
> passes at `LOG_INFO_SAMPLE_RATE`, default `0.01`. A forecast that ran perfectly
> will usually leave no INFO trace at all. Cost and per-node latency come from
> Prometheus (`agent_llm_cost_usd_total`, `agent_node_duration_seconds`), not from
> logs. **`bringup_profiles.md` §5 trap 3 is the canonical statement of the sampling
> behaviour — read it before acting on it, and do not generalise the claim above to
> the Flink workloads.** (Related: KG-B-4.)

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
  us-central1-docker.pkg.dev/anizai-pipehub/anizai-images --include-tags `
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

## Section 8 — Backup and restore procedures

Backups land at `gs://anizai-pipehub-backups/postgres/YYYY-MM-DD/anizai.sql.gz`
daily at 02:00 UTC, via the `postgres-backup` CronJob. 30-day GCS lifecycle.

### Manual backup (on demand)

**When you need this:** `bringup_profiles.md` §4 step 4 calls for it at teardown
whenever something wrote to Postgres during the session. The CronJob fires at a fixed
wall-clock time and is not aware of scale-downs (KG-C-9), so a session that opens and
closes between two firings is never captured. Two daily backups were lost this way in
May 2026.

The CronJob is fully self-contained, so the simplest correct manual backup is to run
it on demand rather than assembling a `pg_dump` by hand:

```powershell
kubectl create job -n anizai --from=cronjob/postgres-backup backup-manual-$(Get-Date -UFormat %Y%m%d-%H%M)

# Watch it to completion (~1-2 min; it apt-installs postgresql-client-16 first):
kubectl get jobs -n anizai -w
kubectl logs -n anizai -l job-name=<job-name> --tail=20
# Expect a final line: "Backup complete: gs://.../YYYY-MM-DD/anizai.sql.gz"

# Confirm the object actually landed before scaling anything down:
gsutil ls -l gs://anizai-pipehub-backups/postgres/$(Get-Date -UFormat %Y-%m-%d)/
```

**One thing to know before you run it:** the destination key is date-stamped, not
time-stamped, so a manual run **overwrites** that day's object. That is usually what
you want (a later snapshot replaces an earlier one), but if the 02:00 run captured a
good state and the database has since been damaged, the manual run destroys the good
copy. In that situation restore first, back up second.

### Restore drill

Tested Phase 9.5 Stage A F4.

```powershell
# 1. Pick a backup to restore. Today (-1 day for guaranteed completeness):
YDAY=(Get-Date).AddDays(-1).ToString("yyyy-MM-dd")
gsutil cp gs://anizai-pipehub-backups/postgres/$YDAY/anizai.sql.gz `
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

### 9.1 — Reading a topic: the fake-zero trap (verified 2026-08-15)

**`kafka-console-consumer.sh --from-beginning` returns "Processed a total of 0 messages" on a topic
that demonstrably has messages.** The consumer-group form must fetch metadata, join a group and be
assigned partitions before it reads anything; `--timeout-ms` is the wait for the *first message* and
expires during that handshake. The output is indistinguishable from a genuinely empty topic.

This has now produced a false conclusion **four times** on this project. On 2026-08-15 it was caught
only because a positive control was run first — the same command against `ingestion_triggers`, known
to hold exactly 1 message, also returned 0.

> **Always read a topic by partition, never by consumer group:**
> ```
> kubectl exec -n anizai kafka-0 -- /opt/kafka/bin/kafka-console-consumer.sh \
>   --bootstrap-server localhost:9092 --topic <topic> \
>   --partition <N> --offset earliest --max-messages 100 --timeout-ms 20000
> ```
> Loop N over every partition — a topic with 3 partitions can have all its data on one, so reading
> partition 0 alone is another way to read a real zero (`ingest.bronze.telegram` had all records on
> partition 2).

**The authoritative message count is the offsets, not a consumer.** `sum(--time -1) - sum(--time -2)`
per topic. To size every topic in one call rather than looping (2 calls × 19 topics):

```
kubectl exec -n anizai kafka-0 -- /opt/kafka/bin/kafka-get-offsets.sh \
  --bootstrap-server localhost:9092 --topic-partitions ".*" --time -1
```
Expect **55 partition rows** across the 19 topics — a row count well under that means the call
itself failed, which is its own built-in control.

### 9.2 — Windows: three ways a correct command silently becomes a wrong one

The `powershell`-tagged blocks in this guide are mixed shell (§3 already warns). On Windows, three
further failure modes were hit on 2026-08-15, all of which produce *plausible wrong output* rather
than an error:

1. **`$(...)` and `$((...))` are mangled in transit** through `kubectl exec ... -- bash -c "<arg>"`.
   Shell arithmetic came back as `0: command not found` (57 times, summing silently to zero) and the
   Airflow connection string lost its quotes. **Do the arithmetic in PowerShell**, or feed the script
   via stdin.
2. **Piping a PowerShell here-string to `kubectl exec -i` prepends a UTF-8 BOM and uses CRLF.** The
   BOM breaks the first line (`bash: ﻿export: command not found`) and CRLF corrupts the last argument
   (`-o plain` → `invalid choice: 'plain\r'`). **Write the script with
   `[IO.File]::WriteAllText($p, $body, (New-Object System.Text.UTF8Encoding $false))` using `\n`
   only, then redirect:** `cmd /c "kubectl exec -i -n anizai <pod> -- bash < ""<path>"""`.
   This is the same `cmd /c` stdin trick §7 and §8 already use for file transfer.
3. **PowerShell variable names are case-insensitive.** `$reg = @()` silently destroys a lookup table
   named `$REG`, and the resulting empty result reads as a finding. Prefer doing lookups inside the
   remote `python`/`bash` script rather than marshalling them through PowerShell.

**The rule underneath all three:** before reporting a zero, prove the command can return non-zero.

### 9.3 — Airflow CLI inside the scheduler pod

`airflow <cmd>` run via `kubectl exec` fails with *"You need to initialize the database"* — the CLI
defaults to SQLite because the connection string is exported by the container's **entrypoint**, and a
fresh `exec` shell does not inherit it. Prefix every invocation:

```
export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN="postgresql+psycopg2://airflow:$(cat /var/secrets/airflow/AIRFLOW_POSTGRES_PASSWORD)@airflow-postgres/airflow"
```

For the same reason, **`printenv <VAR>` in an exec shell returns empty for every secret the
entrypoint exported** — that is a measurement artifact, not a missing secret. Read the running
process instead (`/proc/<pid>/environ` of an `airflow scheduler` process; PID 1's may not be
readable from the exec context).

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
> (1) **INFO may be sampled at 1 %** for a process that ran `setup_logging()`.
> WARNING and above are always at 100 %. This is **proven for the agent** — a ~20-hour
> session on 2026-07-25/26 produced 7 entries in total — so absence of agent INFO is
> not absence of activity. **It is NOT proven for the Flink jobs**; direct observation
> on 2026-07-27 contradicted it. This paragraph previously asserted both, and that was
> wrong. **`bringup_profiles.md` §5 trap 3 is canonical here — read it before you act,
> and note that raising `LOG_INFO_SAMPLE_RATE` on the Flink workloads is banned: it
> OOM-killed the TaskManager twice on 2026-07-27.**
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
| **Producer DAGs (Airflow)** | Producer DAGs are routinely left paused between sessions on purpose. **Which ones are paused right now is live state and is not recorded here** — read it from the cluster (`airflow dags list`); `cloud_state.md` §6 records the current intent and why. **Two separate mechanisms decide what actually runs after a bring-up, and neither is derived from this file or from the repo:** (a) DAG pause state lives in Airflow's metadata DB and survives pod restarts *and* cluster scale-ups — a paused DAG stays paused and will not come back on its own; (b) workload replicas live on the Kubernetes object — `kubectl scale` changes them and `kubectl apply` silently resets them to whatever the manifest declares (KG-C-10). **Pausing the DAGs does not stop ingestion on its own:** `telegram` and `polymarket` are always-on Deployments, not DAGs. | Check both mechanisms on every bring-up rather than assuming what is running. Profiles and the order to apply them: `bringup_profiles.md` §2–§3. |
| `opensky_high_frequency` | Cluster cannot reach `opensky-network.org` (KG-PHASE-C-6). Producer raises on 100% box-failure → Airflow shows `failed`. | When GCP firewall is configured to allow opensky-network.org (separate infra coordination work). |
| `googletrends_daily` | pytrends 4.9.2 returns 404 on Google's unofficial Trends endpoint (KG-PHASE-9.5-5; no pytrends version fixes this). Producer raises on 100% geo-failure. | When (a) pytrends upstream ships a fix, OR (b) we switch to Google's official Trends API (cost + OAuth required), OR (c) we retire googletrends from the producer set. |
| Polymarket comments | `POLYMARKET_COMMENTS_ENABLED=false` (KG-PHASE-9.5-4). Gamma `/comments` endpoint had a breaking change (now requires `parent_entity_id` + `entity_entity_type`; correct enum value unknown). | When upstream API contract is reverse-engineered OR a Polymarket developer support contact resolves it. |

---

## Section 13 — Where the dashboards live

| Dashboard | Purpose | URL after port-forward |
|---|---|---|
| Anizai Pipeline | Detailed: Flink throughput, checkpoint sizes, JVM heap, Kafka producer client metrics. The "is each subsystem behaving?" view. | http://localhost:3000/d/anizai-pipeline-v1 |
| Anizai Pipeline Health | At-a-glance: vault row counts + freshness, Bronze topic rates, DLQ depth, Flink uptime, recent restarts. The "is the pipeline healthy right now?" view. | http://localhost:3000/d/anizai-pipeline-health-v1 |

> **⚠ The two `anizai-pipeline-*` strings in the URLs above are NOT stale project
> references — do not "fix" them.** They are Grafana dashboard `uid`s (class D in
> `cloud_constants.md` §5), opaque keys that merely coincide with the old project
> ID. Each must keep matching its `"uid"` in
> `infrastructure/k8s/grafana-configmap.yaml` (lines 553 and 801); renaming one
> without the other breaks the provisioning link and 404s the drilldown, for zero
> benefit. The project-ID sweep of 2026-08-15 deliberately skipped these two lines.

Port-forward: `kubectl port-forward -n anizai svc/grafana 3000:3000`.
Credentials in Secret Manager: `gcloud secrets versions access latest --secret=GRAFANA_ADMIN_PASSWORD --project=anizai-pipehub`.

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

**But do not record live state here, ever.** If the thing you are about to write down
is an image tag, a replica count, a DAG's paused/unpaused state, or the Cloud Scheduler
jobs' enabled/paused state — it belongs in `cloud_state.md`, not in this file. This
guide once advertised image tags three rebuilds out of date precisely because that rule
did not exist. Two related boundaries, for the same reason: bring-up and teardown
*order and gates* belong to `bringup_profiles.md` (this file holds the commands they
call for), and the sampling behaviour of INFO logs is stated canonically in that file's
§5 trap 3 (§5.6 and §11 here defer to it).
