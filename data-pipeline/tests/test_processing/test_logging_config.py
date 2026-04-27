"""
Unit tests for utils/logging_config.py (Section 7.2).

Tests the three-tier observability contract:
  1. Structured JSON output — required fields present, correct key names
  2. Sampled logging — INFO at ~1%, ERROR/WARNING at 100%
  3. Distributed tracing — trace_id propagates from set_trace_id() into records

Test index:
    [1]  get_trace_id: returns "" by default (no trace_id bound)
    [2]  set_trace_id / get_trace_id: round-trip returns the set value
    [3]  set_trace_id: overwriting replaces the previous value
    [4]  TraceIdFilter: injects trace_id="" into record when no trace_id bound
    [5]  TraceIdFilter: injects active trace_id into record after set_trace_id()
    [6]  TraceIdFilter: always returns True (never suppresses a record)
    [7]  SampledInfoFilter: ERROR records always pass (100% capture)
    [8]  SampledInfoFilter: CRITICAL records always pass (100% capture)
    [9]  SampledInfoFilter: WARNING records always pass (100% capture)
    [10] SampledInfoFilter: INFO records sampled at ~1% over 10,000 trials
    [11] SampledInfoFilter: DEBUG records pass (gate is root logger level)
    [12] JSON output: required fields present (timestamp, level, logger, message)
    [13] JSON output: trace_id field present after set_trace_id()
    [14] JSON output: extra dict fields appear in JSON output
    [15] JSON output: each record is valid JSON (parseable)
    [16] setup_logging: idempotent — two calls produce exactly one handler
    [17] set_trace_id: does not leak across threads (contextvar isolation)
"""

from __future__ import annotations

import io
import json
import logging
import threading
import time

import pytest

import utils.logging_config as log_cfg
from utils.logging_config import (
    INFO_SAMPLE_RATE,
    _SampledInfoFilter,
    _TraceIdFilter,
    get_trace_id,
    set_trace_id,
    setup_logging,
)


# ==========================================================
# Helpers
# ==========================================================

def _make_record(level: int, msg: str = "test message") -> logging.LogRecord:
    """Create a minimal LogRecord at the given level."""
    record = logging.LogRecord(
        name="test.logger",
        level=level,
        pathname="test_logging_config.py",
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    return record


def _capture_handler(stream: io.StringIO, level: int = logging.DEBUG) -> logging.StreamHandler:
    """
    Build a StreamHandler with JsonFormatter + both filters attached.

    Used in JSON-output tests to capture log output without calling
    setup_logging() (which touches the global root logger and is
    guarded by _logging_configured).
    """
    from pythonjsonlogger import jsonlogger
    handler = logging.StreamHandler(stream)
    handler.setLevel(level)
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={
            "asctime":   "timestamp",
            "levelname": "level",
            "name":      "logger",
        },
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    handler.setFormatter(formatter)
    handler.addFilter(_TraceIdFilter())
    handler.addFilter(_SampledInfoFilter())
    return handler


# ==========================================================
# Fixtures
# ==========================================================

@pytest.fixture(autouse=True)
def reset_trace_id():
    """
    Reset trace_id to "" before and after each test.

    Why: set_trace_id() mutates a ContextVar. Without cleanup, a
    trace_id set in one test could bleed into the next test within
    the same thread.
    """
    set_trace_id("")
    yield
    set_trace_id("")


@pytest.fixture()
def reset_logging_flag(monkeypatch):
    """
    Reset the _logging_configured guard and clear root logger handlers.

    Why: setup_logging() is idempotent via a module-level bool. Tests
    that exercise setup_logging() directly need a clean slate. Using
    monkeypatch ensures the original value is restored after each test.
    """
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    monkeypatch.setattr(log_cfg, "_logging_configured", False)
    root.handlers.clear()
    yield
    root.handlers = original_handlers
    monkeypatch.setattr(log_cfg, "_logging_configured", False)


# ==========================================================
# [1-3] ContextVar: get/set round-trips
# ==========================================================

class TestContextVar:

    def test_default_is_empty_string(self):
        """[1] get_trace_id returns '' when no trace_id has been bound (Section 7.2)."""
        set_trace_id("")   # explicit reset (autouse fixture already does this)
        assert get_trace_id() == ""

    def test_set_and_get_roundtrip(self):
        """[2] set_trace_id / get_trace_id round-trip returns the exact set value."""
        trace_id = "4a7b8c9d-0000-0000-0000-123456789abc"
        set_trace_id(trace_id)
        assert get_trace_id() == trace_id

    def test_overwrite_replaces_previous(self):
        """[3] Calling set_trace_id twice replaces the first value."""
        set_trace_id("first-id")
        set_trace_id("second-id")
        assert get_trace_id() == "second-id"


# ==========================================================
# [4-6] _TraceIdFilter
# ==========================================================

class TestTraceIdFilter:

    def test_injects_empty_string_when_no_trace_id(self):
        """[4] TraceIdFilter sets trace_id='' on record when context is empty."""
        filt = _TraceIdFilter()
        record = _make_record(logging.INFO)
        filt.filter(record)
        assert record.trace_id == ""

    def test_injects_active_trace_id(self):
        """[5] TraceIdFilter injects the trace_id set via set_trace_id() (Section 7.2)."""
        trace_id = "deadbeef-1234-5678-abcd-000000000000"
        set_trace_id(trace_id)
        filt = _TraceIdFilter()
        record = _make_record(logging.INFO)
        filt.filter(record)
        assert record.trace_id == trace_id

    def test_always_returns_true(self):
        """[6] TraceIdFilter never suppresses a record — it only annotates."""
        filt = _TraceIdFilter()
        for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL):
            record = _make_record(level)
            assert filt.filter(record) is True


