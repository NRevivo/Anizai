# Task Plan — Implementation Plan (Phase-Level Map)
## Reference Document | Documentation map + open-work pointers

This is the **map** of the Anizai project: where each documentation file lives,
which phase it belongs to, and what work is open vs. closed.

For closed-phase detail, see `data-pipeline/task_plan_implementation_archive.md`.
For active sprint tracking (task-level status, Known Gaps), see
`data-pipeline/task_plan.md`. For granular sprint records (task tables, design
decisions, state ledgers from closed sprints), see
`data-pipeline/task_plan_archive.md`.

---

## Section 1 — Documentation Map

The project's documentation is organized into three categories inside
`data-pipeline/docs/`:

### 1.1 Architecture & Planning Docs (`data-pipeline/docs/` root)

Active, frequently-referenced specs and implementation plans.

**Category legend** (logical classification — Phase 1 doc-cleanup; no physical folders yet):
**1** = Architecture (stable, how the system works) · **2** = Work-plan (sprint plans / what was built) · **3** = State (what exists now / what is deployed) · **4** = Guide (operator how-to, in `guides/`).

| File | Cat | Purpose |
|---|---|---|
| `project_overview.md` | 1 | Spec §1–2: system vision, medallion architecture, 9-source matrix |
| `pipeline_core.md` | 1 | Spec §3–4: Kafka topic hierarchy, Flink Silver/Gold jobs |
| `data_contracts_and_sources.md` | 1 | Spec appendix: per-source parameters, all Bronze/Silver/Gold schemas |
| `storage_and_agent.md` | 1 | Spec §5–7: vault layer, agentic hub overview, GCP integration (§6 hub overview flagged to trim to a pointer in a future hub-doc phase) |
| `deployment_and_sprints.md` | 1 | Spec §8–9: Docker stack, vertical-slice methodology, Triple-Gate testing (§9 is process content; split candidate, deferred) |
| `anizai_cabinet_context.md` | 1 | Cabinet agent entry point — project overview compiled 2026-06-13 |
| `agentic_hub_spec.md` | 1 | Phase 8 architecture (Section 8 of the broader spec, all patches applied; §8.3.2 / §8.5.3 / §8.8.3 / §8.12 partially superseded by the 2026-05-23 revision) |
| `anizai_handoff_consolidated.md` | 1 | Phase 8 frontend contract: Firestore schemas, V1 UX features |
| `agentic_hub_implementation.md` | 2 | Phase 8 sprint plan — authoritative for Sprints 18–21 (closed); Sprints 22–26 sections superseded (see below) |
| `agentic_hub_implementation_phase8_revised.md` | 2 | Phase 8 **active** sprint plan for Sprints 22–27. 2026-05-23 revision: reactive search deferred to Future Enhancements, producer-trigger replacement (Sprint 23 new), Sprint 22 consolidated foundation fixes, Sprint 26 narrowed to pre-initial-test hardening, Sprint 27 new for post-test polish + Phase 8 closeout |
| `phase95_cluster_robustness_implementation.md` | 2 | Phase 9.5 plan (Stages A/B/C, all closed 2026-05-20) — reader-friendly summary; kept as active reference (not archived) for pre-initial-test cloud debugging |
| `phase10_calibration_system.md` | 2 | Phase 10 sprint plan (sub-phases 10A-10E) |
| `deployment_state.md` | 3 | Runtime state: what's on GKE, what's local-only, Cloud Scheduler + Docker Compose state (new 2026-06-13) |
| `phase95_investigation_log.md` | 3 | Phase 9.5 audit trail — raw diagnostic log written during A.1/B.1/C.1/C.2; kept as active reference |
| `project_knowledge_map.md` | 3 | Skills + docs inventory / knowledge-map snapshot (compiled 2026-06-13) |

### 1.2 Operator Guides (`data-pipeline/docs/guides/`)

How-to references and walkthroughs. **Category 4 — Guides** (all files in this section).

