"""
Gate 2 runner tests for `agent.process_query` (T19.11).

T19.11 replaced the Sprint 18 procedural flow with a thin graph runner.
Most of the work that the old runner orchestrated (claim_query call,
claimed/running status writes, question propagation, worker_id passing)
now lives inside `agent.nodes.claim_session` and is covered by
`test_claim_session.py` (T19.12). This file is intentionally smaller than
its Sprint 18 predecessor — only the post-graph orchestration (write,
done transitions, race-loss handling, cleanup) remains here.

Old → new test mapping (Sprint 18 → T19.11):
    test_happy_path_calls_in_order                  → test_happy_path_invokes_graph_then_writes
    test_happy_path_propagates_session_id_and_question
                                                    → test_happy_path_passes_session_id_to_graph
                                                      (question-propagation moves to test_claim_session)
    test_happy_path_stub_result_shape               → COLLAPSED into test_synthesize.py (T19.10) +
                                                      test_happy_path_writes_synthesis_result_from_graph
    test_race_lost_no_further_calls                 → test_race_lost_silent_return
                                                      (now via SessionClaimRaceLostError, not None)
    test_failure_during_write_marks_failed_and_reraises
                                                    → test_post_graph_write_failure_marks_failed
    test_failure_during_first_status_update_marks_failed
                                                    → MOVED to test_claim_session.py (T19.12)
                                                      (replaced here by test_graph_failure_marks_failed_and_reraises
                                                       which covers generic mid-graph failure)
    test_mark_failed_swallows_cleanup_exceptions    → KEPT (adapted to new flow)
    test_worker_id_from_settings_passed_to_claim_query
                                                    → MOVED to test_claim_session.py (T19.12)
                                                      (runner no longer calls claim_query directly)

Mocking strategy:
    Patch `graph` (the compiled singleton) and the three Firestore
    functions AS IMPORTED INTO `agent.process_query` (per unittest.mock's
    "patch where it's looked up" rule). The runner uses `from agent.graph
    import graph` and `from agent.firestore_client import ...`, so
    patching the source modules silently fails to intercept.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.3.2 (Graph Topology)
    - data-pipeline/docs/agentic_hub_spec.md §8.7.2 (SessionResult schema)
    - data-pipeline/docs/agentic_hub_spec.md §8.8.1 (worker pattern)
"""

from unittest.mock import ANY, MagicMock, call, patch

import pytest

from agent.errors import AgentProcessingError, SessionClaimRaceLostError
from agent.process_query import process_query


# ==========================================================
# Shared fixture — patches graph + 3 firestore functions where the runner
# looks them up. Returns a manager mock whose .method_calls gives a single
# ordered list of cross-mock calls (canonical pattern for cross-mock
# ordering assertions).
# ==========================================================

@pytest.fixture
def mocked_runner():
    """Yields (manager, graph_mock, write_mock, session_mock, query_mock)."""
    with (
        patch("agent.process_query.graph") as mock_graph,
        patch("agent.process_query.write_session_result") as mock_write,
        patch("agent.process_query.update_session_status") as mock_session,
        patch("agent.process_query.update_query_status") as mock_query,
    ):
        manager = MagicMock()
        manager.attach_mock(mock_graph, "graph")
        manager.attach_mock(mock_write, "write_session_result")
        manager.attach_mock(mock_session, "update_session_status")
        manager.attach_mock(mock_query, "update_query_status")
        yield manager, mock_graph, mock_write, mock_session, mock_query


def _final_state(session_id="s1", synthesis_result=None) -> dict:
    """Minimal final-state dict the graph returns to the runner."""
    if synthesis_result is None:
        synthesis_result = {"finalProbability": 0.5, "tier": "tier_1"}
    return {
        "session_id": session_id,
        "raw_question": "Q?",
        "synthesis_result": synthesis_result,
    }


# ==========================================================
# 1. Happy path — graph then post-graph writes in order
# ==========================================================

def test_happy_path_invokes_graph_then_writes(mocked_runner):
    """The runner must invoke the graph first, then issue the three
    post-graph writes (write_session_result → session 'done' → query
    'done') in that exact order."""
    manager, mock_graph, _, _, _ = mocked_runner
    mock_graph.invoke.return_value = _final_state(session_id="s1")

    process_query("doc1")

    assert manager.method_calls == [
        call.graph.invoke({"session_id": "doc1"}),
        call.write_session_result("s1", ANY),
        call.update_session_status("s1", "done"),
        call.update_query_status("doc1", "done"),
    ]


# ==========================================================
# 2. Happy path — session_id reaches graph as initial state
# ==========================================================

def test_happy_path_passes_session_id_to_graph(mocked_runner):
    """The query_doc_id must be threaded into the graph's initial state
    as `session_id` (the input field claim_session reads). The session_id
    that the runner uses for post-graph writes comes back from the graph,
    not from the input — pin both ends."""
    _, mock_graph, mock_write, mock_session, mock_query = mocked_runner
    mock_graph.invoke.return_value = _final_state(session_id="s99")

    process_query("doc-arbitrary-id")

    mock_graph.invoke.assert_called_once_with({"session_id": "doc-arbitrary-id"})
    # Runner writes use the session_id returned by the graph
    assert mock_write.call_args[0][0] == "s99"
    mock_session.assert_called_once_with("s99", "done")
    # Queue-side update uses the original doc id (forecastQueries collection key)
    mock_query.assert_called_once_with("doc-arbitrary-id", "done")


