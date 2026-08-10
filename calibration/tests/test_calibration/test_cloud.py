"""
Gate 1/2 — the Phase 10D surface.

Scheduler authentication, the task endpoints, structured logging, and orphan
cleanup. No cloud is contacted; the OIDC verifier and Firestore are stubbed.

The weight here is on the two places where a mistake is a *security* or
*money* problem rather than a wrong number: who may call `/tasks/*`, and what
cleanup is allowed to delete.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from calibration import auth

SCHEDULER_SA = "calibration-scheduler@anizai-pipeline.iam.gserviceaccount.com"
STRANGER_SA = "someone-else@some-other-project.iam.gserviceaccount.com"
AUDIENCE = "https://calibration-runner-abc123-uc.a.run.app"


@pytest.fixture
def scheduler_env(monkeypatch):
    monkeypatch.setenv("CALIBRATION_SCHEDULER_SERVICE_ACCOUNTS", SCHEDULER_SA)
    monkeypatch.setenv("CALIBRATION_OIDC_AUDIENCE", AUDIENCE)


@pytest.fixture
def fake_oidc(monkeypatch):
    """Stub the Google token verifier; return whatever claims a test wants."""

    def _install(claims: dict | Exception):
        class _FakeIdToken:
            @staticmethod
            def verify_oauth2_token(token, request, audience=None):
                if isinstance(claims, Exception):
                    raise claims
                return claims

        import sys
        import types

        module = types.ModuleType("google.oauth2.id_token")
        module.verify_oauth2_token = _FakeIdToken.verify_oauth2_token
        monkeypatch.setitem(sys.modules, "google.oauth2.id_token", module)

    return _install


# ==========================================================
# Who may call /tasks/*
# ==========================================================

def test_a_verified_scheduler_is_accepted(scheduler_env, fake_oidc):
    fake_oidc({"email": SCHEDULER_SA, "email_verified": True, "aud": AUDIENCE})
    assert auth.verify_scheduler_token(f"Bearer tok") == SCHEDULER_SA


def test_a_valid_token_from_another_principal_is_403(scheduler_env, fake_oidc):
    """
    The check that matters. A signature only proves Google issued the token,
    and Google issues tokens to everyone — including anyone with a free
    account. Without the principal check, `/tasks/dispatch` would be callable
    by any Google identity on the internet, and that endpoint spends money.
    """
    fake_oidc({"email": STRANGER_SA, "email_verified": True, "aud": AUDIENCE})
    with pytest.raises(auth.AuthError) as exc:
        auth.verify_scheduler_token("Bearer tok")
    assert exc.value.status_code == 403


def test_an_unverified_email_claim_is_401(scheduler_env, fake_oidc):
    fake_oidc({"email": SCHEDULER_SA, "email_verified": False})
    with pytest.raises(auth.AuthError) as exc:
        auth.verify_scheduler_token("Bearer tok")
    assert exc.value.status_code == 401


def test_a_token_with_no_email_is_401(scheduler_env, fake_oidc):
    fake_oidc({"email_verified": True})
    with pytest.raises(auth.AuthError) as exc:
        auth.verify_scheduler_token("Bearer tok")
    assert exc.value.status_code == 401


def test_an_unverifiable_token_is_401(scheduler_env, fake_oidc):
    fake_oidc(ValueError("token expired"))
    with pytest.raises(auth.AuthError) as exc:
        auth.verify_scheduler_token("Bearer tok")
    assert exc.value.status_code == 401


@pytest.mark.parametrize("header", [None, "", "tok", "Bearer", "Basic abc"])
def test_a_malformed_header_is_401(scheduler_env, header):
    with pytest.raises(auth.AuthError) as exc:
        auth.verify_scheduler_token(header)
    assert exc.value.status_code == 401


def test_an_empty_principal_list_denies_everyone(monkeypatch, fake_oidc):
    """
    Fail closed, exactly like the operator allowlist. An unset variable must
    never mean "accept any caller" on the endpoint that dispatches.
    """
    monkeypatch.setenv("CALIBRATION_SCHEDULER_SERVICE_ACCOUNTS", "")
    fake_oidc({"email": SCHEDULER_SA, "email_verified": True})
    with pytest.raises(auth.AuthError) as exc:
        auth.verify_scheduler_token("Bearer tok")
    assert exc.value.status_code == 403


def test_the_audience_is_passed_to_the_verifier(scheduler_env, monkeypatch):
    """
    The audience is what proves the token was minted for THIS service rather
    than any other service the same scheduler can reach.
    """
    seen = {}

    import sys
    import types

    module = types.ModuleType("google.oauth2.id_token")

    def verify(token, request, audience=None):
        seen["audience"] = audience
        return {"email": SCHEDULER_SA, "email_verified": True}

    module.verify_oauth2_token = verify
    monkeypatch.setitem(sys.modules, "google.oauth2.id_token", module)

    auth.verify_scheduler_token("Bearer tok")
    assert seen["audience"] == AUDIENCE


# ==========================================================
# Structured logging
# ==========================================================

def test_task_log_emits_one_json_line_on_success(capsys):
    from calibration import logging_config

    logging_config.configure(json_output=True)
    with logging_config.task_log("harvest", run_id="r-1") as summary:
        summary["completed"] = 3

    line = [l for l in capsys.readouterr().out.splitlines() if l.strip()][-1]
    payload = json.loads(line)
    assert payload["severity"] == "INFO"
    assert payload["task"] == "harvest"
    assert payload["outcome"] == "ok"
    assert payload["completed"] == 3
    assert payload["run_id"] == "r-1"
    assert "duration_ms" in payload


def test_task_log_emits_a_line_even_when_the_task_raises(capsys):
    """
    A task that dies silently is indistinguishable from one that never fired,
    and during an unattended week nobody is watching to tell the difference.
    """
    from calibration import logging_config

    logging_config.configure(json_output=True)
    with pytest.raises(RuntimeError):
        with logging_config.task_log("dispatch"):
            raise RuntimeError("firestore unreachable")

    line = [l for l in capsys.readouterr().out.splitlines() if l.strip()][-1]
    payload = json.loads(line)
    assert payload["severity"] == "ERROR"
    assert payload["outcome"] == "error"
    assert "firestore unreachable" in payload["error"]


def test_severity_is_the_field_cloud_logging_reads(capsys):
    """
    `severity`, not `level`. With the wrong key every line shows as "Default"
    and no alert can fire on an error.
    """
    from calibration import logging_config

    logging_config.configure(json_output=True)
    logging.getLogger("calibration.test").warning("careful")

    line = [l for l in capsys.readouterr().out.splitlines() if l.strip()][-1]
    assert json.loads(line)["severity"] == "WARNING"


def test_unserialisable_extras_do_not_break_the_line(capsys):
    from calibration import logging_config

    logging_config.configure(json_output=True)
    logging.getLogger("calibration.test").info(
        "x", extra={"obj": object(), "ok": 1}
    )

    line = [l for l in capsys.readouterr().out.splitlines() if l.strip()][-1]
    payload = json.loads(line)          # must still parse
    assert payload["ok"] == 1


# ==========================================================
# Orphan cleanup
# ==========================================================

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def fake_firestore(monkeypatch):
    """Replace the gateway's collection access with an in-memory store."""
    from calibration import firestore_client

    state = {"sessions": [], "deleted": []}

    monkeypatch.setattr(
        firestore_client, "list_calibration_sessions", lambda limit=500: state["sessions"]
    )
    monkeypatch.setattr(
        firestore_client,
        "delete_calibration_session",
        lambda sid: state["deleted"].append(sid),
    )
    return state


