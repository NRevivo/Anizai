# carryover-20260818-node-replacement.md — the node recycled mid-run

> Domain: C — Cloud
> Type: Carry-over (event record + lessons)
> Event: 2026-08-18 10:03:17Z — `main-pool` node replaced during the continuous
> collection run; every workload in `anizai` recreated
> Written: 2026-08-18, during the Domain-B evidence-URL deployment session

## Why this file exists

The cluster replaced its only node in the middle of the continuous run that is
supposed to carry through to Ron's Thursday 2026-08-20 review. Nothing was lost
that could not be re-derived, and the pipeline came back on its own — but the
event is the first real evidence of how this single-node cluster behaves under
an involuntary node loss, and one thing did **not** come back. That asymmetry is
the reason to write it down.

It lives in its own file rather than in `cloud_state.md` because that document's
own header says not to read it as current (KG-C-12). A record of what happened
should not be filed inside a document that is itself fenced as stale.

---

## 1. What happened

```
node gke-anizai-cluster-main-pool-d0e884d7-hjmh   gone
node gke-anizai-cluster-main-pool-d0e884d7-33qa   created 2026-08-18T10:03:17Z
                                                  GKE v1.35.6-gke.1641000
```

All 16 pods in the `anizai` namespace were recreated within ~30 seconds of the
new node registering (10:03:23Z–10:03:48Z): `agent-worker`, both Airflow pods,
Flink JM+TM, `kafka-0`, `postgres-0`, `prometheus`, `grafana`, `alertmanager`,
`telegram`, `polymarket`, both exporters, `kafka-ui`.

The trigger was not investigated (auto-repair / node auto-upgrade is the
expected shape). **The cause matters less than the recovery behaviour**, which
is what this file records.

---

## 2. The pipeline self-recovered — measured, not assumed

Three workloads crash-looped briefly, all against Kafka:

| Pod | Exit | Error | Last restart | Current container uptime at 11:30Z |
|---|---|---|---|---|
| `kafka-exporter` | 255 | `Error Init Kafka Client: … dial tcp 10.20.1.27:29092: connect: connection refused` | 10:05:52Z | ~84 min unbroken |
| `polymarket` | 1 | `kafka.errors.NoBrokersAvailable` | 10:05:56Z | ~85 min unbroken |
| `telegram` | 1 | `kafka.errors.NoBrokersAvailable` | 10:05:46Z | ~85 min unbroken |

A **startup ordering race, not a fault in the producers.** `kafka-0` itself only
started at 10:03:48Z and needs time to become ready; the producers came up
alongside it, failed fast, and Kubernetes restarted them with backoff until the
broker answered. Once it did, they stayed up.

**Read the restart counts correctly.** `kubectl get pods` showed 5 / 4 / 3
restarts, which reads like ongoing instability. It is not — those are cumulative
scars from a single 2½-minute window (10:03:30–10:05:56). No `CrashLoopBackOff`,
no intervention, no recurrence. When triaging churn, take
`lastState.terminated.finishedAt` and the current container's `startedAt`; the
cumulative count alone will mislead you.

**Verdict: the infrastructure converges.** On this evidence, an involuntary node
loss costs a few minutes of producer downtime and then the pipeline settles by
itself.

---

## 3. The load-bearing lesson

> **Infrastructure converges. In-container patches do not.**

Kubernetes restored every workload without help. The single thing that did *not*
come back is the single thing that was never in an image: the HackerNews
`*/20 → hourly` cadence cut, which existed only as a live edit inside the
running `airflow-scheduler` container (the scheduler mounts no DAG volume — DAG
files are baked into `anizai-airflow:2.9.3-7b5i`).

At 10:03:24Z the scheduler came back carrying the image's `*/20`, and it ran
that way for ~90 minutes before anyone noticed.

**Why this failure mode is worse than it sounds:** the pod is `Running` and
`Ready`, no alert fires, no log line reports it, and every health check passes.
The system is healthy and simply doing the wrong thing. A live patch has no
recovery path, and its absence is invisible.

The cadence was re-applied to both `airflow-scheduler` and `airflow-webserver`
at ~11:35Z and verified (`schedule_interval="0 * * * *"`). **It will revert again
on the next pod restart.** Anything applied in-container is one eviction from
being gone; if it matters past the session, it belongs in an image.

