"""
agent/followup/ — the follow-up conversation subgraph (Sprint 24).

A structurally-separate LangGraph pipeline that answers a user's chat
follow-up on an already-completed forecast, using ONLY the parent
SessionResult + the top evidence already retrieved during that forecast
(no escalation, no new vault search — the escalation branch is Future
Enhancement 2, see `docs/B_hub/hub_sprints.md` §3).

This package is deliberately isolated from the main forecast graph
(`agent/graph.py` + `agent/nodes/`): the main graph never imports it, so
the follow-up path can evolve without touching the forecast pipeline
(24.15 "without coupling the main graph to the followup package"). The
worker (`agent/worker.py`) starts this package's listener alongside the
main listener; `agent/process_query.py` calls this package's done-guarded
sweep after a successful forecast completes.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.8.3 (revised — follow-up
      answer-from-context path; escalation deferred)
    - data-pipeline/docs/B_hub/plans/sprint24_followups.md (active plan)
"""
