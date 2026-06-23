# pipeline_storage.md
> Domain: A — Pipeline
> Type: Spec
> Last updated: 2026-06-15
> TL;DR: The multi-vault PostgreSQL storage layer — two Silver JSONB stores, two independent pgvector HNSW tables, a TimescaleDB momentum hypertable, and the canonical-ID mapping dictionary, with their schemas and constraints. Open this for any storage, schema, or retrieval question.

## Navigation
- §1 — Overview — one engine, four vaults + mapping
- §2 — Vaults — §2.1–§2.7, each store's role and shape
- §3 — Schemas — Silver structured metric, Gold global signal, Gold social pulse
- §4 — Known Constraints

---

## §1 — Overview

A single PostgreSQL engine (pgvector + TimescaleDB extensions) holds all Silver and Gold
data across four functional vaults plus a mapping dictionary. Silver stores hold full
text / raw discourse for drill-down; Gold stores hold embeddings and pre-computed
metrics for retrieval. Flink writes here (`pipeline_processing.md`); the Agentic Hub
(Domain B) reads here.

---

## §2 — Vaults

### §2.1 Knowledge Vault (`persistence/knowledge_vault.py`)
- **Layer:** Silver. **Shape:** JSONB document store.
- Cleaned full-text articles and research papers (news / arXiv / Telegram). `VALID_SOURCES = {newsapi, arxiv, telegram}`.
- `full_text_raw` is the agent's RAG drill-down source (kept; trigram GIN index `idx_kv_fulltext_trgm`). Dedup pre-check on canonical URL before INSERT.

### §2.2 Social Vault (`persistence/social_vault.py`)
- **Layer:** Silver. **Shape:** JSONB discussion store.
- Raw comment trees / threaded discourse (Polymarket, HackerNews). Holds the `post_id`/content-hash reference the Gold summary drills back into.

### §2.3 Knowledge Vectors (`persistence/knowledge_vectors.py`)
- **Layer:** Gold. **Shape:** pgvector, HNSW-indexed.
- Embeddings for direct signals (news / arXiv / Telegram). JSONB `domain_context` extension; no `social_context` column. `ON CONFLICT (signal_id) DO NOTHING`.

### §2.4 Social Vectors (`persistence/social_vectors.py`)
- **Layer:** Gold. **Shape:** pgvector, HNSW-indexed.
- Embeddings for community discourse (Polymarket consensus, HackerNews). JSONB `social_context` + `platform_logic` extensions.

### §2.5 Two-table rationale
`knowledge_vectors` and `social_vectors` each carry an **independent HNSW index**. Mixing
heterogeneous object types (news/research vs. community discourse) in one index degrades
recall — post-hoc `entry_type` filtering consumes neighbor slots *after* the approximate
nearest-neighbor search has run. Separate indexes eliminate that penalty for both the
news/research and social retrieval paths.

### §2.6 Momentum Vault (`persistence/momentum_vault.py`)
- **Layer:** Gold. **Shape:** TimescaleDB hypertable (time-partitioned).
- All numeric metrics (FRED, Polymarket prices, OpenWeather, OpenSky, Google Trends). Stores pre-computed Momentum Block deltas (`change_24h/7d/30d`) so the API serves trends with no runtime math. Idempotent inserts (`ON CONFLICT DO NOTHING`).

### §2.7 Mapping Dictionary (`persistence/mapping_dict.py`)
- Relational store mapping platform-specific IDs → `canonical_event_id` (e.g. linking a Polymarket contract to a Reuters article on the same event).
- `link()`, `lookup_by_canonical()`, `lookup_by_platform_id()`, `find_similar_and_link()` (HNSW cosine, 0.85 threshold) are built and Gate-3-tested. **`find_similar_and_link()` is not yet wired into the Gold pipeline** (KG-A-1).

---

## §3 — Schemas

### §3.1 Silver — Master Structured Metric
Universal skeleton for all numeric data points:
```json
{
  "schema_version": "2.0", "layer": "Silver", "entity_type": "Structured_Metric",
  "core_identity": { "metric_id": "UUID", "canonical_event_id": "String",
                     "parent_id": "String", "source_name": "String",
                     "external_reference_id": "String" },
  "data_point": { "current_value": "Float", "unit": "String",
                  "status": "String", "timestamp_utc": "ISO8601" },
  "momentum_block": { "change_24h": "Float", "change_7d": "Float",
                      "change_30d": "Float", "is_new_market": "Boolean" },
  "metadata_extension": "JSON_OBJECT — type-specific (see source params)"
}
```
Metadata extensions vary by source (Polymarket: spread/volume/whale_alert; FRED:
series_id/observation_date; OpenWeather: coordinates/strategic_tag/wind_speed_knots;
OpenSky: bounding_box_id/aircraft_density_count/transponder_silence_events).

### §3.2 Gold — Global Signal → `knowledge_vectors`
Sources: newsapi, arxiv, telegram. Common `metadata` + `content_vitals` + `enrichment_ai`
(the 10-field Cognitive Metadata) blocks, plus a `domain_context` JSONB extension:

| Type | `domain_context` fields |
|---|---|
| Academic (ArXiv) | `authors`, `is_peer_reviewed`, `citation_count`, `domain_tags` |
| News/Tactical (NewsAPI) | `share_count`, `is_breaking`, `sniper_keywords`, `geospatial_focus` |
| Direct Signal (Telegram) | `is_breaking_alert`, `forwarded_from`, `extracted_links` |

### §3.3 Gold — Social Pulse → `social_vectors`
Sources: polymarket, hackernews. Shares `metadata` + `enrichment_ai`, plus:
```json
{ "social_context": { "community_name": "String", "author_handle": "String?",
                      "author_reputation": "Float", "primary_engagement_score": "Integer" } }
```

| Platform | `entry_type` | `platform_logic` fields |
|---|---|---|
| Polymarket | `market_consensus` | `aggregation_window_hours`, `comment_volume_analyzed`, `consensus_rating`, `market_id_ref` |
| HackerNews | `hackernews_story_summary` | `story_type`, `points`, `top_technical_insights`, `external_link_ref`, `community_sentiment` |

---

## §4 — Known Constraints

| Constraint | Detail |
|---|---|
| Dormant `reddit`/`predictit` enum values | PostgreSQL CHECK constraints (`init.sql`, `postgres-configmap.yaml`) still list `reddit` and `predictit`. No active producer writes them; values are dormant. Enum cleanup deferred to a later infra migration. Tracked KG-A-6. |
| Mapping Dictionary not wired into Gold | `find_similar_and_link()` exists and is tested but is not called from the Gold pipeline; cross-source canonical linkage is not yet automatic. Tracked KG-A-1. |
| Momentum cold-start | OpenWeather/OpenSky have no historical backfill, so 24h/7d/30d deltas are only meaningful once enough live readings accrue; `is_new_market=True` suppresses triggers until then. |
