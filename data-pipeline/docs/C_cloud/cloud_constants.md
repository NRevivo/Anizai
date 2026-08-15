# cloud_constants.md — Domain C identity facts

> Domain: C — Cloud
> Type: Constants (facts only — no procedures, no rationale)
> Created: 2026-08-15, at the close of the `anizai-pipeline` → `anizai-pipehub`
> migration
> Source of truth for: project IDs, cluster identity, service accounts, secret
> names, and the two-Google-identity split

## Why this file exists

These facts previously lived only in `.claude/skills/gcp-deployment/SKILL.md`,
which is **git-ignored** (root `.gitignore:10` → `/.claude`). A fresh clone did
not receive them, and during the migration the same project ID had to be
corrected in three separate places that had silently drifted apart.

This file is tracked. The skill points here rather than restating.

**This file carries facts, not instructions.** It deliberately contains no
bring-up order, no gates, no troubleshooting, and no reasoning. Those are owned
elsewhere, and duplicating them here would recreate the drift this file exists
to prevent:

| You want | Read |
|---|---|
| Bring-up / teardown order and gates | `docs/guides/bringup_profiles.md` |
| Runbook commands, triage, Flink job submission | `docs/guides/cluster_operations_guide.md` |
| Live cluster state | `docs/C_cloud/cloud_state.md` |
| Why any of this is the way it is | `docs/C_cloud/carryover-20260815-migration.md` |

---

## 1. Project and cluster

| Item | Value |
|---|---|
| GCP project | `anizai-pipehub` |
| GKE cluster | `anizai-cluster` |
| Zone | `us-central1-a` (zonal, not regional) |
| Node pool | `main-pool`, `e2-standard-8` |
| Kubernetes namespace | `anizai` |
| Workload Identity pool | `anizai-pipehub.svc.id.goog` |
| Artifact Registry | `us-central1-docker.pkg.dev/anizai-pipehub/anizai-images` |
| GCS backup bucket | `gs://anizai-pipehub-backups` |
| Billing account | `010C82-6CA2C4-183381` (ILS) |
| Cross-project Firestore | `anizai-ai` — the only multi-project hop |

**There is one node pool.** A second pool named `polymarket-pool` existed until
Phase 9.5-A and is gone. Do not re-create it.

**The old project `anizai-pipeline` is dead** (expired trial). In
`infrastructure/`, a reference to it outside the two classes in §5 is a bug —
and §5's check proves that tree is clean.

**`docs/` is a different matter and is not clean.** The operational guides,
`cloud_state.md` and `cloud_overview.md` still name the dead project in ~48
places, some of them inside runnable `gcloud` commands. Tracked as **KG-C-11 /
KG-C-12 / KG-C-13** in `cloud_sprints.md §4`. Do not read a `gcloud` invocation
out of those guides without checking its `--project=` flag against §1 above.

---

## 2. Two Google identities — one per project

| Account | Holds |
|---|---|
| `kingron79@gmail.com` | `roles/owner` on **`anizai-pipehub`**. No access whatsoever to `anizai-ai` |
| `ron.mintz21@gmail.com` | `roles/owner` on **`anizai-ai`**. Required for any `anizai-ai` IAM work |

For `anizai-ai` work, pass `--account=ron.mintz21@gmail.com` **as a
per-invocation flag**. Do not `gcloud config set account` — every subsequent
`anizai-pipehub` command would then run as the wrong identity.

Under `kingron79@`, an `anizai-ai` IAM read returns a **permission error**, not
an empty result. These are not the same and must not be read as the same.

`noam.revivo.1@gmail.com` (frontend / BFF) is also an owner of `anizai-ai`.

---

## 3. Service accounts

### Google service accounts (3)

| GSA | Purpose |
|---|---|
| `pipeline-runtime@anizai-pipehub.iam.gserviceaccount.com` | Every workload except the agent |
| `agent-worker@anizai-pipehub.iam.gserviceaccount.com` | The agent only |
| `scheduler-scaler@anizai-pipehub.iam.gserviceaccount.com` | Cloud Scheduler node-pool resize |

### Kubernetes service accounts (2) — the mapping is asymmetric

| KSA | GSA |
|---|---|
| `pipeline-runtime` | `pipeline-runtime@…` |
| **`agent-worker-ksa`** | **`agent-worker@…`** |

The agent's KSA carries a `-ksa` suffix; its GSA does not. This is **not a
typo**. `k8s/agent-deployment.yaml` is the authority — it declares
`serviceAccountName: agent-worker-ksa`. Three documents once stated
`agent-worker` and all three were wrong. A KSA named `agent-worker` leaves the
agent pod unschedulable.

The Workload Identity binding is the one command where both forms appear
together: the member is `…svc.id.goog[anizai/agent-worker-ksa]`, the target GSA
is `agent-worker@…`.

Both KSAs are committed: `k8s/pipeline-runtime-ksa.yaml`,
`k8s/agent-worker-ksa.yaml`.

---

## 4. Secrets

**Three different numbers, all correct:**

| Number | Meaning |
|---|---|
| **17** | Entries in the `03_migrate_secrets.sh` allowlist |
| **14** | Created from `.env` on a clean run |
| **15** | Mounted by a SecretProviderClass |

