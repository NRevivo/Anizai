"""
Gate 1/2 — the scoring layer.

Pure functions over synthetic data. These are the numbers the whole project
exists to produce, so the tests lean hard on the cases where a plausible-but-
wrong answer is possible: bucket boundaries, empty samples, and the AMBIGUOUS
exclusion.
"""

from __future__ import annotations

import pytest

from calibration.metrics import (
    brier,
    calibration_curve,
    cohort_brier,
    improvement_curve,
    snapshots,
    source_contribution,
)


# ==========================================================
# Brier
# ==========================================================

@pytest.mark.parametrize(
    "probability,outcome,expected",
    [
        (1.0, 1.0, 0.0),      # perfect confident YES
        (0.0, 0.0, 0.0),      # perfect confident NO
        (0.0, 1.0, 1.0),      # confidently, completely wrong
        (1.0, 0.0, 1.0),
        (0.5, 1.0, 0.25),     # the coin-flip baseline
        (0.5, 0.0, 0.25),
        (0.8, 1.0, 0.04000000000000007),
        (0.7, 0.0, 0.48999999999999994),
    ],
)
def test_brier_edge_cases(probability, outcome, expected):
    assert brier.compute(probability, outcome) == pytest.approx(expected)


def test_brier_rewards_confidence_only_when_right():
    """The property that makes Brier the right metric for a probability."""
    assert brier.compute(0.9, 1.0) < brier.compute(0.6, 1.0)
    assert brier.compute(0.9, 0.0) > brier.compute(0.6, 0.0)


@pytest.mark.parametrize("bad", [-0.01, 1.01, 2.0])
def test_brier_rejects_out_of_range_probability(bad):
    """
    A hard error, not a clamp. A probability outside [0,1] means extraction
    upstream is broken, and clamping would turn broken input into a real-
    looking score.
    """
    with pytest.raises(ValueError, match="within"):
        brier.compute(bad, 1.0)


@pytest.mark.parametrize("bad", [0.5, -1.0, 0.99])
def test_brier_refuses_a_non_binary_outcome(bad):
    """
    The guard against scoring an AMBIGUOUS resolution. There is no numeric
    ground truth for one, and inventing 0.0 would score every attached
    forecast as though the answer had been NO.
    """
    with pytest.raises(ValueError, match="0.0 or 1.0"):
        brier.compute(0.5, bad)


def test_mean_of_nothing_is_none_not_zero():
    """
    0.0 is a perfect score. An empty sample rendered as a perfect score is the
    single most misleading number this system could emit.
    """
    assert brier.mean([]) is None
    assert brier.mean([0.1, 0.3]) == pytest.approx(0.2)


def test_std_needs_at_least_two_points():
    assert brier.std([0.5]) is None
    assert brier.std([]) is None
    assert brier.std([0.0, 1.0]) == pytest.approx(0.5)


def test_skill_score_against_the_coin_flip_baseline():
    assert brier.skill_score(0.25) == pytest.approx(0.0)     # same as a coin
    assert brier.skill_score(0.0) == pytest.approx(1.0)      # perfect
    assert brier.skill_score(0.5) == pytest.approx(-1.0)     # worse than a coin
    assert brier.skill_score(None) is None


# ==========================================================
# Calibration curve — bucket boundaries
# ==========================================================

@pytest.mark.parametrize(
    "probability,expected",
    [
        (0.0, "0.0-0.2"),
        (0.19, "0.0-0.2"),
        (0.2, "0.2-0.4"),      # half-open: the boundary goes UP
        (0.39, "0.2-0.4"),
        (0.4, "0.4-0.6"),
        (0.6, "0.6-0.8"),
        (0.79, "0.6-0.8"),
        (0.8, "0.8-1.0"),
        (1.0, "0.8-1.0"),      # the closed upper end
    ],
)
def test_bucket_boundaries(probability, expected):
    assert calibration_curve.assign_bucket(probability) == expected


def test_probability_of_exactly_one_is_not_lost():
    """
    Without the closed final bucket, 1.0 would belong to no bucket and vanish
    from the curve silently.
    """
    assert calibration_curve.assign_bucket(1.0) is not None


@pytest.mark.parametrize("bad", [-0.1, 1.1])
def test_out_of_range_probability_has_no_bucket(bad):
    assert calibration_curve.assign_bucket(bad) is None


def test_curve_reports_all_five_buckets_even_when_empty():
    """
    An empty bucket is information: the agent never forecast in that band.
    A curve showing three points would look like a complete picture.
    """
    payload = calibration_curve.compute([(0.65, 1.0), (0.7, 1.0)])
    assert len(payload["points"]) == 5
    assert [p["bucket"] for p in payload["points"]] == [
        "0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"
    ]
    empty = next(p for p in payload["points"] if p["bucket"] == "0.0-0.2")
    assert empty["count"] == 0
    assert empty["mean_predicted"] is None
    assert empty["actual_yes_rate"] is None


