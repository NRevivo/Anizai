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
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

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

    # Metadata — tier is tier_2 because the mock returns polymarket=None
    # (KG-PHASE8-12 still deferred; Sprint 21 tier inference uses market_evidence).
    assert result["tier"] == "tier_2"
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
    # Sprint 22 T22.7: Tier 2 success path writes explicit Firestore null
    # to canonicalKey (clears any candidate UUID Express may have set
    # during a clarification cycle). The field must be PRESENT in the
    # doc with value None — not absent — so the cross-user cache lookup
    # (Future Enhancement 3) can correctly classify the session as
    # cacheless on read.
    assert "canonicalKey" in session, (
        "Sprint 22 T22.7: canonicalKey field must be written on every "
        "success transition, including Tier 2 (explicit null per "
        "Convention B / UNSET sentinel)."
    )
    assert session["canonicalKey"] is None


# ==========================================================
# Sprint 22 T22.10 — Tier 1 round-trip (all 5 BI cards)
# ==========================================================

def test_round_trip_tier_1_writes_full_bi_cards(emulator_db, emulator_test_id):
    """
    Sprint 22 T22.10 — Tier 1 end-to-end against the real Firestore
    emulator. Verifies that:

    1. SessionResult shape carries marketProbability, marketComparison
       (T22.3), tier=tier_1.
    2. predictionSeries subcollection populates with the T22.4 adapter's
       wire shape — `ts` round-trips as a Firestore Timestamp (NOT a
       string; see D3 — the critical bug T22.4 averts).
    3. sentimentTimeSeries subcollection populates with the T22.6
       adapter's wire shape — `ts` Firestore Timestamp, `date` ISO
       string, both sentiments normalized to [0, 1].
    4. canonicalKey on the parent session doc equals the resolved
       market_slug (T22.7) — first end-to-end verification that the
       Convention B UNSET-sentinel write actually lands.

    Same mock strategy as the Tier 2 round-trip above (mock at the
    three retrieval-agent boundaries + every OpenAI client). The
    difference is the agents return POPULATED payloads exercising the
    Sprint 22 wiring end-to-end.

    Evidence dates use relative offsets from now (NOT hardcoded May
    2026) so the bucketing helper's window catches them at the time
    the test runs.
    """
    db = emulator_db
    session_id = f"{emulator_test_id}_tier_1"
    question = "Will the Fed cut rates before June 2026?"
    _seed_pending_query(db, session_id, question)

    qu_client, emb_client, rate_client, synth_client = _build_llm_mocks()

    # Rate-evidence OpenAI mock — must produce a `ratings` list keyed by
    # evidence_id. Since the IDs are generated inside rate_evidence
    # itself, use a side_effect that parses the prompt and constructs
    # a matching response (same trick as Gate 2 subgraph tests).
    import re

    def _build_rate_response(**kwargs):
        user_msg = kwargs["messages"][1]["content"]
        ids = re.findall(
            r"evidence[_-]?id\s*[:=]\s*\"?([A-Za-z0-9_\-]{6,64})\"?",
            user_msg, re.IGNORECASE,
        )
        seen = set()
        unique_ids = [i for i in ids if not (i in seen or seen.add(i))]
        payload = {
            "ratings": [
                {"evidence_id": eid, "relevance_score": 0.8,
                 "justification": "relevant"}
                for eid in unique_ids
            ],
        }
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
            usage=SimpleNamespace(total_tokens=200),
        )
    rate_client.chat.completions.create.side_effect = _build_rate_response

    # Recent dates relative to now (test runs at unknown wall-clock).
    now = datetime.now(timezone.utc)
    two_days_ago = (now - timedelta(days=2)).replace(microsecond=0)
    three_days_ago = (now - timedelta(days=3)).replace(microsecond=0)
    five_days_ago = (now - timedelta(days=5)).replace(microsecond=0)

    # Polymarket market_slug — the value that should reach canonicalKey.
    market_slug = "0xfed_condition_id"

    with (
        patch("agent.nodes.claim_session.settings.AGENT_WORKER_ID", "worker-tier1"),
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
        # Researcher: 2 articles with sentiment_score + published_at
        # (Expert-line bucketing input).
        mock_researcher.return_value = {
            "articles": [
                {
                    "signal_id": "news-a",
                    "source_platform": "newsapi",
                    "publisher": "reuters.com",
                    "title": "Fed signals dovish",
                    "published_at": three_days_ago.isoformat(),
                    "executive_summary": "Fed dovish.",
                    "key_findings": [],
                    "full_text_snippet": "Snippet A.",
                    "impact_level": 4,
                    "reliability_score": 0.8,
                    "sentiment_score": 0.4,         # → normalized 0.7
                    "similarity": 0.7,
                    "evidence_weight": 0.75,
                    "canonical_event_id": "",
                },
                {
                    "signal_id": "news-b",
                    "source_platform": "newsapi",
                    "publisher": "ft.com",
                    "title": "Inflation cooling",
                    "published_at": five_days_ago.isoformat(),
                    "executive_summary": "Inflation cooling.",
                    "key_findings": [],
                    "full_text_snippet": "Snippet B.",
                    "impact_level": 3,
                    "reliability_score": 0.7,
                    "sentiment_score": 0.6,         # → normalized 0.8
                    "similarity": 0.6,
                    "evidence_weight": 0.65,
                    "canonical_event_id": "",
                },
            ],
            "source_diversity": {"newsapi_count": 2, "arxiv_count": 0, "telegram_count": 0},
            "recency_range": None,
            "empty": False,
        }
        # Pulse: 1 HackerNews discussion (Public-line bucketing input).
        # D5 patch: published_at must be present on community_discussion items.
        mock_pulse.return_value = {
            "market_consensus": [],
            "community_discussion": [
                {
                    "signal_id": "hn-a",
                    "platform": "hackernews",
                    "title": "HN discussion",
                    "points": 50,
                    "top_technical_insights": [],
                    "community_sentiment": -0.4,    # → normalized 0.3
                    "published_at": two_days_ago.isoformat(),
                    "similarity": 0.6,
                    "evidence_weight": 0.4,
                },
            ],
            "overall_sentiment": 0.0,
            "empty": False,
        }
        # Market Bridge: populated Polymarket payload with price_history
        # (T22.4 predictionSeries input) and market_slug (T22.7 canonicalKey
        # input).
        mock_market.return_value = {
            "polymarket": {
                "current_odds": 0.62,
                "momentum": {"change_24h": 0.03, "change_7d": 0.07, "change_30d": 0.12},
                "price_history": [
                    {"timestamp": five_days_ago.isoformat(),  "value": 0.55},
                    {"timestamp": three_days_ago.isoformat(), "value": 0.60},
                    {"timestamp": two_days_ago.isoformat(),   "value": 0.62},
                ],
                "whale_alerts": [],
                "market_slug": market_slug,
            },
            "linked_sources": [],
            "fred_anomalies": [],
            "google_trends": [],
            "empty": False,
        }

        process_query(session_id)

    # --- sessionResults/{id} ---
    result_doc = db.collection("sessionResults").document(session_id).get()
    assert result_doc.exists
    result = result_doc.to_dict()

    # MarketComparison BI card (T22.3) — populated.
    assert result["marketProbability"] == pytest.approx(0.62), (
        "T22.3: marketProbability should equal Polymarket's current_odds"
    )
    assert result["marketComparison"] == [
        {"label": "Anizai", "value": pytest.approx(result["finalProbability"])},
        {"label": "Polymarket", "value": pytest.approx(0.62)},
    ]
    # NO_MARKET_CAPTION must NOT fire on Tier 1.
    assert result["marketComparisonInsight"] != synthesize_module.NO_MARKET_CAPTION
    # Tier inference from market_evidence.polymarket (Sprint 21 T21.7).
    assert result["tier"] == "tier_1"

    # PredictionOverview BI card — shape sanity.
    assert isinstance(result["finalProbability"], float)
    assert 0.0 <= result["finalProbability"] <= 1.0
    assert result["confidenceLabel"] in {"Low", "Moderate", "High"}

    # --- sessions/{id} (T22.7 canonicalKey + tier) ---
    session = db.collection("sessions").document(session_id).get().to_dict()
    assert session["status"] == "done"
    assert session["tier"] == "tier_1", (
        "Sprint 21 T21.8: tier on session doc"
    )
    assert "canonicalKey" in session
    assert session["canonicalKey"] == market_slug, (
        "T22.7: canonicalKey on session doc must equal the resolved "
        "Polymarket market_slug for Future Enhancement 3 (cache lookup)"
    )

    # --- predictionSeries subcollection (T22.4) ---
    prediction_docs = list(
        db.collection("sessions").document(session_id)
          .collection("predictionSeries").stream()
    )
    assert len(prediction_docs) == 3, (
        "T22.4: one doc per price_history entry"
    )
    for doc_snap in prediction_docs:
        doc = doc_snap.to_dict()
        # Critical D3 check — `ts` round-trips as Firestore Timestamp
        # (Python datetime via firestore-admin SDK), NOT ISO string.
        # If T22.4 ever regresses to writing a string, this fails because
        # data.ts isn't a datetime instance.
        assert isinstance(doc["ts"], datetime), (
            f"T22.4 D3: ts must be a Firestore Timestamp that the server's "
            f"toISOString(data.ts)?.toDate?.() can call .toDate() on. "
            f"Got {type(doc['ts']).__name__}: {doc['ts']!r}"
        )
        assert doc["ts"].tzinfo is not None
        assert isinstance(doc["probability"], float)
        assert doc["confidence"] == 1.0
        assert doc["reasonType"] == "market"
        assert doc["evidenceIds"] == []

    # predictionSeries docs ordered by ts ascending (server query
    # uses .orderBy('ts', 'asc')).
    timestamps = [d.to_dict()["ts"] for d in prediction_docs]
    # Firestore .stream() doesn't guarantee order; we sort to mirror what
    # the server's query produces and verify the values are at the expected
    # timestamps.
    timestamps_sorted = sorted(timestamps)
    assert timestamps_sorted[0].date() == five_days_ago.date()
    assert timestamps_sorted[-1].date() == two_days_ago.date()

    # --- sentimentTimeSeries subcollection (T22.6) ---
    sentiment_docs = list(
        db.collection("sessions").document(session_id)
          .collection("sentimentTimeSeries").stream()
    )
    assert len(sentiment_docs) >= 1, (
        "T22.6: at least one bucket has data (Expert OR Public)"
    )

    # Track whether expertUpper/expertLower are PRESENT-with-null or ABSENT.
    # Reported in the test output for D6 follow-up consideration.
    expert_upper_states: list[str] = []
    expert_lower_states: list[str] = []

    for doc_snap in sentiment_docs:
        doc = doc_snap.to_dict()
        # D3-pattern check — same Firestore Timestamp gotcha.
        assert isinstance(doc["ts"], datetime), (
            f"T22.6: ts must round-trip as Firestore Timestamp. "
            f"Got {type(doc['ts']).__name__}: {doc['ts']!r}"
        )
        assert doc["ts"].tzinfo is not None
        # date is ISO YYYY-MM-DD.
        assert isinstance(doc["date"], str)
        assert len(doc["date"]) == 10
        assert doc["date"][4] == "-" and doc["date"][7] == "-"
        # T22.6 a1 + normalization: either side may be null; populated
        # values are in the FE's [0, 1] scale.
        for side in ("expertSentiment", "publicSentiment"):
            if doc.get(side) is not None:
                assert 0.0 <= doc[side] <= 1.0, (
                    f"T22.6 scale normalization: {side}={doc[side]} should "
                    f"be in [0, 1] (in-state [-1, 1] mapped via (x+1)/2)"
                )

        # OBSERVATION (per Ron's T22.10 reporting request): record whether
        # expertUpper / expertLower are present in the doc or absent.
        expert_upper_states.append(
            "present-null" if doc.get("expertUpper") is None
                              and "expertUpper" in doc
            else ("present-value" if "expertUpper" in doc else "absent")
        )
        expert_lower_states.append(
            "present-null" if doc.get("expertLower") is None
                              and "expertLower" in doc
            else ("present-value" if "expertLower" in doc else "absent")
        )

    # All buckets emit the same shape — collapse to a single state token.
    assert len(set(expert_upper_states)) == 1, (
        f"expertUpper state should be uniform across docs: {expert_upper_states}"
    )
    assert len(set(expert_lower_states)) == 1
    upper_state = expert_upper_states[0]
    lower_state = expert_lower_states[0]
    # Pin the observation as an assertion so future regressions show up:
    # T22.6's adapter writes the fields with None. If they're absent
    # instead, T22.6 was changed and D6 should be revisited.
    assert upper_state == "present-null", (
        f"T22.10 observation pin: expertUpper expected 'present-null' "
        f"but got '{upper_state}'. If absent, T22.6 stopped writing "
        f"the field — D6 should be revisited (potential cleanup)."
    )
    assert lower_state == "present-null"
    # Echo to stdout so the report-back captures the actual observation.
    print(
        f"\n[T22.10 observation] sentimentTimeSeries docs: "
        f"expertUpper={upper_state}, expertLower={lower_state}"
    )

    # --- evidence subcollection (sanity, not a primary T22 check) ---
    evidence_docs = list(
        db.collection("sessions").document(session_id)
          .collection("evidence").stream()
    )
    assert len(evidence_docs) >= 1


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


# ==========================================================
# Sprint 21 T21.11 — Gate 3 emulator tests for clarification flow
# ==========================================================

def _seed_resume_state(
    db,
    session_id: str,
    fresh_query_doc_id: str,
    canonical_key,
) -> None:
    """Simulate the Firestore state after write_clarification ran and Express
    processed POST /sessions/:id/clarify (G1-corrected production contract).

    Production behavior (server requeueClarifiedSession):
      - sessions/{session_id}: status=queued, canonicalKey=canonical_key,
        clarificationCandidates=null (Express CLEARS this)
      - forecastQueries/{fresh_query_doc_id}: status=pending,
        sessionId=session_id (fresh UUID doc, NOT session_id as doc id)
        NO chosenCandidateId field in the forecastQueries doc.

    sprint21_e2e_run.py uses the same shape.
    """
    # Session doc: Express requeues with canonicalKey set, candidates cleared.
    db.collection("sessions").document(session_id).set({
        "status": "queued",
        "canonicalKey": canonical_key,
        "clarificationCandidates": None,  # cleared by Express
        "question": "Will the Fed cut rates in 2026?",
        "userId": "test-user",
        "createdAt": fb_firestore.SERVER_TIMESTAMP,
        "updatedAt": fb_firestore.SERVER_TIMESTAMP,
        "lastActivityAt": fb_firestore.SERVER_TIMESTAMP,
    })
    # Fresh forecastQueries doc: new random UUID as doc id, sessionId points
    # to the original session. No chosenCandidateId field.
    db.collection("forecastQueries").document(fresh_query_doc_id).set({
        "queryId": f"q_{fresh_query_doc_id}",
        "sessionId": session_id,           # key field: actual session id
        "userId": "test-user",
        "question": "Will the Fed cut rates in 2026?",
        "status": "pending",
        "createdAt": fb_firestore.SERVER_TIMESTAMP,
        "claimedAt": None,
        "claimedBy": None,
    })


def test_sprint21_gate3_clarification_path_writes_awaiting_status(
    emulator_db, emulator_test_id
):
    """Ambiguous question → write_clarification → real Firestore emulator.

    Verifies:
    - sessions/{id}.status == 'awaiting_clarification'
    - sessions/{id}.clarificationCandidates has shaped candidates (UUID id, source=polymarket)
    - forecastQueries/{id}.status == 'awaiting_clarification'
    - sessionResults/{id} does NOT exist (synthesis never ran)
    """
    db = emulator_db
    session_id = f"{emulator_test_id}_clarification"
    _seed_pending_query(db, session_id, "What will happen in the Middle East?")

    qu_client, emb_client, rate_client, synth_client = _build_llm_mocks()
    # Override: return ambiguous candidates (margin = 0.78 - 0.74 = 0.04 < 0.10)
    payload_ambiguous = {"candidates": [
        {"intent": "forecast", "domain": "geopolitics",
         "entities": ["Iran", "Israel"], "polymarket_search_terms": ["iran israel conflict"],
         "has_market_question_intent": True, "confidence": 0.78,
         "too_broad": False, "rejected": False},
        {"intent": "forecast", "domain": "geopolitics",
         "entities": ["Gaza", "Hamas"], "polymarket_search_terms": ["gaza ceasefire"],
         "has_market_question_intent": True, "confidence": 0.74,
         "too_broad": False, "rejected": False},
    ]}
    qu_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps(payload_ambiguous)
        ))],
        usage=SimpleNamespace(total_tokens=150),
    )

    with (
        patch("agent.nodes.claim_session.settings.AGENT_WORKER_ID", "worker-test"),
        patch("agent.nodes.query_understand._get_default_client", return_value=qu_client),
        patch("agent.nodes.build_embedding._get_default_client", return_value=emb_client),
        patch("agent.nodes.rate_evidence._get_default_client", return_value=rate_client),
        patch("agent.nodes.synthesize._get_default_client", return_value=synth_client),
        patch("agent.agents.researcher.run") as mock_researcher,
        patch("agent.agents.pulse_analyst.run") as mock_pulse,
        patch("agent.agents.market_bridge.run") as mock_market,
    ):
        mock_researcher.return_value = {"articles": [], "source_diversity": {}, "recency_range": None, "empty": True}
        mock_pulse.return_value = {"market_consensus": [], "community_discussion": [], "overall_sentiment": 0.0, "empty": True}
        mock_market.return_value = {"polymarket": None, "linked_sources": [], "fred_anomalies": [], "google_trends": [], "empty": True}

        process_query(session_id)

    # Session doc must have awaiting_clarification + candidates
    session = db.collection("sessions").document(session_id).get().to_dict()
    assert session["status"] == "awaiting_clarification"
    candidates = session.get("clarificationCandidates")
    assert candidates is not None
    assert len(candidates) == 2
    for c in candidates:
        assert "id" in c
        assert "label" in c
        assert c.get("source") == "polymarket"
        assert "matchConfidence" in c

    # Queue doc must also have awaiting_clarification
    fq = db.collection("forecastQueries").document(session_id).get().to_dict()
    assert fq["status"] == "awaiting_clarification"

    # No sessionResults — synthesis never ran
    result_doc = db.collection("sessionResults").document(session_id).get()
    assert not result_doc.exists, "SessionResult must NOT exist on clarification path"

    # Vault agents must NOT have run (graph exited at write_clarification)
    mock_researcher.assert_not_called()


