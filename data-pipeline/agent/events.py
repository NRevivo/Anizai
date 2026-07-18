"""
agent/events.py — non-blocking agentEvents emitter (Sprint 25, T25.5).

The main forecast graph streams a chain-of-thought to the frontend's reasoning
panel by writing `agentEvents` docs under `sessions/{sessionId}/agentEvents`.
This module is the emitter: nodes call `emit_event` / `complete_event` (start +
complete pair) or `emit_done_event` (one-shot); the actual Firestore writes
happen off the critical path on a background thread. The follow-up graph emits
NOTHING (plan §1, 2026-07-04) — this module is used by the main graph only.

Non-blocking (plan §3): `emit_event` / `complete_event` / `emit_done_event` /
`fail_event` enqueue a write task and return immediately; a single daemon
writer thread drains the FIFO in order. ~20 event writes per forecast must
never add wall-clock to the ≤60s p95 forecast (KG-B-5) or be fragile under
network jitter.

Fire-and-forget (plan §3): every write error is logged and swallowed by the
writer; `drain` and `fail_event` are non-raising. A cosmetic UI event must
never crash the worker or fail a forecast.

Per-`runId` guarded registry (plan §3-F / heads-up #1): each run has its own
`_RunContext` (session id + a lock-guarded monotonic `sequence` counter +
in-flight events), held in a module registry keyed by `runId`. The `sequence`
is captured under the run's lock AT ENQUEUE time and stamped on the event, so
two concurrent runs (once `AGENT_MAX_CONCURRENT_SESSIONS` > 1 — default is
already 3) can never interleave into one sequence.

Run resolution is by EXPLICIT `run_id`, NOT thread-local (a deliberate
refinement of the plan's §3-F "thread-local" wording — surfaced 2026-07-15):
LangGraph may execute nodes on pool threads, so a thread-local set inside
`claim_session` would not reliably reach the other nodes. `run_id` is the
single-writer `ForecastState.run_id` field `claim_session` mints (T25.6), so
every node already has it in state and passes it to the emitter — robust to
LangGraph's threading and still per-run/guarded.

Ordering contract (plan §3): `init_run(session_id, run_id)` is called by
`claim_session` ONLY after the claim resolves the real session id, and only
after `currentRunId` is written to the session doc — never before (findings
#3). Emitting before `init_run` is a programming error that logs and no-ops.
`fail_event` and `drain` survive being called before any `init_run` (e.g. a
crash inside `claim_session` before the run context exists — heads-up #2):
they silently no-op, never raise.

Spec references:
    - data-pipeline/docs/B_hub/plans/sprint25_suggested_actions.md §3 / §3-C /
      §3-E / §3-F + T25.5 / T25.6 / T25.13
    - .claude/skills/frontend-integration/SKILL.md (agentEvents schema: the 11
      fields; runId + per-run sequence; status pending/running/done/failed)
    - .claude/skills/hub-principles/SKILL.md (P2 centralized Firestore access;
      G4 graceful degradation)
"""

from __future__ import annotations

import functools
import logging
import queue
import threading
import time
import uuid
from typing import Optional

from agent import firestore_client, metrics
from agent.firestore_client import SERVER_TIMESTAMP

logger = logging.getLogger(__name__)


# ==========================================================
# Per-run context
# ==========================================================
class _RunContext:
    """
    Per-run emitter state: the run's session id, a monotonic `sequence`
    counter, and the in-flight (emitted-but-not-completed) events keyed by
    event_id → monotonic start time (for `durationMs`). One instance per
    `runId`, held in the module registry.

    All mutable state is guarded by `_lock` so concurrent runs never share or
    interleave a counter (heads-up #1). `next_sequence` is the only counter
    mutation, always under the lock.
    """

    __slots__ = ("session_id", "run_id", "_lock", "_sequence", "_inflight")

    def __init__(self, session_id: str, run_id: str) -> None:
        self.session_id = session_id
        self.run_id = run_id
        self._lock = threading.Lock()
        self._sequence = 0
        self._inflight: dict[str, float] = {}

    def next_sequence(self) -> int:
        with self._lock:
            self._sequence += 1
            return self._sequence

    def mark_inflight(self, event_id: str, start: float) -> None:
        with self._lock:
            self._inflight[event_id] = start

    def pop_inflight(self, event_id: str) -> Optional[float]:
        with self._lock:
            return self._inflight.pop(event_id, None)

    def drain_inflight(self) -> list[tuple[str, float]]:
        with self._lock:
            items = list(self._inflight.items())
            self._inflight.clear()
            return items

    def has_inflight(self) -> bool:
        with self._lock:
            return bool(self._inflight)


