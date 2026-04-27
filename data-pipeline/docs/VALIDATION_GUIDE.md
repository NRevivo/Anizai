# Anizai Data Pipeline — Full End-to-End Validation Guide
## Sprint 17 | Manual Execution Package

---

## QUICK-START CHEAT SHEET

```powershell
# 1. Start everything (from repo root):
cd data-pipeline/infrastructure; docker compose up -d

# 2. Wait ~3 minutes, then submit Flink jobs:
docker exec anizai-flink-jobmanager flink run -py /opt/flink/usrlib/processing/silver_job.py
docker exec anizai-flink-jobmanager flink run -py /opt/flink/usrlib/processing/gold_job.py

# 3. Open in browser:
#   Kafka UI:  http://localhost:8080
#   Flink:     http://localhost:8081
#   Airflow:   http://localhost:8090  (admin / admin_localdev)
#   Grafana:   http://localhost:3000  (admin / admin_localdev)
#   Prometheus:http://localhost:9090

# 4. Start streaming producers (Polymarket + Telegram):
docker exec -d anizai-airflow-scheduler python -m ingestion.polymarket_producer
docker exec -it anizai-airflow-scheduler python -m ingestion.telegram_producer  # interactive on first run

# 5. Trigger all 7 DAGs in Airflow UI (see Section B).
#    Wait 30-60 minutes for data collection.

# 6. After data collection, run the validation summary:
cd data-pipeline; python -m tests.e2e.run_full_validation
```

---

## Section A — Prerequisites

### A.1 Before You Start

Ensure all API keys are in place:

```powershell
# From the data-pipeline/ directory:
Copy-Item .env.example .env
# Edit .env and fill in every required value.
# Minimum required keys for a full validation run:
#   POSTGRES_PASSWORD     (mandatory — docker-compose refuses to start without it)
#   AIRFLOW_FERNET_KEY    (generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
#   OPENAI_API_KEY        (required for Silver translation + Gold enrichment)
#   NEWS_API_KEY          (NewsAPI source)
#   FRED_API_KEY          (FRED economic data)
#   OPENWEATHER_API_KEY   (OpenWeather source)
#   OPENSKY_CLIENT_ID     (OpenSky OAuth2)
#   OPENSKY_CLIENT_SECRET (OpenSky OAuth2)
#   TELEGRAM_API_ID       (Telegram MTProto)
#   TELEGRAM_API_HASH     (Telegram MTProto)
# Keys NOT required (sources work without them):
#   POLYMARKET_API_KEY    (public market data is unauthenticated)
#   HackerNews, ArXiv, GoogleTrends — no keys needed
```

### A.2 Start the Docker Stack

```powershell
# From data-pipeline/infrastructure/:
docker compose up -d
```

This starts 12 services:
- kafka, kafka-ui, kafka-init
- flink-jobmanager, flink-taskmanager
- postgres
- airflow-postgres, airflow-init, airflow-scheduler, airflow-webserver
- prometheus, grafana

**First run note:** The `flink-jobmanager` and `flink-taskmanager` containers build the
custom `anizai-flink:1.19.1` Docker image on first start. This takes 3–5 minutes.
Subsequent starts use the cached image and complete in under 60 seconds.

### A.3 Verify All Services Are Healthy

Wait approximately 3 minutes after `docker compose up -d`, then run:

```powershell
docker ps --format "table {{.Names}}\t{{.Status}}"
```

**Expected output — all containers should show `Up` and `(healthy)` where applicable:**

```
NAMES                         STATUS
anizai-grafana                Up X minutes (healthy)
anizai-prometheus             Up X minutes (healthy)
anizai-airflow-webserver      Up X minutes (healthy)
anizai-airflow-scheduler      Up X minutes (healthy)
anizai-airflow-postgres       Up X minutes (healthy)
anizai-flink-taskmanager      Up X minutes
anizai-flink-jobmanager       Up X minutes (healthy)
anizai-postgres               Up X minutes (healthy)
anizai-kafka-ui               Up X minutes
anizai-kafka-init             Exited (0) X minutes ago   <- normal, one-shot
anizai-airflow-init           Exited (0) X minutes ago   <- normal, one-shot
anizai-kafka                  Up X minutes (healthy)
```

`kafka-init` and `airflow-init` should show `Exited (0)` — they are one-shot setup
containers and exit cleanly after completing their work.