def test_sprint21_gate3_tier2_resume_freeform_completes_forecast(
    emulator_db, emulator_test_id
):
    """Resume-on-clarify (freeform, canonicalKey=null) → full Tier 2 forecast.

    Uses the G1-corrected production shape:
      - Express creates a NEW forecastQueries doc with a fresh UUID id
        (not the session id) and sessionId=original_session_id in the body.
      - session doc has canonicalKey=null (freeform) and clarificationCandidates=null.
      - process_query detects resume via sessionId!=query_doc_id in the
        forecastQueries doc (NOT via chosenCandidateId field).

    Sprint 21 known limitation (noted in G1 report):
      - write_to_firestore calls update_query_status(state["session_id"])
        which marks forecastQueries/{original_session_id} as "done" (not
        the fresh UUID doc). The fresh UUID doc stays at "claimed". This is
        acceptable for now — the worker listener only picks up "pending" docs
        so no re-processing occurs. Sprint 26 adds query_doc_id to state.
    """
    db = emulator_db
    session_id = f"{emulator_test_id}_tier2_resume"
    fresh_query_doc_id = f"{emulator_test_id}_tier2_resume_fq"  # prefix for cleanup

    _seed_resume_state(db, session_id, fresh_query_doc_id, canonical_key=None)

    qu_client, emb_client, rate_client, synth_client = _build_llm_mocks()

    with (
        patch("agent.nodes.claim_session.settings.AGENT_WORKER_ID", "worker-test"),
        patch("agent.nodes.query_understand._get_default_client", return_value=qu_client),
        patch("agent.nodes.build_embedding._get_default_client", return_value=emb_client),
        patch("agent.nodes.rate_evidence._get_default_client", return_value=rate_client),
        patch("agent.nodes.synthesize._get_default_client", return_value=synth_client),
        patch("agent.agents.researcher.run") as mock_researcher,
        patch("agent.agents.pulse_analyst.run") as mock_pulse,
        patch("agent.agents.market_bridge.run") as mock_market,
    ):
        mock_researcher.return_value = {"articles": [], "source_diversity": {}, "recency_range": None, "empty": True}
        mock_pulse.return_value = {"market_consensus": [], "community_discussion": [], "overall_sentiment": 0.0, "empty": True}
        mock_market.return_value = {"polymarket": None, "linked_sources": [], "fred_anomalies": [], "google_trends": [], "empty": True}

        # process_query receives the FRESH QUERY DOC ID, not the session id.
        process_query(fresh_query_doc_id)

        # query_understand must NOT have called the LLM (skip_matching_step)
        qu_client.chat.completions.create.assert_not_called()

    # sessionResults is keyed by the ACTUAL session_id (from claimed["sessionId"])
    result_doc = db.collection("sessionResults").document(session_id).get()
    assert result_doc.exists, f"sessionResults/{session_id} not written"
    result = result_doc.to_dict()
    assert result["tier"] == "tier_2"
    assert result["marketProbability"] is None
    assert result["marketComparison"] == []
    assert result["marketComparisonInsight"] == "No canonical market available — freeform analysis."

    # Session doc (original session_id) must be done with tier=tier_2
    session = db.collection("sessions").document(session_id).get().to_dict()
    assert session["status"] == "done"
    assert session["tier"] == "tier_2"  # T21.8

    # Fresh forecastQueries doc stays at "claimed" (Sprint 21 known limitation —
    # write_to_firestore uses actual_session_id for update_query_status, which
    # marks the original session's forecastQueries doc, not the fresh one).
    fq_fresh = db.collection("forecastQueries").document(fresh_query_doc_id).get().to_dict()
    assert fq_fresh["status"] == "claimed"  # not "done" — Sprint 26 fix