def _session(sid, status="queued", hours_old=48):
    return {"id": sid, "status": status, "created_at": NOW - timedelta(hours=hours_old)}


@pytest.fixture
def no_postgres_rows(monkeypatch):
    from calibration.services import cleanup_service

    monkeypatch.setattr(cleanup_service, "_known_session_ids", lambda: set())


def test_sweep_defaults_to_reporting_not_deleting(fake_firestore, no_postgres_rows):
    """
    A deletion tool whose default is to delete is a tool that eventually
    deletes something it should not.
    """
    from calibration.services import cleanup_service

    fake_firestore["sessions"] = [_session("cal_orphan1")]
    report = cleanup_service.sweep(now=NOW)

    assert report.orphans == 1
    assert report.deleted == 0
    assert fake_firestore["deleted"] == []
    assert any("dry run" in line for line in report.summary_lines())


def test_sweep_deletes_when_asked(fake_firestore, no_postgres_rows):
    from calibration.services import cleanup_service

    fake_firestore["sessions"] = [_session("cal_orphan1"), _session("cal_orphan2")]
    report = cleanup_service.sweep(apply=True, now=NOW)

    assert report.deleted == 2
    assert set(fake_firestore["deleted"]) == {"cal_orphan1", "cal_orphan2"}


def test_a_session_with_a_postgres_row_is_never_an_orphan(fake_firestore, monkeypatch):
    """Live work, however old it looks. Deleting it would destroy a forecast."""
    from calibration.services import cleanup_service

    monkeypatch.setattr(
        cleanup_service, "_known_session_ids", lambda: {"cal_tracked"}
    )
    fake_firestore["sessions"] = [_session("cal_tracked")]

    assert cleanup_service.sweep(apply=True, now=NOW).deleted == 0
    assert fake_firestore["deleted"] == []


def test_a_recent_session_is_left_alone(fake_firestore, no_postgres_rows):
    """A forecast the agent is still working on must never be swept."""
    from calibration.services import cleanup_service

    fake_firestore["sessions"] = [_session("cal_recent", hours_old=2)]
    assert cleanup_service.sweep(apply=True, now=NOW).deleted == 0


