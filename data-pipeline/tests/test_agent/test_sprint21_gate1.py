"""
Gate 1 unit tests for Sprint 21 — Tier 2 + Clarification Flow (T21.9).

Grows with each bundle. All tests use pure mocks at the SDK boundary;
no real OpenAI, Firestore, or network calls.

Bundle A (T21.1, T21.2) — in this initial commit:
  - MARGIN_THRESHOLD adjusted to 0.10 (OQ-1)
  - ClarificationCandidate shaping via the ambiguous run() return path
  - write_clarification node: success path, missing inputs, Firestore mock

Bundle B (T21.3, T21.4, T21.5) — tests added after bundle lands:
  - _route_after_query_understand routing function
  - process_query chosenCandidateId read paths (non-null + null)
  - skip_matching_step early return in query_understand

Bundle C (T21.6, T21.7, T21.8) — tests added after bundle lands:
  - synthesize tier inference from market_evidence.polymarket
  - Tier 2 marketComparisonInsight caption
  - write_to_firestore tier field on session doc update

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.2.3 (ambiguous routing)
    - data-pipeline/docs/agentic_hub_spec.md §8.2.4 (ClarificationCandidate)
    - data-pipeline/docs/agentic_hub_implementation.md T21.1-T21.9
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.errors import AgentProcessingError
from agent.nodes import query_understand, write_clarification


# ==========================================================
# Shared builder helpers (mirrors test_query_understand.py)
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
    payload = {"candidates": candidates}
    return SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))
        ],
        usage=SimpleNamespace(total_tokens=total_tokens),
    )


def _make_client(response: SimpleNamespace) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = response
    return client


# ==========================================================
# T21.1 — MARGIN_THRESHOLD
# ==========================================================

def test_margin_threshold_constant_is_0_10():
    """Regression guard: OQ-1 set MARGIN_THRESHOLD to 0.10 per spec §8.2.3."""
    assert query_understand.MARGIN_THRESHOLD == 0.10


def test_clarifies_when_delta_below_margin():
    """delta=0.09 < 0.10 → route to clarification."""
    response = _make_response(
        candidates=[
            _make_candidate(confidence=0.80, entities=["Bitcoin"]),
            _make_candidate(confidence=0.71, entities=["Bitcoin Cash"]),
        ]
    )
    out = query_understand.run({"raw_question": "BTC?"}, client=_make_client(response))
    assert out["awaiting_clarification"] is True


def test_auto_picks_when_delta_just_above_margin():
    """delta=0.11 > MARGIN_THRESHOLD=0.10 → auto-pick.

    Uses 0.86/0.75 not 0.85/0.75 because 0.85-0.75 = 0.0999... in IEEE-754
    (< 0.10), which would route to clarification — a float-precision trap
    rather than a useful boundary test. 0.86-0.75=0.11 is clean.
    """
    response = _make_response(
        candidates=[
            _make_candidate(confidence=0.86, entities=["Bitcoin"]),
            _make_candidate(confidence=0.75, entities=["Ethereum"]),
        ]
    )
    out = query_understand.run({"raw_question": "BTC?"}, client=_make_client(response))
    assert out["awaiting_clarification"] is False
    assert out["structured_intent"]["entities"] == ["Bitcoin"]


def test_auto_picks_when_delta_well_above_margin():
    """delta=0.20 > 0.10 → auto-pick (unchanged from Sprint 19 behavior)."""
    response = _make_response(
        candidates=[
            _make_candidate(confidence=0.90, entities=["Bitcoin"]),
            _make_candidate(confidence=0.70, entities=["Ethereum"]),
        ]
    )
    out = query_understand.run({"raw_question": "BTC?"}, client=_make_client(response))
    assert out["awaiting_clarification"] is False


def test_clarifies_when_delta_just_below_margin():
    """delta=0.09 < 0.10 → triggers clarification; was auto-pick at old 0.15 threshold."""
    response = _make_response(
        candidates=[
            _make_candidate(confidence=0.84, entities=["Fed Funds Rate"]),
            _make_candidate(confidence=0.75, entities=["Fed Balance Sheet"]),
        ]
    )
    out = query_understand.run({"raw_question": "Fed question"}, client=_make_client(response))
    assert out["awaiting_clarification"] is True


# ==========================================================
# T21.1 — ClarificationCandidate shaping
# ==========================================================

def test_clarification_candidates_have_uuid_id():
    """id field is a non-empty string (UUID4 format) for each candidate."""
    response = _make_response(
        candidates=[
            _make_candidate(confidence=0.80, entities=["Bitcoin"]),
            _make_candidate(confidence=0.75, entities=["Ethereum"]),
        ]
    )
    out = query_understand.run({"raw_question": "crypto?"}, client=_make_client(response))
    assert out["awaiting_clarification"] is True

    candidates = out["clarification_candidates"]
    assert len(candidates) == 2
    for c in candidates:
        assert "id" in c
        assert isinstance(c["id"], str)
        assert len(c["id"]) == 36  # UUID4 canonical form: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
        assert c["id"].count("-") == 4


def test_clarification_candidate_ids_are_unique():
    """Each candidate gets a distinct UUID; UUIDs are not reused across candidates."""
    response = _make_response(
        candidates=[
            _make_candidate(confidence=0.80, entities=["A"]),
            _make_candidate(confidence=0.75, entities=["B"]),
            _make_candidate(confidence=0.55, entities=["C"]),
        ]
    )
    out = query_understand.run({"raw_question": "q"}, client=_make_client(response))
    ids = [c["id"] for c in out["clarification_candidates"]]
    assert len(set(ids)) == len(ids)


def test_clarification_candidate_has_required_frontend_fields():
    """Every shaped candidate contains all ClarificationCandidate schema fields."""
    response = _make_response(
        candidates=[
            _make_candidate(
                confidence=0.80,
                entities=["Federal Reserve", "Interest Rates"],
                polymarket_search_terms=["fed rate cut 2026"],
            ),
            _make_candidate(confidence=0.75, entities=["FOMC"]),
        ]
    )
    out = query_understand.run({"raw_question": "fed?"}, client=_make_client(response))
    c = out["clarification_candidates"][0]
    for field in ("id", "label", "source", "description", "matchConfidence"):
        assert field in c, f"Missing frontend field: {field}"


def test_clarification_candidate_source_is_polymarket():
    """source field is always 'polymarket' pre-Phase-7 (KG-PHASE8-12)."""
    response = _make_response(
        candidates=[
            _make_candidate(confidence=0.80),
            _make_candidate(confidence=0.75),
        ]
    )
    out = query_understand.run({"raw_question": "q"}, client=_make_client(response))
    for c in out["clarification_candidates"]:
        assert c["source"] == "polymarket"


def test_clarification_candidate_match_confidence_from_llm():
    """matchConfidence echoes the LLM candidate's self-rated confidence."""
    response = _make_response(
        candidates=[
            _make_candidate(confidence=0.82, entities=["Bitcoin"]),
            _make_candidate(confidence=0.74, entities=["Ethereum"]),
        ]
    )
    out = query_understand.run({"raw_question": "q"}, client=_make_client(response))
    assert out["clarification_candidates"][0]["matchConfidence"] == pytest.approx(0.82)
    assert out["clarification_candidates"][1]["matchConfidence"] == pytest.approx(0.74)


