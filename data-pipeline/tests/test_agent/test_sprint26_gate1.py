"""
Gate 1 unit tests for Sprint 26 — Pre-Test Hardening.

Covers the NEW behaviours introduced this sprint (the base-version bump, the
_build_initial_state query_doc_id assertions, and the clarification-strip live in
the existing gate files they extend):

- T26.4 Prometheus metrics wiring — llm-cost counter (record_usage), session
  done/failed counters, write_to_firestore node-duration observe, and the
  generate_latest() exposition of all three families.
- T26.6 vault-read retry — transient errors retried (tight 3-attempt profile),
  permanent errors NOT retried.
- T26.11 delivery-path — step 6 targets state["query_doc_id"] (resume) / falls
  back to session_id (first-time); Layer 1 swallows a queue-write error; Layer 2
  refuses to downgrade a delivered ('done') session.

Metrics are global (default registry) and accumulate across tests, so every
metric assertion checks the DELTA around the call, never an absolute value.

Spec: docs/B_hub/plans/sprint26_pretest_hardening.md 26.8 (§9.3 Gate 1).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import psycopg2
import pytest
from prometheus_client import REGISTRY, generate_latest

from agent import metrics
from agent.nodes import write_to_firestore
from agent.process_query import _mark_failed
from agent.tools import knowledge_tools, market_tools
from agent.utils import llm_cost


# ==========================================================
# Helpers
# ==========================================================
def _sample(name: str, labels: dict) -> float:
    """Current value of a registered sample, or 0.0 if not yet created."""
    v = REGISTRY.get_sample_value(name, labels)
    return v if v is not None else 0.0


def _state(**overrides) -> dict:
    base = {
        "session_id": "s1",
        "synthesis_result": {"finalProbability": 0.7, "tier": "tier_1"},
    }
    base.update(overrides)
    return base


@pytest.fixture
def mocked_firestore():
    """Patch the six firestore_client writes write_to_firestore uses; yield the
    (update_session_status, update_query_status) mocks the delivery-path tests
    assert on. events/drain no-op because state carries no run_id."""
    with (
        patch("agent.nodes.write_to_firestore.firestore_client.write_evidence_batch", return_value=0),
        patch("agent.nodes.write_to_firestore.firestore_client.write_prediction_series", return_value=0),
        patch("agent.nodes.write_to_firestore.firestore_client.write_sentiment_time_series", return_value=0),
        patch("agent.nodes.write_to_firestore.firestore_client.write_session_result"),
        patch("agent.nodes.write_to_firestore.firestore_client.update_session_status") as ss,
        patch("agent.nodes.write_to_firestore.firestore_client.update_query_status") as qs,
    ):
        yield ss, qs


# ==========================================================
# T26.11 — write_to_firestore step 6 targeting + Layer 1
# ==========================================================
def test_step6_targets_query_doc_id_on_resume(mocked_firestore):
    """KG-B-18: on resume-on-clarify query_doc_id != session_id. Step 5 (session
    status) targets session_id; step 6 (queue status) targets the fresh
    query_doc_id the run actually processed."""
    ss, qs = mocked_firestore
    write_to_firestore.run(_state(session_id="orig-sess", query_doc_id="fresh-uuid"))
    ss.assert_called_once_with("orig-sess", "done", tier=None, canonical_key=None)
    qs.assert_called_once_with("fresh-uuid", "done")


def test_step6_falls_back_to_session_id_when_query_doc_id_absent(mocked_firestore):
    """First-time contract: no query_doc_id in state → step 6 falls back to
    session_id (they are equal on first-time runs)."""
    ss, qs = mocked_firestore
    write_to_firestore.run(_state(session_id="s1"))
    qs.assert_called_once_with("s1", "done")


def test_step6_layer1_swallows_queue_write_error(mocked_firestore):
    """Layer 1: the queue write runs AFTER the session is 'done', so a failure
    there must never fail a delivered forecast — run() must not raise, and the
    session 'done' write must have happened."""
    ss, qs = mocked_firestore
    qs.side_effect = RuntimeError("emulator 404 no entity to update")
    out = write_to_firestore.run(_state(session_id="s1", query_doc_id="s1"))
    assert out == {"errors": []}  # returned normally, no raise
    ss.assert_called_once_with("s1", "done", tier=None, canonical_key=None)


# ==========================================================
# T26.4 — write_to_firestore metrics (done counter + node duration)
# ==========================================================
def test_done_session_counter_and_node_duration_observed(mocked_firestore):
    """A delivered forecast increments agent_session_total{status=done} for its
    tier, and write_to_firestore observes its own node-duration histogram."""
    done_before = _sample("agent_session_total", {"tier": "tier_1", "status": "done"})
    dur_before = _sample(
        "agent_node_duration_seconds_count", {"node_name": "write_to_firestore"}
    )

    write_to_firestore.run(_state(session_id="s1", tier="tier_1"))

    done_after = _sample("agent_session_total", {"tier": "tier_1", "status": "done"})
    dur_after = _sample(
        "agent_node_duration_seconds_count", {"node_name": "write_to_firestore"}
    )
    assert done_after - done_before == 1.0
    assert dur_after - dur_before == 1.0


# ==========================================================
# T26.11 Layer 2 — _mark_failed no-downgrade guard (+ failed counter)
# ==========================================================
@patch("agent.process_query.update_query_status")
@patch("agent.process_query.update_session_status")
@patch("agent.process_query.get_session_doc")
def test_mark_failed_refuses_downgrade_when_session_done(mock_get, mock_ss, mock_qs):
    """Layer 2 / KG-B-18 invariant: a session already 'done' (delivered) must NOT
    be downgraded — neither doc gets a 'failed' write, and the failed counter is
    not incremented."""
    mock_get.return_value = {"status": "done"}
    failed_before = _sample("agent_session_total", {"tier": "unknown", "status": "failed"})

    _mark_failed("sess-1", "query-1", "late error after delivery")

    mock_ss.assert_not_called()
    mock_qs.assert_not_called()
    failed_after = _sample("agent_session_total", {"tier": "unknown", "status": "failed"})
    assert failed_after == failed_before  # not counted as failed


@patch("agent.process_query.update_query_status")
@patch("agent.process_query.update_session_status")
@patch("agent.process_query.get_session_doc")
def test_mark_failed_proceeds_and_counts_when_not_done(mock_get, mock_ss, mock_qs):
    """A genuinely failed session (status != 'done') flips both docs to 'failed'
    and increments agent_session_total{status=failed}."""
    mock_get.return_value = {"status": "running"}
    failed_before = _sample("agent_session_total", {"tier": "unknown", "status": "failed"})

    _mark_failed("sess-1", "query-1", "boom")

    mock_ss.assert_called_once()
    mock_qs.assert_called_once_with("query-1", "failed", error_message="boom")
    failed_after = _sample("agent_session_total", {"tier": "unknown", "status": "failed"})
    assert failed_after - failed_before == 1.0


# ==========================================================
# T26.6 — vault-read retry at the tools layer
# ==========================================================
def test_vault_read_retries_transient_then_succeeds():
    """A transient psycopg2.OperationalError is retried; the wrapped read succeeds
    on a later attempt within the tight 3-attempt profile."""
    calls = {"n": 0}

    def flaky(*_a, **_k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise psycopg2.OperationalError("could not translate host name")
        return {"metric_id": "m1"}

    with (
        patch.object(market_tools.momentum_vault, "fetch_latest", side_effect=flaky),
        patch("utils.retry.time.sleep"),  # skip real backoff
    ):
        result = market_tools.fetch_latest("polymarket", "slug")

    assert result == {"metric_id": "m1"}
    assert calls["n"] == 3


def test_vault_read_gives_up_after_three_attempts():
    """Persistent transient errors exhaust the profile and raise — max_attempts=3
    (tight profile, must finish inside vault_query's 15s per-agent timeout)."""
    with (
        patch.object(
            market_tools.momentum_vault, "fetch_latest",
            side_effect=psycopg2.OperationalError("down"),
        ) as m,
        patch("utils.retry.time.sleep"),
    ):
        with pytest.raises(psycopg2.OperationalError):
            market_tools.fetch_latest("polymarket", "slug")
    assert m.call_count == 3


def test_vault_read_does_not_retry_permanent_error():
    """A non-transient error (e.g. ValueError) raises immediately — no retry."""
    with patch.object(
        knowledge_tools.knowledge_vault, "fetch_by_doc_id", side_effect=ValueError("bad")
    ) as m:
        with pytest.raises(ValueError):
            knowledge_tools.fetch_full_text("doc-1")
    assert m.call_count == 1


# ==========================================================
# T26.4 — llm cost counter + /metrics exposition
# ==========================================================
def test_record_usage_increments_llm_cost_counter():
    """record_usage increments agent_llm_cost_usd_total{model} by the call's cost
    (the single site where model + cost coexist)."""
    model = "gpt-4o"
    resp = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=1000, total_tokens=2000)
    )
    before = _sample("agent_llm_cost_usd_total", {"model": model})

    total, cost = llm_cost.record_usage(model, resp, site="gate1-test")

    after = _sample("agent_llm_cost_usd_total", {"model": model})
    assert total == 2000
    assert cost > 0.0  # gpt-4o is priced
    assert abs((after - before) - cost) < 1e-9


