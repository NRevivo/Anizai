# pipeline_sources.md
> Domain: A — Pipeline
> Type: Spec
> Last updated: 2026-06-15
> TL;DR: The 9 active producers — their connection method, polling cadence, per-source filtering parameters, and the common Bronze envelope they emit. Open this when adding, debugging, or tuning a data source.

## Navigation
- §1 — Overview — what a producer does and where it sits
- §2 — Bronze Contract — the envelope and raw-payload schema all producers share
- §3 — Producer Matrix — connection, cadence, and target topic at a glance
- §4 — Per-Source Parameters — §4.1–§4.9, one subsection per producer
- §5 — Reactive Ingestion — on-demand re-fetch loop
- §6 — Known Constraints — degraded or disabled producer paths

---

## §1 — Overview

Producers are **ingestion-only** (no transformation, no DB writes — Service Isolation).
Each fetches from one external source, wraps the raw response in the standard Bronze
envelope, and emits NDJSON to exactly one Bronze Kafka topic. For topic routing, Silver
processing, and enrichment see `pipeline_processing.md`; for storage see
`pipeline_storage.md`.

---

## §2 — Bronze Contract

Every producer emits an NDJSON envelope wrapping a raw payload:

```json
{
  "ingestion_id":        "UUID — unique per fetch operation",
  "source_name":         "String (e.g., polymarket, newsapi, telegram)",
  "source_endpoint":     "String — specific URL or channel",
  "ingestion_timestamp": "ISO8601 UTC",
  "producer_version":    "String — code version for debug tracking",
  "raw_payload":         "Object — raw object as received from the API",
  "metadata": { "http_status_code": "Integer", "request_duration_ms": "Long" }
}
```

> Streaming transports (Telegram MTProto) set `http_status_code=0`,
> `request_duration_ms=0` — there is no HTTP request/response cycle.

---

## §3 — Producer Matrix

| Source | Connection | Cadence | Bronze topic |
|---|---|---|---|
| Polymarket | WebSocket (CLOB) + REST | Real-time; odds 5–10 min | `ingest.bronze.polymarket` |
| Telegram | MTProto streaming (Telethon) | Real-time (event-driven) | `ingest.bronze.telegram` |
| Hacker News | REST (Algolia) | Every 20 min | `ingest.bronze.hackernews` |
| NewsAPI | REST (newsapi.ai / Event Registry) | Every 20 min | `ingest.bronze.newsapi` |
| ArXiv | REST (Atom XML) | Daily 07:00 UTC | `ingest.bronze.arxiv` |
| FRED | REST (JSON) | Daily 06:00 UTC | `ingest.bronze.fred` |
| Google Trends | Pytrends | Daily 08:00 UTC + reactive | `ingest.bronze.googletrends` |
| OpenWeather | REST (`/weather`) | Every 10 min + reactive | `ingest.bronze.openweather` |
| OpenSky | REST (`/states/all`) | Every 3 min (DAG) + reactive | `ingest.bronze.opensky` |

---

## §4 — Per-Source Parameters

### §4.1 Polymarket
- **Method:** WebSocket CLOB stream for prices; REST (Gamma) for market metadata.
- **Pulse:** Market odds every 5–10 min; high-volume markets only.
- **Whale Alert:** Single position `>$100,000` → `impact_level: 5`.
- **Silver routing:** prices → `structured_metrics`; comments → `social_pulse`.
- **Note:** only `event_type=last_trade` WebSocket events produce a Silver metric (others skipped). Comment ingestion is currently **disabled** (`POLYMARKET_COMMENTS_ENABLED=false`, see §6).

### §4.2 Telegram
- **Method:** MTProto streaming via Telethon; single-message micro-articles; media-only messages ignored.
- **7 channels:** `abualiexpress`, `yediotnews25` (100Field News), `Faytuks_Network`, `clashreport`, `financialjuice`, `disclosetv`, `intelslava`.
- **Translation:** `abualiexpress` and `yediotnews25` are Hebrew → translated to English at the Silver layer (GPT-3.5-turbo); all other channels English.
- **No Impact Boost** — channel authority is structural (the 7-entry registry), not per-message.

### §4.3 Hacker News
- **Method:** Algolia Search API (`search_by_date`), no API key.
- **Pulse:** Every 20 min; stories with `points > 50`; top 10 first-level comments per story.
- **Silver store:** Social Vault (Story-Centric); vector target `social_vectors`.

### §4.4 NewsAPI (newsapi.ai / Event Registry)
- **Endpoint:** `https://eventregistry.org/api/v1/article/getArticles`; auth via `apiKey` (`NEWSAI_API_KEY`); full article `body` (`articleBodyLen=-1`, `includeArticleBody=true`, `lang=eng`).
- **5 categories (`categoryUri`):** `news/Business`, `news/Technology`, `news/Health`, `news/Science`, `news/Politics`. (The `news` root category returns 0 results and is not used.)
- **Authority whitelist:** ~15 wire/financial/regional domains via `sourceUri` (tier-1 = 8 wire/financial domains).
- **Impact Boost:** Israeli / Middle East / energy articles → automatic `+1` to `impact_level`.
- **Pagination:** `articlesPage` + `articlesCount` (default `NEWSAI_PAGE_SIZE=10`).

