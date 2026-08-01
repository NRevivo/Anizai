"""
Gate 3 — market SELECTION (plan `polymarket_completion.md`, P1-P3).

Why this file exists
--------------------
The price half of the Polymarket defect had a test the day it was fixed. The
coverage half had none, and coverage is the half that decided WHICH markets the
prices belonged to. The producer asked Gamma for 500 markets with no ordering,
no pagination and no topic filter; Gamma capped it at 100 and served them
oldest-first, so the system stored correct prices for novelty contracts ("Will
Jesus Christ return before GTA VI?"). Nothing failed. Nothing logged. The only
symptom was the contents of the vault.

These tests pin the selection chain so that a filter which quietly stops
matching fails here instead of in six weeks' worth of collected data.

What this covers
    [1] The TARGET tag filter keeps only target events, and exclusion beats
        inclusion.
    [2] A past `endDate` is dropped even when `closed` is still false (D5).
    [3] A never-traded market is skipped SILENTLY (F9) — it is normal data, not
        an error, and 42% of live markets are in this state.
    [4] The server-side filter verifier catches a filter that stopped working
        (F6 — Gamma returns HTTP 200 for params it ignored).
    [5] Paging stops on a short page and reports truncation loudly (P3).

The fixture is a REAL /events page captured live 2026-07-30, trimmed to the keys
the producer reads. Shapes are authentic — tags carry int ids, and
outcomes/outcomePrices/clobTokenIds are JSON-encoded strings.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ingestion.polymarket_producer import (
    EVENT_VOLUME_MIN_USD,
    EXCLUDE_TAGS,
    GAMMA_PAGE_LIMIT,
    TARGET_TAGS,
    PolymarketProducer,
    _attach_parent_event,
    _derive_market_status,
    _event_end_date_passed,
    _event_passes_tag_filter,
    _event_tag_ids,
    _market_is_collectable,
    _market_skip_reason,
    _verify_server_side_filters,
)


@pytest.fixture()
def producer_factory():
    """A producer with no Kafka connection — __init__ opens one and these
    tests neither have nor need it."""
    def _make():
        p = PolymarketProducer.__new__(PolymarketProducer)
        p._skipped_markets = 0
        p._last_event_count = None
        return p
    return _make

MOCKS_DIR = Path(__file__).parent.parent / "mocks"
NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def events() -> list[dict]:
    with open(MOCKS_DIR / "polymarket_events_page.json", encoding="utf-8") as f:
        return json.load(f)["data"]


def _by_case(events: list[dict], fragment: str) -> dict:
    """The fixture event whose recorded case contains `fragment`."""
    with open(MOCKS_DIR / "polymarket_events_page.json", encoding="utf-8") as f:
        cases = json.load(f)["_events"]
    match = next(c for c in cases if fragment in c["case"])
    return next(e for e in events if e["id"] == match["id"])


# ==========================================================
# [1] Tag filter
# ==========================================================

def test_target_event_passes_the_tag_filter(events):
    assert _event_passes_tag_filter(_by_case(events, "KEEP")) is True


def test_event_with_no_target_tag_is_dropped(events):
    event = _by_case(events, "no target tag")
    assert not (_event_tag_ids(event) & set(TARGET_TAGS))
    assert _event_passes_tag_filter(event) is False


def test_exclusion_beats_inclusion(events):
    """
    A market tagged both Politics and Crypto is a crypto price tick that
    mentions politics, not a political market that mentions crypto.
    """
    event = _by_case(events, "target tag AND an exclude tag")
    tags = _event_tag_ids(event)
    assert tags & set(TARGET_TAGS), "fixture must carry a target tag"
    assert tags & set(EXCLUDE_TAGS), "fixture must carry an exclude tag"
    assert _event_passes_tag_filter(event) is False


def test_tag_ids_are_normalised_to_strings():
    """
    Gamma returns tag ids as ints in some responses and strings in others. Under
    F6 a type mismatch would not raise — the event would simply appear to carry
    no tags and be dropped, which looks identical to correct filtering.
    """
    assert _event_tag_ids({"tags": [{"id": 2}, {"id": "126"}]}) == {"2", "126"}
    assert _event_tag_ids({}) == set()
    assert _event_tag_ids({"tags": None}) == set()
    assert _event_tag_ids({"tags": ["not-a-dict"]}) == set()


# ==========================================================
# [2] endDate filter (D5)
# ==========================================================

def test_past_end_date_is_dropped_even_when_not_closed(events):
    event = _by_case(events, "endDate already passed")
    assert event.get("closed") is not True, "fixture must still be open"
    assert _event_passes_tag_filter(event) is True, (
        "this event must be dropped by the DATE filter alone — if the tag "
        "filter also rejects it the test proves nothing"
    )
    assert _event_end_date_passed(event, NOW) is True


def test_future_end_date_is_kept(events):
    assert _event_end_date_passed(_by_case(events, "KEEP"), NOW) is False


@pytest.mark.parametrize("value", ["", None, "not-a-date"])
def test_unreadable_end_date_keeps_the_event(value):
    """
    Absence is not evidence of having ended. The agent-side guard (A4) confirms
    live rather than us dropping an event Gamma simply did not describe.
    """
    assert _event_end_date_passed({"endDate": value}, NOW) is False


# ==========================================================
# [3] Never-traded markets (F9)
# ==========================================================

def test_never_traded_market_is_skipped_quietly(events):
    """
    F9: a missing `outcomePrices` means "never traded", not an error. 1,025 of
    2,421 live markets (42%) are in this state, so warning on each would be
    24,600 warnings a day describing normal data — and a log that cries wolf 42%
    of the time is a log nobody reads.
    """
    event = _by_case(events, "KEEP")
    never_traded = [m for m in event["markets"]
                    if _market_skip_reason(m) == "never_traded"]
    assert never_traded, "fixture must contain at least one never-traded market"
    for market in never_traded:
        assert _market_is_collectable(market) is False


def test_closed_leg_is_skipped_and_distinguishable_from_never_traded(events):
    """
    The reason is returned, not a bare bool: 'settled' and 'never traded' are
    very different signals if either count moves unexpectedly.
    """
    event = _by_case(events, "KEEP")
    closed = [m for m in event["markets"] if _market_skip_reason(m) == "closed"]
    assert closed, "fixture must contain at least one closed leg"


# ==========================================================
# [3b] Inactive placeholder legs (2026-08-01)
# ==========================================================

def test_inactive_market_is_skipped_as_a_placeholder():
    """
    An inactive leg is a TEMPLATE SLOT, not a market with a stale price.

    Measured live 2026-08-01 across the whole target set: 108 inactive markets,
    every one at exactly 0.5000 — distinct_prices = 1, against 305 distinct
    values across the 1,171 active markets. Their questions are unnamed
    placeholders ("Will Company A be the largest company in the world").
    Polymarket writes 0.5/0.5 because there is nothing to price.

    Stored, that 0.5 is indistinguishable from a measurement, and nothing in the
    agent path branches on status — so it would render as "Market Consensus 50%"
    beside a real forecast.
    """
    placeholder = {
        "conditionId": "0xplaceholder",
        "question": "Will Company A be the largest company in the world by 2030?",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.5", "0.5"]',
        "active": False,
    }
    assert _derive_market_status(placeholder) == "inactive"
    assert _market_skip_reason(placeholder) == "inactive"
    assert _market_is_collectable(placeholder) is False


def test_archived_market_is_KEPT():
    """
    The filter targets `inactive` specifically, NEVER "not active".

    Archived markets are real markets that were archived: measured 2026-08-01
    they carry distinct genuine prices (0.49, 0.255, 0.25) and real questions
    (RFK Jr 2028, xAI IPO, MBS). Dropping them would discard real data — this is
    the test that fails if the filter is ever widened to `status != "active"`.
    """
    archived = {
        "conditionId": "0xarchived",
        "question": "Will Robert F. Kennedy Jr. win the 2028 Republican primary?",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.49", "0.51"]',
        "archived": True,
    }
    assert _derive_market_status(archived) == "archived"
    assert _market_skip_reason(archived) is None, (
        "archived markets carry real prices and must survive selection"
    )
    assert _market_is_collectable(archived) is True


@pytest.mark.parametrize("market,expected", [
    ({"closed": True, "archived": True, "active": False}, "closed"),
    ({"archived": True, "active": False},                 "archived"),
    ({"active": False},                                   "inactive"),
    ({},                                                  "active"),
    ({"closed": False, "archived": False, "active": True}, "active"),
])
def test_status_precedence_most_terminal_wins(market, expected):
    """
    A market can carry several flags at once; the most terminal is the honest
    answer. This ordering is shared by the filter and the emitted payload, so a
    change here moves both together rather than letting them drift.
    """
    assert _derive_market_status(market) == expected


def test_filter_and_payload_agree_on_status(producer_factory):
    """
    The selection filter and the emitted `market_status` derive from the SAME
    function. Before this, each computed the state independently — the exact
    shape in which a filter and the field it filters on drift apart.
    """
    archived = {
        "id": "1", "conditionId": "0xarchived", "question": "q",
        "outcomes": '["Yes", "No"]', "outcomePrices": '["0.49", "0.51"]',
        "archived": True,
    }
    payload = producer_factory()._fetch_market_prices(archived)
    assert payload is not None
    assert payload["market_status"] == _derive_market_status(archived) == "archived"


def test_collectable_market_survives(events):
    event = _by_case(events, "KEEP")
    good = [m for m in event["markets"] if _market_is_collectable(m)]
    assert good, "fixture must contain at least one collectable market"
    for market in good:
        assert market.get("closed") is not True
        assert market.get("outcomePrices")


def test_parent_event_identity_travels_onto_each_market(events):
    event = _by_case(events, "KEEP")
    market = next(m for m in event["markets"] if _market_is_collectable(m))
    enriched = _attach_parent_event(market, event)

    assert enriched["_parent_event_id"] == str(event["id"])
    assert enriched["_parent_event_title"] == event["title"]
    assert "_parent_event_id" not in market, "must not mutate the caller's dict"


# ==========================================================
# [4] F6 — verify by inspecting the returned set
# ==========================================================

def test_captured_page_satisfies_the_server_side_filters(events):
    """
    The captured events came back from a correctly-parameterised request, so the
    two set-membership filters must hold over them.

    Ordering is deliberately NOT asserted here: the fixture is a hand-picked
    subset chosen to exercise each selection branch, and picking 5 events out of
    387 does not preserve a descending sort. Ordering is covered against
    synthetic input in `test_verifier_catches_a_reverted_sort_order`, where the
    property is actually meaningful.
    """
    complaints = _verify_server_side_filters(events)
    assert not [c for c in complaints if "volume_min" in c or "closed=false" in c]


def test_verifier_catches_an_ignored_volume_min():
    complaints = _verify_server_side_filters([
        {"volume": EVENT_VOLUME_MIN_USD + 1, "volume24hr": 10, "closed": False},
        {"volume": 1_000.0, "volume24hr": 5, "closed": False},
    ])
    assert any("volume_min" in c for c in complaints)


def test_verifier_catches_an_ignored_closed_filter():
    complaints = _verify_server_side_filters([
        {"volume": 1e6, "volume24hr": 10, "closed": True},
    ])
    assert any("closed=false" in c for c in complaints)


def test_verifier_catches_a_reverted_sort_order():
    """
    A silent revert to Gamma's oldest-first default is the exact shape of the
    original defect — it is what turned a 100-market cap into novelty contracts.
    """
    complaints = _verify_server_side_filters([
        {"volume": 1e6, "volume24hr": 1.0, "closed": False},
        {"volume": 1e6, "volume24hr": 99.0, "closed": False},
    ])
    assert any("ascending" in c for c in complaints)


def test_verifier_tolerates_an_empty_set():
    assert _verify_server_side_filters([]) == []


# ==========================================================
# [5] Paging and the loud bound (P3)
# ==========================================================

def _producer_over(pages: list) -> PolymarketProducer:
    producer = PolymarketProducer.__new__(PolymarketProducer)
    producer._skipped_markets = 0
    producer._last_event_count = None
    calls = iter(pages)
    producer._fetch_events_page = lambda offset: next(calls, [])
    return producer


def test_paging_stops_on_a_short_page():
    full = [{"id": str(i)} for i in range(GAMMA_PAGE_LIMIT)]
    short = [{"id": "x1"}, {"id": "x2"}]
    assert len(_producer_over([full, short])._fetch_target_events()) == (
        GAMMA_PAGE_LIMIT + 2
    )


def test_paging_deduplicates_across_pages():
    """
    The window is ordered by volume24hr, which moves between requests, so an
    event can legitimately appear on two pages.
    """
    page = [{"id": "dup"}] * 3 + [{"id": "a"}, {"id": "b"}]
    assert len(_producer_over([page])._fetch_target_events()) == 3


def test_offset_ceiling_is_reported_loudly(caplog):
    """
    P3: a bound that truncates silently is the original bug with a bigger
    number. HTTP 422 is signalled by `_fetch_events_page` returning None.
    """
    producer = _producer_over([])
    producer._fetch_events_page = lambda offset: (
        None if offset else [{"id": str(i)} for i in range(GAMMA_PAGE_LIMIT)]
    )
    with caplog.at_level("WARNING"):
        producer._fetch_target_events()
    assert "TRUNCATED" in caplog.text


def test_shrinking_event_set_is_reported_loudly(caplog):
    producer = _producer_over([[{"id": "a"}]])
    producer._last_event_count = 500
    with caplog.at_level("WARNING"):
        producer._fetch_target_events()
    assert "SHRANK" in caplog.text
    assert "500" in caplog.text, "the warning must carry the actual numbers"


def test_ordinary_churn_does_not_warn(caplog):
    """
    D6 measured ~8%/day churn against 90%+ day-over-day overlap, so at an hourly
    cadence a normal sweep moves well under 1%. Warning on any decrease would
    fire constantly and train the reader to ignore it.
    """
    producer = _producer_over([[{"id": str(i)} for i in range(95)]])
    producer._last_event_count = 100
    with caplog.at_level("WARNING"):
        producer._fetch_target_events()
    assert "SHRANK" not in caplog.text


# ==========================================================
# End-to-end selection over the captured page
# ==========================================================

def test_full_selection_chain_over_the_fixture(events, caplog):
    """
    The whole funnel, wired as `_fetch_active_markets` runs it: only the KEEP
    event's collectable markets survive, each carrying its parent identity.
    """
    producer = _producer_over([events])
    with caplog.at_level("INFO"):
        collected = producer._fetch_active_markets()

    keep = _by_case(events, "KEEP")
    expected = [m for m in keep["markets"] if _market_is_collectable(m)]

    assert len(collected) == len(expected)
    assert {m["_parent_event_id"] for m in collected} == {str(keep["id"])}
    assert all(m["_parent_event_title"] == keep["title"] for m in collected)
    assert "Discovery funnel" in caplog.text
