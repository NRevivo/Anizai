"""
Gates 4 and 5 — the Silver seam (plan `polymarket_completion.md`, S1-S5).

Why this file exists
--------------------
The producer emitted the catalog fields; `map_price_update_to_silver` was a
fixed 7-key dict, so every one of them hit the floor between Bronze and the
vault. `data_point.status` was hardcoded "active" — wrong for ~7% of live
markets — and `unit` was hardcoded "USD" for a number that is a probability.
None of it failed. The producer's own tests passed, because they stopped at the
payload.

G4 pins the carry-through end to end (payload → mapper → metadata_extension).
G5 pins the range guard, and pins it SOURCE-SCOPED: the same shared Silver
validator serves FRED, OpenWeather and OpenSky, whose values are not 0-1.
"""

import json
from pathlib import Path

import pytest

from config.kafka_topics import DEAD_LETTER_QUEUE, SILVER_STRUCTURED_METRICS
from ingestion.polymarket_producer import PolymarketProducer
from processing.silver_job import (
    map_price_update_to_silver,
    process_fred_message,
    process_polymarket_message,
)
from utils.kafka_utils import build_bronze_message

MOCKS_DIR = Path(__file__).parent.parent / "mocks"
GAMMA_ENDPOINT = "https://gamma-api.polymarket.com/markets"


@pytest.fixture(scope="module")
def rest_payload() -> dict:
    """The payload the REAL producer emits for a real Gamma market."""
    with open(MOCKS_DIR / "polymarket_rest_market.json", encoding="utf-8") as f:
        market = json.load(f)
    producer = PolymarketProducer.__new__(PolymarketProducer)
    producer._skipped_markets = 0
    producer._last_event_count = None
    payload = producer._fetch_market_prices(market)
    assert payload is not None
    return payload


@pytest.fixture()
def silver(rest_payload) -> dict:
    envelope = build_bronze_message("polymarket", GAMMA_ENDPOINT, rest_payload)
    return map_price_update_to_silver(rest_payload, envelope)


# ==========================================================
# G4 — catalog carry-through
# ==========================================================

def test_all_six_catalog_keys_reach_metadata_extension(silver, rest_payload):
    """
    S1. Six keys, not five — `market_id` was added because
    `core_identity.parent_id` (where the market id also travels) is NOT among
    momentum_vault.insert's 13 columns, so it is dropped at the INSERT and the
    Polymarket market id reaches the vault nowhere else.
    """
    meta = silver["metadata_extension"]
    for key in ("clob_token_ids", "market_id", "parent_event_id",
                "event_title", "end_date_iso", "outcome_prices"):
        assert key in meta, f"{key} was dropped by the Silver mapper"
        assert meta[key] == rest_payload[key]


def test_clob_token_ids_survive_as_a_label_keyed_dict(silver):
    """
    P8's shape must reach the vault intact — the CLOB history call reads
    `metadata_extension.clob_token_ids["Yes"]`, and a positional list there would
    chart the complement.
    """
    tokens = silver["metadata_extension"]["clob_token_ids"]
    assert isinstance(tokens, dict)
    assert set(tokens) == {"Yes", "No"}


def test_original_seven_keys_are_not_disturbed(silver):
    """The catalog fields are additive — nothing that existed may regress."""
    meta = silver["metadata_extension"]
    for key in ("liquidity_pool_tvl", "bid_ask_spread", "24h_volume",
                "is_divergent", "whale_alert", "resolution_rules", "question"):
        assert key in meta


def test_unit_is_probability_not_usd(silver):
    """S3. A Polymarket price is a probability in [0, 1]; "USD" invited a reader
    to treat 0.0135 as cents."""
    assert silver["data_point"]["unit"] == "probability"


def test_status_comes_from_the_payload_not_a_hardcoded_active(silver):
    """S2. Measured on the live target set: 1,298 active / 95 inactive / 3
    archived — so the hardcoded "active" was wrong for ~7% of rows."""
    assert silver["data_point"]["status"] == "active"


@pytest.mark.parametrize("status", ["inactive", "archived", "closed"])
def test_non_active_status_is_carried_verbatim(rest_payload, status):
    payload = {**rest_payload, "market_status": status}
    envelope = build_bronze_message("polymarket", GAMMA_ENDPOINT, payload)
    assert map_price_update_to_silver(payload, envelope)["data_point"]["status"] == status


