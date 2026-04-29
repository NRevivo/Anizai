"""
Schema sanity tests for ForecastState (Sprint 18 T10).

ForecastState is a TypedDict — at runtime it behaves as a plain dict and
provides no enforcement. These tests pin the field contract so future
edits cannot silently rename or drop a Patch 6 field that downstream
nodes will depend on (Sprints 19-25).

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.3.1
    - data-pipeline/docs/agentic_hub_spec_patch.md Patch 6
"""

from agent.state import ForecastState


def test_all_patch6_fields_present():
    """Patch 6 added Clarification, Reactive Search, and Output sections.
    These field names are the contract surface for the LangGraph nodes
    landing in Sprints 19-25 — renaming any of them breaks routing."""
    fields = ForecastState.__annotations__

    # Clarification (Patch 6)
    for name in (
        "awaiting_clarification",
        "clarification_candidates",
        "chosen_candidate_id",
        "skip_matching_step",
    ):
        assert name in fields, f"missing Clarification field: {name}"

    # Reactive Search (Patch 6)
    for name in (
        "vault_query_attempts",
        "sufficiency_checks",
        "reactive_search_results",
        "reactive_search_budget_remaining_ms",
    ):
        assert name in fields, f"missing Reactive Search field: {name}"

    # Output (Patch 6)
    for name in (
        "tier",
        "suggested_actions",
        "key_factors",
        "what_i_didnt_find",
    ):
        assert name in fields, f"missing Output field: {name}"


def test_total_false_allows_partial_dict():
    """LangGraph nodes return partial state dicts; constructing
    ForecastState with only one field must not raise."""
    state: ForecastState = {"raw_question": "will it rain?"}
    assert state["raw_question"] == "will it rain?"


def test_empty_dict_is_valid_state():
    """LangGraph's initial state is often {} — must be a valid ForecastState."""
    state: ForecastState = {}
    assert state == {}