def test_metrics_exposition_contains_all_three_families():
    """generate_latest() exposes all three agent metric families with the
    contracted names (Counters expose the _total suffix exactly once)."""
    metrics.NODE_DURATION_SECONDS.labels(node_name="synthesize").observe(0.1)
    metrics.LLM_COST_USD_TOTAL.labels(model="gpt-4o").inc(0.01)
    metrics.SESSION_TOTAL.labels(tier="tier_1", status="done").inc()

    body = generate_latest().decode()
    assert "agent_node_duration_seconds_bucket" in body
    assert "agent_llm_cost_usd_total" in body
    assert "agent_session_total" in body
    # The suffix is not doubled (prometheus_client normalises the _total name).
    assert "agent_session_total_total" not in body
    assert "agent_llm_cost_usd_total_total" not in body


def test_metrics_endpoint_serves_prometheus_exposition():
    """The live /metrics HTTP endpoint (agent/health.py health server) serves the
    prometheus exposition of the agent metrics — the real replacement for the
    Sprint-18 stub. Self-contained: ephemeral port, no emulator/worker needed."""
    import http.client
    import threading

    from agent.health import start_health_server

    metrics.SESSION_TOTAL.labels(tier="tier_1", status="done").inc()
    metrics.LLM_COST_USD_TOTAL.labels(model="gpt-4o").inc(0.01)
    metrics.NODE_DURATION_SECONDS.labels(node_name="synthesize").observe(0.1)

    ready = threading.Event()
    ready.set()
    shutdown = threading.Event()
    server = start_health_server("127.0.0.1", 0, ready, shutdown)  # port 0 → ephemeral
    try:
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/metrics")
        resp = conn.getresponse()
        body = resp.read().decode()
        conn.close()

        assert resp.status == 200
        assert "text/plain" in (resp.getheader("Content-Type") or "")  # CONTENT_TYPE_LATEST
        assert "agent_node_duration_seconds_bucket" in body
        assert "agent_llm_cost_usd_total" in body
        assert "agent_session_total" in body
        assert "Sprint 18 stub" not in body  # the old stub is gone
    finally:
        server.shutdown()


