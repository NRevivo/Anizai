"""
Gate 3 emulator integration tests for the Agentic Hub.

Sprint 18 baseline (T12): real Firestore emulator round-trip; verifies the
@firestore.transactional decorator path that Gate 1 bypassed (via
_claim_query_txn.to_wrap), the real .update()/.set() semantics, and
server-side SERVER_TIMESTAMP resolution.

Sprint 20 extension (T20.10): the round-trip test now exercises the full
forecast graph including rate_evidence (Sprint 20 T20.1), synthesize
(T20.3, gpt-4o), and write_to_firestore (T20.5). LLM clients are mocked
per the Bundle B fixture pattern — Gate 3's purpose is Firestore
round-trip via emulator, not LLM output validation. T20.11 (E2E) is
where real OpenAI lives. The Sprint 18 stub-mode pinned assertions
(finalProbability == 0.5, agentVersion == '0.1.0-sprint18-stub',
reasoningChain == []) are replaced with shape/range assertions per
T20.10 guidance — pinned values bake in Sprint 20's specific output and
would break in Sprint 21+ when synthesis behavior shifts.

Out of scope (covered elsewhere or deferred):
    - Failure paths — Gate 2 (mocked) covers them comprehensively.
    - True concurrent claim race (threading + Aborted retries) — Sprint 26
      hardening. Sequential contention is enough to prove the guard
      clause against real Firestore data.
    - Worker snapshot listener round-trip — T13 (E2E manual run).
    - Real OpenAI calls — T20.11 (E2E).

Setup:
    Start the emulator before running:
        firebase emulators:start --only firestore
    Default binding localhost:8080. Override via FIRESTORE_EMULATOR_HOST.
    If unreachable, the file skips (not fails) — matches the existing
    db_available pattern.
"""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from firebase_admin import firestore as fb_firestore

from agent import firestore_client
from agent.nodes import synthesize as synthesize_module
from agent.process_query import process_query


# ==========================================================
# Helpers
# ==========================================================

def _seed_pending_query(db, session_id: str, question: str = "Will it rain?") -> None:
    """Mimic the server-side write at session.repository.ts:335-347 — both
    docs that the worker expects to read against."""
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


def _make_qu_response(*, candidates_confidence: float = 0.95) -> SimpleNamespace:
    """openai>=1.x ChatCompletion shape for the query_understand node."""
    payload = {
        "candidates": [{
            "intent": "forecast",
            "domain": "macro",
            "entities": ["Federal Reserve"],
            "polymarket_search_terms": None,
            "has_market_question_intent": True,
            "confidence": candidates_confidence,
            "too_broad": False,
            "rejected": False,
        }],
    }
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(payload))
        )],
        usage=SimpleNamespace(total_tokens=120),
    )


def _make_embedding_response(dim: int = 1536) -> SimpleNamespace:
    return SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.01] * dim)],
        usage=SimpleNamespace(total_tokens=8),
    )


def _make_synthesis_response() -> SimpleNamespace:
    """A minimum-valid SynthesisOutput envelope. Fields chosen to
    exercise the label-derivation pipeline (confidence > 0.5 → Moderate;
    consensus > 0.5 → Mixed) and to populate reasoningChain non-empty
    (>= 4 entries per the synthesis_lead schema)."""
    payload = {
        "final_probability": 0.7,
        "confidence": 0.65,
        "consensus_score": 0.6,
        "bottom_line_answer": "Likely yes.",
        "detailed_explanation": "Detailed explanation here.",
        "summary_markdown": "**Forecast:** likely yes.",
        "market_comparison_insight": "Markets agree.",
        "sentiment_analysis_insight": "Sentiment is positive.",
        "evidence_feed_summary": "Evidence reviewed.",
        "what_i_didnt_find": [],
        "key_factors": [
            {"label": "Factor A", "description": "Drives up.",
             "weight": 0.4, "direction": "increases", "evidence_ids": []},
            {"label": "Factor B", "description": "Mild down.",
             "weight": 0.2, "direction": "decreases", "evidence_ids": []},
            {"label": "Factor C", "description": "Drives up.",
             "weight": 0.3, "direction": "increases", "evidence_ids": []},
        ],
        "reasoning_chain": [
            {"step": 1, "title": "Identify question",
             "description": "Parsed the resolution criterion."},
            {"step": 2, "title": "Review evidence",
             "description": "Evaluated retrieved evidence."},
            {"step": 3, "title": "Weigh factors",
             "description": "Identified the key drivers."},
            {"step": 4, "title": "Produce forecast",
             "description": "Calibrated final probability."},
        ],
        "evidence_overlay": [],
    }
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(payload))
        )],
        usage=SimpleNamespace(total_tokens=2000),
    )


