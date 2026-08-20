# carryover-20260820-defense-postponed-teardown.md — full stop, and what git does not know

> Domain: C — Cloud
> Type: Carry-over (pre-teardown drift audit + teardown record)
> Event: 2026-08-20 — defense postponed indefinitely; cluster brought to a full
> stop (`main-pool` → 0 nodes) after a read-only audit of live-only state
> Written: 2026-08-20, during the teardown session itself
> Procedure followed: `docs/guides/bringup_profiles.md` §4, with the audit below
> run as Step 0 before any scale or resize command

## Why this file exists

The system is being stopped with no scheduled restart date. Everything that is
in git or in an image will come back on its own. **Everything that exists only
in the running cluster will not**, and the 2026-08-18 node replacement already
proved that the failure mode is silent: the pod comes back `Running` and
`Ready`, every health check passes, and the system simply does the wrong thing
(`carryover-20260818-node-replacement.md` §3).

This file is the inventory of that live-only state, taken **before** anything
was scaled down, plus the teardown's own carry-over (§8). Read together, §1–§8
are intended to be sufficient on their own to reproduce the 2026-08-20 running
state on the next bring-up, without reading `cloud_state.md` — which is still
fenced as stale (KG-C-12) and, as §2 shows, is now stale in one more place than
its own header admits.

**Nothing in this audit was remediated.** No manifest was edited, no commit was
made, no drift was fixed. That was the explicit instruction and it is also the
right call: a fix applied minutes before a teardown is a fix nobody verifies.

---

## 1. Audit scope and method

Taken 2026-08-20 ~10:45–11:00Z, read-only, against
`gke_anizai-pipehub_us-central1-a_anizai-cluster` / project `anizai-pipehub`
(both confirmed before the first command).

Method notes worth reusing:

- **Live vs "what an apply would do" was measured against the
  `kubectl.kubernetes.io/last-applied-configuration` annotation**, not against
  the repo. That annotation is what the last `kubectl apply` wrote; an
  imperative `kubectl set image` / `kubectl scale` changes the live spec and
  leaves the annotation untouched. Diffing the two is therefore an exact
  detector for imperative overrides — which is how §2 and §6 were found.
- **File mtimes inside the running containers are the cheapest detector of an
  in-container patch.** Image-baked files carry their build mtime; a live edit
  carries a recent one. This resolved the whole "check the other 6 DAGs"
  question in one command (§4).
- **Resource-block "differences" are mostly not differences.** The API
  normalizes `1000m`→`1`, `2048Mi`→`2Gi`, and drops `value: ""`. Six workloads
  flagged on that basis and all six were false positives; they are excluded
  below.

---

## 2. ⚠ The load-bearing finding — an entire agent version is live and recorded nowhere

**`agent-worker` is running a build that does not exist in any manifest, any
doc, or any state file in this repository.**

| | |
|---|---|
| **Live image** | `anizai-agent@sha256:8b2e7e650ee00834741e7f7deee712ba9af1f57f2a462be2cab8953e90cf5d62` |
| **Which is tag** | `0.6.2-dynamic-followups-amd64-419e58e` |
| **Live `AGENT_VERSION`** | `0.5.0-sprint26+419e58e` (read from the running pod) |
| **Live desired replicas** | **1** |
| **ReplicaSet / revision** | `agent-worker-7b9444c6d8`, revision **5**, pod started 2026-08-18T13:33:34Z, **0 restarts in 45 h** |
| **Committed manifest** | `k8s/agent-deployment.yaml` → `replicas: 0`, `image: …/anizai-agent:0.6.1-evidence-url` |
| **That tag's digest** | `sha256:06b72af5…dea00` — a **different, older** image (revision 4) |
| **`last-applied` annotation** | older still: `…/anizai-agent:0.6.0-trackA` |
| **Occurrences of `0.6.2-dynamic-followups` in the repo** | **zero** (grepped repo-wide, excluding `venv`/`node_modules`/`.git`) |

So there are **three** different answers to "what image is the agent on",
depending on where you look, and the live one is the only one that is right.

**The good news: the source is in git.** The image was built from commit
`419e58e`, and the three commits that make up the dynamic-follow-ups feature are
all ancestors of `HEAD`:

```
3d0668a  feat(agent):  generate dynamic follow-up suggestions
3cdc182  feat(server): expose assistant follow-up suggestions
419e58e  feat(client): render latest follow-up suggestions
```

**What is missing is the deployment record, not the code.** The image is
reproducible; nothing needs to be recovered from the registry before it is
garbage-collected. But nobody reading the repo can learn that the agent was
running 0.6.2, and `cloud_state.md` §3 actively says otherwise.

**A second 0.6.2 build exists and is NOT the one to use.**
`0.6.2-dynamic-followups-419e58e` @ `sha256:270b8d8e…61cb9` — same commit, no
`-amd64` in the tag — was rolled at 13:26Z (revision 3) and superseded seven
minutes later by the `-amd64` build that is live now. The tag names differ by
one segment. **Take the digest, not the tag.**

