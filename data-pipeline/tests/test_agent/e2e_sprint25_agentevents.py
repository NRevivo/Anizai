"""
T25.12(a) E2E harness — agentEvents REAL-TIME delivery (Sprint 25).

Standalone; NOT a pytest test (no `test_` prefix → not collected). It makes REAL
OpenAI calls, so it is run ONCE, manually. It proves the agentEvents stream is
DELIVERED DURING the run (not merely written — Gate 3 already showed written),
in order, with a single runId + currentRunId, plus suggestedActions on the
SessionResult. Emulator + real OpenAI, matching the Sprint-24 precedent.

Cost guard (Ron 2026-07-16): the three retrieval agents are MOCKED (empty
packages) so no real sources are hit; with empty evidence rate_evidence
short-circuits, leaving ~4 real OpenAI calls (query_understand, build_embedding,
synthesize [gpt-4o], generate_suggested_actions) — comfortably under $0.10. The
reactive trigger's Kafka producer is stubbed too (Kafka is down; its send is
noise for THIS gate — the trigger node still emits its agentEvent). EXACTLY ONE
run: one listener, one process_query, no loop.

The deliverable is ARRIVAL TIMING: the listener subscribes BEFORE the run,
records the wall-clock arrival of each snapshot, and a live check confirms at
least one event arrived while process_query was still executing.

Run:
    FIRESTORE_EMULATOR_HOST=localhost:8080 \\
      data-pipeline/venv/Scripts/python.exe \\
      data-pipeline/tests/test_agent/e2e_sprint25_agentevents.py
"""

from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from types import SimpleNamespace
from unittest.mock import patch

# Run as a standalone script: put data-pipeline/ (this file's grandparent's
# parent) on sys.path so `agent` / `utils` / `config` import like they do under
# pytest (whose rootdir is data-pipeline).
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "localhost:8080")

import firebase_admin
import google.auth.credentials
from firebase_admin import credentials, firestore


_EXPECTED_SEQUENCE = [
    "claim_session", "query_understand", "build_embedding", "vault_query",
    "sufficiency_check", "trigger_reactive_ingestion", "rate_evidence",
    "synthesize", "generate_suggested_actions", "write_to_firestore",
]


class _EmulatorCreds(credentials.Base):
    """Anonymous credential for the emulator (skips real auth; matches conftest)."""

    def get_credential(self):
        return google.auth.credentials.AnonymousCredentials()


def _init_emulator():
    if not firebase_admin._apps:
        firebase_admin.initialize_app(_EmulatorCreds(), {"projectId": "anizai-ai"})
    return firestore.client()


def _seed(db, session_id: str, question: str) -> None:
    db.collection("sessions").document(session_id).set({
        "status": "queued", "createdAt": firestore.SERVER_TIMESTAMP,
    })
    db.collection("forecastQueries").document(session_id).set({
        "queryId": f"q_{session_id}", "sessionId": session_id,
        "userId": "e2e-user", "question": question, "status": "pending",
        "createdAt": firestore.SERVER_TIMESTAMP, "claimedAt": None, "claimedBy": None,
    })


