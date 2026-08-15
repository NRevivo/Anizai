# carryover-20260815-migration.md

> Domain: C — Cloud
> Type: Carry-over (sprint close record)
> Created: 2026-08-15
> TL;DR: The Anizai cloud deployment was rebuilt in the new GCP project
> `anizai-pipehub`, verified end-to-end with one real forecast, and switched
> off at zero nodes. This file is the definition-of-done record for the
> Cloud Project Migration sprint.

## Navigation
- §1 — Identity & endpoints
- §2 — Images pushed, and which ones are *proven* vs *intended*
- §3 — Secret inventory (by name)
- §4 — Resting state at teardown, and the D10 divergence
- §5 — Flink jobs left in the HA store
- §6 — The deployed agent misidentifies itself (read before trusting `/health`)
- §7 — Schema provenance
- §8 — Verification results, including two gates that FIRED
- §9 — Things that do not travel with the repo
- §10 — Accepted losses at old-project deletion

---

## §1 — Identity & endpoints

| Item | Value |
|---|---|
| GCP project | `anizai-pipehub` |
| GKE cluster | `anizai-cluster`, zone `us-central1-a` |
| Node pool | `main-pool`, `e2-standard-8`, **resting at 0 nodes** |
| Namespace | `anizai` |
| Artifact Registry | `us-central1-docker.pkg.dev/anizai-pipehub/anizai-images` |
| GCS backup bucket | `gs://anizai-pipehub-backups` (30-day lifecycle on `postgres/`) |
| Firestore (cross-project) | `anizai-ai` — unchanged, the only multi-project hop |
| Billing | `010C82-6CA2C4-183381`, ILS, ₪200 warning / ₪400 critical |

**Two Google identities, one per project — this is not optional knowledge.**

| Account | Role |
|---|---|
| `kingron79@gmail.com` | `roles/owner` on **`anizai-pipehub`**. Ran the entire migration. **No access at all to `anizai-ai`** |
| `ron.mintz21@gmail.com` | `roles/owner` on **`anizai-ai`**. Required for the S4 grant and for any future `anizai-ai` IAM work |

Use `--account=ron.mintz21@gmail.com` as a per-invocation flag for `anizai-ai`
work; do **not** `gcloud config set account`, or every subsequent
`anizai-pipehub` command runs as the wrong identity. Under `kingron79@` an
`anizai-ai` IAM read returns a *permission error*, not an empty result — do
not confuse the two.

---

## §2 — Images pushed (10 tags)

All re-tagged from the local daemon, never rebuilt. Digests verified in the
registry after push.

| Image | Tag | Digest | Status |
|---|---|---|---|
| `anizai-flink` | `1.19.1-pmcov` | `sha256:963cbf9dea97d3470b084a9411ca84281bfb1c7222708886aa4cb816c0238c52` | **verified ran** |
| `anizai-airflow` | `2.9.3-7b5i` | `sha256:2ab60dd102369d8a6593639598830a450ec7ff4b57c50199b886b5101452b21f` | **verified ran** |
| `anizai-agent` | `0.6.0-trackA` | `sha256:937dfed1763faf872719210bde6f28000cb8baf3ca036b5fe3961273471d9aee` | **verified ran** |
| `anizai-telegram` | `0.1.0` | `sha256:72dbb4649a510a62fea87d63e2ce3937430fa572d61f5a9731c589bb4bfa1ad0` | **verified ran** |
| `anizai-trigger-consumer` | `0.1.0` | `sha256:36b9079656113daa3cefe9f3fd70c07d6c2b3cde4c457d043158b3987efa0eab` | **verified ran** |
| `anizai-polymarket` | `0.4.1-inactive` | `sha256:c7b15eb715d9c676a6ccaa91a9e3dea1cfe95cb2c57a6e09ffd19bd7e69dcfc4` | ⚠ **latest-intended, never confirmed live** |
| `anizai-agent` | `0.5.0-sprint26` | `sha256:7fce4e8b0b13700fed2ff05071d9ebd277e9b27fab2f70a46d6ba5917c316ef4` | rollback |
| `anizai-flink` | `1.19.1-7d` | `sha256:9a73a780ea7eae000a39cd3c20d9b6f94b223559e92feec41766c4faebced303` | rollback |
| `anizai-polymarket` | `0.4.0-coverage` | `sha256:1d27ec0591f83f71a3c91998d68e321cb54d622f0e9e98a8d66259df6c43d758` | rollback |
| `anizai-polymarket` | `0.2.0-p95` | `sha256:a2a3e82eea47353d4f29f41c1b5e59f15ed50028c6d86f929bfacdc6e93cfd5c` | rollback (optional) |

