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

T19.11 will route this exception through `process_query.py` (the new thin
graph runner) so any uncaught node failure becomes a `failed` write to
`sessionResults/{id}` with `error.code = "AGENT_PROCESSING_ERROR"`.
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
