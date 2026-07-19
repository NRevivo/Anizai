# agentic_hub_spec.md — Agentic Intelligence Hub Architecture
## Anizai Project | Section 8 of Technical Specification

---

## Section 8.1: System Purpose & Scope

The Agentic Intelligence Hub is the **reasoning layer** that connects the existing
data pipeline (Bronze → Silver → Gold → Vaults) to the user-facing frontend
application. It transforms a user's natural-language question into a structured,
multi-sourced analytical report — not through a simple database query, but through
an orchestrated workflow of specialized agents that retrieve, evaluate, and
synthesize evidence from all four vault tables.

**What the Agentic Hub IS:**
- A LangGraph-powered state machine that decomposes questions into retrieval tasks.
- A multi-agent system where specialized agents query specific vaults in parallel.
- A reasoning engine that synthesizes conflicting evidence into confidence-scored forecasts.
- A Firestore worker process that listens on the `forecastQueries` collection and writes results back to Firestore for the frontend's real-time listeners to consume.

**What the Agentic Hub is NOT:**
- It does NOT modify any vault data. The agent has read-only access to all vault tables in PostgreSQL. It has write access only to the new `reactive_article_cache` table (Section 8.12) and to Firestore session-related collections (Section 8.7).
- It does NOT replace the pipeline. Data still flows through Kafka → Flink → Vaults.
  The agent *consumes* vault data; it does not *produce* it (except for reactive
  ingestion triggers — Section 2.4).
- It does NOT make autonomous decisions about data quality. All enrichment scores
  (impact_level, reliability_score, etc.) are pre-computed by the Gold Job.

### 8.1.1 Dependency on Existing Infrastructure

The Agentic Hub sits on top of — and depends on — these completed components:

| Component | Module | Provides | Sprint |
|-----------|--------|----------|--------|
| Knowledge Vectors | `persistence/knowledge_vectors.py` | `similarity_search()`, `fetch_by_canonical_event()` | Sprint 3 |
| Social Vectors | `persistence/social_vectors.py` | `similarity_search()`, `fetch_by_signal_id()` | Sprint 1 |
| Momentum Vault | `persistence/momentum_vault.py` | `fetch_latest()`, `fetch_time_series()`, `fetch_fred_anomalies()` | Sprint 2 |
| Mapping Dictionary | `persistence/mapping_dict.py` | `lookup_by_canonical()`, `find_similar_and_link()` | Sprint 13 |
| Knowledge Vault (Silver) | `persistence/knowledge_vault.py` | `fetch_by_doc_id()` — drill-down to full text | Sprint 3 |
| Social Vault (Silver) | `persistence/social_vault.py` | `fetch_by_social_id()` — drill-down to raw discussions | Sprint 1 |
| Reactive Ingestion | `ingestion_triggers` Kafka topic | On-demand data fetch requests | Sprint 13 |
| Embedding Model | OpenAI `text-embedding-3-small` | 1536-dim query vectors matching HNSW indexes | Sprint 3 |
| Reactive Article Cache | `persistence/reactive_cache.py` | `lookup()`, `store()`, `cleanup_expired()` for online search results | Sprint 22 |
| Source Allowlist Config | `agent/config/source_allowlist.json` | Domain → credibility tier mapping for reactive search | Sprint 22 |
| Firestore Client | `agent/firestore_client.py` | Admin SDK wrapper for writing session results, events, evidence | Sprint 18 |
| Forecast Queries Listener | `agent/worker.py` | Firestore listener on `forecastQueries`, claims `pending` docs | Sprint 18 |

---

## Section 8.2: Question Tier Model

Every user question is classified into one of two tiers. This classification
determines caching behavior, tracking eligibility, and frontend display rules.

### 8.2.1 Tier 1 — Polymarket-Backed Questions

The question maps to an active Polymarket market with liquidity pool > $10K.

**Characteristics:**
- Has a `canonical_event_id` and market slug in `momentum_vault`.
- Has hard market data: current odds, price history, momentum block, whale alerts.
- May have community consensus vectors in `social_vectors`.
- Has clear resolution criteria (the market resolves Yes/No).

**Behavior:**
- Full research cycle: all three specialized agents + Synthesis Lead.
- Results persisted to Firestore as a `sessionResults` document with `tier: "tier_1"` (Section 8.7).
- Eligible for user tracking ("Follow this prediction").
- Eligible for periodic refresh (new evidence triggers re-evaluation).
- Appears in the "Active Forecasts" sidebar on the frontend.
- Other users asking the same question can receive the cached forecast
  (with freshness check) or trigger a refresh.

### 8.2.2 Tier 2 — Freeform Reasonable Questions

The question does NOT map to any Polymarket market but is a legitimate
forecastable question (e.g., "Will a major earthquake hit Tokyo in 2026?").

**Characteristics:**
- No `canonical_event_id` from Polymarket.
- No market odds or price history available.
- May still have relevant evidence in knowledge_vectors, social_vectors,
  and momentum_vault (e.g., weather data, FRED indicators, news articles).

**Behavior:**
- Full research cycle: all three specialized agents + Synthesis Lead. The Market Bridge agent works with whatever structured data exists (FRED, weather, trends) but has no Polymarket price anchor.
- Results **are persisted to Firestore** the same way Tier 1 results are. The session document gets `tier: "tier_2"` and `canonicalKey: null`. The frontend's `MarketComparison` card detects `marketProbability === null` and renders an empty state ("No canonical market available — freeform analysis").
- Eligible for sharing and revisiting from the user's history.
- **Not** eligible for tracking ("Follow this prediction"), automatic refresh, or appearing in "Active Forecasts" sidebar — these are Tier 1-only features because they require a canonical market to anchor against.
- The agent should still check for *related* Polymarket markets and surface them: "I didn't find a direct market for your question, but these related markets informed my analysis: [market 1], [market 2]."

### 8.2.3 Question Validation & Clarification Flow

Before any research begins, the Query Understanding node classifies the question:

