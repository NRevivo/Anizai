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

# SNR filter — applied at the EVENT level on CUMULATIVE volume (plan D1/F2).
#
# Why the event and not the market (D2): measured 2026-07-30, 17 of the top 25
# markets above $1M were individual candidate legs of two 2028 nomination events
# (LeBron James, Oprah Winfrey, MrBeast) — cheap perpetual longshots in 128-outcome
# fields, near-worthless as forecasting signal, and each one severed from its
# parent event's context. The event is the coherent forecasting unit.
#
# Why $500k and not $1M (D1): the $500k→$1M band carries substantive questions
# (Trump impeachment, India–Pakistan strike, Iran enrichment, BoJ decision) while
# $1M's floor is dominated by repetitive per-settlement Ukraine-map markets.
#
# `volume_min` filters on cumulative `volume`, server-side, and is genuinely
# honoured (F2) — unlike `active`, which is silently ignored on /events (F7).
EVENT_VOLUME_MIN_USD = 500_000

# F3 — Gamma hard-caps `limit` at 100 on both /events and /markets regardless of
# the value sent. The original defect asked for 500 and silently received 100.
# Asking for more than the cap does not fail; it lies.
GAMMA_PAGE_LIMIT = 100

# F5 — plain offset paging works to offset 2000; ~2050+ returns HTTP 422 pointing
# at /events/keyset. The keyset cursor (`after_cursor`) is honoured ONLY on the
# /keyset routes — on plain /events it is silently ignored and re-returns page 1.
# The target set is ~386 events, so this ceiling is headroom, not a constraint.
GAMMA_MAX_OFFSET = 2_000

# Run-over-run shrinkage beyond this fraction is reported as a WARNING (P3).
# Sized against D6's measured churn (~8%/day, 90%+ day-over-day overlap): at an
# hourly cadence an ordinary sweep moves by well under 1%, so 20% cannot be
# resolution churn — it is a filter that stopped matching.
EVENT_SET_SHRINK_WARN_RATIO = 0.20

