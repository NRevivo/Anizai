"""
FollowupState — shared state for the follow-up conversation subgraph
(Sprint 24, T24.2).

The single state object that flows through the follow-up LangGraph
(`load_context → answer_from_context → write_message`). It is the contract
between those nodes (agent-design P2 / hub-principles G2: "State is the
contract") — nodes never call each other directly; they read from and write
to this state, and LangGraph merges each node's partial return into the
running state.

Why TypedDict + total=False (same rationale as `agent/state.py`):
    LangGraph's StateGraph natively understands TypedDict and merges partial
    node returns into accumulated state. `total=False` means a field is only
    present once a node has written it; the listener/sweep seed the input
    fields, and each node fills in the rest. Reading a not-yet-written field
    is "not yet computed", not a type error.

Why a separate, smaller state (not a reuse of `ForecastState`):
    The follow-up path is structurally separate (spec §8.8.3 revised) and
    far lighter than the forecast pipeline — it loads parent context and
    makes a single GPT-4o-mini call. Reusing the ~30-field `ForecastState`
    would drag in fields no follow-up node reads or writes and blur the two
    contracts. This is the follow-up analogue of `ForecastState`.

The 8-field ratified set (plan §3 "FollowupState full field set", refined
2026-07-04 to add the two `trigger_*` fields):

    Seeded by the listener/sweep when it builds the initial state
    (`agent/followup/listener.py`, T24.1 / T24.15) — the triggering user
    message doc is already in hand, so both trigger_* fields are free (zero
    extra reads):
        - parent_session_id   — the parent forecast's session id (== the
                                sessions/{id} doc id; the message's parent
                                session)
        - trigger_message_id  — the doc id of the *specific* user message
                                this run answers. The triggering message is
                                identified by THIS id everywhere: the atomic
                                claim, the `sent → answered` flip (T24.5),
                                and `replyToMessageId` on the assistant
                                message. Never a positional pick from
                                message_history — that reintroduces the
                                24.14 back-to-back bug where two rapid
                                `sent` messages both resolve to the newest.
        - trigger_question    — that message's `content` (the actual
                                follow-up question). `answer_from_context`
                                (T24.4) answers THIS text, NEVER
                                message_history[-1] or any positional pick.

    Written by `load_context` (T24.3):
        - message_history        — the last 10 messages in the thread, for
                                   conversational context (each dict carries
                                   at least role/content; may carry the doc
                                   id + status). Context only — the message
                                   being answered is trigger_question, not a
                                   slice of this list.
        - parent_session_result  — the parent forecast's SessionResult dict
                                   (probability, confidence, key factors,
                                   what_i_didnt_find, …).
        - parent_evidence        — the top 5 evidence items (by rank) from
                                   the parent forecast. The ONLY evidence the
                                   follow-up may reason over (no new
                                   retrieval).

    Written by `answer_from_context` (T24.4):
        - response_text          — the assistant reply body (markdown). On
                                   insufficient context this is the
                                   transparent message; on budget overrun a
                                   complete caveat message (never truncated).

    Accumulated by `answer_from_context` (T24.4) — the "born instrumented"
    accumulator (plan §2 / 23.5.11):
        - total_cost_usd         — running USD cost of the follow-up,
                                   accumulated from the GPT-4o-mini call via
                                   `agent/utils/llm_cost.record_usage`. Read
                                   defensively via
                                   `float(state.get("total_cost_usd") or 0.0)`
                                   (total=False semantics), matching the
                                   ForecastState idiom.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.8.3 (revised — follow-up
      answer-from-context path)
    - data-pipeline/docs/B_hub/plans/sprint24_followups.md §3 (FollowupState
      field set; trigger_* fields ratified 2026-07-04) + §4 T24.2
"""

from __future__ import annotations

from typing import TypedDict


class FollowupState(TypedDict, total=False):
    """The single state object that flows through the follow-up LangGraph."""

    # --- Input (seeded by the listener / sweep; §4 T24.1 / T24.15) ---
    parent_session_id: str
    # The specific user message this run answers — identified by id
    # everywhere (atomic claim, sent→answered flip, replyToMessageId).
    # Never resolved positionally from message_history (24.14).
    trigger_message_id: str
    # That message's content — the question answer_from_context answers.
    trigger_question: str

    # --- Parent context (written by load_context; §4 T24.3) ---
    message_history: list[dict]
    parent_session_result: dict
    parent_evidence: list[dict]

    # --- Answer (written by answer_from_context; §4 T24.4) ---
    response_text: str

    # --- Cost ("born instrumented" accumulator; §2 / §4 T24.4) ---
    total_cost_usd: float
