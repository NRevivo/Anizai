# Project Knowledge Map — Anizai

> Generated 2026-06-13 as a read-only inventory of the `.claude/skills/` and
> `data-pipeline/docs/` surface, to support planning an AI agent system. Every
> entry is one honest sentence per field; staleness flags are called out, not
> smoothed over. No source files were modified.

---

## PART 1 — Skills (`.claude/skills/`)

### sprint-kickoff
- **Problem solved:** Enforces the mandatory pre-code kickoff sequence so no work starts without docs read, plan written, and explicit approval.
- **Triggered by:** Starting any new sprint, new data source, or new phase.
- **Claude Code does:** Identifies domain (A pipeline / B hub) and sprint type (A–E), reads the domain's doc set, writes a granular implementation plan, waits for approval, then expands the task table in `task_plan.md`.
- **References:** `CLAUDE.md`, `task_plan.md`, `pipeline_core.md`, `data_contracts_and_sources.md`, `deployment_and_sprints.md`, `storage_and_agent.md`, `agentic_hub_spec.md`, `agentic_hub_implementation.md`, `anizai_handoff_consolidated.md`, `archive/agentic_hub_spec_patch.md`, `task_plan_archive.md`; skills `code-review`, `bugfix`, `infrastructure`, `prompt-engineering`, `filter-analysis`, `agent-design`, `agent-prompt-engineering`, `evidence-handling`, `frontend-integration`.
- **Staleness:** Its Domain B reading list and per-sprint skills table (Sprints 18–26) reflect the ORIGINAL Phase 8 plan; it never mentions `agentic_hub_implementation_phase8_revised.md` (the active plan for Sprints 22–27). Does not reference `gcp-deployment` despite Phase 9 cloud work.

### sprint-closeout
- **Problem solved:** Produces a complete handoff so the next session starts without guessing.
- **Triggered by:** Finishing a sprint / ending a session / wrapping a phase.
- **Claude Code does:** Generates the 6-section Sprint State Ledger (A–F), updates `task_plan.md` statuses + Known Gaps, moves full sprint record to `task_plan_archive.md`, collapses the active section to a keyword summary, and checks `task_plan.md` < 200 lines.
- **References:** `CLAUDE.md` §5.2, `task_plan.md`, `task_plan_archive.md`.
- **Staleness:** None noticed.

### code-review
- **Problem solved:** Enforces engineering guardrails (Section 3) and the test-execution protocol (Section 4) in every coding session.
- **Triggered by:** Reviewing code, running tests, validating standards — MANDATORY in every coding session per `CLAUDE.md`.
- **Claude Code does:** Part A reviews service isolation / DRY / DLQ / docstrings / working dir / commit format; Part B governs when tests run, syntax-bug-vs-design-issue triage, and E2E rules.
- **References:** `CLAUDE.md` §3 and §4.
- **Staleness:** None noticed.

### bugfix
- **Problem solved:** Structures targeted fixes with mandatory before/after comparison and approval gating; applies to both pipeline and hub code.
- **Triggered by:** Fixing a known bug, closing a Known Gap, or a pre-phase checkpoint task.
- **Claude Code does:** Understand→show current code→propose fix with impact + risk level→wait for approval→apply→run affected tests→update `task_plan.md` and docstrings. Includes removal/audit subprotocol and a hub-specific bug-pattern catalog (state-shape, routing, Firestore contract, probability-unit drift, idempotency, budget, agentEvents).
- **References:** `task_plan.md`, `task_plan_archive.md`, `agentic_hub_spec.md`, `agentic_hub_implementation.md`; skills `code-review`, `agent-design`, `evidence-handling`, `frontend-integration`.
- **Staleness:** Cites the historical "Reddit Artifact Audit" as an example — confirms Reddit was a removal target (see Part 3 flag).