**Per-service health checks:**

```powershell
# Kafka — should return a list of topic names:
docker exec anizai-kafka /opt/kafka/bin/kafka-topics.sh `
  --bootstrap-server localhost:9092 --list

# Expected: 15 topics including ingest.bronze.*, process.silver.*, serve.gold.*, dead-letter-queue

# Flink — should return JSON with {"taskmanagers": 1, ...}:
curl -s http://localhost:8081/overview | python -m json.tool

# PostgreSQL — should print "anizai":
docker exec anizai-postgres psql -U anizai -d anizai -c "SELECT current_database();"

# Airflow — should return {"status": "healthy"}:
curl -s http://localhost:8090/health | python -m json.tool
```

### A.4 Browser Interfaces

| Service | URL | Credentials |
|---------|-----|-------------|
| Kafka UI | http://localhost:8080 | None required |
| Flink Dashboard | http://localhost:8081 | None required |
| Airflow | http://localhost:8090 | `admin` / `admin_localdev` |
| Grafana | http://localhost:3000 | `admin` / `admin_localdev` |
| Prometheus | http://localhost:9090 | None required |

**Kafka UI** — click "Topics" in the left sidebar to confirm all 15 topics were created
by `kafka-init`. You should see all `ingest.bronze.*`, `process.silver.*`,
`serve.gold.*`, `ingestion_triggers`, and `dead-letter-queue` topics.

**Flink Dashboard** — click "Overview" to confirm 1 TaskManager is connected with
4 available task slots. Initially there are 0 running jobs — Flink jobs must be
submitted manually (see Step 3 below).

---

## Section B — Running the Test

Follow these steps **in order**. Each step has a clear wait condition before proceeding.

---

### Step 1 — Submit Flink Jobs (REQUIRED before triggering any DAGs)

Without the Flink jobs running, Bronze messages will accumulate in Kafka but will
never be processed into Silver or Gold. Submit both jobs now:

```powershell
# Submit the Silver Job (Bronze -> Silver transformation):
docker exec anizai-flink-jobmanager `
  flink run -py /opt/flink/usrlib/processing/silver_job.py

# Submit the Gold Job (Silver -> Gold enrichment + persistence):
docker exec anizai-flink-jobmanager `
  flink run -py /opt/flink/usrlib/processing/gold_job.py
```

**Verify:** Open http://localhost:8081/jobs/running — you should see two running jobs:
- `anizai-silver-polymarket`
- `anizai-gold-polymarket`

Both should show status `RUNNING` within 30 seconds of submission.

If a job shows `FAILED` immediately, check the Flink JobManager logs:

```powershell
docker logs anizai-flink-jobmanager --tail 50
```

---

### Step 2 — Start Streaming Producers (Polymarket + Telegram)

These two sources run as continuous streaming processes — they are not managed by
Airflow DAGs. Start them inside the Airflow scheduler container (which shares the
Docker network with Kafka):

**Polymarket (non-interactive — starts in background):**

```powershell
docker exec -d anizai-airflow-scheduler `
  python -m ingestion.polymarket_producer
```

The `-d` flag runs it detached. Verify it started:

```powershell
docker exec anizai-airflow-scheduler sh -c "ps aux | grep polymarket_producer"
```

**Telegram (interactive on first run — requires phone authentication):**

```powershell
docker exec -it anizai-airflow-scheduler `
  python -m ingestion.telegram_producer
```

On first run, Telethon will prompt: `Enter your phone number:`
Enter your Telegram phone number (e.g., `+15551234567`), then enter the
confirmation code sent to your Telegram app. This creates a `.session` file.
Subsequent runs use the session file and start silently.

After authentication completes, the producer runs in the foreground. Leave this
terminal open, or run it with `-d` after the session file exists:

```powershell
# After session file is created — run detached:
docker exec -d anizai-airflow-scheduler `
  python -m ingestion.telegram_producer
```

**Wait condition:** Give both producers 30–60 seconds to establish connections.
Check Kafka UI → Topics → `ingest.bronze.polymarket` and `ingest.bronze.telegram`
to confirm messages are arriving (message count > 0).

---

### Step 3 — Trigger DAGs in Airflow

Open Airflow at http://localhost:8090 and log in with `admin` / `admin_localdev`.

You will see 8 DAGs in the DAG list:

