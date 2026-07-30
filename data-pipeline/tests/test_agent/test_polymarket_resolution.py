"""
Gate 6 — market resolution order (plan `polymarket_completion.md`, A1-A5).

Why this file exists
--------------------
On the 2026-07-25/26 cloud run, 0 of 7 forecasts routed `tier_1`. The Sprint 22
pg_trgm resolver never fired, so every forecast rendered "No market benchmark"
regardless of whether a market existed. The resolution path had no test that
exercised the ORDER of its fallbacks — only the individual pieces.

These tests pin the cascade: exact identity, then similarity, then a live
lookup, then an honest refusal. Every step is asserted for what it does AND for
what it must not do — in particular, that supplying a conditionId can never
resolve to LESS than supplying nothing.

Everything here is offline. The two network functions are stubbed at the
`polymarket_api` boundary; their own decode helpers are exercised directly
against captured Gamma shapes.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent.agents import market_bridge
from agent.nodes import synthesize
from agent.tools import polymarket_api

MOCKS_DIR = Path(__file__).parent.parent / "mocks"
NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)

COND = "0x58180f4a69651f67c23ac92845721b2320cc0c76effeb5e79db5288d8d8e6b28"

# Captured before the autouse `no_network` fixture replaces them. The timeout
# tests need the REAL function body with a stubbed `requests.get` underneath —
# that is precisely where the tuple has to arrive.
_REAL_FETCH_MARKET = polymarket_api.fetch_market_by_condition_id


def _vault_row(*, ref=COND, value=0.42, question="Will X happen?",
               end_date="2026-12-31", tokens=None):
    return {
        "external_reference_id": ref,
        "current_value": value,
        "change_24h": 0.0, "change_7d": 0.0, "change_30d": 0.0,
        "status": "active",
        "metadata_extension": {
            "question": question,
            "end_date_iso": end_date,
            "clob_token_ids": tokens if tokens is not None else {},
        },
    }


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """
    Fail loudly if a test reaches the network. Individual tests opt in to a
    stubbed response; none may make a real call.
    """
    def forbidden(*a, **k):  # pragma: no cover - only runs on a defect
        raise AssertionError("unstubbed network call")
    monkeypatch.setattr(polymarket_api, "fetch_market_by_condition_id", forbidden)
    monkeypatch.setattr(polymarket_api, "fetch_price_history", forbidden)


@pytest.fixture()
def quiet_sources(monkeypatch):
    """Silence the non-Polymarket slices so assertions are about resolution."""
    monkeypatch.setattr(market_bridge, "_build_fred_anomalies", lambda: [])
    monkeypatch.setattr(market_bridge, "_build_google_trends", lambda e: [])


# ==========================================================
# [1] conditionId → exact lookup
# ==========================================================

def test_condition_id_resolves_exactly(monkeypatch, quiet_sources):
    seen = {}

    def fetch_latest(source, ref):
        seen["args"] = (source, ref)
        return _vault_row()

    monkeypatch.setattr(market_bridge.market_tools, "fetch_latest", fetch_latest)
    monkeypatch.setattr(market_bridge.market_tools, "fetch_time_series",
                        lambda **k: [])
    monkeypatch.setattr(market_bridge, "_apply_resolved_market_guard", lambda p: p)

    result = market_bridge.run(polymarket_slug=COND, raw_question="anything")

    assert seen["args"] == ("polymarket", COND)
    assert result["polymarket"]["market_slug"] == COND
    assert result["polymarket"]["current_odds"] == pytest.approx(0.42)


def test_resolver_is_not_consulted_when_the_exact_lookup_hits(
    monkeypatch, quiet_sources
):
    """The exact path must not pay for a pg_trgm scan it does not need."""
    called = []
    monkeypatch.setattr(market_bridge.market_tools, "fetch_latest",
                        lambda s, r: _vault_row())
    monkeypatch.setattr(market_bridge.market_tools, "fetch_time_series",
                        lambda **k: [])
    monkeypatch.setattr(market_bridge.market_tools,
                        "find_polymarket_market_by_question",
                        lambda q: called.append(q))
    monkeypatch.setattr(market_bridge, "_apply_resolved_market_guard", lambda p: p)

    market_bridge.run(polymarket_slug=COND, raw_question="Will X happen?")
    assert called == []


# ==========================================================
# [2] fallthrough to the question resolver
# ==========================================================

def test_exact_miss_falls_through_to_the_question_resolver(
    monkeypatch, quiet_sources
):
    """
    A conditionId that misses must not resolve to LESS than no conditionId at
    all. `external_reference_id` is the asset_id for WebSocket rows and the
    condition_id for REST rows, so a market held under the other key is
    invisible to an exact lookup yet still matches on question text.
    """
    monkeypatch.setattr(market_bridge.market_tools, "fetch_latest",
                        lambda s, r: None)
    monkeypatch.setattr(market_bridge.market_tools,
                        "find_polymarket_market_by_question",
                        lambda q: _vault_row(ref="asset-123", value=0.61))
    monkeypatch.setattr(market_bridge.market_tools, "fetch_time_series",
                        lambda **k: [])
    monkeypatch.setattr(market_bridge, "_apply_resolved_market_guard", lambda p: p)

    result = market_bridge.run(polymarket_slug=COND, raw_question="Will X happen?")

    assert result["polymarket"]["market_slug"] == "asset-123"
    assert result["polymarket"]["current_odds"] == pytest.approx(0.61)


def test_no_condition_id_still_uses_the_resolver(monkeypatch, quiet_sources):
    """Sprint 22 behaviour, unchanged — this is the free-text path."""
    monkeypatch.setattr(market_bridge.market_tools,
                        "find_polymarket_market_by_question",
                        lambda q: _vault_row(value=0.33))
    monkeypatch.setattr(market_bridge.market_tools, "fetch_time_series",
                        lambda **k: [])
    monkeypatch.setattr(market_bridge, "_apply_resolved_market_guard", lambda p: p)

    result = market_bridge.run(raw_question="Will X happen?",
                               has_market_question_intent=True)
    assert result["polymarket"]["current_odds"] == pytest.approx(0.33)


def test_open_ended_question_does_not_pay_for_a_resolver_round_trip(
    monkeypatch, quiet_sources
):
    called = []
    monkeypatch.setattr(market_bridge.market_tools,
                        "find_polymarket_market_by_question",
                        lambda q: called.append(q))
    result = market_bridge.run(raw_question="How will the Gulf situation develop?",
                               has_market_question_intent=False)
    assert called == []
    assert result["polymarket"] is None


# ==========================================================
# [3] live safety net (A3)
# ==========================================================

def test_both_vault_paths_miss_then_live_lookup_resolves(
    monkeypatch, quiet_sources
):
    monkeypatch.setattr(market_bridge.market_tools, "fetch_latest",
                        lambda s, r: None)
    monkeypatch.setattr(market_bridge.market_tools,
                        "find_polymarket_market_by_question", lambda q: None)
    monkeypatch.setattr(polymarket_api, "fetch_market_by_condition_id",
                        lambda c, **k: {
                            "condition_id": c, "question": "Will X happen?",
                            "outcome_prices": {"Yes": 0.77, "No": 0.23},
                            "clob_token_ids": {"Yes": "tok-yes", "No": "tok-no"},
                            "end_date_iso": "2026-12-31", "market_status": "active",
                        })
    monkeypatch.setattr(polymarket_api, "fetch_price_history",
                        lambda t, **k: [{"timestamp": "2026-07-01T00:00:00+00:00",
                                         "value": 0.5}])

    result = market_bridge.run(polymarket_slug=COND, raw_question="Will X happen?")
    payload = result["polymarket"]

    assert payload["current_odds"] == pytest.approx(0.77)
    assert payload["resolution_verified"] is True
    assert payload["has_ended"] is False
    assert len(payload["price_history"]) == 1
    assert payload["momentum"] == {"change_24h": 0.0, "change_7d": 0.0,
                                   "change_30d": 0.0}, "must be zeroed, not fabricated"
    assert payload["whale_alerts"] == []


def test_live_path_is_not_attempted_without_a_condition_id(
    monkeypatch, quiet_sources
):
    """Free-text questions must never trigger an outbound HTTP call."""
    monkeypatch.setattr(market_bridge.market_tools,
                        "find_polymarket_market_by_question", lambda q: None)
    result = market_bridge.run(raw_question="Will X happen?",
                               has_market_question_intent=True)
    assert result["polymarket"] is None  # `no_network` would have raised


def test_unreachable_gamma_degrades_to_tier_2(monkeypatch, quiet_sources):
    monkeypatch.setattr(market_bridge.market_tools, "fetch_latest",
                        lambda s, r: None)
    monkeypatch.setattr(market_bridge.market_tools,
                        "find_polymarket_market_by_question", lambda q: None)
    monkeypatch.setattr(polymarket_api, "fetch_market_by_condition_id",
                        lambda c, **k: None)

    result = market_bridge.run(polymarket_slug=COND, raw_question="Will X happen?")
    assert result["polymarket"] is None


def test_live_market_with_no_yes_outcome_is_refused(monkeypatch, quiet_sources):
    monkeypatch.setattr(market_bridge.market_tools, "fetch_latest",
                        lambda s, r: None)
    monkeypatch.setattr(market_bridge.market_tools,
                        "find_polymarket_market_by_question", lambda q: None)
    monkeypatch.setattr(polymarket_api, "fetch_market_by_condition_id",
                        lambda c, **k: {
                            "condition_id": c, "question": "Team A vs Team B",
                            "outcome_prices": {"Team A": 0.6, "Team B": 0.4},
                            "clob_token_ids": {}, "end_date_iso": "",
                            "market_status": "active",
                        })
    result = market_bridge.run(polymarket_slug=COND, raw_question="who wins?")
    assert result["polymarket"] is None, "must not invent an affirmative price"


def test_a3_skips_history_when_the_budget_is_spent(monkeypatch):
    """
    The combined budget: the history call gets 6s minus what the lookup spent.
    A slow lookup costs the chart, never the forecast.
    """
    monkeypatch.setattr(polymarket_api, "fetch_market_by_condition_id",
                        lambda c, **k: {
                            "condition_id": c, "question": "q",
                            "outcome_prices": {"Yes": 0.5},
                            "clob_token_ids": {"Yes": "tok"},
                            "end_date_iso": "2026-12-31",
                            "market_status": "active"})

    clock = iter([0.0, 999.0])
    monkeypatch.setattr(market_bridge.time, "monotonic", lambda: next(clock))

    payload = market_bridge._build_polymarket_live(COND)
    assert payload["current_odds"] == pytest.approx(0.5)
    assert payload["price_history"] == [], "history must be skipped, not awaited"


# ==========================================================
# [4] resolved-market guard (A4)
# ==========================================================

@pytest.mark.parametrize("value,expected", [
    ("2026-07-24", True), ("2026-09-16", False),
    ("2026-07-24T23:59:00Z", True), ("2026-09-16T00:00:00Z", False),
    ("", False), ("not-a-date", False),
])
def test_end_date_passed_parsing(value, expected):
    assert market_bridge._market_end_date_passed(value, NOW) is expected


def test_future_end_date_needs_no_live_confirmation():
    payload = {"market_slug": COND, "end_date_iso": "2026-12-31",
               "current_odds": 0.4}
    out = market_bridge._apply_resolved_market_guard(payload, now=NOW)
    assert out["has_ended"] is False
    assert out["resolution_verified"] is True  # `no_network` would have raised


def test_absent_end_date_triggers_live_confirmation(monkeypatch):
    monkeypatch.setattr(polymarket_api, "fetch_market_by_condition_id",
                        lambda c, **k: {"end_date_iso": "2026-09-16",
                                        "market_status": "active",
                                        "outcome_prices": {"Yes": 0.31}})
    out = market_bridge._apply_resolved_market_guard(
        {"market_slug": COND, "end_date_iso": "", "current_odds": 0.9}, now=NOW)

    assert out["has_ended"] is False
    assert out["end_date_iso"] == "2026-09-16"
    assert out["current_odds"] == pytest.approx(0.31), "live price wins"


def test_stale_stored_end_date_is_corrected_by_the_live_check(monkeypatch):
    """
    The stored status is stale by construction — the producer sweeps hourly. A
    stored date that has passed must be re-checked, not believed.
    """
    monkeypatch.setattr(polymarket_api, "fetch_market_by_condition_id",
                        lambda c, **k: {"end_date_iso": "2026-09-16",
                                        "market_status": "active",
                                        "outcome_prices": {"Yes": 0.31}})
    out = market_bridge._apply_resolved_market_guard(
        {"market_slug": COND, "end_date_iso": "2025-01-01", "current_odds": 0.9},
        now=NOW)
    assert out["has_ended"] is False


def test_live_confirmation_detects_a_resolved_market(monkeypatch):
    monkeypatch.setattr(polymarket_api, "fetch_market_by_condition_id",
                        lambda c, **k: {"end_date_iso": "2026-07-24",
                                        "market_status": "closed",
                                        "outcome_prices": {"Yes": 1.0}})
    out = market_bridge._apply_resolved_market_guard(
        {"market_slug": COND, "end_date_iso": "2026-07-24", "current_odds": 0.9},
        now=NOW)
    assert out["has_ended"] is True
    assert out["current_odds"] == pytest.approx(1.0)


def test_failed_confirmation_is_marked_unverified(monkeypatch):
    monkeypatch.setattr(polymarket_api, "fetch_market_by_condition_id",
                        lambda c, **k: None)
    out = market_bridge._apply_resolved_market_guard(
        {"market_slug": COND, "end_date_iso": "2025-01-01", "current_odds": 0.9},
        now=NOW)
    assert out["resolution_verified"] is False
    assert out["has_ended"] is True, "falls back to the stored signal"


# ==========================================================
# [5] refusal instead of fabrication (A5)
# ==========================================================

def _session_result(**kw):
    output = {
        "final_probability": 0.63, "confidence": 0.7, "consensus_score": 0.5,
        "bottom_line_answer": "b", "detailed_explanation": "d",
        "summary_markdown": "s", "market_comparison_insight": "12 points above",
        "sentiment_analysis_insight": "x", "evidence_feed_summary": "y",
        "key_factors": [], "what_i_didnt_find": [], "reasoning_chain": [],
    }
    return synthesize._build_session_result(
        synthesis_output=output, evidence_trail=[], tier="tier_1", **kw)


def test_market_shaped_question_with_no_match_refuses_explicitly():
    """
    A5. Calling this "freeform analysis" would reclassify OUR coverage gap as a
    property of the USER's question — the same class of defect as a default
    served as a measurement.
    """
    result = _session_result(polymarket_payload=None, market_question_intent=True)
    assert result["marketComparisonInsight"] == synthesize.NO_MATCH_CAPTION
    assert result["marketProbability"] is None
    assert result["marketComparison"] == []


def test_open_ended_question_keeps_the_freeform_caption():
    result = _session_result(polymarket_payload=None, market_question_intent=False)
    assert result["marketComparisonInsight"] == synthesize.NO_MARKET_CAPTION


def test_live_market_renders_a_real_comparison():
    result = _session_result(
        polymarket_payload={"current_odds": 0.51, "has_ended": False},
        market_question_intent=True)
    assert result["marketProbability"] == pytest.approx(0.51)
    assert result["marketComparison"] == [
        {"label": "Anizai", "value": pytest.approx(0.63)},
        {"label": "Polymarket", "value": pytest.approx(0.51)},
    ]
    assert result["marketComparisonInsight"] == "12 points above"


def test_resolved_market_suppresses_the_numeric_comparison():
    """
    A resolved market prices at 0 or 1. That is the ANSWER, not a benchmark —
    publishing it renders "Market Consensus 100% vs Anizai 63%", a scoreboard
    the system appears to have lost when it was never benchmarking at all.
    """
    result = _session_result(
        polymarket_payload={"current_odds": 1.0, "has_ended": True},
        market_question_intent=True)

    assert result["marketProbability"] is None, "card must fall to its empty state"
    assert result["marketComparison"] == []
    assert result["marketComparisonInsight"].startswith(
        synthesize.RESOLVED_MARKET_PREFIX)
    assert "settled YES" in result["marketComparisonInsight"]
    assert "12 points above" not in result["marketComparisonInsight"], (
        "the model's comparison commentary describes a chart that is no longer "
        "on screen"
    )


@pytest.mark.parametrize("odds,fragment", [
    (1.0, "settled YES"), (0.0, "settled NO"), (0.5, "last traded at 50%"),
    (None, "not available"),
])
def test_settled_outcome_sentence(odds, fragment):
    assert fragment in synthesize._settled_outcome_sentence(odds)


# ==========================================================
# [6] the second _parse_json_array copy (hub side)
# ==========================================================
# The producer's copy is covered by test_polymarket_rest_price.py. This is the
# deliberately-duplicated hub copy: importing Domain A into the hub would drag
# the Kafka client in for ten lines of wire decoding, so both copies exist and
# both are tested.

def test_hub_decodes_gammas_json_encoded_strings():
    with open(MOCKS_DIR / "polymarket_rest_market.json", encoding="utf-8") as f:
        market = json.load(f)

    normalised = polymarket_api._normalise_market(market)

    assert normalised["condition_id"] == market["conditionId"]
    assert normalised["outcome_prices"] == {"Yes": 0.0795, "No": 0.9205}
    assert set(normalised["clob_token_ids"]) == {"Yes", "No"}
    assert normalised["market_status"] == "active"
    assert normalised["end_date_iso"] == "2026-07-24"


def test_hub_parse_json_array_matches_the_producers_contract():
    assert polymarket_api._parse_json_array('["Yes", "No"]') == ["Yes", "No"]
    assert polymarket_api._parse_json_array(["Yes", "No"]) == ["Yes", "No"]
    assert polymarket_api._parse_json_array("not json") == []
    assert polymarket_api._parse_json_array(None) == []


@pytest.mark.parametrize("market,status", [
    ({"closed": True}, "closed"),
    ({"archived": True}, "archived"),
    ({"active": False}, "inactive"),
    ({}, "active"),
])
def test_hub_derives_market_status(market, status):
    assert polymarket_api._normalise_market(market)["market_status"] == status


# ==========================================================
# [7] timeout semantics
# ==========================================================
# `requests` has no wall-clock deadline: a scalar timeout applies to the connect
# and read phases SEPARATELY, and the read half bounds the gap between socket
# reads rather than the total. A scalar therefore reads as a total budget it does
# not provide — which is how a nominal 5s call was observed taking 20s+. These
# pin the two halves of the fix: tuples at the request boundary, wall-clock
# enforcement in the caller.

def test_requests_always_receives_a_connect_read_tuple(monkeypatch):
    seen = {}

    class _Resp:
        status_code = 200
        @staticmethod
        def json():
            return []

    def fake_get(url, params=None, timeout=None):
        seen["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(polymarket_api.requests, "get", fake_get)
    _REAL_FETCH_MARKET(COND)

    assert isinstance(seen["timeout"], tuple), (
        "a scalar timeout claims a total budget requests does not enforce"
    )
    assert len(seen["timeout"]) == 2


def test_connect_timeout_never_exceeds_a_shrunken_read_budget():
    """When almost no budget remains, spending it all on connecting is pointless."""
    connect, read = polymarket_api._timeout(0.4)
    assert read == pytest.approx(0.4)
    assert connect <= read


def test_timeout_falls_back_to_the_default_read_budget():
    for value in (None, 0, -1):
        connect, read = polymarket_api._timeout(value)
        assert read == pytest.approx(
            polymarket_api.settings.POLYMARKET_API_TIMEOUT_S)
        assert connect > 0


def test_a3_deducts_real_elapsed_time_from_the_history_allowance(monkeypatch):
    """
    The wall-clock guarantee: the history call is given the budget MINUS what the
    lookup actually consumed, measured with time.monotonic() rather than assumed.
    """
    monkeypatch.setattr(polymarket_api, "fetch_market_by_condition_id",
                        lambda c, **k: {
                            "condition_id": c, "question": "q",
                            "outcome_prices": {"Yes": 0.5},
                            "clob_token_ids": {"Yes": "tok"},
                            "end_date_iso": "2026-12-31",
                            "market_status": "active"})

    granted = {}

    def fake_history(token, *, timeout_s=None):
        granted["timeout_s"] = timeout_s
        return []

    monkeypatch.setattr(polymarket_api, "fetch_price_history", fake_history)
    # start=0, after-lookup=4.0, history-start, history-end
    clock = iter([0.0, 4.0, 4.0, 4.1])
    monkeypatch.setattr(market_bridge.time, "monotonic", lambda: next(clock))

    market_bridge._build_polymarket_live(COND)

    budget = market_bridge.settings.POLYMARKET_A3_COMBINED_BUDGET_S
    assert granted["timeout_s"] == pytest.approx(budget - 4.0), (
        "a lookup that burned 4s must leave the history only the remainder"
    )


def test_hub_drops_unpriceable_outcomes_rather_than_zeroing_them():
    out = polymarket_api._normalise_market(
        {"outcomes": '["Yes","No"]', "outcomePrices": '["abc","0.4"]'})
    assert "Yes" not in out["outcome_prices"], "a sentinel here is the original bug"
    assert out["outcome_prices"]["No"] == pytest.approx(0.4)
