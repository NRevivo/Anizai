"""
utils/logging_config.py — Centralized Structured JSON Logging (Section 7.2).

Implements the three-tier observability contract from Section 7.2:

  1. Structured JSON output — all log records serialized as JSON lines for
     Google Cloud Logging compatibility. Uses python-json-logger (already
     pinned in requirements.txt as python-json-logger==2.0.7).

  2. Sampled logging — 100% capture for ERROR and above; 1% sampling for
     INFO. WARNING is also captured at 100% (it signals degraded state).
     Reduces cloud log costs without sacrificing error visibility.

  3. Distributed tracing — every log record carries the trace_id of the
     active Kafka message envelope, enabling end-to-end debugging from
     Producer to Gold Layer without a separate tracing backend.

Usage pattern — called once per process, typically at the top of main():
    from utils.logging_config import setup_logging, set_trace_id
    setup_logging()  # idempotent — safe to call at module import

Usage pattern — called per-message in processors and producers:
    set_trace_id(envelope["trace_id"])
    logger.info("Processing message", extra={"source": "newsapi"})
    # JSON output: {"timestamp": "...", "level": "INFO",
    #               "logger": "processing.silver_job",
    #               "message": "Processing message",
    #               "trace_id": "4a7b...", "source": "newsapi"}

The trace_id context is held in a contextvars.ContextVar, which is both
thread-safe and async-safe. In PyFlink, each Python worker subprocess has
its own independent ContextVar state — set_trace_id() in one worker never
contaminates another worker's logging context.

References:
    - Section 7.2: Pipeline Observability — The Monitoring Contract
    - Section 3.2: DRY — shared logic must live in utils/, never duplicated
    - Section 3.2: Message Envelope & Metadata (trace_id field)
"""

from __future__ import annotations

import contextvars
import logging
import os
import random
import sys

from pythonjsonlogger import jsonlogger

__all__ = ["setup_logging", "set_trace_id", "get_trace_id", "INFO_SAMPLE_RATE"]

# ==========================================================
# Sampling Configuration (Section 7.2)
# ==========================================================
# INFO_SAMPLE_RATE: fraction of INFO records to emit.
# 0.01 = 1% — at typical pipeline throughput this means one
# INFO log per ~100 records, sufficient to confirm liveness
# without flooding Google Cloud Logging.
#
# Override with LOG_INFO_SAMPLE_RATE=1.0 in .env for local dev
# when you want to see all INFO records during debugging.
# ==========================================================
INFO_SAMPLE_RATE: float = float(os.getenv("LOG_INFO_SAMPLE_RATE", "0.01"))

# ==========================================================
# Distributed Tracing Context (Section 7.2)
# ==========================================================
# Holds the trace_id for the current execution context.
# Default "" — log records with no active message context
# emit trace_id: "" (shows up as an empty string in JSON,
# making it easy to filter "unbound" log lines in Cloud Logging).
# ==========================================================
_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_id", default=""
)

# Guard against multiple setup_logging() calls registering
# duplicate handlers on the root logger.
_logging_configured: bool = False


# ==========================================================
# Public API
# ==========================================================

def set_trace_id(trace_id: str) -> None:
    """
    Bind a trace_id to the current execution context.

    Call this immediately after unpacking a Kafka envelope in any
    processor or producer. All log records emitted in the same
    context after this call will include trace_id in their JSON output.

    Why contextvars (not threading.local): ContextVar is both thread-safe
    and coroutine-safe. PyFlink Python worker subprocesses each start with
    a fresh ContextVar state — set_trace_id() in one worker never leaks
    into another worker's log context.

    Why call it at every message boundary: processors handle many messages
    per second. Without rebinding, every log line after the first message
    would carry the stale trace_id of whichever message happened to call
    set_trace_id() last in that worker.

    Args:
        trace_id: UUID string from envelope["trace_id"] (Section 3.2).
    """
    _trace_id_var.set(trace_id)


def get_trace_id() -> str:
    """
    Return the trace_id bound to the current execution context.

    Useful in persistence modules that receive a processed record
    without direct access to the original envelope — they can call
    get_trace_id() to include the ID in their own log lines.

    Returns:
        The active trace_id, or "" if no message is currently in scope.
    """
    return _trace_id_var.get()


