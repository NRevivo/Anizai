"""
Worker entry point for the Agentic Hub (Sprint 18).

Long-running process that subscribes to forecastQueries with status='pending'
and dispatches each new doc to process_query(). Container target: runs as
`python -m agent.worker` inside the agent-worker service (T8 + T9).

Concurrency model — single-threaded sequential
    The Firestore SDK delivers snapshot callbacks on one dedicated
    background thread; we process each change synchronously inside the
    callback. Atomic-claim (firestore_client.claim_query) prevents races
    if a future deployment runs multiple workers in parallel —
    AGENT_MAX_CONCURRENT_SESSIONS exists in settings as a forward-flag
    for Sprint 19+ when real reasoning lands.

Drain semantics — finish current, refuse new, exit
    1. Signal handler (SIGTERM/SIGINT) sets _shutdown_event.
    2. The snapshot callback checks _shutdown_event at the top of each
       iteration and skips remaining docs — they stay 'pending' for the
       next worker to pick up.
    3. Any in-flight process_query() finishes naturally; the check is
       between docs, not mid-flight.
    4. main() unsubscribes the Watch (which joins the watch thread,
       waiting for any in-flight callback to return) and exits.

Sprint 26 forward-flag — soft-cancel mid-flight
    The drain check is between docs. Sprint 18 stub processing is
    sub-second so an in-flight call always finishes before any reasonable
    SIGTERM grace window. When Sprint 19+ adds real reasoning,
    process_query() will take 15-30s (LLM round-trips, vault queries,
    reactive search). At that point SIGTERM should signal a soft-cancel
    into the in-flight LangGraph run rather than waiting it out — e.g.,
    propagate a cancellation token through ForecastState and have nodes
    check it between LLM calls. Out of scope for Sprint 18; defer to
    Sprint 26 hardening.

Listener error handling — crash + container restart
    The SDK's public on_snapshot takes a single callback; there is no
    separate error channel. The snapshot callback wraps its body in
    try/except. On any uncaught exception the worker sets a fatal flag,
    sets _shutdown_event, and main() exits non-zero so Docker's
    `restart: unless-stopped` policy (T9) takes over. We do not retry
    in-process — that hides systemic problems (expired credentials,
    revoked permissions) under a healthy-looking process.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.8.1 (worker pattern)
    - data-pipeline/docs/agentic_hub_spec.md §8.8.2 (lifecycle)
    - data-pipeline/docs/agentic_hub_spec.md §8.11 (configuration)
"""

import logging
import signal
import sys
import threading

from agent.config import settings
from agent.firestore_client import init_app, subscribe_pending_queries
from agent.health import start_health_server
from agent.process_query import process_query

logger = logging.getLogger(__name__)

# Module-level state. All are safe to mutate from any thread:
#   - threading.Event is thread-safe by design.
#   - _listener_error is a single bool — assignment is atomic in CPython.
# Signals fire on the main thread only (Python signal restriction); the
# watch thread sets _listener_error if its callback raises.
#
# _ready_event flips /health from 503 starting to 200 ok once
# subscribe_pending_queries() returns. _shutdown_event flips /health to
# 503 draining and unblocks main()'s wait().
_shutdown_event = threading.Event()
_ready_event = threading.Event()
_listener_error = False


def _signal_handler(signum: int, _frame) -> None:
    """SIGTERM/SIGINT → set _shutdown_event. Runs on the main thread; the
    watch thread checks the event between docs to drain cleanly."""
    logger.info("worker: received signal %s, draining", signum)
    _shutdown_event.set()


