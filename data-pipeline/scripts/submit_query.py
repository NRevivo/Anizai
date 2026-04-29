"""
Submit a forecastQueries doc to the Firestore emulator.

Usage:
    python scripts/submit_query.py
    python scripts/submit_query.py "Will Bitcoin hit 100k by EOY?"

What it does:
    1. Generates a unique sessionId (e2e_<8-hex>).
    2. Writes sessions/{sessionId} with status='queued' (mimics the server-
       side write at session.repository.ts:335-347 — the worker's
       update_session_status calls require this doc to exist before they
       can run).
    3. Writes forecastQueries/{sessionId} with status='pending', which the
       worker's snapshot listener picks up.
    4. Prints the sessionId + Emulator UI URL so the operator can watch
       the round-trip in the browser.

This script is the Sprint 18 T13 manual-validation submission helper, but
also a general-purpose tool for any sprint that needs to manually trigger
the worker against the emulator (e.g., Sprint 19+ smoke testing while
iterating on a new node).

Auth: uses _EmulatorCredentials so it does NOT require ADC (gcloud login).
The worker process itself goes through the production init_app() path and
does need ADC — see the T13 protocol for that side.

Hub config note: this script intentionally hardcodes localhost:8080 +
projectId='anizai-ai' rather than reading agent.config.settings, so it can
be invoked outside the data-pipeline venv if needed and stays trivially
inspectable.

Usage requires the Firestore emulator running:
    firebase emulators:start --only firestore
"""

import os
import sys
import uuid

os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "localhost:8080")

import firebase_admin
import google.auth.credentials
from firebase_admin import credentials, firestore


class _EmulatorCredentials(credentials.Base):
    """Same pattern as tests/test_agent/conftest.py — wraps Google's
    AnonymousCredentials in a firebase_admin.credentials.Base subclass
    because firebase_admin.credentials does not expose AnonymousCredentials
    itself. With FIRESTORE_EMULATOR_HOST set, the SDK skips real auth at
    RPC time, so the inner credential is never validated."""

    def get_credential(self):
        return google.auth.credentials.AnonymousCredentials()


def main(question: str) -> str:
    if not firebase_admin._apps:
        firebase_admin.initialize_app(
            _EmulatorCredentials(), {"projectId": "anizai-ai"}
        )
    db = firestore.client()

    session_id = f"e2e_{uuid.uuid4().hex[:8]}"

    # Pre-create sessions/{sessionId}. update_session_status uses .update()
    # which raises NotFound if the doc is missing — server normally creates
    # this in the same transaction as forecastQueries; we mimic.
    db.collection("sessions").document(session_id).set({
        "status": "queued",
        "createdAt": firestore.SERVER_TIMESTAMP,
    })

    db.collection("forecastQueries").document(session_id).set({
        "queryId": f"q_{session_id}",
        "sessionId": session_id,
        "userId": "e2e-tester",
        "question": question,
        "status": "pending",
        "createdAt": firestore.SERVER_TIMESTAMP,
        "claimedAt": None,
        "claimedBy": None,
    })

    print(f"Submitted: sessionId={session_id}")
    print(f"Question:  {question}")
    print(f"Watch:     http://localhost:4000/firestore/data/forecastQueries/{session_id}")
    print(f"Result:    http://localhost:4000/firestore/data/sessionResults/{session_id}")
    return session_id


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "Will it rain tomorrow?"
    main(q)
