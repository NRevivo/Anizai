import time
import json
import requests
from kafka import KafkaProducer
from kafka.errors import KafkaError
import sys
import os

# Add the parent directory (data-pipeline) to sys.path so we can import 'config'
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import configuration and schema mappers from the centralized config package
from config import (
    KAFKA_BOOTSTRAP_SERVERS,
    TOPIC_NEWS,           # Maps to 'news_raw_stream'
    NEWS_API_KEY,
    map_news_to_unified   # The function that converts raw data to the unified schema
)

# ==========================================
# CATEGORY CONFIGURATION
# ==========================================
# List of categories to track via NewsAPI.
# NOTE: NewsAPI supports the following categories:
#       business, entertainment, general, health, science, sports, technology.
# TODO: Update this list in the future if you want to filter specific topics.
CATEGORIES = [
    "technology",
    "science",
    "business",
    "health",
    "sports",
    "general"
]

def fetch_news(category):
    """
    Fetches top headlines from NewsAPI for a specific category.
    """
    url = "https://newsapi.org/v2/top-headlines"
    params = {
        "category": category,
        "language": "en",
        "apiKey": NEWS_API_KEY,
        "pageSize": 10  # Limiting batch size to manage API rate limits
    }
    
    try:
        response = requests.get(url, params=params)
        response.raise_for_status() # Raise an exception for HTTP errors (4xx, 5xx)
        return response.json().get("articles", [])
    except Exception as e:
        print(f"[ERROR] Failed to fetch news for category '{category}': {e}")
        return []

def run_producer():
    """
    Main loop: Fetches news continuously and publishes to Kafka using the unified schema.
    """
    print("🚀 Connecting to Kafka...")
    
    # Initialize Kafka Producer
    # Retries are enabled to handle temporary connection glitches
    producer = KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda v: json.dumps(v).encode('utf-8'),
        key_serializer=lambda k: k.encode('utf-8'),
        retries=3
    )

    print(f"🎯 Target Topic: {TOPIC_NEWS}")
    print(f"📋 Tracking Categories: {CATEGORIES}")
    print("Starting continuous stream...")

    try:
        # Infinite loop to simulate a continuous data stream
        while True:
            total_sent = 0
            
            for category in CATEGORIES:
                print(f"\n--- Fetching Category: {category} ---")
                articles = fetch_news(category)
                
                if not articles:
                    print(f"No new articles found for {category}.")
                    continue

                for article in articles:
                    # Transform raw data to Unified Schema
                    # This adds fields like 'event_id', 'stream_type', and 'source'
                    kafka_message = map_news_to_unified(article, category)
                    
                    # Send to Kafka
                    # We use the category as the Key to ensure partition locality
                    producer.send(
                        TOPIC_NEWS,
                        key=category,
                        value=kafka_message
                    )
                    
                    # Log snippet for verification
                    print(f"✅ Sent: {kafka_message['text'][:50]}... (Source: {kafka_message['metadata']['source_name']})")
                    total_sent += 1
                    
                    # Small delay between messages to prevent local congestion
                    time.sleep(0.1)

            # Ensure all messages are sent before sleeping
            producer.flush()
            print(f"\n📊 Batch complete. Total articles sent: {total_sent}")
            
            # API Rate Limit Management
            # NewsAPI Developer plan is limited. We sleep for 15 minutes between batches.
            print("zzZ Sleeping for 15 minutes before next batch...")
            time.sleep(900)

    except KeyboardInterrupt:
        print("\n🛑 Stopping producer (User Interrupt)...")
    except Exception as e:
        print(f"❌ Critical Error: {e}")
    finally:
        producer.close()
        print("Producer closed successfully.")

if __name__ == "__main__":
    run_producer()