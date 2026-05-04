"""
Gate 1 smoke tests for `agent/graph.py` (T20.7).

Strategy: structural introspection only. We assert the graph's shape —
node membership, edge wiring, entry/exit — without invoking it.
End-to-end execution with all real nodes is Gate 2 (T20.9).

Sprint 20 evolution from Sprint 19's 5-node graph:
- T20.7 inserts `rate_evidence` between vault_query and synthesize
- T20.7 appends `write_to_firestore` after synthesize, before END
- The success-path Firestore writes that used to live in
  process_query.py are now Node 7 of the graph

Coverage:
- compiled `graph` singleton imports without raising
- `_build_graph()` returns a StateGraph with all 7 expected nodes
- entry edge: START → claim_session
- exit edge: write_to_firestore → END
- linear sequence between nodes (no surprise branches)
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
def test_graph_registers_all_seven_nodes():
    builder = graph_module._build_graph()

    expected = {
        graph_module.NODE_CLAIM_SESSION,
        graph_module.NODE_QUERY_UNDERSTAND,
        graph_module.NODE_BUILD_EMBEDDING,
        graph_module.NODE_VAULT_QUERY,
        graph_module.NODE_RATE_EVIDENCE,
        graph_module.NODE_SYNTHESIZE,
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
    assert graph_module.NODE_BUILD_EMBEDDING == "build_embedding"
    assert graph_module.NODE_VAULT_QUERY == "vault_query"
    assert graph_module.NODE_RATE_EVIDENCE == "rate_evidence"
    assert graph_module.NODE_SYNTHESIZE == "synthesize"
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


def test_linear_sequence_between_nodes():
    """
    Sprint 20 graph remains strictly linear — no branches, no parallel
    fan-out at the graph level (vault_query does its own threadpool
    internally, but that's invisible to LangGraph). Conditional edges
    (ambiguous?, sufficient?) land in Sprint 21+.
    """
    builder = graph_module._build_graph()

    expected_edges = {
        (START, graph_module.NODE_CLAIM_SESSION),
        (graph_module.NODE_CLAIM_SESSION, graph_module.NODE_QUERY_UNDERSTAND),
        (graph_module.NODE_QUERY_UNDERSTAND, graph_module.NODE_BUILD_EMBEDDING),
        (graph_module.NODE_BUILD_EMBEDDING, graph_module.NODE_VAULT_QUERY),
        (graph_module.NODE_VAULT_QUERY, graph_module.NODE_RATE_EVIDENCE),
        (graph_module.NODE_RATE_EVIDENCE, graph_module.NODE_SYNTHESIZE),
        (graph_module.NODE_SYNTHESIZE, graph_module.NODE_WRITE_TO_FIRESTORE),
        (graph_module.NODE_WRITE_TO_FIRESTORE, END),
    }
    assert builder.edges == expected_edges


def test_no_conditional_edges_in_sprint20_graph():
    """
    Regression guard. Spec §8.3.2 has conditional edges (ambiguous?,
    sufficient?) but Sprint 20 deliberately leaves them unwired. When
    Sprint 21 adds the clarification branch, this test should be
    updated/removed — its purpose is to catch a Sprint 20 PR that
    accidentally lands a conditional edge.
    """
    builder = graph_module._build_graph()
    branches = getattr(builder, "branches", {})
    assert not branches, f"Sprint 20 graph should have no conditional edges, got: {branches}"
