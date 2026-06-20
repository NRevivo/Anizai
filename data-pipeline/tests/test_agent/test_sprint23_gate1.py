"""
Gate 1 unit tests for Sprint 23 — Producer-trigger Infrastructure.

Grows with each bundle landing. All tests are pure unit-level: no Kafka
broker, no Postgres, no Firestore, no live newsapi.ai calls.

Bundle A (T23.6 — config var + state field):
  - `AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION` defaults to 1 when env unset
  - `AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION` honors the env-var override
  - `reactive_triggers_emitted: int` annotation present on ForecastState
  - Defensive `state.get(field) or 0` idiom yields 0 on an empty state
    (the project's established counter-read convention — see
    `vault_query.py:129` / `synthesize.py:241` / `rate_evidence.py:230,249`
    for the same shape).

Future bundles in this file:
  - Bundle B (T23.5) — trigger_reactive_ingestion node payload builder +
    Kafka emission + log insertion + state counter update
  - Bundle C (T23.7) — consolidated Gate 1 coverage including the cross-task
    rate-limit enforcement integration test

Spec references:
    - data-pipeline/docs/agentic_hub_implementation_phase8_revised.md §Sprint 23
    - data-pipeline/agent/state.py (ForecastState contract)
    - data-pipeline/agent/config/settings.py (AGENT_REACTIVE_* block)
"""

from __future__ import annotations

import importlib
from typing import get_type_hints
from unittest.mock import MagicMock, patch

import pytest
from kafka.errors import KafkaError

from agent.state import ForecastState


# ============================================================
# Bundle A: T23.6 — config + state field
# ============================================================


class TestSprint23ConfigVar:
    """`AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION` env-driven config."""

    def test_default_is_one_when_env_unset(self, monkeypatch):
        """[A1] Loading with no override yields the locked default of 1."""
        monkeypatch.delenv(
            "AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION", raising=False
        )
        import agent.config.settings as settings_mod
        importlib.reload(settings_mod)
        try:
            assert settings_mod.AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION == 1
        finally:
            # Module stays reloaded — env was already cleared by monkeypatch,
            # so the post-test state of the cached module already reflects
            # the default. No additional reload needed.
            pass

    def test_env_var_override_propagates(self, monkeypatch):
        """[A2] Setting the env var + reload yields the override value.

        This locks the contract that operators can tighten or relax the
        per-session trigger budget without code changes — same convention
        as every other AGENT_* / OPENAI_* setting in the module.
        """
        monkeypatch.setenv("AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION", "7")
        import agent.config.settings as settings_mod
        importlib.reload(settings_mod)
        try:
            assert settings_mod.AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION == 7
        finally:
            # Undo env BEFORE the final reload so the cached module returns
            # to its default state for subsequent tests. monkeypatch.undo()
            # eagerly reverses everything this monkeypatch instance has set.
            monkeypatch.undo()
            importlib.reload(settings_mod)


class TestSprint23StateField:
    """`reactive_triggers_emitted: int` on ForecastState."""

    def test_field_is_annotated_as_int(self):
        """[A3] Field declared on the TypedDict with type `int`.

        TypedDicts have no runtime enforcement; this test pins the
        annotation so a future refactor can't silently rename or retype
        the field without breaking T23.5's writer and Sprint 26's reader.

        Note: `agent/state.py` uses `from __future__ import annotations`
        (PEP 563), so `ForecastState.__annotations__` stores ForwardRef
        wrappers, not resolved types. Use `typing.get_type_hints()` to
        resolve to the actual `int` type. Same approach would work for
        any other field — this test is the canonical reference for
        annotation-pinning in this module.
        """
        hints = get_type_hints(ForecastState)
        assert "reactive_triggers_emitted" in hints, (
            "ForecastState must declare reactive_triggers_emitted "
            "(Sprint 23 T23.6)"
        )
        assert hints["reactive_triggers_emitted"] is int, (
            "reactive_triggers_emitted must be typed `int` to match the "
            "existing counter-field convention "
            "(vault_query_attempts, llm_calls_count, total_tokens_used)."
        )

    def test_defensive_read_of_unset_state_yields_zero(self):
        """[A4] Codebase-wide idiom: `int(state.get(field) or 0)` returns 0
        when the field hasn't been written yet.

        This is the established read pattern for every counter field on
        ForecastState — see vault_query.py:129, synthesize.py:241,
        rate_evidence.py:230 & 249. `total=False` on the TypedDict means
        absent fields are valid; readers cope via `.get() or 0`.

        Writer convention (T23.5): increment is
            `int(state.get("reactive_triggers_emitted") or 0) + 1`
        """
        state: ForecastState = {}
        assert int(state.get("reactive_triggers_emitted") or 0) == 0