def test_a_perfectly_calibrated_bucket_sits_on_the_diagonal():
    """Four forecasts at 0.75, three of which resolved YES -> actual 0.75."""
    payload = calibration_curve.compute(
        [(0.75, 1.0), (0.75, 1.0), (0.75, 1.0), (0.75, 0.0)]
    )
    bucket = next(p for p in payload["points"] if p["bucket"] == "0.6-0.8")
    assert bucket["count"] == 4
    assert bucket["mean_predicted"] == pytest.approx(0.75)
    assert bucket["actual_yes_rate"] == pytest.approx(0.75)


def test_overconfident_agent_shows_actual_below_predicted():
    """The signal the curve exists to surface."""
    payload = calibration_curve.compute([(0.9, 1.0), (0.9, 0.0), (0.9, 0.0), (0.9, 0.0)])
    bucket = next(p for p in payload["points"] if p["bucket"] == "0.8-1.0")
    assert bucket["mean_predicted"] > bucket["actual_yes_rate"]


def test_wilson_interval_stays_inside_zero_one():
    """
    The reason for Wilson over the normal approximation: at n=1 the normal
    interval runs outside [0,1], which is nonsense on a rate.
    """
    for successes, total in [(0, 1), (1, 1), (0, 3), (3, 3), (5, 10)]:
        lower, upper = calibration_curve.wilson_interval(successes, total)
        assert 0.0 <= lower <= upper <= 1.0


def test_wilson_interval_of_an_empty_bucket_is_total_ignorance():
    assert calibration_curve.wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_interval_narrows_as_n_grows():
    narrow = calibration_curve.wilson_interval(50, 100)
    wide = calibration_curve.wilson_interval(1, 2)
    assert (narrow[1] - narrow[0]) < (wide[1] - wide[0])


def test_curve_renders_small_sample_flag():
    payload = calibration_curve.compute([(0.65, 1.0)])
    assert any("n<10" in line for line in calibration_curve.render_ascii(payload))


def test_empty_curve_renders_without_crashing():
    payload = calibration_curve.compute([])
    assert payload["total_forecasts"] == 0
    assert payload["aggregate_brier"] is None
    assert calibration_curve.render_ascii(payload)


# ==========================================================
# Cohort Brier
# ==========================================================

def test_cohort_brier_splits_by_horizon():
    payload = cohort_brier.compute(
        [
            ("7d", 0.9, 1.0),     # good
            ("7d", 0.8, 1.0),     # good
            ("30-45d", 0.5, 1.0), # mediocre
            ("30-45d", 0.4, 1.0), # bad
        ]
    )
    short = cohort_brier.cohort_of(payload["items"], "7d")
    long = cohort_brier.cohort_of(payload["items"], "30-45d")
    assert short["mean_brier"] < long["mean_brier"]


def test_cohort_brier_always_reports_all_three_plus_all():
    payload = cohort_brier.compute([("7d", 0.9, 1.0)])
    cohorts = [i["cohort"] for i in payload["items"]]
    assert cohorts == ["7d", "14d", "30-45d", "all"]


def test_empty_cohort_reports_zero_not_omission():
    """A report covering one cohort must not look like a report on the system."""
    payload = cohort_brier.compute([("7d", 0.9, 1.0)])
    empty = cohort_brier.cohort_of(payload["items"], "14d")
    assert empty["n"] == 0
    assert empty["mean_brier"] is None


def test_small_sample_is_flagged():
    payload = cohort_brier.compute([("7d", 0.9, 1.0), ("7d", 0.8, 1.0)])
    assert cohort_brier.cohort_of(payload["items"], "7d")["small_sample"] is True


def test_all_aggregate_covers_every_cohort():
    payload = cohort_brier.compute([("7d", 0.9, 1.0), ("14d", 0.9, 1.0)])
    assert cohort_brier.cohort_of(payload["items"], "all")["n"] == 2


# ==========================================================
# Improvement curve
# ==========================================================

def _forecast_row(qid, cohort, index, probability, outcome, version="v1", resolved="2026-08-01"):
    return (qid, cohort, index, probability, outcome, version, resolved)


