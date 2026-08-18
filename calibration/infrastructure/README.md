# Cloud automation — Phase 10D

Everything here is written and tested. **None of it has been provisioned.**
`provision.sh` does nothing until invoked with an explicit step, and every
step confirms before it acts.

Start with `./provision.sh plan` — it prints what would be created and touches
nothing.

---

## Why there is no scheduled dispatch job

This is the most important thing in this directory, so it is first.

The original plan called for a weekly `calibration-weekly-reforecast` job
hitting `/tasks/dispatch`. **That job is deliberately not created, and
`CALIBRATION_DISPATCH_TASK_ENABLED` defaults to `false`.**

The reason is the operating model, not the code. The agent is held at
`replicas: 0` and scaled up by hand for each run — a deliberate cost decision,
documented in `data-pipeline/docs/C_cloud/cloud_state.md` and confirmed by the
team on 2026-07-29. Scheduling an unattended dispatch into an agent nobody
switched on would:

1. write 28 forecast queries into Firestore that nothing ever claims,
2. leave them until the harvester ages every one into `timed_out` after two
   hours,
3. record 28 failures that never happened, and
4. repeat every week, steadily filling the database with fictional failures
   while producing no measurements at all.

The other four tasks are safe to schedule immediately. They cost HTTP calls
rather than tokens, they are idempotent, and they do nothing when there is
nothing to do:

| Job | Schedule | What it costs |
|---|---|---|
| `discover-hourly` | `0 * * * *` | a few Polymarket calls |
| `harvest-5min` | `*/5 * * * *` | Firestore reads for in-flight forecasts only |
| `resolve-hourly` | `15 * * * *` | one Polymarket call per question near its date |
| `snapshot-daily` | `30 3 * * *` | pure computation over Postgres |

**To enable scheduled dispatch**, two things must become true — and the second
is a decision, not a configuration:

1. `CALIBRATION_DISPATCH_TASK_ENABLED=true` on the service, and
2. the agent stays up on a schedule that overlaps the dispatch window.

Until (2) holds, dispatch stays manual: scale the agent up, run
`python -m calibration.cli dispatch --allow-live`, harvest, scale it down.
That is the flow in `docs/OPERATOR_RUNBOOK.md` §4, and it is the flow the
first live run on 2026-07-29 used successfully.

---

## Order of operations

```
./provision.sh plan          # read this first, creates nothing
./provision.sh sa            # service accounts
./provision.sh iam           # bindings  <-- most likely to fail, see below
./provision.sh db            # Cloud SQL  <-- first step that costs money
#   apply the schema through the Cloud SQL proxy (the db step prints how)
./provision.sh secrets       # operator allowlist
./provision.sh build         # image
./provision.sh deploy        # Cloud Run
./provision.sh schedulers    # the four safe jobs
```

`iam` is deliberately before `db`: it is the step most likely to be refused,
and discovering that **before** creating a billable instance is cheaper than
discovering it after.

---

## Known blockers, measured 2026-07-29

Checked against the live project with the credentials available at the time.

> **Re-read this table against the project rename (2026-08-17).** It was
> measured on `anizai-pipeline`, which has since expired and is being deleted;
> the pipeline moved to **`anizai-pipehub`**. The blockers below are stated
> against the new project because their commands are runnable, but none of them
> has been **re-measured** there. Treat the statuses as inherited, not verified.

| Blocker | Status | What it needs |
|---|---|---|
| **Cloud Run API not enabled** on `anizai-pipehub` | blocking `deploy`, unverified since the move | `gcloud services enable run.googleapis.com --project=anizai-pipehub` — a project decision, not a script's |
| **No `resourcemanager.projects.setIamPolicy`** | blocking `iam` | The cross-project Firestore binding must be run by an admin on `anizai-ai`. `provision.sh iam` attempts it and prints the exact command on failure. Without it the service deploys and cannot write — the worst place to discover a permission gap. |
| **Ingestion pipeline paused** since 2026-07-23 | not blocking, but see below | Automation over a frozen vault measures noise. Every scheduled forecast would see the same evidence and return the same answer. |

The third is not a technical blocker and the scripts will run regardless — but
scheduling a measurement system on top of a data source that is being rebuilt
produces numbers that look real and mean nothing. That is the exact failure
this project exists to prevent, so it is recorded here rather than left for
someone to rediscover.

---

## Cost

| | |
|---|---|
| Cloud SQL `db-f1-micro`, 10GB | ~$8–10 / month — **the only fixed cost** |
| Cloud Run, min-instances 0 | idle is free; a few cents per month at this volume |
| Cloud Scheduler, 4 jobs | ~$0.10 / month |
| Artifact Registry | pennies |
| **Forecast tokens** | **not billed here** — they land on the agent's OpenAI account, roughly $0.03 per forecast |

The token cost is the one that scales with use and the one not visible in this
project's billing. 28 questions re-forecast weekly is roughly $3.40/month of
someone else's budget, which is why dispatch has its own switch.

---

## Rollback

```bash
gcloud scheduler jobs pause calibration-discover-hourly --location us-central1
gcloud scheduler jobs pause calibration-harvest-5min    --location us-central1
gcloud scheduler jobs pause calibration-resolve-hourly  --location us-central1
gcloud scheduler jobs pause calibration-snapshot-daily  --location us-central1

gcloud run services update calibration-runner --region us-central1 \
  --update-env-vars=CALIBRATION_ENABLED=false
```

Either is sufficient on its own. Both leave the data intact. To remove the
service entirely, `gcloud run services delete calibration-runner` — the Cloud
SQL instance survives, so no measurements are lost.

Full stopping procedure, including what stopping does **not** undo:
`docs/OPERATOR_RUNBOOK.md` §1.
