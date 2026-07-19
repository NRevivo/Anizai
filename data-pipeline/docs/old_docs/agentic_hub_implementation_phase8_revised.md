# Agentic Hub Implementation — Phase 8 Revised Plan
## Anizai Project | Sprints 22-27 (Pre-Initial-Test Path)

---

## How to use this document

This is the **active implementation plan** for the remaining work on the Agentic
Hub (Phase 8). It supersedes the task tables for Sprints 22–26 in
`agentic_hub_implementation.md` — that document still holds the original task
tables for Sprints 18–21 (closed) for historical reference, but its Sprints 22–26
sections are now superseded and point here.

It should be loaded by Claude Code at the start of every Sprint 22–27 session,
alongside:
- `agentic_hub_spec.md` (architectural specification; some sections superseded — see "Spec Sections Affected" below)
- `agentic_hub_implementation.md` (original Phase 8 plan; load for Sprints 18–21 context only)
- `anizai_handoff_consolidated.md` (frontend contract)
- `task_plan.md` (active sprint tracker)
- The relevant skills per sprint (listed at the bottom)

The data-pipeline sprint conventions still apply:
- Conventional commits with section references
- Update `task_plan.md` after every completed task
- All work inside `data-pipeline/`
- No code without an approved implementation plan
- The four-gate testing model (Gate 1 unit / Gate 2 subgraph / Gate 3 Firestore emulator / E2E real)

---

## Why this revision exists

Three drivers converged in late May 2026 to require a re-plan of Sprints 22–26:

**1. Cost concerns surfaced by Phase 9.5.** KG-PHASE-9.5-9 (parallel
OpenAI cost-analysis session) showed the OpenAI usage model needs re-baselining
before adding new paid surfaces. The original Sprint 22–23 plan was a fresh
reactive-search microservice built on Tavily/Brave APIs — not justifiable until
the cost picture clears.

**2. NewsAPI provider upgrade.** Phase 7A migrated NewsAPI to newsapi.ai with
full article body (`articleBodyLen=-1`). The vault now ingests full text, which
covers a meaningful portion of what reactive search was designed to fetch.
Reactive ingestion via the existing producer becomes a viable alternative to
external search APIs.

**3. Initial-test phase is the immediate goal.** Ron's near-term target is a
two-day live cloud run where producers operate end-to-end and the agent runs
real forecasts, primarily to baseline real production costs and to see actual
forecast output quality with real (not seed) vault data. Everything in the
plan is now prioritized against the question "is this needed to make initial
testing meaningful, or can it ship later?"

The revised plan adds two new sprints (22 Revised + 23) and reorders the rest
to converge on a hardened-enough agent for the two-day initial test, then a
focused polish/closeout sprint (27) afterward.

---

## Revised Phase 8 Overview

| Sprint | Focus | Status | Definition of Done |
|---|---|---|---|
| 22 (Revised) | Foundation Fixes — writeToFirestore + Synthesize wiring + Polymarket fuzzy match | Open | All 5 BI cards render with real data on Tier 1 forecasts where question matches a Polymarket market by trigram similarity |
| 23 (New) | Producer-trigger Infrastructure (replaces original Reactive Search Microservice) | Open | Agent can emit a Kafka trigger to NewsAPI producer with `run_reactive()`, producer pulls targeted articles, vault is enriched on next session. Trigger-and-forget. |
| 24 | Follow-up Conversations (Revised — no escalation) | Open | User can ask follow-ups in chat panel; agent answers from existing SessionResult + evidence context only |
| 25 | Suggested Actions + Chain-of-Thought Events | Open | 3 suggested actions per forecast + agentEvents stream for real-time reasoning panel |
| 26 | Pre-Test Hardening | Open | Critical observability + retry + version tracking in place before two-day initial test |
| 27 (New) | Post-Test Polish + Phase 8 Closeout | Not planned (after initial test) | Everything deferred from original Sprint 26; Phase 8 State Ledger; archive |

**Pre-test path:** Sprint 22 → 23 (parallel-able) → 24 → 25 → 26 → **two-day initial test** → 27.

---

## Implementation Order & Parallelization

Most sprints touch independent code areas. Three pairs touch overlapping
surfaces and are best run sequentially to avoid merge conflicts in
`agent/nodes/write_to_firestore.py`, `agent/nodes/synthesize.py`, and
`agent/graph.py`.

### Dependency map

```
┌────────────────────────────────────────────────────────────────┐
│  Stage 1 — Parallel-able                                        │
│  ┌──────────────────────┐    ┌──────────────────────┐          │
│  │ Sprint 22 (Revised)  │    │ Sprint 23 (New)      │          │
│  │ Foundation Fixes     │    │ Producer-trigger     │          │
│  │ (writeToFS+synth.)   │    │ Infrastructure       │          │
│  │ 3-4 days             │    │ 2-3 days             │          │
│  └──────────────────────┘    └──────────────────────┘          │
└────────────────────────────────────────────────────────────────┘
                ▼                              │
┌────────────────────────────────────┐         │
│  Stage 2 — Sequential (depends 22) │         │
│  ┌──────────────────────┐          │         │
│  │ Sprint 24            │          │         │
│  │ Follow-ups (Revised) │          │         │
│  │ 2-3 days             │          │         │
│  └──────────────────────┘          │         │
│           ▼                        │         │
│  ┌──────────────────────┐          │         │
│  │ Sprint 25            │          │         │
│  │ Suggested + Events   │          │         │
│  │ 3-4 days             │          │         │
│  └──────────────────────┘          │         │
└────────────────────────────────────┘         │
                ▼                              │
┌────────────────────────────────────────────────────────────────┐
│  Stage 3 — Convergence and Hardening                            │
│  ┌──────────────────────────────────────────────────┐          │
│  │ Sprint 26 — Pre-Test Hardening                   │          │
│  │ + Wire `trigger_reactive_ingestion` node into    │          │
│  │   graph (requires both Sprint 22 and 23)         │          │
│  │ 2-3 days                                          │          │
│  └──────────────────────────────────────────────────┘          │
└────────────────────────────────────────────────────────────────┘
                ▼
        ┌──────────────────┐
        │ INITIAL TEST     │
        │ ~2 days, cloud   │
        └──────────────────┘
                ▼
┌────────────────────────────────────┐
│  Sprint 27 — Post-Test Polish      │
│  Remaining T26.* items + closeout  │
└────────────────────────────────────┘
```

### Hard dependencies (must finish first)

- **Sprint 22 blocks 24, 25, 26.** They consume the SessionResult shape that
  Sprint 22 finalizes (marketProbability wired, predictionSeries populated,
  sentimentTimeSeries populated, fuzzy-matched market data threaded through
  synthesize).
- **Sprint 23 blocks the `trigger_reactive_ingestion` node being wired into
  the graph.** It does NOT block Sprints 24 or 25 themselves; the wiring is
  a small task that happens at the end of Sprint 26.
- **Sprint 24 blocks Sprint 25.** Sprint 25 (T25.7) updates follow-up nodes
  to emit agentEvents — those nodes only exist after 24.

### Soft dependencies (preferred order, not strict)

- Sprint 22 and 23 can run in parallel by different sessions/contributors.

---

## Spec Sections Affected

These sections of `agentic_hub_spec.md` are partially superseded by this
revision. The spec was not edited in place — load this document for the
current-of-record plan.

| Spec Section | Status | Successor |
|---|---|---|
| §8.3.2 Graph Topology — `sufficiency_check → reactive_search` branch | Superseded for V1 | Sprint 23 + Sprint 26 wiring: `sufficiency_check → trigger_reactive_ingestion` (trigger-and-forget) |
| §8.3.3 `sufficient?` routing | Partially superseded | The "reactive_search" target is replaced by "trigger_reactive_ingestion (fire-and-forget)" |
| §8.5.3 Reactive ingestion loop note | Reversed | The spec said the Kafka-based loop is "replaced by the inline reactive search microservice"; this revision restores the Kafka loop (Sprint 23) as the V1 path |
| §8.8.3 Follow-up escalation path | Deferred | Sprint 24 implements `answer_from_context` only; escalation moves to Future Enhancements |
| §8.12 Reactive Search Microservice (entire section) | Deferred indefinitely | Replaced by Sprint 23 producer-trigger approach; spec section retained as design reference for the Future Enhancement |

