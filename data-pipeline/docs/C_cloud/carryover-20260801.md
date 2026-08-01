# Cluster carry-over — 2026-08-01 teardown

> Domain: C — Cloud
> Type: Operational record — **point in time**
> Written: 2026-08-01, immediately after `main-pool` → 0
>
> ⚠️ **Point-in-time, not live state.** Verify against the cluster before acting on
> any figure. Canonical live state is `cloud_state.md`; canonical procedure is
> `docs/guides/bringup_profiles.md` §3.

Session purpose: V0 (backlog purge) → V2 (images) → V3 (Flink) → V5 (producer
window). **V5 PASSED, 1,282/1,282 on six criteria** — see
`docs/A_pipeline/reports/v5-cloud-verify-20260801.md`. **V6 was deferred and the
agent was never brought up.**

---

## 1. Zonal cluster — the flag every `gcloud container clusters` call needs

`anizai-cluster` is **ZONAL**, in `us-central1-a` — not regional. Every
`gcloud container clusters` command requires `--zone=us-central1-a` and **fails
outright without it**. `cluster_operations_guide.md` §3 omits it in both the
resize-up and resize-down lines. The kubectl context corroborates the zone:
`gke_anizai-pipeline_us-central1-a_anizai-cluster`.

```
gcloud container clusters resize anizai-cluster --node-pool=main-pool --num-nodes=1 --zone=us-central1-a
```

## 2. Desired replicas at teardown

| Held at **0** — will NOT return by itself | At desired **1** — returns automatically |
|---|---|
| `agent-worker` (never brought up this session) | `airflow-scheduler`, `airflow-webserver` |
| `polymarket` (brought up for V5, returned to 0) | `prometheus`, `grafana`, `alertmanager` |
| `telegram` (never brought up) | `kafka-exporter`, `postgres-exporter`, `kafka-ui` |
| | **`flink-jobmanager`, `flink-taskmanager` — left at 1** |
| | `trigger-consumer`; StatefulSets `postgres`, `kafka`, `airflow-postgres` |

**Note the difference from the 2026-07-30 teardown: Flink is at desired 1, not 0.**
It therefore **comes back automatically** on the next pool resize. If the next
session does not want Flink consuming, scale both to 0 *before* resizing.

**All 7 Airflow producer DAGs remain paused** (`is_paused = t`, 7/7 verified from
the metadata DB at bring-up and again at teardown). None was unpaused this
session. **Do not reflexively unpause them.**

## 3. Flink HA — the recovery is now CORRECT, not dangerous

Jobs were deliberately **not cancelled** at teardown, so both recover from the
Kubernetes HA store on the next bring-up:

```
d1b1df405646b219b21fdaaed8f456f4 : anizai-silver-polymarket
78ab7fe489e934a634f27b464b0950f4 : anizai-gold-all-sources
```

**Both were compiled from `anizai-flink:1.19.1-pmcov` — the image the Deployments
still reference. So an automatic HA restore next time is correct, and the
five-step cancel-and-resubmit is NOT required.**

**The distinction that matters, because getting it wrong is costly in both
directions:**

- **Image unchanged → let HA restore.** Cancelling and resubmitting is
  unnecessary work, and per the disputed reading in
  `cluster-teardown-20260730.md` §2.1 a cancel may clean the graph out of the HA
  store entirely. That dispute is still **UNVERIFIED and deliberately untested.**
- **Image changed → cancel + resubmit is MANDATORY, and the failure is SILENT.**
  Verified live on 2026-08-01: on a cold bring-up with a new image, both jobs
  recovered their **pre-teardown graphs** — old compiled code — and reported
  `RUNNING` with 0 restarts on a pod carrying the new image. JobIDs matched the
  previous teardown's HA ConfigMap names character for character. Nothing in pod
  status, image reference or job state revealed it. Without the cancel, V5 would
  have shown rows missing catalog fields and the hunt would have gone to
  `silver_job.py` — a file already fixed.

## 4. Images deployed (unchanged by teardown)

