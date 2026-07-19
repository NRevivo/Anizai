# Anizai — Cabinet Context File

> Single source of truth for an AI agent that needs full project context.
> Compiled 2026-06-13 from CLAUDE.md, the sprint skills, `task_plan.md`,
> `task_plan_implementation.md`, the three `agentic_hub_*` docs, and the
> pipeline background docs (`pipeline_core.md`, `storage_and_agent.md`,
> `data_contracts_and_sources.md`, `deployment_and_sprints.md`,
> `anizai_handoff_consolidated.md`, `task_plan_implementation_archive.md`).
>
> **Scope note:** Reddit and PredictIt producers do **NOT** exist and are not
> planned — ignore any spec text that treats them as upcoming. Reddit is
> BLOCKED (API pre-approval), PredictIt's API was permanently shut down.

---

## 1. Project Vision & Purpose

Anizai is a **forecasting intelligence platform**. It ingests heterogeneous
real-world signals (news, research, social discourse, prediction-market odds,
macro indicators, weather, flight activity), refines them through a medallion
data pipeline into queryable "vaults", and exposes them through an **Agentic
Intelligence Hub** — a LangGraph reasoning layer that turns a user's
natural-language question into a structured, multi-sourced, confidence-scored
**probability forecast** with a chain-of-thought evidence trail.

The end product the user sees: ask "Will X happen by date Y?", get back a
probability (0–1), a confidence label, key driving factors, an evidence feed,
a market comparison (vs. Polymarket where one exists), sentiment-over-time, and
an explicit "what I didn't find" gap list.

Two question tiers:
- **Tier 1 — Polymarket-backed.** Question maps to an active Polymarket market
  (liquidity pool > $10K). Has hard odds, price history, momentum, and a
  `canonicalKey`. Eligible for tracking, refresh, "Active Forecasts" sidebar.
- **Tier 2 — Freeform.** A legitimate forecastable question with no matching
  market. Full research still runs; persisted with `tier:"tier_2"`,
  `canonicalKey:null`, `marketProbability:null`. Not trackable/refreshable.

Guiding rule: **"One Question, One Thread."** One question per session;
different question = new thread.

---

## 2. Technical Stack

| Layer | Technology |
|---|---|
| Streaming bus | **Apache Kafka**, KRaft mode (Zookeeper-less) |
| Stream processing | **Apache Flink** (PyFlink 1.19), Silver + Gold jobs, async I/O to OpenAI, 60s checkpointing |
| Storage | **PostgreSQL** with **pgvector (HNSW)** + **TimescaleDB** (hypertables) |
| AI enrichment | **OpenAI** — GPT-4o (synthesis), GPT-4o-mini (classification/rating), `text-embedding-3-small` (1536-dim) |
| Agent orchestration | **LangGraph** StateGraph (`langgraph>=0.2`, `langchain>=0.3,<0.4`) |
| Result delivery | **Firestore** (Firebase Admin SDK) — worker pattern, no FastAPI/WebSocket |
| Frontend/BFF | React client + **Express BFF** (separate "Friend 1" partner scope) |
| Orchestration/scheduling | **Airflow** DAGs; **Cloud Scheduler** (cloud) |
| Containerization | **Docker Compose** (local) → **GKE** (cloud), env-parity |
| Cloud | **GCP** — GKE, self-hosted Postgres/Kafka StatefulSets, GCS (Bronze lake, backups), Secret Manager, Workload Identity. Project `anizai-pipeline`; Firestore lives on separate project `anizai-ai` (cross-project WI) |
| Monitoring | Prometheus + Grafana + Alertmanager (Gmail SMTP), Cloud Logging/Monitoring |
| Language | Python; venv at `data-pipeline/venv/` (never system Python) |

Dependency policy: `requirements.txt` (range-pinned, human-edited) +
`requirements.lock` (exact, `pip freeze`) kept in sync and committed together;
Docker/onboarding install from the lock.

---

## 3. Architecture — Medallion (Bronze/Silver/Gold)

Flow: **Producers → Kafka (Bronze) → Flink Silver Job → Flink Gold Job → Postgres Vaults → Agentic Hub → Firestore → Frontend.**

