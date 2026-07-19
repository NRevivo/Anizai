# Agentic Hub Spec — Patch Document
## Apply these changes to `agentic_hub_spec.md` before Phase 8 begins

---

## How to use this document

This is a **patch document**, not a replacement spec. It lists every section of `agentic_hub_spec.md` that needs to change as a result of the architectural decisions agreed during the frontend integration planning rounds (October 2026).

Each entry uses one of three operations:

- **REPLACE WITH** — the existing section text is replaced entirely
- **DELETE** — the existing section is removed
- **ADD** — new section content is inserted at the specified location
- **EDIT** — surgical change to a specific paragraph or table row within an existing section

Apply the changes in order. Once applied, this patch document can be archived alongside `task_plan_archive.md` for future reference.

---

## Why these changes are needed

The original spec was written assuming:
1. A Postgres-only persistence model (a `forecast_sessions` table)
2. A FastAPI HTTP gateway with WebSocket support
3. Tier 2 questions are ephemeral and not persisted
4. The hub has read-only Postgres access

Reality check: the frontend was built on Firebase/Firestore. The cleanest integration is for the hub to be Firebase-native too — writing forecast results directly to Firestore where the frontend's existing real-time listeners pick them up. This eliminates the need for a custom API gateway and gives us streaming chain-of-thought UX for free via Firestore listeners.

These changes align the spec with that reality.

---

## Patch 1 — Section 7.3 (cross-reference to existing spec)

> Note: §7.3 lives in a different doc (`storage_and_agent.md`). This patch describes the change conceptually; apply it in the source file.

**EDIT:** Update the IAM/access requirements to reflect the new dual-store model.

Old text essentially said: "RAG API has read-only access to PostgreSQL."

**New requirement:**

> The Agentic Hub has:
> - **Read-only access to PostgreSQL** for vault queries (knowledge_vectors, social_vectors, momentum_vault, mapping_dict, knowledge_vault, social_vault drill-downs).
> - **Read-write access to PostgreSQL** for the new `reactive_article_cache` table only (Section 8.12).
> - **Write access to Firestore** for forecast result delivery via Firebase Admin SDK with service account credentials. The hub writes to `sessionResults` documents and the `evidence`, `agentEvents`, `predictionSeries`, `sentimentTimeSeries`, `messages` subcollections under each session.
>
> The hub does NOT modify any vault data. The hub does NOT have access to user authentication data or `users` collection beyond reading forecast metadata.

---

## Patch 2 — Section 8.1 (System Purpose & Scope)

**EDIT:** Adjust the "What the Agentic Hub IS / IS NOT" lists.

### "What the Agentic Hub IS:"

REPLACE the bullet `- An API layer (FastAPI) that serves forecasts to the frontend over HTTP and WebSocket.`

WITH:

`- A Firestore worker process that listens on the `forecastQueries` collection and writes results back to Firestore for the frontend's real-time listeners to consume.`

### "What the Agentic Hub is NOT:"

REPLACE the bullet `- It does NOT modify any vault data. The agent has read-only access to PostgreSQL...`

WITH:

`- It does NOT modify any vault data. The agent has read-only access to all vault tables in PostgreSQL. It has write access only to the new `reactive_article_cache` table (Section 8.12) and to Firestore session-related collections (Section 8.7).`

---

## Patch 3 — Section 8.1.1 Dependency table

**ADD** the following rows to the dependency table:

| Component | Module | Provides | Sprint |
|-----------|--------|----------|--------|
| Reactive Article Cache | `persistence/reactive_cache.py` | `lookup()`, `store()`, `cleanup_expired()` for online search results | Sprint 22 |
| Source Allowlist Config | `agent/config/source_allowlist.json` | Domain → credibility tier mapping for reactive search | Sprint 22 |
| Firestore Client | `agent/firestore_client.py` | Admin SDK wrapper for writing session results, events, evidence | Sprint 18 |
| Forecast Queries Listener | `agent/worker.py` | Firestore listener on `forecastQueries`, claims `pending` docs | Sprint 18 |

---

## Patch 4 — Section 8.2.2 (Tier 2 Behavior)

**REPLACE** the entire "Behavior" subsection of §8.2.2 WITH:

> **Behavior:**
> - Full research cycle: all three specialized agents + Synthesis Lead. The Market Bridge agent works with whatever structured data exists (FRED, weather, trends) but has no Polymarket price anchor.
> - Results **are persisted to Firestore** the same way Tier 1 results are. The session document gets `tier: "tier_2"` and `canonicalKey: null`. The frontend's `MarketComparison` card detects `marketProbability === null` and renders an empty state ("No canonical market available — freeform analysis").
> - Eligible for sharing and revisiting from the user's history.
> - **Not** eligible for tracking ("Follow this prediction"), automatic refresh, or appearing in "Active Forecasts" sidebar — these are Tier 1-only features because they require a canonical market to anchor against.
> - The agent should still check for *related* Polymarket markets and surface them: "I didn't find a direct market for your question, but these related markets informed my analysis: [market 1], [market 2]."

