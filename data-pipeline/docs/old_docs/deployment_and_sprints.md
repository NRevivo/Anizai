# deployment_and_sprints.md — Docker, Sprints & Testing Protocol
## Anizai Project | Sections 8–9 of Technical Specification

---

## Section 8: Containerization & Deployment (Docker)

### 8.1 Orchestration Strategy
The entire ecosystem is orchestrated via **Docker Compose** — enabling a "one-command"
startup of the full development environment. This ensures complete parity between the
local machine and the GCP production environment.

### 8.2 Core Services in the Docker Stack

| Service | Implementation | Notes |
|---------|---------------|-------|
| **Kafka** | KRaft mode (Zookeeper-less) | Simplified cluster management, faster startup |
| **Flink** | JobManager + TaskManager containers | Dedicated containers for Silver and Gold jobs |
| **PostgreSQL** | Single container with extensions | Pre-loaded with pgvector and TimescaleDB |
| **Producers** | 9 source producers (not standalone Compose services) | Producer Compose services are commented out; producers run inside the Airflow scheduler (DAGs) or as standalone module execs locally, and as Kubernetes Deployments in cloud |
| **GCS Emulator** | Optional local container | For testing Bronze layer before deploying to GCS |

### 8.3 Network & Volume Persistence

- **Unified Bridge Network** — All containers communicate over a private bridge network
  for secure and fast internal data transfer.
- **Docker Volumes** — Mandatory for PostgreSQL and Kafka to ensure data persistence
  across container restarts.

---

## Section 9: Local-to-Cloud Execution & Sprint Testing Logic

### 9.1 Unified Execution Environment (Docker-First)
To eliminate environment discrepancies between local and cloud:

- **Local Infrastructure** — `docker-compose.yml` manages local instances of Kafka,
  PostgreSQL (pgvector + TimescaleDB), and Flink.
- **Environment Parity** — All API keys (OpenAI, NewsAPI, etc.) and database credentials
  are managed via `.env` files. Moving from local to GCP is a configuration change only
  — no code changes required.

### 9.2 Feature-Driven Sprints — Vertical Slice Methodology
Development follows a **Vertical Slice** approach rather than building layer-by-layer.

- **The Feature Unit** — A single sprint covers one complete data source end-to-end:
  `Producer → Kafka → Silver Processing → Gold Enrichment → Storage`
- **Atomic Completion** — A feature is only "Done" once it passes all three gates of
  the Triple-Gate Test Matrix (Section 9.3).

**Sprint order (recommended):**
1. Polymarket (WebSocket — establishes the real-time streaming pattern)
2. FRED (REST polling — establishes the scheduled polling pattern)
3. News API (REST + Keyword Sniper — establishes the filtering pattern)
4. Remaining 6 sources following established patterns

### 9.3 The Triple-Gate Test Matrix
Every Vertical Slice must pass all three gates before being marked `[x] Done`:

**Gate 1 — Ingestion Gate (Bronze)**
- Validates the Producer pushes a schema-compliant JSON to the correct Bronze Kafka topic.
- Checks: `event_id`, `producer_timestamp`, `source_id` are present and correctly typed.

**Gate 2 — Logic Gate (Silver/Gold Processing)**
- Tests Flink enrichment logic using **Mock Payloads** from `tests/mocks/`.
- Validates: Keyword Sniper filtering, SHA-256 deduplication, AI summarization output.
- Must run without live streams or real API costs.

**Gate 3 — Persistence Gate (Storage)**
- End-to-end query check verifying data is correctly retrievable via:
  - SQL query on TimescaleDB (structured metrics).
  - Semantic search on pgvector (Gold embeddings).

### 9.4 Continuity & The Handoff Protocol
Two living documents maintain project momentum across sessions:

**`task_plan.md`** (maintained by Claude Code)
- Created and updated at the start of every session.
- Tracks status of each Vertical Slice: `[ ]` / `[~]` / `[✓]` / `[x]`
- One row per sprint feature — never deleted, only updated.

**Sprint State Ledger** (generated at session end)
- Summary of work completed this session.
- Pending bugs and open issues.
- Exact entry point for the next session (task, file, step).

### 9.5 Reliability & Schema Enforcement

- **Hard Failures** — Any object failing Silver Layer schema validation is routed to
  `dead-letter-queue` for inspection. Silent failures and DB corruption are
  strictly prohibited.
- **Mock-Driven Development** — `tests/mocks/` stores sample OpenAI responses to enable
  rapid, low-cost iteration of RAG and enrichment logic without live API calls.
