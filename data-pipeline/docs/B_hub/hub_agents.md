# hub_agents.md
> Domain: B — Agentic Hub
> Type: Spec
> Last updated: 2026-07-19
> TL;DR: The LangGraph state machine in detail — nodes, edges, routing logic, each retrieval agent's vault queries and evidence shapes, the Firestore output contract, and known edge cases. Open this when you need to know what a node does or what shape evidence takes. The structurally-separate follow-up subgraph (Sprint 24) is in §2.5 / §3.5 / §4.5.

## Navigation
- §1 — Overview — pointer to hub_overview.md and reading order
- §2 — State Machine — graph structure, nodes, edges, routing functions
- §3 — Agent Nodes — per node and per retrieval agent: role, input, output, vault
- §4 — Firestore Contract — session collections, SessionResult schema, status transitions
- §5 — Known Constraints / Edge Cases

---

## §1 — Overview

This file describes the **as-built** forecast graph (through Sprint 25). For the
macro view and component inventory, read `hub_overview.md` first. For closed-sprint
history and the spec patches, see `hub_archive.md`. For open work, see `hub_sprints.md`.

The graph is a LangGraph `StateGraph` compiled once at worker startup (module-level
singleton — a malformed graph surfaces at startup, not at first request). One
`ForecastState` TypedDict (`agent/state.py`, `total=False`) is the contract between
nodes: nodes never call each other; they read and write state, and routing functions
branch on state fields with no LLM calls.

---

## §2 — State Machine

### 2.1 Topology (as built)

```
START → claim_session → query_understand
                          ├─ ambiguous? True  → write_clarification → END
                          └─ ambiguous? False → build_embedding
                                              → vault_query
                                              → sufficiency_check
                                                 ├─ sufficient?   → rate_evidence
                                                 └─ insufficient? → trigger_reactive_ingestion
                                                                    → rate_evidence
                                              → synthesize
                                              → generate_suggested_actions
                                              → write_to_firestore → END
```

Eleven nodes, two conditional edges (ambiguous? after query_understand; sufficient?
after sufficiency_check — the insufficient branch rejoins rate_evidence after a
trigger-and-forget Kafka emit). All other edges are linear. **As-built through
Sprint 25** (2026-07-16): `sufficiency_check` + `trigger_reactive_ingestion` landed
in Sprint 23.5; `generate_suggested_actions` (Node 6.5, GPT-4o-mini) landed in
Sprint 25 between `synthesize` and `write_to_firestore`. Every main-graph node also
emits `agentEvents` during the run (Sprint 25) — see §4.6.

### 2.2 Nodes and edges

| Node | Edge to | Type |
|---|---|---|
| `START` | `claim_session` | linear |
| `claim_session` | `query_understand` | linear |
| `query_understand` | `write_clarification` OR `build_embedding` | conditional (`_route_after_query_understand`) |
| `write_clarification` | `END` | linear |
| `build_embedding` | `vault_query` | linear |
| `vault_query` | `sufficiency_check` | linear |
| `sufficiency_check` | `trigger_reactive_ingestion` OR `rate_evidence` | conditional (`_route_after_sufficiency`) |
| `trigger_reactive_ingestion` | `rate_evidence` | linear |
| `rate_evidence` | `synthesize` | linear |
| `synthesize` | `generate_suggested_actions` | linear |
| `generate_suggested_actions` | `write_to_firestore` | linear |
| `write_to_firestore` | `END` | linear |

### 2.3 Routing logic

| Routing function | Reads | Branches |
|---|---|---|
| `_route_after_query_understand` | `state.awaiting_clarification` | `True` → `write_clarification` (→ END); `False` → `build_embedding` |
| `_route_after_sufficiency` | latest `state.sufficiency_checks[-1].is_sufficient` + `state.reactive_triggers_emitted` vs `AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION` | sufficient (or budget spent) → `rate_evidence`; insufficient + budget left → `trigger_reactive_ingestion` (→ rejoins `rate_evidence`) |

