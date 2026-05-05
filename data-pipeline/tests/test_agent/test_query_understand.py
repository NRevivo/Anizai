"""
Gate 1 tests for `agent/nodes/query_understand.py` (T19.5).

Strategy: pure-mock at the OpenAI client boundary (OQ-9). We construct
fake `chat.completions.create()` responses with the shape returned by
`openai>=1.0` (`response.choices[0].message.content` is a JSON string;
`response.usage.total_tokens` is an int). No real OpenAI calls.

Coverage:
- happy path (single confident candidate)
- happy path (3 candidates, rank-1 wins by margin)
- ambiguous: rank 1/2 within margin → clarification
- ambiguous: rank 1 below auto-pick threshold → clarification
- too_broad on rank 1 → clarification
- rejected on rank 1 → AgentProcessingError
- rank-1 auto-pick with single candidate (no rank 2)
- empty raw_question → AgentProcessingError
- OpenAI SDK raises → AgentProcessingError wrap
- malformed response (no choices) → AgentProcessingError
- non-JSON content → AgentProcessingError
- empty candidates list → AgentProcessingError
- token usage accumulates into total_tokens_used
- llm_calls_count increments by 1
- prompt + schema forwarded to OpenAI verbatim (regression guard for D9)
- polymarket_search_terms preserved through structured_intent
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.errors import AgentProcessingError
from agent.nodes import query_understand
from agent.prompts.query_understanding import (
    DOMAIN_VALUES,
    INTENT_VALUES,
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
)


# ==========================================================
# Test fixtures
# ==========================================================
def _make_candidate(
    *,
    confidence: float = 0.9,
    intent: str = "forecast",
    domain: str = "macro",
    entities: list[str] | None = None,
    polymarket_search_terms: list[str] | None = None,
    has_market_question_intent: bool = True,
    too_broad: bool = False,
    rejected: bool = False,
) -> dict:
    return {
        "intent": intent,
        "domain": domain,
        "entities": entities or ["Federal Reserve"],
        "polymarket_search_terms": polymarket_search_terms,
        "has_market_question_intent": has_market_question_intent,
        "confidence": confidence,
        "too_broad": too_broad,
        "rejected": rejected,
    }


def _make_response(*, candidates: list[dict], total_tokens: int = 120) -> SimpleNamespace:
    """
    Build a SimpleNamespace shaped like an openai>=1.x ChatCompletion.

    Code reads:
        response.choices[0].message.content     -> JSON string
        response.usage.total_tokens             -> int
    """
    payload = {"candidates": candidates}
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(payload))
            )
        ],
        usage=SimpleNamespace(total_tokens=total_tokens),
    )


def _make_client(response: SimpleNamespace) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = response
    return client


# ==========================================================
# Happy paths
# ==========================================================
def test_run_auto_picks_single_confident_candidate():
    response = _make_response(candidates=[_make_candidate(confidence=0.92)])
    client = _make_client(response)

    state = {"raw_question": "Will the Fed cut rates by December?"}
    out = query_understand.run(state, client=client)

    assert out["awaiting_clarification"] is False
    assert out["clarification_candidates"] is None
    assert out["clarification_needed"] is None
    assert out["structured_intent"]["intent"] == "forecast"
    assert out["structured_intent"]["confidence"] == 0.92


def test_run_auto_picks_when_rank_1_beats_rank_2_by_margin():
    response = _make_response(
        candidates=[
            _make_candidate(confidence=0.90, entities=["Bitcoin"]),
            _make_candidate(confidence=0.70, entities=["Ethereum"]),
            _make_candidate(confidence=0.50, entities=["Solana"]),
        ]
    )
    client = _make_client(response)

    out = query_understand.run({"raw_question": "BTC question"}, client=client)

    assert out["awaiting_clarification"] is False
    assert out["structured_intent"]["entities"] == ["Bitcoin"]


def test_run_clarifies_when_margin_too_narrow():
    response = _make_response(
        candidates=[
            _make_candidate(confidence=0.80, entities=["Bitcoin"]),
            _make_candidate(confidence=0.72, entities=["Bitcoin Cash"]),
        ]
    )
    client = _make_client(response)

    out = query_understand.run({"raw_question": "BTC?"}, client=client)

    assert out["awaiting_clarification"] is True
    assert out["clarification_candidates"] is not None
    assert len(out["clarification_candidates"]) == 2
    assert out["clarification_needed"]["candidates"][0]["entities"] == ["Bitcoin"]
    # rank-1 still seeded into structured_intent so downstream nodes have
    # something to read while the user clarifies.
    assert out["structured_intent"]["entities"] == ["Bitcoin"]


def test_run_clarifies_when_rank_1_below_auto_pick_threshold():
    response = _make_response(
        candidates=[
            _make_candidate(confidence=0.60),
            _make_candidate(confidence=0.20),
        ]
    )
    client = _make_client(response)

    out = query_understand.run({"raw_question": "ambiguous"}, client=client)

    assert out["awaiting_clarification"] is True


def test_run_clarifies_when_too_broad_even_if_high_confidence():
    """too_broad=True on rank_1 forces clarification, bypassing the margin check.

    Two candidates are required — clarification is only meaningful when ≥2
    distinct options exist (KG-PHASE8-21 guard). The second candidate gives
    the user an alternative interpretation to pick instead of the too_broad one.
    """
    response = _make_response(
        candidates=[
            _make_candidate(confidence=0.95, too_broad=True, entities=["Economy"]),
            _make_candidate(confidence=0.80, too_broad=False, entities=["GDP Growth"]),
        ]
    )
    client = _make_client(response)

    out = query_understand.run({"raw_question": "Tell me about the economy"}, client=client)

    assert out["awaiting_clarification"] is True


# ==========================================================
# Hard rejection
# ==========================================================
def test_run_raises_when_rank_1_rejected():
    response = _make_response(
        candidates=[_make_candidate(confidence=0.99, rejected=True)]
    )
    client = _make_client(response)

    with pytest.raises(AgentProcessingError) as exc_info:
        query_understand.run({"raw_question": "give me a brownie recipe"}, client=client)

    assert exc_info.value.code == "AGENT_PROCESSING_ERROR"
    assert "rejected" in str(exc_info.value).lower()


# ==========================================================
# Input validation
# ==========================================================
@pytest.mark.parametrize("question", ["", "   ", None])
def test_run_raises_on_empty_raw_question(question):
    state = {"raw_question": question} if question is not None else {}

    with pytest.raises(AgentProcessingError) as exc_info:
        query_understand.run(state, client=MagicMock())

    assert "empty raw_question" in str(exc_info.value)


# ==========================================================
# OpenAI failure modes wrapped as AgentProcessingError
# ==========================================================
def test_run_wraps_sdk_exception_as_agent_processing_error():
    client = MagicMock()
    client.chat.completions.create.side_effect = TimeoutError("api timeout")

    with pytest.raises(AgentProcessingError) as exc_info:
        query_understand.run({"raw_question": "x"}, client=client)

    assert "OpenAI call failed" in str(exc_info.value)


def test_run_raises_when_response_has_no_choices():
    bad_response = SimpleNamespace(choices=[], usage=SimpleNamespace(total_tokens=0))
    client = _make_client(bad_response)

    with pytest.raises(AgentProcessingError) as exc_info:
        query_understand.run({"raw_question": "x"}, client=client)

    assert "malformed OpenAI response" in str(exc_info.value)


def test_run_raises_when_response_content_not_json():
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="not-json{{"))],
        usage=SimpleNamespace(total_tokens=0),
    )
    client = _make_client(response)

    with pytest.raises(AgentProcessingError) as exc_info:
        query_understand.run({"raw_question": "x"}, client=client)

    assert "not JSON" in str(exc_info.value)


def test_run_raises_when_candidates_empty():
    response = _make_response(candidates=[])
    # _make_response wraps in {"candidates": [...]} so the parsed list is []
    client = _make_client(response)

    with pytest.raises(AgentProcessingError) as exc_info:
        query_understand.run({"raw_question": "x"}, client=client)

    assert "no candidates" in str(exc_info.value)


# ==========================================================
# Metadata accumulators
# ==========================================================
def test_run_increments_llm_calls_count():
    response = _make_response(candidates=[_make_candidate(confidence=0.9)])
    client = _make_client(response)

    out_fresh = query_understand.run({"raw_question": "q"}, client=client)
    assert out_fresh["llm_calls_count"] == 1

    out_continued = query_understand.run(
        {"raw_question": "q", "llm_calls_count": 4}, client=client
    )
    assert out_continued["llm_calls_count"] == 5


def test_run_accumulates_total_tokens_used():
    response = _make_response(
        candidates=[_make_candidate(confidence=0.9)], total_tokens=250
    )
    client = _make_client(response)

    out = query_understand.run(
        {"raw_question": "q", "total_tokens_used": 100}, client=client
    )

    assert out["total_tokens_used"] == 350


def test_run_handles_missing_usage_gracefully():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=json.dumps(
                        {"candidates": [_make_candidate(confidence=0.9)]}
                    )
                )
            )
        ],
        # no .usage attribute
    )
    client = _make_client(response)

    out = query_understand.run({"raw_question": "q"}, client=client)
    assert out["total_tokens_used"] == 0


# ==========================================================
# OpenAI call shape (regression guard for D9 structured-output)
# ==========================================================
def test_run_forwards_system_prompt_and_strict_schema():
    response = _make_response(candidates=[_make_candidate(confidence=0.9)])
    client = _make_client(response)

    query_understand.run({"raw_question": "Will BTC hit 100k?"}, client=client)

    call_kwargs = client.chat.completions.create.call_args.kwargs
    assert call_kwargs["response_format"] == {
        "type": "json_schema",
        "json_schema": RESPONSE_SCHEMA,
    }
    assert call_kwargs["temperature"] == 0.0

    messages = call_kwargs["messages"]
    assert messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert messages[1]["role"] == "user"
    assert "Will BTC hit 100k?" in messages[1]["content"]


def test_run_uses_settings_model():
    """
    Regression guard: model name must come from settings, not be hardcoded.
    Switching models (gpt-4o-mini ↔ gpt-4o) is a one-line env-var change.
    """
    response = _make_response(candidates=[_make_candidate(confidence=0.9)])
    client = _make_client(response)

    query_understand.run({"raw_question": "q"}, client=client)

    from agent.config import settings

    call_kwargs = client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == settings.OPENAI_MODEL_QUERY_UNDERSTANDING


# ==========================================================
# polymarket_search_terms (OQ-3 rename)
# ==========================================================
def test_run_preserves_polymarket_search_terms():
    response = _make_response(
        candidates=[
            _make_candidate(
                confidence=0.9,
                polymarket_search_terms=["fed rate cut december 2026", "fomc dec 2026"],
            )
        ]
    )
    client = _make_client(response)

    out = query_understand.run(
        {"raw_question": "Will the Fed cut rates by December 2026?"}, client=client
    )

    assert out["structured_intent"]["polymarket_search_terms"] == [
        "fed rate cut december 2026",
        "fomc dec 2026",
    ]


def test_run_preserves_null_polymarket_search_terms_for_non_market_question():
    response = _make_response(
        candidates=[
            _make_candidate(
                confidence=0.9,
                has_market_question_intent=False,
                polymarket_search_terms=None,
                intent="explain",
            )
        ]
    )
    client = _make_client(response)

    out = query_understand.run(
        {"raw_question": "Why is inflation rising?"}, client=client
    )

    assert out["structured_intent"]["polymarket_search_terms"] is None
    assert out["structured_intent"]["has_market_question_intent"] is False


# ==========================================================
# Schema enum coverage (catch silent vocabulary drift)
# ==========================================================
def test_schema_intent_enum_matches_module_constant():
    schema = RESPONSE_SCHEMA["schema"]
    intent_enum = schema["properties"]["candidates"]["items"]["properties"]["intent"]["enum"]
    assert intent_enum == INTENT_VALUES


def test_schema_domain_enum_matches_module_constant():
    schema = RESPONSE_SCHEMA["schema"]
    domain_enum = schema["properties"]["candidates"]["items"]["properties"]["domain"]["enum"]
    assert domain_enum == DOMAIN_VALUES
