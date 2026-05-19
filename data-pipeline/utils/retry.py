"""
Transient-error retry helper — Phase 9.5 Stage B Item 1b.

A small retry-with-exponential-backoff utility used by Gold's
momentum_vault_insert path to survive transient Postgres-side failures
(DNS resolution races during cluster scale-up, OperationalError on a
brief connection drop, etc.) without immediately routing every failure
to DLQ.

Why a custom helper instead of `tenacity`:
    Tenacity would be a new top-level dependency requiring a rebuild
    of both the Flink and producer Docker images and a new pinned line
    in requirements.txt / requirements.lock. The retry policy we need
    is small and bounded — a self-contained ~30-line helper avoids the
    dependency-management cost.

Why bounded retries (not infinite):
    A persistent Postgres outage would otherwise block Flink processing
    indefinitely, accumulating Kafka consumer lag and eventually
    exhausting the broker's bronze retention window. 5 retries with
    exponential backoff caps the worst-case at ~30 seconds — long
    enough to ride out a normal scale-cycle Postgres-restart (which
    completes in <15 s in Phase 9.5 F6), short enough that a true
    outage degrades gracefully via DLQ rather than silently freezing.

Why only retry on transient classes:
    Permanent failures (schema mismatch, constraint violation,
    authentication failure) should NOT be retried — they will fail the
    same way every time, and DLQ is the correct destination. We
    enumerate the transient classes explicitly so future psycopg2
    additions don't silently start being retried.

Spec: phase95_cluster_robustness_implementation.md Stage B fix 1b.
"""

from __future__ import annotations

import logging
import socket
import time
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


# Exception classes we treat as transient and retry on.
#
#   - socket.gaierror: DNS resolution failure. The exact class raised when
#     psycopg2 tries to connect to a host whose DNS has not yet resolved —
#     the F6 finding that drove this helper.
#   - ConnectionError: low-level network failures (host unreachable,
#     RST received mid-handshake, etc.).
#   - TimeoutError: connect/recv timeout.
#   - OSError: broader IO failures (e.g. EPIPE on Postgres restart).
#
# psycopg2 wraps DNS / connect failures in OperationalError, which inherits
# from psycopg2.Error → DatabaseError → Exception. We catch psycopg2's
# OperationalError dynamically below to avoid a hard import at module load
# (this file is imported in test environments without psycopg2 installed).
_TRANSIENT_BASE_CLASSES: tuple[type[BaseException], ...] = (
    socket.gaierror,
    ConnectionError,
    TimeoutError,
)


def _is_transient(exc: BaseException) -> bool:
    """
    Return True if `exc` is in a class we should retry.

    Handles psycopg2.OperationalError dynamically (without importing
    psycopg2 at module load) by matching on class qualified name.
    """
    if isinstance(exc, _TRANSIENT_BASE_CLASSES):
        return True
    qualname = type(exc).__qualname__
    module = type(exc).__module__ or ""
    # psycopg2.OperationalError covers connection refused / could-not-
    # translate-host-name / Postgres-restart-mid-write. We also include
    # psycopg2.errors.OperationalError for forward compat (psycopg2 ≥ 2.8
    # has both paths). InterfaceError is its parent class for some
    # connection-state failures.
    if module.startswith("psycopg2") and qualname in (
        "OperationalError",
        "InterfaceError",
    ):
        return True
    return False


def retry_on_transient(
    fn: Callable[..., T],
    *args: Any,
    max_attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 16.0,
    op_name: str = "operation",
    **kwargs: Any,
) -> T:
    """
    Call `fn(*args, **kwargs)` retrying on transient exceptions.

    Total worst-case latency at max_attempts=5, base_delay=1.0, max_delay=16.0:
        attempt 1 fails → sleep 1s
        attempt 2 fails → sleep 2s
        attempt 3 fails → sleep 4s
        attempt 4 fails → sleep 8s
        attempt 5 fails → raise (total elapsed ~15s plus the time
                                 spent inside fn() on each attempt)

    Args:
        fn:           Callable to invoke. Typically a thin wrapper around
                      a DB insert.
        *args, **kwargs: Forwarded to fn.
        max_attempts: Total tries including the first. Default 5.
        base_delay:   Initial backoff in seconds. Default 1s.
        max_delay:    Cap on a single sleep. Default 16s.
        op_name:      Short string used in retry-attempt logging.

    Returns:
        Whatever fn() returns on the first successful attempt.

    Raises:
        Whatever exception fn() raised on the LAST attempt, if all
        attempts failed. A non-transient exception is re-raised
        immediately without retry.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn(*args, **kwargs)
        except BaseException as exc:
            if not _is_transient(exc):
                # Permanent error — surface immediately for DLQ routing.
                raise
            last_exc = exc
            if attempt == max_attempts:
                logger.error(
                    "retry: %s — giving up after %d attempts; last error: %r",
                    op_name, attempt, exc,
                )
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            logger.warning(
                "retry: %s — attempt %d/%d failed (%r); sleeping %.1fs",
                op_name, attempt, max_attempts, exc, delay,
            )
            time.sleep(delay)
    # Unreachable — the loop either returns or re-raises. Belt-and-braces:
    assert last_exc is not None  # nosec: assert-used — defensive
    raise last_exc
