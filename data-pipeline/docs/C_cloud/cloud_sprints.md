# cloud_sprints.md
> Domain: C — Cloud
> Type: Sprints
> Last updated: 2026-07-23 (§2 backlog — Stage 1 landed; §1 / Rationale / §3 / §4 as of 2026-06-15)
> TL;DR: Phase 9/9.5 are closed — there are no open cloud implementation sprints. What is
> open is *deployment work*: what must reach GKE as hub Sprints 24–27 close, plus deferred
> items and cloud Known Gaps. Open this to see the cloud deployment backlog.

## Navigation
- §1 — Status Summary — every cloud phase, status, closed date, key outcome, plan file
- §Rationale — Deployment Model / Rationale — the two-track (pipeline / agent) decoupling model
- §2 — Open Deployment Work — the cloud deployment backlog (the actions hub sprints trigger in GKE)
- §3 — Deferred Items — consciously dropped cloud scope + condition to revisit
- §4 — Known Gaps — the **canonical** KG-C-* table (impact + workaround + condition-to-address)

---

## §1 — Status Summary

| Phase | Status | Closed date | Plan file | Key outcome |
|---|---|---|---|---|
| Phase 9 — Cloud Deployment (9A–9E) | Closed | 2026-05-10 | — (record in `cloud_archive.md`) | Full docker-compose stack ported to GKE; cross-project Firestore; Phase C E2E passed. → `cloud_archive.md` |
| Phase 9 Follow-up — Flink K8s HA | Closed | 2026-05-19 | — (record in `cloud_archive.md`) | `high-availability.type: kubernetes` + ConfigMap leader election; job graphs survive JM restart. → `cloud_archive.md` |
| Phase 9.5 Stage A — Infrastructure robustness | Closed | 2026-05-19 | — (record in `cloud_archive.md`) | Root-caused the May 11–18 silence (Kafka `log.dirs` on ephemeral `/tmp`); durable PVC subdir + hourly kafka-init CronJob; polymarket-pool deleted; scale 0→1 proven robust. → `cloud_archive.md` |
| Phase 9.5 Stage B — Application robustness | Closed | 2026-05-20 | — (record in `cloud_archive.md`) | Gold DB-insert retry; centralized OpenAI client factory (`max_retries=5`); Polymarket comments flag; producer raise-on-0%; 4 images → `*-p95`. → `cloud_archive.md` |
| Phase 9.5 Stage C — Monitoring + ops docs | Closed | 2026-05-20 | — (record in `cloud_archive.md`) | kafka/postgres exporters; Alertmanager + Gmail SMTP; 13 alert rules; `Anizai Pipeline Health` dashboard; `cluster_operations_guide.md`. → `cloud_archive.md` |
| Cloud Project Migration — `anizai-pipeline` → `anizai-pipehub` | **Open (active)** | — | `plans/cloud_project_migration.md` | Re-platform to a new GCP project with empty vaults, left switched off. Repo prep (R1–R9) then a gated cloud rebuild (S0–S7). **Plan is not version-controlled** — the tracker file points to `Claude-anizai-docs/cloud_migration/migration_plan.md`, which is authoritative |

> **Plan file** column: every Phase 9 / 9.5 row is closed and points to its
> `cloud_archive.md` record (`—`, no live plan file). The migration row is the **one open
> sprint** and points into `plans/`.

**There are no open cloud implementation *phases*.** The cluster is built and hardened;
what is open is the project migration above plus the *deployment backlog* below — moving
already-written hub code onto GKE.

---

## Deployment Model / Rationale

How Domain-C deployment work is sequenced. This is **descriptive** — it records the model
Ron decided on (2026-06-27); it does **not** pick a path or set dates. Source of record:
`cabinet-outputs/advisor/problem-reports/initial-test-readiness-and-24-27-sequencing-2026-06-27.md`
§6 / §9.

The cloud has **two largely-independent tracks**:

- **Track 1 — pipeline (A) cloud run.** The primary near-term cloud event: run Domain A in
  cloud to baseline its running cost. It needs **no** `anizai-agent` rebuild and is **not**
  gated on any hub sprint (24–27). Its real gatekeeper is a cost/infra item — KG-C-1 (OpenAI
  RPD cap) — plus Phase 7B.5 filter calibration and Ron's run-readiness ops actions, none of
  which are Domain-B sprints.