# ==========================================================
# 3. Happy path — synthesis_result from graph state is what gets written
# ==========================================================

def test_happy_path_writes_synthesis_result_from_graph(mocked_runner):
    """The dict written to sessionResults/{id} must be the synthesis_result
    the graph produced, byte-for-byte. Field-level §8.7.2 shape is covered
    by test_synthesize.py (T19.10) — pinning that here would duplicate
    that coverage and tightly couple this file to synth's stub copy."""
    _, mock_graph, mock_write, _, _ = mocked_runner
    expected_result = {
        "finalProbability": 0.42,
        "tier": "tier_1",
        "summaryMarkdown": "graph-produced text",
    }
    mock_graph.invoke.return_value = _final_state(synthesis_result=expected_result)

    process_query("doc1")

    assert mock_write.call_args[0][1] is expected_result


# ==========================================================
# 4. Race lost — SessionClaimRaceLostError → quiet no-op
# ==========================================================

def test_race_lost_silent_return(mocked_runner):
    """When claim_session raises SessionClaimRaceLostError (the typed
    subclass introduced in T19.11), the runner returns None and issues
    NO Firestore writes — neither result nor 'done' nor 'failed'. The
    sibling worker that won the claim is responsible for the lifecycle."""
    _, mock_graph, mock_write, mock_session, mock_query = mocked_runner
    mock_graph.invoke.side_effect = SessionClaimRaceLostError(
        "claim_session: session not claimable session_id=doc1"
    )

    result = process_query("doc1")

    assert result is None
    mock_write.assert_not_called()
    mock_session.assert_not_called()
    mock_query.assert_not_called()


# ==========================================================
# 5. Failure during post-graph write — _mark_failed runs, exception re-raised
# ==========================================================

def test_post_graph_write_failure_marks_failed(mocked_runner):
    """If write_session_result raises after the graph completed,
    _mark_failed must transition both docs to 'failed' (using the
    session_id from graph state) and the original exception must
    propagate so the worker logs it."""
    _, mock_graph, mock_write, mock_session, mock_query = mocked_runner
    mock_graph.invoke.return_value = _final_state(session_id="s1")
    mock_write.side_effect = RuntimeError("write boom")

    with pytest.raises(RuntimeError, match="write boom"):
        process_query("doc1")

    mock_session.assert_any_call(
        "s1", "failed",
        error_code="AGENT_PROCESSING_ERROR",
        error_message="write boom",
    )
    mock_query.assert_any_call("doc1", "failed", error_message="write boom")
    # 'done' was never reached
    assert call("s1", "done") not in mock_session.call_args_list
    assert call("doc1", "done") not in mock_query.call_args_list


# ==========================================================
# 6. Failure inside the graph — _mark_failed runs, exception re-raised
# ==========================================================

def test_graph_failure_marks_failed_and_reraises(mocked_runner):
    """If graph.invoke raises a generic AgentProcessingError (e.g., a
    mid-graph node failed), the runner has no session_id from the graph
    state to pass to _mark_failed. By server contract session_id ==
    query_doc_id, so the runner uses query_doc_id for both. Both docs get
    'failed' writes and the original exception propagates."""
    _, mock_graph, mock_write, mock_session, mock_query = mocked_runner
    mock_graph.invoke.side_effect = AgentProcessingError(
        "synthesize: missing raw_question in state"
    )

    with pytest.raises(AgentProcessingError, match="missing raw_question"):
        process_query("doc1")

    # Both cleanup writes use query_doc_id (== session_id by server contract)
    mock_session.assert_any_call(
        "doc1", "failed",
        error_code="AGENT_PROCESSING_ERROR",
        error_message=ANY,
    )
    mock_query.assert_any_call("doc1", "failed", error_message=ANY)
    # No happy-path writes happened
    mock_write.assert_not_called()


# ==========================================================
# 7. Cleanup exception swallowing — original error wins
# ==========================================================

def test_mark_failed_swallows_cleanup_exceptions(mocked_runner):
    """If _mark_failed's own session-update raises, that secondary error
    must be swallowed (logged) so the ORIGINAL exception is what the
    worker sees. _mark_failed has independent try/except blocks per
    cleanup step, so the queue-side 'failed' write must still run even
    after the session-side cleanup raised."""
    _, mock_graph, mock_write, mock_session, mock_query = mocked_runner
    mock_graph.invoke.return_value = _final_state(session_id="s1")
    mock_write.side_effect = RuntimeError("primary")
    # First call (cleanup 'failed') raises
    mock_session.side_effect = RuntimeError("cleanup-error")

    with pytest.raises(RuntimeError, match="primary"):
        process_query("doc1")

    # Queue-side cleanup runs despite session-side cleanup raising
    mock_query.assert_any_call("doc1", "failed", error_message="primary")


# ==========================================================
# 8. Non-Exception raises (BaseException) propagate without cleanup
# ==========================================================

def test_keyboard_interrupt_propagates_without_marking_failed(mocked_runner):
    """KeyboardInterrupt / SystemExit are BaseException, not Exception —
    they should NOT be caught by the runner's broad `except Exception`.
    Pin this so a future edit to `except BaseException` doesn't mask a
    Ctrl-C with cleanup writes that delay shutdown."""
    _, mock_graph, mock_write, mock_session, mock_query = mocked_runner
    mock_graph.invoke.side_effect = KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        process_query("doc1")

    mock_write.assert_not_called()
    mock_session.assert_not_called()
    mock_query.assert_not_called()
