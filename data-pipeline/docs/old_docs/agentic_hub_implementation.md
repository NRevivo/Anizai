# Agentic Hub — Implementation Plan
## Anizai Project | Phase 8 (Sprints 18-26)

---

## How to use this document

> ⚠ **Sprints 22–26 in this document have been superseded** (2026-05-23) by
> `agentic_hub_implementation_phase8_revised.md`. The original Sprint 22–23
> plan (Reactive Search Microservice via Tavily/Brave) was deferred — see
> the revised plan for the producer-trigger replacement and reordered sprint
> structure. Sprints 24, 25, 26 are also superseded with revised task tables
> in the new plan.
>
> **For Sprints 22–27 work, load `agentic_hub_implementation_phase8_revised.md`,
> not this document.** This document remains authoritative for Sprints 18–21
> (closed) only — task tables for 22–26 have been removed below; only the
> sprint scope summary is retained for historical context.

This is the **granular implementation plan** for the Agentic Intelligence Hub (Phase 8 of the Anizai project). It is the hub's equivalent of the per-source phase tables in `task_plan_implementation.md`.

It should be loaded by Claude Code at the start of every Phase 8 sprint, alongside:
- `agentic_hub_spec.md` (architectural specification)
- `agentic_hub_spec_patch.md` (patches reflecting the frontend integration alignment)
- The relevant sprint section in `task_plan.md` (active task tracker)
- The four hub-specific skills: `agent-design`, `agent-prompt-engineering`, `evidence-handling`, `frontend-integration`

The data-pipeline sprint conventions still apply:
- Conventional commits with section references
- Update `task_plan.md` after every completed task
- All work inside `data-pipeline/`
- No code without an approved implementation plan
- The four-gate testing model (adapted for the hub — see Section "Hub Gate Model" below)

---

## Phase 8 Overview

### Goal

Build a LangGraph-powered reasoning layer that:
1. Listens to `forecastQueries` (Firestore) for incoming forecast requests
2. Orchestrates retrieval from the four vault tables (knowledge, social, momentum, mapping)
3. Falls back to live web search via the reactive search microservice when the vault is insufficient
4. Synthesizes evidence into a confidence-scored forecast
5. Writes results back to Firestore for the frontend's real-time listeners
6. Handles follow-up conversations and clarification flows

### Sprint Structure (hybrid by-capability)

The hub is structured as **end-to-end thin slices that grow in capability**, not as horizontal layers. Each sprint produces a working system; later sprints add features without breaking earlier ones.

| Sprint | Phase | Focus | Definition of Done |
|--------|-------|-------|---------------------|
| 18 | 8A | Foundation | Worker process listens on `forecastQueries`, claims pending docs, writes a stub result back to Firestore. No real reasoning yet. |
| 19-20 | 8B | End-to-end Tier 1 thin slice | User submits a Polymarket-backed question → hub runs full pipeline (no clarification, no reactive search) → frontend displays result. |
| 21 | 8C | Tier 2 + clarification flow | Freeform questions and ambiguous market matches handled correctly. |
| 22-23 | 8D | Reactive search microservice | When vault is insufficient, agent calls online search; results integrated into evidence. |
| 24 | 8E | Follow-up conversations | User can chat about a completed forecast; agent responds within 5-7s budget. |
| 25 | 8F | Suggested actions + chain-of-thought events | Dynamic follow-up suggestions; real-time agent thinking events visible in UI. |
| 26 | 8G | Polish + edge cases | Error handling, idempotency, plan limits, agent versioning, graceful degradation. |

### Pre-Phase 8 Checkpoint

Before Sprint 18 begins, verify:

- [ ] Frontend partner has merged the 7 agreed changes (probability units, /api prefix, session status ownership, idempotency keys, demo route hardening, plan limit handling, clarification frontend stubs)
- [ ] `agentic_hub_spec_patch.md` has been applied to `agentic_hub_spec.md`
- [ ] All four hub-specific skills exist in `.claude/skills/`: `agent-design`, `agent-prompt-engineering`, `evidence-handling`, `frontend-integration`
- [ ] `sprint-kickoff` and `bugfix` skills have been edited for the hub branch
- [ ] Phase 5 reactive ingestion (Bronze-layer Kafka triggers, Section 2.4 of pipeline spec) status is confirmed — note any gaps that affect Sprint 22-23

If any item is incomplete, do NOT begin Sprint 18 until it is resolved.

---

## Hub Gate Model (4 gates, adapted from pipeline)