| DAG ID | Schedule | Purpose |
|--------|----------|---------|
| `fred_daily` | 06:00 UTC daily | FRED economic indicators (9 series) |
| `arxiv_daily` | 07:00 UTC daily | ArXiv research papers (7 categories) |
| `googletrends_daily` | 08:00 UTC daily | Google Trends (4 geos, 50 topics) |
| `newsapi_high_frequency` | Every 20 min | NewsAPI headlines (5 categories) |
| `hackernews_high_frequency` | Every 20 min | HackerNews stories (points > 50) |
| `openweather_high_frequency` | Every 10 min | OpenWeather (10 strategic hotspots) |
| `opensky_high_frequency` | Every 3 min | OpenSky flight density (7 bounding boxes) |
| `newsapi_scraper` | Every 30 min | Post-Silver article full-text scraping |

**Trigger order (important):**

First trigger the high-frequency sources — they will produce the most data during
the validation window:

1. Click `opensky_high_frequency` → click the **▶ Run** button (triangle icon, top right of DAG row)
2. Click `openweather_high_frequency` → **▶ Run**
3. Click `newsapi_high_frequency` → **▶ Run**
4. Click `hackernews_high_frequency` → **▶ Run**

Wait 2 minutes, then trigger the daily sources (they run once per trigger):

5. Click `fred_daily` → **▶ Run**
6. Click `arxiv_daily` → **▶ Run**
7. Click `googletrends_daily` → **▶ Run**

**Do NOT trigger `newsapi_scraper` yet** — it needs NewsAPI articles to be in
`knowledge_vault` first (from step 4 above). Trigger it after 10 minutes.

**How to confirm a DAG run succeeded:**
- Click the DAG name to open the DAG detail view
- The latest run should show a green circle (success) or spinning indicator (running)
- Click the run row to see per-task status
- Click a task → "Log" tab to see the task output

**How to check logs for errors:**
- In the task log view, search for `ERROR` or `Traceback`
- Alternatively, from the terminal: `docker logs anizai-airflow-scheduler --tail 100`

---

### Step 4 — Trigger the Scraper DAG

After waiting at least 10 minutes from Step 3 (to give NewsAPI time to land articles
in `knowledge_vault`):

8. Click `newsapi_scraper` → **▶ Run**

The scraper queries `knowledge_vault` for rows where `scrape_attempted = FALSE`,
fetches full article text from scrapable domains (BBC, Guardian, Times of Israel,
Jerusalem Post, Ynetnews, i24 News), and updates `full_text_raw` in-place.

It processes up to 20 articles per run. Trigger it again after 30 minutes to
process the next batch.

---

### Step 5 — Wait for Data Collection

**Minimum wait:** 30 minutes from triggering the first DAG.
**Recommended wait:** 60 minutes for a richer dataset (multiple NewsAPI + HackerNews + OpenSky cycles).

**While waiting — what to watch:**

