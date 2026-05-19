"""
Polymarket Producer — WebSocket / REST, real-time.

Connects to Polymarket's CLOB WebSocket for real-time market price streams
and polls the Gamma REST API for market snapshots and discussion comments.

Two distinct Bronze payload types are emitted to ingest.bronze.polymarket:
  - "price_update": real-time odds, volume, order book snapshot, whale alerts
  - "comment":      batch of discussion comments grouped by market_id

Why two types in one topic: Polymarket's pricing and discourse data are
tightly coupled by market_id. Splitting into two Bronze topics would force
consumers to join across partitions. The Silver Job dispatches by
payload_type: prices → SILVER_STRUCTURED_METRICS, comments → SILVER_SOCIAL_PULSE
(see BRONZE_TO_SILVER_ROUTING in kafka_topics.py and the note in that file).

Kafka Target: ingest.bronze.polymarket
Sprint Priority: 1 — establishes the real-time WebSocket streaming pattern.

References:
    - Section 2.1:  Producer Matrix (Polymarket row)
    - Section B.8:  Polymarket technical parameters (volume filter, whale threshold,
                    poll intervals, WebSocket feed)
    - Section C.1:  Bronze Schema (build_bronze_message wraps every payload)
    - Section 3.2:  Message Envelope & DRY (all Kafka logic in kafka_utils.py)
    - Section 3.3:  Service Isolation — no transformation, no DB writes here
    - Section 3.4:  NDJSON serialisation (handled by kafka_utils.py)
"""

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Optional

import requests
import websockets

from config.kafka_topics import BRONZE_POLYMARKET
from config.settings import POLYMARKET_API_KEY, POLYMARKET_API_SECRET
from utils.kafka_utils import build_bronze_message, make_producer, timed_request
from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)
setup_logging()

# ==========================================================
# Polymarket API Endpoints (Section B.8)
# ==========================================================

# Gamma API: market metadata, comments
GAMMA_API_BASE = "https://gamma-api.polymarket.com"

# CLOB API: order book, price history, recent trades
CLOB_API_BASE = "https://clob.polymarket.com"

# CLOB WebSocket: real-time price_change / book / last_trade events
CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


# ==========================================================
# Ingestion Parameters (Section B.8)
# ==========================================================

# SNR filter: only markets with cumulative volume above this threshold
# are ingested. Prevents low-liquidity noise from entering Bronze.
MIN_VOLUME_USD = 10_000         # $10k minimum market volume (Section 2.2)

# "Whale" trade threshold — any single position exceeding this value
# gets flagged in raw_payload so the Silver/Gold jobs can escalate
# impact_level to 5 without re-checking the trade size (Section B.8).
WHALE_THRESHOLD = 100_000       # $100k single-position whale alert

# Polling intervals
PRICE_POLL_SEC    = 300         # 5-min REST price snapshot (Section B.8: 5-10 min)
COMMENT_POLL_SEC  = 1_200       # 20-min discussion sync    (Section B.8)
MARKET_REFRESH_SEC = 300        # 5-min active market list refresh

# WebSocket reconnect delay on disconnect
WS_RECONNECT_DELAY = 5          # seconds

# Maximum tokens per WebSocket subscription message (CLOB API limit)
WS_BATCH_SIZE = 100

# Canonical source identifier used in every Bronze envelope
SOURCE_NAME = "polymarket"


# ==========================================================
# Producer
# ==========================================================