- **Track 2 — agent (B) cloud presence.** The deployed agent is **Sprint-21-era**
  (`AGENT_VERSION 0.4.0-sprint21-clarification-tier2`). Updating it has **two options**, and
  this doc records both without choosing:
  - **(a) Minimal rebuild** — an `anizai-agent` image carrying **Sprint 22 + 23.5 only**
    (BI-card wiring + the per-query cost layer), for opportunistic observation of real
    forecast output + cost on the data Track 1 accumulates. Does **not** need Sprints 24/25/26.
  - **(b) Full rebuild** — the full cumulative image **through Sprint 26** (agent metrics,
    latency analysis, cost reconciliation, retry), which lands when the B track reaches its
    own initial-test readiness — a separate, later event.

The two tracks are **explicitly decoupled**: the pipeline cloud run does not wait on the
agent rebuild, and the agent rebuild (whichever option) does not gate the pipeline run.
The **backlog items** in §2 and the **local-only facts** in `cloud_state.md §4` are
unchanged by this framing — only the *sequencing / gating story* is. Either Track-2 option
still lands the cumulative agent rebuild + the `reactive_triggers_log` DDL bundled when the
chosen agent scope closes (see §2).

---

## §2 — Open Deployment Work

Derived from `B_hub/hub_sprints.md` (Sprints 24–27 + the initial test). Domain C does not
own this hub code — it owns the cloud actions each hub sprint triggers. The common thread:
nearly every item folds into **one cumulative `anizai-agent` image rebuild** delivered when
the chosen agent scope closes (the Track-2 options — see the Rationale section); only a few
items add new manifests, DDL, or Firestore-side work. The backlog *items* below are
unchanged by the decoupling model — only *when/why* they deploy is (Rationale).

> **✅ Stage 1 landed 2026-07-23 (B-deploy, Track-2 full through-26).** The cumulative
> `anizai-agent` rebuild + redeploy, the `agentVersion` build-stamp, and the
> `reactive_triggers_log` DDL are **done**; the partner-side Firestore index + rules are
> **deployed** on `anizai-ai`. What remains is **Stage 2** (post-A-window): the
> `anizai-airflow` rebuild + scaling the agent from `replicas:0` to 1 to begin the B test.
> Per-row status below. *(Status record only — the KG-C-\* gap entries in §4 are unchanged;
> Ron maintains the gap counts.)*

| Cloud action | Triggered by (hub) | What it is |
|---|---|---|
| **`anizai-agent` image rebuild + redeploy** | Sprints 22, 23, 24, 25, 26 (cumulative) | ✅ **LANDED (B-deploy Stage-1 T1.2, 2026-07-23): `anizai-agent:0.5.0-sprint26` (+55e8093) at `replicas:0`.** The single biggest item. Carries Sprint 22 BI-card wiring, Sprint 23 `trigger_reactive_ingestion` node, Sprint 24 `agent/followup/` subgraph + second Firestore listener, Sprint 25 `generate_suggested_actions` + `agentEvents`, Sprint 26 hardening. Landing scope is a Track-2 choice (see Rationale): a minimal **22 + 23.5** rebuild for opportunistic observation, or the full bundle **through Sprint 26** at B-track readiness. |
| **`anizai-airflow` image rebuild** | Sprint 23 (T23.1) | ⏳ **PENDING — Stage 2 T2.1 (post-A-window).** NewsAPI `run_reactive()` lives in the producer/DAG path baked into the airflow image; the reactive trigger cycle needs it on the cluster, not just in the agent. |
| **Apply `reactive_triggers_log` DDL to cloud Postgres** | Sprint 23 | ✅ **DONE / VERIFIED PRESENT (B-deploy T1.3, 2026-07-23)** — table exists in cloud Postgres (applied bundled with 7B.5-I T7); no re-apply needed. Manual one-time DDL (§7 of `infrastructure/sql/init.sql`) — `init.sql` does not re-run on an existing PVC. Must precede any initial-test run that exercises Sprint 23. |
| **Prometheus alert rules + scrape (real agent metrics)** | Sprint 26 (T26.4) | ⏳ **Image now emits real `agent_*` metrics (0.5.0-sprint26), but the agent is at `replicas:0` so nothing is scraped yet; alert rules still to add.** The `agent-worker:8000/metrics` scrape job already exists, but today it scrapes a Sprint-18 stub (zero `agent_*` metrics). When Sprint 26 emits real node-duration / LLM-cost / queue metrics, add corresponding alert rules to `prometheus-rules-configmap.yaml`. |
| **`agentVersion` build-stamp** | Sprint 26 (T26.5) | ✅ **DONE (B-deploy Stage-1 T1.1, 2026-07-23):** `Dockerfile.agent` gained `ARG`/`ENV AGENT_GIT_COMMIT_SHORT_SHA` (commit `55e8093`); the deployed image stamps `AGENT_VERSION=0.5.0-sprint26+55e8093`. |
| **Resume Cloud Scheduler** | Initial test (~2 days) | The 2-day cloud run needs the daily scale cycle live. Both jobs are PAUSED; **Ron resumes manually** (readiness checklist: `cluster_operations_guide.md` §4). |
| **Firestore security rules for new subcollections** | Sprints 24–25 | ✅ **DEPLOYED to `anizai-ai` (partner-confirmed 2026-07-23, B-deploy T1.4).** `messages` (follow-up flow) and `agentEvents` (chain-of-thought stream) writes may need rule changes on `anizai-ai`. **Partner-side / Firestore project** — flagged here as a cross-boundary dependency, not a GKE manifest change. See `frontend-integration` skill. |
| **Deploy collection-group index for the follow-up `messages` listener** | Sprint 24 (24.1) | ✅ **DEPLOYED to `anizai-ai` (partner-confirmed 2026-07-23, B-deploy T1.4).** The follow-up listener runs a **collection-group** query on `messages` filtered by `role`/`status`. It needs a collection-group index / field-scope config on the `anizai-ai` Firestore project — implicit on the emulator (so it passes locally), but it **must be explicitly deployed to production before any initial-test run that exercises follow-ups**, or the query silently returns nothing. Distinct from the security-rules row above and coordinated alongside it: rules govern *access*, this governs *query execution*. **Partner-side / Firestore project** (`anizai-ai`). |