---

## Sprint 22 (Revised) — Foundation Fixes

### Sprint scope

Consolidate every change to `agent/nodes/write_to_firestore.py`,
`agent/nodes/synthesize.py`, and `agent/agents/market_bridge.py` into one
sprint to avoid merge conflicts with downstream sprints. Sprint 22 produces a
SessionResult that finally populates **all five BI cards** with real data on
Tier 1 forecasts.

Three independent fixes ship together:

**A. Polymarket fuzzy match resolver.** Agent today never resolves to a Polymarket
market because there is no slug-resolution mechanism (KG-PHASE8-12 — see
`task_plan.md` Known Gaps). Every Tier 1 question runs as effective Tier 2.
Fix: `pg_trgm`-based fuzzy match between the user's `raw_question` and the
`question` field of Polymarket markets stored in `momentum_vault.metadata_extension`.

**B. marketProbability + marketComparison + predictionSeries wiring.** Once (A)
yields `market_evidence.polymarket.current_odds`, thread it through `synthesize`
into `SessionResult.marketProbability` and into the `predictionSeries`
subcollection from `market_evidence.polymarket.price_history`.

**C. sentimentTimeSeries generation.** Bucket the agent's already-retrieved
evidence into 14-day time series, one line per source category (Expert from
`knowledge_vectors`, Public from `social_vectors`). Closes KG-PHASE8-22.

### Confirmed design decisions

- **pg_trgm threshold 0.85.** Catches punctuation/word-order variations
  without admitting paraphrases. Initial value; tune after seeing live data.
- **`pg_trgm` extension assumed present** (it's required elsewhere in the
  project). Add `CREATE EXTENSION IF NOT EXISTS pg_trgm;` to `init.sql` if a
  fresh database doesn't already have it.
- **`bucket_sentiment_by_time()` is generic** — accepts the items list, the
  sentiment field name, and the timestamp field name. Same function backs both
  Expert and Public lines, and will back future enrichment-based sources without
  signature change.
- **Tier 2 `predictionSeries` is intentionally empty.** Frontend renders empty
  state ("No prediction history — freeform analysis"). Future option (see
  Future Enhancements): populate with the agent's own probability snapshots
  across delta-refresh runs.
- **`canonicalKey` written to session doc.** Today's code writes it only to
  sessionResult. Wire it to session doc as well — zero-cost infrastructure for
  the future cross-user cache feature.

### Task table

| Task | Status | Description | Gate(s) | Spec Reference |
|------|--------|-------------|---------|----------------|
| 22.1 | `[x]` | Add `find_polymarket_market_by_question(question_text, threshold=0.85)` to `persistence/momentum_vault.py`. Uses `pg_trgm` similarity on `metadata_extension->>'question'`. Returns most-recent matching market row or `None`. | Gate 1 | §8.4.3 (revised) |
| 22.2 | `[x]` | Update `agent/agents/market_bridge.py` to call (22.1) when `structured_intent.has_market_question_intent`. On hit, populate `market_evidence.polymarket` with `current_odds`, `momentum`, `price_history` (via `fetch_time_series(hours=720)`), and `market_slug`. On miss, leave as `None` (Tier 2 behavior preserved). | Gate 1, Gate 2 | §8.4.3 (revised) |
| 22.3 | `[x]` | Update `agent/nodes/synthesize.py` `_build_session_result()` to thread `market_evidence.polymarket.current_odds` → `SessionResult.marketProbability`, and build `marketComparison` array from agent's `finalProbability` + market's `current_odds`. Replaces the hardcoded `marketProbability=None` and `marketComparison=[]`. | Gate 1, Gate 2 | §8.6, §8.7.2 |
| 22.4 | `[x]` | Update `agent/nodes/write_to_firestore.py` to write `predictionSeries` subcollection from `market_evidence.polymarket.price_history` when present. Tier 2: write empty (frontend handles empty state). | Gate 1, Gate 2 | §8.7.1 |
| 22.5 | `[x]` | Add `agent/util/sentiment_bucketing.py` with `bucket_sentiment_by_time(items, sentiment_field, time_field, window_days=14, bucket_days=1)` — generic time-bucketing returning list of `{date, avg_sentiment, sample_count}` dicts. Designed so future enrichment sources plug in without signature change. | Gate 1 | KG-PHASE8-22 (closes), §8.7.1 |
| 22.6 | `[x]` | Update `agent/nodes/write_to_firestore.py` to write `sentimentTimeSeries` subcollection — Expert line from `researcher_evidence.articles[*].sentiment_score`+`published_at`, Public line from `pulse_evidence.community_discussion[*].community_sentiment`+`published_at`. Use (22.5) helper for both. | Gate 1, Gate 2 | KG-PHASE8-22 (closes), §8.7.1 |
| 22.7 | `[x]` | Update `agent/nodes/write_to_firestore.py` to write `canonicalKey` to the session doc (not only to sessionResult). | Gate 1 | §8.7.1 |
| 22.8 | `[x]` | Gate 1 tests: unit tests for fuzzy match (with fixture markets), bucketing helper (empty/single/multi-bucket scenarios), wiring transitions. | Gate 1 | §9.3 Gate 1 |
| 22.9 | `[x]` | Gate 2 tests: subgraph integration test running market_bridge → rate_evidence → synthesize → write_to_firestore. Verify all 5 BI cards' data lands correctly. | Gate 2 | §9.3 Gate 2 |
| 22.10 | `[x]` | Gate 3 tests: against Firestore emulator. Verify all subcollections populated correctly. | Gate 3 | §9.3 Gate 3 |
| 22.11 | `[x]` | E2E test: real environment. Question matching a current Polymarket market verbatim. Verify MarketComparison renders, predictionSeries populates, sentimentTimeSeries shows both lines. | E2E | §9.3 E2E |

### Notes on what Sprint 22 does NOT do

- Does **not** build a Polymarket vector index. Future Enhancement (see below).
- Does **not** implement a clarification flow when multiple markets fuzzy-match
  the question. Today's code returns the most-recent matching row; if multiple
  markets pass the threshold, only one is selected. Future Enhancement.
- Does **not** add new sentiment enrichment passes. Bucketing operates on
  sentiment scores already computed by the Gold layer.

---

## Sprint 23 (New) — Producer-trigger Infrastructure

### Sprint scope

Add a path for the agent to **trigger ingestion of targeted articles** when the
vault is insufficient, replacing the original Sprints 22–23 plan for a Tavily/Brave
reactive search microservice. The path uses the existing `ingestion_triggers`
Kafka topic (added in Sprint 13, consumer running since Sprint C4) and extends
it to cover the NewsAPI producer.

**V1 mode: trigger-and-forget.** Agent emits the trigger and continues
synthesizing with whatever evidence is currently available. Fetched articles
land in the vault asynchronously (Bronze → Silver → Gold → vault), available
for the next session. Rationale: full pipeline propagation can take 30s+, far
exceeding the 30s p95 NFR for a forecast. See Future Enhancements for the
polling/wait variant.

### Confirmed design decisions

- **NewsAPI only as the V1 trigger target.** Telegram / ArXiv / Polymarket
  reactive support can be added by registering them in the same pattern (see
  Future Enhancements) but is not in scope here.
- **Trigger decision uses no LLM call.** The new `trigger_reactive_ingestion`
  node assembles keywords from `structured_intent.entities` **only**
  (**amended by Sprint 23.5 / R1** — `missing_dimensions` and `raw_question`
  are excluded; the keyword set is entities-only, bounded by the 7-day recency
  window. See `docs/B_hub/sprint23_5_pre26_remediation.md` §2/R1 for the
  rationale: the gap closed is recency, not topic). No extra GPT-4o-mini call.
  A `missing_dimensions`-shaped / LLM-refine path is a Future Enhancement.
- **Hard rate limit: one trigger per session.** Config:
  `AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION=1`. Naming mirrors the original
  reactive_search config so a future swap is clean.
- **Minimal trigger log table.** New `reactive_triggers_log` Postgres table —
  session_id, trigger_time, keywords, source, kafka_offset, status. Purpose:
  debugging during initial testing; basis for cost attribution. No polling
  state column (trigger-and-forget).

### Task table

