import os
from dotenv import load_dotenv

# ==========================================
# 1. Project Paths & Environment Loading
# ==========================================
# Calculate the base directory (points to 'data-pipeline' folder)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

# Create data directory if it doesn't exist
os.makedirs(DATA_DIR, exist_ok=True)

# Construct the absolute path to the .env file explicitly
dotenv_path = os.path.join(BASE_DIR, ".env")

# Load environment variables explicitly from that path
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path)
else:
    print(f"[WARNING] .env file not found at: {dotenv_path}")

# ==========================================
# 2. Infrastructure Configuration
# ==========================================
# Kafka server address - dynamic based on environment
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

# ==========================================
# 3. Kafka Topics (All Ingestion Targets)
# ==========================================
# Core pipelines
TOPIC_NEWS = "news_raw_stream"              # Target for: NewsAPI
TOPIC_COMMUNITY = "community_discourse_stream" # Target for: Reddit, Hacker News, YouTube
TOPIC_PROFESSIONAL = "professional_stream"  # Target for: ArXiv

# OSINT streams
TOPIC_WEATHER = "weather_raw_stream"        # Target for: OpenWeatherMap
TOPIC_TELEGRAM = "telegram_raw_stream"      # Target for: Telegram channels
TOPIC_FLIGHTS = "flights_raw_stream"        # Target for: Flight tracking APIs
TOPIC_TRENDS = "trends_raw_stream"          # Target for: Google Trends

# ==========================================
# 4. Data Source Credentials & Config
# ==========================================

# --- Source 1: NewsAPI (General News) ---
NEWS_API_KEY = os.getenv("NEWS_API_KEY")

# --- Source 2: Reddit (Community Discussions) ---
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_SECRET = os.getenv("REDDIT_SECRET")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "AnazaiScraper/1.0")

# --- Source 3: ArXiv (Academic/Professional) ---
ARXIV_MAX_RESULTS = int(os.getenv("ARXIV_MAX_RESULTS", 100))

# --- Source 4: Hacker News (Tech Community) ---
HACKER_NEWS_API_BASE_URL = os.getenv("HACKER_NEWS_API_BASE_URL", "https://hacker-news.firebaseio.com/v0")

# --- Source 5: YouTube Data API ---
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

# --- Source 6: OpenWeatherMap ---
WEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")

# --- Source 7: Telegram (Channel Monitoring via MTProto) ---
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")

# ==========================================
# 5. Processing & Enrichment (OpenAI)
# ==========================================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL_NAME = os.getenv("OPENAI_MODEL_NAME", "gpt-4o")