"""
Gate 2 subgraph integration tests for Sprint 23.5 — Pre-26 Remediation.

Exercises the new post-vault topology
(vault_query →) sufficiency_check → {rate_evidence | trigger_reactive_ingestion
→ rate_evidence} through a real compiled LangGraph, plus total_cost_usd
accumulation across nodes. Pure subgraph integration over mocks — no real
Kafka / Postgres / Firestore / OpenAI.

Two construction styles:
  * `_build_sufficiency_subgraph` wires the REAL sufficiency_check.run and the
    REAL `_route_after_sufficiency` from agent.graph, with stub leaf nodes
    standing in for trigger_reactive_ingestion / rate_evidence so we can
    observe which branch executed.
  * The cost-accumulation test chains two stub nodes that each add to
    total_cost_usd, proving the float accumulator merges across the graph
    (R9 — float accumulation of total_cost_usd).

Spec references:
    - data-pipeline/docs/B_hub/sprint23_5_pre26_remediation.md §5 (Gate 2)
    - cabinet-outputs/advisor/problem-reports/sprint23_5_advisor-ron-decisions.md §6
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agent import graph as graph_module
from agent.nodes import sufficiency_check
from agent.state import ForecastState


# ============================================================
# Helpers
# ============================================================


def _researcher(*titles: str) -> dict:
    return {
        "articles": [
            {"title": t, "full_text_snippet": "", "source_platform": "newsapi"}
            for t in titles
        ]
    }


# NOTE: LangGraph drops any key not declared as a channel on ForecastState,
# so the stubs signal which branch ran via DECLARED fields:
#   trigger → bumps `reactive_triggers_emitted` (its real delta)
#   rate    → writes a marker into `evidence_trail`
_RATE_MARKER = [{"evidence_id": "stub-rate-ran"}]


def _stub_trigger(state: dict) -> dict:
    """Stand-in for trigger_reactive_ingestion: bumps the counter (mirrors the
    real node's state delta) so the rejoin to rate_evidence is exercised."""
    return {
        "reactive_triggers_emitted": int(state.get("reactive_triggers_emitted") or 0) + 1,
    }


def _stub_rate(_state: dict) -> dict:
    return {"evidence_trail": _RATE_MARKER}


def _build_sufficiency_subgraph():
    """Compile START → sufficiency_check → [route] → {trigger → rate | rate} → END
    using the real node + real routing function."""
    builder = StateGraph(ForecastState)
    builder.add_node(graph_module.NODE_SUFFICIENCY_CHECK, sufficiency_check.run)
    builder.add_node(graph_module.NODE_TRIGGER_REACTIVE_INGESTION, _stub_trigger)
    builder.add_node(graph_module.NODE_RATE_EVIDENCE, _stub_rate)

    builder.add_edge(START, graph_module.NODE_SUFFICIENCY_CHECK)
    builder.add_conditional_edges(
        graph_module.NODE_SUFFICIENCY_CHECK,
        graph_module._route_after_sufficiency,
        {
            graph_module.NODE_TRIGGER_REACTIVE_INGESTION:
                graph_module.NODE_TRIGGER_REACTIVE_INGESTION,
            graph_module.NODE_RATE_EVIDENCE: graph_module.NODE_RATE_EVIDENCE,
        },
    )
    builder.add_edge(
        graph_module.NODE_TRIGGER_REACTIVE_INGESTION, graph_module.NODE_RATE_EVIDENCE
    )
    builder.add_edge(graph_module.NODE_RATE_EVIDENCE, END)
    return builder.compile()


# ============================================================
# Routing through the real graph
# ============================================================


class TestSufficiencyRoutingSubgraph:

    def test_sufficient_path_skips_trigger(self):
        """Enough signals + entities covered → sufficiency_check writes a
        sufficient verdict → routes straight to rate_evidence (no trigger)."""
        graph = _build_sufficiency_subgraph()
        state = {
            "session_id": "g2-suff-1",
            "structured_intent": {"entities": ["Iran"]},
            "researcher_evidence": _researcher(
                "Iran a", "Iran b", "Iran c", "Iran d", "Iran e"
            ),
        }
        final = graph.invoke(state)

        assert final["sufficiency_checks"][-1]["is_sufficient"] is True
        assert final.get("evidence_trail") == _RATE_MARKER  # rate_evidence ran
        # No trigger means the counter was never bumped.
        assert int(final.get("reactive_triggers_emitted") or 0) == 0

    def test_insufficient_with_budget_routes_through_trigger(self):
        """Insufficient + trigger budget available → trigger runs, then
        rejoins rate_evidence (trigger-and-forget)."""
        graph = _build_sufficiency_subgraph()
        state = {
            "session_id": "g2-suff-2",
            "structured_intent": {"entities": ["Iran", "Venezuela"]},
            # 2 signals (< floor 5) and "Venezuela" uncovered → insufficient.
            "researcher_evidence": _researcher("Iran a", "Iran b"),
            "reactive_triggers_emitted": 0,
        }
        final = graph.invoke(state)

        verdict = final["sufficiency_checks"][-1]
        assert verdict["is_sufficient"] is False
        assert "Venezuela" in verdict["missing_dimensions"]
        # Trigger fired exactly once, then rate_evidence ran (rejoin).
        assert final["reactive_triggers_emitted"] == 1
        assert final.get("evidence_trail") == _RATE_MARKER

    def test_insufficient_but_budget_spent_skips_trigger(self):
        """Insufficient but reactive_triggers_emitted already at the limit →
        proceed to rate_evidence on available evidence (no second trigger)."""
        from agent.config import settings

        graph = _build_sufficiency_subgraph()
        state = {
            "session_id": "g2-suff-3",
            "structured_intent": {"entities": ["Iran"]},
            "researcher_evidence": _researcher("Iran a"),  # 1 signal
            "reactive_triggers_emitted": settings.AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION,
        }
        final = graph.invoke(state)

        assert final["sufficiency_checks"][-1]["is_sufficient"] is False
        assert final.get("evidence_trail") == _RATE_MARKER  # rate_evidence ran
        # Counter unchanged — no extra trigger.
        assert (
            final["reactive_triggers_emitted"]
            == settings.AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION
        )


# ============================================================
# total_cost_usd accumulation across nodes (R9)
# ============================================================


class TestTotalCostAccumulation:

    def test_total_cost_usd_accumulates_across_nodes(self):
        """R9: total_cost_usd accumulates cleanly across sequential nodes.

        KG-B-19 (closed 2026-07-15, Sprint 25 T25.0): this originally called
        ``graph.invoke({})`` with an EMPTY input and raised LangGraph
        ``InvalidUpdateError`` — a TEST DEFECT, not library drift. An empty
        invoke seeds no input channel; a non-empty initial state (which every
        real run has — ``claim_session`` seeds ``session_id``) lets the two
        sequential deltas merge cleanly. The artifact never reflected the
        production path (four real nodes already write these shared scalars
        sequentially and are green in graph-integration + the Sprint-24 E2E).
        Do NOT restore the empty invoke, and do NOT pin ``requirements*`` over
        this — the root cause was the test input, confirmed by this passing.
        """
        def node_a(state: dict) -> dict:
            return {"total_cost_usd": float(state.get("total_cost_usd") or 0.0) + 0.0025}

        def node_b(state: dict) -> dict:
            return {"total_cost_usd": float(state.get("total_cost_usd") or 0.0) + 0.0100}

        builder = StateGraph(ForecastState)
        builder.add_node("a", node_a)
        builder.add_node("b", node_b)
        builder.add_edge(START, "a")
        builder.add_edge("a", "b")
        builder.add_edge("b", END)
        graph = builder.compile()

        final = graph.invoke({"session_id": "x"})
        # float accumulation merges cleanly across the two node deltas.
        assert abs(final["total_cost_usd"] - 0.0125) < 1e-9


# ============================================================
# Production graph wiring sanity (structural)
# ============================================================


def test_production_graph_wires_sufficiency_between_vault_and_branches():
    builder = graph_module._build_graph()
    edges = builder.edges
    branches = getattr(builder, "branches", {})

    # vault_query now feeds sufficiency_check (not rate_evidence directly).
    assert (
        graph_module.NODE_VAULT_QUERY, graph_module.NODE_SUFFICIENCY_CHECK
    ) in edges
    assert (
        graph_module.NODE_VAULT_QUERY, graph_module.NODE_RATE_EVIDENCE
    ) not in edges
    # sufficiency_check has the conditional branch; trigger rejoins rate_evidence.
    assert graph_module.NODE_SUFFICIENCY_CHECK in branches
    assert (
        graph_module.NODE_TRIGGER_REACTIVE_INGESTION,
        graph_module.NODE_RATE_EVIDENCE,
    ) in edges
