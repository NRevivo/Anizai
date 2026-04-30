"""
agent/graph.py — LangGraph compilation for the forecast pipeline (T19.9).

Compiles a partial StateGraph for Sprint 19's linear retrieval path:

    START → claim_session → query_understand → build_embedding
          → vault_query → synthesize → END

Sprint 19 deliberate omissions (filled in by later sprints):
    - Conditional clarification edge from query_understand (Sprint 21+ when
      a clarification node exists to route to). The `awaiting_clarification`
      flag set by query_understand is logged but not branched on.
    - Reactive-search loop (sufficiency_check → vault_query_2 → reactive_search
      → rate_evidence). Sprint 22.
    - `write_to_firestore` Node 7 (Sprint 20+). Until then the runner
      (T19.11) does the sessionResults / status writes after `graph.invoke()`
      returns.

Why module-level singleton compile:
    StateGraph compilation is cheap (<10ms) but does graph validation
    (cycle/edge checks). Doing it once at import time means the worker
    process surfaces a malformed graph at startup rather than on the first
    request. Tests introspect the uncompiled builder via `_build_graph()`.

Why claim_session is inside the graph (not in the runner):
    Spec §8.3.2 lists it as Node 0. Keeping it inside the graph means the
    full lifecycle is one introspectable LangGraph object — useful for
    tracing and for the eventual reactive-search loop that may want to
    revisit the claim state. T19.11 will replace `process_query.py` with a
    thin runner that just calls `graph.invoke({"session_id": query_doc_id})`.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.3.2 (Graph Topology)
"""

from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from agent.nodes import (
    build_embedding,
    claim_session,
    query_understand,
    synthesize,
    vault_query,
)
from agent.state import ForecastState

logger = logging.getLogger(__name__)


# ==========================================================
# Node names — single source of truth for graph wiring + tests
# ==========================================================
NODE_CLAIM_SESSION = "claim_session"
NODE_QUERY_UNDERSTAND = "query_understand"
NODE_BUILD_EMBEDDING = "build_embedding"
NODE_VAULT_QUERY = "vault_query"
NODE_SYNTHESIZE = "synthesize"


# ==========================================================
# Builder
# ==========================================================
def _build_graph() -> StateGraph:
    """
    Construct the uncompiled StateGraph.

    Exposed (private) for tests that want to introspect node/edge structure
    without invoking the full graph. Production code should use the
    module-level `graph` singleton.
    """
    builder = StateGraph(ForecastState)

    builder.add_node(NODE_CLAIM_SESSION, claim_session.run)
    builder.add_node(NODE_QUERY_UNDERSTAND, query_understand.run)
    builder.add_node(NODE_BUILD_EMBEDDING, build_embedding.run)
    builder.add_node(NODE_VAULT_QUERY, vault_query.run)
    builder.add_node(NODE_SYNTHESIZE, synthesize.run)

    builder.add_edge(START, NODE_CLAIM_SESSION)
    builder.add_edge(NODE_CLAIM_SESSION, NODE_QUERY_UNDERSTAND)
    builder.add_edge(NODE_QUERY_UNDERSTAND, NODE_BUILD_EMBEDDING)
    builder.add_edge(NODE_BUILD_EMBEDDING, NODE_VAULT_QUERY)
    builder.add_edge(NODE_VAULT_QUERY, NODE_SYNTHESIZE)
    builder.add_edge(NODE_SYNTHESIZE, END)

    return builder


# ==========================================================
# Compiled singleton
# ==========================================================
graph = _build_graph().compile()
