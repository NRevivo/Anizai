"""
Gate 1 unit tests for the Sprint 24 follow-up subgraph (T24.10 + the unit
half of T24.14).

Strategy (matches the existing node tests):
    - answer_from_context: pure-mock at the OpenAI client boundary via the
      node's injected `client` kwarg; fake openai>=1.x response objects. No
      real gpt-4o-mini calls.
    - load_context / write_message: pure-mock at the firestore_client
      function boundary (the nodes never touch Firestore directly — all
      access is centralized there, hub-principles P2).

Coverage:
    state.py     — FollowupState importable; the 8 ratified fields.
    followup.py  — build_user_message renders the question + evidence; fixed
                   copy constants present; RESPONSE_SCHEMA is strict.
    load_context — top-5 by rank; relevance fallback; graceful empty on
                   missing parent result; raises on missing parent_session_id.
    answer_from_context — answerable→model text; insufficient/out_of_scope→
                   fixed copy; empty-answer fallback; unexpected class→
                   insufficient; cost accumulation (born instrumented);
                   budget timeout→complete caveat; non-timeout error→raises;
                   missing trigger_question→raises.
    write_message — payload carries replyToMessageId==trigger_message_id +
                   agentVersion; identified by trigger_message_id (never
                   positional); race-loser no-op; missing fields raise.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent import firestore_client
from agent.errors import AgentProcessingError
from agent.followup.nodes import answer_from_context, load_context, write_message
from agent.followup.state import FollowupState
from agent.prompts import followup as followup_prompt


# ==========================================================
# Fakes / helpers
# ==========================================================
def _make_response(output: dict, *, total_tokens: int = 300) -> SimpleNamespace:
    """openai>=1.x ChatCompletion shape carrying a follow-up output payload."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=json.dumps(output)))
        ],
        usage=SimpleNamespace(
            prompt_tokens=200, completion_tokens=100, total_tokens=total_tokens
        ),
    )


def _client_returning(response) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create = MagicMock(return_value=response)
    return client


def _client_raising(exc: Exception) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create = MagicMock(side_effect=exc)
    return client


def _output(
    *,
    classification: str = "answerable",
    classification_reason: str = "on-topic",
    answer: str = "Because the evidence was dense and consistent.",
) -> dict:
    return {
        "classification": classification,
        "classification_reason": classification_reason,
        "answer": answer,
    }


def _followup_state(**overrides) -> dict:
    base = {
        "parent_session_id": "sess-1",
        "trigger_message_id": "msg-1",
        "trigger_question": "Why is the confidence so high?",
        "parent_session_result": {"finalProbability": 0.72, "confidence": 0.81},
        "parent_evidence": [],
        "message_history": [],
    }
    base.update(overrides)
    return base


def _evidence(evidence_id: str, *, rank: int = 0, relevance: float = 0.5) -> dict:
    return {
        "evidence_id": evidence_id,
        "source_type": "vault_news",
        "title": f"Title {evidence_id}",
        "snippet": "snippet",
        "rank": rank,
        "relevance_score": relevance,
    }


# ==========================================================
# state.py — FollowupState
# ==========================================================
def test_followup_state_has_eight_ratified_fields():
    annotations = FollowupState.__annotations__
    assert set(annotations) == {
        "parent_session_id",
        "trigger_message_id",
        "trigger_question",
        "message_history",
        "parent_session_result",
        "parent_evidence",
        "response_text",
        "total_cost_usd",
    }


# ==========================================================
# prompts/followup.py
# ==========================================================
def test_prompt_fixed_messages_present_and_distinct():
    assert followup_prompt.INSUFFICIENT_EVIDENCE_MESSAGE
    assert followup_prompt.OUT_OF_SCOPE_MESSAGE
    assert (
        followup_prompt.INSUFFICIENT_EVIDENCE_MESSAGE
        != followup_prompt.OUT_OF_SCOPE_MESSAGE
    )