The data-pipeline used Gate 1 (Bronze schema) → Gate 2 (Silver/Gold logic) → Gate 3 (Persistence) → E2E. The hub uses an analogous 4-gate model:

| Gate | Pipeline equivalent | Hub equivalent |
|---|---|---|
| **Gate 1** | Bronze schema compliance | **Node-level unit tests** — each LangGraph node tested in isolation with mocked inputs/outputs. State transitions verified. |
| **Gate 2** | Silver/Gold logic with mocks | **Subgraph integration tests** — connected paths tested with mocked external calls (LLM, search APIs, vault queries). Routing decisions verified. |
| **Gate 3** | Live persistence + idempotency | **Firestore round-trip via emulator** — full agent run against Firestore emulator. All writes match contracts. Claim/resume/idempotency verified. |
| **E2E** | Real APIs and DBs | **Real-world run** — real Firestore, real OpenAI, real search APIs, actual user query end-to-end. |

Each task is tagged with which gate(s) it must pass before being marked `[x]`.

---

## Phase 8A — Foundation (Sprint 18)

### Sprint scope

Build the bare skeleton of the hub: a Python worker process that connects to Firestore, listens for pending forecast queries, claims them atomically, writes a stub result, and updates status. **No LangGraph yet, no agents, no LLM calls.** This sprint is purely about establishing the worker pattern and Firestore plumbing.

The end of Sprint 18 deliverable: when the frontend submits a forecast, a stub `sessionResults` doc appears in Firestore within 2 seconds, with hardcoded values like `{finalProbability: 0.5, bottomLineAnswer: "Stub response from Sprint 18 worker."}`. The frontend should display this as a "real" forecast — that proves the integration works end-to-end before any reasoning is added.

### Confirmed design decisions

- Worker process runs as a long-lived Python script invoked via `python -m agent.worker`
- One worker = one Python process = one Firestore listener. Multiple workers can run in parallel (each claims different docs); for V1 we run a single worker.
- Atomic claim uses Firestore transactions (`db.transaction()` context manager) — no race conditions even with multiple workers
- Firebase Admin SDK with service account credentials in production; Application Default Credentials (`gcloud auth application-default login`) in dev
- Stub result conforms to the full `SessionResult` schema (Section 8.7.2 of patched spec) — using sensible defaults for unused fields — so the frontend's contract is satisfied from day 1

### Task table

| Task | Description | Gate(s) | Spec Reference |
|------|-------------|---------|----------------|
| 18.1 | Add Firebase Admin SDK + LangGraph dependencies to `requirements.txt` (firebase-admin>=6.0, langgraph>=0.2, langchain-openai>=0.2). Pin exact versions. | — | §8.9 |
| 18.2 | Implement `agent/firestore_client.py` — Admin SDK wrapper. Functions: `init_app()`, `get_db()`, `claim_query(query_id, worker_id)` (transactional), `update_session_status(session_id, status, error_msg=None)`, `write_session_result(session_id, result_dict)`. Each function with a docstring referencing §8.7. | Gate 1 | §8.7.1, §8.8.1 |
| 18.3 | Implement `agent/worker.py` — entry point. Initializes Firestore client, attaches a snapshot listener on `forecastQueries where status == 'pending'`, invokes the (stub) processing function for each new doc. Handles SIGTERM/SIGINT gracefully (finishes claimed sessions before exiting). | Gate 1 | §8.8.1 |
| 18.4 | Implement `agent/process_query.py` — stub processing function. Takes a `forecastQueries` doc reference, claims it, updates session status `running`, writes a stub `SessionResult`, sets session and query status to `done`. No reasoning. | Gate 1 | §8.7.2 |
| 18.5 | Implement `agent/state.py` — `ForecastState` TypedDict with all fields from Patch 6 of the spec patch doc. No graph yet; this is the data shape that future sprints will use. | Gate 1 | §8.3.1 (patched) |
| 18.6 | Implement `agent/health.py` — minimal HTTP server (using `aiohttp` or `fastapi` minimally) exposing `/health` and `/metrics` (just basic worker stats for now). Runs on `HUB_HEALTH_PORT`. | Gate 1 | §8.8.2 |
| 18.7 | Add hub-related env vars to `config/settings.py`: `FIREBASE_PROJECT_ID`, `GOOGLE_APPLICATION_CREDENTIALS`, `AGENT_WORKER_ID`, all the `AGENT_*` configs from Patch 14. | — | §8.11 |
| 18.8 | Add Dockerfile for the hub worker (`infrastructure/Dockerfile.agent`). Python 3.11, installs requirements, runs `python -m agent.worker`. | — | §8.10 (deployment) |
| 18.9 | Add agent worker service to `docker-compose.yml`. Depends on Postgres + Firestore emulator (in dev). Mounts service-account JSON. | — | §8.10 |
| 18.10 | Gate 1 tests: unit tests for `claim_query` (transactional correctness), `init_app` (handles missing creds), state schema validation. | Gate 1 | §9.3 Gate 1 |
| 18.11 | Gate 2 tests: subgraph test of full stub-processing flow with mocked Firestore. Verifies status transitions queued→claimed→running→done. | Gate 2 | §9.3 Gate 2 |
| 18.12 | Gate 3 tests: integration test against Firestore emulator. Submit a `forecastQueries` doc, verify `SessionResult` doc appears with correct shape. | Gate 3 | §9.3 Gate 3 |
| 18.13 | E2E test: deploy worker locally, submit real `forecastQueries` doc via Firebase console, confirm stub result appears in real Firestore. | E2E | §9.3 E2E |