def _build_llm_mocks():
    """Construct the four MagicMock clients the Sprint 20 graph needs:
    query_understand, build_embedding, rate_evidence, synthesize.
    Each mock is configured with a default valid response so the graph
    runs the cold-start path (empty agent packages → rate_evidence
    short-circuits → synthesize handles empty evidence)."""
    qu_client = MagicMock()
    qu_client.chat.completions.create.return_value = _make_qu_response()

    emb_client = MagicMock()
    emb_client.embeddings.create.return_value = _make_embedding_response()

    rate_client = MagicMock()
    rate_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps({"ratings": []}))
        )],
        usage=SimpleNamespace(total_tokens=50),
    )

    synth_client = MagicMock()
    synth_client.chat.completions.create.return_value = _make_synthesis_response()

    return qu_client, emb_client, rate_client, synth_client


# ==========================================================
# 1. Round-trip — full process_query against the emulator
# ==========================================================

def test_round_trip_writes_session_result(emulator_db, emulator_test_id):
    """Submit a pending forecastQueries doc → run the full Sprint 20
    graph (claim → query_understand → build_embedding → vault_query →
    rate_evidence → synthesize → write_to_firestore) → verify
    sessionResults/{id} matches §8.7.2 shape and all timestamp fields
    resolve to real Timestamps server-side. LLM calls and vault reads
    are mocked at the boundary; only Firestore is real."""
    db = emulator_db
    session_id = f"{emulator_test_id}_round_trip"
    question = "Will Bitcoin hit 100k by EOY?"
    _seed_pending_query(db, session_id, question)

    qu_client, emb_client, rate_client, synth_client = _build_llm_mocks()

    with (
        # Sprint 20: claim_session reads AGENT_WORKER_ID directly (Sprint
        # 18's process_query.settings.AGENT_WORKER_ID lookup site is
        # gone — the runner shrunk in T20.7 and no longer imports
        # settings). Patch where it's actually looked up now.
        patch("agent.nodes.claim_session.settings.AGENT_WORKER_ID", "worker-test"),
        patch(
            "agent.nodes.query_understand._get_default_client",
            return_value=qu_client,
        ),
        patch(
            "agent.nodes.build_embedding._get_default_client",
            return_value=emb_client,
        ),
        patch(
            "agent.nodes.rate_evidence._get_default_client",
            return_value=rate_client,
        ),
        patch(
            "agent.nodes.synthesize._get_default_client",
            return_value=synth_client,
        ),
        patch("agent.agents.researcher.run") as mock_researcher,
        patch("agent.agents.pulse_analyst.run") as mock_pulse,
        patch("agent.agents.market_bridge.run") as mock_market,
    ):
        # Empty agent packages → cold-start path through rate_evidence
        # and synthesize. Sprint 20 D3 + KG-PHASE8-12: marketProbability
        # is always None until the polymarket resolver lands.
        mock_researcher.return_value = {
            "articles": [], "source_diversity": {},
            "recency_range": None, "empty": True,
        }
        mock_pulse.return_value = {
            "market_consensus": [], "community_discussion": [],
            "overall_sentiment": 0.0, "empty": True,
        }
        mock_market.return_value = {
            "polymarket": None, "linked_sources": [],
            "fred_anomalies": [], "google_trends": [], "empty": True,
        }

        process_query(session_id)

    # --- sessionResults/{id} ---
    result_doc = db.collection("sessionResults").document(session_id).get()
    assert result_doc.exists, f"sessionResults/{session_id} not written"
    result = result_doc.to_dict()

    # Numerics — shape + range only (Sprint 20: synthesis output is
    # LLM-driven; pinned 0.5 from Sprint 18 stub no longer applies)
    assert isinstance(result["finalProbability"], float)
    assert 0.0 <= result["finalProbability"] <= 1.0
    assert isinstance(result["confidence"], float)
    assert 0.0 <= result["confidence"] <= 1.0

    # Derived labels — vocabulary only (deterministic from labels.py)
    assert result["confidenceLabel"] in {"Low", "Moderate", "High"}
    assert result["consensusStrength"] in {"Weak", "Mixed", "Strong"}
    assert result["evidenceVolumeLabel"] in {"Low", "Moderate", "High"}

    # Market — null + empty triggers Patch 4 "no canonical market" UI
    # state (KG-PHASE8-12; persists until Sprint 21+ T21.6).
    assert result["marketProbability"] is None
    assert result["marketComparison"] == []

    # List shapes — Sprint 20 produces non-empty reasoningChain (>= 4)
    # and key_factors (>= 3) per the synthesis_lead schema; suggestedActions
    # stays [] until Sprint 25 (D8). whatIDidntFind can be empty when
    # the model is confident; pin it as a list, not on length.
    assert isinstance(result["keyFactors"], list)
    assert len(result["keyFactors"]) >= 3, "spec §8.7.2: 3-5 drivers"
    assert isinstance(result["reasoningChain"], list)
    assert len(result["reasoningChain"]) >= 4, (
        "synthesis_lead schema sets minItems=4 for reasoning_chain (Q3 of "
        "Sprint 20 plan)"
    )
    assert isinstance(result["whatIDidntFind"], list)
    assert result["suggestedActions"] == []  # D8: deferred to Sprint 25

    # Question text reaches the synthesize prompt's user message
    user_msg = synth_client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert question in user_msg

    # Metadata — version is shape only (starts with semver-ish prefix)
    assert result["tier"] == "tier_1"
    assert isinstance(result["agentVersion"], str)
    assert result["agentVersion"].startswith("0."), (
        "agentVersion is semver-ish; pin shape, not the literal Sprint 20 "
        "value (Sprint 21 will bump)"
    )
    assert result["agentVersion"] == synthesize_module.AGENT_VERSION
    assert result["sessionId"] == session_id  # added by write_session_result wrapper

    # Narrative fields non-empty
    for key in (
        "bottomLineAnswer",
        "detailedExplanation",
        "summaryMarkdown",
        "marketComparisonInsight",
        "sentimentAnalysisInsight",
        "evidenceFeedSummary",
    ):
        assert isinstance(result[key], str) and result[key], f"{key} empty"

    # --- Timestamp resolution check ---
    # Firestore returns DatetimeWithNanoseconds (subclass of datetime) for
    # SERVER_TIMESTAMP fields after server resolution. isinstance(...,
    # datetime) works because of the subclass relationship.
    assert isinstance(result["generatedAt"], datetime), (
        f"generatedAt should be datetime; got {type(result['generatedAt']).__name__}"
    )
    assert isinstance(result["createdAt"], datetime)
    assert isinstance(result["updatedAt"], datetime)

    # --- Sprint 20 subcollections (T20.5) ---
    # All three are empty for cold-start (no evidence retrieved →
    # nothing to write to evidence subcollection; KG-PHASE8-12 +
    # Q5 → predictionSeries and sentimentTimeSeries always empty).
    # Pin that the subcollection refs exist and are queryable, not
    # that they're empty (Sprint 21+ may populate predictionSeries
    # once a polymarket anchor is available).
    evidence_docs = list(
        db.collection("sessions").document(session_id).collection("evidence").stream()
    )
    assert evidence_docs == [], "cold-start: no evidence retrieved → empty subcollection"

    prediction_docs = list(
        db.collection("sessions").document(session_id).collection("predictionSeries").stream()
    )
    assert prediction_docs == [], "Sprint 20: predictionSeries empty (KG-PHASE8-12)"

    sentiment_docs = list(
        db.collection("sessions").document(session_id).collection("sentimentTimeSeries").stream()
    )
    assert sentiment_docs == [], "Sprint 20: sentimentTimeSeries empty (Q5)"

    # --- forecastQueries/{id} ---
    fq = db.collection("forecastQueries").document(session_id).get().to_dict()
    assert fq["status"] == "done"
    assert fq["claimedBy"] == "worker-test"
    assert isinstance(fq["claimedAt"], datetime), (
        "claimedAt must be a real Timestamp; the SERVER_TIMESTAMP sentinel "
        "should have been resolved server-side"
    )

    # --- sessions/{id} ---
    session = db.collection("sessions").document(session_id).get().to_dict()
    assert session["status"] == "done"
    assert isinstance(session["updatedAt"], datetime)
    assert isinstance(session["lastActivityAt"], datetime)


