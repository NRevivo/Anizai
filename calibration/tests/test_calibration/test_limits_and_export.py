"""
Gate 2 — the dispatch ceilings and the results export.

The ceilings protect a quota shared with the live product, so they are tested
against the way they actually fail: not "does the cap work on one call" but
"does it hold when something retries in a loop".

The export is tested hardest in its empty and near-empty states, because that
is how the system will be demonstrated — and a submission folder full of
zero-byte files reads as broken rather than as early.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest


class FakeQuestion:
    def __init__(self, i):
        self.id = f"q-{i}"
        self.polymarket_condition_id = f"0x{i:04d}"


@pytest.fixture
def dispatch_harness(monkeypatch):
    """Wire dispatch_service to in-memory stand-ins; return a control dict."""
    from calibration.services import dispatch_service

    state = {"dispatched": [], "already_today": 0}

    monkeypatch.setattr(dispatch_service, "has_open_forecast", lambda qid: False)
    monkeypatch.setattr(dispatch_service.runs_repo, "start", lambda **kw: "run-1")
    monkeypatch.setattr(dispatch_service.runs_repo, "finish", lambda *a, **kw: None)
    monkeypatch.setattr(
        dispatch_service.forecasts_repo,
        "count_dispatched_since",
        lambda hours=24: state["already_today"],
    )

    def fake_dispatch(question, run_id):
        state["dispatched"].append(question.id)
        return f"cal_{question.id}"

    monkeypatch.setattr(dispatch_service, "dispatch_question", fake_dispatch)

    def set_questions(n):
        monkeypatch.setattr(
            dispatch_service.questions_repo,
            "list_questions",
            lambda **kw: [FakeQuestion(i) for i in range(n)],
        )

    state["set_questions"] = set_questions
    return state


# ==========================================================
# Per-run ceiling
# ==========================================================

def test_the_default_run_cap_is_small_enough_for_a_sanity_check():
    """
    A default that quietly sends 30 forecasts is a default that eventually
    sends 30 forecasts by accident, against a quota shared with the live
    product. A "does it work" run needs 2-3.
    """
    from calibration import config

    assert config.CALIBRATION_MAX_FORECASTS_PER_RUN <= 3


def test_a_run_is_truncated_to_the_per_run_cap(dispatch_harness, monkeypatch):
    from calibration import config
    from calibration.services import dispatch_service

    monkeypatch.setattr(config, "CALIBRATION_MAX_FORECASTS_PER_RUN", 3)
    monkeypatch.setattr(config, "CALIBRATION_MAX_FORECASTS_PER_DAY", 100)
    dispatch_harness["set_questions"](10)

    report = dispatch_service.dispatch_questions()
    assert report.dispatched == 3
    assert report.truncated_by_ceiling == 7


# ==========================================================
# Rolling 24-hour ceiling — the one that survives a stuck caller
# ==========================================================

def test_the_daily_ceiling_limits_what_a_run_may_add(dispatch_harness, monkeypatch):
    from calibration import config
    from calibration.services import dispatch_service

    monkeypatch.setattr(config, "CALIBRATION_MAX_FORECASTS_PER_RUN", 10)
    monkeypatch.setattr(config, "CALIBRATION_MAX_FORECASTS_PER_DAY", 30)
    dispatch_harness["already_today"] = 28
    dispatch_harness["set_questions"](10)

    report = dispatch_service.dispatch_questions()
    assert report.dispatched == 2, "should stop at the daily budget, not the run cap"
    assert report.truncated_by_ceiling == 8


def test_reaching_the_daily_ceiling_raises_rather_than_silently_doing_nothing(
    dispatch_harness, monkeypatch
):
    """
    A hard stop, not a quiet zero. Reaching this means something is looping,
    and dispatching zero forever would let it keep looping unnoticed — which
    is exactly the failure the ceiling exists to surface.
    """
    from calibration import config
    from calibration.services import dispatch_service

    monkeypatch.setattr(config, "CALIBRATION_MAX_FORECASTS_PER_DAY", 30)
    dispatch_harness["already_today"] = 30
    dispatch_harness["set_questions"](5)

    with pytest.raises(RuntimeError, match="Daily dispatch ceiling"):
        dispatch_service.dispatch_questions()

    assert dispatch_harness["dispatched"] == []


def test_a_retry_loop_cannot_exceed_the_daily_budget(dispatch_harness, monkeypatch):
    """
    The scenario the per-run cap does not cover: a caller that dispatches the
    maximum, fails to harvest, and immediately tries again. Each individual
    run is legal; the aggregate is what must be bounded.
    """
    from calibration import config
    from calibration.services import dispatch_service

    monkeypatch.setattr(config, "CALIBRATION_MAX_FORECASTS_PER_RUN", 3)
    monkeypatch.setattr(config, "CALIBRATION_MAX_FORECASTS_PER_DAY", 10)
    dispatch_harness["set_questions"](3)

    total = 0
    for _ in range(20):                     # a caller stuck in a loop
        try:
            report = dispatch_service.dispatch_questions()
        except RuntimeError:
            break                            # the ceiling stopped it
        total += report.dispatched
        dispatch_harness["already_today"] += report.dispatched

    assert total <= 10, f"a retry loop emitted {total} forecasts against a cap of 10"


def test_the_run_summary_reports_the_remaining_budget(dispatch_harness, monkeypatch):
    from calibration import config
    from calibration.services import dispatch_service

    monkeypatch.setattr(config, "CALIBRATION_MAX_FORECASTS_PER_DAY", 30)
    dispatch_harness["already_today"] = 5
    dispatch_harness["set_questions"](2)

    report = dispatch_service.dispatch_questions()
    assert any("24h budget" in line for line in report.summary_lines())
    assert any("7/30" in line for line in report.summary_lines())


# ==========================================================
# Export
# ==========================================================

@pytest.fixture
def empty_export(monkeypatch, tmp_path):
    """An export against a system where nothing has happened yet."""
    from calibration.metrics import snapshots
    from calibration.repos import forecasts as f_repo
    from calibration.repos import questions as q_repo
    from calibration.repos import resolutions as r_repo
    from calibration.repos import runs as runs_repo

    monkeypatch.setattr(snapshots, "load_scorable_rows", lambda: [])
    monkeypatch.setattr(q_repo, "list_questions", lambda **kw: [])
    monkeypatch.setattr(r_repo, "count_by_outcome", lambda: {"YES": 0, "NO": 0, "AMBIGUOUS": 0})
    monkeypatch.setattr(
        f_repo, "count_by_status",
        lambda: {"dispatched": 0, "completed": 0, "failed": 0,
                 "timed_out": 0, "needs_clarification": 0},
    )
    monkeypatch.setattr(runs_repo, "list_runs", lambda **kw: [])
    return tmp_path / "results"


def test_export_produces_every_file_even_with_no_data(empty_export):
    """
    The state the system will be demonstrated in. A submission folder must not
    depend on the measurement having finished.
    """
    from calibration.export import export_all

    export_all(empty_export)

    for name in (
        "summary.json", "forecasts.csv", "questions.csv",
        "calibration_curve.csv", "cohort_brier.csv", "improvement.csv",
        "source_contribution.csv", "runs.csv", "metrics.json",
    ):
        assert (empty_export / name).exists(), f"{name} was not written"


def test_empty_csvs_carry_headers_rather_than_being_zero_bytes(empty_export):
    """
    A header-only file says "this measurement exists and is empty". A
    zero-byte file says "something broke". They are read very differently.
    """
    from calibration.export import export_all

    export_all(empty_export)

    for name in ("forecasts.csv", "questions.csv", "improvement.csv"):
        content = (empty_export / name).read_text(encoding="utf-8").strip()
        assert content, f"{name} is empty — it should still carry its header"
        assert "," in content.splitlines()[0]


def test_the_calibration_curve_exports_all_five_buckets_when_empty(empty_export):
    """
    Five rows with n=0 draw a legible, honest chart. Zero rows draw nothing
    and look like a failure.
    """
    from calibration.export import export_all

    export_all(empty_export)

    rows = list(csv.DictReader((empty_export / "calibration_curve.csv").open(encoding="utf-8")))
    assert len(rows) == 5
    assert all(r["n"] == "0" for r in rows)
    assert all(r["mean_predicted"] == "" for r in rows)


def test_the_summary_explains_an_empty_result_in_words(empty_export):
    """
    Whoever opens this folder was not in the conversation that produced it.
    """
    from calibration.export import export_all

    export_all(empty_export)

    summary = json.loads((empty_export / "summary.json").read_text(encoding="utf-8"))
    assert summary["scorable_forecasts"] == 0
    assert summary["mean_brier"] is None
    text = " ".join(summary["how_to_read_this"])
    assert "expected state, not a failure" in text
    assert "0.25" in text


def test_the_summary_flags_a_small_sample(monkeypatch, tmp_path):
    """Between one and nine scored forecasts, the numbers need a caveat."""
    from calibration import export

    notes = export._interpretation({"n": 3}, [])
    assert any("indicative at best" in n for n in notes)


def test_no_score_is_ever_rendered_as_zero(empty_export):
    """
    0.0 is a perfect Brier score. An absent score written as 0 would claim a
    flawless forecaster where there is no data at all.
    """
    from calibration.export import export_all

    export_all(empty_export)

    summary = json.loads((empty_export / "summary.json").read_text(encoding="utf-8"))
    assert summary["mean_brier"] is None
    assert summary["skill_vs_coin_flip"] is None

    rows = list(csv.DictReader((empty_export / "cohort_brier.csv").open(encoding="utf-8")))
    assert all(r["mean_brier"] == "" for r in rows)


def test_export_is_rerunnable(empty_export):
    """Everything is derived, so a second run overwrites cleanly."""
    from calibration.export import export_all

    export_all(empty_export)
    first = (empty_export / "questions.csv").read_text(encoding="utf-8")
    export_all(empty_export)
    assert (empty_export / "questions.csv").read_text(encoding="utf-8") == first