def main() -> int:
    db = _init_emulator()

    # Import AFTER emulator init so the app's firestore_client reuses this app.
    from agent import events as events_mod
    from agent.process_query import process_query

    session_id = f"e2e_s25_{uuid.uuid4().hex[:8]}"
    question = "Will the Federal Reserve cut interest rates in 2026?"
    _seed(db, session_id, question)

    # --- Arrival recorder: monotonic time each snapshot fires ---
    arrivals: list[tuple[float, str, int, str]] = []
    lock = threading.Lock()
    t0 = time.monotonic()

    def _on_snapshot(_col, changes, _read_time):
        now = time.monotonic()
        with lock:
            for ch in changes:
                if ch.type.name in ("ADDED", "MODIFIED"):
                    d = ch.document.to_dict() or {}
                    arrivals.append((now - t0, d.get("type"), d.get("sequence"), d.get("status")))

    watch = (
        db.collection("sessions").document(session_id)
        .collection("agentEvents").on_snapshot(_on_snapshot)
    )
    time.sleep(1.0)  # let the listener establish BEFORE triggering the run

    # --- Mocks: 3 retrieval agents (empty) + the trigger's Kafka producer ---
    fake_future = SimpleNamespace(get=lambda timeout=None: SimpleNamespace(offset=0, partition=0))
    fake_producer = SimpleNamespace(send=lambda *a, **k: fake_future)
    empty_r = {"articles": [], "source_diversity": {}, "recency_range": None, "empty": True}
    empty_p = {"market_consensus": [], "community_discussion": [], "overall_sentiment": 0.0, "empty": True}
    empty_m = {"polymarket": None, "linked_sources": [], "fred_anomalies": [], "google_trends": [], "empty": True}

    proc_error: list[str] = []
    mid_run_delivery = threading.Event()

    def _run():
        try:
            process_query(session_id)
        except Exception as exc:  # noqa: BLE001
            proc_error.append(repr(exc))

    print(f"[e2e] session_id={session_id}")
    print(f"[e2e] question={question!r}")
    print("[e2e] triggering process_query (REAL OpenAI) ...")

    with (
        patch("agent.agents.researcher.run", return_value=empty_r),
        patch("agent.agents.pulse_analyst.run", return_value=empty_p),
        patch("agent.agents.market_bridge.run", return_value=empty_m),
        patch("agent.nodes.trigger_reactive_ingestion._get_producer", return_value=fake_producer),
        patch("agent.nodes.trigger_reactive_ingestion._log_attempt", lambda *a, **k: None),
    ):
        proc_thread = threading.Thread(target=_run, daemon=True)
        proc_thread.start()
        # LIVE check: while process_query runs, watch for the first arrival.
        while proc_thread.is_alive():
            with lock:
                if arrivals:
                    mid_run_delivery.set()
                    break
            time.sleep(0.05)
        proc_thread.join(timeout=120)

    run_returned_at = time.monotonic() - t0

    # Let the final events flush + reach the listener (Firestore delivery latency).
    events_mod.drain(5.0)
    time.sleep(2.0)
    watch.unsubscribe()

    # --- Read final state from the emulator ---
    ev_docs = list(
        db.collection("sessions").document(session_id).collection("agentEvents").stream()
    )
    final_events = sorted((d.to_dict() for d in ev_docs), key=lambda e: e.get("sequence", 0))
    session_doc = db.collection("sessions").document(session_id).get().to_dict() or {}
    result_doc = db.collection("sessionResults").document(session_id).get().to_dict() or {}

    # --- Report: the ordered ARRIVAL log ---
    print("\n=== agentEvents ARRIVAL LOG (wall-clock, relative to listener start) ===")
    with lock:
        for (t, etype, seq, status) in arrivals:
            print(f"  +{t:6.2f}s  seq={str(seq):>3}  {str(etype):<28} {status}")
    print(f"\n[e2e] process_query returned at +{run_returned_at:.2f}s")

    run_ids = {e.get("runId") for e in final_events}
    first_arrival = arrivals[0][0] if arrivals else None

    ok = True

    def _check(label: str, cond: bool) -> None:
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")

    print("\n=== ASSERTIONS ===")
    _check("process_query completed without error", not proc_error)
    _check(
        f"MID-RUN delivery: an event arrived WHILE process_query ran "
        f"(first at +{first_arrival:.2f}s, run returned +{run_returned_at:.2f}s)"
        if first_arrival is not None else "MID-RUN delivery: an event arrived during the run",
        mid_run_delivery.is_set(),
    )
    _check(
        f"full ordered stream ({len(final_events)} events)",
        [e.get("type") for e in final_events] == _EXPECTED_SEQUENCE,
    )
    _check(f"single runId across the run ({run_ids})", len(run_ids) == 1)
    _check(
        "currentRunId on the session doc matches the events",
        len(run_ids) == 1 and session_doc.get("currentRunId") in run_ids,
    )
    sa = result_doc.get("suggestedActions") or []
    _check(
        f"suggestedActions present on SessionResult ({len(sa)} items)",
        bool(sa) and all({"id", "label", "prompt"} <= set(a) for a in sa),
    )

    print(f"\n[e2e] runId          = {next(iter(run_ids)) if run_ids else None}")
    print(f"[e2e] currentRunId   = {session_doc.get('currentRunId')}")
    print(f"[e2e] session.status = {session_doc.get('status')}")
    print(f"[e2e] suggestedActions = {sa}")
    if proc_error:
        print(f"[e2e] process_query error: {proc_error}")

    print(f"\n[e2e] {'PASS  T25.12(a)' if ok else 'FAIL  T25.12(a)'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
