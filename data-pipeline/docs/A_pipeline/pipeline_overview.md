# pipeline_overview.md
> Domain: A — Pipeline
> Type: Overview
> Last updated: 2026-06-15
> TL;DR: The macro view of the Anizai data pipeline — what it ingests, how data flows Bronze→Silver→Gold, and where each component lives. Open this first to orient before the detailed spec files.

## Navigation
- §1 — Purpose & Scope — what the pipeline is and what it is not
- §2 — Architecture — end-to-end data flow, producers → Kafka → Flink → PostgreSQL
- §3 — Components — every moving part, its role, its module, and status
- §4 — Phase & Sprint Status — which phases are closed and what remains open
- §5 — References — the other Domain A files and where to look for what

---

## §1 — Purpose & Scope

The Anizai data pipeline is the **Data Engineering Core** of a RAG-based forecasting
platform. It ingests from 9 external sources, refines the data through a Bronze →
Silver → Gold medallion architecture using Kafka and Flink, and serves vectorized and
time-series data from PostgreSQL (pgvector + TimescaleDB) for downstream retrieval.

**In scope:** ingestion (producers), orchestration (Kafka as single source of truth),
stream processing/enrichment (Flink Silver + Gold jobs), filtering (two-stage keyword
sniper + semantic rescue), and persistence (the multi-vault PostgreSQL layer).

**Not in scope (other domains):** the LangGraph Agentic Hub that *queries* these vaults
(Domain B), the GCP/GKE cloud deployment of this pipeline (Domain C, Phase 9), and the
calibration/backtesting harness (Domain D, Phase 10). This pipeline is the data
foundation those domains build on; it does not reason over the data or render results.

The pipeline is **fully implemented and operationally closed**. The only outstanding
pipeline work is Phase 7B.5 — empirical calibration of filter thresholds against
production data (see `pipeline_sprints.md §2`).

---

## §2 — Architecture

```
 9 PRODUCERS                BRONZE              SILVER (Flink)            GOLD (Flink)              PERSISTENCE (PostgreSQL)
 ───────────                ──────              ─────────────            ────────────              ────────────────────────
 Polymarket  ─┐
 Telegram     │   ingest.bronze.<source>   process.silver.social_pulse   serve.gold.social_pulse   social_vault   (JSONB, Silver)
 HackerNews   │   (9 topics, NDJSON,       process.silver.global_news    serve.gold.global_news    social_vectors (pgvector HNSW)
 NewsAPI      ├──▶ 1 per source)      ──▶   process.silver.structured_    serve.gold.structured_    knowledge_vault   (JSONB, Silver)
 ArXiv        │                                       metrics                    metrics            knowledge_vectors (pgvector HNSW)
 FRED         │   System topics:           Silver job:                   Gold job:                 momentum_vault (TimescaleDB)
 GoogleTrends │    ingestion_triggers       • keyword sniper +            • Cognitive Metadata      mapping_dict   (canonical IDs)
 OpenWeather  │    dead-letter-queue          semantic rescue              (GPT-4o)
 OpenSky     ─┘                             • dedup (SHA-256)             • Consensus bundling
                                            • translation (GPT-3.5)       • Momentum deltas
                                            • DLQ on validation fail      • deterministic triggers

 Reactive loop:  ingestion_triggers topic → ingestion_trigger_consumer → re-runs OpenWeather / OpenSky / GoogleTrends on demand
 Orchestration:  Airflow DAGs (scheduled pollers) + standalone streamers (Polymarket, Telegram)
 Observability:  Flink → Prometheus (:9249) → Grafana; structured JSON logs, 1% INFO sampling, trace_id propagation
```

Data is immutable per layer. Every Kafka message is an NDJSON envelope
(`event_id`, `trace_id`, `producer_timestamp`, `schema_version`, `payload`). Anything
failing Silver schema validation routes to `dead-letter-queue` — never silently dropped.

---

## §3 — Components

