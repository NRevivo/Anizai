"""
Gate 1 tests for `agent/nodes/vault_query.py` (T19.8).

Strategy: pure-mock at the agent boundary via `patch.object(<vault_query
module>.<agent_module>, 'run', ...)`. We never run the real agents — that
would require hitting Postgres. The point of this gate is to verify the
parallel-dispatch plumbing, state wiring, and error semantics, not the
agents themselves (each has its own Gate 1 suite).

Coverage:
- happy path (all three agents called, evidence written to state)
- query_embedding forwarded to Researcher + Pulse Analyst
- entities forwarded to Market Bridge from structured_intent
- polymarket_slug=None / canonical_event_id=None always (KG-PHASE8-12 guard)
- now kwarg forwarded to all three agents
- vault_query_attempts increments from 0 / from existing
- missing query_embedding → AgentProcessingError
- empty list query_embedding → AgentProcessingError
- Researcher failure → AgentProcessingError (fail-fast)
- Pulse Analyst failure → AgentProcessingError
- Market Bridge failure → AgentProcessingError
- Per-agent timeout → AgentProcessingError
- All three agents dispatched in parallel (verifies thread submission,
  not strict ordering)
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from agent.errors import AgentProcessingError
from agent.nodes import vault_query


# ==========================================================
# Fixtures
# ==========================================================
def _make_embedding() -> list[float]:
    return [0.01] * 1536


def _make_state(**overrides) -> dict:
    base = {
        "query_embedding": _make_embedding(),
        "structured_intent": {
            "entities": ["Federal Reserve", "Bitcoin"],
            "intent": "forecast",
            "domain": "macro",
        },
    }
    base.update(overrides)
    return base


# ==========================================================
# Happy paths
# ==========================================================
def test_run_dispatches_all_three_agents():
    with patch.object(vault_query.researcher, "run", return_value={"empty": False, "articles": []}) as r, \
         patch.object(vault_query.pulse_analyst, "run", return_value={"empty": False}) as p, \
         patch.object(vault_query.market_bridge, "run", return_value={"empty": False}) as m:
        vault_query.run(_make_state())

    r.assert_called_once()
    p.assert_called_once()
    m.assert_called_once()


def test_run_writes_three_evidence_keys():
    with patch.object(vault_query.researcher, "run", return_value={"researcher": "evidence"}), \
         patch.object(vault_query.pulse_analyst, "run", return_value={"pulse": "evidence"}), \
         patch.object(vault_query.market_bridge, "run", return_value={"market": "evidence"}):
        out = vault_query.run(_make_state())

    assert out["researcher_evidence"] == {"researcher": "evidence"}
    assert out["pulse_evidence"] == {"pulse": "evidence"}
    assert out["market_evidence"] == {"market": "evidence"}


def test_run_forwards_query_embedding_to_researcher_and_pulse():
    embedding = _make_embedding()
    with patch.object(vault_query.researcher, "run", return_value={}) as r, \
         patch.object(vault_query.pulse_analyst, "run", return_value={}) as p, \
         patch.object(vault_query.market_bridge, "run", return_value={}):
        vault_query.run(_make_state(query_embedding=embedding))

    # First positional arg = query_embedding
    assert r.call_args.args[0] is embedding
    assert p.call_args.args[0] is embedding


def test_run_forwards_entities_to_market_bridge():
    state = _make_state()
    state["structured_intent"]["entities"] = ["NVIDIA", "TSMC"]

    with patch.object(vault_query.researcher, "run", return_value={}), \
         patch.object(vault_query.pulse_analyst, "run", return_value={}), \
         patch.object(vault_query.market_bridge, "run", return_value={}) as m:
        vault_query.run(state)

    assert m.call_args.kwargs["entities"] == ["NVIDIA", "TSMC"]


def test_run_passes_polymarket_slug_none_in_sprint19():
    """
    KG-PHASE8-12 guard: until Sprint 21+ T21.6 builds the resolver,
    Market Bridge must always run with polymarket_slug=None and
    canonical_event_id=None. If this test ever fails, the resolver got
    built and the wiring needs review.
    """
    with patch.object(vault_query.researcher, "run", return_value={}), \
         patch.object(vault_query.pulse_analyst, "run", return_value={}), \
         patch.object(vault_query.market_bridge, "run", return_value={}) as m:
        vault_query.run(_make_state())

    assert m.call_args.kwargs["polymarket_slug"] is None
    assert m.call_args.kwargs["canonical_event_id"] is None


def test_run_forwards_now_kwarg_to_all_agents():
    fixed_now = datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)

    with patch.object(vault_query.researcher, "run", return_value={}) as r, \
         patch.object(vault_query.pulse_analyst, "run", return_value={}) as p, \
         patch.object(vault_query.market_bridge, "run", return_value={}) as m:
        vault_query.run(_make_state(), now=fixed_now)

    assert r.call_args.kwargs["now"] is fixed_now
    assert p.call_args.kwargs["now"] is fixed_now
    assert m.call_args.kwargs["now"] is fixed_now


def test_run_forwards_empty_entities_when_structured_intent_missing():
    """
    Defensive: if structured_intent is unset (shouldn't happen post-T19.5
    but the graph might run nodes in test harnesses without Node 1),
    Market Bridge should get entities=[].
    """
    state = {"query_embedding": _make_embedding()}

    with patch.object(vault_query.researcher, "run", return_value={}), \
         patch.object(vault_query.pulse_analyst, "run", return_value={}), \
         patch.object(vault_query.market_bridge, "run", return_value={}) as m:
        vault_query.run(state)

    assert m.call_args.kwargs["entities"] == []


# ==========================================================
# vault_query_attempts counter
# ==========================================================
def test_run_increments_vault_query_attempts_from_zero():
    with patch.object(vault_query.researcher, "run", return_value={}), \
         patch.object(vault_query.pulse_analyst, "run", return_value={}), \
         patch.object(vault_query.market_bridge, "run", return_value={}):
        out = vault_query.run(_make_state())

    assert out["vault_query_attempts"] == 1


def test_run_increments_vault_query_attempts_from_existing():
    state = _make_state(vault_query_attempts=1)

    with patch.object(vault_query.researcher, "run", return_value={}), \
         patch.object(vault_query.pulse_analyst, "run", return_value={}), \
         patch.object(vault_query.market_bridge, "run", return_value={}):
        out = vault_query.run(state)

    assert out["vault_query_attempts"] == 2


# ==========================================================
# Input validation
# ==========================================================
def test_run_raises_when_query_embedding_missing():
    state = {"structured_intent": {"entities": []}}

    with pytest.raises(AgentProcessingError) as exc_info:
        vault_query.run(state)

    assert "missing query_embedding" in str(exc_info.value)


def test_run_raises_when_query_embedding_empty_list():
    state = _make_state(query_embedding=[])

    with pytest.raises(AgentProcessingError) as exc_info:
        vault_query.run(state)

    assert "missing query_embedding" in str(exc_info.value)


# ==========================================================
# Fail-fast error semantics (OQ-3)
# ==========================================================
def test_run_raises_on_researcher_failure():
    with patch.object(vault_query.researcher, "run", side_effect=RuntimeError("db down")), \
         patch.object(vault_query.pulse_analyst, "run", return_value={}), \
         patch.object(vault_query.market_bridge, "run", return_value={}):
        with pytest.raises(AgentProcessingError) as exc_info:
            vault_query.run(_make_state())

    assert "researcher failed" in str(exc_info.value)


def test_run_raises_on_pulse_failure():
    with patch.object(vault_query.researcher, "run", return_value={}), \
         patch.object(vault_query.pulse_analyst, "run", side_effect=ValueError("bad row")), \
         patch.object(vault_query.market_bridge, "run", return_value={}):
        with pytest.raises(AgentProcessingError) as exc_info:
            vault_query.run(_make_state())

    assert "pulse_analyst failed" in str(exc_info.value)


def test_run_raises_on_market_bridge_failure():
    with patch.object(vault_query.researcher, "run", return_value={}), \
         patch.object(vault_query.pulse_analyst, "run", return_value={}), \
         patch.object(vault_query.market_bridge, "run", side_effect=KeyError("market_id_ref")):
        with pytest.raises(AgentProcessingError) as exc_info:
            vault_query.run(_make_state())

    assert "market_bridge failed" in str(exc_info.value)


def test_run_propagates_agent_processing_error_unwrapped():
    """
    If a downstream agent already raises AgentProcessingError (e.g., the
    Sprint 22 reactive search node bubbles up its own typed error), the
    wrap path shouldn't double-wrap it.
    """
    inner = AgentProcessingError("vault: corrupted row")
    with patch.object(vault_query.researcher, "run", side_effect=inner), \
         patch.object(vault_query.pulse_analyst, "run", return_value={}), \
         patch.object(vault_query.market_bridge, "run", return_value={}):
        with pytest.raises(AgentProcessingError) as exc_info:
            vault_query.run(_make_state())

    # exception is the inner one, not a wrapper around it
    assert exc_info.value is inner


# ==========================================================
# Timeout (OQ-4)
# ==========================================================
def test_run_raises_on_per_agent_timeout(monkeypatch):
    """
    Patch the timeout to a small value and have one agent sleep past it.
    The other agents may or may not finish before the timeout — the
    contract is only that vault_query raises AgentProcessingError naming
    the slow agent.
    """
    monkeypatch.setattr(vault_query, "PER_AGENT_TIMEOUT_S", 0.1)

    def slow_researcher(*args, **kwargs):
        time.sleep(0.5)
        return {}

    with patch.object(vault_query.researcher, "run", side_effect=slow_researcher), \
         patch.object(vault_query.pulse_analyst, "run", return_value={}), \
         patch.object(vault_query.market_bridge, "run", return_value={}):
        with pytest.raises(AgentProcessingError) as exc_info:
            vault_query.run(_make_state())

    assert "timed out" in str(exc_info.value)
    assert "researcher" in str(exc_info.value)


# ==========================================================
# Parallel dispatch (best-effort proof — not strict ordering)
# ==========================================================
def test_run_dispatches_agents_in_parallel():
    """
    With each mocked agent sleeping 0.2s, sequential dispatch would take
    >= 0.6s; parallel dispatch should take ~0.2s. We give generous
    headroom (< 0.5s) to avoid flakiness on slow CI.
    """
    def slow_call(*args, **kwargs):
        time.sleep(0.2)
        return {}

    with patch.object(vault_query.researcher, "run", side_effect=slow_call), \
         patch.object(vault_query.pulse_analyst, "run", side_effect=slow_call), \
         patch.object(vault_query.market_bridge, "run", side_effect=slow_call):
        start = time.perf_counter()
        vault_query.run(_make_state())
        elapsed = time.perf_counter() - start

    assert elapsed < 0.5, f"Expected parallel dispatch (~0.2s), took {elapsed:.2f}s"