The resume-on-clarify path (`skip_matching_step`) is transparent to the graph:
`process_query` pre-populates state before `graph.invoke()`, `query_understand`
early-returns with `awaiting_clarification=False`, and the graph takes the normal
forecast path.

### 2.4 Reactive branch — built in Sprint 23.5 (was the spec-described loop)

The spec (§8.3.2 / Patch 7) shows a `sufficiency_check → vault_query_2 →
reactive_search → rate_evidence` loop. As of Sprint 23 **none of these nodes were
built** — no `sufficiency_check`, no second vault query, no `reactive_search`; the
`sufficiency_checks` state field existed but was written by nothing (KG-B-15).

**Sprint 23.5 builds a simplified, integrated version** of this (V1 — no second
vault query, no external-search microservice; trigger-and-forget into the existing
NewsAPI producer):

```
… vault_query → sufficiency_check
                   ├─ sufficient?   ──▶ rate_evidence → synthesize → write_to_firestore → END
                   └─ insufficient? ──▶ trigger_reactive_ingestion ──▶ rate_evidence → … (trigger-and-forget)
```

The insufficient branch dispatches the trigger (at most once per session,
`AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION=1`) and continues to `rate_evidence` with
whatever evidence is available — it does not wait for ingestion. The Tavily/Brave
`reactive_search` *microservice* remains deferred (Future Enhancement 1). See
`sprint23_5_pre26_remediation.md` and `hub_sprints.md`.

### 2.5 Follow-up subgraph (Sprint 24 — built)

A **second, structurally-separate** LangGraph (`agent/followup/`), compiled once at
worker startup, that answers chat follow-ups on an already-completed forecast. It
does **not** touch the forecast graph above — the main graph never imports the
followup package. It uses its own smaller state, `FollowupState` (`agent/followup/
state.py`, `total=False`): `parent_session_id`, `trigger_message_id`,
`trigger_question`, `message_history`, `parent_session_result`, `parent_evidence`
(top-5), `response_text`, `total_cost_usd`.

**Topology (linear, no branches):**

```
START → load_context → answer_from_context → write_message → END
```

**Scope (revised Sprint 24 — no escalation):** the follow-up is answered
**exclusively** from the parent `SessionResult` + the top-5 evidence the forecast
already used. There is no new vault query and no reactive escalation (that is
Future Enhancement 2). When the context can't answer, the agent returns a
transparent message rather than fabricating one.

**Two trigger entry points (complementary delivery windows — `agent/followup/
listener.py`):**

- **Live listener (T24.1)** — a Firestore **collection-group** Watch on every
  `messages` subcollection where `role == 'user'` AND `status == 'sent'`. The
  parent `session.status == 'done'` condition is not expressible in a
  collection-group query, so it is checked per message via one parent-doc read; a
  message whose parent isn't `done` yet is skipped (the sweep catches it).
- **Done-transition sweep (T24.15)** — called from `process_query` after a
  **successful** `graph.invoke()` (done-guarded — an `awaiting_clarification`
  terminus is not swept). It answers any still-`sent` user messages for that
  session, closing the one-time-delivery gap for a message written just before the
  `done` flip. `process_query` (the runner, not a graph node) is the seam, so the
  main graph stays decoupled from the followup package.