### Cold-start handling

If no pending docs exist when the worker starts, it idles on the Firestore listener (zero CPU usage). When the first doc arrives, processing begins.

### Open questions deferred to future sprints

- Multi-worker coordination (claim races) — handled by Firestore transactions, but stress-test in Sprint 26 (8G).
- Worker restart mid-claim — addressed by `AGENT_CLAIM_TIMEOUT_SECONDS` (other workers can re-claim after timeout). Verified in Sprint 26.

---

## Phase 8B — End-to-end Tier 1 Thin Slice (Sprints 19-20)

### Sprint scope

Replace the stub with a **real, working forecasting pipeline for Tier 1 (Polymarket-backed) questions only**. Skips clarification (always picks the highest-confidence match), skips reactive search (relies entirely on vault), skips follow-ups, skips suggested actions, skips agentEvents streaming.

The goal: by end of Sprint 20, a user submitting a real Polymarket-related question gets a real LLM-generated forecast based on real vault evidence, displayed correctly in all four BI cards. This is the **first "it actually works" milestone**.

The two-sprint structure: Sprint 19 builds the LangGraph skeleton + retrieval agents; Sprint 20 builds the synthesis node and the Firestore writes that populate the BI cards.

### Sprint 19 — Vault retrieval pipeline

#### Confirmed design decisions

- LangGraph StateGraph compiled once at worker startup, reused across sessions
- Three retrieval agents (`researcher`, `pulse_analyst`, `market_bridge`) run in parallel using LangGraph's `dispatch` pattern
- Embedding generation uses OpenAI's `text-embedding-3-small` (matches existing 1536-dim HNSW indexes)
- Tool wrappers in `agent/tools/` wrap the existing `persistence/` modules — no new DB logic in the hub layer
- **Sprint 19 binding overrides** (kickoff message, 2026-04-30):
  - **D1** Placeholder `synthesize` node = Sprint 18 stub builder relocated to `agent/nodes/synthesize.py`. Graph terminates at retrieval; real synthesis lands in Sprint 20.
  - **D2** No `agentEvents` emission this sprint (deferred to Sprint 25). Nodes do not call event helpers.
  - **D3** Embedding cache is an in-memory state field (`state.query_embedding`) only. No Redis/Postgres cache; re-running a session re-embeds.
  - **D4** `canonical_event_id` linkage is sparse pre-Phase 7 — Market Bridge built to spec (§8.4.3) and expected to often return empty `linked_sources`. Tests fixture both populated and empty cases.
  - **D5** `process_query.py` is **replaced**, not extended. Stub helpers (`_build_stub_result`, `_derive_label`, `_derive_consensus`, `_STUB_*` constants, `AGENT_VERSION`) move to `agent/nodes/synthesize.py`. `process_query.py` becomes a thin graph runner.
  - **D6** Gate B sequencing: T19.1 wrappers must produce a working artifact (Gate 1 unit tests passing **AND** smoke-check against dev Postgres) before T19.2-19.4 begin.
  - **D7** Audit-gated start: T19.0 produces a persistence-API audit report which must be approved before any code lands in `agent/tools/`, `agent/agents/`, or `agent/nodes/`.
  - **D8** `AGENT_VERSION` bumped to `"0.2.0-sprint19-retrieval-stub-synthesis"`.
  - **D9** `query_understand` uses OpenAI structured-output mode (`response_format={"type": "json_schema", ...}`) with the schema defined in `agent/prompts/query_understanding.py`.
  - **D10** Top 3 Polymarket candidates persisted on state for Sprint 21 reuse — final state-field choice (reuse `clarification_candidates` vs. add `polymarket_candidates_considered`) flagged in T19.0 audit-report wrap-up so Sprint 21 doesn't re-litigate.

