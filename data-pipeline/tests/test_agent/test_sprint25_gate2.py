"""
Gate 2 integration tests for Sprint 25 — agentEvents through the full graph
(T25.10).

Runs the compiled LangGraph end-to-end via `graph.stream` (the production
runner path) with every boundary mocked — Firestore, all five LLM clients, the
three retrieval agents, and the reactive-trigger producer — plus a recorder on
`firestore_client.write_agent_event` (the single sink the emitter's background
writer calls). After the run, `events.drain` has flushed, so the recorder holds
the full ordered event stream.

Coverage (plan §9.3 Gate 2 + the 2026-07-15 review asks):
    - the emitted event SEQUENCE: bootstrap `claim_session` first, then each
      main-graph node's start/complete in sequence order;
    - a SINGLE runId across every event of the run;
    - drain completes BEFORE the session flips to 'done' (no event lands after
      the panel-hiding 'done' write);
    - HIGHEST-VALUE: resume-on-clarify at the GRAPH level — when the claimed
      doc's sessionId ≠ the queue-doc id, every event lands under the ORIGINAL
      (resolved) session id, never the queue-doc UUID.

The insufficient-evidence path (empty agent packages) is used so the run
exercises the conditional trigger branch too; the hermetic producer keeps it
broker-free.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent import events, graph as graph_module


# ==========================================================
# Response / payload builders
# ==========================================================
def _claim_payload(session_id: str) -> dict:
    return {
        "queryId": "q1",
        "sessionId": session_id,
        "userId": "u1",
        "question": "Will the Fed cut rates in 2026?",
        "status": "pending",
        "claimedAt": None,
        "claimedBy": None,
    }


def _qu_response() -> SimpleNamespace:
    candidate = {
        "intent": "forecast",
        "domain": "macro",
        "entities": ["Federal Reserve"],
        "polymarket_search_terms": None,
        "has_market_question_intent": True,
        "confidence": 0.95,
        "too_broad": False,
        "rejected": False,
    }
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps({"candidates": [candidate]})))],
        usage=SimpleNamespace(total_tokens=120),
    )


def _emb_response() -> SimpleNamespace:
    return SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.01] * 1536)],
        usage=SimpleNamespace(total_tokens=8),
    )


def _rate_response() -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps({"ratings": []})))],
        usage=SimpleNamespace(total_tokens=50),
    )


def _synth_response() -> SimpleNamespace:
    payload = {
        "final_probability": 0.7,
        "confidence": 0.65,
        "consensus_score": 0.6,
        "bottom_line_answer": "Moderate chance.",
        "detailed_explanation": "Based on signals.",
        "summary_markdown": "## Summary",
        "market_comparison_insight": "No canonical market.",
        "sentiment_analysis_insight": "Mixed.",
        "evidence_feed_summary": "0 items.",
        "key_factors": [],
        "what_i_didnt_find": [],
        "reasoning_chain": [],
        "evidence_overlay": [],
    }
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
        usage=SimpleNamespace(total_tokens=1500),
    )


def _sa_response() -> SimpleNamespace:
    actions = [
        {"label": "Why so confident?", "prompt": "What drives the confidence?"},
        {"label": "Strongest driver", "prompt": "Which evidence mattered most?"},
        {"label": "Compare to the market", "prompt": "How does this compare?"},
    ]
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps({"actions": actions})))],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50, total_tokens=150),
    )


def _client_returning(response) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = response
    return client


def _emb_client() -> MagicMock:
    client = MagicMock()
    client.embeddings.create.return_value = _emb_response()
    return client


def _empty_agent_packages() -> tuple[dict, dict, dict]:
    return (
        {"articles": [], "source_diversity": {}, "recency_range": None, "empty": True},
        {"market_consensus": [], "community_discussion": [], "overall_sentiment": 0.0, "empty": True},
        {"polymarket": None, "linked_sources": [], "fred_anomalies": [], "google_trends": [], "empty": True},
    )


# ==========================================================
# Full-graph environment
# ==========================================================
@contextmanager
def _gate2_graph(*, claimed_session_id: str, input_session_id: str):
    """Run the full graph hermetically and capture the agentEvents stream +
    the interleaved order of event-writes vs session-status writes.

    Yields a namespace: `events` (recorded event-write dicts, post-drain),
    `order` (ordered ("agent_event"|"status", detail) log for drain-before-done
    checks), and `status` (the update_session_status mock).
    """
    event_writes: list[dict] = []
    order_log: list[tuple[str, str]] = []
    lock = threading.Lock()

    def _record_event(session_id, event_id, fields, *, merge=False):
        with lock:
            event_writes.append({
                "session_id": session_id, "event_id": event_id,
                "fields": dict(fields), "merge": merge,
            })
            order_log.append(("agent_event", str(fields.get("status"))))

    def _record_status(session_id, status, **kwargs):
        with lock:
            order_log.append(("status", status))

    researcher_pkg, pulse_pkg, market_pkg = _empty_agent_packages()
    fake_future = MagicMock()
    fake_future.get.return_value = SimpleNamespace(offset=0, partition=0)
    fake_producer = MagicMock()
    fake_producer.send.return_value = fake_future

    events.reset()
    with (
        patch("agent.firestore_client.claim_query",
              return_value=_claim_payload(claimed_session_id)),
        patch("agent.firestore_client.update_session_status",
              side_effect=_record_status) as mock_status,
        patch("agent.firestore_client.update_query_status"),
        patch("agent.firestore_client.write_session_result"),
        patch("agent.firestore_client.write_evidence_batch", return_value=0),
        patch("agent.firestore_client.write_prediction_series", return_value=0),
        patch("agent.firestore_client.write_sentiment_time_series", return_value=0),
        patch("agent.firestore_client.write_agent_event", side_effect=_record_event),
        patch("agent.nodes.query_understand._get_default_client",
              return_value=_client_returning(_qu_response())),
        patch("agent.nodes.build_embedding._get_default_client",
              return_value=_emb_client()),
        patch("agent.nodes.rate_evidence._get_default_client",
              return_value=_client_returning(_rate_response())),
        patch("agent.nodes.synthesize._get_default_client",
              return_value=_client_returning(_synth_response())),
        patch("agent.nodes.generate_suggested_actions._get_default_client",
              return_value=_client_returning(_sa_response())),
        patch("agent.agents.researcher.run", return_value=researcher_pkg),
        patch("agent.agents.pulse_analyst.run", return_value=pulse_pkg),
        patch("agent.agents.market_bridge.run", return_value=market_pkg),
        patch("agent.nodes.trigger_reactive_ingestion._get_producer",
              return_value=fake_producer),
        patch("agent.nodes.trigger_reactive_ingestion._log_attempt"),
    ):
        # Drive the full graph exactly as process_query does (stream_mode=values).
        for _ in graph_module.graph.stream(
            {"session_id": input_session_id}, stream_mode="values"
        ):
            pass
        # Belt-and-suspenders: write_to_firestore already drained pre-'done';
        # drain again so the recorder is guaranteed complete before asserting.
        events.drain(5.0)
        yield SimpleNamespace(
            events=event_writes, order=order_log, status=mock_status,
        )
    events.reset()


# The insufficient-evidence path (empty packages) exercises the trigger branch.
_EXPECTED_EVENT_SEQUENCE = [
    "claim_session",
    "query_understand",
    "build_embedding",
    "vault_query",
    "sufficiency_check",
    "trigger_reactive_ingestion",
    "rate_evidence",
    "synthesize",
    "generate_suggested_actions",
    "write_to_firestore",
]


def _creates(event_writes: list[dict]) -> list[dict]:
    """Start/bootstrap docs (merge=False), sorted by sequence."""
    creates = [w for w in event_writes if not w["merge"]]
    return sorted(creates, key=lambda w: w["fields"]["sequence"])


# ==========================================================
# 1. Event sequence + single runId
# ==========================================================
def test_gate2_event_sequence_and_single_run_id():
    with _gate2_graph(claimed_session_id="doc1", input_session_id="doc1") as env:
        creates = _creates(env.events)

        # Bootstrap first, then each node's start in sequence order.
        assert [c["fields"]["type"] for c in creates] == _EXPECTED_EVENT_SEQUENCE
        # Sequence is 1..N monotonic (stamped at enqueue).
        assert [c["fields"]["sequence"] for c in creates] == list(
            range(1, len(_EXPECTED_EVENT_SEQUENCE) + 1)
        )
        # claim_session is a one-shot 'done' bootstrap (no separate completion).
        assert creates[0]["fields"]["status"] == "done"
        assert creates[0]["fields"]["durationMs"] is None

        # A SINGLE runId across every event of the run. (runId lives on the
        # create docs; completion updates merge onto them and carry only
        # status/durationMs, so we read it off the creates.)
        run_ids = {c["fields"]["runId"] for c in creates}
        assert len(run_ids) == 1
        assert next(iter(run_ids))  # non-empty

        # Every non-bootstrap node emitted a completion (merge update) too.
        completed_types = {
            c["fields"]["type"] for c in creates[1:]
        }
        updates = [w for w in env.events if w["merge"]]
        assert updates, "expected start→complete updates"
        assert all(u["fields"]["status"] in {"done", "failed"} for u in updates)
        assert len(updates) == len(completed_types)  # one completion per pair node


# ==========================================================
# 2. Drain completes before the 'done' status flip
# ==========================================================
def test_gate2_drain_completes_before_done_flip():
    with _gate2_graph(claimed_session_id="doc1", input_session_id="doc1") as env:
        # The 'done' status write must come AFTER every agentEvent write — the
        # pre-'done' drain guarantees the panel receives every event before the
        # session flips to 'done' (which hides the panel).
        done_idx = next(
            i for i, (kind, detail) in enumerate(env.order)
            if kind == "status" and detail == "done"
        )
        last_event_idx = max(
            i for i, (kind, _d) in enumerate(env.order) if kind == "agent_event"
        )
        assert done_idx > last_event_idx


# ==========================================================
# 3. HIGHEST-VALUE — resume-on-clarify: events under the ORIGINAL session id
# ==========================================================
def test_gate2_resume_events_land_under_original_session_id():
    # The claimed forecastQueries doc's sessionId (the ORIGINAL session) differs
    # from the queue-doc id the runner passes in. Through the FULL graph, every
    # event must land under the ORIGINAL session id, never the queue-doc UUID.
    with _gate2_graph(
        claimed_session_id="orig-sess", input_session_id="fresh-queue-uuid",
    ) as env:
        assert env.events, "expected events from the full run"
        # Every write (create AND completion) targets the ORIGINAL session id.
        assert all(w["session_id"] == "orig-sess" for w in env.events)
        assert all(w["session_id"] != "fresh-queue-uuid" for w in env.events)
        # And still a single runId, stamped on every create doc.
        run_ids = {c["fields"]["runId"] for c in env.events if not c["merge"]}
        assert len(run_ids) == 1 and next(iter(run_ids))
