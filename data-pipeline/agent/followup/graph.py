"""
agent/followup/graph.py — the follow-up subgraph (Sprint 24, T24.6).

A small, linear LangGraph over `FollowupState`, structurally separate from
the main forecast graph (`agent/graph.py`):

    START → load_context → answer_from_context → write_message → END

No escalation branch (that is Future Enhancement 2) — hence no conditional
edges. Each node exposes `run(state)` and communicates only through
`FollowupState` (hub-principles G2). The compiled graph is a module-level
singleton, built once at import, matching the main graph's convention.

Why a separate compiled graph (not a branch of the forecast graph):
    The follow-up path has its own (smaller) state, its own lifecycle
    (triggered by a chat message, not a forecastQueries doc), and its own
    budget. Keeping it a distinct StateGraph keeps both contracts clean and
    lets the follow-up path evolve without touching the forecast pipeline
    (plan 24.15 "without coupling the main graph to the followup package").

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.8.3 (revised — follow-up path)
    - data-pipeline/docs/B_hub/plans/sprint24_followups.md §4 T24.6
    - .claude/skills/agent-design (graph structure; the follow-up subgraph is
      structurally separate and lives in agent/followup/)
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agent.followup.state import FollowupState
from agent.followup.nodes import answer_from_context, load_context, write_message


# Node names — module-level constants so tests can introspect the graph
# structure by name (mirrors agent/graph.py).
NODE_LOAD_CONTEXT = "load_context"
NODE_ANSWER_FROM_CONTEXT = "answer_from_context"
NODE_WRITE_MESSAGE = "write_message"


# ==========================================================
# Builder
# ==========================================================
def _build_graph() -> StateGraph:
    """
    Construct the uncompiled follow-up StateGraph.

    Exposed (private) for tests that introspect node/edge structure without
    invoking the graph. Production code uses the module-level `graph`
    singleton below.
    """
    builder = StateGraph(FollowupState)

    builder.add_node(NODE_LOAD_CONTEXT, load_context.run)
    builder.add_node(NODE_ANSWER_FROM_CONTEXT, answer_from_context.run)
    builder.add_node(NODE_WRITE_MESSAGE, write_message.run)

    builder.add_edge(START, NODE_LOAD_CONTEXT)
    builder.add_edge(NODE_LOAD_CONTEXT, NODE_ANSWER_FROM_CONTEXT)
    builder.add_edge(NODE_ANSWER_FROM_CONTEXT, NODE_WRITE_MESSAGE)
    builder.add_edge(NODE_WRITE_MESSAGE, END)

    return builder


# ==========================================================
# Compiled singleton
# ==========================================================
graph = _build_graph().compile()
