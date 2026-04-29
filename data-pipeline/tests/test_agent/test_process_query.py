"""
Gate 2 subgraph test for agent.process_query (Sprint 18 T11).

Exercises the full process_query() orchestration with the firestore_client
boundary mocked. No LangGraph yet — Sprint 18 process_query is a linear
sequence of Firestore calls. These tests pin:

    - exact call ordering across all 4 firestore_client functions
    - propagation of sessionId / question from the claim payload
    - stub result shape per §8.7.2 + handoff §5.1
    - race-lost path: no further calls when claim_query returns None
    - failure paths: _mark_failed is called with the right args, and the
      original exception propagates even if cleanup itself raises

Mocking strategy:
    Patch each name AS IMPORTED INTO agent.process_query (not at the
    firestore_client definition site), per unittest.mock's "patch where
    it's looked up" rule. process_query.py uses `from agent.firestore_client
    import claim_query` — that creates a separate binding inside
    agent.process_query, so patching agent.firestore_client.claim_query
    silently fails to intercept the call.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.7.2 (SessionResult)
    - data-pipeline/docs/agentic_hub_spec.md §8.8.1 (worker pattern)
"""

from unittest.mock import ANY, MagicMock, call, patch

import pytest

from agent import firestore_client
from agent.process_query import process_query


# ==========================================================
# Shared fixture — patches all 4 firestore_client functions at the
# location process_query.py looks them up.
# ==========================================================

@pytest.fixture
def mocked_firestore():
    """Yields (manager, claim, session, write, query). The `manager` is a
    Mock with all four child mocks attached so manager.method_calls gives
    a single ordered list of cross-mock calls — the canonical way to
    assert ordering across separately-patched mocks."""
    with (
        patch("agent.process_query.claim_query") as mock_claim,
        patch("agent.process_query.update_session_status") as mock_session,
        patch("agent.process_query.write_session_result") as mock_write,
        patch("agent.process_query.update_query_status") as mock_query,
    ):
        manager = MagicMock()
        manager.attach_mock(mock_claim, "claim_query")
        manager.attach_mock(mock_session, "update_session_status")
        manager.attach_mock(mock_write, "write_session_result")
        manager.attach_mock(mock_query, "update_query_status")
        yield manager, mock_claim, mock_session, mock_write, mock_query


def _claim_payload(session_id="s1", query_id="q1", question="Q?") -> dict:
    """Minimal claim_query return value used across happy-path tests."""
    return {
        "queryId": query_id,
        "sessionId": session_id,
        "userId": "u1",
        "question": question,
        "status": "pending",
        "createdAt": MagicMock(),
        "claimedAt": None,
        "claimedBy": None,
    }


# ==========================================================
# 1. Happy path — exact cross-mock call ordering
# ==========================================================

def test_happy_path_calls_in_order(mocked_firestore):
    """Verify all 6 Firestore calls happen in the contracted sequence:
    claim → claimed → running → write → done(session) → done(query)."""
    manager, mock_claim, _, _, _ = mocked_firestore
    mock_claim.return_value = _claim_payload(session_id="s1")

    with patch("agent.process_query.settings.AGENT_WORKER_ID", "worker-1"):
        process_query("doc1")

    assert manager.method_calls == [
        call.claim_query("doc1", "worker-1"),
        call.update_session_status("s1", "claimed"),
        call.update_session_status("s1", "running"),
        call.write_session_result("s1", ANY),
        call.update_session_status("s1", "done"),
        call.update_query_status("doc1", "done"),
    ]


# ==========================================================
# 2. Happy path — sessionId and question propagation
# ==========================================================

def test_happy_path_propagates_session_id_and_question(mocked_firestore):
    """sessionId from claim payload (NOT the input query_doc_id) must be
    threaded through every status update and the write. The input
    question must surface in the stub's summaryMarkdown."""
    _, mock_claim, mock_session, mock_write, mock_query = mocked_firestore
    mock_claim.return_value = _claim_payload(
        session_id="s99",
        question="Will Bitcoin hit 100k by EOY?",
    )

    process_query("doc-different-from-session")

    # Every status update uses sessionId from the claim, not the doc id
    mock_session.assert_any_call("s99", "claimed")
    mock_session.assert_any_call("s99", "running")
    mock_session.assert_any_call("s99", "done")
    # write uses sessionId
    assert mock_write.call_args[0][0] == "s99"
    # query-side updates use the doc id (forecastQueries collection key)
    mock_query.assert_called_once_with("doc-different-from-session", "done")
    # Question text appears in the stub's summaryMarkdown
    result = mock_write.call_args[0][1]
    assert "Will Bitcoin hit 100k by EOY?" in result["summaryMarkdown"]


# ==========================================================
# 3. Happy path — stub result shape per §8.7.2
# ==========================================================