| Scenario | Detection | Agent Response |
|----------|-----------|---------------|
| **Nonsensical / off-topic** | Topic classification fails; no forecastable event detected | Polite rejection: "This doesn't appear to be a forecastable question. Try asking about a specific future event." |
| **Already resolved** | Polymarket market `status: resolved` in momentum_vault | Return historical result: "This event has already resolved. Here's what happened: [outcome + key evidence]." |
| **Ambiguous / multi-market** | Query embedding matches 2+ Polymarket markets with similarity > 0.75 AND no single dominant match (top match's confidence within 0.10 of second-place match) | Agent writes `session.status = 'awaiting_clarification'` and populates `clarificationCandidates[]` (2-5 candidates sorted by `matchConfidence` desc). Agent emits `clarification_needed` event to `agentEvents` and stops. Frontend listener sees status change, renders picker UI. User selects a candidate (or "freeform") via the new `POST /sessions/:id/clarify` endpoint on the Express BFF. Express updates `canonicalKey` and re-queues. Agent resumes with the matching step skipped. |
| **Extremely broad** | No single market match; entity extraction returns 3+ unrelated entities | Same as ambiguous: suggest 2-5 specific sub-questions derived from active markets + news clusters. |
| **Valid Tier 1** | Single Polymarket market match, pool > $10K, status: active | Proceed with full research. |
| **Valid Tier 2** | No Polymarket match but question is forecastable (has timeframe, measurable outcome) | Proceed with full research; persist to Firestore with `tier: "tier_2"`, `canonicalKey: null`. |

**One Question, One Thread Rule:** The agent researches exactly one question per
session. If the user wants analysis on a different question from the suggested list,
they open a new thread. This keeps context focused, costs predictable, and the
evidence trail clean.

### 8.2.4 ClarificationCandidate Schema

When the agent writes `clarificationCandidates`, each candidate has the shape:

```python
ClarificationCandidate = {
    "id": str,                       # canonical market id
    "label": str,                    # human-readable, e.g., "Will Iran-Israel tensions ease by Dec 2024?"
    "source": Literal["polymarket", "kalshi"],
    "description": str,              # longer context (e.g., resolution criteria)
    "matchConfidence": float,        # 0-1, agent's confidence this is what user meant
}
```

Candidates are written as a Firestore array on the session document (not a subcollection — the array is small and read once).

---

## Section 8.3: Orchestration — LangGraph State Machine

The Agentic Hub is implemented as a **LangGraph StateGraph** — a directed graph
where nodes are processing functions and edges define the flow. A shared state
object is passed between nodes, accumulating evidence and decisions.

### 8.3.1 Graph State Schema

The central state object that flows through all nodes:

```python
class ForecastState(TypedDict):
    # --- Input ---
    raw_question: str                      # User's original question
    session_id: str                        # Unique session identifier
    user_id: str                           # For rate limiting and tracking

    # --- Query Understanding Output ---
    question_tier: Literal["polymarket_backed", "freeform"]
    structured_intent: dict                # Entities, timeframe, domain
    polymarket_market: Optional[dict]      # Market data if Tier 1
    query_embedding: list[float]           # 1536-dim from text-embedding-3-small
    clarification_needed: Optional[dict]   # If ambiguous: {candidates: [...]}

    # --- Clarification (added) ---
    awaiting_clarification: bool
    clarification_candidates: Optional[list[dict]]    # ClarificationCandidate[]
    chosen_candidate_id: Optional[str]                 # Set after user clarifies
    skip_matching_step: bool                           # True on resume after clarification

    # --- Agent Evidence Packages ---
    researcher_evidence: Optional[dict]    # From The Researcher
    pulse_evidence: Optional[dict]         # From The Pulse Analyst
    market_evidence: Optional[dict]        # From The Market Bridge

    # --- Evidence Evaluation ---
    dedup_report: Optional[dict]           # Cross-source dedup results

    # --- Reactive Search ---
    vault_query_attempts: int                          # Counter — max 2 before reactive search
    sufficiency_checks: list[dict]                     # History of sufficiency verdicts (list[VaultSufficiencyCheck], see §8.5.4)
    reactive_search_results: Optional[list[dict]]     # EvidenceItem[] from online search
    reactive_search_budget_remaining_ms: int           # Tracks the 6s budget

    # --- Synthesis Output ---
    synthesis_result: Optional[dict]       # Final structured report
    evidence_trail: list[dict]             # Curated source trail for UI (typed as list[EvidenceItem] — see §8.5.5)
    confidence_score: Optional[float]      # 0-100 final confidence

    # --- Output (added) ---
    tier: Literal["tier_1", "tier_2"]                 # Set during query understanding
    suggested_actions: Optional[list[dict]]            # SuggestedAction[]
    key_factors: Optional[list[dict]]                  # KeyFactor[]
    what_i_didnt_find: Optional[list[str]]             # Explicit gaps

    # --- Metadata ---
    llm_calls_count: int                   # Cost tracking
    total_tokens_used: int                 # Cost tracking
    errors: list[str]                      # Non-fatal errors accumulated
```

### 8.3.2 Graph Topology

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
                        │                  │  Set session.status = 'claimed' → 'running'
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

#### Node 1 (query_understand) output contract

The `query_understand` node writes a populated `structured_intent` dict into state. The contract is:

```python
structured_intent = {
    "intent": Literal["forecast", "explain", "summarize", "compare"],
    "domain": Literal["finance", "macro", "crypto", "tech", "geopolitics", "weather", "aviation", "general"],
    "entities": list[str],                        # 1–5 canonical entity names ("Federal Reserve", "Bitcoin", …)
    "polymarket_search_terms": Optional[list[str]],  # search candidates for vault_query to resolve to a market
    "has_market_question_intent": bool,           # True iff question asks about a discrete market-resolvable outcome
    "confidence": float,                          # rank-1 candidate's self-reported confidence ∈ [0.0, 1.0]
    "too_broad": bool,                            # set True when the question lacks a resolvable criterion
    "rejected": bool,                             # set True for nonsensical / out-of-domain questions
}
```

`question_tier` is finalized post-vault_query (Tier 1 = a Polymarket market was matched; Tier 2 = no market match), not by `query_understand`. Node 1 only signals *intent* (`has_market_question_intent`); the actual market resolution happens in Node 3 (`vault_query`) using `polymarket_search_terms` against the polymarket vector index.

**Correction (T19.5, 2026-04-30):** Earlier drafts of this section used `polymarket_slug: Optional[str]` as a Node 1 output field, with the implication that `query_understand` would name a specific market slug. That created a hallucination risk — the LLM has no authoritative list of live polymarket markets in-context, so a single proposed slug would either be invented or stale. Renamed to `polymarket_search_terms: Optional[list[str]]` so Node 1 produces *retrieval candidates* and Node 3 (`vault_query`) does the actual market resolution via similarity search against the polymarket vector index. Field is `Optional[list[str]]` because Tier 2 / non-market questions have no terms to surface.

### 8.3.3 Conditional Edge Logic

**`ambiguous?`** — Routes to `write_clarification_candidates` if:
- Multiple Polymarket market candidates have similarity > 0.75
- The top match's confidence is within 0.10 of the second-place match
- OR `structured_intent.too_broad == True` (no single resolution criterion)
- OR `structured_intent.rejected == True` (nonsensical question — handled separately by writing a `failed` status with explanation)

**`sufficient?`** — Routes to `synthesize` (sufficient path) when ALL of:
- All coverage dimensions in `VaultSufficiencyCheck` pass (see §8.5.4)
- `avg_relevance_score >= 0.6`
- `confidence_in_assessment >= 0.5`

Otherwise, if `vault_query_attempts < 2`, routes to `vault_query_2` with refinement based on `missing_dimensions`.

If second sufficiency check still fails, routes to `reactive_search` provided:
- Online search budget is available (max 1 reactive_search per session by default)
- Per-question allowlist domains exist for the question's domain

If reactive search budget is exhausted, routes directly to `synthesize` with `low_confidence_flag = true`. Synthesis Lead generates a "limited analysis" response with explicit gaps in `what_i_didnt_find`.

---

## Section 8.4: Specialized Agents

These are NOT autonomous LLM-based agents. They are **Python functions with
structured retrieval logic** that query specific vaults, assemble evidence packages,
and return structured dicts. The LLM reasoning happens in the `sufficiency_check`,
`rate_evidence`, and `synthesize` nodes (§8.3.2) — not in the retrieval agents.
This keeps costs low and behavior deterministic.

Each agent additionally tags every evidence item it produces with `origin: "knowledge_vault"` (vs. `origin: "reactive_search"` for reactive results) and emits `vault_query` and `vault_query_result` events to `agentEvents` so the frontend reasoning panel can display real-time progress. The unified `EvidenceItem` rating fields (§8.5.5) are populated by the downstream `rate_evidence` node, not by the retrieval agents themselves — the agents return raw retrieval payloads in their per-agent shapes (`ResearcherEvidence`, `PulseEvidence`, `MarketEvidence`), and the `EvidenceItem` shape is what gets *written to Firestore* by the `write_to_firestore` node. Internal evidence passing inside the graph stays in the per-agent shapes.

### 8.4.1 The Researcher Agent

**Vault:** `knowledge_vectors` (HNSW similarity search) + `knowledge_vault` (drill-down)
**Purpose:** Find and summarize the most relevant news articles, research papers,
and Telegram signals related to the question.

**Algorithm:**
1. Call `knowledge_vectors.similarity_search(query_embedding, limit=15)` with
   optional filters: `min_impact_level=2`, `min_reliability=0.3`.
2. Score results by a composite ranking: `0.6 * similarity + 0.25 * impact_level_norm + 0.15 * recency_score`.
3. For the top 5 results, drill down to `knowledge_vault` via `silver_data_ref`
   to retrieve `full_text_raw` (the complete article text, not just the snippet).
4. Tag each piece of evidence with `evidence_weight` (0.0-1.0) based on the
   composite ranking score, for use by the Evidence Trail builder.
5. After retrieving evidence, emit a `vault_query_result` event to `agentEvents` with payload `{itemsFound: int, avgRelevance: float, durationMs: int}`.
6. Return structured evidence package:

```python
ResearcherEvidence = {
    "articles": [
        {
            "signal_id": str,          # For citation
            "source_platform": str,    # "newsapi" | "arxiv" | "telegram"
            "publisher": str,          # "Reuters", "ArXiv", channel name
            "title": str,
            "published_at": str,
            "executive_summary": str,  # From enrichment_ai
            "key_findings": list[str],
            "full_text_snippet": str,  # First 500 chars of full_text_raw
            "impact_level": int,
            "reliability_score": float,
            "sentiment_score": float,
            "similarity": float,       # Cosine similarity to query
            "evidence_weight": float,  # Composite ranking score
            "canonical_event_id": str,
        }
    ],
    "source_diversity": {
        "newsapi_count": int,
        "arxiv_count": int,
        "telegram_count": int,
    },
    "recency_range": {
        "oldest": str,   # ISO8601
        "newest": str,   # ISO8601
    },
    "empty": bool,  # True if zero results
}
```

### 8.4.2 The Pulse Analyst Agent

**Vault:** `social_vectors` (HNSW similarity search) + `social_vault` (drill-down)
**Purpose:** Surface community sentiment, market consensus, and contrarian arguments.

**Algorithm:**
1. Call `social_vectors.similarity_search(query_embedding, limit=10)`.
2. Separate results by platform: Polymarket consensus vectors vs. HackerNews summaries.
3. For Polymarket results: extract `consensus_rating`, `comment_volume_analyzed`,
   and `aggregation_window_hours` from `platform_logic` JSONB.
4. For HackerNews results: extract `top_technical_insights`, `community_sentiment`,
   and `points` from `platform_logic` JSONB.
5. If any Polymarket consensus vector has `consensus_rating` in the extreme range
   (>0.8 or <0.2), drill down to `social_vault` via `silver_data_ref` to verify
   against raw comment archive — consensus can be skewed by low volume.
6. Tag each item with `evidence_weight`. Polymarket consensus with high volume
   gets higher weight than HackerNews discussion (market participants have
   financial skin in the game).
7. After retrieving evidence, emit a `vault_query_result` event to `agentEvents` with payload `{itemsFound: int, avgRelevance: float, durationMs: int}`.
8. Return structured evidence package:

```python
PulseEvidence = {
    "market_consensus": [
        {
            "signal_id": str,
            "market_id_ref": str,
            "consensus_rating": float,     # 0.0-1.0 (bearish to bullish)
            "comment_volume_analyzed": int,
            "aggregation_window_hours": int,
            "executive_summary": str,
            "key_arguments_pro": list[str],
            "key_arguments_con": list[str],
            "similarity": float,
            "evidence_weight": float,
        }
    ],
    "community_discussion": [
        {
            "signal_id": str,
            "platform": str,               # "hackernews"
            "title": str,
            "points": int,
            "top_technical_insights": list[str],
            "community_sentiment": float,  # -1.0 to 1.0 (see Correction T19.3 below)
            "similarity": float,
            "evidence_weight": float,
        }
    ],
    "overall_sentiment": float,  # Weighted average: -1.0 to 1.0
    "empty": bool,
}
```

> **Correction (T19.3, 2026-04-30):** `community_sentiment` was originally
> typed as `str` (e.g., `"bullish"` / `"bearish"` / `"neutral"`). The
> production Gold layer (`processing/gold_job.py:1744`) writes the raw
> `sentiment_score` float in `[-1.0, 1.0]`, and there are zero downstream
> consumers (frontend, BFF, handoff §5.2 evidence subcollection) that
> reference the string form. Changing the spec to match production
> preserves precision for synthesis and avoids a lossy float→string
> bucketing step at the agent. T19.3 returns the raw float; the Pulse
> Analyst module docstring records the same correction for traceability.

### 8.4.3 The Market Bridge Agent

**Vault:** `momentum_vault` (time-series) + `mapping_dict` (linkage) + `momentum_vault` (anomalies)
**Purpose:** Provide hard numerical data — market prices, economic indicators,
trend directions, and active anomaly alerts.

**Algorithm:**
1. **Polymarket data** (Tier 1 only): Call `momentum_vault.fetch_latest("polymarket", market_slug)`
   for current odds + momentum block. Call `momentum_vault.fetch_time_series("polymarket", market_slug, hours=720)`
   for 30-day price history.
2. **Cross-platform linkage:** Call `mapping_dict.lookup_by_canonical(canonical_event_id)`
   to find all linked platform IDs. For each linked source, fetch latest data point
   from `momentum_vault`.
3. **FRED anomalies:** Call `momentum_vault.fetch_fred_anomalies(days=14)` to get
   active macro triggers (yield curve inversions, VIX spikes, commodity swings).
   Filter to anomalies whose `impact_area` is relevant to the question's domain.
4. **Google Trends:** Call `momentum_vault.fetch_latest("googletrends", keyword)`
   for each entity extracted by Query Understanding. Check for `Public_Hype_Alert`.
5. **Weather/Flight** (when domain-relevant): Query OpenWeather/OpenSky data from
   `momentum_vault` if the question involves a strategic hotspot.
6. After retrieving evidence, emit a `vault_query_result` event to `agentEvents` with payload `{itemsFound: int, avgRelevance: float, durationMs: int}`.
7. Return structured evidence package:

```python
MarketEvidence = {
    "polymarket": {
        "current_odds": float,         # e.g., 0.723 = 72.3%
        "momentum": {
            "change_24h": float,
            "change_7d": float,
            "change_30d": float,
        },
        "price_history": list[dict],   # For chart rendering
        "whale_alerts": list[dict],    # Recent whale trades if any
        "market_slug": str,
    } | None,  # None for Tier 2 questions
    "linked_sources": [
        {
            "platform": str,
            "external_id": str,
            "latest_value": float,
            "unit": str,
            "momentum": dict,
        }
    ],
    "fred_anomalies": [
        {
            "series_id": str,          # e.g., "T10Y2Y"
            "indicator_name": str,     # e.g., "Treasury Yield Spread"
            "current_value": float,
            "anomaly_flags": list[str],
            "impact_level": int,
            "change_7d": float,
        }
    ],
    "google_trends": [
        {
            "keyword": str,
            "current_score": float,
            "trend_direction": str,    # "rising" | "falling" | "stable"
            "hype_alert": bool,
        }
    ],
    "empty": bool,
}
```

---

## Section 8.5: Evidence Evaluation & Sufficiency Checking

After the retrieval agents return their per-vault evidence packages, the graph
performs three distinct evaluation tasks across separate nodes (§8.3.2):
cross-source deduplication (§8.5.1), structured sufficiency assessment producing
a `VaultSufficiencyCheck` model (§8.5.2 / §8.5.4), and a unified evidence-rating
pass that produces the `EvidenceItem` schema written to Firestore (§8.5.5). Each
LLM call uses a dedicated model (see §8.9 and the `OPENAI_MODEL_*` env vars in §8.11).

### 8.5.1 Cross-Source Deduplication

**Problem:** A Telegram message from @financialjuice may report the same story
that NewsAPI captured from Reuters. Both appear as separate evidence items —
one in `researcher_evidence`, the other also in `researcher_evidence` (different
`source_platform` but same event).

**Detection strategies (ordered by reliability):**
1. **canonical_event_id match** — If both items share a `canonical_event_id`
   (linked via `mapping_dict`), they are about the same event. Highest confidence.
2. **silver_data_ref overlap** — If two Gold records reference the same Silver
   document, they are duplicates of the same ingested item.
3. **High semantic similarity** — If two items from different agents have
   similarity > 0.90 to each other (compare their embeddings), they likely
   describe the same event. Requires an extra embedding comparison step.
4. **Entity + timeframe overlap** — If extracted_entities and published_at are
   nearly identical across items, flag as potential duplicate for LLM verification.

**Action on duplicates:**
- Merge into a single evidence item.
- Keep the source with higher `reliability_score` as primary.
- Cite both sources in the evidence trail ("Reported by Reuters; corroborated by @financialjuice").
- The dedup reduces the Synthesis Lead's context window and prevents double-counting.

### 8.5.2 Sufficiency Assessment

This subsection introduces the conceptual checks performed during sufficiency assessment.
The structured rubric that formalizes these checks as a Pydantic model is defined in §8.5.4
(`VaultSufficiencyCheck`); the routing logic that consumes its output lives in §8.3.3.

Each sufficiency check evaluates four dimensions:
- **Source diversity:** Are at least 2 distinct source types represented? (maps to `covers_required_sources`)
- **Temporal coverage:** Does the evidence span the question's implied timeframe? (maps to `covers_time_window`)
- **Recency:** Is at least one signal less than 48 hours old? (maps to `has_recent_signal`)
- **Volume:** Are there at least 5 distinct evidence items after dedup? (maps to `has_minimum_signals`; threshold matches `AGENT_EVIDENCE_MIN_COUNT=5`)

The structured rubric (§8.5.4) returns these as boolean fields plus quantitative scores
(`avg_relevance_score`, `confidence_in_assessment`) and a list of `missing_dimensions`
describing which dimensions failed. The routing function in §8.3.3 reads `is_sufficient`
and `missing_dimensions` to decide whether to dispatch `vault_query_2` (refined query
targeting the gaps) or escalate to the reactive search microservice (`reactive_search`,
§8.12) when vault attempts are exhausted.

### 8.5.3 Sufficiency Verdict Logic

The original three-state verdict (`"sufficient" | "needs_more" | "insufficient"`)
is replaced with the structured rubric defined in §8.5.4. Each sufficiency check
produces a `VaultSufficiencyCheck` Pydantic model whose `is_sufficient` field
drives the routing function in §8.3.3.

When `is_sufficient == False`:
- If `vault_query_attempts < 2`, the routing function dispatches `vault_query_2`
  with refinement based on `missing_dimensions` (the second query is constructed
  to address the specific gaps identified, not a re-run of the same query).
- If `vault_query_attempts >= 2` and reactive search budget is available, the
  routing function dispatches `reactive_search` (§8.12) for fresh online articles.
- If both options are exhausted, the graph proceeds to synthesis with
  `low_confidence_flag = true` and explicit gaps recorded in `what_i_didnt_find`.

The previously-specified Kafka-based reactive ingestion loop (publishing to
`ingestion_triggers`) is replaced by the inline reactive search microservice
described in §8.12. The `ingestion_triggers` Kafka topic remains in use by the
data-pipeline layer (Section 2.4) for slow-path Bronze-layer fetches; the
hub itself no longer waits on it during a forecast.

### 8.5.4 Sufficiency Check Rubric

Each sufficiency check produces a `VaultSufficiencyCheck` Pydantic model:

```python
class VaultSufficiencyCheck(BaseModel):
    # Coverage dimensions
    has_minimum_signals: bool          # >= 5 relevant signals retrieved
    signal_count: int
    covers_time_window: bool           # signals span the question's implied timeframe
    covers_named_entities: bool        # all entities/regions/people in question represented
    covers_required_sources: bool      # at least 2 distinct source types

    # Quality dimensions
    avg_relevance_score: float         # 0-1
    has_recent_signal: bool            # at least one signal from last 48h
    has_conflicting_views: bool        # signals show debate, not just one side

    # Decision
    is_sufficient: bool
    missing_dimensions: list[str]      # e.g., ["recent_news", "expert_opinion"]
    confidence_in_assessment: float    # 0-1

    # Reasoning
    justification: str                 # one-sentence explanation
```

The routing function reads `is_sufficient` and `missing_dimensions` to decide
the next node. `missing_dimensions` is what makes `vault_query_2` distinct from
`vault_query_1` — the second query is constructed to address the specific gaps
identified.

### 8.5.5 Unified EvidenceItem Schema

All evidence — whether from the vault or from reactive search — is normalized
into a single shape before synthesis and before being written to Firestore:

```python
class EvidenceItem(BaseModel):
    # Identity
    evidence_id: str                    # uuid
    source_type: Literal[
        "vault_news", "vault_telegram", "vault_market",
        "vault_arxiv", "vault_hackernews", "vault_fred",
        "online_news", "online_blog"
    ]
    origin: Literal["knowledge_vault", "reactive_search"]

    # Content
    title: str
    snippet: str                        # first ~200 chars
    url: Optional[str]
    source_domain: str                  # e.g., "reuters.com"
    published_at: datetime
    fetched_at: datetime

    # Ratings
    relevance_score: float              # 0-1
    credibility_tier: Literal["tier_1", "tier_2", "tier_3"]
    recency_weight: float               # 0-1, exponentially decayed

    # Influence
    used_in_answer: bool
    impact_on_forecast: Literal["increases", "decreases", "neutral", "context_only"]
    impact_magnitude: float             # 0-1
    is_key_evidence: bool

    # Transparency
    justification: str                  # one sentence: "why I used this"
    rank: int                           # 1 = most influential
```

Mapping of `source_type` to the existing frontend display `type` field (so the
existing filter tabs keep working):

| source_type | Frontend type |
|---|---|
| `vault_news`, `online_news` | `news` |
| `vault_telegram`, `online_blog` | `social` |
| `vault_arxiv` | `expert` |
| `vault_market`, `vault_fred` | `market` |
| `vault_hackernews` | `social` |

---

## Section 8.6: Synthesis Lead

The final reasoning step. A single GPT-4o call that receives all deduplicated
evidence and produces the forecast report.

### 8.6.1 Input Assembly (Context Window Management)

The Synthesis Lead's prompt is constructed from:
1. **System prompt** — Role definition, scale anchors (similar to `cognitive_metadata.py`
   approach), output schema.
2. **Question context** — The structured intent, question tier, Polymarket market
   data (if Tier 1).
3. **Evidence summaries** — NOT raw full-text articles. Each evidence item is
   compressed to: source, title, date, executive_summary, key_findings,
   impact_level, reliability_score, and evidence_weight.
4. **Numerical data** — Market odds, momentum blocks, FRED anomalies, trend data
   from Market Bridge — included verbatim (small token footprint, high value).
5. **Dedup report** — Which items were merged, what sources corroborate each other.

**Token budget target:** < 8,000 tokens input to the Synthesis Lead. This leaves
ample room for CoT reasoning in the output. If evidence exceeds this budget,
lower-weight items are pruned first.

### 8.6.2 Output Schema

The Synthesis Lead returns a structured JSON:

```json
{
  "probability": 0.723,
  "confidence_score": 84,
  "consensus_strength": "Strong",
  "evidence_volume": "High",

  "executive_summary": "Based on recent legislative momentum...",

  "key_factors": [
    {
      "factor": "Legislative Momentum",
      "direction": "supports",
      "description": "The draft EU AI regulation has passed multiple committee stages...",
      "weight": 0.35
    },
    {
      "factor": "Expert Consensus",
      "direction": "supports",
      "description": "Policy analysts cite 75%+ likelihood based on...",
      "weight": 0.30
    },
    {
      "factor": "Industry Lobbying Risk",
      "direction": "opposes",
      "description": "The main downside risk is industry lobbying for delays...",
      "weight": 0.15
    }
  ],

  "evidence_trail": [
    {
      "rank": 1,
      "type": "news_article",
      "source": "Reuters",
      "title": "EU Parliament Accelerates AI Act Timeline",
      "published": "2026-04-10",
      "summary": "Committee vote passed with strong majority...",
      "impact_on_forecast": "Primary driver of high probability estimate",
      "signal_id": "uuid-for-reference"
    },
    {
      "rank": 2,
      "type": "telegram_signal",
      "source": "FinancialJuice (@financialjuice)",
      "date": "2026-04-12",
      "summary": "Breaking: EU Council signals fast-track...",
      "impact_on_forecast": "Corroborated legislative momentum"
    },
    {
      "rank": 3,
      "type": "market_data",
      "source": "FRED — VIX Volatility Index",
      "value": "Current: 18.2 | 30d change: -2.1",
      "impact_on_forecast": "Low market volatility supports stable regulatory environment"
    },
    {
      "rank": 4,
      "type": "community_consensus",
      "source": "Polymarket Community",
      "summary": "Strong bullish consensus (72% of 847 analyzed comments)",
      "impact_on_forecast": "Market participants align with news signals"
    }
  ],

  "what_i_didnt_find": [
    "Limited academic research found specifically on EU AI Act Q2 2026 timeline",
    "No Google Trends spike detected — topic not yet in mainstream public awareness"
  ],

  "market_comparison": {
    "anizai_probability": 0.723,
    "polymarket_odds": 0.685,
    "delta": 0.038,
    "explanation": "Anizai is 3.8% more bullish than the market..."
  },

  "reasoning_chain": "Step 1: Identified 12 relevant articles... Step 2: ...",

  "data_completeness": "full"
}
```

### 8.6.3 Evidence Trail Construction Rules

The Evidence Trail is the user-facing "chain of thought" — a curated list of the
top evidence items that drove the forecast, ordered by influence.

**INCLUDE (Primary Sources — things the user can mentally verify):**
- Top 2-3 news articles with highest `evidence_weight` (from Researcher).
  Show: publisher name, title, publish date, one-line summary.
- Telegram channel signals that contributed. Show: channel name, date, summary.
  No URL needed — the channel name IS the source identifier.
- FRED indicators that were relevant. Show: indicator name, current value,
  trend direction (change_7d), anomaly flag if triggered.
- Google Trends data. Show: keyword, trend direction, hype alert flag.
- ArXiv papers if relevant. Show: title, authors, one-line summary.
- Polymarket/HackerNews consensus (as a single aggregated item per platform).
  Show: consensus direction, comment volume analyzed, key argument summary.

**EXCLUDE (never show as individual items):**
- Individual HackerNews comments or Polymarket chat messages (already distilled
  into consensus vectors — showing individuals is noisy and misleading).
- Raw weather data points or flight density numbers (contextual inputs, not
  user-readable evidence).
- Internal dedup decisions or reactive ingestion details.

**INCLUDE (when relevant — absence as evidence):**
- "What I didn't find" items — when a vault returned zero results for an
  expected category. E.g., "No academic research found on this regulatory
  timeline" or "Low community discussion volume — forecast relies primarily
  on news sources."

---

## Section 8.7: Forecast Sessions (Persistence & Caching)

### 8.7.1 Storage Model

Forecast results are persisted to **Firestore**, not PostgreSQL. The Firestore
document tree under each session is the single source of truth for the frontend.

The hub writes to (under `sessions/{sessionId}/`):

| Path | Purpose | Lifecycle |
|---|---|---|
| `sessionResults/{sessionId}` doc | Final forecast result (`SessionResult` schema, §8.7.2) | One doc per completed session |
| `evidence/{evidenceId}` subcollection | Each evidence item used in the analysis | Multiple docs per session |
| `agentEvents/{eventId}` subcollection | Real-time chain-of-thought stream | Many docs during processing; can be compacted post-completion |
| `predictionSeries/{...}` subcollection | Time-series for the prediction overview chart | Existing schema, hub populates |
| `sentimentTimeSeries/{...}` subcollection | Expert/public sentiment over time | Existing schema, hub populates |
| `messages/{messageId}` subcollection | Assistant follow-up replies | Hub writes assistant messages on user follow-ups |

The `forecastQueries/{queryId}` collection (written by Express on `POST /sessions`) acts as the work queue. The hub claims pending docs and updates `status`.

The previously-specified `forecast_sessions` PostgreSQL table is **removed** from this spec.

### 8.7.2 SessionResult Schema (Firestore)

The full schema written to `sessionResults/{sessionId}`:

```python
class SessionResult:
    # Core forecast
    finalProbability: float              # 0-1
    confidence: float                    # 0-1

    # Display labels (deterministically derived from numerics)
    confidenceLabel: Literal["Low", "Moderate", "High"]
    consensusStrength: Literal["Weak", "Mixed", "Strong"]
    evidenceVolumeLabel: Literal["Low", "Moderate", "High"]

    # Headlines for BI cards
    bottomLineAnswer: str                # 1-2 sentence executive summary
    detailedExplanation: str             # paragraph-length
    summaryMarkdown: str                 # full markdown summary for chat panel

    # Insight captions per BI card
    marketComparisonInsight: str
    sentimentAnalysisInsight: str
    evidenceFeedSummary: str

    # Market data
    marketProbability: Optional[float]   # 0-1, null for Tier 2
    marketComparison: list[MarketDataPoint]

    # Agent reasoning artifacts
    keyFactors: list[KeyFactor]          # 3-5 drivers, ranked
    whatIDidntFind: list[str]            # explicit gaps
    reasoningChain: list[ReasoningStep]  # ordered persistent summary

    # Suggested follow-up actions (V1: simpler dynamic, 3 items)
    suggestedActions: list[SuggestedAction]

    # Metadata
    generatedAt: Timestamp
    agentVersion: str
    tier: Literal["tier_1", "tier_2"]
```

Threshold rules for label derivation:
- `confidence >= 0.8` → `"High"`; `0.5 <= confidence < 0.8` → `"Moderate"`; `confidence < 0.5` → `"Low"`
- Same pattern for `evidenceVolumeLabel` and `consensusStrength`.

### 8.7.3 Cache Hit Logic (Tier 1 only)

Caching now happens at the session level via `canonicalKey`. When a new Tier 1 question arrives:

1. Query Understanding resolves the canonical market id.
2. Express checks `sessions` collection for an existing session with this `canonicalKey` whose `sessionResults` doc was generated within the staleness window (default 4 hours) AND whose probability is within 3% of current Polymarket odds.
3. **Cache hit** — Express copies the existing `sessionResults` doc to the new session id (preserving the user's per-session ownership). Hub does NOT run.
4. **Cache miss** — Hub runs the full pipeline. New `sessionResults` doc created.

Tier 2 sessions never hit cache (each freeform question is unique). They always run the full pipeline.

### 8.7.4 Staleness & Refresh

A Tier 1 session is considered stale if:
- Time since `generatedAt` exceeds the staleness window (default 4h, configurable via `AGENT_STALENESS_WINDOW_HOURS`)
- **OR** new evidence with `impact_level >= 4` has been ingested into any vault for this `canonicalKey` since `generatedAt`
- **OR** Polymarket odds have moved more than 3% since the cached `marketProbability`

When a stale session is referenced (e.g., user opens it from history, or a new user asks the same question), the hub does a **delta refresh**: queries vaults only for evidence newer than `generatedAt`, merges with the cached evidence, re-runs Synthesis Lead, updates the doc.

### 8.7.5 Market Resolution Detection

When a Polymarket market resolves, a separate background process (out of scope for the hub itself, runs as a scheduled job in the Express BFF or as an Airflow DAG):

1. Updates `session.status = 'resolved'` for all sessions sharing that `canonicalKey`.
2. Sets `resolvedAt` timestamp and stores the actual outcome.
3. Followers (users who clicked "Track this forecast") receive a notification — delivered through Firestore (a write to a per-user `notifications/` collection that the frontend listens on).

No WebSocket needed; the existing Firestore listener architecture handles delivery.

### 8.7.6 Failed Session Retry

When a session terminates with `status = 'failed'` (errorMessage populated), the
frontend exposes a "Retry forecast" affordance on the failed session view. Retry
semantics are deliberately simple in V1:

- The frontend reads the failed session's original `question` text, then calls
  `POST /sessions` with that question and a **freshly generated** UUID v4 as the
  `idempotencyKey`. This produces a brand-new `sessions/{newSessionId}` doc and
  a brand-new `forecastQueries/{newQueryId}` doc with `status: 'pending'`.
- The failed session is **not mutated**. It remains in `status = 'failed'` as
  an audit trail. The new session has no link back to the failed one (no
  `parentSessionId`, no `retryOf` field) — the hub treats it as an independent
  query, including independent vault retrieval, independent reasoning, and
  independent cost (retry IS billed as a fresh forecast).
- The worker's existing claim logic (§8.8.1) handles the new pending doc with
  no special retry awareness; idempotency (§3.4 of the handoff doc) already
  prevents double-submission within the 60s window.

**No retry-eligibility logic in V1.** Permanent failure causes (vault has no
relevant data, query is unresolvable, plan limit) will keep failing on retry.
Sprint 26's error taxonomy will introduce per-error-code retry eligibility
(e.g., transient hub timeouts vs. permanent vault gaps), at which point this
section should be revisited.

---

## Section 8.8: Worker Pattern & Internal Endpoints

### 8.8.1 Architecture: Worker Pattern (Not API Gateway)

The Agentic Hub does **not** expose HTTP endpoints to the frontend. The original FastAPI gateway design has been replaced with a Firestore worker pattern that integrates more cleanly with the Firebase-based frontend.

**Flow:**

1. Frontend submits a forecast via `POST /sessions` to the **Express BFF** (existing endpoint, not part of the hub).
2. Express creates a `sessions/{id}` document with `status: 'queued'` and a `forecastQueries/{queryId}` document with `status: 'pending'`.
3. The hub runs a long-lived worker process that listens on `forecastQueries where status == 'pending'` via the Firestore listener API (`firebase_admin.firestore.Client.collection().on_snapshot()`).
4. When a pending doc appears, the worker claims it (atomic transaction: read `status == 'pending'`, write `status = 'claimed'`, write `claimedAt` and `claimedBy` worker id) to prevent double-processing.

   In parallel with this `forecastQueries` lifecycle, the worker also transitions the corresponding `sessions/{id}.status` through `queued → claimed → running` so the frontend renders distinct stages (queued spinner → "starting analysis" → live chain-of-thought).
5. The worker runs the LangGraph pipeline. Throughout, it writes events to `agentEvents` and partial results to subcollections.
6. On completion, the worker writes the final `sessionResults` doc and updates `forecastQueries.status = 'done'` and `session.status = 'done'`.
7. The frontend, listening to the session's subcollections via the Firebase client SDK, receives all updates in real time.

### 8.8.2 Hub Endpoints (Internal Only)

The hub exposes one HTTP endpoint, used only for health checks and operational monitoring (not by the frontend):

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/health` | Health probe (returns `{status: "healthy", workerId, claimedSessions: int}`) | None |
| GET | `/metrics` | Prometheus metrics endpoint (LangGraph node durations, LLM cost, queue depth) | None (internal) |

No `/api/v1/forecast`, no `/ws/v1/...`, no WebSocket. The previous spec's HTTP/WebSocket API is **deleted**.

### 8.8.3 Follow-up Conversations

Follow-ups are also handled via Firestore listener:

1. User types a follow-up in the chat panel; frontend calls `POST /sessions/:id/messages` (Express BFF endpoint).
2. Express writes a new doc to `sessions/{id}/messages` subcollection with `role: 'user'`.
3. The hub's worker (same process, second listener) watches for new user messages on completed sessions.
4. On a new user message, the worker runs a **lightweight subgraph** (no full vault re-search by default) that loads the parent session's `SessionResult`, top `keyFactors`, top evidence items, and the message history.
5. If the follow-up clearly requires fresh evidence, the agent escalates to vault query / reactive search using the same budget rules but with reduced limits (one search max, smaller time window).
6. The agent writes the assistant reply to the messages subcollection.

**Budget for follow-ups:** ~5-7 seconds total (configurable via `AGENT_FOLLOWUP_BUDGET_MS`, default 6000). Follow-ups also write to `agentEvents` but with `parentMessageId` set, so the existing reasoning-panel UI works for follow-ups.

**Streaming:** Follow-up responses arrive as a complete message for V1. Token-by-token streaming is deferred (would require a different mechanism than Firestore for token-level updates).

### 8.8.4 Clarification Resolution Endpoint

The Express BFF (not the hub) gains a new endpoint:

```
POST /sessions/:id/clarify
Body: { chosenCandidateId: string | null }
```

`null` means "freeform / treat as Tier 2". Express updates the session's `canonicalKey` (set or null), changes status back to `queued`, writes a new `forecastQueries` doc to re-trigger the agent, and clears `clarificationCandidates`. The hub picks up the re-queued doc, sees `skip_matching_step = true` in the state, and proceeds with the locked-in match.

---

## Section 8.9: Technology Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Orchestration | LangGraph (langgraph >= 0.2) | Graph-based state machine matches the multi-node topology; built-in parallel execution for agents |
| LLM — Query Understanding | GPT-4o-mini | Cheap, fast, structured output. ~$0.001 per call (`OPENAI_MODEL_QUERY_UNDERSTANDING`) |
| LLM — Sufficiency Check | GPT-4o-mini | Structured rubric eval; runs up to 2× per session (`OPENAI_MODEL_SUFFICIENCY_CHECK`) |
| LLM — Evidence Rating | GPT-4o-mini | Unified rating pass on all evidence; populates `EvidenceItem` fields (`OPENAI_MODEL_EVIDENCE_RATING`) |
| LLM — Synthesis Lead | GPT-4o | Quality-critical step; CoT reasoning requires stronger model. ~$0.02 per call (`OPENAI_MODEL_SYNTHESIS`) |
| LLM — Suggested Actions | GPT-4o-mini | Generates the post-forecast action list (`OPENAI_MODEL_SUGGESTED_ACTIONS`) |
| LLM — Follow-up Chat | GPT-4o-mini | Conversational, low-stakes, context already assembled (`OPENAI_MODEL_FOLLOWUP`) |
| Embeddings | OpenAI text-embedding-3-small | Must match existing 1536-dim HNSW indexes in vaults |
| Worker Process | Firebase Admin SDK (`firebase-admin>=6.0`) | Firestore listener on `forecastQueries` claims pending docs; replaces FastAPI gateway (§8.8) |
| Real-time Delivery | Firestore subcollections (`agentEvents`, `evidence`, `messages`) | Native real-time push to frontend's existing Firestore listeners; replaces WebSocket |
| Database Access | Existing `persistence/` modules + `persistence/reactive_cache.py` | Read-only on vault tables; read-write on `reactive_article_cache` (§8.12) |
| Reactive Search | New `reactive_search/` microservice (Tavily/Brave + allowlist) | Inline online article search at agent runtime; replaces Kafka-based slow path for hub queries (§8.12) |

### 8.9.1 Estimated Cost Per Forecast (Full Cycle)

> Token counts and costs are pre-implementation estimates. Sprint 19-20 will measure actual usage and update these figures.

| Step | Model | Est. Input Tokens | Est. Output Tokens | Cost |
|------|-------|-------------------|--------------------| -----|
| Query Understanding | GPT-4o-mini | ~500 | ~300 | $0.0002 |
| Embedding | text-embedding-3-small | ~100 | — | $0.00002 |
| Sufficiency Check | GPT-4o-mini | — | — | (estimate, to be measured Sprint 19-20) |
| Evidence Rating | GPT-4o-mini | ~3,000 | ~500 | $0.0006 |
| Synthesis Lead | GPT-4o | ~6,000 | ~2,000 | $0.03 |
| Suggested Actions | GPT-4o-mini | — | — | (estimate, to be measured Sprint 19-20) |
| **Total (sufficient on first vault query)** | — | — | — | **~$0.03** |
| **Total (one extra sufficiency check + reactive search)** | — | — | — | **~$0.05** |

---

## Section 8.10: Directory Structure

All Agentic Hub code lives under `data-pipeline/agent/`, following the existing
project convention of top-level functional directories (ingestion/, processing/,
persistence/, etc.).

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

## Section 8.11: Configuration (.env Additions)

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

---

## Section 8.12: Reactive Search Microservice

Reactive search is a **new microservice** that fetches fresh articles from the open web when the vault doesn't have enough evidence to answer a question well. It is conceptually distinct from the existing data pipeline's "reactive ingestion" feature (Section 2.4), which fires Bronze-layer Kafka triggers to pull new data into the vault. The two are complementary:

| Feature | Layer | Purpose | Result |
|---|---|---|---|
| **Reactive Ingestion** (Section 2.4) | Bronze | Trigger producers to fetch missing data into the vault for future queries | Persists into vault via Bronze→Silver→Gold; takes seconds to minutes |
| **Reactive Search** (this section) | Agent / hub | Fetch fresh article snippets at agent runtime when vault is insufficient | Used inline for the current question; ephemeral in the agent's context, cached briefly in `reactive_article_cache` |

Together they form Anizai's full "reactive" capability: the slow path (ingestion) keeps the vault fresh over time; the fast path (search) handles immediate gaps in real-time.

### 8.12.1 Service Boundary

Reactive search runs in the same process as the agent worker (no separate container needed). It exposes a single internal Python function:

```python
def reactive_search(
    query: str,
    time_window_days: int = 7,
    max_results: int = 5,
    timeout_ms: int = 6000,
) -> ReactiveSearchResult:
    """Returns up to max_results EvidenceItems fetched from allowed domains."""
```

Called by the `reactive_search` node when the second sufficiency check fails.

### 8.12.2 Source Allowlist

A static config file: `data-pipeline/agent/config/source_allowlist.json`. Maps domains to credibility tiers and snippet-extractability flags.

```json
{
  "version": "2026-04",
  "last_probed": "2026-04-15",
  "domains": {
    "reuters.com": {"credibility_tier": "tier_1", "allow_snippet": true},
    "apnews.com": {"credibility_tier": "tier_1", "allow_snippet": true},
    "bbc.com": {"credibility_tier": "tier_1", "allow_snippet": true},
    "ft.com": {"credibility_tier": "tier_1", "allow_snippet": true},
    "bloomberg.com": {"credibility_tier": "tier_1", "allow_snippet": "headline_only"},
    "csis.org": {"credibility_tier": "tier_2", "allow_snippet": true},
    "brookings.edu": {"credibility_tier": "tier_2", "allow_snippet": true},
    "carnegieendowment.org": {"credibility_tier": "tier_2", "allow_snippet": true}
  }
}
```

The probe script (`reactive_search/probe.py`) runs quarterly. For each domain:
1. Search for a recent article via the chosen search API
2. Fetch the URL and check whether title + meta description + first paragraph are extractable
3. Update the JSON config with results

The hub is allowed to query domains not in this list (some search APIs return broader results), but **only domains in the allowlist with `allow_snippet: true` may have their snippets used as evidence**. Other results are dropped.

### 8.12.3 Cache Table Schema

A new PostgreSQL table:

```sql
CREATE TABLE IF NOT EXISTS reactive_article_cache (
    cache_id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Lookup keys
    query_text            TEXT            NOT NULL,
    query_embedding       vector(1536),                    -- For semantic-similarity hits
    query_hash            TEXT            NOT NULL,        -- For exact-match hits

    -- Article data (JSONB array of EvidenceItem-shaped objects)
    articles              JSONB           NOT NULL,
    article_count         INTEGER         NOT NULL,

    -- Metadata
    fetched_at            TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    expires_at            TIMESTAMPTZ     NOT NULL,        -- fetched_at + TTL
    hit_count             INTEGER         NOT NULL DEFAULT 0,

    -- Search params (for debugging)
    search_params         JSONB
);

CREATE INDEX idx_rac_query_hash ON reactive_article_cache (query_hash);
CREATE INDEX idx_rac_expires ON reactive_article_cache (expires_at);
CREATE INDEX idx_rac_embedding ON reactive_article_cache USING hnsw (query_embedding vector_cosine_ops);
```

### 8.12.4 Cache Lookup Logic

1. **Exact match** — hash the normalized query, look up by `query_hash`. If hit and not expired, increment `hit_count` and return.
2. **Semantic match** — embed the query, find entries with cosine similarity > 0.92 using the HNSW index, where `expires_at > NOW()`. If hit, return.
3. **Miss** — call the search API, fetch snippets, filter by allowlist, store result, return.

### 8.12.5 Time Window Logic

The agent passes `time_window_days` based on question semantics:
- "What's happening with X right now?" → 1-3 days
- "How has the situation evolved?" → 7 days (default)
- "Has this happened before?" → 30+ days, no upper bound

Determined during `query_understand` and passed in `ForecastState`.

### 8.12.6 Budget & Degradation

- Default total budget: 6 seconds (configurable: `AGENT_REACTIVE_TIMEOUT_MS`)
- Per-call budget: 2s for the search API call, 3s for parallel snippet fetching of top 5 URLs (each with 3s timeout), 1s buffer
- **Graceful degradation:** if the budget is exceeded, the agent proceeds with whatever was fetched (even if just titles, no snippets). The synthesis node notes the degradation in `whatIDidntFind`.
- **Hard limit:** maximum 1 reactive search per session (configurable: `AGENT_REACTIVE_MAX_PER_SESSION`).

### 8.12.7 Cleanup

A nightly job deletes expired entries:

```sql
DELETE FROM reactive_article_cache WHERE expires_at < NOW();
```

Runs via Airflow DAG (or simple cron). Non-critical; if it fails, expired entries simply don't get reused (they're already filtered out at lookup time).