def test_a_claimed_session_is_not_an_orphan(fake_firestore, no_postgres_rows):
    """
    Past `queued` means a worker claimed it, so the dispatch completed. A
    missing Postgres row there is a different problem, and not one to solve by
    deleting the evidence.
    """
    from calibration.services import cleanup_service

    fake_firestore["sessions"] = [_session("cal_running", status="running")]
    fake_firestore["sessions"].append(_session("cal_done", status="done"))

    assert cleanup_service.sweep(apply=True, now=NOW).deleted == 0


def test_one_failed_deletion_does_not_stop_the_sweep(fake_firestore, no_postgres_rows, monkeypatch):
    from calibration import firestore_client
    from calibration.services import cleanup_service

    fake_firestore["sessions"] = [_session(f"cal_o{i}") for i in range(3)]

    def flaky(sid):
        if sid == "cal_o1":
            raise RuntimeError("permission denied")
        fake_firestore["deleted"].append(sid)

    monkeypatch.setattr(firestore_client, "delete_calibration_session", flaky)

    report = cleanup_service.sweep(apply=True, now=NOW)
    assert report.deleted == 2
    assert report.errors == 1


def test_the_kill_switch_blocks_the_sweep(fake_firestore, monkeypatch):
    from calibration import config
    from calibration.services import cleanup_service

    monkeypatch.setattr(config, "CALIBRATION_ENABLED", False)
    with pytest.raises(RuntimeError, match="kill switch"):
        cleanup_service.sweep()


# ==========================================================
# Dispatch concurrency
# ==========================================================

def test_dispatch_concurrency_is_bounded(monkeypatch):
    """
    The setting was declared in config and documented as the replacement for
    the retired latency gate, but for two sprints dispatch ran sequentially
    and ignored it — config that promises something the code does not do.
    """
    import threading

    from calibration import config
    from calibration.services import dispatch_service

    monkeypatch.setattr(config, "CALIBRATION_DISPATCH_CONCURRENCY", 3)
    monkeypatch.setattr(config, "CALIBRATION_MAX_FORECASTS_PER_RUN", 100)
    monkeypatch.setattr(config, "CALIBRATION_MAX_FORECASTS_PER_DAY", 100)
    monkeypatch.setattr(
        dispatch_service.forecasts_repo, "count_dispatched_since", lambda hours=24: 0
    )

    live = 0
    peak = 0
    lock = threading.Lock()

    class FakeQuestion:
        def __init__(self, i):
            self.id = f"q-{i}"
            self.polymarket_condition_id = f"0x{i:04d}"

    questions = [FakeQuestion(i) for i in range(12)]

    monkeypatch.setattr(
        dispatch_service.questions_repo, "list_questions", lambda **kw: questions
    )
    monkeypatch.setattr(dispatch_service, "has_open_forecast", lambda qid: False)
    monkeypatch.setattr(dispatch_service.runs_repo, "start", lambda **kw: "run-1")
    monkeypatch.setattr(dispatch_service.runs_repo, "finish", lambda *a, **kw: None)

    def slow_dispatch(question, run_id):
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        import time

        time.sleep(0.05)
        with lock:
            live -= 1
        return f"cal_{question.id}"

    monkeypatch.setattr(dispatch_service, "dispatch_question", slow_dispatch)

    report = dispatch_service.dispatch_questions()

    assert report.dispatched == 12
    assert peak <= 3, f"ran {peak} dispatches at once against a limit of 3"
    assert peak > 1, "concurrency setting had no effect — still sequential"


def test_a_failure_in_one_concurrent_dispatch_isolates(monkeypatch):
    from calibration import config
    from calibration.services import dispatch_service

    monkeypatch.setattr(config, "CALIBRATION_DISPATCH_CONCURRENCY", 3)
    monkeypatch.setattr(config, "CALIBRATION_MAX_FORECASTS_PER_RUN", 100)
    monkeypatch.setattr(config, "CALIBRATION_MAX_FORECASTS_PER_DAY", 100)
    monkeypatch.setattr(
        dispatch_service.forecasts_repo, "count_dispatched_since", lambda hours=24: 0
    )

    class FakeQuestion:
        def __init__(self, i):
            self.id = f"q-{i}"
            self.polymarket_condition_id = f"0x{i:04d}"

    monkeypatch.setattr(
        dispatch_service.questions_repo,
        "list_questions",
        lambda **kw: [FakeQuestion(i) for i in range(5)],
    )
    monkeypatch.setattr(dispatch_service, "has_open_forecast", lambda qid: False)
    monkeypatch.setattr(dispatch_service.runs_repo, "start", lambda **kw: "run-1")
    monkeypatch.setattr(dispatch_service.runs_repo, "finish", lambda *a, **kw: None)

    def sometimes_fails(question, run_id):
        if question.id == "q-2":
            raise RuntimeError("firestore rejected the write")
        return f"cal_{question.id}"

    monkeypatch.setattr(dispatch_service, "dispatch_question", sometimes_fails)

    report = dispatch_service.dispatch_questions()
    assert report.dispatched == 4
    assert report.failed == 1
    assert any("q-2" in e or "0x0002" in e for e in report.errors)
