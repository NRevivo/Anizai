"""
agent/errors.py — Shared exception types for the Agentic Hub.

Sprint 19 (T19.5): introduces a single user-visible error code,
`AGENT_PROCESSING_ERROR`, raised when any node along the forecast graph
fails in a way that should surface as a Firestore `failed` session result.

The full error taxonomy (timeout vs. rate-limit vs. validation vs. quota
exhaustion vs. upstream-vault-down, with per-code retry/backoff guidance)
is deferred to Sprint 26 / T26.1. Until then, every node-level failure
collapses to `AGENT_PROCESSING_ERROR` and is logged with `details` for
operator triage.

T19.11 routes `AgentProcessingError` through `process_query.py` (the thin
graph runner) so any uncaught node failure becomes a `failed` write to
`sessionResults/{id}` with `error.code = "AGENT_PROCESSING_ERROR"`.

T19.11 also introduces `SessionClaimRaceLostError` as a typed subclass.
It carries the same user-facing code (until T26.1) but lets the runner
distinguish "another worker won the claim" — a quiet no-op — from a real
processing failure that should mark both docs `failed`. Using a typed
subclass instead of message-prefix matching keeps the race-lost path
explicit at the boundary; T26.1 can keep the same shape and only change
the user-facing code.
"""

from __future__ import annotations


class AgentProcessingError(RuntimeError):
    """
    Raised when an Agentic Hub node fails in a way that should mark the
    session `failed`. Attaches a free-form `details` string for operator
    logs; the user-facing code is always `"AGENT_PROCESSING_ERROR"` until
    T26.1 introduces the full taxonomy.
    """

    code: str = "AGENT_PROCESSING_ERROR"

    def __init__(self, details: str) -> None:
        super().__init__(f"{self.code}: {details}")
        self.details = details


class SessionClaimRaceLostError(AgentProcessingError):
    """
    Raised by `claim_session` when `firestore_client.claim_query` returns
    None — meaning another worker already claimed this forecastQueries doc
    or the doc no longer exists.

    Why a typed subclass (not a sentinel string match):
        The runner needs to distinguish race-loss (a quiet no-op — the
        sibling worker will produce the result) from real processing
        failures (mark both docs `failed`, log the traceback). Catching a
        typed subclass at the boundary keeps that branch explicit; matching
        on `str(exc).startswith("claim_session: session not claimable")`
        would silently break the moment someone reworded the message.

    Why it inherits AgentProcessingError:
        Any node still in the middle of the graph that wraps unexpected
        exceptions in `AgentProcessingError` continues to do so; nothing
        else needs to know about this subclass. Callers that don't care
        about the distinction (e.g., the eventual telemetry layer) keep
        catching the parent class.
    """

