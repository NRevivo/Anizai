"""
Polymarket HTTP client — the only module in this package that performs I/O.

Kept deliberately thin. It knows about URLs, timeouts, pagination, and
transient failures; it knows nothing about cohorts, categories, or what a
resolution means. That separation is what lets `discover.py` and `resolve.py`
be pure functions with fixture-driven tests and no network mocking.

Both APIs are public and require no credentials for the endpoints used here:
    Gamma  GET /markets                   — market discovery
    Gamma  GET /markets?slug=<slug>       — manual-add lookup by slug
    CLOB   GET /markets/{condition_id}    — resolution polling

Retry policy: three attempts with 0.5s -> 1s -> 2s backoff on connection
errors, timeouts, and 5xx. 4xx is not retried — a 404 on a condition_id means
the market is gone, and retrying it just delays the correct conclusion.

References:
    - calibration_plan.md §3 D1 (resolution via CLOB REST)
    - ingestion/polymarket_producer.py (the endpoint shapes mirrored here)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests

from calibration import config

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_BACKOFF_SEC = (0.5, 1.0, 2.0)

# What we ask for per page. Gamma caps this server-side at 100 (observed
# 2026-07-25), which is why pagination advances by records received rather
# than records requested — see `_walk`.
_GAMMA_PAGE_LIMIT = 500

# Gamma refuses offsets beyond roughly 2000 with a 422 telling us to use
# /markets/keyset for deeper pagination (measured 2026-07-25: offset=2000 is
# fine, offset=3000 is a 422). Hitting it must end the walk gracefully, not
# abort discovery — a run that crashes on page 21 loses the twenty pages it
# already had.
_OFFSET_CEILING_MARKER = "offset too large"


class PolymarketError(RuntimeError):
    """Raised when Polymarket cannot be reached or returns an unusable response."""


def _get(url: str, params: Optional[dict] = None) -> Any:
    """
    GET with retry on transient failure.

    Returns the decoded JSON body. Raises PolymarketError after the final
    attempt, or immediately on a non-retryable 4xx.
    """
    last_error: Optional[Exception] = None

    for attempt in range(_MAX_ATTEMPTS):
        try:
            response = requests.get(
                url, params=params, timeout=config.POLYMARKET_HTTP_TIMEOUT_SEC
            )
            if 400 <= response.status_code < 500:
                raise PolymarketError(
                    f"{response.status_code} from {url} — not retrying "
                    f"(body: {response.text[:200]})"
                )
            response.raise_for_status()
            return response.json()
        except PolymarketError:
            raise
        except Exception as exc:  # noqa: BLE001 — retry on anything transient
            last_error = exc
            if attempt < _MAX_ATTEMPTS - 1:
                delay = _BACKOFF_SEC[attempt]
                logger.warning(
                    "[polymarket] %s failed (attempt %d/%d): %s — retrying in %.1fs",
                    url, attempt + 1, _MAX_ATTEMPTS, exc, delay,
                )
                time.sleep(delay)

    raise PolymarketError(
        f"GET {url} failed after {_MAX_ATTEMPTS} attempts: {last_error}"
    ) from last_error


def fetch_active_markets(
    limit: int = _GAMMA_PAGE_LIMIT,
    offset: int = 0,
    **filters: object,
) -> list[dict]:
    """
    Fetch one page of active, unclosed markets from the Gamma API.

    Args:
        limit:   page size (Gamma caps this server-side at 100).
        offset:  pagination offset.
        filters: extra server-side query params. Gamma supports
                 `end_date_min`, `end_date_max`, `volume_num_min`,
                 `liquidity_num_min`, `order`, and `ascending` (all verified
                 live 2026-07-25). Pushing a filter to the server is strictly
                 better than filtering client-side, because the offset ceiling
                 means we can never page through the whole exchange anyway.

    Returns:
        Raw market dicts, exactly as Gamma returned them. Deciding what
        qualifies is `discover.py`'s job, not this module's.
    """
    params: dict[str, object] = {
        "active": "true",
        "closed": "false",
        "limit": limit,
        "offset": offset,
        # REQUIRED. Without include_tag the response has no `tags` field at
        # all — not an empty list, the key is simply absent (verified live
        # 2026-07-25). Every market then fails categorisation and discovery
        # returns zero candidates while reporting that it examined thousands
        # of markets, which is the worst kind of wrong: a confident, detailed,
        # empty answer.
        "include_tag": "true",
    }
    params.update({k: v for k, v in filters.items() if v is not None})

    body = _get(f"{config.POLYMARKET_GAMMA_API}/markets", params=params)
    markets = body if isinstance(body, list) else body.get("data", [])
    logger.info("[polymarket] Gamma returned %d markets (offset=%d)", len(markets), offset)
    return markets


def fetch_active_markets_paged(max_markets: int = 2_000) -> list[dict]:
    """
    Walk Gamma's pagination until it runs out or `max_markets` is reached.

    Two upstream behaviours this has to survive, both observed live on
    2026-07-25:

    1. **Gamma caps `limit` server-side.** Asking for 500 returns 100. So the
       page is advanced by the number of records actually received, never by
       the number requested — advancing by the request size would skip four
       out of every five markets. For the same reason, "a short page means the
       last page" is wrong: every page is short relative to what we asked for.
       Termination is on an *empty* page, not a short one.

    2. **An endpoint that ignores `offset`** would otherwise loop forever
       re-collecting page one. Condition ids are tracked and the walk stops as
       soon as a page contributes nothing new.

    3. **An offset ceiling around 2000**, past which Gamma returns 422. The
       walk stops and keeps what it has.

    The `max_markets` cap bounds a single run's work. When it bites it is
    logged — a silent truncation would read as "we looked at everything".

    Note this cannot reach the whole exchange: between the offset ceiling and
    the default ordering, an unfiltered walk sees an arbitrary ~2000 markets.
    Discovery therefore uses `fetch_markets_in_window` instead. This function
    remains for diagnostics and for the manual-add path.
    """
    return _walk(max_markets=max_markets)


def fetch_markets_in_window(
    end_date_min: str,
    end_date_max: str,
    volume_min: float,
    max_markets: int = 400,
) -> list[dict]:
    """
    Fetch markets resolving inside a date window, above a volume floor.

    This is how discovery actually gets its candidates. Asking the server for
    the cohort's window is not merely an optimisation over scanning everything
    — it is the only approach that works at all, because Gamma's offset
    ceiling (~2000) makes a full scan impossible, and an unfiltered scan
    truncated at 2000 returns whatever the default ordering happens to surface.
    The first live run did exactly that and yielded 4 candidates out of 2000
    markets, 1652 of which were rejected purely for being outside the cohort
    windows we could have asked the server about.

    Results come back ordered by volume descending, so the first page already
    holds the most liquid — and therefore least noisy — markets in the window.

    Args:
        end_date_min: ISO timestamp, inclusive lower bound on resolution date.
        end_date_max: ISO timestamp, inclusive upper bound.
        volume_min:   cumulative volume floor in USD.
        max_markets:  per-window cap.
    """
    return _walk(
        max_markets=max_markets,
        end_date_min=end_date_min,
        end_date_max=end_date_max,
        volume_num_min=volume_min,
        order="volumeNum",
        ascending="false",
    )


def _walk(max_markets: int, **filters: object) -> list[dict]:
    """
    Page through Gamma, deduplicating and stopping safely.

    Shared by the windowed and unfiltered fetches. Handles the three upstream
    behaviours documented on `fetch_active_markets_paged`, plus the offset
    ceiling.
    """
    collected: list[dict] = []
    seen: set[str] = set()
    offset = 0
    pages = 0

    while len(collected) < max_markets:
        try:
            page = fetch_active_markets(
                limit=_GAMMA_PAGE_LIMIT, offset=offset, **filters
            )
        except PolymarketError as exc:
            if _OFFSET_CEILING_MARKER in str(exc):
                logger.warning(
                    "[polymarket] Hit Gamma's offset ceiling at offset=%d — "
                    "keeping the %d markets collected so far. Narrow the query "
                    "(date window / volume floor) to reach deeper results.",
                    offset, len(collected),
                )
                break
            raise
        if not page:
            break
        pages += 1

        fresh = []
        for market in page:
            key = str(
                market.get("conditionId")
                or market.get("condition_id")
                or market.get("id")
                or ""
            )
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            fresh.append(market)

        if not fresh:
            logger.warning(
                "[polymarket] Page at offset=%d contained no new markets — "
                "stopping. The endpoint may be ignoring `offset`.", offset,
            )
            break

        collected.extend(fresh)
        # Advance by what the server actually returned, not what we asked for.
        offset += len(page)

    logger.info(
        "[polymarket] Collected %d unique markets across %d page(s)",
        len(collected), pages,
    )
    if len(collected) >= max_markets:
        logger.warning(
            "[polymarket] Discovery truncated at max_markets=%d — there may be "
            "further eligible markets that were not considered.",
            max_markets,
        )
    return collected[:max_markets]


def fetch_market_by_slug(slug: str) -> Optional[dict]:
    """
    Look up a single market by its Polymarket slug. Used by manual-add.

    Returns None when no market matches, so the caller can raise a message
    naming the slug rather than surfacing an IndexError.
    """
    if not slug:
        raise ValueError("slug must be a non-empty string")

    body = _get(
        f"{config.POLYMARKET_GAMMA_API}/markets",
        params={"slug": slug, "include_tag": "true"},
    )
    markets = body if isinstance(body, list) else body.get("data", [])
    return markets[0] if markets else None


def fetch_clob_market(condition_id: str) -> Optional[dict]:
    """
    Fetch a market's CLOB record — the resolution source of truth (plan D1).

    Returns None on 404: a condition_id that CLOB no longer knows about is an
    expected state for an old market, not an error worth propagating.
    """
    if not condition_id:
        raise ValueError("condition_id must be a non-empty string")

    try:
        return _get(f"{config.POLYMARKET_CLOB_API}/markets/{condition_id}")
    except PolymarketError as exc:
        if "404" in str(exc):
            logger.info("[polymarket] CLOB 404 for condition_id=%s", condition_id)
            return None
        raise
