"""
Gate 1/2 — the operator API.

Auth first, because it is the part where a mistake is a security problem
rather than a wrong number. Then the response shapes the dashboard is written
against.

Needs Postgres (the endpoints read real rows). Skipped without it.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import pytest

TEST_DB_URL = os.getenv("CALIBRATION_TEST_DATABASE_URL", "")


def _postgres_reachable(url: str) -> bool:
    if not url:
        return False
    try:
        import psycopg2

        psycopg2.connect(url, connect_timeout=3).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_reachable(TEST_DB_URL),
    reason="CALIBRATION_TEST_DATABASE_URL not set or unreachable",
)

OPERATOR = "operator@example.com"
OUTSIDER = "someone.else@example.com"


@pytest.fixture
def client(monkeypatch):
    """
    A TestClient with dev auth on, pointed at the test database.

    Dev auth accepts a bare email as the bearer token — it is double-gated on
    FIRESTORE_EMULATOR_HOST being set, so it cannot be reached against a live
    project even by accident.
    """
    from fastapi.testclient import TestClient

    from calibration import auth, config, db

    monkeypatch.setenv(auth.DEV_AUTH_ENV, "1")
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "localhost:8080")
    monkeypatch.setenv(auth.ALLOWLIST_ENV, f"{OPERATOR}, Another.Op@Example.com ")

    original = config.CALIBRATION_DATABASE_URL
    config.CALIBRATION_DATABASE_URL = TEST_DB_URL
    db.close_pool()
    db.apply_schema()
    with db.get_cursor() as cur:
        cur.execute(
            "TRUNCATE calibration_forecasts, calibration_resolutions, "
            "calibration_metrics_snapshots, calibration_questions, "
            "calibration_runs RESTART IDENTITY CASCADE;"
        )

    from calibration.server import app

    yield TestClient(app)

    db.close_pool()
    config.CALIBRATION_DATABASE_URL = original


def _auth(email=OPERATOR):
    return {"Authorization": f"Bearer {email}"}


def _seed_question(condition="0xapi1", cohort="7d"):
    from calibration.models import Question
    from calibration.repos import questions as repo

    return repo.insert(
        Question(
            question_text=f"Will {condition} resolve YES?",
            polymarket_slug=f"will-{condition}",
            polymarket_condition_id=condition,
            category="geopolitical",
            cohort=cohort,
            expected_resolution_date=date.today() + timedelta(days=7),
            added_by="auto",
        )
    )


def _seed_scored_forecast(question_id, run_index=0, probability=0.8, outcome="YES"):
    import uuid

    from calibration.metrics import brier
    from calibration.models import Forecast, Resolution
    from calibration.repos import forecasts as f_repo
    from calibration.repos import questions as q_repo
    from calibration.repos import resolutions as r_repo
    from calibration.repos import runs as runs_repo

    run_id = runs_repo.start("manual", "test")
    session = f"cal_{uuid.uuid4().hex[:12]}"
    fid = f_repo.insert(
        Forecast(
            question_id=question_id, run_id=run_id, forecast_run_index=run_index,
            session_id=session, query_doc_id=session,
            idempotency_key=str(uuid.uuid4()),
        )
    )
    f_repo.mark_completed(
        fid, final_probability=probability, confidence=0.7, tier="tier_1",
        agent_version="0.5.0-sprint26+55e8093",
        agent_evidence_summary={
            "evidence_count_total": 3,
            "vault_types_present": ["knowledge"],
            "projection_version": "1.0",
        },
    )

    if r_repo.get_by_question(question_id) is None:
        r_repo.insert(
            Resolution(
                question_id=question_id,
                resolved_at=datetime.now(timezone.utc),
                outcome=outcome,
                outcome_numeric=1.0 if outcome == "YES" else 0.0,
                raw_resolution_data={"closed": True},
            )
        )
        q_repo.mark_resolved(question_id)
    brier.backfill_for_question(question_id)
    return fid


# ==========================================================
# Auth — 401 vs 403
# ==========================================================

ALL_ENDPOINTS = [
    "/api/overview",
    "/api/questions",
    "/api/metrics/calibration_curve",
    "/api/metrics/cohort_brier",
    "/api/metrics/improvement_curve",
    "/api/metrics/source_contribution",
    "/api/runs",
]


@pytest.mark.parametrize("path", ALL_ENDPOINTS)
def test_no_token_is_401(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", ALL_ENDPOINTS)
def test_a_valid_token_from_a_non_operator_is_403(client, path):
    """
    The distinction that matters. Firebase Auth on the product's project will
    issue a valid token to any signed-up user, so verification proves "a real
    person", not "an operator". 403 also tells the client that signing in
    again will not help.
    """
    assert client.get(path, headers=_auth(OUTSIDER)).status_code == 403


@pytest.mark.parametrize("path", ALL_ENDPOINTS)
def test_an_operator_gets_200(client, path):
    assert client.get(path, headers=_auth()).status_code == 200


def test_a_malformed_authorization_header_is_401(client):
    for header in ({"Authorization": "token abc"}, {"Authorization": "Bearer"},
                   {"Authorization": ""}, {"Authorization": "Bearer   "}):
        assert client.get("/api/overview", headers=header).status_code == 401


def test_allowlist_matching_ignores_case_and_whitespace(client):
    """`Ron@Example.com ` in the secret and `ron@example.com` in the token are
    the same operator; a strict comparison would look like a permissions bug."""
    assert client.get("/api/overview", headers=_auth("ANOTHER.OP@example.com")).status_code == 200
    assert client.get("/api/overview", headers=_auth(" another.op@Example.com ")).status_code == 200


def test_an_empty_allowlist_denies_everyone(client, monkeypatch):
    """
    A missing or misconfigured secret must fail closed. The alternative —
    empty meaning "allow all" — points the failure in the worst direction.
    """
    from calibration import auth

    monkeypatch.setenv(auth.ALLOWLIST_ENV, "")
    assert client.get("/api/overview", headers=_auth()).status_code == 403


def test_dev_auth_refuses_to_engage_without_the_emulator(monkeypatch):
    """
    Double-gated. A developer who exports the flag and later points at a live
    project must not silently end up with an unauthenticated API.
    """
    from calibration import auth

    monkeypatch.setenv(auth.DEV_AUTH_ENV, "1")
    monkeypatch.delenv("FIRESTORE_EMULATOR_HOST", raising=False)
    assert auth._dev_auth_enabled() is False


# ==========================================================
# Health
# ==========================================================

def test_healthz_needs_no_auth(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["db"] == "ok"


def test_healthz_reports_dependencies_separately(client):
    """
    "API up, Postgres unreachable" is a different incident from "API down".
    One boolean cannot say which.
    """
    body = client.get("/healthz").json()
    assert "db" in body
    assert "calibration_enabled" in body


# ==========================================================
# Overview
# ==========================================================

def test_overview_is_valid_when_completely_empty(client):
    """The state the system is in on day one."""
    body = client.get("/api/overview", headers=_auth()).json()
    assert body["openQuestions"] == 0
    assert body["latestAggregateBrier"] is None
    assert body["openByCohort"] == {"7d": 0, "14d": 0, "30-45d": 0}


def test_overview_reports_failures_beside_successes(client):
    """
    The first question an operator asks is whether anything is stuck. A
    dashboard that shows only what worked cannot answer it.
    """
    body = client.get("/api/overview", headers=_auth()).json()
    for field in (
        "completedForecasts", "failedForecasts", "timedOutForecasts",
        "needsClarification", "dispatchedForecasts",
    ):
        assert field in body


def test_overview_counts_reflect_the_database(client):
    qid = _seed_question()
    _seed_scored_forecast(qid)

    body = client.get("/api/overview", headers=_auth()).json()
    assert body["resolvedQuestions"] == 1
    assert body["completedForecasts"] == 1
    assert body["scorableForecasts"] == 1
    assert body["latestAgentVersion"] == "0.5.0-sprint26+55e8093"


def test_overview_exposes_cohort_targets_beside_counts(client):
    body = client.get("/api/overview", headers=_auth()).json()
    assert set(body["cohortTargets"]) == {"7d", "14d", "30-45d"}


# ==========================================================
# Questions
# ==========================================================

def test_questions_list_is_empty_but_well_formed(client):
    body = client.get("/api/questions", headers=_auth()).json()
    assert body == {"items": [], "total": 0, "page": 1}


def test_questions_list_carries_the_columns_the_table_renders(client):
    qid = _seed_question()
    _seed_scored_forecast(qid, probability=0.8)

    item = client.get("/api/questions", headers=_auth()).json()["items"][0]
    for field in (
        "id", "questionText", "category", "cohort", "status",
        "expectedResolutionDate", "latestProbability", "latestBrier",
        "forecastCount", "polymarketUrl", "outcome",
    ):
        assert field in item, f"question row is missing {field}"

    assert item["forecastCount"] == 1
    assert item["latestProbability"] == pytest.approx(0.8)
    assert item["latestBrier"] == pytest.approx(0.04, abs=1e-6)
    assert item["outcome"] == "YES"


def test_latest_probability_skips_a_failed_final_run(client):
    """
    A question whose newest run failed must not render a Brier score beside a
    blank probability — two numbers from different forecasts side by side read
    as broken data rather than as a failed re-forecast.
    """
    import uuid

    from calibration.models import Forecast
    from calibration.repos import forecasts as f_repo
    from calibration.repos import runs as runs_repo

    qid = _seed_question()
    _seed_scored_forecast(qid, run_index=0, probability=0.75)

    run_id = runs_repo.start("manual", "test")
    session = f"cal_{uuid.uuid4().hex[:12]}"
    fid = f_repo.insert(
        Forecast(
            question_id=qid, run_id=run_id, forecast_run_index=1,
            session_id=session, query_doc_id=session,
            idempotency_key=str(uuid.uuid4()),
        )
    )
    f_repo.mark_terminal(fid, "failed", "vault unavailable")

    item = client.get("/api/questions", headers=_auth()).json()["items"][0]
    assert item["forecastCount"] == 2
    assert item["latestProbability"] == pytest.approx(0.75)
    assert item["latestBrier"] is not None


def test_latest_probability_skips_a_clarification_final_run(client):
    import uuid

    from calibration.models import Forecast
    from calibration.repos import forecasts as f_repo
    from calibration.repos import runs as runs_repo

    qid = _seed_question()
    _seed_scored_forecast(qid, run_index=0, probability=0.62)

    run_id = runs_repo.start("manual", "test")
    session = f"cal_{uuid.uuid4().hex[:12]}"
    fid = f_repo.insert(
        Forecast(
            question_id=qid, run_id=run_id, forecast_run_index=1,
            session_id=session, query_doc_id=session,
            idempotency_key=str(uuid.uuid4()),
        )
    )
    f_repo.mark_terminal(fid, "needs_clarification")

    item = client.get("/api/questions", headers=_auth()).json()["items"][0]
    assert item["latestProbability"] == pytest.approx(0.62)


@pytest.mark.parametrize(
    "query,expected",
    [("?cohort=7d", 1), ("?cohort=14d", 0), ("?status=open", 1), ("?status=resolved", 0)],
)
def test_question_filters(client, query, expected):
    _seed_question(cohort="7d")
    body = client.get(f"/api/questions{query}", headers=_auth()).json()
    assert body["total"] == expected


def test_question_detail_returns_forecasts_and_resolution(client):
    qid = _seed_question()
    _seed_scored_forecast(qid)

    body = client.get(f"/api/questions/{qid}", headers=_auth()).json()
    assert body["question"]["id"] == qid
    assert len(body["forecasts"]) == 1
    assert body["forecasts"][0]["brierScore"] == pytest.approx(0.04, abs=1e-6)
    assert body["resolution"]["outcome"] == "YES"
    assert body["resolution"]["scorable"] is True


def test_question_detail_for_an_unknown_id_is_404(client):
    import uuid

    response = client.get(f"/api/questions/{uuid.uuid4()}", headers=_auth())
    assert response.status_code == 404


def test_unresolved_question_detail_has_a_null_resolution(client):
    qid = _seed_question()
    body = client.get(f"/api/questions/{qid}", headers=_auth()).json()
    assert body["resolution"] is None


# ==========================================================
# Compare
# ==========================================================

def test_compare_returns_nulls_with_a_reason_when_there_is_one_forecast(client):
    """
    Not a 404. Most questions have one forecast for most of their life, and
    treating that as an error would make the dashboard show a failure for a
    normal state.
    """
    qid = _seed_question()
    _seed_scored_forecast(qid)

    body = client.get(f"/api/questions/{qid}/forecasts/compare", headers=_auth()).json()
    assert body["original"] is None
    assert body["delta"] is None
    assert "two scored forecasts" in body["reason"]


def test_compare_reports_the_delta_between_original_and_latest(client):
    qid = _seed_question()
    _seed_scored_forecast(qid, run_index=0, probability=0.6)
    _seed_scored_forecast(qid, run_index=1, probability=0.9)

    body = client.get(f"/api/questions/{qid}/forecasts/compare", headers=_auth()).json()
    # (0.6-1)^2 = 0.16 ; (0.9-1)^2 = 0.01 ; delta = +0.15
    assert body["delta"] == pytest.approx(0.15, abs=1e-6)
    assert body["original"]["runIndex"] == 0
    assert body["latest"]["runIndex"] == 1


def test_compare_on_an_unknown_question_is_404(client):
    import uuid

    assert client.get(
        f"/api/questions/{uuid.uuid4()}/forecasts/compare", headers=_auth()
    ).status_code == 404


# ==========================================================
# Manual add
# ==========================================================

def test_manual_add_rejects_an_unknown_slug_as_400(client, monkeypatch):
    """The slug is operator input, so a bad one is a 400 with a readable
    message — not a 500."""
    from calibration.polymarket import client as pm_client

    monkeypatch.setattr(pm_client, "fetch_market_by_slug", lambda _slug: None)

    response = client.post(
        "/api/questions",
        headers=_auth(),
        json={"polymarket_slug": "nope", "category": "financial", "cohort": "7d"},
    )
    assert response.status_code == 400
    assert "No Polymarket market" in response.json()["detail"]


def test_manual_add_records_the_operator_as_provenance(client, monkeypatch):
    from calibration.polymarket import client as pm_client

    monkeypatch.setattr(
        pm_client, "fetch_market_by_slug",
        lambda _slug: {
            "slug": "will-x", "question": "Will X happen?",
            "conditionId": "0xmanual", "endDate": "2026-09-01T00:00:00Z",
            "volumeNum": 500_000,
        },
    )

    response = client.post(
        "/api/questions",
        headers=_auth(),
        json={"polymarket_slug": "will-x", "category": "financial", "cohort": "7d"},
    )
    assert response.status_code == 201
    assert response.json()["alreadyTracked"] is False

    from calibration.repos import questions as q_repo

    stored = q_repo.get_by_condition_id("0xmanual")
    assert stored.added_by == "manual"
    assert stored.added_by_operator == OPERATOR


def test_manual_add_requires_auth(client):
    assert client.post(
        "/api/questions",
        json={"polymarket_slug": "x", "category": "financial", "cohort": "7d"},
    ).status_code == 401


# ==========================================================
# Metrics
# ==========================================================

def test_metrics_compute_live_when_no_snapshot_exists(client):
    """
    A dashboard opened before the first snapshot job should show the current
    picture, not an error.
    """
    body = client.get("/api/metrics/calibration_curve", headers=_auth()).json()
    assert body["live"] is True
    assert len(body["points"]) == 5


def test_metrics_serve_the_stored_snapshot_when_one_exists(client):
    from calibration.metrics import snapshots

    qid = _seed_question()
    _seed_scored_forecast(qid)
    snapshots.write_snapshots()

    body = client.get("/api/metrics/calibration_curve", headers=_auth()).json()
    assert body["live"] is False
    assert body["snapshotAt"] is not None


def test_live_query_param_bypasses_the_snapshot(client):
    from calibration.metrics import snapshots

    snapshots.write_snapshots()
    body = client.get("/api/metrics/cohort_brier?live=true", headers=_auth()).json()
    assert body["live"] is True


def test_every_metric_payload_carries_its_sample_size(client):
    """No aggregate is rendered without the n it was computed from."""
    qid = _seed_question()
    _seed_scored_forecast(qid)

    curve = client.get("/api/metrics/calibration_curve", headers=_auth()).json()
    assert "total_forecasts" in curve
    assert all("count" in p for p in curve["points"])

    cohorts = client.get("/api/metrics/cohort_brier", headers=_auth()).json()
    assert all("n" in item for item in cohorts["items"])

    improvement = client.get("/api/metrics/improvement_curve", headers=_auth()).json()
    assert "n_paired_questions" in improvement
    assert "interpretable" in improvement

    sources = client.get("/api/metrics/source_contribution", headers=_auth()).json()
    assert all("n_with" in item and "n_without" in item for item in sources["items"])
    assert "not causal" in sources["interpretation"].lower()


# ==========================================================
# Runs
# ==========================================================

def test_runs_list_is_empty_but_well_formed(client):
    assert client.get("/api/runs", headers=_auth()).json() == {"items": []}


def test_runs_list_reports_open_and_finished_runs(client):
    from calibration.repos import runs as runs_repo

    open_run = runs_repo.start("manual", "test")
    finished = runs_repo.start("initial_seed", "test")
    runs_repo.finish(finished, questions_dispatched=4)

    items = client.get("/api/runs", headers=_auth()).json()["items"]
    by_id = {r["id"]: r for r in items}
    assert by_id[open_run]["isFinished"] is False
    assert by_id[finished]["isFinished"] is True
    assert by_id[finished]["questionsDispatched"] == 4


def test_trigger_only_runs_discovery_never_dispatch(client, monkeypatch):
    """
    A button that can spend tokens should not be one click from a button that
    cannot, and nothing about the two would look different in the UI.
    """
    from calibration.services import discovery_service

    called = {"discovery": False}

    def fake_discovery(triggered_by="cli", today=None, markets=None):
        called["discovery"] = True
        return discovery_service.DiscoveryReport(run_id=None, markets_fetched=0)

    monkeypatch.setattr(discovery_service, "run_discovery", fake_discovery)

    response = client.post("/api/runs/trigger", headers=_auth(), json={"run_type": "manual"})
    assert response.status_code == 200
    assert called["discovery"] is True


def test_trigger_reports_503_when_the_kill_switch_is_thrown(client, monkeypatch):
    from calibration import config

    monkeypatch.setattr(config, "CALIBRATION_ENABLED", False)
    response = client.post("/api/runs/trigger", headers=_auth(), json={"run_type": "manual"})
    assert response.status_code == 503
    assert "kill switch" in response.json()["detail"]
