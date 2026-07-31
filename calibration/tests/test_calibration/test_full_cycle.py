"""
Gate 3 — the whole system, end to end.

Postgres and the Firestore emulator together, driving the real services rather
than their pieces:

    discover -> dispatch -> (simulated agent) -> harvest -> resolve -> metrics

This is the module that proves the parts fit. Every other test verifies one
component in isolation and can pass while the seams are wrong — a dispatch
that writes a row the harvester's query never selects, a projection whose
output the metrics cannot read, a resolution that scores nothing because the
join predicate disagrees with the one in the repository.

Skipped unless BOTH backends are reachable. Setup:

    docker run -d --name anizai-calibration-test \
        -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=anizai_calibration \
        -p 55432:5432 postgres:16-alpine
    cd server/firebase && npx firebase-tools emulators:start --only firestore \
        --project anizai-ai

    CALIBRATION_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:55432/anizai_calibration
    FIRESTORE_EMULATOR_HOST=localhost:8080
"""

from __future__ import annotations

import os
import socket
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

TEST_DB_URL = os.getenv("CALIBRATION_TEST_DATABASE_URL", "")
EMULATOR_HOST = os.getenv("FIRESTORE_EMULATOR_HOST", "localhost:8080")


def _postgres_reachable(url: str) -> bool:
    if not url:
        return False
    try:
        import psycopg2

        psycopg2.connect(url, connect_timeout=3).close()
        return True
    except Exception:
        return False


def _emulator_reachable(host: str) -> bool:
    try:
        name, _, port = host.partition(":")
        with socket.create_connection((name or "localhost", int(port or 8080)), timeout=2):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not (_postgres_reachable(TEST_DB_URL) and _emulator_reachable(EMULATOR_HOST)),
    reason="needs BOTH Postgres and the Firestore emulator — see module docstring",
)


@pytest.fixture(autouse=True)
def wired(request):
    """
    Point both backends at their test instances and clear all state.

    Truncates Postgres and deletes every `cal_`-prefixed Firestore document.
    The Firestore purge is prefix-scoped rather than collection-wide: a helper
    that wiped `sessions` outright would be exactly the unscoped operation the
    production code is forbidden from performing.
    """
    os.environ["FIRESTORE_EMULATOR_HOST"] = EMULATOR_HOST
    os.environ.setdefault("FIREBASE_PROJECT_ID", "anizai-ai")

    from calibration import config, db, firestore_client

    original_url = config.CALIBRATION_DATABASE_URL
    config.CALIBRATION_DATABASE_URL = TEST_DB_URL
    db.close_pool()
    db.apply_schema()

    with db.get_cursor() as cur:
        cur.execute(
            "TRUNCATE calibration_forecasts, calibration_resolutions, "
            "calibration_metrics_snapshots, calibration_questions, "
            "calibration_runs RESTART IDENTITY CASCADE;"
        )

    firestore_client.reset()
    fs = firestore_client.get_db()

    def _purge_firestore():
        for collection in ("sessions", "forecastQueries", "sessionResults"):
            for doc in fs.collection(collection).list_documents():
                if doc.id.startswith("cal_"):
                    for sub in doc.collections():
                        for child in sub.list_documents():
                            child.delete()
                    doc.delete()

    _purge_firestore()
    yield fs
    _purge_firestore()
    db.close_pool()
    config.CALIBRATION_DATABASE_URL = original_url


# ==========================================================
# Helpers
# ==========================================================

def _seed_question(condition="0xcycle1", cohort="7d", days_out=7):
    from calibration.models import Question
    from calibration.repos import questions as repo

    return repo.insert(
        Question(
            question_text=f"Will {condition} resolve YES?",
            polymarket_slug=f"will-{condition}",
            polymarket_condition_id=condition,
            category="geopolitical",
            cohort=cohort,
            expected_resolution_date=date.today() + timedelta(days=days_out),
            added_by="auto",
        )
    )


