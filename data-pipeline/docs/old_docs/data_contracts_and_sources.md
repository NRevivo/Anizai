# data_contracts_and_sources.md — Source Registry & Data Contracts
## Anizai Project | Appendix: All Schemas, Sources & Ingestion Parameters

---

## Part A: Source Registry

### A.1 Telegram Channels (MTProto Streaming — Real-time)

| Channel | Handle | Primary Signal |
|---------|--------|---------------|
| Abu Ali Express | @abualiexpress | Critical Middle East & Israel security; military movements |
| 100Field News | @news100shatach | Broad Israeli & regional news; tactical events |
| Faytuks Network | @Faytuks_Network | High-frequency global breaking news; source citations |
| Clash Report | @clashreport | Geopolitical & military tech; weapon systems, naval movements |
| FinancialJuice | @financialjuice | Automated financial & macro-economic headlines |
| Disclose.tv | @disclosetv | Verified breaking news; market shifts and public hype detection |
| Intel Slava Z | @intelslava | Pro-Russian military analysis; diversified sentiment perspective |

**Ingestion Method:** Continuous event-driven streaming via MTProto. Single-message
ingestion (treated as micro-articles). Ignore messages containing only media without text.

### A.2 Excluded Sources (Removed)

- **Reddit** — excluded; Reddit API requires mandatory pre-approval since Nov 2025. All producer/dedup/validator code removed in Sprint 11 T4.
- **PredictIt** — permanently blocked; public API shut down by CFTC action (2022–2024). All code removed.

---

## Part B: Per-Source Technical Parameters

### B.1 ArXiv
- **Baseline:** Daily, up to 200 papers/category. Mandatory 3-second delay between requests (TOS).
- **Backfill:** One-time, last 12 months, up to 1,000 top-cited abstracts/category.
- **Categories:** `cs.AI`, `cs.LG`, `econ.GN`, `q-fin.ST`, `q-bio.PE`, `stat.AP`, `cs.CY`
- **SNR Keywords:** Regulation, Policy, Market Impact, Outbreak, Intervention, Trend

### B.2 Hacker News (Algolia API)
- **Baseline:** Every 15–30 min. Stories with `points > 50` only. Top 10 first-level comments per story.
- **Backfill:** One-time, last 12 months. Filter: `points > 100`, keywords: Polymarket/AI Regulation/Geopolitics.
- **Endpoints:** `search_by_date` for pulse · `search` for backfill.

### B.3 FRED (Federal Reserve Economic Data)
- **Endpoint:** `/fred/series/observations` with `file_type=json`
- **Backfill:** One-time, last 10 years for all series.
- **Pulse:** Daily automated polling for latest observations.

**Indicator Matrix:**

| Category | Series ID | Description |
|----------|-----------|-------------|
| Macro | FEDFUNDS | Federal Funds Effective Rate |
| Macro | CPIAUCSL | Consumer Price Index (Inflation) |
| Macro | UNRATE | Unemployment Rate |
| Real Estate | CSUSHPINSA | S&P/Case-Shiller Home Price Index |
| Commodities | DCOILWTICO | WTI Crude Oil Prices |
| Commodities | GASREGW | U.S. Retail Gasoline Prices |
| Commodities | DHHNGSP | Henry Hub Natural Gas Spot Price |
| Risk | VIXCLS | CBOE Volatility Index (Fear Index) |
| Risk | T10Y2Y | 10Y vs 2Y Treasury Yield Spread (Recession Indicator) |

**Flink Automation Triggers:**
- `T10Y2Y < 0` → flag `Market_Anomaly`, set `impact_level: 5` (Yield Curve Inversion)
- `VIXCLS > 30` → escalate `impact_level: 5` (Extreme market uncertainty)
- Any price metric (Oil/Gas/Gold) variance `>5%` in 24h → flag for immediate grounding

