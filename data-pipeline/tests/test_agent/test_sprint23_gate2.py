"""
Gate 2 subgraph integration tests for Sprint 23 — Producer-trigger
Infrastructure (T23.8).

These tests build **test-only LangGraphs** that contain
`trigger_reactive_ingestion` (and, for G2.1, a dummy upstream node).
`agent/graph.py` is NOT modified in Sprint 23 — wiring of
`trigger_reactive_ingestion` into the production graph is Sprint 26's
T26.7. Until then, the node runs in isolation, and Gate 2 verifies that
isolation by constructing the subgraph here in the test setup.

Bundle G2 (T23.8):
    G2.1 — Cross-node state flow: an upstream node injects
           `structured_intent.entities` + `sufficiency_checks` into state
           via LangGraph's partial-state merge; the trigger node consumes
           the upstream-merged fields exactly as it would in production.
           Validates that the node reads state correctly from a real
           upstream merge, not just from a manually-constructed dict
           (which is what Bundle B's unit tests use).
    G2.2 — State lifecycle across invocations: same single-node graph
           invoked twice. First call emits, counter 0→1. Second call,
           using the first call's output as input, blocks because
           counter==limit. Verifies (a) LangGraph's empty-delta merge
           leaves prior state intact and (b) the counter actually
           persists across separate `invoke()` calls.
    G2.3 — Multi-invocation under limit > 1: with monkeypatched
           AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION=2, two sequential
           invocations both emit, counter accumulates 0→1→2.
           Untested regime in Bundles B and C, which all run under
           the default limit=1.

Mock surface (same lookup-site pattern as Bundle B):
    * `agent.nodes.trigger_reactive_ingestion._get_producer` — Kafka factory
    * `agent.nodes.trigger_reactive_ingestion.reactive_triggers_log` — module

No real Kafka, no real Postgres. Pure subgraph integration over mocks.

Spec references:
    - data-pipeline/docs/agentic_hub_implementation_phase8_revised.md §Sprint 23
    - data-pipeline/docs/agentic_hub_implementation_phase8_revised.md
      §"Implementation Order & Parallelization" (subgraph-in-isolation rationale)
"""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch

import pytest
from langgraph.graph import END, START, StateGraph

from agent.state import ForecastState


# ============================================================
# Shared helpers
# ============================================================


def _mock_kafka_send_success(offset: int = 42, partition: int = 0):
    """KafkaProducer mock whose `.send().get()` returns a RecordMetadata-like."""
    producer = MagicMock()
    record_metadata = MagicMock(offset=offset, partition=partition)
    producer.send.return_value.get.return_value = record_metadata
    return producer


def _build_single_node_subgraph(node_fn):
    """
    Compile a one-node LangGraph: START → trigger_reactive_ingestion → END.

    Used by G2.2 and G2.3 — tests where the upstream-state question is not
    the focus and the initial state can be constructed directly.
    """
    builder = StateGraph(ForecastState)
    builder.add_node("trigger_reactive_ingestion", node_fn)
    builder.add_edge(START, "trigger_reactive_ingestion")
    builder.add_edge("trigger_reactive_ingestion", END)
    return builder.compile()


def _inject_upstream_state(_state: dict) -> dict:
    """
    Dummy upstream node for G2.1. Returns a partial-state delta that
    LangGraph merges into the running state — exactly how query_understand,
    sufficiency_check, etc. populate the fields the trigger node consumes
    in production. The graph receives a near-empty initial state and the
    upstream node fills in the trigger-relevant fields.
    """
    return {
        "structured_intent": {
            "entities": ["Iran", "OPEC"],
        },
        "sufficiency_checks": [
            {"missing_dimensions": ["recent reaction", "economic impact"]}
        ],
    }


# ============================================================
# Bundle G2: T23.8 — subgraph integration
# ============================================================


