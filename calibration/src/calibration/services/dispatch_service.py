"""
Dispatch service — hand questions to the existing agent.

One dispatch is: mint a sessionId, write two Firestore documents in a fixed
order, then insert one Postgres row. Nothing else. The agent is not modified,
not configured, and not told this is a calibration submission beyond the
`metadata.calibration` marker it ignores.

Three properties this service must hold, and why each matters:

    **Postgres row last.** The `calibration_forecasts` row is inserted only
    after both Firestore writes return. A crash before that leaves an orphan
    session with no row — invisible to every metric, swept by the 24h orphan
    cleanup. The reverse (row first) would leave a row pointing at a session
    the agent will never process, which ages into a false `timed_out` and
    pollutes the failure rate with a failure that never happened.

    **Per-question isolation.** One question failing must not stop the other
    twenty-nine. Every dispatch is individually guarded and counted.

    **Ceilings checked before any I/O.** `CALIBRATION_ENABLED` and the G8
    limits are evaluated before the first Firestore call, not partway through
    the loop, so throwing the kill switch stops work rather than truncating it
    mid-batch.

References:
    - calibration_plan.md §3 A6, A7, G8
    - calibration_plan.md §7 T10B.2
"""

from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from calibration import config, firestore_client
from calibration.models import Forecast, Question
from calibration.repos import forecasts as forecasts_repo
from calibration.repos import questions as questions_repo
from calibration.repos import runs as runs_repo

logger = logging.getLogger(__name__)


@dataclass
class DispatchReport:
    """What one dispatch cycle did."""

    run_id: Optional[str] = None
    requested: int = 0
    dispatched: int = 0
    skipped_already_pending: int = 0
    failed: int = 0
    truncated_by_ceiling: int = 0
    dispatched_last_24h_before: int = 0
    daily_remaining: int = 0
    errors: list[str] = field(default_factory=list)
    session_ids: list[str] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        lines = [
            f"requested       : {self.requested}",
            f"dispatched      : {self.dispatched}",
            f"skipped (open)  : {self.skipped_already_pending}",
            f"failed          : {self.failed}",
        ]
        if self.truncated_by_ceiling:
            lines.append(
                f"TRUNCATED       : {self.truncated_by_ceiling} question(s) not "
                f"dispatched — per-run cap "
                f"{config.CALIBRATION_MAX_FORECASTS_PER_RUN}, daily cap "
                f"{config.CALIBRATION_MAX_FORECASTS_PER_DAY}"
            )
        lines.append(
            f"24h budget      : {self.dispatched_last_24h_before + self.dispatched}"
            f"/{config.CALIBRATION_MAX_FORECASTS_PER_DAY} used"
        )
        for err in self.errors[:10]:
            lines.append(f"  error: {err}")
        return lines


def _assert_enabled() -> None:
    if not config.CALIBRATION_ENABLED:
        raise RuntimeError(
            "CALIBRATION_ENABLED is false — refusing to dispatch. "
            "This is the kill switch (plan §2.5); unset it to re-enable."
        )


def has_open_forecast(question_id: str) -> bool:
    """
    True when this question already has a forecast awaiting a result.

    Guards against the cost failure mode in Risk 4: dispatch succeeds, harvest
    never sees a result, and the next cycle re-dispatches the same question
    forever. One in-flight forecast per question at a time.
    """
    return any(
        f.status == "dispatched" for f in forecasts_repo.list_by_question(question_id)
    )


def dispatch_question(question: Question, run_id: str) -> str:
    """
    Dispatch one question. Returns the minted sessionId.

    The write order (session document, queue document, Postgres row) is the
    contract from plan A6 and is not incidental — see `firestore_client.
    write_dispatch` for what each ordering fails like.

    Raises:
        Whatever Firestore or Postgres raises. The caller isolates per
        question; this function does not swallow, because a dispatch that
        half-succeeded must not be reported as a success.
    """
    session_id = firestore_client.new_session_id()
    idempotency_key = str(uuid.uuid4())
    run_index = forecasts_repo.next_run_index(question.id)

    session_payload = firestore_client.build_session_payload(
        question_text=question.question_text,
        idempotency_key=idempotency_key,
        question_id=question.id,
        run_id=run_id,
        forecast_run_index=run_index,
    )
    query_payload = firestore_client.build_query_payload(
        session_id=session_id,
        question_text=question.question_text,
        question_id=question.id,
        run_id=run_id,
        forecast_run_index=run_index,
    )

    firestore_client.write_dispatch(session_id, session_payload, query_payload)

    # Only now, with both documents durably written, does the row exist.
    forecasts_repo.insert(
        Forecast(
            question_id=question.id,
            run_id=run_id,
            forecast_run_index=run_index,
            session_id=session_id,
            query_doc_id=session_id,
            idempotency_key=idempotency_key,
            status="dispatched",
        )
    )
    return session_id


