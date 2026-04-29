"""
Gate 3 emulator integration tests for the Agentic Hub (Sprint 18 T12).

Real Firestore emulator round-trip. Verifies the @firestore.transactional
decorator path that Gate 1 bypassed (via _claim_query_txn.to_wrap), the
real .update() / .set() semantics, and server-side SERVER_TIMESTAMP
resolution. The contract from §9.3: submit a forecastQueries doc, verify
the resulting sessionResults/{id} shape per §8.7.2.

Out of scope (covered elsewhere or deferred):
    - Failure paths — Gate 2 (mocked) covers them comprehensively.
    - True concurrent claim race (threading + Aborted retries) — Sprint 26
      hardening. Sequential contention is enough to prove the guard clause
      against real Firestore data.
    - Worker snapshot listener round-trip — T13 (E2E manual run).

Setup:
    Start the emulator before running:
        firebase emulators:start --only firestore
    Default binding localhost:8080. Override via FIRESTORE_EMULATOR_HOST.
    If unreachable, the file skips (not fails) — matches the existing
    db_available pattern.
"""

from datetime import datetime
from unittest.mock import patch

from firebase_admin import firestore as fb_firestore

from agent import firestore_client
from agent.process_query import process_query


# ==========================================================
# Helpers
# ==========================================================

def _seed_pending_query(db, session_id: str, question: str = "Will it rain?") -> None:
    """Mimic the server-side write at session.repository.ts:335-347 — both
    docs that the worker expects to read against."""
    db.collection("sessions").document(session_id).set({
        "status": "queued",
        "createdAt": fb_firestore.SERVER_TIMESTAMP,
    })
    db.collection("forecastQueries").document(session_id).set({
        "queryId": f"q_{session_id}",
        "sessionId": session_id,
        "userId": "test-user",
        "question": question,
        "status": "pending",
        "createdAt": fb_firestore.SERVER_TIMESTAMP,
        "claimedAt": None,
        "claimedBy": None,
    })


# ==========================================================
# 1. Round-trip — full process_query against the emulator
# ==========================================================

def test_round_trip_writes_session_result(emulator_db, emulator_test_id):
    """Submit a pending forecastQueries doc → run process_query → verify
    sessionResults/{id} matches §8.7.2 shape and all timestamp fields
    resolve to real Timestamps server-side."""
    db = emulator_db
    session_id = f"{emulator_test_id}_round_trip"
    question = "Will Bitcoin hit 100k by EOY?"
    _seed_pending_query(db, session_id, question)

    with patch("agent.process_query.settings.AGENT_WORKER_ID", "worker-test"):
        process_query(session_id)

    # --- sessionResults/{id} ---
    result_doc = db.collection("sessionResults").document(session_id).get()
    assert result_doc.exists, f"sessionResults/{session_id} not written"
    result = result_doc.to_dict()

    # Numerics + derived labels (post-T11.5 fix: evidenceVolumeLabel uses
    # Low/Moderate/High vocabulary)
    assert result["finalProbability"] == 0.5
    assert result["confidence"] == 0.5
    assert result["confidenceLabel"] == "Moderate"
    assert result["consensusStrength"] == "Weak"
    assert result["evidenceVolumeLabel"] == "Low"

    # Market — null + empty triggers Patch 4 "no canonical market" UI state
    assert result["marketProbability"] is None
    assert result["marketComparison"] == []

    # Empty list fields
    assert result["keyFactors"] == []
    assert result["reasoningChain"] == []
    assert result["suggestedActions"] == []

    # whatIDidntFind has the stub-mode caption
    assert isinstance(result["whatIDidntFind"], list)
    assert len(result["whatIDidntFind"]) == 1
    assert "stub" in result["whatIDidntFind"][0].lower()

    # Question text reaches summaryMarkdown
    assert question in result["summaryMarkdown"]

    # Metadata
    assert result["tier"] == "tier_1"
    assert result["agentVersion"] == "0.1.0-sprint18-stub"
    assert result["sessionId"] == session_id  # added by write_session_result wrapper

    # Narrative fields non-empty
    for key in (
        "bottomLineAnswer",
        "detailedExplanation",
        "summaryMarkdown",
        "marketComparisonInsight",
        "sentimentAnalysisInsight",
        "evidenceFeedSummary",
    ):
        assert isinstance(result[key], str) and result[key], f"{key} empty"

    # --- Timestamp resolution check ---
    # Firestore returns DatetimeWithNanoseconds (subclass of datetime) for
    # SERVER_TIMESTAMP fields after server resolution. isinstance(..., datetime)
    # works because of the subclass relationship.
    assert isinstance(result["generatedAt"], datetime), (
        f"generatedAt should be datetime; got {type(result['generatedAt']).__name__}"
    )
    assert isinstance(result["createdAt"], datetime)
    assert isinstance(result["updatedAt"], datetime)

    # --- forecastQueries/{id} ---
    fq = db.collection("forecastQueries").document(session_id).get().to_dict()
    assert fq["status"] == "done"
    assert fq["claimedBy"] == "worker-test"
    assert isinstance(fq["claimedAt"], datetime), (
        "claimedAt must be a real Timestamp; the SERVER_TIMESTAMP sentinel "
        "should have been resolved server-side"
    )

    # --- sessions/{id} ---
    session = db.collection("sessions").document(session_id).get().to_dict()
    assert session["status"] == "done"
    assert isinstance(session["updatedAt"], datetime)
    assert isinstance(session["lastActivityAt"], datetime)


# ==========================================================
# 2. Atomic claim — sequential contention
# ==========================================================

def test_atomic_claim_sequential_contention(emulator_db, emulator_test_id):
    """First claim wins, second loses. Exercises the real
    @firestore.transactional decorator path (BEGIN / GET / UPDATE / COMMIT)
    against the emulator — the path Gate 1 bypassed via to_wrap."""
    db = emulator_db
    session_id = f"{emulator_test_id}_contention"
    _seed_pending_query(db, session_id)

    result_a = firestore_client.claim_query(session_id, "worker-A")
    assert result_a is not None
    assert result_a["sessionId"] == session_id
    assert result_a["status"] == "pending"  # original payload is pre-update snapshot

    result_b = firestore_client.claim_query(session_id, "worker-B")
    assert result_b is None  # already claimed by A

    fq = db.collection("forecastQueries").document(session_id).get().to_dict()
    assert fq["status"] == "claimed"
    assert fq["claimedBy"] == "worker-A"  # B never overwrote A's claim
    assert isinstance(fq["claimedAt"], datetime)


# ==========================================================
# 3. Race-lost — pre-claimed doc returns None and is unchanged
# ==========================================================

def test_claim_already_claimed_returns_none(emulator_db, emulator_test_id):
    """Doc that was already claimed (e.g., by another worker before us)
    must return None and leave the doc completely untouched. Verified
    against real Firestore data, not just mocked snapshots."""
    db = emulator_db
    session_id = f"{emulator_test_id}_already_claimed"

    db.collection("forecastQueries").document(session_id).set({
        "queryId": f"q_{session_id}",
        "sessionId": session_id,
        "userId": "test-user",
        "question": "test",
        "status": "claimed",
        "createdAt": fb_firestore.SERVER_TIMESTAMP,
        "claimedAt": fb_firestore.SERVER_TIMESTAMP,
        "claimedBy": "someone-else",
    })

    result = firestore_client.claim_query(session_id, "worker-test")
    assert result is None

    fq = db.collection("forecastQueries").document(session_id).get().to_dict()
    assert fq["status"] == "claimed"
    assert fq["claimedBy"] == "someone-else"  # untouched