#### Task table

| Task | Description | Gate(s) | Spec Reference |
|------|-------------|---------|----------------|
| 19.0 | **Pre-flight persistence-API audit.** Read public signatures of `persistence/knowledge_vectors.py`, `social_vectors.py`, `momentum_vault.py`, `mapping_dict.py`, `knowledge_vault.py`, `social_vault.py`. Map each spec §8.4.1/2/3 required call to the existing function. Identify drift (param-name/shape mismatches, missing functions). Produce `data-pipeline/docs/sprint19_persistence_audit.md` with: (a) call-mapping table per agent, (b) drift list, (c) blocker list, (d) D10 wrap-up flagging the chosen state-field for top-3 Polymarket candidates with rationale. **Gates T19.1.** No code in `agent/tools/`, `agent/agents/`, `agent/nodes/` until the report is approved. | — | §8.4 |
| 19.1 | Implement `agent/tools/knowledge_tools.py`, `social_tools.py`, `market_tools.py`, `mapping_tools.py` — thin wrappers around `persistence/` modules. Each tool accepts a query embedding (or canonical_event_id) and returns evidence dicts. **Gate B artifact required**: Gate 1 unit tests passing AND `pytest -m smoke` smoke-check against dev Postgres (gated on `DATABASE_URL`) **before** T19.2-19.4 begin. | Gate 1 | §8.4 |
| 19.2 | Implement `agent/agents/researcher.py` — Researcher agent algorithm (similarity search + composite ranking + drill-down to knowledge_vault). Returns `ResearcherEvidence` shape. | Gate 1 | §8.4.1 |
| 19.3 | Implement `agent/agents/pulse_analyst.py` — Pulse Analyst agent. Returns `PulseEvidence` shape. Includes the consensus-extreme drill-down logic. | Gate 1 | §8.4.2 |
| 19.4 | Implement `agent/agents/market_bridge.py` — Market Bridge agent. Returns `MarketEvidence` shape with Polymarket data, FRED anomalies, Google Trends. Tier 2 path returns `polymarket: None`. Sparse `canonical_event_id` per D4 — tests fixture both populated and empty `linked_sources` cases. | Gate 1 | §8.4.3 |
| 19.5 | Implement `agent/nodes/query_understand.py` — Node 1. Calls GPT-4o-mini with structured-output mode (D9). Outputs `tier`, `structured_intent`, `polymarket_market`, top-3 candidates per D10, sets `awaiting_clarification = false` (Sprint 21 makes this real). | Gate 1, Gate 2 | §8.3.2, §8.5.4 |
| 19.6 | Implement `agent/prompts/query_understanding.py` — system prompt + JSON schema for question classification. Output schema: tier, entities, timeframe, market candidates. | Gate 1 | §8.3.2 |
| 19.7 | Implement `agent/nodes/build_embedding.py` — Node 2. Calls OpenAI embedding API (`text-embedding-3-small`, 1536-dim). Stores on `state.query_embedding` (in-memory cache only per D3). | Gate 1 | §8.4.1 |
| 19.8 | Implement `agent/nodes/vault_query.py` — Node 3. Dispatches the three retrieval agents in parallel using LangGraph's parallel execution. Aggregates results into state. | Gate 2 | §8.3.2 |
| 19.9 | Implement `agent/graph.py` — Compile the partial graph: claim_session → query_understand → build_embedding → vault_query → (placeholder) synthesize → END. Placeholder synthesize is the relocated Sprint 18 stub builder per D1/D5. | Gate 2 | §8.3.2 |
| 19.10 | Relocate Sprint 18 stub helpers (`_build_stub_result`, `_derive_label`, `_derive_consensus`, `_STUB_*` constants, `AGENT_VERSION`) from `process_query.py` into `agent/nodes/synthesize.py` per D1/D5. Bump `AGENT_VERSION` to `"0.2.0-sprint19-retrieval-stub-synthesis"` per D8. | Gate 1 | §8.6, §8.8.1 |
| 19.11 | Replace `agent/process_query.py` with a thin graph runner per D5. Initializes `ForecastState` from the `forecastQueries` doc, invokes the compiled graph, propagates failures via `_mark_failed`. Single error code `AGENT_PROCESSING_ERROR` (taxonomy deferred to Sprint 26 T26.1). | Gate 2 | §8.8.1 |
| 19.12 | Gate 1 tests: unit tests for each tool wrapper, each agent (mocked vault calls), each node (mocked external calls). Smoke-check fixture for T19.1. | Gate 1 | §9.3 Gate 1 |
| 19.13 | Gate 2 tests: subgraph integration test of `claim_session → query_understand → build_embedding → vault_query → placeholder synthesize`. Mocks LLM and vault calls; verifies state passes through correctly and stub SessionResult lands in (mocked) Firestore. | Gate 2 | §9.3 Gate 2 |
| 19.14 | Close **KG-PHASE8-7** — add `logging.basicConfig()` at worker entry (`agent/worker.py`) so INFO-level logs surface during sprint-19 development and operations. Verify by running the worker and confirming startup, subscription, and per-session log lines appear on stderr. Update `task_plan.md` Known Gaps to mark KG-PHASE8-7 closed. | — | §8.8.2 |

