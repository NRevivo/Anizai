"""
E2E cycle-close tests for Sprint 23.5 (T23.5.7) — the split gate.

Per the Advisor↔Ron decision record §4 (and plan §2 23.5.7), 23.5.7 is split:

  Half A — HARD CLOSEOUT GATE (this is the one that must pass to close 23.5):
    An insufficient question drives the full forecast graph down the reactive
    branch: sufficiency_check marks insufficient → exactly one trigger is
    emitted → a reactive_triggers_log row is written → synthesis proceeds on
    the available evidence (trigger-and-forget, no wait). All in Domain B's
    control. Uses REAL Kafka + REAL Postgres for the trigger emission + audit
    row; the four LLM clients and the three vault agents are mocked so the run
    is deterministic. (The Firestore answer-write is exercised via the
    write_to_firestore boundary; on the §7 infra the firestore patches can be
    lifted to write to the emulator on 8080 — the trigger-and-forget gate this
    test pins does not depend on it.)

  Half B — NON-BLOCKING PoC (deferred-OK if infra is flaky):
    Stub the external NewsAPI fetch to return 1–2 canned articles and assert
    they land in the Bronze raw store (BRONZE_NEWSAPI) via the trigger
    consumer's dispatch. Deterministic, Kafka-only. The "fresh session
    surfaces the new articles as evidence" link is explicitly OUT OF SCOPE for
    23.5 (needs the full Bronze→Silver→Gold→vault enrichment); deferred.

Both classes are @skipif Windows-local (KG-B-13 / KG-PHASE8-25): the
kafka-python-ng bootstrap→coordinator handoff race reliably trips on Windows
dev but not on long-lived Linux/GKE consumers. Verification runs on Linux CI
with the §7 infra (`docker compose up -d kafka kafka-init postgres`).

Spec references:
    - data-pipeline/docs/B_hub/sprint23_5_pre26_remediation.md §2 (23.5.7), §5
    - cabinet-outputs/advisor/problem-reports/sprint23_5_advisor-ron-decisions.md
      §4 (split gate), §7 (test infra)
    - tests/test_agent/test_sprint23_gate3.py (Half B mirrors its Bronze flow)
    - tests/test_agent/test_graph_integration.py (Half A mirrors its mock harness)
"""

from __future__ import annotations

import json
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.nodes import trigger_reactive_ingestion as node_module
from config.kafka_topics import BRONZE_NEWSAPI, INGESTION_TRIGGERS
from ingestion.newsapi_producer import NewsAPIProducer
from orchestration.ingestion_trigger_consumer import dispatch, validate_trigger
from persistence.reactive_triggers_log import list_by_session
from utils.kafka_utils import make_consumer

pytestmark = pytest.mark.usefixtures("db_available", "kafka_available")


_TRIGGER_POLL_DEADLINE_SEC = 15.0
_BRONZE_POLL_DEADLINE_SEC = 15.0
_DISPATCH_THREAD_JOIN_TIMEOUT_SEC = 10.0


# ==========================================================
# Deterministic LLM response builders (mirror test_graph_integration)
# ==========================================================


def _chat_response(payload: dict, total_tokens: int = 120):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
        usage=SimpleNamespace(prompt_tokens=total_tokens // 2,
                              completion_tokens=total_tokens // 2,
                              total_tokens=total_tokens),
    )


def _qu_response(entities):
    candidate = {
        "intent": "forecast", "domain": "macro", "entities": entities,
        "polymarket_search_terms": None, "has_market_question_intent": True,
        "confidence": 0.95, "too_broad": False, "rejected": False,
    }
    return _chat_response({"candidates": [candidate]})


def _embedding_response(dim: int = 1536):
    return SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.01] * dim)],
        usage=SimpleNamespace(prompt_tokens=8, completion_tokens=0, total_tokens=8),
    )


def _synthesis_response():
    payload = {
        "final_probability": 0.55, "confidence": 0.4, "consensus_score": 0.3,
        "bottom_line_answer": "Insufficient evidence — low-confidence lean.",
        "detailed_explanation": "Sparse vault; reactive ingestion triggered.",
        "summary_markdown": "**Low confidence.**",
        "market_comparison_insight": "No market.",
        "sentiment_analysis_insight": "Sparse sentiment.",
        "evidence_feed_summary": "Little evidence found.",
        "what_i_didnt_find": ["recent reporting on the entities"],
        "key_factors": [
            {"label": "A", "description": "x", "weight": 0.4,
             "direction": "increases", "evidence_ids": []},
            {"label": "B", "description": "y", "weight": 0.3,
             "direction": "decreases", "evidence_ids": []},
            {"label": "C", "description": "z", "weight": 0.3,
             "direction": "increases", "evidence_ids": []},
        ],
        "reasoning_chain": [
            {"step": 1, "title": "Parse", "description": "Parsed criterion."},
            {"step": 2, "title": "Review", "description": "Reviewed sparse evidence."},
            {"step": 3, "title": "Weigh", "description": "Weighed factors."},
            {"step": 4, "title": "Forecast", "description": "Low-confidence forecast."},
        ],
        "evidence_overlay": [],
    }
    return _chat_response(payload, total_tokens=1500)