### agent-design
- **Problem solved:** Governs the LangGraph control flow of the Agentic Hub — node boundaries, state schema, routing, budgets.
- **Triggered by:** Designing/building/reviewing graph structure, nodes, conditional edges, sufficiency-check routing.
- **Claude Code does:** Applies 5 principles (one node/one job, state-is-contract, explicit routing functions, mandatory budget tracking, determinism); provides the graph-structure reference, node-design sequence, and the `VaultSufficiencyCheck` routing rubric.
- **References:** `agentic_hub_spec.md` (§8.5.4), `agentic_hub_implementation.md`; skills `agent-prompt-engineering`, `evidence-handling`, `frontend-integration`.
- **Staleness:** Graph reference still shows the reactive-search escalation path; per the revised Phase 8 plan reactive search was deferred and replaced by a producer-trigger (trigger-and-forget) node — the in-skill graph is the original design.

### agent-prompt-engineering
- **Problem solved:** Governs the hub's REASONING prompts (calibration under uncertainty, gap-admission, user-facing copy) — distinct from deterministic Gold-layer enrichment prompts.
- **Triggered by:** Creating/modifying prompts for hub nodes (query understanding, sufficiency, evidence rating, synthesis, suggested actions, follow-up).
- **Claude Code does:** Enforces 6 principles (Pydantic structured output, calibrate-don't-classify, permission to admit gaps, user-facing string rules, system-prompt-under-800-words + few-shot, domain context); gives per-prompt design notes and a cost table.
- **References:** `agentic_hub_spec.md` (§8.5.4), `agentic_hub_implementation.md`; skills `agent-design`, `evidence-handling`, and the deterministic `prompt-engineering` skill (explicit contrast).
- **Staleness:** None noticed (prompts live in `agent/prompts/`).

### evidence-handling
- **Problem solved:** Defines the unified `EvidenceItem` shape and how every piece of evidence is rated, regardless of origin (vault vs reactive search).
- **Triggered by:** Touching evidence schema, ratings, source allowlist, recency/time-window logic, or vault↔reactive reconciliation.
- **Claude Code does:** Enforces one schema for all evidence, ratings-travel-with-item, user-facing justifications, allowlist-as-credibility-truth; specifies relevance/credibility/recency/impact rating logic, `source_allowlist.json`, the reactive cache lookup order, and evidence-volume labels.
- **References:** `agentic_hub_spec.md` (§8.5.5, §8.12), `agent/config/source_allowlist.json`; skills `agent-design`, `agent-prompt-engineering`, `frontend-integration`.
- **Staleness:** Heavily describes reactive-search evidence (allowlist, probe, cache) — the reactive-search microservice was deferred in the revised Phase 8 plan, so portions describe deferred-but-spec'd functionality.

### frontend-integration
- **Problem solved:** Defines the hub↔frontend contract — Firestore as message bus, worker pattern, status transitions, idempotency, probability units.
- **Triggered by:** Any work on the boundary between hub and the Express BFF / Firestore / React frontend.
- **Claude Code does:** Enforces Firestore-only communication, the atomic-claim worker pattern, exact subcollection schemas (`sessions`, `sessionResults`, `evidence`, `agentEvents`, `messages`, `predictionSeries`, `sentimentTimeSeries`), 0-1 probability units, clarification flow, error handling, and graceful shutdown.
- **References:** `anizai_handoff_consolidated.md`, `agentic_hub_spec.md` (§8.7, §8.8); skills `agent-design`, `evidence-handling`, `agent-prompt-engineering`.
- **Staleness:** None noticed; correctly uses the real filename `anizai_handoff_consolidated.md`.

### infrastructure
- **Problem solved:** Adds extra caution for system-wide infra changes (a broken image blocks every source).
- **Triggered by:** Docker images, docker-compose, Dockerfiles, SQL init scripts, Airflow, monitoring.
- **Claude Code does:** Scope→design doc→incremental build→integration verification; carries the `Dockerfile.flink` (PyFlink 1.19) requirements, docker-compose change rules, and `init.sql` rules.
- **References:** `deployment_and_sprints.md` (§8.2), `project_overview.md` (§2.3), `storage_and_agent.md` (§7.2), `docker-compose.yml`, `infrastructure/sql/init.sql`.
- **Staleness:** Describes the LOCAL docker-compose stack as Phases 5/6A/6B; the system has since moved to GKE (Phase 9) — pairs with but doesn't cross-link `gcp-deployment`.

### prompt-engineering
- **Problem solved:** Ensures Gold-layer OpenAI enrichment prompts are deterministic and anchored (structured JSON, anchored scales).
- **Triggered by:** Creating/reviewing/improving GPT-4o enrichment prompts in the Gold layer.
- **Claude Code does:** Enforces `prompts/` directory layout, 6 principles (structured output, anchored scales, domain context, entity guidance, fixed topic vocabulary, explicit fact-check triggers), and a build/test/compare workflow against `tests/mocks/`.
- **References:** `prompts/` (`cognitive_metadata.py`, `consensus_summary.py`, README), `tests/mocks/`, `gold_job.py`.
- **Staleness:** None noticed.

### filter-analysis
- **Problem solved:** Forces evidence-first, data-driven changes to the `keyword_sniper` Silver filter (never tune on a hunch).
- **Triggered by:** Analyzing/evaluating/improving Silver-layer keyword filtering.
- **Claude Code does:** Sample→classify (TP/TN/FP/FN)→compute precision/recall (recall ≥0.85 target)→diagnose→propose tabled changes→re-run same sample to verify.
- **References:** `processing/keyword_sniper.py`; per-source notes (NewsAPI/ArXiv/Telegram). Aligns with the memory that the sniper is a coarse quality filter.
- **Staleness:** Mentions Reddit only implicitly; thresholds here predate Phase 7B's `DEFAULT_THRESHOLD` 0.09→0.15 change (skill examples still use 0.09).

### gcp-deployment
- **Problem solved:** Fast lookup card for Phase C/9 cloud deployment (GKE, project constants, manifests, secrets, Workload Identity).
- **Triggered by:** Any Phase C/9 cloud-deployment sprint (loaded with `infrastructure` + `sprint-kickoff`).
- **Claude Code does:** Provides project constants, file layout, workload-type rules, manifest conventions, secret handling, kubectl/port-forward/build-push references, gate verification, and Workload-Identity troubleshooting.
- **References:** `archive/cloud_deployment_implementation.md` (heavily), `infrastructure/k8s/`, `infrastructure/gcp/`.
- **Staleness:** Titled and written around "Phase C / C1–C5"; that work is renamed Phase 9 / 9A–9E (see `task_plan_implementation.md` §3). Not listed in `CLAUDE.md` §6 skill table.

> **`CLAUDE.md` §6 staleness:** the skill table lists only 7 skills; 5 exist but are unlisted there — `agent-design`, `agent-prompt-engineering`, `evidence-handling`, `frontend-integration`, `gcp-deployment`.

---

## PART 2 — Documentation (`data-pipeline/docs/`)

### project_overview.md
- **Status:** Active (reference spec §1–2).
- **Contents:** System vision, medallion (Bronze/Silver/Gold) architecture, hybrid ingestion, and the 11-source producer matrix.
- **Who needs it:** Claude Code at Domain A pipeline kickoff; `infrastructure` skill (§2.3 orchestration).
- **Last update (content):** Original spec; not dated. Predates source-roster changes.
- **Staleness:** Lists PredictIt and Reddit as active producers (§2.1 matrix; §2.2 "Telegram & Reddit") — both appear deprecated elsewhere (see Part 3).

### pipeline_core.md
- **Status:** Active (reference spec §3–4).
- **Contents:** Kafka topic hierarchy/naming, NDJSON envelope, retention policy, and the Flink Silver/Gold job logic.
- **Who needs it:** Claude Code at Domain A kickoff (Kafka/Flink work).
- **Last update (content):** Original spec; undated.
- **Staleness:** Bronze topic list and Silver families still include `predictit` and `reddit`.

### data_contracts_and_sources.md
- **Status:** Active (reference appendix).
- **Contents:** Per-source registry and technical parameters (Telegram channels, ArXiv, HN, FRED, NewsAPI, OpenWeather, OpenSky, etc.) plus Bronze/Silver/Gold schemas.
- **Who needs it:** Claude Code at every Domain A kickoff; hub sprints for underlying data shapes.
- **Last update (content):** Original spec; undated.
- **Staleness:** §A.2 documents Reddit communities as active ingestion; NewsAPI §B.4 still describes the TheNewsAPI-era whitelist/keyword model superseded by the Phase 7A newsapi.ai migration.

### storage_and_agent.md
- **Status:** Active (reference spec §5–7).
- **Contents:** Four-vault PostgreSQL strategy (Knowledge/Social/pgvector/TimescaleDB + mapping dict), the agentic-hub overview (3 specialized agents + Synthesis Lead), and GCP integration/monitoring contract.
- **Who needs it:** Claude Code at pipeline kickoff (vault/persistence); `infrastructure` skill (§7.2 monitoring).
- **Last update (content):** Original spec; undated.
- **Staleness:** §6 hub description is the high-level original (Researcher/Pulse Analyst/Market Bridge); the detailed and current contract lives in `agentic_hub_spec.md`.

### deployment_and_sprints.md
- **Status:** Active (reference spec §8–9).
- **Contents:** Docker Compose stack, Vertical-Slice methodology, the Triple-Gate test matrix, and the living-document handoff protocol.
- **Who needs it:** Claude Code at pipeline kickoff; `infrastructure` skill (§8.2 service stack).
- **Last update (content):** Original spec; undated.
- **Staleness:** §8.2 says "11 independent Python containers" (includes deprecated sources); describes the local compose stack, now superseded by GKE for production.

### agentic_hub_spec.md
- **Status:** Active (reference) — all 15 patches applied; §8.3.2 / §8.5.3 / §8.8.3 / §8.12 partially superseded.
- **Contents:** Full Phase 8 hub architecture — purpose, two-tier question model, nodes, schemas (`VaultSufficiencyCheck`, `EvidenceItem`, SessionResult), reactive cache.
- **Who needs it:** Claude Code at every Phase 8 hub sprint; skills `agent-design`, `agent-prompt-engineering`, `evidence-handling`, `frontend-integration`, `bugfix`.
- **Last update (content):** Post-patch (patch round Oct 2026 per patch doc); §8.12 etc. flagged superseded by the 2026-05-23 revision.
- **Staleness:** Reactive-search sections (§8.12 cache, allowlist) describe deferred functionality; partial-supersession is documented in headers but the body still carries the original design.

### agentic_hub_implementation.md
- **Status:** Reference — authoritative for Sprints 18–21 (closed) ONLY; Sprints 22–26 sections superseded.
- **Contents:** Granular Phase 8 plan, gate model, pre-Phase-8 checkpoint, sprint scope summaries.
- **Who needs it:** Claude Code for Sprints 18–21 historical context only.
- **Last update (content):** Superseded note dated 2026-05-23.
- **Staleness:** By design — its own header redirects Sprints 22–27 to the revised plan; the `sprint-kickoff` skill still treats it as the always-read hub plan.

### agentic_hub_implementation_phase8_revised.md
- **Status:** Active — the live plan for Sprints 22–27.
- **Contents:** Why the re-plan happened (OpenAI cost, NewsAPI full-body upgrade, initial-test goal), revised sprint overview, dependency map, producer-trigger replacement for reactive search.
- **Who needs it:** Claude Code at every Sprint 22–27 hub session.
- **Last update (content):** 2026-05-23 revision.
- **Staleness:** Current. NOT referenced by `sprint-kickoff`/`agent-design`/`agent-prompt-engineering` skills — a gap, since it is the authoritative plan.

### anizai_handoff_consolidated.md
- **Status:** Active (reference) — Phase 8 frontend/BFF contract.
- **Contents:** Firestore data contracts, worker-pattern architecture, the 5 must-do frontend changes (0-1 probability units, etc.), V1 UX, deferred items, coordination points.
- **Who needs it:** Claude Code at hub sprints touching the frontend boundary; `frontend-integration` skill (single source of truth).
- **Last update (content):** "Three planning rounds"; undated in the first 80 lines.
- **Staleness:** The task request referred to this as `agentic_hub_handoff_consolidated.md`, which does NOT exist — the real and correctly-referenced filename is `anizai_handoff_consolidated.md`.

### phase95_cluster_robustness_implementation.md
- **Status:** Closed (2026-05-20) — reference summary.
- **Contents:** Phase 9.5 cluster-robustness plan (Stages A/B/C): the May 11–18 silence root causes, fixes, and monitoring/ops hardening.
- **Who needs it:** Claude Code for cluster-robustness/ops context; pairs with the investigation log.
- **Last update (content):** Stages closed 2026-05-19/20.
- **Staleness:** Cloud Scheduler noted as still PAUSED (intentional, Ron resumes manually) — a live operational caveat, not a doc error.

### phase10_calibration_system.md
- **Status:** Active plan, work Not started (collaborator-handoff target).
- **Contents:** Standalone Brier-score/calibration backtesting harness plan (10A–10E) that submits Polymarket questions to the existing agent with zero agent changes.
- **Who needs it:** Claude Code (or collaborator) at Phase 10 kickoff.
- **Last update (content):** Reflects post-Phase-9 renumbering; undated body.
- **Staleness:** Self-flags that calibration-specific skills don't exist yet (placeholder names); notes scope/ownership may change before handoff.

### archive/agentic_hub_spec_patch.md
- **Status:** Archive (reference) — all 15 patches applied to the spec.
- **Contents:** The patch operations (REPLACE/DELETE/ADD/EDIT) that converted the original Postgres+FastAPI spec to the Firebase-native model.
- **Who needs it:** Claude Code only for forensic "why did the spec change" lookups; `sprint-kickoff` Pre-Phase-8 checkpoint references it.
- **Last update (content):** Planning rounds Oct 2026.
- **Staleness:** Fully applied — historical only.

### archive/sprint19_persistence_audit.md
- **Status:** Archive (reference) — Sprint 19 pre-flight artifact.
- **Contents:** Audit verifying every `persistence/*` call needed by the three retrieval agents existed with compatible signatures before T19.1.
- **Who needs it:** Rarely — forensic reference for persistence-API decisions.
- **Last update (content):** 2026-04-30.
- **Staleness:** Point-in-time artifact; fine as archive.

### archive/phase7_intelligent_filtering.md
- **Status:** Archive — all sub-sprints closed 2026-05-09.
- **Contents:** Phase 7 plan: newsapi.ai provider migration (7A), two-stage intelligent filter (7B), scraper retirement (7C).
- **Who needs it:** Claude Code for Phase 7 design rationale; `filter-analysis` work referencing the threshold changes.
- **Last update (content):** Closed 2026-05-09; Phase 7B.5 calibration queued separately.
- **Staleness:** Fine as archive; note 7B.5 is still open work tracked in `task_plan.md`.

### archive/cloud_deployment_implementation.md
- **Status:** Archive — Phase 9 (Cloud) closed 2026-05-10.
- **Contents:** GKE migration plan (Sprints C1–C5 / 9A–9E): manifests, Artifact Registry, Secret Manager, Workload Identity.
- **Who needs it:** Claude Code for cloud-deploy history; `gcp-deployment` skill's companion task tables.
- **Last update (content):** Closed 2026-05-10.
- **Staleness:** Uses internal "Phase C / C1–C5" names (renamed Phase 9 per `task_plan_implementation.md` §3); kept un-renamed deliberately.

### guides/newsapi_end_to_end.md
- **Status:** Active (reference walkthrough).
- **Contents:** Traces one Reuters/OPEC article through Ingestion→Bronze→Silver→Gold→Persistence with real code blocks.
- **Who needs it:** Claude Code onboarding to the NewsAPI path; teaching reference.
- **Last update (content):** Reflects post-7A newsapi.ai path.
- **Staleness:** None obvious in the opening; verify keyword/threshold examples match current Phase 7B values if used for filtering work.

### guides/VALIDATION_GUIDE.md
- **Status:** Active for LOCAL validation (labeled Sprint 17).
- **Contents:** Docker-Compose end-to-end validation manual (start stack, submit Flink jobs, trigger DAGs, run validation summary).
- **Who needs it:** Anyone validating the local stack end-to-end.
- **Last update (content):** Sprint 17 era; cleaned during Phase 7C.
- **Staleness:** Local-stack focused; production now runs on GKE — use `cluster_operations_guide.md` for cloud. Still lists 7 DAGs and streaming producers (incl. interactive Telegram).

### guides/cluster_operations_guide.md
- **Status:** Active (Phase 9.5 closing artifact, new 2026-05-20).
- **Contents:** Long-term GKE operations reference — topology, daily flow, start/stop, Scheduler resume, symptom runbooks.
- **Who needs it:** Ron + future Claude sessions operating the live cluster.
- **Last update (content):** 2026-05-20.
- **Staleness:** Current; explicitly supersedes stale parts of `CLOUD_CONNECTION_GUIDE.md`.

### guides/CLOUD_CONNECTION_GUIDE.md
- **Status:** Active but KNOWN-STALE (KG-PHASE-9.5-3).
- **Contents:** GKE operator runbook — auth, port-forwards, PSQL access, GCP console.
- **Who needs it:** Operators connecting to the cluster; treat as topology reference only.
- **Last update (content):** Pre-9.5; not refreshed.
- **Staleness:** 13 wrong secret-name references (lowercase-dash vs UPPER_SNAKE_CASE) and an outdated Cloud Scheduler schedule — flagged in both the doc map and `cluster_operations_guide.md`.

### task_plan.md
- **Status:** Active master tracker (first 50 lines reviewed for structure).
- **Contents:** Granular task checklist; Phase 7 closed, Phase 8 active (Sprints 18–21 closed with keyword summaries, 22–27 open), Known Gaps. Points to the revised hub plan and `anizai_handoff_consolidated.md`.
- **Who needs it:** Claude Code at the start and end of every session.
- **Last update (content):** Through Phase 8 Sprint 20+ summaries; Sprint 23 commits exist in git (T23.x).
- **Staleness:** None at the structural level; it is the canonical live status.

### task_plan_implementation.md
- **Status:** Active (the documentation MAP / phase-level index).
- **Contents:** Documentation map (root/guides/archive), phase timeline (0–10), Phase 9 nomenclature note, open-work pointers for Phases 8/9.5/10.
- **Who needs it:** Claude Code as the entry index to find which doc governs which phase.
- **Last update (content):** Reflects 2026-05-23 hub re-plan and Phase 9.5 closeout (2026-05-20).
- **Staleness:** Accurate and current; does NOT list `anizai_cabinet_context.md` (new untracked file — orphaned, see Part 3).

---

## PART 3 — Connections Map

### Which skills reference which doc files
- **sprint-kickoff** → `pipeline_core.md`, `data_contracts_and_sources.md`, `deployment_and_sprints.md`, `storage_and_agent.md`, `agentic_hub_spec.md`, `agentic_hub_implementation.md`, `anizai_handoff_consolidated.md`, `archive/agentic_hub_spec_patch.md`, `task_plan_archive.md`.
- **agent-design** → `agentic_hub_spec.md`, `agentic_hub_implementation.md`.
- **agent-prompt-engineering** → `agentic_hub_spec.md`, `agentic_hub_implementation.md`.
- **evidence-handling** → `agentic_hub_spec.md`.
- **frontend-integration** → `anizai_handoff_consolidated.md`, `agentic_hub_spec.md`.
- **bugfix** → `agentic_hub_spec.md`, `agentic_hub_implementation.md`.
- **infrastructure** → `deployment_and_sprints.md`, `project_overview.md`, `storage_and_agent.md`.
- **gcp-deployment** → `archive/cloud_deployment_implementation.md`.
- **prompt-engineering**, **filter-analysis**, **code-review**, **sprint-closeout** → reference code/`CLAUDE.md` paths, not the docs mapped here.

### Doc files referenced by multiple skills (highest gravity first)
- `agentic_hub_spec.md` — 6 skills (agent-design, agent-prompt-engineering, evidence-handling, frontend-integration, bugfix, sprint-kickoff). The hub's center of gravity.
- `agentic_hub_implementation.md` — 4 skills (agent-design, agent-prompt-engineering, bugfix, sprint-kickoff) — despite being superseded for Sprints 22–27.
- `anizai_handoff_consolidated.md` — 2 skills (frontend-integration, sprint-kickoff).
- `deployment_and_sprints.md`, `project_overview.md`, `storage_and_agent.md` — each 2 (sprint-kickoff + infrastructure).

### Orphaned / new / duplicated files
- **`anizai_cabinet_context.md`** — NEW, untracked (git `??`), NOT in the documentation map and referenced by no skill or doc. Orphaned until indexed.
- **`phase95_investigation_log.md`** — exists and is mapped, but was not in the task's read-list; it is the raw-log companion to `phase95_cluster_robustness_implementation.md` (intentional pairing, not a duplicate).
- **`agentic_hub_implementation.md` vs `..._phase8_revised.md`** — intentional split (18–21 vs 22–27), but a near-duplicate trap: skills point only to the OLD doc, so a kickoff for Sprint 22+ could load stale task tables.
- **No true content duplicates** found among the mapped docs; the spec/patch/handoff trio is layered, not duplicated.

### ✅ Files mentioning Reddit or PredictIt as active/planned — RESOLVED (Phase 1 doc cleanup, 2026-06-13)
> **Resolved 2026-06-13** (Phase 1 pipeline-doc cleanup). The four spec docs below were reconciled under the "annotate as removed / keep-but-annotate schema" policy: source matrices/topic lists no longer present Reddit/PredictIt as active, an "Excluded Sources" / "Removed sources" note records the real reasons (Reddit — API pre-approval since Nov 2025, code removed Sprint 11 T4; PredictIt — public API shut down by CFTC 2022–2024), and Part-C schema enums are kept-but-annotated as **dormant** because the live PostgreSQL CHECK constraints still include them (SQL/infra enum cleanup is a later phase). Original findings retained below for history.
- **`project_overview.md`** — §2.1 producer matrix listed **PredictIt** and **Reddit** as active sources; §2.2 named "Telegram & Reddit" under Social Pulse. *(Fixed: matrix → "9 Active Sources", rows removed, "Removed sources" note added, Social Pulse → Telegram.)*
- **`pipeline_core.md`** — §3.1 Bronze topics included `ingest.bronze.predictit` and `ingest.bronze.reddit`; Silver `social_pulse` family listed Reddit. *(Fixed: topics removed with a kafka-init annotation; Silver families cleaned; Divergence Alerts marked dormant.)*
- **`data_contracts_and_sources.md`** — §A.2 documented 4 Reddit communities (PRAW streaming) as active. *(Fixed: A.2 → "Excluded Sources", B.9 PredictIt removed, Part-C dormant note added; enums intentionally retained to match the live DB.)*
- **`deployment_and_sprints.md`** — §8.2 "11 independent Python containers" count included the deprecated sources. *(Fixed: corrected to 9 source producers + accurate run-mechanism note; §9.2 "8 sources" → "6 sources".)*
- **Remaining (out of Phase-1 scope):** the live `init.sql` / `postgres-configmap.yaml` CHECK constraints and `kafka-init` topic creation still include `reddit`/`predictit` — infra/SQL cleanup is a deferred phase. The Part-C "dormant" annotation is what keeps the docs honest until then.

### Other cross-cutting flags for the agent-planning effort
- **NewsAPI provider drift:** `data_contracts_and_sources.md` §B.4 still describes the old TheNewsAPI whitelist/keyword model; Phase 7A migrated to newsapi.ai with full article body. Trust `archive/phase7_intelligent_filtering.md` + `newsapi_end_to_end.md` for current behavior.
- **Reactive search deferred:** `agent-design`, `evidence-handling`, and `agentic_hub_spec.md` §8.12 describe a Tavily/Brave reactive-search microservice + allowlist + cache that was DEFERRED (revised plan replaces it with a NewsAPI producer-trigger, Sprint 23). Anything planning the agent's "fill-the-gap" path should follow `agentic_hub_implementation_phase8_revised.md`, not the spec/skills.
- **Phase naming:** "Phase C / C1–C5" == "Phase 9 / 9A–9E"; "Phase 9 (calibration)" in old drafts == "Phase 10". See `task_plan_implementation.md` §3.
