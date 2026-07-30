# Cluster teardown — 2026-07-30, and how to bring it back

> Domain: C — Frontend / BFF (cross-domain handoff)
> Type: Operational record — **point in time**
> Written: 2026-07-30T17:55Z
>
> ⚠️ **This is a point-in-time record, not live state. Verify against the cluster
> before acting on any figure here.** The canonical live-state file is
> `data-pipeline/docs/C_cloud/cloud_state.md`, and the canonical procedure is
> `data-pipeline/docs/guides/bringup_profiles.md` — both in the pipeline repo tree.
> This file exists because the teardown was performed from the Domain-C side and the
> carry-over had to be recorded somewhere tracked and visible **today**, rather than
> in a gitignored scratch file. **A copy has been relayed for `C_cloud/`; once it
> lands there, that copy wins and this one should be reduced to a pointer.**

---

## 1. What state the cluster is in

**Fully scaled down. Nothing is running. Nothing was deleted.**

- `main-pool` resized to **0 nodes** at 2026-07-30T17:49:32Z (`kubectl get nodes` →
  "No resources found").
- Every pod is `Pending` — unschedulable with no node. That is the expected resting
  state, not a fault.
- **All 5 PVCs remain `Bound`:** `postgres-data-postgres-0` (20Gi),
  `kafka-data-kafka-0` (10Gi), `prometheus-data` (10Gi), `flink-checkpoints` (5Gi),
  `airflow-postgres-data-airflow-postgres-0` (5Gi).
- **Cloud Scheduler `scale-up-main-pool` and `scale-down-main-pool` are both
  PAUSED**, so the pool will *not* return on a schedule. Bring-up is manual.

### Desired replicas — what returns on its own, and what does not

The node resize does not change desired replicas. On the next pool resize, anything
at desired 1 **comes back automatically**; anything at 0 must be scaled deliberately.

| Held at **0** — will NOT return by itself | At desired **1** — returns automatically |
|---|---|
| `agent-worker` | `airflow-scheduler`, `airflow-webserver` |
| `flink-jobmanager` | `prometheus`, `grafana`, `alertmanager` |
| `flink-taskmanager` | `kafka-exporter`, `postgres-exporter`, `kafka-ui` |
| `polymarket` | `trigger-consumer` |
| `telegram` | StatefulSets: `postgres`, `kafka`, `airflow-postgres` |

**All 7 Airflow producer DAGs are paused** (`arxiv_daily`, `fred_daily`,
`googletrends_daily`, `hackernews_high_frequency`, `newsapi_high_frequency`,
`opensky_high_frequency`, `openweather_high_frequency`). This is **deliberate and
predates this session** — see `cloud_state.md` §6. **Do not reflexively unpause them.**

---

## 2. Three things the next person needs to know about *this* teardown

### 2.1 Flink HA ConfigMaps were left intact — both jobs should auto-recover

All three survive:

```
anizai-flink-03ad8689c39da094790bef8846a30189-config-map   (anizai-silver-polymarket)
anizai-flink-785ea9a1e70fc1bf30e26f2c5868e756-config-map   (anizai-gold-all-sources)
anizai-flink-cluster-config-map                            (both job graphs)
```

**The jobs were deliberately NOT cancelled.** On scale-up, the Dispatcher recovers
both job graphs from these ConfigMaps and restarts them from their last checkpoint —
exactly as happened earlier on 2026-07-30 (`restored: 1` on both). **Do not manually
submit the jobs**; doing so duplicates them.

`bringup_profiles.md` §4 step 3 offers cancellation as optional tidiness on the basis
that "HA preserves the graphs either way." **That reading is disputed:** a cancelled
job is globally terminal and its graph is cleaned out of the Kubernetes HA store,
which would leave the next bring-up with nothing to recover and a mandatory manual
resubmit. **This has not been empirically verified** — cancellation was declined
precisely to avoid the risk — so treat it as UNKNOWN and test deliberately before
relying on either reading.

Final checkpoints before shutdown: **silver 182, gold 177**, both landed cleanly with
zero failed checkpoints.

Separately, per §5 trap 5: if the `anizai-flink` **image** changes while the cluster
is down, HA recovery replays the *previously compiled* job graph and the new code will
appear dead. That case — and only that case — requires cancel + resubmit
(`cluster_operations_guide.md` §6).

### 2.2 The CronJobs were left running, on purpose

`kafka-init` (`0 * * * *`) and `postgres-backup` (`0 2 * * *`) are both
`SUSPEND=False`. **The documented teardown never mentions CronJobs**, so they were
left exactly as found rather than filling a gap in the procedure.