def test_improvement_pairs_first_and_last_forecast():
    payload = improvement_curve.compute(
        [
            _forecast_row("q1", "7d", 0, 0.55, 1.0),   # original: Brier 0.2025
            _forecast_row("q1", "7d", 1, 0.72, 1.0),   # latest:   Brier 0.0784
        ]
    )
    assert payload["n_paired_questions"] == 1
    point = payload["points"][0]
    assert point["improved"] is True
    assert point["delta"] > 0
    assert point["original_probability"] == 0.55
    assert point["latest_probability"] == 0.72


def test_a_worse_re_forecast_shows_a_negative_delta():
    payload = improvement_curve.compute(
        [
            _forecast_row("q1", "7d", 0, 0.9, 1.0),
            _forecast_row("q1", "7d", 1, 0.4, 1.0),
        ]
    )
    assert payload["points"][0]["improved"] is False
    assert payload["points"][0]["delta"] < 0


def test_single_forecast_questions_are_excluded_not_imputed():
    """
    With one forecast there is no before-and-after. Imputing one would
    manufacture a delta from nothing.
    """
    payload = improvement_curve.compute([_forecast_row("q1", "7d", 0, 0.6, 1.0)])
    assert payload["n_paired_questions"] == 0
    assert payload["single_forecast_questions"] == 1
    assert payload["mean_delta"] is None


def test_middle_forecasts_are_ignored_only_first_and_last_count():
    payload = improvement_curve.compute(
        [
            _forecast_row("q1", "7d", 0, 0.50, 1.0),
            _forecast_row("q1", "7d", 1, 0.10, 1.0),   # a bad middle pass
            _forecast_row("q1", "7d", 2, 0.95, 1.0),
        ]
    )
    point = payload["points"][0]
    assert point["original_run_index"] == 0
    assert point["latest_run_index"] == 2
    assert point["latest_probability"] == 0.95


def test_small_samples_are_marked_not_interpretable():
    """
    The guard against reading noise as a trend. Three unlucky questions read
    as "the agent regressed" with the same visual weight as thirty.
    """
    payload = improvement_curve.compute(
        [
            _forecast_row("q1", "7d", 0, 0.9, 0.0),
            _forecast_row("q1", "7d", 1, 0.95, 0.0),
        ]
    )
    assert payload["interpretable"] is False
    assert any("NOT YET INTERPRETABLE" in ln for ln in improvement_curve.render_ascii(payload))


def test_enough_pairs_becomes_interpretable():
    rows = []
    for i in range(improvement_curve.MIN_INTERPRETABLE_N):
        rows.append(_forecast_row(f"q{i}", "7d", 0, 0.5, 1.0))
        rows.append(_forecast_row(f"q{i}", "7d", 1, 0.8, 1.0))
    payload = improvement_curve.compute(rows)
    assert payload["n_paired_questions"] == improvement_curve.MIN_INTERPRETABLE_N
    assert payload["interpretable"] is True


def test_agent_version_pair_is_recorded_verbatim():
    """Never parsed or ordered — since Sprint 26 it carries a git sha."""
    payload = improvement_curve.compute(
        [
            _forecast_row("q1", "7d", 0, 0.5, 1.0, version="0.5.0-sprint26+55e8093"),
            _forecast_row("q1", "7d", 1, 0.8, 1.0, version="0.6.0-sprint27+abc1234"),
        ]
    )
    assert payload["points"][0]["agent_version_pair"] == [
        "0.5.0-sprint26+55e8093", "0.6.0-sprint27+abc1234"
    ]


def test_empty_improvement_renders_without_crashing():
    payload = improvement_curve.compute([])
    assert payload["n_paired_questions"] == 0
    assert improvement_curve.render_ascii(payload)


# ==========================================================
# Source contribution
# ==========================================================

def _summary(*vaults):
    return {"vault_types_present": list(vaults)}


def test_source_contribution_compares_present_against_absent():
    payload = source_contribution.compute(
        [
            (_summary("knowledge"), 0.9, 1.0),   # with knowledge: good
            (_summary("knowledge"), 0.85, 1.0),
            (_summary("social"), 0.4, 1.0),      # without knowledge: bad
            (_summary("social"), 0.3, 1.0),
        ]
    )
    knowledge = next(i for i in payload["items"] if i["vault_type"] == "knowledge")
    assert knowledge["n_with"] == 2
    assert knowledge["n_without"] == 2
    assert knowledge["delta"] < 0          # negative = helps
    assert knowledge["helps"] is True


def test_every_known_vault_is_reported_even_if_never_used():
    """
    A vault that contributed to nothing is a finding — it is switched off, or
    its retrieval is failing. Omitting it would hide that.
    """
    payload = source_contribution.compute([(_summary("knowledge"), 0.9, 1.0)])
    reported = {i["vault_type"] for i in payload["items"]}
    from calibration.evidence_projection import KNOWN_VAULT_TYPES

    assert reported == set(KNOWN_VAULT_TYPES)


