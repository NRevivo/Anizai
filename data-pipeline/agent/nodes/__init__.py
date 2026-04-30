"""
agent/nodes/ — LangGraph node functions for the forecast graph.

Each node is a pure-ish Python function that takes the running
`ForecastState` (typed in `agent/state.py`) and returns a partial dict
that LangGraph merges back into state. Nodes never call each other
directly — communication is via state (agent-design P2 "State is the
contract"). External I/O (OpenAI, vault tools, Firestore) is reached
through injected clients/wrappers so unit tests can mock at the
SDK boundary.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.3.2 (Graph Topology)
"""
