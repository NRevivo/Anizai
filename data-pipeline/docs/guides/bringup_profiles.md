# bringup_profiles.md

> Domain: C — Cloud (operational)
> Type: Guide
> Last updated: 2026-07-26
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
| `postgres`, `kafka`, `airflow-*`, `trigger-consumer`, monitoring | up | up | up |

**Why AGENTS holds Flink at 0.** The agent's only in-cluster dependency is Postgres
(it reads the vaults; it writes to Firestore, which is a different GCP project; it
emits fire-and-forget to Kafka). It never touches Flink. Leaving Flink up during an
agent-only session means it recovers its jobs from checkpoints and starts chewing
whatever sits in Kafka through Gold enrichment — real OpenAI spend, shared RPD
ceiling, and checkpoint-storm risk, all in parallel with whatever you were trying to
measure.

**Why AGENTS also holds `telegram` and `polymarket` at 0.** See §5 trap 1.

**`trigger-consumer` comes up under every profile.** Harmless: with no reactive
trigger emitted it idles, and if one *is* emitted, a dispatch failure on the consumer
side is logged, not fatal to the agent.

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

Resize `main-pool` to 1 node. Expect ~3–5 min for the node, plus ~30 s per pod.

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
before you ask a single question. Count both. If either is non-zero, decide before
proceeding: clear them (flip status; do not delete), or accept and record that the
session's first N runs are not yours.

Also worth a look: `forecastQueries` stuck at `claimed`. Nothing scans that status, so
a document orphaned by a crashed worker sits there permanently. It will not be picked
up — but it is a signal that a previous session died mid-forecast (KG-B-21).

**PIPELINE and FULL — the backlog and budget gate.**

Before Flink starts consuming, size the Kafka backlog per consumer group and decide
explicitly whether to process it or drop it (`cluster_operations_guide.md` §7 holds
the drop procedure). Then confirm OpenAI credit and RPD headroom — a large backlog
replay and a forecast session share one account and one ceiling.

**All profiles — observability.** If this session's purpose is to produce numbers,
read §5 trap 3 first. The numbers you want may not be reaching the logs.

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
   stdout goes to Cloud Logging — verify it is actually queryable there (mind
   `textPayload` vs `jsonPayload.message`), and dump it if it is not.
2. **Know where the numbers live.** Cost and per-node latency come from the agent's
   Prometheus counters, not the logs (§5 trap 3). Prometheus retention is finite and
   the retention clock is evaluated *on startup*, so data that survives a teardown can
   still be purged minutes into the next bring-up. **If a session produced numbers
   worth keeping, write them into a doc during the session.**
3. Scale `agent-worker` to 0.
4. Resize `main-pool` to 0. Confirm zero nodes.
5. **State the carry-over explicitly.** Which workloads were held at 0 and were
   deliberately *not* restored? Whoever brings the cluster up next needs that
   sentence, or they will spend an hour wondering why Bronze is silent.

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

**3 — INFO logs are 1 %-sampled; the numbers live in Prometheus.**
`utils/logging_config.setup_logging()` installs a sampling filter: WARNING and above
pass at 100 %, INFO passes at `LOG_INFO_SAMPLE_RATE` (default **0.01**). The policy was
written for a pipeline handling ~100 msg/s; it applies to the agent too, which handles
single-digit forecasts per hour. `setup_logging()` is idempotent and first-caller-wins,
so an import-time call anywhere in the agent's dependency graph silently configures it
for the whole process.

Consequence: `llm_usage` lines (emitted at INFO) do not reliably reach Cloud Logging.
Do not plan a measurement session around grepping them. The durable sources are
`agent_llm_cost_usd_total` and `agent_node_duration_seconds` in Prometheus. To get full
INFO for a session, set `LOG_INFO_SAMPLE_RATE=1.0` on the agent Deployment — it is read
at module import, so this requires a fresh pod. (Related: KG-B-4.)

**4 — Live replicas have drifted from git, in the direction that bites.**
On 2026-07-26 `flink-jobmanager`, `flink-taskmanager`, `telegram` and `polymarket` were
all at 0 live while their committed manifests declare `replicas: 1`. A routine
`kubectl apply` of those files therefore *starts ingestion* with no scale command
issued and no obvious cause. Always read desired replicas from the cluster, never from
the repo, and prefer `kubectl scale` over `apply` during a session.

**5 — Flink does not pick up new code from a pod restart.**
Scaling Flink back up recovers the *previously compiled* job graph from HA state. If
the image changed while it was down, the jobs must be cancelled and resubmitted — see
`cluster_operations_guide.md` §6. Verify jobs reach RUNNING rather than a RESTARTING
loop.

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
