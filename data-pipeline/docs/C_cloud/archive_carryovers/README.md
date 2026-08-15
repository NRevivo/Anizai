# C_cloud/archive_carryovers/

Retired Domain-C cluster carry-overs land here — the closed counterpart to the
current carry-over at `C_cloud/` root, mirroring how `archive_plans/` closes out
`plans/`.

**A carry-over is a point-in-time operational record**: what was deployed, at
what replica counts, with which image digests, at the moment a cluster window
was torn down. It is true when written and goes stale the next time anyone
touches the cluster. That is normal and is not a defect in the file.

## Why location carries the signal

The live carry-over stays at `C_cloud/` root with every other current Domain-C
doc. Retired ones move here. **The folder tells you which you are holding before
you open it** — a reader cannot mistake a dead record for a live one by
skim-reading a filename, which is the specific failure this split prevents.

A carry-over becomes archive-eligible when a later cloud window supersedes its
resting-state and image tables. Moving it is the same operation each time: `git
mv` into this folder, add the historical banner at the top, leave the body
untouched, add a row below.

## Index

| File | Window | Project described | Status |
|---|---|---|---|
| `carryover-20260801.md` | 2026-08-01, window 1 (V0/V2/V3/V5) | `anizai-pipeline` — **retired** | ⛔ Historical |
| `carryover-20260801-window2.md` | 2026-08-01, window 2 (V4/V6) | `anizai-pipeline` — **retired** | ⛔ Historical. Supersedes window 1 |

Both were archived 2026-08-15, at the close of the `anizai-pipeline` →
`anizai-pipehub` migration. Read cold they hand out wrong project IDs, a wrong
GCS bucket, wrong image tags and a wrong replica state — each carries a banner
saying so.

## Current, not here

| You want | Read |
|---|---|
| Identity facts — project, cluster, GSAs/KSAs, secrets | `../cloud_constants.md` |
| The live project's carry-over | `../carryover-20260815-migration.md` |
| Live cluster state | `../cloud_state.md` |
| Bring-up / teardown order and gates | `../../guides/bringup_profiles.md` |
| Runbook commands and triage | `../../guides/cluster_operations_guide.md` |