def test_clarification_candidate_label_derived_from_entities():
    """label is a comma-joined string of up to the first 3 entities."""
    entities = ["Federal Reserve", "Interest Rates", "FOMC", "QE"]
    response = _make_response(
        candidates=[
            _make_candidate(confidence=0.80, entities=entities),
            _make_candidate(confidence=0.75),
        ]
    )
    out = query_understand.run({"raw_question": "q"}, client=_make_client(response))
    label = out["clarification_candidates"][0]["label"]
    # Top 3 entities joined
    assert "Federal Reserve" in label
    assert "Interest Rates" in label
    assert "FOMC" in label
    # 4th entity NOT in label (truncated at 3)
    assert "QE" not in label


def test_clarification_candidate_label_fallback_when_no_entities():
    """label falls back to a non-empty string when entities list is empty."""
    response = _make_response(
        candidates=[
            _make_candidate(confidence=0.80, entities=[]),
            _make_candidate(confidence=0.75),
        ]
    )
    out = query_understand.run({"raw_question": "q"}, client=_make_client(response))
    label = out["clarification_candidates"][0]["label"]
    assert isinstance(label, str)
    assert label  # non-empty


def test_clarification_candidate_has_hub_recovery_fields():
    """Hub-recovery fields (intent, domain, entities, polymarket_search_terms) present."""
    response = _make_response(
        candidates=[
            _make_candidate(
                confidence=0.80,
                intent="forecast",
                domain="crypto",
                entities=["Bitcoin"],
                polymarket_search_terms=["btc 100k 2026"],
            ),
            _make_candidate(confidence=0.75),
        ]
    )
    out = query_understand.run({"raw_question": "q"}, client=_make_client(response))
    c = out["clarification_candidates"][0]
    assert c["intent"] == "forecast"
    assert c["domain"] == "crypto"
    assert c["entities"] == ["Bitcoin"]
    assert c["polymarket_search_terms"] == ["btc 100k 2026"]


def test_clarification_candidate_hub_recovery_null_search_terms():
    """polymarket_search_terms preserved as None when not provided by LLM."""
    response = _make_response(
        candidates=[
            _make_candidate(
                confidence=0.80,
                polymarket_search_terms=None,
                has_market_question_intent=False,
            ),
            _make_candidate(confidence=0.75),
        ]
    )
    out = query_understand.run({"raw_question": "q"}, client=_make_client(response))
    assert out["clarification_candidates"][0]["polymarket_search_terms"] is None