| Task | Status | Description | Gate(s) | Spec Reference |
|------|--------|-------------|---------|----------------|
| 23.1 | `[x]` | Add `run_reactive(keywords: list[str], time_window_days: int = 7)` method to `ingestion/newsapi_producer.py`. Uses existing `_fetch_articles()` with the `keyword=` query parameter (newsapi.ai supports this). Emits to BRONZE_NEWSAPI. Returns count of articles emitted. | Gate 1 | §2.4 (data-pipeline spec) |
| 23.2 | `[x]` | Update `orchestration/ingestion_trigger_consumer.py`: register `newsapi` in `VALID_SOURCES`, add to `_REQUIRED_LIST_FIELDS` (required field: `keywords`, optional: `time_window_days`), add dispatch branch in `dispatch()`, add lazy import in `_build_producer()`. | Gate 1 | §2.4 |
| 23.3 | `[x]` | Add `reactive_triggers_log` table to `infrastructure/sql/init.sql`. Columns: `trigger_id UUID PK`, `session_id TEXT`, `trigger_time TIMESTAMPTZ`, `keywords JSONB`, `source TEXT`, `kafka_offset BIGINT NULLABLE`, `status TEXT` (`emitted`/`failed`). Index on `(session_id, trigger_time)`. | Gate 1 | (new) |
| 23.4 | `[x]` | Add `persistence/reactive_triggers_log.py` — `insert(session_id, source, keywords, kafka_offset, status)` and `list_by_session(session_id)`. | Gate 1, Gate 3 | (new) |
| 23.5 | `[x]` | Implement `agent/nodes/trigger_reactive_ingestion.py` — builds payload dict from `state.structured_intent.entities` **only** (amended by Sprint 23.5 / R1: `missing_dimensions` + `raw_question` excluded; entities-only + 7-day recency window). Emits Kafka message to `INGESTION_TRIGGERS`. Logs to `reactive_triggers_log`. Increments counter on state. Does NOT wait for completion. (Entry point renamed `→ run(state)` in 23.5.3.) | Gate 1, Gate 2 | §8.3.2 (revised) |
| 23.6 | `[x]` | Add `AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION=1` to `config/settings.py`. Add `reactive_triggers_emitted: int` to `ForecastState`. | Gate 1 | §8.3.1, §8.11 |
| 23.7 | `[x]` | Gate 1 tests: unit tests for `run_reactive()`, trigger consumer dispatch, payload builder in the new node, rate-limit enforcement. | Gate 1 | §9.3 Gate 1 |
| 23.8 | `[x]` | Gate 2 tests: subgraph test with mocked Kafka producer — verify node emits correct payload and updates state correctly. | Gate 2 | §9.3 Gate 2 |
| 23.9 | `[x]` | Gate 3 tests: against real Kafka + Postgres — emit a trigger, verify consumer dispatches it, verify producer fetches at least one article, verify log row written. (Does not run full pipeline end-to-end.) **Implemented; verification deferred to Linux CI / cloud initial-test runtime — Windows-local `skipif` due to kafka-python-ng bootstrap→coordinator selector race; see KG-PHASE8-25 (revised).** | Gate 3 | §9.3 Gate 3 |
| 23.10 | `[ ]` | E2E test: full session run with a question designed to leave the vault insufficient. Verify trigger emitted, articles fetched, log row created. Articles availability in vault verified on a fresh session run after pipeline propagation (manual step). **Deferred to Sprint 26 — depends on T26.7 graph wiring (the node `trigger_reactive_ingestion` exists in isolation but is not yet wired into `agent/graph.py`). E2E test must run against the wired graph. Closing as deferred 2026-05-26 with Sprint 23. See T26.10.5 for the verification rows that absorb T23.10's scope.** | E2E | §9.3 E2E |

### Notes on what Sprint 23 does NOT do

- Does **not** wire `trigger_reactive_ingestion` into the actual graph topology.
  That wiring is done at the end of Sprint 26 after both Sprint 22 and Sprint 23
  are complete. Until then, the node is built and tested in isolation.
- Does **not** implement polling-and-wait. Future Enhancement.
- Does **not** add Telegram / ArXiv / Polymarket reactive paths. Future Enhancement.

---

## Sprint 24 — Follow-up Conversations (Revised)

### Sprint scope