# ==========================================================
# Fixtures
# ==========================================================


@pytest.fixture
def warm_trigger_producer():
    """Pre-warm + reset the module-level singleton producer (D7) so the
    timed warm-path send budget isn't eaten by cold-start metadata cost."""
    node_module._reset_producer_for_tests()
    producer = node_module._get_producer()
    producer.partitions_for(INGESTION_TRIGGERS)
    producer.partitions_for(BRONZE_NEWSAPI)
    yield producer
    node_module._reset_producer_for_tests()


def _make_fixture_article(run_id: str, idx: int, domain: str = "reuters.com") -> dict:
    return {
        "url": f"https://{domain}/test-e2e-{run_id}-{idx}",
        "title": f"E2E fixture article {idx}",
        "description": "Sprint 23.5 Half B fixture article body.",
        "body": "Full body text for Half B fixture article.",
        "image": "",
        "dateTime": "2026-06-19T10:00:00Z",
        "source": {"uri": domain, "title": "Reuters"},
        "authors": [],
    }


def _poll_for_trigger(consumer, session_id, deadline_sec):
    deadline = time.time() + deadline_sec
    while time.time() < deadline:
        records = consumer.poll(timeout_ms=1_000, max_records=50)
        for _tp, msgs in records.items():
            for msg in msgs:
                if isinstance(msg.value, dict) and msg.value.get("session_id") == session_id:
                    return msg.value
    return None


def _wait_for_assignment(consumer, timeout_sec: float = 10.0) -> None:
    deadline = time.time() + timeout_sec
    while not consumer.assignment() and time.time() < deadline:
        consumer.poll(timeout_ms=500)
    if not consumer.assignment():
        pytest.skip(
            f"Consumer never got partition assignment within {timeout_sec}s "
            "— broker cold/loaded. Re-run after warmup."
        )


def _poll_for_bronze(consumer, run_id, expected_count, deadline_sec):
    marker = f"test-e2e-{run_id}"
    collected: list[dict] = []
    deadline = time.time() + deadline_sec
    while time.time() < deadline and len(collected) < expected_count:
        records = consumer.poll(timeout_ms=1_000, max_records=100)
        for _tp, msgs in records.items():
            for msg in msgs:
                raw = (msg.value or {}).get("payload", {}).get("raw_payload", {})
                if marker in (raw.get("url") or ""):
                    collected.append(msg.value)
    return collected


# ==========================================================
# Half A — HARD CLOSEOUT GATE (real Kafka + real Postgres)
# ==========================================================


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="KG-PHASE8-25 kafka-python-ng handoff race on Windows local-dev; "
           "runs on Linux CI / cloud per decision record §7.",
)
class TestSprint23_5_E2E_HalfA:
    """Half A — insufficient question → trigger emitted + reactive_triggers_log
    row + synthesis proceeds (trigger-and-forget). Real Kafka + real Postgres;
    LLM clients + vault agents mocked for determinism."""

    def test_insufficient_question_emits_trigger_and_synthesizes(
        self, test_run_id, cleanup_reactive_triggers_log, warm_trigger_producer
    ):
        from agent.graph import graph

        session_id = f"test_{test_run_id}_e2e_a"
        entities = [f"iran-e2e-{test_run_id}"]

        # The vault comes back EMPTY → count_raw_signals == 0 (< floor 5) →
        # sufficiency_check marks insufficient → reactive trigger fires.
        # vault_query.run packs each agent's return verbatim into
        # researcher_evidence / pulse_evidence / market_evidence, so the
        # agent mocks return the empty PACKAGE directly (not a state delta).
        empty_pkg = {"empty": True}

        with (
            patch("agent.firestore_client.claim_query") as mock_claim,
            patch("agent.firestore_client.update_session_status"),
            patch("agent.firestore_client.update_query_status"),
            patch("agent.firestore_client.write_session_result") as mock_write_result,
            patch("agent.firestore_client.write_evidence_batch", return_value=0),
            patch("agent.firestore_client.write_prediction_series", return_value=0),
            patch("agent.firestore_client.write_sentiment_time_series", return_value=0),
            patch("agent.nodes.query_understand._get_default_client") as qu_factory,
            patch("agent.nodes.build_embedding._get_default_client") as emb_factory,
            patch("agent.nodes.synthesize._get_default_client") as synth_factory,
            patch("agent.agents.researcher.run", return_value=empty_pkg),
            patch("agent.agents.pulse_analyst.run", return_value=empty_pkg),
            patch("agent.agents.market_bridge.run", return_value=empty_pkg),
        ):
            mock_claim.return_value = {
                "queryId": session_id, "sessionId": session_id, "userId": "u1",
                "question": "Will sanctions escalate?", "status": "pending",
                "createdAt": MagicMock(), "claimedAt": None, "claimedBy": None,
            }
            qu_client = MagicMock()
            qu_client.chat.completions.create.return_value = _qu_response(entities)
            qu_factory.return_value = qu_client

            emb_client = MagicMock()
            emb_client.embeddings.create.return_value = _embedding_response()
            emb_factory.return_value = emb_client

            synth_client = MagicMock()
            synth_client.chat.completions.create.return_value = _synthesis_response()
            synth_factory.return_value = synth_client

            # NOTE: trigger_reactive_ingestion is NOT patched — it hits REAL
            # Kafka + REAL Postgres. That is the point of this gate.
            final = graph.invoke(
                {"session_id": session_id, "raw_question": "Will sanctions escalate?"}
            )

        # --- sufficiency_check marked insufficient ---
        checks = final["sufficiency_checks"]
        assert checks[-1]["is_sufficient"] is False, (
            "Empty vault must be judged insufficient — that is what drives the "
            "reactive branch."
        )

        # --- exactly one trigger emitted (trigger-and-forget) ---
        assert final["reactive_triggers_emitted"] == 1

        # --- real reactive_triggers_log row written with a real offset ---
        rows = list_by_session(session_id)
        assert len(rows) == 1, "Expected exactly one reactive_triggers_log row."
        row = rows[0]
        assert row["status"] == "emitted"
        assert row["source"] == "newsapi"
        assert row["keywords"] == entities  # entities-only (R1)
        assert isinstance(row["kafka_offset"], int)

        # --- the trigger really landed in Kafka ---
        consumer = make_consumer(
            INGESTION_TRIGGERS, group_id=f"e2e-a-{test_run_id}"
        )
        try:
            payload = _poll_for_trigger(consumer, session_id, _TRIGGER_POLL_DEADLINE_SEC)
        finally:
            consumer.close()
        assert payload is not None
        assert validate_trigger(payload) == []
        assert payload["keywords"] == entities
        assert payload["time_window_days"] == 7  # the 7-day recency window (R1)

        # --- synthesis proceeded on available evidence (did not wait) ---
        assert final.get("synthesis_result") is not None
        mock_write_result.assert_called_once()