### Kafka topic hierarchy (`[Layer].[Status].[Source_Group]`)
- **Bronze (one topic per source, isolation):** `ingest.bronze.{polymarket, telegram, hackernews, newsapi, arxiv, fred, googletrends, openweather, opensky}` (plus spec-listed-but-nonexistent `predictit`, `reddit`).
- **Silver (aggregated by family schema):**
  - `process.silver.social_pulse` — Telegram, HackerNews, Polymarket comments
  - `process.silver.global_news` — NewsAPI, ArXiv
  - `process.silver.structured_metrics` — FRED, Polymarket prices, OpenWeather, OpenSky
- **Gold (AI-enriched, vector-ready):** `serve.gold.{social_pulse, global_news, structured_metrics}`
- **System topics:** `ingestion_triggers` (reactive ingestion requests), `dead-letter-queue` (failed schema validation — never silently dropped).

Every message is wrapped in a **NDJSON** envelope: `event_id`, `trace_id`,
`producer_timestamp`, `schema_version`, `payload`. Retention: Bronze 7d,
Silver 3d, Gold 3d, `structured_metrics` compacted (latest value always live).

### Silver Job (Flink) responsibilities
- **Keyword Sniper** filter (coarse quality gate; see §10/keyword note).
- **Dual-store persistence:** full-text docs → Knowledge Vault; social/comment trees → Social Vault.
- **SHA-256 dedup** (content hashing across overlapping aggregators).
- **Real-time translation** of non-English (e.g. Hebrew/Russian Telegram) to English.
- Schema-validation failures → `dead-letter-queue`.

### Gold Job (Flink) responsibilities
- **Semantic enrichment:** temporal consensus bundling (4-hour social blocks → one Consensus Vector via GPT-4o); cognitive metadata extraction (`impact_level` 1–5, `urgency_level` 1–5, `uncertainty_index` 0–1, `extracted_entities`).
- **Structural enrichment:** keyed-state **Momentum Block** (`change_24h/7d/30d` precomputed so the agent never does runtime math); FRED automation triggers (`T10Y2Y<0`→impact 5, `VIXCLS>30`→impact 5, price >5%/24h → grounding flag).
- **Two-stage NewsAPI gate (Phase 7B):** keyword sniper → semantic rescue (`compute_semantic_rescue()` in `GlobalNewsGoldFunction`); articles failing both are dropped.

### The four vaults (PostgreSQL)
| Vault | Table(s) | Role |
|---|---|---|
| Knowledge (Silver doc store) | `knowledge_vault` | Full-text articles/papers (JSONB), `full_text_raw`, drill-down via `silver_data_ref` |
| Social (Silver discussion store) | `social_vault` | Raw comment trees, threaded discourse, drill-down |
| Vector intelligence (Gold) | `knowledge_vectors` (news/arxiv/telegram), `social_vectors` (polymarket/hackernews) | pgvector HNSW — **two separate indexes** (mixing heterogeneous types degrades recall) |
| Momentum (Gold time-series) | `momentum_vault` | TimescaleDB hypertables; odds, FRED, weather, trends + Momentum Blocks |
| Infrastructure | `mapping_dict` | platform-id ↔ `canonical_event_id` linkage (bridges Polymarket↔Reuters etc.) |

Newer hub tables: `reactive_triggers_log` (Sprint 23), and the **deferred**
`reactive_article_cache` (spec §8.12, not built — Future Enhancement 1).

---

## 4. Data Sources — Status of each

9 producers exist (deployed in Phase 9D/C4). Reddit + PredictIt do not.

