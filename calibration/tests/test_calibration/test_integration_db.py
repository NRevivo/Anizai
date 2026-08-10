"""
Gate 3 — the schema and the repository layer against a real Postgres.

Everything else in this suite is a pure function over a fixture. This module
is the only place where `sql/init.sql` is actually executed and where the SQL
in `repos/` is actually parsed by a database. Without it, the DDL and every
query string are unverified text — they look right and have never run.

Skipped automatically when no database is reachable, so the suite stays green
on a machine with no Postgres. That is a deliberate trade: a skipped test is
visible in the output, whereas a suite that refuses to run at all gets
excluded from CI and then rots.

    docker run -d --name anizai-calibration-test \
        -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=anizai_calibration \
        -p 55432:5432 postgres:16-alpine

    CALIBRATION_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:55432/anizai_calibration

Every test runs inside a transaction that is rolled back, so the tests share
one database without ordering dependencies and leave nothing behind.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from calibration.models import Forecast, MetricsSnapshot, Question, Resolution

TEST_DB_URL = os.getenv("CALIBRATION_TEST_DATABASE_URL", "")


def _database_reachable(url: str) -> bool:
    if not url:
        return False
    try:
        import psycopg2

        conn = psycopg2.connect(url, connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _database_reachable(TEST_DB_URL),
    reason="CALIBRATION_TEST_DATABASE_URL not set or database unreachable — "
           "see this module's docstring for the one-line docker command.",
)


@pytest.fixture(autouse=True)
def _point_at_test_db():
    """
    Force the test database before every test in this module, and drop the
    pool afterwards.

    Belt and braces against cross-module state leakage. `test_config.py`
    reloads `calibration.config` to exercise the validation rules, and any
    reload replaces the module's attributes wholesale. Rather than depend on
    that module cleaning up perfectly, this fixture re-asserts the connection
    target per test — the alternative is a suite whose result depends on file
    collection order, which is the kind of flakiness that gets a test file
    deleted rather than fixed.
    """
    from calibration import config, db

    original = config.CALIBRATION_DATABASE_URL
    config.CALIBRATION_DATABASE_URL = TEST_DB_URL
    db.close_pool()
    yield
    config.CALIBRATION_DATABASE_URL = original
    db.close_pool()


@pytest.fixture(scope="module")
def schema_applied():
    """Point the calibration package at the test database and apply the DDL."""
    from calibration import config, db

    original = config.CALIBRATION_DATABASE_URL
    config.CALIBRATION_DATABASE_URL = TEST_DB_URL
    db.close_pool()

    db.apply_schema()
    yield db

    db.close_pool()
    config.CALIBRATION_DATABASE_URL = original


@pytest.fixture
def clean_db(schema_applied):
    """
    Truncate every calibration table before each test.

    TRUNCATE ... CASCADE rather than per-table DELETE so the foreign keys
    between forecasts, questions, and runs do not dictate an ordering that
    would silently break when a table is added.
    """
    with schema_applied.get_cursor() as cur:
        cur.execute(
            "TRUNCATE calibration_forecasts, calibration_resolutions, "
            "calibration_metrics_snapshots, calibration_questions, "
            "calibration_runs RESTART IDENTITY CASCADE;"
        )
    yield schema_applied


def _question(condition="0xtest0001", cohort="7d", days_out=7, **kw) -> Question:
    base = dict(
        question_text="Will the integration test pass?",
        polymarket_slug="will-the-integration-test-pass",
        polymarket_condition_id=condition,
        category="geopolitical",
        cohort=cohort,
        expected_resolution_date=date.today() + timedelta(days=days_out),
        added_by="auto",
    )
    base.update(kw)
    return Question(**base)


# ==========================================================
# Schema
# ==========================================================

def test_schema_creates_all_five_tables(schema_applied):
    """The first execution of init.sql anywhere. Until this runs, it is text."""
    assert set(schema_applied.table_names()) == {
        "calibration_questions",
        "calibration_forecasts",
        "calibration_resolutions",
        "calibration_runs",
        "calibration_metrics_snapshots",
    }


def test_schema_is_idempotent(schema_applied):
    """Re-applying must be a no-op — init-db is run on every deploy."""
    schema_applied.apply_schema()
    schema_applied.apply_schema()
    assert len(schema_applied.table_names()) == 5


def test_db_layer_refuses_the_pipeline_vault_database(monkeypatch, schema_applied):
    """
    The last line of defence before a connection opens. config.validate()
    checks this too; both must hold, because they fire at different moments.
    """
    from calibration import db

    monkeypatch.setattr(
        db.config, "CALIBRATION_DATABASE_URL",
        "postgresql://u:p@localhost:5432/anizai",
    )
    db.close_pool()
    with pytest.raises(RuntimeError, match="vault database"):
        with db.get_cursor():
            pass
    db.close_pool()


# ==========================================================
# Questions repository
# ==========================================================

def test_insert_and_read_back_a_question(clean_db):
    from calibration.repos import questions as repo

    qid = repo.insert(_question())
    assert qid

    fetched = repo.get_by_id(qid)
    assert fetched.polymarket_condition_id == "0xtest0001"
    assert fetched.status == "open"
    assert fetched.created_at is not None


def test_duplicate_condition_id_is_a_silent_noop(clean_db):
    """
    This is what makes hourly discovery idempotent. Re-running must insert
    nothing and raise nothing.
    """
    from calibration.repos import questions as repo

    assert repo.insert(_question(condition="0xdupe")) is not None
    assert repo.insert(_question(condition="0xdupe")) is None
    assert len(repo.list_questions()) == 1


def test_check_constraint_rejects_an_invalid_cohort(clean_db):
    """
    The models validate this too. The database must also, because the model
    is bypassable and the database is not.
    """
    import psycopg2

    from calibration.db import get_cursor

    with pytest.raises(psycopg2.errors.CheckViolation):
        with get_cursor() as cur:
            cur.execute(
                "INSERT INTO calibration_questions (question_text, polymarket_slug, "
                "polymarket_condition_id, category, cohort, expected_resolution_date, "
                "added_by) VALUES ('q','s','0xbad','geopolitical','99d',CURRENT_DATE,'auto');"
            )


def test_count_open_by_cohort_reports_zero_for_empty_cohorts(clean_db):
    from calibration.repos import questions as repo

    repo.insert(_question(condition="0x1", cohort="7d"))
    repo.insert(_question(condition="0x2", cohort="7d"))
    repo.insert(_question(condition="0x3", cohort="14d"))

    counts = repo.count_open_by_cohort()
    assert counts == {"7d": 2, "14d": 1, "30-45d": 0}


def test_list_open_due_for_resolution_respects_the_horizon(clean_db):
    from calibration.repos import questions as repo

    repo.insert(_question(condition="0xsoon", days_out=1))
    repo.insert(_question(condition="0xlater", days_out=30, cohort="30-45d"))

    due = repo.list_open_due_for_resolution(days_ahead=2)
    assert [q.polymarket_condition_id for q in due] == ["0xsoon"]


def test_list_open_due_includes_overdue_questions(clean_db):
    """Settlement lags. A question past its date must keep being polled."""
    from calibration.repos import questions as repo

    repo.insert(_question(condition="0xoverdue", days_out=-5))
    assert len(repo.list_open_due_for_resolution(days_ahead=2)) == 1


def test_mark_resolved_transitions_only_once(clean_db):
    """A concurrent second resolver must not re-resolve an already-resolved question."""
    from calibration.repos import questions as repo

    qid = repo.insert(_question(condition="0xres"))
    assert repo.mark_resolved(qid) is True
    assert repo.mark_resolved(qid) is False
    assert repo.get_by_id(qid).status == "resolved"


def test_list_questions_filters_compose(clean_db):
    from calibration.repos import questions as repo

    repo.insert(_question(condition="0x1", cohort="7d", category="geopolitical"))
    repo.insert(_question(condition="0x2", cohort="7d", category="financial"))
    repo.insert(_question(condition="0x3", cohort="14d", category="financial"))

    assert len(repo.list_questions(cohort="7d")) == 2
    assert len(repo.list_questions(cohort="7d", category="financial")) == 1
    assert len(repo.list_questions(status="open")) == 3


# ==========================================================
# Resolutions repository
# ==========================================================

def _resolution(qid, outcome="YES", numeric="1.0") -> Resolution:
    return Resolution(
        question_id=qid,
        resolved_at=datetime.now(timezone.utc),
        outcome=outcome,
        outcome_numeric=Decimal(numeric) if numeric is not None else None,
        raw_resolution_data={"closed": True, "source": "test"},
    )


def test_insert_and_read_back_a_resolution(clean_db):
    from calibration.repos import questions as q_repo
    from calibration.repos import resolutions as repo

    qid = q_repo.insert(_question(condition="0xr1"))
    assert repo.insert(_resolution(qid)) is not None

    fetched = repo.get_by_question(qid)
    assert fetched.outcome == "YES"
    assert fetched.outcome_numeric == Decimal("1.0")
    assert fetched.raw_resolution_data["source"] == "test"


def test_second_resolution_for_a_question_is_a_noop(clean_db):
    """The hourly resolver sees the same settled market every hour for a week."""
    from calibration.repos import questions as q_repo
    from calibration.repos import resolutions as repo

    qid = q_repo.insert(_question(condition="0xr2"))
    assert repo.insert(_resolution(qid)) is not None
    assert repo.insert(_resolution(qid, outcome="NO", numeric="0.0")) is None
    assert repo.get_by_question(qid).outcome == "YES"


def test_ambiguous_resolution_stores_a_null_numeric(clean_db):
    from calibration.repos import questions as q_repo
    from calibration.repos import resolutions as repo

    qid = q_repo.insert(_question(condition="0xr3"))
    repo.insert(_resolution(qid, outcome="AMBIGUOUS", numeric=None))

    fetched = repo.get_by_question(qid)
    assert fetched.outcome_numeric is None
    assert fetched.is_scorable is False


def test_database_rejects_an_ambiguous_row_carrying_a_numeric(clean_db):
    """
    The CHECK that stops an AMBIGUOUS market from scoring as NO. Enforced in
    the model too — this proves the database backs it up independently.
    """
    import psycopg2

    from calibration.db import get_cursor
    from calibration.repos import questions as q_repo

    qid = q_repo.insert(_question(condition="0xr4"))
    with pytest.raises(psycopg2.errors.CheckViolation):
        with get_cursor() as cur:
            cur.execute(
                "INSERT INTO calibration_resolutions (question_id, resolved_at, "
                "outcome, outcome_numeric, raw_resolution_data) "
                "VALUES (%s, NOW(), 'AMBIGUOUS', 0.0, '{}'::jsonb);",
                (qid,),
            )


def test_count_by_outcome_keeps_ambiguous_visible(clean_db):
    """Excluded from scoring, but a rising ambiguous count is a real signal."""
    from calibration.repos import questions as q_repo
    from calibration.repos import resolutions as repo

    for i, (outcome, numeric) in enumerate(
        [("YES", "1.0"), ("NO", "0.0"), ("AMBIGUOUS", None)]
    ):
        qid = q_repo.insert(_question(condition=f"0xcnt{i}"))
        repo.insert(_resolution(qid, outcome=outcome, numeric=numeric))

    assert repo.count_by_outcome() == {"YES": 1, "NO": 1, "AMBIGUOUS": 1}


# ==========================================================
# Runs repository
# ==========================================================

def test_run_is_open_until_finished(clean_db):
    from calibration.repos import runs as repo

    run_id = repo.start("initial_seed", "cli", {"stage": "discovery"})
    assert repo.get_by_id(run_id).is_finished is False

    repo.finish(run_id, questions_dispatched=4, metadata={"markets_fetched": 100})
    run = repo.get_by_id(run_id)
    assert run.is_finished is True
    assert run.questions_dispatched == 4


def test_finish_merges_metadata_rather_than_replacing_it(clean_db):
    """
    The caller closing a run should not need to know what the caller that
    opened it recorded.
    """
    from calibration.repos import runs as repo

    run_id = repo.start("manual", "cli", {"stage": "resolve"})
    repo.finish(run_id, metadata={"polled": 7})

    metadata = repo.get_by_id(run_id).run_metadata
    assert metadata["stage"] == "resolve"
    assert metadata["polled"] == 7


# ==========================================================
# Forecasts repository — Phase 10B's storage, proven now
# ==========================================================

def _forecast(qid, run_id, index=0, **kw) -> Forecast:
    session = f"cal_{uuid.uuid4().hex[:12]}"
    base = dict(
        question_id=qid, run_id=run_id, forecast_run_index=index,
        session_id=session, query_doc_id=session,
        idempotency_key=str(uuid.uuid4()),
    )
    base.update(kw)
    return Forecast(**base)


def test_insert_forecast_and_read_it_back(clean_db):
    from calibration.repos import forecasts as repo
    from calibration.repos import questions as q_repo
    from calibration.repos import runs as runs_repo

    qid = q_repo.insert(_question(condition="0xf1"))
    run_id = runs_repo.start("initial_seed", "cli")

    fid = repo.insert(_forecast(qid, run_id))
    fetched = repo.get_by_id(fid)
    assert fetched.status == "dispatched"
    assert fetched.session_id.startswith("cal_")
    assert fetched.forecast_dispatched_at is not None


def test_needs_clarification_persists_as_a_real_status(clean_db):
    """
    The status the pre-revision schema had no slot for. If the CHECK
    constraint were missing this value, harvest would fail in Phase 10B.
    """
    from calibration.repos import forecasts as repo
    from calibration.repos import questions as q_repo
    from calibration.repos import runs as runs_repo

    qid = q_repo.insert(_question(condition="0xf2"))
    run_id = runs_repo.start("initial_seed", "cli")

    fid = repo.insert(_forecast(qid, run_id, status="needs_clarification"))
    fetched = repo.get_by_id(fid)
    assert fetched.status == "needs_clarification"
    assert fetched.is_terminal is True
    assert fetched.is_scorable is False


def test_duplicate_run_index_for_a_question_is_rejected(clean_db):
    """
    The improvement loop depends on index 0 always being the original forecast
    and the highest index always being the latest.
    """
    import psycopg2

    from calibration.repos import forecasts as repo
    from calibration.repos import questions as q_repo
    from calibration.repos import runs as runs_repo

    qid = q_repo.insert(_question(condition="0xf3"))
    run_id = runs_repo.start("initial_seed", "cli")

    repo.insert(_forecast(qid, run_id, index=0))
    with pytest.raises(psycopg2.errors.UniqueViolation):
        repo.insert(_forecast(qid, run_id, index=0))


def test_duplicate_idempotency_key_is_rejected(clean_db):
    """Defence in depth against a dispatch retry writing two rows."""
    import psycopg2

    from calibration.repos import forecasts as repo
    from calibration.repos import questions as q_repo
    from calibration.repos import runs as runs_repo

    qid = q_repo.insert(_question(condition="0xf4"))
    run_id = runs_repo.start("initial_seed", "cli")
    key = str(uuid.uuid4())

    repo.insert(_forecast(qid, run_id, index=0, idempotency_key=key))
    with pytest.raises(psycopg2.errors.UniqueViolation):
        repo.insert(_forecast(qid, run_id, index=1, idempotency_key=key))


def test_next_run_index_advances(clean_db):
    from calibration.repos import forecasts as repo
    from calibration.repos import questions as q_repo
    from calibration.repos import runs as runs_repo

    qid = q_repo.insert(_question(condition="0xf5"))
    run_id = runs_repo.start("initial_seed", "cli")

    assert repo.next_run_index(qid) == 0
    repo.insert(_forecast(qid, run_id, index=0))
    assert repo.next_run_index(qid) == 1
    repo.insert(_forecast(qid, run_id, index=1))
    assert repo.next_run_index(qid) == 2


def test_probability_check_constraint_holds_at_the_database(clean_db):
    import psycopg2

    from calibration.db import get_cursor
    from calibration.repos import questions as q_repo
    from calibration.repos import runs as runs_repo

    qid = q_repo.insert(_question(condition="0xf6"))
    run_id = runs_repo.start("initial_seed", "cli")

    with pytest.raises(psycopg2.errors.CheckViolation):
        with get_cursor() as cur:
            cur.execute(
                "INSERT INTO calibration_forecasts (question_id, run_id, "
                "forecast_run_index, session_id, query_doc_id, idempotency_key, "
                "final_probability) VALUES (%s,%s,0,'s','s',%s, 1.5);",
                (qid, run_id, str(uuid.uuid4())),
            )


def test_list_pending_excludes_terminal_rows(clean_db):
    """`needs_clarification` is terminal — the harvester must never rescan it."""
    from calibration.repos import forecasts as repo
    from calibration.repos import questions as q_repo
    from calibration.repos import runs as runs_repo

    run_id = runs_repo.start("initial_seed", "cli")
    for i, status in enumerate(
        ["dispatched", "completed", "failed", "timed_out", "needs_clarification"]
    ):
        qid = q_repo.insert(_question(condition=f"0xp{i}"))
        repo.insert(_forecast(qid, run_id, status=status))

    pending = repo.list_pending()
    assert len(pending) == 1
    assert pending[0].status == "dispatched"


def test_count_by_status_reports_every_status_including_zeros(clean_db):
    from calibration.repos import forecasts as repo

    counts = repo.count_by_status()
    assert set(counts) == {
        "dispatched", "completed", "failed", "timed_out", "needs_clarification"
    }
    assert all(v == 0 for v in counts.values())


def test_evidence_summary_round_trips_as_jsonb(clean_db):
    from calibration.repos import forecasts as repo
    from calibration.repos import questions as q_repo
    from calibration.repos import runs as runs_repo

    qid = q_repo.insert(_question(condition="0xf7"))
    run_id = runs_repo.start("initial_seed", "cli")
    summary = {
        "evidence_count_total": 12,
        "vault_types_present": ["knowledge", "social"],
        "projection_version": "1.0",
    }

    fid = repo.insert(_forecast(qid, run_id, agent_evidence_summary=summary))
    assert repo.get_by_id(fid).agent_evidence_summary == summary


def test_deleting_a_question_cascades_to_its_forecasts(clean_db):
    from calibration.db import get_cursor
    from calibration.repos import forecasts as repo
    from calibration.repos import questions as q_repo
    from calibration.repos import runs as runs_repo

    qid = q_repo.insert(_question(condition="0xf8"))
    run_id = runs_repo.start("initial_seed", "cli")
    repo.insert(_forecast(qid, run_id))

    with get_cursor() as cur:
        cur.execute("DELETE FROM calibration_questions WHERE id = %s;", (qid,))
    assert repo.list_by_question(qid) == []


# ==========================================================
# Metrics repository
# ==========================================================

def test_snapshot_round_trips(clean_db):
    from calibration.repos import metrics as repo

    payload = {"points": [{"bucket": "0.6-0.8", "count": 12}]}
    repo.insert(MetricsSnapshot(metric_type="calibration_curve", cohort=None, payload=payload))

    latest = repo.latest("calibration_curve")
    assert latest.payload == payload


def test_aggregate_and_per_cohort_snapshots_do_not_mix(clean_db):
    """
    `cohort IS NOT DISTINCT FROM` rather than an omitted filter. An aggregate
    and a per-cohort snapshot are different metrics; returning whichever was
    written last would silently conflate them.
    """
    from calibration.repos import metrics as repo

    repo.insert(MetricsSnapshot(metric_type="cohort_brier", cohort=None, payload={"scope": "all"}))
    repo.insert(MetricsSnapshot(metric_type="cohort_brier", cohort="7d", payload={"scope": "7d"}))

    assert repo.latest("cohort_brier", cohort=None).payload["scope"] == "all"
    assert repo.latest("cohort_brier", cohort="7d").payload["scope"] == "7d"


def test_latest_returns_none_when_nothing_is_stored(clean_db):
    from calibration.repos import metrics as repo

    assert repo.latest("aggregate_brier") is None


# ==========================================================
# Service-level flow, end to end against the database
# ==========================================================

def test_discovery_service_full_cycle(clean_db, gamma_markets, today):
    """
    The whole Phase 10A data path: raw Gamma payloads -> filter -> cohort
    selection -> ceiling -> inserted rows -> a closed run row.
    """
    from calibration.services import discovery_service

    report = discovery_service.run_discovery(
        triggered_by="integration-test", today=today, markets=gamma_markets
    )

    assert report.markets_fetched == len(gamma_markets)
    assert report.candidates_found == 4
    assert report.inserted == 4
    assert report.rejections["blocked_category"] == 1

    from calibration.repos import questions as q_repo
    from calibration.repos import runs as runs_repo

    assert q_repo.count_open_by_cohort() == {"7d": 1, "14d": 1, "30-45d": 2}
    assert runs_repo.get_by_id(report.run_id).is_finished is True


def test_discovery_is_idempotent_across_runs(clean_db, gamma_markets, today):
    """Hourly re-discovery must insert nothing the second time."""
    from calibration.services import discovery_service

    first = discovery_service.run_discovery(
        triggered_by="test", today=today, markets=gamma_markets
    )
    second = discovery_service.run_discovery(
        triggered_by="test", today=today, markets=gamma_markets
    )

    assert first.inserted == 4
    assert second.inserted == 0

    from calibration.repos import questions as q_repo

    assert len(q_repo.list_questions()) == 4


def test_discovery_respects_the_open_question_ceiling(
    clean_db, gamma_markets, today, monkeypatch
):
    from calibration import config
    from calibration.services import discovery_service

    monkeypatch.setattr(config, "CALIBRATION_MAX_OPEN_QUESTIONS", 2)
    report = discovery_service.run_discovery(
        triggered_by="test", today=today, markets=gamma_markets
    )

    assert report.inserted == 2
    assert report.truncated_by_ceiling == 2
    assert any("TRUNCATED" in line for line in report.summary_lines())


def test_resolution_service_records_ground_truth(clean_db, today, now):
    """
    Open question -> settled CLOB payload -> resolution row + status flip.
    Driven through an injected fetcher, so no network.
    """
    from calibration.repos import questions as q_repo
    from calibration.repos import resolutions as r_repo
    from calibration.services import resolution_service

    qid = q_repo.insert(_question(condition="0xsettled", days_out=1))

    def fetcher(condition_id):
        return {
            "condition_id": condition_id,
            "closed": True,
            "tokens": [
                {"outcome": "Yes", "winner": True},
                {"outcome": "No", "winner": False},
            ],
            "closedTime": "2026-07-20T09:00:00Z",
        }

    report = resolution_service.resolve_open_questions(
        triggered_by="test", now=now, fetcher=fetcher
    )

    assert report.polled == 1
    assert report.resolved_yes == 1
    assert q_repo.get_by_id(qid).status == "resolved"
    assert r_repo.get_by_question(qid).outcome_numeric == Decimal("1.0")


def test_resolution_service_leaves_unsettled_questions_open(clean_db, now):
    from calibration.repos import questions as q_repo
    from calibration.services import resolution_service

    qid = q_repo.insert(_question(condition="0xopen", days_out=1))

    report = resolution_service.resolve_open_questions(
        triggered_by="test", now=now,
        fetcher=lambda _cid: {"closed": False, "outcomePrices": ["0.6", "0.4"]},
    )

    assert report.still_open == 1
    assert report.newly_resolved == 0
    assert q_repo.get_by_id(qid).status == "open"


def test_one_unreachable_market_does_not_abort_the_cycle(clean_db, now):
    """
    One bad market must not prevent the other twenty-nine from being checked.
    """
    from calibration.repos import questions as q_repo
    from calibration.services import resolution_service

    q_repo.insert(_question(condition="0xboom", days_out=1))
    q_repo.insert(_question(condition="0xfine", days_out=1))

    def fetcher(condition_id):
        if condition_id == "0xboom":
            raise RuntimeError("network exploded")
        return {
            "closed": True,
            "tokens": [{"outcome": "No", "winner": True}],
            "closedTime": "2026-07-20T09:00:00Z",
        }

    report = resolution_service.resolve_open_questions(
        triggered_by="test", now=now, fetcher=fetcher
    )

    assert report.errors == 1
    assert report.resolved_no == 1


def test_ambiguous_resolution_is_recorded_but_not_scorable(clean_db, now):
    from calibration.repos import questions as q_repo
    from calibration.repos import resolutions as r_repo
    from calibration.services import resolution_service

    qid = q_repo.insert(_question(condition="0xdisputed", days_out=1))

    report = resolution_service.resolve_open_questions(
        triggered_by="test", now=now,
        fetcher=lambda _cid: {
            "closed": True, "disputed": True,
            "tokens": [{"outcome": "Yes", "winner": True}],
            "closedTime": "2026-07-20T09:00:00Z",
        },
    )

    assert report.resolved_ambiguous == 1
    resolution = r_repo.get_by_question(qid)
    assert resolution.outcome == "AMBIGUOUS"
    assert resolution.outcome_numeric is None
    assert resolution.is_scorable is False


def test_resolution_service_is_idempotent(clean_db, now):
    """Re-polling a resolved market inserts nothing and raises nothing."""
    from calibration.repos import questions as q_repo
    from calibration.services import resolution_service

    q_repo.insert(_question(condition="0xtwice", days_out=1))

    def fetcher(_cid):
        return {
            "closed": True,
            "tokens": [{"outcome": "Yes", "winner": True}],
            "closedTime": "2026-07-20T09:00:00Z",
        }

    first = resolution_service.resolve_open_questions(
        triggered_by="test", now=now, fetcher=fetcher
    )
    second = resolution_service.resolve_open_questions(
        triggered_by="test", now=now, fetcher=fetcher
    )

    assert first.resolved_yes == 1
    # The question is no longer open, so the second cycle does not even poll it.
    assert second.polled == 0
