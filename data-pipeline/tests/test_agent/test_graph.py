"""
Gate 1 smoke tests for `agent/graph.py` (T19.9).

Strategy: structural introspection only. We assert the graph's shape — node
membership, edge wiring, entry/exit — without invoking it. End-to-end
execution with all real nodes is Gate 2 (T19.13).

Coverage:
- compiled `graph` singleton imports without raising
- `_build_graph()` returns a StateGraph with all 5 expected nodes
- entry edge: START → claim_session
- exit edge: synthesize → END
- linear sequence between nodes (no surprise branches)
- node node-name constants exported from the module are used in the graph
"""

from __future__ import annotations

from langgraph.graph import END, START

from agent import graph as graph_module


# ==========================================================
# Compile / import
# ==========================================================
def test_compiled_graph_singleton_exists():
    """
    Importing the module compiles the graph at module load. If validation
    failed (cycle, missing edge, dangling node), the import would raise.
    """
    assert graph_module.graph is not None


def test_build_graph_returns_uncompiled_state_graph():
    from langgraph.graph import StateGraph

    builder = graph_module._build_graph()
    assert isinstance(builder, StateGraph)


# ==========================================================
# Node membership
# ==========================================================
def test_graph_registers_all_five_nodes():
    builder = graph_module._build_graph()

    expected = {
        graph_module.NODE_CLAIM_SESSION,
        graph_module.NODE_QUERY_UNDERSTAND,
        graph_module.NODE_BUILD_EMBEDDING,
        graph_module.NODE_VAULT_QUERY,
        graph_module.NODE_SYNTHESIZE,
    }
    assert set(builder.nodes.keys()) == expected


def test_node_name_constants_match_string_values():
    """
    The node-name constants are the wire-level identifiers — if anyone
    renames them, the graph still compiles but the names appearing in
    Firestore agentEvents drift. Pin them.
    """
    assert graph_module.NODE_CLAIM_SESSION == "claim_session"
    assert graph_module.NODE_QUERY_UNDERSTAND == "query_understand"
    assert graph_module.NODE_BUILD_EMBEDDING == "build_embedding"
    assert graph_module.NODE_VAULT_QUERY == "vault_query"
    assert graph_module.NODE_SYNTHESIZE == "synthesize"


# ==========================================================
# Edge wiring
# ==========================================================
def test_entry_edge_start_to_claim_session():
    builder = graph_module._build_graph()
    assert (START, graph_module.NODE_CLAIM_SESSION) in builder.edges


def test_exit_edge_synthesize_to_end():
    builder = graph_module._build_graph()
    assert (graph_module.NODE_SYNTHESIZE, END) in builder.edges


def test_linear_sequence_between_nodes():
    """
    Sprint 19 graph is strictly linear — no branches, no parallel fan-out
    at the graph level (vault_query does its own threadpool internally,
    but that's invisible to LangGraph). If a future sprint adds a
    conditional edge here without thinking, this test will flag it.
    """
    builder = graph_module._build_graph()

    expected_edges = {
        (START, graph_module.NODE_CLAIM_SESSION),
        (graph_module.NODE_CLAIM_SESSION, graph_module.NODE_QUERY_UNDERSTAND),
        (graph_module.NODE_QUERY_UNDERSTAND, graph_module.NODE_BUILD_EMBEDDING),
        (graph_module.NODE_BUILD_EMBEDDING, graph_module.NODE_VAULT_QUERY),
        (graph_module.NODE_VAULT_QUERY, graph_module.NODE_SYNTHESIZE),
        (graph_module.NODE_SYNTHESIZE, END),
    }
    assert builder.edges == expected_edges


def test_no_conditional_edges_in_sprint19_graph():
    """
    Regression guard. Spec §8.3.2 has conditional edges (ambiguous?,
    sufficient?), but Sprint 19 deliberately leaves them unwired. When
    Sprint 21 adds the clarification branch, this test should be
    updated/removed — its purpose is to catch a Sprint 19 PR that
    accidentally lands a conditional edge.
    """
    builder = graph_module._build_graph()
    # StateGraph stores conditional edges separately from `.edges`.
    # `.branches` is the dict of conditional routings.
    branches = getattr(builder, "branches", {})
    assert not branches, f"Sprint 19 graph should have no conditional edges, got: {branches}"
