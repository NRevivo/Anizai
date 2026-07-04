"""
agent/followup/nodes/ — the three nodes of the follow-up subgraph (Sprint 24).

    load_context → answer_from_context → write_message

Each module exposes a single `run(state)` entry point (the same wiring
convention the main graph uses in `agent/graph.py`), reads/writes the shared
`FollowupState` (agent/followup/state.py), and never calls another node
directly. No `agentEvents` emission here — that is Sprint 25 (T25.7).
"""