| File | Purpose |
|---|---|
| `CLOUD_CONNECTION_GUIDE.md` | GKE cluster operator runbook: port-forward commands, GCP Console navigation, secret retrieval, node-pool scale. **Known stale entries**: secret names + Cloud Scheduler schedule (KG-PHASE-9.5-3). |
| `cluster_operations_guide.md` | **Phase 9.5 closing artifact (new 2026-05-20)**. Long-term operational reference: architecture, daily flow, start/stop checklist, Cloud Scheduler resume procedure, 9 common-symptom runbooks, Flink-job-resubmission-after-image-rollout procedure, backlog-drop procedure, restore drill, command reference by symptom. Audience: Ron + future Claude sessions. |
| `VALIDATION_GUIDE.md` | Local Docker-Compose end-to-end validation manual |
| `newsapi_end_to_end.md` | Code walkthrough: one NewsAPI article traced through every pipeline layer (Hebrew Python-syntax annotations) |

### 1.3 Archived Plans (`data-pipeline/docs/archive/`)

Closed sprint plans and historical artifacts — kept for forensic reference. **Category 2 — Work-plans (closed)** (all files in this section).

| File | Status |
|---|---|
| `phase7_intelligent_filtering.md` | Phase 7 sprint plan — all sub-sprints closed 2026-05-09 |
| `cloud_deployment_implementation.md` | Phase 9 (Cloud) sprint plan — all sub-sprints closed 2026-05-10. *Uses internal name "Phase C / C1-C5" — see §3.* |
| `agentic_hub_spec_patch.md` | 15 patches that converted the original Phase 8 spec to its current form. All applied to `agentic_hub_spec.md`. |
| `sprint19_persistence_audit.md` | Pre-flight audit artifact from Sprint 19 — verified `persistence/*` call compatibility before T19.1 began |

---

## Section 2 — Phase Timeline

| Phase | Work Category | Sprints | Status | Plan |
|---|---|---|---|---|
| **0–6** | Pipeline Infrastructure | 1–17 | Done | Specs at `docs/` root; expanded steps in `task_plan_implementation_archive.md` |
| **7** | Pipeline — Filtering Refinement | 7A–7C | Done | `docs/archive/phase7_intelligent_filtering.md` |
| **8** | Agentic Hub | 18–27 | Sprints 18–21 done; 22–27 open (re-planned 2026-05-23) | `docs/agentic_hub_implementation.md` (18–21 closed) + `docs/agentic_hub_implementation_phase8_revised.md` (22–27 active) |
| **9** | Cloud Deployment | 9A–9E | Done | `docs/archive/cloud_deployment_implementation.md` |
| **9.5** | Cluster Robustness (Hardening + Monitoring) | A / B / C | **Done (2026-05-20)** | `docs/phase95_cluster_robustness_implementation.md` + `docs/phase95_investigation_log.md` |
| **10** | Calibration & Backtesting | 10A–10E | Not started | `docs/phase10_calibration_system.md` |

---

## Section 3 — Nomenclature Note (Phase 9)

The Phase 9 (Cloud Deployment) implementation doc and git history use an
**earlier phase name** that was renamed for consistency once the project's
phase sequence was finalized. The internal document itself was deliberately
not renamed to avoid breaking cross-references and historical traceability.
Phase 10's plan doc has been renamed in place since that work has not yet
started.

**Mapping:**

- **Phase 9 (Cloud Deployment)** ↔ "Phase C" in `cloud_deployment_implementation.md`
  - 9A ↔ C1   |   9B ↔ C2   |   9C ↔ C3   |   9D ↔ C4   |   9E ↔ C5

When reading `cloud_deployment_implementation.md`, mentally translate the
names accordingly. Active work and new commits going forward use the new
numbering (Phase 9, sub-phases 9A–9E for closed Cloud work; Phase 10,
sub-phases 10A–10E for the upcoming Calibration work).

---

## Section 4 — Open Work

### 4.1 Phase 8 — Agentic Hub (Sprints 22–27 open, re-planned 2026-05-23)

Sprints 18–21 are closed. The Tier 1 + Tier 2 + clarification flow runs in
production; remaining sprints add capabilities on top.