### Registry confirmation (2026-08-20, post-teardown) — no build needed

Checked directly against Artifact Registry, the same three-way method used in
§7.1 and §7.3:

```
manifest: anizai-agent:0.6.1-evidence-url   (stale — this is the drift)
live:     anizai-agent@sha256:8b2e7e650ee00834741e7f7deee712ba9af1f57f2a462be2cab8953e90cf5d62
registry: 0.6.2-dynamic-followups-amd64-419e58e -> sha256:8b2e7e65...   live == registry
```

**The image is already in Artifact Registry. Whoever brings the cluster up next
does not build or push anything for the agent** — the restore is a
`kubectl set image` against the digest above, exactly as written below.

**Provenance, so this doesn't get re-litigated at the next bring-up:** commit
`419e58e` and the image build/push were done by the frontend/BFF partner, not
Ron. If the partner also does the next bring-up, this is his own prior work and
the commit is presumably already on whatever branch he works from. If **Ron**
does the next bring-up, he should confirm `419e58e` (and its two ancestors,
`3d0668a` / `3cdc182`) are present on the shared branch before treating "the
source is in git" as true for anyone but the partner — the digest above works
regardless either way, since it is pulled from the registry, not rebuilt from
source.

### Exact restore action for the next bring-up

```powershell
# AFTER the node pool is up. Do NOT `kubectl apply -f agent-deployment.yaml`:
# that regresses the image to 0.6.1-evidence-url AND scales the agent to 0.
kubectl set image deploy/agent-worker -n anizai `
  agent-worker=us-central1-docker.pkg.dev/anizai-pipehub/anizai-images/anizai-agent@sha256:8b2e7e650ee00834741e7f7deee712ba9af1f57f2a462be2cab8953e90cf5d62

