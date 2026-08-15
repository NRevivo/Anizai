# cloud_project_migration.md

> Domain: C — Cloud
> Type: Plan (**pointer only** — this file tracks status, not content)
> Last updated: 2026-08-14
> TL;DR: Rebuild the Anizai cloud deployment in the new GCP project
> `anizai-pipehub`, with empty vaults, and leave it switched off. This file
> is the in-repo status tracker; the plan itself lives outside the repo.

## Scope

Re-platform Domain C from GCP project `anizai-pipeline` to `anizai-pipehub`.
No data migration, no image rebuild, no Known-Gap fixes. Repo preparation
(R1–R9) followed by a gated cloud rebuild (S0–S7), ending with the cluster
deployed, verified, and resting at zero nodes.

## Authoritative source

The sprint plan is **not** version-controlled and is **not** reproduced here:

| Document | Path | Owns |
|---|---|---|
| `migration_plan.md` (v2.1) | `Claude-anizai-docs/cloud_migration/migration_plan.md` | Everything: tasks, gates, decisions, stop conditions, image tags |
| `replacement_map.md` (v2.1) | `Claude-anizai-docs/cloud_migration/replacement_map.md` | The file-by-file `anizai-pipeline` occurrence map (host-path swap) |

**Do not copy task detail, rationale, or values into this file.** Two copies
of one plan drift, and repairing exactly that class of drift is what this
sprint exists to do. Read `migration_plan.md` for anything beyond a checkbox.

## Task status

**Stage 0 (Block A)** — project created, billing linked, APIs, Artifact
Registry, billing alerts. ✅ done 2026-08-12, outside this tracker.

### Stage 1 — repo preparation (one commit, no cloud)

| | Task | Status |
|---|---|---|
| T0 | This tracker + `cloud_sprints.md` row + `project_master.md` cursor | [x] |
| R1 | Secret allowlist 15 → 17 | [x] |
| R2 | Remove `polymarket-pool` from `04_create_cluster.sh` | [x] |
| R3 | Project-reference replacement (+ Alertmanager prefix, README) | [x] |
| R4 | Commit the two Workload Identity KSAs as YAML | [x] |
| R5 | Manifest inventory (Service placement + replicas) | [x] |
| R6 | Correct the four behind-live image tags | [x] |
| R7 | Regenerate `postgres-configmap.yaml` from `init.sql` | [x] |
| R8 | DAGs paused at creation on both Airflow manifests | [x] |
| R9 | Extend the project sweep to `.claude/` | [x] — **local-only, see note** |
| — | **Gate 1** | [x] |

> **R9 is not version-controlled — and it is not alone.** Three files are git-ignored,
> and they are exactly the ones that tell you how to work on this repo:
>
> | File | Ignored by | Carries |
> |---|---|---|
> | `.claude/` (all skills) | root `.gitignore:10` — `/.claude` | every skill, incl. the two R9 edited |
> | `CLAUDE.md` | root `.gitignore:2` | the operating contract |
> | `data-pipeline/project_master.md` | `data-pipeline/.gitignore:37` | the global next-action cursor |
>
> None ships with the repo, so the R9 edits to `gcp-deployment/SKILL.md` and
> `cloud-principles/SKILL.md` exist only on the machine that made them and will not
> arrive by `git pull`. **Any other machine running a Phase C session is still told
> `anizai-pipeline` by the very file it auto-loads before starting.** The T0 cursor
> update in `project_master.md` is local-only for the same reason.
>
> Ron's decision, deferred out of this sprint (migration plan §9 item 8) — changing a
> `.gitignore` mid-migration is exactly the kind of change that must not ride along.
> When it is taken, the fix is **narrow: un-ignore `.claude/skills/` specifically, NOT
> `/.claude` wholesale**, which would sweep in local settings and permissions.
>
> Until then, the S7 carry-over records R9's exact edits (§6) so a re-clone can redo
> them in minutes rather than silently reinstating the dead project.

### Stage 2 — cloud execution

| | Task | Status |
|---|---|---|
| S0 | Verify local images (P1/P2) — before S1 | [x] — 10/10 matched |
| S1 | Secrets | [x] · Gate S1 [x] |
| S2 | Images — nine tags (+1 optional) | [x] · Gate S2 [x] |
| S3 | Cluster and identity | [x] · Gate S3 [x] |
| S4 | Cross-project Firestore | [x] · Gate S4 [x] |
| S5 | Workloads, layered | [x] · Gates L1 [x] L2 [x] L3 [x] L4 [x] S5 [x] |
| S6 | Proof — one forecast | [x] · Gate S6 [x] |
| S7 | Switch off | [x] · Gate S7 [x] |

## Definition of done

**MET 2026-08-15.** Carry-over: `docs/C_cloud/carryover-20260815-migration.md`.

`migration_plan.md` §6 — Gate S6 passed, Gate S7 verified, and a carry-over
written to `docs/C_cloud/`. Closeout follows the `sprint-closeout` skill;
this file then moves to `archive_plans/`.
