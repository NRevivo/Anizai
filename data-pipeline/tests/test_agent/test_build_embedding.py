"""
Gate 1 tests for `agent/nodes/build_embedding.py` (T19.7).

Strategy: pure-mock at the OpenAI client boundary (OQ-9 from T19.5 carries
forward). We construct fake `embeddings.create()` responses with the shape
returned by `openai>=1.0`:
    response.data[0].embedding   -> list[float]
    response.usage.total_tokens  -> int

Coverage:
- happy path (1536-dim embedding written to state)
- input forwarded to OpenAI as-is (raw_question)
- model from settings
- empty raw_question → AgentProcessingError
- SDK exception → AgentProcessingError wrap
- malformed response (no .data) → AgentProcessingError
- non-list embedding → AgentProcessingError
- dimension mismatch (3072) → AgentProcessingError (OQ-3)
- token usage accumulates into total_tokens_used
- missing usage handled gracefully
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.errors import AgentProcessingError
from agent.nodes import build_embedding


# ==========================================================
# Test fixtures
# ==========================================================
def _make_response(*, dim: int = 1536, total_tokens: int = 8) -> SimpleNamespace:
    """
    Build a SimpleNamespace shaped like an openai>=1.x CreateEmbeddingResponse.
    """
    return SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.01] * dim)],
        usage=SimpleNamespace(total_tokens=total_tokens),
    )


def _make_client(response: SimpleNamespace) -> MagicMock:
    client = MagicMock()
    client.embeddings.create.return_value = response
    return client


# ==========================================================
# Happy paths
# ==========================================================
def test_run_returns_1536_dim_embedding():
    client = _make_client(_make_response(dim=1536))

    out = build_embedding.run({"raw_question": "Will the Fed cut rates?"}, client=client)

    assert "query_embedding" in out
    assert isinstance(out["query_embedding"], list)
    assert len(out["query_embedding"]) == 1536


def test_run_forwards_raw_question_to_openai():
    client = _make_client(_make_response())

    build_embedding.run(
        {"raw_question": "Will BTC hit 100k by EOY 2026?"}, client=client
    )

    call_kwargs = client.embeddings.create.call_args.kwargs
    assert call_kwargs["input"] == "Will BTC hit 100k by EOY 2026?"


def test_run_uses_settings_embedding_model():
    """
    Regression guard: model must come from settings. Switching models is a
    one-line env-var change.
    """
    client = _make_client(_make_response())

    build_embedding.run({"raw_question": "q"}, client=client)

    from agent.config import settings

    call_kwargs = client.embeddings.create.call_args.kwargs
    assert call_kwargs["model"] == settings.OPENAI_EMBEDDING_MODEL


# ==========================================================
# Input validation
# ==========================================================
@pytest.mark.parametrize("question", ["", "   ", None])
def test_run_raises_on_empty_raw_question(question):
    state = {"raw_question": question} if question is not None else {}

    with pytest.raises(AgentProcessingError) as exc_info:
        build_embedding.run(state, client=MagicMock())

    assert "empty raw_question" in str(exc_info.value)


# ==========================================================
# OpenAI failure modes wrapped as AgentProcessingError
# ==========================================================
def test_run_wraps_sdk_exception_as_agent_processing_error():
    client = MagicMock()
    client.embeddings.create.side_effect = TimeoutError("api timeout")

    with pytest.raises(AgentProcessingError) as exc_info:
        build_embedding.run({"raw_question": "x"}, client=client)

    assert "OpenAI call failed" in str(exc_info.value)


def test_run_raises_when_response_has_no_data():
    bad_response = SimpleNamespace(data=[], usage=SimpleNamespace(total_tokens=0))
    client = _make_client(bad_response)

    with pytest.raises(AgentProcessingError) as exc_info:
        build_embedding.run({"raw_question": "x"}, client=client)

    assert "malformed OpenAI response" in str(exc_info.value)


def test_run_raises_when_embedding_not_a_list():
    bad_response = SimpleNamespace(
        data=[SimpleNamespace(embedding="not-a-list")],
        usage=SimpleNamespace(total_tokens=0),
    )
    client = _make_client(bad_response)

    with pytest.raises(AgentProcessingError) as exc_info:
        build_embedding.run({"raw_question": "x"}, client=client)

    assert "expected list[float]" in str(exc_info.value)


# ==========================================================
# Dimension validation (OQ-3)
# ==========================================================
def test_run_raises_on_dimension_mismatch_too_large():
    """
    Guards against env-var swap to text-embedding-3-large (3072-dim) which
    would silently corrupt HNSW retrieval downstream.
    """
    client = _make_client(_make_response(dim=3072))

    with pytest.raises(AgentProcessingError) as exc_info:
        build_embedding.run({"raw_question": "x"}, client=client)

    assert "1536-dim" in str(exc_info.value)
    assert "3072-dim" in str(exc_info.value)


def test_run_raises_on_dimension_mismatch_too_small():
    client = _make_client(_make_response(dim=512))

    with pytest.raises(AgentProcessingError) as exc_info:
        build_embedding.run({"raw_question": "x"}, client=client)

    assert "1536-dim" in str(exc_info.value)
    assert "512-dim" in str(exc_info.value)


# ==========================================================
# Metadata accumulators
# ==========================================================
def test_run_accumulates_total_tokens_used():
    client = _make_client(_make_response(total_tokens=12))

    out = build_embedding.run(
        {"raw_question": "q", "total_tokens_used": 100}, client=client
    )

    assert out["total_tokens_used"] == 112


def test_run_handles_missing_usage_gracefully():
    response = SimpleNamespace(data=[SimpleNamespace(embedding=[0.01] * 1536)])
    client = _make_client(response)

    out = build_embedding.run({"raw_question": "q"}, client=client)

    assert out["total_tokens_used"] == 0


def test_run_seeds_total_tokens_when_state_missing_key():
    """
    First-call case: state has no `total_tokens_used` yet (Node 2 may run
    before Node 1 in tests that mock just this step).
    """
    client = _make_client(_make_response(total_tokens=7))

    out = build_embedding.run({"raw_question": "q"}, client=client)

    assert out["total_tokens_used"] == 7
