"""
Settings — Environment variables, credentials, and project paths.

Loads all API keys and infrastructure config from .env file.
Each source's credentials are grouped by their Section reference
in data_contracts_and_sources.md.

Why .env-driven: Ensures moving from local Docker to GCP is a
configuration change only — no code changes required (Section 9.1).

References:
    - Section 2.1: Producer Matrix (all 11 source credentials)
    - Section 8.1: Docker infrastructure config
    - Section 9.1: Environment parity (.env-driven config)
"""

import os

from dotenv import load_dotenv

# ==========================================================
# 1. Project Paths
# ==========================================================
# BASE_DIR points to the 'data-pipeline/' root directory.
# All paths in code, Docker configs, and .env files must be
# relative to this root (Section 3.6).
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

os.makedirs(DATA_DIR, exist_ok=True)

# ==========================================================
# 2. Load Environment Variables
# ==========================================================
# Primary location: data-pipeline/.env
# Fallback location: data-pipeline/infrastructure/.env
# (allows the infrastructure .env to serve as the project .env
# without requiring a manual copy step during local development)
_dotenv_path = os.path.join(BASE_DIR, ".env")
_dotenv_infra_path = os.path.join(BASE_DIR, "infrastructure", ".env")
if os.path.exists(_dotenv_path):
    load_dotenv(_dotenv_path)
elif os.path.exists(_dotenv_infra_path):
    load_dotenv(_dotenv_infra_path)
else:
    print(f"[WARNING] .env file not found at: {_dotenv_path} or {_dotenv_infra_path}")

# ==========================================================
# 3. Infrastructure — Kafka (Section 8.2)
# ==========================================================
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

# ==========================================================
# 4. Infrastructure — PostgreSQL (Section 5)
# ==========================================================
# Unified PostgreSQL engine with pgvector + TimescaleDB extensions.
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_USER = os.getenv("POSTGRES_USER", "anizai")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")
POSTGRES_DB = os.getenv("POSTGRES_DB", "anizai")

# ==========================================================
# 5. AI & Enrichment — OpenAI (Section 4.2)
# ==========================================================
# Used by Flink Gold Job for Cognitive Metadata Extraction,
# Consensus Bundling (GPT-4o), and real-time translation.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL_NAME = os.getenv("OPENAI_MODEL_NAME", "gpt-4o")

# ==========================================================
# 6. Source Credentials — Grouped by Producer
# ==========================================================

# --- Polymarket (Section B.8) ---
# WebSocket / REST — real-time market odds and discussions.
POLYMARKET_API_KEY = os.getenv("POLYMARKET_API_KEY", "")
POLYMARKET_API_SECRET = os.getenv("POLYMARKET_API_SECRET", "")

# --- PredictIt — API permanently shut down (CFTC 2022-2024) ---
# Constant retained so existing imports in test archives do not break.
# No producer or pipeline is active. Value is irrelevant at runtime.
PREDICTIT_API_BASE_URL = "https://www.predictit.org/api"  # shut down

# --- Telegram (Section A.1) ---
# MTProto streaming via Telethon. Requires Telegram developer credentials.
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID", "")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")

# --- Reddit — API pre-approval required (Nov 2025 policy). All code removed. ---
# REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT removed.

# --- Hacker News (Section B.2) ---
# Algolia public API — no key required.
HACKERNEWS_API_BASE_URL = os.getenv(
    "HACKERNEWS_API_BASE_URL",
    "https://hn.algolia.com/api/v1"
)

# --- newsapi.ai / Event Registry (Section B.4) ---
# REST API — eventregistry.org (replaced thenewsapi.com in Phase 7A).
# Why migration: TheNewsAPI's snippet field is 60 chars max; newsapi.ai returns
# full article body (articleBodyLen=-1), which is what Phase 7B's semantic rescue
# and the hub's full_text_raw RAG drill-down both require.
# Auth: apiKey (query param). Source filter: sourceUri (csv of domains).
# Validated category URIs (T7A.2): news/Business, news/Technology, news/Health,
# news/Science, news/Politics. "news" root returns 0 results — not used.
NEWSAI_API_KEY  = os.getenv("NEWSAI_API_KEY", "")
NEWSAI_BASE_URL = "https://eventregistry.org/api/v1"

# Articles per request. Default 10 (free tier tolerates this; adjust in prod).
NEWSAI_PAGE_SIZE = int(os.getenv("NEWSAI_PAGE_SIZE", "10"))


# --- ArXiv (Section B.1) ---
# Public REST API — no key required. Max results per query.
ARXIV_MAX_RESULTS = int(os.getenv("ARXIV_MAX_RESULTS", "200"))

# --- FRED (Section B.3) ---
# Federal Reserve Economic Data — requires API key from FRED.
FRED_API_KEY = os.getenv("FRED_API_KEY", "")

# --- Google Trends (Section B.5) ---
# Pytrends — no API key required. Uses Google cookies internally.
# No credentials needed.

# --- OpenWeather (Section B.6) ---
# REST API — requires API key from OpenWeatherMap.
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")

# --- OpenSky (Section B.7) ---
# OAuth2 client credentials — replaced Basic Auth (March 2026).
# Authenticated tier: 4,000 calls/day. Anonymous tier: ~100 calls/day.
# Register at https://opensky-network.org/
OPENSKY_CLIENT_ID = os.getenv("OPENSKY_CLIENT_ID", "")
OPENSKY_CLIENT_SECRET = os.getenv("OPENSKY_CLIENT_SECRET", "")
