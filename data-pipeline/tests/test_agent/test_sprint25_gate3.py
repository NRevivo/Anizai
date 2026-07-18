"""
Gate 3 emulator tests for Sprint 25 — suggestedActions + agentEvents persisted
to a real Firestore emulator (T25.11).

Runs the full pipeline via `process_query` against the Firestore EMULATOR: only
Firestore is real. The five LLM clients, the three retrieval agents, and the
reactive-trigger producer (the `mock_reactive_producer` conftest fixture) are
mocked. Verifies the Sprint-25 surfaces round-trip through real Firestore:
    - sessionResults/{id}.suggestedActions is the 3-item `{id,label,prompt}`
      list the new node produced (via write_to_firestore's inject);
    - sessions/{id}/agentEvents holds the full runId-stamped event stream
      (bootstrap `claim_session` + per-node start/complete, merged to their
      final state), sequence-ordered, and the session doc carries currentRunId
      matching them;
    - the events are DURABLY written — a FRESH read sees the whole stream after
      the run ("refresh survives"; §3 events are stored permanently).

Skips when the emulator is unreachable (via the emulator_db fixture).
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from firebase_admin import firestore as fb_firestore

from agent.process_query import process_query


# Insufficient-evidence path (empty packages) → the run also exercises the
# conditional trigger branch (hermetic via mock_reactive_producer).
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


# ==========================================================
# Builders
# ==========================================================
def _seed_pending_query(db, session_id: str, question: str = "Will the Fed cut rates?") -> None:
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


def _seed_resume_query(db, orig_session_id: str, fresh_query_doc_id: str,
                       question: str = "Will the Fed cut rates?") -> None:
    """Resume-on-clarify (freeform) shape:
      - sessions/{orig} EXISTS (status queued, canonicalKey null → freeform);
      - forecastQueries/{fresh-uuid} is the NEW resume doc — its body carries
        sessionId={orig} (Express writes a fresh UUID as the doc id);
      - forecastQueries/{orig} is the ORIGINAL queue doc, which EXISTS in
        production (the first submission's doc, now awaiting_clarification).
        Seeded as realistic production shape. NOTE (Sprint 26 T26.11 closed
        KG-B-18 via the retarget, NOT the 'seed the original' approach): step 6
        now marks the FRESH doc 'done', so {orig} correctly stays
        awaiting_clarification and there is no 404 regardless of this seeding.
        This test asserts only the event path / session status, not queue-doc
        status, so it is unaffected either way.
    """
    db.collection("sessions").document(orig_session_id).set({
        "status": "queued",
        "canonicalKey": None,
        "clarificationCandidates": None,
        "createdAt": fb_firestore.SERVER_TIMESTAMP,
    })
    db.collection("forecastQueries").document(fresh_query_doc_id).set({
        "queryId": f"q_{orig_session_id}",
        "sessionId": orig_session_id,   # ← points to the ORIGINAL session
        "userId": "test-user",
        "question": question,
        "status": "pending",
        "createdAt": fb_firestore.SERVER_TIMESTAMP,
        "claimedAt": None,
        "claimedBy": None,
    })
    db.collection("forecastQueries").document(orig_session_id).set({
        "queryId": f"q_{orig_session_id}",
        "sessionId": orig_session_id,
        "userId": "test-user",
        "question": question,
        "status": "awaiting_clarification",   # the original doc, pre-resume
        "createdAt": fb_firestore.SERVER_TIMESTAMP,
    })


def _json_client(payload: dict, *, total_tokens: int = 120) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
        usage=SimpleNamespace(prompt_tokens=80, completion_tokens=40, total_tokens=total_tokens),
    )
    return client


def _llm_clients() -> tuple[MagicMock, MagicMock, MagicMock, MagicMock, MagicMock]:
    qu = _json_client({"candidates": [{
        "intent": "forecast", "domain": "macro", "entities": ["Federal Reserve"],
        "polymarket_search_terms": None, "has_market_question_intent": True,
        "confidence": 0.95, "too_broad": False, "rejected": False,
    }]})

    emb = MagicMock()
    emb.embeddings.create.return_value = SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.01] * 1536)],
        usage=SimpleNamespace(total_tokens=8),
    )

    rate = _json_client({"ratings": []}, total_tokens=50)

    synth = _json_client({
        "final_probability": 0.7, "confidence": 0.65, "consensus_score": 0.6,
        "bottom_line_answer": "Moderate chance.", "detailed_explanation": "Because.",
        "summary_markdown": "## Summary", "market_comparison_insight": "None.",
        "sentiment_analysis_insight": "Mixed.", "evidence_feed_summary": "0 items.",
        "key_factors": [], "what_i_didnt_find": [], "reasoning_chain": [],
        "evidence_overlay": [],
    }, total_tokens=1500)

    sa = _json_client({"actions": [
        {"label": "Why so confident?", "prompt": "What drives the confidence?"},
        {"label": "Strongest driver", "prompt": "Which evidence mattered most?"},
        {"label": "Compare to the market", "prompt": "How does this compare?"},
    ]}, total_tokens=150)

    return qu, emb, rate, synth, sa


def _empty(**extra) -> dict:
    return {"empty": True, **extra}


@contextmanager
def _pipeline_mocks():
    """Patch the five LLM clients + three agents + worker_id. The reactive
    producer is stubbed by the mock_reactive_producer fixture (requested in each
    test). Firestore stays REAL (the emulator)."""
    qu, emb, rate, synth, sa = _llm_clients()
    with (
        patch("agent.nodes.claim_session.settings.AGENT_WORKER_ID", "worker-test"),
        patch("agent.nodes.query_understand._get_default_client", return_value=qu),
        patch("agent.nodes.build_embedding._get_default_client", return_value=emb),
        patch("agent.nodes.rate_evidence._get_default_client", return_value=rate),
        patch("agent.nodes.synthesize._get_default_client", return_value=synth),
        patch("agent.nodes.generate_suggested_actions._get_default_client", return_value=sa),
        patch("agent.agents.researcher.run",
              return_value=_empty(articles=[], source_diversity={}, recency_range=None)),
        patch("agent.agents.pulse_analyst.run",
              return_value=_empty(market_consensus=[], community_discussion=[], overall_sentiment=0.0)),
        patch("agent.agents.market_bridge.run",
              return_value=_empty(polymarket=None, linked_sources=[], fred_anomalies=[], google_trends=[])),
    ):
        yield


def _agent_events(db, session_id: str) -> list[dict]:
    coll = db.collection("sessions").document(session_id).collection("agentEvents")
    return [doc.to_dict() for doc in coll.stream()]


# ==========================================================
# 1. suggestedActions persisted on sessionResults
# ==========================================================
def test_gate3_suggested_actions_persisted(emulator_db, emulator_test_id, mock_reactive_producer):
    db = emulator_db
    session_id = f"{emulator_test_id}_sa"
    _seed_pending_query(db, session_id)

    with _pipeline_mocks():
        process_query(session_id)

    result = db.collection("sessionResults").document(session_id).get().to_dict()
    actions = result["suggestedActions"]
    assert [a["id"] for a in actions] == ["sa-1", "sa-2", "sa-3"]
    assert all({"id", "label", "prompt"} <= set(a) for a in actions)


# ==========================================================
# 2. agentEvents stream persisted + currentRunId on the session doc
# ==========================================================
def test_gate3_agent_events_stream_persisted(emulator_db, emulator_test_id, mock_reactive_producer):
    db = emulator_db
    session_id = f"{emulator_test_id}_events"
    _seed_pending_query(db, session_id)

    with _pipeline_mocks():
        process_query(session_id)

    events = _agent_events(db, session_id)
    assert events, "expected agentEvents written to the emulator"
    events.sort(key=lambda e: e["sequence"])

    # Bootstrap first, then each node in sequence order (1..N).
    assert [e["type"] for e in events] == _EXPECTED_EVENT_SEQUENCE
    assert [e["sequence"] for e in events] == list(range(1, len(_EXPECTED_EVENT_SEQUENCE) + 1))

    # A single runId across the run; every event carries it + no parentMessageId.
    run_ids = {e["runId"] for e in events}
    assert len(run_ids) == 1
    run_id = next(iter(run_ids))
    assert all("parentMessageId" not in e for e in events)

    # All merged to their final 'done' state; bootstrap has null durationMs,
    # the pair nodes have an int durationMs (start→complete delta).
    assert all(e["status"] == "done" for e in events)
    assert events[0]["type"] == "claim_session"
    assert events[0]["durationMs"] is None
    assert all(isinstance(e["durationMs"], int) for e in events[1:])

    # The session doc carries currentRunId matching the events (the panel's
    # filter key), written before the first event.
    session = db.collection("sessions").document(session_id).get().to_dict()
    assert session["currentRunId"] == run_id
    assert session["status"] == "done"


# ==========================================================
# 3. Refresh survives — a fresh read sees the durable event stream
# ==========================================================
def test_gate3_events_durable_across_fresh_read(emulator_db, emulator_test_id, mock_reactive_producer):
    """§3 storage decision: events are permanently stored, so a FRESH read
    (as a page reload's listener would issue) sees the whole stream after the
    run — the 'refresh survives' property."""
    db = emulator_db
    session_id = f"{emulator_test_id}_refresh"
    _seed_pending_query(db, session_id)

    with _pipeline_mocks():
        process_query(session_id)

    # Brand-new query against the subcollection (independent of any listener
    # that was open during the run).
    fresh = _agent_events(db, session_id)
    assert len(fresh) == len(_EXPECTED_EVENT_SEQUENCE)
    assert {e["type"] for e in fresh} == set(_EXPECTED_EVENT_SEQUENCE)


# ==========================================================
# 4. HIGHEST-VALUE — resume-on-clarify: events under the ORIGINAL session id
# ==========================================================
def test_gate3_resume_events_land_under_original_session(
    emulator_db, emulator_test_id, mock_reactive_producer
):
    """findings #3 at the emulator level (the sprint's #1 risk): on resume-on-
    clarify the claimed forecastQueries doc's sessionId ≠ the queue-doc id. Only
    a REAL Firestore proves events land under the ORIGINAL session's
    subcollection — NOT as a PHANTOM subcollection Firestore would silently
    create under the non-existent queue-doc parent (a mock cannot catch that)."""
    db = emulator_db
    orig = f"{emulator_test_id}_orig"
    fresh_uuid = f"{emulator_test_id}_freshfq"
    _seed_resume_query(db, orig, fresh_uuid)

    with _pipeline_mocks():
        process_query(fresh_uuid)   # the runner receives the FRESH queue-doc id

    # Full event stream under the ORIGINAL (resolved) session id, in order.
    orig_events = _agent_events(db, orig)
    orig_events.sort(key=lambda e: e["sequence"])
    assert [e["type"] for e in orig_events] == _EXPECTED_EVENT_SEQUENCE
    run_ids = {e["runId"] for e in orig_events}
    assert len(run_ids) == 1

    # NO phantom subcollection under the (non-existent) queue-doc parent.
    assert _agent_events(db, fresh_uuid) == []

    # currentRunId on the ORIGINAL session doc matches the events (panel filter).
    session = db.collection("sessions").document(orig).get().to_dict()
    assert session["currentRunId"] == next(iter(run_ids))
    assert session["status"] == "done"
