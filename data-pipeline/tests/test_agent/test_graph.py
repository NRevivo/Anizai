"""
Gate 1 smoke tests for `agent/graph.py` (T20.7, updated T21.3).

Strategy: structural introspection only. We assert the graph's shape —
node membership, edge wiring, entry/exit — without invoking it.
End-to-end execution with all real nodes is Gate 2 (T20.9 / T21.10).

Sprint 20 evolution from Sprint 19's 5-node graph:
- T20.7 inserts `rate_evidence` between vault_query and synthesize
- T20.7 appends `write_to_firestore` after synthesize, before END
- The success-path Firestore writes that used to live in
  process_query.py are now Node 7 of the graph

Sprint 21 evolution from Sprint 20's linear graph:
- T21.3 adds `write_clarification` as Node 2.5 (ambiguous path)
- T21.3 adds the first conditional edge: query_understand →
  (ambiguous?) → write_clarification → END
                 (clear)    → build_embedding → ... → END

Coverage:
- compiled `graph` singleton imports without raising
- `_build_graph()` returns a StateGraph with all 8 expected nodes
- entry edge: START → claim_session
- exit edges: write_to_firestore → END AND write_clarification → END
- conditional edge from query_understand (Sprint 21+)
- node-name constants exported from the module match the registered
  node names
"""

from __future__ import annotations

from langgraph.graph import END, START

from agent import graph as graph_module


# ==========================================================
# Compile / import
# ==========================================================
def test_compiled_graph_singleton_exists():
    """
    Importing the module compiles the graph at module load. If
    validation failed (cycle, missing edge, dangling node), the import
    would raise.
    """
    assert graph_module.graph is not None


def test_build_graph_returns_uncompiled_state_graph():
    from langgraph.graph import StateGraph

    builder = graph_module._build_graph()
    assert isinstance(builder, StateGraph)


# ==========================================================
# Node membership
# ==========================================================
def test_graph_registers_all_nodes():
    """Sprint 23.5 T23.5.5 wires the reactive/sufficiency path: adds
    sufficiency_check (after vault_query) and trigger_reactive_ingestion
    (insufficient branch). Sprint 25 T25.3 adds generate_suggested_actions
    between synthesize and write_to_firestore — 11 nodes total."""
    builder = graph_module._build_graph()

    expected = {
        graph_module.NODE_CLAIM_SESSION,
        graph_module.NODE_QUERY_UNDERSTAND,
        graph_module.NODE_WRITE_CLARIFICATION,
        graph_module.NODE_BUILD_EMBEDDING,
        graph_module.NODE_VAULT_QUERY,
        graph_module.NODE_SUFFICIENCY_CHECK,
        graph_module.NODE_TRIGGER_REACTIVE_INGESTION,
        graph_module.NODE_RATE_EVIDENCE,
        graph_module.NODE_SYNTHESIZE,
        graph_module.NODE_GENERATE_SUGGESTED_ACTIONS,
        graph_module.NODE_WRITE_TO_FIRESTORE,
    }
    assert set(builder.nodes.keys()) == expected


def test_node_name_constants_match_string_values():
    """
    The node-name constants are the wire-level identifiers — if anyone
    renames them, the graph still compiles but the names appearing in
    Firestore agentEvents (Sprint 25) drift. Pin them.
    """
    assert graph_module.NODE_CLAIM_SESSION == "claim_session"
    assert graph_module.NODE_QUERY_UNDERSTAND == "query_understand"
    assert graph_module.NODE_WRITE_CLARIFICATION == "write_clarification"
    assert graph_module.NODE_BUILD_EMBEDDING == "build_embedding"
    assert graph_module.NODE_VAULT_QUERY == "vault_query"
    assert graph_module.NODE_SUFFICIENCY_CHECK == "sufficiency_check"
    assert (
        graph_module.NODE_TRIGGER_REACTIVE_INGESTION
        == "trigger_reactive_ingestion"
    )
    assert graph_module.NODE_RATE_EVIDENCE == "rate_evidence"
    assert graph_module.NODE_SYNTHESIZE == "synthesize"
    assert (
        graph_module.NODE_GENERATE_SUGGESTED_ACTIONS
        == "generate_suggested_actions"
    )
    assert graph_module.NODE_WRITE_TO_FIRESTORE == "write_to_firestore"


# ==========================================================
# Edge wiring
# ==========================================================
def test_entry_edge_start_to_claim_session():
    builder = graph_module._build_graph()
    assert (START, graph_module.NODE_CLAIM_SESSION) in builder.edges


def test_exit_edge_write_to_firestore_to_end():
    """Sprint 20: the terminal node is write_to_firestore, not
    synthesize. Pin this so a future sprint that adds another
    post-synthesis node doesn't accidentally leave write_to_firestore
    mid-graph (the persistence step must always be last)."""
    builder = graph_module._build_graph()
    assert (graph_module.NODE_WRITE_TO_FIRESTORE, END) in builder.edges


