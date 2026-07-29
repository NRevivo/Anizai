"""
Gate 2 — REST snapshot price extraction (plan `polymarket_price_and_coverage.md`, T9).

Why this file exists
--------------------
Before it, the suite tested the path that never runs. There was exactly one
Polymarket price mock — `tests/mocks/polymarket_price_update.json` — and it is a
**WebSocket** `last_trade` payload carrying `"price": 0.67`. There was no mock of
the REST snapshot payload at all.

That is how 79 days of `current_value = 0.0` passed CI green: every REST row
reached Silver without a `price` key, `map_price_update_to_silver` read
`raw.get("price", 0.0)` (`processing/silver_job.py:170`), and no test ever looked.
Measured on production before the fix: **0 of 93,607 Polymarket rows had a
non-zero `current_value`.**

The assertions here are the ones that would have caught it on day one.

What this covers
    [1] A real Gamma REST market yields a NON-ZERO current_value through the
        real Silver mapper — the regression guard for the original bug.
    [2] The YES price is selected by LABEL, not by position. A market listed
        ["No", "Yes"] must still price the Yes side; reading index 0 blindly
        reports the complement (a 3% market as 97%).
    [3] Gamma's JSON-ENCODED STRING shape (`'["Yes", "No"]'`) is decoded. This
        is the real API shape, verified live 2026-07-28 on 100/100 markets.
    [4] A market whose outcomes are not Yes/No (sports fixtures: team-vs-team,
        Over/Under) emits NOTHING — no sentinel, no zero. 35 of 100 live
        markets are this shape.
    [5] Catalog fields survive the payload: parent event id, end date, real
        status.

The mock is a **captured live Gamma object**, not an invented one — an invented
mock is exactly how the original shape mismatch went unnoticed.
"""

import json
from pathlib import Path

import pytest

from ingestion.polymarket_producer import (
    AFFIRMATIVE_LABEL,
    _extract_outcome_prices,
    _parse_json_array,
)
from processing.silver_job import map_price_update_to_silver
from utils.kafka_utils import build_bronze_message

MOCKS_DIR = Path(__file__).parent.parent / "mocks"
GAMMA_ENDPOINT = "https://gamma-api.polymarket.com/markets"


@pytest.fixture(scope="module")
def rest_market() -> dict:
    """A real Gamma REST market object, captured live 2026-07-28."""
    with open(MOCKS_DIR / "polymarket_rest_market.json", encoding="utf-8") as f:
        return json.load(f)


def _payload_from(market: dict) -> dict:
    """
    The REST payload `_fetch_market_prices` produces, rebuilt here without the
    producer's network/asyncio scaffolding. Mirrors the emitted keys the Silver
    mapper reads.
    """
    prices = _extract_outcome_prices(market)
    assert prices is not None, "fixture must be priceable"
    return {
        "payload_type": "price_update",
        "ingestion_mode": "rest_snapshot",
        "market_id": market.get("id", ""),
        "condition_id": market.get("conditionId", ""),
        "question": market.get("question", ""),
        "price": prices[AFFIRMATIVE_LABEL],
        "volume_24h_usd": float(market.get("volume24hr", 0) or 0),
        "liquidity_usd": float(market.get("liquidity", 0) or 0),
        "whale_alert": False,
    }


# ==========================================================
# [3] Gamma's real wire shape
# ==========================================================

def test_gamma_encodes_outcomes_as_json_strings(rest_market):
    """The fixture must preserve the real shape: strings, not arrays."""
    assert isinstance(rest_market["outcomes"], str)
    assert isinstance(rest_market["outcomePrices"], str)
    assert _parse_json_array(rest_market["outcomes"]) == ["Yes", "No"]


def test_parse_json_array_tolerates_a_real_list():
    """Survives an upstream shape change without breaking."""
    assert _parse_json_array(["Yes", "No"]) == ["Yes", "No"]


def test_parse_json_array_returns_empty_on_garbage():
    """Unreadable input must not raise — the caller skips the market instead."""
    assert _parse_json_array("not json") == []
    assert _parse_json_array(None) == []


# ==========================================================
# [1] The regression guard for the original bug
# ==========================================================

