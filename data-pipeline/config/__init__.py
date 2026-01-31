"""
This package initializer exposes key configuration variables and schema mappers.
It allows for cleaner imports throughout the project (e.g., 'from config import TOPIC_NEWS').
"""

# 1. Expose settings and constants (from settings.py)
from .settings import (
    # Infrastructure
    KAFKA_BOOTSTRAP_SERVERS,
    
    # Topics (Core)
    TOPIC_NEWS,
    TOPIC_COMMUNITY,
    TOPIC_PROFESSIONAL,
    # Topics (OSINT)
    TOPIC_WEATHER,
    TOPIC_TELEGRAM,
    TOPIC_FLIGHTS,
    TOPIC_TRENDS,
    
    # Credentials & API Keys
    NEWS_API_KEY,
    REDDIT_CLIENT_ID,
    REDDIT_SECRET,
    REDDIT_USER_AGENT,
    ARXIV_MAX_RESULTS,
    HACKER_NEWS_API_BASE_URL,
    YOUTUBE_API_KEY,
    OPENWEATHER_API_KEY,
    OPENAI_API_KEY,
    OPENAI_MODEL_NAME,
    
    # Paths
    BASE_DIR,
    DATA_DIR
)

# 2. Expose schema mapping functions (from schemas.py)
from .schemas import (
    create_unified_message,
    map_news_to_unified,
    map_reddit_to_unified,
    map_arxiv_to_unified,
    map_hacker_news_to_unified,
    map_youtube_to_unified
)