"""
Gate 3 — the Firestore bridge against a real emulator.

Everything else in the Phase 10B suite is a pure function over fixtures. This
module is the only place the two dispatch writes actually hit Firestore, and
the only place the A6 ordering invariant is proven rather than asserted.

It also carries the negative test that matters most: a dispatch must touch
**nothing** outside the two documents it creates. That is the machine-checkable
form of N5/N6, and it is the test that would catch a future refactor quietly
introducing a write to a real user's session.

Skipped automatically when the emulator is not running, so the suite stays
green without it. Start it with:

    cd server/firebase
    npx firebase-tools emulators:start --only firestore --project anizai-ai

then set FIRESTORE_EMULATOR_HOST=localhost:8080.
"""

from __future__ import annotations

import os
import socket
import uuid
from datetime import datetime, timedelta, timezone

import pytest

EMULATOR_HOST = os.getenv("FIRESTORE_EMULATOR_HOST", "localhost:8080")


def _emulator_running(host: str) -> bool:
    try:
        name, _, port = host.partition(":")
        with socket.create_connection((name or "localhost", int(port or 8080)), timeout=2):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _emulator_running(EMULATOR_HOST),
    reason=f"Firestore emulator not reachable at {EMULATOR_HOST} — "
           "see this module's docstring for the start command.",
)


@pytest.fixture(scope="module", autouse=True)
def _emulator_env():
    """Point the Admin SDK at the emulator for every test in this module."""
    os.environ["FIRESTORE_EMULATOR_HOST"] = EMULATOR_HOST
    os.environ.setdefault("FIREBASE_PROJECT_ID", "anizai-ai")

    from calibration import firestore_client

    firestore_client.reset()
    yield
    firestore_client.reset()


@pytest.fixture
def db():
    from calibration import firestore_client

    return firestore_client.get_db()


@pytest.fixture
def clean_firestore(db):
    """
    Delete every document this suite creates, before and after each test.

    Scoped by the `cal_` id prefix rather than by deleting collections
    wholesale — a test helper that truncated `sessions` would be exactly the
    kind of unscoped operation the production code is forbidden from doing,
    and it would be pointed at a database a developer might have real data in.
    """

    def _purge():
        for collection in ("sessions", "forecastQueries", "sessionResults"):
            for doc in db.collection(collection).list_documents():
                if doc.id.startswith("cal_"):
                    for sub in doc.collections():
                        for child in sub.list_documents():
                            child.delete()
                    doc.delete()

    _purge()
    yield db
    _purge()


def _dispatch_one(question_text="Will the emulator round-trip work?"):
    """Write one dispatch pair directly through the gateway."""
    from calibration import firestore_client

    session_id = firestore_client.new_session_id()
    firestore_client.write_dispatch(
        session_id,
        firestore_client.build_session_payload(
            question_text, str(uuid.uuid4()), "q-1", "r-1", 0
        ),
        firestore_client.build_query_payload(session_id, question_text, "q-1", "r-1", 0),
    )
    return session_id


# ==========================================================
# The dispatch contract, proven
# ==========================================================

def test_dispatch_writes_both_documents(clean_firestore):
    """
    The correction at the heart of the 2026-07-25 plan revision. The
    pre-revision plan wrote only the queue document; the agent's
    `update_session_status` calls `.update()`, which raises NotFound when
    `sessions/{id}` is absent, so every dispatch would have been claimed and
    then immediately failed.
    """
    session_id = _dispatch_one()

    session = clean_firestore.collection("sessions").document(session_id).get()
    query = clean_firestore.collection("forecastQueries").document(session_id).get()

    assert session.exists, "sessions/{id} was not written"
    assert query.exists, "forecastQueries/{id} was not written"


def test_both_documents_share_the_same_id(clean_firestore):
    """Keyed by one operator-minted sessionId — no auto-ids (plan A6)."""
    session_id = _dispatch_one()
    assert clean_firestore.collection("sessions").document(session_id).get().exists
    assert clean_firestore.collection("forecastQueries").document(session_id).get().exists
    assert (
        clean_firestore.collection("forecastQueries").document(session_id).get().to_dict()[
            "sessionId"
        ]
        == session_id
    )


