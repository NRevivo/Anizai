"""
agent/tools/polymarket_api.py — the hub's only outbound Polymarket calls.

Two functions, both public-data-only (no auth — the Polymarket producer is
unauthenticated by design and these read the same public endpoints):

    fetch_market_by_condition_id()  — A3's safety net. The user picked a market
                                      the vault has not collected yet; ask
                                      Polymarket directly rather than refusing.
    fetch_price_history()           — the frontend price chart's data source.

Why this lives in agent/tools/ next to the vault wrappers:
    hub-principles P2 governs VAULT access — "all vault reads go through
    agent/tools/". These are not vault reads, but the same reasoning applies:
    one module owns one external boundary, so a schema or endpoint change has
    exactly one site. Nodes and retrieval agents never call `requests` directly.

Why these calls are strictly optional, and why that shapes every decision here:
    Neither function is on the critical path of producing a forecast. The market
    lookup covers a market we have not collected; the price history decorates a
    chart. A forecast with no chart is a forecast. A forecast that hangs, or that
    fails because a third-party API was slow, is not. So both functions:

      - take an explicit (connect, read) timeout tuple;
      - make exactly ONE attempt, with no retry loop — a retry would multiply
        the ceiling inside a budget that is already shared (see below);
      - NEVER raise. Every failure path returns the empty value (None / []) and
        logs, so the caller's degrade path is the ordinary path, not an
        exception handler.

    What the timeout does NOT do — read this before trusting a number here:
        `requests` has no wall-clock deadline. A scalar `timeout=5` means 5s for
        the connect phase and, separately, 5s for the read phase — where the read
        timeout bounds the gap BETWEEN socket reads, not the total. A server that
        dribbles bytes can hold a nominally-5s call open far longer. An earlier
        revision of this module called POLYMARKET_API_TIMEOUT_S "a HARD ceiling";
        it never was, and a measured 20s+ response under a nominal 5s setting was
        sitting in this sprint's own notes as evidence before it was read
        correctly.

        The wall-clock guarantee lives in the CALLER
        (market_bridge._build_polymarket_live), which measures elapsed time with
        time.monotonic() across both calls and declines to start the second one
        when the budget is spent. These functions bound the phases; the caller
        bounds the total.

    Budget context (hub-principles G4): both are called from market_bridge,
    which runs inside vault_query's per-agent future capped at
    PER_AGENT_TIMEOUT_S (15s, vault_query.py:69) — a budget shared with the FRED
    anomaly scan, one Google Trends fetch per entity, and the vault reads. An
    unbounded or retrying external call here does not degrade the market slice;
    it fails the entire forecast, because vault_query is fail-fast.

F6 applies to these endpoints too — verified live 2026-07-30:
    A bogus `condition_ids` returns **HTTP 200 with an empty list**, and a bogus
    CLOB token returns **HTTP 200 with an empty history**. Absence is signalled
    in the BODY, never by a status code, so both functions verify what came back
    instead of trusting a 200. The market lookup additionally re-checks that the
    returned `conditionId` equals the one requested.

Endpoint shapes, all verified live 2026-07-30:
    GET {GAMMA}/markets?condition_ids=<id>
        -> [ {...market...} ]   (a LIST of 0 or 1; the /markets/<id> path form
                                 returns HTTP 422 and must not be used)
    GET {CLOB}/prices-history?market=<token_id>&interval=max&fidelity=<min>
        -> {"history": [{"t": <unix seconds>, "p": <float>}, ...]}

    `interval=max` is a ROLLING 30-DAY WINDOW, not the market's full life:
    markets created 2025-05-02 return exactly 30.0 days. This corrects the
    plan's F12, which recorded it as full-life. It happens to align exactly with
    market_bridge.POLYMARKET_TIME_SERIES_HOURS (720h), so the CLOB series and
    the vault-derived fallback cover the same span.

Spec references:
    - docs/A_pipeline/plans/polymarket_completion.md §1 F12 (corrected here), A3
    - .claude/skills/hub-principles/SKILL.md G4 (bounded external calls)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import requests

from agent.config import settings

logger = logging.getLogger(__name__)


# ==========================================================
# Endpoints
# ==========================================================
# Hub-local constants, NOT imported from the pipeline's config/ package.
# hub-principles P5: the two domains keep distinct configuration surfaces, so
# the hub does not reach across into `ingestion.polymarket_producer` for these
# even though the same literals appear there. Duplicating two URLs is the lesser
# cost against a Domain-B module importing Domain-A code.
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"

# The widest window CLOB serves (a rolling 30 days — see module docstring).
_HISTORY_INTERVAL = "max"


def _timeout(read_s: Optional[float]) -> tuple:
    """
    Build the (connect, read) tuple for one request.

    Always a tuple, never a scalar: a scalar sets both phases to the same value
    and reads as a total budget it does not provide. The connect half is capped
    separately and short, so an unreachable host fails in ~2s rather than
    consuming the read allowance on a handshake that will not complete.

    The connect timeout is never allowed to exceed the read budget — when the
    caller has almost no budget left, spending it all on connecting is pointless.
    """
    read = read_s if read_s and read_s > 0 else settings.POLYMARKET_API_TIMEOUT_S
    return (min(settings.POLYMARKET_API_CONNECT_TIMEOUT_S, read), read)

# The outcome label whose price is the market's probability. Selected by LABEL
# everywhere, never by position — Gamma does not guarantee outcome order, and a
# market listed ["No", "Yes"] read at index 0 reports the complement.
AFFIRMATIVE_LABEL = "Yes"


# ==========================================================
# Gamma wire-shape decoding
# ==========================================================
# These mirror `_parse_json_array` / `_pair_by_outcome_label` in
# ingestion/polymarket_producer.py. The duplication is DELIBERATE and is the
# lesser of two evils: Domain B importing Domain A would drag the pipeline's
# Kafka client and config package into the hub process, breaking the domain
# isolation both principles files are built around (hub-principles P5/P6). What
# is duplicated is ~10 lines of wire-format decoding against a third-party API
# shape, not business logic — and each side has its own test coverage.

def _parse_json_array(raw) -> list:
    """
    Gamma returns `outcomes`, `outcomePrices` and `clobTokenIds` as JSON-encoded
    STRINGS, not arrays (plan F8). Accepts a real list too, so the helper
    survives an upstream shape change. Returns [] rather than raising — the
    caller treats an unreadable market as unusable, which is the honest outcome.
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


