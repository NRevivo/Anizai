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

> **Plan file** column: every Domain-C phase is closed, so each points to its
> `cloud_archive.md` record (`—`, no live plan file). There is **no open-sprint row** —
> Domain C has none. Future on-demand deployment plans would appear here with a `plans/`
> path once activated.

**There are no open cloud implementation sprints.** The cluster is built and hardened.
The open work below is the *deployment backlog* — moving already-written hub code onto GKE.

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
| KG-C-2 | KG-PHASE-9.5-6 | GKE maintenance window not set; `autoUpgrade`/`autoRepair` can fire anytime | Unplanned node upgrade evicts pods mid-window | Set a low-traffic maintenance window. **Medium** | Phase 9.5-A Area 10 | Set a low-traffic window before resuming Cloud Scheduler |
| KG-C-3 | KG-PHASE-9.5-7 | `imagePullPolicy: Always` + `:tag` (not digest) on Anizai images | Silent runtime drift if a tag is re-pushed | Switch to `@sha256:` digest pinning. **Low** | Phase 9.5-A Area 9 | Defensive; pair with the next image rebuild |
| KG-C-4 | KG-PHASE-9.5-8 | Flink Python code changes require job cancel + re-submit after image rollout (HA recovers the OLD compiled BLOB) | Image rebuild without job restart = code change has no effect | Documented procedure in `cluster_operations_guide.md` §6. Process gap, not code gap. **Low** | Phase 9.5-B execution | Operationally documented; revisit only if a 3rd party automates Flink deploys |
| KG-C-5 | KG-PHASE-9.5-2 | Secret `NEWSAI_API_KEY` holds a thenewsapi.com key (name suggests the deprecated newsapi.ai provider) | Future-bug hazard: an operator could rotate in a wrong-provider key | Coordinated rename to `THE_NEWS_API_KEY` across Secret Manager + code + manifests + scripts. **Medium** | Phase 9.5-A Area 8 | Coordinated rename — fold into the `anizai-airflow` rebuild for Sprint 23 |
| KG-C-6 | KG-PHASE-9.5-3 | `guides/CLOUD_CONNECTION_GUIDE.md` accuracy drift. **Original description (13 lowercase-with-dashes secret-name references + an outdated Scheduler schedule `08:00/18:00 IL` vs the real `05:00/15:00 IL`) was itself stale** — a 2026-07-26 read found neither defect in the file; both had been corrected at some point and the gap was carried unverified for months. The file did contain a different and worse set of errors: `polymarket-pool` documented as a live second node pool (deleted in 9.5-A), "Flink jobs must be re-submitted after a scale-up" (false since K8s HA, and duplicate-submission-inducing — contradicts KG-C-4), wrong Artifact Registry repo + image names, five non-existent agent metrics, 15-vs-19 topics, `anizai-gold-polymarket` vs `anizai-gold-all-sources`, four-vs-five BI cards, and a wrong Firestore collection layout | An operator following it verbatim could submit duplicate Flink jobs, pull from a non-existent registry path, or wait for a node pool that no longer exists | — | Pre-Phase-9.5 brief | ✅ **CLOSED 2026-07-26** — full accuracy sweep of the file: all of the above corrected against the manifests, `agent/metrics.py`, `cluster_operations_guide.md` and `cloud_state.md`; scope header added routing bring-up to `guides/bringup_profiles.md`; KG-C-3 / KG-C-5 / KG-C-10 / KG-B-21 cross-references added at the points where they bite. Retained one cycle, then move to `cloud_archive.md`. **Lesson recorded:** a "do not touch" note on a doc gap stopped anyone re-reading the file, so the gap outlived the defects it described — re-verify a doc KG against the doc before deferring it again. |
| KG-C-7 | KG-PHASE-C-6 (infra slice) | GKE egress cannot reach `opensky-network.org:443` (`ConnectTimeoutError`) | OpenSky produces 0 Bronze from cloud | GCP firewall rule / IP-range investigation. The producer-code silent-success was mitigated app-side in Stage B (Domain A). **Medium** | Phase 9 (9D) closeout | GCP firewall/IP investigation before relying on OpenSky in cloud |
| KG-C-8 | KG-PHASE-C-1 | docker-compose `kafka-ui:latest` not pinned to `:v0.7.2` (cloud manifest is pinned) | Dev/cloud parity drift only | Tighten the compose tag. **Low** | Phase 9 (9B) | Compose parity cleanup |
| KG-C-9 | Phase 9.5-A Area 11 | `postgres-backup` CronJob not robust to scale-down — two daily backups missed (2026-05-16/17) when main-pool was at 0 (`startingDeadlineSeconds` exceeded) | Backup gap during off-hours | Tune `startingDeadlineSeconds` + add missed-run monitoring. **Medium** | Phase 9.5-A | Tune `startingDeadlineSeconds` + missed-run alert before long unattended windows |
| KG-C-10 | (new — 2026-07-26) | **Live replicas have drifted below the committed manifests, in the direction that starts spend.** `flink-jobmanager`, `flink-taskmanager`, `telegram` and `polymarket` all sit at `replicas: 0` in the cluster while their manifests in `infrastructure/k8s/` still declare `replicas: 1`. `agent-worker` is the inverse and is *intentional* (declared 0 since the Stage-1 deploy). The four were confirmed already at 0 when the 2026-07-26 agent-only run began — the drift predates that session | A routine `kubectl apply -f` of any of those four files restarts Flink and/or continuous ingestion with no scale command issued and no obvious cause: OpenAI enrichment spend, shared RPD burn (KG-C-1), and a Kafka backlog replay all begin silently. In the other direction, an operator reading the repo will believe ingestion is running when it is not | Decide per workload which value is authoritative, then make the manifest say it — either commit `replicas: 0` where the hold is deliberate, or scale live back to 1. Until then: read desired replicas from the cluster, never from the repo, and prefer `kubectl scale` over `apply` during a session (`guides/bringup_profiles.md` §5 trap 4). **Medium** — low effort, but the failure mode is silent spend | 2026-07-26 agent-only cloud run | At the next deliberate cluster restore — whoever decides to bring Flink and the continuous producers back up should reconcile git and live in the same pass, not after |

**Domain A producer-code gaps (referenced, not owned here):** GoogleTrends/pytrends 404
(KG-PHASE-9.5-5), Polymarket `/comments` breaking change (KG-PHASE-9.5-4), and the OpenSky
producer logic — all owned by Domain A; see `A_pipeline/pipeline_sprints.md`. Their
app-layer "raise on 0% success" mitigations landed in Phase 9.5 Stage B.
