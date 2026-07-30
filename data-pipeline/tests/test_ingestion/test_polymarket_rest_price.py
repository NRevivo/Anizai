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
    PolymarketProducer,
    _extract_clob_token_ids,
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


@pytest.fixture()
def producer() -> PolymarketProducer:
    """
    A producer with no Kafka connection.

    `__init__` opens a Kafka producer, which these tests neither have nor need —
    `_fetch_market_prices` is a pure transform over a Gamma object. Bypassing
    __init__ and setting only the counters it touches keeps the REAL function
    under test instead of a re-implementation of it (G1).
    """
    p = PolymarketProducer.__new__(PolymarketProducer)
    p._skipped_markets = 0
    p._last_event_count = None
    return p


def _payload_from(producer: PolymarketProducer, market: dict) -> dict:
    """
    The REST payload the producer actually emits — obtained by CALLING
    `_fetch_market_prices`, not by rebuilding it.

    G1: this helper used to hand-assemble the payload dict. That made every
    assertion below a test of `_extract_outcome_prices` → mapper, and left the
    seam that matters — `_fetch_market_prices` → mapper — completely uncovered.
    Renaming the `price` key in the producer would have kept this file green
    while putting zeros back into the vault, which is the exact class of defect
    the file exists to catch.
    """
    payload = producer._fetch_market_prices(market)
    assert payload is not None, "fixture must be priceable"
    return payload


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

def test_rest_snapshot_yields_non_zero_current_value(producer, rest_market):
    """
    THE assertion that was missing for 79 days.

    A real REST market must reach Silver with a real probability, not the
    `raw.get("price", 0.0)` default.
    """
    payload = _payload_from(producer, rest_market)
    envelope = build_bronze_message("polymarket", GAMMA_ENDPOINT, payload)
    silver = map_price_update_to_silver(payload, envelope)

    current_value = silver["data_point"]["current_value"]
    assert current_value != 0.0, "REST snapshot must not store a 0.0 probability"
    assert 0.0 < current_value <= 1.0, f"probability out of range: {current_value}"


def test_price_matches_the_yes_outcome(producer, rest_market):
    """The stored value is the YES price, not the NO price."""
    labels = _parse_json_array(rest_market["outcomes"])
    prices = [float(p) for p in _parse_json_array(rest_market["outcomePrices"])]
    expected = prices[labels.index(AFFIRMATIVE_LABEL)]

    payload = _payload_from(producer, rest_market)
    envelope = build_bronze_message("polymarket", GAMMA_ENDPOINT, payload)
    silver = map_price_update_to_silver(payload, envelope)

    assert silver["data_point"]["current_value"] == pytest.approx(expected)


def test_producer_emits_the_key_the_mapper_reads(producer, rest_market):
    """
    G1's seam, asserted directly: the producer's output key and the mapper's
    input key are the same string.

    The two tests above would both survive a coordinated rename. This one
    pins the contract itself, so renaming `price` on either side fails here
    with an obvious message rather than silently reintroducing zeros.
    """
    payload = producer._fetch_market_prices(rest_market)
    assert "price" in payload, (
        "the Silver mapper reads raw['price'] — renaming this key in the "
        "producer puts 0.0 back into momentum_vault silently"
    )
    assert payload["price"] == pytest.approx(payload["outcome_prices"]["Yes"])


def test_unpriceable_market_emits_nothing_through_the_real_function(producer):
    """
    T3's contract, exercised through `_fetch_market_prices` rather than through
    `_extract_outcome_prices` alone: a market with no Yes outcome produces no
    payload at all, and is counted as skipped.
    """
    before = producer._skipped_markets
    result = producer._fetch_market_prices({
        "conditionId": "0xabc",
        "question": "Team A vs Team B",
        "outcomes": '["Team A", "Team B"]',
        "outcomePrices": '["0.6", "0.4"]',
    })
    assert result is None
    assert producer._skipped_markets == before + 1


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
    Fixture guard only: these keys must exist UPSTREAM for the payload assertions
    below to mean anything. This is deliberately not the catalog contract test —
    see the next one.
    """
    assert rest_market.get("endDateIso"), "end date needed to refuse resolved markets"
    assert isinstance(rest_market.get("closed"), bool)
    assert isinstance(rest_market.get("active"), bool)
    events = rest_market.get("events")
    assert isinstance(events, list) and events, "parent event link must survive"
    assert events[0].get("id"), "parent event id is the catalog key"


def test_catalog_fields_survive_onto_the_emitted_payload(producer, rest_market):
    """
    G2: the catalog contract, asserted where it actually matters.

    The test above checks the GAMMA OBJECT carries these fields — which proves
    nothing about what we emit. The producer could drop every one of them and
    stay green. This asserts the emitted payload.
    """
    payload = _payload_from(producer, rest_market)

    assert payload["parent_event_id"] == rest_market["events"][0]["id"]
    assert payload["event_title"] == rest_market["events"][0]["title"]
    assert payload["end_date_iso"] == rest_market["endDateIso"]
    assert payload["market_status"] == "active"
    assert payload["outcome_prices"] == {"Yes": 0.0795, "No": 0.9205}
    assert payload["whale_alert"] is False, "REST cannot detect a whale (T4)"
    assert payload["fetched_at"], "the Silver timestamp fallback reads this key"


def test_clob_token_ids_are_keyed_by_outcome_label(producer, rest_market):
    """
    P8: token ids arrive as a dict keyed by outcome label, not a positional list.

    The CLOB price-history call needs the YES token specifically. A positional
    list forces the consumer to take `[0]` and hope, which charts the NO side and
    inverts a 7% market into 93% — the D3 label-not-index trap one level down.
    """
    payload = _payload_from(producer, rest_market)
    tokens = payload["clob_token_ids"]

    assert isinstance(tokens, dict), "must be label-keyed, not positional"
    assert set(tokens) == {"Yes", "No"}
    assert set(tokens) == set(payload["outcome_prices"]), (
        "token labels and price labels must agree — otherwise the YES price "
        "and the YES token could describe different outcomes"
    )

    raw_ids = _parse_json_array(rest_market["clobTokenIds"])
    raw_labels = _parse_json_array(rest_market["outcomes"])
    assert tokens["Yes"] == raw_ids[raw_labels.index("Yes")]


@pytest.mark.parametrize("market,expected", [
    ({"outcomes": '["Yes","No"]', "clobTokenIds": '["a"]'}, {}),
    ({"outcomes": '["Yes","No"]'}, {}),
    ({"outcomes": "not json", "clobTokenIds": "not json"}, {}),
    ({}, {}),
])
def test_unalignable_token_ids_degrade_to_empty_dict(market, expected):
    """
    An unreadable token map costs a chart, not a measurement. It must NOT skip
    the market — the price is extracted independently and is the primary signal.
    """
    assert _extract_clob_token_ids(market) == expected
