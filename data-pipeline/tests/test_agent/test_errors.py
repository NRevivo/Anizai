"""
Gate 1 tests for `agent/errors.py` (T19.12).

Pins the exception contract that the runner, every node, and the future
T26.1 taxonomy expansion depend on:

  - `AgentProcessingError.code == "AGENT_PROCESSING_ERROR"` (the single
    user-visible code in Sprint 19; T26.1 will introduce per-failure codes
    via subclass overrides).
  - The message format `"<code>: <details>"`, which the worker logs
    verbatim and operators grep on.
  - `details` is preserved as an attribute so structured logging can read
    it without re-parsing the message string.
  - `SessionClaimRaceLostError` is a subclass of `AgentProcessingError`
    AND inherits the same code (so wider catches and `error_code` writes
    keep working without special-casing).

These tests are tiny by design — the goal is regression protection, not
behaviour exploration. If T26.1 splits the code, these are the canaries
that surface the change.
"""

from __future__ import annotations

import pytest

from agent.errors import AgentProcessingError, SessionClaimRaceLostError


# ==========================================================
# AgentProcessingError contract
# ==========================================================
def test_agent_processing_error_code_is_canonical_string():
    """Pin the user-visible code to the exact string the runner writes
    into `error_code` on `sessions/{id}` failed transitions. Frontend may
    eventually branch on this string — flipping it is a frontend-coordinated
    change, not a refactor."""
    assert AgentProcessingError.code == "AGENT_PROCESSING_ERROR"


def test_agent_processing_error_message_format_and_details_preserved():
    """The str() of the exception must be `<code>: <details>` so worker
    log lines are self-describing without needing the typed exception
    object. `details` must also be available as an attribute for
    structured logging to read without re-parsing."""
    err = AgentProcessingError("synthesize: missing raw_question in state")

    assert str(err) == (
        "AGENT_PROCESSING_ERROR: synthesize: missing raw_question in state"
    )
    assert err.details == "synthesize: missing raw_question in state"


def test_agent_processing_error_is_runtime_error():
    """Pin the inheritance so a `except RuntimeError:` somewhere upstream
    (e.g., the worker's outer catch) keeps catching agent failures even
    if it doesn't import AgentProcessingError directly."""
    err = AgentProcessingError("anything")
    assert isinstance(err, RuntimeError)


# ==========================================================
# SessionClaimRaceLostError contract
# ==========================================================
def test_session_claim_race_lost_is_subclass_of_agent_processing_error():
    """The runner's two error branches rely on this — `except
    SessionClaimRaceLostError` is checked first (quiet skip), then
    `except Exception` catches everything else (mark failed). Subclass
    relationship is what makes the existing wider catches (telemetry,
    `_mark_failed` callers) keep working."""
    assert issubclass(SessionClaimRaceLostError, AgentProcessingError)

    err = SessionClaimRaceLostError("claim_session: session not claimable")
    assert isinstance(err, AgentProcessingError)


def test_session_claim_race_lost_inherits_canonical_code():
    """SessionClaimRaceLostError must expose the same user-visible code
    until T26.1 introduces per-failure codes. Until then, `error_code`
    writes to Firestore stay consistent regardless of which subclass the
    runner caught (in practice the race-lost path doesn't write
    `error_code` — runner returns silently — but pinning the code keeps
    the subclass interchangeable for any future caller that does)."""
    err = SessionClaimRaceLostError("anything")
    assert err.code == "AGENT_PROCESSING_ERROR"
    assert str(err).startswith("AGENT_PROCESSING_ERROR:")


def test_session_claim_race_lost_preserves_details():
    """The race-lost message includes the session_id for log triage —
    pin that the details attribute round-trips so log filters can read
    it from the structured log path, not just the rendered str()."""
    err = SessionClaimRaceLostError(
        "claim_session: session not claimable session_id=abc-123"
    )
    assert err.details == (
        "claim_session: session not claimable session_id=abc-123"
    )