### Sprint 20 — Synthesis + Firestore writes

#### Confirmed design decisions

- Synthesis Lead uses GPT-4o (quality-critical step)
- Output structure: `key_factors` (3-5 ranked), `bottom_line_answer`, `detailed_explanation`, numeric `final_probability` + `confidence`, plus all derived label fields
- `marketComparison` data points use 0-1 floats (per the standardization)
- `evidence` subcollection writes happen in batched transactions for atomicity (max 500 docs per batch — Firestore limit)
- For Sprint 20, `key_factors`, `what_i_didnt_find` are written but rendering is up to the partner; `suggested_actions`, `agentEvents` are deferred to Sprint 25

#### Task table

| Task | Description | Gate(s) | Spec Reference |
|------|-------------|---------|----------------|
| 20.1 | Implement `agent/nodes/rate_evidence.py` — unified rating pass. Takes the three agent evidence packages, normalizes into `EvidenceItem[]` (per Patch 10 §8.5.5). Each item gets relevance/credibility/recency scores via prompt or deterministic logic. | Gate 1, Gate 2 | §8.5.5 |
| 20.2 | Implement `agent/prompts/evidence_rating.py` — system prompt for rating ambiguous evidence items where deterministic scoring isn't enough. | Gate 1 | §8.5.5 |
| 20.3 | Implement `agent/nodes/synthesize.py` — Node 6. GPT-4o call. Takes rated `EvidenceItem[]` + market data + structured intent. Returns full synthesis output. | Gate 1, Gate 2 | §8.6 |
| 20.4 | Implement `agent/prompts/synthesis_lead.py` — system prompt for synthesis. Includes rubrics for `key_factors` ranking, `what_i_didnt_find` generation, `confidence` calibration. | Gate 1 | §8.6 |
| 20.5 | Implement `agent/nodes/write_to_firestore.py` — Node 7. Persists `SessionResult`, batched writes for `evidence` subcollection, populates `predictionSeries` and `sentimentTimeSeries` from market_evidence. Sets session.status = 'done'. | Gate 1, Gate 2 | §8.7.1, §8.7.2 |
| 20.6 | Implement deterministic label derivation helpers in `agent/labels.py`: `confidence_label_from_score()`, `evidence_volume_label_from_count()`, `consensus_strength_from_signals()`. Apply thresholds from Patch 11. | Gate 1 | §8.7.2 |
| 20.7 | Update `agent/graph.py` — wire in `rate_evidence` between `vault_query` and `synthesize`, then `write_to_firestore` after `synthesize`. Compile the full Tier 1 graph. | Gate 2 | §8.3.2 |
| 20.8 | Gate 1 tests: unit tests for `rate_evidence`, `synthesize` (mocked LLM), `write_to_firestore` (mocked Firestore), label derivation. | Gate 1 | §9.3 Gate 1 |
| 20.9 | Gate 2 tests: full graph integration test with mocked LLM, vault, and Firestore. Submit a fixture forecastQueries doc, verify all four BI card data ends up correctly shaped in mock Firestore. | Gate 2 | §9.3 Gate 2 |
| 20.10 | Gate 3 tests: full pipeline against Firestore emulator. Verify SessionResult, evidence subcollection, predictionSeries all written correctly. | Gate 3 | §9.3 Gate 3 |
| 20.11 | E2E test: real Firestore + real OpenAI + real Polymarket data. Question: "Will the Fed cut rates by Q2 2026?" Verify a real forecast appears, BI cards render correctly. Capture sample output for fixture archive. | E2E | §9.3 E2E |
| 20.12 | Document the End-to-end Tier 1 milestone in `task_plan.md` Phase 8B. Phase 8B closes here. | — | — |