### B.4 News API (Massive.com — Stocks Starter Plan)
- **Pulse:** Every 15–30 min for Top Headlines across Business, Tech, Politics, Health, Science.
- **Authority Whitelist:** Reuters, AP, WSJ, Bloomberg, NYT, WaPo, CNBC, CNN, BBC, FT,
  The Guardian, The Economist, Kan 11, Times of Israel, Jerusalem Post, Ynetnews, i24 News.
- **Keyword Sniper (General category):** Conflict, Sanctions, Crude Oil, OPEC,
  Missile Defense, Interest Rates, AI Regulation, NATO, Central Bank.
- **Tiered Backfill:** 0–6 months: full density · 6–24 months: top-tier sources only ·
  2–5 years: Market Anomaly dates and major geopolitical events only.
- **Impact Boost:** Israeli/Middle East security or energy articles → automatic `+1` to `impact_level`.

### B.5 Google Trends (Pytrends)
- **Static Pulse:** Daily — Top 50 trending topics across Politics, Business, Economics, Health, Tech.
- **Geo Monitoring:** United States, United Kingdom, Israel, Germany (EU representative).
- **Reactive Mode:** On-demand by RAG agent for specific keywords (e.g., "Kuwait oil strike").
- **Backfill:** 5-year "Interest Over Time" for core keywords (Inflation, Crude Oil, Recession).
- **Flink Triggers:** `Public_Hype_Alert` if score increases `>50 points` in 24h.
  Cross-reference spikes with Social Pulse to validate event scale.

### B.6 OpenWeather
- **Static Pulse:** Every 10 min for all predefined bounding boxes.
- **Reactive Mode:** Escalates to every 2 min for a 60-min window on RAG trigger.
- **Strategic Hotspots:** Taipei, Tokyo, Kyiv, Tel Aviv, Washington D.C., Hsinchu Science
  Park, Strait of Hormuz, Suez Canal, Port of Houston, Ukraine/US wheat regions.
- **Backfill:** 12-month history for all static hotspots (seasonal baseline).
- **Flink Triggers:** Severe weather → `Natural_Disaster_Alert` (`impact_level: 5`) ·
  Extreme weather in Taiwan/Japan → `Tech_Supply_Risk` tag ·
  Wind `>50 knots` at maritime chokepoints → `Potential_Shipping_Delay`.

### B.7 OpenSky Network
- **Endpoint:** `/states/all` with bounding box filtering (`lamin`, `lomin`, `lamax`, `lomax`).
- **Strategic Boxes:** Polish-Ukrainian border, Taiwan Strait, Iranian borders,
  Strait of Hormuz, Bab al-Mandab, Eastern Mediterranean, D.C./London/Jerusalem/Moscow airspace.
- **Pulse Trigger:** Aircraft density `>30%` above 30-day hourly average →
  `Aerial_Escalation_Risk` (`impact_level: 4`).
- **Reactive Mode:** RAG-triggered tracking of specific callsigns (military cargo / VIP aircraft).
- **Transponder Monitoring:** "Transponder Deactivation" events in tension zones flagged
  as leading indicator of covert operations.

### B.8 Polymarket
- **Backfill:** One-time, last 30 days of price history and comments (1 req/sec).
- **Pulse:** Market odds every 5–10 min (high-volume markets) · Discussion sync every 20 min.
- **Whale Alert:** Single positions `>$100,000` → `impact_level: 5`.
- **Consensus Vector:** Comments grouped by Flink into temporal blocks (every 50 messages
  or hourly) → LLM summarization → only summaries enter Vector DB.
- **Drill-Down:** Each summary carries `has_raw_source: true` + `market_id` for RAG
  verification against raw `raw_discussions` table.

---

## Part C: Data Schemas

**Note:** The schema enums and example rows below still list `reddit` and `predictit` because the deployed PostgreSQL CHECK constraints (`init.sql`, `postgres-configmap.yaml`) still include them. No active producer writes these — treat as **dormant**. Enum cleanup is a later (infra) phase.