def test_happy_path_stub_result_shape(mocked_firestore):
    """The dict passed to write_session_result must match the §8.7.2
    contract field-for-field. Frontend renders this directly."""
    _, mock_claim, _, mock_write, _ = mocked_firestore
    mock_claim.return_value = _claim_payload(question="will it rain?")

    process_query("doc1")

    result = mock_write.call_args[0][1]

    # Numerics
    assert result["finalProbability"] == 0.5
    assert result["confidence"] == 0.5

    # Derived labels — confidenceLabel uses Low/Moderate/High via _derive_label
    assert result["confidenceLabel"] == "Moderate"
    assert result["consensusStrength"] == "Weak"
    # evidenceVolumeLabel uses Low/Moderate/High vocabulary per §8.7.2 line 830
    # (same vocabulary as confidenceLabel, distinct from consensusStrength).
    assert result["evidenceVolumeLabel"] == "Low"

    # Market — null + empty triggers Patch 4 "no canonical market" UI state
    assert result["marketProbability"] is None
    assert result["marketComparison"] == []

    # Empty list fields
    assert result["keyFactors"] == []
    assert result["reasoningChain"] == []
    assert result["suggestedActions"] == []

    # whatIDidntFind has the stub-mode caption (length 1, not empty)
    assert isinstance(result["whatIDidntFind"], list)
    assert len(result["whatIDidntFind"]) == 1
    assert "stub" in result["whatIDidntFind"][0].lower()

    # Metadata
    assert result["tier"] == "tier_1"
    assert result["agentVersion"] == "0.1.0-sprint18-stub"
    assert result["generatedAt"] is firestore_client.SERVER_TIMESTAMP

    # Narrative fields — every one must be a non-empty string so the
    # frontend never has to special-case empty values
    for key in (
        "bottomLineAnswer",
        "detailedExplanation",
        "summaryMarkdown",
        "marketComparisonInsight",
        "sentimentAnalysisInsight",
        "evidenceFeedSummary",
    ):
        assert isinstance(result[key], str), f"{key} is not a string"
        assert result[key], f"{key} is empty"


# ==========================================================
# 4. Race lost — claim returns None
# ==========================================================

def test_race_lost_no_further_calls(mocked_firestore):
    """When another worker won the claim, process_query must return
    quietly without touching any other Firestore endpoint."""
    _, mock_claim, mock_session, mock_write, mock_query = mocked_firestore
    mock_claim.return_value = None

    result = process_query("doc1")

    assert result is None
    mock_session.assert_not_called()
    mock_write.assert_not_called()
    mock_query.assert_not_called()


# ==========================================================
# 5. Failure during write — _mark_failed runs, exception re-raised
# ==========================================================

def test_failure_during_write_marks_failed_and_reraises(mocked_firestore):
    """If write_session_result raises mid-flight: both 'failed' cleanup
    writes happen, no 'done' transitions happen, and the original
    exception propagates so the worker can log it."""
    _, mock_claim, mock_session, mock_write, mock_query = mocked_firestore
    mock_claim.return_value = _claim_payload(session_id="s1")
    mock_write.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        process_query("doc1")

    # Cleanup happened with the actual error_code the code writes.
    # NOTE: code at process_query.py:199 uses "STUB_PROCESSING_ERROR".
    # Earlier T5 plan-approval discussion mentioned "hub_error" — divergence
    # flagged in T11 closeout report. Test pinned to actual behavior.
    mock_session.assert_any_call(
        "s1", "failed",
        error_code="STUB_PROCESSING_ERROR",
        error_message="boom",
    )
    mock_query.assert_any_call("doc1", "failed", error_message="boom")

    # Never reached the 'done' transitions
    assert call("s1", "done") not in mock_session.call_args_list
    assert call("doc1", "done") not in mock_query.call_args_list


# ==========================================================
# 6. Failure during first transition — _mark_failed still runs
# ==========================================================

def test_failure_during_first_status_update_marks_failed(mocked_firestore):
    """If the very first update_session_status('claimed') raises, the
    cleanup-path session+query 'failed' writes must still happen.
    Catches a regression where a failure before 'running' skips cleanup."""
    _, mock_claim, mock_session, mock_write, mock_query = mocked_firestore
    mock_claim.return_value = _claim_payload(session_id="s1")
    # Sequence: 1st (claimed) raises; 2nd (cleanup 'failed') succeeds
    mock_session.side_effect = [RuntimeError("transient"), None]

    with pytest.raises(RuntimeError, match="transient"):
        process_query("doc1")

    mock_session.assert_any_call(
        "s1", "failed",
        error_code="STUB_PROCESSING_ERROR",
        error_message="transient",
    )
    mock_query.assert_any_call("doc1", "failed", error_message="transient")
    # write_session_result never reached
    mock_write.assert_not_called()


# ==========================================================
# 7. Cleanup exception swallowing — original error wins
# ==========================================================

def test_mark_failed_swallows_cleanup_exceptions(mocked_firestore):
    """If _mark_failed's own session-update raises, that secondary error
    must be swallowed (logged) — the ORIGINAL exception is what propagates
    so the worker sees the real cause. Also: update_query_status('failed')
    must still run even after session cleanup raised, because _mark_failed
    has independent try/except blocks for each cleanup step."""
    _, mock_claim, mock_session, mock_write, mock_query = mocked_firestore
    mock_claim.return_value = _claim_payload(session_id="s1")
    mock_write.side_effect = RuntimeError("primary")
    # Sequence: 1st (claimed) ok, 2nd (running) ok, 3rd (cleanup 'failed') raises
    mock_session.side_effect = [None, None, RuntimeError("cleanup-error")]

    with pytest.raises(RuntimeError, match="primary"):
        process_query("doc1")

    # query-side cleanup runs despite session cleanup raising
    mock_query.assert_any_call("doc1", "failed", error_message="primary")


# ==========================================================
# 8. Worker ID from settings is passed to claim_query
# ==========================================================

def test_worker_id_from_settings_passed_to_claim_query(mocked_firestore):
    """AGENT_WORKER_ID must reach claim_query — it ends up in claimedBy
    on the Firestore doc, which is the only audit trail of which worker
    handled which session."""
    _, mock_claim, _, _, _ = mocked_firestore
    mock_claim.return_value = _claim_payload(session_id="s1")

    with patch("agent.process_query.settings.AGENT_WORKER_ID", "worker-test-9"):
        process_query("doc1")

    mock_claim.assert_called_once_with("doc1", "worker-test-9")
