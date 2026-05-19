"""
Unit tests for utils.retry.retry_on_transient — Phase 9.5 Stage B Item 5.
"""

from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import pytest

from utils.retry import _is_transient, retry_on_transient


def test_first_call_success_no_sleep():
    fn = MagicMock(return_value="ok")
    with patch("utils.retry.time.sleep") as mock_sleep:
        result = retry_on_transient(fn, max_attempts=5, base_delay=0.001)
    assert result == "ok"
    assert fn.call_count == 1
    mock_sleep.assert_not_called()


def test_transient_failure_then_success_retries_correctly():
    """
    socket.gaierror is the exact class psycopg2 raises on DNS resolution
    failure — the F6 finding that drove this retry helper. Retry once
    and succeed.
    """
    fn = MagicMock(side_effect=[socket.gaierror("temp DNS fail"), "recovered"])
    with patch("utils.retry.time.sleep") as mock_sleep:
        result = retry_on_transient(fn, max_attempts=5, base_delay=0.001)
    assert result == "recovered"
    assert fn.call_count == 2
    # Exactly one sleep between the two attempts.
    assert mock_sleep.call_count == 1


def test_permanent_exception_not_retried():
    """
    ValueError is not in the transient list — it must propagate
    immediately without retry. Permanent errors must NOT be retried
    (otherwise we waste retry budget and delay DLQ routing).
    """
    fn = MagicMock(side_effect=ValueError("schema mismatch"))
    with patch("utils.retry.time.sleep") as mock_sleep:
        with pytest.raises(ValueError):
            retry_on_transient(fn, max_attempts=5, base_delay=0.001)
    assert fn.call_count == 1
    mock_sleep.assert_not_called()


def test_exhausted_retries_raises_last_exception():
    """
    All 5 attempts fail with the transient class — final raise must
    propagate the last exception so the caller sees the underlying
    failure and can decide DLQ routing.
    """
    final_exc = socket.gaierror("persistent DNS fail")
    fn = MagicMock(side_effect=final_exc)
    with patch("utils.retry.time.sleep") as mock_sleep:
        with pytest.raises(socket.gaierror):
            retry_on_transient(fn, max_attempts=5, base_delay=0.001)
    assert fn.call_count == 5
    # Sleep between attempts 1-4 (4 total), no sleep after the final raise.
    assert mock_sleep.call_count == 4


def test_exponential_backoff_doubles_until_max():
    """
    base_delay=2, max_delay=16, max_attempts=6 → sleeps 2, 4, 8, 16, 16.
    Last attempt fails and raises (no trailing sleep).
    """
    fn = MagicMock(side_effect=socket.gaierror("dns"))
    with patch("utils.retry.time.sleep") as mock_sleep:
        with pytest.raises(socket.gaierror):
            retry_on_transient(
                fn, max_attempts=6, base_delay=2.0, max_delay=16.0,
            )
    # Expected sleep durations between the 6 attempts (5 inter-sleeps).
    sleeps = [call.args[0] for call in mock_sleep.call_args_list]
    assert sleeps == [2.0, 4.0, 8.0, 16.0, 16.0]


def test_is_transient_detects_psycopg2_operational_error_by_qualname():
    """
    We deliberately do NOT import psycopg2 in retry.py (to keep
    test environments without it working). The dynamic detection
    must match the class by module+qualname.
    """
    class FakeOpError(Exception):  # noqa: N818 — mirroring psycopg2 naming
        pass
    # Lie about module so the dynamic check finds it. psycopg2.OperationalError
    # has module='psycopg2' (or 'psycopg2.errors' in newer versions).
    FakeOpError.__module__ = "psycopg2"
    FakeOpError.__qualname__ = "OperationalError"
    assert _is_transient(FakeOpError("boom")) is True


def test_is_transient_rejects_unrelated_psycopg2_class():
    class FakeDataError(Exception):
        pass
    FakeDataError.__module__ = "psycopg2"
    FakeDataError.__qualname__ = "DataError"  # not in the allow list
    assert _is_transient(FakeDataError("bad data")) is False


def test_is_transient_accepts_socket_gaierror():
    assert _is_transient(socket.gaierror("dns")) is True


def test_is_transient_accepts_connection_error():
    assert _is_transient(ConnectionError("refused")) is True


def test_is_transient_rejects_keyboard_interrupt():
    """
    KeyboardInterrupt and SystemExit must NOT be retried. The retry
    helper uses except BaseException — but is_transient must say no.
    """
    assert _is_transient(KeyboardInterrupt()) is False
    assert _is_transient(SystemExit(1)) is False
