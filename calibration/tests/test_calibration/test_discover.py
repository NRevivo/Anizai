"""
Gate 2 — discovery filtering.

Pure functions over fixture payloads: no network, no database. The cohort
boundary cases matter most — an off-by-one there silently misclassifies the
horizon that the whole cohort comparison is built on.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from calibration.polymarket import discover

from .conftest import TODAY, make_market


# ==========================================================
# Cohort windows — boundaries and gaps
# ==========================================================

@pytest.mark.parametrize(
    "days,expected",
    [
        (5, "7d"), (7, "7d"), (9, "7d"),          # 7d window is inclusive 5-9
        (12, "14d"), (14, "14d"), (16, "14d"),    # 14d window is inclusive 12-16
        (28, "30-45d"), (35, "30-45d"), (46, "30-45d"),
    ],
)
def test_cohort_windows_are_inclusive_at_both_ends(days, expected):
    assert discover.cohort_for_days(days) == expected


@pytest.mark.parametrize(
    "cohort,expected_min,expected_max",
    [
        ("7d", "2026-07-30", "2026-08-03"),
        ("14d", "2026-08-06", "2026-08-10"),
        ("30-45d", "2026-08-22", "2026-09-09"),
    ],
)
def test_window_bounds_match_the_cohort_windows(cohort, expected_min, expected_max, today):
    """The date range handed to Gamma must be the cohort window, exactly."""
    date_min, date_max = discover.window_bounds(cohort, today=today)
    assert date_min.startswith(expected_min)
    assert date_max.startswith(expected_max)


def test_window_upper_bound_covers_the_whole_final_day(today):
    """
    A market resolving at 23:59 on the last day of the window is still in the
    window. A bare date bound would silently drop it.
    """
    _, date_max = discover.window_bounds("7d", today=today)
    assert date_max.endswith("T23:59:59Z")


def test_window_bounds_round_trip_through_cohort_for_days(today):
    """
    The server-side window and the client-side binning must agree, or the
    server returns markets the client then rejects as out-of-window.
    """
    from datetime import date as _date

    for cohort in ("7d", "14d", "30-45d"):
        date_min, date_max = discover.window_bounds(cohort, today=today)
        low_days = (_date.fromisoformat(date_min[:10]) - today).days
        high_days = (_date.fromisoformat(date_max[:10]) - today).days
        assert discover.cohort_for_days(low_days) == cohort
        assert discover.cohort_for_days(high_days) == cohort


@pytest.mark.parametrize("days", [0, 4, 10, 11, 17, 27, 47, 90])
def test_days_outside_every_window_have_no_cohort(days):
    """
    The gaps are deliberate. A market resolving in 10 days is neither a
    "1 week" nor a "2 week" question; forcing it into one blurs the horizon
    comparison the cohort split exists to make.
    """
    assert discover.cohort_for_days(days) is None


# ==========================================================
# End-date parsing — Gamma's several field names and formats
# ==========================================================

@pytest.mark.parametrize("key", ["endDate", "end_date_iso", "endDateIso", "end_date"])
def test_parse_end_date_accepts_each_known_field_name(key):
    market = make_market(days_out=7, end_date_key=key)
    assert discover.parse_end_date(market) == date(2026, 8, 1)


def test_parse_end_date_accepts_naive_timestamps_as_utc():
    assert discover.parse_end_date({"endDate": "2026-08-01T00:00:00"}) == date(2026, 8, 1)


def test_parse_end_date_returns_none_when_unparseable():
    """
    None makes the caller skip the market. Guessing a date would corrupt the
    horizon comparison, which is worse than losing one market.
    """
    assert discover.parse_end_date({"endDate": "next tuesday"}) is None
    assert discover.parse_end_date({}) is None


# ==========================================================
# Volume parsing
# ==========================================================

@pytest.mark.parametrize("raw", [250_000, "250000", 250_000.0, Decimal("250000")])
def test_parse_volume_accepts_every_type_gamma_returns(raw):
    assert discover.parse_volume({"volumeNum": raw}) == Decimal("250000")


def test_missing_volume_is_zero_so_the_market_fails_the_floor():
    """Unknown volume is treated as no volume — the conservative direction."""
    assert discover.parse_volume({}) == Decimal(0)
    assert discover.parse_volume({"volume": None}) == Decimal(0)


def test_unparseable_volume_is_zero():
    assert discover.parse_volume({"volumeNum": "lots"}) == Decimal(0)


# ==========================================================
# evaluate_market — one decision, with a reason
# ==========================================================

def test_qualifying_market_is_accepted(today):
    candidate, reason = discover.evaluate_market(
        make_market(days_out=7, volume=500_000, tags=["Politics"]), today=today
    )
    assert reason == "accepted"
    assert candidate is not None
    assert candidate.cohort == "7d"
    assert candidate.category == "geopolitical"
    assert candidate.days_to_resolution == 7


@pytest.mark.parametrize(
    "market_kwargs,expected_reason",
    [
        (dict(question=""), "no_question_text"),
        (dict(condition_id=""), "no_condition_id"),
        (dict(slug=""), "no_slug"),
        (dict(days_out=10), "outside_cohort_windows"),
        (dict(tags=["Sports"]), "blocked_category"),
        (dict(tags=["Weather"]), "unrecognised_category"),
        (dict(volume=100), "below_liquidity_floor"),
    ],
)
def test_rejections_name_the_failing_filter(market_kwargs, expected_reason, today):
    """
    The reason string is what makes discovery debuggable. "Found 3 of 10" is
    not actionable; "47 blocked by category, 12 outside windows" is.
    """
    candidate, reason = discover.evaluate_market(make_market(**market_kwargs), today=today)
    assert candidate is None
    assert reason.startswith(expected_reason)


def test_market_already_past_its_end_date_is_rejected(today):
    candidate, reason = discover.evaluate_market(make_market(days_out=-3), today=today)
    assert candidate is None
    assert reason == "already_past_end_date"


def test_unparseable_end_date_is_rejected(today):
    candidate, reason = discover.evaluate_market({
        "slug": "x", "question": "Q?", "conditionId": "0x1",
        "endDate": "soon", "volumeNum": 900_000, "tags": ["Politics"],
    }, today=today)
    assert candidate is None
    assert reason == "unparseable_end_date"


def test_long_horizon_cohort_uses_the_lower_liquidity_floor(today):
    """
    $30k qualifies at 30-45d (floor $25k) but not at 7d (floor $50k). The
    asymmetry exists because there are far fewer high-volume long-horizon
    markets; holding them to the same bar would starve the cohort.
    """
    long_market = make_market(days_out=35, volume=30_000, tags=["Politics"])
    short_market = make_market(days_out=7, volume=30_000, tags=["Politics"])

    assert discover.evaluate_market(long_market, today=today)[1] == "accepted"
    assert discover.evaluate_market(short_market, today=today)[1].startswith(
        "below_liquidity_floor"
    )


# ==========================================================
# find_candidates — the batch path
# ==========================================================

def test_find_candidates_separates_accepted_from_rejected(gamma_markets, today):
    candidates, rejections = discover.find_candidates(gamma_markets, today=today)

    assert len(candidates) == 4
    assert {c.cohort for c in candidates} == {"7d", "14d", "30-45d"}

    assert rejections["blocked_category"] == 1
    assert rejections["unrecognised_category"] == 1
    assert rejections["outside_cohort_windows"] == 1
    assert rejections["below_liquidity_floor"] == 1
    assert rejections["no_condition_id"] == 1


def test_candidates_are_sorted_by_volume_descending(gamma_markets, today):
    """Callers taking the top N get the most liquid, least noisy markets."""
    candidates, _ = discover.find_candidates(gamma_markets, today=today)
    volumes = [c.volume_usd for c in candidates]
    assert volumes == sorted(volumes, reverse=True)


def test_empty_input_yields_empty_output():
    candidates, rejections = discover.find_candidates([], today=TODAY)
    assert candidates == []
    assert rejections == {}


# ==========================================================
# select_for_cohorts
# ==========================================================

def test_selection_respects_per_cohort_need(gamma_markets, today):
    candidates, _ = discover.find_candidates(gamma_markets, today=today)
    selected = discover.select_for_cohorts(candidates, {"7d": 1, "14d": 1, "30-45d": 0})

    assert len(selected) == 2
    assert {c.cohort for c in selected} == {"7d", "14d"}


def test_cohort_at_target_receives_nothing(gamma_markets, today):
    candidates, _ = discover.find_candidates(gamma_markets, today=today)
    selected = discover.select_for_cohorts(candidates, {"7d": 0, "14d": 0, "30-45d": 0})
    assert selected == []


def test_selection_takes_the_most_liquid_first(today):
    markets = [
        make_market(slug="a", condition_id="0x1", days_out=7, volume=100_000),
        make_market(slug="b", condition_id="0x2", days_out=7, volume=900_000),
        make_market(slug="c", condition_id="0x3", days_out=7, volume=500_000),
    ]
    candidates, _ = discover.find_candidates(markets, today=today)
    selected = discover.select_for_cohorts(candidates, {"7d": 2})

    assert [c.volume_usd for c in selected] == [Decimal("900000"), Decimal("500000")]


def test_thin_pool_returns_fewer_than_requested_without_error(gamma_markets, today):
    """
    A cohort that could not be filled is a real finding about market
    availability, reported by the caller — not an exception here.
    """
    candidates, _ = discover.find_candidates(gamma_markets, today=today)
    selected = discover.select_for_cohorts(candidates, {"7d": 10, "14d": 10, "30-45d": 10})
    assert len(selected) == 4


def test_negative_need_is_treated_as_zero(gamma_markets, today):
    candidates, _ = discover.find_candidates(gamma_markets, today=today)
    assert discover.select_for_cohorts(candidates, {"7d": -5, "14d": 0, "30-45d": 0}) == []