kubectl scale deploy/agent-worker -n anizai --replicas=1
```

Then run the §3 Step 5 health gate as written, and expect
`AGENT_VERSION 0.5.0-sprint26+419e58e` — **not** the `+b71ac5a` that
`cloud_state.md` currently advertises.

### The durable fix, priced (not performed)

Bump `image:` in `k8s/agent-deployment.yaml` to the 0.6.2 digest or tag, and
correct the `agent-worker` row in `cloud_state.md` §3 plus
`project_master.md:143`. **Code change: none. Blast radius: two doc lines and one
manifest line.** Note this does *not* close KG-C-10's `replicas: 0` half — that
`0` is deliberate and documented in the manifest's own comment, and
`bringup_profiles.md` §2 depends on it.

### Why `cloud_state.md` is now wrong in a new way

Its header (2026-08-18) claims the `agent-worker` row is "the one row in this
section that is" verified current. That verification was taken at ~11:23Z on
2026-08-18 against revision 4 (`0.6.1-evidence-url`). **Revision 5 rolled at
13:33Z the same day**, about two hours later, and the row was never updated.
The one row advertised as trustworthy is no longer trustworthy. KG-C-12 does not
merely fail to lift — it now covers the exception too.

---

## 3. ⚠ The HackerNews cadence is still an in-container patch, and it is still live

Confirmed live, and confirmed to be the *only* live DAG edit.

| | |
|---|---|
| **Live** | `schedule_interval="0 * * * *"` — hourly. Airflow DB agrees; last successful run **2026-08-20T10:01:04Z** |
| **In-container file** | `/opt/airflow/data-pipeline/orchestration/dags/hackernews_dag.py`, mtime **2026-08-18 11:28:20Z** (scheduler) / **11:28:22Z** (webserver) |
| **Image** | `anizai-airflow:2.9.3-7b5i` @ `sha256:2ab60dd1…52b21f` ships **`*/20 * * * *`** |
| **Repo HEAD** | hourly, since `bc76927` (2026-08-15) |

**The patch is surgical, and the diff proves it.** The live file is
**byte-identical** to the repo blob at `bc76927^` (i.e. the image's content)
except for **exactly one line**:

```
48c48
<     schedule_interval="*/20 * * * *",      # image
---
>     schedule_interval="0 * * * *",         # live
```

Everything else in the live file still reads `*/20` — the header comment, the
rationale block, and the DAG `description=`. Repo HEAD is 78 lines because
`bc76927` also rewrote all of that prose; the live file is the image's 63 lines
with one value sed'd. **Functionally live == HEAD; textually live == image.**
Anyone diffing the whole file will see 15 lines of noise and one line that
matters.

**Correction to the record:** `carryover-20260818-node-replacement.md` §3 gives
the re-apply time as "~11:35Z". The file mtimes say **11:28:20Z / 11:28:22Z**.
The seven-minute discrepancy changes nothing operationally; it is noted so the
mtimes are not later read as evidence of a *second*, unlogged patch.

### Restore action

Either (a) rebuild `anizai-airflow` from `HEAD` — **no code edit needed**, per
the 2026-08-18 pricing (~20–30 min wall-clock, mostly unattended) — or (b) after
**every** bring-up, and after **every** pod restart, re-apply the one-line patch
to **both** `airflow-scheduler` and `airflow-webserver`, then verify
`schedule_interval="0 * * * *"`.

**(b) is not a fix, it is a standing debt.** It has now survived one node
replacement only because someone noticed within 90 minutes. On a bring-up after
an indefinite pause, nobody will be watching for it. **This is the single
strongest argument for doing the airflow rebuild before the next real run**, and
it is now the second time this file's predecessor has had to say so.

---

## 4. The other six DAGs are clean — verified, not assumed

The audit brief asked whether any of the other six carried a similar
undocumented live edit. **They do not.**

| DAG file | In-container mtime | vs repo HEAD |
|---|---|---|
| `arxiv_dag.py` | 2026-04-11 10:43:54Z | byte-identical |
| `fred_dag.py` | 2026-04-11 10:43:42Z | byte-identical |
| `googletrends_dag.py` | 2026-04-11 10:44:07Z | byte-identical |
| `newsapi_dag.py` | 2026-05-09 14:22:37Z | byte-identical |
| `opensky_dag.py` | 2026-04-11 11:05:22Z | byte-identical |
| `openweather_dag.py` | 2026-04-11 10:57:40Z | byte-identical |
| `hackernews_dag.py` | **2026-08-18 11:28:20Z** | **one line — see §3** |

Every mtime except HackerNews's is an image-build mtime, months old and
untouched. Since those six files are byte-identical to `HEAD`, **the image is
also at `HEAD` for them** — so the airflow rebuild recommended in §3 carries no
risk of changing any other DAG's behaviour. That is a useful thing to know
before pressing the button.

Live `schedule_interval`, all seven, read from the running scheduler and
cross-checked against the Airflow metadata DB:

| DAG | Schedule (live == repo HEAD) | Paused? | Last successful run |
|---|---|:---:|---|
| `arxiv_daily` | `0 7 * * *` | no | 2026-08-20T07:00:36Z |
| `fred_daily` | `0 6 * * *` | no | 2026-08-20T06:02:04Z |
| `googletrends_daily` | `0 8 * * *` | **PAUSED** | never run |
| `hackernews_high_frequency` | `0 * * * *` | no | 2026-08-20T10:01:04Z |
| `newsapi_high_frequency` | `*/20 * * * *` | no | 2026-08-20T10:40:34Z |
| `opensky_high_frequency` | `*/3 * * * *` | **PAUSED** | never run |
| `openweather_high_frequency` | `*/10 * * * *` | no | 2026-08-20T10:50:06Z |

No DAG had a `running` DagRun at audit time, and no DAG has ever recorded a
`failed` run. `googletrends_daily` and `opensky_high_frequency` have never
executed at all — they have been paused since creation, which is the
`AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION=true` default, not a decision anyone
took later.

**Pause state is live-only.** It lives in the Airflow metadata DB on
`airflow-postgres`'s PVC. The PVC survives a teardown, so pause state *should*
survive — but it is not in git, and a PVC loss takes it with no trace. The table
above is the record.

Also confirmed live-only and empty, so nothing to carry: **zero Airflow
Variables**, and only the stock `default_pool` (128 slots). No Connections were
configured beyond the DB URL built in the container's start script.

---

## 5. KG-C-10 — live desired replicas vs committed manifests

Read from the cluster, per §5 trap 4. **Two of the six workloads named in
KG-C-10 diverge from their manifest, in opposite directions.**

| Workload | Live desired | Committed manifest | Divergent? | Notes |
|---|:---:|:---:|:---:|---|
| `agent-worker` | **1** | `0` | **yes** | Manifest `0` is deliberate (§2); live `1` is the drift |
| `flink-jobmanager` | 1 | `1` | no | |
| `flink-taskmanager` | 1 | `1` | no | |
| `telegram` | 1 | `1` | no | |
| `polymarket` | 1 | `1` | no | |
| `trigger-consumer` | **0** | `1` | **yes** | Live `0` is the D10 resting state; an apply would start it |

Everything else in the namespace (`airflow-*`, `alertmanager`, `grafana`,
`kafka-exporter`, `kafka-ui`, `postgres-exporter`, `prometheus`, and all three
StatefulSets) is at desired 1 and matches its manifest.

**The trap is unchanged and is now double-ended.** `kubectl apply -f k8s/` at
the next bring-up would simultaneously **start** `trigger-consumer` (0→1,
producing Bronze nobody asked for — `bringup_profiles.md` §2) and **stop** the
agent (1→0) while regressing its image (§2). Neither failure announces itself.
**Use `kubectl scale` and `kubectl set image`; do not blanket-apply.**

---

## 6. Imperative-override sweep — the result is narrower than feared

Every Deployment and StatefulSet in `anizai` was diffed live-spec vs
`last-applied-configuration`, across replicas, images, full env, and resources.

**No `kubectl set env` override exists anywhere in the namespace.** Every
container's env is exactly what its manifest declares.

In particular, and as specifically asked: **`LOG_INFO_SAMPLE_RATE` is set on
nothing.** Not on `flink-jobmanager`, not on `flink-taskmanager`, not on
`agent-worker`, not on any StatefulSet or CronJob. Both Flink manifests carry
the explicit comment *"LOG_INFO_SAMPLE_RATE intentionally ABSENT (code default
0.01). DO NOT re-add it at 1.0."* and live matches. The §5 trap 3 hazard is
closed by construction and should be left that way.

The complete set of real imperative overrides in the namespace is:

1. `agent-worker` — image set by digest, twice (revisions 4 then 5). §2.
2. `agent-worker` — `replicas` 0→1. §5.
3. `trigger-consumer` — `replicas` 1→0. §5.

That is all. Six further "differences" flagged by the raw diff were API
normalization (`1000m`→`1`, `2048Mi`→`2Gi`, `RUN_ID: ""`→omitted) on
`airflow-scheduler`, `airflow-webserver`, `flink-jobmanager`, `flink-taskmanager`,
`postgres` and `agent-worker`. **`RUN_ID` is live-empty and manifest-empty** —
correct, and neutral until a run sets it.

---

## 7. Everything else the audit turned up

### 7.1 The Flink manifests are committed — the 7D T11 uncommitted state is resolved

The brief expected uncommitted edits to the two Flink manifests. **There are
none: `git status` is clean.** Both were committed in `5546002` (2026-08-19
21:04 +0300), bumping `1.19.1-pmcov` → `1.19.1-tgtrans`, and live matches:

```
flink-jobmanager / flink-taskmanager
  manifest: anizai-flink:1.19.1-tgtrans
  live:     anizai-flink@sha256:511ad08fcdbf33f2631b5ce3744cb0d6e0ff77bb4918a438cb42a2761ade9407
  registry: 1.19.1-tgtrans -> sha256:511ad08f...   all three agree