**The polymarket qualifier is deliberate.** The sprint goal said "byte-identical
to what ran". For five images that is true. For `polymarket:0.4.1-inactive` the
sources disagree and cannot be reconciled:
`archive_carryovers/carryover-20260801-window2.md` §4 (archived 2026-08-15)
lists it in the window-2 teardown table, while `cloud_state.md` §3 says
`0.4.0-coverage` was the image *deployed* for that window and calls
`0.4.1-inactive` the image "to roll out". It is the correct tag either way —
both readings point at it, and it is a P2-verified artifact — but **do not
restate it as "what ran in cloud"**.

### Manifest tags corrected by R6

| File | Was | Now |
|---|---|---|
| `k8s/flink-jobmanager-deployment.yaml` | `1.19.1-7d` | `1.19.1-pmcov` |
| `k8s/flink-taskmanager-deployment.yaml` | `1.19.1-7d` | `1.19.1-pmcov` |
| `k8s/producers/polymarket-deployment.yaml` | `0.2.0-p95` | `0.4.1-inactive` |
| `k8s/agent-deployment.yaml` | `0.5.0-sprint26` | `0.6.0-trackA` |

JM and TM must stay identical — mismatched tags produce Py4J bridge errors at
job submission.

---

## §3 — Secret inventory (15, by name)

`AIRFLOW_ADMIN_PASSWORD` · `AIRFLOW_FERNET_KEY` · `AIRFLOW_POSTGRES_PASSWORD` ·
`FRED_API_KEY` · `GMAIL_APP_PASSWORD` · `GRAFANA_ADMIN_PASSWORD` ·
`NEWSAI_API_KEY` · `OPENAI_API_KEY` · `OPENSKY_CLIENT_ID` ·
`OPENSKY_CLIENT_SECRET` · `OPENWEATHER_API_KEY` · `POSTGRES_PASSWORD` ·
`TELEGRAM_API_HASH` · `TELEGRAM_API_ID` · `TELEGRAM_SESSION_FILE`

**Three numbers, all different, all correct:** the `03_migrate_secrets.sh`
allowlist has **17** entries, a clean run created **14** from `.env`, and **15**
are mounted by a SecretProviderClass. `TELEGRAM_SESSION_FILE` is binary and
uploaded separately with `--data-file=`. `THE_NEWS_API_KEY`,
`POLYMARKET_API_KEY` and `POLYMARKET_API_SECRET` report `absent or empty` and
that is correct behaviour, not a fault.

**IAM: 17 per-secret grants**, verified by read-back — `pipeline-runtime` on all
15, `agent-worker` on exactly `OPENAI_API_KEY` + `POSTGRES_PASSWORD`. Least
privilege, not project-wide.

**Workload Identity — the mapping is asymmetric:**

| KSA (Kubernetes) | GSA (Google) |
|---|---|
| `pipeline-runtime` | `pipeline-runtime@…` |
| **`agent-worker-ksa`** | **`agent-worker@…`** |

Both KSAs are now committed as YAML (`k8s/pipeline-runtime-ksa.yaml`,
`k8s/agent-worker-ksa.yaml`) — previously they existed only as imperative
commands in a runbook. `agent-deployment.yaml` is the authority for the name;
three documents had it wrong and were corrected.

---

## §4 — Resting state at teardown

**Node pool `main-pool` = 0 nodes.** `kubectl get nodes` → *No resources found*.

**Six workloads at desired 0** — read from the cluster, not the repo:

`flink-jobmanager` · `flink-taskmanager` · `agent-worker` · `polymarket` ·
`telegram` · `trigger-consumer`

**Everything else comes back on a resize:** `postgres`, `airflow-postgres`,
`kafka`, `airflow-scheduler`, `airflow-webserver`, `kafka-ui`, `prometheus`,
`grafana`, `alertmanager`, `kafka-exporter`, `postgres-exporter`.

### ⚠ The D10 divergence — read this before the next bring-up

**This cluster rests differently from the old project.** The old project's last
teardown left Flink **and** `trigger-consumer` at desired **1**, and
`bringup_profiles.md` §2 still expects `trigger-consumer` up under every
profile. Here all six rest at **0**.

