"""
Unit tests for the Polymarket comments feature flag — Phase 9.5 Stage B Item 5.

The flag (POLYMARKET_COMMENTS_ENABLED, defaulting to false) exists because
Polymarket's Gamma API `/comments` endpoint had a breaking change that
broke the producer's comment-fetch path. The default-off behaviour stops
the ~100-warnings-per-cycle spam without removing the code.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest


def _make_producer_instance():
    """Build a minimal producer instance with the bits comment-loop touches."""
    from ingestion.polymarket_producer import PolymarketProducer

    inst = PolymarketProducer.__new__(PolymarketProducer)
    inst._active_markets = []
    inst._lock = asyncio.Lock()
    inst._emit = MagicMock()
    return inst


def test_comment_loop_disabled_by_default_returns_immediately():
    """
    POLYMARKET_COMMENTS_ENABLED defaults to False. The loop must:
      1. Log one INFO line about the disabled state.
      2. Return without making any external calls.
      3. NOT emit any Bronze messages.
    """
    with patch("config.settings.POLYMARKET_COMMENTS_ENABLED", False):
        inst = _make_producer_instance()
        # Run the coroutine to completion.
        asyncio.run(inst._comment_poll_loop())
        inst._emit.assert_not_called()


def test_comment_loop_enabled_proceeds_past_guard():
    """
    When the flag is on, the loop SHOULD proceed past the early-exit
    guard. We patch asyncio.sleep to raise so the test doesn't actually
    enter the polling loop — proceeding far enough to hit the sleep
    proves the guard is bypassed.
    """
    inst = _make_producer_instance()

    sleep_called = []

    async def fake_sleep(delay):
        sleep_called.append(delay)
        raise RuntimeError("__test_marker__ — sleep was reached")

    with patch("config.settings.POLYMARKET_COMMENTS_ENABLED", True), \
         patch("asyncio.sleep", side_effect=fake_sleep):
        with pytest.raises(RuntimeError, match="__test_marker__"):
            asyncio.run(inst._comment_poll_loop())

    # The 60s startup offset asyncio.sleep was hit; this is the
    # first line after the feature-flag guard.
    assert sleep_called == [60]
