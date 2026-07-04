"""
Gate 3 emulator integration tests for the Sprint 24 follow-up path
(T24.12 + the Gate-3 halves of T24.14 and T24.15).

Purpose: exercise the parts Gate 1/2 mock — the REAL Firestore
`@firestore.transactional` atomic claim (`claim_and_write_followup_answer`),
the real `sent -> answered` flip, the real collection reads in `load_context`,
and the sweep — against the emulator. The gpt-4o-mini call is mocked at
`answer_from_context._get_default_client` (Gate 3 is about Firestore
round-trip, not LLM output; the real OpenAI call is E2E / T24.13).

If the emulator is unreachable the whole file skips (not fails), matching the
existing emulator-test convention (conftest `emulator_db`).

Coverage:
    T24.12  — follow-up round trip: done session + sent user message → run the
              subgraph → assistant reply written, user message flipped to
              'answered', replyToMessageId linkage, within the ≤7s target.
    T24.14a — no double-answer across a simulated listener re-attach / worker
              restart: running the subgraph twice for the same message yields
              exactly ONE assistant reply (the atomic claim's second pass is a
              no-op).
    T24.14b — back-to-back: two rapid sent messages are each answered
              independently, each assistant linked to the right question by
              replyToMessageId (never a positional pick).
    T24.15  — a message written while the session is still 'running' is SKIPPED
              by the listener (parent-done guard) and then answered by the
              done-transition sweep.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from firebase_admin import firestore as fb_firestore

from agent.followup import listener
from agent.followup.nodes import answer_from_context


# ==========================================================
# Seeding helpers
# ==========================================================
def _seed_done_session(db, session_id: str) -> None:
    """A completed forecast: sessions/{id}.status='done' + a minimal
    sessionResults/{id} doc (load_context reads it; content is not asserted)."""
    db.collection("sessions").document(session_id).set({
        "status": "done",
        "createdAt": fb_firestore.SERVER_TIMESTAMP,
        "updatedAt": fb_firestore.SERVER_TIMESTAMP,
    })
    db.collection("sessionResults").document(session_id).set({
        "sessionId": session_id,
        "finalProbability": 0.72,
        "confidence": 0.81,
        "bottomLineAnswer": "Likely yes.",
        "keyFactors": [],
        "whatIDidntFind": [],
    })


def _seed_running_session(db, session_id: str) -> None:
    """A forecast still in flight — status='running', no sessionResults yet."""
    db.collection("sessions").document(session_id).set({
        "status": "running",
        "createdAt": fb_firestore.SERVER_TIMESTAMP,
        "updatedAt": fb_firestore.SERVER_TIMESTAMP,
    })


def _seed_user_message(db, session_id: str, message_id: str, content: str) -> None:
    """A user follow-up message as the server writes it (role='user',
    status='sent')."""
    db.collection("sessions").document(session_id).collection("messages").document(
        message_id
    ).set({
        "role": "user",
        "content": content,
        "status": "sent",
        "createdAt": fb_firestore.SERVER_TIMESTAMP,
    })


def _answerable_client(answer: str = "Because the evidence was dense.") -> MagicMock:
    """Mock OpenAI client returning a valid 'answerable' FollowupOutput."""
    import json

    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
            "classification": "answerable",
            "classification_reason": "on-topic",
            "answer": answer,
        })))],
        usage=SimpleNamespace(prompt_tokens=200, completion_tokens=100, total_tokens=300),
    )
    return client


def _messages(db, session_id: str) -> list[dict]:
    docs = (
        db.collection("sessions").document(session_id).collection("messages").stream()
    )
    out = []
    for d in docs:
        data = d.to_dict()
        data["_id"] = d.id
        out.append(data)
    return out


def _assistant_messages(db, session_id: str) -> list[dict]:
    return [m for m in _messages(db, session_id) if m.get("role") == "assistant"]


def _user_message(db, session_id: str, message_id: str) -> dict:
    return (
        db.collection("sessions").document(session_id)
        .collection("messages").document(message_id).get().to_dict()
    )


# ==========================================================
# T24.12 — follow-up round trip
# ==========================================================
def test_gate3_followup_round_trip(emulator_db, emulator_test_id):
    db = emulator_db
    session_id = f"{emulator_test_id}_rt"
    message_id = "msg-rt-1"
    _seed_done_session(db, session_id)
    _seed_user_message(db, session_id, message_id, "Why is the confidence so high?")

    with patch.object(
        answer_from_context, "_get_default_client",
        return_value=_answerable_client("Dense, consistent evidence."),
    ):
        start = time.time()
        listener._run_followup(session_id, message_id, "Why is the confidence so high?")
        elapsed = time.time() - start

    # Assistant reply written, linked to the triggering message.
    assistants = _assistant_messages(db, session_id)
    assert len(assistants) == 1
    assert assistants[0]["content"] == "Dense, consistent evidence."
    assert assistants[0]["replyToMessageId"] == message_id
    assert "status" not in assistants[0]  # no frontend-facing status on assistant

    # User message flipped sent -> answered (leaves the listener filter set).
    assert _user_message(db, session_id, message_id)["status"] == "answered"

    # ≤7s end-to-end target (mocked LLM → trivially met; real latency is E2E).
    assert elapsed < 7.0


# ==========================================================
# T24.14a — no double-answer across a simulated re-attach / restart
# ==========================================================
def test_gate3_no_double_answer_on_reattach(emulator_db, emulator_test_id):
    db = emulator_db
    session_id = f"{emulator_test_id}_dup"
    message_id = "msg-dup-1"
    _seed_done_session(db, session_id)
    _seed_user_message(db, session_id, message_id, "Explain the key factors.")

    with patch.object(
        answer_from_context, "_get_default_client",
        return_value=_answerable_client(),
    ):
        # First delivery answers it; second (simulated re-attach / restart
        # redelivery) must be a no-op via the atomic claim.
        listener._run_followup(session_id, message_id, "Explain the key factors.")
        listener._run_followup(session_id, message_id, "Explain the key factors.")

    assistants = _assistant_messages(db, session_id)
    assert len(assistants) == 1, "atomic claim must prevent a second assistant reply"
    assert _user_message(db, session_id, message_id)["status"] == "answered"


# ==========================================================
# T24.14b — back-to-back messages, independent linkage
# ==========================================================
def test_gate3_back_to_back_independent_linkage(emulator_db, emulator_test_id):
    db = emulator_db
    session_id = f"{emulator_test_id}_b2b"
    _seed_done_session(db, session_id)
    _seed_user_message(db, session_id, "q1", "First question?")
    _seed_user_message(db, session_id, "q2", "Second question?")

    with patch.object(
        answer_from_context, "_get_default_client",
        return_value=_answerable_client(),
    ):
        listener._run_followup(session_id, "q1", "First question?")
        listener._run_followup(session_id, "q2", "Second question?")

    assistants = _assistant_messages(db, session_id)
    # Both answered, each linked to the RIGHT question (never positional).
    assert len(assistants) == 2
    linked = {a["replyToMessageId"] for a in assistants}
    assert linked == {"q1", "q2"}
    assert _user_message(db, session_id, "q1")["status"] == "answered"
    assert _user_message(db, session_id, "q2")["status"] == "answered"


# ==========================================================
# T24.15 — listener skips pre-done message; sweep catches it
# ==========================================================
def _fake_change(*, message_id: str, content: str, session_id: str):
    """Fake collection-group change whose reference chain resolves to
    sessions/{session_id} (mirrors the real Firestore reference walk)."""
    session_doc_ref = SimpleNamespace(id=session_id)
    messages_coll_ref = SimpleNamespace(parent=session_doc_ref)
    reference = SimpleNamespace(parent=messages_coll_ref)
    document = SimpleNamespace(
        id=message_id,
        reference=reference,
        to_dict=lambda: {"role": "user", "content": content, "status": "sent"},
    )
    return SimpleNamespace(type=SimpleNamespace(name="ADDED"), document=document)


def test_gate3_listener_skips_running_then_sweep_answers(emulator_db, emulator_test_id):
    db = emulator_db
    session_id = f"{emulator_test_id}_sweep"
    message_id = "msg-sweep-1"
    # Message arrives while the forecast is still running.
    _seed_running_session(db, session_id)
    _seed_user_message(db, session_id, message_id, "Early follow-up.")

    with patch.object(
        answer_from_context, "_get_default_client",
        return_value=_answerable_client("Answered by the sweep."),
    ):
        # Listener delivery while running → parent-done guard skips it.
        listener._process_message_change(
            _fake_change(message_id=message_id, content="Early follow-up.", session_id=session_id)
        )
        assert _assistant_messages(db, session_id) == []
        assert _user_message(db, session_id, message_id)["status"] == "sent"

        # Forecast completes → session flips to done → sweep catches the message.
        db.collection("sessions").document(session_id).update({"status": "done"})
        db.collection("sessionResults").document(session_id).set({
            "sessionId": session_id, "finalProbability": 0.5, "confidence": 0.5,
            "keyFactors": [], "whatIDidntFind": [],
        })
        listener.sweep_done_session(session_id)

    assistants = _assistant_messages(db, session_id)
    assert len(assistants) == 1
    assert assistants[0]["replyToMessageId"] == message_id
    assert _user_message(db, session_id, message_id)["status"] == "answered"