**Rationale:** The original "ephemeral, not saved" rule was over-engineering. Persisting Tier 2 sessions costs negligible storage, lets users revisit their own analyses, and avoids race conditions between the agent worker and a hypothetical cleanup job.

---

## Patch 5 — Section 8.2.3 (Question Validation & Clarification Flow)

**EDIT:** Update the "Ambiguous / multi-market" row to reflect the new clarification flow.

**REPLACE** the existing "Ambiguous / multi-market" row WITH:

| Scenario | Detection | Agent Response |
|----------|-----------|----------------|
| **Ambiguous / multi-market** | Query embedding matches 2+ Polymarket markets with similarity > 0.75 AND no single dominant match (top match's confidence within 0.10 of second-place match) | Agent writes `session.status = 'awaiting_clarification'` and populates `clarificationCandidates[]` (2-5 candidates sorted by `matchConfidence` desc). Agent emits `clarification_needed` event to `agentEvents` and stops. Frontend listener sees status change, renders picker UI. User selects a candidate (or "freeform") via the new `POST /sessions/:id/clarify` endpoint on the Express BFF. Express updates `canonicalKey` and re-queues. Agent resumes with the matching step skipped. |

**ADD** new subsection **§8.2.4 ClarificationCandidate Schema:**

> When the agent writes `clarificationCandidates`, each candidate has the shape:
>
> ```python
> ClarificationCandidate = {
>     "id": str,                       # canonical market id
>     "label": str,                    # human-readable, e.g., "Will Iran-Israel tensions ease by Dec 2024?"
>     "source": Literal["polymarket", "kalshi"],
>     "description": str,              # longer context (e.g., resolution criteria)
>     "matchConfidence": float,        # 0-1, agent's confidence this is what user meant
> }
> ```
>
> Candidates are written as a Firestore array on the session document (not a subcollection — the array is small and read once).

---

## Patch 6 — Section 8.3.1 ForecastState schema

**EDIT:** Add fields to `ForecastState` for clarification flow, evidence ratings, and tier handling.

ADD the following fields to the `ForecastState` TypedDict:

```python
# --- Clarification (added) ---
awaiting_clarification: bool
clarification_candidates: Optional[list[dict]]    # ClarificationCandidate[]
chosen_candidate_id: Optional[str]                 # Set after user clarifies
skip_matching_step: bool                           # True on resume after clarification

# --- Reactive Search (added) ---
vault_query_attempts: int                          # Counter — max 2 before reactive search
sufficiency_checks: list[dict]                     # History of sufficiency verdicts
reactive_search_results: Optional[list[dict]]     # EvidenceItem[] from online search
reactive_search_budget_remaining_ms: int           # Tracks the 6s budget

# --- Output (added) ---
tier: Literal["tier_1", "tier_2"]                 # Set during query understanding
suggested_actions: Optional[list[dict]]            # SuggestedAction[]
key_factors: Optional[list[dict]]                  # KeyFactor[]
what_i_didnt_find: Optional[list[str]]             # Explicit gaps
```

The existing `evidence_trail: list[dict]` field is now formally typed as `list[EvidenceItem]` where `EvidenceItem` follows the unified rating schema (Section 8.5.4 below).

---

## Patch 7 — Section 8.3.2 Graph Topology

**REPLACE** the graph topology diagram with the updated version reflecting the sufficiency check + reactive search loop and the clarification branch:

```
                        ┌──────────────────┐
                        │   START           │
                        │   (forecastQueries│
                        │    pending doc)   │
                        └────────┬─────────┘
                                 │
                                 ▼
                        ┌──────────────────┐
                        │ claim_session     │  Node 0: Mark forecastQueries claimed
                        │                  │  Set session.status = 'running'
                        └────────┬─────────┘
                                 │
                                 ▼
                        ┌──────────────────┐
                        │ query_understand  │  Node 1: GPT-4o-mini classification
                        │                  │  → tier, structured_intent
                        │                  │  → polymarket candidates
                        └────────┬─────────┘
                                 │
                        ┌────────┴─────────┐
                        │ ambiguous?        │  Conditional edge
                        └──┬───────────┬───┘
                      Yes  │           │ No
                           ▼           ▼
                  ┌─────────────┐  ┌──────────────────┐
                  │ write_      │  │ build_embedding   │
                  │ clarification│  │                  │
                  │ candidates  │  └────────┬──────────┘
                  │ (END until  │           │
                  │  user picks)│           ▼
                  └─────────────┘  ┌──────────────────┐
                                   │ vault_query_1     │  Node 3: First vault retrieval
                                   │ (parallel agents) │  Researcher + Pulse + Market
                                   └────────┬──────────┘
                                            │
                                            ▼
                                   ┌──────────────────┐
                                   │ sufficiency_check │  Node 4: GPT-4o-mini structured eval
                                   └────────┬──────────┘
                                            │
                                   ┌────────┴─────────┐
                                   │ sufficient?       │
                                   └──┬───────────┬───┘
                            Yes       │           │ No, attempts<2
                                      │           ▼
                                      │  ┌──────────────────┐
                                      │  │ vault_query_2     │  Refined query using
                                      │  │ (parallel agents) │  missing_dimensions
                                      │  └────────┬──────────┘
                                      │           │
                                      │           ▼
                                      │  ┌──────────────────┐
                                      │  │ sufficiency_check │
                                      │  │      (2nd)        │
                                      │  └────────┬──────────┘
                                      │           │
                                      │  ┌────────┴────────┐
                                      │  │ sufficient?      │
                                      │  └──┬──────────┬───┘
                                      │     │ Yes      │ No, budget OK
                                      │     │          ▼
                                      │     │  ┌──────────────────┐
                                      │     │  │ reactive_search   │  Node 5: Online article search
                                      │     │  │ (microservice)    │  Max 5 articles, 6s budget
                                      │     │  └────────┬──────────┘
                                      │     │           │
                                      │     │           ▼
                                      │     │  ┌──────────────────┐
                                      │     │  │ rate_evidence     │  Unified rating pass
                                      │     │  │                  │  on all evidence
                                      │     │  └────────┬──────────┘
                                      ▼     ▼           ▼
                                   ┌──────────────────┐
                                   │ synthesize       │  Node 6: GPT-4o reasoning
                                   │                  │  → key_factors, confidence,
                                   │                  │  → executive_summary,
                                   │                  │  → suggested_actions,
                                   │                  │  → what_i_didnt_find
                                   └────────┬─────────┘
                                            │
                                            ▼
                                   ┌──────────────────┐
                                   │ write_to_        │  Node 7: Firestore writes
                                   │ firestore        │  → SessionResult
                                   │                  │  → evidence subcollection
                                   │                  │  → set status = 'done'
                                   └────────┬─────────┘
                                            │
                                            ▼
                                        ┌───────┐
                                        │  END  │
                                        └───────┘
```

Note: `agentEvents` writes happen continuously throughout the graph (every node emits at least one event). They are not shown explicitly to keep the diagram readable.

---

## Patch 8 — Section 8.3.3 Conditional Edge Logic

**REPLACE** the entire section WITH:

> **`ambiguous?`** — Routes to `write_clarification_candidates` if:
> - Multiple Polymarket market candidates have similarity > 0.75
> - The top match's confidence is within 0.10 of the second-place match
> - OR `structured_intent.too_broad == True` (no single resolution criterion)
> - OR `structured_intent.rejected == True` (nonsensical question — handled separately by writing a `failed` status with explanation)
>
> **`sufficient?`** — Routes to `synthesize` (sufficient path) when ALL of:
> - All coverage dimensions in `VaultSufficiencyCheck` pass (see §8.5.4)
> - `avg_relevance_score >= 0.6`
> - `confidence_in_assessment >= 0.5`
>
> Otherwise, if `vault_query_attempts < 2`, routes to `vault_query_2` with refinement based on `missing_dimensions`.
>
> If second sufficiency check still fails, routes to `reactive_search` provided:
> - Online search budget is available (max 1 reactive_search per session by default)
> - Per-question allowlist domains exist for the question's domain
>
> If reactive search budget is exhausted, routes directly to `synthesize` with `low_confidence_flag = true`. Synthesis Lead generates a "limited analysis" response with explicit gaps in `what_i_didnt_find`.

---

## Patch 9 — Section 8.4 Specialized Agents

**EDIT** §8.4.1, §8.4.2, §8.4.3 algorithms to reflect that each agent now also tags evidence with `origin: "knowledge_vault"`, the unified `EvidenceItem` rating fields (Section 8.5.4), and emits `vault_query` and `vault_query_result` events to `agentEvents`.

The agent return shapes (`ResearcherEvidence`, `PulseEvidence`, `MarketEvidence`) remain the same internally for inter-node passing. The unified `EvidenceItem` shape is what gets *written to Firestore* by the `write_to_firestore` node — internal evidence passing inside the graph can stay in the per-agent shapes.

**ADD** to each agent algorithm: "After retrieving evidence, emit a `vault_query_result` event to `agentEvents` with payload `{itemsFound: int, avgRelevance: float, durationMs: int}`."

---

## Patch 10 — Section 8.5 Evidence Evaluation

**RENAME** to **Section 8.5: Evidence Evaluation & Sufficiency Checking**

**EDIT §8.5.3 (sufficiency_verdict logic)** — REPLACE the existing simple "sufficient | needs_more | insufficient" logic WITH the structured rubric.

**ADD §8.5.4 — Sufficiency Check Rubric:**

> Each sufficiency check produces a `VaultSufficiencyCheck` Pydantic model:
>
> ```python
> class VaultSufficiencyCheck(BaseModel):
>     # Coverage dimensions
>     has_minimum_signals: bool          # >= 5 relevant signals retrieved
>     signal_count: int
>     covers_time_window: bool           # signals span the question's implied timeframe
>     covers_named_entities: bool        # all entities/regions/people in question represented
>     covers_required_sources: bool      # at least 2 distinct source types
>
>     # Quality dimensions
>     avg_relevance_score: float         # 0-1
>     has_recent_signal: bool            # at least one signal from last 48h
>     has_conflicting_views: bool        # signals show debate, not just one side
>
>     # Decision
>     is_sufficient: bool
>     missing_dimensions: list[str]      # e.g., ["recent_news", "expert_opinion"]
>     confidence_in_assessment: float    # 0-1
>
>     # Reasoning
>     justification: str                 # one-sentence explanation
> ```
>
> The routing function reads `is_sufficient` and `missing_dimensions` to decide the next node. `missing_dimensions` is what makes `vault_query_2` distinct from `vault_query_1` — the second query is constructed to address the specific gaps identified.

**ADD §8.5.5 — Unified EvidenceItem Schema:**

> All evidence — whether from the vault or from reactive search — is normalized into a single shape before synthesis and before being written to Firestore:
>
> ```python
> class EvidenceItem(BaseModel):
>     # Identity
>     evidence_id: str                    # uuid
>     source_type: Literal[
>         "vault_news", "vault_telegram", "vault_market",
>         "vault_arxiv", "vault_hackernews", "vault_fred",
>         "online_news", "online_blog"
>     ]
>     origin: Literal["knowledge_vault", "reactive_search"]
>
>     # Content
>     title: str
>     snippet: str                        # first ~200 chars
>     url: Optional[str]
>     source_domain: str                  # e.g., "reuters.com"
>     published_at: datetime
>     fetched_at: datetime
>
>     # Ratings
>     relevance_score: float              # 0-1
>     credibility_tier: Literal["tier_1", "tier_2", "tier_3"]
>     recency_weight: float               # 0-1, exponentially decayed
>
>     # Influence
>     used_in_answer: bool
>     impact_on_forecast: Literal["increases", "decreases", "neutral", "context_only"]
>     impact_magnitude: float             # 0-1
>     is_key_evidence: bool
>
>     # Transparency
>     justification: str                  # one sentence: "why I used this"
>     rank: int                           # 1 = most influential
> ```
>
> Mapping of `source_type` to the existing frontend display `type` field (so the existing filter tabs keep working):
>
> | source_type | Frontend type |
> |---|---|
> | `vault_news`, `online_news` | `news` |
> | `vault_telegram`, `online_blog` | `social` |
> | `vault_arxiv` | `expert` |
> | `vault_market`, `vault_fred` | `market` |
> | `vault_hackernews` | `social` |

---

## Patch 11 — Section 8.7 Forecast Sessions

**REPLACE THE ENTIRE SECTION** with the Firestore-based persistence model:

> ### 8.7.1 Storage Model
>
> Forecast results are persisted to **Firestore**, not PostgreSQL. The Firestore document tree under each session is the single source of truth for the frontend.
>
> The hub writes to (under `sessions/{sessionId}/`):
>
> | Path | Purpose | Lifecycle |
> |---|---|---|
> | `sessionResults/{sessionId}` doc | Final forecast result (`SessionResult` schema, §8.7.2) | One doc per completed session |
> | `evidence/{evidenceId}` subcollection | Each evidence item used in the analysis | Multiple docs per session |
> | `agentEvents/{eventId}` subcollection | Real-time chain-of-thought stream | Many docs during processing; can be compacted post-completion |
> | `predictionSeries/{...}` subcollection | Time-series for the prediction overview chart | Existing schema, hub populates |
> | `sentimentTimeSeries/{...}` subcollection | Expert/public sentiment over time | Existing schema, hub populates |
> | `messages/{messageId}` subcollection | Assistant follow-up replies | Hub writes assistant messages on user follow-ups |
>
> The `forecastQueries/{queryId}` collection (written by Express on `POST /sessions`) acts as the work queue. The hub claims pending docs and updates `status`.
>
> The previously-specified `forecast_sessions` PostgreSQL table is **removed** from this spec.
>
> ### 8.7.2 SessionResult Schema (Firestore)
>
> The full schema written to `sessionResults/{sessionId}`:
>
> ```python
> class SessionResult:
>     # Core forecast
>     finalProbability: float              # 0-1
>     confidence: float                    # 0-1
>
>     # Display labels (deterministically derived from numerics)
>     confidenceLabel: Literal["Low", "Moderate", "High"]
>     consensusStrength: Literal["Weak", "Mixed", "Strong"]
>     evidenceVolumeLabel: Literal["Low", "Moderate", "High"]
>
>     # Headlines for BI cards
>     bottomLineAnswer: str                # 1-2 sentence executive summary
>     detailedExplanation: str             # paragraph-length
>     summaryMarkdown: str                 # full markdown summary for chat panel
>
>     # Insight captions per BI card
>     marketComparisonInsight: str
>     sentimentAnalysisInsight: str
>     evidenceFeedSummary: str
>
>     # Market data
>     marketProbability: Optional[float]   # 0-1, null for Tier 2
>     marketComparison: list[MarketDataPoint]
>
>     # Agent reasoning artifacts
>     keyFactors: list[KeyFactor]          # 3-5 drivers, ranked
>     whatIDidntFind: list[str]            # explicit gaps
>     reasoningChain: list[ReasoningStep]  # ordered persistent summary
>
>     # Suggested follow-up actions (V1: simpler dynamic, 3 items)
>     suggestedActions: list[SuggestedAction]
>
>     # Metadata
>     generatedAt: Timestamp
>     agentVersion: str
>     tier: Literal["tier_1", "tier_2"]
> ```
>
> Threshold rules for label derivation:
> - `confidence >= 0.8` → `"High"`; `0.5 <= confidence < 0.8` → `"Moderate"`; `confidence < 0.5` → `"Low"`
> - Same pattern for `evidenceVolumeLabel` and `consensusStrength`.
>
> ### 8.7.3 Cache Hit Logic (Tier 1 only)
>
> Caching now happens at the session level via `canonicalKey`. When a new Tier 1 question arrives:
>
> 1. Query Understanding resolves the canonical market id.
> 2. Express checks `sessions` collection for an existing session with this `canonicalKey` whose `sessionResults` doc was generated within the staleness window (default 4 hours) AND whose probability is within 3% of current Polymarket odds.
> 3. **Cache hit** — Express copies the existing `sessionResults` doc to the new session id (preserving the user's per-session ownership). Hub does NOT run.
> 4. **Cache miss** — Hub runs the full pipeline. New `sessionResults` doc created.
>
> Tier 2 sessions never hit cache (each freeform question is unique). They always run the full pipeline.
>
> ### 8.7.4 Staleness & Refresh
>
> A Tier 1 session is considered stale if:
> - Time since `generatedAt` exceeds the staleness window (default 4h, configurable via `AGENT_STALENESS_WINDOW_HOURS`)
> - **OR** new evidence with `impact_level >= 4` has been ingested into any vault for this `canonicalKey` since `generatedAt`
> - **OR** Polymarket odds have moved more than 3% since the cached `marketProbability`
>
> When a stale session is referenced (e.g., user opens it from history, or a new user asks the same question), the hub does a **delta refresh**: queries vaults only for evidence newer than `generatedAt`, merges with the cached evidence, re-runs Synthesis Lead, updates the doc.
>
> ### 8.7.5 Market Resolution Detection
>
> When a Polymarket market resolves, a separate background process (out of scope for the hub itself, runs as a scheduled job in the Express BFF or as an Airflow DAG):
>
> 1. Updates `session.status = 'resolved'` for all sessions sharing that `canonicalKey`.
> 2. Sets `resolvedAt` timestamp and stores the actual outcome.
> 3. Followers (users who clicked "Track this forecast") receive a notification — delivered through Firestore (a write to a per-user `notifications/` collection that the frontend listens on).
>
> No WebSocket needed; the existing Firestore listener architecture handles delivery.

---

## Patch 12 — Section 8.8 API Gateway

**REPLACE THE ENTIRE SECTION** with the worker pattern:

> ### 8.8.1 Architecture: Worker Pattern (Not API Gateway)
>
> The Agentic Hub does **not** expose HTTP endpoints to the frontend. The original FastAPI gateway design has been replaced with a Firestore worker pattern that integrates more cleanly with the Firebase-based frontend.
>
> **Flow:**
>
> 1. Frontend submits a forecast via `POST /sessions` to the **Express BFF** (existing endpoint, not part of the hub).
> 2. Express creates a `sessions/{id}` document with `status: 'queued'` and a `forecastQueries/{queryId}` document with `status: 'pending'`.
> 3. The hub runs a long-lived worker process that listens on `forecastQueries where status == 'pending'` via the Firestore listener API (`firebase_admin.firestore.Client.collection().on_snapshot()`).
> 4. When a pending doc appears, the worker claims it (atomic transaction: read `status == 'pending'`, write `status = 'claimed'`, write `claimedAt` and `claimedBy` worker id) to prevent double-processing.
> 5. The worker runs the LangGraph pipeline. Throughout, it writes events to `agentEvents` and partial results to subcollections.
> 6. On completion, the worker writes the final `sessionResults` doc and updates `forecastQueries.status = 'done'` and `session.status = 'done'`.
> 7. The frontend, listening to the session's subcollections via the Firebase client SDK, receives all updates in real time.
>
> ### 8.8.2 Hub Endpoints (Internal Only)
>
> The hub exposes one HTTP endpoint, used only for health checks and operational monitoring (not by the frontend):
>
> | Method | Path | Description | Auth |
> |--------|------|-------------|------|
> | GET | `/health` | Health probe (returns `{status: "healthy", workerId, claimedSessions: int}`) | None |
> | GET | `/metrics` | Prometheus metrics endpoint (LangGraph node durations, LLM cost, queue depth) | None (internal) |
>
> No `/api/v1/forecast`, no `/ws/v1/...`, no WebSocket. The previous spec's HTTP/WebSocket API is **deleted**.
>
> ### 8.8.3 Follow-up Conversations
>
> Follow-ups are also handled via Firestore listener:
>
> 1. User types a follow-up in the chat panel; frontend calls `POST /sessions/:id/messages` (Express BFF endpoint).
> 2. Express writes a new doc to `sessions/{id}/messages` subcollection with `role: 'user'`.
> 3. The hub's worker (same process, second listener) watches for new user messages on completed sessions.
> 4. On a new user message, the worker runs a **lightweight subgraph** (no full vault re-search by default) that loads the parent session's `SessionResult`, top `keyFactors`, top evidence items, and the message history.
> 5. If the follow-up clearly requires fresh evidence, the agent escalates to vault query / reactive search using the same budget rules but with reduced limits (one search max, smaller time window).
> 6. The agent writes the assistant reply to the messages subcollection.
>
> **Budget for follow-ups:** ~5-7 seconds total (configurable via `AGENT_FOLLOWUP_BUDGET_MS`, default 6000). Follow-ups also write to `agentEvents` but with `parentMessageId` set, so the existing reasoning-panel UI works for follow-ups.
>
> **Streaming:** Follow-up responses arrive as a complete message for V1. Token-by-token streaming is deferred (would require a different mechanism than Firestore for token-level updates).
>
> ### 8.8.4 Clarification Resolution Endpoint
>
> The Express BFF (not the hub) gains a new endpoint:
>
> ```
> POST /sessions/:id/clarify
> Body: { chosenCandidateId: string | null }
> ```
>
> `null` means "freeform / treat as Tier 2". Express updates the session's `canonicalKey` (set or null), changes status back to `queued`, writes a new `forecastQueries` doc to re-trigger the agent, and clears `clarificationCandidates`. The hub picks up the re-queued doc, sees `skip_matching_step = true` in the state, and proceeds with the locked-in match.

---

## Patch 13 — Section 8.10 Directory Structure

**REPLACE** the directory tree WITH:

```
data-pipeline/
├── agent/                          # Agentic Intelligence Hub
│   ├── __init__.py
│   ├── worker.py                   # Firestore listener entry point + lifecycle
│   ├── firestore_client.py         # Firebase Admin SDK wrapper
│   ├── graph.py                    # LangGraph StateGraph definition and compilation
│   ├── state.py                    # ForecastState TypedDict and helper types
│   ├── budgets.py                  # Time/cost budget tracking helpers
│   ├── nodes/                      # One file per graph node
│   │   ├── __init__.py
│   │   ├── claim_session.py        # Node 0: Atomic claim of forecastQueries doc
│   │   ├── query_understand.py     # Node 1: Question classification + market lookup
│   │   ├── write_clarification.py  # Branch: write clarificationCandidates and END
│   │   ├── build_embedding.py      # Node 2: OpenAI embedding call
│   │   ├── vault_query.py          # Node 3: Parallel agent dispatch + retrieval
│   │   ├── sufficiency_check.py    # Node 4: GPT-4o-mini structured eval
│   │   ├── reactive_search.py      # Node 5: Calls reactive search microservice
│   │   ├── rate_evidence.py        # Unified rating pass on all evidence
│   │   ├── synthesize.py           # Node 6: GPT-4o final reasoning
│   │   └── write_to_firestore.py   # Node 7: Persist SessionResult + evidence
│   ├── agents/                     # Per-vault retrieval agents (Python functions)
│   │   ├── __init__.py
│   │   ├── researcher.py           # Knowledge vault retrieval
│   │   ├── pulse_analyst.py        # Social vault retrieval
│   │   └── market_bridge.py        # Momentum vault + mapping dict
│   ├── followup/                   # Lightweight follow-up subgraph
│   │   ├── __init__.py
│   │   ├── listener.py             # Firestore listener for new user messages
│   │   ├── graph.py                # Smaller LangGraph for follow-ups
│   │   └── nodes/
│   │       ├── load_context.py
│   │       ├── escalate_or_answer.py
│   │       └── write_message.py
│   ├── tools/                      # Vault query wrappers exposed as LangGraph tools
│   │   ├── __init__.py
│   │   ├── knowledge_tools.py
│   │   ├── social_tools.py
│   │   ├── market_tools.py
│   │   └── mapping_tools.py
│   ├── prompts/                    # LLM prompt templates
│   │   ├── __init__.py
│   │   ├── query_understanding.py
│   │   ├── sufficiency_check.py
│   │   ├── evidence_rating.py
│   │   ├── synthesis_lead.py
│   │   ├── suggested_actions.py
│   │   └── followup.py
│   └── config/
│       └── source_allowlist.json   # Domain → credibility tier (reactive search)
│
├── reactive_search/                 # NEW — Online article search microservice (Section 8.12)
│   ├── __init__.py
│   ├── service.py                  # Main entry point, called by reactive_search node
│   ├── search_clients/             # One file per upstream search API
│   │   ├── __init__.py
│   │   ├── tavily_client.py
│   │   └── brave_client.py
│   ├── snippet_extractor.py        # Fetches and extracts title/snippet from URLs
│   ├── allowlist.py                # Reads source_allowlist.json + filters results
│   └── probe.py                    # Standalone allowlist probe script (run quarterly)
│
├── persistence/
│   ├── ... (existing modules unchanged)
│   └── reactive_cache.py           # NEW — CRUD for reactive_article_cache table
│
├── infrastructure/
│   └── sql/
│       └── init.sql                # MODIFIED — add reactive_article_cache table DDL
│                                   # (forecast_sessions table is NOT added — see Patch 11)
│
└── config/
    └── settings.py                 # MODIFIED — add Firestore + reactive search env vars
```

**Removed from previous spec:**
- `data-pipeline/api/` — entire directory deleted (no FastAPI gateway)
- `data-pipeline/persistence/forecast_sessions.py` — no Postgres forecast table

---

## Patch 14 — Section 8.11 Configuration

**REPLACE** the env var block WITH:

```env
# === Agentic Hub (Section 8) ===

# OpenAI models
OPENAI_MODEL_QUERY_UNDERSTANDING=gpt-4o-mini
OPENAI_MODEL_SUFFICIENCY_CHECK=gpt-4o-mini
OPENAI_MODEL_EVIDENCE_RATING=gpt-4o-mini
OPENAI_MODEL_SYNTHESIS=gpt-4o
OPENAI_MODEL_FOLLOWUP=gpt-4o-mini
OPENAI_MODEL_SUGGESTED_ACTIONS=gpt-4o-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small

# Firebase Admin SDK
FIREBASE_PROJECT_ID=anizai-prod
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
# (Or use ADC in dev — leave GOOGLE_APPLICATION_CREDENTIALS unset and run `gcloud auth application-default login`)

# Worker behavior
AGENT_WORKER_ID=worker-1                       # Set per worker instance
AGENT_MAX_CONCURRENT_SESSIONS=3
AGENT_CLAIM_TIMEOUT_SECONDS=600                # If a worker dies mid-claim, others can re-claim after this

# Sufficiency / vault query loops
AGENT_MAX_VAULT_QUERY_ATTEMPTS=2
AGENT_SUFFICIENCY_MIN_RELEVANCE=0.6
AGENT_SUFFICIENCY_MIN_CONFIDENCE=0.5
AGENT_EVIDENCE_MIN_COUNT=5

# Reactive search
AGENT_REACTIVE_SEARCH_ENABLED=true
AGENT_REACTIVE_MAX_PER_SESSION=1
AGENT_REACTIVE_TIMEOUT_MS=6000
AGENT_REACTIVE_MAX_ARTICLES=5
AGENT_REACTIVE_DEFAULT_WINDOW_DAYS=7
TAVILY_API_KEY=...                             # Or whichever search API is chosen
REACTIVE_CACHE_TTL_SECONDS=1800                # 30 min default

# Caching / staleness
AGENT_STALENESS_WINDOW_HOURS=4
AGENT_POLYMARKET_MIN_POOL=10000
AGENT_POLYMARKET_DRIFT_THRESHOLD=0.03

# Follow-up budget
AGENT_FOLLOWUP_BUDGET_MS=6000

# Health endpoint (internal monitoring only — not user-facing)
HUB_HEALTH_HOST=0.0.0.0
HUB_HEALTH_PORT=8000
```

**Removed:**
- `API_HOST`, `API_PORT`, `API_CORS_ORIGINS` — no user-facing API
- `AGENT_MAX_REACTIVE_ITERATIONS=2` — replaced with `AGENT_REACTIVE_MAX_PER_SESSION` (we no longer iterate; one reactive search per session is the limit)

---

## Patch 15 — NEW Section 8.12: Reactive Search Microservice

**ADD** as a new section at the end of the spec:

> ## Section 8.12: Reactive Search Microservice
>
> Reactive search is a **new microservice** that fetches fresh articles from the open web when the vault doesn't have enough evidence to answer a question well. It is conceptually distinct from the existing data pipeline's "reactive ingestion" feature (Section 2.4), which fires Bronze-layer Kafka triggers to pull new data into the vault. The two are complementary:
>
> | Feature | Layer | Purpose | Result |
> |---|---|---|---|
> | **Reactive Ingestion** (Section 2.4) | Bronze | Trigger producers to fetch missing data into the vault for future queries | Persists into vault via Bronze→Silver→Gold; takes seconds to minutes |
> | **Reactive Search** (this section) | Agent / hub | Fetch fresh article snippets at agent runtime when vault is insufficient | Used inline for the current question; ephemeral in the agent's context, cached briefly in `reactive_article_cache` |
>
> Together they form Anizai's full "reactive" capability: the slow path (ingestion) keeps the vault fresh over time; the fast path (search) handles immediate gaps in real-time.
>
> ### 8.12.1 Service Boundary
>
> Reactive search runs in the same process as the agent worker (no separate container needed). It exposes a single internal Python function:
>
> ```python
> def reactive_search(
>     query: str,
>     time_window_days: int = 7,
>     max_results: int = 5,
>     timeout_ms: int = 6000,
> ) -> ReactiveSearchResult:
>     """Returns up to max_results EvidenceItems fetched from allowed domains."""
> ```
>
> Called by the `reactive_search` node when the second sufficiency check fails.
>
> ### 8.12.2 Source Allowlist
>
> A static config file: `data-pipeline/agent/config/source_allowlist.json`. Maps domains to credibility tiers and snippet-extractability flags.
>
> ```json
> {
>   "version": "2026-04",
>   "last_probed": "2026-04-15",
>   "domains": {
>     "reuters.com": {"credibility_tier": "tier_1", "allow_snippet": true},
>     "apnews.com": {"credibility_tier": "tier_1", "allow_snippet": true},
>     "bbc.com": {"credibility_tier": "tier_1", "allow_snippet": true},
>     "ft.com": {"credibility_tier": "tier_1", "allow_snippet": true},
>     "bloomberg.com": {"credibility_tier": "tier_1", "allow_snippet": "headline_only"},
>     "csis.org": {"credibility_tier": "tier_2", "allow_snippet": true},
>     "brookings.edu": {"credibility_tier": "tier_2", "allow_snippet": true},
>     "carnegieendowment.org": {"credibility_tier": "tier_2", "allow_snippet": true}
>   }
> }
> ```
>
> The probe script (`reactive_search/probe.py`) runs quarterly. For each domain:
> 1. Search for a recent article via the chosen search API
> 2. Fetch the URL and check whether title + meta description + first paragraph are extractable
> 3. Update the JSON config with results
>
> The hub is allowed to query domains not in this list (some search APIs return broader results), but **only domains in the allowlist with `allow_snippet: true` may have their snippets used as evidence**. Other results are dropped.
>
> ### 8.12.3 Cache Table Schema
>
> A new PostgreSQL table:
>
> ```sql
> CREATE TABLE IF NOT EXISTS reactive_article_cache (
>     cache_id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
>
>     -- Lookup keys
>     query_text            TEXT            NOT NULL,
>     query_embedding       vector(1536),                    -- For semantic-similarity hits
>     query_hash            TEXT            NOT NULL,        -- For exact-match hits
>
>     -- Article data (JSONB array of EvidenceItem-shaped objects)
>     articles              JSONB           NOT NULL,
>     article_count         INTEGER         NOT NULL,
>
>     -- Metadata
>     fetched_at            TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
>     expires_at            TIMESTAMPTZ     NOT NULL,        -- fetched_at + TTL
>     hit_count             INTEGER         NOT NULL DEFAULT 0,
>
>     -- Search params (for debugging)
>     search_params         JSONB
> );
>
> CREATE INDEX idx_rac_query_hash ON reactive_article_cache (query_hash);
> CREATE INDEX idx_rac_expires ON reactive_article_cache (expires_at);
> CREATE INDEX idx_rac_embedding ON reactive_article_cache USING hnsw (query_embedding vector_cosine_ops);
> ```
>
> ### 8.12.4 Cache Lookup Logic
>
> 1. **Exact match** — hash the normalized query, look up by `query_hash`. If hit and not expired, increment `hit_count` and return.
> 2. **Semantic match** — embed the query, find entries with cosine similarity > 0.92 using the HNSW index, where `expires_at > NOW()`. If hit, return.
> 3. **Miss** — call the search API, fetch snippets, filter by allowlist, store result, return.
>
> ### 8.12.5 Time Window Logic
>
> The agent passes `time_window_days` based on question semantics:
> - "What's happening with X right now?" → 1-3 days
> - "How has the situation evolved?" → 7 days (default)
> - "Has this happened before?" → 30+ days, no upper bound
>
> Determined during `query_understand` and passed in `ForecastState`.
>
> ### 8.12.6 Budget & Degradation
>
> - Default total budget: 6 seconds (configurable: `AGENT_REACTIVE_TIMEOUT_MS`)
> - Per-call budget: 2s for the search API call, 3s for parallel snippet fetching of top 5 URLs (each with 3s timeout), 1s buffer
> - **Graceful degradation:** if the budget is exceeded, the agent proceeds with whatever was fetched (even if just titles, no snippets). The synthesis node notes the degradation in `whatIDidntFind`.
> - **Hard limit:** maximum 1 reactive search per session (configurable: `AGENT_REACTIVE_MAX_PER_SESSION`).
>
> ### 8.12.7 Cleanup
>
> A nightly job deletes expired entries:
>
> ```sql
> DELETE FROM reactive_article_cache WHERE expires_at < NOW();
> ```
>
> Runs via Airflow DAG (or simple cron). Non-critical; if it fails, expired entries simply don't get reused (they're already filtered out at lookup time).

---

## End of Patch Document

After applying all 15 patches, verify the spec is internally consistent:
- No remaining mentions of `forecast_sessions` PostgreSQL table
- No remaining mentions of FastAPI gateway, WebSocket endpoints, or HTTP `/api/v1/forecast` URLs
- Tier 2 persistence is consistently described as "saved to Firestore"
- The reactive search microservice (§8.12) is referenced from §8.3.2 (graph topology) and §8.5.4 (sufficiency rubric)
- Section numbering is sequential

If any inconsistency is found, treat it as a follow-up gap to fix before Sprint 18 begins.