**Sequencing (two decoupled tracks — see the Rationale section).** There is **no**
single "nothing deploys until Sprint 26 / everything at once" gate. **Track 1** — the
pipeline (A) cloud run — is the near-term cloud event and is **independent** of hub Sprints
24–27 (it needs no agent rebuild). **Track 2** — the agent (B) cloud update — is the
cumulative `anizai-agent` rebuild above, landing either as a **minimal 22 + 23.5** image
(opportunistic observation during the Track-1 run) or as the **full through-26** bundle when
the B track reaches its own initial-test readiness. Within the hub, the dev path remains
24 → 25 → 26 → (B-centric) test → 27, but that path runs **in parallel with** the Track-1
pipeline run rather than gating it. Sprint 27 (post-test polish) still closes into a final
agent rebuild after the B-centric test. (Source: sequencing report §6 / §9 — see Rationale.)

---

## §3 — Deferred Items

Cloud scope consciously left out of V1. Pipeline/hub deferrals that have a cloud
footprint are included; pure code deferrals stay in `A_pipeline` / `B_hub`.

| Item | Deferred from | Reason | Condition to revisit |
|---|---|---|---|
| Multi-zone HA / multi-node pools / autoscaling (HPA/VPA) | Phase 9 | Single-zone single-node well under capacity (~12 pods on e2-standard-8); Spot would kill in-flight Flink checkpoints | Any pod OOMKilled, or sustained CPU > 70%, or a real second user |
| Public ingress / DNS / TLS / OAuth | Phase 9 | Laptop is the only client; everything via `kubectl port-forward` | A non-laptop client (hosted frontend) needs cluster access |
| Helm / Argo CD / GitOps | Phase 9 | Plain `kubectl apply`; manifests simple enough that a Helm layer is net cost | Manifest count or environments grow enough to justify it |
| Per-workload least-privilege GSAs | Phase 9 | V1 uses one shared `pipeline-runtime` GSA + one `agent-worker` GSA | A secret leak, or a compliance requirement |
| Cloud SQL (managed Postgres) | Phase 9 (9B) | Cloud SQL lacks the TimescaleDB extension the `momentum_vault` hypertable needs | Only if/when Cloud SQL adds TimescaleDB |
| Reactive Search Microservice (Tavily/Brave + `reactive_article_cache` table) | Hub Sprints 22–23 (spec §8.12) | OpenAI cost concerns + NewsAPI producer-trigger covers most of the need; would add a new cloud service + a new Postgres table | After cost picture stabilizes AND producer-trigger coverage proves insufficient (hub FE 1) |
| Cross-user cache + delta refresh (spec §8.7.3/§8.7.4) | Hub Phase 8 V1 | Low repeat-question rate over a 2-day / 3–4 user test; cache would obscure real cost numbers | After the initial test produces enough sessions (hub FE 3) |
| Automated restore testing in CI | Phase 9 (9E) | Restore tested once manually (9E + re-verified 9.5-A F4) | Post-V1 hardening |