**Idempotency — atomic `sent → answered` claim (T24.14):** the listener and the
sweep can both see the same message as `sent`. Exactly-once is guaranteed by a
Firestore transaction (`firestore_client.claim_and_write_followup_answer`,
mirroring the main path's `claim_query`): re-read the user message; if still
`sent`, flip it to `answered` **and** write the assistant reply in the same
transaction; else no-op. Only the winner writes. A crash before the commit leaves
the message `sent` → re-answered on the next listener (re)attach.

**Budget / degradation (T24.9):** `answer_from_context` is bounded by a local
deadline + per-call timeout of `AGENT_FOLLOWUP_BUDGET_MS` (6000 ms, `max_retries=0`)
and is **born-instrumented** via `agent/utils/llm_cost.record_usage` into
`total_cost_usd`. On overrun it returns a **complete** caveat message (not a
truncated reply). End-to-end target ≤7 s (Gate 3). No `agentEvents` emission yet —
Sprint 25 (T25.7) retrofits these three nodes to emit them.

---

## §3 — Agent Nodes

### 3.1 Graph nodes

| Node | Role | Input (state) | Output (state) | LLM / external |
|---|---|---|---|---|
| `claim_session` | Atomically claim the `forecastQueries` doc; status claimed → running | `session_id`, query doc | claim confirmed; `errors` on race | Firestore txn |
| `query_understand` | Classify question; build `structured_intent`; top-3 Polymarket candidates; ambiguity flag | `raw_question` (or pre-populated resume state) | `structured_intent`, `polymarket_candidates_considered`, `awaiting_clarification`, `clarification_candidates` | GPT-4o-mini (structured-output, strict JSON) |
| `write_clarification` | Persist `clarificationCandidates`; status=awaiting_clarification; end graph | `clarification_candidates` | Firestore writes | Firestore |
| `build_embedding` | Embed `raw_question` (1536-dim), cache on state | `raw_question` | `query_embedding` | OpenAI `text-embedding-3-small` |
| `vault_query` | Dispatch the 3 retrieval agents in parallel (ThreadPoolExecutor, 15s per-agent timeout) | `query_embedding`, `structured_intent` | `researcher_evidence`, `pulse_evidence`, `market_evidence` | — (agents query vaults) |
| `sufficiency_check` *(Planned — Sprint 23.5)* | Evaluate the 3 evidence packages vs. `structured_intent`; decide sufficient/insufficient; derive `missing_dimensions` | the three evidence packages, `structured_intent` | appends to `sufficiency_checks` (`is_sufficient`, `missing_dimensions`, `reason`, `attempt`) | Deterministic V1 (no LLM — see 23.5.2) |
| `rate_evidence` | Normalize all agent evidence to unified `EvidenceItem[]`; score relevance + justification (batches of 8) | the three evidence packages | `evidence_trail` | GPT-4o-mini |
| `synthesize` | Final reasoning: probability, confidence, key_factors, gaps; infer tier | `evidence_trail`, `structured_intent`, `market_evidence` | `synthesis_result`, `tier` | GPT-4o |
| `write_to_firestore` | Persist SessionResult + subcollections; status=done | `synthesis_result`, `evidence_trail` | Firestore writes | Firestore |

`query_understand` outputs a `structured_intent` contract: `intent`
(forecast/explain/summarize/compare), `domain` (8-value enum), `entities` (1–5),
`polymarket_search_terms` (retrieval candidates — **not** a specific slug; see
§5), `has_market_question_intent`, `confidence`, `too_broad`, `rejected`. Tier is
**not** finalized here — it is inferred downstream in `synthesize` from whether a
Polymarket market was resolved.

### 3.2 Retrieval agents

The three agents are deterministic Python functions (not autonomous LLM agents).
They run in parallel under `vault_query`, touch the vaults only through
`agent/tools/` wrappers (Service Isolation, CLAUDE.md §3.3), and return per-agent
evidence shapes for inter-node passing. The unified `EvidenceItem` shape (§3.3) is
produced later by `rate_evidence`.

| Agent | Vault(s) | Tool wrapper | Returns | Notes |
|---|---|---|---|---|
| The Researcher | `knowledge_vectors` (HNSW) + `knowledge_vault` (drill-down) | `knowledge_tools.py` | `ResearcherEvidence` | similarity_search(limit=15, min_impact_level=2, min_reliability=0.3); composite rank `0.6·sim + 0.25·impact_norm + 0.15·recency`; top-5 drill-down to full text |
| The Pulse Analyst | `social_vectors` (HNSW) + `social_vault` (drill-down) | `social_tools.py` | `PulseEvidence` | splits Polymarket consensus vs. HackerNews; consensus-extreme (>0.8 / <0.2) drill-down to raw comments; `community_sentiment` is a float [-1,1] |
| The Market Bridge | `momentum_vault` + `mapping_dict` | `market_tools.py`, `mapping_tools.py` | `MarketEvidence` | pg_trgm Polymarket fuzzy-match (Sprint 22); FRED anomalies; Google Trends; Tier 2 → `polymarket: None` |

**Polymarket resolution (Market Bridge, Sprint 22):** when
`structured_intent.has_market_question_intent`, the agent calls
`persistence.momentum_vault.find_polymarket_market_by_question(raw_question,
threshold=0.85)` — a `pg_trgm` trigram-similarity match against
`metadata_extension->>'question'`. On hit it populates
`market_evidence.polymarket` (current_odds, momentum, price_history via
`fetch_time_series(hours=720)`, market_slug); on miss it leaves `polymarket: None`
(Tier 2 behavior). No vector/semantic match — that is a deferred enhancement
(see `hub_sprints.md` §3).

### 3.3 Unified EvidenceItem (produced by `rate_evidence`)

All evidence — every agent's output — is normalized into one shape before
synthesis and before Firestore. Identity (`evidence_id`, `source_type`, `origin`),
content (`title`, `snippet`, `url`, `source_domain`, `published_at`, `fetched_at`),
ratings (`relevance_score`, `credibility_tier`, `recency_weight` — 7-day half-life,
deterministic), influence (`used_in_answer`, `impact_on_forecast`,
`impact_magnitude`, `is_key_evidence`, `rank`), and transparency (`justification`).

`source_type` maps to the frontend `type` field so the existing filter tabs work:
`vault_news`/`online_news` → `news`; `vault_telegram`/`online_blog` → `social`;
`vault_arxiv` → `expert`; `vault_market`/`vault_fred` → `market`; `vault_hackernews`
→ `social`.

Division of labor: `rate_evidence` sets `relevance_score`, `credibility_tier`,
`recency_weight`, `justification`. `synthesize` sets the influence fields
(`impact_on_forecast`, `impact_magnitude`, `is_key_evidence`, `rank`) — these are
final-forecast judgments, not retrieval-time judgments.

### 3.4 Hub-side reactive trigger node (built, not wired)

`agent/nodes/trigger_reactive_ingestion.py` (Sprint 23) emits a single Kafka
message to the `ingestion_triggers` topic targeting the NewsAPI producer's
`run_reactive()` method — **trigger-and-forget**: it does not wait for fetch or
for Bronze→Silver→Gold propagation. Fetched articles land in the vault for the
next session. Keywords are built from `structured_intent.entities` first, then the
latest sufficiency check's `missing_dimensions` (case-insensitive dedup, capped at
8); `raw_question` is intentionally excluded (decision D4 — canonical; the revised
plan text is corrected to match in Sprint 23.5.4). Now that `sufficiency_check`
(Sprint 23.5) populates `missing_dimensions`, this keyword set is no longer
entities-only. The counter `reactive_triggers_emitted` increments on every emit
attempt (success or Kafka failure); the per-session limit is
`AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION=1`.

