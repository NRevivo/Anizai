> # ⛔ HISTORICAL — DESCRIBES THE RETIRED `anizai-pipeline` PROJECT
>
> **Archived 2026-08-15. Do not act on anything in this file.** It describes the
> GCP project **`anizai-pipeline`**, which was retired in the
> `anizai-pipeline` → `anizai-pipehub` migration. Every project ID, Artifact
> Registry path, GCS bucket and replica state below is **wrong for the live
> cluster**. Specifically: the backup bucket is now `gs://anizai-pipehub-backups`,
> the Flink JobIDs in §3 were replaced on the rebuild, and §2's "`agent-worker`
> and Flink rest at desired 1" is **inverted** on the live cluster — six
> workloads, Flink and the agent among them, now rest at **0**.
>
> **Current facts live in:**
> - `../cloud_constants.md` — identity: project, cluster, service accounts, secrets
> - `../carryover-20260815-migration.md` — the live project's carry-over
> - `../cloud_state.md` — live cluster state
>
> **The body below is unedited** and is a true record of what was true on
> 2026-08-01. Only this banner was added.
>
> *Path notes:* §"Supersedes `carryover-20260801.md`" now resolves within this
> archive folder — both files moved together. The `cluster-teardown-20260730.md`
> §2.1 citation in §3 refers to `docs/backend-specs/cluster-teardown-20260730.md`,
> at the **repository root**, not under `data-pipeline/docs/`.

---

# Cluster carry-over — 2026-08-01 window 2 (V4 + V6)

> Domain: C — Cloud
> Type: Operational record — **point in time**
>
> ⚠️ **Point-in-time, not live state.** Verify against the cluster before acting on
> any figure. Canonical live state is `cloud_state.md`; canonical procedure is
> `docs/guides/bringup_profiles.md` §3. Supersedes `carryover-20260801.md` (window 1).

Session purpose: producer rebuild carrying the `inactive` filter → cold bring-up →
V4 (agent) → V6 (two forecasts, Ron driving). **V6 passed on three of four; one
open item, KG-A-22.**

---

## 1. Zonal cluster

`anizai-cluster` is **ZONAL** in `us-central1-a`. Every `gcloud container clusters`
command needs `--zone=us-central1-a` and **fails outright without it**. The
operations guide §3 omits it in both resize lines.

## 2. Desired replicas at teardown

| Held at **0** — will NOT return by itself | At desired **1** — returns automatically |
|---|---|
| `polymarket` (up for the window, returned to 0) | **`agent-worker` — left at 1** |
| `telegram` (never up) | **`flink-jobmanager`, `flink-taskmanager` — left at 1** |
| | `airflow-scheduler`, `airflow-webserver`, `trigger-consumer` |
| | `prometheus`, `grafana`, `alertmanager`, `kafka-exporter`, `postgres-exporter`, `kafka-ui` |
| | StatefulSets `postgres`, `kafka`, `airflow-postgres` |

**Both Flink AND the agent are at desired 1 and return automatically on the next
pool resize.** A session that does not want them running must scale them to 0
*before* resizing. This differs from the 2026-07-30 teardown, where all four were
held at 0.

**All 7 producer DAGs remain paused** — verified `7|7` from the metadata DB at
bring-up and again at teardown. None unpaused this session.

## 3. Flink HA — restore is CORRECT, and was proven so this window

Jobs deliberately **not cancelled**. Currently in HA state:

```
d1b1df405646b219b21fdaaed8f456f4 : anizai-silver-polymarket
78ab7fe489e934a634f27b464b0950f4 : anizai-gold-all-sources
```

Both compiled from `anizai-flink:1.19.1-pmcov`, the image the Deployments
reference. **This window demonstrated the correct case:** the Flink image was NOT
rebuilt (see §4), the jobs auto-recovered with the same JobIDs, and no cancel or
resubmit was performed or needed.

**The distinction, both directions:**

- **Image unchanged → let HA restore.** Cancelling is unnecessary, and per the
  disputed reading in `cluster-teardown-20260730.md` §2.1 a cancel may clean the
  graph out of the HA store. That dispute remains **UNVERIFIED and deliberately
  untested**.
- **Image changed → cancel + resubmit is MANDATORY and the failure is SILENT.**
  Verified live earlier the same day: both jobs recovered pre-teardown graphs
  running OLD compiled code while reporting `RUNNING` with 0 restarts on a pod
  carrying the new image.

