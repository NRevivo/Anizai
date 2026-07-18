"""
agent/tools/_retry.py — shared vault-read retry policy (Sprint 26 T26.6).

Single home for the agent tools layer's transient-error retry, so the profile
is defined ONCE (DRY, CLAUDE.md §3.2) rather than repeated across the nine
vault-read wrappers in market/knowledge/social/mapping_tools. Wraps the Phase
9.5 helper `utils.retry.retry_on_transient` — no new retry infrastructure.

Why a TIGHTER profile than Gold's insert path (which uses the helper's 5 / 1.0
/ 16.0 defaults, ~15s worst-case backoff):
    Agent vault reads execute inside vault_query's per-agent future, hard-capped
    at PER_AGENT_TIMEOUT_S (15s, agent/nodes/vault_query.py:69), and vault_query
    is fail-fast — an exhausted-retry read RAISES and fails the whole forecast
    (no per-agent degradation until the deferred error taxonomy). Gold's ~15s
    backoff cannot complete inside that 15s window: the future would time out
    first, so we'd pay the full wait and still fail. The tighter profile
    (settings AGENT_VAULT_RETRY_* → 3 / 0.5 / 2.0 → ≤1.5s total backoff) rides
    out short transient DNS/connection races (the Phase-9.5 F6 threat model)
    while leaving ample room for the ~10 DB queries an agent issues.

Scope note: this retry covers short transient errors ONLY, not a full Postgres
scale-restart (<15s) — the per-agent timeout fails that regardless. That is an
accepted existing property, unchanged by 26.6.

Config isolation (hub-principles P5): the numeric profile lives in
agent/config/settings.py, not hardcoded here.

Spec: docs/B_hub/plans/sprint26_pretest_hardening.md 26.6 (§8.7.5).
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

from agent.config import settings
from utils.retry import retry_on_transient

T = TypeVar("T")


def vault_read_retry(
    fn: Callable[..., T],
    *args: Any,
    op_name: str,
    **kwargs: Any,
) -> T:
    """
    Call a persistence read `fn(*args, **kwargs)` under the agent vault-read
    retry profile (settings.AGENT_VAULT_RETRY_*).

    `op_name` is required (keyword-only) so every retry log line names the vault
    op that flaked. All other positional/keyword args forward to `fn` unchanged;
    on a transient error the call is retried, on a permanent error it raises
    immediately (see utils.retry.retry_on_transient).
    """
    return retry_on_transient(
        fn,
        *args,
        max_attempts=settings.AGENT_VAULT_RETRY_MAX_ATTEMPTS,
        base_delay=settings.AGENT_VAULT_RETRY_BASE_DELAY_S,
        max_delay=settings.AGENT_VAULT_RETRY_MAX_DELAY_S,
        op_name=op_name,
        **kwargs,
    )