def setup_logging(
    level: int = logging.INFO,
    stream=None,
) -> None:
    """
    Configure the root logger with structured JSON output and sampling.

    Idempotent — calling this multiple times has no effect after the
    first call. This allows every module to call setup_logging() at
    import time (or in main()) without risking duplicate handlers or
    double-logging.

    Handler stack applied to every log record (in order):
        1. _TraceIdFilter    — injects trace_id from ContextVar into record
        2. _SampledInfoFilter — drops ~99% of INFO records, keeps all ERRORs
        3. JsonFormatter      — serializes the surviving record as a JSON line

    JSON output fields:
        timestamp: ISO8601 UTC (e.g., "2024-01-15T12:34:56.789Z")
        level:     levelname (e.g., "INFO", "ERROR")
        logger:    module logger name (e.g., "processing.silver_job")
        message:   the log message string
        trace_id:  active trace_id from envelope (injected by TraceIdFilter)
        <extra>:   any fields passed via extra={...} on the logger call

    Args:
        level:  Root logger level. Default INFO. Use DEBUG in tests.
        stream: Output stream. Default None → sys.stdout.
                Passed explicitly in tests to capture output.
    """
    global _logging_configured
    if _logging_configured:
        return

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setLevel(level)

    # JSON formatter with clean field names for Cloud Logging.
    # pythonjsonlogger automatically includes any non-reserved record
    # attributes as extra JSON fields — trace_id set by _TraceIdFilter
    # appears in output without being explicitly listed in the format string.
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

    # TraceIdFilter runs first so that trace_id is set on the record
    # before SampledInfoFilter decides whether to emit it — a sampled
    # INFO record that is kept will already carry the correct trace_id.
    handler.addFilter(_TraceIdFilter())
    handler.addFilter(_SampledInfoFilter())

    root_logger.addHandler(handler)
    _logging_configured = True


# ==========================================================
# Internal Filters — not exported; use setup_logging() instead
# ==========================================================

class _TraceIdFilter(logging.Filter):
    """
    Inject the active trace_id into every log record.

    Why a Filter (not a custom Formatter subclass): Filters run before
    the Formatter and mutate the LogRecord in-place. Setting
    record.trace_id here means it is available to any Formatter or
    downstream handler — not just the JSON formatter we configure.
    pythonjsonlogger automatically includes non-reserved record attributes
    as extra JSON fields, so trace_id appears in the JSON output with
    no additional Formatter configuration.

    References:
        - Section 7.2: Distributed Tracing — trace_id in every log line
        - Section 3.2: Message Envelope (trace_id field)
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _trace_id_var.get()
        return True  # always allow the record through


class _SampledInfoFilter(logging.Filter):
    """
    Enforce the Section 7.2 sampling policy:

        ERROR and above  → 100% emitted (incident response, never drop)
        WARNING          → 100% emitted (degraded state, must be visible)
        INFO             → INFO_SAMPLE_RATE fraction emitted (default 1%)
        DEBUG            → 100% emitted when DEBUG level is active
                           (DEBUG is off in production; it passes here
                           but the root logger level gates it upstream)

    Why sample only INFO: INFO is the highest-volume tier in a streaming
    pipeline — it fires on every processed message. At 100msg/sec, 1%
    sampling means ~1 log line/sec vs. 100/sec, a 99% cost reduction.
    WARNING and ERROR fire rarely and must always reach the operator.

    The INFO_SAMPLE_RATE value is read once at module import time from
    the LOG_INFO_SAMPLE_RATE environment variable (default 0.01).
    Set LOG_INFO_SAMPLE_RATE=1.0 in .env to see all INFO during dev.

    References:
        - Section 7.2: Sampling-Based Logging (1% INFO, 100% ERROR)
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True                          # WARNING / ERROR / CRITICAL
        if record.levelno == logging.INFO:
            return random.random() < INFO_SAMPLE_RATE
        return True                              # DEBUG — gate is upstream
