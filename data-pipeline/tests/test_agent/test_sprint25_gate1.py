"""
Gate 1 unit tests for Sprint 25 — Suggested Actions + Chain-of-Thought Events
(T25.9, incl. the T25.7 zero-follow-up-events assertion).

Strategy (matches the existing node/emitter tests):
    - suggested_actions node: pure-mock at the OpenAI client boundary via the
      node's injected `client` kwarg; fake openai>=1.x response objects.
    - events emitter: patch `firestore_client.write_agent_event` (the single
      Firestore write path the background writer calls) to a recorder, then
      `events.drain()` so all writes complete before asserting. The
      `event_writes` fixture also isolates emitter state per test via
      `events.reset()`.
    - claim_session: mock claim_query + update_session_status; assert the
      bootstrap sequence and — the highest-value test — the resume-on-clarify
      event-path (findings #3): events land under the ORIGINAL session id.

Coverage:
    prompts/suggested_actions.py — strict schema (exactly 3 {label,prompt});
        build_user_message renders forecast + gaps.
    generate_suggested_actions   — sa-1/2/3 ids + born-instrumented cost; call
        failure → [] (no cost); parse failure → [] (cost kept); event done on
        success, FAILED on degrade (Ron 2026-07-15).
    agent/events.py              — emit before init_run no-ops; 11 panel fields
        + no parentMessageId; monotonic sequence stamped at enqueue + FIFO;
        drain flushes fully; write failure swallowed; emit_done_event one-shot;
        fail_event by run_id / by session_id / no-op before init_run; two
        concurrent runs keep independent sequences (dispose-not-prune).
    claim_session                — first-time bootstrap + resume-on-clarify
        events under the ORIGINAL session id (findings #3).
    firestore_client             — current_run_id Convention-A kwarg.
    T25.7                        — the follow-up path emits ZERO agentEvents.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent import events, firestore_client
from agent.followup.nodes import load_context
from agent.nodes import claim_session
from agent.nodes import generate_suggested_actions as gsa
from agent.prompts import suggested_actions as sa_prompt


# ==========================================================
# Fixtures / helpers
# ==========================================================
@pytest.fixture
def event_writes(monkeypatch):
    """Record every agentEvents write the emitter's background writer makes and
    isolate emitter state per test. The writer calls
    firestore_client.write_agent_event at drain time, so patching it here
    captures the actual (session_id, event_id, fields, merge) the run produced.
    """
    writes: list[dict] = []

    def _record(session_id, event_id, fields, *, merge=False):
        writes.append(
            {
                "session_id": session_id,
                "event_id": event_id,
                "fields": dict(fields),
                "merge": merge,
            }
        )

    monkeypatch.setattr(firestore_client, "write_agent_event", _record)
    events.reset()
    yield writes
    events.reset()


def _sa_response(actions, *, total_tokens: int = 150) -> SimpleNamespace:
    """openai>=1.x ChatCompletion shape carrying a suggested-actions payload."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps({"actions": actions}))
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=100, completion_tokens=50, total_tokens=total_tokens
        ),
    )


def _three_actions() -> list[dict]:
    return [{"label": f"Label {i}", "prompt": f"Prompt {i}"} for i in range(1, 4)]


def _client_returning(response) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create = MagicMock(return_value=response)
    return client


def _client_raising(exc: Exception) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create = MagicMock(side_effect=exc)
    return client


def _synth_state(**overrides) -> dict:
    base = {
        "run_id": None,
        "raw_question": "Will the Fed cut rates by Q2 2026?",
        "synthesis_result": {
            "finalProbability": 0.72,
            "confidence": 0.81,
            "bottomLineAnswer": "Likely.",
            "keyFactors": [{"label": "Inflation below consensus", "direction": "increases"}],
            "whatIDidntFind": [],
        },
    }
    base.update(overrides)
    return base


