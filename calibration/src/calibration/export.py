"""
Export — write the measurements out as files.

The deliverable for the project submission is a folder of files, not a running
service. This module produces that folder.

Two formats on purpose:

    JSON  keeps the full structure — nested bucket points, per-question
          forecast history, the caveats. It is the record.
    CSV   opens in Excel. Whoever grades this will open the CSV.

Everything written is derived from the database, so re-running overwrites
cleanly and the folder can be deleted at any time without losing anything.

**The empty case is a first-class output, not an error.** A calibration
harness with no resolved questions yet is the normal state for its first
month, and the export says so in the file rather than producing a zero-byte
CSV that looks like a bug.
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    """
    Write a CSV, including the header when there are no rows.

    A header-only file communicates "this measurement exists and is empty".
    A zero-byte file communicates "something broke".
    """
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def export_all(output_dir: Path) -> dict[str, int]:
    """
    Write every artifact into `output_dir`. Returns row counts per file.

    Files:
        summary.json            headline numbers plus the caveats
        forecasts.csv/.json     one row per forecast, with its Brier score
        questions.csv           one row per tracked question
        calibration_curve.csv   the five buckets
        cohort_brier.csv        per-horizon scores
        improvement.csv         first vs latest forecast per resolved question
        source_contribution.csv per-vault comparison
        runs.csv                what ran, when, and what it was for
    """
    from calibration.metrics import snapshots
    from calibration.repos import forecasts as f_repo
    from calibration.repos import questions as q_repo
    from calibration.repos import resolutions as r_repo
    from calibration.repos import runs as runs_repo

    output_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}

    payloads = snapshots.compute_all()
    aggregate = payloads["aggregate_brier"]

    questions = q_repo.list_questions(limit=500)
    q_by_id = {q.id: q for q in questions}

    # ---------------------------------------------------------------- summary
    forecast_counts = f_repo.count_by_status()
    summary = {
        "generated_at": _stamp(),
        "scorable_forecasts": aggregate["n"],
        "mean_brier": aggregate["mean_brier"],
        "uninformed_baseline": aggregate["uninformed_baseline"],
        "skill_vs_coin_flip": aggregate["skill_vs_coin_flip"],
        "questions_tracked": len(questions),
        "questions_resolved": sum(1 for q in questions if q.status == "resolved"),
        "forecasts_by_status": forecast_counts,
        "resolutions_by_outcome": r_repo.count_by_outcome(),
        # Carried in the file itself. A Brier score read six months from now
        # without knowing the pipeline was paused is a misleading number.
        "how_to_read_this": _interpretation(aggregate, questions),
    }
    _write_json(output_dir / "summary.json", summary)
    counts["summary.json"] = 1

    # ---------------------------------------------------------------- forecasts
    forecast_rows = []
    for question in questions:
        for f in f_repo.list_by_question(question.id):
            forecast_rows.append(
                {
                    "question_id": question.id,
                    "question": question.question_text,
                    "cohort": question.cohort,
                    "category": question.category,
                    "run_index": f.forecast_run_index,
                    "status": f.status,
                    "probability": float(f.final_probability) if f.final_probability is not None else "",
                    "confidence": float(f.confidence) if f.confidence is not None else "",
                    "tier": f.tier or "",
                    "brier_score": float(f.brier_score) if f.brier_score is not None else "",
                    "agent_version": f.agent_version or "",
                    "evidence_count": (f.agent_evidence_summary or {}).get("evidence_count_total", ""),
                    "vaults_used": ";".join((f.agent_evidence_summary or {}).get("vault_types_present", [])),
                    "session_id": f.session_id,
                    "dispatched_at": f.forecast_dispatched_at,
                    "completed_at": f.forecast_completed_at,
                    "error": f.error_message or "",
                }
            )
    _write_csv(
        output_dir / "forecasts.csv",
        forecast_rows,
        ["question_id", "question", "cohort", "category", "run_index", "status",
         "probability", "confidence", "tier", "brier_score", "agent_version",
         "evidence_count", "vaults_used", "session_id", "dispatched_at",
         "completed_at", "error"],
    )
    _write_json(output_dir / "forecasts.json", forecast_rows)
    counts["forecasts.csv"] = len(forecast_rows)

    # ---------------------------------------------------------------- questions
    question_rows = []
    for question in questions:
        resolution = r_repo.get_by_question(question.id)
        question_rows.append(
            {
                "id": question.id,
                "question": question.question_text,
                "cohort": question.cohort,
                "category": question.category,
                "status": question.status,
                "expected_resolution": question.expected_resolution_date,
                "outcome": resolution.outcome if resolution else "",
                "resolved_at": resolution.resolved_at if resolution else "",
                "polymarket_url": question.polymarket_url,
                "liquidity_at_pickup": float(question.liquidity_usd_at_pickup or 0),
                "added_by": question.added_by,
            }
        )
    _write_csv(
        output_dir / "questions.csv",
        question_rows,
        ["id", "question", "cohort", "category", "status", "expected_resolution",
         "outcome", "resolved_at", "polymarket_url", "liquidity_at_pickup", "added_by"],
    )
    counts["questions.csv"] = len(question_rows)

    # ---------------------------------------------------------------- metrics
    _write_csv(
        output_dir / "calibration_curve.csv",
        [
            {
                "bucket": p["bucket"],
                "n": p["count"],
                "mean_predicted": p["mean_predicted"] if p["mean_predicted"] is not None else "",
                "actual_yes_rate": p["actual_yes_rate"] if p["actual_yes_rate"] is not None else "",
                "ci_lower": p["lower_bound"],
                "ci_upper": p["upper_bound"],
            }
            for p in payloads["calibration_curve"]["points"]
        ],
        ["bucket", "n", "mean_predicted", "actual_yes_rate", "ci_lower", "ci_upper"],
    )
    counts["calibration_curve.csv"] = len(payloads["calibration_curve"]["points"])

    _write_csv(
        output_dir / "cohort_brier.csv",
        [
            {
                "cohort": i["cohort"],
                "n": i["n"],
                "mean_brier": i["mean_brier"] if i["mean_brier"] is not None else "",
                "std_brier": i["std_brier"] if i["std_brier"] is not None else "",
                "skill_vs_coin_flip": i["skill_vs_coin_flip"] if i["skill_vs_coin_flip"] is not None else "",
                "small_sample": i["small_sample"],
            }
            for i in payloads["cohort_brier"]["items"]
        ],
        ["cohort", "n", "mean_brier", "std_brier", "skill_vs_coin_flip", "small_sample"],
    )
    counts["cohort_brier.csv"] = len(payloads["cohort_brier"]["items"])

    improvement = payloads["improvement_curve"]
    _write_csv(
        output_dir / "improvement.csv",
        [
            {
                "question": q_by_id[p["question_id"]].question_text
                if p["question_id"] in q_by_id else p["question_id"],
                "cohort": p["cohort"],
                "first_probability": p["original_probability"],
                "latest_probability": p["latest_probability"],
                "first_brier": p["original_brier"],
                "latest_brier": p["latest_brier"],
                "delta": p["delta"],
                "improved": p["improved"],
            }
            for p in improvement["points"]
        ],
        ["question", "cohort", "first_probability", "latest_probability",
         "first_brier", "latest_brier", "delta", "improved"],
    )
    counts["improvement.csv"] = len(improvement["points"])

    _write_csv(
        output_dir / "source_contribution.csv",
        [
            {
                "vault": i["vault_type"],
                "n_with": i["n_with"],
                "n_without": i["n_without"],
                "brier_with": i["mean_brier_with"] if i["mean_brier_with"] is not None else "",
                "brier_without": i["mean_brier_without"] if i["mean_brier_without"] is not None else "",
                "delta": i["delta"] if i["delta"] is not None else "",
                "statistically_comparable": i["comparable"],
            }
            for i in payloads["source_contribution"]["items"]
        ],
        ["vault", "n_with", "n_without", "brier_with", "brier_without", "delta",
         "statistically_comparable"],
    )
    counts["source_contribution.csv"] = len(payloads["source_contribution"]["items"])

    # ---------------------------------------------------------------- runs
    run_rows = [
        {
            "triggered_at": r.triggered_at,
            "run_type": r.run_type,
            "triggered_by": r.triggered_by,
            "questions_dispatched": r.questions_dispatched if r.questions_dispatched is not None else "",
            "forecasts_completed": r.forecasts_completed if r.forecasts_completed is not None else "",
            "forecasts_failed": r.forecasts_failed if r.forecasts_failed is not None else "",
            "finished_at": r.finished_at or "",
            "purpose": (r.run_metadata or {}).get("purpose", ""),
            "evidence_caveat": (r.run_metadata or {}).get("evidence_caveat", ""),
        }
        for r in runs_repo.list_runs(limit=200)
    ]
    _write_csv(
        output_dir / "runs.csv",
        run_rows,
        ["triggered_at", "run_type", "triggered_by", "questions_dispatched",
         "forecasts_completed", "forecasts_failed", "finished_at", "purpose",
         "evidence_caveat"],
    )
    counts["runs.csv"] = len(run_rows)

    # Full metric payloads, structure intact.
    _write_json(output_dir / "metrics.json", payloads)
    counts["metrics.json"] = len(payloads)

    logger.info("[export] Wrote %d files to %s", len(counts), output_dir)
    return counts


def _interpretation(aggregate: dict[str, Any], questions: list) -> list[str]:
    """
    How to read these numbers — written into the export.

    Present tense and specific, because whoever reads the folder will not have
    been in the conversation that produced it.
    """
    n = aggregate["n"]
    notes = []

    if n == 0:
        notes.append(
            "No forecast has been scored yet. A forecast is scored only once its "
            "Polymarket market settles, which for these questions is weeks after "
            "the forecast was made. An empty calibration curve at this stage is "
            "the expected state, not a failure."
        )
    elif n < 10:
        notes.append(
            f"Only {n} forecast(s) have been scored. At this sample size the "
            "calibration curve and the per-cohort scores are indicative at best "
            "— a single question resolving the other way would move them "
            "visibly. Every table here carries its n for that reason."
        )

    open_count = sum(1 for q in questions if q.status == "open")
    if open_count:
        notes.append(
            f"{open_count} question(s) are still open and will be scored as their "
            "markets settle."
        )

    notes.append(
        "Brier score: lower is better. 0.25 is what you get by always saying "
        "50%, so it is the bar any forecaster must clear to be worth running."
    )
    notes.append(
        "Forecasts marked failed, timed_out, or needs_clarification are excluded "
        "from every score but are counted in forecasts_by_status — a metric that "
        "silently drops its failures overstates itself."
    )
    return notes