# ============================================================
# Bundle B: T23.5 — node behavior
# ============================================================
# Mock points (at lookup site in the node module, per CLAUDE.md §4.3):
#   * `agent.nodes.trigger_reactive_ingestion._get_producer`
#       → KafkaProducer factory; replaced with MagicMock per test
#   * `agent.nodes.trigger_reactive_ingestion.reactive_triggers_log`
#       → persistence module; .insert is asserted/forced-to-raise per test
#
# No real Kafka broker, no real Postgres, no real LLM call.
# All tests construct minimal ForecastState dicts inline.
# ============================================================


def _state_with_entities(*entities: str, **extra) -> dict:
    """
    Build a minimal ForecastState carrying entities + the optional
    `reactive_triggers_emitted` counter (defaults to 0 when omitted).

    Why a helper: every Bundle B test needs a session_id + structured_intent
    shape, and uniform construction keeps the test bodies focused on the
    one behavior each is asserting.
    """
    state: dict = {
        "session_id": extra.pop("session_id", "test-session-b"),
        "structured_intent": {"entities": list(entities)},
    }
    state.update(extra)
    return state


def _mock_kafka_send_success(offset: int = 42, partition: int = 0):
    """
    Build a MagicMock producer whose `.send().get()` returns a
    RecordMetadata-like object with the given offset/partition.
    """
    producer = MagicMock()
    record_metadata = MagicMock(offset=offset, partition=partition)
    producer.send.return_value.get.return_value = record_metadata
    return producer


def _mock_kafka_send_failure(exc: Exception | None = None):
    """
    Build a MagicMock producer whose `.send().get()` raises KafkaError.
    Default to a generic KafkaError; tests can pass a specific subclass.
    """
    producer = MagicMock()
    producer.send.return_value.get.side_effect = (
        exc if exc is not None else KafkaError("simulated kafka outage")
    )
    return producer


