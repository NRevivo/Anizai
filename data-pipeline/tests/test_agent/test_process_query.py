"""
Gate 2 runner tests for `agent.process_query` (Sprint 20 T20.7).

T20.7 collapsed the post-graph success-path writes (write_session_result,
session 'done', query 'done') into the new write_to_firestore node
(T20.5). The runner is now purely a lifecycle wrapper: invoke graph,
catch SessionClaimRaceLostError quietly, mark-failed any other
exception, re-raise.

Sprint 19 → Sprint 20 test mapping:
    test_happy_path_invokes_graph_then_writes
        → test_happy_path_only_invokes_graph
          (the writes moved into Node 7; runner doesn't see them)
    test_happy_path_passes_session_id_to_graph
        → KEPT (session_id threading is still the runner's job)
    test_happy_path_writes_synthesis_result_from_graph
        → REMOVED (write_session_result is no longer called by runner)
    test_race_lost_silent_return
        → KEPT (SessionClaimRaceLostError handling unchanged)
    test_post_graph_write_failure_marks_failed
        → REWRITTEN as test_graph_write_to_firestore_failure_marks_failed
          (failures from inside the graph propagate; the user's added
          Gate 1 requirement "_mark_failed handles cleanup correctly
          when write_to_firestore raises mid-batch" is the headline test
          here — exercises the failure mode introduced by D9)
    test_graph_failure_marks_failed_and_reraises
        → KEPT (mid-graph failures still propagate)
    test_mark_failed_swallows_cleanup_exceptions
        → KEPT (cleanup-on-cleanup-error semantics unchanged)
    test_keyboard_interrupt_propagates_without_marking_failed
        → KEPT (BaseException not caught)

Mocking strategy:
    Patch `graph` and the two surviving Firestore functions
    (update_session_status, update_query_status — for _mark_failed)
    AS IMPORTED INTO `agent.process_query`. write_session_result is
    no longer imported by process_query — its tests live in
    test_write_to_firestore.py now.

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
# Shared fixture — patches graph + the two firestore functions used
# by _mark_failed cleanup. Returns a manager mock whose .method_calls
# gives a single ordered cross-mock call sequence.
# ==========================================================

@pytest.fixture
def mocked_runner():
    """Yields (manager, graph_mock, session_mock, query_mock).

    Sprint 20: write_session_result is no longer in the runner's
    import surface — those writes happen inside the graph (Node 7).

    Sprint 21 added `_build_initial_state`, which calls
    `get_query_doc(query_doc_id)` for resume-on-clarify detection BEFORE
    `graph.invoke`. That call is real Firestore (firestore.client() →
    ADC) and would crash these unit tests on a machine with no Google
    credentials (Sprint 23.5 Disposition A). Patch it here so the runner
    tests are fully self-contained — no Firestore, no emulator. The
    default return value is `None`, which routes `_build_initial_state`
    down the first-time-query branch (`{"session_id": query_doc_id}`) —
    exactly the initial state every test below asserts.
    """
    with (
        patch("agent.process_query.graph") as mock_graph,
        patch("agent.process_query.get_query_doc", return_value=None) as mock_get_query_doc,
        # Sprint 26 T26.11: _mark_failed's no-downgrade guard reads session status
        # via get_session_doc. Patch it (default None → not 'done' → cleanup
        # proceeds) so failure-path tests stay self-contained (no real Firestore).
        patch("agent.process_query.get_session_doc", return_value=None) as mock_get_session_doc,
        patch("agent.process_query.update_session_status") as mock_session,
        patch("agent.process_query.update_query_status") as mock_query,
    ):
        manager = MagicMock()
        manager.attach_mock(mock_graph, "graph")
        manager.attach_mock(mock_session, "update_session_status")
        manager.attach_mock(mock_query, "update_query_status")
        # get_query_doc is intentionally NOT attached to the manager — it
        # runs in _build_initial_state, before the runner's tracked
        # lifecycle calls, so keeping it off manager.method_calls preserves
        # the exact ordered call sequence the happy-path test pins.
        _ = mock_get_query_doc
        _ = mock_get_session_doc
        yield manager, mock_graph, mock_session, mock_query


# ==========================================================
# 1. Happy path — graph is invoked; runner does no writes
# ==========================================================
def test_happy_path_only_invokes_graph(mocked_runner):
    """Sprint 20: success path is graph.invoke and exit. No
    write_session_result, no update_session_status('done'), no
    update_query_status('done') from the runner — those happen inside
    Node 7 (write_to_firestore)."""
    manager, mock_graph, mock_session, mock_query = mocked_runner
    # Sprint 25 T25.13: the runner streams (stream_mode="values") so it can read
    # run_id off the accumulated state before a raise. The happy path yields the
    # final state and does no writes (Node 7 handles persistence).
    mock_graph.stream.return_value = [{"session_id": "doc1"}]

    process_query("doc1")

    assert manager.method_calls == [
        call.graph.stream(
            {"session_id": "doc1", "query_doc_id": "doc1"}, stream_mode="values",
        ),
    ]
    mock_session.assert_not_called()
    mock_query.assert_not_called()


# ==========================================================
# 2. Happy path — session_id reaches graph as initial state
# ==========================================================
def test_happy_path_passes_session_id_to_graph(mocked_runner):
    """The query_doc_id must be threaded into the graph's initial state
    as `session_id` (the input field claim_session reads). Pin the
    initial-state shape — claim_session contract."""
    _, mock_graph, _, _ = mocked_runner
    mock_graph.stream.return_value = [{"session_id": "s99"}]

    process_query("doc-arbitrary-id")

    mock_graph.stream.assert_called_once_with(
        {"session_id": "doc-arbitrary-id", "query_doc_id": "doc-arbitrary-id"},
        stream_mode="values",
    )


# ==========================================================
# 3. Race lost — SessionClaimRaceLostError → quiet no-op
# ==========================================================
def test_race_lost_silent_return(mocked_runner):
    """When claim_session raises SessionClaimRaceLostError, the runner
    returns None and issues NO Firestore writes — neither result nor
    'done' nor 'failed'. The sibling worker that won the claim is
    responsible for the lifecycle."""
    _, mock_graph, mock_session, mock_query = mocked_runner
    mock_graph.stream.side_effect = SessionClaimRaceLostError(
        "claim_session: session not claimable session_id=doc1"
    )

    result = process_query("doc1")

    assert result is None
    mock_session.assert_not_called()
    mock_query.assert_not_called()


# ==========================================================
# 4. Generic mid-graph failure → _mark_failed runs, exception re-raised
# ==========================================================
def test_graph_failure_marks_failed_and_reraises(mocked_runner):
    """If graph.invoke raises a generic AgentProcessingError (e.g., a
    mid-graph node failed), the runner has no session_id from the
    graph state to pass to _mark_failed. By server contract session_id
    == query_doc_id, so the runner uses query_doc_id for both. Both
    docs get 'failed' writes and the original exception propagates."""
    _, mock_graph, mock_session, mock_query = mocked_runner
    mock_graph.stream.side_effect = AgentProcessingError(
        "synthesize: missing raw_question in state"
    )

    with pytest.raises(AgentProcessingError, match="missing raw_question"):
        process_query("doc1")

    mock_session.assert_any_call(
        "doc1", "failed",
        error_code="AGENT_PROCESSING_ERROR",
        error_message=ANY,
    )
    mock_query.assert_any_call("doc1", "failed", error_message=ANY)


# ==========================================================
# 5. write_to_firestore mid-batch failure → _mark_failed cleanup
# ==========================================================
def test_write_to_firestore_failure_marks_failed_and_reraises(mocked_runner):
    """User's Sprint 20 added requirement: 'Add an explicit Gate 1 test
    that write_to_firestore raises mid-batch → process_query._mark_failed
    handles cleanup correctly.'

    Even though write_to_firestore is now inside the graph, an
    exception escaping it bubbles through graph.invoke and lands in
    the runner's broad `except Exception` clause — same path as any
    other mid-graph failure. Pin that:
      - the exception propagates (worker logs it)
      - both docs flip to 'failed' (frontend sees consistent failure
        state, no half-done renders)
      - 'done' transitions never happen"""
    _, mock_graph, mock_session, mock_query = mocked_runner
    # Simulate a Firestore batch commit failure escaping Node 7
    mock_graph.stream.side_effect = RuntimeError("firestore batch commit failed")

    with pytest.raises(RuntimeError, match="firestore batch commit failed"):
        process_query("doc1")

    # Cleanup: both docs flipped to failed
    mock_session.assert_any_call(
        "doc1", "failed",
        error_code="AGENT_PROCESSING_ERROR",
        error_message="firestore batch commit failed",
    )
    mock_query.assert_any_call(
        "doc1", "failed", error_message="firestore batch commit failed",
    )
    # 'done' was never reached (write_to_firestore would have set it
    # but raised before doing so; runner doesn't add a 'done' write)
    assert call("doc1", "done") not in mock_session.call_args_list
    assert call("doc1", "done") not in mock_query.call_args_list


# ==========================================================
# 6. Cleanup-on-cleanup-error — original error wins
# ==========================================================
def test_mark_failed_swallows_cleanup_exceptions(mocked_runner):
    """If _mark_failed's own session-update raises, that secondary
    error must be swallowed (logged) so the ORIGINAL exception is what
    the worker sees. _mark_failed has independent try/except blocks
    per cleanup step, so the queue-side 'failed' write must still run
    even after the session-side cleanup raised."""
    _, mock_graph, mock_session, mock_query = mocked_runner
    mock_graph.stream.side_effect = RuntimeError("primary")
    # First call (cleanup 'failed') raises
    mock_session.side_effect = RuntimeError("cleanup-error")

    with pytest.raises(RuntimeError, match="primary"):
        process_query("doc1")

    # Queue-side cleanup runs despite session-side cleanup raising
    mock_query.assert_any_call("doc1", "failed", error_message="primary")


# ==========================================================
# 7. Non-Exception raises (BaseException) propagate without cleanup
# ==========================================================
def test_keyboard_interrupt_propagates_without_marking_failed(mocked_runner):
    """KeyboardInterrupt / SystemExit are BaseException, not Exception
    — they should NOT be caught by the runner's broad `except
    Exception`. Pin this so a future edit to `except BaseException`
    doesn't mask a Ctrl-C with cleanup writes that delay shutdown."""
    _, mock_graph, mock_session, mock_query = mocked_runner
    mock_graph.stream.side_effect = KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        process_query("doc1")

    mock_session.assert_not_called()
    mock_query.assert_not_called()


# ==========================================================
# 8. Sprint 25 T25.13 — fail_event fires with the CAPTURED run_id
# ==========================================================
def test_failure_calls_fail_event_with_captured_run_id(mocked_runner):
    """Sprint 25 T25.13: on a mid-graph failure the runner must call
    events.fail_event with the run_id CAPTURED from the streamed state (the
    exact-run path in events._resolve_for_failure), not None. This is the whole
    reason process_query streams instead of invokes — pin it so the invoke→stream
    swap can't quietly lose run_id capture (which would demote every failure to
    the session_id fallback, or a no-op if session lookup ever changed)."""
    _, mock_graph, _, _ = mocked_runner

    def _stream_then_raise(*_a, **_k):
        yield {"session_id": "doc1"}                       # claim_session output
        yield {"session_id": "doc1", "run_id": "run-xyz"}  # after run_id minted
        raise RuntimeError("boom mid-graph")

    mock_graph.stream.side_effect = _stream_then_raise

    with (
        patch("agent.events.fail_event") as mock_fail,
        patch("agent.events.drain"),
        patch("agent.events.dispose_run"),
    ):
        with pytest.raises(RuntimeError, match="boom mid-graph"):
            process_query("doc1")

    # Captured run_id (exact-run path) + session_id backup — NOT run_id=None.
    mock_fail.assert_called_once()
    assert mock_fail.call_args.kwargs["run_id"] == "run-xyz"
    assert mock_fail.call_args.kwargs["session_id"] == "doc1"