**Phase 8 re-plan (2026-05-23):** The original Sprint 22–23 (Tavily/Brave
reactive search microservice) was deferred to Future Enhancements after
KG-PHASE-9.5-9 (OpenAI cost analysis) and the Phase 7A NewsAPI provider
upgrade made an external paid search API hard to justify pre-initial-test.
The revised plan introduces a producer-trigger replacement (new Sprint 23),
consolidates wiring fixes (Sprint 22 Revised), narrows Sprint 26 to pre-test
critical work, and adds Sprint 27 for post-initial-test polish + Phase 8
closeout. Full design rationale in the revised plan's "Design Rationale Log".

**Closed sprints** (full records in `task_plan_archive.md`):
- Sprint 18 (Phase 8A) — Foundation: Firestore worker, stub `SessionResult`
- Sprints 19–20 (Phase 8B) — Tier 1 thin slice: vault retrieval + GPT-4o synthesis + Firestore writes
- Sprint 21 (Phase 8C) — Tier 2 + clarification flow

**Open sprints** (full task tables in `docs/agentic_hub_implementation_phase8_revised.md`):

| Sprint | Focus |
|---|---|
| 22 (Revised) | **Foundation Fixes.** Consolidated wiring of `marketProbability` + `predictionSeries` + `sentimentTimeSeries` through `synthesize` and `write_to_firestore`; Polymarket fuzzy-match resolver via `pg_trgm` (replaces deferred vector index); `canonicalKey` written to session doc for future cache. Closes KG-PHASE8-12 (wiring portion) + KG-PHASE8-22. Blocks Sprints 24/25/26. |
| 23 (New) | **Producer-trigger Infrastructure.** Replaces original reactive search microservice. NewsAPI `run_reactive()` method + `ingestion_triggers` Kafka registration + `reactive_triggers_log` table + `trigger_reactive_ingestion` node (built in isolation, wired at Sprint 26). Trigger-and-forget pattern. Parallel-able with Sprint 22. |
| 24 | **Follow-up Conversations (Revised).** Same Sprint 24 scope as original — second Firestore listener on `messages` subcollection, lightweight subgraph reuses parent session's context. Budget ~5-7s. Complete-message responses. Escalation branch (original T24.4) deferred to Future Enhancement 2; V1 ships `answer_from_context` only. |
| 25 | **Suggested Actions + Chain-of-Thought Events.** Unchanged from original plan: `suggestedActions[]` generation (one GPT-4o-mini call after synthesis) + continuous `agentEvents` stream powering the frontend's real-time reasoning panel. |
| 26 | **Pre-Test Hardening.** Narrowed from original Sprint 26 to pre-initial-test critical work: KG-PHASE8-16 latency analysis (analysis only), KG-PHASE8-17 OpenAI usage logging on synthesize+build_embedding (gating for cost-analysis goal), KG-PHASE8-20 ClarificationCandidate cleanup, Postgres retry wrapper on agent's `momentum_vault` calls, Prometheus metrics on the agent, git short-hash in `agentVersion`, and wiring of Sprint 23's `trigger_reactive_ingestion` node into the graph. |
| 27 (New) | **Post-Test Polish + Phase 8 Closeout.** After two-day initial test. KG-PHASE8-7, KG-PHASE8-15, original T26.1 (`error_handler.py`), T26.3-T26.5/T26.8-T26.11 (stress test, restart resilience, structured JSON logging, graceful shutdown, edge case tests, load test, documentation pass), Firestore retry wrapper, performance optimization (if KG-PHASE8-16 analysis found regressions — also gating for Phase 10's 100+-forecast load), Phase 8 closeout via State Ledger. Some tasks (27.12+) added based on initial-test findings. |

### 4.2 Phase 9.5 — Cluster Robustness (Done 2026-05-20, monitoring + ops docs live)

> **Implementation Plan:** `data-pipeline/docs/phase95_cluster_robustness_implementation.md`
> (Stage A + Stage B + Stage C, all closed; per-stage fix packages with verification)
> **Investigation Log:** `data-pipeline/docs/phase95_investigation_log.md`
> (read-only audit trail across all stage investigations)
> **Operations Guide (closing artifact):** `data-pipeline/docs/guides/cluster_operations_guide.md`
> (long-term reference — runbooks per Phase 9.5 finding)

Phase 9.5 is a three-stage robustness + monitoring hardening phase that
followed Phase 9's cloud-deployment closure and the May 11–18 silence
debugging session.

| Stage | Closed | Outcome |
|---|---|---|
| A — Infrastructure robustness | 2026-05-19 14:30 UTC | Discovered the primary May 11–18 root cause: Kafka was writing to ephemeral `/tmp/kafka-logs` (image default), not the PVC. Fixed via explicit `KAFKA_LOG_DIRS=/var/lib/kafka/data/kafka-logs`. Plus: Polymarket reverted to main-pool, polymarket-pool deleted; Airflow probe port 8793 → 8974; Prometheus 2Gi + 7d retention; kafka-init hourly idempotent CronJob. FRED + NewsAPI E2E verified through a scale 0→1 cycle. |
| B — Application robustness | 2026-05-20 00:15 UTC | Postgres-DNS resilience via `publishNotReadyAddresses` + transient-retry wrapper; 12 OpenAI client sites consolidated through `utils/openai_client.py` factory; Polymarket comments gated behind `POLYMARKET_COMMENTS_ENABLED=false`; OpenSky/googletrends raise-on-0%-success at the producer layer. 20 new tests pass, 102 existing tests still pass. 4 Docker images rebuilt to `*-p95` tags. |
| C — Monitoring + ops docs | 2026-05-20 16:30 UTC | 5 → 7 Prometheus scrape targets (+kafka_exporter, +postgres_exporter), 13 alert rules, Alertmanager via Gmail SMTP, Cloud Logging-based OpenAI 429 proxy + 2 Cloud Monitoring policies, 2nd Grafana dashboard, `cluster_operations_guide.md`. Pre-action: Silver→Gold backlog drop (~5,900 messages, saved ~$88 + 14,600 RPD-calls). |

**Known Gap surface (9 new + 3 pre-existing pointers):**
KG-PHASE-9.5-1 (OpenAI Tier 1 RPD ceiling), KG-PHASE-9.5-2 (NEWSAI_API_KEY
rename), KG-PHASE-9.5-3 (CLOUD_CONNECTION_GUIDE drift), KG-PHASE-9.5-4
(Polymarket /comments retire/repair), KG-PHASE-9.5-5 (pytrends 404
upstream), KG-PHASE-9.5-6 (maintenance window tuning), KG-PHASE-9.5-7
(image digest pinning), KG-PHASE-9.5-8 (Flink jobs need
cancel+resubmit-after-rollout — operationally documented), KG-PHASE-9.5-9
(OpenAI cost analysis — parallel session). Plus KG-PHASE-C-5/6/7 from
Phase 9 mitigated at the application layer.

**Cloud Scheduler:** still PAUSED. Resume readiness checklist in
`cluster_operations_guide.md` §4. Ron handles the resume.

### 4.3 Phase 10 — Calibration & Backtesting (Not started)

> **Note for future maintenance:** This plan is targeted for collaborator handoff.
> Specific updates to scope, ownership, and possibly the implementation approach
> will be made to `phase10_calibration_system.md` in a future session before the
> handoff. The summary below reflects the plan as currently written.

Standalone research harness that submits Polymarket-anchored questions to the
existing agent, polls Polymarket for resolutions, and computes Brier scores +
calibration curves. Zero changes to the agent. Full plan in
`docs/phase10_calibration_system.md`.

| Sub-phase | Focus |
|---|---|
| 10A | Postgres schema (5 tables) + Polymarket adapter (auto-select + manual add + resolution polling). |
| 10B | Forecast engine bridge: dispatch to `forecastQueries`, harvest from `sessionResults`. |
| 10C | Scoring layer: Brier + calibration curve + cohort + improvement delta. |
| 10D | Cloud automation: Cloud Run service + Cloud Scheduler weekly cycle. |
| 10E | Operator API + React UI contract handoff. |

---

## Section 5 — Closed Work (Pointers)

For Phases 0–7 and Phase 9: see `task_plan_implementation_archive.md` for
expanded phase-level descriptions, and `task_plan_archive.md` for granular
per-sprint records.

In short:
- **Phases 0–6** built the data pipeline foundation through monitoring (Sprints 1–17)
- **Phase 7** refined the NewsAPI ingestion path (provider migration + filtering + scraper retirement)
- **Phase 9** ported the entire stack to GKE