| Component | Role | File / Module | Status |
|---|---|---|---|
| Polymarket producer | Market odds + price stream (WebSocket/REST) → Bronze | `ingestion/polymarket_producer.py` | Active (comments path disabled — KG) |
| Telegram producer | MTProto streaming, 7 vetted channels → Bronze | `ingestion/telegram_producer.py` | Active |
| HackerNews producer | Algolia REST, stories + top comments → Bronze | `ingestion/hackernews_producer.py` | Active |
| NewsAPI producer | newsapi.ai (Event Registry) REST → Bronze | `ingestion/newsapi_producer.py` | Active |
| ArXiv producer | Atom XML REST, AI/finance pre-prints → Bronze | `ingestion/arxiv_producer.py` | Active |
| FRED producer | 9 macro/commodity/risk series → Bronze | `ingestion/fred_producer.py` | Active |
| Google Trends producer | Pytrends, daily top-50 + reactive → Bronze | `ingestion/googletrends_producer.py` | Degraded (pytrends 404 — KG) |
| OpenWeather producer | Strategic-hotspot weather → Bronze | `ingestion/openweather_producer.py` | Active |
| OpenSky producer | Aircraft density, OAuth2, 7 boxes → Bronze | `ingestion/opensky_producer.py` | Active |
| Silver job | Sniper + rescue, dedup, translation, routing, DLQ | `processing/silver_job.py` | Active |
| Gold job | Cognitive Metadata, consensus, momentum, triggers | `processing/gold_job.py` | Active |
| Keyword Sniper | Two-stage deterministic + semantic-rescue gate | `processing/keyword_sniper.py`, `build_sniper_reference_vector.py` | Active |
| Deduplication | SHA-256 content hashing | `processing/deduplication.py` | Active |
| Translation | Silver-layer non-English → English (GPT-3.5-turbo) | `processing/translation.py` | Active |
| Consensus bundling | Temporal-window social grouping helpers | `processing/consensus.py` | Active |
| Momentum | Deterministic `change_24h/7d/30d` deltas | `processing/momentum.py` | Active |
| Knowledge Vault | Silver full-text store (news/research/telegram) | `persistence/knowledge_vault.py` | Active |
| Knowledge Vectors | Gold pgvector HNSW (news/arxiv/telegram) | `persistence/knowledge_vectors.py` | Active |
| Social Vault | Silver discussion store (Polymarket/HackerNews) | `persistence/social_vault.py` | Active |
| Social Vectors | Gold pgvector HNSW (social pulse) | `persistence/social_vectors.py` | Active |
| Momentum Vault | TimescaleDB hypertable for numeric metrics | `persistence/momentum_vault.py` | Active |
| Mapping Dictionary | canonical_event_id linkage across sources | `persistence/mapping_dict.py` | Built; Gold-wiring deferred (KG) |
| Reactive trigger consumer | Consumes `ingestion_triggers`, re-runs reactive producers | `orchestration/ingestion_trigger_consumer.py` | Active |
| Orchestration | Airflow DAGs (scheduled pollers) | `orchestration/dags/*.py` | Active |
| Observability | Prometheus + Grafana + structured JSON logging | `infrastructure/`, `utils/logging_config.py` | Active |

---

## §4 — Phase & Sprint Status

| Phase | Sprints | Status | Plan doc |
|---|---|---|---|
| Phase 0 — Foundation (config, Kafka utils, validators, DB, init.sql) | — | Closed | `task_plan_archive.md` |
| Phase 1–4 — 9 source vertical slices | Sprints 1–10 | Closed | `task_plan_archive.md` |
| Pre-Phase 5 Checkpoint — bugfix/gap closure + Reddit removal | Sprint 11 | Closed | `task_plan_archive.md` |
| Phase 5 — Dockerfile.flink + cross-cutting enrichment | Sprints 12–13 | Closed | `task_plan_archive.md` |
| Phase 6A — Airflow ingestion orchestration | Sprint 14 | Closed | `task_plan_archive.md` |
| Production readiness (OAuth2, orchestration test; scraper later retired) | Sprint 15 | Closed | `task_plan_archive.md` |
| Phase 6B — Prometheus/Grafana monitoring | Sprint 16 | Closed | `task_plan_archive.md` |
| Full end-to-end live validation | Sprint 17 | Closed | `task_plan_archive.md` |
| Phase 7 — Provider migration + intelligent filtering + scraper retirement | 7A / 7B / 7C | Closed 2026-05-09 | `docs/archive/phase7_intelligent_filtering.md` |
| Phase 7B.5 — Filter-threshold calibration on production data | — | **Open / queued** | `docs/archive/phase7_intelligent_filtering.md` |

> Phase naming note: cloud deployment of this pipeline is **Phase 9** (Domain C) and the
> Agentic Hub is **Phase 8** (Domain B); neither is pipeline scope. See `pipeline_sprints.md`
> for the full closed/open breakdown and Known Gaps.

---

## §5 — References

- `pipeline_sources.md` — the 9 producers, per-source ingestion parameters, and Bronze schemas.
- `pipeline_processing.md` — Kafka topic hierarchy, the Flink Silver/Gold jobs, the two-stage filter, retention.
- `pipeline_storage.md` — the multi-vault PostgreSQL layer: pgvector HNSW tables, TimescaleDB hypertable, schemas, constraints.
- `pipeline_sprints.md` — sprint status summary, the open Phase 7B.5 work, deferred items, and Known Gaps (KG-A-*).
- `pipeline_archive.md` — append-only record of every closed pipeline sprint (Sprints 1–17, Phase 7A/7B/7C).
- `docs/old_docs/phase7_intelligent_filtering.md` — the granular Phase 7 implementation plan (design decisions M1–C6, gate criteria).