### Cold-start handling

If `query_understand` returns no Polymarket match for what should be a Tier 1 question (sometimes happens for very recent markets not yet in Gold layer), the agent treats it as Tier 2 with a flag. Sprint 21 will improve this with the proper clarification flow.

### Phase 8B success criteria

- [ ] User submits a Polymarket-related question → real forecast appears in 15-30s
- [ ] All four BI cards render with real (not stub) data
- [ ] No errors in Firestore writes
- [ ] Cost per forecast within ±20% of estimate ($0.03)
- [ ] Latency p95 < 30 seconds

---

## Phase 8C — Tier 2 + Clarification Flow (Sprint 21)

### Sprint scope

Sprint 8B always picked the highest-confidence Polymarket match. Sprint 8C makes the agent honest about ambiguity: when no clear match exists or multiple candidates compete, write `clarificationCandidates` and stop. Wait for user pick. Resume on `POST /sessions/:id/clarify`.

Also handles **Tier 2 freeform questions** properly: questions with no Polymarket match but still forecastable (e.g., "Will a major earthquake hit Tokyo in 2026?"). The agent runs the full pipeline but with `tier: "tier_2"` and `marketProbability: null`.

### Confirmed design decisions

- The agent does NOT call `POST /sessions/:id/clarify` — that endpoint is the partner's BFF. The agent only writes the candidates and stops. Re-trigger comes from the partner's BFF writing a new `forecastQueries` doc.
- On re-trigger, the new `forecastQueries` doc has a `chosenCandidateId` field. The agent reads this and sets `state.skip_matching_step = true`.
- "None of these — analyze as freeform" sets `chosenCandidateId: null` → agent treats as Tier 2.
- Match confidence threshold: top match's `matchConfidence` must be >= 0.10 above second-place to avoid clarification. Otherwise, write candidates.

### Task table

| Task | Description | Gate(s) | Spec Reference |
|------|-------------|---------|----------------|
| 21.1 | Update `agent/nodes/query_understand.py` — return all candidates with similarity > 0.75 (not just the top one). Compute confidence delta between top two. | Gate 1 | §8.2.3 (patched) |
| 21.2 | Implement `agent/nodes/write_clarification.py` — branch node. Writes `clarificationCandidates` array on the session doc, sets `session.status = 'awaiting_clarification'`, emits `clarification_needed` to agentEvents (preview of Sprint 25), ends the graph. | Gate 1 | §8.2.4 (patched) |
| 21.3 | Update `agent/graph.py` — add the `ambiguous?` conditional edge after `query_understand`. If ambiguous → `write_clarification` → END. Else → `build_embedding`. | Gate 2 | §8.3.2 (patched) |
| 21.4 | Update `agent/process_query.py` — when starting a new session, check the `forecastQueries` doc for a `chosenCandidateId` field. If present, populate `state.chosen_candidate_id` and `state.skip_matching_step = true`. | Gate 1 | §8.8.4 (patched) |
| 21.5 | Update `agent/nodes/query_understand.py` — if `state.skip_matching_step == true`, skip the matching step entirely and use `state.chosen_candidate_id` directly. | Gate 1 | §8.2.3 (patched) |
| 21.6 | Update `agent/agents/market_bridge.py` — when Tier 2, return `polymarket: None` and skip Polymarket-specific queries. Other data sources (FRED, weather, trends) still queried. | Gate 1 | §8.2.2 (patched) |
| 21.7 | Update `agent/nodes/synthesize.py` — handle Tier 2 input. `marketComparisonInsight` becomes "No canonical market available — freeform analysis." `marketComparison` array empty. `marketProbability` null. | Gate 1 | §8.2.2 (patched) |
| 21.8 | Update `agent/nodes/write_to_firestore.py` — write `tier` field on SessionResult. Tier 2 sessions persist same as Tier 1 (per partner agreement). | Gate 1 | §8.2.2 (patched) |
| 21.9 | Gate 1 tests: unit tests for ambiguity detection, clarification candidate writing, skip-matching resume, Tier 2 synthesis. | Gate 1 | §9.3 Gate 1 |
| 21.10 | Gate 2 tests: subgraph test of full clarification flow — submit ambiguous question, verify candidates written, simulate user choice, verify resume produces correct forecast. | Gate 2 | §9.3 Gate 2 |
| 21.11 | Gate 3 tests: end-to-end via Firestore emulator. Test all three paths: clear Tier 1 match, ambiguous (clarification flow), and Tier 2 freeform. | Gate 3 | §9.3 Gate 3 |
| 21.12 | E2E test: real environment. Submit "Will the Fed cut rates?" (likely ambiguous — multiple Polymarket markets). Verify clarification UI in frontend. Pick one, verify forecast resumes. | E2E | §9.3 E2E |

