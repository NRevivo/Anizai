"""
agent/followup/nodes/ — the nodes of the follow-up subgraph.

    load_context → answer_from_context → generate_suggested_actions
    → write_message

Each module exposes a single `run(state)` entry point (the same wiring
convention the main graph uses in `agent/graph.py`), reads/writes the shared
`FollowupState` (agent/followup/state.py), and never calls another node
directly. No `agentEvents` emission here — that is Sprint 25 (T25.7).
"""