# ==========================================================
# Module state — registry + shared FIFO + background writer
# ==========================================================
# Lock ordering (to avoid deadlock): _registry_lock is ALWAYS acquired before
# a _RunContext._lock, never the reverse. emit/complete resolve the context
# under _registry_lock, release it, then take the context lock — so the two
# are never held simultaneously there; init_run / _resolve_for_failure hold
# _registry_lock while probing has_inflight(), the only nested acquisition.
_registry_lock = threading.Lock()
_registry: "dict[str, _RunContext]" = {}

_queue: "queue.Queue[dict]" = queue.Queue()

_writer_lock = threading.Lock()
_writer_thread: Optional[threading.Thread] = None


def _ensure_writer() -> None:
    """Lazily start the single daemon writer thread (idempotent)."""
    global _writer_thread
    with _writer_lock:
        if _writer_thread is not None and _writer_thread.is_alive():
            return
        _writer_thread = threading.Thread(
            target=_writer_loop, name="agent-events-writer", daemon=True,
        )
        _writer_thread.start()


def _writer_loop() -> None:
    """
    Drain the shared FIFO forever, writing each task via
    `firestore_client.write_agent_event`. A single consumer preserves FIFO
    order, so the panel never sees a completion before its start. Any write
    error is logged and swallowed — an agentEvents write must never crash the
    worker or block a forecast (fire-and-forget).
    """
    while True:
        task = _queue.get()
        try:
            firestore_client.write_agent_event(
                task["session_id"],
                task["event_id"],
                task["fields"],
                merge=task["merge"],
            )
        except Exception:
            logger.warning(
                "agent-events writer: write failed, swallowing "
                "(session_id=%s event_id=%s)",
                task.get("session_id"), task.get("event_id"),
                exc_info=True,
            )
        finally:
            _queue.task_done()


def _get_context(run_id: Optional[str]) -> Optional[_RunContext]:
    if not run_id:
        return None
    with _registry_lock:
        return _registry.get(run_id)


def _enqueue(session_id: str, event_id: str, fields: dict, *, merge: bool) -> None:
    _queue.put(
        {
            "session_id": session_id,
            "event_id": event_id,
            "fields": fields,
            "merge": merge,
        }
    )


# ==========================================================
# Public API — run lifecycle
# ==========================================================
def init_run(session_id: str, run_id: str) -> None:
    """
    Register a run's emitter context and ensure the writer thread is running.

    Called by `claim_session` (T25.6) ONLY after the claim resolves the real
    session id and after `currentRunId` is written to the session doc — never
    before (findings #3 ordering contract).

    The registry is bounded by EXPLICIT end-of-run disposal (`dispose_run`, from
    process_query's finally), NOT by pruning here on "no in-flight events": a
    run BETWEEN nodes momentarily has zero in-flight events yet is not done, so
    idle-pruning would drop an active concurrent run's context once
    AGENT_MAX_CONCURRENT_SESSIONS > 1 (default is already 3). init_run only
    registers.
    """
    _ensure_writer()
    with _registry_lock:
        _registry[run_id] = _RunContext(session_id, run_id)
    logger.debug("events.init_run: session_id=%s run_id=%s", session_id, run_id)


def dispose_run(run_id: Optional[str]) -> None:
    """
    Remove a run's context from the registry once its processing has ended —
    called from `process_query`'s finally with the captured `run_id`, AFTER the
    final drain. Bounds the registry without the concurrency hazard of pruning
    by idle (see `init_run`): explicit end-of-run disposal is correct under any
    `AGENT_MAX_CONCURRENT_SESSIONS`. The queued write tasks are unaffected (each
    already carries its full stamped doc), so disposing after the drain is safe.
    No-op on None / unknown run_id (e.g. a failure before `init_run`).
    """
    if not run_id:
        return
    with _registry_lock:
        _registry.pop(run_id, None)


# ==========================================================
# Public API — emission
# ==========================================================
def emit_event(
    run_id: Optional[str],
    event_type: str,
    title: str,
    *,
    description: Optional[str] = None,
    payload: Optional[dict] = None,
) -> Optional[str]:
    """
    Enqueue a START event (status 'running') and return its event_id, or None
    when there is no run context for `run_id` (emitting before `init_run` is a
    programming error that logs and no-ops — findings #3). Returns immediately;
    the Firestore write happens on the background writer. Pair with
    `complete_event(run_id, event_id)`.
    """
    ctx = _get_context(run_id)
    if ctx is None:
        logger.debug(
            "events.emit_event: no run context for run_id=%r "
            "(emit before init_run?) — no-op", run_id,
        )
        return None
    event_id = uuid.uuid4().hex
    sequence = ctx.next_sequence()
    ctx.mark_inflight(event_id, time.monotonic())
    _enqueue(
        ctx.session_id, event_id,
        _build_event_doc(
            ctx, event_id, sequence, event_type, title,
            description=description, status="running",
            duration_ms=None, payload=payload,
        ),
        merge=False,
    )
    return event_id