Rationale: nothing that can ingest or spend should start by itself on a resize
weeks from now. `trigger-consumer` in particular ingests nothing on its own, but
its SPC mounts the OpenWeather and OpenSky keys and it dispatches whatever
arrives on `ingestion_triggers` — so with the agent live and Flink live, one
reactive trigger becomes real ingestion and real enrichment spend.

**Scheduler jobs: both PAUSED** (`scale-up-main-pool`, `scale-down-main-pool`).
Ron resizes `main-pool` manually.

**Airflow: 7 DAGs, 7 paused, 0 unpaused.** Established by manifest
(`AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION="true"` on both Airflow
Deployments), **not by hand**. On a virgin metadata DB the old `"false"` would
have created 6 of 7 unpaused, and `opensky_high_frequency` runs `*/3 * * * *` —
a three-minute fuse, with ingestion starting under LocalExecutor even with every
producer Deployment at 0. `docker-compose.yml` deliberately stays `'false'`.

**KG-C-10 is now wider, not narrower.** The manifests still declare
`replicas: 1` for the six workloads resting at 0. A routine
`kubectl apply -f infrastructure/k8s/` restarts Flink and all three producers.

---

## §5 — Flink jobs, left in the HA store uncancelled

| Job | JobID |
|---|---|
| `anizai-silver-polymarket` | `84fa1cf650a65beb25568298795d94f5` |
| `anizai-gold-all-sources` | `bac4e3027f70d36363008822b13c6110` |

Both reached `RUNNING` and completed 2 checkpoints each (0 failed) before Flink
was scaled to 0. **The jobs were not cancelled** — cancellation at teardown is
disputed and deliberately untested (`bringup_profiles.md` §4 item 3). Their job
graphs survive as ConfigMaps
`anizai-flink-<jobid>-config-map`, confirmed present after scale-down.

Both jobs compiled from `1.19.1-pmcov`, running identically on JM and TM.

---

## §6 — ⚠ The deployed agent misidentifies itself

```
/health  →  agent_version: "0.5.0-sprint26+35c343b"
image    →  anizai-agent:0.6.0-trackA
digest   →  sha256:937dfed1763faf872719210bde6f28000cb8baf3ca036b5fe3961273471d9aee
```

**The digest is the only reliable identifier.** The image tagged `0.6.0-trackA`
carries a build-stamp that still says `0.5.0-sprint26`; only the git sha moved
(`55e8093` → `35c343b`). The semantic version was never bumped at build time —
the `0.6.0` exists in the tag alone.

Anyone reading `/health` cold will conclude the Sprint-26 image is deployed. The
old project's agent reported `0.5.0-sprint26+55e8093`, so **only the sha
distinguishes them**. This is a build defect inherited from the artifact, not
introduced by the migration — the old project reported the same thing. Fix it at
the build (see backlog); never by patching a running Deployment. Related: KG-C-3
(tags rather than digests in manifests).

---

## §7 — Schema provenance

The database schema was created **automatically from `init.sql` on first init,
including migration 004**. Nothing needs applying by hand on this database.

- `postgres-configmap.yaml` was regenerated from `init.sql` (R7). `init.sql`
  already carried 004 as of commit `6ebd753`; the **ConfigMap** was the stale
  artifact, last regenerated at `da856bc`, one commit behind. The StatefulSet
  mounts the ConfigMap, so a fresh DB would otherwise have been born without
  `filter_rejects.canonical_event_id`.
- Verified live: `canonical_event_id` column and `idx_filter_rejects_cei` index
  both present on `filter_rejects`.
- Init log confirmed the VERIFICATION block: 3 extensions
  (`pg_trgm`, `timescaledb`, `vector`), 10 tables.
- Migrations 002/003 reflected — `scrape_attempted` absent, `filter_rejects` and
  `llm_cost_events` present.

**Trap for the next person:** if `init.sql` ever fails partway, restarting the
pod does **not** re-run it — Postgres only runs init scripts against empty
PGDATA, so the container restarts cleanly into a half-built schema and reports
Ready. Recovery is deleting the **PVC**, not the pod.

Kafka: **19 topics**, created by `kafka-init`, hourly reassert CronJob active.

---

## §8 — Verification results

**Gate S6 — passed.** One real forecast through the partner frontend
("Will Abiy Ahmed be the next Prime Minister of Ethiopia?") reached `done`.

- `agent_session_total{status="done",tier="tier_2"} 1.0`
- Result shape correct for an empty vault: 50% probability, confidence 20/100,
  consensus Weak, "Coin Flip — Avoid", with the agent stating plainly that the
  evidence does not support a confident forecast. **The agent correctly
  reporting its own epistemic state is the proof, not a shortfall.**