### C.1 Bronze Schema (Raw Ingestion)
```json
{
  "ingestion_id":        "UUID — unique per fetch operation",
  "source_name":         "String (e.g., Polymarket, NewsAPI, Telegram)",
  "source_endpoint":     "String — specific URL or channel",
  "ingestion_timestamp": "ISO8601 UTC",
  "producer_version":    "String — code version for debug tracking",
  "raw_payload":         "Object/JSON — raw object as received from API",
  "metadata": {
    "http_status_code":    "Integer (200, 404, etc.)",
    "request_duration_ms": "Long — fetch duration"
  }
}
```

### C.2 Silver — Master Structured Schema (Metrics Vault)
Universal skeleton for all numerical data points (Polymarket, FRED, OpenWeather, OpenSky):
```json
{
  "schema_version": "2.0", "layer": "Silver", "entity_type": "Structured_Metric",
  "core_identity": {
    "metric_id": "UUID", "canonical_event_id": "String",
    "parent_id": "String", "source_name": "String", "external_reference_id": "String"
  },
  "data_point": {
    "current_value": "Float", "unit": "String", "status": "String", "timestamp_utc": "ISO8601"
  },
  "momentum_block": {
    "change_24h": "Float", "change_7d": "Float", "change_30d": "Float", "is_new_market": "Boolean"
  },
  "metadata_extension": "JSON_OBJECT — see type-specific extensions below"
}
```

**Metadata Extensions by source_name:**

| Source | Key Fields |
|--------|-----------|
| Polymarket / PredictIt | `liquidity_pool_tvl`, `bid_ask_spread`, `24h_volume`, `is_divergent`, `whale_alert`, `resolution_rules` |
| FRED | `series_id`, `observation_date`, `release_priority (1-5)`, `seasonal_adjustment` |
| OpenWeather | `coordinates (lat/lon)`, `strategic_tag`, `impacted_assets`, `condition_severity`, `wind_speed_knots`, `official_alerts` |
| OpenSky | `bounding_box_id`, `aircraft_density_count`, `transponder_silence_events`, `anomaly_score` |

### C.3 Silver — Document & Social Stores

**Full-Text Document Store** (Articles & Academic Papers):
Key fields: `doc_id (UUID)`, `document_hash (SHA-256)`, `canonical_event_id`,
`full_text_raw`, `inverted_pyramid_lead`, `source_name`, `author`, `original_url`,
`publish_date`, `detected_entities`, `relevance_score (0.0–1.0)`

**Social Discourse Store** — Four patterns:

| Platform | Pattern | Storage Logic |
|----------|---------|---------------|
| Reddit | Post-Centric | `post_body` + `full_comment_archive` + `upvote_ratio` |
| Polymarket | Volume-Centric | Flat `raw_comments` stream grouped by `market_id` |
| Telegram | News-Centric | `message_text` + `extracted_links` + `channel_source` (media-only messages ignored) |
| HackerNews | Story-Centric | `title` + `url` + `points` + `top_comments` (up to 10 first-level comments per story) |

### C.4 Silver → Gold Transition

| Source Type | Silver Representation | Gold Representation |
|-------------|----------------------|---------------------|
| News / Papers | Full body text + Sniper metadata | Semantic summary + key entities |
| Reddit | Original post + full comment list | Post summary + community sentiment |
| Polymarket | Raw comments stream by `market_id` | Consensus Vector (grouped summary) |
| Telegram | Cleaned text / links | Individual message vector (direct signal) |
| HackerNews | Story + top comments list | Story summary + `top_technical_insights` + `community_sentiment` |

### C.5 Gold — Global Signal Schema → `knowledge_vectors` (newsapi, arxiv, telegram)

**DB Table:** `knowledge_vectors` (pgvector, HNSW-indexed)
**Sources:** newsapi, arxiv, telegram
**JSONB Extension:** `domain_context` — academic/tactical domain fields. No `social_context` column.

> **Architectural note — two separate vector tables:** `knowledge_vectors` (C.5) and `social_vectors` (C.6) each carry their own independent HNSW index. Mixing heterogeneous object types (news/research vs. community discourse) in a single HNSW index degrades recall quality — post-hoc `entry_type` filtering consumes neighbor slots *after* the approximate nearest-neighbor search has already run, returning fewer relevant results to both The Researcher agent (news/research) and The Pulse Analyst (social). Independent indexes eliminate this penalty entirely.