def test_prompt_schema_is_strict_with_three_required_fields():
    schema = followup_prompt.RESPONSE_SCHEMA
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False
    assert set(schema["schema"]["required"]) == {
        "classification",
        "classification_reason",
        "answer",
    }
    enum = schema["schema"]["properties"]["classification"]["enum"]
    assert set(enum) == {"answerable", "insufficient_evidence", "out_of_scope"}


def test_build_user_message_includes_question_and_evidence():
    msg = followup_prompt.build_user_message(
        question="Why so confident?",
        parent_session_result={"finalProbability": 0.72, "confidence": 0.81},
        parent_evidence=[_evidence("ev-1", rank=1)],
        message_history=[{"role": "user", "content": "earlier question"}],
    )
    assert "Why so confident?" in msg
    assert "Title ev-1" in msg
    assert "FOLLOW-UP QUESTION TO ANSWER" in msg
    # The question is rendered LAST (freshest in context).
    assert msg.rindex("Why so confident?") > msg.index("Title ev-1")


# ==========================================================
# load_context.py
# ==========================================================
def test_load_context_selects_top_five_by_rank(monkeypatch):
    # 7 items, ranks scrambled; ranks 0 (unused) must be excluded.
    evidence = [
        _evidence("a", rank=3),
        _evidence("b", rank=1),
        _evidence("c", rank=0),
        _evidence("d", rank=5),
        _evidence("e", rank=2),
        _evidence("f", rank=4),
        _evidence("g", rank=0),
    ]
    monkeypatch.setattr(firestore_client, "get_session_result", lambda sid: {"x": 1})
    monkeypatch.setattr(firestore_client, "get_session_evidence", lambda sid: evidence)
    monkeypatch.setattr(firestore_client, "get_recent_messages", lambda sid, n: [])

    out = load_context.run({"parent_session_id": "sess-1"})
    ids = [e["evidence_id"] for e in out["parent_evidence"]]
    # rank 1..5 in ascending order; rank-0 items dropped.
    assert ids == ["b", "e", "a", "f", "d"]


def test_load_context_falls_back_to_relevance_when_no_rank(monkeypatch):
    evidence = [
        _evidence("a", rank=0, relevance=0.2),
        _evidence("b", rank=0, relevance=0.9),
        _evidence("c", rank=0, relevance=0.5),
    ]
    monkeypatch.setattr(firestore_client, "get_session_result", lambda sid: {})
    monkeypatch.setattr(firestore_client, "get_session_evidence", lambda sid: evidence)
    monkeypatch.setattr(firestore_client, "get_recent_messages", lambda sid, n: [])

    out = load_context.run({"parent_session_id": "sess-1"})
    ids = [e["evidence_id"] for e in out["parent_evidence"]]
    assert ids == ["b", "c", "a"]  # relevance desc


def test_load_context_graceful_when_parent_result_missing(monkeypatch):
    monkeypatch.setattr(firestore_client, "get_session_result", lambda sid: None)
    monkeypatch.setattr(firestore_client, "get_session_evidence", lambda sid: [])
    monkeypatch.setattr(firestore_client, "get_recent_messages", lambda sid, n: [])

    out = load_context.run({"parent_session_id": "sess-1"})
    assert out["parent_session_result"] == {}
    assert out["parent_evidence"] == []


def test_load_context_requires_parent_session_id():
    with pytest.raises(ValueError):
        load_context.run({})


# ==========================================================
# answer_from_context.py
# ==========================================================
def test_answer_answerable_uses_model_text():
    client = _client_returning(_make_response(_output(answer="Dense evidence.")))
    out = answer_from_context.run(_followup_state(), client=client)
    assert out["response_text"] == "Dense evidence."


def test_answer_insufficient_uses_fixed_copy():
    client = _client_returning(
        _make_response(_output(classification="insufficient_evidence", answer=""))
    )
    out = answer_from_context.run(_followup_state(), client=client)
    assert out["response_text"] == followup_prompt.INSUFFICIENT_EVIDENCE_MESSAGE