# ==========================================================
# TARGET tag set (plan §1.1 — the D3 artifact)
# ==========================================================
# An event is IN if it carries >=1 TARGET tag, then dropped if it carries any
# EXCLUDE tag. Tags live on the EVENT, never on the market (F10).
#
# Verified live 2026-07-30 against the 386-event >=$500k non-closed set:
#   386 raw -> 253 after this include pass -> 240 after EXCLUDE -> 192 after the
#   endDate filter -> 2,421 live nested markets -> 1,396 priceable.
#
# 13 of these ids matched zero events in that set (Earnings, Earnings Calls,
# House Elections, India, North Korea, Nov 4 Elections, Pakistan, Trade War,
# defense, eu, gpt, interest rates, monetary policy). They are RETAINED
# deliberately: each carries a large active-event count platform-wide and is
# merely below the $500k floor today, membership is re-derived every run (D6),
# and a tag that matches nothing costs nothing.
#
# The 19 tags marked (tail) below were found by that same sweep and are NOT in
# the plan's §1.1 list. Adding them nets ZERO events today — every one currently
# co-occurs with Politics/Geopolitics/Elections — and they are included anyway,
# as tail insurance. The failure they guard against is an event tagged ONLY
# `Fed Rates` or ONLY `Strait of Hormuz`, carrying neither `Fed` nor `Iran`:
# under the include set alone that event is invisible, and its absence looks
# exactly like it not existing. A tag that matches nothing costs nothing, so the
# asymmetry is entirely one-sided.
TARGET_TAGS: dict[str, str] = {
    # --- Geopolitics / wars: umbrella ---
    "100265": "Geopolitics",     "101970": "World",         "101794": "Foreign Policy",
    "464":    "Military Actions", "1289":   "Nuclear",       "101761": "Trade War",
    "193":    "military",        "793":    "defense",
    # --- Geopolitics: regions and named conflicts (D4 — subscribe by REGION,
    #     never by conflict noun; `war`/`conflict`/`invasion`/`Sanctions` and the
    #     rest all carry zero active events, re-confirmed 2026-07-30) ---
    "78":     "Iran",            "95":     "Russia",        "180":    "Israel",
    "154":    "Middle East",     "102486": "Ukraine Map",   "96":     "Ukraine",
    "303":    "China",           "270":    "putin",         "104010": "Iran Ceasefire",
    "246":    "Venezuela",       "734":    "UK",            "61":     "Gaza",
    "192":    "NATO",            "103027": "Ukraine Peace Deal", "452": "zelensky",
    "297":    "Hezbollah",       "101270": "Turkey",        "102305": "US-Iran",
    "582":    "Houthis",         "867":    "Taiwan",        "102475": "Russia Capture",
    "1476":   "eu",              "114":    "Syria",         "518":    "India",
    "102498": "Trump-Zelenskyy", "351":    "North Korea",   "102477": "Trump-Putin",
    "102824": "Trump x al-Sharaa", "1383": "Poland",        "872":    "Pakistan",
    "104039": "U.S. x Iran",     "415":    "Peace Deal",    "104005": "Iran Regime",      # (tail)
    "262":    "Strait of Hormuz", "104064": "Israel x Iran", "849":   "Lebanon",          # (tail)
    "102304": "Khamenei",        "216":    "zelenskyy",     "103996": "Reza Pahlavi",     # (tail)
    "102868": "Cuba",            "101569": "Greenland",                                   # (tail)
    # --- Politics ---
    "2":      "Politics",        "126":    "Trump",         "514":    "Congress",
    "100199": "Senate",          "1628":   "Courts",        "102886": "President",
    "101191": "Trump Presidency", "165":   "United States",                               # (tail)
    # --- Elections ---
    "144":    "Elections",       "1101":   "US Election",   "102289": "Midterms",
    "103899": "House Elections", "102786": "Nov 4 Elections", "1597": "Global Elections",
    "264":    "Primaries",       "902":    "primary elections", "104743": "Main Election",
    "105438": "Intl Election Props",
    "101206": "World Elections",                                                          # (tail)
    # --- Macro & economy (Hit Price / Pre-Market deliberately omitted: the
    #     substantive ladder markets arrive via Finance without the tick noise) ---
    "120":    "Finance",         "102676": "Equities",      "100328": "Economy",
    "1013":   "Earnings",        "102000": "Macro Indicators", "702":  "Inflation",
    "101800": "Economic Policy", "159":    "Fed",           "370":    "GDP",
    "103339": "Fed Chair",       "131":    "interest rates", "132":   "monetary policy",
    "309":    "Oil",             "100196": "Fed Rates",     "101550": "Jerome Powell",    # (tail)
    "101031": "Commodities",                                                              # (tail)
    # --- Business ---
    "107":    "Business",        "600":    "IPOs",          "102599": "IPO",
    "102451": "Earnings Calls",  "105048": "OpenAI IPO",                                  # (tail)
    # --- Technology ---
    "1401":   "Tech",            "439":    "AI",            "101999": "Big Tech",
    "537":    "OpenAI",          "285":    "sam altman",    "662":    "llm",
    "473":    "gpt",             "102464": "GPT-5",
}

# Applied AFTER the include pass — an event carrying any of these is dropped even
# if it also carries a TARGET tag. Verified 2026-07-30: this removes 13 of 253
# events, and ZERO sports-family events (MLB/NFL/UFC/mma/Basketball/football)
# survive into the final set, because `1` Sports catches them all upstream.
#
# Health and Science are absent by design (D3): Polymarket has effectively no
# coverage (23 and 91 events; no general health tag exists), and what exists
# arrives incidentally through Politics/Geopolitics.
#
# NOT included, and deliberately so: `101757` Recurring, `102127` Up or Down,
# `102892` 5M, `102467` 15M, `102175` 1H, `84` Weather. §1.1g flagged these as
# candidate exclusions to VERIFY before applying. Measured against the target
# set they each touch ZERO events — they would neither drop the legitimate
# periodic macro events that were the stated worry (Fed decisions, CPI) nor
# remove any noise. Six inert filters are worse than none.
EXCLUDE_TAGS: dict[str, str] = {
    # --- Crypto (D3): more events than all target categories combined, but
    #     overwhelmingly 1-market recurring price ticks. Only 41 events carry
    #     both a target tag and a crypto tag, so excluding it costs almost
    #     nothing. ---
    "21":     "Crypto",          "1312":   "Crypto Prices", "336":    "token launch",
    "100171": "Stablecoins",     "136":    "Airdrops",      "235":    "Bitcoin",
    "39":     "Ethereum",        "101267": "XRP",           "818":    "Solana",
    "101312": "Ripple",          "100178": "Dogecoin",      "102716": "BNB",
    # --- Platform internals: a Polymarket display flag, not a topic ---
    "102169": "Hide From New",
    # --- Sports / games (only reachable when a target tag co-occurs) ---
    "1":      "Sports",          "100639": "Games",         "100350": "Soccer",
    "64":     "Esports",         "65":     "league of legends",
}

