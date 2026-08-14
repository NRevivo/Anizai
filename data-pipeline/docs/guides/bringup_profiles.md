# bringup_profiles.md

> Domain: C — Cloud (operational)
> Type: Guide
> Last updated: 2026-08-12 (`anizai-pipehub` migration pass — §2 gains an explicit
> `trigger-consumer` row and a resting-state subsection; six workloads now rest at
> desired 0, so "comes up on its own" is no longer true for any of them)
> Previous: 2026-08-01 (post-Polymarket-completion pass: §3 Step 3 resize command now
> carries the mandatory `--zone`; §3 Step 4 backlog gate rewritten — no consumer groups
> exist, size by `--time -2`/`-1` across every topic in the path, and treat a backlog as
> a cost; §4 item 3 teardown cancellation marked DISPUTED; §5 trap 3 Flink half resolved
> — the mechanism is off-heap direct-buffer exhaustion and container memory cannot fix
> it; §5 trap 5 now states that the stale-code failure is silent and gives the five-step
> sequence)
> TL;DR: **One** bring-up/teardown procedure with a **profile selector** — AGENTS,
> PIPELINE, or FULL. Point Claude Code at this file and name a profile. Procedural
> only: it holds sequences and gates, never live facts (those live in
> `docs/C_cloud/cloud_state.md` and in the running cluster).

## Navigation
- §1 — What this file is, and what it deliberately does not contain
- §2 — The profile table (the only thing that differs between profiles)
- §3 — Bring-up procedure
- §4 — Teardown procedure
- §5 — Traps (each one has already cost a session)
- §6 — Claude Code handoff template

---

## §1 — Scope

**This file is procedural.** It answers "in what order, gated on what?" It does not
answer "what is running?" or "what image is deployed?" — copying those facts into a
guide is exactly how `cluster_operations_guide.md` ended up advertising image tags
three rebuilds out of date.

- **Live state** → `docs/C_cloud/cloud_state.md`, and above it, the cluster itself.
  The cluster always wins.
- **Triage, runbooks, dashboards, Cloud Logging queries** →
  `docs/guides/cluster_operations_guide.md`.
- **This file** → bring it up, bring it down, without surprises.

**Why profiles exist.** The cluster runs on a single node pool (`main-pool`). There
is no per-workload switch: bringing up *anything* means bringing up the node, and the
node schedules every Deployment whose desired replicas are ≥ 1. A profile is nothing
more than "which Deployments are held at 0 before the node arrives."

**A profile is not a manifest change.** Every profile is applied with `kubectl scale`
against live objects. No YAML is edited, nothing is committed, and a later
`kubectl apply` restores whatever the repo declares.

---

## §2 — The profile table

Desired replicas to set **before** the node pool is resized. Anything not listed is
left alone and comes up on its own.

| Workload | AGENTS | PIPELINE | FULL |
|---|:---:|:---:|:---:|
| `agent-worker` | **1** | 0 | **1** |
| `flink-jobmanager` | 0 | **1** | **1** |
| `flink-taskmanager` | 0 | **1** | **1** |
| `telegram` | 0 | **1** | **1** |
| `polymarket` | 0 | **1** | **1** |
| Airflow producer DAGs | stay paused | unpause as needed | unpause as needed |
| `trigger-consumer` | 0 | **1** | **1** |
| `postgres`, `kafka`, `airflow-*`, monitoring | up | up | up |

**Why AGENTS holds Flink at 0.** The agent's only in-cluster dependency is Postgres
(it reads the vaults; it writes to Firestore, which is a different GCP project; it
emits fire-and-forget to Kafka). It never touches Flink. Leaving Flink up during an
agent-only session means it recovers its jobs from checkpoints and starts chewing
whatever sits in Kafka through Gold enrichment — real OpenAI spend, shared RPD
ceiling, and checkpoint-storm risk, all in parallel with whatever you were trying to
measure.

**Why AGENTS also holds `telegram` and `polymarket` at 0.** See §5 trap 1.

