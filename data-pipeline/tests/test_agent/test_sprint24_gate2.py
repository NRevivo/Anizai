"""
Gate 2 integration test for the follow-up subgraph (Sprint 24, T24.11).

Drives the compiled graph end-to-end — load_context → answer_from_context →
write_message — with the OpenAI client and firestore_client mocked (no
emulator, no real LLM). Verifies state flows correctly through all three
nodes and that both branches produce the right assistant write:

    - sufficient context (classification=answerable) → the model's answer is
      written as the assistant message;
    - insufficient context (classification=insufficient_evidence) → the fixed
      transparent message is written.

Mock boundaries:
    - load_context reads: firestore_client.get_session_result /
      get_session_evidence / get_recent_messages.
    - answer_from_context LLM: patch _get_default_client (the graph invokes
      node.run(state) with no injected client, so we mock the module's
      default client factory).
    - write_message: firestore_client.claim_and_write_followup_answer captures
      the assistant payload.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent import firestore_client
from agent.followup.graph import graph as followup_graph
from agent.followup.nodes import answer_from_context, generate_suggested_actions
from agent.prompts import followup as followup_prompt


# ==========================================================
# Helpers
# ==========================================================
def _make_response(output: dict) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=json.dumps(output)))
        ],
        usage=SimpleNamespace(
            prompt_tokens=200, completion_tokens=100, total_tokens=300
        ),
    )


def _output(*, classification: str, answer: str) -> dict:
    return {
        "classification": classification,
        "classification_reason": "test",
        "answer": answer,
    }


def _suggestions_response() -> SimpleNamespace:
    return _make_response({"actions": [
        {"label": "First next question", "prompt": "What is the first next question?"},
        {"label": "Second next question", "prompt": "What is the second next question?"},
        {"label": "Third next question", "prompt": "What is the third next question?"},
    ]})


def _suggestions_client() -> MagicMock:
    client = MagicMock()
    client.chat.completions.create = MagicMock(return_value=_suggestions_response())
    return client


def _wire_common(monkeypatch, captured):
    """Mock the context reads + the assistant-write capture shared by both
    branches. Returns nothing; mutates `captured`."""
    monkeypatch.setattr(
        firestore_client, "get_session_result",
        lambda sid: {"finalProbability": 0.72, "confidence": 0.81},
    )
    monkeypatch.setattr(
        firestore_client, "get_session_evidence",
        lambda sid: [
            {"evidence_id": "e1", "source_type": "vault_news",
             "title": "T", "snippet": "s", "rank": 1, "relevance_score": 0.8},
        ],
    )
    monkeypatch.setattr(
        firestore_client, "get_recent_messages", lambda sid, n: []
    )

    def _fake_claim(session_id, user_message_id, assistant_message):
        captured["session_id"] = session_id
        captured["user_message_id"] = user_message_id
        captured["assistant_message"] = assistant_message
        return True

    monkeypatch.setattr(
        firestore_client, "claim_and_write_followup_answer", _fake_claim
    )
    monkeypatch.setattr(
        generate_suggested_actions.shared_actions,
        "_get_default_client",
        _suggestions_client,
    )


def _initial_state() -> dict:
    return {
        "parent_session_id": "sess-1",
        "trigger_message_id": "msg-1",
        "trigger_question": "Why is the confidence so high?",
    }


# ==========================================================
# Gate 2 — both branches through the compiled graph
# ==========================================================
def test_graph_sufficient_context_writes_model_answer(monkeypatch):
    captured = {}
    _wire_common(monkeypatch, captured)
    client = MagicMock()
    client.chat.completions.create = MagicMock(
        return_value=_make_response(
            _output(classification="answerable", answer="Dense, consistent evidence.")
        )
    )
    monkeypatch.setattr(answer_from_context, "_get_default_client", lambda: client)
    final_state = followup_graph.invoke(_initial_state())

    assert final_state["response_text"] == "Dense, consistent evidence."
    # State flowed through load_context → answer → write_message.
    assert captured["user_message_id"] == "msg-1"
    assert captured["assistant_message"]["content"] == "Dense, consistent evidence."
    assert captured["assistant_message"]["replyToMessageId"] == "msg-1"
    assert captured["assistant_message"]["suggestedActions"][0]["id"] == "fu-sa-1"


def test_graph_insufficient_context_writes_transparent_message(monkeypatch):
    captured = {}
    _wire_common(monkeypatch, captured)
    client = MagicMock()
    client.chat.completions.create = MagicMock(
        return_value=_make_response(
            _output(classification="insufficient_evidence", answer="")
        )
    )
    monkeypatch.setattr(answer_from_context, "_get_default_client", lambda: client)

    final_state = followup_graph.invoke(_initial_state())

    assert final_state["response_text"] == followup_prompt.INSUFFICIENT_EVIDENCE_MESSAGE
    assert (
        captured["assistant_message"]["content"]
        == followup_prompt.INSUFFICIENT_EVIDENCE_MESSAGE
    )


def test_graph_accumulates_cost_through_answer_node(monkeypatch):
    captured = {}
    _wire_common(monkeypatch, captured)
    client = MagicMock()
    client.chat.completions.create = MagicMock(
        return_value=_make_response(
            _output(classification="answerable", answer="ok")
        )
    )
    monkeypatch.setattr(answer_from_context, "_get_default_client", lambda: client)
    monkeypatch.setattr(
        answer_from_context.llm_cost, "record_usage",
        lambda model, response, *, site: (300, 0.0007)
        if site == "answer_from_context"
        else (300, 0.0),
    )

    final_state = followup_graph.invoke(_initial_state())
    assert final_state["total_cost_usd"] == pytest.approx(0.0007)