# ==========================================================
# [7-11] _SampledInfoFilter
# ==========================================================

class TestSampledInfoFilter:

    def test_error_always_passes(self):
        """[7] ERROR records always pass the filter regardless of INFO_SAMPLE_RATE (Section 7.2)."""
        filt = _SampledInfoFilter()
        results = [filt.filter(_make_record(logging.ERROR)) for _ in range(1000)]
        assert all(results), "ERROR records must never be sampled away"

    def test_critical_always_passes(self):
        """[8] CRITICAL records always pass the filter (Section 7.2)."""
        filt = _SampledInfoFilter()
        results = [filt.filter(_make_record(logging.CRITICAL)) for _ in range(1000)]
        assert all(results), "CRITICAL records must never be sampled away"

    def test_warning_always_passes(self):
        """[9] WARNING records always pass the filter (degraded state signal — Section 7.2)."""
        filt = _SampledInfoFilter()
        results = [filt.filter(_make_record(logging.WARNING)) for _ in range(1000)]
        assert all(results), "WARNING records must never be sampled away"

    def test_info_sampled_at_approximately_one_percent(self):
        """
        [10] INFO records pass at approximately INFO_SAMPLE_RATE (default 1%) (Section 7.2).

        Statistical bounds: with N=10_000 trials and p=0.01, expected passes=100,
        stddev≈9.95. Using ±5 sigma (50-150) gives effectively zero false-failure
        probability in CI — the binomial 5-sigma range is far wider than any
        reasonable RNG variation.
        """
        filt = _SampledInfoFilter()
        n_trials = 10_000
        passed = sum(
            1 for _ in range(n_trials)
            if filt.filter(_make_record(logging.INFO))
        )
        # Accept 0.5% – 1.5% (5-sigma for p=0.01, N=10000)
        low  = int(n_trials * INFO_SAMPLE_RATE * 0.5)
        high = int(n_trials * INFO_SAMPLE_RATE * 1.5) + 1
        assert low <= passed <= high, (
            f"INFO sampling ratio out of range: {passed}/{n_trials} passed "
            f"(expected {low}–{high} at INFO_SAMPLE_RATE={INFO_SAMPLE_RATE})"
        )

    def test_debug_passes_through_filter(self):
        """[11] DEBUG records pass _SampledInfoFilter; gating is the root logger level."""
        filt = _SampledInfoFilter()
        results = [filt.filter(_make_record(logging.DEBUG)) for _ in range(100)]
        assert all(results), "DEBUG must not be filtered by SampledInfoFilter"


# ==========================================================
# [12-15] JSON Output Format
# ==========================================================