**`trigger-consumer` is now an explicit row, and it no longer comes up on its own.**
Until 2026-08-12 it sat in the "up under every profile" line, on the reasoning that with
no reactive trigger emitted it idles, and that a dispatch failure on the consumer side is
logged rather than fatal to the agent. Two things changed that:

- Its SecretProviderClass mounts `OPENWEATHER_API_KEY`, `OPENSKY_CLIENT_ID` and
  `OPENSKY_CLIENT_SECRET`, and the pod exports all three before starting. A dispatch for
  those sources therefore does **not** fail — it succeeds, and writes Bronze nobody asked
  for. Under FULL, Flink then enriches it, at cost.
- On `anizai-pipehub` it rests at desired **0** (below), so it will not appear by itself
  whatever this table says.

Under AGENTS it is held at 0: the agent is the only thing running, so a trigger it emits
produces ingestion that serves no purpose in that session. Under PIPELINE and FULL it is
at 1, which is where the reactive path belongs. **If you disagree, this is one row to
change** — nothing else depends on it.

### Resting state on `anizai-pipehub` (from 2026-08-12)

The migration left **six** workloads at desired 0: `flink-jobmanager`,
`flink-taskmanager`, `agent-worker`, `telegram`, `polymarket`, `trigger-consumer`
(decision D10 in `Claude-anizai-docs/cloud_migration/migration_plan.md`). Everything else
— Postgres, Kafka, `airflow-*`, monitoring, `kafka-ui` — still comes up on a resize.

**What this changes:** a resize now brings up infrastructure only. Nothing ingests,
nothing processes, nothing calls OpenAI until you raise it deliberately. The procedure
itself is unchanged — §3 step 1 already has you set every workload in the table
explicitly — but the floor underneath it is now 0, so a workload you forget to raise is
**absent**, not quietly present. That is the intended failure direction.

This is a deliberate divergence from the old project, whose last teardown left Flink and
`trigger-consumer` at desired 1. The committed manifests still declare `replicas: 1` for
all six (KG-C-10), so a routine `kubectl apply` still overrides the resting state — read
desired replicas from the cluster, never from the repo (§5 trap 4).

---

## §3 — Bring-up

### Step 0 — Confirm context (read-only)

Confirm the active kubectl context and GCP project, then list the node pool and
nodes. Expect 0 nodes. If the context is not the intended cluster, stop.

### Step 1 — Apply the profile (live objects, pool still at 0)

The GKE control plane is reachable while the node pool is at 0, so desired replicas
can be set **before** any node exists. A Deployment held at 0 is simply never
scheduled when the node arrives.

```
kubectl scale deploy/<name> -n anizai --replicas=<0|1>     # per §2, per profile
```

Record each workload's pre-session desired replicas as you go, and report them. Do
not assume the repo value — live has drifted from git before (§5 trap 4).

### Step 2 — Gate: verify the profile before the node arrives

Re-read desired replicas for every workload in §2. All must match the profile. If any
does not, **stop** — do not resize. This gate is the whole point of the procedure;
skipping it means discovering the mistake by looking at a bill.

### Step 3 — Resize the pool

```
gcloud container clusters resize anizai-cluster --node-pool=main-pool --num-nodes=1 \
  --zone=us-central1-a
```

**`--zone` is mandatory and the cluster is ZONAL** — `us-central1-a`, note the `-a`
suffix. Without a location flag the command fails outright with *"One of [--location,
--zone, --region] must be supplied"*; with `--region us-central1` it addresses a
regional cluster that does not exist. This line omitted the flag until 2026-08-01 and
failed for everyone who ran it from the guide.

Expect ~3–5 min for the node, plus ~30 s per pod.

Poll until steady. Expected end state:
- Everything in the profile's "up" row → Running.
- Everything held at 0 → **no pod at all**. A pod appearing for a held workload means
  the scale did not take — stop and investigate before going further.
- `agent-worker` → `0/0` under PIPELINE; Running under AGENTS/FULL only after Step 4
  clears.

### Step 4 — Profile-specific pre-flight gates