| Source | Bronze→vault | Status | Notes |
|---|---|---|---|
| **Polymarket** | momentum_vault + social_vectors | **Active** | Odds pulse + comments. Comments **disabled** via `POLYMARKET_COMMENTS_ENABLED=false` (KG-PHASE-9.5-4: `/comments` endpoint broke, `entity_entity_type` enum unknown). WebSocket streaming pattern source. |
| **FRED** | momentum_vault | **Active** | 9 indicators (FEDFUNDS, CPIAUCSL, UNRATE, CSUSHPINSA, DCOILWTICO, GASREGW, DHHNGSP, VIXCLS, T10Y2Y). REST polling pattern source. |
| **NewsAPI** | knowledge_vault + knowledge_vectors | **Active** | Provider migrated org→thenewsapi.com (Sprint 21.5)→**newsapi.ai / Event Registry** (Phase 7A, full body `articleBodyLen=-1`). `source_name="newsapi"`, topic `BRONZE_NEWSAPI` preserved. Keyword-filtering pattern source. |
| **Telegram** | knowledge_vectors | **Active** | 7 channels (@abualiexpress, @news100shatach, @Faytuks_Network, @clashreport, @financialjuice, @disclosetv, @intelslava). MTProto streaming; media-only msgs ignored. Translated to English in Silver. |
| **HackerNews** | social_vectors | **Active** | Algolia API; stories `points>50`, top-10 comments. |
| **ArXiv** | knowledge_vectors | **Active** | cs.AI/LG/CY, econ.GN, q-fin.ST, q-bio.PE, stat.AP. 3s TOS delay. |
| **OpenWeather** | momentum_vault | **Active** | Strategic hotspots (Taipei, Tokyo, Tel Aviv, Hormuz, Suez...). `CONDITION_ID_TO_SEVERITY` mapping, disaster/supply/shipping triggers. |
| **Google Trends** | momentum_vault | **BLOCKED at runtime** | pytrends 404 (KG-PHASE-C-7/9.5-5); 4.9.2 is latest, no fix. Producer raises-on-0% so Airflow marks `failed`. 0 Bronze messages. |
| **OpenSky** | momentum_vault | **BLOCKED at runtime (cloud)** | `ConnectTimeoutError` from GKE node (KG-PHASE-C-6). 0 Bronze in cloud. Raises-on-0%. |
| ~~PredictIt~~ | — | **Does not exist** | API permanently shut down; `processing/divergence.py` removed. No second prediction market → no Market Divergence Alerts. |
| ~~Reddit~~ | — | **Does not exist** | Blocked on API pre-approval, deferred indefinitely. |

---

## 5. Current Phase & Sprint Status

**Today's anchor:** Pipeline (Phases 0–7) DONE; Cloud (Phase 9) DONE; Cluster
robustness (Phase 9.5) DONE; **Agentic Hub (Phase 8) is the active work** with
Sprints 18–23 done and 24–27 open. Phase 10 (calibration) not started.

### Completed
- **Phases 0–6 (Sprints 1–17):** full pipeline foundation — shared utils (`config/`, `utils/kafka_utils.py`, `utils/validators.py`, `utils/db.py`), Docker stack, `init.sql`, all source vertical slices, Flink Silver/Gold, mapping_dict, reactive-ingestion consumer, Airflow DAGs, Prometheus/Grafana monitoring, E2E validation.
- **Phase 7 (7A/7B/7C, done 2026-05-09):** NewsAPI → newsapi.ai full body; two-stage keyword-sniper + semantic rescue (`DEFAULT_THRESHOLD` 0.09→0.15, `GOLD_SEMANTIC_RESCUE_THRESHOLD=0.35`, 10 noisy keywords removed, `sniper_reference_vector.npy`); scraper deleted, `scrape_attempted` column dropped (migration 002). Calibration deferred to **Phase 7B.5** (unblocked — 616+ cloud vault rows).
- **Phase 9 / "Phase C" (C1–C5, done 2026-05-10):** parity port to GKE. 9A foundation, 9B Postgres+Kafka StatefulSets (self-hosted TimescaleDB; Cloud SQL rejected), 9C Flink, 9D Airflow + 9 producers + trigger consumer, 9E agent worker + monitoring + backups. Phase 9 E2E PASSED (local FE → anizai-ai Firestore → cloud agent Tier 1 → cloud Postgres → GPT-4o → render).
- **Phase 9.5 (Stages A/B/C, done 2026-05-20):** root-caused May 11–18 silence (Kafka writing to ephemeral `/tmp` → `KAFKA_LOG_DIRS` fix); Postgres-DNS resilience; centralized `utils/openai_client.py` (`max_retries=5`) + `utils/retry.py`; producers raise-on-0%; full monitoring (kafka/postgres exporters, 13 alert rules, Alertmanager, 2nd Grafana dashboard); `cluster_operations_guide.md`. Surfaced KG-PHASE-9.5-1…9.