class TestJsonOutput:

    def _emit_and_parse(self, level: int, msg: str, extra: dict | None = None) -> dict:
        """Emit one record through the full handler stack, return parsed JSON."""
        buf = io.StringIO()
        handler = _capture_handler(buf, level=logging.DEBUG)
        logger = logging.getLogger(f"test.json.{level}")
        logger.setLevel(logging.DEBUG)
        logger.handlers = [handler]
        logger.propagate = False

        if extra:
            logger.log(level, msg, extra=extra)
        else:
            logger.log(level, msg)

        output = buf.getvalue().strip()
        assert output, "Handler produced no output — record may have been sampled away"
        return json.loads(output)

    def test_required_fields_present(self):
        """[12] JSON output contains timestamp, level, logger, message (Section 7.2)."""
        data = self._emit_and_parse(logging.ERROR, "required fields test")
        assert "timestamp" in data, "missing 'timestamp'"
        assert "level"     in data, "missing 'level'"
        assert "logger"    in data, "missing 'logger'"
        assert "message"   in data, "missing 'message'"

    def test_trace_id_present_after_set(self):
        """[13] trace_id appears in JSON after set_trace_id() is called (Section 7.2)."""
        trace_id = "test-trace-00000000-0000-0000-0000"
        set_trace_id(trace_id)
        data = self._emit_and_parse(logging.ERROR, "trace id test")
        assert "trace_id" in data, "trace_id field missing from JSON output"
        assert data["trace_id"] == trace_id

    def test_extra_fields_appear_in_json(self):
        """[14] extra dict fields passed to the logger call appear in JSON output."""
        data = self._emit_and_parse(
            logging.ERROR,
            "extra fields test",
            extra={"source": "newsapi", "record_count": 42},
        )
        assert data.get("source") == "newsapi"
        assert data.get("record_count") == 42

    def test_output_is_valid_json(self):
        """[15] Each emitted record is a valid, parseable JSON object."""
        buf = io.StringIO()
        handler = _capture_handler(buf, level=logging.DEBUG)
        logger = logging.getLogger("test.json.validity")
        logger.setLevel(logging.DEBUG)
        logger.handlers = [handler]
        logger.propagate = False

        # Emit several ERROR records (guaranteed to pass the sampler)
        messages = ["first error", "second error", "third error"]
        for msg in messages:
            logger.error(msg)

        lines = [l for l in buf.getvalue().splitlines() if l.strip()]
        assert len(lines) == len(messages), (
            f"Expected {len(messages)} JSON lines, got {len(lines)}"
        )
        for line in lines:
            obj = json.loads(line)   # raises JSONDecodeError if invalid
            assert isinstance(obj, dict)


# ==========================================================
# [16] setup_logging idempotency
# ==========================================================

class TestSetupLogging:

    def test_idempotent_two_calls_one_handler(self, reset_logging_flag):
        """
        [16] Calling setup_logging() twice registers exactly one StreamHandler.

        Uses a delta check (handlers_after - handlers_before == 1) rather than
        asserting an absolute count of 1, because pytest's log-capture plugin
        may add its own handler to the root logger independently of our fixture.
        """
        root = logging.getLogger()
        buf = io.StringIO()
        count_before = len(root.handlers)
        setup_logging(stream=buf)
        setup_logging(stream=buf)  # second call must be a no-op
        count_after = len(root.handlers)
        assert count_after - count_before == 1, (
            f"Expected exactly 1 new handler after two setup_logging() calls, "
            f"got {count_after - count_before} new handlers "
            f"(before={count_before}, after={count_after})"
        )


# ==========================================================
# [17] Thread isolation
# ==========================================================

class TestThreadIsolation:

    def test_trace_id_does_not_leak_across_threads(self):
        """
        [17] set_trace_id() in one thread does not affect another thread's context.

        Why: ContextVar state is per-context. Threads start with the ContextVar
        value of the thread that spawned them (copy-on-write), but mutations via
        set_trace_id() are isolated to the calling thread's context. This test
        verifies that isolation holds for two concurrent threads.
        """
        id_in_thread_b: list[str] = []

        def thread_b_fn():
            # Thread B starts, reads the current trace_id (should be "" or whatever
            # the spawning thread had), then sets its own trace_id.
            # Crucially: thread A's set_trace_id() must not have overwritten B's value.
            time.sleep(0.02)  # let thread A set its trace_id first
            id_in_thread_b.append(get_trace_id())

        thread_a_trace = "thread-a-trace-id"
        thread_b = threading.Thread(target=thread_b_fn)

        set_trace_id("")     # reset spawning context
        thread_b.start()
        set_trace_id(thread_a_trace)  # thread A sets its own ID
        thread_b.join()

        # Thread B should NOT have seen thread A's trace_id
        assert id_in_thread_b[0] != thread_a_trace, (
            "trace_id leaked from thread A into thread B — ContextVar isolation broken"
        )
        # Thread A's trace_id is still correct in this context
        assert get_trace_id() == thread_a_trace
