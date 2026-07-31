"""
Resolution service — detect settled markets and record ground truth.

Orchestrates: list open questions near or past their expected resolution date
-> poll CLOB for each -> parse (resolve.py) -> insert a resolution row and
flip the question to `resolved`.

Ordering matters and is deliberate: the resolution row is inserted BEFORE the
question status flips. If the process dies between the two, the next run finds
a question still marked open whose resolution row already exists; the insert
no-ops on the UNIQUE constraint and the status flip completes. The reverse
order would leave a question marked resolved with no ground truth attached —
invisible to the resolver forever, and silently absent from every metric.

Phase 10C will extend this service to backfill Brier scores in the same
transaction as the resolution insert. That hook does not exist yet and is
marked below rather than stubbed, so there is no half-wired code path.

References:
    - calibration_plan.md §3 D1-D4
    - calibration_plan.md §6 T10A.10
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from calibration import config
from calibration.metrics import brier
from calibration.models import Question, Resolution
from calibration.polymarket import client
from calibration.polymarket.resolve import ResolutionReading, parse_resolution
from calibration.repos import questions as questions_repo
from calibration.repos import resolutions as resolutions_repo
from calibration.repos import runs as runs_repo

logger = logging.getLogger(__name__)

# Injectable fetcher so the integration test can drive the whole service from
# fixtures without patching the requests module.
MarketFetcher = Callable[[str], Optional[dict]]


@dataclass
class ResolutionReport:
    """What one resolution cycle found."""

    run_id: Optional[str] = None
    polled: int = 0
    resolved_yes: int = 0
    resolved_no: int = 0
    resolved_ambiguous: int = 0
    still_open: int = 0
    errors: int = 0
    details: list[str] = field(default_factory=list)

    @property
    def newly_resolved(self) -> int:
        return self.resolved_yes + self.resolved_no + self.resolved_ambiguous

    def summary_lines(self) -> list[str]:
        return [
            f"polled          : {self.polled}",
            f"resolved YES    : {self.resolved_yes}",
            f"resolved NO     : {self.resolved_no}",
            f"resolved AMBIG  : {self.resolved_ambiguous}",
            f"still open      : {self.still_open}",
            f"errors          : {self.errors}",
        ]


def _record(question: Question, reading: ResolutionReading) -> bool:
    """
    Persist one settled reading. Returns True if this call recorded it.

    See the module docstring for why the resolution row is written before the
    question status flips.
    """
    resolution = Resolution(
        question_id=question.id,
        resolved_at=reading.resolved_at or datetime.now(tz=None).astimezone(),
        outcome=reading.outcome,
        outcome_numeric=reading.outcome_numeric,
        resolution_source="polymarket_clob",
        raw_resolution_data=reading.raw,
    )
    inserted_id = resolutions_repo.insert(resolution)
    flipped = questions_repo.mark_resolved(question.id)

    if inserted_id is None and not flipped:
        # Both no-ops: a previous run already completed this. Normal.
        return False

    # Phase 10C: score every forecast on this question against the ground
    # truth we just recorded. Runs unconditionally rather than only on a fresh
    # insert, so a question whose resolution landed but whose scoring was
    # interrupted gets picked up on the next cycle instead of staying
    # permanently unscored and silently absent from every metric.
    #
    # A safe no-op for AMBIGUOUS resolutions — the backfill's own join
    # excludes them, so the caller does not have to remember that rule.
    scored = brier.backfill_for_question(question.id)
    if scored:
        logger.info(
            "[resolve] Scored %d forecast(s) for question_id=%s", scored, question.id
        )

    return inserted_id is not None


def resolve_open_questions(
    triggered_by: str = "cli",
    days_ahead: int = 2,
    now: Optional[datetime] = None,
    fetcher: Optional[MarketFetcher] = None,
) -> ResolutionReport:
    """
    Poll open questions that are at or near their expected resolution date.

    Args:
        triggered_by: recorded on the run row.
        days_ahead:   also poll questions resolving within this many days, to
                      absorb early settlement.
        now:          reference time for the settle-window guard. Injectable.
        fetcher:      condition_id -> CLOB payload. Defaults to the live client.

    Returns:
        A ResolutionReport. Per-question failures are counted and logged but
        never abort the cycle: one unreachable market must not prevent the
        other twenty-nine from being checked.
    """
    if not config.CALIBRATION_ENABLED:
        raise RuntimeError(
            "CALIBRATION_ENABLED is false — refusing to run resolution. "
            "This is the kill switch (plan §2.5); unset it to re-enable."
        )

    fetch = fetcher or client.fetch_clob_market
    report = ResolutionReport()
    report.run_id = runs_repo.start(
        run_type="manual", triggered_by=triggered_by, metadata={"stage": "resolve"}
    )

    try:
        due = questions_repo.list_open_due_for_resolution(days_ahead=days_ahead)
        logger.info("[resolve] %d open question(s) due for polling", len(due))

        for question in due:
            report.polled += 1
            try:
                payload = fetch(question.polymarket_condition_id)
                reading = parse_resolution(payload, now=now)
            except Exception as exc:  # noqa: BLE001 — one bad market must not stop the cycle
                report.errors += 1
                logger.error(
                    "[resolve] condition_id=%s failed: %s",
                    question.polymarket_condition_id, exc,
                )
                continue

            if not reading.resolved:
                report.still_open += 1
                logger.debug(
                    "[resolve] %s still open: %s",
                    question.polymarket_condition_id[:12], reading.detail,
                )
                continue

            recorded = _record(question, reading)
            if reading.outcome == "YES":
                report.resolved_yes += 1
            elif reading.outcome == "NO":
                report.resolved_no += 1
            else:
                report.resolved_ambiguous += 1

            line = (
                f"{question.polymarket_condition_id[:12]} -> {reading.outcome} "
                f"({reading.detail})" + ("" if recorded else " [already recorded]")
            )
            report.details.append(line)
            logger.info("[resolve] %s", line)

        return report
    finally:
        runs_repo.finish(
            report.run_id,
            metadata={
                "polled": report.polled,
                "resolved_yes": report.resolved_yes,
                "resolved_no": report.resolved_no,
                "resolved_ambiguous": report.resolved_ambiguous,
                "still_open": report.still_open,
                "errors": report.errors,
            },
        )
