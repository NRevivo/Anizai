"""
ForecastState — shared state for the LangGraph forecast pipeline.

This module defines the single state object that flows through every node in
the hub's forecast graph. It is the contract between nodes (agent-design P2:
"State is the contract"). Nodes never call each other directly — they read
from and write to this state, and LangGraph dispatches based on routing
functions that read state fields.

Spec references:
- data-pipeline/docs/agentic_hub_spec.md §8.3.1 (graph state schema)
- data-pipeline/docs/agentic_hub_spec_patch.md Patch 6 (Clarification, Reactive
  Search, and Output sections appended to the schema)

Why TypedDict (not Pydantic):
LangGraph's StateGraph natively understands TypedDict and merges partial
returns from nodes into the accumulated state. Pydantic would force every
node to construct a full validated model on each return, which conflicts
with the partial-update pattern.

Why total=False:
Nodes return partial dicts that LangGraph merges into the running state.
A field is only present once a node has written it. Reading a field that
hasn't been populated yet should be treated as "not yet computed" rather
than a type error. Sprint 18's stub pipeline writes only a small subset of
these fields; later sprints fill in the rest.

Why nested dicts (not helper TypedDicts) for now:
Helper types (ClarificationCandidate, EvidenceItem, SufficiencyCheck,
ReactiveSearchResult, KeyFactor, SuggestedAction) will be introduced in the
sprints that produce them (Sprints 19-25). Defining them here before any
node uses them would be premature abstraction. For Sprint 18 the foundation
only needs the field names and outer types in place.
"""

from __future__ import annotations

from typing import Literal, Optional, TypedDict


class ForecastState(TypedDict, total=False):
    """The single state object that flows through the forecast LangGraph."""

    # --- Input ---
    raw_question: str
    session_id: str
    user_id: str

    # --- Query Understanding Output ---
    question_tier: Literal["polymarket_backed", "freeform"]
    structured_intent: dict
    polymarket_market: Optional[dict]
    query_embedding: list[float]
    clarification_needed: Optional[dict]

    # --- Clarification (added) ---
    awaiting_clarification: bool
    clarification_candidates: Optional[list[dict]]
    chosen_candidate_id: Optional[str]
    skip_matching_step: bool

    # --- Agent Evidence Packages ---
    researcher_evidence: Optional[dict]
    pulse_evidence: Optional[dict]
    market_evidence: Optional[dict]

    # --- Evidence Evaluation ---
    dedup_report: Optional[dict]

    # --- Reactive Search (added) ---
    vault_query_attempts: int
    sufficiency_checks: list[dict]
    reactive_search_results: Optional[list[dict]]
    reactive_search_budget_remaining_ms: int

    # --- Synthesis Output ---
    synthesis_result: Optional[dict]
    evidence_trail: list[dict]
    confidence_score: Optional[float]

    # --- Output (added) ---
    tier: Literal["tier_1", "tier_2"]
    suggested_actions: Optional[list[dict]]
    key_factors: Optional[list[dict]]
    what_i_didnt_find: Optional[list[str]]

    # --- Metadata ---
    llm_calls_count: int
    total_tokens_used: int
    errors: list[str]