#### Phase 8 closed sprints (Agentic Hub)
- **Sprint 18 (8A) Foundation:** Firestore worker pattern, atomic claim, stub `SessionResult` (`finalProbability=0.5`). Hub config in **new** `agent/config/settings.py` (separate from pipeline config).
- **Sprint 19 (8B) Vault Retrieval:** the three retrieval agents, parallel dispatch (ThreadPoolExecutor, 15s/agent), module-level graph singleton, OpenAI structured-output JSON-schema strict.
- **Sprint 20 (8B) Synthesis + Firestore:** GPT-4o synthesis + GPT-4o-mini rate_evidence; top-level `sessionResults/{id}` writes; WriteBatch 500-cap. E2E cold 36.3s/warm 32.2s, ~$0.025–0.03/query.
- **Sprint 21 (8C) Tier 2 + Clarification:** `MARGIN_THRESHOLD=0.10`; clarification candidates; Express `requeueClarifiedSession` resume path. AGENT_VERSION `0.4.0-sprint21-clarification-tier2`.
- **Sprint 21.5:** out-of-band NewsAPI provider maintenance (org→thenewsapi).
- **Sprint 22 (Revised) Foundation Fixes (done 2026-05-26):** Polymarket fuzzy-match resolver (`find_polymarket_market_by_question`, pg_trgm 0.85), `marketProbability`/`marketComparison`/`predictionSeries` wiring, `sentimentTimeSeries` bucketing, `canonicalKey` on session doc. Closed KG-PHASE8-12 + KG-PHASE8-22. All 5 BI cards render on Tier 1.
- **Sprint 23 (New) Producer-trigger Infrastructure (closed 2026-05-26, 8/10):** `NewsAPIProducer.run_reactive()`, `newsapi` registered as 4th reactive trigger source in `ingestion_trigger_consumer.py`, `reactive_triggers_log` table + persistence module, `trigger_reactive_ingestion` node (built **in isolation**, not yet wired into graph). T23.9 Gate 3 implemented but Windows-`skipif` (kafka-python-ng race, KG-PHASE8-25); T23.10 E2E deferred to Sprint 26 (T26.10.5).

### Current / Open (Phase 8 re-planned 2026-05-23 → `agentic_hub_implementation_phase8_revised.md`)
Pre-test path: **22 → 23 (parallel-able) → 24 → 25 → 26 → 2-day initial test → 27.**
- **Sprint 24 — Follow-up Conversations (Revised):** second Firestore listener on `messages` subcollection; lightweight subgraph; **answer-from-context only** (escalation deferred to Future Enhancement 2). Budget 5–7s.
- **Sprint 25 — Suggested Actions + Chain-of-Thought Events:** 3 `suggestedActions` (one GPT-4o-mini call post-synthesis) + continuous `agentEvents` stream; `agent/events.py` helpers.
- **Sprint 26 — Pre-Test Hardening:** KG-PHASE8-17 (OpenAI usage logging on synthesize+build_embedding — gates cost measurement), Prometheus agent metrics, git-short-hash `agentVersion`, KG-PHASE8-16 latency analysis (analysis only), KG-PHASE8-20 ClarificationCandidate cleanup, Postgres retry wrapper on momentum_vault calls, and **wiring `trigger_reactive_ingestion` into `agent/graph.py`** (T26.7 — needs both Sprint 22 & 23).
- **Initial test (~2 days, cloud):** gated on Sprint 26. Goal: real production cost numbers + forecast quality on live vault data.
- **Sprint 27 (New) — Post-Test Polish + Phase 8 Closeout:** KG-PHASE8-7, KG-PHASE8-15, `error_handler.py`, Firestore retry wrapper, stress/restart/load tests, structured JSON logging, graceful shutdown, docs pass, Phase 8 State Ledger. Tasks 27.12+ from test findings.

### Next (planned, not started)
- **Phase 10 — Calibration & Backtesting (10A–10E):** standalone research harness submitting Polymarket-anchored questions via `forecastQueries`, polling Polymarket for resolutions, computing Brier scores + calibration curves. **Zero agent changes.** New Cloud SQL + Cloud Run in `anizai-pipeline`; operator-only React UI. Sensitive to single-forecast latency (KG-PHASE8-16).
- **Phase 7B.5** (keyword sniper calibration — unblocked).
- **Cloud Scheduler** still PAUSED (Ron resumes manually; checklist in `cluster_operations_guide.md` §4).

---

## 6. Agentic Hub — Design & Current Status

Code lives under `data-pipeline/agent/`. Spec is `agentic_hub_spec.md` §8;
**active plan is `agentic_hub_implementation_phase8_revised.md`** (supersedes
Sprints 22–26 of the original `agentic_hub_implementation.md`).

