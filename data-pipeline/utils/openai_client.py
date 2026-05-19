"""
Centralised OpenAI client factory — Phase 9.5 Stage B Item 2.

Single source of truth for the OpenAI client used across Gold processing,
agent nodes, and one-off scripts. Replaces 12 ad-hoc `OpenAI(api_key=...)`
instantiations that had drifted across the codebase, each with different
(or no) `timeout` and `max_retries` settings.

Why a factory function rather than a module-level singleton:
    The OpenAI SDK v1 client is not pickle-safe across some Flink JVM
    serialisation boundaries. The factory returns a fresh client per
    invocation; callers are expected to cache it at the call-site
    (e.g. the existing `_default_client` pattern in agent nodes).

Why max_retries=5 rather than the SDK default of 2:
    Phase 9.5 Stage B Area 3 finding. SDK retry uses exponential backoff
    with `Retry-After` honouring on 429 responses. 2 retries covers a
    ~10s window; 5 retries covers a ~60s window. 5 trades extra latency
    on rare failures for substantially fewer failed-after-retries.

Why timeout=60.0:
    Synthesis calls (GPT-4o with structured-output JSON schema) routinely
    take 20-30s wall-clock. The agent's existing TIMEOUT_S setting is
    60s for synthesise/rate_evidence; this matches that.

Why the OpenAI import is deferred to inside the function:
    The agent nodes had a deliberate lazy-import pattern at module level
    (importing OpenAI inside _get_default_client) so that pytest test
    discovery in environments without OPENAI_API_KEY set wouldn't fail
    on import. The factory preserves that: `from openai import OpenAI`
    is a local import inside get_openai_client.

Spec: phase95_cluster_robustness_implementation.md Stage B fix 2.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from openai import OpenAI


def get_openai_client(
    *,
    api_key: Optional[str] = None,
    timeout: float = 60.0,
    max_retries: int = 5,
) -> "OpenAI":
    """
    Return a properly-configured OpenAI client.

    Args:
        api_key:     Override the API key. If None (the default), the
                     SDK reads OPENAI_API_KEY from the environment.
        timeout:     Request timeout in seconds. Default 60s.
        max_retries: Retries for 429 / 5xx / connection errors / timeouts.
                     Default 5 (SDK default is 2; Phase 9.5 Stage B bumps
                     this to 5 for tighter resilience against transient
                     OpenAI rate limits).

    Returns:
        Configured OpenAI client. Caller should cache at the call-site
        for hot-path performance.
    """
    from openai import OpenAI  # local import preserves the lazy-load contract

    return OpenAI(
        api_key=api_key,
        timeout=timeout,
        max_retries=max_retries,
    )