**AGENTS and FULL — the Firestore stale-doc gate. Run this before the agent serves
anything.**

The worker attaches **two** Firestore listeners on startup, and each delivers its
entire current match set as ADDED on first attach:
1. `forecastQueries` where `status == 'pending'`
2. collection-group `messages` where `role == 'user'` and `status == 'sent'`

Anything already sitting in either set is claimed and processed *immediately* —
before you ask a single question.

**This check is free, and it does not need the cluster. Run it first, before you
resize anything.** Firestore lives in a different GCP project (`anizai-ai`) and is
reachable whether or not a node exists, so both counts can be taken while the pool is
still at 0. Treat it as a decision you make *before* the session, not a step you
discover halfway through it.

Count both sets. If either is non-zero, choose deliberately between three options —
not two:

1. **Clear them** — flip status; do not delete.
2. **Accept them**, and record that the session's first N runs are not yours.
3. **Do not bring the agent up at all.** A large or unexplained queue is a legitimate
   reason to stop and decide before spending anything. Under AGENTS that means
   abandoning or postponing the session; under FULL it means running pipeline-only for
   now and scaling `agent-worker` later.

The cost of skipping this is not hypothetical: on 2026-07-26 an agent-only bring-up
would have executed **nine unsolicited follow-ups across six historical sessions**
before the operator asked anything (§5 trap 2).

Also worth a look: `forecastQueries` stuck at `claimed`. Nothing scans that status, so
a document orphaned by a crashed worker sits there permanently. It will not be picked
up — but it is a signal that a previous session died mid-forecast (KG-B-21).

**PIPELINE and FULL — the backlog and budget gate.**

Before Flink starts consuming, size the Kafka backlog and decide explicitly whether to
process it or drop it (`cluster_operations_guide.md` §7 holds the drop procedure).

**Size it as `--time -1` (latest) minus `--time -2` (earliest), per topic.** There are
**no consumer groups** — `kafka-consumer-groups.sh --list` returns nothing, because both
jobs track position by Flink checkpoint rather than by group offset. An earlier version
of this line said "per consumer group" and was not executable. Note also that
`kafka.tools.GetOffsetShell` no longer exists in the deployed Kafka build; it errors out
and a naive loop silently sums to zero. Use `kafka-get-offsets.sh`.

**A cumulative end offset is not a backlog.** On 2026-08-01 an end offset of 53,319 was
read as "53k pending" and a whole clearing step was planned around it; retained was
**1,400**, because seven-day retention had already aged the rest out.

**Size EVERY topic in the path, including the ones you expect to be zero.** Purging an
ingress topic is not purging a pipeline. On 2026-08-01 `ingest.bronze.polymarket` was
cleared while 700 records sat in `process.silver.structured_metrics` — already past
Silver, one stage from the vault, and about to be consumed by Gold on `earliest()`. A
Bronze-only purge would have produced exactly the contamination the purge existed to
prevent, by a route the step did not cover.

**A backlog is a cost, not only a correctness question.** Bringing the pipeline up with
one spends real GPT-4o enrichment on stale data: on 2026-08-01, 3,326 retained
arxiv/hackernews/newsapi records would have been enriched, against a shared RPD ceiling,
in a window that cared only about Polymarket. Decide whether you want that spend before
Flink starts, not after. Then confirm OpenAI credit and RPD headroom — a large backlog
replay and a forecast session share one account and one ceiling.

**All profiles — observability.** If this session's purpose is to produce numbers,
read §5 trap 3 first. The numbers you want may not be reaching the logs — and the
obvious remedy is banned: **do not set `LOG_INFO_SAMPLE_RATE` on the Flink workloads.**
It killed the TaskManager twice on 2026-07-27 and cost hours.

### Step 5 — Bring the agent up (AGENTS / FULL only)

Scale `agent-worker` to 1 and hold it to a health gate before declaring the session
open:
- pod Ready, no restarts, no crash-loop
- `/health` returns 200
- running image digest matches the intended one (`cloud_state.md`)
- `AGENT_VERSION` in the logs matches that image
- **both** listeners attach (see Step 4 — the second one is easy to forget)
- `/metrics` serves real exposition and Prometheus is scraping the target