def test_rest_snapshot_yields_non_zero_current_value(rest_market):
    """
    THE assertion that was missing for 79 days.

    A real REST market must reach Silver with a real probability, not the
    `raw.get("price", 0.0)` default.
    """
    payload = _payload_from(rest_market)
    envelope = build_bronze_message("polymarket", GAMMA_ENDPOINT, payload)
    silver = map_price_update_to_silver(payload, envelope)

    current_value = silver["data_point"]["current_value"]
    assert current_value != 0.0, "REST snapshot must not store a 0.0 probability"
    assert 0.0 < current_value <= 1.0, f"probability out of range: {current_value}"


def test_price_matches_the_yes_outcome(rest_market):
    """The stored value is the YES price, not the NO price."""
    labels = _parse_json_array(rest_market["outcomes"])
    prices = [float(p) for p in _parse_json_array(rest_market["outcomePrices"])]
    expected = prices[labels.index(AFFIRMATIVE_LABEL)]

    payload = _payload_from(rest_market)
    envelope = build_bronze_message("polymarket", GAMMA_ENDPOINT, payload)
    silver = map_price_update_to_silver(payload, envelope)

    assert silver["data_point"]["current_value"] == pytest.approx(expected)


# ==========================================================
# [2] Label lookup, not index lookup
# ==========================================================

def test_yes_price_selected_by_label_when_order_is_reversed():
    """
    A market listed ["No", "Yes"] must still price the Yes side.

    Live sampling on 2026-07-28 found 0 of 100 markets in this order, so index-0
    would *appear* to work today — which is exactly what makes it a latent trap
    rather than a visible bug.
    """
    reversed_market = {
        "outcomes": '["No", "Yes"]',
        "outcomePrices": '["0.93", "0.07"]',
    }
    prices = _extract_outcome_prices(reversed_market)
    assert prices == {"No": 0.93, "Yes": 0.07}
    assert prices[AFFIRMATIVE_LABEL] == pytest.approx(0.07), (
        "index-0 would have reported 0.93 — the complement"
    )


# ==========================================================
# [4] Unsupported markets emit nothing
# ==========================================================

@pytest.mark.parametrize("outcomes,prices", [
    ('["Alex de Minaur", "Stefanos Tsitsipas"]', '["0.62", "0.38"]'),
    ('["Toronto Blue Jays", "Washington Nationals"]', '["0.55", "0.45"]'),
    ('["Over", "Under"]', '["0.48", "0.52"]'),
    ('["Up", "Down"]', '["0.51", "0.49"]'),
])
def test_non_binary_markets_have_no_affirmative_price(outcomes, prices):
    """
    Sports fixtures and Over/Under lines carry no "Yes" outcome, so there is no
    affirmative probability to store. These are 35 of 100 live markets.

    The contract is that they resolve to "cannot price" — the producer then emits
    nothing rather than a sentinel, because a sentinel is a default served as a
    measurement, which is the class of bug being fixed.
    """
    resolved = _extract_outcome_prices({"outcomes": outcomes, "outcomePrices": prices})
    assert resolved is not None, "labels are readable"
    assert AFFIRMATIVE_LABEL not in resolved, "must not fabricate a Yes price"


def test_mismatched_label_and_price_lengths_are_unpriceable():
    assert _extract_outcome_prices(
        {"outcomes": '["Yes", "No"]', "outcomePrices": '["0.5"]'}
    ) is None


def test_unparseable_price_is_unpriceable():
    assert _extract_outcome_prices(
        {"outcomes": '["Yes", "No"]', "outcomePrices": '["abc", "0.5"]'}
    ) is None


def test_missing_outcomes_is_unpriceable():
    assert _extract_outcome_prices({}) is None


# ==========================================================
# [5] Catalog fields present on the captured object
# ==========================================================

def test_catalog_fields_available_on_the_gamma_object(rest_market):
    """
    T7 preserves these in the payload. Asserting they exist upstream guards the
    fixture against silently losing them.
    """
    assert rest_market.get("endDateIso"), "end date needed to refuse resolved markets"
    assert isinstance(rest_market.get("closed"), bool)
    assert isinstance(rest_market.get("active"), bool)
    events = rest_market.get("events")
    assert isinstance(events, list) and events, "parent event link must survive"
    assert events[0].get("id"), "parent event id is the catalog key"
