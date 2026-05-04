"""
agent/graph.py — LangGraph compilation for the forecast pipeline.

Sprint 20 T20.7 — extends the Sprint 19 retrieval graph with
`rate_evidence` (between vault_query and synthesize) and
`write_to_firestore` (after synthesize). The full Tier 1 path now
runs end-to-end inside the graph; `process_query.py` (the runner)
shrinks to claim/invoke/cleanup-on-failure only.

    START → claim_session → query_understand → build_embedding
          → vault_query → rate_evidence → synthesize
          → write_to_firestore → END

Sprint 20 deliberate omissions (filled in by later sprints):
    - Conditional clarification edge from query_understand (Sprint 21+
      when a clarification node exists to route to). The
      `awaiting_clarification` flag set by query_understand is logged
      but not branched on.
    - Reactive-search loop (sufficiency_check → vault_query_2 →
      reactive_search). Sprint 22.
    - agentEvents writes throughout the graph. Sprint 25.

Why the success-path Firestore writes moved into the graph (D9):
    Sprint 18/19 had process_query.py do `write_session_result →
    update_session_status('done') → update_query_status('done')` after
    `graph.invoke()` returned. T20.5 introduces a dedicated Node 7
    that does those writes inside the graph. Two reasons:
      1. Single introspectable lifecycle — the full forecast (claim →
         retrieve → rate → synthesize → persist) is one LangGraph
         object, useful for tracing and for the eventual reactive-
         search loop that may want to revisit persistence state.
      2. The runner becomes purely an exception-handling thin wrapper
         (claim race, mark-failed cleanup), which is easier to reason
         about than orchestration plumbing scattered across nodes and
         the runner.

Why module-level singleton compile (unchanged from Sprint 19):
    StateGraph compilation is cheap (<10ms) but does graph validation
    (cycle/edge checks). Doing it once at import time means the worker
    process surfaces a malformed graph at startup rather than on the
    first request. Tests introspect the uncompiled builder via
    `_build_graph()`.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.3.2 (Graph Topology)
    - data-pipeline/docs/agentic_hub_spec_patch.md Patch 7 (rate_evidence
      between vault_query and synthesize; write_to_firestore as Node 7)
"""

from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from agent.nodes import (
    build_embedding,
    claim_session,
    query_understand,
    rate_evidence,
    synthesize,
    vault_query,
    write_to_firestore,
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
NODE_RATE_EVIDENCE = "rate_evidence"
NODE_SYNTHESIZE = "synthesize"
NODE_WRITE_TO_FIRESTORE = "write_to_firestore"


# ==========================================================
# Builder
# ==========================================================
def _build_graph() -> StateGraph:
    """
    Construct the uncompiled StateGraph.

    Exposed (private) for tests that want to introspect node/edge
    structure without invoking the full graph. Production code should
    use the module-level `graph` singleton.
    """
    builder = StateGraph(ForecastState)

    builder.add_node(NODE_CLAIM_SESSION, claim_session.run)
    builder.add_node(NODE_QUERY_UNDERSTAND, query_understand.run)
    builder.add_node(NODE_BUILD_EMBEDDING, build_embedding.run)
    builder.add_node(NODE_VAULT_QUERY, vault_query.run)
    builder.add_node(NODE_RATE_EVIDENCE, rate_evidence.run)
    builder.add_node(NODE_SYNTHESIZE, synthesize.run)
    builder.add_node(NODE_WRITE_TO_FIRESTORE, write_to_firestore.run)

    builder.add_edge(START, NODE_CLAIM_SESSION)
    builder.add_edge(NODE_CLAIM_SESSION, NODE_QUERY_UNDERSTAND)
    builder.add_edge(NODE_QUERY_UNDERSTAND, NODE_BUILD_EMBEDDING)
    builder.add_edge(NODE_BUILD_EMBEDDING, NODE_VAULT_QUERY)
    builder.add_edge(NODE_VAULT_QUERY, NODE_RATE_EVIDENCE)
    builder.add_edge(NODE_RATE_EVIDENCE, NODE_SYNTHESIZE)
    builder.add_edge(NODE_SYNTHESIZE, NODE_WRITE_TO_FIRESTORE)
    builder.add_edge(NODE_WRITE_TO_FIRESTORE, END)

    return builder


# ==========================================================
# Compiled singleton
# ==========================================================
graph = _build_graph().compile()