### What it is / isn't
Read-only over all vault tables; write access only to Firestore session
collections (+ the deferred `reactive_article_cache`). Does NOT modify vault
data, does NOT replace the pipeline, does NOT recompute Gold enrichment scores.

### Graph topology (`agent/graph.py`, LangGraph StateGraph over `ForecastState`)
```
START → claim_session (Node 0, atomic claim, status queued→claimed→running)
      → query_understand (Node 1, GPT-4o-mini → tier, structured_intent, polymarket_search_terms)
      → [ambiguous?] ── Yes → write_clarification (END until user picks)
                     └─ No  → build_embedding (Node 2, text-embedding-3-small)
                            → vault_query (Node 3, parallel: Researcher + Pulse + Market)
                            → sufficiency_check (Node 4, GPT-4o-mini VaultSufficiencyCheck)
                            → [sufficient?] ── Yes → rate_evidence → synthesize
                                            └─ No, attempts<2 → vault_query_2 (refined via missing_dimensions)
                                                              → sufficiency_check (2nd)
                                                              └─ still No → trigger_reactive_ingestion (V1, fire-and-forget) → synthesize
                            → synthesize (Node 6, GPT-4o)
                            → generate_suggested_actions (Node 6.5, Sprint 25)
                            → write_to_firestore (Node 7) → END
```
`agentEvents` emitted continuously by every node.

### The three retrieval agents (`agent/agents/`) — Python functions, NOT LLMs
- **The Researcher** (`researcher.py`): `knowledge_vectors` HNSW + `knowledge_vault` drill-down. Composite rank `0.6*similarity + 0.25*impact + 0.15*recency`. Returns `ResearcherEvidence`.
- **The Pulse Analyst** (`pulse_analyst.py`): `social_vectors` + `social_vault`. Splits Polymarket consensus vs HackerNews. `community_sentiment` is a raw float −1..1 (T19.3 correction). Returns `PulseEvidence`.
- **The Market Bridge** (`market_bridge.py`): `momentum_vault` (odds, FRED anomalies, trends, weather/flight) + `mapping_dict`. Returns `MarketEvidence` (`polymarket=None` for Tier 2).

LLM reasoning happens only in `sufficiency_check`, `rate_evidence`, `synthesize`,
`query_understand`, `generate_suggested_actions`, follow-up `answer_from_context`.

### Key models
- **`ForecastState`** (TypedDict, `agent/state.py`): raw_question, session_id, structured_intent, polymarket_market, query_embedding, the three evidence packages, `vault_query_attempts`, `sufficiency_checks[]`, `reactive_triggers_emitted`, synthesis_result, tier, suggested_actions, etc.
- **`VaultSufficiencyCheck`** (Pydantic, §8.5.4): coverage booleans + `avg_relevance_score`, `confidence_in_assessment`, `is_sufficient`, `missing_dimensions[]`. Routing: sufficient = all coverage pass AND `avg_relevance>=0.6` AND `confidence>=0.5`. `missing_dimensions` is what makes vault_query_2 a *refined* query, not a re-run.
- **`EvidenceItem`** (Pydantic, §8.5.5): unified shape written to Firestore — `source_type`, `origin`, ratings (`relevance_score`, `credibility_tier`, `recency_weight`), influence (`impact_on_forecast`, `is_key_evidence`, `rank`), `justification`.
- **`SessionResult`** (§8.7.2): `finalProbability`, `confidence` + derived labels (`confidence>=0.8`→High, 0.5–0.8→Moderate, <0.5→Low), `marketProbability` (null Tier 2), `keyFactors`, `whatIDidntFind`, `reasoningChain`, `suggestedActions`, `tier`.

### Firestore document tree (under `sessions/{sessionId}/`)
`sessionResults/{id}` (top-level per server contract — KG-PHASE8-13 drift),
`evidence/`, `agentEvents/`, `predictionSeries/`, `sentimentTimeSeries/`,
`messages/`. The `forecastQueries/{queryId}` collection (written by Express on
`POST /sessions`) is the work queue.

### Worker pattern (no HTTP API to frontend)
Long-lived worker listens `forecastQueries where status=='pending'`, atomically
claims (read pending → write claimed + claimedBy/claimedAt), runs the graph,
streams writes, sets `done`. Status lifecycle owned by hub: `queued → claimed →
running → done | failed | awaiting_clarification`. Only internal HTTP: `/health`,
`/metrics`. Clarification resolved via Express `POST /sessions/:id/clarify`;
follow-ups via `POST /sessions/:id/messages` (second listener, Sprint 24).

