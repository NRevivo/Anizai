"""
Kafka Topics — Constants for all topic names across Bronze, Silver, Gold, and System layers.

Naming convention: [Layer].[Status].[Source_Group]  (Section 3.1)

Why one constant per topic: prevents string literals scattered across producers and Flink
jobs from drifting out of sync. Every component imports from here — never hardcodes a
topic name.

References:
    - Section 3.1: Topic Hierarchy & Naming Convention
    - Section 3.3: Retention & Lifecycle Policy
    - Section 4.1: Silver Job — which Bronze topics it consumes
    - Section 4.2: Gold Job — which Silver topics it consumes
"""


# ==========================================================
# BRONZE LAYER — one topic per source (Section 3.1)
# Retention: 7 days (Section 3.3)
# ==========================================================

BRONZE_POLYMARKET  = "ingest.bronze.polymarket"
BRONZE_PREDICTIT   = "ingest.bronze.predictit"
BRONZE_TELEGRAM    = "ingest.bronze.telegram"
BRONZE_REDDIT      = "ingest.bronze.reddit"
BRONZE_HACKERNEWS  = "ingest.bronze.hackernews"
BRONZE_NEWSAPI     = "ingest.bronze.newsapi"
BRONZE_ARXIV       = "ingest.bronze.arxiv"
BRONZE_FRED        = "ingest.bronze.fred"
BRONZE_GOOGLETRENDS = "ingest.bronze.googletrends"
BRONZE_OPENWEATHER = "ingest.bronze.openweather"
BRONZE_OPENSKY     = "ingest.bronze.opensky"

# Convenience tuple — used by Flink Silver Job to subscribe to all Bronze topics at once.
ALL_BRONZE_TOPICS = (
    BRONZE_POLYMARKET,
    BRONZE_PREDICTIT,
    BRONZE_TELEGRAM,
    BRONZE_REDDIT,
    BRONZE_HACKERNEWS,
    BRONZE_NEWSAPI,
    BRONZE_ARXIV,
    BRONZE_FRED,
    BRONZE_GOOGLETRENDS,
    BRONZE_OPENWEATHER,
    BRONZE_OPENSKY,
)


# ==========================================================
# SILVER LAYER — aggregated by family schema (Section 3.1)
# Retention: 3 days, purged after persistence to PostgreSQL (Section 3.3)
# ==========================================================

# Social Pulse — Telegram, Reddit, HackerNews, Polymarket comments
SILVER_SOCIAL_PULSE       = "process.silver.social_pulse"

# Global News — NewsAPI, ArXiv
SILVER_GLOBAL_NEWS        = "process.silver.global_news"

# Structured Metrics — FRED, Polymarket prices, PredictIt, OpenWeather, OpenSky
# Note: compaction enabled on this topic so the latest value of each ticker
# (e.g., WTI Crude Oil) is always available for replay (Section 3.3).
SILVER_STRUCTURED_METRICS = "process.silver.structured_metrics"


# ==========================================================
# GOLD LAYER — AI-enriched, vector-ready (Section 3.1)
# Retention: 3 days, purged after persistence to Vector DB (Section 3.3)
# ==========================================================

GOLD_SOCIAL_PULSE         = "serve.gold.social_pulse"
GOLD_GLOBAL_NEWS          = "serve.gold.global_news"
GOLD_STRUCTURED_METRICS   = "serve.gold.structured_metrics"


# ==========================================================
# SYSTEM TOPICS (Section 3.1)
# ==========================================================

# RAG agent → on-demand reactive ingestion requests (Section 2.4)
INGESTION_TRIGGERS = "ingestion_triggers"

# Failed Silver schema validation — never silently dropped (Section 3.5)
DEAD_LETTER_QUEUE  = "dead-letter-queue"


# ==========================================================
# ROUTING MAP — Bronze → Silver family assignment
# ==========================================================
# Used by the Flink Silver Job to route each Bronze topic's
# messages to the correct Silver family topic (Section 4.1).

BRONZE_TO_SILVER_ROUTING = {
    # Social Pulse family
    BRONZE_TELEGRAM:     SILVER_SOCIAL_PULSE,
    BRONZE_REDDIT:       SILVER_SOCIAL_PULSE,
    BRONZE_HACKERNEWS:   SILVER_SOCIAL_PULSE,
    BRONZE_POLYMARKET:   SILVER_SOCIAL_PULSE,    # comments stream

    # Global News family
    BRONZE_NEWSAPI:      SILVER_GLOBAL_NEWS,
    BRONZE_ARXIV:        SILVER_GLOBAL_NEWS,

    # Structured Metrics family
    BRONZE_FRED:         SILVER_STRUCTURED_METRICS,
    BRONZE_PREDICTIT:    SILVER_STRUCTURED_METRICS,
    BRONZE_GOOGLETRENDS: SILVER_STRUCTURED_METRICS,
    BRONZE_OPENWEATHER:  SILVER_STRUCTURED_METRICS,
    BRONZE_OPENSKY:      SILVER_STRUCTURED_METRICS,
    # Note: BRONZE_POLYMARKET also produces price records routed to
    # SILVER_STRUCTURED_METRICS. The Silver Job dispatches by payload
    # type: comments → social_pulse, prices → structured_metrics.
}
