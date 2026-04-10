"""
Telegram Producer — MTProto Streaming, real-time (Section A.1).

Monitors 7 pre-vetted Telegram channels via Telethon (MTProto protocol).
Each incoming message is treated as a micro-article and emitted individually
to the Bronze Kafka topic. Media-only messages (images, videos, stickers, or
polls with no text caption) are dropped at the producer boundary — before any
Bronze write — because the Knowledge Vault requires non-empty full_text_raw.

Session File:
    Telethon persists MTProto authentication state to SESSION_PATH + '.session'
    (data-pipeline/anizai_telegram.session). This file is excluded from git via
    .gitignore (*.session). It must never be committed — it contains account
    session tokens tied to the authenticated Telegram account.

    First-run note: Telethon will prompt for a phone number and a verification
    code the first time main() is called. After that one-time step, the session
    file enables silent re-authentication on every subsequent run.

Why no authority whitelist (Section A.1):
    All 7 channels are manually curated high-signal sources covering geopolitical,
    military, and financial events. There is no fragmentation problem (as there is
    for NewsAPI across many publishers) — the whitelist IS the channel list.

Why no Impact Boost (Section B.4 / B.1):
    Impact Boost (+1 to impact_level) is reserved for Israeli/Middle East
    security and energy articles from live news feeds (Section B.4). Telegram
    messages receive their impact_level purely from the Gold Job's Cognitive
    Metadata Extraction prompt (Section 4.2A).

Silver store:  knowledge_vault (direct signal, not community discourse).
Vector target: knowledge_vectors (C.5, entry_type='direct_message').
Silver topic:  process.silver.global_news (SILVER_GLOBAL_NEWS).
    — Telegram is treated as a micro-article, not social discourse.
      This overrides the legacy BRONZE_TO_SILVER_ROUTING entry in
      kafka_topics.py which maps BRONZE_TELEGRAM → SILVER_SOCIAL_PULSE.
      The Silver Job's process_telegram_message() routes explicitly to
      SILVER_GLOBAL_NEWS (Task 4B.2). The routing dict is not used
      by the Silver Job for source-specific routing decisions.

Partition key: channel_username — all messages from the same channel land on
    the same Kafka partition, preserving per-channel arrival order for the
    Silver Job's SHA-256 deduplication logic (Section 4.1C).

Kafka Target: ingest.bronze.telegram (BRONZE_TELEGRAM)

References:
    - Section A.1:  Telegram Channel Registry (7 channels + ingestion method)
    - Section C.1:  Bronze Schema (build_bronze_message wraps every payload)
    - Section C.3:  Silver Social Discourse Store — Telegram News-Centric pattern
    - Section C.5:  Gold Global Signal — domain_context.Telegram extension
                    (is_breaking_alert, forwarded_from, extracted_links)
    - Section 2.1:  Producer Matrix (Telegram row — MTProto, real-time)
    - Section 3.3:  Service Isolation — no transformation, no DB writes here
    - Section 3.4:  NDJSON serialisation (handled by kafka_utils.py)
    - Section 3.6:  Working Directory Constraint — session saved inside data-pipeline/
    - Section 4.1C: SHA-256 deduplication — message_url is the canonical dedup key
    - Section 9.1:  Environment parity — credentials in .env (TELEGRAM_API_ID/HASH)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional

from telethon import TelegramClient, events
from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl

from config.kafka_topics import BRONZE_TELEGRAM
from config.settings import BASE_DIR, TELEGRAM_API_HASH, TELEGRAM_API_ID
from utils.kafka_utils import build_bronze_message, make_producer

logger = logging.getLogger(__name__)


# ==========================================================
# Session & Source Constants
# ==========================================================

# Session file lives inside data-pipeline/ (Section 3.6).
# Telethon appends '.session' automatically — path must be WITHOUT the extension.
# Result: data-pipeline/anizai_telegram.session (excluded by .gitignore: *.session).
SESSION_PATH = os.path.join(BASE_DIR, "anizai_telegram")

SOURCE_NAME = "telegram"


# ==========================================================
# Channel Registry (Section A.1 — 7 pre-vetted channels)
# ==========================================================
# Stored as a list of dicts so both the usernames (for Telethon subscription)
# and the human-readable titles (for raw_payload) are available without a
# separate lookup or API call during message handling.

CHANNELS: list[dict[str, str]] = [
    {"username": "abualiexpress",   "title": "Abu Ali Express"},
    {"username": "yediotnews25",  "title": "100Field News"},
    {"username": "Faytuks_Network", "title": "Faytuks Network"},
    {"username": "ClashReport",     "title": "Clash Report"},
    {"username": "FinancialJuice",  "title": "FinancialJuice"},
    {"username": "disclosetv",      "title": "Disclose.tv"},
    {"username": "intelslava",      "title": "Intel Slava Z"},
]

# Flat list passed to events.NewMessage(chats=...) for Telethon subscription.
CHANNEL_USERNAMES: list[str] = [ch["username"] for ch in CHANNELS]

# Case-insensitive lookup: username.lower() → canonical title.
# Used in _build_raw_payload() to map Telegram's username back to a title
# without fetching the chat entity on every message.
_USERNAME_TO_TITLE: dict[str, str] = {
    ch["username"].lower(): ch["title"] for ch in CHANNELS
}

# Canonical casing lookup: username.lower() → username as registered in CHANNELS.
# Telegram usernames are case-insensitive; this restores our canonical casing
# (e.g. "Faytuks_Network") when Telegram returns "faytuks_network".
_USERNAME_CANONICAL: dict[str, str] = {
    ch["username"].lower(): ch["username"] for ch in CHANNELS
}


# ==========================================================
# Helpers
# ==========================================================

def _extract_links(message) -> list[str]:
    """
    Extract all hyperlinks from a Telegram message's entity list.

    Why entities (not regex on text): Telegram pre-parses links server-side
    into typed entity objects. MessageEntityUrl covers bare URLs typed
    directly in the message; MessageEntityTextUrl covers hyperlinked text
    (display text + separate URL attribute). Using entities is more reliable
    than a client-side regex which would miss URL-shortened or hyperlinked
    forms.

    The extracted_links list is stored verbatim in raw_payload and forwarded
    to the Silver/Gold layers as part of the Telegram domain_context extension
    (Section C.5: 'extracted_links').

    Args:
        message: Telethon Message object.

    Returns:
        List of URL strings (may be empty). No deduplication — all entity
        URLs are preserved for downstream analysis.
    """
    links: list[str] = []
    if not message.entities:
        return links

    for entity in message.entities:
        if isinstance(entity, MessageEntityUrl):
            # Bare URL: the URL text lives at [offset:offset+length] in message.text
            url = message.text[entity.offset: entity.offset + entity.length]
            links.append(url)
        elif isinstance(entity, MessageEntityTextUrl):
            # Hyperlinked text: the actual URL is the entity's .url attribute
            if entity.url:
                links.append(entity.url)

    return links


def _get_forwarded_from(message) -> Optional[str]:
    """
    Return the original sender name if the message was forwarded; None otherwise.

    Why fwd_from.from_name (not peer_id): from_name is always a plain string
    (the channel or user display name as Telegram resolves it). Resolving the
    peer_id to a full entity requires an async API call, which would add latency
    to every forwarded message. The display name is sufficient for domain_context
    (Section C.5: 'forwarded_from' field — a string tag, not a relational key).

    Args:
        message: Telethon Message object.

    Returns:
        Display name of the original sender, or None if not forwarded.
    """
    if not message.fwd_from:
        return None
    fwd = message.fwd_from
    if hasattr(fwd, "from_name") and fwd.from_name:
        return fwd.from_name
    # from_name can be None for private forwards where the sender is hidden.
    return "private"


async def _resolve_channel_username(event) -> Optional[str]:
    """
    Resolve the canonical channel username from a Telethon NewMessage event.

    Why await get_chat() instead of accessing event.chat directly:
        event.chat is not guaranteed to be populated synchronously for all
        event types. get_chat() uses the entity cache when available (no
        network round-trip for subscribed channels) and falls back to a
        lightweight MTProto resolution otherwise. This avoids AttributeError
        on event.chat for the small subset of events where the entity is not
        in cache at handler entry time.

    Normalization: Telegram usernames are case-insensitive. We normalize to
        the canonical casing defined in CHANNELS (e.g. "Faytuks_Network")
        so the raw_payload username always matches the registry.

    Args:
        event: Telethon NewMessage event.

    Returns:
        Canonical username string (from CHANNELS), or None if resolution fails.
    """
    try:
        chat = await event.get_chat()
        raw_username: Optional[str] = getattr(chat, "username", None)
        if not raw_username:
            return None
        return _USERNAME_CANONICAL.get(raw_username.lower(), raw_username)
    except Exception as exc:
        logger.warning("[telegram] _resolve_channel_username failed: %s", exc)
        return None


# ==========================================================
# Producer
# ==========================================================

class TelegramProducer:
    """
    Real-time MTProto streaming producer for 7 pre-vetted Telegram channels.

    Uses Telethon's event-driven NewMessage handler — no polling loop.
    Each qualifying message (non-empty text) is immediately serialised into
    a Bronze envelope and emitted to BRONZE_TELEGRAM.

    Lifecycle:
        1. __init__  — validates credentials, builds Kafka producer + Telethon client.
        2. run()     — authenticates, registers handler, blocks until disconnected.
        3. close()   — flushes and closes the Kafka producer (Telethon disconnects
                       via asyncio.run() cancellation in main()).
    """

    def __init__(self) -> None:
        if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
            raise EnvironmentError(
                "[telegram] TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env. "
                "Credentials are tied to your personal Telegram account (Section A.1, 9.1)."
            )

        self._kafka = make_producer()

        # TelegramClient takes the session file path (without .session extension)
        # and the integer API ID. TELEGRAM_API_ID is loaded as a string from os.getenv.
        self._client = TelegramClient(
            SESSION_PATH,
            int(TELEGRAM_API_ID),
            TELEGRAM_API_HASH,
        )

        self._emitted = 0

    # ----------------------------------------------------------
    # Payload Builder
    # ----------------------------------------------------------

    @staticmethod
    def _build_raw_payload(message, channel_username: str) -> dict:
        """
        Construct the raw_payload dict for one Telegram message.

        All fields are stored verbatim (Bronze layer is immutable, Section 2.3).
        The Silver Job's map_telegram_message_to_silver() reads these fields
        directly — field names here are the canonical interface between Bronze
        and Silver for Telegram (Task 4B.2).

        Field design rationale:
            message_url  — canonical dedup key for hash_document() in the Silver
                           Job (Section 4.1C). Format: https://t.me/{username}/{id}.
                           Stable and unique per message; equivalent to the
                           'canonical_url' field in arxiv_producer._build_raw_payload().

            message_text — stored as-is (no whitespace normalisation at Bronze).
                           The Silver Job applies cleaning before storing to
                           knowledge_vault.full_text_raw.

            has_media    — True when the message has BOTH text and media (e.g. a
                           photo with a caption). Media-only messages (no text)
                           are filtered before this function is called, so
                           has_media=True here means "rich message, text preserved."

            views        — may be None for channels where Telethon cannot read
                           view counts (permissions vary by channel configuration).

        Args:
            message:          Telethon Message object (text already confirmed non-empty).
            channel_username: Canonical username from CHANNELS (e.g. 'clashreport').

        Returns:
            raw_payload dict for build_bronze_message().
        """
        extracted_links  = _extract_links(message)
        forwarded_from   = _get_forwarded_from(message)

        # Canonical message URL — used as dedup key in Silver Job (Section 4.1C)
        message_url = f"https://t.me/{channel_username}/{message.id}"

        # Telethon returns message.date as a UTC datetime; ensure tz-aware isoformat.
        if message.date:
            msg_dt = (
                message.date.replace(tzinfo=timezone.utc)
                if message.date.tzinfo is None
                else message.date
            )
            message_date = msg_dt.isoformat()
        else:
            message_date = datetime.now(timezone.utc).isoformat()

        return {
            # --- Core message fields (Section C.3 Telegram News-Centric pattern) ---
            "message_id":       message.id,
            "channel_username": channel_username,
            "channel_title":    _USERNAME_TO_TITLE.get(channel_username.lower(), channel_username),
            "message_text":     message.text,
            "message_date":     message_date,
            "message_url":      message_url,   # canonical dedup key (Section 4.1C)
            # --- Telegram domain_context fields (Section C.5) ---
            "extracted_links":  extracted_links,
            "is_forwarded":     message.fwd_from is not None,
            "forwarded_from":   forwarded_from,
            # --- Supplementary context ---
            "views":            getattr(message, "views", None),
            "has_media":        message.media is not None,  # True = text + media
        }

    # ----------------------------------------------------------
    # Kafka Emission
    # ----------------------------------------------------------

    def _emit(self, raw_payload: dict, channel_username: str) -> None:
        """
        Wrap raw_payload in a Bronze envelope and publish to BRONZE_TELEGRAM.

        Why http_status_code=0: MTProto is not HTTP. Using 0 signals
            "not applicable" rather than a misleading 200. The Bronze
            schema accepts any integer in this field (Section C.1).

        Why request_duration_ms=0: Telethon delivers messages via a
            persistent push connection — there is no discrete request/response
            cycle to time. 0 is the correct sentinel for event-driven sources.

        Partition key = channel_username: All messages from the same channel
            land on the same Kafka partition. This preserves per-channel
            arrival order for the Silver Job, which is important when the
            same article is re-broadcast across multiple channels (dedup
            relies on message_url hash, not message ordering, but consistent
            partitioning makes replay audits simpler).

        Args:
            raw_payload:      Dict from _build_raw_payload().
            channel_username: Canonical channel username (Kafka partition key).
        """
        source_endpoint = f"https://t.me/{channel_username}"

        msg = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=source_endpoint,
            raw_payload=raw_payload,
            http_status_code=0,       # MTProto — no HTTP request/response
            request_duration_ms=0,    # push event — no request duration
        )

        self._kafka.send(BRONZE_TELEGRAM, value=msg, key=channel_username)
        self._emitted += 1

        logger.info(
            "[telegram] Emitted @%s message_id=%s  links=%d  forwarded=%s  bronze_total=%d",
            channel_username,
            raw_payload["message_id"],
            len(raw_payload["extracted_links"]),
            raw_payload["is_forwarded"],
            self._emitted,
        )

    # ----------------------------------------------------------
    # Event Handler
    # ----------------------------------------------------------

    async def _handle_new_message(self, event) -> None:
        """
        Telethon NewMessage event handler — entry point for every incoming message.

        Media-only filter (Section A.1):
            Messages where message.text is None, empty, or whitespace-only are
            dropped here. This covers: photos/videos with no caption, stickers,
            polls without a question body, voice messages, and any other media-only
            type. The filter runs BEFORE any raw_payload construction or Kafka write
            to keep the Bronze topic signal-dense.

            Why at producer level (not Silver):
                Section A.1 explicitly states "Ignore messages containing only media
                without text." Filtering at the boundary prevents unnecessary Bronze
                Kafka writes, reduces 7-day Bronze retention volume, and keeps the
                Knowledge Vault free of empty-text rows that would fail the
                full_text_raw NOT NULL constraint.

        Error isolation:
            Individual message failures are caught and logged without crashing the
            handler loop. A single malformed or permission-denied message must not
            disconnect the producer from all 7 channels.

        Args:
            event: Telethon NewMessage event from a subscribed channel.
        """
        try:
            message = event.message

            # --- Media-only filter (Section A.1) ---
            if not message.text or not message.text.strip():
                logger.debug(
                    "[telegram] Dropped media-only message_id=%s (no text content)",
                    getattr(message, "id", "?"),
                )
                return

            # Resolve canonical channel username from the event's chat entity
            channel_username = await _resolve_channel_username(event)
            if not channel_username:
                logger.warning(
                    "[telegram] Could not resolve channel for message_id=%s — skipping",
                    message.id,
                )
                return

            raw_payload = self._build_raw_payload(message, channel_username)
            self._emit(raw_payload, channel_username)

        except Exception as exc:
            logger.error(
                "[telegram] Unhandled error in _handle_new_message: %s — skipping message",
                exc,
                exc_info=True,
            )

    # ----------------------------------------------------------
    # Run Loop
    # ----------------------------------------------------------

    async def run(self) -> None:
        """
        Authenticate, register the event handler, and stream indefinitely.

        First-run authentication:
            Telethon will prompt for:
                1. Your Telegram phone number (international format, e.g. +15551234567)
                2. The verification code sent to your Telegram app (or SMS)
            After this one-time step, the session is persisted to SESSION_PATH.session
            and subsequent runs authenticate silently.

        Channel subscription:
            events.NewMessage(chats=CHANNEL_USERNAMES) subscribes to all 7 channels
            in a single registration. Telethon resolves usernames to peer IDs on
            connection and uses them for efficient server-side filtering — only
            messages from the subscribed channels trigger the handler.

        Blocking:
            run_until_disconnected() yields control back to the asyncio event loop.
            The producer stays alive until the Telethon session is disconnected
            (e.g. keyboard interrupt in main(), server-side disconnect, or network
            failure that exhausts Telethon's built-in reconnect retries).
        """
        await self._client.start()

        self._client.add_event_handler(
            self._handle_new_message,
            events.NewMessage(chats=CHANNEL_USERNAMES),
        )

        logger.info(
            "[telegram] Producer connected. Streaming from %d channels: %s",
            len(CHANNEL_USERNAMES),
            "  ".join(f"@{u}" for u in CHANNEL_USERNAMES),
        )

        await self._client.run_until_disconnected()

    # ----------------------------------------------------------
    # Shutdown
    # ----------------------------------------------------------

    def close(self) -> None:
        """
        Flush the Kafka producer and close the connection cleanly.

        The Telethon client is disconnected by asyncio.run() in main() when the
        event loop is cancelled (KeyboardInterrupt). This method handles only the
        Kafka side: flush() drains the in-memory send buffer synchronously before
        exit, preventing silent message loss (same pattern as arxiv_producer.py).
        """
        self._kafka.flush()
        self._kafka.close()
        logger.info(
            "[telegram] Kafka producer flushed and closed. Total messages emitted: %d",
            self._emitted,
        )


# ==========================================================
# Docker / Airflow Entry Point
# ==========================================================

def main() -> None:
    """
    Entry point for Docker container invocation (Section 8.2).

    E2E Runbook Note (Task 4B.9):
        First run will prompt for phone number + Telegram verification code.
        This is a one-time MTProto authentication step. After authentication,
        the session is saved to data-pipeline/anizai_telegram.session and
        all subsequent runs authenticate silently.

        The .session file must NOT be committed to git — it is excluded by
        .gitignore (*.session rule). It is tied to your personal Telegram
        account credentials.

    Usage:
        python -m ingestion.telegram_producer
        (from the data-pipeline/ directory)
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        stream=sys.stdout,
    )

    producer = TelegramProducer()
    try:
        asyncio.run(producer.run())
    except KeyboardInterrupt:
        logger.info("[telegram] Shutdown requested — flushing Kafka buffer.")
    finally:
        producer.close()


if __name__ == "__main__":
    main()