**Consequence:** with the pool at 0 they will keep creating Jobs that cannot be
scheduled. Expect a backlog of `Pending` Jobs waiting at the next bring-up — one per
hour for `kafka-init`, one per day for `postgres-backup`. They are harmless but noisy,
and they are **not** evidence that something broke. KG-C-9 already records that
`postgres-backup` is unaware of scale-downs.

If a long shutdown is planned, suspending them is worth considering — but that is a
change to the documented procedure and should be decided, not assumed.

### 2.3 A benign recurring ERROR that is not a fault

`airflow-scheduler` emits ERROR-severity entries in Cloud Logging every ~30 s. They
are health-probe access lines (`GET /health HTTP/1.1 200`) written to stderr, which
GKE classifies as ERROR. **Pre-existing, benign, unrelated to the teardown.** Do not
chase them.

---

## 3. Bring-up — order and gates

Follow **`data-pipeline/docs/guides/bringup_profiles.md` §3**, not this file. Summary
only, so nothing here is mistaken for the procedure:

1. **Choose a profile** (§2): AGENTS, PIPELINE, or FULL. Set desired replicas on live
   objects **before** resizing the pool — the control plane is reachable at 0 nodes.
2. **Gate — verify the profile before resizing** (§3 Step 2). Skipping this is how the
   mistake gets discovered on a bill.
3. **Resize `main-pool` to 1** (`cluster_operations_guide.md` §3). ~3–5 min for the
   node, ~30 s per pod.
4. **Profile gates** (§3 Step 4):
   - AGENTS/FULL — the **Firestore stale-doc gate**. The worker attaches two
     listeners and claims their entire current match set on first attach. Run this
     *before* resizing; it needs no cluster.
   - PIPELINE/FULL — backlog and OpenAI RPD headroom.
5. Expect `polymarket` / `telegram` / `trigger-consumer` to show 2–3 restarts from
   `NoBrokersAvailable` while Kafka boots — settles in ~60 s, not a fault. The
   TaskManager may show 0–1 restarts from HA recovery; that too is documented-normal.
   **Abort only on a restart loop or OOMKilled (exit 137).**
6. Verify both Flink jobs reach `RUNNING` via
   `curl localhost:8081/jobs/overview` on the JobManager pod.

### Sizing a Kafka backlog — do not use the end offset

Retained backlog is `--time -1` **minus** `--time -2`. The cumulative end offset is
not pending work. On 2026-07-30 `ingest.bronze.polymarket` showed a cumulative
**53,319** while only **1,400** messages were retained — 7-day retention had already
cleared the rest, and every `process.silver.*` topic was at **0**. A whole
backlog-clearing step was planned against a number that meant nothing.

Also: `kafka.tools.GetOffsetShell` no longer exists in the deployed Kafka build — it
errors, and a naive loop silently sums to zero. Use `kafka-get-offsets.sh`.

---

## 4. Teardown gates satisfied on 2026-07-30

Per `bringup_profiles.md` §4, in order:

| # | Gate | Result |
|---|---|---|
| 1 | Cloud Logging queryable | ✅ verified — container stdout present through the 17:22:37Z shutdown, in **both** `textPayload` and `jsonPayload.message` (§11's trap is real; query both) |
| 2 | Numbers written to a doc | ✅ `frontend_ui.md` §6.3, `frontend_sprints.md`, and the Domain-A relay list |
| 3 | Close the taps | ✅ `flink-*`, `polymarket`, `telegram` already at 0; all 7 DAGs paused; none unpaused this session |
| 4 | **Postgres backup** | ✅ `gs://anizai-pipeline-backups/postgres/2026-07-30/anizai.sql.gz` — **229,339,412 bytes, 2026-07-30T17:48:43Z**, md5 `5m8EzjD0DcQ0JyD79yByAQ==`. Taken via the documented `kubectl create job --from=cronjob/postgres-backup` path with the DB confirmed quiet |
| 5 | `agent-worker` → 0 | ✅ |
| 6 | `main-pool` → 0 | ✅ zero nodes |
| 7 | Carry-over stated | ✅ this file |

**On the backup:** two were taken this session. The 17:19:49Z one overlapped the final
vault write at 17:18:12, so a second was taken at 17:48:43Z with everything stopped,
to remove the ambiguity. The destination key is date-stamped, so the later one
replaced the earlier — intended, and the later copy is strictly more complete.

---

## 5. What this teardown does *not* cover

- **Nothing was deleted.** No PVCs, ConfigMaps, Kafka topics, GCS objects or
  Firestore documents. This was a scale-down, not a decommission.
- **The `dead-letter-queue` topic was not touched** — it holds diagnostic value.
- **Kafka retention keeps running conceptually but the broker is down.** Topics will
  not age out while there is no node; the 7-day/3-day clocks resume on bring-up.
- **Firestore is a different GCP project (`anizai-ai`) and is entirely unaffected** —
  sessions, results and `predictionSeries` all survive independently of this cluster.