**Interface note (KG-B-16):** as built in Sprint 23 the entry point is
`trigger_reactive_ingestion(state)`, but every graph-wired node exposes
`run(state)` and `graph.py` wires via `module.run`. Sprint 23.5.3 renames the
entry point to `run(state)` and Sprint 23.5.5 wires the node into `agent/graph.py`
on the insufficient branch (this replaces the old Sprint 26 T26.7). The
pipeline-side consumer (`orchestration/ingestion_trigger_consumer.py`) is Domain A
— see `pipeline_overview.md` for the consumer side.

### 3.5 Follow-up nodes (Sprint 24)

The three nodes of the §2.5 subgraph. Each exposes `run(state)` (same wiring
convention as the forecast graph), communicates only through `FollowupState`, and
emits no `agentEvents` (Sprint 25).

| Node | Role | Input (state) | Output (state) | LLM / external |
|---|---|---|---|---|
| `load_context` | Load the parent forecast's context (via `firestore_client` reads only) | `parent_session_id` | `parent_session_result`, `parent_evidence` (top-5 by `rank`; `is_key_evidence` set), `message_history` (last 10) | Firestore reads |
| `answer_from_context` | Single GPT-4o-mini call that **classifies and answers**; born-instrumented; budget-bounded | `trigger_question`, `parent_session_result`, `parent_evidence`, `message_history` | `response_text`, `total_cost_usd` | GPT-4o-mini (strict JSON) |
| `write_message` | Persist the assistant reply + atomically claim the user message | `parent_session_id`, `trigger_message_id`, `response_text` | Firestore transaction (`sent→answered` + assistant write) | Firestore txn |