---

## §4 — Known Gaps

**Canonical cloud Known-Gaps table (KG-C-\*).** This is the single source of truth for the
cloud-infra gaps — `cloud_state.md §5` points here. The `Origin` column preserves the legacy
ID. Producer-**code** gaps (OpenSky producer logic, GoogleTrends/pytrends 404, Polymarket
`/comments`) are **Domain A** — referenced at the bottom, not re-owned here.

| ID | Origin | Description | Impact | Workaround / Priority | Raised in | Condition to address |
|---|---|---|---|---|---|---|
| KG-C-1 | KG-PHASE-9.5-1 | OpenAI Tier 1 RPD cap (10k/day) can exhaust during Silver→Gold backlog processing | Agent queries + Gold embeddings halt until midnight UTC | Drop backlog before catch-up (ops guide §7); upgrade to Tier 2+; separate Gold/agent budgets. **High** (gates initial-test cost work). Supersedes KG-PHASE-C-5 (Gold 429) with Stage B Item 2 | Phase 9.5-A backlog processing | Before/at the initial test — needs Tier upgrade or split budgets; gates the cost-measurement goal |
| KG-C-1a | (new — 2026-08-15) | **RPD load measured for the first time, and the HackerNews cadence cut in response.** During the continuous bring-up, per-run OpenAI cost was measured against real schedules: hackernews **47.3 calls/run**, newsapi **~27/run**, arxiv **~0.95 calls per Bronze record**. `fred`, `openweather` and `polymarket` cost **zero** — they take the `structured_metrics` path with no enrichment, which is why the highest-volume source (polymarket, 1,235 records/hour) is free. At `*/20`, hackernews alone was **~3,406 calls/day**, the single largest consumer. Total projected **~6–7k/day — measured on a SATURDAY**, i.e. at the weekly minimum; a weekday at 1.5–2× reaches **9–14k** and exhausts the 10k Tier-1 ceiling, which blocks the agent's own forecasts as well as Gold | Ceiling exhaustion blocks the operator's forecasts — the exact capability a collection run exists to enable | ✅ **Lever applied 2026-08-15**: `hackernews_high_frequency` `*/20 * * * *` → `0 * * * *` (72 → 24 runs/day), removing ~2,270 calls/day for the least signal loss, since HN front-page turnover is slower than 20 minutes. Revised projection **~4.6k/day Saturday basis, ~6.3–8.9k weekday**. ⚠️ **The change was applied BOTH to the repo DAG and as a LIVE IN-CONTAINER PATCH, because DAG files are baked into the `anizai-airflow` image (the scheduler mounts only `airflow-secrets`, no DAG volume) — so the repo edit alone has no effect until a rebuild.** The live patch is **EPHEMERAL: any `airflow-scheduler` pod restart silently reverts the cadence to `*/20`** and the RPD projection with it. Detect with `airflow dags details hackernews_high_frequency \| grep schedule_interval` — expect `0 * * * *`. Fold the durable fix into the next `anizai-airflow` rebuild (pairs with KG-C-5's rename). **High** | 2026-08-15 continuous bring-up | Re-measure the call rate on a weekday; flag if it crosses ~8,000/day. Durable fix at the next image rebuild |
| KG-C-2 | KG-PHASE-9.5-6 | GKE maintenance window not set; `autoUpgrade`/`autoRepair` can fire anytime | Unplanned node upgrade evicts pods mid-window | Set a low-traffic maintenance window. **Medium** | Phase 9.5-A Area 10 | Set a low-traffic window before resuming Cloud Scheduler |
| KG-C-3 | KG-PHASE-9.5-7 | `imagePullPolicy: Always` + `:tag` (not digest) on Anizai images | Silent runtime drift if a tag is re-pushed | Switch to `@sha256:` digest pinning. **Low** | Phase 9.5-A Area 9 | Defensive; pair with the next image rebuild |
| KG-C-4 | KG-PHASE-9.5-8 | Flink Python code changes require job cancel + re-submit after image rollout (HA recovers the OLD compiled BLOB) | Image rebuild without job restart = code change has no effect | Documented procedure in `cluster_operations_guide.md` §6. Process gap, not code gap. **Low** | Phase 9.5-B execution | Operationally documented; revisit only if a 3rd party automates Flink deploys |
| KG-C-5 | KG-PHASE-9.5-2 | Secret `NEWSAI_API_KEY` holds a thenewsapi.com key (name suggests the deprecated newsapi.ai provider) | Future-bug hazard: an operator could rotate in a wrong-provider key | Coordinated rename to `THE_NEWS_API_KEY` across Secret Manager + code + manifests + scripts. **Medium** | Phase 9.5-A Area 8 | Coordinated rename — fold into the `anizai-airflow` rebuild for Sprint 23 |
| KG-C-6 | KG-PHASE-9.5-3 | `guides/CLOUD_CONNECTION_GUIDE.md` accuracy drift. **Original description (13 lowercase-with-dashes secret-name references + an outdated Scheduler schedule `08:00/18:00 IL` vs the real `05:00/15:00 IL`) was itself stale** — a 2026-07-26 read found neither defect in the file; both had been corrected at some point and the gap was carried unverified for months. The file did contain a different and worse set of errors: `polymarket-pool` documented as a live second node pool (deleted in 9.5-A), "Flink jobs must be re-submitted after a scale-up" (false since K8s HA, and duplicate-submission-inducing — contradicts KG-C-4), wrong Artifact Registry repo + image names, five non-existent agent metrics, 15-vs-19 topics, `anizai-gold-polymarket` vs `anizai-gold-all-sources`, four-vs-five BI cards, and a wrong Firestore collection layout | An operator following it verbatim could submit duplicate Flink jobs, pull from a non-existent registry path, or wait for a node pool that no longer exists | — | Pre-Phase-9.5 brief | ✅ **CLOSED 2026-07-26** — full accuracy sweep of the file: all of the above corrected against the manifests, `agent/metrics.py`, `cluster_operations_guide.md` and `cloud_state.md`; scope header added routing bring-up to `guides/bringup_profiles.md`; KG-C-3 / KG-C-5 / KG-C-10 / KG-B-21 cross-references added at the points where they bite. Retained one cycle, then move to `cloud_archive.md`. **Lesson recorded:** a "do not touch" note on a doc gap stopped anyone re-reading the file, so the gap outlived the defects it described — re-verify a doc KG against the doc before deferring it again. |
| KG-C-7 | KG-PHASE-C-6 (infra slice) | GKE egress cannot reach `opensky-network.org:443` (`ConnectTimeoutError`) | OpenSky produces 0 Bronze from cloud | GCP firewall rule / IP-range investigation. The producer-code silent-success was mitigated app-side in Stage B (Domain A). **Medium** | Phase 9 (9D) closeout | GCP firewall/IP investigation before relying on OpenSky in cloud |
| KG-C-8 | KG-PHASE-C-1 | docker-compose `kafka-ui:latest` not pinned to `:v0.7.2` (cloud manifest is pinned) | Dev/cloud parity drift only | Tighten the compose tag. **Low** | Phase 9 (9B) | Compose parity cleanup |
| KG-C-9 | Phase 9.5-A Area 11 | `postgres-backup` CronJob not robust to scale-down — two daily backups missed (2026-05-16/17) when main-pool was at 0 (`startingDeadlineSeconds` exceeded) | Backup gap during off-hours | Tune `startingDeadlineSeconds` + add missed-run monitoring. **Medium** | Phase 9.5-A | Tune `startingDeadlineSeconds` + missed-run alert before long unattended windows |
| KG-C-10 | (new — 2026-07-26) | **Live replicas have drifted below the committed manifests, in the direction that starts spend.** `flink-jobmanager`, `flink-taskmanager`, `telegram` and `polymarket` all sit at `replicas: 0` in the cluster while their manifests in `infrastructure/k8s/` still declare `replicas: 1`. `agent-worker` is the inverse and is *intentional* (declared 0 since the Stage-1 deploy). The four were confirmed already at 0 when the 2026-07-26 agent-only run began — the drift predates that session | A routine `kubectl apply -f` of any of those four files restarts Flink and/or continuous ingestion with no scale command issued and no obvious cause: OpenAI enrichment spend, shared RPD burn (KG-C-1), and a Kafka backlog replay all begin silently. In the other direction, an operator reading the repo will believe ingestion is running when it is not | Decide per workload which value is authoritative, then make the manifest say it — either commit `replicas: 0` where the hold is deliberate, or scale live back to 1. Until then: read desired replicas from the cluster, never from the repo, and prefer `kubectl scale` over `apply` during a session (`guides/bringup_profiles.md` §5 trap 4). **Medium** — low effort, but the failure mode is silent spend | 2026-07-26 agent-only cloud run | At the next deliberate cluster restore — whoever decides to bring Flink and the continuous producers back up should reconcile git and live in the same pass, not after |
| KG-C-11 | (new — 2026-08-15) | **The two operational guides carry runnable `gcloud` commands against the dead project.** `guides/CLOUD_CONNECTION_GUIDE.md` (27 sites) and `guides/cluster_operations_guide.md` (15 sites) embed `--project=anizai-pipeline` inside copy-paste invocations, and also name the retired Artifact Registry path `us-central1-docker.pkg.dev/anizai-pipeline/anizai-images`, the retired bucket `gs://anizai-pipeline-backups`, and `gke_anizai-pipeline_us-central1-a_anizai-cluster` as *expected* `kubectl config current-context` output. The migration's R9 sweep deliberately scoped itself to `infrastructure/` and left `docs/**` alone, on the reasoning that most doc references are historical — correct in general, wrong for these two files, which are instructions rather than history | An operator copies a command mid-task and it fails against a deleted project, or — worse, once someone re-creates a same-named resource — succeeds somewhere unintended. The context-check line actively teaches the wrong expected output, so a correctly-configured operator reads their own healthy state as a fault | ✅ **CLOSED 2026-08-15.** All 42 identity strings re-pointed (27 + 15). **Two of the 15 in `cluster_operations_guide.md` were NOT project references and were deliberately left**: the `/d/anizai-pipeline-v1` and `/d/anizai-pipeline-health-v1` dashboard links are class-D Grafana `uid`s (`cloud_constants.md` §5) that must keep matching `grafana-configmap.yaml` lines 553/801 — a blanket sweep would have 404'd both drilldowns. A warning now sits beside them so the next sweeper does not "fix" them. Four sites where the rename alone does **not** make the instruction correct were flagged inline rather than silently swapped: a hardcoded `2026-05-10` restore path (that object never existed in the new bucket — replaced with a list-then-pick form), the registry's 10-tag inventory (`0.3.0-price` is gone for good), the billing-history break at 2026-08-14, and the absence of any two-Google-identity note in the auth section. **Scope limit recorded in both headers: identity strings only — no procedure was re-verified live** | Doc reorg, 2026-08-15. Pre-registered as `migration_plan.md` §9 item 5 | Done. The *procedure* accuracy of these guides (log paths, pod names, whether `kafka-get-offsets.sh` superseded the old tool) is a separate, still-open question — it needs a collection run, not a text pass |
| KG-C-12 | (new — 2026-08-15) | **`cloud_state.md` — the file every other Domain-C doc calls the live-state authority — describes the retired project.** §2's identity table gives project `anizai-pipeline`, registry `…/anizai-pipeline/anizai-images` and bucket `gs://anizai-pipeline-backups`. Independently of the project rename it also states **"16 secrets"**, where the verified count is **15** (`cloud_constants.md` §4, confirmed 2026-08-15 against all 10 SecretProviderClass files). Its §3 workload table is stamped "Phase 9.5 closeout (2026-05-20)" and predates both the 2026-08-01 windows and the migration, so the resting state it implies is two teardowns stale | Anyone routed here for "current state" — which `cloud_constants.md`, `cloud-principles` and `cloud_sprints.md` all do — gets the wrong project and a wrong secret count. The 16-vs-15 discrepancy is the more insidious half: it survived the whole migration unnoticed and would make a genuine secret-mount failure look like an expected shortfall | ⚠️ **PARTIAL 2026-08-15 — identity only.** §2's project / registry / bucket re-pointed and the secret count corrected 16 → 15 against `cloud_constants.md` §4. **§1, §3 and §6 were deliberately NOT touched** and a warning block now heads the file listing what is known-wrong in them (Flink tag, agent tag + its version-misreport, polymarket tag, both Flink JobIDs, four-vs-six workloads at rest). **What remains is a rewrite, not a sweep** — §1/§3/§6 predate two teardowns and the migration, and roughly 30 factual rows need re-sourcing. Two ways to finish it, and they are not equivalent: **(a)** re-base on `carryover-20260815-migration.md` now, stamped "as at 2026-08-15 teardown, not re-verified live" — doable at a desk, no cloud; **(b)** verify against the cluster at the next bring-up, which is the only thing that makes a *live-state* document honestly live. (a) is the recommended interim. **Still paired with un-fencing `project_master.md` §3** (git-ignored → machine-local, arrives by no `git pull`). **High** | Doc reorg, 2026-08-15. Pre-registered as `migration_plan.md` §9 item 4 | Option (a) any time; option (b) at the next bring-up. Do not let (a) close the gap — it changes the stamp, not the verification |
| KG-C-13 | (new — 2026-08-15) | `cloud_overview.md` names the dead project in 3 places — all ASCII architecture-diagram labels (§the project box, the GCS backup arrow, the supporting-services line) | Low. A reader building a mental model gets the wrong project name, but nothing here is executable and nothing is copy-pasted | ✅ **CLOSED 2026-08-15** — folded into the KG-C-11 pass; three labels swapped. The file's image-tag rows still predate the migration, but that is `cloud_overview.md`'s own staleness, not a project-reference gap; its header now says so. **Low** | Doc reorg, 2026-08-15 | Done |
| KG-C-14 | (new — 2026-08-15) | **`read_env_value()` does not strip surrounding whitespace or matched quotes, despite its header claiming otherwise — two of the 14 migrated secrets were written malformed.** `gcp/03_migrate_secrets.sh` copies `.env` values verbatim, so a line written `KEY= "value"` reaches Secret Manager as `<space>"value"<quote>`. Confirmed live 2026-08-15 by reading all 14 secrets and checking **shape only, never value**: `FRED_API_KEY` was **35 bytes** where a valid FRED key is 32 (leading space + two wrapping quotes), and `GRAFANA_ADMIN_PASSWORD` was **14 bytes** with a leading space. The other 12 were clean. **The R1 response was a single length check for `GMAIL_APP_PASSWORD` at Gate S1** — correct in itself (a Gmail App Password is exactly 16 chars) and it did fire — but it was applied to one secret rather than generalised, so the same defect passed unnoticed on two others | `FRED_API_KEY`: every FRED request returned `400 Bad Request`, so the producer collected **nothing** — and, because of KG-A-23, the DAG still reported success. `GRAFANA_ADMIN_PASSWORD`: admin login rejected, so dashboards were unreachable — which would have surfaced exactly when someone opened Grafana to check spend. **Second-order and worse: `fred_producer` logs the full request URL on error, so the malformed key (and therefore the real key) was written in plaintext to the Airflow task log and to Cloud Logging, and re-logged on every scheduled run** | Grafana: **fixed in place 2026-08-15** — trimmed, re-uploaded as version 2, proven by HTTP 200 against the trimmed value vs 401 against both the old space-prefixed value and a deliberately wrong one. FRED: **deliberately NOT trimmed** — a key written to two log sinks is burned, so it is being **rotated at FRED** and pushed clean; `fred_daily` re-paused meanwhile to stop further re-logging. Durable fix: make `read_env_value()` strip surrounding whitespace and matched quotes (the parser is the real defect), and have `03_migrate_secrets.sh` validate byte length against an expected shape wherever one is known — the generalised version of the check bolted on at S1. **High** | 2026-08-15 cloud bring-up (FRED 400s) | Parser fix before the next `.env`-sourced secret migration. Re-run the 14-secret shape sweep after any future migration — it is cheap and it is what caught this |
| KG-C-15 | (new — 2026-08-16) | **`polymarket`'s container memory limit was too low to survive a multi-day run, and it was OOMKilled at it.** `OOMKilled`, `exit=137` at `2026-08-16T02:29:16Z`, ~11h into `0.4.1-inactive`'s first multi-day run, against `limits: 512Mi` / `requests: 128Mi`. **The underlying cause is a producer-side memory leak and is owned by Domain A as KG-A-24** — this row covers only the cloud-side sizing and the operational bridge. The 128Mi request was additionally a fiction: the scheduler was placing the pod on roughly a third of its real steady-state footprint (373Mi measured) | The kill itself lost nothing — the restart completed inside the hour, that sweep still landed 1,224 rows, and 19 consecutive hourly sweeps held Bronze == `momentum_vault` at 23,276. The exposure is a kill landing *mid-sweep*, which drops a full hour of Polymarket that cannot be backfilled | ✅ **Raised 2026-08-16: `512Mi → 1Gi`, `requests 128Mi → 384Mi`.** Applied in the quiet part of the hour with the 10:30 sweep confirmed complete (1,223 emitted, 0 skipped, offset stable across two reads) rather than into the run-up to one; verified after: cgroup limit `1073741824`, baseline reset to 178Mi, startup sweep emitted 1,223, replicas still 1. **Mitigation only** — because the cause is a leak (KG-A-24), 1Gi moves time-to-kill from ~11h to ~28h rather than removing it, so a **deliberate restart cadence** is still needed for the rest of the run: restart right after a sweep completes, since a chosen restart costs nothing and a random mid-sweep one costs an hour. Drop the limit back to something sane once KG-A-24 is fixed, rather than leaving 1Gi as a permanent monument to a leak. ⚠️ **Do NOT apply the Flink OOM lesson here.** `bringup_profiles.md` §5 trap 3 says container memory *cannot* fix the Flink TaskManager OOM, because that is JVM direct-buffer exhaustion bounded by `taskmanager.memory.*` (`-XX:MaxDirectMemorySize`) rather than by cgroups — there the container reported `OOMKilled=false` with 750MiB of 7.6GiB in use, and 6Gi changed nothing. **This is the opposite case**: a pure-Python process hitting a genuine cgroup ceiling, `OOMKilled=true`, `exit=137`, where the container limit is exactly the lever. Same symptom, opposite fix; conflating them produced the 6Gi mistake. **Medium** | 2026-08-16, during the PolymarketBronzeStale investigation | Deliberate restarts through Thursday; revisit the limit once KG-A-24 lands |
| KG-C-16 | (new — 2026-08-16) | **Prometheus scrapes no container/kubelet metrics, so pod-level memory, CPU and OOMKills are invisible in Grafana.** The 5 active targets are all application endpoints — `agent-worker`, `flink-jobmanager`, `flink-taskmanager`, `kafka-exporter`, `postgres-exporter`. `container_memory_working_set_bytes`, `container_memory_usage_bytes` and `kube_pod_container_resource_limits` all return **ABSENT**; there is no cAdvisor/kubelet scrape job and no `kube-state-metrics` deployment | The KG-C-15 OOMKill was **invisible to our own monitoring**. No alert could fire on memory pressure, no dashboard panel shows a pod approaching its limit, and diagnosing it required going outside the stack entirely — the curve was recovered from **GKE's managed Cloud Monitoring** (`kubernetes.io/container/memory/used_bytes` via the Monitoring API), which collects independently of this Prometheus. Any future resource-exhaustion failure is equally invisible | Either add a kubelet/cAdvisor scrape job plus `kube-state-metrics` (gives `container_memory_working_set_bytes` and the limit series, so a "pod >80% of memory limit" alert becomes possible), or accept the split and document that pod-resource questions are answered in Cloud Monitoring rather than Grafana. **The second is cheaper and may be the right call** — GKE already collects it and the data was there when needed. What is NOT acceptable is the current state, where an operator reasonably assumes Grafana covers pod health. **Medium** | 2026-08-16, while investigating KG-C-15 | Before relying on Grafana as the single operational view; pair with any monitoring-stack change |

**Deliberately NOT in scope for KG-C-11/12/13 — verified 2026-08-15, do not "fix" these:**
`cloud_archive.md` (4 sites) and `docs/backend-specs/cluster-teardown-20260730.md` (1) are
**historical records and correct as written** — D1/D5 design decisions and a 2026-07-30
teardown gate genuinely happened in `anizai-pipeline`; rewriting them would falsify the
record. Same for `docs/old_docs/**` and `archive_carryovers/**`, the latter now carrying
explicit historical banners. `calibration/**` (7 sites) is **Domain D and cross-owner** —
`calibration-runner@anizai-pipeline` is the collaborator's identity, hosted in the dying
project; it is tracked as `migration_plan.md` §9 item 3b and must be handled *with* them,
before old-project deletion. Not a Domain C doc gap.

**Domain A producer-code gaps (referenced, not owned here):** GoogleTrends/pytrends 404
(KG-PHASE-9.5-5), Polymarket `/comments` breaking change (KG-PHASE-9.5-4), and the OpenSky
producer logic — all owned by Domain A; see `A_pipeline/pipeline_sprints.md`. Their
app-layer "raise on 0% success" mitigations landed in Phase 9.5 Stage B.