# "Whale" trade threshold — any single position exceeding this value
# gets flagged in raw_payload so the Silver/Gold jobs can escalate
# impact_level to 5 without re-checking the trade size (Section B.8).
WHALE_THRESHOLD = 100_000       # $100k single-position whale alert

# Polling intervals
#
# D7 — hourly, not 5-minutely, for both the price sweep and the market-list
# refresh. Dense sampling bought density in our own time-series, and the frontend
# price-history chart is not served from that series: it comes from the CLOB
# prices-history endpoint (F12), which carries a market's full life at ~10-minute
# granularity regardless of when we started collecting. So 5-minutely sampling
# paid 12x the write volume for a chart that does not read it.
#
# At 1,396 collectable markets (measured 2026-07-30) hourly is ~33.5k rows/day,
# ~235k over a week-long run — against the 93,607 rows that measured 415 ms on
# the agent's resolver, so this stays well inside the 15 s PER_AGENT_TIMEOUT_S.
PRICE_POLL_SEC    = 3_600       # hourly REST price snapshot (D7)
COMMENT_POLL_SEC  = 1_200       # 20-min discussion sync     (Section B.8)
MARKET_REFRESH_SEC = 3_600      # hourly active market list refresh (D7)

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


def _pair_by_outcome_label(market: dict, value_field: str) -> Optional[dict]:
    """
    Pair each outcome LABEL with its corresponding entry in `value_field`.

    Returns None when the two arrays cannot be aligned (mismatched lengths or no
    outcomes at all), which every caller reads as "this market is unreadable".

    Why this alignment lives in exactly one place:
        Gamma returns `outcomes`, `outcomePrices` and `clobTokenIds` as three
        separate, index-aligned, JSON-encoded arrays (F8). Any consumer that
        reaches into one of them by POSITION is one reordering away from
        silently reporting the complement — a 3% market published as 97%, or the
        NO token charted as the YES line. Neither failure raises; both just look
        like data. Doing the pairing once, by label, means that trap has a single
        site to get right rather than one per caller.
    """
    labels = [str(label).strip() for label in _parse_json_array(market.get("outcomes"))]
    values = _parse_json_array(market.get(value_field))

    if not labels or len(labels) != len(values):
        return None
    return dict(zip(labels, values))


def _extract_outcome_prices(market: dict) -> Optional[dict]:
    """
    Map outcome label -> price for one Gamma market.

    Returns None when the market cannot be priced — mismatched label/price
    lengths, an unparseable price, or an empty outcome list. None means "skip
    this market entirely" (T3); it never degrades to a zero, because a zero here
    is indistinguishable from a market the world genuinely prices at 0.
    """
    paired = _pair_by_outcome_label(market, "outcomePrices")
    if paired is None:
        return None

    resolved: dict = {}
    for label, price in paired.items():
        try:
            resolved[label] = float(price)
        except (TypeError, ValueError):
            return None
    return resolved


