# pipeline_core.md — Kafka Orchestration & Flink Processing Engine
## Anizai Project | Sections 3–4 of Technical Specification

---

## Section 3: Apache Kafka & Topic Management

### 3.1 Topic Hierarchy & Naming Convention
All topics follow the strict convention: `[Layer].[Status].[Source_Group]`

**Bronze Layer — One topic per source (isolation & fault tolerance):**
```
ingest.bronze.polymarket      ingest.bronze.telegram
ingest.bronze.hackernews      ingest.bronze.newsapi
ingest.bronze.arxiv           ingest.bronze.fred
ingest.bronze.googletrends    ingest.bronze.openweather
ingest.bronze.opensky
```
*(`ingest.bronze.reddit` / `ingest.bronze.predictit` are still provisioned by the cloud `kafka-init` job but receive no data — pending infra cleanup.)*

**Silver Layer — Aggregated by Family Schema (cleaned & unified):**
```
process.silver.social_pulse         # Telegram, HackerNews, Polymarket comments
process.silver.global_news          # NewsAPI, ArXiv
process.silver.structured_metrics   # FRED, Polymarket prices, OpenWeather, OpenSky
```

**Gold Layer — AI-enriched, vector-ready:**
```
serve.gold.social_pulse
serve.gold.global_news
serve.gold.structured_metrics
```

**System Topics:**
```
ingestion_triggers    # RAG agent → On-Demand reactive ingestion requests (Section 2.4)
dead-letter-queue     # Failed schema-validation objects — never silently dropped
```

### 3.2 Message Envelope & Metadata
Every Kafka message is wrapped in a standard envelope serialized in **NDJSON format**.
This provides a full "Paper Trail" from Producer to Gold Layer.

```json
{
  "event_id":           "UUIDv4 — shared across all layers",
  "trace_id":           "tracks message from Producer to Gold",
  "producer_timestamp": "ISO8601 UTC — original capture time",
  "schema_version":     "for handling future schema updates",
  "payload":            "the actual data object (see data_contracts_and_sources.md)"
}
```

### 3.3 Retention & Lifecycle Policy

| Layer | Retention | Rationale |
|-------|-----------|-----------|
| Bronze | 7 days | Auditing and replay in case of Silver processing failures |
| Silver | 3 days | Purged after successful persistence to PostgreSQL |
| Gold | 3 days | Purged after successful persistence to Vector DB |
| `structured_metrics` | Compaction enabled | Ensures latest value of a ticker (e.g., Oil Price) is always available |

---

## Section 4: Apache Flink — Cognitive Processing Engine

Flink is the system's **Central Nervous System**, handling both deterministic math and
asynchronous AI enrichment. It operates two distinct jobs: Silver and Gold.

### 4.1 Silver Job — Standardization & Dual-Persistence
Consumes raw JSON from Bronze topics and executes the following transformations:

**A. Keyword Sniper Filter**
Before any heavy processing, Flink matches payloads against the Master Keyword List.
Only "High-Signal" items (based on keyword density and position) proceed to enrichment,
significantly reducing downstream token costs.

**B. Dual-Store Persistence**
Flink strictly bifurcates storage based on content type:
1. **Full-Text Document Store** — Cleaned articles and research papers → relational store
   for deep verification (Knowledge Vault).
2. **Social Discussion Store** — Raw comments (Polymarket) → threaded structure
   for drill-down audits (Social Vault).

**C. Deduplication (SHA-256)**
Flink calculates a content hash for every article to prevent duplicate ingestion from
overlapping news aggregators (e.g., NewsAPI vs. Massive.com).

**D. Real-time Translation**
Non-English signals (e.g., Hebrew/Russian Telegram channels) are translated to English
via OpenAI or specialized translation APIs, ensuring a linguistically unified Gold Layer.

### 4.2 Gold Job — Semantic & Structural Enrichment

**A. Semantic Enrichment (Social & News Pulse)**

- **Temporal Consensus Bundling** — To prevent Vector DB bloat, Flink groups social
  comments (Polymarket) into 4-hour temporal blocks. GPT-4o then summarizes
  each group into a single Consensus Vector instead of individual message embeddings.
- **Cognitive Metadata Extraction** — Via OpenAI API, Flink extracts:
  - `impact_level` (1–5): Geopolitical/market significance.
  - `urgency_level` (1–5): Time-sensitivity of the signal.
  - `uncertainty_index` (0.0–1.0): Volatility of the consensus.
  - `extracted_entities`: Automated tagging of actors, weapon systems, or locations.

**B. Structural Enrichment (Metrics & Momentum Block)**

- **Keyed State Management** — Flink maintains fault-tolerant state for every numerical
  metric (Prediction Prices, Inflation Rates, etc.).
- **Momentum Block** — Flink calculates deterministic deltas as new data arrives:
  `change_24h`, `change_7d`, `change_30d`. This allows the RAG agent to answer
  "What is the trend?" without performing math at runtime.
- **Market Divergence Alerts** — Flink compares identical markets across platforms
  (Polymarket vs. PredictIt). A price discrepancy >3% triggers a Divergence Alert
  pushed directly to the Gold Layer (see Divergence Alert schema in
  `data_contracts_and_sources.md`).
  > ⚠ Cross-platform divergence requires a second market platform. Since PredictIt
  > removal, Polymarket is the only active market source, so Divergence Alerts are
  > **dormant** (logic + schema retained for when a second platform is added).

### 4.3 Operational Guardrails & Resilience

| Guardrail | Implementation |
|-----------|---------------|
| **Async I/O for OpenAI** | Flink manages async calls to prevent pipeline stall while awaiting AI responses |
| **Backpressure & Rate Limiting** | Built-in throttling respects OpenAI TPM quotas without crashing |
| **Checkpointing** | State-store snapshots every **60 seconds** — ensures Momentum calculations and in-flight AI requests survive restarts |
| **Dead-Letter Queue** | Any object failing Silver schema validation is routed to `dead-letter-queue` for inspection — never silently dropped |