def _simulate_agent(fs, session_id, status="done", probability=0.8, evidence=("news", "news", "market")):
    """Move a session to a terminal state the way the agent would."""
    fs.collection("sessions").document(session_id).update({"status": status})
    if status != "done":
        return
    fs.collection("sessionResults").document(session_id).set(
        {
            "finalProbability": probability,
            "confidence": 0.75,
            "tier": "tier_1",
            "agentVersion": "0.5.0-sprint26+55e8093",
            "keyFactors": [{"title": "Key driver"}],
        }
    )
    sub = fs.collection("sessions").document(session_id).collection("evidence")
    for i, source in enumerate(evidence):
        sub.document(f"e{i}").set({"sourceType": source})


def _resolve(question_id, outcome="YES"):
    from calibration.models import Resolution
    from calibration.repos import questions as q_repo
    from calibration.repos import resolutions as r_repo
    from calibration.metrics import brier

    r_repo.insert(
        Resolution(
            question_id=question_id,
            resolved_at=datetime.now(timezone.utc),
            outcome=outcome,
            outcome_numeric=None if outcome == "AMBIGUOUS" else (
                1.0 if outcome == "YES" else 0.0
            ),
            raw_resolution_data={"closed": True},
        )
    )
    q_repo.mark_resolved(question_id)
    return brier.backfill_for_question(question_id)


# ==========================================================
# Dispatch
# ==========================================================

def test_dispatch_creates_both_documents_and_one_row(wired):
    from calibration.repos import forecasts as f_repo
    from calibration.services import dispatch_service

    qid = _seed_question()
    report = dispatch_service.dispatch_questions()

    assert report.dispatched == 1
    session_id = report.session_ids[0]

    assert wired.collection("sessions").document(session_id).get().exists
    assert wired.collection("forecastQueries").document(session_id).get().exists

    rows = f_repo.list_by_question(qid)
    assert len(rows) == 1
    assert rows[0].status == "dispatched"
    assert rows[0].session_id == session_id
    assert rows[0].forecast_run_index == 0


def test_dispatch_skips_a_question_with_a_forecast_already_in_flight(wired):
    """
    The brake on Risk 4: dispatch succeeds, harvest never sees a result, and
    the next cycle re-dispatches the same question forever.
    """
    from calibration.services import dispatch_service

    _seed_question()
    assert dispatch_service.dispatch_questions().dispatched == 1

    second = dispatch_service.dispatch_questions()
    assert second.dispatched == 0
    assert second.skipped_already_pending == 1


def test_dispatch_respects_the_forecasts_per_run_ceiling(wired, monkeypatch):
    from calibration import config
    from calibration.services import dispatch_service

    for i in range(5):
        _seed_question(condition=f"0xmany{i}")

    monkeypatch.setattr(config, "CALIBRATION_MAX_FORECASTS_PER_RUN", 2)
    report = dispatch_service.dispatch_questions()

    assert report.dispatched == 2
    assert report.truncated_by_ceiling == 3
    assert any("TRUNCATED" in line for line in report.summary_lines())


def test_kill_switch_blocks_dispatch_before_any_write(wired, monkeypatch):
    from calibration import config
    from calibration.repos import forecasts as f_repo
    from calibration.services import dispatch_service

    _seed_question()
    monkeypatch.setattr(config, "CALIBRATION_ENABLED", False)

    with pytest.raises(RuntimeError, match="kill switch"):
        dispatch_service.dispatch_questions()

    assert f_repo.count_by_status()["dispatched"] == 0
    assert not list(wired.collection("forecastQueries").list_documents())


def test_a_second_forecast_gets_the_next_run_index(wired):
    """The improvement loop depends on index 0 always being the original."""
    from calibration.repos import forecasts as f_repo
    from calibration.services import dispatch_service, harvest_service

    qid = _seed_question()
    first = dispatch_service.dispatch_questions()
    _simulate_agent(wired, first.session_ids[0])
    harvest_service.harvest_pending()

    second = dispatch_service.dispatch_questions(run_type="weekly_reforecast")
    assert second.dispatched == 1

    indexes = [f.forecast_run_index for f in f_repo.list_by_question(qid)]
    assert indexes == [0, 1]