def test_static_edges_in_graph():
    """
    Straight (non-conditional) edges after the Sprint 23.5 reactive wiring:
    - claim_session → query_understand (always)
    - write_clarification → END   (ambiguous path terminal)
    - build_embedding → vault_query → sufficiency_check
    - trigger_reactive_ingestion → rate_evidence   (trigger-and-forget rejoin)
    - rate_evidence → synthesize → generate_suggested_actions →
      write_to_firestore → END   (Sprint 25 T25.3 inserts Node 6.5)

    The conditional edges (query_understand ambiguous?, sufficiency_check
    sufficient?) are verified separately below. The edge out of
    sufficiency_check is conditional, so it is NOT in this straight set.
    """
    builder = graph_module._build_graph()

    expected_straight_edges = {
        (START, graph_module.NODE_CLAIM_SESSION),
        (graph_module.NODE_CLAIM_SESSION, graph_module.NODE_QUERY_UNDERSTAND),
        (graph_module.NODE_WRITE_CLARIFICATION, END),
        (graph_module.NODE_BUILD_EMBEDDING, graph_module.NODE_VAULT_QUERY),
        (graph_module.NODE_VAULT_QUERY, graph_module.NODE_SUFFICIENCY_CHECK),
        (
            graph_module.NODE_TRIGGER_REACTIVE_INGESTION,
            graph_module.NODE_RATE_EVIDENCE,
        ),
        (graph_module.NODE_RATE_EVIDENCE, graph_module.NODE_SYNTHESIZE),
        (
            graph_module.NODE_SYNTHESIZE,
            graph_module.NODE_GENERATE_SUGGESTED_ACTIONS,
        ),
        (
            graph_module.NODE_GENERATE_SUGGESTED_ACTIONS,
            graph_module.NODE_WRITE_TO_FIRESTORE,
        ),
        (graph_module.NODE_WRITE_TO_FIRESTORE, END),
    }
    assert builder.edges == expected_straight_edges


def test_conditional_edge_from_query_understand_exists():
    """Sprint 21 T21.3: verify the ambiguous? conditional edge is wired."""
    builder = graph_module._build_graph()
    branches = getattr(builder, "branches", {})
    assert graph_module.NODE_QUERY_UNDERSTAND in branches, (
        "Expected a conditional edge on query_understand — _route_after_query_understand"
    )


def test_conditional_edge_from_sufficiency_check_exists():
    """Sprint 23.5 T23.5.5: verify the sufficient? conditional edge is wired."""
    builder = graph_module._build_graph()
    branches = getattr(builder, "branches", {})
    assert graph_module.NODE_SUFFICIENCY_CHECK in branches, (
        "Expected a conditional edge on sufficiency_check — "
        "_route_after_sufficiency"
    )


def test_route_after_sufficiency_sufficient_goes_to_rate_evidence():
    """Sufficient verdict routes straight to rate_evidence."""
    state = {"sufficiency_checks": [{"is_sufficient": True}]}
    assert (
        graph_module._route_after_sufficiency(state)
        == graph_module.NODE_RATE_EVIDENCE
    )


def test_route_after_sufficiency_insufficient_with_budget_triggers():
    """Insufficient + trigger budget available routes to the reactive trigger."""
    state = {
        "sufficiency_checks": [{"is_sufficient": False}],
        "reactive_triggers_emitted": 0,
    }
    assert (
        graph_module._route_after_sufficiency(state)
        == graph_module.NODE_TRIGGER_REACTIVE_INGESTION
    )


def test_route_after_sufficiency_insufficient_budget_spent_skips_trigger():
    """Insufficient but the per-session trigger budget is spent → proceed to
    rate_evidence on available evidence (no second trigger)."""
    from agent.config import settings

    state = {
        "sufficiency_checks": [{"is_sufficient": False}],
        "reactive_triggers_emitted": settings.AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION,
    }
    assert (
        graph_module._route_after_sufficiency(state)
        == graph_module.NODE_RATE_EVIDENCE
    )


def test_routing_function_routes_ambiguous_to_write_clarification():
    """_route_after_query_understand returns write_clarification when ambiguous."""
    result = graph_module._route_after_query_understand({"awaiting_clarification": True})
    assert result == graph_module.NODE_WRITE_CLARIFICATION


def test_routing_function_routes_clear_to_build_embedding():
    """_route_after_query_understand returns build_embedding when not ambiguous."""
    result = graph_module._route_after_query_understand({"awaiting_clarification": False})
    assert result == graph_module.NODE_BUILD_EMBEDDING


def test_routing_function_routes_clear_when_flag_absent():
    """_route_after_query_understand returns build_embedding when flag is absent."""
    result = graph_module._route_after_query_understand({})
    assert result == graph_module.NODE_BUILD_EMBEDDING