Any failure → stop and report. Do not repair by editing manifests mid-session.

---

## §4 — Teardown

1. **Confirm durability before removing anything.** Firestore is external and
   survives regardless. Postgres and Prometheus survive on their PVCs. Container
   stdout goes to Cloud Logging — verify it is actually queryable there and dump it if
   it is not (`cluster_operations_guide.md` §11 holds the filters and the
   `textPayload` vs `jsonPayload.message` trap).
2. **Know where the numbers live.** Cost and per-node latency come from the agent's
   Prometheus counters, not the logs (§5 trap 3). Prometheus retention is finite and
   the retention clock is evaluated *on startup*, so data that survives a teardown can
   still be purged minutes into the next bring-up. **If a session produced numbers
   worth keeping, write them into a doc during the session.**
3. **Close the taps before anything else stops.** Re-pause whatever DAGs this session
   unpaused; scale `telegram` and `polymarket` back to 0 if the profile brought them
   up. Nothing below this step should be done while data is still flowing.

   **On cancelling the Flink jobs at teardown — DISPUTED, do not act on either
   reading.** This step used to call cancellation optional tidiness "since HA preserves
   the graphs either way." That is contested: a cancelled job is globally terminal and
   its graph is cleaned out of the Kubernetes HA store, which would leave nothing for
   the next bring-up to recover. **Neither reading has been tested.** Until one is, do
   not cancel at teardown — leaving the jobs running preserves the HA state that §5
   trap 5 depends on, and costs only a less tidy final checkpoint.
   (`cluster_operations_guide.md` §3 holds the command if a deliberate test is ever
   run.) This does not affect the image-change case, where cancellation is mandatory at
   the next bring-up regardless — see §5 trap 5.
4. **Back up Postgres if anything wrote to it this session.** Flink writes to the
   vaults, and a schema migration counts; the agent reads only. If either happened,
   take a manual backup now — `cluster_operations_guide.md` §8 holds the procedure.
   **Why this is a gate and not a nicety:** the `postgres-backup` CronJob fires at
   02:00 UTC and is not aware of scale-downs (KG-C-9), so a session that opens and
   closes between two firings is never backed up at all. Two daily backups were
   already missed this way in May 2026.
5. Scale `agent-worker` to 0.
6. Resize `main-pool` to 0. Confirm zero nodes.
7. **State the carry-over explicitly.** Which workloads were held at 0 and were
   deliberately *not* restored? Which DAGs are left paused? Whoever brings the cluster
   up next needs those sentences, or they will spend an hour wondering why Bronze is
   silent.

---

## §5 — Traps

**1 — "Producers off" is not the same as "DAGs paused."**
Only seven producers are Airflow DAGs. `telegram` and `polymarket` are always-on
Deployments, unaffected by pausing anything in Airflow. A session that pauses the DAGs
and considers ingestion stopped is wrong.

**2 — The agent claims stale work the instant it starts.**
Both listeners deliver their full current match set on first attach. On 2026-07-26 an
agent-only bring-up would have executed nine unsolicited follow-ups across six
historical sessions before the operator asked anything. The parent-`done` guard does
not help: a follow-up's parent is *always* done, so the guard always passes, and there
is no age check. Always run the Step 4 gate.

**3 — Log sampling: what is proven, what is not, and what you must not do about it.**
This trap is the canonical statement of the sampling behaviour. `cluster_operations_guide.md`
§5.6 and §11 defer to it; if they ever disagree with this section, this section wins.

*The mechanism.* `utils/logging_config.setup_logging()` installs a sampling filter:
WARNING and above pass at 100 %, INFO passes at `LOG_INFO_SAMPLE_RATE` (default
**0.01**). `setup_logging()` is idempotent and first-caller-wins, so an import-time call
anywhere in a process's dependency graph silently configures it for that whole process.