def dispatch_questions(
    question_ids: Optional[list[str]] = None,
    run_type: str = "manual",
    triggered_by: str = "cli",
    purpose: Optional[str] = None,
    evidence_caveat: Optional[str] = None,
) -> DispatchReport:
    """
    Dispatch a set of questions, or every open question when none are named.

    Args:
        question_ids:    explicit question ids, or None for all open questions.
        run_type:        'initial_seed' | 'weekly_reforecast' | 'manual' |
                         'single_question'.
        triggered_by:    recorded on the run row.
        purpose:         what this run was *for*. A pipeline sanity check and a
                         real measurement produce identical-looking rows; only
                         this field tells them apart afterwards.
        evidence_caveat: what was known to be wrong or partial about the
                         agent's evidence at dispatch time.

    On the last two arguments — a Brier score outlives the conversation that
    produced it. A run executed while the ingestion pipeline is paused yields a
    real number that will later read as "how well calibrated the agent is",
    when it actually means "how well calibrated the agent is on partial
    evidence". Stamping that onto the run row is the only version of the
    caveat that survives; agreeing it in a chat is not.

    Returns:
        A DispatchReport. Never raises for a per-question failure; raises only
        when the kill switch is thrown, before any I/O.
    """
    _assert_enabled()

    report = DispatchReport()

    if question_ids:
        questions = [q for q in (questions_repo.get_by_id(qid) for qid in question_ids) if q]
    else:
        questions = questions_repo.list_questions(status="open", limit=500)

    report.requested = len(questions)

    # --- Ceiling 1: this run ---
    limit = config.CALIBRATION_MAX_FORECASTS_PER_RUN
    if len(questions) > limit:
        report.truncated_by_ceiling = len(questions) - limit
        questions = questions[:limit]
        logger.warning(
            "[dispatch] Truncated to %d question(s) — "
            "CALIBRATION_MAX_FORECASTS_PER_RUN=%d. %d not dispatched this cycle.",
            limit, limit, report.truncated_by_ceiling,
        )

    # --- Ceiling 2: the last 24 hours, across every run ---
    #
    # This is the one that survives a stuck caller. A loop that dispatches 3,
    # fails to harvest, and retries every five minutes never violates the
    # per-run cap while emitting hundreds of forecasts a day against a quota
    # shared with the live product. The per-run cap trusts the caller; this
    # one does not.
    daily_limit = config.CALIBRATION_MAX_FORECASTS_PER_DAY
    already_today = forecasts_repo.count_dispatched_since(hours=24)
    remaining = max(0, daily_limit - already_today)

    if remaining == 0:
        # A hard stop, not a truncation. Reaching this means something is
        # looping, and quietly dispatching zero would let it keep looping
        # silently. The caller should see an error.
        raise RuntimeError(
            f"Daily dispatch ceiling reached: {already_today} forecast(s) in the "
            f"last 24h against CALIBRATION_MAX_FORECASTS_PER_DAY={daily_limit}. "
            "Refusing to dispatch. If this is unexpected, something is retrying "
            "in a loop — check for forecasts stuck in 'dispatched'."
        )

    if len(questions) > remaining:
        dropped = len(questions) - remaining
        report.truncated_by_ceiling += dropped
        questions = questions[:remaining]
        logger.warning(
            "[dispatch] Daily ceiling: %d of %d already dispatched in the last "
            "24h, so only %d more this cycle. %d question(s) held back.",
            already_today, daily_limit, remaining, dropped,
        )

    report.dispatched_last_24h_before = already_today
    report.daily_remaining = remaining

    report.run_id = runs_repo.start(
        run_type=run_type,
        triggered_by=triggered_by,
        metadata={
            "stage": "dispatch",
            "purpose": purpose,
            "evidence_caveat": evidence_caveat,
        },
    )

    try:
        pending = []
        for question in questions:
            if has_open_forecast(question.id):
                report.skipped_already_pending += 1
                logger.info(
                    "[dispatch] Skipping %s — a forecast is already in flight",
                    question.polymarket_condition_id[:12],
                )
            else:
                pending.append(question)

        # Bounded concurrency. A dispatch is two Firestore round-trips plus a
        # Postgres insert — almost entirely network wait — so a small pool
        # turns a 30-question run from 30 sequential round-trips into ~10.
        #
        # Bounded rather than unbounded because the far side is the agent's
        # work queue. How fast to fill someone else's queue is a load decision
        # about their service, not ours to make silently. The default of 3 is
        # deliberately conservative and is the concrete replacement for the
        # retired P1 latency gate.
        limit = max(1, config.CALIBRATION_DISPATCH_CONCURRENCY)
        run_id = report.run_id

        def _dispatch_one(question: Question) -> tuple[Optional[str], Optional[str]]:
            try:
                return dispatch_question(question, run_id), None
            except Exception as exc:  # noqa: BLE001 — isolate per question
                return None, f"{question.polymarket_condition_id[:12]}: {exc}"

        if limit == 1 or len(pending) <= 1:
            results = [_dispatch_one(q) for q in pending]
        else:
            logger.info(
                "[dispatch] Dispatching %d question(s), %d at a time",
                len(pending), limit,
            )
            with ThreadPoolExecutor(max_workers=limit) as pool:
                results = list(pool.map(_dispatch_one, pending))

        for session_id, error in results:
            if session_id:
                report.dispatched += 1
                report.session_ids.append(session_id)
            else:
                report.failed += 1
                report.errors.append(error or "unknown dispatch failure")
                logger.error("[dispatch] %s", error)

        return report
    finally:
        runs_repo.finish(
            report.run_id,
            questions_dispatched=report.dispatched,
            forecasts_failed=report.failed,
            metadata={
                "requested": report.requested,
                "skipped_already_pending": report.skipped_already_pending,
                "truncated_by_ceiling": report.truncated_by_ceiling,
            },
        )
