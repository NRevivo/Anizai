"""
Config Package — Centralized project configuration.
Exposes Kafka topic constants, API credentials, and file paths.

References: Section 3.1 (Topic Hierarchy), Section 2.1 (Producer Matrix).
"""

from .kafka_topics import (
    # Bronze (BRONZE_PREDICTIT omitted — API permanently shut down CFTC 2022-2024)
    # (BRONZE_REDDIT omitted — API pre-approval required Nov 2025. All code removed.)
    BRONZE_POLYMARKET, BRONZE_TELEGRAM,
    BRONZE_HACKERNEWS, BRONZE_NEWSAPI, BRONZE_ARXIV, BRONZE_FRED,
    BRONZE_GOOGLETRENDS, BRONZE_OPENWEATHER, BRONZE_OPENSKY,
    ALL_BRONZE_TOPICS,
    # Silver
    SILVER_SOCIAL_PULSE, SILVER_GLOBAL_NEWS, SILVER_STRUCTURED_METRICS,
    # Gold
    GOLD_SOCIAL_PULSE, GOLD_GLOBAL_NEWS, GOLD_STRUCTURED_METRICS,
    # System
    INGESTION_TRIGGERS, DEAD_LETTER_QUEUE,
    # Routing
    BRONZE_TO_SILVER_ROUTING,
)

from .settings import (
    # Paths
    BASE_DIR,
    DATA_DIR,
    # Infrastructure — Kafka
    KAFKA_BOOTSTRAP_SERVERS,
    # Infrastructure — PostgreSQL
    POSTGRES_HOST,
    POSTGRES_PORT,
    POSTGRES_USER,
    POSTGRES_PASSWORD,
    POSTGRES_DB,
    # AI & Enrichment
    OPENAI_API_KEY,
    OPENAI_MODEL_NAME,
    # Source credentials
    POLYMARKET_API_KEY,
    POLYMARKET_API_SECRET,
    # PREDICTIT_API_BASE_URL omitted — API permanently shut down (CFTC 2022-2024)
    TELEGRAM_API_ID,
    TELEGRAM_API_HASH,
    # REDDIT credentials removed — API pre-approval required (Nov 2025 policy)
    HACKERNEWS_API_BASE_URL,
    THE_NEWS_API_KEY,
    THE_NEWS_API_PAGE_SIZE,
    ARXIV_MAX_RESULTS,
    FRED_API_KEY,
    OPENWEATHER_API_KEY,
    OPENSKY_CLIENT_ID,
    OPENSKY_CLIENT_SECRET,
)