def test_answer_out_of_scope_uses_fixed_copy():
    client = _client_returning(
        _make_response(_output(classification="out_of_scope", answer=""))
    )
    out = answer_from_context.run(_followup_state(), client=client)
    assert out["response_text"] == followup_prompt.OUT_OF_SCOPE_MESSAGE


def test_answer_answerable_but_empty_falls_back_to_insufficient():
    client = _client_returning(_make_response(_output(answer="   ")))
    out = answer_from_context.run(_followup_state(), client=client)
    assert out["response_text"] == followup_prompt.INSUFFICIENT_EVIDENCE_MESSAGE


def test_answer_unexpected_classification_defaults_to_insufficient():
    client = _client_returning(
        _make_response(_output(classification="banana", answer="x"))
    )
    out = answer_from_context.run(_followup_state(), client=client)
    assert out["response_text"] == followup_prompt.INSUFFICIENT_EVIDENCE_MESSAGE


def test_answer_accumulates_cost_onto_prior(monkeypatch):
    # Force a deterministic cost so the accumulation is assertable.
    monkeypatch.setattr(
        answer_from_context.llm_cost, "record_usage",
        lambda model, response, *, site: (300, 0.0007),
    )
    client = _client_returning(_make_response(_output()))
    out = answer_from_context.run(
        _followup_state(total_cost_usd=0.0003), client=client
    )
    assert out["total_cost_usd"] == pytest.approx(0.0010)


def test_answer_budget_timeout_returns_complete_caveat():
    import httpx
    from openai import APITimeoutError

    timeout_exc = APITimeoutError(request=httpx.Request("POST", "https://api.openai.com"))
    client = _client_raising(timeout_exc)
    out = answer_from_context.run(_followup_state(), client=client)
    assert out["response_text"] == answer_from_context.TIMEOUT_CAVEAT_MESSAGE
    # No billable response on timeout — cost untouched.
    assert "total_cost_usd" not in out


def test_answer_non_timeout_error_raises():
    client = _client_raising(RuntimeError("500 server error"))
    with pytest.raises(AgentProcessingError):
        answer_from_context.run(_followup_state(), client=client)


def test_answer_requires_trigger_question():
    with pytest.raises(AgentProcessingError):
        answer_from_context.run(_followup_state(trigger_question="  "), client=MagicMock())


# ==========================================================
# write_message.py
# ==========================================================
def test_write_message_payload_and_claim_target(monkeypatch):
    captured = {}

    def _fake_claim(session_id, user_message_id, assistant_message):
        captured["session_id"] = session_id
        captured["user_message_id"] = user_message_id
        captured["assistant_message"] = assistant_message
        return True

    monkeypatch.setattr(
        firestore_client, "claim_and_write_followup_answer", _fake_claim
    )

    state = _followup_state(
        trigger_message_id="msg-42",
        response_text="Here is the answer.",
    )
    write_message.run(state)

    # Claim targets the specific triggering message id (never positional).
    assert captured["session_id"] == "sess-1"
    assert captured["user_message_id"] == "msg-42"
    payload = captured["assistant_message"]
    assert payload["role"] == "assistant"
    assert payload["content"] == "Here is the answer."
    assert payload["replyToMessageId"] == "msg-42"
    assert "agentVersion" in payload
    # No frontend-facing status stamped on the assistant message.
    assert "status" not in payload


def test_write_message_race_loser_is_noop(monkeypatch):
    monkeypatch.setattr(
        firestore_client,
        "claim_and_write_followup_answer",
        lambda *a, **k: False,
    )
    # Loser returns cleanly (no raise); response_text is still echoed.
    out = write_message.run(_followup_state(response_text="answer"))
    assert out["response_text"] == "answer"


@pytest.mark.parametrize(
    "missing",
    ["parent_session_id", "trigger_message_id", "response_text"],
)
def test_write_message_requires_fields(missing, monkeypatch):
    monkeypatch.setattr(
        firestore_client,
        "claim_and_write_followup_answer",
        lambda *a, **k: True,
    )
    state = _followup_state(response_text="answer")
    state.pop(missing, None)
    with pytest.raises(AgentProcessingError):
        write_message.run(state)
