"""
Weather Forecast Producer
=========================
Fetches 5-day weather forecasts from OpenWeatherMap API for strategic locations
and streams them to a Kafka topic for historical analysis.

Source: OpenWeatherMap 5-day/3-hour Forecast API
Output: Kafka topic `weather_raw_stream`
Strategy: Append-only snapshots to track forecast evolution over time.

Usage:
    python ingestion/weather_producer.py
"""

# ==========================================
# IMPORTS
# ==========================================
import logging
import sys
import os
import json
import time
from datetime import datetime, timezone

import requests
from kafka import KafkaProducer
from kafka.errors import KafkaError, NoBrokersAvailable

# ==========================================
# PATH SETUP
# ==========================================
# Required to import from 'config' package when running as standalone script
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ==========================================
# LOGGING
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ==========================================
# CONFIGURATION
# ==========================================
from config import (
    KAFKA_BOOTSTRAP_SERVERS,
    TOPIC_WEATHER,
    WEATHER_API_KEY,
    BASE_DIR
)

WEATHER_TARGETS_PATH = os.path.join(BASE_DIR, "config", "weather_targets.json")

# API endpoints
GEOCODING_API_URL = "http://api.openweathermap.org/geo/1.0/direct"
FORECAST_API_URL = "http://api.openweathermap.org/data/2.5/forecast"


