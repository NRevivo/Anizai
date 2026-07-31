"""
Gate 2 — service logic that does not need a database.

The pure decision functions inside the services are tested directly. The
persistence paths (`run_discovery`, `resolve_open_questions`) need a live
Postgres and are deferred to the Gate 3 integration test, which Phase 10A does
not run — there is no database provisioned in a local-only sprint.

What IS covered here is the logic most likely to be wrong in a way that costs
money or silently loses data: the ceiling arithmetic and the manual-add
validation.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from calibration.models import MarketCandidate
from calibration.services import discovery_service, manual_add_service


def _candidate(cohort="7d", volume=100_000, condition="0x1") -> MarketCandidate:
    return MarketCandidate(
        question_text="Will X happen?",
        polymarket_slug="will-x",
        polymarket_condition_id=condition,
        category="geopolitical",
        cohort=cohort,
        expected_resolution_date=date(2026, 8, 1),
        volume_usd=Decimal(volume),
        days_to_resolution=7,
    )


# ==========================================================
# compute_needed
# ==========================================================

def test_needed_is_target_minus_open():
    needed = discovery_service.compute_needed({"7d": 3, "14d": 0, "30-45d": 8})
    from calibration import config

    assert needed["7d"] == config.CALIBRATION_TARGET_COUNT_7D - 3
    assert needed["14d"] == config.CALIBRATION_TARGET_COUNT_14D
    assert needed["30-45d"] == max(0, config.CALIBRATION_TARGET_COUNT_30_45D - 8)


def test_a_cohort_over_target_asks_for_zero_not_a_negative():
    """
    Over-target is reachable via manual adds. It must mean "want nothing", not
    imply an eviction — an evicted question would take its in-flight forecasts
    with it and silently shrink the calibration sample.
    """
    needed = discovery_service.compute_needed({"7d": 999, "14d": 999, "30-45d": 999})
    assert all(v == 0 for v in needed.values())


def test_missing_cohort_key_is_treated_as_zero_open():
    needed = discovery_service.compute_needed({})
    assert needed["7d"] > 0


# ==========================================================
# apply_ceiling — the cost brake
# ==========================================================

def test_ceiling_does_not_trim_when_there_is_headroom():
    selected = [_candidate(condition=f"0x{i}") for i in range(3)]
    kept, dropped = discovery_service.apply_ceiling(selected, total_open=0)
    assert len(kept) == 3
    assert dropped == 0


def test_ceiling_trims_to_the_remaining_headroom(monkeypatch):
    from calibration import config

    monkeypatch.setattr(config, "CALIBRATION_MAX_OPEN_QUESTIONS", 10)
    selected = [_candidate(condition=f"0x{i}") for i in range(5)]

    kept, dropped = discovery_service.apply_ceiling(selected, total_open=8)
    assert len(kept) == 2
    assert dropped == 3


def test_ceiling_already_reached_drops_everything(monkeypatch):
    from calibration import config

    monkeypatch.setattr(config, "CALIBRATION_MAX_OPEN_QUESTIONS", 10)
    selected = [_candidate(condition=f"0x{i}") for i in range(4)]

    kept, dropped = discovery_service.apply_ceiling(selected, total_open=10)
    assert kept == []
    assert dropped == 4


def test_ceiling_overshoot_does_not_produce_negative_headroom(monkeypatch):
    """Open count can exceed the ceiling after a config change. Must not crash."""
    from calibration import config

    monkeypatch.setattr(config, "CALIBRATION_MAX_OPEN_QUESTIONS", 10)
    kept, dropped = discovery_service.apply_ceiling([_candidate()], total_open=50)
    assert kept == []
    assert dropped == 1


def test_truncation_is_reported_in_the_summary():
    """
    A silent cap reads as full coverage. The report must say what it dropped.
    """
    report = discovery_service.DiscoveryReport(truncated_by_ceiling=7)
    assert any("TRUNCATED" in line for line in report.summary_lines())


def test_shortfall_is_reported_in_the_summary():
    report = discovery_service.DiscoveryReport(
        open_before={"7d": 2, "14d": 0, "30-45d": 0},
        needed={"7d": 8, "14d": 10, "30-45d": 8},
        shortfall={"7d": 0, "14d": 4, "30-45d": 0},
    )
    assert any("SHORT by 4" in line for line in report.summary_lines())


# ==========================================================
# fetch_cohort_windows — one narrow server query per cohort
# ==========================================================

def test_one_query_is_issued_per_cohort(monkeypatch, today):
    """
    Three narrow queries, not one broad scan. Gamma's offset ceiling means a
    broad scan can never reach the whole exchange.
    """
    from calibration.polymarket import client

    calls: list[dict] = []

    def fake_window(end_date_min, end_date_max, volume_min, max_markets=400):
        calls.append(
            {"min": end_date_min, "max": end_date_max, "volume": volume_min}
        )
        return []

    monkeypatch.setattr(client, "fetch_markets_in_window", fake_window)
    discovery_service.fetch_cohort_windows(today)

    assert len(calls) == 3
    assert calls[0]["min"].startswith("2026-07-30")     # 7d
    assert calls[1]["min"].startswith("2026-08-06")     # 14d
    assert calls[2]["min"].startswith("2026-08-22")     # 30-45d


def test_each_window_carries_its_own_liquidity_floor(monkeypatch, today):
    """
    The long-horizon floor is applied by the server, not after truncation —
    otherwise the cheaper bar never takes effect on a truncated result set.
    """
    from calibration import config
    from calibration.polymarket import client

    floors: list[float] = []
    monkeypatch.setattr(
        client, "fetch_markets_in_window",
        lambda end_date_min, end_date_max, volume_min, max_markets=400: floors.append(volume_min) or [],
    )
    discovery_service.fetch_cohort_windows(today)

    assert floors == [
        config.CALIBRATION_LIQUIDITY_MIN_7_14D_USD,
        config.CALIBRATION_LIQUIDITY_MIN_7_14D_USD,
        config.CALIBRATION_LIQUIDITY_MIN_30_45D_USD,
    ]


def test_markets_repeated_across_windows_are_deduplicated(monkeypatch, today):
    from calibration.polymarket import client

    shared = {"conditionId": "0xdup", "question": "Q?", "slug": "q"}
    monkeypatch.setattr(
        client, "fetch_markets_in_window",
        lambda **_kw: [shared],
    )
    collected = discovery_service.fetch_cohort_windows(today)
    assert len(collected) == 1


# ==========================================================
# manual_add_service.build_question — validation without a DB
# ==========================================================

_MARKET = {
    "slug": "will-x-happen",
    "question": "Will X happen before the deadline?",
    "conditionId": "0xabc123",
    "endDate": "2026-08-01T00:00:00Z",
    "volumeNum": 250_000,
}


def test_build_question_populates_everything_from_the_market():
    """
    The operator supplies the slug; the condition id is looked up. Requiring
    them to copy it by hand would be the most error-prone field in the system,
    and a wrong one silently attaches the question to someone else's market.
    """
    q = manual_add_service.build_question(
        slug="will-x-happen", category="financial", cohort="7d",
        operator_email="op@example.com", market=_MARKET,
    )
    assert q.polymarket_condition_id == "0xabc123"
    assert q.expected_resolution_date == date(2026, 8, 1)
    assert q.question_text == "Will X happen before the deadline?"
    assert q.added_by == "manual"
    assert q.added_by_operator == "op@example.com"
    assert q.liquidity_usd_at_pickup == Decimal("250000")


def test_unknown_slug_raises_an_operator_readable_error():
    with pytest.raises(manual_add_service.ManualAddError, match="No Polymarket market"):
        manual_add_service.build_question(
            slug="nope", category="financial", cohort="7d",
            operator_email="op@example.com", market=None or {},
        )


def test_market_without_condition_id_is_rejected():
    market = {k: v for k, v in _MARKET.items() if k != "conditionId"}
    with pytest.raises(manual_add_service.ManualAddError, match="no conditionId"):
        manual_add_service.build_question(
            slug="x", category="financial", cohort="7d",
            operator_email="op@example.com", market=market,
        )


def test_market_without_a_parseable_end_date_is_rejected():
    market = dict(_MARKET, endDate="whenever")
    with pytest.raises(manual_add_service.ManualAddError, match="end date"):
        manual_add_service.build_question(
            slug="x", category="financial", cohort="7d",
            operator_email="op@example.com", market=market,
        )


def test_market_without_question_text_is_rejected():
    market = dict(_MARKET, question="")
    with pytest.raises(manual_add_service.ManualAddError, match="no question text"):
        manual_add_service.build_question(
            slug="x", category="financial", cohort="7d",
            operator_email="op@example.com", market=market,
        )


def test_question_text_can_be_overridden():
    q = manual_add_service.build_question(
        slug="x", category="financial", cohort="7d",
        operator_email="op@example.com", market=dict(_MARKET, question=""),
        question_text="A clearer phrasing of the same question?",
    )
    assert q.question_text == "A clearer phrasing of the same question?"


def test_thin_market_warns_but_is_accepted(caplog):
    """
    A deliberate choice to measure a thin market is the operator's to make.
    They should be told its implied probability is noisier, not blocked.
    """
    market = dict(_MARKET, volumeNum=500)
    with caplog.at_level("WARNING"):
        q = manual_add_service.build_question(
            slug="x", category="financial", cohort="7d",
            operator_email="op@example.com", market=market,
        )
    assert q.liquidity_usd_at_pickup == Decimal("500")
    assert any("noisier" in r.message for r in caplog.records)


def test_cohort_is_taken_from_the_operator_not_derived():
    """
    Deriving it would silently reject anything falling in the gaps between
    cohort windows — exactly the questions an operator adds by hand.
    """
    q = manual_add_service.build_question(
        slug="x", category="ai", cohort="30-45d",
        operator_email="op@example.com", market=_MARKET,
    )
    assert q.cohort == "30-45d"