```

**No build is needed at the next bring-up** — the image is already in Artifact
Registry and the manifest already points at it (unlike the agent in §2, this
one isn't even drifted).

**But the source commit is not yet on the shared remote.** `5546002` exists
only in Ron's local repo — confirmed 2026-08-20, not pushed. This does not
block a bring-up (the image is what runs, and it's already built and pushed to
the registry), but it does mean anyone other than Ron who needs to read or
modify the Flink/Telegram-translation source (e.g. to work on the §7.6 gap)
is missing it until Ron pushes. Not urgent — no bring-up depends on it — but
worth clearing before someone else picks up §7.6.

No action needed at the next bring-up beyond the normal §5 trap 5 discipline.

### 7.2 Flink job identity and checkpoint position — needed to apply §5 trap 5 correctly

Both jobs were **cancelled and resubmitted** on 2026-08-19 against the new
`-tgtrans` image (JobManager pod started 17:54:25Z; jobs submitted 17:57:00Z and
17:57:49Z — three minutes later, which is the signature of a deliberate
resubmit, not an HA restore).

| Job | JobID | State | Latest completed checkpoint |
|---|---|---|---|
| `anizai-silver-polymarket` | `12d70fd54e00c4297ef1a3a81bbcf308` | RUNNING | `chk-1007` (1007 completed, 0 failed, 18 restores) |
| `anizai-gold-all-sources` | `d9d7c23f66fc0b1012589b5e66e753c9` | RUNNING | `chk-978` (977 completed, 1 failed, 5 restores) |

HA ConfigMaps `anizai-flink-<jobid>-config-map` exist for both, plus
`anizai-flink-cluster-config-map`. Checkpoints are on the `flink-checkpoints`
PVC (5 Gi), which survives the teardown.

**At the next bring-up: if the image is still `1.19.1-tgtrans`, do NOT cancel.**
The automatic HA restore is correct in that case, and the JobIDs above are what
should reappear — character for character. If they do reappear on a *changed*
image, that is the silent §5 trap 5 failure and the five-step cancel/resubmit
sequence applies.

### 7.3 `polymarket` — live matches the manifest; `cloud_state.md` does not

Checked against the registry directly rather than trusting `cloud_state.md`, as
instructed:

```
manifest: anizai-polymarket:0.4.1-inactive
live:     anizai-polymarket@sha256:c7b15eb715d9c676a6ccaa91a9e3dea1cfe95cb2c57a6e09ffd19bd7e69dcfc4
registry: 0.4.1-inactive -> sha256:c7b15eb7...   all three agree
```

`cloud_state.md`'s `0.3.0-price` is stale exactly as KG-C-12 says. Its 1 Gi
memory limit (`251c8e9`, KG-C-15) **is** applied live. No action needed.

### 7.4 Cloud Scheduler — both jobs are PAUSED

| Job | Schedule | Time zone | State |
|---|---|---|---|
| `scale-up-main-pool` | `0 5 * * 1-5` | Asia/Jerusalem | **PAUSED** |
| `scale-down-main-pool` | `0 15 * * 1-5` | Asia/Jerusalem | **PAUSED** |

Both were already paused before the teardown and were left that way. **This is
load-bearing for an indefinite stop:** nothing will resurrect the node pool on a
schedule, and the cluster will stay at zero node cost until someone resizes it
by hand. Equally: a future operator who expects the 05:00 scale-up to work will
wait for a node that never arrives. `main-pool` also has **no autoscaling**
configured (`e2-standard-8`, no min/max), so a manual resize is the only path up.

### 7.5 A stray SecretProviderClass

`openai-key-spc` exists live but belongs to no running workload. It is the
residue of `k8s/wi-smoke-test.yaml` (applied 2026-08-14T10:52:30Z); the smoke-test
Pod was cleaned up and the SPC was not. It **is** in git, so nothing is lost.
Harmless — noted only so a future inventory does not treat it as unexplained.

### 7.6 ⚠ The Telegram translation fix is live, works, and has not achieved its goal

Not part of the audit brief; found while capturing spend numbers, and it needs a
decision — after the teardown, not before it.

`5546002` (deployed 2026-08-19 17:57Z) wired the OpenAI client into the Telegram
Silver branch. **The translation half demonstrably works.** `llm_cost_events`
now carries `site='translate'` rows for the first time ever — 374 on 2026-08-19,
37 on 2026-08-20, $0.1386 total — and the text reaching `filter_rejects` is
English:

```
"Airport Authority employees opened a surprise strike, Ben Gurion Airport management: ..."
"Chaos at Ben Gurion Airport following peak loads: Flight check-ins halted, ..."
```

**But the outcome the commit was written to produce has not appeared.** After
~17 hours live:

- `social_vault` contains **309 rows, all `hackernews`. Zero Telegram rows.**
- Telegram is still being rejected — 1,485 rejects on 08-19, 51 on 08-20.
- Post-fix Telegram `relevance_score` is still **exactly 0.0000**, avg and max.
- `rescue_cosine` reaches **0.3476**, still under the `0.35`
  `GOLD_SEMANTIC_RESCUE_THRESHOLD` — close, but under.

So the alphabet-mismatch diagnosis in the commit message was right about the
mechanism and the fix does what it claims, yet the messages still do not clear
the gate in English. **Read the 0.0000 carefully before concluding anything:**
`hackernews` and `arxiv` rejects also score exactly 0.0000, so a zero in
`filter_rejects` may be what *any* rejected row looks like rather than evidence
of a Telegram-specific defect. Distinguishing those two readings needs the
Silver/Gold scoring code, which is out of scope here and was deliberately not
opened.

**Candidate for a Domain-A KG row; deliberately not opened.** Whoever picks it
up starts with one question: is `relevance_score` computed on the translated
text or the original?

### 7.7 Numbers worth keeping (§4 item 2)

Prometheus counters are process-local and its retention clock is evaluated on
startup, so these were written down rather than left in the cluster.

**Agent (Prometheus, since the pod started 2026-08-18T13:33:34Z):**

| Metric | Value |
|---|---|
| `sum(agent_llm_cost_usd_total)` | **$0.10337127** |
| `sum(agent_session_total)` | **4** |

**Pipeline (Postgres `llm_cost_events`, durable — survives everything):**

| Site | Calls | Cost USD | First | Last |
|---|---:|---:|---|---|
| `gold_enrich` | 4,097 | 1.6162 | 2026-08-15 | 2026-08-20 |
| `rescue_embed` | 43,435 | 0.1511 | 2026-08-15 | 2026-08-20 |
| `translate` | 411 | 0.1386 | 2026-08-19 | 2026-08-20 |
| `gold_consensus` | 38 | 0.0152 | 2026-08-15 | 2026-08-20 |
| `gold_embed` | 4,135 | 0.0041 | 2026-08-15 | 2026-08-20 |
| **Total** | **52,116** | **$1.9253** | | |

**$1.93 of OpenAI spend for the whole 2026-08-15 → 2026-08-20 continuous run.**
Roughly 10,400 calls/day against the Tier-1 10,000 RPD ceiling — but note that
43,435 of the 52,116 calls are `rescue_embed`, i.e. embeddings, which is the
volume driver rather than the cost driver. The hourly HackerNews cadence (§3)
was the mitigation that kept this in range; if the airflow image is ever rebuilt
back to `*/20`, this number moves.

**Vault state at teardown:**

| Table | Rows | Latest write |
|---|---:|---|
| `momentum_vault` | 281,793 | 2026-08-20T10:40:10Z |
| `filter_rejects` | 43,312 | 2026-08-20T10:39:12Z |
| `knowledge_vault` | 4,095 | 2026-08-20T10:24:09Z |
| `knowledge_vectors` | 4,095 | 2026-08-20T10:24:13Z |
| `social_vault` | 309 | 2026-08-20T09:00:08Z |
| `social_vectors` | 38 | 2026-08-20T01:00:37Z |

**Kafka retained records (`--time -1` minus `--time -2`, every topic in the
path).** Every topic reports `earliest=0`: nothing has aged out, because the
cluster was rebuilt on 2026-08-15 and seven-day retention has not yet elapsed.
**These are cumulative totals, not a backlog** — Flink tracks position by
checkpoint, not by offset (§7.2), and both jobs were caught up at teardown.

| Topic | Retained | | Topic | Retained |
|---|---:|---|---|---:|
| `serve.gold.structured_metrics` | 458,776 | | `ingest.bronze.newsapi` | 10,948 |
| `process.silver.structured_metrics` | 281,803 | | `ingest.bronze.arxiv` | 9,800 |
| `ingest.bronze.polymarket` | 144,565 | | `ingest.bronze.openweather` | 6,910 |
| `process.silver.global_news` | 43,210 | | `ingest.bronze.hackernews` | 5,950 |
| `process.silver.social_pulse` | 11,050 | | `ingest.bronze.telegram` | 1,689 |
| `serve.gold.global_news` | 4,095 | | `ingest.bronze.fred` | 508 |
| `serve.gold.social_pulse` | 38 | | `ingestion_triggers` | 4 |

`dead-letter-queue` is at **0** — nothing was dead-lettered across the entire
run. `ingest.bronze.googletrends`, `.opensky`, `.predictit` and `.reddit` are
all 0 (paused or retired producers).

**Firestore (project `anizai-ai`) — clean, and this matters for §3 Step 4 of the
next bring-up:**

| Query | Count |
|---|---:|
| `forecastQueries` by status | `done: 60`, `failed: 6` |
| `forecastQueries` at `pending` | **0** |
| `forecastQueries` at `claimed` | **0** — no orphan from a crashed worker (KG-B-21) |
| collection-group `messages` where `role=='user'` and `status=='sent'` | **0** |

**Both agent listener match sets were empty at teardown.** Nothing was in flight,
so nothing was orphaned by scaling the agent down, and — as of 2026-08-20 — the
§3 Step 4 stale-doc gate would pass with nothing to clear. **Re-run it anyway at
the next bring-up**; the frontend is external and can enqueue work at any time
while the cluster is down.

> Incidental: a collection-group query on `role` **alone** fails with
> `FailedPrecondition: requires a COLLECTION_GROUP_ASC index`. The worker's real
> query (`role` + `status`) has its composite index and works. If you are
> counting documents ad hoc, use the two-predicate form.

---

## 8. The teardown itself — carry-over (§4 step 7)

Executed 2026-08-20 11:03Z → 11:12Z, `bringup_profiles.md` §4 in order.
**This section alone is intended to be sufficient to reproduce today's running
state.** Read it with §2 and §3, which hold the two things git cannot give you.

### 8.1 What was done, in order

| Time (UTC) | §4 item | Action | Gate result |
|---|:---:|---|---|
| 11:00 | 1 | Verified Cloud Logging queryable for `agent-worker`, `flink-taskmanager`, `airflow-scheduler` | **Pass** — live entries returned; agent messages arrive on `jsonPayload.message`, so no log dump was needed |
| — | 2 | Numbers captured to §7.7 **before** anything stopped | Done |
| 11:03 | 3 | Paused all five running producer DAGs | 7/7 paused |
| 11:04 | 3 | `telegram` → 0, `polymarket` → 0 (§5 trap 1 — these are Deployments, not DAGs) | Both 0 |
| 11:05 | 5 | `agent-worker` → 0 | 0 |
| 11:05 | 3 | `flink-taskmanager` → 0, then `flink-jobmanager` → 0. **Jobs NOT cancelled** | Both per-job HA ConfigMaps still present after the scale — job graphs preserved |
| 11:05–11:06 | 4 | Manual `pg_dump` via `kubectl create job --from=cronjob/postgres-backup` | **Pass** — 68 s, 142.33 MiB at `gs://anizai-pipehub-backups/postgres/2026-08-20/anizai.sql.gz`, written 11:06:30Z |
| 11:07–11:12 | 6 | `gcloud container clusters resize anizai-cluster --node-pool=main-pool --num-nodes=0 --zone=us-central1-a --project=anizai-pipehub` | **Pass** — `kubectl get nodes` → *No resources found* |

