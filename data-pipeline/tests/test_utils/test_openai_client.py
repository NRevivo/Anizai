"""
Unit tests for utils.openai_client.get_openai_client — Phase 9.5 Stage B Item 5.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_factory_returns_client_with_expected_defaults():
    """
    Factory should return an OpenAI client constructed with
    max_retries=5 and timeout=60.0 by default (Phase 9.5 Stage B Item 2).
    """
    with patch("openai.OpenAI") as mock_openai_cls:
        mock_openai_cls.return_value = MagicMock(name="MockOpenAIClient")

        from utils.openai_client import get_openai_client

        client = get_openai_client(api_key="test-key")

        # Factory must construct the client exactly once with the expected
        # kwargs. The kwargs are the contract — drift here is what the
        # centralised factory is supposed to prevent.
        mock_openai_cls.assert_called_once_with(
            api_key="test-key",
            timeout=60.0,
            max_retries=5,
        )
        assert client is mock_openai_cls.return_value


def test_factory_honors_overrides():
    """
    Callers (e.g. agent nodes with their own TIMEOUT_S) can override
    timeout and max_retries.
    """
    with patch("openai.OpenAI") as mock_openai_cls:
        mock_openai_cls.return_value = MagicMock()

        from utils.openai_client import get_openai_client

        get_openai_client(api_key="k", timeout=30.0, max_retries=3)

        mock_openai_cls.assert_called_once_with(
            api_key="k",
            timeout=30.0,
            max_retries=3,
        )


def test_factory_lazy_import_does_not_load_openai_at_module_load():
    """
    Importing utils.openai_client itself MUST NOT import the openai package.
    The agent nodes' original pattern was to defer the openai import until
    first call to support test discovery in environments without
    OPENAI_API_KEY. The factory must preserve that contract.
    """
    import importlib
    import sys

    # Remove any cached imports of utils.openai_client + openai so we can
    # observe what the next import touches.
    for mod in list(sys.modules):
        if mod == "utils.openai_client" or mod == "openai":
            del sys.modules[mod]

    # Importing the factory module must NOT pull in openai.
    importlib.import_module("utils.openai_client")
    assert "openai" not in sys.modules, (
        "utils.openai_client must defer the `from openai import OpenAI` "
        "to inside get_openai_client() so module import is cheap and does "
        "not require the openai package at import time."
    )


def test_factory_propagates_rate_limit_after_retries_exhausted():
    """
    Verify the SDK's max_retries=5 path: when the underlying API call
    raises RateLimitError repeatedly, the call eventually raises.

    We mock at the client.chat.completions level rather than testing the
    real SDK retry behaviour (which would require network and tokens).
    The point of this test is that callers can rely on the SDK propagating
    a final RateLimitError after retries — the centralised factory
    doesn't swallow or transform it.
    """
    from openai import RateLimitError

    mock_response = MagicMock(status_code=429)
    mock_response.request = MagicMock()
    rate_limit_exc = RateLimitError("rate limited", response=mock_response, body=None)

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = rate_limit_exc

    with patch("openai.OpenAI", return_value=mock_client):
        from utils.openai_client import get_openai_client

        client = get_openai_client(api_key="k")

        with pytest.raises(RateLimitError):
            client.chat.completions.create(model="gpt-4o", messages=[])