def _extract_clob_token_ids(market: dict) -> dict:
    """
    Map outcome label -> CLOB token id, e.g. {"Yes": "97186…", "No": "81470…"}.

    P8 — why a dict and not the positional list this used to emit:
        The CLOB price-history call that feeds the frontend chart
        (`prices-history?market=<token_id>`) needs the YES token specifically.
        A positional list forces every consumer to pick `[0]` and hope, which
        reproduces the D3 label-not-index defect one level down: the chart would
        render the NO side's history as the market's probability, inverting a 7%
        market into 93%. The label is the only thing that identifies which token
        is which, so the label travels with it.

    Returns {} — not None — when the arrays cannot be aligned. An unreadable
    token map degrades the chart; it does not invalidate the price, which is the
    primary signal and is extracted independently. Skipping an otherwise
    perfectly priceable market because its token ids were malformed would trade a
    real measurement for a missing one.
    """
    paired = _pair_by_outcome_label(market, "clobTokenIds")
    if paired is None:
        return {}
    return {label: str(token_id) for label, token_id in paired.items()}


# ==========================================================
# Event selection helpers (P1) — pure functions, no HTTP
# ==========================================================
# Kept module-level and side-effect free so the selection rules can be tested
# against a captured /events response without a producer instance or a network
# stub (plan G3).

def _event_tag_ids(event: dict) -> set:
    """
    The set of tag ids carried by one Gamma event, as strings.

    Tags live on the EVENT, never on the market (F10) — this is the whole
    reason discovery moved to /events. Gamma returns tag ids as ints in some
    responses and strings in others; normalising to str here means the
    TARGET_TAGS / EXCLUDE_TAGS lookups cannot miss on type alone, which under
    F6 would fail silently as "this event has no tags" rather than as an error.
    """
    ids = set()
    for tag in event.get("tags") or []:
        if isinstance(tag, dict) and tag.get("id") is not None:
            ids.add(str(tag["id"]))
    return ids


def _event_passes_tag_filter(event: dict) -> bool:
    """
    D3: keep events carrying >=1 TARGET tag, then drop any carrying an EXCLUDE
    tag. Exclusion wins over inclusion — a market tagged both `Politics` and
    `Crypto` is a crypto price tick that mentions politics, not the reverse.
    """
    tag_ids = _event_tag_ids(event)
    if not tag_ids & set(TARGET_TAGS):
        return False
    return not (tag_ids & set(EXCLUDE_TAGS))


def _event_end_date_passed(event: dict, now: datetime) -> bool:
    """
    D5: True when the event's `endDate` is already in the past.

    Measured 2026-07-30: 48 of 240 tag-passing events (20%) have an `endDate`
    behind us while still reporting `closed=false`. Relying on `closed` alone
    therefore means collecting — and forecasting — questions that have already
    ended. `closed=false` is necessary but not sufficient.

    An absent or unparseable `endDate` returns False (keep the event). A market
    is not evidence of having ended just because Gamma omitted a field; the
    agent-side resolved-market guard (A4) is the second line of defence.
    """
    raw = event.get("endDate") or ""
    if not isinstance(raw, str) or not raw:
        return False
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed < now


def _derive_market_status(market: dict) -> str:
    """
    The market's real state: "closed" | "archived" | "inactive" | "active".

    Single source of truth, used by BOTH the selection filter and the emitted
    payload. They previously derived it independently, which is precisely how a
    filter and the field it filters on drift apart — the filter drops one set
    while the payload reports another, and nothing detects the divergence.

    Order matters and is not arbitrary: `closed` wins over `archived` wins over
    `inactive`, because a market can carry several of these flags at once and
    the most terminal one is the honest answer.
    """
    if market.get("closed") is True:
        return "closed"
    if market.get("archived") is True:
        return "archived"
    if market.get("active") is False:
        return "inactive"
    return "active"


