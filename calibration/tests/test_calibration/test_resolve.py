"""
Gate 2 — resolution parsing.

This module produces the ground truth every Brier score is measured against,
so the tests are weighted toward the ways it could be wrong in the expensive
direction: calling a market resolved when it is not, or assigning an outcome
to a market that has no clean answer.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from calibration.polymarket.resolve import SETTLE_WINDOW, parse_resolution


# ==========================================================
# Clean resolutions
# ==========================================================

def test_winner_flag_on_yes(clob_resolved_yes, now):
    reading = parse_resolution(clob_resolved_yes, now=now)
    assert reading.resolved is True
    assert reading.outcome == "YES"
    assert reading.outcome_numeric == Decimal("1.0")
    assert reading.resolved_at is not None


def test_winner_flag_on_no(clob_resolved_no, now):
    reading = parse_resolution(clob_resolved_no, now=now)
    assert reading.resolved is True
    assert reading.outcome == "NO"
    assert reading.outcome_numeric == Decimal("0.0")


def test_raw_payload_is_preserved_for_audit(clob_resolved_yes, now):
    """raw_resolution_data is the forensic trail if Polymarket's shape drifts."""
    assert parse_resolution(clob_resolved_yes, now=now).raw == clob_resolved_yes


# ==========================================================
# Not resolved
# ==========================================================

def test_open_market_is_not_resolved(clob_still_open, now):
    reading = parse_resolution(clob_still_open, now=now)
    assert reading.resolved is False
    assert reading.outcome is None


def test_missing_payload_is_not_resolved(now):
    """A 404 means the market is gone, not that it resolved."""
    reading = parse_resolution(None, now=now)
    assert reading.resolved is False
    assert "no_payload" in reading.detail


def test_closed_without_winner_or_settled_prices_is_not_resolved(now):
    reading = parse_resolution({"closed": True, "outcomePrices": ["0.6", "0.4"]}, now=now)
    assert reading.resolved is False


def test_every_reading_explains_itself(clob_still_open, now):
    """A resolver that says 'not resolved' without saying why is undebuggable."""
    assert parse_resolution(clob_still_open, now=now).detail


# ==========================================================
# Ambiguity — never coerced to an outcome
# ==========================================================

def test_disputed_market_is_ambiguous_even_with_a_winner_flag(clob_disputed, now):
    """
    Ambiguity is checked before the winner path on purpose: a disputed market
    that also carries a winner flag must not be recorded as clean ground truth.
    """
    reading = parse_resolution(clob_disputed, now=now)
    assert reading.resolved is True
    assert reading.outcome == "AMBIGUOUS"
    assert reading.outcome_numeric is None


@pytest.mark.parametrize("flag", ["void", "invalid"])
def test_void_and_invalid_markets_are_ambiguous(flag, now):
    reading = parse_resolution({"closed": True, flag: True}, now=now)
    assert reading.outcome == "AMBIGUOUS"
    assert reading.outcome_numeric is None


def test_two_winners_is_ambiguous_not_a_coin_flip(now):
    market = {
        "closed": True,
        "tokens": [
            {"outcome": "Yes", "winner": True},
            {"outcome": "No", "winner": True},
        ],
        "closedTime": "2026-07-20T09:00:00Z",
    }
    reading = parse_resolution(market, now=now)
    assert reading.outcome == "AMBIGUOUS"
    assert reading.outcome_numeric is None


def test_unrecognised_winner_label_is_ambiguous(now):
    """A multi-outcome market is not a binary question and cannot be scored."""
    market = {
        "closed": True,
        "tokens": [{"outcome": "Candidate C", "winner": True}],
        "closedTime": "2026-07-20T09:00:00Z",
    }
    reading = parse_resolution(market, now=now)
    assert reading.outcome == "AMBIGUOUS"
    assert reading.outcome_numeric is None


# ==========================================================
# Settle-window guard — the expensive failure mode
# ==========================================================

def test_settled_prices_inside_the_window_are_not_yet_trusted(
    clob_settled_prices_recent, now
):
    """
    Prices can touch 1.0/0.0 in the final minutes of an active market without
    that being a settlement. Reading such a moment as resolution would assign
    ground truth to a market that has not resolved.
    """
    reading = parse_resolution(clob_settled_prices_recent, now=now)
    assert reading.resolved is False
    assert "settle window" in reading.detail


def test_settled_prices_past_the_window_are_trusted(clob_settled_prices_old, now):
    reading = parse_resolution(clob_settled_prices_old, now=now)
    assert reading.resolved is True
    assert reading.outcome == "NO"          # prices are [0.0, 1.0]
    assert reading.outcome_numeric == Decimal("0.0")


def test_settled_prices_yes_direction_past_the_window(now):
    market = {
        "closed": True,
        "outcomePrices": ["1.0", "0.0"],
        "closedTime": (now - SETTLE_WINDOW - timedelta(hours=1))
        .isoformat().replace("+00:00", "Z"),
    }
    reading = parse_resolution(market, now=now)
    assert reading.outcome == "YES"
    assert reading.outcome_numeric == Decimal("1.0")


def test_settled_prices_without_a_close_timestamp_wait(now):
    """
    No timestamp means the settle-window guard cannot be applied. Waiting
    costs one polling cycle; guessing corrupts a score permanently.
    """
    reading = parse_resolution({"closed": True, "outcomePrices": ["1", "0"]}, now=now)
    assert reading.resolved is False
    assert "settle-window guard" in reading.detail


def test_near_settled_prices_count_as_settled(now):
    """Polymarket has returned '0.999' and '1' for the same settled state."""
    market = {
        "closed": True,
        "outcomePrices": ["0.999", "0.001"],
        "closedTime": (now - timedelta(days=3)).isoformat().replace("+00:00", "Z"),
    }
    assert parse_resolution(market, now=now).outcome == "YES"


def test_mid_range_prices_on_a_closed_market_do_not_resolve(now):
    market = {
        "closed": True,
        "outcomePrices": ["0.8", "0.2"],
        "closedTime": (now - timedelta(days=3)).isoformat().replace("+00:00", "Z"),
    }
    assert parse_resolution(market, now=now).resolved is False


# ==========================================================
# Shape tolerance
# ==========================================================

def test_outcome_prices_accepted_as_a_json_encoded_string(now):
    """CLOB returns this field as a JSON string on some endpoints."""
    market = {
        "closed": True,
        "outcomePrices": '["1.0", "0.0"]',
        "closedTime": (now - timedelta(days=3)).isoformat().replace("+00:00", "Z"),
    }
    assert parse_resolution(market, now=now).outcome == "YES"


def test_malformed_outcome_prices_do_not_raise(now):
    assert parse_resolution({"closed": True, "outcomePrices": "not json"}, now=now).resolved is False
    assert parse_resolution({"closed": True, "outcomePrices": {"a": 1}}, now=now).resolved is False


def test_outcomes_key_is_accepted_as_an_alias_for_tokens(now):
    market = {
        "closed": True,
        "outcomes": [{"outcome": "Yes", "winner": True}],
        "closedTime": "2026-07-20T09:00:00Z",
    }
    assert parse_resolution(market, now=now).outcome == "YES"


def test_true_false_labels_are_accepted_as_yes_no(now):
    market = {
        "closed": True,
        "tokens": [{"outcome": "True", "winner": True}],
        "closedTime": "2026-07-20T09:00:00Z",
    }
    assert parse_resolution(market, now=now).outcome == "YES"
