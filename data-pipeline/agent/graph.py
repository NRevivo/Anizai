"""
agent/graph.py — LangGraph compilation for the forecast pipeline.

Sprint 20 T20.7 — extends the Sprint 19 retrieval graph with
`rate_evidence` (between vault_query and synthesize) and
`write_to_firestore` (after synthesize). The full Tier 1 path now
runs end-to-end inside the graph; `process_query.py` (the runner)
shrinks to claim/invoke/cleanup-on-failure only.

Sprint 21 T21.3 — adds the first conditional edge: after query_understand,
the routing function `_route_after_query_understand` branches on
`state.awaiting_clarification`:

    START → claim_session → query_understand
                              ├─ ambiguous? True  → write_clarification → END
                              └─ ambiguous? False → build_embedding
                                                  → vault_query
                                                  → rate_evidence
                                                  → synthesize
                                                  → write_to_firestore → END

The `skip_matching_step` path (resume-on-clarify, T21.4/T21.5) is transparent
to the graph: process_query pre-populates state before graph.invoke, and
query_understand's early-return produces `awaiting_clarification=False`
(no second clarification), so the graph takes the normal forecast path.

Sprint 20 deliberate omissions (filled in by later sprints):
    - Reactive-search loop (sufficiency_check → vault_query_2 →
      reactive_search). Sprint 22.
    - agentEvents writes throughout the graph. Sprint 25.

Why module-level singleton compile (unchanged from Sprint 19):
    StateGraph compilation is cheap (<10ms) but does graph validation
    (cycle/edge checks). Doing it once at import time means the worker
    process surfaces a malformed graph at startup rather than on the
    first request. Tests introspect the uncompiled builder via
    `_build_graph()`.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.3.2 (Graph Topology)
    - data-pipeline/docs/agentic_hub_spec.md §8.3.3 (conditional edge logic)
    - data-pipeline/docs/agentic_hub_spec_patch.md Patch 7 (rate_evidence
      between vault_query and synthesize; write_to_firestore as Node 7)
"""

from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from typing import Literal

from agent.nodes import (
    build_embedding,
    claim_session,
    query_understand,
    rate_evidence,
    synthesize,
    vault_query,
    write_clarification,
    write_to_firestore,
)
from agent.state import ForecastState

logger = logging.getLogger(__name__)


# ==========================================================
# Node names — single source of truth for graph wiring + tests
# ==========================================================
NODE_CLAIM_SESSION = "claim_session"
NODE_QUERY_UNDERSTAND = "query_understand"
NODE_WRITE_CLARIFICATION = "write_clarification"
NODE_BUILD_EMBEDDING = "build_embedding"
NODE_VAULT_QUERY = "vault_query"
NODE_RATE_EVIDENCE = "rate_evidence"
NODE_SYNTHESIZE = "synthesize"
NODE_WRITE_TO_FIRESTORE = "write_to_firestore"


# ==========================================================
# Routing function — after query_understand (Sprint 21 T21.3)
# ==========================================================

def _route_after_query_understand(
    state: dict,
) -> Literal["write_clarification", "build_embedding"]:
    """
    Conditional edge routing function executed after query_understand completes.

    Reads:
        state["awaiting_clarification"] — bool set by query_understand

    Returns:
        "write_clarification" if the question is ambiguous (multiple
        Polymarket candidates with tight confidence margin, or too_broad).
        The write_clarification node persists candidates, sets
        session.status='awaiting_clarification', and the graph exits at END.

        "build_embedding" for clear questions (auto-picked Tier 1 or
        Tier 2 freeform). The forecast continues on the normal path.

    This routing function reads only state, no LLM calls (agent-design P3).
    It does NOT handle the skip_matching_step/resume-on-clarify path
    specially — when process_query pre-populates state for a resume run,
    query_understand returns awaiting_clarification=False and this function
    routes straight to build_embedding as normal.

    Spec references:
        §8.3.3 (ambiguous routing)
        §8.2.3 (clarification flow)
    """
    if state.get("awaiting_clarification"):
        return NODE_WRITE_CLARIFICATION
    return NODE_BUILD_EMBEDDING


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
    builder.add_node(NODE_WRITE_CLARIFICATION, write_clarification.run)
    builder.add_node(NODE_BUILD_EMBEDDING, build_embedding.run)
    builder.add_node(NODE_VAULT_QUERY, vault_query.run)
    builder.add_node(NODE_RATE_EVIDENCE, rate_evidence.run)
    builder.add_node(NODE_SYNTHESIZE, synthesize.run)
    builder.add_node(NODE_WRITE_TO_FIRESTORE, write_to_firestore.run)

    builder.add_edge(START, NODE_CLAIM_SESSION)
    builder.add_edge(NODE_CLAIM_SESSION, NODE_QUERY_UNDERSTAND)
    # Sprint 21 T21.3: first conditional edge — ambiguous? routes to
    # write_clarification (→ END); clear questions continue to build_embedding.
    builder.add_conditional_edges(
        NODE_QUERY_UNDERSTAND,
        _route_after_query_understand,
        {
            NODE_WRITE_CLARIFICATION: NODE_WRITE_CLARIFICATION,
            NODE_BUILD_EMBEDDING: NODE_BUILD_EMBEDDING,
        },
    )
    builder.add_edge(NODE_WRITE_CLARIFICATION, END)
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
