"""
Gate 1 tests for `agent/worker.py` logging setup (T19.14 — KG-PHASE8-7).

Scope is intentionally narrow: pin the `logging.basicConfig` call shape
at worker entry. The rest of `main()` (signal handlers, Firebase init,
snapshot subscription, drain loop) is process-boundary orchestration —
Gate 3 emulator territory, not Gate 1.

What's pinned:
  - basicConfig is called with `level` from `settings.LOG_LEVEL`
  - format string matches the pipeline-side producer convention
    (`%(asctime)s %(levelname)s %(name)s — %(message)s`, em-dash separator)
  - stream is `sys.stdout` (containerized log capture; docker/k8s tail
    stdout, not stderr)
  - basicConfig fires BEFORE the first `logger.info(...)` call in main()
    — without this ordering the kg-phase8-7 fix is moot

Why mock `_shutdown_event` to be pre-set:
    `main()` parks on `_shutdown_event.wait(timeout=1)` until SIGTERM. Pre-
    setting the event makes the wait loop exit on the first iteration, so
    the unit test is fast and deterministic.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.8.2 (worker lifecycle)
    - task_plan.md Known Gap KG-PHASE8-7
"""

from __future__ import annotations

import logging
import sys
from unittest.mock import MagicMock, patch

import pytest

from agent import worker
from agent.config import settings


# ==========================================================
# Settings — LOG_LEVEL contract
# ==========================================================
def test_settings_exposes_log_level():
    """settings.LOG_LEVEL must exist as a module attribute. Worker reads
    it directly — a missing attribute would AttributeError at startup.
    Default is INFO unless AGENT_LOG_LEVEL env var overrides; the
    instance under test may have either value depending on the dev's
    .env, so we only pin presence + type here."""
    assert hasattr(settings, "LOG_LEVEL")
    assert isinstance(settings.LOG_LEVEL, str)
    # Sanity: the module-level default is INFO. Any value other than the
    # standard logging vocabulary is a misconfiguration worth flagging.
    assert settings.LOG_LEVEL in {
        "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL",
    }


# ==========================================================
# Fixture — prepare worker.main() for one-shot execution
# ==========================================================
@pytest.fixture
def main_with_immediate_drain():
    """Run `worker.main()` once with the drain loop short-circuited.

    Patches:
      - logging.basicConfig: spy (no-op, so prior test config is preserved)
      - signal.signal: no-op (don't install real handlers in test)
      - worker.init_app / subscribe_pending_queries / start_health_server:
        mocked since they touch Firebase + bind a TCP port
      - _shutdown_event: pre-set so the `while not is_set: wait()` loop
        exits on the first check
      - _listener_error: forced False so main() exits cleanly (no sys.exit)

    Yields the basicConfig spy so each test can assert call shape.
    """
    # Pre-set the shutdown event so the drain loop exits immediately
    worker._shutdown_event.set()
    # Force-clear the listener-error flag (other tests could have set it)
    worker._listener_error = False

    with (
        patch("agent.worker.logging.basicConfig") as mock_basic_config,
        patch("agent.worker.signal.signal"),
        patch("agent.worker.init_app"),
        patch("agent.worker.subscribe_pending_queries") as mock_subscribe,
        patch("agent.worker.start_health_server") as mock_health,
    ):
        mock_subscribe.return_value = MagicMock()  # watch with .unsubscribe()
        mock_health.return_value = MagicMock()      # server with .shutdown()

        try:
            worker.main()
            yield mock_basic_config
        finally:
            # Reset module-global state so other tests aren't poisoned
            worker._shutdown_event.clear()
            worker._ready_event.clear()
            worker._listener_error = False


# ==========================================================
# basicConfig call shape
# ==========================================================
def test_main_calls_basic_config_once(main_with_immediate_drain):
    """Exactly one basicConfig call at startup. Multiple calls are
    silently ignored by Python's logging module (the first wins) but
    pinning a single call surfaces accidental duplicates introduced by
    future edits."""
    assert main_with_immediate_drain.call_count == 1


def test_main_basic_config_uses_settings_log_level(main_with_immediate_drain):
    """The `level` kwarg must come from settings.LOG_LEVEL — that's the
    indirection that lets ops flip to DEBUG via AGENT_LOG_LEVEL=DEBUG
    without a code change."""
    kwargs = main_with_immediate_drain.call_args.kwargs
    assert kwargs["level"] == settings.LOG_LEVEL


def test_main_basic_config_uses_pipeline_format_string(main_with_immediate_drain):
    """Format string matches the pipeline-side producer convention so
    log lines from the worker are visually consistent with producer log
    lines in aggregated output (e.g., `docker compose logs`)."""
    kwargs = main_with_immediate_drain.call_args.kwargs
    assert kwargs["format"] == (
        "%(asctime)s %(levelname)s %(name)s — %(message)s"
    )


def test_main_basic_config_streams_to_stdout(main_with_immediate_drain):
    """stream must be sys.stdout — containerized log capture (docker,
    k8s) tails stdout. Default Python logging goes to stderr, which
    works in dev but mixes with real error streams in prod."""
    kwargs = main_with_immediate_drain.call_args.kwargs
    assert kwargs["stream"] is sys.stdout


# ==========================================================
# Ordering — basicConfig must fire BEFORE the first logger.info
# ==========================================================
def test_main_configures_logging_before_first_info_log():
    """The whole point of the fix is that basicConfig fires first;
    `logger.info("worker: starting...")` immediately after must reach
    stdout. We can't easily assert ordering with two patches alone, so
    we attach both to a manager mock and check the relative position
    of the first basicConfig vs. the first logger.info call."""
    worker._shutdown_event.set()
    worker._listener_error = False

    manager = MagicMock()
    try:
        with (
            patch(
                "agent.worker.logging.basicConfig",
                manager.basicConfig,
            ),
            patch.object(worker.logger, "info", manager.info),
            patch("agent.worker.signal.signal"),
            patch("agent.worker.init_app"),
            patch(
                "agent.worker.subscribe_pending_queries",
                return_value=MagicMock(),
            ),
            patch(
                "agent.worker.start_health_server",
                return_value=MagicMock(),
            ),
        ):
            worker.main()

        # First call on the manager must be basicConfig — anything
        # earlier would mean a logger.info ran without a handler.
        first_call_name = manager.method_calls[0][0]
        assert first_call_name == "basicConfig", (
            f"Expected basicConfig to be the first call, got "
            f"{manager.method_calls[0]}"
        )
    finally:
        worker._shutdown_event.clear()
        worker._ready_event.clear()
        worker._listener_error = False


# ==========================================================
# Regression guard — basicConfig respects the level constants
# ==========================================================
def test_basic_config_accepts_settings_log_level_value():
    """settings.LOG_LEVEL is a string. logging.basicConfig accepts both
    string names ('INFO') and integer constants. Pin that the value
    flowing in from settings is one Python's logging accepts at face
    value, not a typo or accidental cast."""
    # Python's logging.getLevelName accepts the level name and returns the
    # corresponding int — anything other than NOTSET/0 means it parsed.
    parsed = logging.getLevelName(settings.LOG_LEVEL)
    assert isinstance(parsed, int)
    assert parsed != 0  # NOTSET / unknown name
