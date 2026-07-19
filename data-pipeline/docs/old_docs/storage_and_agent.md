# storage_and_agent.md — Storage, Agentic Hub & System Integration
## Anizai Project | Sections 5–7 of Technical Specification

---

## Section 5: Storage Layer — The Multi-Vault Strategy

A unified **PostgreSQL engine** (with extensions) manages all Silver and Gold data
across four distinct functional Vaults.

### 5.1 Knowledge & Social Vaults (Silver Layer)

**Knowledge Vault (Document Store)**
- Stores cleaned full-text articles and research papers.
- Implemented using **JSONB** for schema flexibility.
- Enables the RAG agent to "Drill-Down" from a Gold summary to the original source
  via the `silver_data_ref` field.

**Social Vault (Discussion Store)**
- Manages raw comment trees and threaded social discourse (Polymarket; Reddit dormant — no active producer).
- Same JSONB architecture — stores post body and full comment archive.
- The Gold Layer holds summaries; Silver holds the `post_id` reference for
  sentiment verification drill-downs.

### 5.2 Vector Intelligence — pgvector with HNSW (Gold Layer)

- **High-Dimensional Search** — Stores Gold Layer embeddings with **HNSW indexing**
  for sub-second semantic retrieval.
- **Metadata Filtering** — Enables atomic queries combining semantic similarity with
  hard filters, e.g.: `reliability > 0.8 AND impact > 4`.

### 5.3 Time-Series & Momentum Vault — TimescaleDB (Gold Layer)

- **Hypertables** — Organizes all numerical metrics (FRED indicators, Prediction Prices)
  into time-partitioned tables for fast range queries.
- **Momentum Block Storage** — Stores pre-calculated deltas (`change_24h`, `change_7d`,
  `change_30d`) computed by Flink, allowing the API to serve real-time trend data
  instantly without runtime math.

### 5.4 Infrastructure Tables — The Mapping Dictionary

- A dedicated relational store that maps platform-specific IDs to the
  `canonical_event_id`.
- Enables the system to bridge disparate sources — e.g., linking a Polymarket contract
  to a Reuters article covering the same event.

---

## Section 6: The Agentic Intelligence Hub

Transforms a user query into a multi-layered analytical report via a
**LangGraph-powered Agentic Workflow** — not a simple linear RAG search.

### 6.1 Orchestration Layer — LangGraph
A graph-based state machine decomposes the user's request into specialized sub-tasks,
handled by a "Swarm" of purpose-built agents running in parallel.

### 6.2 Specialized Agents (Preparation Layer)
Task-specific agents (GPT-4o-mini or equivalent) prepare context in parallel:

| Agent | Responsibility |
|-------|---------------|
| **The Researcher** | Semantic search in the Gold Layer; fetches full-text from Knowledge Vault |
| **The Pulse Analyst** | Summarizes community sentiment from Social Vault; identifies contrasting arguments |
| **The Market Bridge** | Queries Momentum Vault for hard numbers; surfaces active Divergence Alerts |

### 6.3 Synthesis Lead — Final Reasoning
The "Final Boss" LLM (GPT-4o) receives structured outputs from all agents and performs
**Chain-of-Thought (CoT)** reasoning to:
- Calculate the final **Confidence Score**.
- Reconcile conflicting data (e.g., when News and Markets diverge).
- Generate the final structured response: text, charts, and source citations.

### 6.4 Real-time Evidence Alerts
Leveraging the Kafka stream, the Agentic Hub writes high-urgency signals into per-user
Firestore `notifications/` subcollections. The frontend's existing Firestore listeners
deliver these Evidence Alerts to the UI in real time — bypassing the need for a
user-initiated query. (See `agentic_hub_spec.md` §8.7.5 for the delivery model.)

---

## Section 7: System Integration & Pipeline Monitoring

### 7.1 Cloud Infrastructure — GCP Deployment

| Service | Role |
|---------|------|
| **GCS** | Bronze Data Lake — long-term raw JSON storage, organized by date and source |
| **Cloud SQL** | Central Warehouse — managed PostgreSQL hosting Silver (text) and Gold (vector/metrics) layers |
| **GKE / Compute** | Processing Engine — Kubernetes or VM clusters running Kafka brokers and Flink jobs |

### 7.2 Pipeline Observability — The Monitoring Contract
A tiered strategy to prevent log flooding while maintaining full traceability:

- **Asynchronous Metrics** — Prometheus/Grafana track throughput (msg/sec) and latency
  without overhead.
- **Sampling-Based Logging** — 100% capture of ERROR logs; 1% sampling for INFO logs
  to optimize cloud costs. All logs are structured JSON for fast filtering in
  Google Cloud Logging.
- **Distributed Tracing** — Every event carries a unique `trace_id` (Correlation ID),
  enabling end-to-end debugging from the initial Producer fetch to final Vector DB
  ingestion.

### 7.3 Integration & Handoff — The Agent Handshake
Ensuring the RAG Agent and the Data Pipeline are perfectly aligned:

- **IAM & Security** — Fine-grained access control. The Agentic Hub has:
  - **Read-only access to PostgreSQL** for vault queries (knowledge_vectors, social_vectors, momentum_vault, mapping_dict, knowledge_vault, social_vault drill-downs).
  - **Read-write access to PostgreSQL** for the new `reactive_article_cache` table only (Section 8.12).
  - **Write access to Firestore** for forecast result delivery via Firebase Admin SDK with service account credentials. The hub writes to `sessionResults` documents and the `evidence`, `agentEvents`, `predictionSeries`, `sentimentTimeSeries`, `messages` subcollections under each session.

  The hub does NOT modify any vault data. The hub does NOT have access to user authentication data or `users` collection beyond reading forecast metadata.
- **Data Catalog** — A technical dictionary for the partner/frontend engineer detailing:
  table names, JSONB keys, and pre-calculated Continuous Aggregates (Materialized Views)
  available for UI visualization.
