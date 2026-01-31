"""
Telegram Producer
=================
Monitors Telegram channels for new messages using MTProto (Telethon)
and streams them to Kafka for analysis.

Source: Telegram public channels
Output: Kafka topic `telegram_raw_stream`
Strategy: Real-time message streaming with historical context.

Usage:
    python ingestion/telegram_producer.py
"""

# ==========================================
# IMPORTS
# ==========================================
import os
import sys
import json
import asyncio
import logging
import time
from datetime import datetime, timezone

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from kafka import KafkaProducer
from kafka.errors import KafkaError, NoBrokersAvailable

# ==========================================
# PATH SETUP
# ==========================================
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
    TOPIC_TELEGRAM,
    TELEGRAM_API_ID,
    TELEGRAM_API_HASH,
    BASE_DIR
)

TELEGRAM_TARGETS_PATH = os.path.join(BASE_DIR, "config", "telegram_targets.json")


# ==========================================
# DATA LOADING
# ==========================================
def load_target_channels() -> list[dict]:
    """
    Load target channels from JSON config file.
    
    Returns:
        List of dicts with 'name' and 'channel_username' keys.
    """
    try:
        with open(TELEGRAM_TARGETS_PATH, "r", encoding="utf-8") as f:
            targets = json.load(f)
            logger.info(f"Loaded {len(targets)} target channels from config.")
            return targets
    except FileNotFoundError:
        logger.error(f"Config file not found: {TELEGRAM_TARGETS_PATH}")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in config file: {e}")
        return []


# ==========================================
# MESSAGE FETCHING & STREAMING
# ==========================================
async def fetch_and_stream_messages(
    client: TelegramClient, 
    producer: KafkaProducer,
    channels: list[dict], 
    limit: int = 10
) -> tuple[int, int]:
    """
    Fetch messages from channels and stream to Kafka.
    
    Args:
        client: Authenticated TelegramClient.
        producer: Initialized KafkaProducer.
        channels: List of channel configs.
        limit: Messages per channel.
    
    Returns:
        Tuple of (success_count, error_count).
    """
    success_count = 0
    error_count = 0
    
    for channel_config in channels:
        channel_name = channel_config['name']
        channel_username = channel_config['channel_username']
        
        logger.info(f"\n📡 Fetching from: {channel_name} (@{channel_username})")
        
        try:
            # Resolve channel entity
            entity = await client.get_entity(channel_username)
            logger.info(f"   Channel ID: {entity.id}")
            
            # Fetch and stream messages
            async for message in client.iter_messages(entity, limit=limit):
                # Skip non-text messages
                if not message.text:
                    continue
                
                # Construct Kafka message
                msg_data = {
                    "source": "telegram",
                    "channel_name": channel_name,
                    "channel_username": channel_username,
                    "message_id": message.id,
                    "text": message.text,
                    "date": message.date.isoformat() if message.date else None,
                    "sender_id": message.sender_id,
                    "ingested_at": time.time()
                }
                
                try:
                    # Send to Kafka
                    producer.send(TOPIC_TELEGRAM, value=msg_data)
                    success_count += 1
                    logger.info(f"   ✅ Sent message [{message.id}] to Kafka")
                    
                except KafkaError as e:
                    logger.error(f"   ❌ Failed to send [{message.id}]: {e}")
                    error_count += 1
            
        except Exception as e:
            logger.error(f"   ❌ Error fetching from {channel_name}: {e}")
            error_count += 1
            continue
        
        # Rate limit protection between channels
        if len(channels) > 1:
            logger.info("   ⏳ Waiting 2 seconds before next channel...")
            time.sleep(2)
    
    return success_count, error_count


# ==========================================
# MAIN PRODUCER
# ==========================================
async def run_producer():
    """
    Main loop: Authenticates with Telegram, fetches messages, streams to Kafka.
    """
    logger.info("=" * 50)
    logger.info("📱 Telegram Producer - Starting")
    logger.info("=" * 50)
    
    # Validate Telegram credentials
    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        logger.error("TELEGRAM_API_ID or TELEGRAM_API_HASH not set in .env!")
        sys.exit(1)
    
    logger.info(f"Kafka Bootstrap Servers: {KAFKA_BOOTSTRAP_SERVERS}")
    logger.info(f"Target Topic: {TOPIC_TELEGRAM}")
    
    # Load targets
    channels = load_target_channels()
    if not channels:
        logger.error("No target channels configured. Exiting.")
        sys.exit(1)
    
    logger.info(f"Target Channels ({len(channels)}):")
    for ch in channels:
        logger.info(f"   - {ch['name']} (@{ch['channel_username']})")
    
    # Initialize Kafka Producer
    logger.info("-" * 50)
    logger.info("🚀 Connecting to Kafka...")
    logger.info(f"   Bootstrap Server: {KAFKA_BOOTSTRAP_SERVERS}")
    
    producer = None
    try:
        producer = KafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
            value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode('utf-8'),
            acks='all',  # Wait for all replicas to acknowledge
            retries=3,
            request_timeout_ms=10000
        )
        logger.info("✅ Kafka producer initialized.")
    except NoBrokersAvailable:
        logger.error("❌ Kafka not available - is Docker running?")
        sys.exit(1)
    except KafkaError as e:
        logger.error(f"❌ Kafka connection error: {e}")
        sys.exit(1)
    
    # Initialize Telegram Client
    session_file = os.path.join(BASE_DIR, "data", "userbot_session")
    logger.info("-" * 50)
    logger.info(f"📁 Session file: {session_file}.session")
    logger.info("🔐 Initializing Telegram client...")
    
    client = TelegramClient(
        session_file,
        int(TELEGRAM_API_ID),
        TELEGRAM_API_HASH
    )
    
    try:
        # Start Telegram client
        await client.start(
            phone=lambda: input("📞 Please enter your phone (e.g., +972...): ")
        )
        
        me = await client.get_me()
        logger.info("=" * 50)
        logger.info(f"✅ Logged in as: {me.first_name} {me.last_name or ''}")
        logger.info("=" * 50)
        
        # Fetch and stream messages
        logger.info("-" * 50)
        logger.info("📥 Fetching and streaming messages to Kafka...")
        
        success, errors = await fetch_and_stream_messages(
            client, producer, channels, limit=10
        )
        
        # Flush pending messages
        logger.info("-" * 50)
        logger.info("📤 Flushing Kafka producer...")
        producer.flush()
        
        # Summary
        logger.info("=" * 50)
        logger.info("🏁 Telegram Producer - Complete")
        logger.info(f"   ✅ Messages sent: {success}")
        logger.info(f"   ❌ Errors: {errors}")
        logger.info(f"   📝 Topic: {TOPIC_TELEGRAM}")
        logger.info("=" * 50)
        
    except SessionPasswordNeededError:
        logger.warning("⚠️ 2FA detected!")
        password = input("🔑 Enter your 2FA password: ")
        await client.sign_in(password=password)
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
        
    finally:
        # Cleanup
        if producer:
            producer.close()
            logger.info("✅ Kafka producer closed.")
        
        logger.info("🔌 Disconnecting from Telegram...")
        await client.disconnect()
        logger.info("✅ Disconnected successfully.")


# ==========================================
# ENTRY POINT
# ==========================================
if __name__ == "__main__":
    asyncio.run(run_producer())