**Two deliberate deviations from the literal text of §4, both noted so nobody
reads them as slips:**

1. **Flink was scaled to 0 and the backup was taken afterwards**, rather than
   backing up first. §4 does not scale Flink at all and puts the backup at item
   4. Taking the dump once nothing was writing gives a cleaner end state and
   costs nothing. Flink's *jobs* were **not** cancelled — §4 item 3's DISPUTED
   note was honoured; scaling the Deployment kills pods but leaves the HA
   ConfigMaps and the checkpoint PVC intact, which is what §5 trap 5 relies on.
2. **All five running DAGs were paused**, where §4 item 3 only asks you to
   re-pause what *this* session unpaused (which was nothing). This is an
   indefinite stop, and the alternative is that the next `resize --num-nodes=1`
   — by anyone, for any reason — silently restarts five producers, Gold
   enrichment and real OpenAI spend with no command issued. **If you disagree,
   §8.3 tells you exactly which five to unpause.**

### 8.2 State going in vs state left

**Desired replicas.** Everything not listed was at 1 going in and was left at 1
(`airflow-scheduler`, `airflow-webserver`, `alertmanager`, `grafana`,
`kafka-exporter`, `kafka-ui`, `postgres-exporter`, `prometheus`, and the
StatefulSets `airflow-postgres`, `kafka`, `postgres`). Those are all `Pending`
now — unschedulable with no node, which is the correct end state, not a fault.