### Cold-start handling

If a user clicks "None of these — analyze as freeform" and there's also no relevant FRED/weather/trends data (Tier 2 with very thin context), synthesis still runs but produces `confidence < 0.5` and a populated `whatIDidntFind` array. Frontend displays as low-confidence with caveats.

---

## Phase 8D — SUPERSEDED (originally: Reactive Search Microservice, Sprints 22-23)

> **The original Sprint 22–23 plan — a Tavily/Brave-based reactive search
> microservice with `reactive_article_cache` Postgres table, allowlist filter,
> and snippet extractor — has been deferred indefinitely (2026-05-23).**
>
> **Reason:** OpenAI cost concerns surfaced by Phase 9.5 (KG-PHASE-9.5-9
> parallel session) plus the NewsAPI provider upgrade (Phase 7A migration to
> newsapi.ai with full article body) made an external paid search API hard
> to justify before the initial test phase.
>
> **Replacement (current plan):** Sprint 23 in
> `agentic_hub_implementation_phase8_revised.md` ships a **producer-trigger
> infrastructure** that reuses the existing `ingestion_triggers` Kafka topic
> to ask the NewsAPI producer to fetch targeted articles. Trigger-and-forget;
> articles land in the vault for the next session.
>
> The original architecture remains documented in `agentic_hub_spec.md` §8.12
> as a Future Enhancement (see Future Enhancement 1 in the revised plan) if
> external search becomes justified later.

---

## Phase 8E — SUPERSEDED (originally: Follow-up Conversations, Sprint 24)

> **Sprint 24 has been revised** (2026-05-23). The original plan implemented
> both `answer-from-context` and `escalate-to-reactive-search` branches in
> the follow-up subgraph. The escalation branch has been deferred.
>
> **Current plan:** Sprint 24 in `agentic_hub_implementation_phase8_revised.md`
> ships only `answer_from_context.py` — follow-ups answer exclusively from
> the parent SessionResult and existing evidence. When context is insufficient,
> the agent returns a transparent message hinting at future capability rather
> than fabricating partial answers.
>
> The escalation branch is Future Enhancement 2 in the revised plan, designed
> to connect to Sprint 23's `trigger_reactive_ingestion` node when re-enabled.

---

## Phase 8F — SUPERSEDED (originally: Suggested Actions + Chain-of-Thought Events, Sprint 25)

> **Sprint 25 is largely unchanged** (2026-05-23). The task table moves to
> `agentic_hub_implementation_phase8_revised.md` for self-containment, with a
> single adjustment: T25.7 (updating follow-up nodes to emit `agentEvents`)
> now refers to the revised follow-up nodes from Sprint 24.

---

## Phase 8G — SUPERSEDED (originally: Polish + Edge Cases, Sprint 26)

> **Sprint 26 has been split** (2026-05-23). The original 12-task list mixed
> pre-test-critical work (cost-tracking, version-tracking, retry wrappers)
> with production-hardening work (50-session load tests, 3-worker stress
> tests, edge-case coverage) that targets a multi-tenant production
> environment.
>
> **Current plan:**
> - **Sprint 26** in `agentic_hub_implementation_phase8_revised.md`: only
>   pre-test-critical tasks (KG-PHASE8-16, -17, -20 + T26.2/T26.6/T26.7 +
>   wiring of `trigger_reactive_ingestion` into the graph). 2-3 days.
> - **Sprint 27 (new)** in the revised plan: production-hardening tasks
>   (T26.1, T26.3, T26.4, T26.5, T26.8, T26.9, T26.10, T26.11) plus
>   KG-PHASE8-7, -15, and Phase 8 closeout. Runs after the two-day initial
>   test.
>
> The original Sprint 26 task table is removed below; load the revised plan
> for current task definitions.

---

## Out of Scope for V1 (Phase 8)




Per the partner agreement and consolidated handoff doc, these features are **explicitly deferred** beyond Phase 8:

