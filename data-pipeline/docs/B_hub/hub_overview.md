# hub_overview.md
> Domain: B — Agentic Hub
> Type: Overview
> Last updated: 2026-07-19
> TL;DR: The macro view of the Anizai Agentic Intelligence Hub — what it is, how a question flows through the LangGraph state machine, where each component lives, and which sprints are closed vs. open. Open this first to orient before the detailed hub files.

## Navigation
- §1 — Purpose & Scope — what the hub is and what it is not
- §2 — Architecture — the LangGraph forecast graph as currently built
- §3 — Components — every node, agent, tool, and module, plus status
- §4 — Phase & Sprint Status — which sprints are closed and what remains open
- §5 — References — the other Domain B files and where to look for what

---

## §1 — Purpose & Scope

The Agentic Intelligence Hub (Phase 8) is the **reasoning layer** connecting the
data pipeline (Domain A) to the user-facing frontend via Firestore. It turns a
user's natural-language question into a confidence-scored, multi-sourced forecast
by orchestrating retrieval from the four PostgreSQL vaults, rating the evidence,
and synthesizing it with GPT-4o.

**In scope:** a LangGraph state machine; a Firestore worker that claims pending
`forecastQueries` docs; three retrieval agents (Researcher, Pulse Analyst, Market
Bridge) reading the vaults read-only; question classification + clarification +
two-tier handling; evidence rating; synthesis; and writing `SessionResult` +
subcollections back to Firestore for the frontend's real-time listeners.

**Not in scope (other domains):** the data pipeline that *produces* the vaults
(Domain A); the GCP/GKE cloud deployment (Domain C, Phase 9); the
calibration/backtesting harness (Domain D, Phase 10); and the frontend itself
(`client/`, `server/`) — the hub touches only its Firestore interface to them.

**What the hub is NOT:** it does not modify vault data (read-only on all vaults;
write-only to Firestore session collections + the Sprint 23 `reactive_triggers_log`
table); it does not expose an HTTP API to the frontend (worker pattern, not a
FastAPI/WebSocket gateway); and it does not re-compute enrichment scores
(impact_level, reliability, sentiment are pre-computed by the Gold Job).

The hub is **partially implemented**: Sprints 18–26 are closed (end-to-end Tier 1
+ Tier 2 + clarification + producer-trigger infrastructure; the Sprint 23.5 pre-26
remediation that built the `sufficiency_check` node, wired the reactive path, and
added the central LLM cost layer — closed 2026-06-20; the **Sprint 24 follow-up
conversation path** — a 2nd Firestore collection-group listener on `messages` + the
structurally-separate `agent/followup/` subgraph + the done-transition sweep, closed
2026-07-04; **Sprint 25** — the `generate_suggested_actions` node + the
non-blocking `agentEvents` chain-of-thought emitter, closed 2026-07-16; and
**Sprint 26** (Pre-Test Hardening) — clarification-field strip (KG-B-8), vault-read
retry, git-sha `agentVersion`, delivery-path retarget closing KG-B-18, 3 Prometheus
metrics + a real `/metrics`, and the per-node latency report, closed 2026-07-18). The
current NEXT task is the **initial cloud test** (~2 days — baseline real production
cost + forecast quality on live vault data; gated on Sprint 26, now unblocked), after
which comes Sprint 27 (post-test polish + Phase 8 closeout). Note the `agentEvents`
stream is emitted by the **main forecast graph only** — the follow-up graph emits
**NO** events (the 2026-07-04 inversion of the original T25.7 plan); follow-ups get a
status-based "thinking" indicator instead. See `hub_sprints.md §1` for full per-sprint
status and `hub_archive.md` for the closed-sprint digests.

---

## §2 — Architecture

The hub is a **LangGraph `StateGraph`** compiled once at worker startup. A single
`ForecastState` TypedDict flows through every node; nodes never call each other —
they read and write state, and routing functions branch on state fields.

**As-built forecast graph (topology through Sprint 25 — Sprint 26 was hardening-only, no graph changes):**