def test_small_groups_are_marked_not_comparable():
    payload = source_contribution.compute(
        [(_summary("knowledge"), 0.9, 1.0), (_summary("social"), 0.4, 1.0)]
    )
    knowledge = next(i for i in payload["items"] if i["vault_type"] == "knowledge")
    assert knowledge["comparable"] is False


def test_a_vault_present_everywhere_has_no_comparison_group():
    payload = source_contribution.compute(
        [(_summary("knowledge"), 0.9, 1.0) for _ in range(10)]
    )
    knowledge = next(i for i in payload["items"] if i["vault_type"] == "knowledge")
    assert knowledge["n_without"] == 0
    assert knowledge["mean_brier_without"] is None
    assert knowledge["delta"] is None


def test_missing_or_malformed_summary_counts_as_no_vaults():
    payload = source_contribution.compute(
        [(None, 0.9, 1.0), ("not a dict", 0.8, 1.0), ({}, 0.7, 1.0)]
    )
    knowledge = next(i for i in payload["items"] if i["vault_type"] == "knowledge")
    assert knowledge["n_with"] == 0
    assert knowledge["n_without"] == 3


def test_the_causal_caveat_travels_with_the_payload():
    """
    The number will outlive anyone's memory of the caveat, so the caveat has
    to be in the data, not only in the docs.
    """
    payload = source_contribution.compute([(_summary("knowledge"), 0.9, 1.0)])
    assert "not causal" in payload["interpretation"].lower()
    assert any("Observational" in ln for ln in source_contribution.render_ascii(payload))


# ==========================================================
# Snapshots — all five metrics from one consistent row set
# ==========================================================

def _row(qid, cohort, index, probability, outcome, summary=None, version="v1"):
    from datetime import datetime, timezone

    return {
        "forecast_id": f"f-{qid}-{index}",
        "question_id": qid,
        "cohort": cohort,
        "category": "geopolitical",
        "run_index": index,
        "probability": probability,
        "agent_version": version,
        "evidence_summary": summary or _summary("knowledge"),
        "outcome": "YES" if outcome == 1.0 else "NO",
        "outcome_numeric": outcome,
        "resolved_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
    }


def test_compute_all_returns_every_metric_type():
    payloads = snapshots.compute_all(
        [_row("q1", "7d", 0, 0.8, 1.0), _row("q1", "7d", 1, 0.9, 1.0)]
    )
    assert set(payloads) == set(snapshots.METRIC_TYPES)


def test_compute_all_with_no_data_still_returns_every_metric():
    """
    A metric missing from the output is indistinguishable from one that
    failed. An empty payload with n=0 says clearly there is nothing yet.
    """
    payloads = snapshots.compute_all([])
    assert set(payloads) == set(snapshots.METRIC_TYPES)
    assert payloads["aggregate_brier"]["n"] == 0
    assert payloads["aggregate_brier"]["mean_brier"] is None


def test_synthetic_full_cycle_produces_coherent_numbers():
    """
    Three questions, two forecasts each, all resolved — the shape the plan's
    Phase 10C acceptance criteria describe.
    """
    rows = []
    for i, (cohort, outcome) in enumerate(
        [("7d", 1.0), ("14d", 0.0), ("30-45d", 1.0)]
    ):
        rows.append(_row(f"q{i}", cohort, 0, 0.55 if outcome else 0.45, outcome))
        rows.append(_row(f"q{i}", cohort, 1, 0.85 if outcome else 0.15, outcome))

    payloads = snapshots.compute_all(rows)

    assert payloads["aggregate_brier"]["n"] == 6
    assert payloads["calibration_curve"]["total_forecasts"] == 6
    assert cohort_brier.cohort_of(payloads["cohort_brier"]["items"], "all")["n"] == 6
    # Every second forecast moved toward the truth, so all three improved.
    assert payloads["improvement_curve"]["n_paired_questions"] == 3
    assert payloads["improvement_curve"]["improved_count"] == 3
    assert payloads["source_contribution"]["total_forecasts"] == 6


def test_render_summary_covers_the_empty_case_readably():
    lines = snapshots.render_summary(snapshots.compute_all([]))
    assert any("No scorable forecasts yet" in ln for ln in lines)


def test_render_summary_covers_the_populated_case():
    payloads = snapshots.compute_all([_row("q1", "7d", 0, 0.8, 1.0)])
    text = "\n".join(snapshots.render_summary(payloads))
    assert "Calibration curve" in text
    assert "Cohort Brier" in text
    assert "Improvement" in text
    assert "Source contribution" in text