# ==========================================================
# Harvest — every terminal state
# ==========================================================

def test_harvest_completes_a_finished_forecast(wired):
    from calibration.repos import forecasts as f_repo
    from calibration.services import dispatch_service, harvest_service

    qid = _seed_question()
    session_id = dispatch_service.dispatch_questions().session_ids[0]
    _simulate_agent(wired, session_id, probability=0.8)

    report = harvest_service.harvest_pending()
    assert report.completed == 1

    row = f_repo.list_by_question(qid)[0]
    assert row.status == "completed"
    assert float(row.final_probability) == 0.8
    assert float(row.confidence) == 0.75
    assert row.tier == "tier_1"
    assert row.agent_version == "0.5.0-sprint26+55e8093"
    assert row.agent_evidence_summary["evidence_count_total"] == 3
    assert set(row.agent_evidence_summary["vault_types_present"]) == {"knowledge", "momentum"}
    assert row.forecast_completed_at is not None


def test_harvest_records_a_failed_session(wired):
    from calibration.repos import forecasts as f_repo
    from calibration.services import dispatch_service, harvest_service

    qid = _seed_question()
    session_id = dispatch_service.dispatch_questions().session_ids[0]
    wired.collection("sessions").document(session_id).update(
        {"status": "failed", "errorMessage": "vault unavailable"}
    )

    assert harvest_service.harvest_pending().failed == 1
    row = f_repo.list_by_question(qid)[0]
    assert row.status == "failed"
    assert row.error_message == "vault unavailable"


def test_harvest_records_clarification_as_its_own_state(wired):
    """
    Not a failure. Recording it as one would inflate the failure rate with
    non-failures; leaving it pending would let it age into a 120-minute lie.
    """
    from calibration.repos import forecasts as f_repo
    from calibration.services import dispatch_service, harvest_service

    qid = _seed_question()
    session_id = dispatch_service.dispatch_questions().session_ids[0]
    wired.collection("sessions").document(session_id).update(
        {"status": "awaiting_clarification"}
    )

    report = harvest_service.harvest_pending()
    assert report.needs_clarification == 1
    assert report.failed == 0
    assert f_repo.list_by_question(qid)[0].status == "needs_clarification"


def test_harvest_times_out_a_stuck_forecast(wired):
    from calibration.repos import forecasts as f_repo
    from calibration.services import dispatch_service, harvest_service

    qid = _seed_question()
    dispatch_service.dispatch_questions()
    # Nothing simulates the agent — the session stays `queued`.

    report = harvest_service.harvest_pending(
        now=datetime.now(timezone.utc) + timedelta(hours=5)
    )
    assert report.timed_out == 1
    assert f_repo.list_by_question(qid)[0].status == "timed_out"


def test_harvest_leaves_an_in_flight_forecast_alone(wired):
    from calibration.repos import forecasts as f_repo
    from calibration.services import dispatch_service, harvest_service

    qid = _seed_question()
    session_id = dispatch_service.dispatch_questions().session_ids[0]
    wired.collection("sessions").document(session_id).update({"status": "running"})

    report = harvest_service.harvest_pending()
    assert report.still_pending == 1
    assert f_repo.list_by_question(qid)[0].status == "dispatched"


def test_harvest_is_idempotent(wired):
    """A terminal row must not be re-harvested on the next 5-minute poll."""
    from calibration.services import dispatch_service, harvest_service

    _seed_question()
    session_id = dispatch_service.dispatch_questions().session_ids[0]
    _simulate_agent(wired, session_id)

    assert harvest_service.harvest_pending().completed == 1
    second = harvest_service.harvest_pending()
    assert second.scanned == 0
    assert second.completed == 0


