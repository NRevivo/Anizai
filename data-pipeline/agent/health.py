"""
Internal health + metrics HTTP server for the Agentic Hub worker (T7).

Runs in a daemon thread inside the same process as agent.worker.main().
Two endpoints:

    GET /health   200 ok | 503 starting | 503 draining
    GET /metrics  200 text/plain — Sprint 18 stub (planned counters listed)

Library choice: stdlib http.server (ThreadingHTTPServer + BaseHTTPRequestHandler).
Two endpoints, no JSON body parsing, no auth, no routing complexity — adds
zero dependencies. aiohttp/flask would be appropriate if we exposed user-
facing endpoints, but the frontend talks to Firestore; this server is
internal-monitoring-only.

State model — three-state /health:
    1. shutdown_event.is_set()        →  503 {"status": "draining"}
    2. not ready_event.is_set()       →  503 {"status": "starting"}
    3. otherwise                      →  200 {"status": "ok", ...}

The events are passed in by argument (not imported from worker.py) so this
module is testable in isolation and there is no private-name leakage
between modules.

No Firestore-connectivity ping in /health (Sprint 18 deliberate scope):
a doc read on every probe at typical 5-30s probe intervals adds thousands
of reads/day per pod and only confirms the SDK's local cache is responsive,
not that the listener is actually delivering. A meaningful "last-successful-
callback timestamp" check belongs in Sprint 26 alongside real metrics
counters.

/metrics is an explicit stub — Prometheus scrapers treat zero counters as
"this counter exists and never increments," masking absence of real
instrumentation. Comment-only payload is honest about the state.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.8.2 (lifecycle, health)
    - data-pipeline/docs/agentic_hub_spec.md §8.11 (HUB_HEALTH_HOST/PORT)
"""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from agent.config import settings
from agent.process_query import AGENT_VERSION

logger = logging.getLogger(__name__)


_METRICS_STUB = (
    "# Sprint 18 stub — Prometheus metrics populated in Sprint 26\n"
    "# Planned counters:\n"
    "#   agent_queries_claimed_total{worker_id}\n"
    "#   agent_queries_done_total{worker_id}\n"
    "#   agent_queries_failed_total{worker_id}\n"
    "#   agent_inflight_queries{worker_id}\n"
    "#   agent_listener_callback_errors_total{worker_id}\n"
)


class _HealthRequestHandler(BaseHTTPRequestHandler):
    """
    Request handler for /health and /metrics.

    Class-level attributes hold the shutdown/ready events so all handler
    instances (BaseHTTPRequestHandler instantiates one per request) share
    the same state. start_health_server() injects them before the server
    starts; tests can override them directly.
    """

    ready_event: Optional[threading.Event] = None
    shutdown_event: Optional[threading.Event] = None

    def do_GET(self) -> None:  # noqa: N802 — name dictated by stdlib
        if self.path == "/health":
            self._handle_health()
        elif self.path == "/metrics":
            self._handle_metrics()
        else:
            self.send_error(404, "Not Found")

    def _handle_health(self) -> None:
        # Precedence: draining beats starting beats ok. Worker shutdown
        # implies the listener is being torn down — orchestrator should
        # stop sending traffic immediately.
        if self.shutdown_event is not None and self.shutdown_event.is_set():
            self._json(503, {"status": "draining"})
            return
        if self.ready_event is None or not self.ready_event.is_set():
            self._json(503, {"status": "starting"})
            return
        self._json(
            200,
            {
                "status": "ok",
                "worker_id": settings.AGENT_WORKER_ID,
                "agent_version": AGENT_VERSION,
            },
        )

    def _handle_metrics(self) -> None:
        body = _METRICS_STUB.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        # Suppress BaseHTTPRequestHandler's stderr access log. Probe traffic
        # is high-volume + low-signal; route through the project logger at
        # DEBUG so it can be enabled selectively.
        logger.debug("health: " + format, *args)


def start_health_server(
    host: str,
    port: int,
    ready_event: threading.Event,
    shutdown_event: threading.Event,
) -> ThreadingHTTPServer:
    """
    Start the health/metrics HTTP server in a daemon thread.

    Args:
        host:           HUB_HEALTH_HOST. 0.0.0.0 inside containers; loopback
                        for local dev. The container compose service uses
                        0.0.0.0 so the host port mapping reaches it.
        port:           HUB_HEALTH_PORT (default 8000 per §8.11).
        ready_event:    set by the worker once subscribe_pending_queries()
                        has returned successfully — flips /health from 503
                        starting to 200 ok.
        shutdown_event: set by the worker on SIGTERM/SIGINT — flips /health
                        from 200 ok to 503 draining.

    Returns:
        The ThreadingHTTPServer handle. Caller MUST call .shutdown() during
        worker shutdown to unblock serve_forever() and release the port
        cleanly. The thread is daemon so it auto-exits with the process
        even if shutdown() is missed.
    """
    _HealthRequestHandler.ready_event = ready_event
    _HealthRequestHandler.shutdown_event = shutdown_event

    server = ThreadingHTTPServer((host, port), _HealthRequestHandler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="health-server",
        daemon=True,
    )
    thread.start()
    logger.info("health: server listening on %s:%d", host, port)
    return server
