"""
Gate 2 subgraph integration tests for `agent.graph` (T19.13 + Sprint 20
T20.7/T20.9 extensions).

Runs the compiled LangGraph end-to-end with mocks at the system
boundaries:

    - agent.firestore_client.{claim_query, update_session_status}
        — for claim_session
    - agent.nodes.query_understand._get_default_client
        — for query_understand (OpenAI chat.completions)
    - agent.nodes.build_embedding._get_default_client
        — for build_embedding (OpenAI embeddings)
    - agent.agents.{researcher, pulse_analyst, market_bridge}.run
        — for vault_query
    - agent.nodes.rate_evidence._get_default_client
        — for rate_evidence (gpt-4o-mini batch rating; Sprint 20 T20.1)
    - agent.nodes.synthesize._get_default_client
        — for synthesize (gpt-4o synthesis; Sprint 20 T20.3)
    - agent.firestore_client.{write_evidence_batch,
        write_prediction_series, write_sentiment_time_series,
        write_session_result, update_query_status}
        — for write_to_firestore (Sprint 20 T20.5)

Gate 1 unit tests already cover each node's internal logic. Gate 2 here
proves the wiring: state field names line up between nodes, ordering
matches the spec, exceptions propagate through `graph.invoke()`
cleanly, and the full Tier 1 graph end-to-end produces the §8.7.2
SessionResult AND triggers the persistence side effects.

Real-OpenAI / real-Firestore fallback is explicitly out of scope —
that's a separate gate (Gate 3 / E2E, T20.10/T20.11).

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.3.2 (Graph Topology)
    - data-pipeline/docs/agentic_hub_spec.md §8.7.2 (SessionResult schema)
    - data-pipeline/docs/agentic_hub_spec.md §9.3 (Gate 2 contract)
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.errors import AgentProcessingError, SessionClaimRaceLostError
from agent.graph import graph


# ==========================================================
# Fake response builders — mirror Gate 1 helpers
# ==========================================================
def _claim_payload(
    session_id="s1", question="Will the Fed cut rates in 2026?", user_id="u1"
) -> dict:
    return {
        "queryId": session_id,
        "sessionId": session_id,
        "userId": user_id,
        "question": question,
        "status": "pending",
        "createdAt": MagicMock(),
        "claimedAt": None,
        "claimedBy": None,
    }


def _make_candidate(
    *,
    confidence: float = 0.9,
    intent: str = "forecast",
    domain: str = "macro",
    entities: list[str] | None = None,
    polymarket_search_terms: list[str] | None = None,
    has_market_question_intent: bool = True,
    too_broad: bool = False,
    rejected: bool = False,
) -> dict:
    return {
        "intent": intent,
        "domain": domain,
        "entities": entities or ["Federal Reserve"],
        "polymarket_search_terms": polymarket_search_terms,
        "has_market_question_intent": has_market_question_intent,
        "confidence": confidence,
        "too_broad": too_broad,
        "rejected": rejected,
    }


def _make_chat_response(*, candidates: list[dict], total_tokens: int = 120):
    """openai>=1.x ChatCompletion shape: choices[0].message.content is a
    JSON string; usage.total_tokens is an int."""
    payload = {"candidates": candidates}
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(payload))
            )
        ],
        usage=SimpleNamespace(total_tokens=total_tokens),
    )


def _make_embedding_response(*, dim: int = 1536, total_tokens: int = 8):
    """openai>=1.x CreateEmbeddingResponse shape."""
    return SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.01] * dim)],
        usage=SimpleNamespace(total_tokens=total_tokens),
    )


def _make_synthesis_response(
    *,
    final_probability: float = 0.7,
    confidence: float = 0.65,
    consensus_score: float = 0.6,
    total_tokens: int = 1500,
):
    """openai>=1.x ChatCompletion shape with a synthesize.run-compatible
    payload. The full SynthesisOutput schema is enforced upstream by
    `agent.prompts.synthesis_lead.RESPONSE_SCHEMA`; here we hand-build a
    minimum-valid object."""
    payload = {
        "final_probability": final_probability,
        "confidence": confidence,
        "consensus_score": consensus_score,
        "bottom_line_answer": "Forecast: likely yes.",
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
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(payload))
            )
        ],
        usage=SimpleNamespace(total_tokens=total_tokens),
    )


# ==========================================================
# Fixtures
# ==========================================================
@pytest.fixture
def mocked_boundaries(mock_reactive_producer):
    """Patch every external boundary the graph reaches: Firestore, both
    OpenAI clients, and the three retrieval agents. Yields a namespace
    with named children so tests can configure each independently.

    Default behaviour (overridable per test):
      - claim_query returns a valid payload
      - update_session_status succeeds
      - query_understand client returns a single high-confidence candidate
        (auto-pick path)
      - build_embedding client returns a 1536-dim embedding
      - all three agents return empty evidence dicts
      - synthesize client returns a minimum-valid SynthesisOutput
        (Sprint 20: synthesize is now LLM-driven; pre-Sprint 20 it was
        stub mode and didn't need a client mock)

    Sprint 20 note: `evidence_trail` is non-existent at vault_query
    output because the agents return empty packages by default —
    rate_evidence's normalize step produces zero items and short-
    circuits without calling its LLM. Synthesize then runs in
    cold-start mode (Example 2 in its prompt). Tests that need
    populated evidence configure the agents to return non-empty
    packages.
    """
    with (
        patch("agent.firestore_client.claim_query") as mock_claim,
        # Both claim_session AND write_to_firestore call this — single
        # patch, single receiver. Tests that need to distinguish the
        # 'claimed'/'running' calls (claim_session) from the 'done'
        # call (write_to_firestore) inspect call_args_list.
        patch("agent.firestore_client.update_session_status") as mock_status,
        patch("agent.firestore_client.update_query_status") as mock_query_status,
        patch("agent.firestore_client.write_session_result") as mock_write_result,
        patch("agent.firestore_client.write_evidence_batch") as mock_write_evidence,
        patch("agent.firestore_client.write_prediction_series") as mock_write_prediction,
        patch("agent.firestore_client.write_sentiment_time_series") as mock_write_sentiment,
        patch(
            "agent.nodes.query_understand._get_default_client"
        ) as mock_qu_factory,
        patch(
            "agent.nodes.build_embedding._get_default_client"
        ) as mock_emb_factory,
        patch(
            "agent.nodes.rate_evidence._get_default_client"
        ) as mock_rate_factory,
        patch(
            "agent.nodes.synthesize._get_default_client"
        ) as mock_synth_factory,
        patch(
            "agent.nodes.generate_suggested_actions._get_default_client"
        ) as mock_sa_factory,
        patch("agent.agents.researcher.run") as mock_researcher,
        patch("agent.agents.pulse_analyst.run") as mock_pulse,
        patch("agent.agents.market_bridge.run") as mock_market,
    ):
        # Defaults — happy path.
        # G1 fix: sessionId in the claimed payload must match the query_doc_id
        # passed to graph.invoke (both "doc1") so claim_session treats it as a
        # first-time query (not resume). In production, first-time forecastQueries
        # docs have the session id as their doc id — sessionId == doc_id.
        mock_claim.return_value = _claim_payload(session_id="doc1")
        mock_status.return_value = None

        qu_client = MagicMock()
        qu_client.chat.completions.create.return_value = _make_chat_response(
            candidates=[_make_candidate(confidence=0.95)]
        )
        mock_qu_factory.return_value = qu_client

        emb_client = MagicMock()
        emb_client.embeddings.create.return_value = _make_embedding_response()
        mock_emb_factory.return_value = emb_client

        # rate_evidence client — only invoked when there's evidence to
        # rate; default agent packages are empty so this typically
        # never runs. Provided so tests with non-empty evidence work
        # without per-test setup.
        rate_client = MagicMock()
        rate_client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=json.dumps({"ratings": []}))
            )],
            usage=SimpleNamespace(total_tokens=50),
        )
        mock_rate_factory.return_value = rate_client

        synth_client = MagicMock()
        synth_client.chat.completions.create.return_value = _make_synthesis_response()
        mock_synth_factory.return_value = synth_client

        # Sprint 25: generate_suggested_actions (Node 6.5) — a valid 3-action
        # response so the node runs its success path hermetically.
        sa_client = MagicMock()
        sa_client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                "actions": [
                    {"label": "Why so confident?", "prompt": "What drives the confidence?"},
                    {"label": "Strongest driver", "prompt": "Which evidence mattered most?"},
                    {"label": "Compare to the market", "prompt": "How does this compare to the market?"},
                ]
            })))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        )
        mock_sa_factory.return_value = sa_client

        # write_to_firestore helper return values (counts of items written)
        mock_write_evidence.return_value = 0
        mock_write_prediction.return_value = 0
        mock_write_sentiment.return_value = 0
        mock_write_result.return_value = None
        mock_query_status.return_value = None

        mock_researcher.return_value = {
            "articles": [],
            "source_diversity": {},
            "recency_range": None,
            "empty": True,
        }
        mock_pulse.return_value = {
            "market_consensus": [],
            "community_discussion": [],
            "overall_sentiment": 0.0,
            "empty": True,
        }
        mock_market.return_value = {
            "polymarket": None,
            "linked_sources": [],
            "fred_anomalies": [],
            "google_trends": [],
            "empty": True,
        }

        yield SimpleNamespace(
            claim=mock_claim,
            status=mock_status,           # all session-status writes (3 on happy path)
            query_status=mock_query_status,  # forecastQueries 'done' from write_to_firestore
            qu_client=qu_client,
            emb_client=emb_client,
            rate_client=rate_client,
            synth_client=synth_client,
            researcher=mock_researcher,
            pulse=mock_pulse,
            market=mock_market,
            write_evidence=mock_write_evidence,
            write_prediction=mock_write_prediction,
            write_sentiment=mock_write_sentiment,
            write_result=mock_write_result,
        )


# ==========================================================
# 1. Happy path — synthesis_result has §8.7.2 shape in final state
# ==========================================================
def test_invoke_happy_path_produces_8_7_2_synthesis_result(mocked_boundaries):
    """Full graph run produces a synthesis_result in final state matching
    the §8.7.2 shape that synthesize.run() builds. Field-level pinning is
    Gate 1's job (test_synthesize.py); here we just confirm the wiring:
    state flows through every node, and synthesize's output is in the
    final merged state."""
    from agent.nodes import synthesize  # local import to avoid module-level coupling

    final = graph.invoke({"session_id": "doc1"})

    assert "synthesis_result" in final
    result = final["synthesis_result"]
    # Sentinel fields proving synthesize.run actually executed (vs.
    # short-circuiting somewhere upstream). Values come from the
    # default _make_synthesis_response in the fixture.
    assert result["finalProbability"] == 0.7
    assert result["confidence"] == 0.65
    # mocked_boundaries fixture returns polymarket=None → tier inferred as "tier_2"
    assert result["tier"] == "tier_2"
    assert result["agentVersion"] == synthesize.AGENT_VERSION


# ==========================================================
# 2. Happy path — raw_question and user_id propagate through state
# ==========================================================
def test_invoke_propagates_question_and_user_through_state(mocked_boundaries):
    """claim_session pulls question + userId off the claim payload into
    state. By the time control reaches synthesize, raw_question is what
    embeds into the summaryMarkdown. user_id must also reach final state
    for write_to_firestore in Sprint 20+."""
    mocked_boundaries.claim.return_value = _claim_payload(
        session_id="s99",
        question="Will BTC hit 100k by EOY 2026?",
        user_id="user-abc",
    )

    final = graph.invoke({"session_id": "s99"})

    assert final["session_id"] == "s99"
    assert final["raw_question"] == "Will BTC hit 100k by EOY 2026?"
    assert final["user_id"] == "user-abc"
    # synthesize forwards raw_question into the LLM user message (Sprint
    # 20 onward — Sprint 19 stub embedded the question into
    # summaryMarkdown directly; Sprint 20 lets the model produce
    # summaryMarkdown so the question→prose path is asserted at the
    # call boundary instead).
    user_msg = (
        mocked_boundaries.synth_client.chat.completions.create.call_args
        .kwargs["messages"][1]["content"]
    )
    assert "Will BTC hit 100k by EOY 2026?" in user_msg


# ==========================================================
# 3. Happy path — every boundary is hit, in dependency order
# ==========================================================
def test_invoke_calls_every_boundary_node_in_order(mocked_boundaries):
    """Every external boundary mock is hit exactly once on a happy run.
    claim is the only one we can pin to a strict ordering against
    others (it must precede everything else); the three agents fan out
    in parallel inside vault_query so their relative order is
    intentionally unconstrained."""
    graph.invoke({"session_id": "doc1"})

    mocked_boundaries.claim.assert_called_once()
    # update_session_status fires THREE times on a happy run:
    # claim_session writes ('claimed', 'running'); write_to_firestore
    # writes ('done'). Pin the count and the per-call args.
    assert mocked_boundaries.status.call_count == 3
    statuses_written = [c.args for c in mocked_boundaries.status.call_args_list]
    assert ("doc1", "claimed") in statuses_written
    assert ("doc1", "running") in statuses_written
    assert ("doc1", "done") in statuses_written
    mocked_boundaries.qu_client.chat.completions.create.assert_called_once()
    mocked_boundaries.emb_client.embeddings.create.assert_called_once()
    mocked_boundaries.researcher.assert_called_once()
    mocked_boundaries.pulse.assert_called_once()
    mocked_boundaries.market.assert_called_once()
    # rate_evidence's LLM client is only invoked when there's evidence
    # to rate; default packages are empty so it short-circuits without
    # an LLM call. Synthesize ALWAYS runs (cold-start handles empty).
    mocked_boundaries.synth_client.chat.completions.create.assert_called_once()
    # write_to_firestore (Sprint 20 T20.5) — full persistence sequence
    mocked_boundaries.write_evidence.assert_called_once()
    mocked_boundaries.write_prediction.assert_called_once()
    mocked_boundaries.write_sentiment.assert_called_once()
    mocked_boundaries.write_result.assert_called_once()
    mocked_boundaries.query_status.assert_called_once_with("doc1", "done")


# ==========================================================
# 4. Happy path — embedding flows from build_embedding to vault_query
# ==========================================================
def test_invoke_threads_embedding_into_retrieval_agents(mocked_boundaries):
    """build_embedding writes query_embedding into state; vault_query
    forwards it as the first positional arg to researcher and pulse_analyst.
    This is the load-bearing wire between Node 2 and Node 3 — pin that
    the same 1536-dim list shows up at both ends."""
    distinctive_embedding = [0.42] * 1536
    mocked_boundaries.emb_client.embeddings.create.return_value = SimpleNamespace(
        data=[SimpleNamespace(embedding=distinctive_embedding)],
        usage=SimpleNamespace(total_tokens=8),
    )

    final = graph.invoke({"session_id": "doc1"})

    assert final["query_embedding"] == distinctive_embedding
    # Researcher and pulse_analyst both get the embedding as positional[0]
    assert mocked_boundaries.researcher.call_args.args[0] == distinctive_embedding
    assert mocked_boundaries.pulse.call_args.args[0] == distinctive_embedding


# ==========================================================
# 5. Happy path — entities flow from query_understand to market_bridge
# ==========================================================
def test_invoke_threads_entities_into_market_bridge(mocked_boundaries):
    """structured_intent.entities must reach market_bridge as a kwarg.
    Pin both the field-name (entities) and content propagation."""
    mocked_boundaries.qu_client.chat.completions.create.return_value = (
        _make_chat_response(
            candidates=[
                _make_candidate(
                    confidence=0.95,
                    entities=["Federal Reserve", "FOMC", "Jerome Powell"],
                )
            ]
        )
    )

    graph.invoke({"session_id": "doc1"})

    market_kwargs = mocked_boundaries.market.call_args.kwargs
    assert market_kwargs["entities"] == [
        "Federal Reserve", "FOMC", "Jerome Powell",
    ]
    # KG-PHASE8-12 regression guard: polymarket_slug stays None in Sprint 19
    assert market_kwargs["polymarket_slug"] is None


# ==========================================================
# 6. Race lost — graph.invoke surfaces SessionClaimRaceLostError
# ==========================================================
def test_invoke_surfaces_race_lost_as_typed_exception(mocked_boundaries):
    """When claim_query returns None, claim_session raises
    SessionClaimRaceLostError. graph.invoke must let it propagate through
    the LangGraph machinery untouched so the runner can match on the
    typed subclass and quiet-skip. No downstream nodes execute."""
    mocked_boundaries.claim.return_value = None

    with pytest.raises(SessionClaimRaceLostError):
        graph.invoke({"session_id": "doc1"})

    # No status writes (claim_session raises before reaching them)
    mocked_boundaries.status.assert_not_called()
    # No downstream node ran
    mocked_boundaries.qu_client.chat.completions.create.assert_not_called()
    mocked_boundaries.emb_client.embeddings.create.assert_not_called()
    mocked_boundaries.researcher.assert_not_called()
    mocked_boundaries.pulse.assert_not_called()
    mocked_boundaries.market.assert_not_called()
    mocked_boundaries.synth_client.chat.completions.create.assert_not_called()
    # write_to_firestore never reached
    mocked_boundaries.write_evidence.assert_not_called()
    mocked_boundaries.write_result.assert_not_called()
    mocked_boundaries.query_status.assert_not_called()


# ==========================================================
# 7. Per-node failure surfaces — query_understand SDK error
# ==========================================================
def test_invoke_surfaces_query_understand_sdk_failure(mocked_boundaries):
    """If the OpenAI chat.completions call raises, query_understand wraps
    it in AgentProcessingError. graph.invoke must surface that
    AgentProcessingError so the runner's _mark_failed catches it.
    Downstream nodes must not run."""
    mocked_boundaries.qu_client.chat.completions.create.side_effect = (
        RuntimeError("openai 503")
    )

    with pytest.raises(AgentProcessingError) as exc_info:
        graph.invoke({"session_id": "doc1"})

    assert "OpenAI call failed" in str(exc_info.value)
    # Downstream nodes never ran
    mocked_boundaries.emb_client.embeddings.create.assert_not_called()
    mocked_boundaries.researcher.assert_not_called()


# ==========================================================
# 8. Per-node failure surfaces — embedding dim mismatch
# ==========================================================
def test_invoke_surfaces_embedding_dim_mismatch(mocked_boundaries):
    """If OPENAI_EMBEDDING_MODEL gets swapped to a model with the wrong
    dimension (e.g., text-embedding-3-large 3072), build_embedding raises
    AgentProcessingError before any vault query happens. This is the
    OQ-3 strict-dim guard reaching graph level. vault_query must NOT run."""
    mocked_boundaries.emb_client.embeddings.create.return_value = (
        _make_embedding_response(dim=3072)
    )

    with pytest.raises(AgentProcessingError) as exc_info:
        graph.invoke({"session_id": "doc1"})

    assert "expected 1536-dim" in str(exc_info.value)
    mocked_boundaries.researcher.assert_not_called()
    mocked_boundaries.pulse.assert_not_called()
    mocked_boundaries.market.assert_not_called()


# ==========================================================
# 9. Per-node failure surfaces — retrieval agent failure
# ==========================================================
def test_invoke_surfaces_retrieval_agent_failure(mocked_boundaries):
    """If any of the three retrieval agents raises, vault_query wraps it
    in AgentProcessingError (Sprint 19 fail-fast model — no per-agent
    isolation; T26.1 introduces partial-results semantics). graph.invoke
    must surface that. synthesize must NOT run."""
    mocked_boundaries.researcher.side_effect = RuntimeError("vault down")

    with pytest.raises(AgentProcessingError) as exc_info:
        graph.invoke({"session_id": "doc1"})

    assert "researcher failed" in str(exc_info.value)
    # Synthesize and write_to_firestore never ran. We can't introspect
    # final state on an exception, but the LLM and persistence mocks
    # tell us nothing fired downstream of vault_query. status was
    # called for claim_session's 'claimed'/'running' but not 'done'.
    mocked_boundaries.synth_client.chat.completions.create.assert_not_called()
    mocked_boundaries.write_evidence.assert_not_called()
    mocked_boundaries.write_result.assert_not_called()
    mocked_boundaries.query_status.assert_not_called()
    statuses_written = [c.args for c in mocked_boundaries.status.call_args_list]
    assert ("doc1", "done") not in statuses_written


# ==========================================================
# 10. Sprint 21 T21.3 — ambiguous questions route to write_clarification
# ==========================================================
def test_invoke_routes_ambiguous_to_write_clarification(mocked_boundaries):
    """Sprint 21: when query_understand sets awaiting_clarification=True,
    the graph routes to write_clarification and terminates at END without
    running vault_query, synthesize, or write_to_firestore.

    Verified behavior:
    - awaiting_clarification=True in final state
    - synthesis_result NOT in final state (graph ended early)
    - vault agents NOT called (no evidence retrieval)
    - write_to_firestore side effects NOT triggered (no 'done' status)
    - update_session_status called with 'awaiting_clarification'
    """
    mocked_boundaries.qu_client.chat.completions.create.return_value = (
        _make_chat_response(
            candidates=[
                _make_candidate(confidence=0.6),  # below 0.75 auto-pick
                _make_candidate(confidence=0.55),
            ]
        )
    )

    final = graph.invoke({"session_id": "doc1"})

    # Graph terminates at write_clarification → END (not write_to_firestore → END).
    assert final["awaiting_clarification"] is True
    assert "synthesis_result" not in final or final.get("synthesis_result") is None

    # Vault agents and Firestore persistence must NOT have run.
    mocked_boundaries.researcher.assert_not_called()
    mocked_boundaries.pulse.assert_not_called()
    mocked_boundaries.market.assert_not_called()
    mocked_boundaries.write_result.assert_not_called()

    # 'done' status must NOT be written; 'awaiting_clarification' must be.
    statuses_written = [c.args[1] for c in mocked_boundaries.status.call_args_list]
    assert "done" not in statuses_written
    assert "awaiting_clarification" in statuses_written


# ==========================================================
# 11. Bundle C T20.9 — full Tier 1 graph end-to-end persistence
# ==========================================================
def test_invoke_persists_full_session_with_correct_session_id(mocked_boundaries):
    """End-to-end Gate 2 for Sprint 20: when a non-default session_id
    flows in via the claim payload, every write_to_firestore-side
    helper must use that exact session_id. This is the headline
    integration assertion for T20.5+T20.7 — proves the session_id
    threads from claim_session through every node into the
    persistence boundary unchanged."""
    mocked_boundaries.claim.return_value = _claim_payload(
        session_id="end-to-end-99",
        question="Will the Fed cut rates?",
        user_id="u-1",
    )

    graph.invoke({"session_id": "end-to-end-99"})

    # Every persistence helper called with session_id from claim payload
    assert mocked_boundaries.write_evidence.call_args.args[0] == "end-to-end-99"
    assert mocked_boundaries.write_prediction.call_args.args[0] == "end-to-end-99"
    assert mocked_boundaries.write_sentiment.call_args.args[0] == "end-to-end-99"
    assert mocked_boundaries.write_result.call_args.args[0] == "end-to-end-99"
    # status writes: 3 calls total (claimed, running, done) all with same session_id
    statuses_written = [c.args for c in mocked_boundaries.status.call_args_list]
    assert ("end-to-end-99", "done") in statuses_written
    mocked_boundaries.query_status.assert_called_once_with("end-to-end-99", "done")


def test_invoke_writes_synthesis_result_payload_to_session_results(mocked_boundaries):
    """The dict written to sessionResults must be the synthesis_result
    the synthesize node produced — pin that the persistence boundary
    doesn't drop or mangle fields between synthesize and Firestore."""
    graph.invoke({"session_id": "doc1"})

    written_session_id, written_payload = mocked_boundaries.write_result.call_args.args
    assert written_session_id == "doc1"
    # Sentinel checks: §8.7.2 fields synthesize.run produces are present.
    # mocked_boundaries fixture returns polymarket=None → tier="tier_2".
    assert "finalProbability" in written_payload
    assert "agentVersion" in written_payload
    assert written_payload["tier"] == "tier_2"


def test_invoke_persistence_failure_propagates(mocked_boundaries):
    """write_to_firestore raising mid-batch must propagate through
    graph.invoke so process_query._mark_failed can clean up. Pin the
    headline failure path the user explicitly required as a Bundle B
    addition."""
    mocked_boundaries.write_evidence.side_effect = RuntimeError(
        "firestore batch commit failed"
    )

    with pytest.raises(RuntimeError, match="firestore batch commit failed"):
        graph.invoke({"session_id": "doc1"})

    # Status='done' transitions never happen on failure. claim_session's
    # 'claimed'/'running' writes did fire before the failure, so we
    # check call_args_list rather than asserting not_called.
    statuses_written = [c.args for c in mocked_boundaries.status.call_args_list]
    assert ("doc1", "done") not in statuses_written
    mocked_boundaries.query_status.assert_not_called()


# ==========================================================
# 12. T20.9 gap closure — populated evidence threads end-to-end
# ==========================================================
def test_invoke_populated_evidence_threads_through_to_subcollection(mocked_boundaries):
    """Closes the Gate 2 gap surfaced during T20.8/T20.9 verification:
    Bundle C's other integration tests use empty agent packages, so
    rate_evidence short-circuits and write_to_firestore writes an empty
    evidence subcollection. None of them exercises the populated path
    where evidence flows researcher → vault_query → rate_evidence (LLM
    call) → synthesize (LLM overlay) → write_to_firestore (subcollection
    write with frontend `type` field).

    A field-name drift between rate_evidence (writes `evidence_trail`)
    and synthesize (reads `evidence_trail`) — or between synthesize
    overlay output and write_to_firestore's expectations — would slip
    past every Gate 1 unit test and only surface when a real session
    has populated evidence. This test pins the populated path."""
    import re

    # 1. Researcher returns one newsapi article. Researcher's pack shape
    # matches what rate_evidence._normalize_researcher consumes.
    mocked_boundaries.researcher.return_value = {
        "articles": [{
            "signal_id": "sig-1",
            "source_platform": "newsapi",
            "publisher": "reuters.com",
            "title": "Powell signals patience on rate cuts",
            "published_at": "2026-05-04T12:00:00+00:00",
            "executive_summary": "Fed Chair urged caution.",
            "key_findings": [],
            "full_text_snippet": "Federal Reserve Chair Jerome Powell told the Senate banking committee...",
            "impact_level": 4,
            "reliability_score": 0.9,
            "sentiment_score": 0.0,
            "similarity": 0.85,
            "evidence_weight": 0.8,
            "canonical_event_id": "",
        }],
        "source_diversity": {"newsapi_count": 1, "arxiv_count": 0, "telegram_count": 0},
        "recency_range": None,
        "empty": False,
    }

    # 2. rate_evidence's client: parse evidence_ids from the user
    # message and return high-relevance ratings for each. evidence_id
    # is generated by rate_evidence (uuid4) so we can't hard-code it.
    evidence_id_pattern = re.compile(r"evidence_id:\s*(\S+)")

    def rate_create(**kwargs):
        body = kwargs["messages"][1]["content"]
        ids = evidence_id_pattern.findall(body)
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=json.dumps({
                    "ratings": [
                        {"evidence_id": eid,
                         "relevance_score": 0.9,
                         "justification": "directly addresses rate-cut question"}
                        for eid in ids
                    ]
                }))
            )],
            usage=SimpleNamespace(total_tokens=200),
        )

    mocked_boundaries.rate_client.chat.completions.create.side_effect = rate_create

    # 3. synthesize's client: parse evidence_ids from its own user
    # message format ([N] evidence_id: <id>) and return a synthesis
    # output that overlays each item as used_in_answer=True with
    # decreasing impact_magnitude.
    def synth_create(**kwargs):
        body = kwargs["messages"][1]["content"]
        ids = evidence_id_pattern.findall(body)
        payload = {
            "final_probability": 0.72,
            "confidence": 0.8,
            "consensus_score": 0.75,
            "bottom_line_answer": "Likely yes.",
            "detailed_explanation": "Powell's testimony supports timing.",
            "summary_markdown": "**Forecast:** likely yes.",
            "market_comparison_insight": "Markets agree.",
            "sentiment_analysis_insight": "Sentiment positive.",
            "evidence_feed_summary": "1 high-relevance item reviewed.",
            "what_i_didnt_find": [],
            "key_factors": [
                {"label": "Powell signals patience",
                 "description": "Fed Chair telegraphed timing.",
                 "weight": 0.5, "direction": "increases",
                 "evidence_ids": ids},
                {"label": "Inflation context",
                 "description": "Recent prints support cut path.",
                 "weight": 0.3, "direction": "increases",
                 "evidence_ids": []},
                {"label": "Hawkish dissent",
                 "description": "Some FOMC members oppose.",
                 "weight": 0.2, "direction": "decreases",
                 "evidence_ids": []},
            ],
            "reasoning_chain": [
                {"step": 1, "title": "Identify question",
                 "description": "Parsed Q2 2026 rate-cut criterion."},
                {"step": 2, "title": "Review evidence",
                 "description": "Evaluated retrieved articles."},
                {"step": 3, "title": "Weigh drivers",
                 "description": "Identified 3 factors."},
                {"step": 4, "title": "Produce forecast",
                 "description": "Calibrated final probability."},
            ],
            "evidence_overlay": [
                {"evidence_id": eid,
                 "used_in_answer": True,
                 "impact_on_forecast": "increases",
                 "impact_magnitude": 0.8 - (i * 0.1)}
                for i, eid in enumerate(ids)
            ],
        }
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
            usage=SimpleNamespace(total_tokens=2000),
        )

    mocked_boundaries.synth_client.chat.completions.create.side_effect = synth_create

    # 4. Run the full graph
    graph.invoke({"session_id": "doc1"})

    # 5. Verify rate_evidence's LLM was called (was short-circuited in
    # all other Gate 2 tests; this one exercises the populated path)
    mocked_boundaries.rate_client.chat.completions.create.assert_called_once()

    # 6. Verify the evidence subcollection write got the rated +
    # overlaid item with the frontend `type` field
    mocked_boundaries.write_evidence.assert_called_once()
    written_session_id, written_items = mocked_boundaries.write_evidence.call_args.args
    assert written_session_id == "doc1"
    assert len(written_items) == 1

    item = written_items[0]
    # Identity from rate_evidence normalization
    assert item["source_type"] == "vault_news"
    assert item["origin"] == "knowledge_vault"
    assert item["title"] == "Powell signals patience on rate cuts"
    # Frontend `type` mapping at the persistence boundary (T20.5)
    assert item["type"] == "news"
    # Rating from rate_evidence's LLM call
    assert item["relevance_score"] == 0.9
    assert "directly addresses" in item["justification"]
    assert item["credibility_tier"] == "tier_2"  # newsapi default mapping
    # Synthesize overlay
    assert item["used_in_answer"] is True
    assert item["impact_on_forecast"] == "increases"
    assert item["impact_magnitude"] == 0.8
    # Post-synthesis deterministic ranking (top 1 of 1 used items)
    assert item["is_key_evidence"] is True
    assert item["rank"] == 1
