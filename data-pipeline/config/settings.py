import os
from dotenv import load_dotenv

# ==========================================
# 1. Project Paths & Environment Loading
# ==========================================
# Calculate the base directory (points to 'data-pipeline' folder)
# We go up two levels: config/settings.py -> config -> data-pipeline
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

# Construct the absolute path to the .env file explicitly
# This ensures we find the file regardless of where the script is run from
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
# 3. Kafka Topics (The Ingestion Targets)
# ==========================================
# The 3 main pipelines (Producers will send data here)
TOPIC_NEWS = "news_raw_stream"              # Target for: NewsAPI
TOPIC_COMMUNITY = "community_discourse_stream" # Target for: Reddit, Hacker News, YouTube
TOPIC_PROFESSIONAL = "professional_stream"  # Target for: ArXiv

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
# Configuration for search limits (No private key usually required)
ARXIV_MAX_RESULTS = int(os.getenv("ARXIV_MAX_RESULTS", 100))

# --- Source 4: Hacker News (Tech Community) ---
# Hacker News API is usually public, but we define the base URL here
HACKER_NEWS_API_BASE_URL = os.getenv("HACKER_NEWS_API_BASE_URL", "https://hacker-news.firebaseio.com/v0")

# --- Source 5: YouTube Data API (Video Transcripts/Comments) ---
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

# ==========================================
# 5. Processing & Enrichment (OpenAI)
# ==========================================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL_NAME = os.getenv("OPENAI_MODEL_NAME", "gpt-4o")