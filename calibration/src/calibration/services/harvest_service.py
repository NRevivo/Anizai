"""
Harvest service — collect agent results into Postgres.

Polls rather than listens (plan A3). Drives from Postgres, not Firestore: it
reads `calibration_forecasts` rows in `dispatched` state and looks each
session up by an id we minted. There is no collection scan and no query, so
the harvester structurally cannot encounter a document it did not create —
a stronger guarantee than a `userId` filter, which would depend on being
written correctly at every call site.

The state mapping is total. Every session status the agent can produce lands
somewhere explicit:

    done                    -> completed
    failed / error          -> failed          (+ error_message)
    awaiting_clarification  -> needs_clarification   TERMINAL in V1
    anything else, aged out -> timed_out
    anything else, in window-> stay dispatched, poll again

`awaiting_clarification` is the branch the pre-revision plan had no slot for.
It is not a failure: the agent did its job and asked a reasonable question.
Recording it as `failed` would inflate the failure rate with non-failures;
leaving it as `dispatched` would let it age into a 120-minute lie. V1 does not
answer clarifications, so it is terminal — counted, displayed, and excluded
from Brier.

References:
    - calibration_plan.md §7 (state mapping, error-handling table)
    - calibration_plan.md §3 E5 (evidence projection)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from calibration import config, evidence_projection, firestore_client
from calibration.repos import forecasts as forecasts_repo
from calibration.repos import runs as runs_repo

logger = logging.getLogger(__name__)

SessionReader = Callable[[str], Optional[dict[str, Any]]]
EvidenceReader = Callable[[str], list[dict[str, Any]]]

# Session statuses the agent uses. Grouped rather than inlined so the mapping
# is readable as a table and so an unfamiliar status is handled by the
# fall-through (wait, then time out) instead of silently matching nothing.
_DONE_STATES = frozenset({"done", "complete", "completed"})
_FAILED_STATES = frozenset({"failed", "error"})
_CLARIFY_STATES = frozenset({"awaiting_clarification", "needs_clarification"})


@dataclass
class HarvestReport:
    """What one harvest cycle did."""

    run_id: Optional[str] = None
    scanned: int = 0
    completed: int = 0
    failed: int = 0
    timed_out: int = 0
    needs_clarification: int = 0
    still_pending: int = 0
    errors: int = 0
    details: list[str] = field(default_factory=list)

    @property
    def terminal(self) -> int:
        return self.completed + self.failed + self.timed_out + self.needs_clarification

    def summary_lines(self) -> list[str]:
        return [
            f"scanned              : {self.scanned}",
            f"completed            : {self.completed}",
            f"failed               : {self.failed}",
            f"timed out            : {self.timed_out}",
            f"needs clarification  : {self.needs_clarification}",
            f"still pending        : {self.still_pending}",
            f"errors               : {self.errors}",
        ]


def classify_session(
    session: Optional[dict[str, Any]],
    dispatched_at: Optional[datetime],
    now: datetime,
    timeout_minutes: int,
) -> tuple[str, Optional[str]]:
    """
    Map a session document onto a forecast status.

    Pure function — no I/O, so the whole state machine is unit-testable
    against fixtures rather than a live agent.

    Returns `(status, error_message)`. `status` is 'dispatched' when the
    forecast should stay pending and be polled again.

    Args:
        session:         the `sessions/{id}` document, or None if absent.
        dispatched_at:   when we dispatched, for the timeout comparison.
        now:             reference time. Injectable so tests are not
                         time-dependent.
        timeout_minutes: how long a forecast may stay non-terminal.
    """
    status = str((session or {}).get("status") or "").strip().lower()

    if status in _DONE_STATES:
        return "completed", None

    if status in _FAILED_STATES:
        message = (session or {}).get("errorMessage") or (session or {}).get("errorCode")
        return "failed", str(message) if message else "agent reported failure"

    if status in _CLARIFY_STATES:
        return "needs_clarification", None

    # Not terminal. Either still working, or the session document never
    # appeared at all — both are "wait" until the timeout, because a missing
    # document is indistinguishable from a slow one and guessing costs a
    # forecast.
    if dispatched_at is not None:
        age = now - _as_aware(dispatched_at)
        if age > timedelta(minutes=timeout_minutes):
            reason = "session document never appeared" if session is None else status
            return "timed_out", f"no terminal state after {age} ({reason or 'unknown'})"

    return "dispatched", None


def _as_aware(value: datetime) -> datetime:
    """Treat a naive timestamp as UTC — every stored time is UTC (plan B3)."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def harvest_pending(
    triggered_by: str = "cli",
    now: Optional[datetime] = None,
    session_reader: Optional[SessionReader] = None,
    result_reader: Optional[SessionReader] = None,
    evidence_reader: Optional[EvidenceReader] = None,
) -> HarvestReport:
    """
    Walk every `dispatched` forecast and resolve what happened to it.

    Args:
        triggered_by:    recorded on the run row.
        now:             reference time for timeouts. Injectable.
        session_reader,
        result_reader,
        evidence_reader: Firestore readers, injectable so the whole service
                         can be driven from fixtures with no emulator.

    Returns:
        A HarvestReport. A single unreadable session is counted as an error
        and skipped — it must not stop the other twenty-nine from being
        harvested.
    """
    if not config.CALIBRATION_ENABLED:
        raise RuntimeError(
            "CALIBRATION_ENABLED is false — refusing to harvest. "
            "This is the kill switch (plan §2.5); unset it to re-enable."
        )

    read_session = session_reader or firestore_client.read_session
    read_result = result_reader or firestore_client.read_session_result
    read_evidence = evidence_reader or firestore_client.read_evidence

    now = now or datetime.now(timezone.utc)
    report = HarvestReport()
    report.run_id = runs_repo.start(
        run_type="manual", triggered_by=triggered_by, metadata={"stage": "harvest"}
    )

    try:
        pending = forecasts_repo.list_pending()
        report.scanned = len(pending)
        logger.info("[harvest] %d forecast(s) awaiting a result", len(pending))

        for forecast in pending:
            try:
                session = read_session(forecast.session_id)
                status, error_message = classify_session(
                    session,
                    forecast.forecast_dispatched_at,
                    now,
                    config.CALIBRATION_DISPATCH_TIMEOUT_MIN,
                )
            except Exception as exc:  # noqa: BLE001 — one bad read must not stop the cycle
                report.errors += 1
                logger.error(
                    "[harvest] session_id=%s read failed: %s", forecast.session_id, exc
                )
                continue

            if status == "dispatched":
                report.still_pending += 1
                continue

            if status == "completed":
                try:
                    _complete(forecast.id, forecast.session_id, read_result, read_evidence)
                    report.completed += 1
                except Exception as exc:  # noqa: BLE001
                    report.errors += 1
                    logger.error(
                        "[harvest] session_id=%s completed but projection failed: %s",
                        forecast.session_id, exc,
                    )
                    continue
            else:
                forecasts_repo.mark_terminal(forecast.id, status, error_message)
                if status == "failed":
                    report.failed += 1
                elif status == "timed_out":
                    report.timed_out += 1
                else:
                    report.needs_clarification += 1

            line = f"{forecast.session_id} -> {status}"
            report.details.append(line)
            logger.info("[harvest] %s", line)

        return report
    finally:
        runs_repo.finish(
            report.run_id,
            forecasts_completed=report.completed,
            forecasts_failed=report.failed + report.timed_out,
            metadata={
                "scanned": report.scanned,
                "needs_clarification": report.needs_clarification,
                "still_pending": report.still_pending,
                "errors": report.errors,
            },
        )


def _complete(
    forecast_id: str,
    session_id: str,
    read_result: SessionReader,
    read_evidence: EvidenceReader,
) -> None:
    """
    Populate a forecast row from a finished session.

    A missing or empty evidence subcollection is not an error — the projection
    degrades to zero counts and the forecast is still recorded. Losing an
    otherwise-good forecast because its evidence trail was thin would remove
    it from the calibration sample for a reason unrelated to forecast quality.
    """
    result = read_result(session_id)
    try:
        evidence = read_evidence(session_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[harvest] session_id=%s evidence read failed (%s) — projecting without it",
            session_id, exc,
        )
        evidence = []

    if not evidence:
        logger.warning("[harvest] session_id=%s has no evidence documents", session_id)

    forecasts_repo.mark_completed(
        forecast_id,
        final_probability=evidence_projection.extract_probability(result),
        confidence=evidence_projection.extract_confidence(result),
        tier=evidence_projection.extract_tier(result),
        agent_version=evidence_projection.extract_agent_version(result),
        agent_evidence_summary=evidence_projection.project_evidence(result, evidence),
    )