*The agent — evidenced.* Sampling demonstrably applies. A ~20-hour agent session on
2026-07-25/26 produced **7 log entries in total**. `llm_usage` lines are emitted at INFO
and therefore do not reliably reach Cloud Logging: do not plan a measurement session
around grepping them. The durable sources are `agent_llm_cost_usd_total` and
`agent_node_duration_seconds` in Prometheus. (Related: KG-B-4.)

*Flink — RESOLVED 2026-08-01, and the reasoning previously given here was wrong.* This
file argued that the sampling claim "does not hold" on Flink, because with the variable
absent the operator startup lines and `[gold/dedup]` lines all appeared. **Those were
submit-time CLIENT output, not the UDF path.** They never came from the TaskManager, so
they said nothing about the path that actually fails — and that confusion sent two
months of investigation in the wrong direction.

Measured directly on 2026-08-01, local, single-variable: records emitted by the
TaskManager's Python worker are **0 at 0.01 and 0 at 1.0** — identical — yet 1.0 OOMs on
the first message and 0.01 runs clean. Our Python records reach **no TaskManager sink at
any sample rate**: `docker logs`, `flink--taskexecutor-*.log` and the UDF boot log all
contain zero `processing.silver_job` records at either setting. **If you are debugging a
Flink UDF in this project, the log lines you are looking for do not arrive** —
independent of this defect, and worth knowing before you spend a session grepping for
them.

> ### Do not set `LOG_INFO_SAMPLE_RATE` on the Flink workloads.
>
> Setting it to `1.0` on the JobManager / TaskManager manifests **killed the pipeline on
> 2026-07-27.** The TaskManager was OOMKilled (exit 137) twice — 38 s and then 34 s after
> data arrived — and it cost hours.
>
> The evidence is a clean natural experiment, not a hunch. Attempt 1 died at the
> committed `2560Mi`. The limit was raised to `6Gi` and attempt 2 died at 34 s — 2.4× the
> memory moved time-to-kill by four seconds, which is the signature of consumption
> growing with throughput, not of a fixed footprint meeting a wall. Attempt 3 reverted
> the limit to `2560Mi` **and removed the variable**, processed the same accumulated
> queue, and ran clean with zero restarts. Same burst, same memory, one variable
> different, opposite outcome. JVM heap stayed a flat bounded sawtooth (~170–536 MB)
> throughout, so the growth was entirely Python-side.
>
> **The failure is off-heap, and container memory CANNOT fix it.** Reproduced locally
> 2026-08-01 with a single-variable change: the exception is
> `java.lang.OutOfMemoryError: Direct buffer memory` in the TaskManager JVM — **not** a
> Beam Python worker fault. Everything below it in the stack (`Failed to start remote
> bundle`, `CANCELLED: client cancelled`, `BeamFnDataGrpcMultiplexer: Hanged up`) is
> wreckage from the OOM tearing down the gRPC channel, which is why the symptom has read
> as a Python crash for so long. At the moment of failure the container reported
> `OOMKilled=false`, `Restarts=0`, and **750 MiB in use of 7.6 GiB available**. The
> container survives; only the JOB dies. Direct buffer memory is bounded by Flink's own
> off-heap configuration (`taskmanager.memory.*` → `-XX:MaxDirectMemorySize`), not by
> cgroups — so raising `mem_limit` on a compose service, or `resources.limits.memory` on
> the k8s Deployment, changes nothing. The JVM refuses the allocation long before Docker
> or the kernel is involved. `6Gi` proved this in cloud before the mechanism was
> understood. **If your instinct on an OOM is more memory, this is the line that should
> stop you.**
>
> **The remaining question is narrow.** Volume is eliminated: the emitted-record count is
> identical at both settings (above). Duplicate handlers are eliminated —
> `setup_logging()` installs one handler with two filters unconditionally and never
> branches on the rate. The only thing the value changes is the return of
> `_SampledInfoFilter.filter()`. Remaining hypothesis, **UNVERIFIED**: records passing
> the filter are still transported over Beam's FnLogging gRPC channel — which uses direct
> buffers — and discarded at the JVM end, which would explain the OOM and the zero
> visible records simultaneously. The distinguishing test is to instrument
> `_SampledInfoFilter.filter()` return counts inside the image; counting sinks cannot
> resolve it. See KG-A-10 / KG-A-17.
>
> **Local and cloud were one defect.** `infrastructure/.env` carried
> `LOG_INFO_SAMPLE_RATE=1.0`, and `docker-compose.yml` hands both Flink legs the entire
> file via `env_file` — which is why local Flink had never once worked (KG-A-10, closed
> 2026-08-01). Same variable, same first-message signature, same fix as the cloud OOM.
> Compose now pins the value on both Flink services so no `.env` value can reach the
> Flink JVM; the k8s manifests set it nowhere and use no `envFrom`, so cloud is safe by
> construction.