def _on_snapshot(_col_snapshot, changes, _read_time) -> None:
    """
    Snapshot callback fired by the Firestore SDK on each query update.

    The SDK serializes callbacks on a single watch thread, so two snapshots
    cannot run concurrently for this Watch. We iterate `changes` and
    dispatch each ADDED doc to process_query synchronously.

    Filtering rules:
      - Only ADDED matters. MODIFIED/REMOVED are byproducts of our own
        claim flipping status to 'claimed' (the doc leaves the where set).
      - _shutdown_event check at the top of the loop: skip remaining docs
        if the user has hit Ctrl+C or SIGTERM has fired. Skipped docs
        stay 'pending' for the next worker.
      - Per-doc try/except: a process_query failure logs and continues —
        we do not crash the listener over one bad doc.

    The outer try/except is the listener's only failure signal (the SDK
    has no separate error channel). On uncaught exception we set the
    fatal flag and exit; the container restart policy takes over.
    """
    global _listener_error
    try:
        added = [c for c in changes if c.type.name == "ADDED"]
        if added:
            logger.info(
                "worker: snapshot delivered %d pending doc(s)", len(added),
            )
        for change in added:
            if _shutdown_event.is_set():
                logger.info(
                    "worker: shutdown in progress, skipping remaining docs",
                )
                return
            doc_id = change.document.id
            try:
                process_query(doc_id)
            except Exception:
                logger.exception(
                    "worker: process_query raised for doc_id=%s; "
                    "continuing with next doc",
                    doc_id,
                )
    except Exception:
        logger.exception(
            "worker: snapshot callback raised; treating as fatal "
            "and triggering shutdown",
        )
        _listener_error = True
        _shutdown_event.set()


def main() -> None:
    """
    Worker entry point. `python -m agent.worker` resolves here.

    Lifecycle:
      1. Log startup + worker_id.
      2. Register signal handlers on the main thread (Python restriction:
         signals can only be installed and delivered on the main thread).
      3. init_app() — Firebase Admin SDK initialization (idempotent).
      4. subscribe_pending_queries() — register snapshot listener; the
         SDK starts a background thread that delivers callbacks.
      5. _shutdown_event.wait() — main thread parks until SIGTERM,
         SIGINT, or a fatal listener error sets the event.
      6. watch.unsubscribe() — joins the watch thread, waits for any
         in-flight callback to return, closes the gRPC stream.
      7. Exit non-zero if a fatal listener error happened so the
         container restart policy reacts.
    """
    # Fix for KG-PHASE8-7: configure the root logger before any logger.info
    # call. Without this, INFO-level lines from every agent module are
    # silently dropped — Python's root logger has no handlers by default,
    # so the existing `logger.info(...)` calls throughout worker.py and
    # the agent nodes were never reaching stdout. Format string matches
    # the pipeline-side producer convention (e.g., orchestration/
    # ingestion_trigger_consumer.py:356, ingestion/hackernews_producer.py:691).
    # stream=sys.stdout is intentional for containerized log capture —
    # docker/k8s tail stdout, not stderr.
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        stream=sys.stdout,
    )

    worker_id = settings.AGENT_WORKER_ID
    logger.info("worker: starting, worker_id=%s", worker_id)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # Health server starts before init_app/subscribe so orchestrator
    # probes get a clean 503 'starting' rather than connection refused
    # while Firebase init is in flight.
    health_server = start_health_server(
        settings.HUB_HEALTH_HOST,
        settings.HUB_HEALTH_PORT,
        _ready_event,
        _shutdown_event,
    )

    init_app()
    watch = subscribe_pending_queries(_on_snapshot)
    logger.info(
        "worker: subscribed to forecastQueries pending listener, "
        "worker_id=%s",
        worker_id,
    )

    # Flip /health to 200 ok now that the listener is registered.
    _ready_event.set()

    # Poll-based wait: Python on Windows cannot deliver SIGINT to interrupt
    # a blocking Event.wait() with no timeout. Polling every 1s lets the
    # signal handler run and set the event. SIGTERM on Linux/Mac is
    # unaffected — same end behavior, added Windows Ctrl+C support.
    while not _shutdown_event.is_set():
        _shutdown_event.wait(timeout=1.0)

    logger.info("worker: unsubscribing watch, worker_id=%s", worker_id)
    watch.unsubscribe()

    # Shutdown order: watch first (so no new processing kicks off while
    # the health server is going down), health server second. The daemon
    # thread auto-exits with the process if shutdown() is missed, but
    # explicit shutdown releases the port immediately.
    logger.info("worker: stopping health server, worker_id=%s", worker_id)
    health_server.shutdown()

    if _listener_error:
        logger.error(
            "worker: exiting non-zero due to listener error, worker_id=%s",
            worker_id,
        )
        sys.exit(1)
    logger.info("worker: shutdown complete, worker_id=%s", worker_id)


if __name__ == "__main__":
    main()
