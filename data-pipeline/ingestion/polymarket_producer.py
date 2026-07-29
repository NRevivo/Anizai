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
import time
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
# Retained after T2 removed the per-market CLOB price call: the price-history
# endpoint lives on this same host —
#   GET {CLOB_API_BASE}/prices-history?market=<token_id>&interval=max
# — and it is the data source for the frontend's price chart. The token ids it
# needs are the `clob_token_ids` preserved in the REST payload. Verified live
# 2026-07-28: 712 points over a market's life, ~10-minute granularity.
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

# The outcome label whose price becomes the market's probability. Selected by
# LABEL, never by position — Gamma does not guarantee outcome order, and reading
# index 0 blindly reports the complement for any market listed ["No", "Yes"].
AFFIRMATIVE_LABEL = "Yes"


def _parse_json_array(raw) -> list:
    """
    Gamma returns `outcomes`, `outcomePrices` and `clobTokenIds` as
    JSON-encoded STRINGS, not arrays — verified against the live API on
    2026-07-28 across 100/100 active markets:

        outcomes       '["Yes", "No"]'
        outcomePrices  '["0.505", "0.495"]'
        clobTokenIds   '["98022490…", "53831553…"]'

    Every one of them therefore needs decoding before use. Accepts a real list
    too, so the helper survives an upstream shape change without breaking.
    Returns [] rather than raising: the caller treats an unreadable market as
    unpriceable and skips it (T3), which is the honest outcome.
    """
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, str):
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _extract_outcome_prices(market: dict) -> Optional[dict]:
    """
    Map outcome label -> price for one Gamma market.

    Returns None when the market cannot be priced — mismatched label/price
    lengths, an unparseable price, or an empty outcome list. None means "skip
    this market entirely" (T3); it never degrades to a zero, because a zero here
    is indistinguishable from a market the world genuinely prices at 0.
    """
    labels = [str(label).strip() for label in _parse_json_array(market.get("outcomes"))]
    prices = _parse_json_array(market.get("outcomePrices"))

    if not labels or len(labels) != len(prices):
        return None

    resolved: dict = {}
    for label, price in zip(labels, prices):
        try:
            resolved[label] = float(price)
        except (TypeError, ValueError):
            return None
    return resolved


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
        # T3: markets dropped this sweep because they could not be priced.
        # Counted so a skipped market is distinguishable from one that was never
        # fetched — without it the two look identical in the logs, which is the
        # ambiguity that made the original zero-price bug invisible for 79 days.
        self._skipped_markets = 0

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
        Build a price snapshot for one market from the Gamma market object.

        T2 — why there is no longer a CLOB call here:
            The Gamma object already carries `outcomePrices` (verified live
            2026-07-28: present on 100/100 active markets, alongside
            `lastTradePrice`/`bestBid`/`bestAsk`). The previous implementation
            spent one CLOB round-trip per market to read `tokens`, then never
            extracted a price from it — so every REST row reached Silver with
            `current_value = 0.0`, because `map_price_update_to_silver` reads
            `raw.get("price", 0.0)` (`processing/silver_job.py:170`) and no REST
            payload ever carried that key. Emitting `price` is the whole fix:
            the Silver mapper is unchanged and Flink is not resubmitted.

            Dropping the CLOB call also removes ~2,100 HTTP requests per sweep
            once pagination lands (T5), which is what made the 21x load concern
            moot rather than merely survivable.

        T3 — a market whose outcomes we cannot read emits NOTHING.
            No sentinel (`0.0`, `null`, `-1`): a sentinel is a default served as
            a measurement, which is precisely the bug being fixed. No DLQ: this
            is an unsupported market shape, not a malformed message, and replay
            cannot help. WARNING + a skip counter instead, so a skipped market is
            distinguishable from one that was never fetched.

        Returns None when the market cannot be priced (caller skips it).
        """
        condition_id = market.get("conditionId") or market.get("condition_id", "")
        if not condition_id:
            logger.warning(
                "[polymarket] Skipping market with no conditionId: id=%s question=%r",
                market.get("id", ""), str(market.get("question", ""))[:80],
            )
            self._skipped_markets += 1
            return None

        outcome_prices = _extract_outcome_prices(market)
        if outcome_prices is None or AFFIRMATIVE_LABEL not in outcome_prices:
            logger.warning(
                "[polymarket] Skipping market %s — unsupported outcome labels %r "
                "(need %r); question=%r",
                condition_id,
                _parse_json_array(market.get("outcomes")),
                AFFIRMATIVE_LABEL,
                str(market.get("question", ""))[:80],
            )
            self._skipped_markets += 1
            return None

        # D3: select by LABEL, never by index. Outcome order is not guaranteed,
        # and reading the wrong index silently reports the complement — a 3%
        # market as 97%.
        price = outcome_prices[AFFIRMATIVE_LABEL]

        volume_24h = float(market.get("volume24hr", 0) or 0)

        # T7 — preserve what the catalog needs. `endDate` was already fetched and
        # then dropped; `status` was hardcoded "active" downstream regardless of
        # real state, which would let the agent forecast a resolved market.
        parent_event_id = ""
        events = market.get("events")
        if isinstance(events, list) and events and isinstance(events[0], dict):
            parent_event_id = str(events[0].get("id", ""))

        if market.get("closed") is True:
            market_status = "closed"
        elif market.get("archived") is True:
            market_status = "archived"
        elif market.get("active") is False:
            market_status = "inactive"
        else:
            market_status = "active"

        return {
            "payload_type":    "price_update",
            "ingestion_mode":  "rest_snapshot",
            "market_id":       market.get("id", ""),
            "condition_id":    condition_id,
            "question":        market.get("question", ""),
            # T2: the key silver_job has always read and never found. 0-1
            # probability — see the plan's §2.2 on scale.
            "price":           price,
            # Both sides survive: NO is not 1 - YES once a spread exists.
            "outcome_prices":  outcome_prices,
            # Replaces the CLOB `tokens` list. Kept because a WebSocket revival
            # and the catalog both need the token ids.
            "clob_token_ids":  [str(t) for t in _parse_json_array(market.get("clobTokenIds"))],
            "parent_event_id": parent_event_id,
            "end_date_iso":    str(market.get("endDateIso", "") or ""),
            "market_status":   market_status,
            "volume_24h_usd":  volume_24h,
            "liquidity_usd":   float(market.get("liquidity", 0) or 0),
            "end_date":        market.get("endDate", ""),
            # T4 — REST cannot detect a whale. The documented meaning is a single
            # trade over $100k (see WHALE_THRESHOLD); this path only ever saw
            # total 24h market volume, so it fired on essentially every active
            # market on every observation. `volume24hr` is present on 100/100
            # live markets, so that branch was live, not dormant. Real whale
            # detection exists only on the WebSocket path.
            "whale_alert":     False,
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
        Emit a price snapshot every 5 min for every active market.

        Since T2 this loop makes **no per-market HTTP call** — the price comes
        from the Gamma object already held in `_active_markets`, so a sweep is a
        pure transform over the cached list. The executor hop is kept only
        because the list can be large enough for the transform to be worth
        yielding on.

        T8 — every sweep reports emitted / skipped / total. The old loop logged
        nothing at all, so a market dropped by `_fetch_market_prices` looked
        exactly like a market that was never in the list.

        A 30-second startup delay gives the market refresh loop time to populate
        _active_markets before the first sweep.
        """
        await asyncio.sleep(30)

        while True:
            async with self._lock:
                markets = list(self._active_markets)

            self._skipped_markets = 0
            emitted = 0
            started = time.monotonic()

            loop = asyncio.get_running_loop()
            for market in markets:
                price_payload = await loop.run_in_executor(
                    None, self._fetch_market_prices, market
                )
                if price_payload:
                    self._emit(
                        price_payload,
                        source_endpoint=f"{GAMMA_API_BASE}/markets",
                    )
                    emitted += 1

            # C7: the loop sleeps AFTER the sweep, so the real cadence is
            # "sweep duration + PRICE_POLL_SEC". Logging the duration makes that
            # visible instead of letting the cadence drift silently.
            logger.info(
                "[polymarket] Price sweep: %d emitted, %d skipped, %d total "
                "in %.1fs (next sweep in %ds)",
                emitted, self._skipped_markets, len(markets),
                time.monotonic() - started, PRICE_POLL_SEC,
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
