# pipeline_processing.md
> Domain: A — Pipeline
> Type: Spec
> Last updated: 2026-06-15
> TL;DR: The Kafka topic hierarchy and the two Flink jobs (Silver standardization, Gold enrichment), including the two-stage keyword filter, retention policy, and the PyFlink runtime contract. Open this when changing topic routing, the filter, or any Flink transform.

## Navigation
- §1 — Overview — where processing sits
- §2 — Kafka Topic Hierarchy — Bronze / Silver / Gold / system topics
- §3 — Message Envelope — the NDJSON paper trail
- §4 — Retention & Lifecycle — per-layer retention policy
- §5 — Silver Job — standardization, filtering, dedup, translation, DLQ
- §6 — Gold Job — Cognitive Metadata, consensus, momentum, triggers
- §7 — Two-Stage Filter — deterministic sniper + semantic rescue
- §8 — Flink Runtime — PyFlink 1.19 contract
- §9 — Known Constraints

---

## §1 — Overview

Flink is the processing core: two stateful jobs consume Bronze, standardize to Silver,
and enrich to Gold. Producers feed it (`pipeline_sources.md`); persistence drains it
(`pipeline_storage.md`). Kafka is the single source of truth between every stage.

---

## §2 — Kafka Topic Hierarchy

Convention: `[layer].[status].[source_group]`.

**Bronze — one topic per source (isolation & fault tolerance):**
```
ingest.bronze.polymarket    ingest.bronze.telegram     ingest.bronze.hackernews
ingest.bronze.newsapi       ingest.bronze.arxiv        ingest.bronze.fred
ingest.bronze.googletrends  ingest.bronze.openweather  ingest.bronze.opensky
```

**Silver — aggregated by family schema:**
```
process.silver.social_pulse        # Telegram→no; HackerNews, Polymarket comments
process.silver.global_news         # NewsAPI, ArXiv, Telegram
process.silver.structured_metrics  # FRED, Polymarket prices, OpenWeather, OpenSky, GoogleTrends
```

**Gold — AI-enriched, vector/metric-ready:**
```
serve.gold.social_pulse   serve.gold.global_news   serve.gold.structured_metrics
```

**System topics:**
```
ingestion_triggers   # agent → on-demand reactive ingestion requests
dead-letter-queue    # objects failing Silver schema validation — never silently dropped
```

> Telegram routes to `global_news` (vetted OSINT/news direct signals), **not**
> `social_pulse` — a routing correction made before Silver implementation.

---

## §3 — Message Envelope

Every message carries a standard NDJSON envelope for an end-to-end paper trail:

```json
{
  "event_id":           "UUIDv4 — shared across all layers",
  "trace_id":           "tracks a message Producer → Gold",
  "producer_timestamp": "ISO8601 UTC — original capture time",
  "schema_version":     "for forward-compatible schema changes",
  "payload":            "the actual data object"
}
```

---

## §4 — Retention & Lifecycle

| Layer / topic group | Policy | Rationale |
|---|---|---|
| Bronze | `delete`, 7 days | Audit + replay if Silver processing fails |
| Silver | `delete`, 3 days | Purged after persistence to PostgreSQL |
| Gold | `delete`, 3 days | Purged after persistence to the vector/metric stores |
| `structured_metrics` (Silver + Gold) | `cleanup.policy=delete`, 3 days | PyFlink 1.19 emits keyless messages; compacted topics reject keyless records, so these topics use `delete`, not compaction |

---

## §5 — Silver Job (`processing/silver_job.py`)

Consumes Bronze, standardizes, and routes to Silver topics. Transforms:

- **A. Keyword Sniper + semantic rescue** — two-stage relevance gate (see §7).
- **B. Dual-store routing** — documents (news/research/Telegram) → Knowledge Vault path; social discussion (Polymarket/HackerNews) → Social Vault path; numeric metrics → structured_metrics.
- **C. Deduplication (SHA-256)** — content hash on the **original** text (stable across translation/reruns); canonical URL as the dedup key.
- **D. Translation** — non-English signals (Hebrew Telegram channels, IL/DE Google Trends keywords) → English via GPT-3.5-turbo at the Silver layer. Hash is taken before translation; the sniper runs on the translated text.
- **DLQ** — any object failing Silver schema validation routes to `dead-letter-queue`. Each source has its own DLQ decision branch.

---

## §6 — Gold Job (`processing/gold_job.py`)

Three pipelines: Social Pulse, Global News, Structured Metrics.

**A. Semantic enrichment (Social & Global News)** — via OpenAI GPT-4o:
- **Cognitive Metadata Extraction** (shared 10-field schema): `impact_level` (1–5), `urgency_level` (1–5), `reliability_score` (0.0–1.0), `sentiment_score` (-1.0–1.0), `extracted_entities`, `topic_classification`, `fact_check_flag`, plus an executive summary and key findings.
- **Temporal Consensus Bundling** — social comments grouped into temporal blocks (`processing/consensus.py` windowing helpers); GPT-4o summarizes each block into a single Consensus Vector rather than embedding individual messages.
- Embeddings via `text-embedding-3-small`. Deterministic `signal_id = uuid5(content_hash)` so re-deliveries dedup via `ON CONFLICT DO NOTHING` — **as of Phase 7D (T5, KG-A-8) the global_news builders (newsapi/arxiv/telegram) use this too**, not just the social path; live-verified 1:1 vectors:archives (newsapi 62=62, arxiv 102=102). Going-forward-only: pre-existing `uuid4` rows are not retroactively deduped.
- **Pre-dispatch enrichment dedup gate (Phase 7D, KG-A-7 — `ENRICHMENT_DEDUP_GATE_ENABLED`, default on).** Before the LLM call, both Gold paths skip enrichment for an already-seen item: global_news gates on `knowledge_vault.archive()` returning `None` **without raising** (a duplicate `document_hash`; an archive *failure* fail-opens to enrichment, per D3); the social/HackerNews path gates on `exists_by_content_hash()`. On a skip: no enrichment, no embedding, no Gold build, no vector write — logged INFO `[gold/dedup]`. **Live-verified 2026-07-27:** arxiv **18.1→1.0** and newsapi **1.74→1.0** `gold_enrich` per distinct item, 0 wasted enrichment, 0 DLQ regression. The dedup guard inside `archive()` and the `ON CONFLICT` guards remain last-resort backstops regardless of the flag.