### Reactive capability (V1)
Original Tavily/Brave **reactive search microservice (spec §8.12) is DEFERRED**
(Future Enhancement 1). V1 uses **producer-trigger, fire-and-forget**
(Sprint 23): node emits a Kafka message to `ingestion_triggers` → NewsAPI
producer's `run_reactive()` fetches targeted articles → vault enriched async
for the *next* session. Keyword set built deterministically (no LLM): entities +
missing_dimensions, deduped, capped at 8. Cap `AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION=1`.

---

## 7. Key Architectural Decisions (must be preserved)

1. **Service isolation (CLAUDE.md §3.3).** Producers ingest only; Flink transforms only; persistence logic only in `persistence/` modules. A producer writing to a DB or doing transformation is an architectural violation — stop and propose a compliant alternative.
2. **NDJSON everywhere** for Kafka messages (§3.4). Deviations need explicit approval.
3. **DLQ, never drop.** Silver-validation failures route to `dead-letter-queue` (§3.5).
4. **DRY / centralized shared logic** — `utils/db.py`, `utils/kafka_utils.py`, `utils/validators.py`, prompts in `prompts/`, OpenAI through `utils/openai_client.py`, retries through `utils/retry.py`.
5. **Two separate vector tables** (`knowledge_vectors` vs `social_vectors`), independent HNSW indexes — mixing heterogeneous object types degrades recall.
6. **Momentum Block precomputed in Flink** — agent answers "what's the trend?" without runtime math.
7. **Worker pattern over FastAPI/WebSocket** — Firestore is the message bus; Admin SDK writes; frontend reads via real-time listeners.
8. **Hub→frontend only through Firestore writes** (frontend-integration contract). No other side channel.
9. **LangGraph node discipline (agent-design skill):** one node/one job; state is the only contract (no direct cross-node calls); routing lives in named routing functions that read state only (never call LLMs); budget checked before every external call and consumed after; graceful degradation sets `degraded:True` rather than blocking; every node emits start + complete/failed events; cycles capped by a counter (`vault_query_attempts`).
10. **Determinism around LLMs** — label derivation, routing, sequencing all deterministic; only the LLM call itself is non-deterministic.
11. **Probability convention: 0–1 floats everywhere**, converted to 0–100 only at the render `<span>`.
12. **Idempotency** on `POST /sessions` (UUID v4, 60s window) to prevent double-billing.
13. **Retry semantics (Sprint 23 D5):** trigger counter increments on attempt (success OR Kafka failure) — budget is "≤N attempts/session", prevents retry loops.
14. **KafkaProducer is a module-level lazy singleton** in the trigger node (Sprint 23 D7) — amortizes 1–3s cold-start across the long-lived worker; per-call producers blew the 2s send budget.
15. **pg_trgm 0.85 over a vector index for Polymarket matching (V1)** — paraphrase-defense not punctuation-defense; vector index is Future Enhancement 4.
16. **Trigger-and-forget over polling** — full Bronze→Silver→Gold often >30s, exceeding the p95 forecast NFR.
17. **Reactive ingestion (Kafka, Bronze) ≠ reactive search (agent runtime).** The revised plan restores the Kafka loop as the V1 path and defers the search microservice.
18. **Working directory constraint:** all work strictly inside `data-pipeline/`.

---

## 8. Anti-patterns & Problems Encountered (and solutions)