`answer_from_context` returns a strict-JSON `{classification, classification_reason,
answer}` (`agent/prompts/followup.py`). The node maps the classification to the
reply deterministically (hub-principles G5): `answerable` → the model's `answer`;
`insufficient_evidence` → the fixed transparent message; `out_of_scope` (off-topic,
forbidden action — re-run / revise probability / fetch new evidence —, or abuse) →
a fixed polite redirect. The two non-answerable strings are **fixed constants**, not
LLM-authored (guardrail-lite; a dedicated pre-LLM filter is deferred).

---

## §4 — Firestore Contract

### 4.1 Collections written (under `sessions/{sessionId}/`)

| Path | Purpose | Lifecycle |
|---|---|---|
| `sessionResults/{sessionId}` (top-level) | Final forecast (`SessionResult`, §4.2) | One per completed session |
| `sessions/{sessionId}/evidence/{evidenceId}` | Each evidence item used | Many per session; batched writes (500-doc cap) |
| `sessions/{sessionId}/predictionSeries/{…}` | Time-series for the prediction chart | Populated from `market_evidence.polymarket.price_history` (Tier 1); empty for Tier 2 |
| `sessions/{sessionId}/sentimentTimeSeries/{…}` | Expert/Public sentiment over time | One doc per bucket-date where ≥1 source has data (Sprint 22) |
| `sessions/{sessionId}/agentEvents/{eventId}` | Real-time chain-of-thought (MAIN graph only) | Emitted continuously during the run by the non-blocking emitter (Sprint 25); stored permanently — see §4.6 |
| `sessions/{sessionId}/messages/{…}` | Chat thread: user follow-ups (server-written) + assistant replies (hub-written, Sprint 24) | User messages `status: 'sent'` → `'answered'` when answered; assistant replies carry `replyToMessageId` — see §4.5 |

`forecastQueries/{queryId}` (written by the Express BFF on `POST /sessions`) is the
work queue. `sessionResults` is written **top-level**, not as a subcollection —
this matches the server's `getSessionResult` reads (drift vs. spec patch §8.7.1
tracked as KG-B-2; see §5).

### 4.2 SessionResult schema (§8.7.2)

Core: `finalProbability` (0-1), `confidence` (0-1). Deterministic labels:
`confidenceLabel` / `consensusStrength` / `evidenceVolumeLabel` (thresholds:
≥0.8 → High; 0.5–0.8 → Moderate; <0.5 → Low — see `agent/labels.py`). Headlines:
`bottomLineAnswer`, `detailedExplanation`, `summaryMarkdown`. BI-card insights:
`marketComparisonInsight`, `sentimentAnalysisInsight`, `evidenceFeedSummary`.
Market: `marketProbability` (0-1, **null for Tier 2**), `marketComparison`.
Reasoning artifacts: `keyFactors` (3-5 ranked), `whatIDidntFind`, `reasoningChain`.
`suggestedActions` (Sprint 25 — 3 `{id,label,prompt}` contextual follow-ups; `[]`
when the node degrades). Metadata: `generatedAt`, `agentVersion`,
`tier` (`tier_1` / `tier_2`).