class TestTriggerReactiveIngestionNode:
    """Bundle B — `agent.nodes.trigger_reactive_ingestion` (Sprint 23 T23.5)."""

    # --- B1: happy path — Kafka send + log row both called once ---

    def test_below_rate_limit_emits_and_logs(self):
        from agent.nodes import trigger_reactive_ingestion as node

        producer = _mock_kafka_send_success(offset=42)
        state = _state_with_entities("Iran", "OPEC")

        with patch.object(node, "_get_producer", return_value=producer), \
             patch.object(node, "reactive_triggers_log") as mock_log:
            delta = node.trigger_reactive_ingestion(state)

        # Kafka send invoked exactly once on the triggers topic
        producer.send.assert_called_once()
        topic_arg, _, send_kwargs = (
            producer.send.call_args.args[0],
            None,
            producer.send.call_args.kwargs,
        )
        assert topic_arg == "ingestion_triggers"
        assert send_kwargs["value"]["source"] == "newsapi"
        assert send_kwargs["value"]["keywords"] == ["Iran", "OPEC"]

        # Log row written exactly once with status="emitted" + the offset
        mock_log.insert.assert_called_once()
        log_kwargs = mock_log.insert.call_args.kwargs
        assert log_kwargs["session_id"] == "test-session-b"
        assert log_kwargs["source"] == "newsapi"
        assert log_kwargs["keywords"] == ["Iran", "OPEC"]
        assert log_kwargs["kafka_offset"] == 42
        assert log_kwargs["status"] == "emitted"

        # Counter incremented in the state delta
        assert delta == {"reactive_triggers_emitted": 1}

    # --- B2: rate-limit gate at entry blocks all side effects ---

    def test_at_rate_limit_skips_emit(self):
        from agent.nodes import trigger_reactive_ingestion as node

        # Already at the per-session limit (AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION=1)
        state = _state_with_entities("Iran", reactive_triggers_emitted=1)

        with patch.object(node, "_get_producer") as mock_make, \
             patch.object(node, "reactive_triggers_log") as mock_log:
            delta = node.trigger_reactive_ingestion(state)

        mock_make.assert_not_called()
        mock_log.insert.assert_not_called()
        # Counter written back at the same value — LangGraph requires every
        # node to write at least one channel. Semantic no-op for defensive
        # readers (`int(state.get(field) or 0)` yields 1 either way).
        assert delta == {"reactive_triggers_emitted": 1}

    # --- B3: counter increments on successful send ---

    def test_increments_counter_on_success(self):
        from agent.nodes import trigger_reactive_ingestion as node

        state = _state_with_entities("Iran", reactive_triggers_emitted=0)
        producer = _mock_kafka_send_success()

        with patch.object(node, "_get_producer", return_value=producer), \
             patch.object(node, "reactive_triggers_log"):
            delta = node.trigger_reactive_ingestion(state)

        assert delta == {"reactive_triggers_emitted": 1}

    # --- B4: D5 — Kafka failure still counts as an attempt ---

    def test_increments_counter_on_kafka_failure(self):
        from agent.nodes import trigger_reactive_ingestion as node

        state = _state_with_entities("Iran", reactive_triggers_emitted=0)
        producer = _mock_kafka_send_failure()

        with patch.object(node, "_get_producer", return_value=producer), \
             patch.object(node, "reactive_triggers_log") as mock_log:
            delta = node.trigger_reactive_ingestion(state)

        # D5: a failed Kafka send STILL counts as an attempt against the
        # per-session budget.
        assert delta == {"reactive_triggers_emitted": 1}

        # Log row written with status="failed" + kafka_offset=None
        mock_log.insert.assert_called_once()
        log_kwargs = mock_log.insert.call_args.kwargs
        assert log_kwargs["kafka_offset"] is None
        assert log_kwargs["status"] == "failed"

    # --- B5: entities come first in the payload's keywords list ---

    def test_payload_keywords_from_entities_first(self):
        from agent.nodes import trigger_reactive_ingestion as node

        state = _state_with_entities("Iran", "OPEC")
        producer = _mock_kafka_send_success()

        with patch.object(node, "_get_producer", return_value=producer), \
             patch.object(node, "reactive_triggers_log"):
            node.trigger_reactive_ingestion(state)

        payload = producer.send.call_args.kwargs["value"]
        assert payload["keywords"] == ["Iran", "OPEC"]

    # --- B6: missing_dimensions are NOT merged — keywords entities-only (R1) ---

    def test_payload_keywords_exclude_missing_dimensions(self):
        """[B6] R1 (Advisor↔Ron decision record §1): V1 reactive keywords are
        entities-only. `sufficiency_checks[-1].missing_dimensions` is recorded
        as telemetry but is deliberately NOT folded into the keyword set —
        the gap reactive ingestion closes is recency (the trigger's 7-day
        window), not topic. This pins that missing_dimensions can't leak back
        into keywords via a future refactor."""
        from agent.nodes import trigger_reactive_ingestion as node

        state: dict = {
            "session_id": "test-session-b",
            "structured_intent": {"entities": ["Iran", "OPEC"]},
            "sufficiency_checks": [
                {"missing_dimensions": ["recent reaction", "economic impact"]}
            ],
        }
        producer = _mock_kafka_send_success()

        with patch.object(node, "_get_producer", return_value=producer), \
             patch.object(node, "reactive_triggers_log"):
            node.trigger_reactive_ingestion(state)

        payload = producer.send.call_args.kwargs["value"]
        assert payload["keywords"] == ["Iran", "OPEC"]
        for leaked in ("recent reaction", "economic impact"):
            assert leaked not in payload["keywords"], (
                f"missing_dimension {leaked!r} must not enter keywords (R1)"
            )

    # --- B7: raw_question excluded from keyword construction (D4) ---

    def test_payload_excludes_raw_question_words(self):
        from agent.nodes import trigger_reactive_ingestion as node

        state: dict = {
            "session_id": "test-session-b",
            "structured_intent": {"entities": ["Iran"]},
            "raw_question": "Will the Federal Reserve cut interest rates Q4?",
        }
        producer = _mock_kafka_send_success()

        with patch.object(node, "_get_producer", return_value=producer), \
             patch.object(node, "reactive_triggers_log"):
            node.trigger_reactive_ingestion(state)

        payload = producer.send.call_args.kwargs["value"]
        # D4: raw_question is not word-split into keywords. The payload's
        # keywords list contains only the entity from structured_intent.
        assert payload["keywords"] == ["Iran"]
        # Specific sanity check — none of these stop-words leak in.
        for stop in ("Will", "the", "Federal", "Reserve", "cut", "rates"):
            assert stop not in payload["keywords"], (
                f"raw_question token {stop!r} must not leak into keywords"
            )

    # --- B8: case-insensitive dedup; first-occurrence case preserved ---

    def test_payload_dedup_case_insensitive(self):
        from agent.nodes import trigger_reactive_ingestion as node

        # "Iran" and "iran" collapse to a single entry; first occurrence
        # ("Iran") preserves its original case for newsapi.ai's
        # case-insensitive matching.
        state = _state_with_entities("Iran", "iran", "IRAN", "OPEC")
        producer = _mock_kafka_send_success()

        with patch.object(node, "_get_producer", return_value=producer), \
             patch.object(node, "reactive_triggers_log"):
            node.trigger_reactive_ingestion(state)

        payload = producer.send.call_args.kwargs["value"]
        assert payload["keywords"] == ["Iran", "OPEC"]

    # --- B9: cap at 8 keywords (D4) ---

    def test_payload_capped_at_8_keywords(self):
        from agent.nodes import trigger_reactive_ingestion as node

        # 12 entities supplied; cap enforces exactly 8.
        many = [f"kw{i}" for i in range(12)]
        state = _state_with_entities(*many)
        producer = _mock_kafka_send_success()

        with patch.object(node, "_get_producer", return_value=producer), \
             patch.object(node, "reactive_triggers_log"):
            node.trigger_reactive_ingestion(state)

        payload = producer.send.call_args.kwargs["value"]
        assert len(payload["keywords"]) == 8
        # First-8 preservation: the cap drops the LATER terms, not random
        # ones — entities-first semantic guarantees the earlier ones stay.
        assert payload["keywords"] == [f"kw{i}" for i in range(8)]

    # --- B10: log-write failure is non-fatal ---

    def test_log_write_failure_does_not_fail_node(self):
        from agent.nodes import trigger_reactive_ingestion as node

        state = _state_with_entities("Iran")
        producer = _mock_kafka_send_success(offset=99)

        with patch.object(node, "_get_producer", return_value=producer), \
             patch.object(node, "reactive_triggers_log") as mock_log:
            # Persistence outage: insert raises.
            mock_log.insert.side_effect = RuntimeError(
                "simulated postgres outage"
            )

            # Node must NOT propagate the exception. It still returns the
            # state delta because Kafka already accepted the message.
            delta = node.trigger_reactive_ingestion(state)

        assert delta == {"reactive_triggers_emitted": 1}
        # Kafka still sent.
        producer.send.assert_called_once()
        # Log was attempted (and raised — swallowed by the node).
        mock_log.insert.assert_called_once()