*The agent Deployment is a different manifest and a different decision.* `1.0` is
recommended there in several places (KG-B-4), but **it has never actually been set** —
the variable is absent from `agent-deployment.yaml` as of 2026-07-27, and the
2026-07-25/26 run went out at the 1 % default. The agent's volume is lower by orders of
magnitude, so it is probably safe — but probably is the honest word while the Flink
mechanism is unexplained. If you set it, do it as a deliberate step with the pod watched
for the first few minutes, not as routine pre-session hygiene. It is read at module
import, so it needs a fresh pod. The durable numbers come from Prometheus regardless.

**4 — Live replicas have drifted from git, in the direction that bites.**
On 2026-07-26 `flink-jobmanager`, `flink-taskmanager`, `telegram` and `polymarket` were
all at 0 live while their committed manifests declare `replicas: 1`. A routine
`kubectl apply` of those files therefore *starts ingestion* with no scale command
issued and no obvious cause. Always read desired replicas from the cluster, never from
the repo, and prefer `kubectl scale` over `apply` during a session.

**5 — Flink does not pick up new code from a pod restart, and the failure is SILENT.**
Scaling Flink back up recovers the *previously compiled* job graph from HA state. If
the image changed while it was down, the jobs must be cancelled and resubmitted — see
`cluster_operations_guide.md` §6. Verify jobs reach RUNNING rather than a RESTARTING
loop.

**Why this is worse than it sounds.** The restored job does not error, warn, or restart.
It reaches `RUNNING`, processes messages, and reports perfectly healthy — on a pod whose
image is the new one, running the old compiled code. Observed 2026-08-01: both jobs
recovered on their own with `restored: 1`, and the recovered JobIDs matched the
pre-teardown HA ConfigMap names character for character. Had it not been caught, the
verification downstream would have shown rows missing fields that were already fixed,
and the investigation would have gone to a file that was already correct.

**The five steps, in order, whenever the image changed:**
1. Bring Flink up (JobManager, then TaskManager)
2. `flink list` — **record** what recovered, including JobIDs
3. **Cancel** both recovered jobs
4. Submit from the new image
5. `flink list` again — confirm the running JobIDs are the ones you just submitted

Step 5 is the whole point: a running JobManager does not imply a submitted job, and
nothing else distinguishes a restored graph from a fresh one. **If the image did NOT
change, the automatic restore is correct** — do not cancel, and see §4 item 3.

---

## §6 — Claude Code handoff template

```
→ CLUSTER BRING-UP — profile: <AGENTS | PIPELINE | FULL>
Procedure: data-pipeline/docs/guides/bringup_profiles.md — follow §3 in order.
Purpose: <one line>

Constraints:
  - kubectl scale / gcloud resize only. No manifest edits, no env/image/Flink
    changes, no code, no commits.
  - Report the pre-session desired replicas you find (§3 Step 1); do not assume
    the repo values.
  - Stop at the §3 Step 2 gate and at the §3 Step 4 gate. Do not self-heal.
  - Do not scale anything down until I say so. Teardown is §4, on my word only.
```

Add to the handoff only what this run needs beyond the file: the session's purpose,
anything deliberately out of scope, and whether the operator or Claude Code drives the
workload once the cluster is up.
