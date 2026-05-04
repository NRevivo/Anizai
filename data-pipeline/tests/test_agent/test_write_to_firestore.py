"""
Gate 1 tests for `agent/nodes/write_to_firestore.py` (Sprint 20 T20.5).

Strategy: pure-mock at the firestore_client function boundary. The
node never touches Firestore directly — every write goes through a
named helper in agent.firestore_client, so patching those is
sufficient.

Coverage:
- write order: subcollections → sessionResults → status='done'
- session.status='done' fires AFTER subcollections + sessionResults
- frontend `type` field added to evidence dicts via SOURCE_TYPE mapping
- evidence with unknown source_type is dropped (defensive)
- empty evidence_trail still issues the (zero-doc) batch + status writes
- predictionSeries / sentimentTimeSeries are empty lists for Sprint 20
- raw_question NOT required (synthesize already produced result)
- missing session_id → AgentProcessingError
- missing synthesis_result → AgentProcessingError
- mid-batch failure (write_evidence_batch raises) → propagates to runner
- session_id == query_doc_id contract (one id used for both writes)
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from agent.errors import AgentProcessingError
from agent.nodes import write_to_firestore


# ==========================================================
# Helpers
# ==========================================================
def _evidence_item(
    *,
    evidence_id: str = "ev-1",
    source_type: str = "vault_news",
) -> dict:
    """Minimal post-synthesis EvidenceItem dict shape."""
    return {
        "evidence_id": evidence_id,
        "source_type": source_type,
        "origin": "knowledge_vault",
        "title": "Title",
        "snippet": "snippet",
        "url": None,
        "source_domain": "reuters.com",
        "published_at": None,
        "fetched_at": None,
        "relevance_score": 0.7,
        "credibility_tier": "tier_1",
        "recency_weight": 0.9,
        "used_in_answer": True,
        "impact_on_forecast": "increases",
        "impact_magnitude": 0.6,
        "is_key_evidence": True,
        "rank": 1,
        "justification": "ok",
    }


def _state(**overrides) -> dict:
    base = {
        "session_id": "s1",
        "synthesis_result": {"finalProbability": 0.7, "tier": "tier_1"},
    }
    base.update(overrides)
    return base


# ==========================================================
# Shared fixture — patches every firestore_client call site at
# `agent.firestore_client.<name>` (where the node looks them up
# via the module import).
# ==========================================================
@pytest.fixture
def mocked_firestore():
    """Yields a manager mock + per-function child mocks. The manager's
    .method_calls list gives a single ordered cross-mock call sequence
    so tests can assert write order across multiple firestore helpers."""
    with (
        patch("agent.nodes.write_to_firestore.firestore_client.write_evidence_batch") as ev,
        patch("agent.nodes.write_to_firestore.firestore_client.write_prediction_series") as ps,
        patch("agent.nodes.write_to_firestore.firestore_client.write_sentiment_time_series") as sts,
        patch("agent.nodes.write_to_firestore.firestore_client.write_session_result") as sr,
        patch("agent.nodes.write_to_firestore.firestore_client.update_session_status") as ss,
        patch("agent.nodes.write_to_firestore.firestore_client.update_query_status") as qs,
    ):
        # Default returns: helpers report counts of items written
        ev.return_value = 0
        ps.return_value = 0
        sts.return_value = 0

        manager = MagicMock()
        manager.attach_mock(ev, "evidence")
        manager.attach_mock(ps, "prediction_series")
        manager.attach_mock(sts, "sentiment_time_series")
        manager.attach_mock(sr, "session_result")
        manager.attach_mock(ss, "session_status")
        manager.attach_mock(qs, "query_status")
        yield manager, ev, ps, sts, sr, ss, qs


# ==========================================================
# Happy path — write order
# ==========================================================
def test_writes_subcollections_before_session_result_before_done(mocked_firestore):
    """frontend-integration skill: status='done' is the frontend's
    'render now' signal. Subcollections must land first, then
    sessionResults, then status='done'. Pin the order."""
    manager, *_ = mocked_firestore
    write_to_firestore.run(_state(evidence_trail=[_evidence_item()]))

    # Expected order: evidence → predictionSeries → sentimentTimeSeries
    # → sessionResults → session.status=done → forecastQueries.status=done
    assert manager.method_calls == [
        call.evidence("s1", [{**_evidence_item(), "type": "news"}]),
        call.prediction_series("s1", []),
        call.sentiment_time_series("s1", []),
        call.session_result("s1", {"finalProbability": 0.7, "tier": "tier_1"}),
        call.session_status("s1", "done"),
        call.query_status("s1", "done"),
    ]


def test_returns_identity_echo_of_errors_field(mocked_firestore):
    """write_to_firestore is conceptually a sink (its product is the
    Firestore writes), but LangGraph requires every node to write at
    least one state field. The node echoes `errors` as an identity
    write — satisfies the framework without mutating state."""
    out = write_to_firestore.run(_state(evidence_trail=[_evidence_item()]))
    # Only the errors field is written; it's an identity copy of state.errors
    assert out == {"errors": []}


def test_identity_echo_preserves_existing_errors(mocked_firestore):
    """If state.errors has accumulated entries from upstream nodes, the
    echo must preserve them (not clobber to empty)."""
    out = write_to_firestore.run(_state(
        evidence_trail=[_evidence_item()],
        errors=["upstream warning"],
    ))
    assert out == {"errors": ["upstream warning"]}


# ==========================================================
# Frontend `type` mapping
# ==========================================================
@pytest.mark.parametrize("source_type,expected_type", [
    ("vault_news", "news"),
    ("vault_telegram", "social"),
    ("vault_market", "market"),
    ("vault_arxiv", "expert"),
    ("vault_hackernews", "social"),
    ("vault_fred", "market"),
])
def test_frontend_type_added_per_source_type(mocked_firestore, source_type, expected_type):
    """Each EvidenceItem's source_type is mapped to the frontend's
    filter-tab vocab via SOURCE_TYPE_TO_FRONTEND_TYPE. Pin every value."""
    _, ev, *_ = mocked_firestore
    item = _evidence_item(source_type=source_type)
    write_to_firestore.run(_state(evidence_trail=[item]))

    written = ev.call_args.args[1]
    assert written[0]["type"] == expected_type
    # Original fields preserved
    assert written[0]["evidence_id"] == item["evidence_id"]


def test_unknown_source_type_dropped_with_warning(mocked_firestore, caplog):
    """A source_type that's not in the mapping → log + drop the item.
    Avoids writing a doc with no `type` field that breaks filter UI."""
    _, ev, *_ = mocked_firestore
    bad_item = _evidence_item(source_type="vault_unknown_kind")
    good_item = _evidence_item(evidence_id="ev-good", source_type="vault_news")

    write_to_firestore.run(_state(evidence_trail=[bad_item, good_item]))

    # Only the good item lands in the batch
    written = ev.call_args.args[1]
    assert len(written) == 1
    assert written[0]["evidence_id"] == "ev-good"


# ==========================================================
# Sprint 20 empty subcollections
# ==========================================================
def test_prediction_series_is_empty_for_sprint_20(mocked_firestore):
    """KG-PHASE8-12: no polymarket data → no time series → write [].
    Frontend renders empty state for PredictionOverview BI card."""
    _, _, ps, *_ = mocked_firestore
    write_to_firestore.run(_state())
    assert ps.call_args == call("s1", [])


def test_sentiment_time_series_is_empty_for_sprint_20(mocked_firestore):
    """Q5 default: empty for Sprint 20. Sprint 22+ may add points."""
    _, _, _, sts, *_ = mocked_firestore
    write_to_firestore.run(_state())
    assert sts.call_args == call("s1", [])


def test_empty_evidence_trail_still_runs_full_write_sequence(mocked_firestore):
    """Cold-start: vault returned nothing → evidence_trail empty.
    The node still issues all 6 helper calls (with empty lists) so
    the session lifecycle completes correctly."""
    manager, *_ = mocked_firestore
    write_to_firestore.run(_state(evidence_trail=[]))

    method_names = [c[0] for c in manager.method_calls]
    assert method_names == [
        "evidence", "prediction_series", "sentiment_time_series",
        "session_result", "session_status", "query_status",
    ]


# ==========================================================
# session_id usage
# ==========================================================
def test_session_id_used_for_both_session_and_query_status(mocked_firestore):
    """Per server contract (session.repository.ts:347), session_id ==
    query_doc_id. write_to_firestore uses state.session_id for BOTH
    sessions/{id} and forecastQueries/{id} writes — pin this."""
    _, _, _, _, _, ss, qs = mocked_firestore
    write_to_firestore.run(_state(session_id="abc-123"))

    ss.assert_called_once_with("abc-123", "done")
    qs.assert_called_once_with("abc-123", "done")


def test_session_id_used_for_subcollection_paths(mocked_firestore):
    """All three subcollection writes are scoped to the same session_id."""
    _, ev, ps, sts, *_ = mocked_firestore
    write_to_firestore.run(_state(session_id="xyz-789", evidence_trail=[_evidence_item()]))

    assert ev.call_args.args[0] == "xyz-789"
    assert ps.call_args.args[0] == "xyz-789"
    assert sts.call_args.args[0] == "xyz-789"


# ==========================================================
# Input validation
# ==========================================================
def test_missing_session_id_raises(mocked_firestore):
    _, *firestore_mocks = mocked_firestore
    with pytest.raises(AgentProcessingError, match="session_id"):
        write_to_firestore.run({"synthesis_result": {"finalProbability": 0.5}})

    # No firestore writes attempted on validation failure
    for mock in firestore_mocks:
        mock.assert_not_called()


def test_missing_synthesis_result_raises(mocked_firestore):
    _, *firestore_mocks = mocked_firestore
    with pytest.raises(AgentProcessingError, match="synthesis_result"):
        write_to_firestore.run({"session_id": "s1"})

    for mock in firestore_mocks:
        mock.assert_not_called()


def test_empty_synthesis_result_dict_raises(mocked_firestore):
    """A dict that exists but is empty (falsy) is treated the same as
    missing — synthesize should never produce an empty result."""
    _, *firestore_mocks = mocked_firestore
    with pytest.raises(AgentProcessingError, match="synthesis_result"):
        write_to_firestore.run({"session_id": "s1", "synthesis_result": {}})


# ==========================================================
# Mid-batch failure — propagates to runner
# ==========================================================
def test_evidence_batch_failure_propagates(mocked_firestore):
    """Per the user's added Gate 1 requirement: write_to_firestore
    raising mid-batch must propagate so process_query._mark_failed
    can clean up. Pin that the failure short-circuits — later writes
    don't happen."""
    _, ev, ps, sts, sr, ss, qs = mocked_firestore
    ev.side_effect = RuntimeError("firestore unavailable")

    with pytest.raises(RuntimeError, match="firestore unavailable"):
        write_to_firestore.run(_state(evidence_trail=[_evidence_item()]))

    # Subsequent writes never fire
    ps.assert_not_called()
    sts.assert_not_called()
    sr.assert_not_called()
    ss.assert_not_called()
    qs.assert_not_called()


def test_session_result_failure_propagates_with_no_done_status(mocked_firestore):
    """If sessionResults write fails, the status='done' transitions
    must NOT happen — frontend would render 'done' against an absent
    sessionResults doc otherwise."""
    _, _, _, _, sr, ss, qs = mocked_firestore
    sr.side_effect = RuntimeError("write failed")

    with pytest.raises(RuntimeError, match="write failed"):
        write_to_firestore.run(_state(evidence_trail=[_evidence_item()]))

    ss.assert_not_called()
    qs.assert_not_called()


def test_session_status_failure_short_circuits_query_status(mocked_firestore):
    """If session.status='done' write fails, forecastQueries 'done'
    write must NOT happen. Both docs landing in inconsistent state is
    cleaner than one done + one failed-mid-status."""
    _, _, _, _, _, ss, qs = mocked_firestore
    ss.side_effect = RuntimeError("status flip failed")

    with pytest.raises(RuntimeError, match="status flip failed"):
        write_to_firestore.run(_state(evidence_trail=[_evidence_item()]))

    qs.assert_not_called()