class PolymarketProducer:
    """
    Dual-mode Polymarket ingestion: WebSocket (real-time) + REST (scheduled).

    Why dual-mode: The CLOB WebSocket delivers sub-second price ticks — essential
    for capturing whale movements before they surface in REST snapshots. However,
    WebSocket feeds can miss ticks during reconnects. The REST price poller runs
    every 5 min as a reconciliation fallback. Discussion comments have no WebSocket
    feed and are polled via Gamma REST every 20 min.

    Concurrency model: asyncio with three coroutine loops running in parallel via
    asyncio.gather(). Blocking REST calls are offloaded to a thread-pool executor
    so they never stall the WebSocket receive loop.

    Emits to: ingest.bronze.polymarket (BRONZE_POLYMARKET)
    """

    def __init__(self) -> None:
        self._producer = make_producer()
        # Shared state — updated by _market_refresh_loop, read by all others.
        # Protected by _lock to prevent torn reads during refresh.
        self._active_markets: list[dict] = []
        self._token_ids: list[str] = []
        self._lock = asyncio.Lock()

    # ----------------------------------------------------------
    # Market Discovery (Gamma REST API)
    # ----------------------------------------------------------

    def _fetch_active_markets(self) -> list[dict]:
        """
        Fetch active markets from the Gamma API and apply the $10k volume filter.

        Why filter at source: Section 2.2 mandates SNR optimisation. Only
        high-conviction markets (volume > $10k) carry useful forecasting signal.
        Low-liquidity markets are dominated by noise and would inflate downstream
        processing costs.

        Returns:
            List of market dicts enriched with: id, question, volume, liquidity,
            conditionId, and tokens (each with tokenId and outcome label).
        """
        url = f"{GAMMA_API_BASE}/markets"
        params = {"active": "true", "closed": "false", "limit": 500}
        headers = _auth_headers()

        response, duration_ms = timed_request(
            lambda: requests.get(url, params=params, headers=headers, timeout=15)
        )
        response.raise_for_status()

        markets = response.json()
        if isinstance(markets, dict):
            # Some API versions wrap in {"data": [...]}
            markets = markets.get("data", [])

        filtered = [
            m for m in markets
            if float(m.get("volume", 0) or 0) >= MIN_VOLUME_USD
        ]

        logger.info(
            "[polymarket] Market discovery: %d total fetched, %d pass $%dk filter",
            len(markets), len(filtered), MIN_VOLUME_USD // 1_000,
        )
        return filtered

    # ----------------------------------------------------------
    # Price Snapshots (CLOB REST API)
    # ----------------------------------------------------------

    def _fetch_market_prices(self, market: dict) -> Optional[dict]:
        """
        Fetch current order book snapshot for a market via CLOB REST.

        Why REST alongside WebSocket: REST snapshots serve as a reconciliation
        fallback and fill any ticks missed during WebSocket reconnects. They
        also provide structured liquidity_pool_tvl and bid_ask_spread values
        needed by the Silver structured_metrics schema (Section C.2).

        Returns None on 404 (market expired) or transient network errors — the
        producer skips silently rather than flooding DLQ with expected 404s.
        """
        condition_id = market.get("conditionId") or market.get("condition_id", "")
        if not condition_id:
            return None

        url = f"{CLOB_API_BASE}/markets/{condition_id}"
        try:
            response, duration_ms = timed_request(
                lambda: requests.get(url, headers=_auth_headers(), timeout=10)
            )
            if response.status_code == 404:
                return None  # market closed/expired — expected, not an error
            response.raise_for_status()
            clob_data = response.json()
        except requests.RequestException as exc:
            logger.warning(
                "[polymarket] Price fetch failed for %s: %s", condition_id, exc
            )
            return None

        tokens = clob_data.get("tokens", [])

        # Whale detection on REST snapshot: a token with one-sided price near 0
        # or 1 combined with large volume suggests a large position sweep.
        volume_24h = float(market.get("volume24hr", 0) or 0)
        whale_alert = volume_24h >= WHALE_THRESHOLD

        return {
            "payload_type":    "price_update",
            "ingestion_mode":  "rest_snapshot",
            "market_id":       market.get("id", ""),
            "condition_id":    condition_id,
            "question":        market.get("question", ""),
            "tokens":          tokens,
            "volume_24h_usd":  volume_24h,
            "liquidity_usd":   float(market.get("liquidity", 0) or 0),
            "end_date":        market.get("endDate", ""),
            "whale_alert":     whale_alert,
            "fetched_at":      datetime.now(timezone.utc).isoformat(),
        }

    # ----------------------------------------------------------
    # Discussion Comments (Gamma REST API)
    # ----------------------------------------------------------

    def _fetch_market_comments(self, market: dict) -> list[dict]:
        """
        Fetch the most recent discussion comments for a market from Gamma API.

        Comments are returned as a flat list grouped by market_id. The Silver
        Job receives these as "volume-centric" social objects and routes them
        to process.silver.social_pulse (Section C.3 — Polymarket pattern).

        Media-only or empty comment bodies are skipped, consistent with the
        Telegram ingestion rule: ignore messages without text (Section A.1).

        Returns:
            List of normalised comment dicts. Empty list if no comments or on error.
        """
        market_id = market.get("id", "")
        if not market_id:
            return []

        url = f"{GAMMA_API_BASE}/comments"
        params = {"market": market_id, "limit": 100}

        try:
            response, _ = timed_request(
                lambda: requests.get(
                    url, params=params, headers=_auth_headers(), timeout=10
                )
            )
            if response.status_code in (403, 404):
                return []
            response.raise_for_status()
            data = response.json()
            raw_comments = data if isinstance(data, list) else data.get("data", [])
        except requests.RequestException as exc:
            logger.warning(
                "[polymarket] Comment fetch failed for market %s: %s", market_id, exc
            )
            return []

        normalised = []
        for c in raw_comments:
            body = c.get("body", "").strip()
            if not body:
                # Skip media-only / empty comments (Section A.1 principle)
                continue
            normalised.append({
                "comment_id": c.get("id", ""),
                "author":     c.get("user", {}).get("username", ""),
                "text":       body,
                "timestamp":  c.get("createdAt", ""),
                "upvotes":    int(c.get("upvotes", 0) or 0),
            })

        return normalised

    # ----------------------------------------------------------
    # Kafka Emission
    # ----------------------------------------------------------

    def _emit(self, raw_payload: dict, source_endpoint: str) -> None:
        """
        Wrap raw_payload in a Bronze envelope and publish to ingest.bronze.polymarket.

        Partition key: market_id (or condition_id as fallback).
        Keying by market_id ensures all price and comment records for the same
        market land in the same Kafka partition — preserving chronological order
        for the Silver Job's per-market state processing.
        """
        msg = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=source_endpoint,
            raw_payload=raw_payload,
        )
        key = raw_payload.get("market_id") or raw_payload.get("condition_id", "")
        self._producer.send(BRONZE_POLYMARKET, value=msg, key=key or None)

    # ----------------------------------------------------------
    # WebSocket Loop — real-time CLOB stream
    # ----------------------------------------------------------

    async def _run_websocket(self) -> None:
        """
        Maintain a persistent WebSocket connection to the CLOB real-time feed.

        Event types received from CLOB WebSocket:
          - "price_change":  new best bid/ask for a token
          - "book":          full order book snapshot
          - "last_trade":    most recent matched trade — used for whale detection

        Reconnects with a fixed WS_RECONNECT_DELAY on any disconnect or error.
        The loop is intentionally infinite; cancellation is handled externally
        when asyncio.gather() receives a KeyboardInterrupt.

        Why we subscribe in batches of WS_BATCH_SIZE: the CLOB WebSocket
        rejects subscription messages with more than 100 asset IDs.
        """
        while True:
            async with self._lock:
                token_ids = list(self._token_ids)

            if not token_ids:
                logger.debug(
                    "[polymarket] No token IDs available yet; retrying WS in %ds",
                    WS_RECONNECT_DELAY,
                )
                await asyncio.sleep(WS_RECONNECT_DELAY)
                continue

            try:
                async with websockets.connect(
                    CLOB_WS_URL,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    logger.info(
                        "[polymarket] WebSocket connected — subscribing to %d tokens",
                        len(token_ids),
                    )
                    # Subscribe in batches to respect the API limit
                    for i in range(0, len(token_ids), WS_BATCH_SIZE):
                        batch = token_ids[i : i + WS_BATCH_SIZE]
                        await ws.send(
                            json.dumps({"assets_ids": batch, "type": "market"})
                        )

                    async for raw_msg in ws:
                        try:
                            event = json.loads(raw_msg)
                        except json.JSONDecodeError:
                            continue

                        # CLOB emits either a single event dict or a list of events
                        events = event if isinstance(event, list) else [event]
                        for evt in events:
                            self._handle_ws_event(evt)

            except (websockets.ConnectionClosed, ConnectionRefusedError, OSError) as exc:
                logger.warning(
                    "[polymarket] WebSocket disconnected: %s — reconnecting in %ds",
                    exc, WS_RECONNECT_DELAY,
                )
                await asyncio.sleep(WS_RECONNECT_DELAY)

            except Exception as exc:
                logger.error(
                    "[polymarket] WebSocket unexpected error: %s — reconnecting in %ds",
                    exc, WS_RECONNECT_DELAY,
                )
                await asyncio.sleep(WS_RECONNECT_DELAY)

    def _handle_ws_event(self, event: dict) -> None:
        """
        Convert a single CLOB WebSocket event into a Bronze message and emit.

        Whale alert logic for "last_trade" events:
            trade_value = size (shares) × price (USD per share)
            If trade_value ≥ $100,000 → whale_alert = True (Section B.8)

        The whale_alert flag is embedded in raw_payload so the Silver/Gold jobs
        can escalate impact_level to 5 without re-computing the trade size.
        """
        event_type = event.get("event_type", "")
        asset_id   = event.get("asset_id", "")
        size       = float(event.get("size", 0) or 0)
        price      = float(event.get("price", 0) or 0)

        trade_value = size * price if event_type == "last_trade" else 0.0
        whale_alert = trade_value >= WHALE_THRESHOLD

        raw_payload = {
            "payload_type":    "price_update",
            "ingestion_mode":  "websocket",
            "event_type":      event_type,
            "asset_id":        asset_id,
            # market field may be present on CLOB events; fall back to asset_id
            "market_id":       event.get("market", asset_id),
            "price":           price,
            "side":            event.get("side", ""),
            "size":            size,
            "trade_value_usd": trade_value if event_type == "last_trade" else None,
            "whale_alert":     whale_alert,
            "timestamp":       event.get(
                "timestamp", datetime.now(timezone.utc).isoformat()
            ),
        }

        self._emit(raw_payload, source_endpoint=CLOB_WS_URL)

        if whale_alert:
            logger.warning(
                "[polymarket] WHALE ALERT — asset_id=%s  $%.0f  price=%.4f",
                asset_id, trade_value, price,
            )

    # ----------------------------------------------------------
    # Market Refresh Loop
    # ----------------------------------------------------------

    async def _market_refresh_loop(self) -> None:
        """
        Periodically re-fetch the active market list and update token_ids.

        Why periodic refresh: new markets launch and old ones close continuously.
        A stale market list causes the WebSocket to miss emerging high-volume
        contracts and keeps subscriptions alive for closed markets.

        The first refresh runs immediately (no initial sleep) to populate state
        before the WebSocket and poll loops start making decisions based on it.
        """
        while True:
            try:
                loop = asyncio.get_running_loop()
                markets = await loop.run_in_executor(
                    None, self._fetch_active_markets
                )

                # Extract CLOB token IDs from each market's tokens list.
                # Each binary-outcome market has two tokens: YES and NO.
                new_token_ids: list[str] = []
                for m in markets:
                    for token in m.get("tokens", []):
                        tid = token.get("token_id") or token.get("tokenId", "")
                        if tid:
                            new_token_ids.append(tid)

                async with self._lock:
                    self._active_markets = markets
                    self._token_ids = new_token_ids

                logger.info(
                    "[polymarket] Market list refreshed: %d markets, %d tokens",
                    len(markets), len(new_token_ids),
                )

            except Exception as exc:
                logger.error("[polymarket] Market refresh error: %s", exc)

            await asyncio.sleep(MARKET_REFRESH_SEC)

    # ----------------------------------------------------------
    # Price Poll Loop — REST fallback
    # ----------------------------------------------------------

    async def _price_poll_loop(self) -> None:
        """
        Poll REST price snapshots every 5 min for all active markets.

        Why REST alongside WebSocket: WebSocket is the primary stream but REST
        snapshots serve as a reconciliation check and cover any ticks missed
        during reconnects. They also provide structured fields (liquidity,
        bid_ask_spread) that CLOB WebSocket events do not always include.

        A 30-second startup delay gives the market refresh loop time to populate
        _active_markets before the first REST sweep.
        """
        await asyncio.sleep(30)

        while True:
            async with self._lock:
                markets = list(self._active_markets)

            loop = asyncio.get_running_loop()
            for market in markets:
                price_payload = await loop.run_in_executor(
                    None, self._fetch_market_prices, market
                )
                if price_payload:
                    condition_id = market.get("conditionId", "")
                    self._emit(
                        price_payload,
                        source_endpoint=f"{CLOB_API_BASE}/markets/{condition_id}",
                    )

            await asyncio.sleep(PRICE_POLL_SEC)

    # ----------------------------------------------------------
    # Comment Poll Loop
    # ----------------------------------------------------------

    async def _comment_poll_loop(self) -> None:
        """
        Poll discussion comments every 20 min for all active markets.

        Comments for each market are batched into a single Bronze message
        (volume-centric pattern — Section C.3) with payload_type="comment"
        and the full comment list in raw_comments. The Silver Job routes
        this to process.silver.social_pulse.

        A 60-second startup offset staggers REST calls against the price
        poll loop to smooth Gamma API rate-limit usage.

        Phase 9.5 Stage B Item 3 — Gated behind POLYMARKET_COMMENTS_ENABLED.
        Polymarket's Gamma API /comments endpoint made a breaking change
        (now requires parent_entity_id + entity_entity_type; correct enum
        value pending discovery). Until upstream is resolved, the loop
        exits early so the producer doesn't spam ~100 422 warnings every
        20-min cycle. Re-enable via `POLYMARKET_COMMENTS_ENABLED=true`
        once the API call signature is repaired.
        """
        from config.settings import POLYMARKET_COMMENTS_ENABLED
        if not POLYMARKET_COMMENTS_ENABLED:
            logger.info(
                "[polymarket] _comment_poll_loop disabled "
                "(POLYMARKET_COMMENTS_ENABLED=false). Gamma API /comments "
                "endpoint has a breaking change pending upstream fix — see "
                "phase95 Stage B Item 3."
            )
            return

        await asyncio.sleep(60)

        while True:
            async with self._lock:
                markets = list(self._active_markets)

            loop = asyncio.get_running_loop()
            for market in markets:
                comments = await loop.run_in_executor(
                    None, self._fetch_market_comments, market
                )
                if not comments:
                    continue

                # Emit as a per-market batch — volume-centric pattern (Section C.3)
                batch_payload = {
                    "payload_type": "comment",
                    "market_id":    market.get("id", ""),
                    "question":     market.get("question", ""),
                    "raw_comments": comments,
                    "fetched_at":   datetime.now(timezone.utc).isoformat(),
                }
                self._emit(
                    batch_payload,
                    source_endpoint=(
                        f"{GAMMA_API_BASE}/comments?market={market.get('id', '')}"
                    ),
                )

            await asyncio.sleep(COMMENT_POLL_SEC)

    # ----------------------------------------------------------
    # Main Entry Point
    # ----------------------------------------------------------

    async def run(self) -> None:
        """
        Start all concurrent loops via asyncio.gather().

        Startup sequence:
          1. Warm-up: fetch active markets synchronously so WebSocket and
             poll loops have a populated market list from the first iteration.
          2. Launch: market_refresh_loop, WebSocket loop, price poll loop,
             and comment poll loop run concurrently.

        Why asyncio over threading: the WebSocket loop is I/O-bound and
        benefits from the event loop's cooperative scheduling. asyncio also
        provides clean cancellation semantics when the process receives SIGINT.
        """
        logger.info("[polymarket] Producer starting — performing warm-up fetch...")

        # Warm-up: blocking fetch before coroutines start, so the WebSocket
        # has token IDs to subscribe to on its very first connect attempt.
        try:
            warm_markets = self._fetch_active_markets()
            warm_tokens: list[str] = []
            for m in warm_markets:
                for token in m.get("tokens", []):
                    tid = token.get("token_id") or token.get("tokenId", "")
                    if tid:
                        warm_tokens.append(tid)

            self._active_markets = warm_markets
            self._token_ids = warm_tokens
            logger.info(
                "[polymarket] Warm-up complete: %d markets, %d tokens",
                len(warm_markets), len(warm_tokens),
            )
        except Exception as exc:
            logger.error(
                "[polymarket] Warm-up failed: %s — continuing with empty market list",
                exc,
            )

        await asyncio.gather(
            self._market_refresh_loop(),
            self._run_websocket(),
            self._price_poll_loop(),
            self._comment_poll_loop(),
        )

    def close(self) -> None:
        """
        Flush pending Kafka messages and close the producer connection cleanly.

        Must be called in the finally block of the main() function to prevent
        message loss on shutdown — kafka-python-ng buffers messages in memory
        and flush() forces a synchronous drain before exit.
        """
        self._producer.flush()
        self._producer.close()
        logger.info("[polymarket] Producer closed cleanly.")


# ==========================================================
# Helpers
# ==========================================================

def _auth_headers() -> dict:
    """
    Build HTTP auth headers for Polymarket API requests.

    Polymarket's public endpoints work without auth; API keys unlock higher
    rate limits and private endpoint access. The key is optional — if absent
    from .env the producer runs in anonymous mode (Section 9.1).
    """
    if POLYMARKET_API_KEY:
        return {"Authorization": f"Bearer {POLYMARKET_API_KEY}"}
    return {}


# ==========================================================
# Docker Entry Point
# ==========================================================

def main() -> None:
    """
    Entry point for the Docker container (Section 8.2).

    asyncio.run() manages the event loop lifecycle and handles
    KeyboardInterrupt / SIGINT for graceful Docker container shutdown.
    Log format matches the structured JSON pattern described in Section 7.2;
    the JSON handler (python-json-logger) is applied by the Flink/Airflow
    observability layer — basic format is used here for local readability.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        stream=sys.stdout,
    )

    producer = PolymarketProducer()
    try:
        asyncio.run(producer.run())
    except KeyboardInterrupt:
        logger.info("[polymarket] Shutdown signal received.")
    finally:
        producer.close()


if __name__ == "__main__":
    main()