- **Kafka writing to ephemeral `/tmp/kafka-logs`** (root cause of May 11–18 cloud silence) → explicit `KAFKA_LOG_DIRS=/var/lib/kafka/data/kafka-logs` on the PVC subdir (Phase 9.5 A).
- **kafka-python-ng bootstrap→coordinator selector race** (`ValueError: Invalid file descriptor: -1`) on Windows local-dev (KG-PHASE8-25). IPv4 guard in `utils/kafka_utils._resolve_bootstrap_servers()` reduces but doesn't eliminate it; production Linux/GKE unaffected (handoff happens once at startup). Mitigation: Gate 3/E2E Kafka tests `@pytest.mark.skipif(sys.platform=="win32")`, verify on Linux CI.
- **`__consumer_offsets` never auto-created after cold start** (KG-PHASE8-24) — consumer-group joins hang forever. Surgical fix: manually create the topic via `kafka-topics.sh`.
- **`seek_to_end()` race with deferred position-reset** (KG-PHASE8-26) — use `auto_offset_reset='latest'` + one `poll()` instead of `seek_to_end()`.
- **LangGraph "Must write to at least one channel"** on empty-dict node returns (Sprint 23 D6) — trigger node always writes the counter, even on no-op paths.
- **`polymarket_slug` hallucination risk** (T19.5) — LLM has no authoritative live-market list; renamed Node 1 output to `polymarket_search_terms` (retrieval candidates), market resolution moved to Node 3 vault_query.
- **OpenAI Tier 1 RPD ceiling (10,000/day) hit during backlog** (KG-PHASE-9.5-1) — graceful failure via centralized client `max_retries=5` → `AgentProcessingError` → `_mark_failed` Firestore doc → FE "could not complete" + retry.
- **OpenAI cost > estimates** (KG-PHASE-9.5-9) — 5,800 Silver backlog ≈ $88; whole point of the 2-day initial test is to re-baseline cost. Drove the Phase 8 re-plan (deferred paid reactive search).
- **Flink code changes need job cancel+resubmit after rollout** (KG-PHASE-9.5-8) — HA restores old job graph/BLOB regardless of new image; `PATCH /jobs/<jid>` then `flink run -d -py`.
- **GKE-native CSI can't sync K8s Secrets** (KG-PHASE-C-3) — use shell-wrapper + file-read env pattern.
- **Airflow scheduler liveness probe killing healthy pod** (KG-PHASE-C-4) — use `httpGet :8793/health` not `airflow jobs check` (subprocess lacks DB env).
- **`FLINK_PROPERTIES` `#` comment lines corrupt the next key** (KG-PHASE-C-2) — keep all comments in the YAML header, never inside a multi-line value.
- **Polymarket `/comments` breaking change** (KG-PHASE-9.5-4) — feature-flagged off.
- **pytrends 404 / OpenSky GKE timeout** — producers raise-on-0% so Airflow marks `failed` (no silent green).
- **init.sql DDL doesn't re-run on existing volumes** — apply new sections manually via `docker exec ... psql`.

---

## 9. Open Questions & Deferred Tasks

### Active Known Gaps (open)
- **KG-PHASE8-7** — worker uses `basicConfig()` not `setup_logging()` (→ Sprint 27).
- **KG-PHASE8-11** — Polymarket `key_arguments_pro/con` not populated (returns `[]`).
- **KG-PHASE8-13** — `sessionResults` top-level-vs-subcollection spec/server drift.
- **KG-PHASE8-15** — no schema validation on inbound `forecastQueries` (`KeyError` not typed `MalformedQueryError`) (→ Sprint 27).
- **KG-PHASE8-16** — forecast latency > 30s p95 NFR; analysis-only in Sprint 26, fix in 27/Phase 10. **Critical link to Phase 10** (100+ parallel forecasts).
- **KG-PHASE8-17** — synthesize + build_embedding don't log OpenAI usage (gates cost measurement) (→ Sprint 26).
- **KG-PHASE8-19** — Trending Forecasts widget still mock data (Friend 1/2).
- **KG-PHASE8-20** — ClarificationCandidate carries 5 hub-internal fields (→ Sprint 26).
- **KG-PHASE8-23** — dead config `AGENT_REACTIVE_MAX_PER_SESSION` (reactive-search microservice leftover).
- **KG-PHASE8-24/25/26** — kafka-python-ng quirks (above).
- **KG-PHASE-9.5-1/2/4/5/6/7/9** — OpenAI RPD ceiling; `NEWSAI_API_KEY` misnamed (holds a thenewsapi key — needs coordinated rename to `THE_NEWS_API_KEY`); Polymarket comments; pytrends; GKE maintenance window; image digest pinning; OpenAI cost analysis.
- **KG-PHASE-C-1/5/6/7** — kafka-ui `:latest` drift; OpenAI 429 on Gold; OpenSky GKE timeout; pytrends 404.

### Closed (don't reopen)
KG-PHASE8-3 (trigger consumer deployed), KG-PHASE8-12 + KG-PHASE8-22 (Sprint 22),
KG-PHASE8-18 (Sprint 21), KG-PHASE-9.5-3.

