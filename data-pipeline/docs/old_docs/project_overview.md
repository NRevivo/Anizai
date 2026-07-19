# project_overview.md — System Vision & Data Ingestion Layer
## Anizai Project | Sections 1–2 of Technical Specification

---

## Section 1: System Vision & Architecture Philosophy

### 1.1 System Vision
Anizai is an advanced **RAG-based forecasting platform** that provides contextual,
data-driven insights into future events. Unlike static prediction models, Anizai builds
a **dynamic, real-time knowledge base** to address the volatility of predictive markets
(e.g., Polymarket). The system generates **explainable analytical reports** grounded in
live news, social sentiment, and expert publications — not simple Yes/No outcomes.

### 1.2 Infrastructure Mission (Scope of This Document)
This project focuses exclusively on the **Data Engineering Core**. The objective is a
robust, fault-tolerant, reactive pipeline that orchestrates data from external sources
to optimized storage layers. Core responsibilities:

- **Ingestion** — Connecting to diverse APIs and WebSockets (hybrid static/dynamic).
- **Orchestration** — Managing data flow via Apache Kafka as the Single Source of Truth.
- **Processing** — Real-time cleaning, translation, and AI enrichment via Apache Flink.
- **Serving** — Delivering structured and vectorized data to specialized databases for RAG.
- **Feedback Loop** — Enabling the RAG agent to trigger on-demand ingestion to fill gaps.

### 1.3 Medallion Architecture
All data flows through three immutable quality layers:

| Layer | Name | Description |
|-------|------|-------------|
| 🥉 Bronze | Raw | Immutable logs of raw events captured directly from producers |
| 🥈 Silver | Cleansed | Normalized, de-duplicated, translated, and filtered streams |
| 🥇 Gold | Enriched | Vector embeddings, source attribution, and reliability scores — ready for RAG |

### 1.4 Hybrid Ingestion Strategy
Two ingestion modes operate in parallel:

- **Baseline Ingestion (Static)** — Continuous streaming and scheduled polling of primary
  sources (Social Pulse, Global News, Financial Indices) to maintain a broad knowledge base.
- **Reactive Ingestion (On-Demand)** — A tool-calling mechanism allowing the RAG engine
  to request specific data points (e.g., localized weather, flight tracking, Google Trends)
  in real-time when a knowledge gap is identified during inference.

---

## Section 2: Data Sources & Ingestion (The Bronze Layer)

### 2.1 Producer Matrix — 9 Active Sources

| Source | Connection | Frequency | Key Filtering Parameters |
|--------|-----------|-----------|--------------------------|
| Polymarket | WebSocket / REST | Real-time | topic: orders/prices, min_volume >$10k |
| Telegram | Streaming (MTProto) | Real-time | channel_ids: verified_news, expert_analysts |
| Hacker News | REST (Algolia) | 5–10 min | tags: comment, query: prediction/market/regulation |
| News API | REST API | 15–30 min | categories: business/politics/tech/general/science/health |
| ArXiv | REST API | Daily | cat: cs.AI, econ.GN, q-fin.PM |
| FRED | REST API | Daily | series_ids: CPIAUCSL, FEDFUNDS, UNRATE |
| Google Trends | REST API | Hourly | keywords: dynamic_market_list, geo: US |
| OpenWeather | REST API | Hourly | lat/lon: event_location, units: metric |
| OpenSky | REST API | 1 min | bbox: region_of_interest, api_version: v2 |

> **Removed sources:** *Reddit* (excluded — API mandatory pre-approval since Nov 2025; code removed Sprint 11 T4) and *PredictIt* (permanently blocked — public API shut down by CFTC action 2022–2024) are no longer ingested.

### 2.2 SNR Optimization — Domain Filtering Strategy
To maximize the Signal-to-Noise Ratio, producers apply domain-specific filters at the
source. Only high-signal data enters Bronze Kafka topics:

- **News API (Global Security & Commodities)** — "General" and "Business" categories
  combined with mandatory keywords: Conflict, Sanctions, Treaty, Crude Oil, OPEC,
  Missile Defense, Naval Warships.
- **Social Pulse (Telegram)** — Restricted to high-authority communities and
  expert-led channels only (see `data_contracts_and_sources.md` for full Source Registry).
- **ArXiv (Scientific Grounding)** — Pre-print abstracts in AI and Finance only. Grounds
  predictions in emerging research before it reaches mainstream news.
- **Polymarket (Market Conviction)** — Monitors "Whale" trades (>$100k) and order book
  depth to capture shifts in market confidence that often precede public news breaks.

### 2.3 Ingestion Orchestration Logic

- **Real-time Streamers** — Dockerized Python services (FastAPI/Trio) maintaining
  persistent WebSockets for low-latency sources (Polymarket, Social Pulse).
- **Scheduled Pollers** — Apache Airflow DAGs managing periodic REST calls with built-in
  retry logic and rate-limit handling for high-latency sources (FRED, ArXiv, News API).
- **Bronze Layer Integrity** — Every Producer attaches `producer_timestamp` and `source_id`
  to the raw JSON before emitting to Kafka, ensuring a clear audit trail for replay.

### 2.4 Reactive Ingestion — On-Demand Tool Calling
A feedback loop between the RAG agent and the Ingestion Layer:

1. **Gap Identification** — The RAG agent detects a missing data point during inference
   (e.g., current weather in a specific conflict zone like Kuwait).
2. **Trigger Mechanism** — An On-Demand Request is sent to the dedicated Kafka topic
   `ingestion_triggers`, prompting a specialized Worker to fetch from OpenWeather,
   OpenSky, or Google Trends in real-time.
3. **Dynamic Injection** — Retrieved data is enriched and served back to the agent within
   seconds to complete the analysis.
