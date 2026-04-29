"""
Fixtures for hub tests.

T10/T11 (mocked Firestore):
    reset_firebase_state — opt-in. Saves/clears/restores firebase_admin._apps
    and agent.firestore_client._db around a test. Required by init_app
    tests so each starts from a clean slate; harmless for other mocked
    tests; MUST NOT be used by emulator tests (would wipe the session
    init).

T12 (real Firestore emulator):
    firestore_emulator_available — session-scoped probe. pytest.skip if
        the emulator is unreachable (matches the db_available pattern in
        the parent conftest.py — Gate 1/2 mocked tests stay green when
        the emulator is not running).
    emulator_db — session-scoped Firestore client pointing at the emulator.
        Bypasses agent.firestore_client.init_app() (which goes through ADC
        and would require gcloud login). Uses AnonymousCredentials —
        valid because the emulator skips real auth when
        FIRESTORE_EMULATOR_HOST is set. Clears firebase_admin._apps both
        before and after the session.
    emulator_test_id — function-scoped. Unique per-test prefix; teardown
        deletes all docs with that prefix from forecastQueries, sessions,
        and sessionResults.
"""

import os
import socket
import uuid

import firebase_admin
import google.auth.credentials
import pytest
from firebase_admin import credentials, firestore

from agent import firestore_client


class _EmulatorCredentials(credentials.Base):
    """Fake firebase_admin credential for emulator-only use.

    firebase_admin.credentials does NOT expose AnonymousCredentials (that
    class lives at google.auth.credentials.AnonymousCredentials, not in
    the Firebase wrapper). firebase_admin.initialize_app requires a
    credentials.Base instance, so we wrap the Google-level anonymous
    credential in a thin Base subclass. With FIRESTORE_EMULATOR_HOST set,
    the Admin SDK routes RPCs through the emulator and skips real auth —
    these credentials are never validated.
    """

    def get_credential(self):
        return google.auth.credentials.AnonymousCredentials()


# ==========================================================
# T10/T11 — mocked-Firestore tests
# ==========================================================

@pytest.fixture
def reset_firebase_state():
    """Save → clear → run test → restore. Opt-in via @pytest.mark.usefixtures
    or direct fixture request. Not autouse: T12 emulator tests must keep
    the session-initialized firebase_admin app intact."""
    saved_apps = dict(firebase_admin._apps)
    firebase_admin._apps.clear()
    saved_db = firestore_client._db
    firestore_client._db = None
    yield
    firebase_admin._apps.clear()
    firebase_admin._apps.update(saved_apps)
    firestore_client._db = saved_db


# ==========================================================
# T12 — Firestore emulator
# ==========================================================

def _emulator_host() -> str:
    """Resolve the emulator host:port. FIRESTORE_EMULATOR_HOST overrides;
    default localhost:8080 (Firebase CLI standard)."""
    return os.getenv("FIRESTORE_EMULATOR_HOST", "localhost:8080")


@pytest.fixture(scope="session")
def firestore_emulator_available():
    """TCP probe the emulator. pytest.skip the requesting test if the
    port is not bound — the test runner stays green when the user hasn't
    started the emulator. Matches the db_available skip pattern in
    tests/conftest.py."""
    host = _emulator_host()
    try:
        host_part, port_part = host.rsplit(":", 1)
        port = int(port_part)
        with socket.create_connection((host_part, port), timeout=2):
            pass
    except (OSError, ValueError) as exc:
        pytest.skip(
            f"Firestore emulator not reachable at {host}. "
            f"Start with: firebase emulators:start --only firestore. "
            f"({exc})"
        )


@pytest.fixture(scope="session")
def emulator_db(firestore_emulator_available):
    """Session-scoped Firestore client pointing at the emulator.

    Pre-clear protects against firebase_admin pollution from earlier tests
    in the same pytest run that may have initialized the SDK with different
    credentials. Post-clear leaves a clean slate for any downstream tests
    that need to init themselves (e.g., reset_firebase_state-using tests
    in test_firestore_client.py).

    AnonymousCredentials is valid here: with FIRESTORE_EMULATOR_HOST set,
    the Admin SDK routes RPCs through the emulator and skips real auth.
    """
    firebase_admin._apps.clear()
    firestore_client._db = None

    os.environ.setdefault("FIRESTORE_EMULATOR_HOST", _emulator_host())

    firebase_admin.initialize_app(
        _EmulatorCredentials(),
        {"projectId": "anizai-ai"},
    )
    db = firestore.client()
    yield db

    firebase_admin._apps.clear()
    firestore_client._db = None


@pytest.fixture
def emulator_test_id(emulator_db):
    """Unique sessionId prefix; teardown removes any docs that start with
    it. Test sizes (< 10 docs/test) make a client-side prefix filter on
    a full collection scan acceptable."""
    test_id = f"test_{uuid.uuid4().hex[:8]}"
    yield test_id
    for collection_name in ("forecastQueries", "sessions", "sessionResults"):
        for doc in emulator_db.collection(collection_name).stream():
            if doc.id.startswith(test_id):
                doc.reference.delete()