| Workload | Going in | Left at | Changed by this teardown? |
|---|:---:|:---:|:---:|
| `agent-worker` | **1** | **0** | yes |
| `flink-jobmanager` | **1** | **0** | yes |
| `flink-taskmanager` | **1** | **0** | yes |
| `telegram` | **1** | **0** | yes |
| `polymarket` | **1** | **0** | yes |
| `trigger-consumer` | 0 | 0 | no — already at rest |

The end state is **exactly the D10 resting state**: the same six workloads at 0
that the 2026-08-15 migration established. A resize now brings up
infrastructure only.

**DAG pause state.**

| DAG | Going in | Left | Paused by this teardown? |
|---|:---:|:---:|:---:|
| `arxiv_daily` | running | **paused** | **yes** |
| `fred_daily` | running | **paused** | **yes** |
| `hackernews_high_frequency` | running | **paused** | **yes** |
| `newsapi_high_frequency` | running | **paused** | **yes** |
| `openweather_high_frequency` | running | **paused** | **yes** |
| `googletrends_daily` | paused | paused | no — never run, paused since creation |
| `opensky_high_frequency` | paused | paused | no — never run, paused since creation |

**Cloud Scheduler:** `scale-up-main-pool` and `scale-down-main-pool` were
already PAUSED and were left PAUSED (§7.4). **Nothing will bring this cluster
back on a schedule.** The next bring-up is entirely manual.