def test_one_broken_session_does_not_stop_the_others(wired):
    """One unreadable session must not cost the other twenty-nine."""
    from calibration.services import dispatch_service, harvest_service

    _seed_question(condition="0xgood1")
    _seed_question(condition="0xgood2")
    report = dispatch_service.dispatch_questions()
    for session_id in report.session_ids:
        _simulate_agent(wired, session_id)

    calls = {"n": 0}
    from calibration import firestore_client

    real_reader = firestore_client.read_session

    def flaky(session_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient Firestore error")
        return real_reader(session_id)

    harvest = harvest_service.harvest_pending(session_reader=flaky)
    assert harvest.errors == 1
    assert harvest.completed == 1


# ==========================================================
# Scoring
# ==========================================================

def test_resolution_scores_every_forecast_on_the_question(wired):
    from calibration.repos import forecasts as f_repo
    from calibration.services import dispatch_service, harvest_service

    qid = _seed_question()
    session_id = dispatch_service.dispatch_questions().session_ids[0]
    _simulate_agent(wired, session_id, probability=0.8)
    harvest_service.harvest_pending()

    assert _resolve(qid, "YES") == 1

    row = f_repo.list_by_question(qid)[0]
    # (0.8 - 1.0)^2 = 0.04
    assert float(row.brier_score) == pytest.approx(0.04, abs=1e-6)


def test_a_no_outcome_scores_against_zero(wired):
    from calibration.repos import forecasts as f_repo
    from calibration.services import dispatch_service, harvest_service

    qid = _seed_question()
    session_id = dispatch_service.dispatch_questions().session_ids[0]
    _simulate_agent(wired, session_id, probability=0.8)
    harvest_service.harvest_pending()

    _resolve(qid, "NO")
    # (0.8 - 0.0)^2 = 0.64 — a confident call that was wrong.
    assert float(f_repo.list_by_question(qid)[0].brier_score) == pytest.approx(0.64, abs=1e-6)


def test_an_ambiguous_resolution_scores_nothing(wired):
    """
    The guard that matters most. Scoring AMBIGUOUS as 0.0 would mark every
    attached forecast as though the answer had been NO — a plausible number
    that is entirely wrong.
    """
    from calibration.repos import forecasts as f_repo
    from calibration.services import dispatch_service, harvest_service

    qid = _seed_question()
    session_id = dispatch_service.dispatch_questions().session_ids[0]
    _simulate_agent(wired, session_id, probability=0.8)
    harvest_service.harvest_pending()

    assert _resolve(qid, "AMBIGUOUS") == 0
    assert f_repo.list_by_question(qid)[0].brier_score is None


def test_a_failed_forecast_is_never_scored(wired):
    from calibration.repos import forecasts as f_repo
    from calibration.services import dispatch_service, harvest_service

    qid = _seed_question()
    session_id = dispatch_service.dispatch_questions().session_ids[0]
    wired.collection("sessions").document(session_id).update({"status": "failed"})
    harvest_service.harvest_pending()

    assert _resolve(qid, "YES") == 0
    assert f_repo.list_by_question(qid)[0].brier_score is None


def test_scorable_set_excludes_every_non_completed_state(wired):
    from calibration.repos import forecasts as f_repo
    from calibration.services import dispatch_service, harvest_service

    good = _seed_question(condition="0xgood")
    bad = _seed_question(condition="0xbad")
    clarify = _seed_question(condition="0xclarify")

    report = dispatch_service.dispatch_questions()
    by_question = {f.question_id: f.session_id for f in f_repo.list_pending()}

    _simulate_agent(wired, by_question[good])
    wired.collection("sessions").document(by_question[bad]).update({"status": "failed"})
    wired.collection("sessions").document(by_question[clarify]).update(
        {"status": "awaiting_clarification"}
    )
    harvest_service.harvest_pending()

    for qid in (good, bad, clarify):
        _resolve(qid, "YES")

    scorable = f_repo.list_scorable()
    assert len(scorable) == 1
    assert scorable[0].question_id == good


# ==========================================================
# The whole loop
# ==========================================================

def test_end_to_end_cycle_produces_every_metric(wired):
    """
    Three questions, two forecasts each, all resolved — then every metric.

    The shape the plan's Phase 10C acceptance criteria describe, run against
    real backends rather than synthetic rows.
    """
    from calibration.metrics import snapshots
    from calibration.repos import metrics as metrics_repo
    from calibration.services import dispatch_service, harvest_service

    question_ids = [
        _seed_question(condition=f"0xcycle{i}", cohort=cohort)
        for i, cohort in enumerate(("7d", "14d", "30-45d"))
    ]

    # Week 0 — a hedged first forecast.
    first = dispatch_service.dispatch_questions(run_type="initial_seed")
    for session_id in first.session_ids:
        _simulate_agent(wired, session_id, probability=0.6)
    assert harvest_service.harvest_pending().completed == 3

    # Week 1 — a more confident re-forecast.
    second = dispatch_service.dispatch_questions(run_type="weekly_reforecast")
    for session_id in second.session_ids:
        _simulate_agent(wired, session_id, probability=0.9)
    assert harvest_service.harvest_pending().completed == 3

    for qid in question_ids:
        assert _resolve(qid, "YES") == 2

    payloads = snapshots.compute_all()

    assert payloads["aggregate_brier"]["n"] == 6
    assert payloads["aggregate_brier"]["mean_brier"] == pytest.approx(
        (0.16 + 0.01) / 2, abs=1e-6
    )
    assert payloads["calibration_curve"]["total_forecasts"] == 6
    # Every question improved: 0.6 -> 0.9 against a YES outcome.
    assert payloads["improvement_curve"]["n_paired_questions"] == 3
    assert payloads["improvement_curve"]["improved_count"] == 3
    assert payloads["improvement_curve"]["interpretable"] is False   # only 3 pairs

    written = snapshots.write_snapshots(payloads)
    assert set(written) == set(snapshots.METRIC_TYPES)
    for metric_type in snapshots.METRIC_TYPES:
        assert metrics_repo.latest(metric_type) is not None


def test_metrics_are_empty_but_valid_before_anything_resolves(wired):
    """The state the system is actually in on day one."""
    from calibration.metrics import snapshots
    from calibration.services import dispatch_service, harvest_service

    _seed_question()
    session_id = dispatch_service.dispatch_questions().session_ids[0]
    _simulate_agent(wired, session_id)
    harvest_service.harvest_pending()

    payloads = snapshots.compute_all()
    assert payloads["aggregate_brier"]["n"] == 0
    assert payloads["aggregate_brier"]["mean_brier"] is None
    assert len(payloads["calibration_curve"]["points"]) == 5
    assert snapshots.write_snapshots(payloads)


def test_a_full_cycle_touches_no_foreign_document(wired):
    """
    N5/N6 across the whole pipeline, not just a single dispatch.

    A pre-existing session must be byte-identical after discover, dispatch,
    harvest, resolve, and metrics have all run.
    """
    from calibration.metrics import snapshots
    from calibration.services import dispatch_service, harvest_service

    victim_id = f"real_user_{uuid.uuid4().hex[:8]}"
    victim = {"userId": "a-real-uid", "question": "Real question", "status": "done"}
    wired.collection("sessions").document(victim_id).set(victim)

    try:
        qid = _seed_question()
        session_id = dispatch_service.dispatch_questions().session_ids[0]
        _simulate_agent(wired, session_id)
        harvest_service.harvest_pending()
        _resolve(qid, "YES")
        snapshots.write_snapshots()

        after = wired.collection("sessions").document(victim_id).get()
        assert after.exists
        assert after.to_dict() == victim
    finally:
        wired.collection("sessions").document(victim_id).delete()
