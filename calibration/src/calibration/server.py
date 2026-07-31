"""
Operator API — the read-mostly surface behind the calibration dashboard.

FastAPI. Two route families with different auth:

    /api/*    Firebase ID token + operator email allowlist (auth.py)
    /tasks/*  scheduler-invoked work. Phase 10D adds OIDC verification;
              until then they are gated on CALIBRATION_ENABLED and are not
              reachable from the dashboard.
    /healthz  unauthenticated liveness

Almost everything is a GET. The two writes are `POST /api/questions` (manual
add) and `POST /api/runs/trigger` (an operator kicking a cycle for debugging).

One shape decision runs through every response: **failure counts and sample
sizes are first-class fields, not omissions.** `/api/overview` reports
`failedForecasts` and `needsClarification` beside `completedForecasts`; every
metric payload carries its `n`. A dashboard that shows only what worked cannot
answer the first question an operator has, which is whether anything is stuck.

References:
    - calibration_plan.md §7 (API), §10 (Phase 10E), §11 (consolidated contract)
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from calibration import __version__, auth, config

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Anizai Calibration API",
    version=__version__,
    description="Operator-only. Not part of the user-facing product.",
)

# The dashboard is a separate origin in development (Vite on 5173) and a
# separate Hosting site in production. Credentials are not used — the ID token
# travels in the Authorization header — so there is no cookie surface here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


def require_operator(authorization: Optional[str]) -> str:
    """Translate an AuthError into the matching HTTP status."""
    try:
        return auth.authenticate(authorization)
    except auth.AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def require_scheduler(authorization: Optional[str]) -> str:
    """Gate for `/tasks/*` — a verified, allow-listed scheduler principal."""
    try:
        return auth.verify_scheduler_token(authorization)
    except auth.AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _refuse_if_disabled() -> None:
    """
    The kill switch, checked before any I/O in every task handler.

    503 rather than a 200 carrying a "disabled" message: Cloud Scheduler
    retries on 5xx and records the failure in the job's history, so a disabled
    service is *visible* rather than looking like a long series of successful
    no-ops. Somebody should be able to tell "switched off" from "working" by
    glancing at the scheduler.
    """
    if not config.CALIBRATION_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="CALIBRATION_ENABLED is false — kill switch engaged (plan §2.5)",
        )


# ==========================================================
# Request bodies
# ==========================================================

class ManualQuestionIn(BaseModel):
    question_text: Optional[str] = Field(
        default=None,
        description="Overrides the market's own text. Changes what is measured — "
                    "use sparingly.",
    )
    polymarket_slug: str = Field(min_length=1)
    category: str
    cohort: str


class TriggerRunIn(BaseModel):
    run_type: str = "manual"


# ==========================================================
# Health
# ==========================================================

@app.get("/healthz")
def healthz() -> dict[str, Any]:
    """
    Liveness plus dependency reachability.

    Reports each dependency separately rather than collapsing to one boolean:
    "the API is up but Postgres is unreachable" is a different incident from
    "the API is down", and a single flag cannot distinguish them.
    """
    status: dict[str, Any] = {
        "status": "ok",
        "version": __version__,
        "calibration_enabled": config.CALIBRATION_ENABLED,
    }

    try:
        from calibration.db import get_cursor

        with get_cursor() as cur:
            cur.execute("SELECT 1;")
        status["db"] = "ok"
    except Exception as exc:  # noqa: BLE001
        status["db"] = f"error: {exc}"
        status["status"] = "degraded"

    return status


# ==========================================================
# Overview
# ==========================================================

@app.get("/api/overview")
def overview(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    """The single call behind the Overview screen."""
    require_operator(authorization)

    from calibration.metrics import snapshots
    from calibration.repos import forecasts as f_repo
    from calibration.repos import metrics as m_repo
    from calibration.repos import questions as q_repo

    questions = q_repo.count_by_status()
    forecasts = f_repo.count_by_status()
    latest = m_repo.latest("aggregate_brier")

    scorable = f_repo.list_scorable()
    agent_versions = [f.agent_version for f in scorable if f.agent_version]

    return {
        "openQuestions": questions["open"],
        "resolvedQuestions": questions["resolved"],
        "archivedQuestions": questions["archived"],
        "dispatchedForecasts": forecasts["dispatched"],
        "completedForecasts": forecasts["completed"],
        "failedForecasts": forecasts["failed"],
        "timedOutForecasts": forecasts["timed_out"],
        "needsClarification": forecasts["needs_clarification"],
        "scorableForecasts": len(scorable),
        "latestAggregateBrier": (
            latest.payload.get("mean_brier") if latest else None
        ),
        "uninformedBaseline": (
            latest.payload.get("uninformed_baseline") if latest else 0.25
        ),
        "latestSnapshotAt": (
            latest.snapshot_at.isoformat() if latest and latest.snapshot_at else None
        ),
        "latestAgentVersion": agent_versions[-1] if agent_versions else None,
        "openByCohort": q_repo.count_open_by_cohort(),
        "cohortTargets": {
            cohort: config.target_count_for(cohort) for cohort in ("7d", "14d", "30-45d")
        },
    }


# ==========================================================
# Questions
# ==========================================================

def _question_row(question, forecasts, resolution) -> dict[str, Any]:
    """
    Project a question plus its forecasts into the list/detail shape.

    `latestProbability` comes from the most recent forecast that actually
    produced one, not simply the highest run index. A question whose latest
    run failed or asked for clarification has no probability at that index,
    and taking it there rendered a row with a Brier score but a blank
    probability — two numbers from different forecasts sitting side by side,
    which reads as a bug in the data rather than as a failed re-forecast.
    The failure is still visible: the forecast history on the detail screen
    shows every run and its status.
    """
    scored = [f for f in forecasts if f.brier_score is not None]
    with_probability = [f for f in forecasts if f.final_probability is not None]
    latest = max(
        with_probability, key=lambda f: f.forecast_run_index, default=None
    )

    return {
        "id": question.id,
        "questionText": question.question_text,
        "category": question.category,
        "cohort": question.cohort,
        "status": question.status,
        "expectedResolutionDate": question.expected_resolution_date.isoformat(),
        "addedBy": question.added_by,
        "addedByOperator": question.added_by_operator,
        "polymarketUrl": question.polymarket_url,
        "polymarketConditionId": question.polymarket_condition_id,
        "liquidityAtPickup": (
            float(question.liquidity_usd_at_pickup)
            if question.liquidity_usd_at_pickup is not None
            else None
        ),
        "forecastCount": len(forecasts),
        "latestProbability": (
            float(latest.final_probability)
            if latest and latest.final_probability is not None
            else None
        ),
        "latestBrier": (
            float(scored[-1].brier_score) if scored else None
        ),
        "outcome": resolution.outcome if resolution else None,
        "createdAt": question.created_at.isoformat() if question.created_at else None,
    }


def _forecast_row(forecast) -> dict[str, Any]:
    return {
        "id": forecast.id,
        "runIndex": forecast.forecast_run_index,
        "sessionId": forecast.session_id,
        "status": forecast.status,
        "probability": (
            float(forecast.final_probability)
            if forecast.final_probability is not None else None
        ),
        "confidence": (
            float(forecast.confidence) if forecast.confidence is not None else None
        ),
        "tier": forecast.tier,
        "agentVersion": forecast.agent_version,
        "brierScore": (
            float(forecast.brier_score) if forecast.brier_score is not None else None
        ),
        "errorMessage": forecast.error_message,
        "evidenceSummary": forecast.agent_evidence_summary,
        "dispatchedAt": (
            forecast.forecast_dispatched_at.isoformat()
            if forecast.forecast_dispatched_at else None
        ),
        "completedAt": (
            forecast.forecast_completed_at.isoformat()
            if forecast.forecast_completed_at else None
        ),
    }


@app.get("/api/questions")
def list_questions(
    authorization: Optional[str] = Header(default=None),
    status: Optional[str] = Query(default=None),
    cohort: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, Any]:
    require_operator(authorization)

    from calibration.repos import forecasts as f_repo
    from calibration.repos import questions as q_repo
    from calibration.repos import resolutions as r_repo

    questions = q_repo.list_questions(
        status=status, cohort=cohort, category=category, limit=limit
    )
    items = [
        _question_row(
            q, f_repo.list_by_question(q.id), r_repo.get_by_question(q.id)
        )
        for q in questions
    ]
    return {"items": items, "total": len(items), "page": 1}


@app.get("/api/questions/{question_id}")
def question_detail(
    question_id: str, authorization: Optional[str] = Header(default=None)
) -> dict[str, Any]:
    require_operator(authorization)

    from calibration.repos import forecasts as f_repo
    from calibration.repos import questions as q_repo
    from calibration.repos import resolutions as r_repo

    question = q_repo.get_by_id(question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")

    forecasts = f_repo.list_by_question(question_id)
    resolution = r_repo.get_by_question(question_id)

    return {
        "question": _question_row(question, forecasts, resolution),
        "forecasts": [_forecast_row(f) for f in forecasts],
        "resolution": (
            {
                "outcome": resolution.outcome,
                "outcomeNumeric": (
                    float(resolution.outcome_numeric)
                    if resolution.outcome_numeric is not None else None
                ),
                "resolvedAt": resolution.resolved_at.isoformat(),
                "detectedAt": (
                    resolution.detected_at.isoformat() if resolution.detected_at else None
                ),
                "source": resolution.resolution_source,
                "scorable": resolution.is_scorable,
            }
            if resolution else None
        ),
    }


@app.get("/api/questions/{question_id}/forecasts/compare")
def compare_forecasts(
    question_id: str, authorization: Optional[str] = Header(default=None)
) -> dict[str, Any]:
    """
    Original versus latest forecast for one question.

    Returns nulls rather than 404 when the question has fewer than two scored
    forecasts. That is a normal state — most questions have one forecast for
    most of their life — and a 404 would make the dashboard treat "not yet"
    as an error.
    """
    require_operator(authorization)

    from calibration.repos import forecasts as f_repo
    from calibration.repos import questions as q_repo

    if q_repo.get_by_id(question_id) is None:
        raise HTTPException(status_code=404, detail="Question not found")

    scored = [
        f for f in f_repo.list_by_question(question_id) if f.brier_score is not None
    ]
    if len(scored) < 2:
        return {
            "original": None,
            "latest": None,
            "delta": None,
            "agentVersionPair": None,
            "reason": (
                "Needs two scored forecasts. This question has "
                f"{len(scored)}."
            ),
        }

    original, latest = scored[0], scored[-1]
    return {
        "original": _forecast_row(original),
        "latest": _forecast_row(latest),
        "delta": float(original.brier_score) - float(latest.brier_score),
        "agentVersionPair": [original.agent_version, latest.agent_version],
    }


@app.post("/api/questions", status_code=201)
def add_question(
    body: ManualQuestionIn, authorization: Optional[str] = Header(default=None)
) -> dict[str, Any]:
    operator = require_operator(authorization)

    from calibration.services import manual_add_service

    try:
        question, inserted_id = manual_add_service.add_manual_question(
            slug=body.polymarket_slug,
            category=body.category,
            cohort=body.cohort,
            operator_email=operator,
            question_text=body.question_text,
        )
    except manual_add_service.ManualAddError as exc:
        # 400, not 500: the slug is the operator's input and the message names
        # exactly what is wrong with it.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "id": inserted_id,
        "alreadyTracked": inserted_id is None,
        "question": {
            "questionText": question.question_text,
            "polymarketSlug": question.polymarket_slug,
            "polymarketConditionId": question.polymarket_condition_id,
            "cohort": question.cohort,
            "category": question.category,
            "expectedResolutionDate": question.expected_resolution_date.isoformat(),
            "polymarketUrl": question.polymarket_url,
        },
    }


# ==========================================================
# Metrics
# ==========================================================

def _metric(metric_type: str, live: bool) -> dict[str, Any]:
    from calibration.metrics import snapshots
    from calibration.repos import metrics as m_repo

    if live:
        return {
            "snapshotAt": None,
            "live": True,
            **snapshots.compute_all()[metric_type],
        }

    latest = m_repo.latest(metric_type)
    if latest is None:
        # Compute rather than 404. A dashboard opened before the first
        # snapshot job has run should show the current picture, not an error.
        return {
            "snapshotAt": None,
            "live": True,
            **snapshots.compute_all()[metric_type],
        }
    return {
        "snapshotAt": latest.snapshot_at.isoformat() if latest.snapshot_at else None,
        "live": False,
        **latest.payload,
    }


@app.get("/api/metrics/calibration_curve")
def metrics_calibration_curve(
    authorization: Optional[str] = Header(default=None),
    live: bool = Query(default=False),
) -> dict[str, Any]:
    require_operator(authorization)
    return _metric("calibration_curve", live)


@app.get("/api/metrics/cohort_brier")
def metrics_cohort_brier(
    authorization: Optional[str] = Header(default=None),
    live: bool = Query(default=False),
) -> dict[str, Any]:
    require_operator(authorization)
    return _metric("cohort_brier", live)


@app.get("/api/metrics/improvement_curve")
def metrics_improvement_curve(
    authorization: Optional[str] = Header(default=None),
    live: bool = Query(default=False),
) -> dict[str, Any]:
    require_operator(authorization)
    return _metric("improvement_curve", live)


@app.get("/api/metrics/source_contribution")
def metrics_source_contribution(
    authorization: Optional[str] = Header(default=None),
    live: bool = Query(default=False),
) -> dict[str, Any]:
    require_operator(authorization)
    return _metric("source_contribution", live)


@app.get("/api/metrics/aggregate_brier")
def metrics_aggregate_brier(
    authorization: Optional[str] = Header(default=None),
    live: bool = Query(default=False),
) -> dict[str, Any]:
    require_operator(authorization)
    return _metric("aggregate_brier", live)


# ==========================================================
# Runs
# ==========================================================

@app.get("/api/runs")
def list_runs(
    authorization: Optional[str] = Header(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    require_operator(authorization)

    from calibration.repos import runs as runs_repo

    return {
        "items": [
            {
                "id": run.id,
                "runType": run.run_type,
                "triggeredAt": run.triggered_at.isoformat() if run.triggered_at else None,
                "triggeredBy": run.triggered_by,
                "questionsDispatched": run.questions_dispatched,
                "forecastsCompleted": run.forecasts_completed,
                "forecastsFailed": run.forecasts_failed,
                "finishedAt": run.finished_at.isoformat() if run.finished_at else None,
                "isFinished": run.is_finished,
                "metadata": run.run_metadata,
            }
            for run in runs_repo.list_runs(limit=limit)
        ]
    }


@app.post("/api/runs/trigger")
def trigger_run(
    body: TriggerRunIn, authorization: Optional[str] = Header(default=None)
) -> dict[str, Any]:
    """
    Operator-triggered discovery cycle, for debugging.

    Deliberately only triggers **discovery**, never dispatch. Discovery costs
    a few HTTP calls; dispatch spends real tokens on the agent. A button that
    can spend money should not be one click away from a button that cannot,
    and nothing about the two would look different in the UI.
    """
    operator = require_operator(authorization)

    from calibration.services import discovery_service

    try:
        report = discovery_service.run_discovery(triggered_by=operator)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "runId": report.run_id,
        "marketsFetched": report.markets_fetched,
        "candidatesFound": report.candidates_found,
        "inserted": report.inserted,
        "alreadyPresent": report.already_present,
        "truncatedByCeiling": report.truncated_by_ceiling,
        "shortfall": report.shortfall,
    }


# ==========================================================
# Scheduled tasks — Cloud Scheduler only, never the dashboard
# ==========================================================
#
# Each handler is a thin wrapper over a service that already exists and is
# already tested. The wrapper adds exactly three things: the kill-switch
# check, the OIDC gate, and one structured summary line.
#
# `/tasks/dispatch` is the only endpoint here that spends money, and it is
# deliberately the only one NOT wired to a recurring schedule by default —
# see `infrastructure/gcp/README.md` for why.


@app.post("/tasks/discover")
def task_discover(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    """Top the question pool up to its cohort targets. Costs a few HTTP calls."""
    require_scheduler(authorization)
    _refuse_if_disabled()

    from calibration.logging_config import task_log
    from calibration.services import discovery_service

    with task_log("discover") as summary:
        report = discovery_service.run_discovery(triggered_by="cloud_scheduler")
        summary.update(
            run_id=report.run_id,
            markets_fetched=report.markets_fetched,
            candidates=report.candidates_found,
            inserted=report.inserted,
            truncated=report.truncated_by_ceiling,
        )
        return {"status": "ok", **summary}


@app.post("/tasks/dispatch")
def task_dispatch(
    body: Optional[dict[str, Any]] = None,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """
    Send open questions to the agent. **This is the endpoint that spends money.**

    It refuses unless `CALIBRATION_DISPATCH_TASK_ENABLED` is explicitly true.
    A second switch, on top of the master one, because everything else here is
    cheap and idempotent while this is neither — and because the agent is
    currently held at `replicas: 0` and brought up per run, so an unattended
    dispatch would queue work nobody will collect and age it all into
    `timed_out`.
    """
    require_scheduler(authorization)
    _refuse_if_disabled()

    if not config.CALIBRATION_DISPATCH_TASK_ENABLED:
        raise HTTPException(
            status_code=503,
            detail=(
                "Scheduled dispatch is off (CALIBRATION_DISPATCH_TASK_ENABLED). "
                "It stays off while the agent is brought up manually per run — "
                "an unattended dispatch would queue forecasts nobody collects."
            ),
        )

    from calibration.logging_config import task_log
    from calibration.services import dispatch_service

    payload = body or {}
    with task_log("dispatch") as summary:
        report = dispatch_service.dispatch_questions(
            run_type=payload.get("run_type", "weekly_reforecast"),
            triggered_by="cloud_scheduler",
            purpose=payload.get("purpose"),
            evidence_caveat=payload.get("evidence_caveat"),
        )
        summary.update(
            run_id=report.run_id,
            requested=report.requested,
            dispatched=report.dispatched,
            skipped=report.skipped_already_pending,
            failed=report.failed,
            truncated=report.truncated_by_ceiling,
        )
        return {"status": "ok", **summary}


@app.post("/tasks/harvest")
def task_harvest(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    """Collect results for forecasts still in flight. Read-only on Firestore."""
    require_scheduler(authorization)
    _refuse_if_disabled()

    from calibration.logging_config import task_log
    from calibration.services import harvest_service

    with task_log("harvest") as summary:
        report = harvest_service.harvest_pending(triggered_by="cloud_scheduler")
        summary.update(
            run_id=report.run_id,
            scanned=report.scanned,
            completed=report.completed,
            failed=report.failed,
            timed_out=report.timed_out,
            needs_clarification=report.needs_clarification,
            still_pending=report.still_pending,
            errors=report.errors,
        )
        return {"status": "ok", **summary}


@app.post("/tasks/resolve")
def task_resolve(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    """Poll Polymarket for settled markets and score what resolved."""
    require_scheduler(authorization)
    _refuse_if_disabled()

    from calibration.logging_config import task_log
    from calibration.services import resolution_service

    with task_log("resolve") as summary:
        report = resolution_service.resolve_open_questions(
            triggered_by="cloud_scheduler"
        )
        summary.update(
            run_id=report.run_id,
            polled=report.polled,
            resolved_yes=report.resolved_yes,
            resolved_no=report.resolved_no,
            resolved_ambiguous=report.resolved_ambiguous,
            still_open=report.still_open,
            errors=report.errors,
        )
        return {"status": "ok", **summary}


@app.post("/tasks/snapshot_metrics")
def task_snapshot_metrics(
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Compute every metric and persist a snapshot. Pure computation."""
    require_scheduler(authorization)
    _refuse_if_disabled()

    from calibration.logging_config import task_log
    from calibration.metrics import snapshots

    with task_log("snapshot_metrics") as summary:
        payloads = snapshots.compute_all()
        written = snapshots.write_snapshots(payloads)
        summary.update(
            snapshots_written=len(written),
            scorable_forecasts=payloads["aggregate_brier"]["n"],
            mean_brier=payloads["aggregate_brier"]["mean_brier"],
        )
        return {"status": "ok", **summary}


# ==========================================================
# Errors
# ==========================================================

@app.exception_handler(auth.AuthError)
async def _auth_error_handler(_request: Request, exc: auth.AuthError):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