def _market_skip_reason(market: dict) -> Optional[str]:
    """
    Why one nested market is not worth emitting a Bronze record for, or None
    when it is collectable.

    Three rejections, all quiet by design:
      - "closed"       — the event is open but this leg has settled.
      - "inactive"     — an UNNAMED PLACEHOLDER LEG, not a market. Measured live
        2026-08-01 across the whole target set: 108 inactive markets, and every
        single one priced at exactly 0.5000 — `distinct_prices = 1`, against 305
        distinct values across the 1,171 active markets. Their questions are
        template slots with no entity bound ("Will Company A be the largest
        company in the world"). Polymarket writes 0.5/0.5 because there is
        nothing to price; the value is a placeholder, not a measurement, and
        once stored it is indistinguishable from one. The frontend reached the
        same conclusion independently — `server/src/repositories/trending.repository.ts`
        already filters `active !== false`, measured as removing 521 of 1,039
        non-closed markets without dropping a single pickable market.
        **`archived` is deliberately NOT dropped**: its markets carry real,
        distinct prices and real questions (0.49, 0.255, 0.25 on the three seen).
        The filter targets `inactive` specifically, never "not active".
      - "never_traded" — no `outcomePrices`. F9: a missing key means "never
        traded", NOT an error. Every market lacking it has cumulative volume of
        exactly $0 (n=7,224, max $0, zero exceptions).

    Why quiet matters: 1,025 of the 2,421 live markets in the target set (42%)
    are never-traded legs of large candidate fields. Routing those through
    `_fetch_market_prices` would emit a WARNING per market per sweep — 24,600
    warnings a day describing entirely normal data. A log that cries wolf 42%
    of the time is a log nobody reads, which is how the original zero-price
    defect survived 79 days. They are counted in the funnel instead (P2).

    Returning the reason rather than a bare bool is what lets the funnel
    distinguish "settled" from "placeholder" from "never traded" — three very
    different signals if any of the counts moves unexpectedly.
    """
    status = _derive_market_status(market)
    if status == "closed":
        return "closed"
    if status == "inactive":
        return "inactive"
    if not _parse_json_array(market.get("outcomePrices")):
        return "never_traded"
    return None


def _market_is_collectable(market: dict) -> bool:
    """Predicate form of `_market_skip_reason` (plan G3 reads it this way)."""
    return _market_skip_reason(market) is None


def _verify_server_side_filters(events: list[dict]) -> list[str]:
    """
    Confirm the server-side query parameters actually took effect, by inspecting
    the returned set — never by the absence of an HTTP error.

    This exists because of F6: **Gamma silently accepts unknown query params and
    returns HTTP 200.** A renamed or typo'd filter therefore looks exactly like a
    working one. `volume_min`, `closed` and `order` are the three parameters this
    producer's entire coverage story rests on, so each is re-derived from the
    payload we got back:

      - `volume_min` — no event may sit below the threshold (F2).
      - `closed=false` — no event may report `closed=true` (F7: `active` is NOT
        a functioning filter on /events, so `closed` carries the whole load).
      - `order=volume24hr&ascending=false` — `volume24hr` must be non-increasing.
        A silent revert to Gamma's oldest-first default is the exact shape of the
        original defect.

    Returns a list of human-readable complaints; empty means every filter held.
    """
    complaints: list[str] = []
    if not events:
        return complaints

    below = [
        e for e in events
        if float(e.get("volume") or 0) < EVENT_VOLUME_MIN_USD
    ]
    if below:
        complaints.append(
            f"volume_min={EVENT_VOLUME_MIN_USD} did not hold: {len(below)} of "
            f"{len(events)} events are below it (min "
            f"${min(float(e.get('volume') or 0) for e in below):,.0f})"
        )

    still_closed = [e for e in events if e.get("closed") is True]
    if still_closed:
        complaints.append(
            f"closed=false did not hold: {len(still_closed)} of {len(events)} "
            f"events report closed=true"
        )

    volumes = [float(e.get("volume24hr") or 0) for e in events]
    inversions = sum(
        1 for earlier, later in zip(volumes, volumes[1:]) if later > earlier
    )
    if inversions:
        complaints.append(
            f"order=volume24hr&ascending=false did not hold: {inversions} "
            f"descending-order violations across {len(events)} events"
        )

    return complaints