# ==========================================================
# Half B — NON-BLOCKING PoC (real Kafka; NewsAPI fetch stubbed)
# ==========================================================


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="KG-PHASE8-25 kafka-python-ng handoff race on Windows local-dev; "
           "runs on Linux CI / cloud per decision record §7.",
)
class TestSprint23_5_E2E_HalfB:
    """Half B — STUBBED NewsAPI fetch → 1-2 canned articles land in Bronze
    (BRONZE_NEWSAPI) via the trigger consumer's dispatch. Kafka only. The
    next-session-sees-it enrichment link is OUT OF SCOPE for 23.5 (deferred)."""

    def test_stubbed_fetch_lands_canned_articles_in_bronze(
        self, test_run_id, warm_trigger_producer
    ):
        run_id = test_run_id
        fixture_articles = [
            _make_fixture_article(run_id, 1, domain="reuters.com"),
            _make_fixture_article(run_id, 2, domain="apnews.com"),
        ]

        # A minimal, valid reactive trigger (entities-only keywords, R1).
        trigger_payload = {
            "source": "newsapi",
            "keywords": [f"iran-e2e-{run_id}"],
            "time_window_days": 7,
            "session_id": f"test_{run_id}_e2e_b",
        }
        assert validate_trigger(trigger_payload) == []

        bronze_consumer = make_consumer(
            BRONZE_NEWSAPI,
            group_id=f"e2e-b-bronze-{run_id}",
            auto_offset_reset="latest",
        )
        try:
            _wait_for_assignment(bronze_consumer)
            bronze_consumer.poll(timeout_ms=500)  # resolve positions to high-water mark

            # STUB the external NewsAPI HTTP fetch — everything downstream
            # (whitelist, dedup, raw_payload build, real Kafka emit to Bronze)
            # runs unmocked.
            with patch.object(
                NewsAPIProducer,
                "_fetch_articles",
                return_value=(fixture_articles, len(fixture_articles), 50),
            ):
                thread = dispatch(trigger_payload)
                thread.join(timeout=_DISPATCH_THREAD_JOIN_TIMEOUT_SEC)

            assert not thread.is_alive(), "Dispatch thread did not complete in time."

            bronze_messages = _poll_for_bronze(
                bronze_consumer, run_id,
                expected_count=len(fixture_articles),
                deadline_sec=_BRONZE_POLL_DEADLINE_SEC,
            )
        finally:
            bronze_consumer.close()

        assert len(bronze_messages) == len(fixture_articles), (
            f"Expected {len(fixture_articles)} stubbed articles in {BRONZE_NEWSAPI} "
            f"(marker test-e2e-{run_id}); got {len(bronze_messages)}."
        )
        for bmsg in bronze_messages:
            raw = bmsg["payload"]["raw_payload"]
            assert raw["fetch_mode"] == "reactive"
            assert f"test-e2e-{run_id}" in raw["url"]