- Sentiment confidence band rendering (data is written, but rendering is partner's frontend task)
- Trending sidebar consuming from Gold layer (currently bypasses pipeline; refactoring is post-V1)
- Token-by-token streaming for follow-ups (complete-message responses for V1)
- Full-richness Suggested Actions with per-button icons (simpler dynamic for V1)
- Multi-question session support (one question per session per the "One Question, One Thread" rule, §8.2.3)
- User-facing API endpoints from the hub (no FastAPI gateway; only `/health` and `/metrics` for monitoring)

These can become Phase 9+ work if/when there's demand.

---

## Post-V1 Future Directions

Forward-looking architectural and product ideas surfaced during design but **not yet committed** to any phase. Distinct from "Out of Scope for V1" above (features explicitly deferred via partner agreement during bucket coordination); these items are unscheduled and may or may not become future phases.

- **Calibration system:** Track Anizai probability vs. actual market resolution
  outcomes over time. Build a calibration curve to improve confidence scoring.
- **Multi-language frontend:** The agent handles non-English questions, but the
  evidence trail and executive summary are always in English. A future phase
  could add response translation.
- **Agent memory / learning:** Store which evidence patterns led to accurate
  predictions. Use this to improve retrieval ranking weights over time.
- **Cost optimization:** If volume grows, consider self-hosted embedding model
  (e.g., sentence-transformers) to eliminate per-query embedding costs.
  Would require rebuilding HNSW indexes with new dimensions.
- **Evidence alerts (Section 6.4):** Push-based alerts when Flink identifies
  high-urgency signals relevant to active forecast sessions. Requires a
  Kafka consumer in the API layer watching Gold topics.

---

## Cross-Sprint References & Skill Loading

### Skills required per sprint

| Sprint | Required skills |
|---|---|
| 18 | sprint-kickoff, infrastructure, agent-design |
| 19 | sprint-kickoff, agent-design, evidence-handling, frontend-integration |
| 20 | sprint-kickoff, agent-design, agent-prompt-engineering, evidence-handling, frontend-integration, prompt-engineering |
| 21 | sprint-kickoff, agent-design, frontend-integration |
| 22 | sprint-kickoff, infrastructure, evidence-handling |
| 23 | sprint-kickoff, agent-design, agent-prompt-engineering, evidence-handling |
| 24 | sprint-kickoff, agent-design, agent-prompt-engineering, frontend-integration |
| 25 | sprint-kickoff, agent-design, agent-prompt-engineering, frontend-integration |
| 26 | sprint-kickoff, code-review, bugfix |

### Documents required per sprint

All sprints require:
- `agentic_hub_spec.md` (with patches applied)
- `agentic_hub_implementation.md` (this doc)
- `task_plan.md` (active tracker)
- The relevant skills listed above

Sprint 19+ also needs the consolidated handoff doc (`anizai_handoff_consolidated.md`) for evidence schemas and Firestore contracts.

---

## Phase 8 Acceptance Criteria (V1 Done)

The hub is V1-complete when:

- [ ] All 9 sprints (18-26) closed with Sprint State Ledgers
- [ ] All gates passing for all sprints
- [ ] E2E demo: end-to-end live run for both Tier 1 and Tier 2 questions, including follow-ups, with chain-of-thought visible in frontend
- [ ] Cost per forecast within ±20% of estimate ($0.03 base, $0.05 with reactive search)
- [ ] p95 latency under 30 seconds for full forecasts, under 7 seconds for follow-ups
- [ ] No critical or high-severity bugs open
- [ ] All hub code documented (docstrings, prompts/README.md, .env.example)
- [ ] CI pipeline green; Phase 8 closeout doc archived

---

## Appendix: Test Question Bank

Use these questions for consistent testing across all sprints:

| ID | Question | Expected Tier | Expected Behavior |
|----|----------|--------------|-------------------|
| Q1 | "Will AI regulation pass in the EU by Q2 2026?" | Tier 1 | Full Polymarket-backed forecast |
| Q2 | "Will Bitcoin reach $150K by end of 2026?" | Tier 1 | Full forecast with momentum data |
| Q3 | "Will a major earthquake hit Tokyo in 2026?" | Tier 2 | Freeform; weather + news evidence |
| Q4 | "What will happen in the Middle East?" | Ambiguous | Clarification: suggest 2-5 specific markets |
| Q5 | "What's the best pizza in New York?" | Rejected | Polite rejection: not a forecastable question |
| Q6 | "Will Biden win the 2024 election?" | Resolved | Historical result: event already resolved |
| Q7 | "הָאִם רגולציה על AI תעבור באיחוד האירופי?" | Tier 1 | Hebrew question → detect, translate, classify |
| Q8 | "Will inflation drop below 2% tomorrow?" | Tier 1/2 | Time horizon mismatch warning |