| Workload | Tag | Digest |
|---|---|---|
| `flink-jobmanager` / `-taskmanager` | `anizai-flink:1.19.1-pmcov` | `sha256:963cbf9d…c0238c52` |
| `polymarket` | `anizai-polymarket:0.4.0-coverage` | `sha256:1d27ec05…6c43d758` |
| `agent-worker` | `anizai-agent:0.5.0-sprint26` (untouched) | `sha256:7fce4e8b…c316ef4` |

Rollbacks: flink `1.19.1-7d` = `sha256:9a73a780…ebced303`; polymarket
`0.3.0-price` = `sha256:2ae00dae…899cac8`, `0.2.0-p95` = `sha256:a2a3e82e…e93cfd5c`.

## 5. Kafka state

**V0 purged 5,826 retained records** across `ingest.bronze.polymarket` (1,800),
`process.silver.structured_metrics` (700), `ingest.bronze.arxiv` (2,800),
`ingest.bronze.hackernews` (300), `ingest.bronze.newsapi` (226). All verified at
low-watermark == end offset by independent re-measurement.

The V5 window then produced **1,282 fresh Polymarket Bronze records**, which
Silver and Gold consumed to completion before teardown. Retained now is whatever
those left plus `serve.gold.structured_metrics` (700, terminal),
`dead-letter-queue` (30, preserved for diagnostics) and `ingestion_triggers` (8).

Size a backlog as `--time -1` **minus** `--time -2`. A cumulative end offset is
not pending work. Use `kafka-get-offsets.sh`; `kafka.tools.GetOffsetShell` no
longer exists in the deployed build.

## 6. Gates satisfied

| # | Gate | Result |
|---|---|---|
| 1 | Cloud Logging queryable | ✅ both `jsonPayload.message` and `textPayload` (§11 trap is real) |
| 2 | Numbers written to a doc | ✅ `reports/v5-cloud-verify-20260801.md`, committed |
| 3 | Close the taps | ✅ `polymarket` → 0; 7/7 DAGs still paused; Flink left running deliberately |
| 4 | **Postgres backup** | ✅ `gs://anizai-pipeline-backups/postgres/2026-08-01/anizai.sql.gz` — **229,654,673 bytes, 2026-08-01T10:06:53Z**, +315,261 bytes over the 07-30 backup (consistent with 1,282 new rows). Verified by size and timestamp, not exit code. **This is the pre-wipe snapshot.** |
| 5 | `agent-worker` → 0 | ✅ never brought up |
| 6 | `main-pool` → 0 | ✅ zero nodes, 14 pods Pending |
| 7 | Carry-over stated | ✅ this file |

## 7. Nothing was deleted

All 5 PVCs remain `Bound` — `postgres-data-postgres-0` (20Gi),
`kafka-data-kafka-0` (10Gi), `prometheus-data` (10Gi), `flink-checkpoints` (5Gi),
`airflow-postgres-data-airflow-postgres-0` (5Gi). No ConfigMaps, Kafka topics,
GCS objects or Firestore documents removed. Scale-down, not decommission.

CronJobs `kafka-init` and `postgres-backup` left unsuspended, as found. Expect a
small backlog of `Pending` Jobs at next bring-up — harmless, and history limits
cap retention (6 Job objects at this bring-up, not the ~48 a naive
one-per-hour estimate predicts).

## 8. Open, and blocking V6

**The 0.5000 rows.** All 108 `inactive` markets carry exactly `0.5000` —
`distinct_prices = 1`, against 305 distinct values across 1,171 active rows.
Their questions are unnamed placeholder legs ("Will Company A be the largest
company in the world"), i.e. template slots with no entity bound. Nothing in the
agent path branches on `status`, so such a row resolves normally and would render
an invented "Market Consensus 50%" beside a real forecast. `archived` is **not**
the same case — its rows carry real distinct prices. Decision pending between a
producer-side `inactive` drop and an agent-side refusal.

## 9. Firestore (project `anizai-ai`, unaffected by this cluster)

Stale-doc gate at bring-up: `forecastQueries` pending = **0**, collection-group
`messages` role=user status=sent = **0**. Two documents stranded at `claimed`
(`e2e-sprint21-resume-661493bb`, `…-8118feec`, 87 days old, Sprint 21 E2E
residue) were cleared by flipping status to `failed` — not deleted, and not to
`pending`, which would have handed them to the listener on the next agent start.