def complete_event(
    run_id: Optional[str],
    event_id: Optional[str],
    *,
    status: str = "done",
    payload: Optional[dict] = None,
) -> None:
    """
    Enqueue the COMPLETION update for a previously-emitted event: sets `status`
    ('done' by default, or 'failed') and `durationMs` (start→now delta, ms). No-op
    if `event_id` or the run context is missing. Returns immediately.
    """
    if not event_id:
        return
    ctx = _get_context(run_id)
    if ctx is None:
        return
    start = ctx.pop_inflight(event_id)
    duration_ms = None if start is None else int((time.monotonic() - start) * 1000)
    fields = {"status": status, "durationMs": duration_ms}
    if payload is not None:
        fields["payload"] = payload
    _enqueue(ctx.session_id, event_id, fields, merge=True)


def emits(event_type: str, title: str):
    """
    Decorator wiring a main-graph node's `run(state, …)` to emit a
    start('running') event on entry and a complete('done') event on a normal
    return — the DRY form of the start/complete pair every emitting node needs
    (§3-E). On an exception the start event is deliberately LEFT in-flight so
    `process_query`'s `fail_event` marks it 'failed' (§3-D); the decorator never
    swallows.

    `run_id` is read from the node's `state` arg (`state["run_id"]`, the
    single-writer field `claim_session` mints). With no run context (unit tests
    that don't `init_run`) both emit/complete no-op, so the decorator is
    transparent — the wrapped return value is passed through unchanged.

    NOT used by `claim_session` (the bootstrap emits a one-shot 'done', not a
    pair, and must `init_run` first) nor `write_to_firestore` (its complete must
    be ordered before the pre-`done` drain and the 'done' status flip — §3-D).
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(state, *args, **kwargs):
            run_id = state.get("run_id") if isinstance(state, dict) else None
            event_id = emit_event(run_id, event_type, title)
            start = time.monotonic()
            result = fn(state, *args, **kwargs)  # raise → event left in-flight
            # Sprint 26 T26.4: record per-node latency on the happy path. A raising
            # node's event is left in-flight for fail_event and its (failed)
            # duration is not part of the latency profile 26.3 reads, so observe
            # only on normal return. Covers the 9 decorated pair-nodes;
            # write_to_firestore (manual emit) observes itself.
            metrics.NODE_DURATION_SECONDS.labels(node_name=event_type).observe(
                time.monotonic() - start
            )
            complete_event(run_id, event_id)
            return result
        return wrapper
    return decorator


def emit_done_event(
    run_id: Optional[str],
    event_type: str,
    title: str,
    *,
    description: Optional[str] = None,
    payload: Optional[dict] = None,
) -> Optional[str]:
    """
    Enqueue a single ONE-SHOT 'done' event (no running→done pair; durationMs
    null). For instantaneous stages like `claim_session`'s bootstrap
    "Analyzing your question…" line (Flag #1) — a start/complete pair would
    write a meaningless durationMs, and an empty panel under status 'running'
    reads as stuck. Returns the event_id, or None if there is no run context.
    """
    ctx = _get_context(run_id)
    if ctx is None:
        logger.debug(
            "events.emit_done_event: no run context for run_id=%r — no-op",
            run_id,
        )
        return None
    event_id = uuid.uuid4().hex
    sequence = ctx.next_sequence()
    _enqueue(
        ctx.session_id, event_id,
        _build_event_doc(
            ctx, event_id, sequence, event_type, title,
            description=description, status="done",
            duration_ms=None, payload=payload,
        ),
        merge=False,
    )
    return event_id


def fail_event(
    run_id: Optional[str] = None,
    *,
    session_id: Optional[str] = None,
    description: Optional[str] = None,
) -> None:
    """
    Fail a run's in-flight event(s) so an aborted run never leaves a dangling
    'running' event (§3-D). Owner: `process_query`'s exception branch (T25.13),
    which passes BOTH the `run_id` it captured from the streamed state and the
    `session_id` it already has (the failing session): `run_id` resolves the
    exact run; `session_id` is the exact backup when `run_id` wasn't captured
    (e.g. the graph raised inside `claim_session` before it minted/returned
    run_id). It NEVER guesses by count (that mis-targets a run once
    `AGENT_MAX_CONCURRENT_SESSIONS` > 1 — default is already 3), NEVER raises,
    and is a silent no-op when neither identifier resolves a run (e.g. a crash
    before `init_run` — heads-up #2).

    - If the resolved run has in-flight events, each is completed as 'failed'
      (with the description).
    - If it has none, a standalone 'error' event is emitted so the panel still
      shows the failure.
    """
    try:
        ctx = _resolve_for_failure(run_id, session_id)
        if ctx is None:
            return
        inflight = ctx.drain_inflight()
        if inflight:
            for event_id, start in inflight:
                duration_ms = int((time.monotonic() - start) * 1000)
                fields = {"status": "failed", "durationMs": duration_ms}
                if description:
                    fields["description"] = description
                _enqueue(ctx.session_id, event_id, fields, merge=True)
        else:
            event_id = uuid.uuid4().hex
            sequence = ctx.next_sequence()
            _enqueue(
                ctx.session_id, event_id,
                _build_event_doc(
                    ctx, event_id, sequence, "error",
                    description or "Run failed",
                    description=description, status="failed",
                    duration_ms=None, payload=None,
                ),
                merge=False,
            )
    except Exception:
        logger.warning("events.fail_event: swallowed error", exc_info=True)


# ==========================================================
# Public API — drain
# ==========================================================
def drain(timeout: float) -> None:
    """
    Block until the shared write queue is fully flushed, up to `timeout`
    seconds. Non-raising. Called: (1) by `write_to_firestore` before the
    session flips to 'done' (pre-done drain, T25.4/§3-D); (2) in
    `process_query`'s `finally` (T25.13); (3) on worker shutdown (T25.13). A
    bounded wait so a stuck Firestore write can't hang shutdown.
    """
    try:
        deadline = time.monotonic() + max(0.0, timeout)
        # Reuse the Queue's own join machinery with a deadline (this is
        # Queue.join() plus a timeout — all_tasks_done is notified on every
        # task_done()).
        with _queue.all_tasks_done:
            while _queue.unfinished_tasks:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning(
                        "events.drain: timed out with %d task(s) still queued",
                        _queue.unfinished_tasks,
                    )
                    return
                _queue.all_tasks_done.wait(remaining)
    except Exception:
        logger.warning("events.drain: swallowed error", exc_info=True)


# ==========================================================
# Internal helpers
# ==========================================================
def _build_event_doc(
    ctx: _RunContext,
    event_id: str,
    sequence: int,
    event_type: str,
    title: str,
    *,
    description: Optional[str],
    status: str,
    duration_ms: Optional[int],
    payload: Optional[dict],
) -> dict:
    """
    Assemble a full agentEvents doc — every field the frontend panel reads
    (§3-C): eventId, sessionId, runId, sequence, timestamp, type, title,
    description, status, durationMs, payload. Deliberately NOT `parentMessageId`
    (a dead field from the cancelled follow-up-events design).
    """
    return {
        "eventId": event_id,
        "sessionId": ctx.session_id,
        "runId": ctx.run_id,
        "sequence": sequence,
        "type": event_type,
        "title": title,
        "description": description,
        "status": status,
        "durationMs": duration_ms,
        "payload": payload,
        "timestamp": SERVER_TIMESTAMP,
    }


def _resolve_for_failure(
    run_id: Optional[str], session_id: Optional[str]
) -> Optional[_RunContext]:
    """
    Resolve the run to fail — by `run_id` first (exact), else by `session_id`
    (each session has at most one active run, so this is unambiguous and
    correct under concurrency, unlike a count-based "single in-flight run"
    guess, which mis-targets or no-ops once >1 run is in flight). Returns None
    when neither resolves a run — a safe no-op (heads-up #2).
    """
    if run_id:
        ctx = _get_context(run_id)
        if ctx is not None:
            return ctx
    if session_id:
        with _registry_lock:
            matches = [
                ctx for ctx in _registry.values()
                if ctx.session_id == session_id
            ]
        # Prefer the run that actually dangled (has in-flight events); fall back
        # to a lone match. >1 matching run WITH in-flight events for one session
        # should never happen (one active run per session) — no-op if it does,
        # rather than fail the wrong run.
        inflight = [ctx for ctx in matches if ctx.has_inflight()]
        if len(inflight) == 1:
            return inflight[0]
        if len(matches) == 1:
            return matches[0]
    return None


# ==========================================================
# Test helper
# ==========================================================
def reset() -> None:
    """
    Drain and clear all run contexts. For test isolation only (NOT used in
    production). The daemon writer thread persists across the reset — it simply
    finds an empty queue and an empty registry.
    """
    drain(timeout=2.0)
    with _registry_lock:
        _registry.clear()