```
 START
   │
   ▼
 claim_session        Atomic claim of forecastQueries doc; status claimed → running;
   │                  mints run_id, writes currentRunId, emits agentEvents bootstrap
   │
   ▼
 query_understand     GPT-4o-mini classification → structured_intent, tier signal,
   │                  top-3 Polymarket candidates; sets awaiting_clarification
   │
   ├─ ambiguous? ──Yes──▶ write_clarification ──▶ END
   │                       (writes clarificationCandidates, status=awaiting_clarification)
   │
   └─ No
   ▼
 build_embedding      OpenAI text-embedding-3-small (1536-dim), cached on state
   │
   ▼
 vault_query          Parallel dispatch (ThreadPoolExecutor) of the 3 retrieval agents
   │
   ▼
 sufficiency_check    Deterministic (no LLM) rubric over the 3 evidence packages vs.
   │                  structured_intent; writes sufficiency_checks + missing_dimensions
   │
   ├─ insufficient? ─Yes─▶ trigger_reactive_ingestion ──▶ (rejoins rate_evidence)
   │                        fire-and-forget Kafka trigger to the NewsAPI producer
   │                        (≤1/session); does NOT wait — articles land for a later run
   │
   └─ sufficient
   ▼
 rate_evidence        GPT-4o-mini: normalize to unified EvidenceItem[], score relevance
   │                  (both branches converge here)
   │
   ▼
 synthesize           GPT-4o: probability, confidence, key_factors, what_i_didnt_find;
   │                  infers tier from market_evidence.polymarket
   │
   ▼
 generate_suggested_actions   GPT-4o-mini → 3 contextual {id,label,prompt} actions;
   │                          G4-graceful (degrades to [] / event failed, never fails
   │                          the forecast)
   │
   ▼
 write_to_firestore   SessionResult (incl. suggestedActions) + evidence /
   │                  predictionSeries / sentimentTimeSeries subcollections;
   │                  drain agentEvents → status=done → queue done
   ▼
 END

 agentEvents (Sprint 25): every MAIN-graph node emits a non-blocking start/complete
 event during the run; the follow-up subgraph (Sprint 24) emits none.
```

The graph has two conditional edges: the clarification branch after `query_understand`
and the sufficiency branch after `sufficiency_check`. The spec's sufficiency-check →
reactive path (§8.3.2) is built as a **simplified V1** (Sprint 23.5, closed
2026-06-20): `sufficiency_check` (deterministic, no LLM) routes sufficient →
`rate_evidence` and insufficient → `trigger_reactive_ingestion` (a fire-and-forget
Kafka trigger to the NewsAPI producer, ≤1/session) which then rejoins `rate_evidence`
— there is no second vault query and no external-search microservice (Tavily/Brave
remains deferred, Future Enhancement 1). See `hub_agents.md §2.4`,
`archive_plans/sprint23_5_pre26_remediation.md`, and `hub_sprints.md`.

> The diagram above is the current as-built topology — graph nodes through Sprint 25;
> Sprint 26 was hardening-only and added no nodes. `sufficiency_check` +
> `trigger_reactive_ingestion` landed in Sprint 23.5, `generate_suggested_actions` in
> Sprint 25; the authoritative node / edge / routing detail lives in `hub_agents.md §2`.
>
> The **follow-up subgraph** (Sprint 24) is a *second, separate* LangGraph and is
> deliberately not in the forecast-graph diagram above — see `hub_agents.md` §2.5 /
> §3.5 / §4.5 for its topology, nodes, and the `messages` contract.

Every Firestore write targets `sessions/{sessionId}/…`; `forecastQueries` is the
work queue. Failures route to a `failed` SessionResult via `process_query._mark_failed`.

---

## §3 — Components