### 8.3 Exact image identity per workload, at teardown

Every digest below was read from the running pod and cross-checked against
Artifact Registry. **Pin by digest where the tag is ambiguous** — which for the
agent it is (§2).

| Workload | Tag at teardown | Digest | Matches its manifest? |
|---|---|---|:---:|
| `agent-worker` | `0.6.2-dynamic-followups-amd64-419e58e` | `sha256:8b2e7e650ee00834741e7f7deee712ba9af1f57f2a462be2cab8953e90cf5d62` | **NO — §2** |
| `flink-jobmanager` | `1.19.1-tgtrans` | `sha256:511ad08fcdbf33f2631b5ce3744cb0d6e0ff77bb4918a438cb42a2761ade9407` | yes |
| `flink-taskmanager` | `1.19.1-tgtrans` | `sha256:511ad08fcdbf33f2631b5ce3744cb0d6e0ff77bb4918a438cb42a2761ade9407` | yes |
| `airflow-scheduler` | `2.9.3-7b5i` | `sha256:2ab60dd102369d8a6593639598830a450ec7ff4b57c50199b886b5101452b21f` | yes — but see §3 |
| `airflow-webserver` | `2.9.3-7b5i` | `sha256:2ab60dd102369d8a6593639598830a450ec7ff4b57c50199b886b5101452b21f` | yes — but see §3 |
| `telegram` | `0.1.0` | `sha256:72dbb4649a510a62fea87d63e2ce3937430fa572d61f5a9731c589bb4bfa1ad0` | yes |
| `polymarket` | `0.4.1-inactive` | `sha256:c7b15eb715d9c676a6ccaa91a9e3dea1cfe95cb2c57a6e09ffd19bd7e69dcfc4` | yes |
| `trigger-consumer` | `0.1.0` | `sha256:36b9079656113daa3cefe9f3fd70c07d6c2b3cde4c457d043158b3987efa0eab` | yes (was at 0; digest from AR) |

Third-party images, unchanged and unremarkable: `timescale/timescaledb-ha:pg16`,
`apache/kafka:3.7.0`, `postgres:16`, `prom/prometheus:v2.51.2`,
`prom/alertmanager:v0.27.0`, `grafana/grafana:10.4.2`,
`danielqsj/kafka-exporter:v1.7.0`,
`prometheuscommunity/postgres-exporter:v0.15.0`,
`provectuslabs/kafka-ui:v0.7.2`, `google/cloud-sdk:slim` (backup CronJob).

### 8.4 What survives, and what did not

**Survives:** all five PVCs — `postgres-data-postgres-0` (20 Gi),
`kafka-data-kafka-0` (10 Gi), `prometheus-data` (10 Gi), `flink-checkpoints`
(5 Gi), `airflow-postgres-data-airflow-postgres-0` (5 Gi). Plus the GCS backup
(§8.1), all Secret Manager secrets, Firestore in `anizai-ai`, and every
Kubernetes object in the namespace (Deployments, ConfigMaps,
SecretProviderClasses, the Flink HA ConfigMaps).

