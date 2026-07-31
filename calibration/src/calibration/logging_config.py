"""
Structured logging.

One JSON object per log line. Cloud Logging parses those into queryable
fields; a human-formatted line becomes an opaque string you can only grep.
During an unattended week these lines are the *only* window into what
happened, so they need to be queryable rather than readable-if-you-scroll.

Every task invocation emits exactly one summary line with a stable field set:

    {"severity":"INFO","task":"dispatch","run_id":"...","duration_ms":412,
     "dispatched":5,"failed":0,"truncated":0,"enabled":true}

`severity` rather than `level` — that is the field name Cloud Logging maps to
its own severity, and getting it wrong means every line shows up as "Default"
and no alert can ever fire on an error.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from typing import Any, Iterator

# Python level -> the strings Cloud Logging understands.
_SEVERITY = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}


class JsonFormatter(logging.Formatter):
    """Render a record as a single JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "severity": _SEVERITY.get(record.levelno, "DEFAULT"),
            "message": record.getMessage(),
            "logger": record.name,
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Anything attached via logger.info(..., extra={...}) rides along as a
        # top-level field, which is what makes it queryable.
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in _RESERVED:
                continue
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                value = repr(value)
            payload[key] = value

        return json.dumps(payload, ensure_ascii=False)


_RESERVED = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "module", "msecs",
        "message", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName", "taskName",
    }
)


def configure(level: str | None = None, json_output: bool | None = None) -> None:
    """
    Install the log configuration for this process.

    JSON is the default in the container and plain text locally: a developer
    reading a terminal is served badly by JSON, and Cloud Logging is served
    badly by prose. `K_SERVICE` is set by Cloud Run, so the environment
    decides without anyone having to remember a flag.
    """
    if json_output is None:
        json_output = bool(os.getenv("K_SERVICE")) or os.getenv(
            "CALIBRATION_LOG_JSON", ""
        ).lower() in {"1", "true", "yes"}

    level = level or os.getenv("CALIBRATION_LOG_LEVEL", "INFO")

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter()
        if json_output
        else logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s", "%H:%M:%S")
    )

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())

    # These two are chatty at INFO and say nothing about calibration.
    logging.getLogger("google").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


@contextmanager
def task_log(task: str, **context: Any) -> Iterator[dict[str, Any]]:
    """
    Wrap a scheduled task so it emits exactly one summary line, always.

    Usage:

        with task_log("dispatch") as summary:
            report = dispatch_questions(...)
            summary["dispatched"] = report.dispatched

    The line is emitted on the way out whether the body succeeded or raised —
    a task that dies silently is indistinguishable from one that never fired,
    and during an unattended week nobody is watching to tell the difference.
    """
    started = time.monotonic()
    summary: dict[str, Any] = {}
    logger = logging.getLogger("calibration.task")

    try:
        yield summary
    except Exception as exc:
        logger.error(
            "task %s failed", task,
            extra={
                "task": task,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "outcome": "error",
                "error": str(exc),
                **context, **summary,
            },
            exc_info=True,
        )
        raise
    else:
        logger.info(
            "task %s ok", task,
            extra={
                "task": task,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "outcome": "ok",
                **context, **summary,
            },
        )