**B. Structural enrichment (Structured Metrics)** — no OpenAI:
- **Momentum Block** — deterministic `change_24h`, `change_7d`, `change_30d` from keyed state, so the agent answers "what's the trend?" without runtime math.
- **Deterministic triggers** — per-source automation (FRED `Market_Anomaly`/extreme-VIX; OpenWeather `Natural_Disaster_Alert`/`Tech_Supply_Risk`/`Potential_Shipping_Delay`; OpenSky `Aerial_Escalation_Risk`/`Transponder_Deactivation_Alert`; Google Trends `Public_Hype_Alert`). Cold-start (`is_new_market=True`) suppresses triggers.
- **Market Divergence Alerts** — **dormant.** The cross-platform price-divergence logic and schema are retained but inactive: divergence requires a second prediction-market platform, and Polymarket is currently the only one. Reactivates if a second platform is added.

---

## §7 — Two-Stage Filter

Replaces the original single-layer keyword filter. Runs in the Global News Gold path:

1. **Deterministic sniper** (`processing/keyword_sniper.py`) — scores title/description/body against `MASTER_KEYWORD_LIST` with positional/density weighting and word-boundary regex. Items scoring ≥ `DEFAULT_THRESHOLD = 0.15` pass. 10 noisy single-word terms (e.g. `strike`, `attack`, `vote`, `energy`) were removed; compound forms (`missile defense`, `energy crisis`) retained.
2. **Semantic rescue** — articles that fail the sniper are embedded (`text-embedding-3-small`) and compared by cosine similarity against `processing/sniper_reference_vector.npy` (mean-pooled embedding of the keyword set). Items scoring ≥ `GOLD_SEMANTIC_RESCUE_THRESHOLD = 0.35` are promoted (`is_high_signal=True`).

**Drop semantics:** articles failing **both** stages are dropped entirely — no `kv_archive`, no Gold. This is a deliberate change from the old "archive every Silver doc" behavior; the vault becomes strictly higher-signal. Drops and promotions both log at INFO.

**Fail-safety:**
- Missing `sniper_reference_vector.npy` at `open()` → hard `raise` (no silent regression to keyword-only filtering).
- OpenAI embedding failure during rescue → `dead-letter-queue` (cannot make a sound rescue/drop decision without the embedding).

**Social-path reject capture (Phase 7D, T6 — KG-A-12).** The two-stage filter above runs in the Global News
Gold path; as of Phase 7D the **HackerNews** path also computes the rescue cosine and writes a `filter_rejects`
row when a low-signal story is dropped (gated by `REJECT_CAPTURE_ENABLED`, fail-open), so HackerNews reject rate
is measurable for the first time. Per D2 the social path stores the cosine but does **not** wire the promote
branch — 7B.5 owns the social threshold (the sniper reference vector is news-built, so HN cosines are **not**
comparable to the news-calibrated 0.35; live max HN cosine 0.3523 already exceeds 0.35). The T6 capture is
ordered **before** the T4 dedup gate, so a low-signal HN duplicate is captured on every pulse. Live 2026-07-27:
288 HN rejects, 50 distinct stories, 0 NULL instance keys.

> The thresholds (0.15, 0.35) were set theoretically in Phase 7B; empirical calibration
> against production vault rows is the open Phase 7B.5 work (`pipeline_sprints.md §2`).

---

## §8 — Flink Runtime

- **Image:** custom `anizai-flink:1.19.1` (Python 3.11 + PyFlink 1.19.1 + Kafka SQL connector uber-JAR). Same image for JobManager and TaskManager (byte-identical operator bytecode).
- **Routing pattern:** PyFlink 1.19 has no `OutputTag` / `ctx.output()` / `.returns()`. Side outputs use **tagged tuple routing**: `yield (tag, json_str)` with `output_type=Types.TUPLE([...])`, split downstream via `filter().map()`.
- **Semantics:** EXACTLY_ONCE; checkpointing every 60s (momentum keyed state + in-flight AI requests survive restarts).
- **Dispatch:** Gold metrics read `core_identity.source_name`; Gold social/document records read top-level `source_name`.

---

## §9 — Known Constraints

| Constraint | Detail |
|---|---|
| Divergence Alerts dormant | Needs a second market platform; logic + schema retained. |
| Filter thresholds un-calibrated | 0.15 / 0.35 are theoretical; Phase 7B.5 validates them on production data. |
| `PolymarketGoldMetricsFunction` misnamed | Now dispatches 5 metric sources (Polymarket/FRED/GoogleTrends/OpenWeather/OpenSky); cosmetic rename deferred. Tracked KG-A-2. |
| Flink code reload | Code-bearing image rebuilds require a job cancel + re-submit, not just a pod restart (HA preserves the old compiled job graph). Operational note, not a code gap. |