Implement chat follow-ups on completed forecasts. **No escalation** — answers
are constructed exclusively from the parent SessionResult and the top evidence
items already retrieved during the original forecast. When the existing context
is insufficient, the agent returns a transparent message ("can't answer with
high confidence... improvements pending escalation enablement") rather than
fabricating a partial answer.

The original Sprint 24 plan (`agentic_hub_implementation.md`) included a branch
in `T24.4` that escalates to reactive search when the context is insufficient.
This revision implements only the simpler, cheaper "answer from context" path.
The escalation branch is reserved as a Future Enhancement that connects cleanly
to Sprint 23's `trigger_reactive_ingestion` node when re-enabled.

### Cost-instrumentation acceptance criterion (added by Sprint 23.5 / 23.5.11)

- **Born instrumented.** `answer_from_context` (the new GPT-4o-mini follow-up
  node) **must** route its token usage through `agent/utils/llm_cost.py`
  (`record_usage` / `compute_cost`) and contribute to `state`/`FollowupState`'s
  cost accumulator from its **first commit** — never retrofitted. The cost
  layer already exists (Sprint 23.5 Track 2); a new LLM node that does not call
  it is incomplete.

### Confirmed design decisions

- **`T24.4` renamed to `answer_from_context.py`.** Single GPT-4o-mini call.
  System prompt explicitly forbids re-running the forecast, revising the
  probability, or speculating beyond the evidence already in context.
- **Insufficient-evidence response is transparent.** "Based on this forecast's
  evidence, I can't answer with high confidence. This will improve once
  expanded retrieval is enabled." The phrasing intentionally hints at the
  Future Enhancement without exposing internal mechanics to a real user.
- **Follow-ups never modify the parent SessionResult.** Reinforces the "One
  Question, One Thread" rule (spec §8.2.3).
- **Budget: 6s for the LLM call (`AGENT_FOLLOWUP_BUDGET_MS=6000`); end-to-end
  reply target ≤7s** including `load_context` + the Firestore write. The 6s
  value is the answer-node call budget; the extra ~1s is context-load +
  write/delivery overhead. Gate 3 (24.12) asserts the ≤7s end-to-end target so
  the test threshold and the call budget stay consistent. Tighter than the main
  forecast (~15-30s).
- **Timeout returns a complete message with a caveat, not a truncated reply.**
  Per handoff §6.1 / §7's degradation contract: on budget overrun the agent
  writes a full assistant message whose *content* carries an "I had to stop
  early" caveat and `status: "complete"` — the frontend never has to render a
  partial/streaming follow-up.
- **Follow-up processing is idempotent (added 2026-06-27).** The `messages`
  listener must answer each user follow-up **exactly once**. Firestore re-delivers
  all matching docs on listener (re)attach, and a worker restart would otherwise
  re-answer every historical follow-up. The guard (24.14) skips any user message
  that already has a later `role=='assistant'` reply. This mirrors the atomic
  claim that protects the main `forecastQueries` path.
- **`FollowupState` carries cost (added 2026-06-27).** `FollowupState` includes
  `total_cost_usd: float` so `answer_from_context` can satisfy the Sprint 23.5 /
  23.5.11 "born instrumented" criterion — the accumulator the acceptance
  criterion refers to must actually exist on the state.
- **Complete-message responses, no streaming.** Token-by-token streaming
  deferred (handoff doc §7).

### Task table

| Task | Status | Description | Gate(s) | Spec Reference |
|------|--------|-------------|---------|----------------|
| 24.1 | `[ ]` | Implement `agent/followup/listener.py` — second Firestore listener on `messages` subcollection writes where `role == 'user'` AND parent `session.status == 'done'`. Triggers the follow-up subgraph. **Must fire only for *unanswered* user messages — see the idempotency guard in 24.14.** | Gate 1 | §8.8.3 (revised) |
| 24.2 | `[ ]` | Implement `agent/followup/state.py` — `FollowupState` TypedDict: `parent_session_id`, `message_history`, `parent_session_result`, `parent_evidence` (top 5), `response_text`, **`total_cost_usd: float`** (the cost accumulator the 23.5.11 "born instrumented" criterion writes into; see 24.4). (Removed from original plan: `needs_escalation`, `escalation_results`.) | Gate 1 | §8.8.3 (revised) |
| 24.3 | `[ ]` | Implement `agent/followup/nodes/load_context.py` — loads parent SessionResult, top 5 evidence items by rank, last 10 messages from history. | Gate 1 | §8.8.3 (revised) |
| 24.4 | `[ ]` | Implement `agent/followup/nodes/answer_from_context.py` (revised T24.4) — GPT-4o-mini call. System prompt explicitly forbids re-running the forecast, revising probability, or fetching new evidence. On insufficient evidence, returns the transparent message. **Born instrumented (23.5.11): route token usage through `agent/utils/llm_cost.py` (`record_usage` / `compute_cost`) and accumulate into `FollowupState.total_cost_usd` from the first commit.** | Gate 1, Gate 2 | §8.8.3 (revised) |
| 24.5 | `[ ]` | Implement `agent/followup/nodes/write_message.py` — writes assistant message to `messages` subcollection with `role: "assistant"`. | Gate 1 | §8.8.3 (revised) |
| 24.6 | `[ ]` | Implement `agent/followup/graph.py` — small LangGraph: `load_context → answer_from_context → write_message`. No escalation branch. | Gate 2 | §8.8.3 (revised) |
| 24.7 | `[ ]` | Implement `agent/prompts/followup.py` — system prompt encoding the no-revise / no-fetch / transparent-insufficiency constraints. | Gate 1 | §8.8.3 (revised) |
| 24.8 | `[ ]` | Update `agent/worker.py` — initialize the follow-up listener alongside the main listener. Both run concurrently. | Gate 1 | §8.8.1 |
| 24.9 | `[ ]` | Implement budget enforcement (`AGENT_FOLLOWUP_BUDGET_MS=6000` — the answer-node call budget). On overrun, write a **complete** assistant message whose content carries a degradation caveat with `status: "complete"` (per handoff §6.1 — not a truncated reply). End-to-end reply target ≤7s (6s call budget + context-load/write overhead), consistent with the 24.12 Gate 3 threshold. | Gate 1 | §8.8.3 (revised) |
| 24.10 | `[ ]` | Gate 1 tests: unit tests for each follow-up node (mocked LLM, mocked parent context loading). | Gate 1 | §9.3 Gate 1 |
| 24.11 | `[ ]` | Gate 2 tests: integration test of follow-up graph. Both branches: sufficient context (real answer) and insufficient context (transparent message). | Gate 2 | §9.3 Gate 2 |
| 24.12 | `[ ]` | Gate 3 tests: against Firestore emulator. Submit a forecast, wait for done, submit a follow-up message, verify assistant reply appears within the ≤7s end-to-end target (consistent with the 6s call budget + overhead per 24.9). | Gate 3 | §9.3 Gate 3 |
| 24.13 | `[ ]` | E2E test: real environment. Run a forecast, then ask three follow-ups (two clearly answerable from context, one requiring evidence the agent didn't retrieve). Verify the third gets the transparent message. | E2E | §9.3 E2E |
| 24.14 | `[ ]` | **Idempotency guard (added 2026-06-27).** The follow-up listener must process each user message **exactly once**. On listener (re)attach Firestore re-delivers all matching docs, and a worker restart would otherwise re-answer historical follow-ups. Guard: before invoking the subgraph, skip any user message that already has a later `role=='assistant'` reply in `messages` (or mark the message processed). Verify no double-answers across a simulated listener re-attach / worker restart. Mirrors the atomic-claim protection on the main `forecastQueries` path. | Gate 1, Gate 3 | §8.8.1 |

---

## Sprint 25 — Suggested Actions + Chain-of-Thought Events

### Sprint scope

Add the two streaming/dynamic UI features deferred from earlier sprints:

1. **Suggested actions** — 3 contextual follow-up suggestions per forecast.
2. **Chain-of-thought events** — continuous `agentEvents` stream during
   processing, displayed in real time by the frontend reasoning panel.

This sprint is essentially **unchanged from the original plan** in
`agentic_hub_implementation.md`. The only adjustment: T25.7 (updating
follow-up nodes to emit events) refers to the revised follow-up nodes from
Sprint 24.

### Cost-instrumentation acceptance criterion (added by Sprint 23.5 / 23.5.11)

- **Born instrumented.** `generate_suggested_actions` (the new GPT-4o-mini node
  after `synthesize`) **must** route its token usage through
  `agent/utils/llm_cost.py` (`record_usage` / `compute_cost`) and contribute to
  `state.total_cost_usd` from its **first commit** — never retrofitted. The
  central cost layer landed in Sprint 23.5 (Track 2) precisely so this and
  `answer_from_context` plug in without re-implementing cost tracking.

### Confirmed design decisions

(Unchanged from original Sprint 25 — see `agentic_hub_implementation.md` for
the original list. Repeated here for self-containment.)

- Suggested actions generated by an extra GPT-4o-mini call after `synthesize`,
  to keep main synthesis lean.
- Schema: `{id, label, prompt}`. Frontend uses one default icon for all (V1
  "simpler dynamic" agreement).
- agentEvents emitted continuously; sequence ordered by an autoincrement
  counter on state.
- agentEvents writes are fire-and-forget; write failures are logged and
  swallowed, never failing the agent.

### Task table

(Identical to original Sprint 25 task table in `agentic_hub_implementation.md`,
sections T25.1–T25.12. Repeated here in full for self-containment so this
document is the single source of truth.)

| Task | Status | Description | Gate(s) | Spec Reference |
|------|--------|-------------|---------|----------------|
| 25.1 | `[ ]` | Implement `agent/nodes/generate_suggested_actions.py` — node after `synthesize`. GPT-4o-mini call. Generates 3 contextual SuggestedAction items. | Gate 1, Gate 2 | §8.7.2 |
| 25.2 | `[ ]` | Implement `agent/prompts/suggested_actions.py` — system prompt. Clear labels over polished phrasing. | Gate 1 | §8.7.2 |
| 25.3 | `[ ]` | Update `agent/graph.py` — add `generate_suggested_actions` between `synthesize` and `write_to_firestore`. | Gate 2 | §8.3.2 |
| 25.4 | `[ ]` | Update `agent/nodes/write_to_firestore.py` — include `suggestedActions` in SessionResult. | Gate 1 | §8.7.2 |
| 25.5 | `[ ]` | Implement `agent/events.py` — helpers: `emit_event(state, type, title, payload, status='in_progress')`, `complete_event(state, event_id, duration_ms)`, `fail_event(state, event_id, error)`. Auto-increments sequence counter. | Gate 1 | §5.3 of consolidated handoff doc |
| 25.6 | `[ ]` | Update every existing node to emit start + complete events. | Gate 1 | §5.3 of consolidated handoff doc |
| 25.7 | `[ ]` | Update follow-up nodes (Sprint 24) to also emit events with `parentMessageId` set. | Gate 1 | §6.1 of consolidated handoff doc |
| 25.8 | `[ ]` | Implement event compaction job (optional, deferred to Sprint 27 if scope-pressed). | Gate 1 | §5.3 of consolidated handoff doc |
| 25.9 | `[ ]` | Gate 1 tests: unit tests for suggested actions generation, event emission helpers, sequence ordering. | Gate 1 | §9.3 Gate 1 |
| 25.10 | `[ ]` | Gate 2 tests: full graph run against mocked Firestore. Verify expected event sequence. | Gate 2 | §9.3 Gate 2 |
| 25.11 | `[ ]` | Gate 3 tests: full pipeline against Firestore emulator. Verify suggestedActions and agentEvents both appear. | Gate 3 | §9.3 Gate 3 |
| 25.12 | `[ ]` | E2E test: real environment. Confirm with partner that the chain-of-thought UI updates in real time. | E2E | §9.3 E2E |

---

## Sprint 26 — Pre-Test Hardening

### Sprint scope

Focused observability + retry + version tracking work needed before the
two-day initial test. Most of the original Sprint 26 task list moved to
Sprint 27 (post-test polish). This sprint includes only items that genuinely
gate meaningful initial-test results:

- KG-PHASE8-17 — cost tracking accuracy (logs token usage from synthesize and
  build_embedding nodes that today are silent)
- T26.6 — Prometheus metrics specific to the agent
- T26.7 — `agentVersion` with git short-hash for forensic tracking
- KG-PHASE8-16 — latency analysis (analysis only; fix in Sprint 27 or Phase 10)
- KG-PHASE8-20 — ClarificationCandidate hub-internal field cleanup
- T26.2 (partial) — Postgres retry wrapper on the agent's `momentum_vault` calls
- **Wire `trigger_reactive_ingestion` node from Sprint 23 into the graph**
  (the only task here that depends on Sprint 23)

### Confirmed design decisions

- **Latency analysis is per-node, not just total.** Without per-node
  attribution, the data is useless. Output is a written summary (in
  `task_plan.md` Known Gaps or its own doc) — not a code change.
- **OpenAI usage logging matches the existing `rate_evidence` pattern.**
  Same log format, same field names. Single source of cost truth across
  all OpenAI-calling nodes.
- **Postgres retry uses the existing `utils/retry.retry_on_transient()`
  helper from Phase 9.5 Stage B.** Zero new code in `utils/`. Only wraps
  the call sites in `market_bridge.py`, `researcher.py`, `pulse_analyst.py`.
- **OpenAI retry is already complete** (verified 2026-05-23 against
  `utils/openai_client.py`). No further work needed in Sprint 26.

### Task table

| Task | Status | Description | Gate(s) | Spec Reference |
|------|--------|-------------|---------|----------------|
| 26.1 | `[ ]` | Close KG-PHASE8-17 — add OpenAI usage logging to `agent/nodes/synthesize.py` and `agent/nodes/build_embedding.py` matching the `rate_evidence` pattern. Logs `model`, `prompt_tokens`, `completion_tokens`, `total_tokens`, computed `cost_usd`. | Gate 1 | §8.8.2 (patched) |
| 26.2 | `[ ]` | Close KG-PHASE8-20 — clean ClarificationCandidate writes in `agent/nodes/query_understand.py`. Either strip hub-internal fields (intent, domain, entities, polymarket_search_terms, polymarket_slug) before writing, or move them to a private subcollection. | Gate 1, Gate 2 | §8.2.4 (patched) |
| 26.3 | `[ ]` | Close KG-PHASE8-16 (analysis only) — write per-node timing instrumentation. Run T20.11 + T21.12 scenarios; produce a per-node-latency report in `task_plan.md` Known Gaps with classification per node: "token-volume-bound (expected)" vs "O(1) regression candidate". | — | KG-PHASE8-16 |
| 26.4 | `[ ]` | Implement T26.6 — Prometheus metrics on `/metrics` endpoint specific to the agent: `agent_node_duration_seconds` histogram (label: `node_name`), `agent_llm_cost_usd_total` counter (label: `model`), `agent_session_total` counter (label: `tier`, `status`), `agent_queue_depth` gauge. Reads from logs/state added by (26.1). | Gate 1 | §8.8.2 (patched) |
| 26.5 | `[ ]` | Implement T26.7 — `agentVersion` includes git commit short-hash. Set via build-time env var (`AGENT_GIT_COMMIT_SHORT_SHA`, read in `config/settings.py`). Format: `0.5.0-sprint26+<short-sha>`. Surfaced in SessionResult. | Gate 1 | §8.7.2 (patched) |
| 26.6 | `[ ]` | Implement T26.2 (partial) — wrap `persistence/momentum_vault.fetch_latest`, `fetch_time_series`, `fetch_fred_anomalies`, and `find_polymarket_market_by_question` call sites in agent code with `utils/retry.retry_on_transient(...)`. Same pattern as Gold's wrapping from Phase 9.5 Stage B Item 1b. | Gate 1 | §8.7.5 |
| 26.7 | `[ ]` | Wire `trigger_reactive_ingestion` (built in Sprint 23) into `agent/graph.py`. Routing: after `sufficiency_check` second attempt fails AND `state.reactive_triggers_emitted < AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION`, dispatch to `trigger_reactive_ingestion`, then continue to `synthesize` (trigger-and-forget). | Gate 2 | §8.3.2 (revised) |
| 26.8 | `[ ]` | Gate 1 tests: unit tests for new logging, version-string assembly, retry wrapping. | Gate 1 | §9.3 Gate 1 |
| 26.9 | `[ ]` | Gate 2 tests: subgraph test verifying trigger node fires on the insufficient path. | Gate 2 | §9.3 Gate 2 |
| 26.10 | `[ ]` | Gate 3 tests: E2E run with a question expected to be insufficient (e.g., very recent breaking event). Verify trigger emitted, log row created, synthesis proceeds with what was available. | Gate 3 | §9.3 Gate 3 |
| 26.10.5 | `[ ]` | E2E cycle-closes verification (absorbed from deferred Sprint 23 T23.10). After T26.10's upstream run completes, verify (a) the trigger's articles actually reached BRONZE_NEWSAPI (Kafka topic check, not just the log row), and (b) on a fresh session with the same question topic, the previously-fetched articles appear in the agent's evidence (manual two-session propagation check). Closes the trigger-and-forget cycle: trigger fires → producer fetches → vault enriched → next session uses the new evidence. | E2E | §9.3 E2E |

### Notes on what Sprint 26 does NOT do (moved to Sprint 27)

See Sprint 27 task list.

---

## Sprint 27 (New) — Post-Test Polish + Phase 8 Closeout

### Sprint scope

Pick up after the two-day initial test. Address everything deferred from
the original Sprint 26 plan plus anything surfaced by the initial test
itself. Close Phase 8 with a State Ledger.

This sprint will receive new tasks based on initial-test findings — for
example, performance optimization (KG-PHASE8-16 follow-up) if the latency
analysis showed real regressions, or specific error-handler enhancements
(T26.1) tuned to the actual failure modes seen in production.

### Confirmed design decisions

- **Sprint 27 scope is partially defined now, partially after initial test.**
  Tasks 27.1–27.11 are known today. Tasks 27.12+ will be added based on
  initial-test findings.
- **Firestore retry wrapper is separate from Postgres retry.** Postgres has
  a working helper (`utils/retry.retry_on_transient`). Firestore needs its
  own — similar pattern, different transient-error classes.

### Task table (known today)

| Task | Status | Description | Gate(s) | Spec Reference |
|------|--------|-------------|---------|----------------|
| 27.1 | `[ ]` | Close KG-PHASE8-7 — replace `logging.basicConfig()` in `agent/worker.py` with the standard `setup_logging()` from `utils/logging_config.py`. Verify INFO-level logs appear correctly. | — | §8.8.2 |
| 27.2 | `[ ]` | Close KG-PHASE8-15 — add schema validation on inbound `forecastQueries` before `claim_session` reads `question`. Raise typed `MalformedQueryError` instead of `KeyError`. | Gate 1 | §8.8.1 |
| 27.3 | `[ ]` | Implement T26.1 — unified `agent/error_handler.py`. Wraps every node. Categorizes exceptions as retryable / non-retryable / escalate. Hooked in based on real failure modes seen in initial test. | Gate 1, Gate 2 | §8.7.5 |
| 27.4 | `[ ]` | Implement T26.2 (completion) — Firestore retry wrapper. Similar pattern to `utils/retry.retry_on_transient` but tuned for `google.api_core.exceptions.ServiceUnavailable`, `DeadlineExceeded`, etc. Wraps `firestore_client.py` write paths. | Gate 1 | §8.7.5 |
| 27.5 | `[ ]` | Implement T26.3 — stress test claim atomicity. 100 `forecastQueries` docs submitted simultaneously, 3 concurrent workers. Verify exactly-once. | Gate 3 | §8.8.1 |
| 27.6 | `[ ]` | Implement T26.4 — worker restart resilience. Kill worker mid-session, verify re-claim after `AGENT_CLAIM_TIMEOUT_SECONDS`. | Gate 3 | §8.8.1 |
| 27.7 | `[ ]` | Implement T26.5 — structured JSON logging. Every node entry/exit, every external call, every state transition. | Gate 1 | §7.2 of pipeline_core |
| 27.8 | `[ ]` | Implement T26.8 — graceful shutdown on SIGTERM. Finish all claimed sessions before exiting. | Gate 1 | §8.8.1 (patched) |
| 27.9 | `[ ]` | Implement T26.9 — edge case tests: empty vault, vault returns malformed data, OpenAI returns invalid JSON, Firestore transient unavailability, plan limit hit mid-claim. | Gate 1, Gate 2 | §9.3 |
| 27.10 | `[ ]` | Implement T26.10 — load test E2E. 50 sessions over 10 minutes. Verify p95 latency < 30s, error rate < 2%, cost within budget. | E2E | §9.3 E2E |
| 27.11 | `[ ]` | Implement T26.11 — documentation pass: docstrings, `prompts/README.md`, `.env.example`. | — | §5.4 |
| 27.12+ | `[ ]` | New tasks added based on initial-test findings (placeholder — defined post-test). | — | — |
| 27.last | `[ ]` | Phase 8 closeout — generate Phase 8 State Ledger covering all sprints. Move all sprint sections to `task_plan_archive.md`. Collapse `task_plan.md` to summary keywords. | — | sprint-closeout skill |

---

## Future Enhancements (Deferred — Not in Initial Test Path)

Trade-offs we accepted to reach the initial test faster. Each entry is a
realistic next-iteration target with notes for the implementer. Order is
roughly by readiness and dependency, not by priority.

### 1. Reactive Search via External Search APIs (original Sprint 22-23 plan)

**Why deferred:** Cost concerns (KG-PHASE-9.5-9 cost analysis is mid-stream)
and the realization that NewsAPI-with-full-body covers most of the snippet
quality reactive search was designed to provide.

**What was deferred:** The entire `reactive_search/` microservice as specified
in `agentic_hub_spec.md` §8.12: Tavily client, Brave fallback, allowlist
filter, snippet extractor, `reactive_article_cache` Postgres table.

**When to revisit:** After (a) OpenAI cost picture stabilizes per
KG-PHASE-9.5-9, (b) initial-test data shows whether Sprint 23's producer-trigger
approach is producing useful coverage, (c) a use case appears that the
internal vault genuinely can't satisfy (real-time breaking-event tier).

**Three architectural options on the table:**

- **Option A (current V1) — Producer-trigger, fire-and-forget.** What Sprint 23
  ships. Cheapest, slowest (async; data available next session). Already built.
- **Option B — Producer-trigger with short polling window.** Agent emits
  trigger, then polls `knowledge_vault` for 10-15s for matching new rows. If
  hit, fetches inline. Limitation: full pipeline (Bronze→Silver→Gold) often
  takes >15s; many waits will time out. Implementation: ~1 day on top of
  Option A.
- **Option C — Direct producer call, bypassing Kafka.** Agent imports and
  calls `NewsAPIProducer.fetch_articles(...)` directly, receives raw
  articles, performs lightweight enrichment, threads into evidence stream.
  **Requires architectural rethink** (breaks `CLAUDE.md` §3.3 Service
  Isolation: producers ingest only, do not transform). Not implementable
  by hand-waving — needs a new "agent runtime ingestion" layer or an
  explicit Service Isolation exception with isolation guarantees enforced
  in the new layer. Estimated 1+ week of design + implementation.

### 2. Follow-up Escalation

**Why deferred:** Sprint 24 implements only the "answer from context" path.
The escalation path (when context is insufficient, fetch fresh evidence) was
designed in the original plan but moved to Future Enhancements to keep Sprint
24 lean.

**What needs to be built:**

- Restore `T24.4` to its escalate-or-answer form. Two output branches: answer
  from context (current) or trigger escalation.
- Wire the escalation branch to call `agent/nodes/trigger_reactive_ingestion`
  from Sprint 23 with reduced parameters: `keywords` derived from the user's
  follow-up question (not the original forecast question), `time_window_days=3`
  (narrower than main forecast's 7), and the per-session trigger budget treated
  as a follow-up budget (one trigger per follow-up, separate counter).
- Update the user-facing transparent message: replace "this will improve once
  expanded retrieval is enabled" with active "fetching fresh evidence..." UI
  state via agentEvents.

**Estimated effort:** ~1 day if Sprint 23 trigger node is unchanged; up to 2 days
if Sprint 23 had to also implement Option B polling.

**When to revisit:** When users (or test users) regularly hit insufficient-context
follow-ups in initial testing.

### 3. Cross-User Cache + Delta Refresh (original spec §8.7.3, §8.7.4)

**Why deferred:** Initial test has 3-4 users for 2 days. Probability of the
same question being asked twice within the 4-hour staleness window is low.
Building cache before initial test would also obscure the real cost numbers
the test is designed to measure.

**What needs to be built (full picture):**

- **Frontend / Express side (Friend 1's scope):**
  - `findRecentSessionByCanonicalKey(canonicalKey, staleness_window_hours=4)`
    in `server/src/repositories/session.repository.ts`.
  - Modify `createSession()` to check for cache hit before creating new
    `forecastQueries` doc. On hit, copy existing `sessionResults` doc to new
    session id, return new session immediately. On miss, create as today.
  - Firestore composite index on `canonicalKey + generatedAt` for the lookup.

- **Agent side (this scope):**
  - `state.is_refresh: bool` and `state.previous_session_id: Optional[str]` in
    `ForecastState`.
  - In `vault_query.py`, when `is_refresh`, query vaults filtered to
    `published_at > previous.generatedAt`. Merge results with previous evidence.
  - In `synthesize.py`, when `is_refresh`, include previous SessionResult as
    additional context with instruction: "Update the previous forecast with
    the new evidence; do not start from scratch."

- **Scenario 1 (refresh from chat panel):** UI button "Update this forecast"
  in the follow-up chat. Triggers a delta-refresh session with `previous_session_id`
  pointing to the current session.

- **Scenario 2 (cross-user same question):** Express checks for any user's
  session with matching `canonicalKey` within staleness window. Returns
  cache hit if available regardless of which user originally generated it.
  Privacy note: returned SessionResult is read-only; no user-specific data
  is leaked.

**Why these two are coupled:** Both depend on the same agent-side delta-refresh
plumbing. Should ship together.

**When to revisit:** After initial test produces enough sessions to make the
cache valuable, and after cost analysis (KG-PHASE-9.5-9) shows that the
synthesize node dominates cost — confirming that delta refresh actually saves
meaningful money.

**Pre-test minor fix already shipping in Sprint 22.7:** Writing `canonicalKey`
to the session doc (not only to sessionResult) so cache lookup has a key to
query against once implemented.

### 4. Polymarket Vector Index + Clarification on Multi-Match

**Why deferred:** Sprint 22 ships `pg_trgm` fuzzy matching which catches
verbatim and near-verbatim question matches. Vector-index-based semantic
matching catches paraphrases ("Will BTC hit 150K end of 2026?" vs "Will Bitcoin
reach $150K by year-end 2026?") but is meaningfully more complex.

**What needs to be built:**

- New `polymarket_markets_index` table with `vector(1536)` embeddings per
  active market question. Or extend `momentum_vault` with an embedding column.
- Embedding generation as part of Polymarket producer's existing flow: each
  new market gets embedded on first ingest. `text-embedding-3-small` to match
  existing 1536-dim HNSW indexes.
- Backfill embeddings for existing markets in the vault.
- Update `find_polymarket_market_by_question` (Sprint 22) to fall back from
  `pg_trgm` miss to vector similarity search with cosine > 0.92 threshold.
- When the search returns 2+ candidates with close similarity scores, write
  to `clarificationCandidates` and trigger the existing clarification flow
  (Sprint 21 already supports this on the Tier 1 ambiguous path).

**Estimated effort:** 3-5 days including backfill and threshold calibration.

**When to revisit:** When initial-test data shows users frequently asking
paraphrased versions of Polymarket questions that the current `pg_trgm` match
misses. **Empirical measurement during T22.1 (2026-05-23) found that legitimate
non-paraphrase rewrites (e.g., "Will BTC hit 150K by 2026?" → "Bitcoin to hit
$150K by 2026?") score ~0.49 — meaning the V1 `pg_trgm` approach rejects user
phrasings that are not paraphrases. If initial-test data shows this
missing-match rate is meaningful, FE4 priority increases.**

### 5. Sentiment Time Series Quality

**Why deferred:** Sprint 22 ships time-bucketing on already-computed sentiment
scores from `knowledge_vectors` and `social_vectors`. This produces a
reasonable Expert and Public line for the BI card with zero new OpenAI cost.
The lines may be noisy because they reflect whatever evidence the agent
happened to retrieve, not a representative sample.

**Possible upgrades, ordered by complexity:**

- **Larger time window.** Currently 14 days. Could extend to 30+ for slower-
  moving topics. Trivial change.
- **Sample-floor enforcement.** Discard buckets with <3 samples (too noisy).
  Half-day implementation.
- **Dedicated sentiment enrichment pass.** A separate pre-pass that queries
  `knowledge_vault` and `social_vault` for a question's topic over a longer
  window than the agent's evidence retrieval, computes sentiment scores on
  those records (using either the existing Gold-layer scores or a fresh LLM
  pass), buckets them. Cleaner output. Cost: extra GPT-4o-mini calls per
  forecast (~$0.001-0.003) if fresh sentiment scoring is needed.
- **Confidence bands rendering.** Frontend currently doesn't render
  `expertUpper` / `expertLower` confidence bands even though `SessionResult`
  supports them. Pure frontend work. **Empirical observation during
  T22.10 (2026-05-26): `expertUpper` and `expertLower` fields are
  currently written as explicit null. Server's `?? null` decoding
  would produce identical FE behavior if these were omitted. When
  confidence bands become real values in this FE5 work, the fields will
  carry meaningful data; until then this is minor wire-payload
  redundancy (~2 fields × N docs per session).**

**When to revisit:** When initial-test data shows the sentiment lines are
either consistently empty or visibly noisy.

### 6. Additional Reactive Trigger Sources

**Why deferred:** Sprint 23 ships NewsAPI reactive only. Telegram, ArXiv,
Polymarket comments support is identical-pattern but requires per-source work.

**What needs to be built per source:**

- `run_reactive(keywords, time_window_days)` method on the producer (Telegram
  could query by channel + keyword filter, ArXiv has a search API, Polymarket
  comments has the existing-but-broken endpoint per KG-PHASE-9.5-4).
- Register in `VALID_SOURCES` and `_REQUIRED_LIST_FIELDS` in
  `ingestion_trigger_consumer.py`.
- Add to the `_build_producer()` factory.
- Update `trigger_reactive_ingestion.py` to pick the right source(s) per
  domain — currently always picks `newsapi`.

**When to revisit:** When initial test shows NewsAPI alone isn't producing
enough coverage for certain question domains (e.g., crypto-heavy questions
might benefit more from Telegram reactive than NewsAPI).

### 7. Performance Optimization (if KG-PHASE8-16 finds regressions)

**Why deferred:** Sprint 26 only does the analysis. Optimization happens here
based on findings.

**Expected scope (depending on analysis output):**

- If `synthesize` token volume is the bottleneck → reduce prompt size by
  pruning lower-weight evidence items more aggressively.
- If `vault_query` is O(1)-regressing → investigate Postgres query plans,
  HNSW index health, connection pool behavior.
- If `rate_evidence` is slow → batch multiple items per LLM call instead of
  one-per-call (would also reduce cost).

**Critical link to Phase 10 (Calibration):** Phase 10 runs 100+ forecasts in
parallel to compute Brier scores. Any latency regression in single-forecast
path multiplies under that load. Performance optimization is genuinely
gating Phase 10 success — not just polish. If KG-PHASE8-16 analysis flags
real issues, this work may need to move ahead of Phase 10.

### 8. Public Sentiment Line — Source Quality

**Why noted:** Sprint 22 ships both Expert and Public sentiment lines. Public
is `social_vectors.sentiment_score` bucketed — but `social_vectors` is sparse
(few signals per question typically). Real-world quality of the Public line
will determine whether to invest in additional public-sentiment sources.

**Possible upgrades:** Twitter/X signals if a credible source becomes
available, broader HackerNews coverage, dedicated public-sentiment scraping.

**When to revisit:** When initial-test data shows the Public line is empty
on too many forecasts.

---

## Design Rationale Log

A condensed record of why each significant decision in this revision was
made. Useful for understanding the plan in the future when memory of the
discussion has faded. Read entries when a decision feels surprising.

### Sprint 22 decisions

- **All wiring fixes consolidated into one sprint.** Three independent fixes
  (KG-PHASE8-12 marketProbability, KG-PHASE8-22 sentimentTimeSeries, fuzzy
  match resolver) all touch `synthesize.py` and `write_to_firestore.py`.
  Spreading them across sprints would cause repeated merge conflicts in the
  same files. Consolidation removes that risk.
- **pg_trgm chosen over vector index for V1.** The user clarified during
  planning (2026-05-23) that the V1 scenario is users asking questions
  verbatim from Polymarket — paraphrase matching isn't critical to test the
  basic flow. pg_trgm gives 90% of the value at 20% of the implementation
  cost. Vector index is a clean upgrade later (Future Enhancement 4).
- **Sentiment time series uses already-retrieved evidence, not a fresh
  enrichment pass.** Zero new OpenAI cost. Output may be noisy because it
  reflects the agent's retrieval pattern, but for initial-test purposes the
  goal is "the BI card renders something real, not the empty state". Quality
  upgrade is Future Enhancement 5.
- **canonicalKey written to session doc as a pre-emptive fix.** Trivial cost
  now (one extra field on a doc); enables Future Enhancement 3 (cache) without
  losing the history accumulated during initial testing.

### Sprint 23 decisions

- **Trigger-and-forget instead of polling.** The full Bronze→Silver→Gold
  pipeline routinely takes longer than the 30s p95 forecast NFR. A polling
  approach would time out on most calls in production. Future Enhancement 1
  describes Option B (polling) for when the pipeline gets faster.
- **NewsAPI-only for V1.** Telegram/ArXiv/Polymarket-comments reactive
  follows the identical pattern but tripled the per-source implementation
  surface for V1. NewsAPI alone covers the largest signal category and
  validates the architecture; other sources are mechanical follow-ups.
- **No LLM call for keyword construction.** The keyword set
  (`structured_intent.entities` + `missing_dimensions` + `raw_question` words)
  is good enough for initial test. If trigger quality is poor, adding a
  dedicated LLM call is a small follow-up.
- **`reactive_triggers_log` table is minimal.** Just enough fields for
  debugging during initial test and for the cost-attribution analysis.
  No polling status, no result-quality tracking — those add complexity
  that isn't justified pre-test.
- **D1 (2026-05-23, locked at T23.1) — One API call per keyword + URL dedup
  in `run_reactive`.** When the agent's reactive trigger arrives with a list
  of keywords (e.g. `["iran", "opec", "crude oil"]`), the NewsAPI producer
  iterates the list and makes one `getArticles` call per keyword, then
  dedupes articles by URL across the per-keyword result sets before emitting
  to Bronze.


  **Rejected: joining keywords with `" OR "` into a single `keyword=` value.**
  newsapi.ai's documented mechanism for OR semantics is the official client
  library's `QueryItems.OR(...)`, which produces multiple `keyword=` HTTP
  params plus an explicit `keywordOper=or` override (default operator is
  AND). Inline boolean-string syntax inside a single `keyword=` value is
  undocumented behavior — likely treated as a literal phrase, possibly an
  AND query, possibly a 400 — we did not run a live probe to find out.
  **Why one call per keyword wins:** the single-keyword `_fetch_articles(
  keyword=<str>)` code path has been in production since Phase 7A (backfill
  Tier 3, 2026-05-09) and is well-tested. The OR-batched form would require
  a new `keywordOper` parameter and a multi-value `keyword=` HTTP shape,
  both untested in this codebase. With the agent's per-session trigger cap
  of 1 (`AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION=1`) and the per-node
  keyword cap of ≤8 (D4, T23.5), worst case is ≤8 HTTP calls per trigger
  — comfortably under any newsapi.ai rate limit. Trigger-and-forget runs
  inside a daemon thread on the consumer side, so the extra HTTP calls
  do not gate the forecast NFR. Dedup by URL is a Python `set` operation;
  cost is negligible. Locked invariant: `TestRunReactive.
  test_multiple_keywords_one_call_per_keyword` asserts `_fetch_articles`
  call count equals the keyword count — any future "optimization" that
  batches into a single call will trip this test.
- **D4 (2026-05-23, locked at T23.5) — Keyword-construction algorithm in
  `trigger_reactive_ingestion` node.** Build the trigger's `keywords` list
  by merging two state sources in priority order, deduping case-insensitively
  on first occurrence (preserving original case), capped at 8 keywords total:
  (1) `state.structured_intent.entities` first — highest-signal source, the
  entities the query_understand node already extracted via GPT-4o-mini;
  (2) `state.sufficiency_checks[-1].missing_dimensions` next — the gaps the
  sufficiency check flagged, filling remaining slots until the cap.
  **`state.raw_question` is intentionally excluded.** Word-splitting the
  raw question would mostly contribute stop-words and noise; meaningful
  signal is already captured in (1) and (2) via earlier LLM passes.
  **Cap-of-8 rationale:** worst case 8 HTTP calls per trigger (D1 invariant,
  one call per keyword), under per-session limit of 1 trigger — comfortably
  below any plausible newsapi.ai rate limit. More keywords past 8 = diminishing
  returns: by definition the agent is grasping at gap-coverage, so flooding
  with low-rank terms dilutes precision. **Reliance on `structured_intent`:**
  this design assumes the entities extraction in query_understand captures
  what the user actually mentioned. If reactive coverage looks poor in initial
  test, the first lever is to inspect entities quality before adding
  raw_question word-splitting or a dedicated LLM call. Configurable later
  via a new env var if 8 proves wrong — not necessary now.
- **D5 (2026-05-23, locked at T23.5) — Trigger counter increments on both
  emit success and Kafka failure.** Rate limit semantic is "≤ N attempts
  per session," not "≤ N successful emits." Rationale: prevents retry
  loops if Kafka is down; treats trigger budget as session energy rather
  than outcome. Trade-off: a transient Kafka outage during the only attempt
  of a session means no further triggers that session even if Kafka recovers.
  With V1 limit=1 the impact is small; revisit if limit ever rises to 2+.
- **D6 (2026-05-24, locked at T23.8) — Node always writes the counter on
  all paths, including no-op paths (rate-limit-blocked, no-usable-keywords).**
  LangGraph raises `InvalidUpdateError: Must write to at least one channel`
  on empty-dict returns from a node — every node must contribute at least
  one channel write per step. Returning `{"reactive_triggers_emitted": <prior>}`
  is a true semantic no-op: defensive readers via
  `int(state.get(field) or 0)` see identical results whether the field is
  absent-with-default-0 or present-with-value-0. **Surfaced by Gate 2 test
  G2.2** (`test_subgraph_idempotent_at_rate_limit_across_two_invocations`)
  at subgraph-invocation time — would have been a Sprint 26 wire-up bug
  if missed. The fix updated three Bundle B/C tests (`test_at_rate_limit_skips_emit`,
  C1's at-limit branch, `test_empty_or_whitespace_entities_skips_emit`)
  to assert the new no-op-write shape instead of `delta == {}`.
- **D7 (2026-05-24, locked at T23.9) — KafkaProducer is a module-level lazy
  singleton, not per-call. Reverses earlier guidance.** Initial design (during
  T23.5 review) defaulted to per-call producer based on the absence of any
  prior agent-node Kafka producer precedent and on a "≤1 send per session,
  overhead negligible" estimate. **Reversed at T23.9** when the Gate 3 test
  surfaced that per-call producers consistently exceed the 2s
  `send().get(timeout=...)` budget on first send — the bootstrap connect +
  metadata-refresh round-trip alone runs 1-3s on dev hardware. In a
  long-lived worker process serving many sessions sequentially, the singleton
  amortizes that cold-start cost over the worker's lifetime: cold once, warm
  for every subsequent session. The 2s timeout stays correct for the
  warm-producer steady state it was originally chosen for.
  **No health check (option (a)) — declined for V1.** The next send fails
  naturally per D5 (counter +1, log row with `status="failed"`); kafka-python-ng's
  reconnect logic handles transient broker bounces transparently for the
  subsequent session. Adding `producer.bootstrap_connected()` polling at every
  call would be defensive code for a rare event after Phase 9.5 robustness
  work. Revisit in T26+ if initial-test data shows real broker-bounce-mid-session
  incidents. **Test surface:** new `_reset_producer_for_tests()` helper +
  `_get_producer()` lookup point lets Bundle B/G2/G3 tests inject mocks
  cleanly without touching `_producer` directly. Bundle B/G2 tests swap their
  `patch.object(node, "make_producer", ...)` for
  `patch.object(node, "_get_producer", ...)`. **Surfaced by T23.9 Gate 3
  cold-start KafkaTimeoutError on first send.**

### Sprint 24 decisions

- **Escalation path explicitly deferred, not deleted.** Future Enhancement 2
  is the live target for re-adding it. Decision made because escalation
  effectively duplicates Sprint 23's work in a different context — building
  both during this phase would touch overlapping code twice. Better to ship
  Sprint 23 (main-forecast escalation) first, see how it behaves, then port
  the pattern to follow-ups.
- **Transparent insufficient-evidence message hints at future capability.**
  "This will improve once expanded retrieval is enabled" signals that the
  system has a known limitation rather than an inability — important for
  user perception even though no external user sees this in initial test.

### Sprint 26 decisions

- **Original Sprint 26 was scaled back from 12 tasks to ~6.** The original
  task list was production-hardening (50-session load test, 3-worker
  concurrency, edge case coverage) that targets a multi-tenant production
  environment, not an initial test with 3-4 users for 2 days. Moved
  production-hardening work to Sprint 27.
- **KG-PHASE8-17 prioritized as a pre-test must-have.** The whole point of
  the two-day initial test is cost measurement (per KG-PHASE-9.5-9). Without
  this fix, two of the three OpenAI-using nodes don't log their token usage,
  meaning cost numbers from initial test would be incomplete. Cannot defer.
- **KG-PHASE8-16 reduced to analysis-only.** The full latency optimization
  task was originally in Sprint 26. Analysis is cheap (half day); fix-in-
  response is expensive and uncertain. Splitting them: analysis ships in
  26, optimization (if needed) ships in 27 or Phase 10.
- **OpenAI retry verified already complete.** Phase 9.5 Stage B Item 2 added
  `utils/openai_client.py` with `max_retries=5`. Verified against the file
  on 2026-05-23: every OpenAI call across the agent goes through this
  centralized factory. No new retry work needed for OpenAI.
- **Postgres retry already has a helper.** Phase 9.5 Stage B Item 1b added
  `utils/retry.retry_on_transient()`. Sprint 26 just wraps the agent's
  `momentum_vault` call sites in it. No new infrastructure.

### Sprint 27 decisions

- **Partially defined now, partially after initial test.** Pre-defining all
  of Sprint 27 would risk addressing problems that don't exist. Leaving
  space for new tasks (27.12+) means real findings from initial test get
  the implementation effort, not speculative ones.

---

## Cross-References

- `agentic_hub_spec.md` — Architectural specification. See "Spec Sections
  Affected" above for which sections are superseded by this revision.
- `agentic_hub_implementation.md` — Original Phase 8 plan. Authoritative for
  Sprints 18–21 (closed). Sections for Sprints 22–26 are now superseded by
  this document; an inline pointer at the top of each superseded section
  refers readers here.
- `anizai_handoff_consolidated.md` — Frontend contract. Most of it is
  unchanged; section 7 (deferred) loosely overlaps with Future Enhancements
  here but at a different scope (frontend deferrals vs. agent deferrals).
- `task_plan.md` — Active sprint tracker. Sprints 22–27 status lives there.
- `phase95_cluster_robustness_implementation.md` + `phase95_investigation_log.md`
  — Phase 9.5 closeout artifacts. KG-PHASE-9.5-9 (OpenAI cost analysis) is
  the parallel-session work that drives this revision.

---

## Skills Required Per Sprint

| Sprint | Required skills |
|---|---|
| 22 (Revised) | sprint-kickoff, agent-design, frontend-integration, code-review |
| 23 (New) | sprint-kickoff, infrastructure, agent-design |
| 24 | sprint-kickoff, agent-design, agent-prompt-engineering, frontend-integration |
| 25 | sprint-kickoff, agent-design, agent-prompt-engineering, frontend-integration |
| 26 | sprint-kickoff, code-review, infrastructure |
| 27 | sprint-kickoff, code-review, bugfix, sprint-closeout |