def _pair_by_outcome_label(market: dict, value_field: str) -> dict:
    """
    Pair each outcome LABEL with its entry in `value_field`, or {} when the two
    arrays cannot be aligned. Doing this once, by label, is what keeps the
    "read index 0 and hope" trap out of every caller.
    """
    labels = [str(x).strip() for x in _parse_json_array(market.get("outcomes"))]
    values = _parse_json_array(market.get(value_field))
    if not labels or len(labels) != len(values):
        return {}
    return dict(zip(labels, values))


def _normalise_market(market: dict) -> dict:
    """
    Decode a raw Gamma market object into the same key names the producer emits
    on the Bronze payload (and therefore the same names that reach the vault's
    `metadata_extension`).

    Why normalise here rather than returning the raw object: it means
    market_bridge reads `outcome_prices` / `clob_token_ids` identically whether
    the market came from a stored vault row or from this live lookup. Without it,
    every consumer would need to know which of the two shapes it was holding —
    and the live path, being the rare one, is exactly where that branch would rot
    untested.
    """
    prices: dict = {}
    for label, raw_price in _pair_by_outcome_label(market, "outcomePrices").items():
        try:
            prices[label] = float(raw_price)
        except (TypeError, ValueError):
            # An unpriceable outcome yields no entry rather than a 0.0 sentinel;
            # a sentinel is a default served as a measurement.
            continue

    tokens = {
        label: str(token)
        for label, token in _pair_by_outcome_label(market, "clobTokenIds").items()
    }

    if market.get("closed") is True:
        status = "closed"
    elif market.get("archived") is True:
        status = "archived"
    elif market.get("active") is False:
        status = "inactive"
    else:
        status = "active"

    return {
        "condition_id":   str(market.get("conditionId", "") or ""),
        "market_id":      str(market.get("id", "") or ""),
        "question":       str(market.get("question", "") or ""),
        "outcome_prices": prices,
        "clob_token_ids": tokens,
        "end_date_iso":   str(market.get("endDateIso", "") or ""),
        "end_date":       str(market.get("endDate", "") or ""),
        "market_status":  status,
    }


