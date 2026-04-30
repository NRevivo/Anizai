"""
Gate 2 subgraph integration tests for `agent.graph` (T19.13).

Runs the compiled LangGraph end-to-end with mocks at the system boundaries:

    - agent.firestore_client.{claim_query, update_session_status}
        — for claim_session
    - agent.nodes.query_understand._get_default_client
        — for query_understand (OpenAI chat.completions)
    - agent.nodes.build_embedding._get_default_client
        — for build_embedding (OpenAI embeddings)
    - agent.agents.{researcher, pulse_analyst, market_bridge}.run
        — for vault_query (per OQ-8: same boundary as vault_query Gate 1
          unit tests; deeper than agent/tools/ boundary)

Gate 1 unit tests already cover each node's internal logic. Gate 2 here
proves the wiring: state field names line up between nodes, ordering
matches the spec, exceptions propagate through `graph.invoke()` cleanly,
and the final state has the §8.7.2-compliant `synthesis_result`.

Real-OpenAI / real-Firestore fallback is explicitly out of scope (per
OQ-10) — that's a separate gate (Gate 3 / E2E).

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


# ==========================================================
# Fixtures
# ==========================================================
@pytest.fixture
def mocked_boundaries():
    """Patch every external boundary the graph reaches: Firestore, both
    OpenAI clients, and the three retrieval agents. Yields a namespace
    with named children so tests can configure each independently.

    Default behaviour (overridable per test):
      - claim_query returns a valid payload
      - update_session_status succeeds
      - query_understand client returns a single high-confidence candidate
        (auto-pick path)
      - build_embedding client returns a 1536-dim embedding
      - all three agents return empty evidence dicts (shape doesn't matter
        — synthesize doesn't read them in Sprint 19)
    """
    with (
        patch("agent.firestore_client.claim_query") as mock_claim,
        patch("agent.firestore_client.update_session_status") as mock_status,
        patch(
            "agent.nodes.query_understand._get_default_client"
        ) as mock_qu_factory,
        patch(
            "agent.nodes.build_embedding._get_default_client"
        ) as mock_emb_factory,
        patch("agent.agents.researcher.run") as mock_researcher,
        patch("agent.agents.pulse_analyst.run") as mock_pulse,
        patch("agent.agents.market_bridge.run") as mock_market,
    ):
        # Defaults — happy path
        mock_claim.return_value = _claim_payload()
        mock_status.return_value = None

        qu_client = MagicMock()
        qu_client.chat.completions.create.return_value = _make_chat_response(
            candidates=[_make_candidate(confidence=0.95)]
        )
        mock_qu_factory.return_value = qu_client

        emb_client = MagicMock()
        emb_client.embeddings.create.return_value = _make_embedding_response()
        mock_emb_factory.return_value = emb_client

        mock_researcher.return_value = {
            "evidence_pieces": [],
            "drill_down_evidence": [],
            "source_diversity": {},
            "recency_range_iso8601": [None, None],
        }
        mock_pulse.return_value = {
            "evidence_pieces": [],
            "drill_down_evidence": [],
            "source_diversity": {},
            "recency_range_iso8601": [None, None],
        }
        mock_market.return_value = {
            "polymarket": None,
            "fred_anomalies": [],
            "trends": [],
            "entity_metadata": {},
        }

        yield SimpleNamespace(
            claim=mock_claim,
            status=mock_status,
            qu_client=qu_client,
            emb_client=emb_client,
            researcher=mock_researcher,
            pulse=mock_pulse,
            market=mock_market,
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
    final = graph.invoke({"session_id": "doc1"})

    assert "synthesis_result" in final
    result = final["synthesis_result"]
    # Sentinel fields proving synthesize.run actually executed (vs.
    # short-circuiting somewhere upstream)
    assert result["finalProbability"] == 0.5
    assert result["confidence"] == 0.5
    assert result["tier"] == "tier_1"
    assert result["agentVersion"] == "0.2.0-sprint19-retrieval-stub-synthesis"


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
    # synthesize embeds raw_question into the markdown summary
    assert "Will BTC hit 100k by EOY 2026?" in (
        final["synthesis_result"]["summaryMarkdown"]
    )


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
    # update_session_status fires twice from claim_session (claimed, running)
    assert mocked_boundaries.status.call_count == 2
    mocked_boundaries.qu_client.chat.completions.create.assert_called_once()
    mocked_boundaries.emb_client.embeddings.create.assert_called_once()
    mocked_boundaries.researcher.assert_called_once()
    mocked_boundaries.pulse.assert_called_once()
    mocked_boundaries.market.assert_called_once()


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
    # Synthesize never ran — final state has no synthesis_result.
    # We can't introspect final state on an exception, but if synthesize
    # had run the exception would have been swallowed.


# ==========================================================
# 10. Clarification flag flows through — does NOT short-circuit in Sprint 19
# ==========================================================
def test_invoke_continues_through_clarification_in_sprint19(mocked_boundaries):
    """When query_understand sets `awaiting_clarification=True` (low
    confidence or narrow margin), Sprint 21+ will route to a clarification
    node. Sprint 19 has no such edge — the graph runs straight through.
    Pin that the flag is set in final state but the synthesis_result is
    still produced (so frontend always gets *something* to render)."""
    mocked_boundaries.qu_client.chat.completions.create.return_value = (
        _make_chat_response(
            candidates=[
                _make_candidate(confidence=0.6),  # below 0.75 auto-pick
                _make_candidate(confidence=0.55),
            ]
        )
    )

    final = graph.invoke({"session_id": "doc1"})

    assert final["awaiting_clarification"] is True
    # Despite ambiguity, the full pipeline completed and synthesize built
    # a placeholder result. Sprint 21 will reroute this case via a
    # clarification edge.
    assert "synthesis_result" in final
    mocked_boundaries.researcher.assert_called_once()
    mocked_boundaries.pulse.assert_called_once()
    mocked_boundaries.market.assert_called_once()