**Note that the PVCs continue to bill** — 50 Gi of `standard-rwo` — even at zero
nodes. That is the intended trade (the alternative is losing the vaults), but it
is not free, and on an *indefinite* pause it is the only recurring cost left.
Someone should decide, at some point, whether 50 Gi is worth keeping warm.

**Did not survive:** the agent's Prometheus counters (`agent_llm_cost_usd_total`,
`agent_session_total`, `agent_node_duration_seconds`) are process-local and went
with the pod — which is why §7.7 exists. Prometheus's own on-disk history is on
its PVC and should return, but **its retention clock is evaluated on startup**,
so history near the retention edge may be purged minutes into the next bring-up.
Treat anything you still need from it as already gone unless §7.7 has it.

### 8.5 The next bring-up, in order

To reproduce **today's** running state (this is the FULL profile plus the two
live-only patches):

1. `bringup_profiles.md` §3 Step 0 — confirm context and project. Expect 0 nodes.
2. **Run the §3 Step 4 Firestore gate first, while the pool is still at 0.** It
   was clean at teardown (§7.7) but the frontend can enqueue at any time, and
   both agent listeners claim their entire match set on attach (§5 trap 2).
3. §3 Step 1 — set desired replicas per profile. **Six workloads are at 0 and
   none of them come up on their own.** For FULL: raise `flink-jobmanager`,
   `flink-taskmanager`, `telegram`, `polymarket`, `trigger-consumer` to 1.
   Leave `agent-worker` for step 7.
4. §3 Step 2 gate — re-read every one from the cluster. Do not skip.
5. §3 Step 3 — resize to 1 node. `--zone=us-central1-a` and
   `--project=anizai-pipehub` are both mandatory.
6. **Re-apply the two live-only patches — neither is in any image:**
   - **HackerNews cadence (§3).** Patch `schedule_interval` to `"0 * * * *"` in
     `hackernews_dag.py` in **both** `airflow-scheduler` and
     `airflow-webserver`, then verify. Unless the airflow image has been rebuilt
     from `HEAD` by then — in which case skip this and verify anyway.
   - **Unpause the five producer DAGs** (§8.2): `arxiv_daily`, `fred_daily`,
     `hackernews_high_frequency`, `newsapi_high_frequency`,
     `openweather_high_frequency`. `googletrends_daily` and
     `opensky_high_frequency` stay paused — they have never run.
7. **Agent: `kubectl set image` to the 0.6.2 digest, then scale to 1** — the
   exact commands are in §2. **No build or push is needed** — the image is
   already in Artifact Registry, confirmed 2026-08-20 (§2 "Registry
   confirmation"). **Do not `kubectl apply` the agent manifest.** Then run the
   §3 Step 5 health gate; expect `AGENT_VERSION 0.5.0-sprint26+419e58e`.
8. **Flink:** if the image is still `1.19.1-tgtrans`, the HA restore is correct —
   **do not cancel**. Confirm the restored JobIDs match §7.2 character for
   character. If the image changed, §5 trap 5's five-step cancel/resubmit is
   mandatory and the failure it prevents is silent.
9. §3 Step 4 backlog gate — size every topic (§7.7 has the teardown baseline).
   Both jobs were caught up at teardown, so the backlog should be ~0 plus
   whatever the producers emit once raised.

### 8.6 The one-paragraph version

**The cluster is at zero nodes with everything off and nothing scheduled to
bring it back.** Six workloads rest at desired 0; all seven DAGs are paused;
both Cloud Scheduler jobs are paused. Data is safe on five PVCs plus a fresh
142 MiB GCS dump. **Two things will not come back on their own and are not in
any image: the agent is running a version (`0.6.2-dynamic-followups`,
digest `sha256:8b2e7e65…`) that appears nowhere in this repository (§2), and the
HackerNews hourly cadence is a one-line in-container patch that the image
overwrites with `*/20` (§3).** If you restore this cluster by applying the
manifests and nothing else, you will get an older agent that is scaled to zero,
a trigger-consumer nobody asked for, and HackerNews at three times its intended
rate — and every pod will report itself perfectly healthy while doing it.

---

## 9. Pointers

- Bring-up / teardown procedure → `docs/guides/bringup_profiles.md`
- The previous carry-over, and the origin of the in-container-patch lesson →
  `carryover-20260818-node-replacement.md` §3
- KG-C-10 (manifest vs live replicas) and KG-C-12 (`cloud_state.md` staleness) →
  `cloud_sprints.md` §4 — **KG-C-12 now also covers the `agent-worker` row that
  its own header exempts (§2)**
- Agent image identity → **this file §2 and §8.3**, not `cloud_state.md` §3
- Manual backup / restore procedure → `cluster_operations_guide.md` §8
- Telegram translation follow-up → §7.6, unfiled