# ============================================================
# Bundle C: T23.7 — Gate 1 consolidated coverage
# ============================================================
# Tests in this bundle span more than one task and would not fit
# cleanly into Bundle A (T23.6) or Bundle B (T23.5) alone:
#   * C1  cross-task: T23.6's env-var override is wired into T23.5's
#         rate-limit gate at runtime
#   * C2  T23.5 robustness: empty/whitespace entities → no emit, no
#         counter increment
#   * C2b T23.5 robustness: missing or empty sufficiency_checks → no
#         crash; entities-only path
#   * C4  T23.2-vs-producer drift protection: dispatch's kwargs must
#         match NewsAPIProducer.run_reactive's real signature
#   * C5  contract: T23.5's emitted payload passes T23.2's
#         validate_trigger
# ============================================================


class TestSprint23ConsolidatedGate1:
    """Bundle C — cross-task / drift-protection tests (Sprint 23 T23.7)."""

    # --- C1: env-var override on the per-session limit reaches the node ---

    def test_rate_limit_responds_to_env_var_override(self, monkeypatch):
        """[C1] B2 covered limit=1 (the default). C1 covers limit=3 via
        env-var override + module reloads, then asserts the node permits
        exactly 3 attempts before blocking.

        Why two reloads in sequence:
            1. Reload `agent.config.settings` so its module-level
               `AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION = int(os.getenv(...))`
               re-reads the override.
            2. Reload `agent.nodes.trigger_reactive_ingestion` so its
               `from agent.config.settings import ...` rebinds to the new
               value (Python's import statements snapshot the symbol; they
               don't track later mutations).

        Teardown reverses both: `monkeypatch.undo()` clears the env var,
        then both modules are reloaded to restore default state so
        subsequent tests aren't poisoned.
        """
        monkeypatch.setenv("AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION", "3")

        import agent.config.settings as settings_mod
        importlib.reload(settings_mod)
        from agent.nodes import trigger_reactive_ingestion as node
        importlib.reload(node)

        try:
            # Below the limit at prior counter values 0, 1, 2 — each
            # attempt is permitted and the counter increments.
            for prior in (0, 1, 2):
                state = _state_with_entities(
                    "Iran", reactive_triggers_emitted=prior
                )
                producer = _mock_kafka_send_success()
                with patch.object(
                    node, "_get_producer", return_value=producer
                ), patch.object(node, "reactive_triggers_log"):
                    delta = node.trigger_reactive_ingestion(state)
                assert delta == {"reactive_triggers_emitted": prior + 1}, (
                    f"At prior={prior}, expected emit. Got delta={delta}."
                )

            # At the limit (counter == 3) — blocks. No producer, no log,
            # counter written back unchanged (LangGraph requires a write).
            state = _state_with_entities(
                "Iran", reactive_triggers_emitted=3
            )
            with patch.object(node, "_get_producer") as mock_make, \
                 patch.object(node, "reactive_triggers_log") as mock_log:
                delta = node.trigger_reactive_ingestion(state)
            mock_make.assert_not_called()
            mock_log.insert.assert_not_called()
            assert delta == {"reactive_triggers_emitted": 3}
        finally:
            monkeypatch.undo()
            importlib.reload(settings_mod)
            importlib.reload(node)

    # --- C2: empty/whitespace entities → no emit, no counter increment ---

    def test_empty_or_whitespace_entities_skips_emit(self):
        """[C2] When `_build_keywords` produces an empty list (entities
        are all empty/whitespace strings; keywords are entities-only per R1),
        the node skips Kafka emit and does NOT increment the counter.

        This is the "no usable signal" branch — distinct from the
        rate-limit gate. The counter is reserved for actual emit
        attempts; a no-signal skip shouldn't burn budget the agent
        might use productively in a later sufficiency check.
        """
        from agent.nodes import trigger_reactive_ingestion as node

        state = {
            "session_id": "test-session-c",
            "structured_intent": {"entities": ["", "   ", "\t\n"]},
        }

        with patch.object(node, "_get_producer") as mock_make, \
             patch.object(node, "reactive_triggers_log") as mock_log:
            delta = node.trigger_reactive_ingestion(state)

        mock_make.assert_not_called()
        mock_log.insert.assert_not_called()
        # Counter written back at its current (unset → 0) value. Semantic
        # no-op: "nothing to be rate-limited for in the absence of any
        # input signal." LangGraph requires every node to write at least
        # one channel; the same-value-back idiom satisfies that without
        # observable state change.
        assert delta == {"reactive_triggers_emitted": 0}

    # --- C2b: missing or empty sufficiency_checks → no crash ---

    @pytest.mark.parametrize(
        "sufficiency_checks_value",
        [None, []],
        ids=["explicit_None", "empty_list"],
    )
    def test_missing_or_empty_sufficiency_checks_does_not_crash(
        self, sufficiency_checks_value
    ):
        """[C2b] `state['sufficiency_checks'][-1]` is the canonical access
        point for missing_dimensions inside _build_keywords. If
        sufficiency_checks is absent, explicit None, or an empty list,
        the node must NOT raise IndexError — it should fall back to
        entities-only keyword construction and emit normally.

        This invariant is currently enforced by the `if sufficiency else []`
        guard inside `_build_keywords` ([trigger_reactive_ingestion.py]).
        Pinned here so a future "refactor for clarity" can't reintroduce
        the crash. Bundle B's tests happen to omit sufficiency_checks
        entirely (implicit absent-key coverage); this test makes the
        invariant explicit for the present-but-falsy cases.
        """
        from agent.nodes import trigger_reactive_ingestion as node

        state = {
            "session_id": "test-session-c",
            "structured_intent": {"entities": ["Iran"]},
            "sufficiency_checks": sufficiency_checks_value,
        }
        producer = _mock_kafka_send_success()

        with patch.object(node, "_get_producer", return_value=producer), \
             patch.object(node, "reactive_triggers_log"):
            delta = node.trigger_reactive_ingestion(state)

        payload = producer.send.call_args.kwargs["value"]
        # Did not crash, did emit, used entities only.
        assert payload["keywords"] == ["Iran"]
        assert delta == {"reactive_triggers_emitted": 1}

    # --- C4: consumer dispatch's kwargs match the producer's real signature ---

    def test_consumer_dispatch_matches_producer_signature(self):
        """[C4] Drift protection between the consumer's dispatch kwargs
        and `NewsAPIProducer.run_reactive`'s real signature.

        The existing test_trigger_consumer tests (T23.2 N4/N5) mock the
        producer entirely with MagicMock, which means a signature drift
        in the real producer (e.g., renaming `keywords` to `terms`)
        would not break those tests. A live trigger, however, would fail
        at runtime with `TypeError: run_reactive() got an unexpected
        keyword argument 'keywords'`.

        This test reads the real signature via inspect.signature and
        pins the parameter names the consumer's dispatch() relies on.
        Pure introspection — no mocks, no Kafka, no HTTP.
        """
        import inspect
        from ingestion.newsapi_producer import NewsAPIProducer

        sig = inspect.signature(NewsAPIProducer.run_reactive)
        param_names = set(sig.parameters.keys())

        # `keywords` — required kwarg the dispatcher always passes
        assert "keywords" in param_names, (
            "NewsAPIProducer.run_reactive must accept 'keywords' — the "
            "consumer's dispatch() (ingestion_trigger_consumer.py) calls "
            "producer.run_reactive(keywords=trigger['keywords'])."
        )
        # `time_window_days` — optional kwarg the dispatcher forwards when
        # the trigger payload includes it
        assert "time_window_days" in param_names, (
            "NewsAPIProducer.run_reactive must accept 'time_window_days' — "
            "the consumer's dispatch() forwards it as a kwarg when the "
            "trigger payload includes the field."
        )

    # --- C5: T23.5's payload passes T23.2's validate_trigger ---

    def test_node_payload_passes_consumer_validate_trigger(self):
        """[C5] The Kafka payload built by trigger_reactive_ingestion must
        pass the consumer-side `validate_trigger` without errors. This is
        the canonical contract test between the agent's emit-side and the
        pipeline's consume-side.

        A failure here means either the node's payload shape drifted or
        the consumer's validation tightened — either way, live triggers
        emitted by the node would be DLQ'd by the consumer. Catching it
        in Gate 1 avoids that whole class of regressions.
        """
        from agent.nodes import trigger_reactive_ingestion as node
        from orchestration.ingestion_trigger_consumer import validate_trigger

        state = _state_with_entities("Iran", "OPEC")
        producer = _mock_kafka_send_success()

        with patch.object(node, "_get_producer", return_value=producer), \
             patch.object(node, "reactive_triggers_log"):
            node.trigger_reactive_ingestion(state)

        emitted_payload = producer.send.call_args.kwargs["value"]
        errors = validate_trigger(emitted_payload)
        assert errors == [], (
            f"Node payload rejected by consumer validate_trigger: {errors}. "
            f"Payload was: {emitted_payload!r}"
        )