def test_clarification_needed_contains_shaped_candidates():
    """clarification_needed.candidates also contains the shaped (not raw) candidates."""
    response = _make_response(
        candidates=[
            _make_candidate(confidence=0.80, entities=["A"]),
            _make_candidate(confidence=0.75, entities=["B"]),
        ]
    )
    out = query_understand.run({"raw_question": "q"}, client=_make_client(response))
    needed = out["clarification_needed"]["candidates"]
    assert all("id" in c for c in needed), "clarification_needed should use shaped candidates"
    assert all("source" in c for c in needed)


# ==========================================================
# T21.2 — write_clarification node
# ==========================================================

_CANDIDATES_FIXTURE = [
    {
        "id": "uuid-abc",
        "label": "Federal Reserve, Interest Rates",
        "source": "polymarket",
        "description": "Forecast question about fed rate cut",
        "matchConfidence": 0.80,
        "intent": "forecast",
        "domain": "macro",
        "entities": ["Federal Reserve"],
        "polymarket_search_terms": ["fed rate cut"],
    },
    {
        "id": "uuid-def",
        "label": "FOMC",
        "source": "polymarket",
        "description": "Forecast question about FOMC",
        "matchConfidence": 0.75,
        "intent": "forecast",
        "domain": "macro",
        "entities": ["FOMC"],
        "polymarket_search_terms": None,
    },
]


@patch("agent.nodes.write_clarification.firestore_client")
def test_write_clarification_success_calls_update_session_status(mock_fc):
    state = {
        "session_id": "session-123",
        "clarification_candidates": _CANDIDATES_FIXTURE,
        "errors": [],
    }
    out = write_clarification.run(state)

    mock_fc.update_session_status.assert_called_once_with(
        "session-123",
        "awaiting_clarification",
        clarification_candidates=_CANDIDATES_FIXTURE,
    )
    assert out == {"errors": []}


@patch("agent.nodes.write_clarification.firestore_client")
def test_write_clarification_success_calls_update_query_status(mock_fc):
    state = {
        "session_id": "session-123",
        "clarification_candidates": _CANDIDATES_FIXTURE,
    }
    write_clarification.run(state)

    mock_fc.update_query_status.assert_called_once_with(
        "session-123",
        "awaiting_clarification",
    )


@patch("agent.nodes.write_clarification.firestore_client")
def test_write_clarification_firestore_write_order(mock_fc):
    """session update (with candidates) must precede query status update."""
    call_order = []
    mock_fc.update_session_status.side_effect = lambda *a, **kw: call_order.append("session")
    mock_fc.update_query_status.side_effect = lambda *a, **kw: call_order.append("query")

    write_clarification.run({
        "session_id": "s1",
        "clarification_candidates": _CANDIDATES_FIXTURE,
    })

    assert call_order == ["session", "query"]


@patch("agent.nodes.write_clarification.firestore_client")
def test_write_clarification_raises_on_missing_session_id(_mock_fc):
    with pytest.raises(AgentProcessingError) as exc_info:
        write_clarification.run({"clarification_candidates": _CANDIDATES_FIXTURE})
    assert "missing session_id" in str(exc_info.value)


@patch("agent.nodes.write_clarification.firestore_client")
def test_write_clarification_raises_on_missing_candidates(_mock_fc):
    with pytest.raises(AgentProcessingError) as exc_info:
        write_clarification.run({"session_id": "s1"})
    assert "missing clarification_candidates" in str(exc_info.value)


@patch("agent.nodes.write_clarification.firestore_client")
def test_write_clarification_raises_on_empty_candidates(_mock_fc):
    with pytest.raises(AgentProcessingError) as exc_info:
        write_clarification.run({"session_id": "s1", "clarification_candidates": []})
    assert "missing clarification_candidates" in str(exc_info.value)


@patch("agent.nodes.write_clarification.firestore_client")
def test_write_clarification_returns_errors_echo(mock_fc):
    """Node must return at least one state field; echoes errors for identity write."""
    state = {
        "session_id": "s1",
        "clarification_candidates": _CANDIDATES_FIXTURE,
        "errors": ["prior-error"],
    }
    out = write_clarification.run(state)
    assert out == {"errors": ["prior-error"]}


@patch("agent.nodes.write_clarification.firestore_client")
def test_write_clarification_no_agentevents_written(mock_fc):
    """Sprint 19 D2: no agentEvents writes until Sprint 25."""
    write_clarification.run({
        "session_id": "s1",
        "clarification_candidates": _CANDIDATES_FIXTURE,
    })
    # Only two Firestore calls allowed: update_session_status + update_query_status
    assert mock_fc.update_session_status.call_count == 1
    assert mock_fc.update_query_status.call_count == 1
    # No evidence writes, no session result writes, no agentEvents
    assert mock_fc.write_evidence_batch.call_count == 0
    assert mock_fc.write_session_result.call_count == 0