**Common Skeleton** (all vectorized signals):
```json
{
  "metadata": {
    "signal_id": "UUID", "canonical_event_id": "String",
    "source_platform": "news_api | arxiv | telegram | etc.",
    "publisher": "String", "published_at": "ISO8601",
    "silver_data_ref": "UUID — bridge to full text in Silver DB",
    "raw_data_ref":    "UUID — bridge to raw JSON in Bronze DB"
  },
  "content_vitals": { "title": "String", "url": "String", "description_snippet": "String" },
  "enrichment_ai": {
    "executive_summary": "String (2-3 sentences)", "key_findings": ["List"],
    "impact_level": "1-5", "urgency_level": "1-5",
    "reliability_score": "0.0-1.0", "sentiment_score": "-1.0 to 1.0",
    "extracted_entities": ["Names, Locations, Systems"],
    "topic_classification": "String", "fact_check_flag": "Boolean"
  }
}
```

**Domain Extensions** (stored in `domain_context` JSONB):

| Type | Extra Fields |
|------|-------------|
| Academic (ArXiv) | `authors`, `is_peer_reviewed`, `citation_count`, `domain_tags` |
| News / Tactical (NewsAPI) | `share_count`, `is_breaking`, `sniper_keywords`, `geospatial_focus` |
| Direct Signal (Telegram) | `is_breaking_alert`, `forwarded_from`, `extracted_links` |

### C.6 Gold — Social Pulse Schema → `social_vectors` (reddit, polymarket, hackernews)

**DB Table:** `social_vectors` (pgvector, HNSW-indexed)
**Sources:** reddit, polymarket, hackernews
**JSONB Extensions:** `social_context` (community identity block) + `platform_logic` (platform-specific extension fields)

> See architectural note in C.5 above on why these are two separate tables with independent HNSW indexes.

Shares the same `metadata` and `enrichment_ai` blocks as C.5, plus:
```json
{
  "social_context": {
    "community_name": "String", "author_handle": "String (optional)",
    "author_reputation": "Float", "primary_engagement_score": "Integer"
  }
}
```

**Platform Extensions** (stored in `platform_logic` JSONB):

| Platform | `entry_type` | Key Extra Fields |
|----------|-------------|-----------------|
| Reddit | `reddit_post_summary` | `thread_depth`, `upvote_ratio`, `key_community_arguments` |
| Polymarket | `market_consensus` | `aggregation_window_hours`, `comment_volume_analyzed`, `consensus_rating`, `market_id_ref` |
| HackerNews | `hackernews_story_summary` | `story_type`, `points`, `top_technical_insights`, `external_link_ref`, `community_sentiment` |

### C.7 Gold — Market Divergence Alert Schema
Generated by Flink (non-AI) when price discrepancy `>3%` is detected across platforms:
```json
{
  "schema_version": "1.0", "layer": "Gold", "entity_type": "Divergence_Alert",
  "identity": { "alert_id": "UUID", "canonical_event_id": "String", "timestamp_generated": "ISO8601" },
  "divergence_data": {
    "source_a": { "platform": "Polymarket", "current_price": "Float", "market_id_ref": "String" },
    "source_b": { "platform": "PredictIt", "current_price": "Float", "market_id_ref": "String" },
    "spread_delta": "Float (% difference)", "is_statistically_significant": "Boolean"
  },
  "alert_metrics": {
    "urgency_level": "1-5 (>5% spread = Level 4)",
    "confidence_score": "Float (0.0-1.0)",
    "impact_area": "String (e.g., Geopolitics, US Elections)"
  },
  "ai_trigger_logic": {
    "divergence_summary": "String (short summary for RAG context)",
    "anomaly_type": "String (e.g., Information Lag, Liquidity Gap, Insider Movement)",
    "suggested_action": "String (e.g., Verify latest Telegram leaks, Check FRED data)"
  }
}
```