def test_websocket_rows_keep_active_as_the_default():
    """A last_trade event carries no market_status, and "active" is correct for
    a live trade."""
    payload = {"payload_type": "price_update", "ingestion_mode": "websocket",
               "asset_id": "tok", "price": 0.67}
    envelope = build_bronze_message("polymarket", "wss://x", payload)
    assert map_price_update_to_silver(payload, envelope)["data_point"]["status"] == "active"


def test_timestamp_falls_back_to_fetched_at(rest_payload):
    """
    S4. `timestamp` is the WebSocket key; `fetched_at` is what REST sends.
    Without the middle fallback every REST row aged silently to the envelope's
    producer_timestamp — close enough to look right, wrong enough to skew a
    time series.
    """
    envelope = build_bronze_message("polymarket", GAMMA_ENDPOINT, rest_payload)
    silver = map_price_update_to_silver(rest_payload, envelope)
    assert silver["data_point"]["timestamp_utc"] == rest_payload["fetched_at"]


def test_websocket_timestamp_still_wins_when_present(rest_payload):
    payload = {**rest_payload, "timestamp": "2026-07-30T09:00:00+00:00"}
    envelope = build_bronze_message("polymarket", GAMMA_ENDPOINT, payload)
    silver = map_price_update_to_silver(payload, envelope)
    assert silver["data_point"]["timestamp_utc"] == "2026-07-30T09:00:00+00:00"


def test_full_route_reaches_structured_metrics(rest_payload):
    envelope = build_bronze_message("polymarket", GAMMA_ENDPOINT, rest_payload)
    topic, record = process_polymarket_message(envelope)
    assert topic == SILVER_STRUCTURED_METRICS
    assert record["metadata_extension"]["event_title"]


# ==========================================================
# G5 — probability range guard
# ==========================================================

def _route(payload: dict):
    return process_polymarket_message(
        build_bronze_message("polymarket", GAMMA_ENDPOINT, payload)
    )


@pytest.mark.parametrize("price", [0.0, 0.0005, 0.5, 0.9995, 1.0])
def test_in_range_probabilities_pass(rest_payload, price):
    """
    INCLUSIVE at both ends. 0.0 and 1.0 are legitimate live market states — a
    resolved-NO leg trades at exactly 0.0 and longshots sit at 0.0005 — so
    rejecting them would discard real data. The zero-price DEFECT is caught by
    the producer's per-sweep all-zeros warning, which can tell "this market is
    at 0" from "every market is at 0".
    """
    topic, _ = _route({**rest_payload, "price": price})
    assert topic == SILVER_STRUCTURED_METRICS


@pytest.mark.parametrize("price", [-0.01, 1.01, 67.0, -1.0])
def test_out_of_range_probabilities_route_to_dlq(rest_payload, price):
    """Scale errors: a percentage sent as 67 rather than 0.67, or a sign flip."""
    topic, record = _route({**rest_payload, "price": price})
    assert topic == DEAD_LETTER_QUEUE
    assert record["failed_stage"] == "price_range_guard"
    assert record["source_topic"] == "ingest.bronze.polymarket"


@pytest.mark.parametrize("price", [None, "abc", ""])
def test_missing_or_non_numeric_price_routes_to_dlq(rest_payload, price):
    """
    The mapper used to read `raw.get("price", 0.0)`, so a payload with no price
    became a confident zero — the mechanism behind 93,607 zero rows. That
    default is now unreachable.
    """
    payload = {**rest_payload}
    if price is None:
        payload.pop("price")
    else:
        payload["price"] = price
    topic, record = _route(payload)
    assert topic == DEAD_LETTER_QUEUE
    assert record["failed_stage"] == "price_guard"


def test_websocket_prices_are_guarded_too():
    payload = {"payload_type": "price_update", "ingestion_mode": "websocket",
               "event_type": "last_trade", "asset_id": "tok", "price": 42.0}
    topic, record = _route(payload)
    assert topic == DEAD_LETTER_QUEUE
    assert record["failed_stage"] == "price_range_guard"


def test_guard_is_source_scoped_and_does_not_touch_fred():
    """
    G5's other half. FRED values are not 0-1 — a 4.35% federal funds rate is
    ordinary — so the guard must live inside the Polymarket branch, not in the
    shared validator that FRED, OpenWeather and OpenSky also call.
    """
    payload = {"series_id": "FEDFUNDS", "value": "4.35",
               "observation_date": "2026-07-30", "realtime_start": "2026-07-30"}
    topic, _ = process_fred_message(
        build_bronze_message("fred", "https://api.stlouisfed.org", payload)
    )
    assert topic == SILVER_STRUCTURED_METRICS