# ==========================================================
# 2. Atomic claim — sequential contention
# ==========================================================

def test_atomic_claim_sequential_contention(emulator_db, emulator_test_id):
    """First claim wins, second loses. Exercises the real
    @firestore.transactional decorator path (BEGIN / GET / UPDATE / COMMIT)
    against the emulator — the path Gate 1 bypassed via to_wrap. No
    LLM mocks needed; this test only touches the claim transaction."""
    db = emulator_db
    session_id = f"{emulator_test_id}_contention"
    _seed_pending_query(db, session_id)

    result_a = firestore_client.claim_query(session_id, "worker-A")
    assert result_a is not None
    assert result_a["sessionId"] == session_id
    assert result_a["status"] == "pending"  # original payload is pre-update snapshot

    result_b = firestore_client.claim_query(session_id, "worker-B")
    assert result_b is None  # already claimed by A

    fq = db.collection("forecastQueries").document(session_id).get().to_dict()
    assert fq["status"] == "claimed"
    assert fq["claimedBy"] == "worker-A"  # B never overwrote A's claim
    assert isinstance(fq["claimedAt"], datetime)


# ==========================================================
# 3. Race-lost — pre-claimed doc returns None and is unchanged
# ==========================================================

def test_claim_already_claimed_returns_none(emulator_db, emulator_test_id):
    """Doc that was already claimed (e.g., by another worker before us)
    must return None and leave the doc completely untouched. Verified
    against real Firestore data, not just mocked snapshots."""
    db = emulator_db
    session_id = f"{emulator_test_id}_already_claimed"

    db.collection("forecastQueries").document(session_id).set({
        "queryId": f"q_{session_id}",
        "sessionId": session_id,
        "userId": "test-user",
        "question": "test",
        "status": "claimed",
        "createdAt": fb_firestore.SERVER_TIMESTAMP,
        "claimedAt": fb_firestore.SERVER_TIMESTAMP,
        "claimedBy": "someone-else",
    })

    result = firestore_client.claim_query(session_id, "worker-test")
    assert result is None

    fq = db.collection("forecastQueries").document(session_id).get().to_dict()
    assert fq["status"] == "claimed"
    assert fq["claimedBy"] == "someone-else"  # untouched