def test_sprint21_gate3_tier1_clear_path_writes_tier1(
    emulator_db, emulator_test_id
):
    """Clear Tier 1 path: market_bridge returns non-None polymarket → tier='tier_1'.

    Gate 3 regression guard added post-Sprint-21 review (#2 from review action
    items). Complements test_round_trip_writes_session_result (polymarket=None
    → tier_2) and the Gate 2 Tier 1 test in test_sprint21_gate2.py. Verifies
    the tier inference in synthesize.py and the tier field write in
    write_to_firestore.py against real emulator Firestore semantics.
    """
    db = emulator_db
    session_id = f"{emulator_test_id}_tier1_clear"
    question = "Will the Fed cut rates by Q3 2026?"
    _seed_pending_query(db, session_id, question)

    qu_client, emb_client, rate_client, synth_client = _build_llm_mocks()

    with (
        patch("agent.nodes.claim_session.settings.AGENT_WORKER_ID", "worker-test"),
        patch("agent.nodes.query_understand._get_default_client", return_value=qu_client),
        patch("agent.nodes.build_embedding._get_default_client", return_value=emb_client),
        patch("agent.nodes.rate_evidence._get_default_client", return_value=rate_client),
        patch("agent.nodes.synthesize._get_default_client", return_value=synth_client),
        patch("agent.agents.researcher.run") as mock_researcher,
        patch("agent.agents.pulse_analyst.run") as mock_pulse,
        patch("agent.agents.market_bridge.run") as mock_market,
    ):
        mock_researcher.return_value = {"articles": [], "source_diversity": {}, "recency_range": None, "empty": True}
        mock_pulse.return_value = {"market_consensus": [], "community_discussion": [], "overall_sentiment": 0.0, "empty": True}
        # Non-None polymarket triggers Tier 1 path in synthesize.py
        mock_market.return_value = {
            "polymarket": {"current_odds": 0.72, "market_slug": "fed-rate-cut-q3-2026"},
            "linked_sources": [],
            "fred_anomalies": [],
            "google_trends": [],
            "empty": False,
        }

        process_query(session_id)

    # sessionResults tier must be tier_1
    result_doc = db.collection("sessionResults").document(session_id).get()
    assert result_doc.exists, f"sessionResults/{session_id} not written"
    result = result_doc.to_dict()
    assert result["tier"] == "tier_1"

    # Session doc must also carry tier_1 (T21.8 write_to_firestore)
    session = db.collection("sessions").document(session_id).get().to_dict()
    assert session["status"] == "done"
    assert session["tier"] == "tier_1"