| Component | Role | File / Module | Status |
|---|---|---|---|
| Worker | Long-lived Firestore listener on `forecastQueries where status=='pending'`; atomic claim; SIGTERM/SIGINT drain | `agent/worker.py` | Active |
| Firestore client | Firebase Admin SDK wrapper — claim, status updates, batched writes | `agent/firestore_client.py` | Active |
| Graph runner | Thin exception wrapper around `graph.invoke()`; builds initial state; `_mark_failed` | `agent/process_query.py` | Active |
| Graph definition | `StateGraph` compile (module-level singleton) | `agent/graph.py` | Active |
| Forecast state | `ForecastState` TypedDict (`total=False`) — the inter-node contract | `agent/state.py` | Active |
| Node 0 — claim_session | Atomic claim; status claimed → running | `agent/nodes/claim_session.py` | Active |
| Node 1 — query_understand | GPT-4o-mini classification; intent, tier signal, candidates; ambiguity detection | `agent/nodes/query_understand.py` | Active |
| Branch — write_clarification | Writes `clarificationCandidates`; status=awaiting_clarification; ends graph | `agent/nodes/write_clarification.py` | Active |
| Node 2 — build_embedding | OpenAI `text-embedding-3-small` (1536-dim) | `agent/nodes/build_embedding.py` | Active |
| Node 3 — vault_query | Parallel dispatch of the 3 retrieval agents | `agent/nodes/vault_query.py` | Active |
| rate_evidence | Normalize to unified `EvidenceItem[]`; GPT-4o-mini relevance scoring | `agent/nodes/rate_evidence.py` | Active |
| Node 6 — synthesize | GPT-4o reasoning → probability, key_factors, gaps; tier inference | `agent/nodes/synthesize.py` | Active |
| Node 6.5 — generate_suggested_actions | GPT-4o-mini → 3 contextual `{id,label,prompt}` suggested actions after synthesize; born-instrumented; degrades to `[]`/event `failed`, never fails the forecast (G4) | `agent/nodes/generate_suggested_actions.py` | Active (Sprint 25) |
| Node 7 — write_to_firestore | Persist SessionResult (incl. `suggestedActions`) + subcollections; §3-D pre-`done` drain; status=done | `agent/nodes/write_to_firestore.py` | Active |
| sufficiency_check | Evaluates retrieved evidence vs. intent; writes `sufficiency_checks` + `missing_dimensions`; routes sufficient → rate_evidence, insufficient → trigger | `agent/nodes/sufficiency_check.py` | Active (built + wired in Sprint 23.5, closed 2026-06-20 — closed KG-B-15) |
| trigger_reactive_ingestion | Emits Kafka `ingestion_triggers` message to NewsAPI producer (trigger-and-forget) | `agent/nodes/trigger_reactive_ingestion.py` | Active (`.run` interface fix + graph wiring landed in Sprint 23.5, closed 2026-06-20 — closed KG-B-16) |
| LLM cost helper | Central model→price table → `cost_usd`; fed by all LLM call sites | `agent/utils/llm_cost.py` | Active (Sprint 23.5, closed 2026-06-20 — closed KG-B-6) |
| agentEvents emitter | Non-blocking chain-of-thought stream (MAIN graph only): per-`runId` guarded registry + lock-guarded sequence + FIFO background writer; `@events.emits` decorator on the main-graph pair-nodes **except `write_to_firestore` and `generate_suggested_actions`, which emit their event pairs manually** (each self-observing for the T26.4 `agent_node_duration_seconds` histogram; `generate_suggested_actions` emits manually so its degrade path reports `failed` vs `done`) + `claim_session` one-shot bootstrap; `run_id` (state) / `currentRunId` (session doc); `emit`/`complete`/`fail`/`drain`/`dispose_run`; fire-and-forget (write failures swallowed) | `agent/events.py` (+ `firestore_client.write_agent_event`) | Active (Sprint 25) |
| The Researcher | knowledge_vectors + knowledge_vault retrieval → `ResearcherEvidence` | `agent/agents/researcher.py` | Active |
| The Pulse Analyst | social_vectors + social_vault retrieval → `PulseEvidence` | `agent/agents/pulse_analyst.py` | Active |
| The Market Bridge | momentum_vault + mapping_dict; pg_trgm Polymarket fuzzy-match → `MarketEvidence` | `agent/agents/market_bridge.py` | Active |
| Vault tool wrappers | Thin `persistence/` wrappers — the only place the hub touches the vaults (§3.3 isolation) | `agent/tools/{knowledge,social,market,mapping}_tools.py` | Active |
| Vault-read retry | Shared tight-profile retry (3 attempts / 0.5s→2.0s backoff, fits `vault_query`'s 15s per-agent budget) wrapping the vault-read tool functions | `agent/tools/_retry.py` | Active (Sprint 26) |
| Prompts | Query-understanding, evidence-rating, synthesis-lead system prompts + schemas | `agent/prompts/*.py` | Active |
| Schemas | `EvidenceItem` + synthesis Pydantic models | `agent/schemas.py` | Active |
| Label derivation | Deterministic confidence/consensus/volume labels (0.5/0.8 thresholds) | `agent/labels.py` | Active |
| Sentiment bucketing | Generic time-bucketing for sentimentTimeSeries (Sprint 22) | `agent/utils/sentiment_bucketing.py` | Active |
| Recency helper | Shared recency scoring | `agent/utils/recency.py` | Active |
| Errors | `AgentProcessingError` + typed subclasses | `agent/errors.py` | Active |
| Hub config | Hub env vars (separate from pipeline `config/`) | `agent/config/settings.py` | Active |
| Health server | `/health` + real `/metrics` (Prometheus exposition via `generate_latest()` — replaced the Sprint-25 stub in Sprint 26; internal monitoring only) | `agent/health.py` | Active |
| Prometheus metrics | 3 metrics on the default registry — `agent_node_duration_seconds` histogram (via `@events.emits` + a manual `.observe()` in the two manual-emit nodes `write_to_firestore` / `generate_suggested_actions`), `agent_llm_cost_usd_total`, `agent_session_total{tier,status}`; scraped via the real `/metrics` | `agent/metrics.py` | Active (Sprint 26) |
| Reactive triggers log | Postgres audit table for emitted triggers (debugging + cost attribution) | `persistence/reactive_triggers_log.py` | Active (Sprint 23) |
| Follow-up subgraph | Answer-from-context chat follow-ups (`load_context → answer_from_context → write_message`); no escalation | `agent/followup/{graph,state,nodes/*}.py` | Active (Sprint 24) |
| Follow-up listener + sweep | 2nd Firestore collection-group listener on `messages` (`role=='user' AND status=='sent'`) + done-transition safety-net sweep; atomic `sent→answered` claim | `agent/followup/listener.py` | Active (Sprint 24) |
| Follow-up prompt | Classify-and-answer prompt + fixed transparent/redirect copy | `agent/prompts/followup.py` | Active (Sprint 24) |
| Reactive search microservice (Tavily/Brave) | Spec §8.12 external-search second-pass | — | Not built — deferred indefinitely (Future Enhancement 1). Distinct from the `sufficiency_check` node above, which **was** built in Sprint 23.5 |

---

## §4 — Phase & Sprint Status

The full sprint-status table (18–27, with closed/open status and the per-sprint
Plan-file links) lives in **`hub_sprints.md §1`** — that is the single source of
sprint status; this overview no longer duplicates it.

> Phase naming: Phase 8 = Agentic Hub; sprint numbers 18–27. The 2026-05-23 revision
> deferred the reactive-search microservice and split pre-test vs. post-test scope;
> the 2026-06-18 audit added the (now-closed) Sprint 23.5 remediation. The full
> "why + order" is in `hub_sprints.md` (Rationale / Phase-8 Context).

---

## §5 — References — Domain-B navigation map

Start here, then route by what you need:

- **Active plan / what to implement now** → `hub_sprints.md §1` (the **Plan-file**
  column). Sprints 18–26 are **closed**; the current NEXT is the **initial cloud test**
  (gated on Sprint 26, now unblocked), then Sprint 27
  (`plans/sprint27_post_test_closeout.md`, partial/stub). Each `plans/` file is
  self-contained: scope, design decisions, and the `[ ]` task table.
  > `plans/sprint24_followups.md`, `plans/sprint25_suggested_actions.md`, and
  > `plans/sprint26_pretest_hardening.md` are all **closed** (2026-07-04 / 2026-07-16 /
  > 2026-07-18) and now **archived** in `archive_plans/`. Sprint 24's plan was held in
  > `plans/` past its close because Sprint 25 (T25.7) extended its follow-up nodes and
  > KG-B-17 pointed into its §3 budget bullet — so the two archived together when
  > Sprint 25 closed.
- **Phase-8 rationale + sprint order / dependencies** → `hub_sprints.md`
  (Rationale / Phase-8 Context).
- **How the hub actually works** — the LangGraph state machine (graph, nodes, edges,
  routing, the three retrieval agents and their vault queries / evidence shapes, the
  Firestore contract, edge cases) → `hub_agents.md`.
- **Known Gaps (KG-B-*)** → `hub_sprints.md §4`; **Deferred items** →
  `hub_sprints.md §3`.
- **Closed work** → `hub_archive.md` (closed Sprints 18–26 + the 15 spec patches),
  plus `archive_plans/` for retired plan files — including the closed Sprint 23.5
  remediation (`archive_plans/sprint23_5_pre26_remediation.md`).

Cross-domain / spec:
- `pipeline_overview.md` (Domain A) — the vaults, schemas, and the pipeline-side reactive trigger consumer the hub builds on.
- `docs/old_docs/agentic_hub_spec.md` — the original architectural spec (§8); §8.3.2 / §8.3.3 / §8.5.3 / §8.8.3 / §8.12 are partially superseded — see `hub_sprints.md` (Rationale / Phase-8 Context, "Spec sections affected").