### §4.5 ArXiv
- **Method:** Atom XML REST, no API key; mandatory 3-second delay between requests (TOS).
- **Baseline:** Daily, up to 200 papers/category.
- **7 categories:** `cs.AI`, `cs.LG`, `econ.GN`, `q-fin.ST`, `q-bio.PE`, `stat.AP`, `cs.CY`.
- **SNR keywords:** Regulation, Policy, Market Impact, Outbreak, Intervention, Trend.
- **No Impact Boost; no authority whitelist** (single origin, arxiv.org). `full_text_raw == inverted_pyramid_lead == abstract`.

### §4.6 FRED
- **Endpoint:** `/fred/series/observations` (`file_type=json`); daily polling for latest observations.
- **9 series:** `FEDFUNDS`, `CPIAUCSL`, `UNRATE`, `CSUSHPINSA`, `DCOILWTICO`, `GASREGW`, `DHHNGSP`, `VIXCLS`, `T10Y2Y`.
- **Gold automation triggers:** `T10Y2Y < 0` → `Market_Anomaly` (impact 5); `VIXCLS > 30` → impact 5; any price metric variance `>5%`/24h → flagged.
- Stable monthly series (e.g. FEDFUNDS) legitimately emit zero deltas — not a bug.

### §4.7 Google Trends
- **Method:** Pytrends (unofficial endpoint).
- **Static pulse:** Daily top-50 trending topics across 4 geos: US, GB, IL, DE.
- **Reactive mode:** On-demand by the agent for specific keywords.
- **Gold trigger:** `Public_Hype_Alert` if score increases `>50 points` in 24h.
- **Status:** Degraded — Google moved the unofficial endpoint; see §6.

### §4.8 OpenWeather
- **Endpoint:** OpenWeatherMap `/weather` REST.
- **Static pulse:** Every 10 min for all hotspots; **reactive** escalates to every 2 min for a 60-min window.
- **10 strategic hotspots:** Taipei, Tokyo, Kyiv, Tel Aviv, Washington D.C., Hsinchu Science Park, Strait of Hormuz, Suez Canal, Port of Houston, Ukraine/US wheat regions.
- **Silver mapping:** `current_value=temperature_celsius`; `weather[0].id` → `condition_severity` (1–5); `wind.speed` m/s × 1.94384 → `wind_speed_knots`. Missing `wind`/`weather` arrays → **DLQ** (never default to zero).
- **Gold triggers:** `Natural_Disaster_Alert` (severity ≥4 → impact 5), `Tech_Supply_Risk` (severity ≥4 + Taiwan/Japan tech tag), `Potential_Shipping_Delay` (`wind_speed_knots > 50` + maritime tag).
- **No backfill** — free-tier OWM has no History API; momentum baseline accrues from first live run.

### §4.9 OpenSky
- **Endpoint:** `/states/all` with bounding-box filtering; **OAuth2 client_credentials** (`OPENSKY_CLIENT_ID`/`SECRET`, Bearer token, proactive refresh).
- **Cadence:** Static 1-min one-shot (Airflow DAG every 3 min, rate-limit-safe under the per-day cap) + reactive 30s × 30-min callsign tracking.
- **7 bounding boxes:** `polish_ukrainian_border`, `taiwan_strait`, `iranian_borders`, `strait_of_hormuz`, `bab_al_mandab`, `eastern_mediterranean`, `washington_dc_airspace`.
- **Silver mapping:** `current_value=aircraft_density_count`; domain filter computes `transponder_silence_events` (`lat is None AND lon is None AND on_ground=False`).
- **Gold triggers:** `Aerial_Escalation_Risk` (`change_30d > 0.30` → impact 4), `Transponder_Deactivation_Alert` (silence > 0 in a tension zone). Empty `states=[]` is valid (`aircraft_count=0`); cold-start (`is_new_market=True`) suppresses all triggers.

---

## §5 — Reactive Ingestion

The agent can request an on-demand re-fetch by publishing to the `ingestion_triggers`
Kafka topic. `orchestration/ingestion_trigger_consumer.py` validates the trigger, routes
it to the matching producer's `run_reactive()` entry point (OpenWeather, OpenSky, or
Google Trends), and dispatches it in a daemon thread so the reactive window does not block
the consumer loop. Producer classes are lazily imported to avoid loading heavy
dependencies at module import time. Malformed triggers route to `dead-letter-queue`.

> The hub-side node that *publishes* these triggers belongs to the Agentic Hub (Domain B).
> This section covers only the pipeline consumer.

---

## §6 — Known Constraints

| Constraint | Source | Detail |
|---|---|---|
| Google Trends 404 | googletrends | Pytrends 4.9.2 (latest) hits Google's moved unofficial endpoint → 404 for all geos, 0 Bronze messages. Producer raises on 0% success so Airflow marks the task `failed`. Tracked KG-A-3. |
| Polymarket comments disabled | polymarket | Gamma `/comments` breaking change (now requires `parent_entity_id` + unknown `entity_entity_type` enum). Feature-flagged off (`POLYMARKET_COMMENTS_ENABLED=false`); price/momentum path unaffected. Tracked KG-A-4. |
| OpenSky reachability (cloud) | opensky | `ConnectTimeoutError` to `opensky-network.org` from the GKE main-pool node; suspected cloud-IP block or firewall rule. Local ingestion works. Tracked KG-A-5. |
| No historical backfill | openweather, opensky | Both APIs are real-time only; momentum baselines accrue organically from first live run. |