## 4. Images — one rebuilt, one deliberately not

| Workload | Image | Digest |
|---|---|---|
| `polymarket` | **`0.4.1-inactive`** (new) | `sha256:c7b15eb7…e69dcfc4` |
| `agent-worker` | **`0.6.0-trackA`** (new) | `sha256:937dfed1…471d9aee` |
| `flink-*` | `1.19.1-pmcov` (**unchanged**) | `sha256:963cbf9d…c0238c52` |

**Flink was NOT rebuilt, on evidence:** `processing/` is byte-identical between
`1.19.1-pmcov`'s build commit (`4d28fb5`) and `35c343b` — no commit since touches
it. Skipping preserved the "image unchanged" property that makes the HA restore
safe.

**The agent rebuild was NOT optional.** The deployed `0.5.0-sprint26` was built
2026-07-23, a week before Track A landed in `d603450`. Running it would have
tested an agent with no A1/A3/A4/A5 and emitted the OLD `NO_MARKET_CAPTION` —
a plausible-looking refusal proving nothing. Same silent-wrong-code shape as the
Flink HA hazard, one image over.

Rollbacks: polymarket `0.4.0-coverage` `sha256:1d27ec05…`, `0.3.0-price`
`sha256:2ae00dae…`, `0.2.0-p95` `sha256:a2a3e82e…`; agent `0.5.0-sprint26`
`sha256:7fce4e8b…`; flink `1.19.1-7d` `sha256:9a73a780…`.

## 5. Results

**Producer window.** Funnel: `374 events -> 230 tags -> 177 endDate -> 2547
nested -> 1173 collectable (skipped 266 closed, 1106 inactive, 2 never-traded)`.
Acceptance **1,173/1,173** on six criteria; **0 rows at exactly 0.5000** (was
108). `inactive` is the new counter and the live proof of `35c343b`.

**V6.** Three of four pass — no wrong-market resolution (August 3 against seven
one-word-apart siblings), YES side correct (0.245 / 0.77 vs cards of 25% / 76%),
CLOB history real (743 points / 30d 23h / hourly / 0.14s, vault fallback unused).
**Open: `via=question-match` on both — `conditionId` never arrives. KG-A-22.**

**A5's refusal path also ran live** on an accidental no-markets submission:
`tier_2`, `marketProbability` NULL rather than a number, and the new
`NO_MATCH_CAPTION` text.

## 6. Kafka

No purge this window — the retained records were the previous window's, already
consumed, and HA restore resumes from checkpoint offsets rather than replaying.
Size a backlog as `--time -1` minus `--time -2`; `kafka-get-offsets.sh`, not
`kafka.tools.GetOffsetShell`.

## 6b. Teardown gates satisfied

| # | Gate | Result |
|---|---|---|
| 1 | Cloud Logging queryable | ✅ both `jsonPayload.message` (agent `RESOLVED` line + producer funnel) and `textPayload` (Flink Java). The `RESOLVED` line is durable in Cloud Logging independent of pod lifetime |
| 2 | Numbers written to a doc | ✅ `reports/v5-cloud-verify-20260801.md` §7, committed |
| 3 | Close the taps | ✅ `polymarket` → 0; 7/7 DAGs still paused; Flink left RUNNING deliberately |
| 4 | **Postgres backup** | ✅ `gs://anizai-pipeline-backups/postgres/2026-08-01/anizai.sql.gz` — **229,944,909 bytes, 2026-08-01T11:52:41Z**, +290,236 over the 10:06:53 window-1 backup, consistent with 1,173 new rows. Verified by size and timestamp, not exit code |
| 5 | `agent-worker` | left at desired 1 — see §2 |
| 6 | `main-pool` → 0 | ✅ |
| 7 | Carry-over stated | ✅ this file |

## 7. Nothing deleted

All 5 PVCs remain `Bound`. No ConfigMaps, topics, GCS objects or Firestore
documents removed. CronJobs left unsuspended as found.

## 8. Firestore (project `anizai-ai`)

Stale-doc gate at bring-up: `pending` = **1**, `messages sent` = 0, `claimed` = 0.
The pending document was a real but accidental submission (Ron clicked an event
with no markets). Brought up and allowed to run deliberately — it exercised A5.