def test_the_session_document_exists_before_the_query_document(clean_firestore):
    """
    The ordering invariant, verified against a real database rather than
    asserted in a docstring.

    A patched write path records the order the two documents actually landed
    in. Getting this backwards is not a style problem — it produces a
    claimable query whose session does not exist, which is the exact NotFound
    crash A6 exists to prevent.
    """
    from calibration import firestore_client

    order: list[str] = []
    real_get_db = firestore_client.get_db

    class _RecordingDb:
        def __init__(self, inner):
            self._inner = inner

        def collection(self, name):
            order.append(name)
            return self._inner.collection(name)

    firestore_client.get_db = lambda: _RecordingDb(real_get_db())
    try:
        _dispatch_one()
    finally:
        firestore_client.get_db = real_get_db

    assert order == ["sessions", "forecastQueries"], (
        f"documents were written in the order {order}; the session document must "
        "be written first (plan A6)"
    )


def test_written_documents_carry_the_marker_and_sentinel(clean_firestore):
    session_id = _dispatch_one()

    for collection in ("sessions", "forecastQueries"):
        doc = clean_firestore.collection(collection).document(session_id).get().to_dict()
        assert doc["userId"] == "calibration-runner"
        assert doc["metadata"]["calibration"]["enabled"] is True
        assert doc["metadata"]["calibration"]["questionId"] == "q-1"
        assert doc["metadata"]["calibration"]["forecastRunIndex"] == 0


def test_server_timestamps_resolve_to_real_times(clean_firestore):
    """SERVER_TIMESTAMP sentinels must actually be written, not left as markers."""
    session_id = _dispatch_one()
    session = clean_firestore.collection("sessions").document(session_id).get().to_dict()
    assert isinstance(session["createdAt"], datetime)


# ==========================================================
# The negative test — N5/N6 in machine-checkable form
# ==========================================================

def test_a_dispatch_touches_nothing_else(clean_firestore):
    """
    A pre-existing session belonging to someone else must be byte-identical
    after a dispatch runs.

    This is the test that would catch a future refactor quietly introducing a
    write to a real user's document — the failure mode that would be invisible
    in every other test, because calibration's own data would still look
    perfect.
    """
    victim_id = f"real_user_session_{uuid.uuid4().hex[:8]}"
    victim = {
        "userId": "a-real-firebase-uid",
        "question": "A real user's question",
        "status": "done",
        "latestProbability": 0.42,
    }
    clean_firestore.collection("sessions").document(victim_id).set(victim)
    clean_firestore.collection("sessionResults").document(victim_id).set(
        {"finalProbability": 0.42}
    )

    try:
        _dispatch_one()
        _dispatch_one()

        after = clean_firestore.collection("sessions").document(victim_id).get()
        assert after.exists, "calibration deleted a pre-existing session"
        assert after.to_dict() == victim, "calibration modified a pre-existing session"

        result_after = clean_firestore.collection("sessionResults").document(victim_id).get()
        assert result_after.to_dict() == {"finalProbability": 0.42}
    finally:
        clean_firestore.collection("sessions").document(victim_id).delete()
        clean_firestore.collection("sessionResults").document(victim_id).delete()


def test_calibration_never_writes_to_session_results(clean_firestore):
    """sessionResults is agent-owned. Calibration reads it and nothing more (N5)."""
    session_id = _dispatch_one()
    assert not clean_firestore.collection("sessionResults").document(session_id).get().exists


# ==========================================================
# Reads
# ==========================================================

def test_reading_a_session_that_does_not_exist_returns_none(clean_firestore):
    from calibration import firestore_client

    assert firestore_client.read_session("cal_does_not_exist") is None
    assert firestore_client.read_session_result("cal_does_not_exist") is None


def test_reading_an_absent_evidence_subcollection_returns_empty(clean_firestore):
    """
    Normal for an evidence-thin forecast. Must not be an error — the
    projection degrades and the harvest continues.
    """
    from calibration import firestore_client

    session_id = _dispatch_one()
    assert firestore_client.read_evidence(session_id) == []


def test_evidence_subcollection_round_trips(clean_firestore):
    from calibration import firestore_client

    session_id = _dispatch_one()
    evidence = clean_firestore.collection("sessions").document(session_id).collection("evidence")
    evidence.document("e1").set({"sourceType": "news", "title": "A"})
    evidence.document("e2").set({"sourceType": "social", "title": "B"})

    docs = firestore_client.read_evidence(session_id)
    assert len(docs) == 2
    assert {d["sourceType"] for d in docs} == {"news", "social"}