### The durable fix, priced (not performed — Ron's call on timing)

| | |
|---|---|
| Code edit needed | **None.** `bc76927` already put hourly in the repo at `orchestration/dags/hackernews_dag.py:63` |
| Blast radius | **Exactly one commit** (`bc76927`) touches airflow-image code since the image was built (2026-08-14 10:21). Of the 7 DAGs, only `hackernews_dag.py` differs from live (63→78 lines); the other 6 are identical |
| Build / push / roll / verify | ~6–12 min / ~2–6 min (base layers already in AR) / ~3 min for the two Deployments / ~5 min |
| **Total** | **~20–30 min wall-clock, mostly unattended** |

**KG-C-5 does not ride along.** The 2026-08-16 audit
(`verification-queue-20260816.md` §2) established that `NEWSAI_API_KEY` holds an
Event Registry key — which is what the producer needs — so the rename has no
forcing function; `THE_NEWS_API_KEY` was judged the *wrong* target because it
names the retired provider; and the record states the rebuild "stands on its own
hackernews-cadence justification regardless." The rebuild would also close the
cadence half of B-deploy Stage 2 T2.1.

---

## 4. What was lost

**Prometheus agent counters.** `agent_llm_cost_usd_total`, `agent_session_total`
and the `agent_node_duration_seconds` histograms are process-local and reset
when the agent pod was recreated at 10:03:23Z. By the time they were read
(~11:20Z) the endpoint returned no `agent_*` samples at all. Prometheus itself
also restarted (10:03:48Z), and per `bringup_profiles.md` its retention clock is
evaluated on startup — so pre-10:03 history should be treated as suspect until
checked, not assumed present.

Accepted by Ron; no action taken.

**`polymarket`'s deliberate restart was discharged involuntarily.** The
memory-tracked plan called for a chosen restart on the evening of 2026-08-18 to
stay ahead of its ~23.8 MiB/h leak. The node event restarted it at 10:03:30Z and
reset the baseline. 10:03 is outside the ~:30–:33 sweep window, so an hour of
Polymarket was probably not lost. Accepted by Ron as routine.

---

## 5. ⚠ Defect found, deliberately not acted on

**`orchestration/dags/hackernews_dag.py` — the header comment's cadence
rationale contradicts Phase 7D.**

The header justifies the move to hourly on OpenAI spend, citing ~47.3 calls/run
and ~3,406 calls/day at `*/20`. That reasoning predates Phase 7D and no longer
holds:

- `processing/silver_job.py:1459` — `content_hash = hash_hackernews_story(story_id)`,
  story_id **alone** (Phase 7D decision D1a). Its own docstring records that the
  earlier derivation folded the comment set into the hash, "so the key drifted
  every pulse as comments accrued and the same story was re-archived and
  re-enriched."
- With a stable key, `social_vault.exists_by_content_hash()` hits on re-fetch and
  — per `config/settings.py:117-119` — "the consensus call is skipped."

**So re-fetching the same story at `*/20` costs no enrichment calls.** What the
faster cadence actually inflates is `filter_rejects` row growth:
`persistence/filter_rejects.py` inserts with no `ON CONFLICT` and no unique
constraint, so each rejection writes a fresh row and a low-signal story is
captured 3×/hour instead of 1× (KG-A-19). **Disk, not quota.**

Both statements cannot be true. **The decision (hourly) is unaffected** — Ron
restored it because it is the state he chose — but the stated justification
should be reframed on reject growth rather than OpenAI spend. Consequence of
leaving it: the `*/20` window on 2026-08-18 looks like ~90 minutes of wasted
spend in the record when it was not.

**Candidate for a Domain-A KG row; deliberately not opened here.**

---

## 6. Pointers

- Agent image identity + rollback chain → `cloud_state.md` §3 (`agent-worker`
  row, the one row verified 2026-08-18)
- The evidence-URL patch this session shipped → `docs/B_hub/hub_sprints.md` §1
- Bring-up / teardown procedure → `docs/guides/bringup_profiles.md`
  *(advisor-owned; the in-container-patch lesson in §3 above is arguably guide
  material but was deliberately not written there)*
- KG-C-10 (manifests declare `replicas: 1`/`0` against divergent live state) and
  KG-C-12 (this file's staleness) → `cloud_sprints.md` §4