- Cost **$0.01074** (`gpt-4o` $0.01024, `gpt-4o-mini` $0.00050, embeddings
  $0.00000024) against the ≈$0.026/forecast baseline from the 2026-07-25/26 run
  — cheaper exactly where the missing vault evidence makes it cheaper.
- Node timings make the empty vault measurable: `vault_query` 0.04s,
  `rate_evidence` 0.0002s, `synthesize` 6.11s dominating. ≈12.7s total.
- **Both Firestore listeners proven behaviourally**, not inferred: listener 1
  (`forecastQueries`) by the forecast pickup; listener 2 (the `messages`
  collection-group listener) by a follow-up question that was answered. Listener
  2 is the one that fails **silently** on a missing collection-group index in
  `anizai-ai` — the 2026-07-23 index deployment is now confirmed against *this*
  cluster, not merely inherited.

### Two gates that FIRED — record them as catches, not formalities

**1. The Firestore stale-document gate (`bringup_profiles.md` §3 step 4).**
Run before the agent came up, it found **two** stale documents: one
`forecastQueries` doc at `pending` (an accidental click, two days old) and one
collection-group `messages` doc with `role=user`/`status=sent` dated the 11th.
Both deleted. Had they survived, the agent's listener would have picked up the
pending doc within seconds of scale-up — before a browser was even open — and
S6 would have had two forecasts running concurrently with no way to tell which
result was the one under test. **This gate has been carried as a formality; it
is not one.**

**2. The backup-path check (S7 step 4).** Verified by **listing the object**,
not by the Job's exit code:

```
gs://anizai-pipehub-backups/postgres/2026-08-15/anizai.sql.gz   4865 bytes
```

The GCS bucket rename is a class-B change (a separate globally-unique
namespace, not a substring swap). A wrong bucket name in the CronJob would have
surfaced only at 02:00 UTC the next day, as a silent failure.

### Layer 6 — a measured failure worth recording

`kubectl apply && kubectl scale --replicas=0` **does not prevent producer
containers from starting.** Measured:

| Pod | scale issued | Started | Killing |
|---|---|---|---|
| `polymarket` | 08:38:41 | 08:38:44 | 08:38:44 |
| `telegram` | 08:38:42 | 08:38:45 | 08:38:46 |
| `trigger-consumer` | 08:38:42 | 08:38:48 | 08:38:48 |

The race is **image-pull time against scale propagation**, not typing speed. All
three containers reached `Started` and were killed within ~1s. **No harm this
time** — 55 partitions enumerated, 0 messages across every Kafka topic — but the
mitigation is unsound and would fail worse with a warm image cache. The correct
approach is to transform the manifest to `replicas: 0` *before* apply, never to
correct after. This upgrades KG-C-10 from a proposal to a measured behaviour and
needs a `bringup_profiles.md` §5 trap entry.

---

## §9 — Things that do not travel with the repo

**Three files are git-ignored, and they are exactly the ones that tell you how
to work on this repo:**

| File | Ignored by |
|---|---|
| `.claude/` (all skills) | root `.gitignore:10` — `/.claude` |
| `CLAUDE.md` | root `.gitignore:2` |
| `data-pipeline/project_master.md` | `data-pipeline/.gitignore:37` |

**R9's corrections are machine-local and will not arrive by `git pull`.** On any
other machine, `.claude/skills/gcp-deployment/SKILL.md` still names
`anizai-pipeline` and is auto-loaded at every Phase C kickoff. Redo them there:

- `gcp-deployment/SKILL.md` — constants table (project, Artifact Registry path,
  both GSA emails, GCS bucket); the front-matter `description` (project ID
  **only** — every other term is skill-trigger surface); the Agent KSA name in
  **three** places (§1 row, §4 bullet, §12 Quick Reference Card) → `agent-worker-ksa`;
  the "15 baseline secrets" line → the 17/14/15 explanation; the upstream
  CSI-driver label check → the GKE-native add-on check; broken `docs/archive/`
  pointers → `docs/old_docs/` (3 occurrences).
- `cloud-principles/SKILL.md` — lines 42 and 76, the P3 cross-project boundary.
- `CLAUDE.md` — zero hits, nothing to do.

#### Second batch — the doc reorg, 2026-08-15 (also machine-local)

`cloud_constants.md` was created so these facts would stop living only in
git-ignored files. The corresponding edits *to* the ignored files — replacing
their restated constants with references to it — are themselves ignored and will
not arrive by `git pull`. Redo on any other machine:

