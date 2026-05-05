"""
Gate 1 unit tests for Sprint 21 — Tier 2 + Clarification Flow (T21.9).

Grows with each bundle. All tests use pure mocks at the SDK boundary;
no real OpenAI, Firestore, or network calls.

Bundle A (T21.1, T21.2):
  - MARGIN_THRESHOLD adjusted to 0.10 (OQ-1)
  - ClarificationCandidate shaping via the ambiguous run() return path
  - write_clarification node: success path, missing inputs, Firestore mock

Bundle B (T21.3, T21.4, T21.5):
  - _route_after_query_understand routing function
  - process_query _build_initial_state: non-null + null chosenCandidateId paths
  - skip_matching_step early return in query_understand (no LLM call)

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


# ==========================================================
# T21.3 — _route_after_query_understand routing function
# ==========================================================

from agent.graph import _route_after_query_understand, NODE_WRITE_CLARIFICATION, NODE_BUILD_EMBEDDING  # noqa: E402


def test_routing_fn_returns_write_clarification_when_ambiguous():
    assert _route_after_query_understand({"awaiting_clarification": True}) == NODE_WRITE_CLARIFICATION


def test_routing_fn_returns_build_embedding_when_clear():
    assert _route_after_query_understand({"awaiting_clarification": False}) == NODE_BUILD_EMBEDDING


def test_routing_fn_returns_build_embedding_when_flag_absent():
    """Flag absent defaults to non-ambiguous (normal first-time runs)."""
    assert _route_after_query_understand({}) == NODE_BUILD_EMBEDDING


def test_routing_fn_reads_only_state_no_side_effects():
    """Routing functions must not mutate state or call external services."""
    state = {"awaiting_clarification": True, "some_other_field": "value"}
    _route_after_query_understand(state)
    assert state == {"awaiting_clarification": True, "some_other_field": "value"}


# ==========================================================
# T21.5 — skip_matching_step early return in query_understand
# ==========================================================

def test_skip_matching_step_returns_without_llm_call():
    """When skip_matching_step=True, run() returns without calling OpenAI."""
    client = MagicMock()
    state = {
        "skip_matching_step": True,
        "structured_intent": {"intent": "forecast", "entities": ["Fed"]},
    }
    out = query_understand.run(state, client=client)
    client.chat.completions.create.assert_not_called()
    assert out["awaiting_clarification"] is False


def test_skip_matching_step_clears_clarification_fields():
    """Resume path returns None for clarification fields so the graph stays clean."""
    client = MagicMock()
    out = query_understand.run({"skip_matching_step": True}, client=client)
    assert out["clarification_candidates"] is None
    assert out["clarification_needed"] is None


def test_skip_matching_step_false_still_calls_llm():
    """When skip_matching_step=False, the normal LLM path runs."""
    import json
    from types import SimpleNamespace

    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps({"candidates": [_make_candidate(confidence=0.9)]})
        ))],
        usage=SimpleNamespace(total_tokens=100),
    )
    client = MagicMock()
    client.chat.completions.create.return_value = response

    query_understand.run(
        {"skip_matching_step": False, "raw_question": "Will the Fed cut rates?"},
        client=client,
    )
    client.chat.completions.create.assert_called_once()


# ==========================================================
# T21.4 — process_query._build_initial_state chosenCandidateId paths
# ==========================================================

from agent.process_query import _build_initial_state  # noqa: E402


def _stored_candidate(*, id_val="uuid-abc", entities=None, search_terms=None):
    return {
        "id": id_val,
        "label": "Federal Reserve, Rates",
        "source": "polymarket",
        "description": "Forecast about Fed rates",
        "matchConfidence": 0.82,
        "intent": "forecast",
        "domain": "macro",
        "entities": entities or ["Federal Reserve"],
        "polymarket_search_terms": search_terms or ["fed rate cut"],
    }


@patch("agent.process_query.get_query_doc")
@patch("agent.process_query.get_session_doc")
def test_build_initial_state_normal_run_no_chosen_id(mock_session, mock_query):
    """No chosenCandidateId in doc → plain initial state with just session_id."""
    mock_query.return_value = {"status": "pending", "question": "q"}
    result = _build_initial_state("session-1")
    assert result == {"session_id": "session-1"}
    mock_session.assert_not_called()


@patch("agent.process_query.get_query_doc")
@patch("agent.process_query.get_session_doc")
def test_build_initial_state_doc_missing(mock_session, mock_query):
    """get_query_doc returns None → safe fallback to plain state."""
    mock_query.return_value = None
    result = _build_initial_state("session-1")
    assert result == {"session_id": "session-1"}


@patch("agent.process_query.get_query_doc")
@patch("agent.process_query.get_session_doc")
def test_build_initial_state_null_chosen_id_means_freeform(mock_session, mock_query):
    """chosenCandidateId=null → skip_matching_step=True, has_market_question_intent=False."""
    mock_query.return_value = {"chosenCandidateId": None}
    result = _build_initial_state("session-1")
    assert result["skip_matching_step"] is True
    assert result["chosen_candidate_id"] is None
    assert result["structured_intent"]["has_market_question_intent"] is False
    mock_session.assert_not_called()


@patch("agent.process_query.get_query_doc")
@patch("agent.process_query.get_session_doc")
def test_build_initial_state_non_null_chosen_id_recovers_candidate(mock_session, mock_query):
    """chosenCandidateId=UUID → reads session doc, recovers structured_intent."""
    mock_query.return_value = {"chosenCandidateId": "uuid-abc"}
    mock_session.return_value = {
        "clarificationCandidates": [
            _stored_candidate(id_val="uuid-abc", entities=["Fed"], search_terms=["rate cut"]),
        ]
    }
    result = _build_initial_state("session-1")
    assert result["skip_matching_step"] is True
    assert result["chosen_candidate_id"] == "uuid-abc"
    assert result["structured_intent"]["entities"] == ["Fed"]
    assert result["structured_intent"]["polymarket_search_terms"] == ["rate cut"]
    assert result["structured_intent"]["has_market_question_intent"] is True


@patch("agent.process_query.get_query_doc")
@patch("agent.process_query.get_session_doc")
def test_build_initial_state_unknown_candidate_id_falls_back_to_freeform(mock_session, mock_query):
    """chosenCandidateId not in stored candidates → freeform fallback (not a hard error)."""
    mock_query.return_value = {"chosenCandidateId": "uuid-xyz"}
    mock_session.return_value = {
        "clarificationCandidates": [
            _stored_candidate(id_val="uuid-abc"),  # different ID
        ]
    }
    result = _build_initial_state("session-1")
    assert result["skip_matching_step"] is True
    assert result["chosen_candidate_id"] == "uuid-xyz"
    assert result["structured_intent"]["has_market_question_intent"] is False


# ==========================================================
# T21.7 — synthesize.py tier inference from market_evidence
# ==========================================================

from agent.nodes import synthesize  # noqa: E402
from agent import firestore_client as _fc  # noqa: E402


def _synth_state(**overrides):
    base = {"raw_question": "Will the Fed cut rates in 2026?"}
    base.update(overrides)
    return base


def _mock_synth_client():
    import json
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    payload = {
        "final_probability": 0.55,
        "confidence": 0.65,
        "consensus_score": 0.6,
        "bottom_line_answer": "Moderate chance.",
        "detailed_explanation": "Based on signals...",
        "summary_markdown": "## Summary\nModerate.",
        "market_comparison_insight": "Market suggests caution.",
        "sentiment_analysis_insight": "Mixed sentiment.",
        "evidence_feed_summary": "16 items analyzed.",
        "key_factors": [],
        "what_i_didnt_find": [],
        "reasoning_chain": [],
        "evidence_overlay": [],
    }
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
        usage=SimpleNamespace(total_tokens=1500),
    )
    client = MagicMock()
    client.chat.completions.create.return_value = response
    return client


def test_synthesize_infers_tier_2_when_market_evidence_polymarket_is_none():
    """No polymarket in market_evidence → tier_2."""
    state = _synth_state(market_evidence={"polymarket": None, "fred_anomalies": [], "empty": True})
    out = synthesize.run(state, client=_mock_synth_client())
    assert out["tier"] == "tier_2"
    assert out["synthesis_result"]["tier"] == "tier_2"


def test_synthesize_infers_tier_1_when_market_evidence_has_polymarket():
    """Polymarket data present in market_evidence → tier_1."""
    state = _synth_state(market_evidence={
        "polymarket": {"current_odds": 0.72, "market_slug": "fed-cut"},
        "fred_anomalies": [], "empty": False,
    })
    out = synthesize.run(state, client=_mock_synth_client())
    assert out["tier"] == "tier_1"
    assert out["synthesis_result"]["tier"] == "tier_1"


def test_synthesize_infers_tier_2_when_market_evidence_absent():
    """No market_evidence key in state → tier_2 (safe default for cold start)."""
    out = synthesize.run(_synth_state(), client=_mock_synth_client())
    assert out["tier"] == "tier_2"


def test_synthesize_tier2_sets_spec_exact_caption():
    """Tier 2 marketComparisonInsight is the spec-exact string (§8.2.2)."""
    state = _synth_state(market_evidence={"polymarket": None, "empty": True})
    out = synthesize.run(state, client=_mock_synth_client())
    assert out["synthesis_result"]["marketComparisonInsight"] == (
        "No canonical market available — freeform analysis."
    )


def test_synthesize_agent_version_bumped_to_sprint_21():
    assert synthesize.AGENT_VERSION == "0.4.0-sprint21-clarification-tier2"


# ==========================================================
# T21.8 — write_to_firestore writes tier to session doc
# ==========================================================

from agent.nodes import write_to_firestore  # noqa: E402


@patch("agent.nodes.write_to_firestore.firestore_client")
def test_write_to_firestore_passes_tier_to_session_status_update(mock_fc):
    """When state.tier is set, it is included in the session doc update."""
    mock_fc.write_evidence_batch.return_value = 0
    mock_fc.write_prediction_series.return_value = 0
    mock_fc.write_sentiment_time_series.return_value = 0

    state = {
        "session_id": "s1",
        "synthesis_result": {"finalProbability": 0.7, "tier": "tier_2"},
        "tier": "tier_2",
    }
    write_to_firestore.run(state)

    mock_fc.update_session_status.assert_called_once_with("s1", "done", tier="tier_2")


@patch("agent.nodes.write_to_firestore.firestore_client")
def test_write_to_firestore_passes_none_tier_when_tier_absent(mock_fc):
    """When state has no tier field, tier=None is passed (safe default)."""
    mock_fc.write_evidence_batch.return_value = 0
    mock_fc.write_prediction_series.return_value = 0
    mock_fc.write_sentiment_time_series.return_value = 0

    state = {
        "session_id": "s1",
        "synthesis_result": {"finalProbability": 0.7},
    }
    write_to_firestore.run(state)

    mock_fc.update_session_status.assert_called_once_with("s1", "done", tier=None)