def _attach_parent_event(market: dict, event: dict) -> dict:
    """
    Return a shallow copy of `market` carrying its parent event's identity.

    Why explicit attachment rather than reading `market["events"][0]`: that
    back-reference exists on markets fetched from /markets (F10, the other
    direction) but is not guaranteed on markets arrived at by nesting from
    /events. Carrying the parent forward explicitly means the payload builder
    never has to care which endpoint the market came from, and `event_title` —
    the picker's headline, and the only thing downstream that can NAME the
    parent question — has a single source.

    Underscore-prefixed keys mark these as ours, not Gamma's, so a future
    reader does not mistake them for API fields.
    """
    enriched = dict(market)
    enriched["_parent_event_id"] = str(event.get("id", "") or "")
    enriched["_parent_event_title"] = str(event.get("title", "") or "")
    return enriched


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
        # P3: size of the previous sweep's event set, for shrinkage detection.
        # None on the first sweep — there is nothing to compare against yet.
        self._last_event_count: Optional[int] = None

    # ----------------------------------------------------------
    # Market Discovery (Gamma REST API)
    # ----------------------------------------------------------

    def _fetch_events_page(self, offset: int) -> Optional[list[dict]]:
        """
        Fetch one page of the >=$500k non-closed event set.

        Returns the page's events, or None when Gamma refuses the offset
        (HTTP 422 — the F5 ceiling), which the caller treats as end-of-set
        rather than as an error.

        Parameter notes, all measured (plan §1):
          - `closed=false` is the parameter that actually filters. `active` does
            NOT exist on /events: true / false / absent return byte-identical
            lists (F7). Sending it would look like a filter and be a no-op.
          - `limit` is capped at 100 (F3). We send the cap rather than a larger
            number so the request states what it will really get.
          - `order=volume24hr&ascending=false` is honoured, and an invalid order
            field returns HTTP 422 — so a typo here fails loudly rather than
            silently reverting to oldest-first, which is precisely how the
            original defect collected novelty contracts (F4).
        """
        params = {
            "closed":     "false",
            "limit":      GAMMA_PAGE_LIMIT,
            "volume_min": EVENT_VOLUME_MIN_USD,
            "order":      "volume24hr",
            "ascending":  "false",
            "offset":     offset,
        }
        response, _ = timed_request(
            lambda: requests.get(
                f"{GAMMA_API_BASE}/events",
                params=params,
                headers=_auth_headers(),
                timeout=15,
            )
        )
        if response.status_code == 422:
            return None
        response.raise_for_status()

        events = response.json()
        if isinstance(events, dict):
            # Some API versions wrap in {"data": [...]}
            events = events.get("data", [])
        return events if isinstance(events, list) else []

    def _fetch_target_events(self) -> list[dict]:
        """
        Page the full >=$500k non-closed event set (D1), de-duplicated.

        Why de-duplicate across pages: the set is ordered by `volume24hr`, which
        changes between requests. A market rising or falling mid-sweep shifts the
        window under us and can hand back an event we already hold. Keying on
        event id makes the sweep idempotent regardless.

        Why re-derive membership every run rather than pinning a list (D6):
        cumulative volume cannot decrease, so entry is one-way (~+3/day) and exit
        happens only on resolution (~-11/day) — roughly 8%/day churn against
        90%+ day-over-day overlap. A pinned list would go stale in one direction
        only, silently, which is the worst shape for a coverage bug.
        """
        events: list[dict] = []
        seen: set = set()
        truncated_by: Optional[str] = None
        last_offset = 0

        for offset in range(0, GAMMA_MAX_OFFSET + GAMMA_PAGE_LIMIT, GAMMA_PAGE_LIMIT):
            last_offset = offset
            if offset > GAMMA_MAX_OFFSET:
                # We asked for more than plain offset paging can serve and the
                # previous page was still full — the set is larger than we can
                # reach this way.
                truncated_by = "offset_ceiling"
                break

            page = self._fetch_events_page(offset)
            if page is None:
                truncated_by = "http_422"
                break
            if not page:
                break

            for event in page:
                event_id = str(event.get("id", "") or "")
                if event_id and event_id not in seen:
                    seen.add(event_id)
                    events.append(event)

            # A short page is the natural end of the set (F3: full pages are
            # exactly GAMMA_PAGE_LIMIT).
            if len(page) < GAMMA_PAGE_LIMIT:
                break

        # P3 — a bound that truncates silently is the original defect with a
        # bigger number. The first version of this producer asked for 500 markets,
        # received Gamma's cap of 100, and said nothing; everything downstream then
        # reasoned confidently about a fifth of the data. Any bound that stops the
        # sweep early must therefore say so, with the actual figures.
        if truncated_by is not None:
            logger.warning(
                "[polymarket] Event sweep TRUNCATED by %s at offset=%d after %d "
                "events — the >=$%s set is larger than plain offset paging can "
                "reach (F5). Coverage is incomplete; migrate to /events/keyset "
                "with after_cursor if this persists.",
                truncated_by, last_offset, len(events), f"{EVENT_VOLUME_MIN_USD:,}",
            )

        # A set that shrinks materially between runs is either real resolution
        # churn or a filter that has quietly stopped working. D6 measured ~8%/day
        # churn (~+3 entries, ~-11 exits per day) against 90%+ day-over-day
        # overlap, so at an hourly cadence a normal run moves by well under 1%.
        # Warning on ANY decrease would fire on ordinary churn and train the reader
        # to ignore it — the threshold is what keeps this signal worth reading.
        previous = self._last_event_count
        if previous and len(events) < previous * (1.0 - EVENT_SET_SHRINK_WARN_RATIO):
            logger.warning(
                "[polymarket] Event set SHRANK from %d to %d (-%.0f%%) between "
                "sweeps — beyond the %.0f%% churn threshold. Expect resolution "
                "churn of well under 1%% per hour; a drop this size points at a "
                "filter that stopped matching, not at markets closing.",
                previous, len(events),
                100.0 * (previous - len(events)) / previous,
                100.0 * EVENT_SET_SHRINK_WARN_RATIO,
            )
        self._last_event_count = len(events)

        return events

    def _fetch_active_markets(self) -> list[dict]:
        """
        Discover the markets worth collecting: the >=$500k event set (D1),
        filtered to the TARGET topic set (D3) and to events that have not
        already ended (D5), flattened to their nested markets.

        Why /events and not /markets — the defect this replaces:
            The previous implementation asked /markets for
            `{"active": "true", "closed": "false", "limit": 500}` with no
            ordering, no pagination, and no topic filter. Gamma silently capped
            that at 100 (F3) and defaults to oldest-first, so the collected
            universe was novelty contracts ("Will Jesus Christ return before
            GTA VI?"). Every downstream fix — real prices included — was
            operating on the wrong markets.

        Why the event is the selection unit: tags live on the event (F10), event
        volume is the exact arithmetic sum of its nested markets (F1), and
        market-level filtering severs each leg from its parent's context (D2).

        Returns:
            Nested market dicts, each carrying its parent event's identity via
            `_parent_event_id` / `_parent_event_title`. Never-traded and settled
            legs are already removed, so every element is priceable.
        """
        events = self._fetch_target_events()

        # F6 defence — confirm the server-side params took effect before trusting
        # the set they produced. A silently-ignored filter is indistinguishable
        # from a working one at the HTTP layer.
        for complaint in _verify_server_side_filters(events):
            logger.warning("[polymarket] Gamma filter not honoured — %s", complaint)

        collectable: list[dict] = []
        now = datetime.now(timezone.utc)

        tag_passed = 0
        date_passed = 0
        markets_seen = 0
        skipped: dict = {"closed": 0, "inactive": 0, "never_traded": 0}

        for event in events:
            if not _event_passes_tag_filter(event):
                continue
            tag_passed += 1
            if _event_end_date_passed(event, now):
                continue
            date_passed += 1

            for market in event.get("markets") or []:
                if not isinstance(market, dict):
                    continue
                markets_seen += 1
                reason = _market_skip_reason(market)
                if reason is not None:
                    skipped[reason] += 1
                    continue
                collectable.append(_attach_parent_event(market, event))

        # The funnel. Every stage is reported, every run — the previous
        # implementation logged only a fetched/filtered pair, so a market lost to
        # the topic filter looked identical to one that was never returned, and a
        # collapse at any single stage was invisible. Read left to right, a
        # healthy sweep looks like ~386 -> ~253 -> ~192 -> ~2,421 -> ~1,396
        # (measured 2026-07-30); a zero anywhere is the alarm.
        logger.info(
            "[polymarket] Discovery funnel: %d events fetched -> %d passed tags "
            "-> %d passed endDate -> %d nested markets -> %d collectable "
            "(skipped %d closed, %d inactive, %d never-traded)",
            len(events), tag_passed, date_passed, markets_seen, len(collectable),
            skipped["closed"], skipped["inactive"], skipped["never_traded"],
            # P5: exempt from 1% INFO sampling. This fires once per hourly
            # refresh (24/day), and it is the only evidence that discovery ran
            # and what it selected — at 1% it would surface roughly once every
            # five days, which is indistinguishable from not logging it.
            extra={"always_emit": True},
        )
        return collectable

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
        #
        # P1: prefer the parent attached during the /events flatten. The
        # `market["events"][0]` back-reference is the /markets-direction view of
        # the same link (F10) and is kept as a fallback so this function still
        # works on a standalone Gamma market object — which is exactly how the
        # regression tests exercise it.
        parent_event_id = str(market.get("_parent_event_id", "") or "")
        event_title = str(market.get("_parent_event_title", "") or "")
        if not parent_event_id or not event_title:
            events = market.get("events")
            if isinstance(events, list) and events and isinstance(events[0], dict):
                parent_event_id = parent_event_id or str(events[0].get("id", ""))
                event_title = event_title or str(events[0].get("title", "") or "")

        # Shared with the selection filter (_market_skip_reason) so the state we
        # filter on and the state we report can never disagree.
        market_status = _derive_market_status(market)

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
            # P8: keyed by outcome label, mirroring `outcome_prices`. The CLOB
            # history call needs the YES token by name, not by position.
            "clob_token_ids":  _extract_clob_token_ids(market),
            "parent_event_id": parent_event_id,
            # P1: without the event title nothing downstream can NAME the parent
            # question — the picker's headline and the catalog's human label.
            "event_title":     event_title,
            "end_date_iso":    str(market.get("endDateIso", "") or ""),
            "market_status":   market_status,
            "volume_24h_usd":  volume_24h,
            # F8: on MARKET objects `volume` and `liquidity` arrive as strings;
            # `volumeNum` / `liquidityNum` are the float fields. (On EVENT objects
            # `volume` is already a float — which is what the server-side
            # `volume_min` threshold filters on.) Falling back to the string form
            # keeps this correct against either shape.
            "liquidity_usd":   float(
                market.get("liquidityNum") or market.get("liquidity", 0) or 0
            ),
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
            zero_priced = 0
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
                    if price_payload["price"] == 0.0:
                        zero_priced += 1

            # The zero-price tripwire. A single market at 0.0 is ordinary — a
            # resolved-NO leg prices there honestly, which is why the Silver
            # range guard accepts it. An ENTIRE sweep at 0.0 is not a market
            # state, it is an extraction failure: exactly the shape of the defect
            # that put 93,607 rows of zeros into the vault over 79 days while
            # every component reported success. Checking the population rather
            # than the individual value is what separates the two, and it costs
            # one counter.
            if emitted and zero_priced == emitted:
                logger.warning(
                    "[polymarket] ALL %d emitted prices this sweep are 0.0 — "
                    "that is an extraction failure, not a market state. Check "
                    "that Gamma still returns `outcomePrices` and that the "
                    "affirmative outcome is still labelled %r.",
                    emitted, AFFIRMATIVE_LABEL,
                )

            # C7: the loop sleeps AFTER the sweep, so the real cadence is
            # "sweep duration + PRICE_POLL_SEC". Logging the duration makes that
            # visible instead of letting the cadence drift silently.
            logger.info(
                "[polymarket] Price sweep: %d emitted, %d skipped, %d total "
                "in %.1fs (next sweep in %ds)",
                emitted, self._skipped_markets, len(markets),
                time.monotonic() - started, PRICE_POLL_SEC,
                # P5: same reasoning as the discovery funnel — one record per
                # hourly sweep, and the only place emitted-vs-skipped is visible.
                extra={"always_emit": True},
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

        LOAD WARNING before re-enabling (P1 blast radius): this loop makes one
        HTTP call PER MARKET per cycle, and P1 grew `_active_markets` from the
        ~100 markets Gamma silently returned to ~1,396. Re-enabling as-is takes
        this from ~100 calls per 20-min cycle to ~1,396 — roughly 100k requests
        a day. The enum fix must therefore land together with a bound (top-N
        markets by volume, a longer cadence, or both). It is no longer the
        one-line re-enable the paragraph above implies.
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