Kafka UI (http://localhost:8080):
- Click each `ingest.bronze.*` topic → "Messages" tab → confirm message count is growing

Flink Dashboard (http://localhost:8081):
- Click "Jobs" → click the running Silver job → "Subtasks" tab
- The `numRecordsIn` counter should be incrementing as Bronze messages arrive

Grafana (http://localhost:3000):
- Open "Anizai Pipeline" dashboard (left sidebar → Dashboards)
- "Throughput" row: should show a rising `records/sec` line for the Silver job
- "Checkpoint" row: should show checkpoint duration < 10 seconds every 60 seconds

---

### Step 6 — Run Validation Script

After data collection is complete:

```powershell
# From the data-pipeline/ directory (venv active):
cd data-pipeline
python -m tests.e2e.run_full_validation
```

This prints a structured summary of all vault tables. See Section D for expected output.

---

## Section C — Where to See Results

### C.1 knowledge_vault (NewsAPI + ArXiv + Telegram — full-text documents)

```sql
-- Connect to PostgreSQL:
docker exec -it anizai-postgres psql -U anizai -d anizai

-- Record count by source:
SELECT source_name, COUNT(*) AS records
FROM knowledge_vault
GROUP BY source_name
ORDER BY source_name;

-- Sample record (shows key fields):
SELECT
    doc_id,
    source_name,
    LEFT(full_text_raw, 200)    AS text_preview,
    relevance_score,
    scrape_attempted,
    ingested_at
FROM knowledge_vault
ORDER BY ingested_at DESC
LIMIT 3;

-- Check scraping progress:
SELECT
    scrape_attempted,
    COUNT(*) AS count
FROM knowledge_vault
GROUP BY scrape_attempted;
```

**What you should see:** Rows grouped by `newsapi`, `arxiv`, `telegram` with
`relevance_score` between 0.0 and 1.0. After the scraper DAG runs, some rows
will show `scrape_attempted = true` and longer `full_text_raw` values.

---

### C.2 knowledge_vectors (Gold embeddings — NewsAPI + ArXiv + Telegram)

```sql
-- Record count by source platform:
SELECT source_platform, entry_type, COUNT(*) AS records
FROM knowledge_vectors
GROUP BY source_platform, entry_type
ORDER BY source_platform;

-- Sample record with AI enrichment:
SELECT
    signal_id,
    source_platform,
    entry_type,
    content_vitals->>'title'                  AS title,
    (enrichment_ai->>'impact_level')::int     AS impact,
    (enrichment_ai->>'urgency_level')::int    AS urgency,
    (enrichment_ai->>'sentiment_score')::float AS sentiment,
    LEFT(enrichment_ai->>'executive_summary', 150) AS summary
FROM knowledge_vectors
ORDER BY ingested_at DESC
LIMIT 3;
```

**What you should see:** Rows with `source_platform` = `newsapi`, `arxiv`, or `telegram`.
`impact_level` between 1–5, `sentiment_score` between -1.0 and 1.0.
The `embedding` column holds a 1536-dimension vector (not shown here for brevity).

---

### C.3 social_vault (Polymarket + HackerNews — raw discourse)

```sql
-- Record count by source:
SELECT source_name, COUNT(*) AS records
FROM social_vault
GROUP BY source_name
ORDER BY source_name;

-- Sample HackerNews record:
SELECT
    social_id,
    source_name,
    platform_data->>'title'   AS title,
    platform_data->>'points'  AS points,
    ingested_at
FROM social_vault
WHERE source_name = 'hackernews'
ORDER BY ingested_at DESC
LIMIT 3;

-- Sample Polymarket record:
SELECT
    social_id,
    platform_data->>'market_id' AS market_id,
    ingested_at
FROM social_vault
WHERE source_name = 'polymarket'
ORDER BY ingested_at DESC
LIMIT 3;
```

---

### C.4 social_vectors (Gold embeddings — Polymarket consensus + HackerNews summaries)

```sql
-- Record count by source:
SELECT source_platform, entry_type, COUNT(*) AS records
FROM social_vectors
GROUP BY source_platform, entry_type
ORDER BY source_platform;

-- Sample record:
SELECT
    signal_id,
    source_platform,
    entry_type,
    content_vitals->>'title'               AS title,
    social_context->>'community_name'      AS community,
    (enrichment_ai->>'impact_level')::int  AS impact,
    LEFT(enrichment_ai->>'executive_summary', 150) AS summary
FROM social_vectors
ORDER BY ingested_at DESC
LIMIT 3;
```

**Note:** Polymarket entries have `entry_type = 'market_consensus'` — these are
Flink-generated summaries of temporal comment groups (4-hour windows), not
individual messages. There will be fewer rows here than in `social_vault`.

---

### C.5 momentum_vault (FRED + GoogleTrends + OpenWeather + OpenSky + Polymarket prices)

```sql
-- Record count by source:
SELECT source_name, COUNT(*) AS records
FROM momentum_vault
GROUP BY source_name
ORDER BY source_name;

-- FRED — sample with momentum block:
SELECT
    external_reference_id       AS series_id,
    current_value,
    unit,
    change_24h,
    change_30d,
    timestamp_utc
FROM momentum_vault
WHERE source_name = 'fred'
ORDER BY timestamp_utc DESC
LIMIT 9;

-- Automation triggers fired (records with trigger_type set):
SELECT
    source_name,
    external_reference_id,
    metadata_extension->>'trigger_type'   AS trigger,
    metadata_extension->>'trigger_label'  AS label,
    current_value,
    timestamp_utc
FROM momentum_vault
WHERE metadata_extension->>'trigger_type' IS NOT NULL
ORDER BY timestamp_utc DESC
LIMIT 10;

-- OpenWeather — sample with strategic tags:
SELECT
    external_reference_id           AS location,
    current_value                   AS temperature_c,
    metadata_extension->>'strategic_tag'       AS tag,
    metadata_extension->>'condition_severity'  AS severity,
    timestamp_utc
FROM momentum_vault
WHERE source_name = 'openweather'
ORDER BY timestamp_utc DESC
LIMIT 5;
```

---

### C.6 mapping_dict (Cross-platform canonical event linkages)

```sql
-- Total linkages:
SELECT COUNT(*) AS total_linkages FROM mapping_dict;

-- Linkages by platform:
SELECT platform, COUNT(*) AS count
FROM mapping_dict
GROUP BY platform
ORDER BY platform;

-- Sample linkages:
SELECT
    canonical_event_id,
    platform,
    platform_specific_id,
    similarity_score,
    created_at
FROM mapping_dict
ORDER BY created_at DESC
LIMIT 10;
```

**Note:** `mapping_dict` is populated by the `persistence/mapping_dict.py` module
whenever two records from different sources are linked via vector similarity > 0.85.
Early in a validation run it may be empty — this is expected on first run.

---

### C.7 Grafana Dashboard

Open http://localhost:3000 → log in → left sidebar → **Dashboards** →
click **"Anizai Pipeline"**.

The dashboard has three row sections:

| Row | Panels | What to Look For |
|-----|--------|-----------------|
| **Throughput** | Records/sec (Silver), Records/sec (Gold) | Should show non-zero rate when DAGs are running |
| **Checkpoint** | Checkpoint duration, Checkpoint lag | Duration < 10s, lag < 60s = healthy |
| **JVM / Resources** | Heap usage, GC count | Heap < 80% of assigned memory |

If panels show "No data": Prometheus may still be waiting for the Flink jobs to
expose metrics. Flink metrics are only available once a job is in `RUNNING` state.
Wait 30 seconds and click the refresh button (top right of dashboard).

---

### C.8 Searching Logs by trace_id

Every Kafka message envelope carries a `trace_id`. Find one from the DB:

```sql
-- Get a trace_id from a recent knowledge_vectors record:
SELECT raw_data_ref AS trace_id_hint FROM knowledge_vectors ORDER BY ingested_at DESC LIMIT 1;
```

Then search the Airflow scheduler logs for that ID:

```powershell
docker logs anizai-airflow-scheduler 2>&1 | Select-String "<paste-trace-id-here>"
```

Or search Flink TaskManager logs:

```powershell
docker logs anizai-flink-taskmanager 2>&1 | Select-String "<paste-trace-id-here>"
```

A complete trace looks like:
```
Producer  -> [INFO] Envelope built  trace_id=abc123...
Silver    -> [INFO] Processed       trace_id=abc123...  source=newsapi
Gold      -> [INFO] Enriched        trace_id=abc123...  impact=4  urgency=3
Vault     -> [INFO] Inserted        trace_id=abc123...  table=knowledge_vectors
```

---

## Section D — Expected Results Table

After a 60-minute validation run, you should see approximately the following:

| Source | Vault Table | Expected Records | Notes |
|--------|-------------|-----------------|-------|
| **NewsAPI** | `knowledge_vault` | 20–100 | 3 runs x ~5–15 articles/run after Keyword Sniper |
| **NewsAPI** | `knowledge_vectors` | 20–100 | 1:1 with knowledge_vault (Gold enrichment) |
| **ArXiv** | `knowledge_vault` | 50–200 | Daily run, 7 categories x up to ~30 papers each |
| **ArXiv** | `knowledge_vectors` | 50–200 | 1:1 with knowledge_vault |
| **Telegram** | `knowledge_vault` | 5–50 | Depends on channel activity at time of run |
| **Telegram** | `knowledge_vectors` | 5–50 | 1:1 with knowledge_vault |
| **HackerNews** | `social_vault` | 10–60 | 3 runs x ~5–20 stories with points > 50 |
| **HackerNews** | `social_vectors` | 10–60 | 1:1 (each story -> one summary vector) |
| **Polymarket** | `social_vault` | 10–100 | Depends on active market comment volume |
| **Polymarket** | `social_vectors` | 1–10 | Fewer than social_vault — temporal bundling collapses many comments into consensus vectors |
| **FRED** | `momentum_vault` | 9–27 | 9 series x 1–3 observations per pulse run |
| **GoogleTrends** | `momentum_vault` | 50–200 | Top 50 trending topics x 4 geo regions |
| **OpenWeather** | `momentum_vault` | 60–600 | 10 hotspots x 6 polls/hour (every 10 min) |
| **OpenSky** | `momentum_vault` | 140–1400 | 7 boxes x 20 polls/hour (every 3 min) |
| **Polymarket prices** | `momentum_vault` | 10–100 | 5–10 min intervals from WebSocket producer |
| **mapping_dict** | `mapping_dict` | 0–50 | Low on first run; grows as data volume increases |

**Scraping (NewsAPI articles):**

| Metric | Expected Value |
|--------|---------------|
| `scrape_attempted = TRUE` | 0–60 articles (up to 20 per 30-min scraper DAG run) |
| Scrape success rate | 40–80% (CNN excluded — JS-rendered; some outlets rate-limit) |
| Scrapable domains | BBC, Guardian, Times of Israel, Jerusalem Post, Ynetnews, i24 News |

**Automation Triggers (momentum_vault):**

| Trigger | Source | Condition | Expected Frequency |
|---------|--------|-----------|-------------------|
| `Yield_Curve_Inversion` | FRED / T10Y2Y | `T10Y2Y < 0` | Fired if current spread is negative |
| `Extreme_Volatility` | FRED / VIXCLS | `VIX > 30` | Fired during high-uncertainty periods |
| `Public_Hype_Alert` | GoogleTrends | Score delta > 50 in 24h | Rare — only on rapid trend spikes |
| `Natural_Disaster_Alert` | OpenWeather | Severe weather condition | Rare — depends on active weather |
| `Tech_Supply_Risk` | OpenWeather | Extreme weather at Taipei/Hsinchu | Rare — depends on Taiwan weather |
| `Potential_Shipping_Delay` | OpenWeather | Wind > 50 knots at maritime chokepoint | Rare |
| `Aerial_Escalation_Risk` | OpenSky | Aircraft density > 30% above 30-day avg | Depends on zone activity |

**Grafana — What to Expect with Live Data:**

- `Records/sec (Silver)`: Non-zero spikes when OpenSky (every 3 min) or OpenWeather (every 10 min) DAGs run
- `Checkpoint duration`: Should complete in 1–3 seconds under normal load
- `JVM Heap`: Should stabilize at 30–50% of allocated 2GB TaskManager memory

**If a source has 0 records after 60 minutes:**

| Symptom | Likely Cause |
|---------|-------------|
| All sources empty | Flink jobs not submitted (Step 1 skipped) |
| Only momentum sources empty | `.env` missing API keys for FRED/OpenWeather/OpenSky |
| Only Telegram empty | Session file not created / API credentials wrong |
| Only ArXiv empty | Daily DAG not triggered, or ArXiv API temporarily unavailable |
| DLQ has messages | Schema validation failures — run the DLQ check in the Troubleshooting section below |

---

## Troubleshooting

### Check the Dead-Letter Queue

If records are missing unexpectedly, check DLQ for validation failures:

```powershell
docker exec anizai-kafka `
  /opt/kafka/bin/kafka-console-consumer.sh `
  --bootstrap-server localhost:9092 `
  --topic dead-letter-queue `
  --from-beginning `
  --max-messages 10
```

### Restart a Failed Flink Job

If a Flink job shows `FAILED` in the dashboard:

```powershell
# Check what went wrong:
docker logs anizai-flink-taskmanager --tail 100

# Resubmit (jobs are stateless restarts — checkpoint state may be used):
docker exec anizai-flink-jobmanager `
  flink run -py /opt/flink/usrlib/processing/silver_job.py
```

### Force-Trigger a Stuck Airflow DAG

```powershell
# Unpause and manually trigger a DAG via CLI:
docker exec anizai-airflow-scheduler airflow dags trigger fred_daily
docker exec anizai-airflow-scheduler airflow dags trigger newsapi_high_frequency
```

### View Airflow Task Logs from Terminal

```powershell
# Stream scheduler logs (all DAG runs):
docker logs -f anizai-airflow-scheduler

# View logs for a specific DAG run:
docker exec anizai-airflow-scheduler `
  airflow tasks logs fred_daily fred_pulse <run_id>
# (Find run_id from: docker exec anizai-airflow-scheduler airflow dags list-runs -d fred_daily)
```

### Stop Everything

```powershell
cd data-pipeline/infrastructure
docker compose down

# To also delete all stored data (Kafka messages, PostgreSQL rows, Flink checkpoints):
docker compose down -v
```