`THE_NEWS_API_KEY`, `POLYMARKET_API_KEY` and `POLYMARKET_API_SECRET` report
`absent or empty`. That is correct behaviour, not a fault.
`TELEGRAM_SESSION_FILE` is binary and uploaded separately with `--data-file=`;
the script never creates it.

### The 15 mounted secrets

`AIRFLOW_ADMIN_PASSWORD` · `AIRFLOW_FERNET_KEY` · `AIRFLOW_POSTGRES_PASSWORD` ·
`FRED_API_KEY` · `GMAIL_APP_PASSWORD` · `GRAFANA_ADMIN_PASSWORD` ·
`NEWSAI_API_KEY` · `OPENAI_API_KEY` · `OPENSKY_CLIENT_ID` ·
`OPENSKY_CLIENT_SECRET` · `OPENWEATHER_API_KEY` · `POSTGRES_PASSWORD` ·
`TELEGRAM_API_HASH` · `TELEGRAM_API_ID` · `TELEGRAM_SESSION_FILE`

**IAM is per-secret, not project-wide:** `pipeline-runtime` on all 15,
`agent-worker` on exactly `OPENAI_API_KEY` and `POSTGRES_PASSWORD`.

**Re-verifying the 15 needs two directories, not one.** The obvious glob
`k8s/*secretproviderclass*.yaml` returns only **12** — the three Telegram
secrets (`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_FILE`) are
declared in `k8s/producers/telegram-secretproviderclass.yaml`. A check that
misses `k8s/producers/` yields 12 and makes this file look wrong. Count across
both:

```
grep -rho "secrets/[A-Z_]*/versions" data-pipeline/infrastructure/ | sort -u | wc -l
```

### CSI driver

The cluster uses the **GKE-native** Secret Manager add-on, enabled with:

```
gcloud container clusters update anizai-cluster \
  --zone=us-central1-a --project=anizai-pipehub --enable-secret-manager
```

Every SecretProviderClass declares `provider: gke`, and every CSI volume
declares `driver: secrets-store-gke.csi.k8s.io`. The upstream community driver
`secrets-store.csi.k8s.io` is **not installed and must not be**. A pod list
filtered on the upstream label `k8s-app=secrets-store-csi-driver` returns
nothing on a healthy cluster — that emptiness is expected, not a failure.

---

## 5. Strings containing `anizai-pipeline` that are NOT project references

Two classes survive by design. Neither is a leftover.

| Class | What | Why untouched |
|---|---|---|
| **C** | `app.kubernetes.io/part-of: anizai-pipeline` — 42 label lines | A Kubernetes label whose value coincidentally equals the old project ID |
| **D** | Grafana `folderUid`, dashboard `uid: anizai-pipeline-v1`, the drilldown `url: /d/anizai-pipeline-v1`, `anizai-pipeline-health-v1` — 8 sites | Opaque Grafana keys. Changing a `uid` breaks the provisioning link and 404s the drilldown for zero benefit |

If a class-D `uid` is ever changed, the matching `/d/<uid>` URL must move with
it or the link breaks.

**Completeness check — and exactly what it proves.** `anizai-pipehub` was chosen
deliberately so that it does not contain the substring `anizai-pipeline`:

```
grep -rn "anizai-pipeline" data-pipeline/infrastructure/
```

should return only class-C and class-D lines — 42 and 8 respectively, 50 total.
Anything else is an unfinished replacement.

**This proves `infrastructure/` only.** It says nothing about `docs/`, which
still carries stale references (KG-C-11 / KG-C-12 / KG-C-13 — see §1). Widening
the same grep to the repo root returns many more hits, and most are legitimate:
historical records (`cloud_archive.md`, the archived carry-overs,
`docs/old_docs/`, `docs/backend-specs/`) are correct as written, and
`calibration/**` is Domain D, which genuinely provisions in `anizai-pipeline`.
A repo-wide zero is therefore **not** the target and never will be. Scope the
grep, or read the KG rows.

---

## 6. Scheduled jobs

| Cloud Scheduler job | Location | Resting state |
|---|---|---|
| `scale-up-main-pool` | `us-central1` | **PAUSED** |
| `scale-down-main-pool` | `us-central1` | **PAUSED** |

Both are paused. Node-pool resizing is manual.

---

## 7. Named workloads

**Kafka:** 19 topics.

**Flink jobs** — two, and both must be resubmitted on any code change; a pod
restart does not reload code:

- `anizai-silver-polymarket`
- `anizai-gold-all-sources`

**Never set `LOG_INFO_SAMPLE_RATE=1.0` on a Flink workload.** It causes
direct-buffer exhaustion and TaskManager OOMKills (KG-A-10 / KG-A-17). It has
never been set on the agent Deployment either — treat that as untested, not as
safe.

---

## 8. Identify the agent by digest, not by version

The deployed agent **misreports its own version**. `/health` returns
`0.5.0-sprint26+35c343b` while the running image is tagged `0.6.0-trackA`
(digest `sha256:937dfed1…471d9aee`). The build moved the tag and not the
internal stamp.

Only the git sha distinguishes it from the genuine Sprint-26 image, which
reported `0.5.0-sprint26+55e8093`.

**Identify the deployed agent by image digest.** `AGENT_VERSION` is not
reliable and will mislead anyone reading it cold.