# ==========================================
# DATA LOADING
# ==========================================
def load_target_cities() -> list[str]:
    """
    Load target cities from JSON config file.
    
    Returns:
        List of city names in "City, CountryCode" format.
    """
    try:
        with open(WEATHER_TARGETS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            targets = data.get("targets", [])
            logger.info(f"Loaded {len(targets)} target cities from config.")
            return targets
    except FileNotFoundError:
        logger.error(f"Config file not found: {WEATHER_TARGETS_PATH}")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in config file: {e}")
        return []


# ==========================================
# API FUNCTIONS
# ==========================================
def get_coordinates(city_name: str) -> dict | None:
    """
    Convert city name to lat/lon using OpenWeatherMap Geocoding API.
    
    Args:
        city_name: City in "City, CountryCode" format (e.g., "Tehran, IR").
    
    Returns:
        Dict with 'lat', 'lon', 'name', 'country' keys, or None on failure.
    """
    params = {
        "q": city_name,
        "limit": 1,
        "appid": WEATHER_API_KEY
    }
    
    try:
        response = requests.get(GEOCODING_API_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if not data:
            logger.error(f"City not found: '{city_name}' - API returned empty result.")
            return None
        
        location = data[0]
        return {
            "lat": location.get("lat"),
            "lon": location.get("lon"),
            "name": location.get("name"),
            "country": location.get("country")
        }
        
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error for '{city_name}': {e.response.status_code}")
        return None
    except requests.exceptions.ConnectionError:
        logger.error(f"Connection error while geocoding '{city_name}'.")
        return None
    except requests.exceptions.Timeout:
        logger.error(f"Timeout while geocoding '{city_name}'.")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error for '{city_name}': {e}")
        return None
    except (KeyError, IndexError, TypeError) as e:
        logger.error(f"Unexpected response format for '{city_name}': {e}")
        return None


def fetch_weather_forecast(lat: float, lon: float) -> dict | None:
    """
    Fetch 5-day/3-hour weather forecast from OpenWeatherMap.
    
    Args:
        lat: Latitude of the location.
        lon: Longitude of the location.
    
    Returns:
        Full API response dict, or None on failure.
    """
    params = {
        "lat": lat,
        "lon": lon,
        "appid": WEATHER_API_KEY,
        "units": "metric"  # Return temperatures in Celsius
    }
    
    try:
        response = requests.get(FORECAST_API_URL, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        if "list" not in data or not data["list"]:
            logger.error(f"No forecast data returned for ({lat}, {lon}).")
            return None
        
        return data
        
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error fetching forecast: {e.response.status_code}")
        return None
    except requests.exceptions.ConnectionError:
        logger.error(f"Connection error fetching forecast for ({lat}, {lon}).")
        return None
    except requests.exceptions.Timeout:
        logger.error(f"Timeout fetching forecast for ({lat}, {lon}).")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error fetching forecast: {e}")
        return None
    except (KeyError, TypeError, ValueError) as e:
        logger.error(f"Unexpected response format: {e}")
        return None


# ==========================================
# MAIN PRODUCER
# ==========================================
def run_producer():
    """
    Main loop: Fetches weather for all configured cities and streams to Kafka.
    
    Workflow per city:
        1. Geocode city name → lat/lon
        2. Fetch weather forecast
        3. Construct message with snapshot timestamp
        4. Send to Kafka topic
    """
    logger.info("=" * 50)
    logger.info("🌤️  Weather Forecast Producer - Starting")
    logger.info("=" * 50)
    
    # Validate required configuration
    if not WEATHER_API_KEY:
        logger.error("WEATHER_API_KEY is not set in .env file!")
        sys.exit(1)
    
    logger.info(f"Kafka Bootstrap Servers: {KAFKA_BOOTSTRAP_SERVERS}")
    logger.info(f"Target Topic: {TOPIC_WEATHER}")
    
    cities = load_target_cities()
    if not cities:
        logger.error("No target cities configured. Exiting.")
        sys.exit(1)
    
    logger.info(f"Target Cities ({len(cities)}): {cities}")
    
    # Initialize Kafka Producer
    logger.info("-" * 50)
    logger.info("🚀 Connecting to Kafka...")
    
    try:
        producer = KafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            key_serializer=lambda k: k.encode('utf-8') if k else None,
            retries=3,
            request_timeout_ms=10000
        )
        logger.info("✅ Kafka producer initialized successfully.")
    except NoBrokersAvailable:
        logger.error("❌ Kafka not available - is Docker running?")
        sys.exit(1)
    except KafkaError as e:
        logger.error(f"❌ Kafka connection error: {e}")
        sys.exit(1)
    
    # Process cities
    logger.info("-" * 50)
    logger.info("🌍 Processing all target cities...")
    
    success_count = 0
    error_count = 0
    
    for city in cities:
        logger.info(f"\n📍 Processing: {city}")
        
        coords = get_coordinates(city)
        if not coords:
            logger.warning(f"   ⚠️ Skipping {city} - geocoding failed.")
            error_count += 1
            continue
        
        forecast_data = fetch_weather_forecast(coords['lat'], coords['lon'])
        if not forecast_data:
            logger.warning(f"   ⚠️ Skipping {city} - forecast fetch failed.")
            error_count += 1
            continue
        
        # Construct message with UTC timestamp for consistent time tracking
        message = {
            "source": "OpenWeatherMap",
            "city": city,
            "coordinates": {
                "lat": coords['lat'],
                "lon": coords['lon']
            },
            "snapshot_timestamp": datetime.now(timezone.utc).isoformat(),
            "forecast_data": forecast_data.get('list', [])
        }
        
        try:
            # Use city as key for partition locality
            producer.send(TOPIC_WEATHER, key=city, value=message)
            success_count += 1
            
            entry_count = len(message['forecast_data'])
            logger.info(f"   ✅ Sent forecast for '{city}' ({entry_count} entries)")
            
        except KafkaError as e:
            logger.error(f"   ❌ Failed to send message for {city}: {e}")
            error_count += 1
        
        # Respect API rate limits
        time.sleep(1)
    
    # Finalize
    logger.info("-" * 50)
    logger.info("📤 Flushing Kafka producer...")
    producer.flush()
    producer.close()
    
    # Summary
    logger.info("=" * 50)
    logger.info("🏁 Weather Producer - Complete")
    logger.info(f"   ✅ Success: {success_count} cities")
    logger.info(f"   ❌ Errors: {error_count} cities")
    logger.info(f"   📝 Topic: {TOPIC_WEATHER}")
    logger.info("=" * 50)


# ==========================================
# ENTRY POINT
# ==========================================
if __name__ == "__main__":
    run_producer()