> Compatibility: the hub also writes `createdAt` and `updatedAt` alongside
> `generatedAt` because of a spec/server field-name drift (KG-B-3).

### 4.3 Session status transitions

```
queued → claimed → running → done
                           ↘ awaiting_clarification  (clarification branch)
                           ↘ failed                  (process_query._mark_failed)
```

The worker transitions both `forecastQueries.status` and `sessions/{id}.status`
through this lifecycle so the frontend renders distinct stages. `done` is written
**after** all subcollections are written, preserving the frontend invariant that
"done" means evidence/series are ready.

### 4.4 Clarification flow

When ambiguous, `write_clarification` writes a `clarificationCandidates[]` array on
the session doc (each: `id`, `label`, `source`, `description`, `matchConfidence`)
and sets status=`awaiting_clarification`, then the graph ends. The Express BFF (not
the hub) exposes `POST /sessions/:id/clarify`; on the user's pick it updates
`canonicalKey` and writes a fresh `forecastQueries` doc to re-trigger. The hub
detects the resume (sessionId ≠ query_doc_id), reads `canonicalKey` from the
session doc (null → Tier 2 freeform), sets `skip_matching_step=True`, and proceeds.
On completion the `done` write targets the **processed** fresh queue doc
(`query_doc_id`, carried through state as a single-writer field), so the doc that
actually ran is the one cleared from the queue; the original clarified queue doc
correctly stays `awaiting_clarification` (it never produced a forecast) — KG-B-18.

### 4.5 Follow-up message contract (Sprint 24)

The `messages` subcollection is the chat thread on a completed forecast. Division of
labor and the status transition that makes answering exactly-once:

- **User messages** are written by the Express BFF (`addMessage`) with
  `role: 'user'`, `content`, and `status: 'sent'`. The hub never writes user
  messages.
- **The hub answers** each `sent` user message once (§2.5): in one Firestore
  transaction it writes an **assistant** message (`role: 'assistant'`, `content`
  markdown, `replyToMessageId: <user message id>`, `agentVersion`) **and** flips the
  answered user message `status: 'sent' → 'answered'`. The flip removes it from the
  listener's filter set — the follow-up analogue of the main path's
  `pending → done`.
- **`replyToMessageId`** gives explicit answer→question linkage, so two back-to-back
  user questions are each answered independently with no time-ordering inference.
- **No frontend-facing status on assistant messages** — a budget-overrun caveat
  rides in the message *content*, not a status field. The only new user-facing
  status value is `answered` on **user** messages.
- **Deployment:** the collection-group listener needs a `messages` composite CG
  index — it lives partner-side in `server/firebase/firestore.indexes.json` (the
  `evidence` CG index is the template) and must be deployed to production Firestore
  before the initial test (implicit on the emulator).

---

### 4.6 agentEvents — chain-of-thought stream (Sprint 25)

The MAIN forecast graph streams a real-time reasoning trace to
`sessions/{sessionId}/agentEvents/{eventId}`; the **follow-up graph emits nothing**
(a status-based "thinking" indicator covers follow-ups). The emitter
(`agent/events.py`) is **non-blocking**: nodes enqueue and return immediately; a
single FIFO background-writer thread drains to Firestore via
`firestore_client.write_agent_event`. Fire-and-forget — a write failure is logged
and swallowed, never failing the forecast.

**Event doc** (fields the frontend panel reads): `eventId`, `sessionId`, `runId`,
`sequence`, `timestamp`, `type` (the emitting node's own name — open vocabulary,
NOT a fixed enum), `title`, `description`, `status` (`pending` | `running` | `done`
| `failed`), `durationMs`, `payload`. (No `parentMessageId` — a dead field from the
cancelled follow-up-events design.) A pair node emits a `running` start + a `done`
completion on the same `eventId` (merge-update); `claim_session` emits a single
one-shot `done` bootstrap ("Analyzing your question…") — the panel's first line.

