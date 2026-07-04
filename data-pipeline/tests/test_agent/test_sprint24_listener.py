"""
Gate 1 unit tests for the follow-up listener + done-transition sweep
(Sprint 24, T24.1 / T24.15 — the routing/guard logic, mocked at the
firestore_client + subgraph boundary).

Strategy:
    - Mock firestore_client reads (get_session_doc, get_unanswered_user_messages).
    - Mock the subgraph invocation by patching listener._run_followup, so these
      tests exercise the listener's parent-`done` guard, the sweep's guard, and
      the drain behavior WITHOUT running the real graph or hitting OpenAI.

Coverage:
    _session_is_done       — done→True; other status→False; read error→False.
    _process_message_change — parent done → runs; parent not done → skips.
    sweep_done_session      — not done → no sweep; done+pending → runs each;
                              done+empty → no-op; read error → never raises.
    _run_followup           — invokes the subgraph with exactly the 3 seeded
                              fields (trigger_message_id/-question threaded).
    listener drain          — shutdown_event set → remaining messages skipped;
                              one bad message → logged + continue (not fatal).
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from agent import firestore_client
from agent.followup import listener


# ==========================================================
# Fakes
# ==========================================================
def _fake_change(
    *, message_id: str, content: str, session_id: str, change_type: str = "ADDED"
) -> SimpleNamespace:
    """
    A fake collection-group snapshot change. `document.reference.parent.parent`
    walks messages-doc → messages-collection → session-doc (carrying `id`),
    mirroring the real Firestore reference chain the listener relies on.
    """
    session_doc_ref = SimpleNamespace(id=session_id)
    messages_coll_ref = SimpleNamespace(parent=session_doc_ref)
    reference = SimpleNamespace(parent=messages_coll_ref)
    document = SimpleNamespace(
        id=message_id,
        reference=reference,
        to_dict=lambda: {"content": content, "role": "user", "status": "sent"},
    )
    return SimpleNamespace(
        type=SimpleNamespace(name=change_type), document=document
    )


# ==========================================================
# _session_is_done
# ==========================================================
def test_session_is_done_true(monkeypatch):
    monkeypatch.setattr(
        firestore_client, "get_session_doc", lambda sid: {"status": "done"}
    )
    assert listener._session_is_done("s1") is True


def test_session_is_done_false_for_other_status(monkeypatch):
    monkeypatch.setattr(
        firestore_client, "get_session_doc",
        lambda sid: {"status": "awaiting_clarification"},
    )
    assert listener._session_is_done("s1") is False


def test_session_is_done_false_on_read_error(monkeypatch):
    def _boom(sid):
        raise RuntimeError("firestore transient")

    monkeypatch.setattr(firestore_client, "get_session_doc", _boom)
    # M1: guarded — returns False, does not raise.
    assert listener._session_is_done("s1") is False


# ==========================================================
# _process_message_change
# ==========================================================
def test_process_message_change_runs_when_parent_done(monkeypatch):
    calls = []
    monkeypatch.setattr(listener, "_session_is_done", lambda sid: True)
    monkeypatch.setattr(
        listener, "_run_followup",
        lambda sid, mid, content: calls.append((sid, mid, content)),
    )

    change = _fake_change(
        message_id="m1", content="why?", session_id="s1"
    )
    listener._process_message_change(change)
    assert calls == [("s1", "m1", "why?")]


def test_process_message_change_skips_when_not_done(monkeypatch):
    calls = []
    monkeypatch.setattr(listener, "_session_is_done", lambda sid: False)
    monkeypatch.setattr(
        listener, "_run_followup",
        lambda sid, mid, content: calls.append((sid, mid, content)),
    )

    change = _fake_change(message_id="m1", content="why?", session_id="s1")
    listener._process_message_change(change)
    assert calls == []  # skipped; sweep will catch it later


# ==========================================================
# sweep_done_session
# ==========================================================
def test_sweep_skips_when_not_done(monkeypatch):
    calls = []
    monkeypatch.setattr(listener, "_session_is_done", lambda sid: False)
    unanswered = MagicMock()
    monkeypatch.setattr(firestore_client, "get_unanswered_user_messages", unanswered)
    monkeypatch.setattr(
        listener, "_run_followup", lambda *a: calls.append(a)
    )

    listener.sweep_done_session("s1")
    assert calls == []
    unanswered.assert_not_called()  # never even queries when not done


def test_sweep_processes_unanswered_when_done(monkeypatch):
    calls = []
    monkeypatch.setattr(listener, "_session_is_done", lambda sid: True)
    monkeypatch.setattr(
        firestore_client, "get_unanswered_user_messages",
        lambda sid: [
            {"messageId": "m1", "content": "q1"},
            {"messageId": "m2", "content": "q2"},
        ],
    )
    monkeypatch.setattr(
        listener, "_run_followup",
        lambda sid, mid, content: calls.append((sid, mid, content)),
    )

    listener.sweep_done_session("s1")
    assert calls == [("s1", "m1", "q1"), ("s1", "m2", "q2")]


def test_sweep_noop_when_no_pending(monkeypatch):
    calls = []
    monkeypatch.setattr(listener, "_session_is_done", lambda sid: True)
    monkeypatch.setattr(
        firestore_client, "get_unanswered_user_messages", lambda sid: []
    )
    monkeypatch.setattr(listener, "_run_followup", lambda *a: calls.append(a))

    listener.sweep_done_session("s1")
    assert calls == []


def test_sweep_never_raises_on_status_read_error(monkeypatch):
    # get_session_doc raises → _session_is_done False → sweep returns cleanly.
    def _boom(sid):
        raise RuntimeError("firestore transient")

    monkeypatch.setattr(firestore_client, "get_session_doc", _boom)
    listener.sweep_done_session("s1")  # must not raise


def test_sweep_continues_past_one_bad_message(monkeypatch):
    calls = []
    monkeypatch.setattr(listener, "_session_is_done", lambda sid: True)
    monkeypatch.setattr(
        firestore_client, "get_unanswered_user_messages",
        lambda sid: [
            {"messageId": "m1", "content": "q1"},
            {"messageId": "m2", "content": "q2"},
        ],
    )

    def _run(sid, mid, content):
        if mid == "m1":
            raise RuntimeError("subgraph blew up on m1")
        calls.append((sid, mid, content))

    monkeypatch.setattr(listener, "_run_followup", _run)
    listener.sweep_done_session("s1")  # must not raise
    assert calls == [("s1", "m2", "q2")]  # m2 still processed


# ==========================================================
# _run_followup
# ==========================================================
def test_run_followup_invokes_subgraph_with_seeded_fields(monkeypatch):
    invoked = {}
    fake_graph = MagicMock()
    fake_graph.invoke = MagicMock(side_effect=lambda st: invoked.update(st))
    monkeypatch.setattr(listener, "followup_graph", fake_graph)

    listener._run_followup("s1", "m1", "why so confident?")
    assert invoked == {
        "parent_session_id": "s1",
        "trigger_message_id": "m1",
        "trigger_question": "why so confident?",
    }


# ==========================================================
# Listener drain + per-message isolation
# ==========================================================
def _capture_callback(monkeypatch):
    """Subscribe via start_followup_listener and capture the callback the SDK
    would receive, so we can drive snapshots synchronously."""
    captured = {}

    def _fake_subscribe(cb):
        captured["cb"] = cb
        return MagicMock()  # fake Watch handle

    monkeypatch.setattr(
        firestore_client, "subscribe_followup_messages", _fake_subscribe
    )
    return captured


def test_listener_drains_on_shutdown(monkeypatch):
    processed = []
    monkeypatch.setattr(
        listener, "_process_message_change",
        lambda change: processed.append(change.document.id),
    )
    captured = _capture_callback(monkeypatch)

    shutdown = threading.Event()
    shutdown.set()  # already draining
    listener.start_followup_listener(shutdown, on_fatal=lambda: None)

    change = _fake_change(message_id="m1", content="q", session_id="s1")
    captured["cb"](None, [change], None)
    assert processed == []  # drain → skipped


def test_listener_continues_past_one_bad_message(monkeypatch):
    processed = []

    def _process(change):
        if change.document.id == "m1":
            raise RuntimeError("bad message")
        processed.append(change.document.id)

    monkeypatch.setattr(listener, "_process_message_change", _process)
    captured = _capture_callback(monkeypatch)

    fatal_called = []
    listener.start_followup_listener(
        threading.Event(), on_fatal=lambda: fatal_called.append(True)
    )

    changes = [
        _fake_change(message_id="m1", content="q1", session_id="s1"),
        _fake_change(message_id="m2", content="q2", session_id="s1"),
    ]
    captured["cb"](None, changes, None)
    # Per-message try/except: m1 fails but m2 still processed; NOT fatal.
    assert processed == ["m2"]
    assert fatal_called == []
