"""
Gate 1 — model validation.

Focused on the invariants that protect the score, not on Pydantic's own
behaviour. The resolution outcome/numeric agreement is the important one: a
bug there produces wrong Brier scores that still look like plausible numbers.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from calibration.models import (
    TERMINAL_FORECAST_STATUSES,
    Forecast,
    MarketCandidate,
    Question,
    Resolution,
    Run,
)


# ==========================================================
# Question
# ==========================================================

def _question(**overrides) -> Question:
    base = dict(
        question_text="Will X happen?",
        polymarket_slug="will-x-happen",
        polymarket_condition_id="0xabc",
        category="geopolitical",
        cohort="7d",
        expected_resolution_date=date(2026, 8, 1),
        added_by="auto",
    )
    base.update(overrides)
    return Question(**base)


def test_question_builds_with_valid_fields():
    q = _question()
    assert q.status == "open"
    assert q.polymarket_url == "https://polymarket.com/event/will-x-happen"


def test_question_rejects_unknown_cohort():
    with pytest.raises(ValidationError):
        _question(cohort="90d")


def test_question_rejects_unknown_category():
    with pytest.raises(ValidationError):
        _question(category="sports")


def test_question_rejects_empty_text():
    with pytest.raises(ValidationError):
        _question(question_text="")


def test_auto_question_may_not_carry_operator_email():
    """Provenance must not be ambiguous — see Question._operator_only_for_manual."""
    with pytest.raises(ValidationError):
        _question(added_by="auto", added_by_operator="op@example.com")


def test_manual_question_may_carry_operator_email():
    q = _question(added_by="manual", added_by_operator="op@example.com")
    assert q.added_by_operator == "op@example.com"


def test_whitespace_is_stripped_from_slug():
    """A slug pasted from a browser often carries a trailing newline."""
    assert _question(polymarket_slug="  will-x-happen\n").polymarket_slug == "will-x-happen"


# ==========================================================
# Resolution — the outcome/numeric agreement
# ==========================================================

def _resolution(**overrides) -> Resolution:
    base = dict(
        question_id="q-1",
        resolved_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        outcome="YES",
        outcome_numeric=Decimal("1.0"),
        raw_resolution_data={"closed": True},
    )
    base.update(overrides)
    return Resolution(**base)


def test_yes_requires_numeric_one():
    assert _resolution().outcome_numeric == Decimal("1.0")
    with pytest.raises(ValidationError):
        _resolution(outcome="YES", outcome_numeric=Decimal("0.0"))


def test_no_requires_numeric_zero():
    assert _resolution(outcome="NO", outcome_numeric=Decimal("0.0")).outcome_numeric == 0
    with pytest.raises(ValidationError):
        _resolution(outcome="NO", outcome_numeric=Decimal("1.0"))


def test_ambiguous_must_have_no_numeric():
    """
    The failure this prevents: an AMBIGUOUS market coerced to 0.0 would score
    every attached forecast as though the answer had been NO — a silent,
    plausible, entirely wrong number.
    """
    r = _resolution(outcome="AMBIGUOUS", outcome_numeric=None)
    assert r.outcome_numeric is None
    assert r.is_scorable is False

    with pytest.raises(ValidationError):
        _resolution(outcome="AMBIGUOUS", outcome_numeric=Decimal("0.0"))


def test_yes_and_no_are_scorable():
    assert _resolution(outcome="YES", outcome_numeric=Decimal("1.0")).is_scorable
    assert _resolution(outcome="NO", outcome_numeric=Decimal("0.0")).is_scorable


# ==========================================================
# Forecast
# ==========================================================

def _forecast(**overrides) -> Forecast:
    base = dict(
        question_id="q-1",
        run_id="r-1",
        forecast_run_index=0,
        session_id="cal_abc123",
        query_doc_id="cal_abc123",
        idempotency_key="key-1",
    )
    base.update(overrides)
    return Forecast(**base)


def test_forecast_defaults_to_dispatched_and_is_not_terminal():
    f = _forecast()
    assert f.status == "dispatched"
    assert f.is_terminal is False
    assert f.is_scorable is False


def test_needs_clarification_is_terminal_but_not_scorable():
    """
    The state the pre-revision plan had no slot for. It must end polling, and
    it must not be counted as a failure or scored.
    """
    f = _forecast(status="needs_clarification")
    assert f.is_terminal is True
    assert f.is_scorable is False
    assert "needs_clarification" in TERMINAL_FORECAST_STATUSES


@pytest.mark.parametrize("status", ["completed", "failed", "timed_out", "needs_clarification"])
def test_all_terminal_statuses_stop_polling(status):
    assert _forecast(status=status).is_terminal is True


def test_completed_with_probability_is_scorable():
    f = _forecast(status="completed", final_probability=Decimal("0.67"))
    assert f.is_scorable is True


def test_completed_without_probability_is_not_scorable():
    """A completed session that somehow carried no probability cannot be scored."""
    assert _forecast(status="completed").is_scorable is False


@pytest.mark.parametrize("bad", [Decimal("-0.01"), Decimal("1.01")])
def test_probability_must_be_in_unit_interval(bad):
    with pytest.raises(ValidationError):
        _forecast(final_probability=bad)


@pytest.mark.parametrize("edge", [Decimal("0"), Decimal("1")])
def test_probability_boundaries_are_allowed(edge):
    assert _forecast(final_probability=edge).final_probability == edge


def test_session_id_is_required():
    """Minted at dispatch (plan A6), so it can never legitimately be absent."""
    with pytest.raises(ValidationError):
        _forecast(session_id="")


def test_run_index_cannot_be_negative():
    with pytest.raises(ValidationError):
        _forecast(forecast_run_index=-1)


# ==========================================================
# Run / MarketCandidate
# ==========================================================

def test_run_is_unfinished_until_finished_at_is_set():
    run = Run(run_type="initial_seed", triggered_by="cli")
    assert run.is_finished is False
    assert Run(
        run_type="initial_seed", triggered_by="cli",
        finished_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
    ).is_finished is True


def test_candidate_projects_to_auto_question():
    candidate = MarketCandidate(
        question_text="Will Y happen?",
        polymarket_slug="will-y",
        polymarket_condition_id="0xdef",
        category="financial",
        cohort="14d",
        expected_resolution_date=date(2026, 8, 8),
        volume_usd=Decimal("120000"),
        days_to_resolution=14,
    )
    q = candidate.to_question()
    assert q.added_by == "auto"
    assert q.added_by_operator is None
    assert q.status == "open"
    assert q.liquidity_usd_at_pickup == Decimal("120000")
