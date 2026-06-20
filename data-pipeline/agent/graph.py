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

Sprint 23.5 T23.5.5 — wires the reactive/sufficiency path end-to-end
(absorbs the graph-wiring half of the old T26.7). The full post-vault
topology is now:

    vault_query → sufficiency_check
                    ├─ sufficient?   → rate_evidence → synthesize → …
                    └─ insufficient? → trigger_reactive_ingestion
                                       → rate_evidence → …   (trigger-and-forget)

`_route_after_sufficiency` reads the latest `sufficiency_checks` entry plus
the per-session trigger budget and routes; the insufficient branch dispatches
the reactive trigger and then **continues to rate_evidence with whatever
evidence is currently available** — it does not wait for ingestion
(trigger-and-forget). The trigger fires at most once per session
(AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION). This is the locked V1 topology:
a SINGLE sufficiency_check with no second vault query (`vault_query_2`) and
no refine loop (Advisor↔Ron decision record §6 R5; plan §6).

Sprint 20 deliberate omissions (filled in by later sprints):
    - vault_query_2 / reactive_search refine loop (the richer multi-attempt
      rubric in the agent-design skill) — a Future Enhancement, not V1.
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

from agent.config import settings
from agent.nodes import (
    build_embedding,
    claim_session,
    query_understand,
    rate_evidence,
    sufficiency_check,
    synthesize,
    trigger_reactive_ingestion,
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
NODE_SUFFICIENCY_CHECK = "sufficiency_check"
NODE_TRIGGER_REACTIVE_INGESTION = "trigger_reactive_ingestion"
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
# Routing function — after sufficiency_check (Sprint 23.5 T23.5.5)
# ==========================================================

def _route_after_sufficiency(
    state: dict,
) -> Literal["trigger_reactive_ingestion", "rate_evidence"]:
    """
    Conditional edge routing function executed after sufficiency_check.

    Reads (state only — no LLM, no side effects, agent-design P3):
        state["sufficiency_checks"][-1]["is_sufficient"] — the latest verdict
        state["reactive_triggers_emitted"]               — per-session counter

    Returns:
        "trigger_reactive_ingestion" when ALL of:
            - the latest sufficiency verdict is_sufficient is False, AND
            - reactive_triggers_emitted < AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION
          The trigger node emits one ingestion_triggers Kafka message
          (trigger-and-forget) and the graph then proceeds to rate_evidence.

        "rate_evidence" otherwise — i.e. the vault is sufficient, OR it is
        insufficient but the per-session trigger budget is already spent.
        Either way synthesis proceeds on the available evidence (low
        confidence + populated whatIDidntFind handles the sparse case in
        synthesize). This is the primary per-session trigger gate; the
        trigger node's in-node check is belt-and-suspenders.

    Locked V1 topology (decision record §6 R5): there is no second vault
    query and no refine loop — a single check routes to trigger-or-not, then
    always lands at rate_evidence.
    """
    checks = state.get("sufficiency_checks") or []
    latest = checks[-1] if checks else {}
    is_sufficient = bool(latest.get("is_sufficient"))

    if is_sufficient:
        return NODE_RATE_EVIDENCE

    emitted = int(state.get("reactive_triggers_emitted") or 0)
    if emitted < settings.AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION:
        return NODE_TRIGGER_REACTIVE_INGESTION

    logger.info(
        "_route_after_sufficiency: insufficient but trigger budget spent "
        "(%d/%d) — proceeding to rate_evidence on available evidence. "
        "session_id=%s",
        emitted, settings.AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION,
        state.get("session_id"),
    )
    return NODE_RATE_EVIDENCE


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
    builder.add_node(NODE_SUFFICIENCY_CHECK, sufficiency_check.run)
    builder.add_node(
        NODE_TRIGGER_REACTIVE_INGESTION, trigger_reactive_ingestion.run
    )
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
    # Sprint 23.5 T23.5.5: vault_query → sufficiency_check, then a conditional
    # edge routes to the reactive trigger (insufficient + budget) or straight
    # to rate_evidence. The trigger branch rejoins rate_evidence afterwards
    # (trigger-and-forget — no wait for ingestion).
    builder.add_edge(NODE_VAULT_QUERY, NODE_SUFFICIENCY_CHECK)
    builder.add_conditional_edges(
        NODE_SUFFICIENCY_CHECK,
        _route_after_sufficiency,
        {
            NODE_TRIGGER_REACTIVE_INGESTION: NODE_TRIGGER_REACTIVE_INGESTION,
            NODE_RATE_EVIDENCE: NODE_RATE_EVIDENCE,
        },
    )
    builder.add_edge(NODE_TRIGGER_REACTIVE_INGESTION, NODE_RATE_EVIDENCE)
    builder.add_edge(NODE_RATE_EVIDENCE, NODE_SYNTHESIZE)
    builder.add_edge(NODE_SYNTHESIZE, NODE_WRITE_TO_FIRESTORE)
    builder.add_edge(NODE_WRITE_TO_FIRESTORE, END)

    return builder


# ==========================================================
# Compiled singleton
# ==========================================================
graph = _build_graph().compile()
