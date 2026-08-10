"""
Gate 1 tests for `agent/nodes/claim_session.py` (T19.12).

claim_session is the only node that talks to the Firestore claim path.
Sprint 18 covered most of these behaviours from the runner side in
test_process_query.py; T19.11 moved claim_query off the runner, so the
behaviours moved with it. This file is the new home for:

  - claim_query call shape (worker_id from settings, query_doc_id == session_id)
  - claimed → running status transition order
  - race-loss detection (claim_query returns None → SessionClaimRaceLostError)
  - status-update failure wrapping
  - payload field propagation (raw_question, user_id)
  - state validation (session_id missing)

Mocking strategy:
    claim_session.py uses `from agent import firestore_client` and calls
    `firestore_client.claim_query(...)`. That is module-attribute access at
    call time, so patching `agent.firestore_client.<func>` is what
    intercepts the call (NOT `agent.nodes.claim_session.firestore_client.<func>`).
    settings.AGENT_WORKER_ID is patched on the imported settings object.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.3.2 (Node 0 in topology)
    - data-pipeline/docs/agentic_hub_spec.md §8.7.1 (Firestore document model)
    - data-pipeline/docs/agentic_hub_spec.md §8.8.1 (worker pattern, atomic claim)
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from agent.errors import AgentProcessingError, SessionClaimRaceLostError
from agent.nodes import claim_session


# ==========================================================
# Helpers
# ==========================================================
def _claim_payload(
    session_id="s1",
    query_id="q1",
    question="Will the Fed cut rates?",
    user_id="u1",
) -> dict:
    """Mirror the dict shape `firestore_client.claim_query` returns."""
    return {
        "queryId": query_id,
        "sessionId": session_id,
        "userId": user_id,
        "question": question,
        "status": "pending",
        "createdAt": MagicMock(),
        "claimedAt": None,
        "claimedBy": None,
    }


@pytest.fixture
def mocked_firestore():
    """Patch claim_query + update_session_status at the firestore_client
    module — claim_session.py reaches them via `firestore_client.<attr>`,
    so module-level patches intercept correctly. Returns a manager mock
    with both children attached so .method_calls exposes a single ordered
    list of cross-mock calls."""
    with (
        patch("agent.firestore_client.claim_query") as mock_claim,
        patch("agent.firestore_client.update_session_status") as mock_status,
        # Sprint 25 T25.6: stub the events emitter so claim_session's bootstrap
        # doesn't spin up the real background writer / touch Firestore — these
        # tests isolate CLAIM behaviour (bootstrap event → test_sprint25_gate1).
        patch("agent.events.init_run"),
        patch("agent.events.emit_done_event"),
    ):
        manager = MagicMock()
        manager.attach_mock(mock_claim, "claim_query")
        manager.attach_mock(mock_status, "update_session_status")
        yield manager, mock_claim, mock_status


# ==========================================================
# 1. State validation — session_id missing
# ==========================================================
@pytest.mark.parametrize("session_id", [None, "", "   "])
def test_run_raises_when_session_id_missing(session_id, mocked_firestore):
    """claim_session is the entry node — its only required input is
    session_id. Reaching it without one means a runner bug; fail loudly
    with AgentProcessingError so the runner's _mark_failed path catches
    it. Does NOT raise SessionClaimRaceLostError — that's specifically
    for race-loss, not a missing input."""
    _, mock_claim, mock_status = mocked_firestore

    state = {"session_id": session_id} if session_id is not None else {}

    with pytest.raises(AgentProcessingError) as exc_info:
        claim_session.run(state)

    assert "missing session_id" in str(exc_info.value)
    assert not isinstance(exc_info.value, SessionClaimRaceLostError)
    mock_claim.assert_not_called()
    mock_status.assert_not_called()


# ==========================================================
# 2. Happy path — call ordering
# ==========================================================
def test_run_calls_claim_then_claimed_then_running_in_order(mocked_firestore):
    """Verify the three Firestore calls happen in the contracted sequence:
    claim_query → update_session_status('claimed') → update_session_status('running').
    Pinning the order matters because the frontend onSnapshot listener
    drives the user-visible status pill off of these transitions."""
    manager, mock_claim, _ = mocked_firestore
    mock_claim.return_value = _claim_payload(session_id="s1")

    with patch.object(claim_session.settings, "AGENT_WORKER_ID", "worker-1"):
        out = claim_session.run({"session_id": "s1"})

    # Sprint 25 T25.6: the 'running' transition now also carries current_run_id
    # (the minted run_id, also returned as state.run_id); 'claimed' does NOT.
    assert manager.method_calls == [
        call.claim_query("s1", "worker-1"),
        call.update_session_status("s1", "claimed"),
        call.update_session_status("s1", "running", current_run_id=out["run_id"]),
    ]


# ==========================================================
# 3. Worker ID from settings
# ==========================================================
def test_run_passes_worker_id_from_settings(mocked_firestore):
    """AGENT_WORKER_ID must reach claim_query — it ends up in claimedBy
    on the Firestore doc, the only audit trail of which worker handled
    which session."""
    _, mock_claim, _ = mocked_firestore
    mock_claim.return_value = _claim_payload()

    with patch.object(claim_session.settings, "AGENT_WORKER_ID", "worker-test-9"):
        claim_session.run({"session_id": "doc1"})

    mock_claim.assert_called_once_with("doc1", "worker-test-9")


# ==========================================================
# 4. Payload field propagation
# ==========================================================
def test_run_propagates_question_and_user_id_from_payload(mocked_firestore):
    """The claim payload's `question` and `userId` must surface in the
    returned state under the names downstream nodes expect (raw_question,
    user_id). session_id is echoed back so the synthesize wrapper can
    keep using it without re-reading state."""
    _, mock_claim, _ = mocked_firestore
    mock_claim.return_value = _claim_payload(
        session_id="s99",
        question="Will BTC hit 100k by EOY 2026?",
        user_id="user-abc",
    )

    out = claim_session.run({"session_id": "s99"})

    # Sprint 25 T25.6: claim_session also mints + returns run_id (the
    # single-writer ForecastState field); the other three fields are unchanged.
    assert out["session_id"] == "s99"
    assert out["raw_question"] == "Will BTC hit 100k by EOY 2026?"
    assert out["user_id"] == "user-abc"
    assert out["run_id"]  # minted uuid4 hex
    # A1 (2026-07-30): condition_id joins the claim payload. Absent on the queue
    # doc today — the picker selects one but CreateForecastView drops it before
    # submit — so it normalises to "" and downstream falls through to the
    # pg_trgm resolver exactly as before.
    assert set(out) == {
        "session_id", "raw_question", "user_id", "run_id", "condition_id",
    }
    assert out["condition_id"] == ""


# ==========================================================
# 5. Race lost — claim_query returns None
# ==========================================================
def test_run_raises_session_claim_race_lost_when_claim_returns_none(mocked_firestore):
    """`claim_query` returns None when another worker won the race or the
    doc is missing. claim_session must convert that to the typed subclass
    (NOT a generic AgentProcessingError) so the runner's race-lost branch
    can match by type. No 'claimed'/'running' writes happen."""
    _, mock_claim, mock_status = mocked_firestore
    mock_claim.return_value = None

    with pytest.raises(SessionClaimRaceLostError) as exc_info:
        claim_session.run({"session_id": "s1"})

    # Subclass relationship preserved so wider catches still work
    assert isinstance(exc_info.value, AgentProcessingError)
    assert "session not claimable" in str(exc_info.value)
    mock_status.assert_not_called()


# ==========================================================
# 6. Unexpected exception from claim_query is wrapped
# ==========================================================
def test_run_wraps_unexpected_claim_query_exception(mocked_firestore):
    """If claim_query raises something other than AgentProcessingError
    (e.g., a Firestore SDK exception, a network blip), it must be wrapped
    so the runner only ever sees AgentProcessingError on the failure path.
    `from exc` chaining preserves the original traceback for operator
    triage."""
    _, mock_claim, mock_status = mocked_firestore
    mock_claim.side_effect = RuntimeError("connection refused")

    with pytest.raises(AgentProcessingError) as exc_info:
        claim_session.run({"session_id": "s1"})

    assert "claim_query raised" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    # Must NOT be the race-lost subclass — that would mute a real failure
    assert not isinstance(exc_info.value, SessionClaimRaceLostError)
    mock_status.assert_not_called()


# ==========================================================
# 7. AgentProcessingError from claim_query is re-raised, not re-wrapped
# ==========================================================
def test_run_does_not_re_wrap_agent_processing_error_from_claim_query(mocked_firestore):
    """If claim_query itself raises AgentProcessingError (e.g., a future
    pre-flight validation), claim_session must propagate it as-is rather
    than wrapping it again. Re-wrapping would bury the original code/details
    behind a 'claim_query raised' prefix and make telemetry noisier."""
    _, mock_claim, _ = mocked_firestore
    original = AgentProcessingError("upstream validation failed")
    mock_claim.side_effect = original

    with pytest.raises(AgentProcessingError) as exc_info:
        claim_session.run({"session_id": "s1"})

    assert exc_info.value is original
    assert "claim_query raised" not in str(exc_info.value)


# ==========================================================
# 8. update_session_status('claimed') failure is wrapped
# ==========================================================
def test_run_wraps_claimed_status_update_failure(mocked_firestore):
    """If the very first update_session_status('claimed') raises, the
    failure must surface as AgentProcessingError (not whatever the
    Firestore SDK threw). 'running' is never reached. This is the
    behaviour Sprint 18's test_failure_during_first_status_update_marks_failed
    pinned at the runner level — moves here because the call now lives
    in the node."""
    _, mock_claim, mock_status = mocked_firestore
    mock_claim.return_value = _claim_payload()
    mock_status.side_effect = RuntimeError("transient")

    with pytest.raises(AgentProcessingError) as exc_info:
        claim_session.run({"session_id": "s1"})

    assert "status update failed" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    # 'running' transition was never reached
    assert mock_status.call_args_list == [call("s1", "claimed")]


# ==========================================================
# 9. update_session_status('running') failure is wrapped
# ==========================================================
def test_run_wraps_running_status_update_failure(mocked_firestore):
    """If the second update_session_status('running') raises after
    'claimed' succeeded, the failure must still surface as
    AgentProcessingError. Pin both branches because they take different
    paths through the try/except (one call before the failure, two)."""
    _, mock_claim, mock_status = mocked_firestore
    mock_claim.return_value = _claim_payload()
    # 'claimed' succeeds, 'running' raises
    mock_status.side_effect = [None, RuntimeError("running-blip")]

    with pytest.raises(AgentProcessingError) as exc_info:
        claim_session.run({"session_id": "s1"})

    assert "status update failed" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    # Sprint 25 T25.6: 'claimed' then 'running'; the 'running' call carries the
    # minted current_run_id (a uuid4 — not capturable here since run() raised).
    claimed_call, running_call = mock_status.call_args_list
    assert claimed_call == call("s1", "claimed")
    assert running_call.args == ("s1", "running")
    assert set(running_call.kwargs) == {"current_run_id"}
    assert running_call.kwargs["current_run_id"]


# ==========================================================
# 10. G1 fix — resume-on-clarify: claimed doc's sessionId ≠ query_doc_id
# ==========================================================

def test_run_uses_session_id_from_claimed_doc_on_resume(mocked_firestore):
    """G1 fix (Sprint 21): when Express creates a new forecastQueries doc
    with a fresh UUID for resume-on-clarify, that doc's id (query_doc_id)
    differs from the actual session id (stored in claimed["sessionId"]).
    claim_session must write to sessions/{actual_session_id} — NOT
    sessions/{query_doc_id} — and return state["session_id"] = actual_session_id.

    server/src/repositories/session.repository.ts requeueClarifiedSession:318-453
    uses `collectionRef('forecastQueries').doc()` (fresh UUID) as doc id and
    writes sessionId: session.id in the document body.
    """
    manager, mock_claim, _ = mocked_firestore
    # Simulate: forecastQueries/{fresh-uuid} has sessionId pointing to the
    # original session (different from the doc id "fresh-uuid").
    mock_claim.return_value = _claim_payload(
        session_id="original-session-id",  # this is claimed["sessionId"]
        question="Middle East question?",
        user_id="u-abc",
    )

    with patch.object(claim_session.settings, "AGENT_WORKER_ID", "worker-1"):
        # The graph runner passes the fresh forecastQueries doc id as session_id.
        out = claim_session.run({"session_id": "fresh-uuid-doc-id"})

    # claim_query receives the forecastQueries doc id (to claim the right doc).
    assert manager.method_calls[0] == call.claim_query("fresh-uuid-doc-id", "worker-1")
    # Status updates go to the ACTUAL session, not the fresh UUID.
    assert manager.method_calls[1] == call.update_session_status("original-session-id", "claimed")
    # Sprint 25 T25.6: 'running' carries current_run_id (== the returned run_id).
    assert manager.method_calls[2] == call.update_session_status(
        "original-session-id", "running", current_run_id=out["run_id"]
    )
    # Returned state carries the actual session id, not the query doc id.
    assert out["session_id"] == "original-session-id"
    assert out["raw_question"] == "Middle East question?"
    assert out["user_id"] == "u-abc"


def test_run_first_time_query_session_id_unchanged(mocked_firestore):
    """G1 fix backward-compat: for first-time queries where sessionId ==
    query_doc_id, behaviour is identical to before the fix."""
    _, mock_claim, _ = mocked_firestore
    # First-time: forecastQueries/{session_id} — doc id == session id.
    mock_claim.return_value = _claim_payload(session_id="s42")

    with patch.object(claim_session.settings, "AGENT_WORKER_ID", "worker-1"):
        out = claim_session.run({"session_id": "s42"})

    assert out["session_id"] == "s42"