class TestSprint23Gate2Subgraph:
    """Bundle G2 — `trigger_reactive_ingestion` in a real LangGraph context."""

    # --- G2.1: cross-node state flow ---

    def test_subgraph_with_upstream_node_populates_state_correctly(self):
        """[G2.1] An upstream node injects `structured_intent.entities` +
        `sufficiency_checks` via LangGraph's partial-state merge. The
        trigger node then consumes those upstream-set fields and emits
        the correct Kafka payload.

        Why this matters beyond Bundle B:
            Bundle B's tests construct state manually as a dict and call
            the node function directly. That bypasses LangGraph's state
            merge — so a subtle bug in how the node reads from
            already-merged state (e.g., expecting a different key name
            than what the merger produces) would not be caught at the
            unit level. This test exercises the real merge path.

        Assertions:
            (a) Kafka sent exactly once with entities-only keywords derived
                from the upstream-injected fields (R1 — missing_dimensions
                from the upstream sufficiency_check are NOT folded in).
            (b) Counter ends at 1 (a single trigger attempt).
            (c) Upstream-set fields (`structured_intent`,
                `sufficiency_checks`) are preserved in the final state —
                the trigger node's `{"reactive_triggers_emitted": 1}`
                delta must not stomp on unrelated state.
        """
        from agent.nodes import trigger_reactive_ingestion as node

        producer = _mock_kafka_send_success(offset=11)

        # Two-node subgraph:  START → inject_upstream → trigger → END
        builder = StateGraph(ForecastState)
        builder.add_node("inject_upstream", _inject_upstream_state)
        builder.add_node(
            "trigger_reactive_ingestion", node.trigger_reactive_ingestion
        )
        builder.add_edge(START, "inject_upstream")
        builder.add_edge("inject_upstream", "trigger_reactive_ingestion")
        builder.add_edge("trigger_reactive_ingestion", END)
        graph = builder.compile()

        initial_state: dict = {"session_id": "g2-session-1"}

        with patch.object(node, "_get_producer", return_value=producer), \
             patch.object(node, "reactive_triggers_log"):
            final_state = graph.invoke(initial_state)

        # (a) Kafka sent with entities-only keywords (R1) — the upstream
        # sufficiency_check's missing_dimensions are recorded in state (see
        # assertion (c)) but must NOT shape the keyword set.
        producer.send.assert_called_once()
        payload = producer.send.call_args.kwargs["value"]
        assert payload["keywords"] == ["Iran", "OPEC"], (
            "V1 reactive keywords are entities-only (R1) — even though the "
            "upstream node merged missing_dimensions into state, they must "
            "not enter the keyword set."
        )

        # (b) Counter ends at 1
        assert final_state["reactive_triggers_emitted"] == 1

        # (c) Upstream-set fields preserved through the trigger node's delta
        assert final_state["structured_intent"] == {
            "entities": ["Iran", "OPEC"],
        }
        assert final_state["sufficiency_checks"] == [
            {"missing_dimensions": ["recent reaction", "economic impact"]}
        ]
        assert final_state["session_id"] == "g2-session-1"

    # --- G2.2: empty-delta merge + state-passing across invocations ---

    def test_subgraph_idempotent_at_rate_limit_across_two_invocations(self):
        """[G2.2] Run the same compiled subgraph twice in sequence. The
        first invocation emits and the counter goes 0 → 1. The second
        invocation, using the first's output as input, must block at the
        rate limit (counter == 1 == default limit).

        What this exercises that Bundle B cannot:
            * LangGraph's empty-delta merge: the trigger node returns
              `{}` on the rate-limit-blocked path; the subgraph must
              propagate the unchanged state through to END without
              raising or mutating other fields.
            * State persistence across invocations: passing the first
              invocation's output back in as the second's input must
              preserve `reactive_triggers_emitted`, which is what the
              gate reads.

        Mock for both invocations: producer is replaced fresh each call;
        on the blocked path, producer factory must never be reached
        (assertion: factory call count == 1 across the two invocations).
        """
        from agent.nodes import trigger_reactive_ingestion as node

        graph = _build_single_node_subgraph(node.trigger_reactive_ingestion)

        initial_state: dict = {
            "session_id": "g2-session-2",
            "structured_intent": {"entities": ["Iran"]},
        }

        producer = _mock_kafka_send_success(offset=22)
        with patch.object(node, "_get_producer", return_value=producer) as mock_make, \
             patch.object(node, "reactive_triggers_log"):
            # First invocation: emits, counter 0 → 1
            after_first = graph.invoke(initial_state)
            assert after_first["reactive_triggers_emitted"] == 1
            assert mock_make.call_count == 1
            assert producer.send.call_count == 1

            # Second invocation: feed the first's output back in. Counter
            # is already at the limit (default = 1), so the node returns
            # `{}` and the running state's counter is unchanged.
            after_second = graph.invoke(after_first)
            assert after_second["reactive_triggers_emitted"] == 1, (
                "Counter must remain at the limit; LangGraph's empty-delta "
                "merge from the blocked path must not mutate the counter."
            )
            # No additional Kafka activity across the two invocations
            assert mock_make.call_count == 1, (
                "_get_producer must not be called on the second invocation — "
                "the rate-limit gate at node entry short-circuits before "
                "the producer factory is reached."
            )
            assert producer.send.call_count == 1

    # --- G2.3: counter accumulates correctly when limit > 1 ---

    def test_subgraph_two_invocations_each_within_budget_with_limit_2(
        self, monkeypatch
    ):
        """[G2.3] With AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION=2, two
        sequential invocations both emit; counter accumulates 0 → 1 → 2.

        Why this matters:
            All Bundle B + C tests run with limit=1 (the default). C1
            tested limit=3 but only across a synthetic one-call-per-prior
            sweep of state dicts — it did NOT exercise actual graph
            state-passing across invocations under limit > 1. This test
            covers that gap: the accumulating-budget regime through the
            real LangGraph state merge.

        Module reload pattern: same as C1 — reload settings first so the
        constant picks up the env override, then reload the node module
        so its `from agent.config.settings import` rebinds to the new
        value. Cleanup reverses both.
        """
        monkeypatch.setenv("AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION", "2")

        import agent.config.settings as settings_mod
        importlib.reload(settings_mod)
        from agent.nodes import trigger_reactive_ingestion as node
        importlib.reload(node)

        try:
            graph = _build_single_node_subgraph(
                node.trigger_reactive_ingestion
            )

            initial_state: dict = {
                "session_id": "g2-session-3",
                "structured_intent": {"entities": ["Iran"]},
            }

            producer = _mock_kafka_send_success(offset=33)
            with patch.object(
                node, "_get_producer", return_value=producer
            ) as mock_make, patch.object(node, "reactive_triggers_log"):
                # First invocation — under budget, emits, counter 0 → 1
                after_first = graph.invoke(initial_state)
                assert after_first["reactive_triggers_emitted"] == 1
                assert mock_make.call_count == 1

                # Second invocation — still under the limit of 2, emits,
                # counter 1 → 2. The budget accumulates correctly across
                # invocations via the state delta returned from the node.
                after_second = graph.invoke(after_first)
                assert after_second["reactive_triggers_emitted"] == 2
                assert mock_make.call_count == 2

                # Both calls hit Kafka — no skip.
                assert producer.send.call_count == 2
        finally:
            monkeypatch.undo()
            importlib.reload(settings_mod)
            importlib.reload(node)