# ==========================================================
# Full round trip: dispatch -> simulated agent -> harvest
# ==========================================================

def _simulate_agent(db, session_id, status="done", probability=0.67, evidence=True):
    """
    Stand in for the agent: move the session to a terminal state and, on
    success, write the SessionResult and evidence the harvester will read.
    """
    db.collection("sessions").document(session_id).update(
        {"status": status, "latestProbability": probability if status == "done" else None}
    )
    if status != "done":
        return

    db.collection("sessionResults").document(session_id).set(
        {
            "finalProbability": probability,
            "confidence": 0.8,
            "tier": "tier_1",
            "agentVersion": "0.5.0-sprint26+55e8093",
            "keyFactors": [{"title": "Driver one"}, {"title": "Driver two"}],
        }
    )
    if evidence:
        sub = db.collection("sessions").document(session_id).collection("evidence")
        sub.document("e1").set({"sourceType": "news"})
        sub.document("e2").set({"sourceType": "news"})
        sub.document("e3").set({"sourceType": "market"})


def test_full_round_trip_through_the_projection(clean_firestore):
    """
    Dispatch, let a simulated agent finish, then read everything back through
    the same code the harvester uses.
    """
    from calibration import evidence_projection, firestore_client
    from calibration.services.harvest_service import classify_session

    session_id = _dispatch_one()
    _simulate_agent(clean_firestore, session_id)

    session = firestore_client.read_session(session_id)
    status, error = classify_session(
        session, datetime.now(timezone.utc) - timedelta(minutes=1),
        datetime.now(timezone.utc), 120,
    )
    assert status == "completed"
    assert error is None

    result = firestore_client.read_session_result(session_id)
    evidence = firestore_client.read_evidence(session_id)

    assert evidence_projection.extract_probability(result) == 0.67
    assert evidence_projection.extract_confidence(result) == 0.8
    assert evidence_projection.extract_tier(result) == "tier_1"
    assert evidence_projection.extract_agent_version(result) == "0.5.0-sprint26+55e8093"

    projection = evidence_projection.project_evidence(result, evidence)
    assert projection["evidence_count_total"] == 3
    assert projection["evidence_count_by_source_type"]["news"] == 2
    assert set(projection["vault_types_present"]) == {"knowledge", "momentum"}
    assert projection["top_3_key_factor_titles"] == ["Driver one", "Driver two"]


def test_a_failed_session_round_trips_as_failed(clean_firestore):
    from calibration import firestore_client
    from calibration.services.harvest_service import classify_session

    session_id = _dispatch_one()
    clean_firestore.collection("sessions").document(session_id).update(
        {"status": "failed", "errorMessage": "vault unavailable"}
    )

    status, error = classify_session(
        firestore_client.read_session(session_id),
        datetime.now(timezone.utc), datetime.now(timezone.utc), 120,
    )
    assert status == "failed"
    assert error == "vault unavailable"


def test_a_clarification_session_round_trips_as_needs_clarification(clean_firestore):
    """
    The state the pre-revision schema had no slot for. Proven end to end
    against a real database rather than only in a unit test.
    """
    from calibration import firestore_client
    from calibration.services.harvest_service import classify_session

    session_id = _dispatch_one()
    clean_firestore.collection("sessions").document(session_id).update(
        {
            "status": "awaiting_clarification",
            "clarificationCandidates": ["Did you mean A or B?"],
        }
    )

    status, error = classify_session(
        firestore_client.read_session(session_id),
        datetime.now(timezone.utc), datetime.now(timezone.utc), 120,
    )
    assert status == "needs_clarification"
    assert error is None


def test_a_completed_session_with_no_evidence_still_projects(clean_firestore):
    """An evidence-thin forecast must not be lost."""
    from calibration import evidence_projection, firestore_client

    session_id = _dispatch_one()
    _simulate_agent(clean_firestore, session_id, evidence=False)

    projection = evidence_projection.project_evidence(
        firestore_client.read_session_result(session_id),
        firestore_client.read_evidence(session_id),
    )
    assert projection["evidence_count_total"] == 0
    assert projection["vault_types_present"] == []