### Future Enhancements (deferred from initial-test path)
1. Reactive Search via external APIs (Tavily/Brave + `reactive_article_cache`) — three options A (current fire-and-forget) / B (short polling) / C (direct producer call, breaks service isolation).
2. Follow-up escalation (Sprint 24 ships answer-from-context only).
3. Cross-user cache + delta refresh (`canonicalKey` already written as groundwork).
4. Polymarket vector index + multi-match clarification (pg_trgm rejects legit non-paraphrase rewrites ~0.49).
5. Sentiment time-series quality (larger window, sample-floor, enrichment pass, confidence bands).
6. Additional reactive trigger sources (Telegram/ArXiv/Polymarket — NewsAPI-only in V1).
7. Performance optimization (gated on KG-PHASE8-16 findings; gating for Phase 10).
8. Public sentiment line source quality (`social_vectors` is sparse).

### Doc tension to be aware of
`KG-PHASE-9.5-2` calls newsapi.ai a "deprecated provider", but Phase 7A
(later, 2026-05-09) and the revised plan (2026-05-23) treat **newsapi.ai /
Event Registry as the current, full-body provider**. Treat newsapi.ai as
current; the "deprecated" wording refers to the secret accidentally holding a
thenewsapi.com key. Verify the live key/provider before touching NewsAPI auth.

---

## 10. Working Style & Conventions

### How sprints are structured
- **Vertical slices** (one source/capability end-to-end), not layer-by-layer. A slice is "Done" only after passing the **Triple-Gate** (pipeline) or **4-gate** (hub) matrix.
  - **Pipeline gates:** Gate 1 Ingestion (Bronze schema), Gate 2 Logic (Silver/Gold with `tests/mocks/`, no live cost), Gate 3 Persistence (SQL + pgvector retrieval).
  - **Hub gates:** Gate 1 node unit, Gate 2 subgraph integration (mocks), Gate 3 Firestore emulator round-trip, E2E real (Firestore + OpenAI).
- **Mock-driven development:** AI-intensive features store sample responses in `tests/mocks/`.
- **Living documents:** `task_plan.md` (active tracker + Known Gaps, kept <200 lines), `task_plan_implementation.md` (doc map), `task_plan_archive.md` + `task_plan_implementation_archive.md` (collapsed sprint records), Sprint State Ledger at closeout.

### How Claude Code is used (the protocol)
- **Session kickoff (`sprint-kickoff` skill):** identify domain (A pipeline / B hub) → read the domain's required docs → load required skills → identify sprint type → write a **granular implementation plan** (design decisions needing approval, numbered tasks with file paths, constants/thresholds, dependencies, DLQ/error paths, cold-start, open questions).
- **Mandatory skills:** `code-review` in **every** coding session (governs the test-execution protocol). Domain-specific: `infrastructure` (Docker/SQL/Airflow), `agent-design`/`agent-prompt-engineering`/`evidence-handling`/`frontend-integration` (hub), `prompt-engineering` (Gold enrichment), `filter-analysis` (keyword sniper), `gcp-deployment` (Phase C), `bugfix`, `sprint-closeout`.
- **Closeout (`sprint-closeout` skill):** Sprint State Ledger with sections A–F (completed work, decisions+rationale, test results, new gaps, carried gaps, next entry point); move full record to archive; collapse `task_plan.md` to keyword summary; verify <200 lines.

### Gate-by-gate approval process (non-negotiable)
- **No code or commands until the user explicitly approves the implementation plan.** End the plan with: *"Awaiting your explicit approval before writing any code."*
- **No unauthorized execution** — no tests/Docker/pip/shell without explicit per-session permission.
- **One sub-task at a time;** update `task_plan.md` `[ ]`→`[x]` immediately after each; never bundle without approval.
- **Architectural conflict →** stop, alert, propose compliant alternative.

### Git & docs
- **Conventional Commits with section ref:** `<type>(<scope>): <description> (Section X.X)` — types feat/fix/refactor/test/docs/chore.
- **Docstrings reference the spec section and explain *why*,** not just what.
- **Python env:** `data-pipeline/venv/` only (`data-pipeline\venv\Scripts\python.exe`); keep `requirements.txt` + `requirements.lock` in sync, commit together.

### Conversation conventions
Terse on routine output, deliberate on architecture/design. No break/pacing
suggestions, no structural meta-preamble, no filler. Produce requested artifacts
directly without asking mid-task.