# ==========================================================
# A3 — live market lookup
# ==========================================================
def fetch_market_by_condition_id(
    condition_id: str, *, timeout_s: Optional[float] = None,
) -> Optional[dict]:
    """
    Look up a single live market by its Polymarket `conditionId`.

    A3's safety net: the frontend supplied a conditionId, but no vault row
    carries it. That is an ordinary, expected state — the producer collects on
    an hourly sweep over a filtered universe, so a market can be pickable in the
    UI minutes before we have ever stored it, and can be pickable forever if it
    falls outside the collector's topic filter. Refusing there would tell the
    user their own selection does not exist.

    Args:
        condition_id: the `conditionId` from the market picker. Empty or
                      whitespace returns None without touching the network.
        timeout_s:    override the default ceiling. Used by the A3 caller, which
                      shares one combined budget across this call and the
                      history call that follows it.

    Returns:
        A normalised market dict (see `_normalise_market` — same key names the
        producer emits, so callers need not know this came from the live path),
        or None when the market does not exist, the request fails, or the
        response does not actually contain the market we asked for. Never raises.
    """
    condition_id = (condition_id or "").strip()
    if not condition_id:
        return None

    try:
        response = requests.get(
            f"{GAMMA_API_BASE}/markets",
            params={"condition_ids": condition_id},
            timeout=_timeout(timeout_s),
        )
        if response.status_code != 200:
            logger.warning(
                "polymarket_api: market lookup returned HTTP %s for condition_id=%s",
                response.status_code, condition_id,
            )
            return None
        markets = response.json()
    except (requests.RequestException, ValueError) as exc:
        # ValueError covers a 200 carrying unparseable JSON.
        logger.warning(
            "polymarket_api: market lookup failed for condition_id=%s — %r "
            "(degrading to no-market; the forecast continues)",
            condition_id, exc,
        )
        return None

    if isinstance(markets, dict):
        markets = markets.get("data", [])
    if not isinstance(markets, list) or not markets:
        # The F6 case: HTTP 200, empty list. The market genuinely is not there.
        logger.info(
            "polymarket_api: no live market for condition_id=%s", condition_id,
        )
        return None

    market = markets[0]
    if not isinstance(market, dict):
        return None

    # Confirm we got what we asked for rather than whatever the filter matched.
    # An ignored/renamed query param (F6) returns HTTP 200 and page 1 of
    # everything, which would otherwise resolve the user's question against an
    # arbitrary unrelated market — a wrong benchmark presented as theirs.
    returned = str(market.get("conditionId", "") or "")
    if returned != condition_id:
        logger.warning(
            "polymarket_api: condition_ids filter not honoured — asked for %s, "
            "got %s. Discarding rather than benchmarking against the wrong "
            "market.",
            condition_id, returned,
        )
        return None

    return _normalise_market(market)


# ==========================================================
# OQ1 — CLOB price history
# ==========================================================
def fetch_price_history(
    token_id: str, *, timeout_s: Optional[float] = None,
) -> list[dict]:
    """
    Fetch a market's price history from CLOB, in the in-state shape.

    Why CLOB and not our own momentum_vault:
        The vault only holds what we have collected. Immediately after a database
        wipe that is a handful of points, so the chart would be technically
        working and visually empty for the first days of a collection run. CLOB
        serves a rolling 30 days on the first request, independent of when we
        started collecting.

    Args:
        token_id: the CLOB token id for the outcome to chart. This MUST be the
                  affirmative ("Yes") token — `clob_token_ids` is keyed by
                  outcome label precisely so the caller can name it instead of
                  picking index 0, which would chart the complement and invert a
                  7% market into 93%.

    Returns:
        `[{"timestamp": <ISO-8601 UTC str>, "value": <float>}, ...]` ascending by
        time — the same shape `market_bridge._price_history_point` produces from
        vault rows, so `write_to_firestore._shape_prediction_series` and the
        partner-facing document shape are both entirely unchanged by this
        source switch. Empty list on any failure. Never raises.
    """
    token_id = (token_id or "").strip()
    if not token_id:
        return []

    try:
        response = requests.get(
            f"{CLOB_API_BASE}/prices-history",
            params={
                "market": token_id,
                "interval": _HISTORY_INTERVAL,
                "fidelity": settings.POLYMARKET_HISTORY_FIDELITY_MIN,
            },
            timeout=_timeout(timeout_s),
        )
        if response.status_code != 200:
            logger.warning(
                "polymarket_api: price history returned HTTP %s for token=%s…",
                response.status_code, token_id[:12],
            )
            return []
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning(
            "polymarket_api: price history failed for token=%s… — %r "
            "(falling back to vault-derived history)",
            token_id[:12], exc,
        )
        return []

    raw_points = (body or {}).get("history") if isinstance(body, dict) else None
    if not isinstance(raw_points, list) or not raw_points:
        # F6 again: a bogus token returns HTTP 200 with an empty history.
        logger.info(
            "polymarket_api: empty price history for token=%s…", token_id[:12],
        )
        return []

    shaped: list[dict] = []
    for point in raw_points:
        if not isinstance(point, dict):
            continue
        try:
            # `t` is unix EPOCH SECONDS; `p` is the price as a float.
            stamp = datetime.fromtimestamp(float(point["t"]), tz=timezone.utc)
            value = float(point["p"])
        except (KeyError, TypeError, ValueError, OSError, OverflowError):
            # One malformed point must not discard the other ~700.
            continue
        shaped.append({"timestamp": stamp.isoformat(), "value": value})

    return shaped