- **`gcp-deployment/SKILL.md`**
  - §1 — the 13-row Project Constants table **deleted**, replaced by a pointer to
    `data-pipeline/docs/C_cloud/cloud_constants.md` §1–§4 plus a note on why the
    values are not restated. The **only** value kept inline is the
    `agent-worker-ksa` / `agent-worker@` asymmetry — load-bearing at every point
    of use, and a wrong value leaves the agent pod unschedulable.
  - §4 bullet 2 — `agent-worker-ksa` name kept, restated rationale dropped
    (→ `cloud_constants.md` §3).
  - §5 bullet 1 — the 17/14/15 paragraph replaced by a pointer to §4.
  - §10 row 2 — CSI-driver explanation trimmed to the check itself; the
    which-driver-and-why sent to §4 "CSI driver". The `kubectl get csidriver`
    command **stays** — that is a procedure, not a constant.
  - §12 row 1 — unchanged; it already carries only the name, no rationale.
- **`cloud-principles/SKILL.md`** — Cross-References only. P3/P4 keep
  `anizai-pipehub` and `polymarket-pool` inline: those are guardrail sentences
  where removing the name would weaken the rule, not lookup entries. Added a
  pointer to `cloud_constants.md` and a KG-C-12 warning not to take identity from
  `cloud_state.md` until its refresh lands.
- **`CLAUDE.md`, `project_master.md`** — no constants restated; no edit. (Verified
  by sweep, not assumed: `project_master.md`'s 5 Domain-C hits are narrative
  sprint prose, not fact-table entries.)

**Sweep method, recorded because it changed the result.** ripgrep honours
`.gitignore`, so a default `rg carryover .` silently skipped `/.claude`,
`/CLAUDE.md` and `data-pipeline/project_master.md` — the three most suspect
files. Use `rg --no-ignore --hidden` and prove reachability with a positive
control before reading any zero as evidence.

When the ignore is revisited, un-ignore **`.claude/skills/`** specifically, not
`/.claude` wholesale — the latter would sweep in local settings and permissions.

**The `project_master.md` §3 cursor update is also local-only**, so the
un-fencing of that section must happen on whichever machine holds the file.

### Explanatory comment blocks added to code (11 committed files)

These carry rationale that exists nowhere else, and they have no review cycle:

`gcp/03_migrate_secrets.sh` · `gcp/04_create_cluster.sh` · `gcp/README.md` ·
`k8s/pipeline-runtime-ksa.yaml` · `k8s/agent-worker-ksa.yaml` ·
`k8s/flink-jobmanager-deployment.yaml` · `k8s/flink-taskmanager-deployment.yaml` ·
`k8s/producers/polymarket-deployment.yaml` · `k8s/agent-deployment.yaml` ·
`k8s/airflow-scheduler-deployment.yaml` · `k8s/airflow-webserver-deployment.yaml`

**KG-C-5 (the `NEWSAI_API_KEY` → `THE_NEWS_API_KEY` rename) invalidates two of
them**: the `SECRET_KEYS` rationale block in `03_migrate_secrets.sh`, which
explains why both news keys coexist, and the secrets block in `gcp/README.md`.
Both go false the moment the rename lands. A comment that outlives its reason is
how the original drift started.

---

## §10 — Accepted losses at old-project deletion

**`anizai-polymarket:0.3.0-price`** (`sha256:2ae00dae…899cac8`) — the
collaborator's price fix, the chain link between `0.2.0-p95` and
`0.4.0-coverage`. It is **absent from the local Docker daemon** and was not
pushed. When `anizai-pipeline` is deleted it ceases to exist anywhere. Accepted:
it is a superseded intermediate, and `0.4.0-coverage` was pushed precisely to
protect the immediate predecessor. **Recorded so deletion is a decision, not a
discovery.**

Two bindings on `anizai-ai` still reference the dying project and need
`ron.mintz21@gmail.com` to remove:

- `agent-worker@anizai-pipeline.iam.gserviceaccount.com` — dangling after
  deletion; sits beside the new binding this sprint created.
- `calibration-runner@anizai-pipeline.iam.gserviceaccount.com` — the calibration
  collaborator's identity, **hosted in the dying project**. Deletion destroys it.
  Must be handled **before** old-project deletion, with them directly.

---

## Definition of done

Gate S6 passed, Gate S7 verified, this carry-over written. §9 item 3
(old-project deletion) is now unlocked — subject to the two items in §10 above.