**runId / sequence.** Every event carries a `runId` and a per-run `sequence`
(1..N, monotonic). Both live in the emitter's **per-`runId` guarded registry**
(lock-guarded counter), NOT on state; `ForecastState.run_id` is a single-writer state
field, minted once by `claim_session` (the delivery-path `query_doc_id`, §4.4, is the
other single-writer field). `runId` namespaces a run (for the planned
"re-run forecast" button); the frontend renders only events whose
`runId == session.currentRunId`.

**currentRunId.** `claim_session` writes `currentRunId` onto the session doc on the
`running` transition, **before the first event** (the ordering contract — the panel's
filter key must exist before any event referencing it lands).

**Ordering / drain.** Events are flushed BEFORE the session flips to `done` (§3-D
pinned order in `write_to_firestore`: outputs → complete last event → drain →
session `done` → queue `done`), so the panel — which hides on `done` — receives every
event. Additional non-raising drains: `process_query`'s `finally` (covers the failure
and `awaiting_clarification` paths) and worker shutdown; the run's context is disposed
(`dispose_run`) after the finally drain. On a mid-run failure, `process_query` marks
the in-flight event `failed` (via `fail_event`, resolved by the `run_id` captured off
the streamed state, `session_id` backup).

**Storage vs display.** Events are stored **permanently** (survive a mid-run refresh;
per-stage timestamps feed the Sprint-26 latency analysis). The frontend renders the
panel only while status ∈ {queued, claimed, running}; after `done`, events are never
re-rendered. No compaction job for V1.

---

## §5 — Known Constraints / Edge Cases

- **No sufficiency loop / no second vault query** *(as of Sprint 23; addressed in
  Sprint 23.5)*. The graph runs vault_query exactly once and the `sufficiency_checks`
  field is never populated by a built node (KG-B-15). Sprint 23.5 adds a
  `sufficiency_check` node (no second vault query in V1 — see §2.4).
- **Reactive trigger node is unwired** *(as of Sprint 23; wired in Sprint 23.5)*.
  It exists and is unit/subgraph tested but is not in the compiled graph, and its
  entry point is `trigger_reactive_ingestion()` rather than the `run(state)` the
  graph wiring expects (KG-B-16). Sprint 23.5 fixes the interface and wires it onto
  the insufficient branch (this replaces the old Sprint 26 T26.7).
- **Polymarket matching is `pg_trgm` only.** Verbatim and near-verbatim questions
  match; legitimate paraphrase rewrites can score ~0.49 and miss. Vector-index
  semantic matching + multi-match clarification is a deferred enhancement.
- **Node 1 does not name a market slug.** It emits `polymarket_search_terms`
  (retrieval candidates) — the LLM has no authoritative in-context list of live
  markets, so naming a single slug would hallucinate or go stale. Resolution
  happens in Market Bridge.
- **`community_sentiment` is a float**, not a string — corrected to match the Gold
  layer's raw `sentiment_score` in [-1.0, 1.0].
- **Polymarket `key_arguments_pro` / `key_arguments_con` return `[]`.** The Gold
  job writes an undifferentiated `key_findings` list; the contract holds but those
  two fields are empty (KG-B-1).
- **`sessionResults` is top-level**, contradicting spec patch §8.7.1's implied
  subcollection — the server reads top-level (KG-B-2).
- **Tier 2 persists like Tier 1** (to Firestore, `tier="tier_2"`,
  `canonicalKey: null`) — the original "ephemeral" rule was dropped (Patch 4).
- **Latency exceeds the 30s p95 NFR** (Sprint 20: 36.3s cold / 32.2s warm; Sprint
  21: 47s on a broad question). Per-node analysis is Sprint 26; any fix is Sprint
  27 / Phase 10 (KG-B-5).