# ==========================================================
# prompts/suggested_actions.py
# ==========================================================
def test_suggested_actions_schema_is_strict_exactly_three():
    schema = sa_prompt.RESPONSE_SCHEMA
    assert schema["strict"] is True
    inner = schema["schema"]
    assert inner["additionalProperties"] is False
    actions = inner["properties"]["actions"]
    assert actions["minItems"] == actions["maxItems"] == sa_prompt.SUGGESTED_ACTIONS_COUNT == 3
    item = actions["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {"label", "prompt"}


def test_suggested_actions_build_user_message_includes_forecast_and_gaps():
    msg = sa_prompt.build_user_message(
        question="Will X pass?",
        final_probability=0.6,
        confidence=0.4,
        bottom_line="Uncertain.",
        key_factors=[{"label": "Driver A", "direction": "increases"}],
        gaps=["recent polling data"],
    )
    assert "Will X pass?" in msg
    assert "Driver A" in msg
    assert "recent polling data" in msg
    assert "0.6" in msg  # probability rendered


# ==========================================================
# generate_suggested_actions node
# ==========================================================
def test_gsa_success_assigns_ids_and_accumulates_cost(monkeypatch):
    monkeypatch.setattr(gsa.llm_cost, "record_usage", lambda m, r, *, site: (150, 0.0004))
    client = _client_returning(_sa_response(_three_actions()))
    out = gsa.run(_synth_state(total_cost_usd=0.001), client=client)

    assert [a["id"] for a in out["suggested_actions"]] == ["sa-1", "sa-2", "sa-3"]
    assert out["suggested_actions"][0] == {"id": "sa-1", "label": "Label 1", "prompt": "Prompt 1"}
    assert out["total_cost_usd"] == pytest.approx(0.0014)  # 0.001 prior + 0.0004
    assert out["llm_calls_count"] == 1
    assert out["total_tokens_used"] == 150


def test_gsa_call_failure_degrades_to_empty_no_cost():
    client = _client_raising(RuntimeError("boom"))
    out = gsa.run(_synth_state(), client=client)
    assert out["suggested_actions"] == []
    assert "total_cost_usd" not in out  # nothing was spent


def test_gsa_parse_failure_degrades_but_keeps_cost(monkeypatch):
    monkeypatch.setattr(gsa.llm_cost, "record_usage", lambda m, r, *, site: (150, 0.0004))
    # Only 2 actions — _extract_actions requires exactly 3 → parse failure.
    client = _client_returning(_sa_response([{"label": "L", "prompt": "P"}]))
    out = gsa.run(_synth_state(total_cost_usd=0.001), client=client)
    assert out["suggested_actions"] == []
    assert out["total_cost_usd"] == pytest.approx(0.0014)  # call succeeded → cost kept


def test_gsa_event_done_on_success(event_writes, monkeypatch):
    monkeypatch.setattr(gsa.llm_cost, "record_usage", lambda m, r, *, site: (150, 0.0004))
    events.init_run("s1", "r1")
    client = _client_returning(_sa_response(_three_actions()))
    gsa.run(_synth_state(run_id="r1"), client=client)
    events.drain(2.0)
    updates = [w for w in event_writes if w["merge"]]
    assert updates and updates[-1]["fields"]["status"] == "done"


def test_gsa_event_failed_on_degrade(event_writes):
    events.init_run("s1", "r1")
    client = _client_raising(RuntimeError("boom"))
    gsa.run(_synth_state(run_id="r1"), client=client)
    events.drain(2.0)
    updates = [w for w in event_writes if w["merge"]]
    assert updates and updates[-1]["fields"]["status"] == "failed"


# ==========================================================
# agent/events.py — emitter
# ==========================================================
def test_emit_before_init_run_is_noop(event_writes):
    # No init_run for this run_id → emit is a logged no-op returning None.
    assert events.emit_event("ghost-run", "vault_query", "x") is None
    events.drain(1.0)
    assert event_writes == []


def test_emit_and_complete_write_all_panel_fields(event_writes):
    events.init_run("sess-A", "run-A")
    ev = events.emit_event(
        "run-A", "vault_query", "Gathering evidence…",
        description="desc", payload={"k": 1},
    )
    events.complete_event("run-A", ev)
    events.drain(2.0)

    creates = [w for w in event_writes if not w["merge"]]
    updates = [w for w in event_writes if w["merge"]]
    assert len(creates) == 1 and len(updates) == 1

    doc = creates[0]["fields"]
    assert set(doc) == {
        "eventId", "sessionId", "runId", "sequence", "type", "title",
        "description", "status", "durationMs", "payload", "timestamp",
    }
    assert "parentMessageId" not in doc            # dead field never written
    assert creates[0]["session_id"] == "sess-A"
    assert doc["runId"] == "run-A"
    assert doc["sessionId"] == "sess-A"
    assert doc["sequence"] == 1
    assert doc["type"] == "vault_query"
    assert doc["title"] == "Gathering evidence…"
    assert doc["status"] == "running"
    assert doc["durationMs"] is None
    assert doc["payload"] == {"k": 1}

    upd = updates[0]
    assert upd["merge"] is True
    assert upd["event_id"] == ev                   # completion targets the same doc id
    assert upd["fields"]["status"] == "done"
    assert isinstance(upd["fields"]["durationMs"], int)   # start→complete delta, ms


def test_sequence_is_monotonic_and_fifo(event_writes):
    events.init_run("s", "r")
    events.emit_event("r", "a", "A")
    events.emit_event("r", "b", "B")
    events.emit_event("r", "c", "C")
    events.drain(2.0)
    creates = [w for w in event_writes if not w["merge"]]
    assert [c["fields"]["sequence"] for c in creates] == [1, 2, 3]
    assert [c["fields"]["type"] for c in creates] == ["a", "b", "c"]  # FIFO order preserved


def test_drain_flushes_all_queued_events(event_writes):
    events.init_run("s", "r")
    for _ in range(10):
        events.emit_event("r", "t", "T")
    events.drain(3.0)
    assert len([w for w in event_writes if not w["merge"]]) == 10


def test_writer_swallows_write_failures(monkeypatch):
    events.reset()
    monkeypatch.setattr(
        firestore_client, "write_agent_event",
        MagicMock(side_effect=RuntimeError("firestore down")),
    )
    events.init_run("s", "r")
    events.emit_event("r", "t", "T")
    # drain must return normally despite the writer raising on every task.
    events.drain(2.0)
    events.reset()


def test_emit_done_event_is_one_shot(event_writes):
    events.init_run("s", "r")
    events.emit_done_event("r", "claim_session", "Analyzing your question…")
    events.drain(2.0)
    creates = [w for w in event_writes if not w["merge"]]
    assert len(creates) == 1
    assert creates[0]["fields"]["status"] == "done"
    assert creates[0]["fields"]["durationMs"] is None
    assert [w for w in event_writes if w["merge"]] == []  # no separate completion write


def test_fail_event_by_run_id_marks_inflight_failed(event_writes):
    events.init_run("s", "r")
    events.emit_event("r", "synthesize", "Writing the forecast…")  # in-flight
    events.fail_event(run_id="r", description="boom")
    events.drain(2.0)
    updates = [w for w in event_writes if w["merge"]]
    assert updates and updates[-1]["fields"]["status"] == "failed"


def test_fail_event_session_id_backup(event_writes):
    events.init_run("sess-X", "run-X")
    events.emit_event("run-X", "synthesize", "Writing the forecast…")
    # No run_id (the process_query pre-run_id crash case) → resolve by session.
    events.fail_event(run_id=None, session_id="sess-X", description="boom")
    events.drain(2.0)
    updates = [w for w in event_writes if w["merge"]]
    assert updates and updates[-1]["fields"]["status"] == "failed"


def test_fail_event_before_init_run_is_noop():
    events.reset()
    # Crash before any init_run → nothing resolvable; must no-op, never raise.
    events.fail_event(run_id=None, session_id="never-inited", description="boom")
    events.drain(1.0)
    events.reset()


def test_concurrent_runs_have_independent_sequences(event_writes):
    # Two runs registered before either finishes — the concurrency case. With
    # idle-pruning removed (dispose-at-run-end), both keep independent per-run
    # counters and never interleave into one sequence.
    events.init_run("s1", "r1")
    events.init_run("s2", "r2")
    events.emit_event("r1", "a", "A")
    events.emit_event("r2", "b", "B")
    events.emit_event("r1", "c", "C")
    events.drain(2.0)

    creates = [w for w in event_writes if not w["merge"]]
    by_run: dict[str, list[int]] = {}
    for c in creates:
        by_run.setdefault(c["fields"]["runId"], []).append(c["fields"]["sequence"])
    assert by_run["r1"] == [1, 2]   # r1's own counter, uninterrupted
    assert by_run["r2"] == [1]      # independent, not interleaved


# ==========================================================
# claim_session — bootstrap + findings #3 (resume-on-clarify)
# ==========================================================
def test_claim_session_first_time_mints_run_id_and_bootstraps(event_writes, monkeypatch):
    status_calls: list[tuple] = []
    monkeypatch.setattr(
        firestore_client, "claim_query",
        lambda sid, wid: {"sessionId": "s1", "question": "Q?", "userId": "u1", "queryId": "q1"},
    )
    monkeypatch.setattr(
        firestore_client, "update_session_status",
        lambda sid, status, **kw: status_calls.append((sid, status, kw)),
    )

    out = claim_session.run({"session_id": "s1"})
    events.drain(2.0)

    assert out["session_id"] == "s1"
    assert out["run_id"]
    # currentRunId rides the 'running' transition; the 'claimed' one does NOT.
    running = next(c for c in status_calls if c[1] == "running")
    assert running[2].get("current_run_id") == out["run_id"]
    claimed = next(c for c in status_calls if c[1] == "claimed")
    assert claimed[2].get("current_run_id") is None
    # One-shot 'done' bootstrap event under s1.
    assert event_writes
    boot = event_writes[0]
    assert boot["session_id"] == "s1"
    assert boot["fields"]["type"] == "claim_session"
    assert boot["fields"]["status"] == "done"
    assert boot["fields"]["durationMs"] is None


def test_claim_session_resume_events_land_under_original_session_id(event_writes, monkeypatch):
    # findings #3 (the sprint's highest-value assertion): on resume-on-clarify,
    # the claimed forecastQueries doc's sessionId (the ORIGINAL session) differs
    # from the fresh queue-doc id. Every event MUST land under the ORIGINAL
    # session id, never the fresh queue-doc UUID.
    status_calls: list[tuple] = []
    monkeypatch.setattr(
        firestore_client, "claim_query",
        lambda sid, wid: {
            "sessionId": "orig-sess",         # ← the real session
            "question": "Will X happen?",
            "userId": "u1",
            "queryId": "q1",
        },
    )
    monkeypatch.setattr(
        firestore_client, "update_session_status",
        lambda sid, status, **kw: status_calls.append((sid, status, kw)),
    )

    out = claim_session.run({"session_id": "fresh-queue-uuid"})  # ← the queue-doc id
    events.drain(2.0)

    assert out["session_id"] == "orig-sess"    # resolved to the ORIGINAL, not the queue-doc id
    assert out["run_id"]

    # currentRunId written on 'running', on the ORIGINAL doc.
    running = next(c for c in status_calls if c[1] == "running")
    assert running[0] == "orig-sess"
    assert running[2].get("current_run_id") == out["run_id"]

    # Events land under the ORIGINAL session id, stamped with the run's runId.
    assert event_writes, "expected a bootstrap agentEvent"
    for w in event_writes:
        assert w["session_id"] == "orig-sess"          # NOT 'fresh-queue-uuid'
        assert w["fields"].get("runId") == out["run_id"]


# ==========================================================
# firestore_client.update_session_status — current_run_id (Convention A)
# ==========================================================
def test_update_session_status_current_run_id_is_convention_a(monkeypatch):
    captured: dict = {}

    class _Ref:
        def update(self, data):
            captured.clear()
            captured.update(data)

    class _Col:
        def document(self, _id):
            return _Ref()

    class _DB:
        def collection(self, _name):
            return _Col()

    monkeypatch.setattr(firestore_client, "get_db", lambda: _DB())

    # Provided → currentRunId written.
    firestore_client.update_session_status("s1", "running", current_run_id="run-9")
    assert captured.get("currentRunId") == "run-9"

    # Omitted → NOT written (Convention A: absence means "don't touch").
    firestore_client.update_session_status("s1", "running")
    assert "currentRunId" not in captured


# ==========================================================
# T25.7 — the follow-up path emits ZERO agentEvents
# ==========================================================
def test_followup_path_emits_zero_agent_events(event_writes, monkeypatch):
    # The follow-up graph emits NO agentEvents (plan §1, 2026-07-04). Grep
    # already confirms agent/followup/ has no emitter references; this is the
    # behavioral Gate 1 assertion. load_context is the follow-up entry node.
    monkeypatch.setattr(firestore_client, "get_session_result", lambda sid: {})
    monkeypatch.setattr(firestore_client, "get_session_evidence", lambda sid: [])
    monkeypatch.setattr(firestore_client, "get_recent_messages", lambda sid, n: [])

    load_context.run({"parent_session_id": "sess-1"})
    events.drain(1.0)
    assert event_writes == []
