"""
Firestore client — calibration's narrow, mostly-read-only door into Firestore.

This is the first module in the package with a Firestore surface at all
(Phase 10A had none). Everything it can do is deliberately enumerated here,
because "what can this code touch" is the load-bearing safety property of the
whole calibration system (plan §2.5, N5-N7):

    WRITES — exactly two documents, both created by us, both keyed by a
    sessionId we mint:
        sessions/{sessionId}
        forecastQueries/{sessionId}

    READS — only documents whose id we already hold in Postgres:
        sessions/{sessionId}
        sessionResults/{sessionId}
        sessions/{sessionId}/evidence/*

There is no collection scan anywhere in this module, and no query. The
harvester drives from `calibration_forecasts` rows, so it can only ever ask
for a document it created. That is a stronger guarantee than filtering by
`userId == "calibration-runner"` would be, because it does not depend on the
filter being written correctly at every call site.

There is also no claim logic, no transaction, and no listener. Calibration
does not participate in the agent's work queue; it deposits work and reads
results.

References:
    - calibration_plan.md §3 A6 (two-document dispatch), A7 (userId sentinel)
    - calibration_plan.md §7 (the dispatch contract)
    - data-pipeline/scripts/submit_query.py (the reference implementation)
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)

# The sentinel owner of every calibration session (plan A7). Not a Firebase
# Auth UID, and deliberately so — nothing in the product's user-facing surface
# can enumerate, bill, or render a session owned by a user that does not exist.
CALIBRATION_USER_ID = "calibration-runner"

# Prefix on every sessionId we mint, so a calibration session is recognisable
# in the Firestore console at a glance without opening the document.
SESSION_ID_PREFIX = "cal"

_app = None


class FirestoreUnavailable(RuntimeError):
    """Raised when Firestore cannot be reached or initialised."""


def new_session_id() -> str:
    """
    Mint a Firestore document id for one dispatch.

    Used as the document id for BOTH documents (plan A6). Minting it ourselves
    rather than letting Firestore auto-assign is what makes the two writes
    addressable as a pair, and what lets `calibration_forecasts.session_id` be
    NOT NULL at insert time.
    """
    return f"{SESSION_ID_PREFIX}_{uuid.uuid4().hex[:12]}"


def is_emulator() -> bool:
    """True when FIRESTORE_EMULATOR_HOST is set."""
    return bool(os.getenv("FIRESTORE_EMULATOR_HOST"))


def init_app(project_id: Optional[str] = None):
    """
    Initialise the Firebase Admin app, once per process.

    Against the emulator it uses anonymous credentials, mirroring the pattern
    `scripts/submit_query.py` and `tests/test_agent/conftest.py` already use:
    `firebase_admin.credentials` exposes no AnonymousCredentials, so a thin
    Base subclass wraps Google's. With FIRESTORE_EMULATOR_HOST set the SDK
    never validates the inner credential.

    Against real Firestore it uses Application Default Credentials.
    """
    global _app
    if _app is not None:
        return _app

    import firebase_admin
    from firebase_admin import credentials

    project = project_id or os.getenv("FIREBASE_PROJECT_ID", "anizai-ai")

    if firebase_admin._apps:
        _app = firebase_admin.get_app()
        return _app

    if is_emulator():
        import google.auth.credentials

        class _EmulatorCredentials(credentials.Base):
            def get_credential(self):
                return google.auth.credentials.AnonymousCredentials()

        _app = firebase_admin.initialize_app(
            _EmulatorCredentials(), {"projectId": project}
        )
        logger.info(
            "[firestore] Initialised against EMULATOR at %s (project=%s)",
            os.getenv("FIRESTORE_EMULATOR_HOST"), project,
        )
    else:
        _app = firebase_admin.initialize_app(options={"projectId": project})
        logger.warning(
            "[firestore] Initialised against LIVE Firestore (project=%s)", project
        )
    return _app


def get_db():
    """Return the Firestore client, initialising the app if needed."""
    from firebase_admin import firestore

    init_app()
    return firestore.client()


def reset() -> None:
    """Tear down the app. For test isolation between emulator sessions."""
    global _app
    if _app is not None:
        import firebase_admin

        try:
            firebase_admin.delete_app(_app)
        except Exception:  # noqa: BLE001 — teardown must never fail a test
            pass
        _app = None


# ==========================================================
# Payload construction (pure — no I/O, unit-testable)
# ==========================================================

def calibration_metadata(
    question_id: str, run_id: str, forecast_run_index: int
) -> dict[str, Any]:
    """
    The marker block carried by both documents (plan §2.5, A4).

    Nested under `metadata` rather than spread across top-level fields so that
    a future strict-validation pass on inbound forecastQueries has one
    namespaced key to allow, and so every cleanup query has one stable path to
    filter on.
    """
    return {
        "calibration": {
            "enabled": True,
            "questionId": question_id,
            "runId": run_id,
            "forecastRunIndex": forecast_run_index,
        }
    }


def build_session_payload(
    question_text: str,
    idempotency_key: str,
    question_id: str,
    run_id: str,
    forecast_run_index: int,
) -> dict[str, Any]:
    """
    The `sessions/{sessionId}` document.

    Field set mirrors what the BFF writes for a real session, so the worker
    sees nothing unusual. Two fields carry intent rather than convention:

    `followEnabled: false` keeps the session out of the Sprint 24 follow-up
    subgraph, which would otherwise spend tokens on a conversation nobody is
    having.

    `status: "queued"` is what the worker's `update_session_status` transitions
    away from. The document must exist before that call, which is the entire
    reason this is written first (A6).
    """
    from firebase_admin import firestore

    return {
        "userId": CALIBRATION_USER_ID,
        "question": question_text,
        "title": None,
        "idempotencyKey": idempotency_key,
        "status": "queued",
        "latestProbability": None,
        "latestConfidence": None,
        "followEnabled": False,
        "isFollowing": False,
        "canonicalKey": None,
        "errorCode": None,
        "errorMessage": None,
        "clarificationCandidates": None,
        "createdAt": firestore.SERVER_TIMESTAMP,
        "updatedAt": firestore.SERVER_TIMESTAMP,
        "lastActivityAt": firestore.SERVER_TIMESTAMP,
        "metadata": calibration_metadata(question_id, run_id, forecast_run_index),
    }


def build_query_payload(
    session_id: str,
    question_text: str,
    question_id: str,
    run_id: str,
    forecast_run_index: int,
) -> dict[str, Any]:
    """
    The `forecastQueries/{sessionId}` document — the work-queue entry.

    Written second, because this is what the worker's listener watches. Until
    it exists nothing is claimable, which is what makes the write order a
    safety property rather than a style choice.
    """
    from firebase_admin import firestore

    return {
        "queryId": f"q_{session_id}",
        "sessionId": session_id,
        "userId": CALIBRATION_USER_ID,
        "question": question_text,
        "status": "pending",
        "createdAt": firestore.SERVER_TIMESTAMP,
        "claimedAt": None,
        "claimedBy": None,
        "metadata": calibration_metadata(question_id, run_id, forecast_run_index),
    }


# ==========================================================
# Writes — the only two documents calibration ever creates
# ==========================================================

def write_dispatch(
    session_id: str,
    session_payload: dict[str, Any],
    query_payload: dict[str, Any],
) -> None:
    """
    Write the session document, then the queue document. In that order.

    Not a transaction, deliberately. The two orderings fail differently and
    only one of them fails safely:

        session first (this)  — a crash between the writes leaves an orphan
            `sessions/{id}` in `queued` that no worker will ever claim,
            because `forecastQueries` has no entry. It has no Postgres row
            either (the caller inserts that only after both writes return), so
            it is invisible to every metric and is swept by the 24h orphan
            cleanup.

        queue first           — a crash between the writes leaves a claimable
            query whose session document does not exist. The worker claims it
            and immediately dies on `NotFound` from `update_session_status`.

    A transaction would avoid both, but Firestore transactions across two
    top-level collections add contention and failure modes for a race that
    only matters if the process dies inside a two-write window — and whose
    benign direction is already free.
    """
    db = get_db()
    db.collection("sessions").document(session_id).set(session_payload)
    db.collection("forecastQueries").document(session_id).set(query_payload)
    logger.info("[firestore] Dispatched session_id=%s", session_id)


# ==========================================================
# Reads — only documents we created
# ==========================================================

def read_session(session_id: str) -> Optional[dict[str, Any]]:
    """Read `sessions/{sessionId}`. None when absent."""
    snap = get_db().collection("sessions").document(session_id).get()
    return snap.to_dict() if snap.exists else None


def read_session_result(session_id: str) -> Optional[dict[str, Any]]:
    """Read `sessionResults/{sessionId}`. None until the agent writes it."""
    snap = get_db().collection("sessionResults").document(session_id).get()
    return snap.to_dict() if snap.exists else None


def read_evidence(session_id: str) -> list[dict[str, Any]]:
    """
    Read the `sessions/{sessionId}/evidence` subcollection.

    Returns an empty list when the subcollection is missing or empty. That is
    a normal state for an early-version or evidence-thin forecast and must not
    be an error — the projection handles it and the harvest continues.
    """
    docs = (
        get_db()
        .collection("sessions")
        .document(session_id)
        .collection("evidence")
        .stream()
    )
    return [d.to_dict() for d in docs]


def read_query(session_id: str) -> Optional[dict[str, Any]]:
    """Read our own `forecastQueries/{sessionId}` — used to confirm a dispatch landed."""
    snap = get_db().collection("forecastQueries").document(session_id).get()
    return snap.to_dict() if snap.exists else None


# ==========================================================
# Orphan cleanup — the one place a collection is queried
# ==========================================================
#
# Everything above reads a document by an id we minted, which is what makes it
# structurally impossible to reach anyone else's data. Cleanup cannot work
# that way: an orphan is by definition a session with no Postgres row, so
# there is no id to look up.
#
# The compensating control is that the filter runs on Firestore's side, not
# ours. `where userId == "calibration-runner"` means the server never sends a
# document belonging to anyone else — as opposed to fetching everything and
# discarding the rest, which would still have fetched it.


def list_calibration_sessions(limit: int = 500) -> list[dict[str, Any]]:
    """
    List sessions owned by the calibration sentinel user.

    Returns `{id, status, created_at}` per session.
    """
    from google.cloud.firestore_v1.base_query import FieldFilter

    query = (
        get_db()
        .collection("sessions")
        .where(filter=FieldFilter("userId", "==", CALIBRATION_USER_ID))
        .limit(limit)
    )

    sessions = []
    for snap in query.stream():
        data = snap.to_dict() or {}
        sessions.append(
            {
                "id": snap.id,
                "status": data.get("status"),
                "created_at": data.get("createdAt"),
            }
        )
    return sessions


def delete_calibration_session(session_id: str) -> None:
    """
    Delete one calibration session and its queue document.

    Refuses any id lacking the calibration prefix. The caller already scopes
    by `userId`, so this check is redundant — deliberately. It is the last
    gate before an irreversible operation on a collection that also holds real
    users' data, and redundancy is the right amount of caution there.
    """
    if not session_id.startswith(f"{SESSION_ID_PREFIX}_"):
        raise ValueError(
            f"Refusing to delete {session_id!r}: not a calibration session id. "
            "Calibration only ever deletes documents it created."
        )

    db = get_db()
    session_ref = db.collection("sessions").document(session_id)

    # Firestore does not remove subcollections when a parent is deleted.
    for sub in session_ref.collections():
        for child in sub.list_documents():
            child.delete()

    session_ref.delete()
    db.collection("forecastQueries").document(session_id).delete()
    logger.info("[firestore] Deleted calibration session %s", session_id)