def test_generate_suggested_actions_observes_node_duration():
    """T26.4: generate_suggested_actions emits MANUALLY (not via @events.emits),
    so it must observe its own duration histogram — the gap surfaced by the 26.3
    latency run. The success path increments agent_node_duration_seconds{node_name}."""
    import json
    from unittest.mock import MagicMock

    from agent.nodes import generate_suggested_actions

    actions = [
        {"label": "Why so confident?", "prompt": "What drives the confidence?"},
        {"label": "Strongest driver", "prompt": "Which evidence mattered most?"},
        {"label": "Compare to the market", "prompt": "How does this compare?"},
    ]
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"actions": actions})))],
        usage=SimpleNamespace(prompt_tokens=80, completion_tokens=40, total_tokens=120),
    )
    client = MagicMock()
    client.chat.completions.create = MagicMock(return_value=response)
    state = {"synthesis_result": {"finalProbability": 0.6}, "raw_question": "Will X happen?"}

    before = _sample("agent_node_duration_seconds_count", {"node_name": "generate_suggested_actions"})
    out = generate_suggested_actions.run(state, client=client)
    after = _sample("agent_node_duration_seconds_count", {"node_name": "generate_suggested_actions"})

    assert len(out["suggested_actions"]) == 3
    assert after - before == 1.0
